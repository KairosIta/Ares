"""Preparazione, installazione e rollback del restore degli snapshot."""

import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from ares.backup import files, integrity
from ares.config import Percorsi
from ares.state.lock import lock_stato
from ares.state.platform_files import rendi_privato

# Alias storici del modulo estratto: conservano import e monkeypatch mirati,
# mentre l'implementazione condivisa vive in un solo posto.
_privato = files.rendi_albero_privato
_rinomina_directory = files.rinomina_directory_nuova


@dataclass(frozen=True)
class OperazioniRestore:
    """Operazioni della façade richieste dal restore, iniettate senza cicli."""

    crea_snapshot_senza_lock: Callable[[Percorsi, str], Path]
    risolvi_snapshot: Callable[[Percorsi, str], Path]
    stato_presente: Callable[[Percorsi], bool]
    verifica_snapshot: Callable[[Percorsi, Any, bool], dict[str, Any]]


def _prepara_restore(percorsi: Percorsi, snapshot: Path, manifest: dict[str, Any]) -> Path:
    stato = percorsi.stato.resolve()
    parent = stato.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="." + stato.name + "-restore-", dir=parent))
    rendi_privato(staging)
    componenti = manifest.get("components") or {}
    cronologia = percorsi.cronologia_file.name
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
        viva = percorsi.stato / cronologia
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
    # Finche' questa copia non e' completa l'originale e' l'unico stato
    # affidabile. Un guasto qui non deve entrare nel rollback, che svuota
    # la destinazione prima di ricopiarla.
    if esisteva:
        try:
            shutil.copytree(destinazione, precedente)
        except Exception:
            shutil.rmtree(precedente, ignore_errors=True)
            raise
    try:
        if not esisteva:
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
    percorsi: Percorsi,
    nome: str,
    snapshot_sicurezza: bool,
    operazioni: OperazioniRestore,
) -> Path | None:
    """Ripristina uno snapshot verificato e ritorna l'eventuale pre-restore."""
    with lock_stato(percorsi.lock_file, esclusivo=True):
        snapshot = operazioni.risolvi_snapshot(percorsi, nome)
        manifest = operazioni.verifica_snapshot(percorsi, snapshot, True)
        sicurezza = None
        if snapshot_sicurezza and operazioni.stato_presente(percorsi):
            sicurezza = operazioni.crea_snapshot_senza_lock(percorsi, "pre-restore")

        staging = _prepara_restore(percorsi, snapshot, manifest)
        destinazione = percorsi.stato.resolve()
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
        except Exception as errore:
            residuo = None
            if spostato and precedente.exists() and not destinazione.exists():
                try:
                    _rinomina_directory(precedente, destinazione)
                except Exception as ripristino:
                    # Il rollback e' fallito a sua volta: l'eccezione da far
                    # risalire e' quella vera, ma va detto dove sta l'unica
                    # copia dello stato precedente, invece di nominare solo il
                    # guasto del rollback e perdere l'altro.
                    residuo = (precedente, ripristino)
            shutil.rmtree(staging, ignore_errors=True)
            if residuo is not None:
                raise integrity.ErroreBackup(
                    "restore fallito e ripristino fallito: lo stato precedente e' rimasto in "
                    + str(residuo[0])
                    + " ("
                    + str(residuo[1])
                    + ")"
                ) from errore
            raise
        else:
            if precedente.exists():
                # Il nuovo stato e' gia' installato: un residuo che non si
                # lascia rimuovere non deve trasformare un restore riuscito
                # in un falso fallimento.
                shutil.rmtree(precedente, ignore_errors=True)
        return sicurezza
