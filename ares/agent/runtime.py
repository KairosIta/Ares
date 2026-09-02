"""Componenti runtime dell'assistente: modelli, archivi e strumenti locali."""

from pathlib import Path

from agno.db.sqlite import SqliteDb
from agno.fs import FileSystem
from agno.knowledge.embedder.ollama import OllamaEmbedder
from agno.knowledge.knowledge import Knowledge
from agno.models.ollama import Ollama
from agno.offload.store import ResultStore
from agno.tools.workspace import Workspace
from agno.vectordb.lancedb import LanceDb
from agno.vectordb.search import SearchType

from ares import config
from ares.state.platform_files import rendi_privato
from ares.state.stores import namespace_utente


def _esigi_locale(nome: str, ruolo: str) -> str:
    """Rifiuta un modello cloud per un ruolo che deve restare sulla macchina.

    Il solo ruolo a cui `config.py` consente un modello cloud e' la
    conversazione. Estrazione delle memorie ed embedding ricevono il profilo,
    le osservazioni e le intuizioni dell'utente: un errore all'avvio e' meglio
    di un turno che li spedisce fuori in silenzio.
    """
    if config.e_modello_cloud(nome):
        raise ValueError(ruolo + " non puo' usare un modello cloud (" + nome + "): solo MAIN_MODEL puo'.")
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
    """Modello a bassa temperatura usato per l'estrazione strutturata. Locale sempre."""
    return Ollama(
        id=_esigi_locale(config.LEARNING_MODEL, "LEARNING_MODEL"),
        host=config.OLLAMA_HOST,
        options=config.LEARNING_OPTIONS,
        keep_alive=config.KEEP_ALIVE,
        request_params={"think": config.LEARNING_THINK},
    )


def _archivio_privato(percorso: str) -> str:
    """Crea il file SQLite con permessi privati prima della prima connessione."""
    config.prepara_archivio()
    file_db = Path(percorso)
    file_db.parent.mkdir(parents=True, exist_ok=True)
    if not file_db.exists():
        file_db.touch()
    rendi_privato(file_db)
    return percorso


def _build_sqlite(percorso: str) -> SqliteDb:
    """Costruisce SQLite e materializza subito i pragma persistenti di Agno."""
    db = SqliteDb(db_file=_archivio_privato(percorso))
    # Agno registra WAL sull'evento di connessione, ma il costruttore e'
    # lazy. Senza questa apertura un archivio nuovo resta in DELETE mode fino
    # alla prima lettura e un comando di ispezione finisce per modificarlo.
    with db.db_engine.connect():
        pass
    return db


def build_db() -> SqliteDb:
    """Stato dell'agente: sessioni, profilo, memorie, entita'."""
    return _build_sqlite(config.DB_FILE)


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


def build_filesystem(user_id: str = config.DEFAULT_USER_ID) -> FileSystem:
    """Quaderno privato su SQLite, separato e isolato per utente."""
    return FileSystem(
        _build_sqlite(config.FS_DB_FILE),
        namespace=namespace_utente(user_id),
    )


def build_result_store(filesystem: FileSystem) -> ResultStore:
    """Conserva i risultati grandi nel FileSystem gia' incluso nei backup."""
    return ResultStore(
        fs=filesystem,
        threshold_chars=config.TOOL_RESULT_THRESHOLD_CHARS,
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


def build_workspace() -> AresWorkspace:
    """Costruisce lo spazio di lavoro dopo averne verificato i confini."""
    radice = config.WORKSPACE_DIR.resolve()

    for nome, percorso in (("il progetto", config.BASE_DIR), ("l'archivio", config.TMP_DIR)):
        if percorso.resolve().is_relative_to(radice):
            raise ValueError(
                "WORKSPACE_DIR contiene " + nome + " (" + str(percorso) + "): "
                "scegliere una directory che non lo comprenda."
            )

    radice.mkdir(parents=True, exist_ok=True)
    return AresWorkspace(
        radice,
        prefisso=config.WORKSPACE_PREFIX,
        allowed=config.WORKSPACE_ALLOWED,
        confirm=config.WORKSPACE_CONFIRM,
        require_read_before_write=config.WORKSPACE_READ_BEFORE_WRITE,
    )
