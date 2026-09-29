"""Un turno completo, con le garanzie che valgono per ogni client.

La sequenza e' sempre la stessa, sotto il lock del turno dell'utente:
fotografia di profilo e memorie, turno (con le pause per autorizzare gli
strumenti), variazioni, conferma degli apprendimenti e, se l'utente
rifiuta, ripristino. Il client decide solo come mostrare e come chiedere,
attraverso `ClienteTurno`; qui non si stampa niente.

Il ripristino e' a posteriori: vedi i limiti in `agent/echo.py`.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Protocol

from agno.run.agent import RunOutput

from ares import config
from ares.agent.echo import fotografa, istantanea, riduci, ripristina, variazioni
from ares.agent.turn_core import TurnEvent, run_turn_cycle
from ares.config import Percorsi, Politica
from ares.state.identita import Utente
from ares.state.lock import lock_turno


class ClienteTurno(Protocol):
    """Cio' che un'interfaccia offre al turno: mostrare e chiedere."""

    def flusso(self) -> AbstractContextManager[Callable[[TurnEvent], None]]:
        """Apre la presentazione del turno e restituisce chi riceve gli eventi.

        Il contesto si chiude anche su eccezione, prima che il turno la gestisca.
        """
        ...

    def risolvi_pausa(self, output: RunOutput) -> int:
        """Conferma o rifiuta i requirement in pausa; quanti ne ha risolti."""
        ...

    def pausa_irrisolta(self) -> None:
        """Il turno resta in pausa per qualcosa che il client non sa chiedere."""
        ...

    def interrotto(self) -> None:
        """Ctrl-C fuori da cio' che Agno gestisce da se'."""
        ...

    def guasto(self, errore: Exception) -> None:
        """Un errore fuori da cio' che Agno gestisce da se'."""
        ...

    def apprendimenti(self, righe: list[str], *, chiedi: bool) -> bool:
        """Mostra cio' che il turno ha scritto in memoria; con `chiedi`, se tenerlo.

        Vero per tenere. Senza `chiedi` il valore restituito e' ignorato.
        """
        ...


@dataclass(frozen=True)
class EsitoTurno:
    """Come e' finito un turno.

    `ripristino` e' `None` se non c'era niente da annullare, altrimenti dice
    se profilo e memorie sono tornati davvero come prima del turno.
    """

    risposta: RunOutput | None
    appreso: tuple[str, ...] = ()
    ripristino: bool | None = None


def esegui_turno(percorsi: Percorsi, politica: Politica, agent: Any, testo: str, cliente: ClienteTurno) -> EsitoTurno:
    """Esegue un turno serializzato con le altre conversazioni dello stesso utente.

    Il lock copre tutta la sequenza, conferma compresa: un'altra chat non
    scrive in memoria mentre l'utente decide se tenere cio' che vede.
    """
    utente = Utente.da_grezzo(getattr(agent, "user_id", None) or config.DEFAULT_USER_ID)
    with lock_turno(percorsi, utente):
        return _turno_protetto(politica, agent, testo, cliente)


def _turno_protetto(politica: Politica, agent: Any, testo: str, cliente: ClienteTurno) -> EsitoTurno:
    """Il turno con una rete per cio' che Agno non prende.

    Agno trasforma da se' Ctrl-C e guasti dentro lo streaming negli eventi
    `RunCancelled` e `RunError`. Qui si copre il resto: costruire la
    chiamata, risolvere le conferme, `confirm()` e `reject()`. Anche dopo un
    errore le memorie gia' scritte passano da eco e conferma.
    """
    # Prima del turno, non del post-hook: `update_user_memory` scrive durante il run.
    prima = istantanea(agent) if politica.mostra.apprendimenti else None
    risposta = None
    try:
        with cliente.flusso() as su_evento:
            risposta = run_turn_cycle(agent, testo, on_event=su_evento, resolve_pause=cliente.risolvi_pausa)
        if risposta is not None and risposta.is_paused:
            cliente.pausa_irrisolta()
    except KeyboardInterrupt:
        cliente.interrotto()
    except Exception as errore:
        cliente.guasto(errore)

    if prima is None:
        return EsitoTurno(risposta)
    righe = variazioni(riduci(prima), fotografa(agent))
    if not righe:
        return EsitoTurno(risposta)
    chiedi = politica.mostra.conferma_apprendimenti
    tenere = cliente.apprendimenti(righe, chiedi=chiedi)
    ripristino = None if tenere or not chiedi else ripristina(agent, prima)
    return EsitoTurno(risposta, tuple(righe), ripristino)
