"""Cosa e' entrato in memoria durante un turno, letto dagli archivi.

Le scritture negli store non passano da un punto solo. L'estrazione
automatica accende il modello locale dopo la risposta e scrive profilo e
memorie attraverso strumenti interni di Agno; `update_user_memory` fa lo
stesso durante il turno, su richiesta del modello. Intercettare quelle
scritture vorrebbe dire agganciarsi a funzioni private del framework, che
cambiano fra una minor e l'altra.

La fotografia non ha questo problema: legge i due store con le loro API
pubbliche prima del turno e dopo, e cio' che e' diverso e' cio' che il turno
ha scritto, da qualunque strada sia passato. Sono due letture SQLite in piu'
per turno, locali e senza modello.

La stessa lettura fatta prima del turno e' anche cio' che permette di
tornare indietro. Agno non offre una conferma su profilo e memorie -
`PROPOSE` vale per le sole intuizioni, `HITL` per nessuno store - quindi la
scrittura avviene comunque; ma gli store espongono `save` e `delete`, e
riscrivere cio' che si era letto prima del turno e' una conferma a
posteriori con lo stesso effetto di una a priori: cio' che l'utente non
vuole non sopravvive al turno. `istantanea` conserva gli oggetti come li
restituiscono gli store, `ripristina` li riscrive.

Solo profilo e memorie, cioe' cio' che e' durevole e attraversa le sessioni:
un'osservazione sbagliata entra in ogni conversazione futura, ed e' per
questo che va vista subito. Il contesto di sessione viene riscritto a ogni
turno per costruzione - riassunto e avanzamento cambiano sempre - e
stamparne la differenza ogni volta sarebbe la riga che si smette di leggere;
resta a portata con `/contesto`. Entita' e intuizioni si scrivono solo con
strumenti agentici (`remember_about`, `save_learning`), che il flusso del
turno mostra gia' con nome, argomenti ed esito.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Campi che il framework popola da se': identificativi e date. Non sono
# cio' che il modello ha appreso, e una data che cambia a ogni scrittura
# farebbe comparire il profilo fra le variazioni anche quando nessun campo
# e' cambiato.
CAMPI_DI_SERVIZIO = frozenset({"user_id", "session_id", "agent_id", "team_id", "created_at", "updated_at", "memories"})


@dataclass(frozen=True)
class Fotografia:
    """Profilo e memorie di un utente in un istante, ridotti a testo."""

    profilo: dict[str, str] = field(default_factory=dict)
    memorie: dict[str, str] = field(default_factory=dict)


def _testo(valore: Any) -> str:
    """Un valore come riga sola: senza a-capo, liste unite, vuoto se non c'e'."""
    if valore is None:
        return ""
    if isinstance(valore, (list, tuple)):
        return "; ".join(t for t in (_testo(v) for v in valore) if t)
    return " ".join(str(valore).split())


def _campi(oggetto: Any) -> dict[str, str]:
    """I campi popolati di uno schema Agno, senza la contabilita'."""
    if oggetto is None:
        return {}
    campi = {}
    for nome, valore in vars(oggetto).items():
        if nome in CAMPI_DI_SERVIZIO or nome.startswith("_"):
            continue
        testo = _testo(valore)
        if testo:
            campi[nome] = testo
    return campi


def _memorie(contenitore: Any) -> dict[str, str]:
    """Le memorie per identificativo. Una senza id vale per il suo testo."""
    memorie = {}
    for voce in getattr(contenitore, "memories", None) or []:
        if not isinstance(voce, dict):
            continue
        testo = _testo(voce.get("content"))
        if testo:
            memorie[str(voce.get("id") or testo)] = testo
    return memorie


@dataclass(frozen=True)
class Istantanea:
    """Profilo e memorie come li restituiscono gli store, per riscriverli.

    `None` vuol dire che lo store non c'era o non aveva niente per l'utente:
    ripristinare `None` cancella cio' che il turno ha creato.
    """

    profilo: Any = None
    memorie: Any = None


def _store(agent: Any) -> tuple[Any, Any, str | None]:
    """Gli store di profilo e memorie e l'utente, o `None` dove mancano."""
    macchina = getattr(agent, "learning_machine", None)
    user_id = getattr(agent, "user_id", None)
    if macchina is None or not user_id:
        return None, None, None
    return getattr(macchina, "user_profile_store", None), getattr(macchina, "user_memory_store", None), user_id


def istantanea(agent: Any) -> Istantanea:
    """Legge profilo e memorie dell'utente dell'agente, senza ridurli.

    Tollera tutto cio' che puo' mancare - un agente senza macchina di
    apprendimento, uno store spento in `config.py`, un archivio ancora vuoto -
    restituendo un'istantanea vuota: l'eco e' un di piu', e non deve poter
    impedire un turno.
    """
    profilo, memorie, user_id = _store(agent)
    return Istantanea(
        profilo=profilo.get(user_id=user_id) if profilo is not None else None,
        memorie=memorie.get(user_id=user_id) if memorie is not None else None,
    )


def riduci(stato: Istantanea) -> Fotografia:
    """L'istantanea come testo confrontabile."""
    return Fotografia(profilo=_campi(stato.profilo), memorie=_memorie(stato.memorie))


def fotografa(agent: Any) -> Fotografia:
    """Profilo e memorie dell'utente dell'agente, ridotti a testo."""
    return riduci(istantanea(agent))


def ripristina(agent: Any, stato: Istantanea) -> bool:
    """Riporta profilo e memorie a un'istantanea. Vero se ci e' riuscito.

    Riscrive per intero: `save` sostituisce la riga dell'utente, `delete`
    la toglie quando prima non c'era o era vuota. Passa dalle API pubbliche
    degli store, che inghiottono i propri errori e li scrivono nel log a
    livello debug; per questo il risultato non si presume, si rilegge: e'
    vero solo se la fotografia dopo il ripristino e' uguale a quella di
    prima del turno.
    """
    profilo, memorie, user_id = _store(agent)
    agent_id = getattr(agent, "id", None)
    for store, valore, vuoto in ((profilo, stato.profilo, _campi), (memorie, stato.memorie, _memorie)):
        if store is None:
            continue
        if valore is None or not vuoto(valore):
            store.delete(user_id=user_id)
        else:
            store.save(user_id, valore, agent_id=agent_id)
    return fotografa(agent) == riduci(stato)


def variazioni(prima: Fotografia, dopo: Fotografia) -> list[str]:
    """Le righe da mostrare, o nessuna se il turno non ha scritto niente.

    La prima riga riassume, le altre mostrano il testo intero: l'eco esiste
    per leggere cosa e' entrato in memoria, e una memoria troncata a meta'
    e' proprio la meta' che non si e' letta. Sono righe corte per istruzione
    - una memoria "comprensibile da sola", un campo del profilo - e al piu'
    `MAX_UPDATES_PER_RUN` per store.
    """
    righe: list[str] = []

    campi_cambiati = 0
    for nome in sorted(set(prima.profilo) | set(dopo.profilo)):
        if prima.profilo.get(nome) == dopo.profilo.get(nome):
            continue
        campi_cambiati += 1
        if nome in dopo.profilo:
            righe.append("   | profilo " + nome + ": " + dopo.profilo[nome])
        else:
            righe.append("   | profilo " + nome + ": (tolto)")

    nuove = modificate = tolte = 0
    for chiave, testo in dopo.memorie.items():
        if chiave not in prima.memorie:
            nuove += 1
            righe.append("   | + " + testo)
        elif prima.memorie[chiave] != testo:
            modificate += 1
            righe.append("   | ~ " + testo)
    for chiave, testo in prima.memorie.items():
        if chiave not in dopo.memorie:
            tolte += 1
            righe.append("   | - " + testo)

    if not righe:
        return []

    pezzi = []
    if campi_cambiati:
        pezzi.append("profilo " + str(campi_cambiati) + (" campo" if campi_cambiati == 1 else " campi"))
    conteggi = "".join(
        " " + segno + str(quanti) for segno, quanti in (("+", nuove), ("~", modificate), ("-", tolte)) if quanti
    )
    if conteggi:
        pezzi.append("memorie" + conteggi)
    return ["   appreso: " + ", ".join(pezzi), *righe]
