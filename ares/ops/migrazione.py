"""Lo stato di Ares si sposta da dentro il clone a `~/.ares`, una volta sola.

Prima del comando sul PATH lo stato viveva in `tmp/` dentro il repository e
gli snapshot in `ares-backup` accanto. Ora vivono in `~/.ares/stato` e
`~/.ares/backup`, e chi aggiorna un clone che ha gia' mesi di memorie deve
poterli portare dietro senza rifare niente a mano: `ares migrate` li sposta,
i setup lo chiamano, e la chat si ferma finche' non e' successo, perche'
partire con uno stato vuoto accanto a uno pieno li sdoppierebbe.

E' uno spostamento di directory sotto lock esclusivo: sullo stesso filesystem
e' una rinomina, e su un filesystem diverso una copia in una sorella
temporanea che solo la rinomina rende visibile, cosi' nessuna chat lo vede a
meta'.
"""

import contextlib
import errno
import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from ares import config
from ares.cli.comando import ESITO_GUASTO, ESITO_OCCUPATO, nuova_app
from ares.cli.ui import UI
from ares.config import Percorsi
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


def avviso(percorsi: Percorsi) -> list[str]:
    """Le righe con cui la chat si ferma quando lo stato e' ancora nel posto di prima. Vuoto se no."""
    da_fare = parti(percorsi)
    if not da_fare:
        return []
    righe = ["Lo stato di Ares e' ancora dove stava prima, e qui non c'e' niente:"]
    for cosa, vecchio, nuovo in da_fare:
        righe.append("  " + cosa + ": " + str(vecchio) + "  ->  " + str(nuovo))
    righe.append("Spostalo con: " + config.comando_ares("migrate"))
    return righe


def _sposta(vecchio: Path, nuovo: Path) -> None:
    """Sposta `vecchio` in `nuovo` senza lasciare mai un `nuovo` a meta'.

    Sullo stesso filesystem `os.rename` e' atomico e basta. Fra filesystem
    diversi non lo e', e `shutil.move` degrada a copia piu' cancellazione: un
    guasto a meta' lascerebbe in `nuovo` uno stato incompleto che la chat
    aprirebbe senza accorgersene, con la copia buona ancora in `vecchio`. Qui
    la copia va in una sorella temporanea di `nuovo` e solo la rinomina la
    rende visibile, cosi' `nuovo` c'e' tutto o non c'e' per niente: nel primo
    caso la chat procede, nel secondo `avviso` la ferma.

    Il nome della sorella e' fisso e viene ripulito prima: i lock esclusivi
    tengono fuori un'altra migrazione, quindi un residuo e' di un tentativo
    precedente finito male, e ricominciare da capo e' la cosa giusta.
    """
    try:
        os.rename(vecchio, nuovo)
        return
    except OSError as errore:
        if errore.errno != errno.EXDEV:
            raise
    staging = nuovo.with_name("." + nuovo.name + "-migrazione")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        shutil.copytree(vecchio, staging, symlinks=True)
        os.replace(staging, nuovo)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(vecchio)


@app.default
def migra() -> int:
    """Sposta stato e backup da dentro il clone a ~/.ares, se sono ancora li'.

    Idempotente: dopo la prima volta dice che non c'e' niente da spostare.
    Non tocca una parte quando la destinazione contiene gia' dei dati: lo
    dice, e la decisione resta a chi guarda le due directory. Lo spostamento
    non lascia mai una destinazione a meta', nemmeno fra filesystem diversi:
    ci pensa `_sposta`.
    """
    percorsi = config.leggi_percorsi()
    for cosa, vecchio, nuovo in conflitti(percorsi):
        UI.line(
            "Attenzione: " + cosa + " in " + str(vecchio) + " restano li': " + str(nuovo) + " contiene gia' dei dati.",
            style="ares.warning",
        )
    da_fare = parti(percorsi)
    if not da_fare:
        UI.line("Niente da spostare: lo stato di Ares vive in " + str(percorsi.home) + ".", style="ares.muted")
        return 0

    vecchio_lock = Path(config.VECCHIO_TMP_DIR).with_name(Path(config.VECCHIO_TMP_DIR).name + ".lock")
    try:
        # Tutti e due i lock: quello nuovo, che le chat di questa versione
        # prendono, e quello vecchio accanto a `tmp/`, che una chat ancora
        # aperta dalla versione precedente potrebbe tenere.
        with (
            lock_stato(percorsi.lock_file, esclusivo=True),
            lock_stato(vecchio_lock, esclusivo=True),
        ):
            for cosa, vecchio, nuovo in da_fare:
                nuovo.parent.mkdir(parents=True, exist_ok=True)
                if nuovo.parent == percorsi.home:
                    rendi_privato(nuovo.parent)
                if nuovo.is_dir():
                    # Vuota, per costruzione di `parti()`: si toglie perche'
                    # la rinomina dentro una directory esistente la anniderebbe.
                    nuovo.rmdir()
                _sposta(vecchio, nuovo)
                rendi_privato(nuovo)
                UI.pair("Spostato " + cosa, str(vecchio) + "  ->  " + str(nuovo), style="ares.title")

            # Il vecchio lock si toglie mentre lo teniamo ancora. Dopo il
            # rilascio, fra il `close` e l'`unlink`, un processo della versione
            # precedente puo' prendere il lock su questo file: l'unlink lo
            # staccherebbe dall'inode, il processo dopo ne creerebbe uno nuovo,
            # e i due si crederebbero soli. Su Windows un file aperto non si
            # cancella - `lock_file` lo tiene aperto finche' il contesto non
            # esce - quindi li' il tentativo non riesce e si ripiega sotto.
            with contextlib.suppress(PermissionError):
                vecchio_lock.unlink()
    except StatoOccupato as errore:
        UI.err("ERRORE: " + str(errore))
        UI.err("Chiudi Ares e riprova.", style="ares.muted")
        return ESITO_OCCUPATO
    except OSError as errore:
        UI.err("ERRORE: spostamento fallito: " + str(errore))
        UI.err("Niente e' andato perso: cio' che non si e' mosso e' ancora dov'era.", style="ares.muted")
        return ESITO_GUASTO
    # Ripiego per Windows, dove il lock aperto impedisce l'unlink: qui il file
    # e' chiuso e si cancella. Su POSIX e' un no-op, il file e' gia' sparito.
    with contextlib.suppress(OSError):
        vecchio_lock.unlink()
    UI.line("Da ora `ares` legge da " + str(percorsi.home) + ", da qualunque cartella.", style="ares.muted")
    return 0


def main(argomenti: Sequence[str] | None = None) -> int:
    from ares.cli.app import esegui

    return esegui("migrate", argomenti)


if __name__ == "__main__":
    raise SystemExit(main())
