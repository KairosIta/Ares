"""
Lock cooperativo dello stato di Ares
====================================

La chat mantiene un lock condiviso per tutta la propria vita. Backup e restore
chiedono quello esclusivo: se Ares e' aperto si fermano invece di copiare i due
SQLite e LanceDB in istanti diversi.

E' un lock cooperativo, non una sandbox. Protegge i percorsi ufficiali del
progetto; uno script che scrive direttamente in tmp/ senza usarlo resta fuori
dal contratto.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ares import config
from ares.state.platform_files import FileOccupato, lock_file


class StatoOccupato(RuntimeError):
    """Un altro processo sta usando lo stato con un lock incompatibile."""


@contextmanager
def lock_stato(
    esclusivo: bool,
    bloccante: bool = False,
    percorso: Path = config.STATE_LOCK_FILE,
) -> Iterator[None]:
    """Acquisisce il lock condiviso o esclusivo e lo rilascia sempre."""
    try:
        with lock_file(
            Path(percorso),
            esclusivo=esclusivo,
            bloccante=bloccante,
        ):
            yield
    except FileOccupato as errore:
        tipo = "esclusivo" if esclusivo else "condiviso"
        raise StatoOccupato("lo stato di Ares e' in uso: impossibile acquisire il lock " + tipo) from errore
