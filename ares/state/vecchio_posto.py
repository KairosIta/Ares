"""Lo stato ancora nel posto delle versioni vecchie, riconosciuto senza toccarlo.

Le versioni precedenti tenevano lo stato in `tmp/` nel clone e gli snapshot
in `ares-backup`. Qui si dice soltanto cosa e' rimasto li'; lo sposta
`ares migrate` (`ops/migrazione.py`), e finche' non l'ha fatto il nucleo non
apre lo stato (`core/stato.py`).
"""

from pathlib import Path

from ares import config
from ares.config import Percorsi


def _pieno(percorso: Path) -> bool:
    try:
        return percorso.is_dir() and any(percorso.iterdir())
    except OSError:
        return False


def _diversi(a: Path, b: Path) -> bool:
    try:
        return a.resolve() != b.resolve()
    except OSError:
        return a != b


def _coppie(percorsi: Percorsi) -> tuple[tuple[str, Path, Path], ...]:
    return (
        ("lo stato", Path(config.VECCHIO_TMP_DIR), percorsi.stato),
        ("i backup", Path(config.VECCHIO_BACKUP_DIR), percorsi.backup),
    )


def parti(percorsi: Percorsi) -> list[tuple[str, Path, Path]]:
    """(cosa, vecchio, nuovo) per ogni parte ancora nel posto di prima, con il nuovo vuoto o assente."""
    return [(c, v, n) for c, v, n in _coppie(percorsi) if _pieno(v) and _diversi(v, n) and not _pieno(n)]


def conflitti(percorsi: Percorsi) -> list[tuple[str, Path, Path]]:
    """Le parti in cui vecchio e nuovo sono entrambi pieni: qui non si tocca niente."""
    return [(c, v, n) for c, v, n in _coppie(percorsi) if _pieno(v) and _diversi(v, n) and _pieno(n)]
