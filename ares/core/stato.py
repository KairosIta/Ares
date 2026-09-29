"""Lo stato di Ares in uso da un client, per tutta la sua vita.

Chi apre sessioni o fa turni tiene il lock condiviso dello stato: piu' client
convivono, mentre backup, restore e migrazione, che chiedono quello
esclusivo, aspettano. Lo stato rimasto nel posto delle versioni vecchie
ferma l'apertura: aprirne uno vuoto accanto sdoppierebbe l'archivio.

Aprire lo stato non scrive niente al suo interno; la directory la prepara
`Sessioni` (`core/session.py`), dopo le domande del client.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ares.config import Percorsi
from ares.state.lock import lock_stato
from ares.state.vecchio_posto import parti


class StatoDaMigrare(RuntimeError):
    """Lo stato e' ancora nel posto di prima: `parti` dice cosa, da dove e verso dove."""

    def __init__(self, parti: list[tuple[str, Path, Path]]) -> None:
        super().__init__("lo stato di Ares e' ancora nel posto di prima: " + ", ".join(p[0] for p in parti))
        self.parti = parti


@contextmanager
def stato_in_uso(percorsi: Percorsi) -> Iterator[None]:
    """Tiene il lock condiviso dello stato finche' il client lo usa.

    Solleva `StatoOccupato` se un'operazione esclusiva e' in corso e
    `StatoDaMigrare` se lo stato e' ancora nel posto di prima. Il controllo
    sta dentro il lock: la migrazione prende quello esclusivo, quindi la
    risposta non cambia mentre il client lavora.
    """
    with lock_stato(percorsi.lock_file, esclusivo=False):
        da_spostare = parti(percorsi)
        if da_spostare:
            raise StatoDaMigrare(da_spostare)
        yield
