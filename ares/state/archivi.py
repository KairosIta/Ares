"""I due SQLite di Ares e il deposito dei risultati grandi, aperti come vanno aperti.

Stavano in `agent/runtime.py`, e `sessions` li importava da li': la
retention delle sessioni finiva per dipendere dall'agente per aprire un
database. Aprire gli archivi e' di chi legge lo stato, e `state/` e' quel
posto; l'agente li usa come tutti gli altri.
"""

from pathlib import Path

from agno.db.sqlite import SqliteDb
from agno.fs import FileSystem
from agno.offload.store import ResultStore

from ares import config
from ares.state.platform_files import rendi_privato
from ares.state.stores import namespace_utente


def _archivio_privato(percorso: str) -> str:
    """Crea il file SQLite con permessi privati prima della prima connessione."""
    config.prepara_archivio()
    file_db = Path(percorso)
    file_db.parent.mkdir(parents=True, exist_ok=True)
    if not file_db.exists():
        file_db.touch()
    rendi_privato(file_db)
    return percorso


def apri_sqlite(percorso: str) -> SqliteDb:
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
    return apri_sqlite(config.DB_FILE)


def build_filesystem(user_id: str | None = None) -> FileSystem:
    """Quaderno privato su SQLite, separato e isolato per utente.

    `user_id` vuoto vale `config.DEFAULT_USER_ID`, letto adesso: un default
    nella firma lo fotograferebbe all'import.
    """
    return FileSystem(
        apri_sqlite(config.FS_DB_FILE),
        namespace=namespace_utente(user_id or config.DEFAULT_USER_ID),
    )


def build_result_store(filesystem: FileSystem) -> ResultStore:
    """Conserva i risultati grandi nel FileSystem gia' incluso nei backup."""
    return ResultStore(
        fs=filesystem,
        threshold_chars=config.TOOL_RESULT_THRESHOLD_CHARS,
    )
