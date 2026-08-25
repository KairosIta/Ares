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

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO

import config


class StatoOccupato(RuntimeError):
    """Un altro processo sta usando lo stato con un lock incompatibile."""


def _apri_lock(percorso: Path) -> TextIO:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    file_lock = percorso.open("a+", encoding="utf-8")
    os.chmod(percorso, 0o600)
    return file_lock


@contextmanager
def lock_stato(
    esclusivo: bool,
    bloccante: bool = False,
    percorso: Path = config.STATE_LOCK_FILE,
) -> Iterator[None]:
    """Acquisisce il lock condiviso o esclusivo e lo rilascia sempre."""
    file_lock = _apri_lock(Path(percorso))
    operazione = fcntl.LOCK_EX if esclusivo else fcntl.LOCK_SH
    if not bloccante:
        operazione |= fcntl.LOCK_NB

    try:
        try:
            fcntl.flock(file_lock.fileno(), operazione)
        except BlockingIOError as errore:
            tipo = "esclusivo" if esclusivo else "condiviso"
            raise StatoOccupato(
                "lo stato di Ares e' in uso: impossibile acquisire il lock " + tipo
            ) from errore
        yield
    finally:
        try:
            fcntl.flock(file_lock.fileno(), fcntl.LOCK_UN)
        finally:
            file_lock.close()
