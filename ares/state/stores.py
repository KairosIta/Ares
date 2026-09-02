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

# Query usata quando chi chiama non ne ha una: recall() e' semantica e senza
# query non restituisce niente, quindi serve qualcosa di abbastanza largo da
# pescare le intuizioni tipiche di questo archivio.
QUERY_DI_RIPIEGO = "criterio decisione preferenza configurazione"


def namespace_utente(user_id: str) -> str:
    """Contenitore di tutto cio' che appartiene a un utente.

    Un solo posto costruisce questa stringa, perche' un refuso in una
    concatenazione manuale non solleva errori: le scritture finiscono in un
    contenitore, le letture ne interrogano un altro, e l'archivio sembra
    vuoto mentre e' pieno.

    La barra invece dei due punti perche' il FileSystem di Agno normalizza i
    namespace in forma URL-safe: `user:demo` finisce nel database come
    `user%3ademo`, mentre `user/demo` resta leggibile con qualsiasi
    client SQLite. L'id va in minuscolo per lo stesso motivo: il FileSystem
    lo farebbe comunque, e senza questo `Demo` scriverebbe i file in un
    posto e le memorie in un altro.
    """
    return "user/" + user_id.strip().lower()


def namespace_entita(user_id: str) -> str:
    """Namespace delle entita': persone, progetti e sistemi di quell'utente.

    Sottocontenitore separato perche' le entita' sono l'unico store con una
    granularita' propria; il resto vive direttamente sotto l'utente.
    """
    return namespace_utente(user_id) + "/personale"


def stampa_store(store: Any, etichetta: str, **filtri: Any) -> None:
    """Stampa uno store, o dice che e' spento invece di sollevare AttributeError.

    Gli store spenti in `config.py` non sono None per errore: la
    LearningMachine non li costruisce affatto, e `lm.user_profile_store`
    restituisce None. Chiamarci `.print()` sopra faceva morire `/profilo` con
    un AttributeError, e `config.py` invita esplicitamente a spegnerli per
    guadagnare latenza. La guardia sta qui e non nei due lettori perche' li'
    sarebbe scritta due volte, ed e' gia' successo con `/entita`.
    """
    if store is None:
        print(etichetta + ": store spento in config.py")
        return
    store.print(**filtri)


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


def leggi_entita(lm: Any, user_id: str, query: str = "", limit: int = 50) -> list[Any]:
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
    namespace = namespace_entita(user_id)
    if query:
        larghe = store.search(query=query, user_id=user_id, namespace=namespace, limit=config.ENTITA_FINESTRA_RICERCA)
        strette = [e for e in larghe if values_match_query(contenuto_entita(e), query)]
        return strette[:limit]
    return store.list_entities(user_id=user_id, namespace=namespace, limit=limit)


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


def leggi_intuizioni(lm: Any, user_id: str, query: str = "", limit: int = 20) -> list[Any]:
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
    return store.recall(query=query or QUERY_DI_RIPIEGO, user_id=user_id, limit=limit) or []


def leggi_sessioni(agent: Any, user_id: str, query: str = "") -> list[Any]:
    """Le sessioni di questo utente, dalla piu' toccata di recente.

    Non passa dagli store di apprendimento: le conversazioni stanno nella
    tabella delle sessioni, la stessa da cui l'agente rilegge il passato con
    `search_past_sessions`. Quello strumento pero' salta la sessione in corso,
    perche' il modello ce l'ha gia' davanti; qui invece torna, marcata da chi
    stampa: chi legge a schermo non ha nessuna finestra di contesto.

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
    sessioni = (
        db.get_sessions(
            session_type=SessionType.AGENT,
            user_id=user_id,
            sort_by="updated_at",
            sort_order="desc",
        )
        or []
    )
    if not query:
        return list(sessioni)
    cercato = query.casefold()
    return [s for s in sessioni if cercato in str(getattr(s, "session_id", "")).casefold()]


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


def righe_sessione(sessione: Any, corrente: bool = False) -> list[str]:
    """Rende una sessione in righe di testo gia' pronte per la stampa."""
    nome = str(getattr(sessione, "session_id", "?"))
    scambi = len(getattr(sessione, "runs", None) or [])
    quando = _quando(getattr(sessione, "updated_at", None) or getattr(sessione, "created_at", None))
    testa = "- " + nome + ("   (questa)" if corrente else "")
    righe = [testa + "   " + quando + "   " + str(scambi) + (" scambio" if scambi == 1 else " scambi")]
    domanda = prima_domanda(sessione)
    if domanda:
        righe.append("    inizio: " + domanda)
    return righe


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
