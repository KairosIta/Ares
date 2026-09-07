"""Lo stato di Ares si sposta da dentro il clone a `~/.ares`, una volta sola.

Prima del comando sul PATH lo stato viveva in `tmp/` dentro il repository e
gli snapshot in `ares-backup` accanto. Ora vivono in `~/.ares/stato` e
`~/.ares/backup`, e chi aggiorna un clone che ha gia' mesi di memorie deve
poterli portare dietro senza rifare niente a mano: `ares migrate` li sposta,
i setup lo chiamano, e la chat si ferma finche' non e' successo, perche'
partire con uno stato vuoto accanto a uno pieno li sdoppierebbe.

E' uno spostamento di directory, non una copia: sullo stesso disco e' una
rinomina, e sotto lock esclusivo, cosi' nessuna chat lo vede a meta'.
"""

import contextlib
import shutil
from collections.abc import Sequence
from pathlib import Path

from ares import config
from ares.cli.comando import nuova_app
from ares.cli.ui import UI
from ares.state.lock import StatoOccupato, lock_stato
from ares.state.platform_files import rendi_privato

app = nuova_app("migrate", "Sposta stato e backup di prima in ~/.ares")


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


def _coppie() -> tuple[tuple[str, Path, Path], ...]:
    return (
        ("lo stato", Path(config.VECCHIO_TMP_DIR), Path(config.TMP_DIR)),
        ("i backup", Path(config.VECCHIO_BACKUP_DIR), Path(config.BACKUP_DIR)),
    )


def parti() -> list[tuple[str, Path, Path]]:
    """(cosa, vecchio, nuovo) per ogni parte ancora nel posto di prima, con il nuovo vuoto o assente."""
    return [(c, v, n) for c, v, n in _coppie() if _pieno(v) and _diversi(v, n) and not _pieno(n)]


def conflitti() -> list[tuple[str, Path, Path]]:
    """Le parti in cui vecchio e nuovo sono entrambi pieni: qui non si tocca niente."""
    return [(c, v, n) for c, v, n in _coppie() if _pieno(v) and _diversi(v, n) and _pieno(n)]


def avviso() -> list[str]:
    """Le righe con cui la chat si ferma quando lo stato e' ancora nel posto di prima. Vuoto se no."""
    da_fare = parti()
    if not da_fare:
        return []
    righe = ["Lo stato di Ares e' ancora dove stava prima, e qui non c'e' niente:"]
    for cosa, vecchio, nuovo in da_fare:
        righe.append("  " + cosa + ": " + str(vecchio) + "  ->  " + str(nuovo))
    righe.append("Spostalo con: " + config.comando_ares("migrate"))
    return righe


@app.default
def migra() -> int:
    """Sposta stato e backup da dentro il clone a ~/.ares, se sono ancora li'.

    Idempotente: dopo la prima volta dice che non c'e' niente da spostare.
    Non tocca una parte quando la destinazione contiene gia' dei dati: lo
    dice, e la decisione resta a chi guarda le due directory.
    """
    for cosa, vecchio, nuovo in conflitti():
        UI.line(
            "Attenzione: " + cosa + " in " + str(vecchio) + " restano li': " + str(nuovo) + " contiene gia' dei dati.",
            style="ares.warning",
        )
    da_fare = parti()
    if not da_fare:
        UI.line("Niente da spostare: lo stato di Ares vive in " + str(config.ARES_HOME) + ".", style="ares.muted")
        return 0

    vecchio_lock = Path(config.VECCHIO_TMP_DIR).with_name(Path(config.VECCHIO_TMP_DIR).name + ".lock")
    try:
        # Tutti e due i lock: quello nuovo, che le chat di questa versione
        # prendono, e quello vecchio accanto a `tmp/`, che una chat ancora
        # aperta dalla versione precedente potrebbe tenere.
        with (
            lock_stato(esclusivo=True, percorso=Path(config.STATE_LOCK_FILE)),
            lock_stato(esclusivo=True, percorso=vecchio_lock),
        ):
            for cosa, vecchio, nuovo in da_fare:
                nuovo.parent.mkdir(parents=True, exist_ok=True)
                if nuovo.parent == Path(config.ARES_HOME):
                    rendi_privato(nuovo.parent)
                if nuovo.is_dir():
                    # Vuota, per costruzione di `parti()`: si toglie perche'
                    # `move` dentro una directory esistente la annidera'.
                    nuovo.rmdir()
                shutil.move(str(vecchio), str(nuovo))
                rendi_privato(nuovo)
                UI.pair("Spostato " + cosa, str(vecchio) + "  ->  " + str(nuovo), style="ares.title")
    except StatoOccupato as errore:
        UI.err("ERRORE: " + str(errore))
        UI.err("Chiudi Ares e riprova.", style="ares.muted")
        return 1
    except OSError as errore:
        UI.err("ERRORE: spostamento fallito: " + str(errore))
        UI.err("Niente e' andato perso: cio' che non si e' mosso e' ancora dov'era.", style="ares.muted")
        return 1
    with contextlib.suppress(OSError):
        vecchio_lock.unlink()
    UI.line("Da ora `ares` legge da " + str(config.ARES_HOME) + ", da qualunque cartella.", style="ares.muted")
    return 0


def main(argomenti: Sequence[str] | None = None) -> int:
    from ares.cli.app import esegui

    return esegui("migrate", argomenti)


if __name__ == "__main__":
    raise SystemExit(main())
