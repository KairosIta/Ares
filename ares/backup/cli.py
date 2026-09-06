"""Interfaccia a riga di comando per gli snapshot di Ares.

E' il sotto-comando `ares backup`; `ares-backup` resta come alias. Le
operazioni vere arrivano da `snapshots.py`, che le inietta all'import per
evitare il ciclo fra i due moduli.
"""

import functools
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ares import config
from ares.backup import integrity
from ares.cli.comando import nuova_app
from ares.state.lock import StatoOccupato, lock_stato


@dataclass(frozen=True)
class OperazioniBackup:
    """Operazioni della façade usate dalla CLI, iniettate per evitare cicli."""

    avviso_residui: Callable[[], list[str]]
    crea_snapshot: Callable[[], Path]
    elenco_snapshot: Callable[[], list[Path]]
    pota_snapshot: Callable[[int, bool], list[Path]]
    ripristina_snapshot: Callable[[str, bool], Path | None]
    risolvi_snapshot: Callable[[str], Path]
    verifica_snapshot: Callable[[Any, bool], dict[str, Any]]


app = nuova_app("backup", "Snapshot locali dello stato di Ares")

_operazioni: OperazioniBackup | None = None


def imposta_operazioni(operazioni: OperazioniBackup) -> None:
    global _operazioni
    _operazioni = operazioni


def _op() -> OperazioniBackup:
    if _operazioni is None:
        raise RuntimeError("operazioni di backup non impostate: importa ares.backup.snapshots")
    return _operazioni


def _protetto(funzione):
    """Un guasto previsto diventa una riga e il codice 1, non un traceback."""

    @functools.wraps(funzione)
    def involucro(*argomenti, **opzioni):
        try:
            return funzione(*argomenti, **opzioni)
        except (integrity.ErroreBackup, StatoOccupato, OSError) as errore:
            print("ERRORE:", errore)
            return 1

    return involucro


def _dimensione(percorso: Path) -> int:
    return sum(voce.stat().st_size for voce in percorso.rglob("*") if voce.is_file())


@app.command(name="create")
@_protetto
def crea() -> int:
    """Crea e verifica uno snapshot."""
    creato = _op().crea_snapshot()
    print("Snapshot creato e verificato:", creato)
    return 0


@app.command(name="list")
@_protetto
def elenca() -> int:
    """Elenca gli snapshot, dal piu' recente."""
    operazioni = _op()
    # Prima del catalogo: chi elenca gli snapshot sta decidendo se e da cosa
    # ripristinare, e un restore rimasto a meta' e' la prima cosa da sapere.
    for riga in operazioni.avviso_residui():
        print(riga)
    snapshot = operazioni.elenco_snapshot()
    if not snapshot:
        print("Nessuno snapshot in", config.BACKUP_DIR)
        return 0
    for percorso in reversed(snapshot):
        try:
            manifest = json.loads((percorso / integrity.MANIFEST).read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest non oggetto")
            tipo = manifest.get("type", "?")
            creato = manifest.get("created_at", "?")
        except (OSError, UnicodeError, json.JSONDecodeError):
            tipo, creato = "CORROTTO", "?"
        print(percorso.name, " ", tipo, " ", creato, " ", _dimensione(percorso), "byte")
    return 0


@app.command(name="verify")
@_protetto
def verifica(snapshot: str = "latest") -> int:
    """Verifica checksum e database di uno snapshot.

    Args:
        snapshot: nome dello snapshot, o `latest` per l'ultimo.
    """
    manifest = _op().verifica_snapshot(snapshot, False)
    print("Snapshot valido:", manifest["snapshot_id"])
    return 0


@app.command(name="restore")
@_protetto
def ripristina(snapshot: str, *, yes: bool = False, skip_safety: bool = False) -> int:
    """Ripristina uno snapshot, dopo averlo verificato.

    Args:
        snapshot: nome dello snapshot da ripristinare.
        yes: non chiedere conferma.
        skip_safety: ripristina senza snapshot pre-restore (solo se lo stato corrente e' irrecuperabile).
    """
    operazioni = _op()
    percorso = operazioni.risolvi_snapshot(snapshot)
    operazioni.verifica_snapshot(percorso, True)
    if not yes:
        conferma = input("Scrivi " + percorso.name + " per ripristinarlo: ").strip()
        if conferma != percorso.name:
            print("Restore annullato.")
            return 2
    sicurezza = operazioni.ripristina_snapshot(percorso.name, not skip_safety)
    print("Restore completato:", percorso.name)
    if sicurezza is not None:
        print("Stato precedente salvato in:", sicurezza)
    return 0


@app.command(name="prune")
@_protetto
def pota(*, keep: int = config.BACKUP_KEEP, yes: bool = False) -> int:
    """Elimina gli snapshot piu' vecchi, conservandone un numero fisso.

    Args:
        keep: quanti snapshot recenti conservare.
        yes: non chiedere conferma.
    """
    operazioni = _op()
    if keep < 1:
        raise integrity.ErroreBackup("--keep deve essere almeno 1")
    disponibili = operazioni.elenco_snapshot()
    candidati = disponibili[:-keep] if len(disponibili) > keep else []
    if not candidati:
        print("Niente da eliminare; snapshot:", len(disponibili), " keep:", keep)
        return 0
    print("Snapshot da eliminare:")
    for percorso in candidati:
        print("-", percorso.name)
    if not yes and input("Scrivi ELIMINA per continuare: ").strip() != "ELIMINA":
        print("Prune annullato.")
        return 2
    # Fra anteprima e conferma potrebbe essere nato uno snapshot. Non eliminare
    # mai qualcosa che l'utente non ha appena visto.
    nomi_visti = [percorso.name for percorso in candidati]
    with lock_stato(esclusivo=True):
        attuali = operazioni.elenco_snapshot()
        candidati_attuali = attuali[:-keep] if len(attuali) > keep else []
        if [percorso.name for percorso in candidati_attuali] != nomi_visti:
            raise integrity.ErroreBackup("l'elenco degli snapshot e' cambiato; ripeti prune")
        eliminati = operazioni.pota_snapshot(keep, False)
    print("Eliminati", len(eliminati), "snapshot; conservati", keep)
    return 0


def main(operazioni: OperazioniBackup, argomenti: Sequence[str] | None = None) -> int:
    """L'alias `ares-backup`: passa da `ares backup`, cosi' l'aiuto dice la forma nuova."""
    imposta_operazioni(operazioni)
    from ares.cli.app import esegui

    return esegui("backup", argomenti)
