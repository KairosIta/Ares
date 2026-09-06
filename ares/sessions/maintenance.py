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

from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from cyclopts import Parameter

from ares import config
from ares.agent.runtime import build_db
from ares.backup.snapshots import ErroreBackup, crea_snapshot
from ares.cli.comando import nuova_app
from ares.cli.conferma import conferma_scritta
from ares.cli.ui import UI, byte_leggibili
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


def _data(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _riga(sessione: SessioneRetention) -> tuple[str, str, str, str]:
    return (
        sessione.session_id,
        _data(sessione.ultimo_uso),
        str(sessione.offload_count),
        byte_leggibili(sessione.offload_bytes),
    )


def _tabella_sessioni(sessioni: Sequence[SessioneRetention]) -> None:
    if sessioni:
        UI.table(
            ("sessione", "ultimo uso", ("offload", "ares.text", "right"), ("payload", "ares.text", "right")),
            (_riga(sessione) for sessione in sessioni),
        )


def _dati_sessione(sessione: SessioneRetention) -> dict[str, Any]:
    return {
        "session_id": sessione.session_id,
        "last_used": datetime.fromtimestamp(sessione.ultimo_uso).isoformat(timespec="seconds"),
        "offload_count": sessione.offload_count,
        "offload_bytes": sessione.offload_bytes,
    }


def _stato(user_id: str, come_json: bool) -> int:
    db = build_db()
    sessioni = inventario(db, user_id)
    offload = sum(s.offload_count for s in sessioni)
    payload = sum(s.offload_bytes for s in sessioni)
    if come_json:
        UI.json(
            {
                "user": user_id,
                "sessions": [_dati_sessione(s) for s in sessioni],
                "offload_count": offload,
                "offload_bytes": payload,
            }
        )
        return 0
    UI.pair("Utente", user_id)
    UI.pair("Sessioni", len(sessioni))
    UI.pair("Offload indicizzati", offload)
    UI.pair("Payload logici", byte_leggibili(payload))
    if sessioni:
        UI.blank()
        _tabella_sessioni(sessioni)
    return 0


def _confermata(numero: int, yes: bool) -> bool:
    if yes:
        return True
    frase = "ELIMINA " + str(numero) + (" SESSIONE" if numero == 1 else " SESSIONI")
    return conferma_scritta(frase)


def _applica(user_id: str, sessioni: Sequence[SessioneRetention], yes: bool) -> int:
    if not _confermata(len(sessioni), yes):
        UI.line("Cancellazione annullata.", style="ares.warning")
        return 2
    snapshot = crea_snapshot(tipo="pre-session-prune", acquisisci_lock=False)
    UI.pair("Backup verificato", snapshot.name)
    comando = config.comando_ares("backup", "restore", snapshot.name)
    db, store = apri_archivio(user_id)
    try:
        eliminate = elimina_sessioni(db, store, sessioni, user_id)
    except StatoParziale as errore:
        # Non e' un rifiuto: qualcosa e' gia' stato cancellato. Il rendiconto
        # dice cosa, e lo snapshot appena fatto e' il punto da cui si torna
        # allo stato di prima senza dover capire il guasto.
        UI.err("Cancellazione interrotta: " + str(errore))
        UI.err(
            "Stato parziale: eliminate "
            + str(len(errore.eliminate))
            + " sessioni su "
            + str(len(sessioni))
            + ", ancora presenti "
            + str(len(errore.rimaste))
            + ".",
            style="ares.text",
        )
        if errore.eliminate:
            UI.err("Eliminate senza verifica: " + ", ".join(errore.eliminate), style="ares.text")
            UI.err("Contesto appreso o payload di queste possono essere rimasti orfani.", style="ares.muted")
        if errore.rimaste:
            UI.err("Ancora presenti: " + ", ".join(errore.rimaste), style="ares.text")
        UI.err("Per tornare allo stato di prima della manutenzione: " + comando, style="ares.muted")
        return 1
    UI.line("Sessioni eliminate e verificate: " + str(eliminate), style="ares.success")
    UI.line("Per tornare indietro: " + comando, style="ares.muted")
    return 0


def _prune(user: str, older_than: int, keep: Sequence[str], apply: bool, yes: bool) -> int:
    db = build_db()
    protette = set(config.SESSIONI_PROTETTE) | set(keep)
    candidate = seleziona_inattive(
        inventario(db, user),
        giorni=older_than,
        protette=protette,
    )
    UI.pair("Sessioni inattive da oltre " + str(older_than) + " giorni", len(candidate))
    _tabella_sessioni(candidate)
    if protette:
        UI.pair("Protette", ", ".join(sorted(protette)))
    if not candidate:
        UI.line("Niente da eliminare.", style="ares.muted")
        return 0
    if not apply:
        _anteprima()
        return 0
    return _applica(user, candidate, yes)


def _delete(user: str, session_id: str, apply: bool, yes: bool) -> int:
    db = build_db()
    sessione = trova_sessione(inventario(db, user), session_id)
    UI.line("Sessione da eliminare:", style="ares.warning")
    _tabella_sessioni([sessione])
    if sessione.session_id in config.SESSIONI_PROTETTE:
        UI.line(
            "Nota: la sessione e' protetta dal prune per eta', ma una cancellazione esatta puo' rimuoverla.",
            style="ares.muted",
        )
    if not apply:
        _anteprima()
        return 0
    return _applica(user, [sessione], yes)


def _anteprima() -> None:
    UI.line("Anteprima soltanto: nessun dato e' stato modificato.", style="ares.warning")
    UI.line("Per applicarla, ripeti lo stesso comando aggiungendo --apply.", style="ares.muted")


def _esegui(
    azione: Callable[[], int],
    *,
    apply: bool = False,
    yes: bool = False,
    senza_archivio: Callable[[], int] | None = None,
) -> int:
    """Il contorno comune ai tre comandi: coerenza dei flag, archivio, lock, errori.

    `senza_archivio` e' cio' che si fa se il database non esiste: di default
    una riga che lo dice, ma `status --json` deve rispondere comunque con
    dati, perche' uno script non legge le frasi.
    """
    if yes and not apply:
        UI.err("ERRORE: --yes richiede --apply")
        return 2
    if not Path(config.DB_FILE).is_file():
        if senza_archivio is not None:
            return senza_archivio()
        UI.line("Nessun archivio di Ares trovato in " + str(config.DB_FILE), style="ares.muted")
        return 0
    try:
        config.prepara_archivio()
        with lock_stato(esclusivo=apply):
            return azione()
    except StatoOccupato as errore:
        UI.err("Impossibile usare lo stato di Ares: " + str(errore))
        UI.err("Chiudi la chat e attendi che le altre manutenzioni terminino.", style="ares.muted")
        return 2
    except (ErroreRetention, ErroreBackup, OSError) as errore:
        UI.err("Manutenzione rifiutata: " + str(errore))
        return 2


@app.command
def status(*, user: str = config.DEFAULT_USER_ID, come_json: Annotated[bool, Parameter(name="--json")] = False) -> int:
    """Mostra sessioni e spazio logico degli offload.

    Args:
        user: utente di cui elencare le sessioni.
        come_json: stampa sessioni e totali come JSON, per gli script.
    """

    def vuoto() -> int:
        if come_json:
            UI.json({"user": user, "sessions": [], "offload_count": 0, "offload_bytes": 0})
            return 0
        UI.line("Nessun archivio di Ares trovato in " + str(config.DB_FILE), style="ares.muted")
        return 0

    return _esegui(lambda: _stato(user, come_json), senza_archivio=vuoto)


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
