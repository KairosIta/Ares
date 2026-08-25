"""
Manutenzione delle entita' di Ares
==================================

Uso:
    .venv/bin/python entity_maintenance.py audit
    .venv/bin/python entity_maintenance.py audit --all
    .venv/bin/python entity_maintenance.py audit --all-pairs
    .venv/bin/python entity_maintenance.py merge --source project/doppione --into project/canonico
    .venv/bin/python entity_maintenance.py merge --source project/doppione --into project/canonico --apply

L'audit legge direttamente le righe ``entity_memory`` di Agno senza avviare
un modello e senza scrivere nello stato. Include le entita' archiviate: Agno
puo' riattivarle alla successiva ``remember_about``, quindi ignorarle
nasconderebbe proprio i doppioni che possono tornare.

Non decide mai che due entita' vadano fuse. Produce candidati e motivi
deterministici; la scelta del canonico resta umana.
"""

import argparse
import copy
import hashlib
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from agno.db.sqlite import SqliteDb
from agno.learn.schemas import EntityMemory
from sqlalchemy import MetaData, Table, select

import config
from backup import ErroreBackup, crea_snapshot
from state_lock import StatoOccupato, lock_stato
from stores import namespace_entita


SOGLIA_NOMI_SIMILI = 0.88
SOGLIA_CONTENUTO_SIMILE = 0.60

# Parole troppo comuni per trasformare due entita' dello stesso tipo in un
# candidato solo perche' entrambe sono, per esempio, un progetto di Ares.
PAROLE_COMUNI = frozenset(
    """
    a ad al alla alle allo anche and are as che con da dal dalla delle dello
    di del della dei degli e ed for from gli ha i il in is la le lo nel nella
    of on o per project progetto sistema system the to un una uno uses usa
    """.split()
)


@dataclass(frozen=True)
class EntitaArchivio:
    """Una riga valida dello store, con la sua identita' persistita."""

    learning_id: str
    entita: EntityMemory
    contenuto_originale: Any

    @property
    def riferimento(self) -> str:
        return str(self.entita.entity_type) + "/" + str(self.entita.entity_id)

    @property
    def nome(self) -> str:
        return str(self.entita.name or self.entita.entity_id)

    @property
    def archiviata(self) -> bool:
        return bool(getattr(self.entita, "archived_at", None))


@dataclass(frozen=True)
class CandidatoDuplicato:
    """Una coppia da mostrare all'utente, non una decisione di fusione."""

    prima: EntitaArchivio
    seconda: EntitaArchivio
    livello: str
    motivi: tuple[str, ...]


@dataclass(frozen=True)
class EsitoAudit:
    entita: tuple[EntitaArchivio, ...]
    candidati: tuple[CandidatoDuplicato, ...]
    righe_ignorate: tuple[str, ...]


class ErroreManutenzione(RuntimeError):
    """La fusione non puo' essere pianificata o applicata in sicurezza."""


@dataclass(frozen=True)
class AggiornamentoEntita:
    learning_id: str
    riferimento: str
    prima: Any
    dopo: dict[str, Any]


@dataclass(frozen=True)
class StatisticheFusione:
    alias_aggiunti: int
    fatti_aggiunti: int
    fatti_unificati: int
    eventi_aggiunti: int
    eventi_unificati: int
    proprieta_aggiunte: int
    relazioni_riscritte: int
    relazioni_unificate: int
    auto_relazioni_rimosse: int
    reciproche_aggiunte: int
    righe_modificate: int
    conflitti: tuple[str, ...]


@dataclass(frozen=True)
class PianoFusione:
    sorgente: EntitaArchivio
    canonica: EntitaArchivio
    aggiornamenti: tuple[AggiornamentoEntita, ...]
    statistiche: StatisticheFusione

    @property
    def conferma(self) -> str:
        return "FONDI " + self.sorgente.riferimento + " IN " + self.canonica.riferimento


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


def _somiglianza_contenuto(prima: EntityMemory, seconda: EntityMemory) -> Optional[float]:
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
                livello = "forte" if any(
                    motivo.startswith(("stesso entity_id", "stesso nome", "nome o alias")) for motivo in motivi
                ) else "possibile"
            elif includi_tutte_le_coppie and normalizza_testo(
                str(prima.entita.entity_type)
            ) == normalizza_testo(str(seconda.entita.entity_type)):
                livello = "manuale"
                motivi = ("nessun indizio lessicale; coppia dello stesso tipo",)
            else:
                continue
            candidati.append(
                CandidatoDuplicato(prima=prima, seconda=seconda, livello=livello, motivi=motivi)
            )

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


# ---------------------------------------------------------------------------
# Fusione deterministica
# ---------------------------------------------------------------------------


def _ora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chiave_entita(entita: EntityMemory) -> tuple[str, str]:
    return str(entita.entity_type), str(entita.entity_id)


def _riferimento_chiave(chiave: tuple[str, str]) -> str:
    return chiave[0] + "/" + chiave[1]


def _chiave_relazione(relazione: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(relazione.get("entity_type", "")),
        str(relazione.get("entity_id", "")),
        str(relazione.get("relation", "")),
        str(relazione.get("direction", "outgoing")),
    )


def _chiave_lontana(relazione: dict[str, Any]) -> tuple[str, str]:
    return str(relazione.get("entity_type", "")), str(relazione.get("entity_id", ""))


def _valida_riferimenti(entita: Sequence[EntitaArchivio]) -> dict[str, EntitaArchivio]:
    indice = {}
    for voce in entita:
        if voce.riferimento in indice:
            raise ErroreManutenzione(
                "piu' righe dichiarano la stessa identita' " + voce.riferimento + "; fusione rifiutata"
            )
        indice[voce.riferimento] = voce
    return indice


def _trova_riferimento(indice: dict[str, EntitaArchivio], riferimento: str, ruolo: str) -> EntitaArchivio:
    riferimento = riferimento.strip()
    trovato = indice.get(riferimento)
    if trovato is not None:
        return trovato
    disponibili = ", ".join(sorted(indice)) or "nessuna"
    raise ErroreManutenzione(
        ruolo + " inesistente: " + repr(riferimento) + ". Riferimenti disponibili: " + disponibili
    )


def _valida_collezione(entita: EntityMemory, campo: str, riferimento: str) -> list[dict[str, Any]]:
    valori = list(getattr(entita, campo, None) or [])
    if any(not isinstance(valore, dict) for valore in valori):
        raise ErroreManutenzione(
            riferimento + " contiene " + campo + " non strutturati come dizionari; fusione rifiutata"
        )
    return copy.deepcopy(valori)


def _unisci_alias(canonica: EntityMemory, sorgente: EntityMemory) -> tuple[list[str], int]:
    risultato = []
    visti = set()
    nome_canonico = normalizza_testo(str(canonica.name or canonica.entity_id))
    for valore in [
        *(getattr(canonica, "aliases", None) or []),
        sorgente.name or sorgente.entity_id,
        *(getattr(sorgente, "aliases", None) or []),
    ]:
        testo = str(valore).strip()
        normalizzato = normalizza_testo(testo)
        if not normalizzato or normalizzato == nome_canonico or normalizzato in visti:
            continue
        visti.add(normalizzato)
        risultato.append(testo)
    iniziali = {
        normalizza_testo(str(alias)) for alias in (getattr(canonica, "aliases", None) or []) if str(alias).strip()
    }
    aggiunti = sum(normalizza_testo(alias) not in iniziali for alias in risultato)
    return risultato, aggiunti


def _valore_temporale(primo: Any, secondo: Any, piu_recente: bool) -> Any:
    valori = [str(v) for v in (primo, secondo) if v]
    if not valori:
        return primo if primo is not None else secondo
    return (max if piu_recente else min)(valori)


def _unisci_metadati(
    canonico: dict[str, Any],
    sorgente: dict[str, Any],
    etichetta: str,
    conflitti: list[str],
) -> dict[str, Any]:
    """Combina record equivalenti senza cambiare id e contenuto canonici."""
    risultato = copy.deepcopy(canonico)
    ignorati = {"id", "content", "created_at", "updated_at"}
    for chiave, valore in sorgente.items():
        if chiave in ignorati:
            continue
        if chiave not in risultato or risultato[chiave] in (None, "", [], {}):
            risultato[chiave] = copy.deepcopy(valore)
        elif risultato[chiave] != valore:
            conflitti.append(etichetta + ": metadato " + chiave + " diverso; conservato il canonico")
    if canonico.get("created_at") or sorgente.get("created_at"):
        risultato["created_at"] = _valore_temporale(
            canonico.get("created_at"), sorgente.get("created_at"), piu_recente=False
        )
    if canonico.get("updated_at") or sorgente.get("updated_at"):
        risultato["updated_at"] = _valore_temporale(
            canonico.get("updated_at"), sorgente.get("updated_at"), piu_recente=True
        )
    return risultato


def _identita_ricordo(record: dict[str, Any], campo: str) -> tuple[Any, ...]:
    contenuto = normalizza_testo(str(record.get("content", "")))
    if not contenuto:
        raise ErroreManutenzione(campo + " con contenuto vuoto; fusione rifiutata")
    if campo == "fatto":
        return contenuto, bool(record.get("superseded_at"))
    return contenuto, normalizza_testo(str(record.get("date", "")))


def _unisci_ricordi(
    canonici: list[dict[str, Any]],
    sorgenti: list[dict[str, Any]],
    campo: str,
    conflitti: list[str],
) -> tuple[list[dict[str, Any]], int, int]:
    risultato = []
    per_identita = {}
    per_id = {}
    aggiunti = 0
    unificati = 0

    for viene_dalla_sorgente, raccolta in ((False, canonici), (True, sorgenti)):
        for record in raccolta:
            identita = _identita_ricordo(record, campo)
            record_id = str(record.get("id")) if record.get("id") else None
            if record_id and record_id in per_id and per_id[record_id] != identita:
                raise ErroreManutenzione(
                    campo + " con id " + record_id + " usato per contenuti diversi; fusione rifiutata"
                )
            if record_id:
                # Registrato anche quando il contenuto viene unificato: un
                # secondo record che riusa quell'id per altro deve fallire.
                per_id[record_id] = identita
            if identita in per_identita:
                indice = per_identita[identita]
                risultato[indice] = _unisci_metadati(
                    risultato[indice], record, campo + " " + str(record_id or "senza-id"), conflitti
                )
                unificati += 1
                continue
            risultato.append(copy.deepcopy(record))
            per_identita[identita] = len(risultato) - 1
            if viene_dalla_sorgente:
                aggiunti += 1
    # Agno rende l'ultima fetta della lista come la piu' recente. Dopo aver
    # accodato la sorgente, rimettiamo in ordine i record che hanno una data
    # tecnica; quelli storici senza data conservano fra loro l'ordine stabile.
    risultato = [
        record
        for _, record in sorted(
            enumerate(risultato),
            key=lambda voce: (str(voce[1].get("created_at") or ""), voce[0]),
        )
    ]
    return risultato, aggiunti, unificati


def _valida_relazione(relazione: Any, proprietario: str) -> dict[str, Any]:
    if not isinstance(relazione, dict):
        raise ErroreManutenzione(proprietario + " contiene una relazione non strutturata; fusione rifiutata")
    lontana = _chiave_lontana(relazione)
    if not all(lontana) or not str(relazione.get("relation", "")).strip():
        raise ErroreManutenzione(proprietario + " contiene una relazione incompleta; fusione rifiutata")
    if relazione.get("direction", "outgoing") not in {"incoming", "outgoing"}:
        raise ErroreManutenzione(proprietario + " contiene una direzione di relazione non valida")
    return copy.deepcopy(relazione)


def _deduplica_relazioni(
    relazioni: list[dict[str, Any]], proprietario: str, conflitti: list[str]
) -> tuple[list[dict[str, Any]], int]:
    risultato = []
    per_chiave = {}
    unificate = 0
    for relazione in relazioni:
        relazione = _valida_relazione(relazione, proprietario)
        chiave = _chiave_relazione(relazione)
        if chiave in per_chiave:
            indice = per_chiave[chiave]
            risultato[indice] = _unisci_metadati(
                risultato[indice], relazione, "relazione su " + proprietario, conflitti
            )
            unificate += 1
        else:
            per_chiave[chiave] = len(risultato)
            risultato.append(relazione)
    return risultato, unificate


def _id_relazione(
    proprietario: tuple[str, str], lontana: tuple[str, str], relazione: str, direzione: str
) -> str:
    testo = "|".join((*proprietario, *lontana, relazione, direzione))
    return hashlib.sha256(testo.encode("utf-8")).hexdigest()[:8]


def _reciproca(
    relazione: dict[str, Any], proprietario: tuple[str, str], lontana: tuple[str, str], ora: str
) -> dict[str, Any]:
    direzione = "incoming" if relazione.get("direction", "outgoing") == "outgoing" else "outgoing"
    return {
        "id": _id_relazione(lontana, proprietario, str(relazione.get("relation")), direzione),
        "entity_id": proprietario[1],
        "entity_type": proprietario[0],
        "relation": str(relazione.get("relation")),
        "direction": direzione,
        "created_at": ora,
        "updated_at": ora,
    }


def _ha_reciproca(
    relazioni: Sequence[dict[str, Any]], proprietario: tuple[str, str], relazione: dict[str, Any]
) -> bool:
    direzione = "incoming" if relazione.get("direction", "outgoing") == "outgoing" else "outgoing"
    chiave = (proprietario[0], proprietario[1], str(relazione.get("relation", "")), direzione)
    return any(_chiave_relazione(candidata) == chiave for candidata in relazioni)


def _valida_grafo_fuso(
    entita: dict[tuple[str, str], EntityMemory],
    sorgente: tuple[str, str],
    canonica: tuple[str, str],
) -> None:
    for proprietario, oggetto in entita.items():
        viste = set()
        for relazione in getattr(oggetto, "relationships", None) or []:
            relazione = _valida_relazione(relazione, _riferimento_chiave(proprietario))
            chiave = _chiave_relazione(relazione)
            if chiave in viste:
                raise ErroreManutenzione("la fusione produrrebbe una relazione duplicata")
            viste.add(chiave)
            lontana = _chiave_lontana(relazione)
            if lontana == sorgente:
                raise ErroreManutenzione("la fusione lascerebbe una relazione verso la sorgente")
            if proprietario == canonica and lontana == canonica:
                raise ErroreManutenzione("la fusione produrrebbe un'auto-relazione sul canonico")
            if proprietario != canonica and lontana != canonica:
                continue
            destinazione = entita.get(lontana)
            if destinazione is None:
                raise ErroreManutenzione(
                    "relazione coinvolta nella fusione verso entita' inesistente: "
                    + _riferimento_chiave(lontana)
                )
            if not _ha_reciproca(destinazione.relationships or [], proprietario, relazione):
                raise ErroreManutenzione("la fusione produrrebbe una relazione senza reciproca")


def pianifica_fusione(
    entita: Sequence[EntitaArchivio], riferimento_sorgente: str, riferimento_canonico: str
) -> PianoFusione:
    """Costruisce l'intero nuovo grafo in memoria, senza scrivere nel DB."""
    indice = _valida_riferimenti(entita)
    sorgente = _trova_riferimento(indice, riferimento_sorgente, "sorgente")
    canonica = _trova_riferimento(indice, riferimento_canonico, "canonica")
    if sorgente.learning_id == canonica.learning_id:
        raise ErroreManutenzione("sorgente e canonica sono la stessa riga")
    if canonica.archiviata:
        raise ErroreManutenzione("l'entita' canonica e' archiviata; scegli una canonica attiva")
    tipo_sorgente = normalizza_testo(str(sorgente.entita.entity_type))
    tipo_canonico = normalizza_testo(str(canonica.entita.entity_type))
    if tipo_sorgente != tipo_canonico and tipo_sorgente != "unknown":
        raise ErroreManutenzione(
            "tipi incompatibili: "
            + tipo_sorgente
            + " -> "
            + tipo_canonico
            + ". Agno non risolve alias fra due tipi reali diversi"
        )

    per_chiave = {_chiave_entita(voce.entita): voce for voce in entita}
    chiave_sorgente = _chiave_entita(sorgente.entita)
    chiave_canonica = _chiave_entita(canonica.entita)
    oggetti = {chiave: copy.deepcopy(voce.entita) for chiave, voce in per_chiave.items()}
    oggetto_sorgente = oggetti[chiave_sorgente]
    oggetto_canonico = oggetti[chiave_canonica]
    conflitti = []
    contatori = {
        "alias": 0,
        "fatti_aggiunti": 0,
        "fatti_unificati": 0,
        "eventi_aggiunti": 0,
        "eventi_unificati": 0,
        "proprieta": 0,
        "riscritte": 0,
        "relazioni_unificate": 0,
        "auto": 0,
        "reciproche": 0,
    }

    oggetto_canonico.aliases, contatori["alias"] = _unisci_alias(oggetto_canonico, oggetto_sorgente)
    if not oggetto_canonico.description and oggetto_sorgente.description:
        oggetto_canonico.description = oggetto_sorgente.description
    elif (
        oggetto_canonico.description
        and oggetto_sorgente.description
        and oggetto_canonico.description != oggetto_sorgente.description
    ):
        conflitti.append("descrizione diversa; conservata quella canonica")

    proprieta = copy.deepcopy(oggetto_canonico.properties or {})
    for chiave, valore in (oggetto_sorgente.properties or {}).items():
        if chiave not in proprieta:
            proprieta[chiave] = copy.deepcopy(valore)
            contatori["proprieta"] += 1
        elif proprieta[chiave] != valore:
            conflitti.append("proprieta' " + str(chiave) + " diversa; conservato il valore canonico")
    oggetto_canonico.properties = proprieta

    fatti_canonici = _valida_collezione(oggetto_canonico, "facts", canonica.riferimento)
    fatti_sorgenti = _valida_collezione(oggetto_sorgente, "facts", sorgente.riferimento)
    (
        oggetto_canonico.facts,
        contatori["fatti_aggiunti"],
        contatori["fatti_unificati"],
    ) = _unisci_ricordi(fatti_canonici, fatti_sorgenti, "fatto", conflitti)

    eventi_canonici = _valida_collezione(oggetto_canonico, "events", canonica.riferimento)
    eventi_sorgenti = _valida_collezione(oggetto_sorgente, "events", sorgente.riferimento)
    (
        oggetto_canonico.events,
        contatori["eventi_aggiunti"],
        contatori["eventi_unificati"],
    ) = _unisci_ricordi(eventi_canonici, eventi_sorgenti, "evento", conflitti)

    relazioni_canoniche = _valida_collezione(oggetto_canonico, "relationships", canonica.riferimento)
    relazioni_sorgenti = _valida_collezione(oggetto_sorgente, "relationships", sorgente.riferimento)
    oggetto_canonico.relationships = relazioni_canoniche + relazioni_sorgenti
    del oggetti[chiave_sorgente]

    toccate = {chiave_canonica}
    for proprietario, oggetto in oggetti.items():
        originali = list(oggetto.relationships or [])
        riscritte = []
        cambiata = proprietario == chiave_canonica
        for relazione_grezza in originali:
            relazione = _valida_relazione(relazione_grezza, _riferimento_chiave(proprietario))
            lontana_originale = _chiave_lontana(relazione)
            if lontana_originale == chiave_sorgente:
                relazione["entity_type"], relazione["entity_id"] = chiave_canonica
                contatori["riscritte"] += 1
                cambiata = True
            lontana = _chiave_lontana(relazione)
            coinvolta = proprietario == chiave_canonica or lontana_originale in {
                chiave_sorgente,
                chiave_canonica,
            }
            if coinvolta and lontana == proprietario:
                contatori["auto"] += 1
                cambiata = True
                continue
            riscritte.append(relazione)
        if cambiata:
            oggetto.relationships, unificate = _deduplica_relazioni(
                riscritte, _riferimento_chiave(proprietario), conflitti
            )
            contatori["relazioni_unificate"] += unificate
            toccate.add(proprietario)

    ora = _ora_iso()
    # Completa soltanto gli archi che ora coinvolgono il canonico. In questo
    # modo la fusione non riscrive eventuali difetti preesistenti e scollegati.
    for proprietario, oggetto in list(oggetti.items()):
        for relazione in list(oggetto.relationships or []):
            lontana = _chiave_lontana(relazione)
            if proprietario != chiave_canonica and lontana != chiave_canonica:
                continue
            destinazione = oggetti.get(lontana)
            if destinazione is None:
                raise ErroreManutenzione(
                    "relazione coinvolta nella fusione verso entita' inesistente: "
                    + _riferimento_chiave(lontana)
                )
            if _ha_reciproca(destinazione.relationships or [], proprietario, relazione):
                continue
            destinazione.relationships = [
                *(destinazione.relationships or []),
                _reciproca(relazione, proprietario=proprietario, lontana=lontana, ora=ora),
            ]
            contatori["reciproche"] += 1
            toccate.add(lontana)

    for chiave in sorted(toccate):
        oggetto = oggetti[chiave]
        oggetto.relationships, unificate = _deduplica_relazioni(
            list(oggetto.relationships or []), _riferimento_chiave(chiave), conflitti
        )
        contatori["relazioni_unificate"] += unificate
        oggetto.updated_at = ora

    _valida_grafo_fuso(oggetti, sorgente=chiave_sorgente, canonica=chiave_canonica)

    aggiornamenti = []
    for chiave in sorted(toccate):
        voce = per_chiave[chiave]
        aggiornamenti.append(
            AggiornamentoEntita(
                learning_id=voce.learning_id,
                riferimento=voce.riferimento,
                prima=copy.deepcopy(voce.contenuto_originale),
                dopo=oggetti[chiave].to_dict(),
            )
        )
    statistiche = StatisticheFusione(
        alias_aggiunti=contatori["alias"],
        fatti_aggiunti=contatori["fatti_aggiunti"],
        fatti_unificati=contatori["fatti_unificati"],
        eventi_aggiunti=contatori["eventi_aggiunti"],
        eventi_unificati=contatori["eventi_unificati"],
        proprieta_aggiunte=contatori["proprieta"],
        relazioni_riscritte=contatori["riscritte"],
        relazioni_unificate=contatori["relazioni_unificate"],
        auto_relazioni_rimosse=contatori["auto"],
        reciproche_aggiunte=contatori["reciproche"],
        righe_modificate=len(aggiornamenti),
        conflitti=tuple(dict.fromkeys(conflitti)),
    )
    return PianoFusione(
        sorgente=sorgente,
        canonica=canonica,
        aggiornamenti=tuple(aggiornamenti),
        statistiche=statistiche,
    )


def applica_piano(db: SqliteDb, piano: PianoFusione) -> None:
    """Aggiorna tutte le righe e cancella la sorgente in una transazione."""
    metadata = MetaData()
    try:
        tabella = Table(db.learnings_table_name, metadata, autoload_with=db.db_engine)
        attesi = {agg.learning_id: agg.prima for agg in piano.aggiornamenti}
        attesi[piano.sorgente.learning_id] = piano.sorgente.contenuto_originale
        with db.Session() as sessione, sessione.begin():
            righe = sessione.execute(
                select(tabella.c.learning_id, tabella.c.content).where(tabella.c.learning_id.in_(list(attesi)))
            ).fetchall()
            correnti = {str(riga.learning_id): riga.content for riga in righe}
            mancanti = sorted(set(attesi) - set(correnti))
            if mancanti:
                raise ErroreManutenzione("righe cambiate o scomparse prima della fusione: " + ", ".join(mancanti))
            alterate = sorted(learning_id for learning_id in attesi if correnti[learning_id] != attesi[learning_id])
            if alterate:
                raise ErroreManutenzione("stato cambiato dopo l'anteprima: " + ", ".join(alterate))

            istante = int(time.time())
            for aggiornamento in piano.aggiornamenti:
                risultato = sessione.execute(
                    tabella.update()
                    .where(tabella.c.learning_id == aggiornamento.learning_id)
                    .values(content=aggiornamento.dopo, updated_at=istante)
                )
                if risultato.rowcount != 1:
                    raise ErroreManutenzione("aggiornamento non applicato: " + aggiornamento.learning_id)
            cancellazione = sessione.execute(
                tabella.delete().where(tabella.c.learning_id == piano.sorgente.learning_id)
            )
            if cancellazione.rowcount != 1:
                raise ErroreManutenzione("sorgente non eliminata: " + piano.sorgente.learning_id)
    except ErroreManutenzione:
        raise
    except Exception as errore:
        raise ErroreManutenzione(
            "transazione annullata: " + type(errore).__name__ + ": " + str(errore)
        ) from errore


def verifica_piano(db: Any, namespace: str, piano: PianoFusione) -> None:
    entita, ignorate = carica_entita(db=db, namespace=namespace)
    if ignorate:
        raise ErroreManutenzione("verifica fallita: sono comparse righe malformate")
    indice = _valida_riferimenti(entita)
    if piano.sorgente.riferimento in indice:
        raise ErroreManutenzione("verifica fallita: la sorgente esiste ancora")
    if piano.canonica.riferimento not in indice:
        raise ErroreManutenzione("verifica fallita: la canonica non esiste")
    per_id = {voce.learning_id: voce for voce in entita}
    for aggiornamento in piano.aggiornamenti:
        voce = per_id.get(aggiornamento.learning_id)
        if voce is None or voce.contenuto_originale != aggiornamento.dopo:
            raise ErroreManutenzione("verifica fallita sulla riga " + aggiornamento.learning_id)
    oggetti = {_chiave_entita(voce.entita): voce.entita for voce in entita}
    _valida_grafo_fuso(
        oggetti,
        sorgente=_chiave_entita(piano.sorgente.entita),
        canonica=_chiave_entita(piano.canonica.entita),
    )


def _stato(voce: EntitaArchivio) -> str:
    return " (archiviata)" if voce.archiviata else ""


def stampa_esito(esito: EsitoAudit, namespace: str, mostra_tutte: bool = False) -> None:
    attive = sum(not voce.archiviata for voce in esito.entita)
    archiviate = len(esito.entita) - attive
    print("Namespace:", namespace)
    print("Entita' analizzate:", len(esito.entita), "- attive:", attive, "archiviate:", archiviate)

    if esito.righe_ignorate:
        print("Righe malformate ignorate:", len(esito.righe_ignorate))
        for learning_id in esito.righe_ignorate:
            print("  -", learning_id)

    if mostra_tutte and esito.entita:
        print()
        print("Inventario:")
        for voce in esito.entita:
            alias = list(getattr(voce.entita, "aliases", None) or [])
            suffisso = " - alias: " + ", ".join(str(a) for a in alias) if alias else ""
            print("  -", voce.riferimento, "-", voce.nome + _stato(voce) + suffisso)

    print()
    if not esito.candidati:
        print("Nessun candidato duplicato trovato con i criteri correnti.")
        return

    print("Candidati duplicati:", len(esito.candidati))
    for candidato in esito.candidati:
        print()
        print("[" + candidato.livello + "]")
        print("  A:", candidato.prima.riferimento, "-", candidato.prima.nome + _stato(candidato.prima))
        print("  B:", candidato.seconda.riferimento, "-", candidato.seconda.nome + _stato(candidato.seconda))
        for motivo in candidato.motivi:
            print("  -", motivo)

    print()
    print("L'audit non modifica nulla e non sceglie quale entita' conservare.")


def stampa_piano(piano: PianoFusione) -> None:
    statistiche = piano.statistiche
    aggiornamento_canonico = next(
        aggiornamento
        for aggiornamento in piano.aggiornamenti
        if aggiornamento.learning_id == piano.canonica.learning_id
    )
    print("Fusione proposta:")
    print("  sorgente:", piano.sorgente.riferimento, "-", piano.sorgente.nome + _stato(piano.sorgente))
    print("  canonica:", piano.canonica.riferimento, "-", piano.canonica.nome)
    print()
    print("Trasferimenti:")
    print("  alias aggiunti:", statistiche.alias_aggiunti)
    print(
        "  fatti aggiunti/unificati:",
        statistiche.fatti_aggiunti,
        "/",
        statistiche.fatti_unificati,
    )
    print(
        "  eventi aggiunti/unificati:",
        statistiche.eventi_aggiunti,
        "/",
        statistiche.eventi_unificati,
    )
    print("  proprieta' aggiunte:", statistiche.proprieta_aggiunte)
    print("  relazioni riscritte/unificate:", statistiche.relazioni_riscritte, "/", statistiche.relazioni_unificate)
    print("  auto-relazioni rimosse:", statistiche.auto_relazioni_rimosse)
    print("  reciproche ricostruite:", statistiche.reciproche_aggiunte)
    print("  righe che cambieranno:", statistiche.righe_modificate)
    print()
    print("Alias finali del canonico:")
    alias_finali = aggiornamento_canonico.dopo.get("aliases") or []
    print("  " + (", ".join(str(alias) for alias in alias_finali) if alias_finali else "(nessuno)"))

    fatti_canonici = {
        _identita_ricordo(fatto, "fatto") for fatto in (piano.canonica.entita.facts or [])
    }
    if piano.sorgente.entita.facts:
        print()
        print("Fatti della sorgente:")
        for fatto in piano.sorgente.entita.facts:
            azione = "unifica" if _identita_ricordo(fatto, "fatto") in fatti_canonici else "aggiunge"
            print("  -", azione + ":", fatto.get("content"))

    eventi_canonici = {
        _identita_ricordo(evento, "evento") for evento in (piano.canonica.entita.events or [])
    }
    if piano.sorgente.entita.events:
        print()
        print("Eventi della sorgente:")
        for evento in piano.sorgente.entita.events:
            azione = "unifica" if _identita_ricordo(evento, "evento") in eventi_canonici else "aggiunge"
            data = " [" + str(evento.get("date")) + "]" if evento.get("date") else ""
            print("  -", azione + ":", str(evento.get("content")) + data)

    print()
    print("Righe aggiornate:")
    for aggiornamento in piano.aggiornamenti:
        print("  -", aggiornamento.riferimento)
    if statistiche.conflitti:
        print()
        print("Conflitti risolti conservando il valore canonico:")
        for conflitto in statistiche.conflitti:
            print("  -", conflitto)
    print()
    print("La sorgente sara' eliminata dopo un backup verificato; il canonico restera' attivo.")


def costruisci_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manutenzione offline delle entita' di Ares")
    sottocomandi = parser.add_subparsers(dest="comando", required=True)
    audit = sottocomandi.add_parser("audit", help="trova possibili duplicati senza modificare lo stato")
    audit.add_argument("--user", default=config.DEFAULT_USER_ID, help="utente di cui analizzare le entita'")
    audit.add_argument("--all", action="store_true", help="mostra anche l'inventario completo")
    audit.add_argument(
        "--all-pairs",
        action="store_true",
        help="mostra anche ogni coppia dello stesso tipo priva di indizi automatici",
    )
    merge = sottocomandi.add_parser("merge", help="prepara o applica una fusione esplicita")
    merge.add_argument("--user", default=config.DEFAULT_USER_ID, help="utente proprietario delle entita'")
    merge.add_argument("--source", required=True, help="entita' da assorbire, nel formato tipo/id")
    merge.add_argument(
        "--into",
        "--canonical",
        dest="canonical",
        required=True,
        help="entita' canonica da conservare, nel formato tipo/id",
    )
    merge.add_argument(
        "--apply",
        action="store_true",
        help="dopo l'anteprima chiede conferma, crea un backup e applica la fusione",
    )
    return parser


def _esegui_audit(user_id: str, mostra_tutte: bool, tutte_le_coppie: bool) -> int:
    percorso = Path(config.DB_FILE)
    if not percorso.is_file():
        print("Nessun archivio di Ares trovato in", percorso)
        return 0

    namespace = namespace_entita(user_id)
    db = SqliteDb(db_file=str(percorso))
    esito = analizza(db=db, namespace=namespace, includi_tutte_le_coppie=tutte_le_coppie)
    stampa_esito(esito, namespace=namespace, mostra_tutte=mostra_tutte)
    return 0


def _esegui_merge(user_id: str, source: str, canonical: str, applica: bool) -> int:
    percorso = Path(config.DB_FILE)
    if not percorso.is_file():
        raise ErroreManutenzione("nessun archivio di Ares trovato in " + str(percorso))

    namespace = namespace_entita(user_id)
    db = SqliteDb(db_file=str(percorso))
    entita, ignorate = carica_entita(db=db, namespace=namespace)
    if ignorate:
        raise ErroreManutenzione(
            "la scansione contiene righe malformate ("
            + ", ".join(ignorate)
            + "); correggile prima di fondere"
        )
    piano = pianifica_fusione(
        entita=entita,
        riferimento_sorgente=source,
        riferimento_canonico=canonical,
    )
    stampa_piano(piano)
    if not applica:
        print()
        print("Anteprima soltanto: nessun dato e' stato modificato.")
        print("Per applicarla, ripeti lo stesso comando aggiungendo --apply.")
        return 0

    print()
    print("Per confermare scrivi esattamente:")
    print(piano.conferma)
    try:
        conferma = input("> ").strip()
    except EOFError:
        conferma = ""
    if conferma != piano.conferma:
        print("Conferma non corrispondente: fusione annullata.")
        return 1

    snapshot = crea_snapshot(tipo="pre-merge", acquisisci_lock=False)
    print("Backup verificato:", snapshot.name)
    try:
        applica_piano(db=db, piano=piano)
        verifica_piano(db=db, namespace=namespace, piano=piano)
    except Exception:
        print(
            "La fusione o la verifica finale non e' stata completata. Backup di sicurezza:",
            snapshot.name,
            file=sys.stderr,
        )
        raise
    print("Fusione completata e verificata:", piano.sorgente.riferimento, "->", piano.canonica.riferimento)
    print("Per tornare indietro: .venv/bin/python backup.py restore", snapshot.name)
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = costruisci_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.comando == "audit":
            with lock_stato(esclusivo=False):
                return _esegui_audit(
                    user_id=args.user,
                    mostra_tutte=args.all,
                    tutte_le_coppie=args.all_pairs,
                )
        if args.comando == "merge":
            with lock_stato(esclusivo=args.apply):
                return _esegui_merge(
                    user_id=args.user,
                    source=args.source,
                    canonical=args.canonical,
                    applica=args.apply,
                )
    except StatoOccupato as errore:
        print("Impossibile usare lo stato di Ares:", errore, file=sys.stderr)
        print("Attendi che backup, restore o manutenzione terminino e riprova.", file=sys.stderr)
        return 2
    except (ErroreManutenzione, ErroreBackup) as errore:
        print("Manutenzione rifiutata:", errore, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
