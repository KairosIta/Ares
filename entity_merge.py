"""Pianificazione, applicazione e verifica delle fusioni di entita'."""

import copy
import hashlib
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from agno.db.sqlite import SqliteDb
from agno.learn.schemas import EntityMemory
from sqlalchemy import MetaData, Table, select

from entity_audit import carica_entita, normalizza_testo
from entity_models import (
    AggiornamentoEntita,
    EntitaArchivio,
    ErroreManutenzione,
    PianoFusione,
    StatisticheFusione,
)


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
    raise ErroreManutenzione(ruolo + " inesistente: " + repr(riferimento) + ". Riferimenti disponibili: " + disponibili)


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


def identita_ricordo(record: dict[str, Any], campo: str) -> tuple[Any, ...]:
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
    risultato: list[dict[str, Any]] = []
    # Le due mappe non conservano record: `per_identita` porta all'indice in
    # `risultato`, `per_id` all'identita' gia' vista per quell'id.
    per_identita: dict[tuple[Any, ...], int] = {}
    per_id: dict[str, tuple[Any, ...]] = {}
    aggiunti = 0
    unificati = 0

    for viene_dalla_sorgente, raccolta in ((False, canonici), (True, sorgenti)):
        for record in raccolta:
            identita = identita_ricordo(record, campo)
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
    risultato: list[dict[str, Any]] = []
    per_chiave: dict[tuple[str, str, str, str], int] = {}
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


def _id_relazione(proprietario: tuple[str, str], lontana: tuple[str, str], relazione: str, direzione: str) -> str:
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
                    "relazione coinvolta nella fusione verso entita' inesistente: " + _riferimento_chiave(lontana)
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
    for nome, valore in (oggetto_sorgente.properties or {}).items():
        if nome not in proprieta:
            proprieta[nome] = copy.deepcopy(valore)
            contatori["proprieta"] += 1
        elif proprieta[nome] != valore:
            conflitti.append("proprieta' " + str(nome) + " diversa; conservato il valore canonico")
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
                    "relazione coinvolta nella fusione verso entita' inesistente: " + _riferimento_chiave(lontana)
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
        raise ErroreManutenzione("transazione annullata: " + type(errore).__name__ + ": " + str(errore)) from errore


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
