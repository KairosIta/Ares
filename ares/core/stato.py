"""Lo stato di Ares in uso da un client o da una manutenzione.

Chi apre sessioni o fa turni tiene il lock condiviso dello stato per tutta la
sua vita: piu' client convivono. Backup, restore e le manutenzioni che
scrivono tengono quello esclusivo, e aspettano che nessuno lo usi. Lo stato
rimasto nel posto delle versioni vecchie ferma entrambi: aprirne uno vuoto
accanto, o installarne uno con un restore, sdoppierebbe l'archivio. Solo
`ares migrate` (`ops/migrazione.py`) lo tocca, con i suoi lock.

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


def verifica_posto(percorsi: Percorsi) -> None:
    """Solleva `StatoDaMigrare` se stato o backup sono ancora nel posto di prima.

    Senza lock la risposta puo' cambiare subito dopo: serve a chi deve solo
    dire perche' non trova niente, senza creare il file del lock.
    """
    da_spostare = parti(percorsi)
    if da_spostare:
        raise StatoDaMigrare(da_spostare)


@contextmanager
def stato_in_uso(percorsi: Percorsi) -> Iterator[None]:
    """Tiene il lock condiviso dello stato finche' il client lo usa.

    Solleva `StatoOccupato` se un'operazione esclusiva e' in corso e
    `StatoDaMigrare` se lo stato e' ancora nel posto di prima. Il controllo
    sta dentro il lock: la migrazione prende quello esclusivo, quindi la
    risposta non cambia mentre il client lavora.
    """
    with lock_stato(percorsi.lock_file, esclusivo=False):
        verifica_posto(percorsi)
        yield


@contextmanager
def stato_esclusivo(percorsi: Percorsi) -> Iterator[None]:
    """Tiene il lock esclusivo dello stato, per una manutenzione che scrive.

    Solleva `StatoOccupato` se un client o un'altra manutenzione usa lo stato
    e `StatoDaMigrare` come `stato_in_uso`. Non si annida: dentro, snapshot e
    restore si chiamano con `acquisisci_lock=False`.
    """
    with lock_stato(percorsi.lock_file, esclusivo=True):
        verifica_posto(percorsi)
        yield
