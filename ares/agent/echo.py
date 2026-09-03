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


def fotografa(agent: Any) -> Fotografia:
    """Legge profilo e memorie dell'utente dell'agente.

    Tollera tutto cio' che puo' mancare - un agente senza macchina di
    apprendimento, uno store spento in `config.py`, un archivio ancora vuoto -
    restituendo una fotografia vuota: l'eco e' un di piu', e non deve poter
    impedire un turno.
    """
    macchina = getattr(agent, "learning_machine", None)
    user_id = getattr(agent, "user_id", None)
    if macchina is None or not user_id:
        return Fotografia()
    profilo = getattr(macchina, "user_profile_store", None)
    memorie = getattr(macchina, "user_memory_store", None)
    return Fotografia(
        profilo=_campi(profilo.get(user_id=user_id)) if profilo is not None else {},
        memorie=_memorie(memorie.get(user_id=user_id)) if memorie is not None else {},
    )


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
