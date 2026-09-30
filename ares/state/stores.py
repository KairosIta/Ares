"""Lettura degli archivi di apprendimento, in un posto solo.

Le sessioni si leggono da `state/sessioni.py`.

Le trappole degli store (query vuote, chiavi dei fatti, namespace) vanno
evitate una volta: ogni copia della lettura e' un'occasione per sbagliarla.
Nessuna funzione qui scrive; solo `leggi_intuizioni` accende un modello.
"""

from typing import Any

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
