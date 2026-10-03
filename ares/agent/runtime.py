"""Componenti runtime dell'assistente: modelli, indice vettoriale e strumenti locali."""

import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from agno.fs import FileSystem
from agno.knowledge.embedder.ollama import OllamaEmbedder
from agno.knowledge.knowledge import Knowledge
from agno.tools.toolkit import Toolkit
from agno.tools.workspace import Workspace
from agno.vectordb.lancedb import LanceDb
from agno.vectordb.search import SearchType

from ares import config
from ares.agent.agno_interni import OllamaConRagionamento
from ares.agent.descrizioni import CARTELLA, QUADERNO, descrivi
from ares.agent.marcatura import ConNota, DiAres
from ares.agent.prompts import AVVISO_SANDBOX, data_e_ora, descrizione_del_comando
from ares.agent.sandbox import Sandbox, prepara_sandbox
from ares.config import Impostazioni, Percorsi, Politica
from ares.core.regole import Regole, leggi_regole
from ares.state.archivi import build_db, build_filesystem, build_result_store
from ares.state.platform_files import rendi_privato

# I costruttori degli archivi vivono in `state/archivi.py`: sono di chi legge
# lo stato, non dell'agente, e `sessions` li usa senza passare da qui. Restano
# importabili da questo modulo perche' le prove e i comandi lo fanno da sempre.
__all__ = [
    "AresWorkspace",
    "ambiente_del_comando",
    "build_chat_model",
    "build_db",
    "build_filesystem",
    "build_knowledge",
    "build_learning_model",
    "build_orologio",
    "build_quaderno",
    "build_result_store",
    "build_workspace",
    "testa_e_coda",
]


def _esigi_locale(nome: str, ruolo: str) -> str:
    """Rifiuta un modello cloud per un ruolo che deve restare sulla macchina.

    Vale per l'embedder: cambiarlo invaliderebbe l'indice LanceDB gia' scritto.
    Meglio un errore all'avvio di un turno che spedisce fuori le intuizioni.
    """
    if config.e_modello_cloud(nome):
        raise ValueError(ruolo + " non puo' usare un modello cloud (" + nome + "): resta locale sempre.")
    return nome


def build_chat_model(impostazioni: Impostazioni) -> OllamaConRagionamento:
    """Modello conversazionale, locale o cloud secondo `impostazioni.principale`.

    L'host resta quello locale anche per il cloud: e' il daemon a inoltrare.
    """
    return OllamaConRagionamento(
        id=impostazioni.principale,
        host=impostazioni.host,
        options=impostazioni.opzioni,
        keep_alive=impostazioni.keep_alive,
        # `think` non e' una option di Ollama ma un parametro top-level
        # dell'API, e Agno non lo espone: request_params viene fuso nei
        # kwargs di ogni chiamata al client, streaming compreso.
        request_params={"think": impostazioni.think},
    )


def build_learning_model(impostazioni: Impostazioni) -> OllamaConRagionamento:
    """Modello a bassa temperatura per l'estrazione strutturata.

    Locale o cloud secondo `impostazioni.apprendimento`. Il contesto ridotto,
    quando i due modelli differiscono, lo decide `Impostazioni`.
    """
    return OllamaConRagionamento(
        id=impostazioni.apprendimento,
        host=impostazioni.host,
        options=impostazioni.opzioni_apprendimento,
        keep_alive=impostazioni.keep_alive,
        request_params={"think": impostazioni.think_apprendimento},
    )


def build_knowledge(percorsi: Percorsi, impostazioni: Impostazioni) -> Knowledge:
    """Indice vettoriale locale delle intuizioni apprese."""
    config.prepara_archivio(percorsi)
    indice = Path(percorsi.lancedb_uri)
    indice.mkdir(parents=True, exist_ok=True)
    rendi_privato(indice)
    return Knowledge(
        vector_db=LanceDb(
            uri=percorsi.lancedb_uri,
            table_name="learned_knowledge",
            search_type=SearchType.hybrid,
            embedder=build_embedder(impostazioni),
        ),
    )


def build_embedder(impostazioni: Impostazioni) -> OllamaEmbedder:
    """L'embedder locale: indicizza le intuizioni e confronta le memorie da consolidare."""
    return OllamaEmbedder(
        id=_esigi_locale(impostazioni.embedder, "EMBEDDER_MODEL"),
        host=impostazioni.host,
        dimensions=impostazioni.embedder_dimensioni,
    )


def _con_prefisso(strumenti: Toolkit, prefisso: str) -> None:
    """Rinomina sul posto ogni strumento del toolkit, e le liste che li nominano."""
    for elenco in (strumenti.functions, strumenti.async_functions):
        for nome in list(elenco):
            funzione = elenco.pop(nome)
            funzione.name = prefisso + nome
            elenco[funzione.name] = funzione
    strumenti.requires_confirmation_tools = [prefisso + nome for nome in strumenti.requires_confirmation_tools]


def build_quaderno(fs: FileSystem) -> Toolkit:
    """Gli strumenti del quaderno, con `config.QUADERNO_PREFIX` davanti al nome.

    Il prefisso rende ogni nome esplicito quanto `workspace_`: il modello non
    deve dedurre dall'assenza di un prefisso che `read_file` e' il quaderno.
    """
    strumenti = fs.tools()
    _con_prefisso(strumenti, config.QUADERNO_PREFIX)
    descrivi(strumenti, config.QUADERNO_PREFIX, QUADERNO, q=config.QUADERNO_PREFIX)
    return strumenti


def che_ora_e() -> str:
    """Dice che giorno e che ora e' adesso, nel fuso orario della persona.

    Il messaggio di sistema porta solo la data: chiama questo strumento
    quando serve l'ora precisa, o per sapere quanto tempo e' passato.
    """
    return data_e_ora()


def build_orologio() -> Toolkit:
    """L'unico strumento che dice l'ora: il system message porta solo il giorno.

    Cosi' il prefisso del prompt non cambia a ogni turno (vedi
    `prompts.riga_della_data`), e l'ora costa token solo quando serve.
    """
    return Toolkit(name="orologio", tools=[che_ora_e])


# Le variabili che un comando eredita dalla shell di Ares. Tutto il resto
# resta fuori: un token esportato nella shell della persona non deve arrivare
# ne' al comando ne', attraverso il suo output, al modello. PATH e HOME
# bastano a trovare i programmi e le loro configurazioni; lingua e terminale
# decidono come scrivono; i proxy servono a chi usa la rete; su Windows le
# variabili di sistema sono necessarie anche a `dir`. `LC_*` entra per
# prefisso.
VARIABILI_DEL_COMANDO = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LANGUAGE",
        "TERM",
        "TZ",
        "TMPDIR",
        "SSH_AUTH_SOCK",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
        "all_proxy",
        # Windows
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "USERNAME",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "NUMBER_OF_PROCESSORS",
        "OS",
    }
)

# Le sequenze ANSI (colori, cursore) che i programmi scrivono anche su una pipe
# quando sono forzati: nel risultato per il modello sono solo token sprecati.
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def ambiente_del_comando(ambiente: Mapping[str, str] | None = None) -> dict[str, str]:
    """L'ambiente minimo per un comando: le `VARIABILI_DEL_COMANDO` presenti in `ambiente` (di serie `os.environ`).

    Su Windows i nomi sono senza distinzione di maiuscole, e `os.environ` li
    espone gia' maiuscoli; qui si confronta in maiuscolo per entrambi i sistemi,
    conservando il nome originale.
    """
    sorgente = os.environ if ambiente is None else ambiente
    ammesse = {nome.upper() for nome in VARIABILI_DEL_COMANDO}
    return {
        nome: valore for nome, valore in sorgente.items() if nome.upper() in ammesse or nome.upper().startswith("LC_")
    }


def testa_e_coda(testo: str, righe: int) -> str:
    """Al piu' `righe` righe di `testo`: la prima meta' e l'ultima, con il conto di quelle tolte in mezzo.

    Le ultime cento righe sole perdono la testa, dove `git log`, i test e i
    compilatori mettono cio' che conta; la testa sola perde l'esito finale.
    """
    tutte = _ANSI.sub("", testo).splitlines()
    if righe < 1 or len(tutte) <= righe:
        return "\n".join(tutte)
    testa = (righe + 1) // 2
    coda = righe - testa
    omesse = len(tutte) - testa - coda
    avviso = "[... " + str(omesse) + " righe omesse: in tutto " + str(len(tutte)) + " ...]"
    return "\n".join([*tutte[:testa], avviso, *tutte[len(tutte) - coda :]] if coda else [*tutte[:testa], avviso])


class AresWorkspace(Workspace):
    """Workspace Agno con nomi distinti dagli strumenti del quaderno, e la shell del sistema.

    `run_command` e' riscritto: quello di Agno eredita stdin e l'ambiente
    intero della shell e tiene solo la coda dell'output. Qui lo stdin e' chiuso
    (un comando che aspetta input termina subito invece di restare appeso fino
    al timeout), l'ambiente e' `ambiente_del_comando`, e l'output torna con
    testa e coda. La firma resta quella di Agno, cosi' lo schema per il
    modello non cambia; la descrizione viene da `prompts.descrizione_del_comando`.

    `regole` legge le regole di autorizzazione della persona (`core/regole.py`)
    al momento del comando: una `nega` lo ferma prima di eseguirlo, anche in
    `auto`, dove nessuna conferma passa dal nucleo. Con `sandbox` il comando
    gira dentro `bwrap` (`agent/sandbox.py`).
    """

    prefisso: str = ""
    regole: Callable[[], Regole] | None = None
    sandbox: Sandbox | None = None

    def run_command(self, args: list[str], tail: int = 100, timeout: int = 120) -> str:
        """Esegue `args` nella cartella di lavoro e restituisce testa e coda dell'output, o l'errore."""
        regola = self.regole().decidi(args) if self.regole is not None else None
        if regola is not None and regola.effetto == "nega":
            return DiAres(
                "Errore: comando negato da una regola di autorizzazione della persona (" + regola.fonte + "). "
                "Non riprovare con una variante."
            )
        try:
            esito = subprocess.run(
                self.sandbox.argv(args) if self.sandbox is not None else args,
                capture_output=True,
                text=True,
                errors="replace",
                cwd=str(self.root),
                timeout=timeout,
                stdin=subprocess.DEVNULL,
                env=ambiente_del_comando(),
            )
        except subprocess.TimeoutExpired:
            return DiAres("Errore: il comando non e' finito entro " + str(timeout) + " secondi ed e' stato interrotto.")
        except OSError as errore:
            return DiAres("Errore nell'avvio del comando: " + str(errore))
        if esito.returncode != 0:
            # Molti programmi scrivono l'errore su stdout: si danno entrambi.
            pezzi = ["Errore (uscita " + str(esito.returncode) + ")."]
            if esito.stderr.strip():
                pezzi.append(testa_e_coda(esito.stderr, tail))
            if esito.stdout.strip():
                pezzi.append("Output: " + testa_e_coda(esito.stdout, tail))
            testo = "\n".join(pezzi)
            # L'avviso e' di Ares, non del comando: resta fuori dal blocco dei dati.
            if self.sandbox is not None and self.sandbox.forse_colpa_sua(esito.stderr + "\n" + esito.stdout):
                return ConNota(testo, AVVISO_SANDBOX.format(rete="" if self.sandbox.rete else ", e la rete e' spenta"))
            return testo
        return testa_e_coda(esito.stdout, tail)

    def _check_read_before_write(self, file_path: Path, op: str) -> str | None:
        """L'errore di Agno per un file esistente non ancora letto, con il nome vero dello strumento di lettura."""
        if super()._check_read_before_write(file_path, op) is None:
            return None
        return (
            "Errore: " + file_path.name + " esiste e in questa conversazione non l'hai ancora letto. "
            "Leggilo con " + self.prefisso + "read_file, poi riprova."
        )

    async def arun_command(self, args: list[str], tail: int = 100, timeout: int = 120) -> str:
        """La variante asincrona delega a `run_command` in un thread: stesse garanzie, un codice solo."""
        import asyncio

        return await asyncio.to_thread(self.run_command, args, tail, timeout)

    def __init__(
        self,
        root,
        prefisso: str,
        regole: Callable[[], Regole] | None = None,
        sandbox: Sandbox | None = None,
        **kwargs,
    ):
        super().__init__(root, **kwargs)
        self.prefisso = prefisso
        self.regole = regole
        self.sandbox = sandbox
        _con_prefisso(self, prefisso)
        descrivi(self, prefisso, CARTELLA, w=prefisso)
        for elenco in (self.functions, self.async_functions):
            if prefisso + "run_command" in elenco:
                elenco[prefisso + "run_command"].description = descrizione_del_comando(
                    sandbox is not None, sandbox is not None and sandbox.rete
                )

        # L'istruzione predefinita nomina gli strumenti prima della rinomina.
        # Il prompt italiano e coerente viene composto da assistant_prompts.
        self.instructions = None
        self.add_instructions = False


def build_workspace(percorsi: Percorsi, politica: Politica, modo: str | None = None) -> AresWorkspace:
    """Costruisce lo spazio di lavoro sulla cartella di lavoro, nella modalita' data.

    La cartella e' gia' stata vagliata e autorizzata da `cli/cartella.py`; qui
    si pretende solo che esista, per non lavorare in una directory nata da un
    refuso. `modo` vuoto vale `config.MODO_PREDEFINITO`. Solleva
    `SandboxNonDisponibile` se la politica chiede una sandbox che qui non si
    puo' applicare.
    """
    radice = percorsi.lavoro.resolve()
    if not radice.is_dir():
        raise ValueError("La cartella di lavoro " + str(radice) + " non esiste.")
    silenziosi, confermati = config.liste_modalita(modo or config.MODO_PREDEFINITO)
    return AresWorkspace(
        radice,
        prefisso=politica.workspace.prefisso,
        regole=lambda: leggi_regole(percorsi, politica),
        sandbox=prepara_sandbox(replace(percorsi, lavoro=radice), politica),
        allowed=silenziosi,
        confirm=confermati,
        require_read_before_write=politica.workspace.leggi_prima_di_scrivere,
    )
