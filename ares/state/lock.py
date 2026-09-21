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

    Il lock condiviso dello stato resta esterno e impedisce la manutenzione.
    Questo lock esclusivo coordina invece le chat fra loro, anche con eco
    spento o in pipe. Non attende: chi trova un turno attivo puo' riprovare.
    Il nome e' un hash per non esporre l'identita' o usarla come percorso.
    Il file resta sul disco: rimuoverlo separerebbe i lock su inode diversi.

    L'id e' quello canonico del tipo `Utente`: due grafie della stessa
    persona - `Demo` e `demo` - non possono arrivare qui come due utenti,
    perche' non sono due `Utente`. Namespace e lock parlano percio' sempre
    della stessa persona, e due chat non scrivono lo stesso profilo credendo
    di essere sole.
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
    """Acquisisce il lock condiviso o esclusivo e lo rilascia sempre.

    Il file arriva come parametro - di norma `percorsi.lock_file` - e non ha
    un valore predefinito: un default nella firma fotograferebbe l'archivio
    corrente all'import, e chi ne apre un altro continuerebbe a bloccare
    quello vero. `ops/migrazione.py` ne prende due, e li nomina entrambi.
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
