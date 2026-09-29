"""Cosa e' entrato in memoria durante un turno, e come annullarlo.

Profilo e memorie si scrivono per piu' strade (estrazione automatica,
`update_user_memory`), tutte interne ad Agno. Invece di intercettarle, il
modulo legge i due store con le API pubbliche prima e dopo il turno: la
differenza e' cio' che il turno ha scritto. `istantanea` conserva gli
oggetti letti prima, `ripristina` li riscrive se l'utente rifiuta.

Limite: e' una conferma a posteriori. Se il processo muore fra la scrittura
e la risposta (kill, crash, terminale chiuso) la scrittura resta. La
scrittura differita e' la voce 3 di `docs/ROADMAP.md`.

Solo profilo e memorie, perche' sono durevoli e attraversano le sessioni.
Il contesto di sessione cambia a ogni turno per costruzione (si legge con
`/contesto`); entita' e intuizioni passano da strumenti agentici, che il
flusso del turno mostra gia'.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Identificativi e date popolati dal framework: non sono appresi, e una
# data aggiornata a ogni scrittura segnerebbe il profilo come cambiato.
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

    Se manca qualcosa (macchina, store, archivio) restituisce un'istantanea
    vuota: l'eco non deve mai impedire un turno.
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

    Riscrive per intero con `save`, o `delete` se prima non c'era niente.
    Gli store inghiottono i propri errori, quindi l'esito si verifica
    rileggendo e confrontando le fotografie.
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

    La prima riga riassume, le altre riportano il testo intero, senza
    troncare: l'eco serve a leggere davvero cosa e' entrato in memoria.
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
