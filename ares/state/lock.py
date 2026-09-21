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

from ares import config
from ares.state.identita import utente_canonico
from ares.state.platform_files import FileOccupato, lock_file


class StatoOccupato(RuntimeError):
    """Un altro processo sta usando lo stato con un lock incompatibile."""


@contextmanager
def lock_turno(user_id: str) -> Iterator[None]:
    """Un turno per utente, dall'istantanea fino all'eventuale ripristino.

    Il lock condiviso dello stato resta esterno e impedisce la manutenzione.
    Questo lock esclusivo coordina invece le chat fra loro, anche con eco
    spento o in pipe. Non attende: chi trova un turno attivo puo' riprovare.
    Il nome e' un hash per non esporre l'identita' o usarla come percorso.
    Il file resta sul disco: rimuoverlo separerebbe i lock su inode diversi.

    L'id passa prima da `utente_canonico`: due grafie della stessa persona -
    `Demo` e `demo` - devono contendere lo stesso lock, altrimenti namespace
    dice che sono un utente e qui risultano due, e due chat scrivono lo
    stesso profilo credendo di essere sole.
    """
    chiave = sha256(utente_canonico(user_id).encode("utf-8")).hexdigest()
    percorso = config.STATE_LOCK_FILE.with_name(config.STATE_LOCK_FILE.name + ".utente-" + chiave)
    try:
        with lock_file(percorso, esclusivo=True, bloccante=False):
            yield
    except FileOccupato as errore:
        raise StatoOccupato(
            "un'altra chat di questo utente ha un turno in corso; riprova quando ha concluso anche le conferme"
        ) from errore


@contextmanager
def lock_stato(
    esclusivo: bool,
    bloccante: bool = False,
    percorso: Path | None = None,
) -> Iterator[None]:
    """Acquisisce il lock condiviso o esclusivo e lo rilascia sempre.

    `percorso` vuoto vale `config.STATE_LOCK_FILE`, letto adesso: un default
    nella firma lo fotograferebbe all'import, e una prova che cambia lo stato
    con `patch.object` continuerebbe a bloccare quello vero.
    """
    percorso = Path(config.STATE_LOCK_FILE) if percorso is None else percorso
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
