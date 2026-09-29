"""Lo stato di Ares si sposta da dentro il clone a `~/.ares`, una volta sola.

Le versioni precedenti tenevano lo stato in `tmp/` nel repository e gli
snapshot in `ares-backup`; ora stanno in `~/.ares/stato` e
`~/.ares/backup`. `ares migrate` li sposta, i setup lo chiamano e la chat
si ferma finche' non e' fatto, per non sdoppiare lo stato.

Lo spostamento avviene sotto lock esclusivo e nessuna chat lo vede a meta'
(vedi `_sposta`).
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

    Sullo stesso filesystem basta `os.rename`. Fra filesystem diversi la copia
    va in una sorella temporanea di `nuovo`, resa visibile solo dalla rinomina
    finale. La sorella ha un nome fisso: un residuo viene da un tentativo
    fallito (i lock escludono migrazioni concorrenti) e si ripulisce.
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

    Idempotente. Non tocca una parte la cui destinazione contiene gia' dati:
    lo dice, e decide l'utente.
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
        # Anche il vecchio lock, che una chat della versione precedente
        # potrebbe ancora tenere.
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

            # Si cancella il vecchio lock mentre lo si tiene: dopo il rilascio
            # un altro processo potrebbe prenderlo, e l'unlink lo staccherebbe
            # dall'inode lasciando due processi convinti di essere soli. Su
            # Windows un file aperto non si cancella, e l'errore si ignora.
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
