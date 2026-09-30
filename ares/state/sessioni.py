"""Le conversazioni in archivio, come riferimenti che non espongono Agno.

Gli oggetti di sessione di Agno restano in questo modulo: nucleo, prompt e
client ricevono `SessioneRiferimento` e `Conversazione`.

Un elenco filtra, conta e taglia sulle sessioni senza run; i run si leggono
solo per le voci tagliate. Caricarli tutti costa quanto l'intera storia
dell'utente, e l'elenco si chiede anche a ogni TAB.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from agno.db.base import SessionType

from ares.state.identita import Utente

# La chiave dei metadati di sessione con la cartella in cui e' nata, scritta
# da `build_assistant`. Le sessioni piu' vecchie non ce l'hanno.
CHIAVE_CARTELLA = "cartella"

# `nate_qui`: solo quelle nate nella cartella, per riprendere. `qui`: anche
# quelle senza cartella, che altrimenti sparirebbero da ogni elenco.
Ambito = Literal["qui", "nate_qui", "tutte"]


@dataclass(frozen=True)
class SessioneRiferimento:
    """Una conversazione in archivio.

    `aggiornata` e' il timestamp unix dell'ultima modifica, o della
    creazione; `inizio` la prima domanda, con gli spazi normalizzati e intera.
    """

    id: str
    cartella: str | None
    aggiornata: float | None
    scambi: int
    inizio: str


@dataclass(frozen=True)
class Elenco:
    """Le sessioni che passano il filtro: `nomi` tutte, `voci` le prime, con i loro run."""

    nomi: tuple[str, ...]
    voci: tuple[SessioneRiferimento, ...]

    @property
    def totale(self) -> int:
        return len(self.nomi)


@dataclass(frozen=True)
class Messaggio:
    ruolo: Literal["user", "assistant"]
    testo: str


@dataclass(frozen=True)
class Scambio:
    """Un turno: i messaggi di persona e assistente, e gli strumenti usati, per nome."""

    messaggi: tuple[Messaggio, ...]
    strumenti: tuple[str, ...]


@dataclass(frozen=True)
class Conversazione:
    sessione: SessioneRiferimento
    utente: str
    scambi: tuple[Scambio, ...]


def cartella_di(sessione: Any) -> str | None:
    """La cartella in cui una sessione di Agno e' nata, o None se non ne ha una."""
    metadata = getattr(sessione, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    valore = metadata.get(CHIAVE_CARTELLA)
    return str(valore) if valore else None


def elenca(
    db: Any,
    utente: Utente,
    *,
    ambito: Ambito,
    cartella: Path | str | None = None,
    testo: str = "",
    escludi: str | None = None,
    limite: int | None = None,
) -> Elenco:
    """Le sessioni dell'utente dalla piu' toccata di recente, filtrate e poi tagliate a `limite`.

    `testo` filtra sul nome, senza distinguere maiuscole. Filtrare dopo aver
    tagliato perderebbe le sessioni oltre le prime `limite`. `qui` e
    `nate_qui` vogliono `cartella`.
    """
    if ambito != "tutte" and cartella is None:
        raise ValueError("l'ambito " + ambito + " vuole una cartella")
    qui = str(cartella) if cartella is not None else None
    ammesse = {"qui": (None, qui), "nate_qui": (qui,)}.get(ambito)
    cercato = testo.casefold()
    sessioni = [
        s
        for s in _sessioni_db(db, utente)
        if (ammesse is None or cartella_di(s) in ammesse)
        and str(getattr(s, "session_id", "")) != escludi
        and cercato in str(getattr(s, "session_id", "")).casefold()
    ]
    tagliate = sessioni if limite is None else sessioni[: max(0, limite)]
    return Elenco(
        nomi=tuple(str(getattr(s, "session_id", "")) for s in sessioni),
        voci=tuple(riferimento(_con_run(db, s)) for s in tagliate),
    )


def conversazione(db: Any, utente: Utente, session_id: str) -> Conversazione | None:
    """La conversazione `session_id` dell'utente, o None se non c'e' o e' di un altro."""
    sessione = db.get_session(session_id=session_id, session_type=SessionType.AGENT, user_id=utente.id)
    if sessione is None:
        return None
    scambi = []
    for run in getattr(sessione, "runs", None) or []:
        messaggi = []
        # I messaggi `from_history` sono la storia che Agno ripete in ogni run.
        for messaggio in getattr(run, "messages", None) or []:
            ruolo = getattr(messaggio, "role", None)
            if getattr(messaggio, "from_history", False) or ruolo not in ("user", "assistant"):
                continue
            testo = _testo_messaggio(messaggio).strip()
            if testo:
                messaggi.append(Messaggio(ruolo=ruolo, testo=testo))
        strumenti = [str(getattr(t, "tool_name", "") or "") for t in getattr(run, "tools", None) or []]
        scambi.append(Scambio(messaggi=tuple(messaggi), strumenti=tuple(dict.fromkeys(s for s in strumenti if s))))
    return Conversazione(
        sessione=riferimento(sessione),
        utente=str(getattr(sessione, "user_id", None) or ""),
        scambi=tuple(scambi),
    )


def sessione_di_altri(db: Any, session_id: str | None, utente: Utente) -> bool:
    """Vero se la sessione esiste ma appartiene a un altro utente.

    Serve a `--session` e `/sessione`, che scavalcano gli elenchi filtrati per
    utente: Agno filtra i run per `session_id` soltanto, e senza questo
    controllo i run di un utente finirebbero nella sessione di un altro. Legge
    un solo run, perche' serve solo il proprietario.
    """
    if not session_id:
        return False
    riga = db.get_session(session_id=session_id, session_type=SessionType.AGENT, deserialize=False, runs_limit=1)
    proprietario = riga.get("user_id") if isinstance(riga, dict) else None
    return bool(proprietario) and str(proprietario) != utente.id


def riferimento(sessione: Any) -> SessioneRiferimento:
    """Il riferimento di una sessione di Agno letta con i suoi run."""
    runs = getattr(sessione, "runs", None) or []
    return SessioneRiferimento(
        id=str(getattr(sessione, "session_id", "") or ""),
        cartella=cartella_di(sessione),
        aggiornata=getattr(sessione, "updated_at", None) or getattr(sessione, "created_at", None) or None,
        scambi=len(runs),
        inizio=_prima_domanda(runs),
    )


def quando(sessione: SessioneRiferimento) -> str:
    """L'ultima modifica in cifre e in ora locale, la stessa per prompt e client."""
    if not sessione.aggiornata:
        return "data ignota"
    return datetime.fromtimestamp(sessione.aggiornata).strftime("%Y-%m-%d %H:%M")


def tronca(testo: str, larghezza: int) -> str:
    return testo if len(testo) <= larghezza else testo[:larghezza] + "..."


def _sessioni_db(db: Any, utente: Utente) -> list[Any]:
    """Le sessioni dell'utente, senza run, dalla piu' toccata di recente."""
    return list(
        db.get_sessions(
            session_type=SessionType.AGENT,
            user_id=utente.id,
            sort_by="updated_at",
            sort_order="desc",
            include_runs=False,
        )
        or []
    )


def _con_run(db: Any, sessione: Any) -> Any:
    """La stessa sessione riletta con i suoi run, o com'era se la rilettura fallisce.

    `get_session` unisce anche i run rimasti nella vecchia colonna di Agno.
    """
    intera = db.get_session(
        session_id=getattr(sessione, "session_id", None),
        session_type=SessionType.AGENT,
        user_id=getattr(sessione, "user_id", None),
    )
    return intera if intera is not None else sessione


def _prima_domanda(runs: list[Any]) -> str:
    for run in runs:
        for messaggio in getattr(run, "messages", None) or []:
            if getattr(messaggio, "role", None) != "user":
                continue
            testo = " ".join(_testo_messaggio(messaggio).split())
            if testo:
                return testo
    return ""


def _testo_messaggio(messaggio: Any) -> str:
    contenuto = getattr(messaggio, "content", None)
    if isinstance(contenuto, str):
        return contenuto
    if isinstance(contenuto, list):
        parti = []
        for parte in contenuto:
            if isinstance(parte, str):
                parti.append(parte)
            elif isinstance(parte, dict) and "text" in parte:
                parti.append(str(parte["text"]))
        return " ".join(parti)
    return ""
