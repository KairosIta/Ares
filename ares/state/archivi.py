"""I due SQLite di Ares e il deposito dei risultati grandi, aperti come vanno aperti.

Stanno in `state/` perche' aprire gli archivi e' di chi legge lo stato:
anche `sessions` li usa senza passare dall'agente.
"""

from pathlib import Path

from agno.db.sqlite import SqliteDb
from agno.fs import FileSystem
from agno.offload.store import ResultStore

from ares import config
from ares.config import Percorsi
from ares.state.identita import Utente
from ares.state.platform_files import rendi_privato
from ares.state.stores import namespace_utente


def _archivio_privato(percorsi: Percorsi, percorso: str) -> str:
    """Crea il file SQLite con permessi privati prima della prima connessione."""
    config.prepara_archivio(percorsi)
    file_db = Path(percorso)
    file_db.parent.mkdir(parents=True, exist_ok=True)
    if not file_db.exists():
        file_db.touch()
    rendi_privato(file_db)
    return percorso


def apri_sqlite(percorsi: Percorsi, percorso: str) -> SqliteDb:
    """Costruisce SQLite e materializza subito i pragma persistenti di Agno."""
    db = SqliteDb(db_file=_archivio_privato(percorsi, percorso))
    # Agno attiva WAL alla prima connessione, ma il costruttore e' lazy:
    # aprendo subito, un comando di ispezione non modifica l'archivio dopo.
    with db.db_engine.connect():
        pass
    return db


def build_db(percorsi: Percorsi) -> SqliteDb:
    """Stato dell'agente: sessioni, profilo, memorie, entita'."""
    return apri_sqlite(percorsi, percorsi.db_file)


def build_filesystem(percorsi: Percorsi, utente: Utente) -> FileSystem:
    """Quaderno privato su SQLite, isolato per utente."""
    return FileSystem(
        apri_sqlite(percorsi, percorsi.fs_db_file),
        namespace=namespace_utente(utente),
    )


def build_result_store(filesystem: FileSystem) -> ResultStore:
    """Conserva i risultati grandi nel FileSystem gia' incluso nei backup."""
    return ResultStore(
        fs=filesystem,
        threshold_chars=config.TOOL_RESULT_THRESHOLD_CHARS,
    )
