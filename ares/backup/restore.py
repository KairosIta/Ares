"""Preparazione, installazione e rollback del restore degli snapshot."""

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from ares import config
from ares.backup import files, integrity
from ares.state.lock import lock_stato
from ares.state.platform_files import rendi_privato

# Alias storici del modulo estratto: conservano import e monkeypatch mirati,
# mentre l'implementazione condivisa vive in un solo posto.
_privato = files.rendi_albero_privato
_rinomina_directory = files.rinomina_directory_nuova


@dataclass(frozen=True)
class OperazioniRestore:
    """Operazioni della façade richieste dal restore, iniettate senza cicli."""

    crea_snapshot_senza_lock: Callable[[str], Path]
    risolvi_snapshot: Callable[[str], Path]
    stato_presente: Callable[[], bool]
    verifica_snapshot: Callable[[Any, bool], dict[str, Any]]


def _prepara_restore(snapshot: Path, manifest: dict[str, Any]) -> Path:
    parent = config.TMP_DIR.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="." + config.TMP_DIR.name + "-restore-", dir=parent))
    rendi_privato(staging)
    componenti = manifest.get("components") or {}
    cronologia = config.CRONOLOGIA_FILE.name
    try:
        for nome in integrity.DATABASE:
            if componenti.get(nome):
                shutil.copy2(snapshot / nome, staging / nome)
                integrity.verifica_sqlite(staging / nome)
        # Il restore sostituisce l'intera directory dello stato, quindi cio'
        # che non entra in staging viene cancellato. La cronologia viva ha la
        # precedenza su quella dello snapshot: riportare indietro i database e'
        # il senso dell'operazione, riavvolgere cio' che l'utente ha digitato
        # no. Quella dello snapshot serve al caso per cui esiste un backup:
        # tmp/ persa, e allora non c'e' niente da conservare.
        viva = config.TMP_DIR / cronologia
        if viva.is_file():
            shutil.copy2(viva, staging / cronologia)
        elif componenti.get(cronologia):
            shutil.copy2(snapshot / cronologia, staging / cronologia)

        lance = componenti.get("lancedb") or {}
        if lance.get("present"):
            shutil.copytree(snapshot / "lancedb", staging / "lancedb")
            if integrity.conta_tabelle_lancedb(staging / "lancedb") != lance.get("tables"):
                raise integrity.ErroreBackup("LanceDB cambia durante la preparazione del restore")
        _privato(staging)
        return staging
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _svuota_directory(percorso: Path) -> None:
    """Rimuove il contenuto lasciando stabile la directory radice."""
    for voce in list(percorso.iterdir()):
        if voce.is_symlink() or voce.is_file():
            voce.unlink()
        else:
            shutil.rmtree(voce)


def _installa_restore_per_copia(staging: Path, destinazione: Path, precedente: Path) -> None:
    """Fallback Windows con copia di rollback gia' pronta prima dello swap."""
    esisteva = destinazione.is_dir()
    try:
        if esisteva:
            shutil.copytree(destinazione, precedente)
        else:
            destinazione.mkdir(parents=True)
        _svuota_directory(destinazione)
        shutil.copytree(staging, destinazione, dirs_exist_ok=True)
        _privato(destinazione)
    except Exception as errore_originale:
        try:
            if destinazione.is_dir():
                _svuota_directory(destinazione)
            if esisteva and precedente.exists():
                destinazione.mkdir(parents=True, exist_ok=True)
                shutil.copytree(precedente, destinazione, dirs_exist_ok=True)
            elif destinazione.exists():
                shutil.rmtree(destinazione)
        except Exception as errore_rollback:
            raise integrity.ErroreBackup(
                "restore fallito ("
                + str(errore_originale)
                + ") e rollback fallito ("
                + str(errore_rollback)
                + "); copia precedente: "
                + str(precedente)
            ) from errore_rollback
        raise
    else:
        shutil.rmtree(precedente, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)


def ripristina_snapshot(
    nome: str,
    snapshot_sicurezza: bool,
    operazioni: OperazioniRestore,
) -> Path | None:
    """Ripristina uno snapshot verificato e ritorna l'eventuale pre-restore."""
    with lock_stato(esclusivo=True):
        snapshot = operazioni.risolvi_snapshot(nome)
        manifest = operazioni.verifica_snapshot(snapshot, True)
        sicurezza = None
        if snapshot_sicurezza and operazioni.stato_presente():
            sicurezza = operazioni.crea_snapshot_senza_lock("pre-restore")

        staging = _prepara_restore(snapshot, manifest)
        destinazione = config.TMP_DIR.resolve()
        precedente = destinazione.with_name("." + destinazione.name + "-precedente-" + uuid4().hex)
        if os.name == "nt":
            # Windows puo' rifiutare il rename di directory LanceDB non vuote
            # anche senza processi Ares attivi. La copia precedente permette
            # il rollback e lo snapshot pre-restore resta la rete di sicurezza
            # persistente in caso di interruzione del processo.
            _installa_restore_per_copia(staging, destinazione, precedente)
            return sicurezza

        spostato = False
        try:
            if destinazione.exists():
                _rinomina_directory(destinazione, precedente)
                spostato = True
            _rinomina_directory(staging, destinazione)
        except Exception:
            if spostato and precedente.exists() and not destinazione.exists():
                _rinomina_directory(precedente, destinazione)
            shutil.rmtree(staging, ignore_errors=True)
            raise
        else:
            if precedente.exists():
                # Il nuovo stato e' gia' installato: un residuo che non si
                # lascia rimuovere non deve trasformare un restore riuscito
                # in un falso fallimento.
                shutil.rmtree(precedente, ignore_errors=True)
        return sicurezza
