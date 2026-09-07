"""Componenti runtime dell'assistente: modelli, indice vettoriale e strumenti locali."""

from pathlib import Path

from agno.knowledge.embedder.ollama import OllamaEmbedder
from agno.knowledge.knowledge import Knowledge
from agno.models.ollama import Ollama
from agno.tools.workspace import Workspace
from agno.vectordb.lancedb import LanceDb
from agno.vectordb.search import SearchType

from ares import config
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
    "build_result_store",
    "build_workspace",
]


def _esigi_locale(nome: str, ruolo: str) -> str:
    """Rifiuta un modello cloud per un ruolo che deve restare sulla macchina.

    Conversazione ed estrazione delle memorie accettano un modello cloud,
    ciascuna per scelta esplicita nel `.env`. L'embedder no: indicizza le
    intuizioni gia' scritte in LanceDB, e cambiarlo invaliderebbe l'indice.
    Un errore all'avvio e' meglio di un turno che le spedisce fuori in
    silenzio.
    """
    if config.e_modello_cloud(nome):
        raise ValueError(ruolo + " non puo' usare un modello cloud (" + nome + "): resta locale sempre.")
    return nome


def build_chat_model() -> Ollama:
    """Modello conversazionale, con il contesto esteso oltre il default di Ollama.

    Locale o cloud secondo `config.MAIN_MODEL`; l'host resta comunque
    `config.OLLAMA_HOST`, perche' e' il daemon a inoltrare i modelli cloud.
    """
    return Ollama(
        id=config.MAIN_MODEL,
        host=config.OLLAMA_HOST,
        options=config.OLLAMA_OPTIONS,
        keep_alive=config.KEEP_ALIVE,
        # `think` non e' una option di Ollama ma un parametro top-level
        # dell'API, e Agno non lo espone: request_params viene fuso nei
        # kwargs di ogni chiamata al client, streaming compreso.
        request_params={"think": config.MAIN_THINK},
    )


def build_learning_model() -> Ollama:
    """Modello a bassa temperatura usato per l'estrazione strutturata.

    Locale o cloud secondo `config.LEARNING_MODEL`, come la conversazione:
    e' l'utente a decidere nel `.env` a chi affidare cio' che Ares ricorda.
    """
    return Ollama(
        id=config.LEARNING_MODEL,
        host=config.OLLAMA_HOST,
        options=config.LEARNING_OPTIONS,
        keep_alive=config.KEEP_ALIVE,
        request_params={"think": config.LEARNING_THINK},
    )


def build_knowledge() -> Knowledge:
    """Indice vettoriale locale delle intuizioni apprese."""
    config.prepara_archivio()
    indice = Path(config.LANCEDB_URI)
    indice.mkdir(parents=True, exist_ok=True)
    rendi_privato(indice)
    return Knowledge(
        vector_db=LanceDb(
            uri=config.LANCEDB_URI,
            table_name="learned_knowledge",
            search_type=SearchType.hybrid,
            embedder=OllamaEmbedder(
                id=_esigi_locale(config.EMBEDDER_MODEL, "EMBEDDER_MODEL"),
                host=config.OLLAMA_HOST,
                dimensions=config.EMBEDDER_DIMENSIONS,
            ),
        ),
    )


class AresWorkspace(Workspace):
    """Workspace Agno con nomi distinti dagli strumenti del quaderno."""

    def __init__(self, root, prefisso: str, **kwargs):
        super().__init__(root, **kwargs)

        for elenco in (self.functions, self.async_functions):
            for nome in list(elenco):
                funzione = elenco.pop(nome)
                funzione.name = prefisso + nome
                elenco[funzione.name] = funzione
        self.requires_confirmation_tools = [prefisso + nome for nome in self.requires_confirmation_tools]

        # L'istruzione predefinita nomina gli strumenti prima della rinomina.
        # Il prompt italiano e coerente viene composto da assistant_prompts.
        self.instructions = None
        self.add_instructions = False


def build_workspace(modo: str | None = None) -> AresWorkspace:
    """Costruisce lo spazio di lavoro sulla cartella scelta all'avvio, nella modalita' data.

    `modo` vuoto vale `config.MODO_PREDEFINITO`, letto adesso e non alla
    definizione della funzione: un default nella firma fotografa il valore
    all'import, e una prova che lo cambia con `patch.object` non lo vedrebbe.

    La cartella e' quella dell'utente, decisa e autorizzata da
    `cli/cartella.py` prima di arrivare qui: i rischi - la home, il disco
    intero, lo stato di Ares dentro - li dice quel modulo e li conferma
    l'utente. Qui si pretende soltanto che esista: crearla vorrebbe dire
    lavorare in una directory vuota nata da un refuso.
    """
    radice = config.WORKSPACE_DIR.resolve()
    if not radice.is_dir():
        raise ValueError("La cartella di lavoro " + str(radice) + " non esiste.")
    silenziosi, confermati = config.liste_modalita(modo or config.MODO_PREDEFINITO)
    return AresWorkspace(
        radice,
        prefisso=config.WORKSPACE_PREFIX,
        allowed=silenziosi,
        confirm=confermati,
        require_read_before_write=config.WORKSPACE_READ_BEFORE_WRITE,
    )
