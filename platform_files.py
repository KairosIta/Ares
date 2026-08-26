"""Primitive di filesystem che cambiano fra POSIX e Windows.

I lock sono cooperativi e conservano la stessa semantica su entrambe le
famiglie di sistemi: piu' lettori condivisi oppure un solo scrittore
esclusivo. I permessi numerici sono invece una proprieta' POSIX; su Windows i
file mantengono la DACL ereditata dalla directory che li contiene, perche'
``chmod`` li renderebbe soltanto read-only senza limitarne la lettura.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Iterator

import portalocker


class FileOccupato(RuntimeError):
    """Un lock cooperativo incompatibile e' gia' attivo sul file."""


def rendi_privato(percorso: Path) -> None:
    """Applica i bit privati POSIX senza simulare ACL inesistenti su Windows."""
    percorso = Path(percorso)
    if os.name == "posix":
        os.chmod(percorso, 0o700 if percorso.is_dir() else 0o600)


@contextmanager
def lock_file(
    percorso: Path,
    *,
    esclusivo: bool,
    bloccante: bool,
) -> Iterator[BinaryIO]:
    """Apre e blocca un file, traducendo la sola contesa in ``FileOccupato``."""
    percorso = Path(percorso)
    percorso.parent.mkdir(parents=True, exist_ok=True)
    file_lock = percorso.open("a+b")
    acquisito = False
    try:
        rendi_privato(percorso)
        operazione = (
            portalocker.LockFlags.EXCLUSIVE
            if esclusivo
            else portalocker.LockFlags.SHARED
        )
        if not bloccante:
            operazione |= portalocker.LockFlags.NON_BLOCKING
        try:
            portalocker.lock(file_lock, operazione)
        except portalocker.AlreadyLocked as errore:
            raise FileOccupato(str(percorso)) from errore
        except portalocker.LockException as errore:
            # Il vecchio backend fcntl esponeva i guasti del filesystem come
            # OSError. Conservare quel contratto permette alla cronologia di
            # degradare in memoria invece di impedire l'avvio della chat.
            raise OSError("lock non disponibile: " + str(percorso)) from errore
        acquisito = True
        yield file_lock
    finally:
        try:
            if acquisito:
                try:
                    portalocker.unlock(file_lock)
                except portalocker.LockException as errore:
                    raise OSError("rilascio del lock fallito: " + str(percorso)) from errore
        finally:
            file_lock.close()
