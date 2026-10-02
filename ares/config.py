"""Configurazione centrale di Ares.

I valori predefiniti sono pensati per un host locale con circa 16 GiB di
VRAM. Modelli, percorsi, identita', contesto, campionamento e gli
interruttori delle funzioni facoltative si sovrascrivono dal `.env` o
dall'ambiente (l'elenco e' `.env.example`); il resto si cambia qui.

Importare il modulo non tocca il disco: legge `.env` e definisce nomi. I
nomi sono la sorgente; `leggi_percorsi`, `leggi_impostazioni` e
`leggi_politica` li fotografano in `Percorsi`, `Impostazioni` e `Politica`,
che il resto del codice riceve come parametri (vedi "Dipendenze esplicite"
in docs/architecture.md). La directory dello stato la crea
`prepara_archivio`.
"""

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values

from ares.state.platform_files import rendi_privato

# La radice del clone, un livello sopra `ares/`: da qui si trovano `.env` e
# le vecchie directory da migrare.
BASE_DIR = Path(__file__).resolve().parent.parent


def leggi_ambiente(percorso_env: Path, ambiente: Mapping[str, str]) -> dict[str, str]:
    """L'ambiente di questo avvio: le righe del `.env` sotto, l'ambiente dato sopra.

    Il `.env` si legge in un dizionario e non in `os.environ`, cosi' i
    comandi lanciati dal modello non lo ereditano (un `env` lo farebbe
    tornare nel contesto, magari cloud). L'ambiente vince sul `.env`. Su
    Windows le chiavi del file vanno in maiuscolo, come fa `os.environ`.
    """
    righe = {
        chiave.upper() if os.name == "nt" else chiave: valore
        for chiave, valore in dotenv_values(percorso_env).items()
        if valore is not None
    }
    return {**righe, **ambiente}


AMBIENTE: Mapping[str, str] = leggi_ambiente(BASE_DIR / ".env", os.environ)

# ---------------------------------------------------------------------------
# Modelli
# ---------------------------------------------------------------------------

# Modello locale: 9B Q8_0 con supporto per tools, thinking e vision. Gira
# in scheda e non esce mai dalla macchina.
MODELLO_LOCALE = "hf.co/empero-ai/Qwen3.8-9B-Distill-GGUF:Q8_0"

# Modello cloud di Ollama, con tools, thinking e vision. Il tag `:cloud` (o
# `-cloud`) lo fa inoltrare dal daemon locale a https://ollama.com, dopo un
# `ollama signin` una tantum: Ares parla sempre con localhost e nessuna
# chiave API entra nell'ambiente. Cio' che il modello riceve esce pero'
# dalla macchina; le garanzie di Ollama sono contrattuali, non tecniche
# (README, "Locale, cloud, o entrambi").
MODELLO_CLOUD = "glm-5.3-flash:cloud"

# Modello della conversazione. Il default e' locale: chi clona non manda
# nulla a `ollama.com` senza averlo deciso. Per il cloud si usa il `.env`, non
# questo file, che divergerebbe a ogni `git pull`:
#
#     ARES_MAIN_MODEL=glm-5.3-flash:cloud
#
# Se il modello non e' scaricato lo dice `ares preflight`, e la chat all'avvio.
MAIN_MODEL = AMBIENTE.get("ARES_MAIN_MODEL") or MODELLO_LOCALE

# Modello per l'estrazione delle memorie, locale di default. E' una scelta
# separata dalla conversazione perche' ogni estrazione manda al modello il
# testo del turno piu' le memorie gia' salvate, cioe' la parte piu'
# sensibile:
#
#     ARES_LEARNING_MODEL=glm-5.3-flash:cloud
#
# Con la conversazione locale conviene lo stesso modello in entrambi i ruoli
# (il default), per non scambiare pesi in VRAM fra risposta ed estrazione.
LEARNING_MODEL = AMBIENTE.get("ARES_LEARNING_MODEL") or MODELLO_LOCALE


def e_modello_cloud(nome: str) -> bool:
    """Vero se il nome designa un modello cloud di Ollama.

    Guarda solo il tag, nelle due forme `:cloud` e `:<taglia>-cloud`: un
    repository con "cloud" nel nome non basta, e senza tag vale `:latest`.
    """
    _, separatore, tag = nome.rpartition(":")
    return bool(separatore) and (tag == "cloud" or tag.endswith("-cloud"))


# Embedder unico per LanceDB: indici e query devono usare lo stesso modello
# e la stessa dimensionalita'. Resta sempre locale (`build_knowledge` rifiuta
# un nome cloud): cambiarlo invaliderebbe l'indice gia' scritto.
EMBEDDER_MODEL = "nomic-embed-text-v2-moe"
EMBEDDER_DIMENSIONS = 768

# ---------------------------------------------------------------------------
# Tuning Ollama
# ---------------------------------------------------------------------------

# Server Ollama, sempre esplicito: senza host, con OLLAMA_API_KEY
# nell'ambiente Agno dirotterebbe le chiamate su https://ollama.com. Anche i
# modelli cloud passano da qui, inoltrati dal daemon.
OLLAMA_HOST = "http://localhost:11434"

# Il default di Ollama (4096 token) si satura in pochi turni, e il
# troncamento silenzioso sembra "l'agente ha dimenticato". 128k tiene il
# modello locale di serie tutto in VRAM su 16 GiB: a 256k un quarto finiva
# sulla CPU, e risposta ed estrazione andavano due volte e mezzo piu' lente
# (docs/memory-quality.md). Dipende dalla scheda, quindi `ARES_NUM_CTX` lo
# cambia dal `.env`; `ares preflight` avvisa se il modello non sta in VRAM.
# Per un modello cloud non costa VRAM locale e il tetto vero lo decide il
# servizio.
NUM_CTX_MINIMO = 8192


def leggi_num_ctx(valore: str | None, predefinito: int = 131072) -> int:
    """Il contesto dal `.env`, o `predefinito` se non c'e'.

    Solleva `ValueError` se non e' un intero di almeno `NUM_CTX_MINIMO`:
    un contesto sbagliato si scopre solo quando il modello comincia a
    dimenticare.
    """
    if valore is None or not valore.strip():
        return predefinito
    try:
        numero = int(valore.strip())
    except ValueError:
        numero = 0
    if numero < NUM_CTX_MINIMO:
        raise ValueError("ARES_NUM_CTX deve essere un intero di almeno " + str(NUM_CTX_MINIMO) + ": " + repr(valore))
    return numero


# Il campionamento: variabile del `.env` -> (default, tipo, minimo, massimo).
# I default sono quelli che Ollama applica a cio' che non riceve: scritti
# qui, Ares li manda espliciti e i rapporti degli eval li registrano. Le
# schede dei modelli ne consigliano spesso altri (Qwen3.5 e derivati:
# temperatura 1.0, top_p 0.95, top_k 20, presence_penalty 1.5,
# repeat_penalty 1.0 per l'uso generale), e un `repeat_penalty` sopra 1 pesa
# su una tool call JSON, dove parentesi e nomi di campo si ripetono per
# forza: si provano con gli eval prima di cambiare i default. Gli intervalli
# sono quelli dell'API di Ollama; `None` vuol dire nessun tetto.
CAMPIONAMENTO: dict[str, tuple[float | int, type, float, float | None]] = {
    "ARES_TEMPERATURE": (0.7, float, 0.0, 2.0),
    "ARES_TOP_P": (0.9, float, 0.0, 1.0),
    "ARES_TOP_K": (40, int, 0, None),
    "ARES_MIN_P": (0.0, float, 0.0, 1.0),
    "ARES_REPEAT_PENALTY": (1.1, float, 0.0, None),
    "ARES_PRESENCE_PENALTY": (0.0, float, -2.0, 2.0),
}


def leggi_campionamento(ambiente: Mapping[str, str]) -> dict[str, float | int]:
    """Le opzioni di campionamento dall'ambiente, con il nome che Ollama vuole.

    Una variabile assente o vuota vale il default di `CAMPIONAMENTO`. Solleva
    `ValueError`, nominando la variabile, per un valore che non e' del tipo
    atteso o esce dall'intervallo: `ARES_TOP_K=2.5` e' un errore, non 2.
    `repeat_penalty` deve essere positivo, perche' zero azzera i logit.
    """
    opzioni: dict[str, float | int] = {}
    for variabile, (predefinito, tipo, minimo, massimo) in CAMPIONAMENTO.items():
        nome = variabile.removeprefix("ARES_").lower()
        valore = ambiente.get(variabile)
        if valore is None or not valore.strip():
            opzioni[nome] = predefinito
            continue
        try:
            numero = tipo(valore.strip())
        except ValueError:
            numero = None
        fuori = (
            numero is None
            or numero < minimo
            or (massimo is not None and numero > massimo)
            or (variabile == "ARES_REPEAT_PENALTY" and numero <= 0)
        )
        if fuori:
            intervallo = str(minimo) + (" e " + str(massimo) if massimo is not None else " in su")
            raise ValueError(
                variabile
                + " deve essere "
                + ("un intero" if tipo is int else "un numero")
                + " fra "
                + intervallo
                + ": "
                + repr(valore)
            )
        opzioni[nome] = numero
    return opzioni


def leggi_interruttore(variabile: str, valore: str | None) -> bool | None:
    """`1` o `0` da una variabile d'ambiente; `None` se assente o vuota.

    Solleva `ValueError`, nominando la variabile, per qualunque altro valore.
    """
    if valore is None or not valore.strip():
        return None
    if valore.strip() in ("0", "1"):
        return valore.strip() == "1"
    raise ValueError(variabile + " deve essere 0 o 1: " + repr(valore))


def leggi_sandbox(valore: str | None) -> str | None:
    """`bwrap` o `None` (assente, vuoto o `0`) da `ARES_SANDBOX`; `ValueError` per altro."""
    if valore is None or valore.strip() in ("", "0"):
        return None
    if valore.strip() == "bwrap":
        return "bwrap"
    raise ValueError("ARES_SANDBOX deve essere bwrap o 0: " + repr(valore))


try:
    NUM_CTX = leggi_num_ctx(AMBIENTE.get("ARES_NUM_CTX"))
    _CAMPIONAMENTO = leggi_campionamento(AMBIENTE)
    # Il contesto di sessione estratto come JSON vincolato dallo schema invece
    # che con una tool call; vale solo con un estrattore locale (vedi
    # `Impostazioni.estrazione_vincolata`). Assente: acceso.
    ESTRAZIONE_VINCOLATA = leggi_interruttore("ARES_ESTRAZIONE_VINCOLATA", AMBIENTE.get("ARES_ESTRAZIONE_VINCOLATA"))
    # Gli strumenti di entita' e intuizioni fuori dal prompt finche' il
    # modello non li attiva (`agent/scaffale.py`). Assente: acceso.
    SU_RICHIESTA = leggi_interruttore("ARES_STRUMENTI_SU_RICHIESTA", AMBIENTE.get("ARES_STRUMENTI_SU_RICHIESTA"))
    # I comandi dentro bubblewrap (`agent/sandbox.py`). Assente: spenta, perche'
    # accesa toglierebbe la rete a comandi che oggi funzionano.
    SANDBOX = leggi_sandbox(AMBIENTE.get("ARES_SANDBOX"))
    # La rete dentro la sandbox. Assente: niente rete.
    SANDBOX_RETE = leggi_interruttore("ARES_SANDBOX_RETE", AMBIENTE.get("ARES_SANDBOX_RETE")) is True
    # Le skill della persona e del progetto, e lo strumento che ne propone
    # di nuove (`agent/skill.py`). Assente: accese.
    SKILL = leggi_interruttore("ARES_SKILL", AMBIENTE.get("ARES_SKILL"))
except ValueError as errore:
    # All'import, prima di ogni comando: una riga e non un traceback.
    raise SystemExit("Configurazione di Ares non valida: " + str(errore)) from None

# Tiene i pesi in VRAM fra un turno e l'altro (il default di Ollama e' 5
# minuti). Innocuo per un modello cloud.
KEEP_ALIVE = "30m"

TEMPERATURE = float(_CAMPIONAMENTO["temperature"])
TOP_P = float(_CAMPIONAMENTO["top_p"])
TOP_K = int(_CAMPIONAMENTO["top_k"])
MIN_P = float(_CAMPIONAMENTO["min_p"])
REPEAT_PENALTY = float(_CAMPIONAMENTO["repeat_penalty"])
PRESENCE_PENALTY = float(_CAMPIONAMENTO["presence_penalty"])

# Ragionamento prima di rispondere: acceso per la conversazione, spento per
# le estrazioni strutturate, dove aggiungerebbe solo latenza.
MAIN_THINK = True
LEARNING_THINK = False

# Contesto dell'estrazione quando i due modelli sono diversi (la regola e' in
# `Impostazioni.num_ctx_apprendimento`). Un'estrazione riceve solo il testo
# del turno e le memorie, pochi migliaia di token: 32k lascia ampio margine,
# che serve perche' Ollama tronca oltre num_ctx senza errore, e fa
# risparmiare parecchia VRAM (i numeri sono nel README).
NUM_CTX_ESTRAZIONE = 32768

# Temperatura bassa: l'estrazione e' trascrizione strutturata.
TEMPERATURE_ESTRAZIONE = 0.2


# ---------------------------------------------------------------------------
# Impostazioni della conversazione
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Impostazioni:
    """Con quali modelli parla una conversazione, e come li raggiunge.

    Le `options` di Ollama sono proprieta' e non campi perche' dipendono
    dalla coppia di modelli.
    """

    principale: str
    apprendimento: str
    embedder: str
    embedder_dimensioni: int
    host: str
    keep_alive: str
    num_ctx: int
    temperatura: float
    temperatura_apprendimento: float
    think: bool
    think_apprendimento: bool
    # Il resto del campionamento, uguale per i due ruoli; i default sono
    # quelli di Ollama (`CAMPIONAMENTO`), cosi' una prova che costruisce
    # l'oggetto a mano non deve nominarli.
    top_p: float = 0.9
    top_k: int = 40
    min_p: float = 0.0
    repeat_penalty: float = 1.1
    presence_penalty: float = 0.0
    # `ARES_ESTRAZIONE_VINCOLATA`: `None` vuol dire il default, acceso.
    vincolo_estrazione: bool | None = None

    @property
    def estrazione_vincolata(self) -> bool:
        """Vero se il contesto di sessione si estrae come JSON vincolato dallo schema.

        Solo con un estrattore locale: il cloud di Ollama non applica `format`
        con uno schema. Con la tool call, un modello piccolo spesso non chiama
        lo strumento o lo chiama con argomenti non validi; con lo schema la
        forma la garantisce Ollama (docs/memory-quality.md).
        """
        return self.vincolo_estrazione is not False and not e_modello_cloud(self.apprendimento)

    @property
    def num_ctx_apprendimento(self) -> int:
        """Il contesto dell'estrazione: stretto solo se i due modelli sono diversi.

        Con lo stesso modello, un `num_ctx` diverso farebbe riavviare il
        runner di Ollama a ogni passaggio, perdendo la cache del prompt. Non
        supera mai quello della conversazione, anche se `ARES_NUM_CTX` e'
        sotto `NUM_CTX_ESTRAZIONE`.
        """
        return min(NUM_CTX_ESTRAZIONE, self.num_ctx) if self.apprendimento != self.principale else self.num_ctx

    @property
    def estrazione_in_parallelo(self) -> bool:
        """Vero se gli store estraggono insieme invece che uno dopo l'altro.

        Solo con l'estrazione cloud: le chiamate si sovrappongono e il turno
        aspetta la piu' lenta. In locale Ollama le serve comunque una alla
        volta, e in parallelo non si guadagna niente (docs/memory-quality.md).
        """
        return e_modello_cloud(self.apprendimento)

    @property
    def _campionamento(self) -> dict[str, Any]:
        return {
            "top_p": self.top_p,
            "top_k": self.top_k,
            "min_p": self.min_p,
            "repeat_penalty": self.repeat_penalty,
            "presence_penalty": self.presence_penalty,
        }

    @property
    def opzioni(self) -> dict[str, Any]:
        """Le `options` di Ollama per la conversazione: contesto, temperatura e campionamento."""
        return {"num_ctx": self.num_ctx, "temperature": self.temperatura, **self._campionamento}

    @property
    def opzioni_apprendimento(self) -> dict[str, Any]:
        """Quelle dell'estrazione: il suo contesto e la sua temperatura, lo stesso campionamento."""
        return {
            "num_ctx": self.num_ctx_apprendimento,
            "temperature": self.temperatura_apprendimento,
            **self._campionamento,
        }

    def avviso_cloud(self) -> list[str]:
        """Righe che dicono cosa esce dalla macchina; vuoto se niente esce.

        Preflight e chat le stampano identiche. Elencano tutto cio' che il
        modello riceve, non solo prompt e risposte.
        """
        conversazione = e_modello_cloud(self.principale)
        estrazione = e_modello_cloud(self.apprendimento)
        if conversazione and estrazione:
            return [
                "Conversazione ed estrazione delle memorie sono cloud: prompt, risposte, file letti, output dei",
                "comandi, memorie e conversazioni rilette escono dalla macchina verso ollama.com.",
                "Solo l'embedding resta locale.",
            ]
        if conversazione:
            return [
                "Il modello conversazionale e' cloud: prompt, risposte, file letti, output dei comandi, memorie",
                "iniettate e conversazioni rilette escono dalla macchina verso ollama.com.",
                "Estrazione delle memorie ed embedding restano locali.",
            ]
        if estrazione:
            return [
                "Il modello di estrazione e' cloud: il testo dei turni e le memorie gia' salvate",
                "escono dalla macchina verso ollama.com. Conversazione ed embedding restano locali.",
            ]
        return []


def leggi_impostazioni() -> Impostazioni:
    """Fotografa i nomi del modulo in un `Impostazioni`, al confine del processo."""
    return Impostazioni(
        principale=MAIN_MODEL,
        apprendimento=LEARNING_MODEL,
        embedder=EMBEDDER_MODEL,
        embedder_dimensioni=EMBEDDER_DIMENSIONS,
        host=OLLAMA_HOST,
        keep_alive=KEEP_ALIVE,
        num_ctx=NUM_CTX,
        temperatura=TEMPERATURE,
        temperatura_apprendimento=TEMPERATURE_ESTRAZIONE,
        think=MAIN_THINK,
        think_apprendimento=LEARNING_THINK,
        top_p=TOP_P,
        top_k=TOP_K,
        min_p=MIN_P,
        repeat_penalty=REPEAT_PENALTY,
        presence_penalty=PRESENCE_PENALTY,
        vincolo_estrazione=ESTRAZIONE_VINCOLATA,
    )


# Quando l'estrazione del contesto non scrive niente - il modello non chiama
# `save_session_context`, o lo chiama con argomenti non validi - si ripete,
# questo numero di volte; zero la disattiva.
SESSION_CONTEXT_RETRIES = 1

# ---------------------------------------------------------------------------
# Apprendimento
# ---------------------------------------------------------------------------

# Ogni store ALWAYS costa una chiamata a LEARNING_MODEL dopo ogni risposta,
# in sequenza: disattiva quello che non ti serve. Gli AGENTIC costano solo
# quando il modello usa i loro strumenti.
LEARN_USER_PROFILE = True  # ALWAYS - chi sei, come preferisci le risposte
LEARN_USER_MEMORY = True  # ALWAYS - osservazioni non strutturate su di te
LEARN_SESSION_CONTEXT = True  # ALWAYS - obiettivo, piano, avanzamento
LEARN_ENTITIES = True  # AGENTIC - persone, progetti (nessun costo fisso)
LEARN_KNOWLEDGE = True  # AGENTIC - intuizioni riutilizzabili

# Tetto di scritture per singola estrazione. Il default di Agno e' 10.
MAX_UPDATES_PER_RUN = 5

# Da' al modello `update_user_memory` per correggere o cancellare memorie su
# richiesta; senza, una memoria sbagliata si toglie solo aprendo SQLite.
# Svuotare tutto resta impossibile.
MEMORY_AGENT_TOOLS = True

# ---------------------------------------------------------------------------
# Cronologia e sessioni passate
# ---------------------------------------------------------------------------

# Strumenti con cui il modello rilegge altre sessioni o questa oltre la
# finestra.
SEARCH_PAST_SESSIONS = True  # elenca le sessioni passate e ne rilegge una
READ_CHAT_HISTORY = True  # rilegge questa sessione oltre NUM_HISTORY_RUNS

# Quante sessioni elenca `search_past_sessions` e quanti scambi per ognuna.
# I default di Agno (20 e 3) costano circa 6k token per chiamata; questi
# meno di 1,5k.
PAST_SESSIONS_LIMIT = 6
PAST_SESSION_RUNS_PREVIEW = 2

# Quante sessioni mostra `/sessioni` (vincolo del terminale, non del
# modello); le altre vengono contate.
SESSIONI_ELENCO = 20

# Quante conversazioni recenti della stessa cartella entrano nel prompt (id,
# data, prima domanda), cosi' "dove eravamo rimasti" funziona anche senza
# `ares resume`. Poche, perche' stanno in ogni turno.
SESSIONI_RECENTI_NEL_PROMPT = 5

# Quante entita' chiedere allo store per `/entita <testo>`. La ricerca di
# Agno confronta anche namespace e date, quindi `stores.leggi_entita` filtra
# dopo: serve una finestra piu' larga di cio' che si mostra.
ENTITA_FINESTRA_RICERCA = 200

# Quanti turni di questa sessione restano nel contesto senza chiedere nulla.
NUM_HISTORY_RUNS = 5

# Delle chiamate a strumenti nella cronologia si reiniettano solo le piu'
# recenti, perche' un turno che legge molti file non li trascini nei
# successivi. Vale per il prompt, non per l'archivio.
MAX_TOOL_CALLS_FROM_HISTORY = 10

# I risultati di strumenti oltre la soglia finiscono nel FileSystem di Agno,
# e il modello riceve un'anteprima piu' `read_result` e `search_result` per
# leggerli a pagine. Payload e indice sono negli snapshot. Oltre le quote di
# Agno si conservano solo testa e coda.
OFFLOAD_TOOL_RESULTS = True
TOOL_RESULT_THRESHOLD_CHARS = 16_000

# Default della CLI di retention, che propone le sessioni inattive da
# eliminare per intero (offload compresi). Non parte mai da sola.
SESSION_RETENTION_DAYS = 180

# Sessioni escluse dal prune per eta'; restano cancellabili una per una.
# `principale` e' la sessione senza cartella di lavoro.
SESSIONI_PROTETTE = ("principale",)

# ---------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------

# Il fuso della data nel prompt e dell'orologio (`prompts.riga_della_data`, `prompts.data_e_ora`).
FUSO_ORARIO = "Europe/Rome"

# Mostra al modello la data di ogni memoria (`AresMemories` in schemas.py).
# Cambia solo il rendering: spegnerlo non tocca l'archivio.
DATE_MEMORIE = True

# ---------------------------------------------------------------------------
# Metriche del turno
# ---------------------------------------------------------------------------

# Una riga sotto ogni risposta: finestra occupata, costo del turno e quota
# dell'apprendimento, dai conteggi che Ollama restituisce gia'. Spenta di
# default; `ares --metriche` la accende per una sessione.
MOSTRA_METRICHE = False

# ---------------------------------------------------------------------------
# Esito degli strumenti
# ---------------------------------------------------------------------------

# Mostra come e' finito ogni strumento, non solo che e' partito: senza,
# l'utente non vede gli errori su cui il modello costruisce la risposta.
MOSTRA_ESITO_STRUMENTI = True

# Quanto esito mostrare. Si tronca qui, mai nelle richieste di conferma,
# dove si autorizza e ogni riga puo' contare.
ESITO_RIGHE = 3
ESITO_LARGHEZZA = 100

# ---------------------------------------------------------------------------
# Eco di cio' che entra in memoria
# ---------------------------------------------------------------------------

# Mostra sotto la risposta cosa e' cambiato in profilo e memorie, per
# intero, e gli argomenti degli strumenti di memoria. Cio' che entra torna in
# ogni sessione futura, e un file o un output con dentro un'istruzione puo'
# lasciarvi traccia: l'eco lo rende visibile subito. Vedi `agent/echo.py`.
MOSTRA_APPRENDIMENTI = True

# Con l'eco acceso, dopo un turno che ha scritto in profilo o memorie la CLI
# chiede se tenere: `n` riporta entrambi a prima del turno, tutto o niente.
# E' una conferma a posteriori, con i limiti descritti in `agent/echo.py`.
CONFERMA_APPRENDIMENTI = True

# ---------------------------------------------------------------------------
# Percorsi
# ---------------------------------------------------------------------------

# Tutto cio' che Ares impara e conserva vive in `~/.ares`, indipendente dal
# clone. `ARES_HOME` lo sposta in blocco; `ARES_TMP` e `ARES_BACKUP_DIR`
# spostano stato e backup da soli (le prove li usano per un archivio
# usa-e-getta).


@dataclass(frozen=True)
class Percorsi:
    """Dove sta lo stato, dove i backup e dove si lavora. L'identita' e' a parte, in `Utente`."""

    home: Path
    stato: Path
    backup: Path
    lavoro: Path

    # Nome storico, ma fa parte del formato degli snapshot (`DATABASE` in
    # backup/integrity.py): cambiarlo richiede un nuovo FORMATO_BACKUP.
    @property
    def db_file(self) -> str:
        return str(self.stato / "kairos.db")

    @property
    def fs_db_file(self) -> str:
        return str(self.stato / "filesystem.db")

    # Fuori dallo stato: sono file della persona, che li scrive o li adotta,
    # e un restore non deve riavvolgerli.
    @property
    def skill(self) -> Path:
        return self.home / "skills"

    @property
    def lancedb_uri(self) -> str:
        return str(self.stato / "lancedb")

    # Lock fratello della directory dello stato, non al suo interno: il
    # restore sostituisce l'intera directory e il file che coordina
    # l'operazione deve restare fermo.
    @property
    def lock_file(self) -> Path:
        return self.stato.with_name(self.stato.name + ".lock")

    # Nello stato, quindi negli snapshot, ma un restore non la riavvolge.
    # `CronologiaSicura` la crea a 0600 su POSIX.
    @property
    def cronologia_file(self) -> Path:
        return self.stato / "cronologia_chat.txt"


def leggi_percorsi(ambiente: Mapping[str, str] | None = None, cwd: Path | None = None) -> Percorsi:
    """I percorsi di questo avvio, dall'ambiente dato o da `AMBIENTE`.

    La directory corrente si risolve subito (su Windows puo' arrivare con i
    nomi corti, `RUNNER~1`); se e' stata cancellata si ripiega sulla home,
    che `cli/cartella.py` fermera' con un avviso.
    """
    env: Mapping[str, str] = AMBIENTE if ambiente is None else ambiente
    home = Path(env.get("ARES_HOME") or Path.home() / ".ares")
    if cwd is None:
        try:
            cwd = Path(os.getcwd()).resolve()
        except FileNotFoundError:
            cwd = Path.home()
    return Percorsi(
        home=home,
        stato=Path(env.get("ARES_TMP") or home / "stato"),
        backup=Path(env.get("ARES_BACKUP_DIR") or home / "backup"),
        lavoro=cwd,
    )


# Le posizioni dello stato nelle versioni vecchie, lette da
# `ops/migrazione.py`. Finche' li' ci sono dati e nel posto nuovo no, la
# chat si ferma per non sdoppiare lo stato.
VECCHIO_TMP_DIR = BASE_DIR / "tmp"
VECCHIO_BACKUP_DIR = BASE_DIR.parent / "ares-backup"


def prepara_archivio(percorsi: Percorsi) -> Path:
    """Crea la directory dello stato, privata, e restituisce il percorso.

    Idempotente: la chiama chiunque apra l'archivio, dopo aver letto gli
    argomenti (cosi' `--help` non crea niente). Rende privata la directory e
    non i file, perche' e' la directory il confine che regge; su Windows non
    fa nulla e vale la DACL ereditata.
    """
    percorsi.stato.mkdir(parents=True, exist_ok=True)
    rendi_privato(percorsi.stato)
    # Anche `~/.ares`, quando lo stato ci sta dentro: e' la directory che si
    # attraversa per arrivare a tutto il resto, backup compresi.
    if percorsi.stato.parent == percorsi.home:
        rendi_privato(percorsi.home)
    return percorsi.stato


# Snapshot da tenere, solo come default di `ares backup prune`, che mostra
# sempre i candidati e chiede conferma (salvo --yes).
BACKUP_KEEP = 20

# Dopo quanti giorni la chat ricorda all'avvio che manca un backup; zero lo
# spegne. Il backup resta manuale: automatico all'uscita costerebbe secondi a
# ogni sessione e potrebbe fallire senza nessuno a guardare.
BACKUP_PROMEMORIA_GIORNI = 7

# ---------------------------------------------------------------------------
# Cronologia della riga di comando
# ---------------------------------------------------------------------------

# Voci massime della cronologia (`Percorsi.cronologia_file`), applicate
# atomicamente a ogni nuova voce tenendo le piu' recenti.
CRONOLOGIA_RIGHE = 2000

# ---------------------------------------------------------------------------
# Spazio di lavoro sul disco
# ---------------------------------------------------------------------------

# Lo spazio di lavoro e' la cartella da cui si lancia `ares` (o
# `--workspace`), vagliata da `cli/cartella.py`: radice, home, directory di
# sistema o con lo stato di Ares chiedono una conferma scritta.
#
# E' un confine sui percorsi, non una sandbox: gli strumenti sui file non
# escono, ma `run_command` gira sulla macchina vera e puo' uscire, leggere
# l'ambiente e usare la rete. Lo regge la conferma umana, per questo la
# shell e' fra le azioni da confermare (tranne in `auto`).
WORKSPACE = True

# Le regole del progetto: se il file c'e' nella cartella, entra nel prompt,
# troncato al tetto. `ares init` ne scrive uno scheletro.
WORKSPACE_ISTRUZIONI = "ARES.md"
WORKSPACE_ISTRUZIONI_MAX_BYTE = 32_000

# Necessario: il quaderno (FileSystem) ha gia' `read_file`, `write_file`...
# e senza prefisso Agno scarterebbe gli strumenti omonimi del Workspace.
WORKSPACE_PREFIX = "workspace_"

# Anche il quaderno ha il suo: con un `read_file` nudo un modello piccolo
# legge dal quaderno un file della cartella (docs/conversation-eval.md).
QUADERNO_PREFIX = "quaderno_"

# Le modalita': per ognuna, gli strumenti che girano in silenzio e quelli
# che chiedono conferma; gli altri il modello non li vede. Anche il prompt
# che le descrive si genera da qui.
#
#   manuale    legge in silenzio; tutto cio' che lascia traccia chiede
#              conferma, perche' cio' che il modello legge puo' contenere
#              istruzioni e una scrittura non vista puo' riscrivere uno
#              script che eseguira' domani.
#   modifiche  scrive e modifica in silenzio; sposta, cancella ed esegue
#              con conferma.
#   piano      sola lettura, e il prompt chiede di proporre invece di fare.
#   auto       nessuna conferma. Solo con `ares --modo auto`, mai come
#              default e mai con `-p` (che rifiuta anche `modifiche`).
#
# `/modo` cambia modalita' ricostruendo l'agente sulla stessa sessione.
Modo = Literal["manuale", "modifiche", "piano", "auto"]
MODALITA: dict[str, tuple[list[str], list[str]]] = {
    "manuale": (["read", "list", "search"], ["write", "edit", "move", "delete", "shell"]),
    "modifiche": (["read", "list", "search", "write", "edit"], ["move", "delete", "shell"]),
    "piano": (["read", "list", "search"], []),
    "auto": (["read", "list", "search", "write", "edit", "move", "delete", "shell"], []),
}
MODO_PREDEFINITO: Modo = "manuale"


def liste_modalita(modo: str) -> tuple[list[str], list[str]]:
    """Le due liste - silenziosi, con conferma - della modalita', o ValueError con i nomi validi."""
    if modo not in MODALITA:
        raise ValueError("modalita' sconosciuta: " + modo + ". Valide: " + ", ".join(MODALITA))
    return MODALITA[modo]


# Gli strumenti che lasciano traccia o eseguono: quelli che `manuale` mette
# sotto conferma. `ares -p` rifiuta le modalita' che ne lasciano uno in
# silenzio.
STRUMENTI_CON_TRACCIA = frozenset(MODALITA["manuale"][1])


def modalita_scrive_in_silenzio(modo: str) -> bool:
    """Vero se la modalita' scrive, sposta, cancella o esegue senza conferma."""
    silenziosi, _ = liste_modalita(modo)
    return not STRUMENTI_CON_TRACCIA.isdisjoint(silenziosi)


# In ogni modalita', vieta di scrivere su un file esistente non ancora letto
# in questa sessione: il modello non lo riscrive da un contenuto immaginato.
WORKSPACE_READ_BEFORE_WRITE = True

# Le regole di autorizzazione della persona sui comandi (`core/regole.py`):
# prefissi che non chiedono conferma (`consenti`) o non girano mai (`nega`,
# anche in `auto`). Quelle del progetto stanno nella cartella di lavoro,
# quelle personali in `~/.ares`; il nucleo le legge, il modello no.
REGOLE_PROGETTO = ".ares/permessi.toml"
REGOLE_PERSONALI = "permessi.toml"

# Le skill (`agent/skill.py`): quelle del progetto nella cartella di lavoro,
# quelle della persona in `~/.ares/skills`. Le proposte di Ares stanno in una
# sottocartella che non si carica, finche' la persona non le adotta.
SKILL_PROGETTO = ".ares/skills"
SKILL_PROPOSTE = "proposte"

# Quante chiamate a strumenti puo' fare un turno. Oltre, Agno risponde allo
# strumento con un errore e il modello conclude; i rilettori dei risultati
# lunghi (`read_result`, `search_result`) non contano. E' un tetto ai cicli
# di tentativi, non un budget: una richiesta ordinaria ne usa meno di dieci.
TOOL_CALL_LIMIT = 30

# Dopo questi rifiuti consecutivi di una conferma il turno smette di
# riprendere il modello: l'ultimo rifiuto gli dice di rispondere con cio' che
# ha, e se chiede ancora uno strumento il turno finisce li', senza altre
# domande alla persona (`core/autorizzazioni.Arbitro`).
RIFIUTI_CONSECUTIVI = 3


# ---------------------------------------------------------------------------
# Politica della conversazione
# ---------------------------------------------------------------------------
#
# `leggi_politica` fotografa i nomi qui sopra in quattro gruppi. Cosa ne
# resta fuori, e perche', e' in docs/architecture.md ("Configurazione").


@dataclass(frozen=True)
class Apprendimento:
    """Cosa Ares impara, da solo e su richiesta, e con quali limiti.

    `memorie_datate` viene da `DATE_MEMORIE`, `strumenti_memoria` da
    `MEMORY_AGENT_TOOLS`, `su_richiesta` da `ARES_STRUMENTI_SU_RICHIESTA`:
    gli strumenti di entita' e intuizioni arrivano al modello solo dopo che
    li ha attivati. `skill` da `ARES_SKILL`: le procedure scritte o adottate
    dalla persona, lette quando servono.
    """

    profilo: bool
    memorie: bool
    contesto: bool
    entita: bool
    intuizioni: bool
    memorie_datate: bool
    max_aggiornamenti: int
    tentativi_contesto: int
    strumenti_memoria: bool
    su_richiesta: bool = True
    skill: bool = True

    @property
    def automatici(self) -> bool:
        """Vero se almeno uno store si aggiorna dopo ogni risposta, senza strumenti."""
        return self.profilo or self.memorie or self.contesto

    @property
    def agentici(self) -> bool:
        """Vero se almeno uno store si aggiorna solo quando il modello lo decide."""
        return self.entita or self.intuizioni


@dataclass(frozen=True)
class Cronologia:
    """Quanto contesto storico entra in vista, e con quali strumenti si rilegge il resto.

    `sessioni_passate` e `cronologia_chat` accendono gli strumenti; i numeri
    dicono quanto il modello riceve senza chiedere.
    """

    sessioni_passate: bool
    cronologia_chat: bool
    turni: int
    strumenti_dalla_cronologia: int
    sessioni_ricerca: int
    sessioni_anteprima: int
    sessioni_nel_prompt: int


@dataclass(frozen=True)
class Workspace:
    """Come Ares lavora nella cartella; la cartella stessa e' `Percorsi.lavoro`."""

    attivo: bool
    istruzioni: str
    istruzioni_max_byte: int
    prefisso: str
    leggi_prima_di_scrivere: bool
    # I file delle regole di autorizzazione: relativo alla cartella di lavoro
    # il primo, alla home di Ares il secondo.
    regole_progetto: str = ".ares/permessi.toml"
    regole_personali: str = "permessi.toml"
    # La sandbox dei comandi: `None` o `"bwrap"`, e se ha la rete.
    sandbox: str | None = None
    sandbox_rete: bool = False


@dataclass(frozen=True)
class Mostra:
    """Cosa la persona vede del turno, e cosa le viene chiesto.

    `apprendimenti` e `conferma_apprendimenti` finiscono anche nel prompt,
    che dice al modello se la persona vede e puo' rifiutare cio' che impara.
    """

    metriche: bool
    esito_strumenti: bool
    esito_righe: int
    esito_larghezza: int
    apprendimenti: bool
    conferma_apprendimenti: bool
    sessioni: int


@dataclass(frozen=True)
class Politica:
    """Cosa una conversazione impara, vede, mostra e come lavora."""

    apprendimento: Apprendimento
    cronologia: Cronologia
    workspace: Workspace
    mostra: Mostra


def leggi_politica() -> Politica:
    """Fotografa i nomi del modulo in una `Politica`, al confine del processo.

    Per una variante si usa `dataclasses.replace` sul risultato.
    """
    return Politica(
        apprendimento=Apprendimento(
            profilo=LEARN_USER_PROFILE,
            memorie=LEARN_USER_MEMORY,
            contesto=LEARN_SESSION_CONTEXT,
            entita=LEARN_ENTITIES,
            intuizioni=LEARN_KNOWLEDGE,
            memorie_datate=DATE_MEMORIE,
            max_aggiornamenti=MAX_UPDATES_PER_RUN,
            tentativi_contesto=SESSION_CONTEXT_RETRIES,
            strumenti_memoria=MEMORY_AGENT_TOOLS,
            su_richiesta=SU_RICHIESTA is not False,
            skill=SKILL is not False,
        ),
        cronologia=Cronologia(
            sessioni_passate=SEARCH_PAST_SESSIONS,
            cronologia_chat=READ_CHAT_HISTORY,
            turni=NUM_HISTORY_RUNS,
            strumenti_dalla_cronologia=MAX_TOOL_CALLS_FROM_HISTORY,
            sessioni_ricerca=PAST_SESSIONS_LIMIT,
            sessioni_anteprima=PAST_SESSION_RUNS_PREVIEW,
            sessioni_nel_prompt=SESSIONI_RECENTI_NEL_PROMPT,
        ),
        workspace=Workspace(
            attivo=WORKSPACE,
            istruzioni=WORKSPACE_ISTRUZIONI,
            istruzioni_max_byte=WORKSPACE_ISTRUZIONI_MAX_BYTE,
            prefisso=WORKSPACE_PREFIX,
            leggi_prima_di_scrivere=WORKSPACE_READ_BEFORE_WRITE,
            regole_progetto=REGOLE_PROGETTO,
            regole_personali=REGOLE_PERSONALI,
            sandbox=SANDBOX,
            sandbox_rete=SANDBOX_RETE,
        ),
        mostra=Mostra(
            metriche=MOSTRA_METRICHE,
            esito_strumenti=MOSTRA_ESITO_STRUMENTI,
            esito_righe=ESITO_RIGHE,
            esito_larghezza=ESITO_LARGHEZZA,
            apprendimenti=MOSTRA_APPRENDIMENTI,
            conferma_apprendimenti=CONFERMA_APPRENDIMENTI,
            sessioni=SESSIONI_ELENCO,
        ),
    )


# ---------------------------------------------------------------------------
# Identita'
# ---------------------------------------------------------------------------

# L'utente quando non si passa `--user`. Va normalizzato con
# `Utente.da_grezzo`, che puo' rifiutarlo.
DEFAULT_USER_ID: str = AMBIENTE.get("ARES_USER_ID", "default")


def comando_ares(*parole: str) -> str:
    """Il comando `ares` come lo si scrive da fuori dal venv, per i messaggi di rimedio.

    `comando_ares("backup", "restore", nome)` da' `ares backup restore <nome>`
    se `ares` e' sul PATH, altrimenti `.venv/bin/ares ...` (o
    `.venv\\Scripts\\ares ...` su Windows).
    """
    nel_venv = r".venv\Scripts\ares" if os.name == "nt" else ".venv/bin/ares"
    eseguibile = "ares" if shutil.which("ares") else nel_venv
    return " ".join((eseguibile, *parole))
