"""Componenti runtime dell'assistente: modelli, indice vettoriale e strumenti locali."""

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
from ares.agent.prompts import data_e_ora, descrizione_del_comando
from ares.config import Impostazioni, Percorsi, Politica
from ares.state.archivi import build_db, build_filesystem, build_result_store
from ares.state.platform_files import rendi_privato

# I costruttori degli archivi vivono in `state/archivi.py`: sono di chi legge
# lo stato, non dell'agente, e `sessions` li usa senza passare da qui. Restano
# importabili da questo modulo perche' le prove e i comandi lo fanno da sempre.
__all__ = [
    "AresWorkspace",
    "build_chat_model",
    "build_db",
    "build_filesystem",
    "build_knowledge",
    "build_learning_model",
    "build_orologio",
    "build_quaderno",
    "build_result_store",
    "build_workspace",
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
            embedder=OllamaEmbedder(
                id=_esigi_locale(impostazioni.embedder, "EMBEDDER_MODEL"),
                host=impostazioni.host,
                dimensions=impostazioni.embedder_dimensioni,
            ),
        ),
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


class AresWorkspace(Workspace):
    """Workspace Agno con nomi distinti dagli strumenti del quaderno, e la shell del sistema."""

    def __init__(self, root, prefisso: str, **kwargs):
        super().__init__(root, **kwargs)
        _con_prefisso(self, prefisso)
        for elenco in (self.functions, self.async_functions):
            if prefisso + "run_command" in elenco:
                elenco[prefisso + "run_command"].description = descrizione_del_comando()

        # L'istruzione predefinita nomina gli strumenti prima della rinomina.
        # Il prompt italiano e coerente viene composto da assistant_prompts.
        self.instructions = None
        self.add_instructions = False


def build_workspace(percorsi: Percorsi, politica: Politica, modo: str | None = None) -> AresWorkspace:
    """Costruisce lo spazio di lavoro sulla cartella di lavoro, nella modalita' data.

    La cartella e' gia' stata vagliata e autorizzata da `cli/cartella.py`; qui
    si pretende solo che esista, per non lavorare in una directory nata da un
    refuso. `modo` vuoto vale `config.MODO_PREDEFINITO`.
    """
    radice = percorsi.lavoro.resolve()
    if not radice.is_dir():
        raise ValueError("La cartella di lavoro " + str(radice) + " non esiste.")
    silenziosi, confermati = config.liste_modalita(modo or config.MODO_PREDEFINITO)
    return AresWorkspace(
        radice,
        prefisso=politica.workspace.prefisso,
        allowed=silenziosi,
        confirm=confermati,
        require_read_before_write=politica.workspace.leggi_prima_di_scrivere,
    )
