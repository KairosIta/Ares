"""
Lettura degli archivi di apprendimento
======================================
Un solo posto da cui leggere le entita', perche' le trappole degli store si
moltiplicano per il numero di copie: `/entita` in `chat.py` stampava
"Nessuna entita' registrata" con tre entita' in archivio, mentre
`inspect_learning.py` le mostrava correttamente. Le due letture erano
scritte due volte e solo una era giusta.

Le funzioni qui dentro non avviano il modello e non scrivono nulla.
"""

from datetime import datetime
from typing import Any

from agno.db.base import SessionType

# Verifica di Agno riusata invece che riscritta: e' la meta' precisa della sua
# ricerca - la query confrontata con i valori e non con i nomi dei campi - e
# una copia locale verificherebbe la copia. La differenza sta in cosa le si
# passa, non in come confronta: vedi `contenuto_entita`.
from agno.learn.utils import values_match_query

from ares import config
from ares.state.identita import Utente

# Query usata quando chi chiama non ne ha una: recall() e' semantica e senza
# query non restituisce niente, quindi serve qualcosa di abbastanza largo da
# pescare le intuizioni tipiche di questo archivio.
QUERY_DI_RIPIEGO = "criterio decisione preferenza configurazione"


def namespace_utente(utente: Utente) -> str:
    """Contenitore di tutto cio' che appartiene a un utente.

    Un solo posto costruisce questa stringa, perche' un refuso in una
    concatenazione manuale non solleva errori: le scritture finiscono in un
    contenitore, le letture ne interrogano un altro, e l'archivio sembra
    vuoto mentre e' pieno.

    La barra invece dei due punti perche' il FileSystem di Agno normalizza i
    namespace in forma URL-safe: `user:demo` finisce nel database come
    `user%3ademo`, mentre `user/demo` resta leggibile con qualsiasi
    client SQLite. La forma dell'id arriva dal tipo `Utente`, che l'ha
    gia' portata a quella canonica: qui non si normalizza una seconda volta,
    altrimenti le due regole tornerebbero a divergere.
    """
    return "user/" + utente.id


def namespace_entita(utente: Utente) -> str:
    """Namespace delle entita': persone, progetti e sistemi di quell'utente.

    Sottocontenitore separato perche' le entita' sono l'unico store con una
    granularita' propria; il resto vive direttamente sotto l'utente.
    """
    return namespace_utente(utente) + "/personale"


# Campi che il framework mette e toglie da solo. Restano fuori dalla ricerca:
# un fatto porta un `id` e due date, e cercarci dentro vuol dire che "2026"
# trova ogni entita' scritta quest'anno.
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
    """I campi di un'entita' che sono contenuto, senza cio' che la archivia.

    Serve a verificare una ricerca. Agno confronta la query con **tutti** i
    valori dell'entita', che sono anche il namespace, gli identificativi e le
    date: qui il namespace e' `user/<utente>/personale`, quindi cercare
    "person" restituisce l'archivio intero e sembra che il filtro non
    funzioni. Togliendo quei campi la verifica di Agno risponde su cio' che
    l'utente intendeva cercare.
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

    search() e' una ricerca testuale: con query vuota non matcha nulla e
    l'archivio sembra vuoto anche quando e' pieno. Per l'elenco integrale
    serve list_entities(), che ordina per aggiornamento piu' recente.

    Con una query, cio' che torna dallo store e' un soprainsieme: si chiede
    una finestra larga e si scarta qui quello che ha corrisposto solo per il
    namespace o per una data. Vedi `contenuto_entita`.

    Elenco vuoto se lo store e' spento: per chi legge non c'e' differenza tra
    nessuna entita' registrata e nessuna entita' registrabile, e la seconda
    la dice `config.py`.
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

    I fatti sono dizionari con chiave `content`, non `fact`: leggere la
    chiave sbagliata restituisce None per ogni fatto senza sollevare errori.
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

    recall() e' ricerca semantica: senza query non esiste un elenco
    integrale, quindi una query di ripiego larga e' il meglio che si puo'
    fare per un comando che vuole mostrare "cosa c'e' dentro".

    Attenzione al costo: questa e' l'unica funzione di lettura del progetto
    che accende un modello, perche' LanceDb vettorizza anche la query con
    l'embedder dell'indice.
    """
    store = lm.learned_knowledge_store
    if store is None:
        return []
    return store.recall(query=query or QUERY_DI_RIPIEGO, user_id=utente.id, limit=limit) or []


# La chiave, nei metadati della sessione, della cartella in cui e' nata.
# La scrive `build_assistant` passando `metadata=` all'agente: Agno la copia
# nella sessione nuova e la lascia com'e' in una ripresa. Le sessioni di
# prima di questa chiave non ne hanno una, e valgono "senza cartella".
CHIAVE_CARTELLA = "cartella"


def cartella_sessione(sessione: Any) -> str | None:
    """La cartella in cui una sessione e' nata, o None se non ne ha una."""
    metadata = getattr(sessione, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    valore = metadata.get(CHIAVE_CARTELLA)
    return str(valore) if valore else None


def _sessioni_db(db: Any, utente: Utente, *, con_run: bool = True) -> list[Any]:
    """Tutte le sessioni dell'utente dal database, dalla piu' toccata di recente.

    L'id e' quello canonico del tipo `Utente`, come per namespace, lock e
    profilo: `leggi_sessioni` e `sessioni_della_cartella` sono porte
    pubbliche, e una che cercasse con la grafia grezza non troverebbe le
    sessioni scritte con quella canonica.
    """
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
    """Le sessioni di questo utente, dalla piu' toccata di recente.

    Non passa dagli store di apprendimento: le conversazioni stanno nella
    tabella delle sessioni, la stessa da cui l'agente rilegge il passato con
    `search_past_sessions`. Quello strumento pero' salta la sessione in corso,
    perche' il modello ce l'ha gia' davanti; qui invece torna, marcata da chi
    stampa: chi legge a schermo non ha nessuna finestra di contesto.

    Con `cartella` restano quelle nate li' e quelle senza cartella: le
    seconde sono le conversazioni di prima che le sessioni si legassero a una
    directory, e nasconderle le farebbe sparire da ogni elenco.

    Nessun taglio qui. Chi chiama filtra e poi taglia, mai il contrario:
    chiedere al database le prime N e filtrarle dopo nasconderebbe una
    sessione piu' vecchia delle prime N, cioe' esattamente quella che si sta
    cercando quando si scrive un filtro.

    L'ordinamento e' per `updated_at` perche' chi riprende una conversazione
    cerca l'ultima toccata, non l'ultima aperta; Agno fa un COALESCE su
    `created_at`, quindi una sessione mai aggiornata non finisce in fondo.
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

    E' la lettura di `ares resume` e dell'elenco che il modello riceve
    all'avvio: qui una sessione senza cartella non c'entra, perche' riprendere
    vuol dire tornare al lavoro fatto in questo posto. Senza i run, che per
    un elenco pesano e non servono; chi vuole la prima domanda rilegge la
    sessione con `con_run`.
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

    `--session <nome>` e `/sessione <nome>` scavalcano gli elenchi per
    cartella, che sono gia' filtrati per utente: senza questo controllo i run
    di un secondo utente finirebbero nella sessione del primo, perche' Agno
    filtra i run per solo `session_id` e il proprietario della riga non li
    protegge. Riguarda le sole sessioni che esistono: un nome mai visto e' una
    conversazione nuova, e si apre.

    `runs_limit=1` perche' qui serve l'intestazione della riga - il
    proprietario - e non la conversazione: senza, `get_session` caricherebbe
    tutti i run per leggere un campo.
    """
    if not session_id:
        return False
    riga = db.get_session(session_id=session_id, session_type=SessionType.AGENT, deserialize=False, runs_limit=1)
    proprietario = riga.get("user_id") if isinstance(riga, dict) else None
    return bool(proprietario) and str(proprietario) != utente.id


def prima_domanda(sessione: Any, larghezza: int = 90) -> str:
    """La prima cosa chiesta in una sessione, troncata.

    E' l'etichetta piu' onesta che si possa dare a una conversazione senza
    farla riassumere a un modello: dice di cosa e' partita. Il contenuto di un
    messaggio non e' sempre una stringa - puo' essere una lista di parti - e
    leggerlo come stringa e basta restituisce righe vuote in silenzio.
    """
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

    `con_cartella` aggiunge dove e' nata: serve nell'elenco di tutte le
    sessioni, dove conversazioni di progetti diversi stanno una sotto l'altra.
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

    Solo i messaggi del turno: Agno rimette nei `messages` di ogni run anche
    la storia precedente, marcata `from_history`, e senza il filtro ogni
    scambio comparirebbe tante volte quanti sono i turni che lo seguono.
    Gli strumenti chiamati stanno in una riga per turno, col solo nome: e'
    un'esportazione da leggere, non un log.
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
    """Data e ora di un timestamp unix, in cifre.

    In cifre e non a parole perche' i nomi di giorno e mese di `strftime`
    seguono la locale del processo, e cambiarla e' una mutazione globale per
    una parola.
    """
    if not timestamp:
        return "data ignota"
    # Senza fuso, quindi ora locale: e' l'ora a cui l'utente stava davvero
    # scrivendo. Un orario in UTC sarebbe corretto e illeggibile.
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
