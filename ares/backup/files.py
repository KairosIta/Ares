"""Primitive di filesystem condivise dai flussi di backup e restore."""

import os
from pathlib import Path

from ares.backup.integrity import ErroreBackup
from ares.state.platform_files import rendi_privato


def rendi_albero_privato(percorso: Path) -> None:
    """Applica i permessi privati a un percorso e al suo contenuto."""
    if percorso.is_dir():
        rendi_privato(percorso)
        for voce in percorso.rglob("*"):
            if voce.is_symlink():
                continue
            rendi_privato(voce)
    elif percorso.exists():
        rendi_privato(percorso)


def rinomina_directory_nuova(sorgente: Path, destinazione: Path) -> None:
    """Rinomina una directory senza sostituire una destinazione esistente.

    ``os.replace`` seleziona su Windows la semantica di sostituzione e alcuni
    filesystem la rifiutano per directory non vuote. Qui il target deve essere
    assente per contratto, quindi ``os.rename`` e' l'operazione corretta.
    """
    if os.path.lexists(destinazione):
        raise ErroreBackup("la destinazione della rinomina esiste gia': " + str(destinazione))
    os.rename(sorgente, destinazione)
