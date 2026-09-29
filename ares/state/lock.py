"""Lock cooperativi dello stato di Ares.

La chat tiene un lock condiviso per tutta la sua vita; backup e restore
chiedono quello esclusivo, cosi' non copiano gli archivi mentre Ares scrive.
Un lock per utente serializza inoltre i turni fra chat. Sono cooperativi,
non una sandbox: proteggono solo chi li usa.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path

from ares.config import Percorsi
from ares.state.identita import Utente
from ares.state.platform_files import FileOccupato, lock_file


class StatoOccupato(RuntimeError):
    """Un altro processo sta usando lo stato con un lock incompatibile."""


@contextmanager
def lock_turno(percorsi: Percorsi, utente: Utente) -> Iterator[None]:
    """Un turno per utente, dall'istantanea fino all'eventuale ripristino.

    Esclusivo e non bloccante: chi trova un turno attivo riceve
    `StatoOccupato`. Il nome del file e' un hash dell'id, per non esporre
    l'identita'; il file resta sul disco, perche' rimuoverlo separerebbe i lock
    su inode diversi.
    """
    chiave = sha256(utente.id.encode("utf-8")).hexdigest()
    lock = percorsi.lock_file
    percorso = lock.with_name(lock.name + ".utente-" + chiave)
    try:
        with lock_file(percorso, esclusivo=True, bloccante=False):
            yield
    except FileOccupato as errore:
        raise StatoOccupato(
            "un'altra chat di questo utente ha un turno in corso; riprova quando ha concluso anche le conferme"
        ) from errore


@contextmanager
def lock_stato(
    percorso: Path,
    *,
    esclusivo: bool,
    bloccante: bool = False,
) -> Iterator[None]:
    """Acquisisce il lock condiviso o esclusivo sul file dato e lo rilascia sempre.

    Il file (di norma `percorsi.lock_file`) non ha default: `ops/migrazione.py`
    ne usa due.
    """
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
