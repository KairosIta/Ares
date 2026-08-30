"""Inventario e cancellazione coerente delle sessioni di Ares.

La politica vive sopra il ciclo di vita delle sessioni, non sopra i singoli
risultati: una conversazione conservata non deve contenere riferimenti scaduti.
Questo modulo non decide quando cancellare e non interagisce con l'utente;
espone le operazioni che la CLI coordina sotto lock e dopo un backup.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from time import time
from typing import Any

from agno.db.base import SessionType
from agno.db.sqlite import SqliteDb
from agno.fs import FileSystem
from agno.learn.utils import build_learning_id
from agno.offload.setup import build_result_store as configura_result_store
from agno.offload.store import ResultStore

from assistant_runtime import build_db, build_filesystem, build_result_store

SECONDI_AL_GIORNO = 86_400


class ErroreRetention(RuntimeError):
    """Una selezione o cancellazione di sessioni non e' sicura o completa."""


@dataclass(frozen=True)
class SessioneRetention:
    """I soli dati necessari a decidere e spiegare una retention."""

    session_id: str
    user_id: str | None
    created_at: int
    updated_at: int | None
    offload_count: int
    offload_bytes: int

    @property
    def ultimo_uso(self) -> int:
        return self.updated_at or self.created_at


class _Manutenzione:
    id = "ares-session-maintenance"


def apri_archivio(user_id: str) -> tuple[SqliteDb, ResultStore]:
    """Apre i due SQLite e registra il backend payload nella cascata Agno.

    `filesystem.db` e' intenzionalmente distinto dal database delle sessioni.
    La registrazione fatta normalmente da `Agent.initialize_agent()` va quindi
    ripetuta nel processo offline: senza, Agno eliminerebbe sessione e indice
    ma non saprebbe in quale backend cercare il payload.
    """
    db = build_db()
    filesystem = build_filesystem(user_id)
    store = configura_result_store(
        setting=build_result_store(filesystem),
        db=db,
        owner=_Manutenzione(),
        owner_kind="session maintenance",
    )
    if store is None:
        raise ErroreRetention("Agno non ha potuto inizializzare il ResultStore della manutenzione")
    return db, store


def inventario(db: SqliteDb, user_id: str) -> list[SessioneRetention]:
    """Sessioni dell'utente con l'occupazione logica dei relativi offload."""
    sessioni = db.get_sessions(
        session_type=SessionType.AGENT,
        user_id=user_id,
        sort_by="updated_at",
        sort_order="desc",
        include_runs=False,
    )
    risultato = []
    for sessione in sessioni or []:
        session_id = str(getattr(sessione, "session_id", ""))
        if not session_id:
            continue
        righe = db.get_tool_results_for_session(session_id, None)
        risultato.append(
            SessioneRetention(
                session_id=session_id,
                user_id=getattr(sessione, "user_id", None),
                created_at=int(getattr(sessione, "created_at", 0) or 0),
                updated_at=getattr(sessione, "updated_at", None),
                offload_count=len(righe),
                offload_bytes=sum(int(riga.get("size_bytes") or 0) for riga in righe),
            )
        )
    return risultato


def seleziona_inattive(
    sessioni: Iterable[SessioneRetention],
    giorni: int,
    protette: Iterable[str] = (),
    adesso: int | None = None,
) -> list[SessioneRetention]:
    """Sessioni non protette il cui ultimo uso precede la soglia richiesta."""
    if giorni < 1:
        raise ErroreRetention("--older-than deve essere almeno 1 giorno")
    istante = int(time()) if adesso is None else adesso
    limite = istante - giorni * SECONDI_AL_GIORNO
    escluse = set(protette)
    return [sessione for sessione in sessioni if sessione.session_id not in escluse and sessione.ultimo_uso < limite]


def trova_sessione(sessioni: Iterable[SessioneRetention], session_id: str) -> SessioneRetention:
    """Risolve un id esatto, senza abbreviazioni per una scelta distruttiva."""
    for sessione in sessioni:
        if sessione.session_id == session_id:
            return sessione
    raise ErroreRetention("sessione inesistente per questo utente: " + session_id)


def _contesto_sessione_presente(db: SqliteDb, session_id: str) -> bool:
    return (
        db.get_learning(
            learning_type="session_context",
            session_id=session_id,
        )
        is not None
    )


def _verifica_payload_rimosso(store: ResultStore, righe: list[dict[str, Any]]) -> None:
    for riga in righe:
        result_id = str(riga["result_id"])
        if store.get_row(result_id) is not None:
            raise ErroreRetention("indice offload non eliminato: " + result_id)
        filesystem = FileSystem(
            backend=store.fs.backend,
            namespace=str(riga["namespace"]),
        )
        if filesystem.read(str(riga["path"])) is not None:
            raise ErroreRetention("payload offload non eliminato: " + result_id)


def elimina_sessioni(
    db: SqliteDb,
    store: ResultStore,
    sessioni: Iterable[SessioneRetention],
    user_id: str,
) -> int:
    """Elimina sessioni, run, contesti e offload, poi verifica la cascata."""
    selezionate = list(sessioni)
    if not selezionate:
        return 0
    ids = [sessione.session_id for sessione in selezionate]
    righe_offload = [riga for session_id in ids for riga in db.get_tool_results_for_session(session_id, None)]
    # Questa API Agno rimuove anche i run e avvia la cascata degli offload.
    # Il ResultStore aperto sopra ha registrato filesystem.db sullo stesso db.
    db.delete_sessions(session_ids=ids, user_id=user_id)

    for session_id in ids:
        if db.get_session(session_id=session_id, session_type=SessionType.AGENT, user_id=user_id) is not None:
            raise ErroreRetention("sessione non eliminata: " + session_id)
        context_id = build_learning_id("session_context", session_id=session_id)
        if context_id is None:
            raise ErroreRetention("id del contesto non costruibile per " + session_id)
        # L'id e' deterministico e globale per sessione. Non filtrare per
        # user_id: una riga storica priva del proprietario appartiene comunque
        # alla conversazione appena eliminata e non deve restare orfana.
        db.delete_learning(context_id)
        if _contesto_sessione_presente(db, session_id):
            raise ErroreRetention("contesto di sessione non eliminato: " + session_id)

    _verifica_payload_rimosso(store, righe_offload)
    return len(ids)
