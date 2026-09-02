"""Manutenzione offline del ciclo di vita delle sessioni di Ares.

Uso:
    .venv/bin/ares-sessions status
    .venv/bin/ares-sessions prune --older-than 180
    .venv/bin/ares-sessions prune --older-than 180 --apply
    .venv/bin/ares-sessions delete <session-id> --apply

Senza ``--apply`` i comandi distruttivi sono soltanto un'anteprima. Quando si
applicano richiedono il lock esclusivo, creano uno snapshot verificato e
usano la cancellazione a cascata di Agno.
"""

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from ares import config
from ares.agent.runtime import build_db
from ares.backup.snapshots import ErroreBackup, crea_snapshot
from ares.sessions.retention import (
    ErroreRetention,
    SessioneRetention,
    apri_archivio,
    elimina_sessioni,
    inventario,
    seleziona_inattive,
    trova_sessione,
)
from ares.state.lock import StatoOccupato, lock_stato


def _giorni(valore: str) -> int:
    try:
        giorni = int(valore)
    except ValueError as errore:
        raise argparse.ArgumentTypeError("servono giorni interi") from errore
    if giorni < 1:
        raise argparse.ArgumentTypeError("deve essere almeno 1")
    return giorni


def costruisci_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ares-sessions", description="Retention delle sessioni e dei risultati tool di Ares"
    )
    sottocomandi = parser.add_subparsers(dest="comando", required=True)

    status = sottocomandi.add_parser("status", help="mostra sessioni e spazio logico degli offload")
    status.add_argument("--user", default=config.DEFAULT_USER_ID)

    prune = sottocomandi.add_parser("prune", help="propone o elimina sessioni inattive")
    prune.add_argument("--user", default=config.DEFAULT_USER_ID)
    prune.add_argument(
        "--older-than",
        type=_giorni,
        default=config.SESSION_RETENTION_DAYS,
        metavar="GIORNI",
        help="ultimo uso precedente a questo numero di giorni",
    )
    prune.add_argument(
        "--keep",
        action="append",
        default=[],
        metavar="SESSIONE",
        help="protegge un'altra sessione in questa esecuzione; ripetibile",
    )
    prune.add_argument("--apply", action="store_true", help="crea un backup e applica la selezione mostrata")
    prune.add_argument("--yes", action="store_true", help="con --apply, non chiedere conferma")

    delete = sottocomandi.add_parser("delete", help="propone o elimina una sessione esatta")
    delete.add_argument("session_id")
    delete.add_argument("--user", default=config.DEFAULT_USER_ID)
    delete.add_argument("--apply", action="store_true", help="crea un backup ed elimina la sessione mostrata")
    delete.add_argument("--yes", action="store_true", help="con --apply, non chiedere conferma")
    return parser


def _dimensione(byte: int) -> str:
    valore = float(byte)
    for unita in ("B", "KiB", "MiB", "GiB"):
        if valore < 1024 or unita == "GiB":
            return (str(int(valore)) if unita == "B" else format(valore, ".1f")) + " " + unita
        valore /= 1024
    return str(byte) + " B"


def _data(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _stampa_sessioni(sessioni: Sequence[SessioneRetention]) -> None:
    for sessione in sessioni:
        print(
            "-",
            sessione.session_id,
            " ",
            _data(sessione.ultimo_uso),
            " ",
            sessione.offload_count,
            "offload,",
            _dimensione(sessione.offload_bytes),
        )


def _stato(user_id: str) -> int:
    db = build_db()
    sessioni = inventario(db, user_id)
    print("Utente:", user_id)
    print("Sessioni:", len(sessioni))
    print("Offload indicizzati:", sum(s.offload_count for s in sessioni))
    print("Payload logici:", _dimensione(sum(s.offload_bytes for s in sessioni)))
    if sessioni:
        print()
        _stampa_sessioni(sessioni)
    return 0


def _confermata(numero: int, yes: bool) -> bool:
    if yes:
        return True
    frase = "ELIMINA " + str(numero) + (" SESSIONE" if numero == 1 else " SESSIONI")
    try:
        ricevuta = input("Scrivi " + frase + " per continuare: ").strip()
    except EOFError:
        ricevuta = ""
    return ricevuta == frase


def _applica(user_id: str, sessioni: Sequence[SessioneRetention], yes: bool) -> int:
    if not _confermata(len(sessioni), yes):
        print("Cancellazione annullata.")
        return 2
    snapshot = crea_snapshot(tipo="pre-session-prune", acquisisci_lock=False)
    print("Backup verificato:", snapshot.name)
    db, store = apri_archivio(user_id)
    eliminate = elimina_sessioni(db, store, sessioni, user_id)
    print("Sessioni eliminate e verificate:", eliminate)
    comando = r".venv\Scripts\ares-backup.exe" if os.name == "nt" else ".venv/bin/ares-backup"
    print("Per tornare indietro:", comando, "restore", snapshot.name)
    return 0


def _prune(args: argparse.Namespace) -> int:
    db = build_db()
    protette = set(config.SESSIONI_PROTETTE) | set(args.keep)
    candidate = seleziona_inattive(
        inventario(db, args.user),
        giorni=args.older_than,
        protette=protette,
    )
    print("Sessioni inattive da oltre", args.older_than, "giorni:", len(candidate))
    _stampa_sessioni(candidate)
    if protette:
        print("Protette:", ", ".join(sorted(protette)))
    if not candidate:
        print("Niente da eliminare.")
        return 0
    if not args.apply:
        print("Anteprima soltanto: nessun dato e' stato modificato.")
        print("Per applicarla, ripeti lo stesso comando aggiungendo --apply.")
        return 0
    return _applica(args.user, candidate, args.yes)


def _delete(args: argparse.Namespace) -> int:
    db = build_db()
    sessione = trova_sessione(inventario(db, args.user), args.session_id)
    print("Sessione da eliminare:")
    _stampa_sessioni([sessione])
    if sessione.session_id in config.SESSIONI_PROTETTE:
        print("Nota: la sessione e' protetta dal prune per eta', ma una cancellazione esatta puo' rimuoverla.")
    if not args.apply:
        print("Anteprima soltanto: nessun dato e' stato modificato.")
        print("Per applicarla, ripeti lo stesso comando aggiungendo --apply.")
        return 0
    return _applica(args.user, [sessione], args.yes)


def main(argv: Sequence[str] | None = None) -> int:
    args = costruisci_parser().parse_args(argv)
    if getattr(args, "yes", False) and not getattr(args, "apply", False):
        print("ERRORE: --yes richiede --apply", file=sys.stderr)
        return 2
    if not Path(config.DB_FILE).is_file():
        print("Nessun archivio di Ares trovato in", config.DB_FILE)
        return 0
    try:
        config.prepara_archivio()
        esclusivo = bool(getattr(args, "apply", False))
        with lock_stato(esclusivo=esclusivo):
            if args.comando == "status":
                return _stato(args.user)
            if args.comando == "prune":
                return _prune(args)
            if args.comando == "delete":
                return _delete(args)
    except StatoOccupato as errore:
        print("Impossibile usare lo stato di Ares:", errore, file=sys.stderr)
        print("Chiudi la chat e attendi che le altre manutenzioni terminino.", file=sys.stderr)
        return 2
    except (ErroreRetention, ErroreBackup, OSError) as errore:
        print("Manutenzione rifiutata:", errore, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
