"""Modelli immutabili condivisi dalla manutenzione delle entita'."""

from dataclasses import dataclass
from typing import Any

from agno.learn.schemas import EntityMemory


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
