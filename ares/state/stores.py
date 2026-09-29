"""Lettura degli archivi di apprendimento e delle sessioni, in un posto solo.

Le trappole degli store (query vuote, chiavi dei fatti, namespace) vanno
evitate una volta: ogni copia della lettura e' un'occasione per sbagliarla.
Nessuna funzione qui scrive; solo `leggi_intuizioni` accende un modello.
"""

from datetime import datetime
from typing import Any

from agno.db.base import SessionType

# Il confronto query/valori di Agno, riusato; cio' che cambia e' cosa gli si
# passa (vedi `contenuto_entita`).
from agno.learn.utils import values_match_query

from ares import config
from ares.state.identita import Utente

# Query larga per `recall`, che senza query non restituisce niente.
QUERY_DI_RIPIEGO = "criterio decisione preferenza configurazione"


def namespace_utente(utente: Utente) -> str:
    """Contenitore di tutto cio' che appartiene a un utente: `user/<id>`.

    Costruito solo qui, perche' un refuso sparpaglierebbe letture e scritture
    in contenitori diversi senza errori. La barra e non i due punti perche'
    Agno percent-encoda i namespace (`user:demo` -> `user%3ademo`).
    """
    return "user/" + utente.id


def namespace_entita(utente: Utente) -> str:
    """Namespace delle entita' di un utente, sotto il suo contenitore."""
    return namespace_utente(utente) + "/personale"


# Campi messi dal framework, esclusi dalla ricerca: altrimenti "2026"
# troverebbe ogni entita' scritta quest'anno.
CONTABILITA = ("id", "created_at", "updated_at")


def _senza_contabilita(voci: Any) -> list[Any]:
    """Fatti o eventi ridotti a cio' che ci ha scritto qualcuno."""
    ripulite = []
    for voce in voci or []:
        if isinstance(voce, dict):
            ripulite.append({c: v for c, v in voce.items() if c not in CONTABILITA})
        else:
            ripulite.append(voce)
    return ripulite


def contenuto_entita(entita: Any) -> dict:
    """I campi di un'entita' che sono contenuto, senza namespace, id e date.

    Agno confronta la query con tutti i valori dell'entita': con il namespace
    dentro, cercare "person" restituirebbe l'archivio intero.
    """
    return {
        "name": getattr(entita, "name", None),
        "entity_type": getattr(entita, "entity_type", None),
        "description": getattr(entita, "description", None),
        "aliases": getattr(entita, "aliases", None),
        "properties": getattr(entita, "properties", None),
        "facts": _senza_contabilita(getattr(entita, "facts", None)),
        "events": _senza_contabilita(getattr(entita, "events", None)),
        "relationships": getattr(entita, "relationships", None),
    }


def leggi_entita(lm: Any, utente: Utente, query: str = "", limit: int = 50) -> list[Any]:
    """Elenca le entita' registrate, filtrandole per query se ne arriva una.

    Senza query usa `list_entities`: `search` con query vuota non trova nulla.
    Con una query chiede allo store una finestra larga e filtra qui con
    `contenuto_entita`. Elenco vuoto se lo store e' spento.
    """
    store = lm.entity_memory_store
    if store is None:
        return []
    namespace = namespace_entita(utente)
    if query:
        larghe = store.search(query=query, user_id=utente.id, namespace=namespace, limit=config.ENTITA_FINESTRA_RICERCA)
        strette = [e for e in larghe if values_match_query(contenuto_entita(e), query)]
        return strette[:limit]
    return store.list_entities(user_id=utente.id, namespace=namespace, limit=limit)


def righe_entita(entita: Any, max_fatti: int = 5) -> list[str]:
    """Rende un'entita' in righe di testo gia' pronte per la stampa.

    Il testo di un fatto sta sotto `content`, non `fact`.
    """
    nome = getattr(entita, "name", None) or getattr(entita, "entity_id", "?")
    tipo = getattr(entita, "entity_type", "?")
    righe = ["- " + str(nome) + "   [" + str(tipo) + "]"]
    for f in (getattr(entita, "facts", None) or [])[:max_fatti]:
        testo = f.get("content") if isinstance(f, dict) else getattr(f, "content", f)
        righe.append("    fatto: " + str(testo))
    return righe


def leggi_intuizioni(lm: Any, utente: Utente, query: str = "", limit: int = 20) -> list[Any]:
    """Intuizioni apprese, cercate per somiglianza semantica.

    `recall` non ha un elenco integrale: senza query usa `QUERY_DI_RIPIEGO`.
    Accende l'embedder per vettorizzare la query.
    """
    store = lm.learned_knowledge_store
    if store is None:
        return []
    return store.recall(query=query or QUERY_DI_RIPIEGO, user_id=utente.id, limit=limit) or []


# La chiave dei metadati di sessione con la cartella in cui e' nata, scritta
# da `build_assistant`. Le sessioni piu' vecchie non ce l'hanno.
CHIAVE_CARTELLA = "cartella"


def cartella_sessione(sessione: Any) -> str | None:
    """La cartella in cui una sessione e' nata, o None se non ne ha una."""
    metadata = getattr(sessione, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    valore = metadata.get(CHIAVE_CARTELLA)
    return str(valore) if valore else None


def _sessioni_db(db: Any, utente: Utente, *, con_run: bool = True) -> list[Any]:
    """Tutte le sessioni dell'utente dal database, dalla piu' toccata di recente."""
    return list(
        db.get_sessions(
            session_type=SessionType.AGENT,
            user_id=utente.id,
            sort_by="updated_at",
            sort_order="desc",
            include_runs=con_run,
        )
        or []
    )


def leggi_sessioni(agent: Any, utente: Utente, query: str = "", cartella: Any = None) -> list[Any]:
    """Le sessioni di questo utente, dalla piu' toccata di recente, sessione corrente compresa.

    Con `cartella` restano quelle nate li' e quelle senza cartella (le piu'
    vecchie, che altrimenti sparirebbero da ogni elenco). Non taglia: chi
    chiama filtra e poi taglia, mai il contrario, o un filtro perderebbe le
    sessioni oltre le prime N.
    """
    db = getattr(agent, "db", None)
    if db is None:
        return []
    sessioni = _sessioni_db(db, utente)
    if cartella is not None:
        qui = str(cartella)
        sessioni = [s for s in sessioni if cartella_sessione(s) in (None, qui)]
    if not query:
        return sessioni
    cercato = query.casefold()
    return [s for s in sessioni if cercato in str(getattr(s, "session_id", "")).casefold()]


def sessioni_della_cartella(db: Any, utente: Utente, cartella: Any, *, escludi: str | None = None) -> list[Any]:
    """Le sole sessioni nate in `cartella`, dalla piu' toccata di recente.

    Per `ares resume` e per l'elenco nel prompt. Senza i run: chi vuole la
    prima domanda rilegge la sessione con `con_run`.
    """
    qui = str(cartella)
    return [
        s
        for s in _sessioni_db(db, utente, con_run=False)
        if cartella_sessione(s) == qui and getattr(s, "session_id", None) != escludi
    ]


def con_run(db: Any, sessione: Any) -> Any:
    """La stessa sessione riletta con i suoi run, o com'era se la rilettura fallisce."""
    intera = db.get_session(
        session_id=getattr(sessione, "session_id", None),
        session_type=SessionType.AGENT,
        user_id=getattr(sessione, "user_id", None),
    )
    return intera if intera is not None else sessione


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


def prima_domanda(sessione: Any, larghezza: int = 90) -> str:
    """La prima cosa chiesta in una sessione, troncata: l'etichetta della conversazione."""
    for run in getattr(sessione, "runs", None) or []:
        for messaggio in getattr(run, "messages", None) or []:
            if getattr(messaggio, "role", None) != "user":
                continue
            testo = _testo_messaggio(messaggio)
            if testo:
                testo = " ".join(testo.split())
                return testo if len(testo) <= larghezza else testo[:larghezza] + "..."
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


def righe_sessione(sessione: Any, corrente: bool = False, con_cartella: bool = False) -> list[str]:
    """Rende una sessione in righe di testo gia' pronte per la stampa.

    `con_cartella` aggiunge dove e' nata, per gli elenchi fra cartelle.
    """
    nome = str(getattr(sessione, "session_id", "?"))
    scambi = len(getattr(sessione, "runs", None) or [])
    quando = quando_sessione(sessione)
    testa = "- " + nome + ("   (questa)" if corrente else "")
    righe = [testa + "   " + quando + "   " + str(scambi) + (" scambio" if scambi == 1 else " scambi")]
    domanda = prima_domanda(sessione)
    if domanda:
        righe.append("    inizio: " + domanda)
    if con_cartella:
        righe.append("    cartella: " + (cartella_sessione(sessione) or "nessuna"))
    return righe


def testo_conversazione(sessione: Any, *, modello: str = "") -> str:
    """La conversazione in Markdown: una testata, poi ogni scambio come `Tu` e `Ares`.

    Salta i messaggi `from_history`, che Agno ripete in ogni run; degli
    strumenti riporta solo i nomi, una riga per turno.
    """
    nome = str(getattr(sessione, "session_id", "?"))
    runs = getattr(sessione, "runs", None) or []
    righe = ["# Conversazione " + nome, ""]
    righe.append("- utente: " + str(getattr(sessione, "user_id", None) or "?"))
    dove = cartella_sessione(sessione)
    if dove:
        righe.append("- cartella: " + dove)
    if modello:
        righe.append("- modello: " + modello)
    righe.append("- ultima modifica: " + quando_sessione(sessione))
    righe.append("- scambi: " + str(len(runs)))
    for run in runs:
        for messaggio in getattr(run, "messages", None) or []:
            if getattr(messaggio, "from_history", False):
                continue
            ruolo = getattr(messaggio, "role", None)
            testo = _testo_messaggio(messaggio).strip()
            if ruolo == "user" and testo:
                righe.extend(["", "## Tu", "", testo])
            elif ruolo == "assistant" and testo:
                righe.extend(["", "## Ares", "", testo])
        strumenti = [str(getattr(t, "tool_name", "") or "") for t in getattr(run, "tools", None) or []]
        strumenti = [s for s in strumenti if s]
        if strumenti:
            righe.extend(["", "_strumenti: " + ", ".join(dict.fromkeys(strumenti)) + "_"])
    return "\n".join(righe) + "\n"


def quando_sessione(sessione: Any) -> str:
    """L'ultima modifica di una sessione, o la creazione, in cifre."""
    return _quando(getattr(sessione, "updated_at", None) or getattr(sessione, "created_at", None))


def _quando(timestamp: Any) -> str:
    """Data e ora di un timestamp unix, in cifre e in ora locale."""
    if not timestamp:
        return "data ignota"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
