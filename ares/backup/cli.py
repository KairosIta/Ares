"""Interfaccia a riga di comando per gli snapshot di Ares."""

import argparse
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ares import config
from ares.backup import integrity
from ares.state.lock import StatoOccupato, lock_stato


@dataclass(frozen=True)
class OperazioniBackup:
    """Operazioni della façade usate dalla CLI, iniettate per evitare cicli."""

    crea_snapshot: Callable[[], Path]
    elenco_snapshot: Callable[[], list[Path]]
    pota_snapshot: Callable[[int, bool], list[Path]]
    ripristina_snapshot: Callable[[str, bool], Path | None]
    risolvi_snapshot: Callable[[str], Path]
    verifica_snapshot: Callable[[Any, bool], dict[str, Any]]


def costruisci_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ares-backup", description="Snapshot locali dello stato di Ares")
    sottocomandi = parser.add_subparsers(dest="comando", required=True)
    sottocomandi.add_parser("create", help="crea e verifica uno snapshot")
    sottocomandi.add_parser("list", help="elenca gli snapshot")
    verifica = sottocomandi.add_parser("verify", help="verifica checksum e database")
    verifica.add_argument("snapshot", nargs="?", default="latest")
    restore = sottocomandi.add_parser("restore", help="ripristina uno snapshot")
    restore.add_argument("snapshot")
    restore.add_argument("--yes", action="store_true", help="non chiedere conferma")
    restore.add_argument(
        "--skip-safety",
        action="store_true",
        help="ripristina senza snapshot pre-restore (solo se lo stato corrente e' irrecuperabile)",
    )
    prune = sottocomandi.add_parser("prune", help="elimina gli snapshot piu' vecchi")
    prune.add_argument("--keep", type=int, default=config.BACKUP_KEEP)
    prune.add_argument("--yes", action="store_true", help="non chiedere conferma")
    return parser


def _dimensione(percorso: Path) -> int:
    return sum(voce.stat().st_size for voce in percorso.rglob("*") if voce.is_file())


def _stampa_elenco(operazioni: OperazioniBackup) -> None:
    snapshot = operazioni.elenco_snapshot()
    if not snapshot:
        print("Nessuno snapshot in", config.BACKUP_DIR)
        return
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


def _crea(operazioni: OperazioniBackup) -> int:
    creato = operazioni.crea_snapshot()
    print("Snapshot creato e verificato:", creato)
    return 0


def _verifica(args: argparse.Namespace, operazioni: OperazioniBackup) -> int:
    manifest = operazioni.verifica_snapshot(args.snapshot, False)
    print("Snapshot valido:", manifest["snapshot_id"])
    return 0


def _ripristina(args: argparse.Namespace, operazioni: OperazioniBackup) -> int:
    snapshot = operazioni.risolvi_snapshot(args.snapshot)
    operazioni.verifica_snapshot(snapshot, True)
    if not args.yes:
        conferma = input("Scrivi " + snapshot.name + " per ripristinarlo: ").strip()
        if conferma != snapshot.name:
            print("Restore annullato.")
            return 2
    sicurezza = operazioni.ripristina_snapshot(snapshot.name, not args.skip_safety)
    print("Restore completato:", snapshot.name)
    if sicurezza is not None:
        print("Stato precedente salvato in:", sicurezza)
    return 0


def _pota(args: argparse.Namespace, operazioni: OperazioniBackup) -> int:
    if args.keep < 1:
        raise integrity.ErroreBackup("--keep deve essere almeno 1")
    disponibili = operazioni.elenco_snapshot()
    candidati = disponibili[: -args.keep] if len(disponibili) > args.keep else []
    if not candidati:
        print("Niente da eliminare; snapshot:", len(disponibili), " keep:", args.keep)
        return 0
    print("Snapshot da eliminare:")
    for percorso in candidati:
        print("-", percorso.name)
    if not args.yes and input("Scrivi ELIMINA per continuare: ").strip() != "ELIMINA":
        print("Prune annullato.")
        return 2
    # Fra anteprima e conferma potrebbe essere nato uno snapshot. Non eliminare
    # mai qualcosa che l'utente non ha appena visto.
    nomi_visti = [percorso.name for percorso in candidati]
    with lock_stato(esclusivo=True):
        attuali = operazioni.elenco_snapshot()
        candidati_attuali = attuali[: -args.keep] if len(attuali) > args.keep else []
        if [percorso.name for percorso in candidati_attuali] != nomi_visti:
            raise integrity.ErroreBackup("l'elenco degli snapshot e' cambiato; ripeti prune")
        eliminati = operazioni.pota_snapshot(args.keep, False)
    print("Eliminati", len(eliminati), "snapshot; conservati", args.keep)
    return 0


def main(operazioni: OperazioniBackup, argomenti: Sequence[str] | None = None) -> int:
    args = costruisci_parser().parse_args(argomenti)
    try:
        if args.comando == "create":
            return _crea(operazioni)
        if args.comando == "list":
            _stampa_elenco(operazioni)
            return 0
        if args.comando == "verify":
            return _verifica(args, operazioni)
        if args.comando == "restore":
            return _ripristina(args, operazioni)
        if args.comando == "prune":
            return _pota(args, operazioni)
    except (integrity.ErroreBackup, StatoOccupato, OSError) as errore:
        print("ERRORE:", errore)
        return 1
    return 0
