"""Manutenzione offline del ciclo di vita delle sessioni di Ares.

Uso:
    ares sessions status
    ares sessions prune --older-than 180
    ares sessions prune --older-than 180 --apply
    ares sessions delete <session-id> --apply

Senza ``--apply`` i comandi distruttivi sono soltanto un'anteprima. Quando si
applicano richiedono il lock esclusivo, creano uno snapshot verificato e
usano la cancellazione a cascata di Agno. `ares-sessions` e' l'alias.
"""

import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from ares import config
from ares.agent.runtime import build_db
from ares.backup.snapshots import ErroreBackup, crea_snapshot
from ares.cli.comando import nuova_app
from ares.sessions.retention import (
    ErroreRetention,
    SessioneRetention,
    StatoParziale,
    apri_archivio,
    elimina_sessioni,
    inventario,
    seleziona_inattive,
    trova_sessione,
)
from ares.state.lock import StatoOccupato, lock_stato

app = nuova_app("sessions", "Retention delle sessioni e dei risultati tool di Ares")


def _almeno_un_giorno(tipo, valore: int) -> None:
    if valore < 1:
        raise ValueError("deve essere almeno 1")


Giorni = Annotated[int, Parameter(validator=_almeno_un_giorno)]


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
    comando = config.comando_ares("backup")
    db, store = apri_archivio(user_id)
    try:
        eliminate = elimina_sessioni(db, store, sessioni, user_id)
    except StatoParziale as errore:
        # Non e' un rifiuto: qualcosa e' gia' stato cancellato. Il rendiconto
        # dice cosa, e lo snapshot appena fatto e' il punto da cui si torna
        # allo stato di prima senza dover capire il guasto.
        print("Cancellazione interrotta:", errore, file=sys.stderr)
        print(
            "Stato parziale: eliminate",
            len(errore.eliminate),
            "sessioni su",
            str(len(sessioni)) + ", ancora presenti",
            str(len(errore.rimaste)) + ".",
            file=sys.stderr,
        )
        if errore.eliminate:
            print("Eliminate senza verifica:", ", ".join(errore.eliminate), file=sys.stderr)
            print("Contesto appreso o payload di queste possono essere rimasti orfani.", file=sys.stderr)
        if errore.rimaste:
            print("Ancora presenti:", ", ".join(errore.rimaste), file=sys.stderr)
        print("Per tornare allo stato di prima della manutenzione:", comando, "restore", snapshot.name, file=sys.stderr)
        return 1
    print("Sessioni eliminate e verificate:", eliminate)
    print("Per tornare indietro:", comando, "restore", snapshot.name)
    return 0


def _prune(user: str, older_than: int, keep: Sequence[str], apply: bool, yes: bool) -> int:
    db = build_db()
    protette = set(config.SESSIONI_PROTETTE) | set(keep)
    candidate = seleziona_inattive(
        inventario(db, user),
        giorni=older_than,
        protette=protette,
    )
    print("Sessioni inattive da oltre", older_than, "giorni:", len(candidate))
    _stampa_sessioni(candidate)
    if protette:
        print("Protette:", ", ".join(sorted(protette)))
    if not candidate:
        print("Niente da eliminare.")
        return 0
    if not apply:
        print("Anteprima soltanto: nessun dato e' stato modificato.")
        print("Per applicarla, ripeti lo stesso comando aggiungendo --apply.")
        return 0
    return _applica(user, candidate, yes)


def _delete(user: str, session_id: str, apply: bool, yes: bool) -> int:
    db = build_db()
    sessione = trova_sessione(inventario(db, user), session_id)
    print("Sessione da eliminare:")
    _stampa_sessioni([sessione])
    if sessione.session_id in config.SESSIONI_PROTETTE:
        print("Nota: la sessione e' protetta dal prune per eta', ma una cancellazione esatta puo' rimuoverla.")
    if not apply:
        print("Anteprima soltanto: nessun dato e' stato modificato.")
        print("Per applicarla, ripeti lo stesso comando aggiungendo --apply.")
        return 0
    return _applica(user, [sessione], yes)


def _esegui(azione: Callable[[], int], *, apply: bool = False, yes: bool = False) -> int:
    """Il contorno comune ai tre comandi: coerenza dei flag, archivio, lock, errori."""
    if yes and not apply:
        print("ERRORE: --yes richiede --apply", file=sys.stderr)
        return 2
    if not Path(config.DB_FILE).is_file():
        print("Nessun archivio di Ares trovato in", config.DB_FILE)
        return 0
    try:
        config.prepara_archivio()
        with lock_stato(esclusivo=apply):
            return azione()
    except StatoOccupato as errore:
        print("Impossibile usare lo stato di Ares:", errore, file=sys.stderr)
        print("Chiudi la chat e attendi che le altre manutenzioni terminino.", file=sys.stderr)
        return 2
    except (ErroreRetention, ErroreBackup, OSError) as errore:
        print("Manutenzione rifiutata:", errore, file=sys.stderr)
        return 2


@app.command
def status(*, user: str = config.DEFAULT_USER_ID) -> int:
    """Mostra sessioni e spazio logico degli offload.

    Args:
        user: utente di cui elencare le sessioni.
    """
    return _esegui(lambda: _stato(user))


@app.command
def prune(
    *,
    user: str = config.DEFAULT_USER_ID,
    older_than: Giorni = config.SESSION_RETENTION_DAYS,
    keep: list[str] | None = None,
    apply: bool = False,
    yes: bool = False,
) -> int:
    """Propone o elimina le sessioni inattive.

    Args:
        user: utente proprietario delle sessioni.
        older_than: ultimo uso precedente a questo numero di giorni.
        keep: protegge un'altra sessione in questa esecuzione; ripetibile.
        apply: crea un backup e applica la selezione mostrata.
        yes: con --apply, non chiedere conferma.
    """
    return _esegui(lambda: _prune(user, older_than, keep or [], apply, yes), apply=apply, yes=yes)


@app.command
def delete(session_id: str, *, user: str = config.DEFAULT_USER_ID, apply: bool = False, yes: bool = False) -> int:
    """Propone o elimina una sessione esatta.

    Args:
        session_id: la sessione da eliminare.
        user: utente proprietario della sessione.
        apply: crea un backup ed elimina la sessione mostrata.
        yes: con --apply, non chiedere conferma.
    """
    return _esegui(lambda: _delete(user, session_id, apply, yes), apply=apply, yes=yes)


def main(argv: Sequence[str] | None = None) -> int:
    from ares.cli.app import esegui

    return esegui("sessions", argv)


if __name__ == "__main__":
    raise SystemExit(main())
