"""Audit deterministico delle entita', senza modello e senza scritture."""

import copy
import re
import unicodedata
from collections.abc import Sequence
from difflib import SequenceMatcher
from typing import Any

from agno.learn.schemas import EntityMemory

from ares.entities.models import CandidatoDuplicato, EntitaArchivio, EsitoAudit

SOGLIA_NOMI_SIMILI = 0.88
SOGLIA_CONTENUTO_SIMILE = 0.60

# Parole troppo comuni per trasformare due entita' dello stesso tipo in un
# candidato solo perche' entrambe sono, per esempio, un progetto di Ares.
PAROLE_COMUNI = frozenset(
    [
        "a",
        "ad",
        "al",
        "alla",
        "alle",
        "allo",
        "anche",
        "and",
        "are",
        "as",
        "che",
        "con",
        "da",
        "dal",
        "dalla",
        "delle",
        "dello",
        "di",
        "del",
        "della",
        "dei",
        "degli",
        "e",
        "ed",
        "for",
        "from",
        "gli",
        "ha",
        "i",
        "il",
        "in",
        "is",
        "la",
        "le",
        "lo",
        "nel",
        "nella",
        "of",
        "on",
        "o",
        "per",
        "project",
        "progetto",
        "sistema",
        "system",
        "the",
        "to",
        "un",
        "una",
        "uno",
        "uses",
        "usa",
    ]
)


def normalizza_testo(testo: str) -> str:
    """Confronto conservativo: Unicode e spazi, senza cancellare simboli.

    ``C++`` e ``C#`` devono restare nomi diversi. La punteggiatura non viene
    quindi rimossa; l'``entity_id`` gia' persistito copre separatamente le
    normalizzazioni fatte da Agno quando il record e' nato.
    """
    normalizzato = unicodedata.normalize("NFKC", str(testo)).casefold()
    return re.sub(r"\s+", " ", normalizzato).strip()


def _nomi(entita: EntityMemory) -> set[str]:
    valori = [entita.name or "", *(getattr(entita, "aliases", None) or [])]
    risultati = set()
    for valore in valori:
        normalizzato = normalizza_testo(str(valore))
        if normalizzato:
            risultati.add(normalizzato)
    return risultati


def _fatti_vivi(entita: EntityMemory) -> set[str]:
    risultati = set()
    for fatto in getattr(entita, "facts", None) or []:
        if not isinstance(fatto, dict) or fatto.get("superseded_at"):
            continue
        testo = normalizza_testo(str(fatto.get("content", "")))
        # Una frase molto corta tende a essere una tecnologia o proprieta'
        # comune, non un indizio di identita'.
        if len(testo) >= 16:
            risultati.add(testo)
    return risultati


def _testo_descrittivo(entita: EntityMemory) -> str:
    parti = [str(entita.description or "")]
    parti.extend(str(v) for v in (getattr(entita, "properties", None) or {}).values())
    for fatto in getattr(entita, "facts", None) or []:
        if isinstance(fatto, dict) and not fatto.get("superseded_at"):
            parti.append(str(fatto.get("content", "")))
    return " ".join(parte for parte in parti if parte)


def _parole_significative(testo: str) -> set[str]:
    parole = re.findall(r"[^\W_]{3,}", normalizza_testo(testo), flags=re.UNICODE)
    return {parola for parola in parole if parola not in PAROLE_COMUNI}


def _somiglianza_contenuto(prima: EntityMemory, seconda: EntityMemory) -> float | None:
    parole_prima = _parole_significative(_testo_descrittivo(prima))
    parole_seconda = _parole_significative(_testo_descrittivo(seconda))
    comuni = parole_prima & parole_seconda
    unione = parole_prima | parole_seconda
    if len(comuni) < 3 or not unione:
        return None
    valore = len(comuni) / len(unione)
    return valore if valore >= SOGLIA_CONTENUTO_SIMILE else None


def _motivi(prima: EntitaArchivio, seconda: EntitaArchivio) -> tuple[str, ...]:
    a, b = prima.entita, seconda.entita
    forti = []
    possibili = []

    if a.entity_id == b.entity_id:
        forti.append("stesso entity_id: " + str(a.entity_id))

    nome_a = normalizza_testo(prima.nome)
    nome_b = normalizza_testo(seconda.nome)
    if nome_a and nome_a == nome_b:
        forti.append("stesso nome visualizzato")

    nomi_comuni = _nomi(a) & _nomi(b)
    if nomi_comuni and nome_a != nome_b:
        forti.append("nome o alias condiviso: " + repr(sorted(nomi_comuni)[0]))

    stesso_tipo = normalizza_testo(str(a.entity_type)) == normalizza_testo(str(b.entity_type))
    if stesso_tipo and nome_a and nome_b and nome_a != nome_b:
        similarita = SequenceMatcher(None, nome_a, nome_b).ratio()
        if similarita >= SOGLIA_NOMI_SIMILI:
            possibili.append("nomi simili: " + format(similarita, ".0%"))

    descrizione_a = normalizza_testo(str(a.description or ""))
    descrizione_b = normalizza_testo(str(b.description or ""))
    if stesso_tipo and len(descrizione_a) >= 20 and descrizione_a == descrizione_b:
        possibili.append("stessa descrizione")

    fatti_comuni = _fatti_vivi(a) & _fatti_vivi(b)
    if stesso_tipo and fatti_comuni:
        possibili.append("fatti identici: " + str(len(fatti_comuni)))

    if stesso_tipo:
        somiglianza = _somiglianza_contenuto(a, b)
        if somiglianza is not None:
            possibili.append("contenuto lessicale sovrapposto: " + format(somiglianza, ".0%"))

    # L'ordine comunica anche il livello: qualunque prova d'identita' esatta
    # rende il candidato forte; gli altri segnali restano solo da verificare.
    return tuple(forti + possibili)


def carica_entita(db: Any, namespace: str) -> tuple[list[EntitaArchivio], list[str]]:
    """Legge tutte le righe, archiviate comprese, tramite la data API Agno."""
    righe = db.get_learnings(learning_type="entity_memory", namespace=namespace, limit=None) or []
    valide = []
    ignorate = []
    for indice, riga in enumerate(righe, start=1):
        learning_id = str(riga.get("learning_id") or "riga-" + str(indice))
        entita = EntityMemory.from_dict(riga.get("content"))
        if entita is None:
            ignorate.append(learning_id)
            continue
        valide.append(
            EntitaArchivio(
                learning_id=learning_id,
                entita=entita,
                contenuto_originale=copy.deepcopy(riga.get("content")),
            )
        )
    valide.sort(key=lambda voce: (voce.riferimento, voce.learning_id))
    return valide, ignorate


def trova_candidati(
    entita: Sequence[EntitaArchivio], includi_tutte_le_coppie: bool = False
) -> list[CandidatoDuplicato]:
    """Confronta ogni coppia una sola volta, senza modello e senza scritture."""
    candidati = []
    for indice, prima in enumerate(entita):
        for seconda in entita[indice + 1 :]:
            motivi = _motivi(prima, seconda)
            if motivi:
                livello = (
                    "forte"
                    if any(motivo.startswith(("stesso entity_id", "stesso nome", "nome o alias")) for motivo in motivi)
                    else "possibile"
                )
            elif includi_tutte_le_coppie and normalizza_testo(str(prima.entita.entity_type)) == normalizza_testo(
                str(seconda.entita.entity_type)
            ):
                livello = "manuale"
                motivi = ("nessun indizio lessicale; coppia dello stesso tipo",)
            else:
                continue
            candidati.append(CandidatoDuplicato(prima=prima, seconda=seconda, livello=livello, motivi=motivi))

    priorita = {"forte": 0, "possibile": 1, "manuale": 2}
    candidati.sort(
        key=lambda voce: (
            priorita[voce.livello],
            voce.prima.riferimento,
            voce.seconda.riferimento,
        )
    )
    return candidati


def analizza(db: Any, namespace: str, includi_tutte_le_coppie: bool = False) -> EsitoAudit:
    entita, ignorate = carica_entita(db=db, namespace=namespace)
    candidati = trova_candidati(entita, includi_tutte_le_coppie=includi_tutte_le_coppie)
    return EsitoAudit(tuple(entita), tuple(candidati), tuple(ignorate))
