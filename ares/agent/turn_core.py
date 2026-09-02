"""Ciclo di un turno Ares, indipendente da terminale e futura web UI.

Il modulo traduce gli eventi specifici di Agno in un vocabolario piccolo e
stabile. Non importa Rich, Prompt Toolkit o la configurazione visuale: un
client decide come mostrare gli eventi e come risolvere una pausa, mentre il
core conserva la sequenza corretta ``run -> conferma -> continue_run``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from agno.run.agent import RunOutput


class TurnEventKind(StrEnum):
    PROCESSING_STARTED = "processing_started"
    RUN_STARTED = "run_started"
    MODEL_STARTED = "model_started"
    MODEL_COMPLETED = "model_completed"
    PRE_HOOK_STARTED = "pre_hook_started"
    PRE_HOOK_COMPLETED = "pre_hook_completed"
    POST_HOOK_STARTED = "post_hook_started"
    POST_HOOK_COMPLETED = "post_hook_completed"
    MEMORY_STARTED = "memory_started"
    MEMORY_COMPLETED = "memory_completed"
    SUMMARY_STARTED = "summary_started"
    SUMMARY_COMPLETED = "summary_completed"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_ERROR = "tool_error"
    CONTENT = "content"
    CONTENT_COMPLETED = "content_completed"
    RUN_COMPLETED = "run_completed"
    RUN_PAUSED = "run_paused"
    RUN_ERROR = "run_error"
    RUN_CANCELLED = "run_cancelled"
    OUTPUT = "output"
    OTHER = "other"


@dataclass(frozen=True)
class TurnEvent:
    """Evento neutro consegnato a qualunque client di Ares."""

    kind: TurnEventKind
    content: object = None
    tool: object = None
    error: object = None
    output: RunOutput | None = None
    source_name: str = ""


_EVENT_KIND = {
    "RunStarted": TurnEventKind.RUN_STARTED,
    "ModelRequestStarted": TurnEventKind.MODEL_STARTED,
    "ModelRequestCompleted": TurnEventKind.MODEL_COMPLETED,
    "PreHookStarted": TurnEventKind.PRE_HOOK_STARTED,
    "PreHookCompleted": TurnEventKind.PRE_HOOK_COMPLETED,
    "PostHookStarted": TurnEventKind.POST_HOOK_STARTED,
    "PostHookCompleted": TurnEventKind.POST_HOOK_COMPLETED,
    "MemoryUpdateStarted": TurnEventKind.MEMORY_STARTED,
    "MemoryUpdateCompleted": TurnEventKind.MEMORY_COMPLETED,
    "SessionSummaryStarted": TurnEventKind.SUMMARY_STARTED,
    "SessionSummaryCompleted": TurnEventKind.SUMMARY_COMPLETED,
    "ToolCallStarted": TurnEventKind.TOOL_STARTED,
    "ToolCallCompleted": TurnEventKind.TOOL_COMPLETED,
    "ToolCallError": TurnEventKind.TOOL_ERROR,
    "RunContent": TurnEventKind.CONTENT,
    "RunContentCompleted": TurnEventKind.CONTENT_COMPLETED,
    "RunCompleted": TurnEventKind.RUN_COMPLETED,
    "RunPaused": TurnEventKind.RUN_PAUSED,
    "RunError": TurnEventKind.RUN_ERROR,
    "RunCancelled": TurnEventKind.RUN_CANCELLED,
}


def normalize_events(raw_events: Iterable[object]) -> Iterator[TurnEvent]:
    """Traduce uno stream Agno senza scartare gli eventi ancora ignoti."""
    for raw_event in raw_events:
        if isinstance(raw_event, RunOutput):
            yield TurnEvent(
                TurnEventKind.OUTPUT,
                output=raw_event,
                source_name=type(raw_event).__name__,
            )
            continue

        source_name = str(getattr(raw_event, "event", "") or "")
        yield TurnEvent(
            _EVENT_KIND.get(source_name, TurnEventKind.OTHER),
            content=getattr(raw_event, "content", None),
            tool=getattr(raw_event, "tool", None),
            error=getattr(raw_event, "error", None),
            source_name=source_name,
        )


class TurnEngine:
    """Avvia e riprende run Agno esponendo soltanto eventi normalizzati."""

    def __init__(self, agent) -> None:
        self.agent = agent

    def start(self, text: str) -> Iterator[TurnEvent]:
        # Questo yield precede perfino la chiamata ad ``agent.run``: anche
        # un provider che facesse lavoro prima di restituire l'iteratore non
        # lascerebbe il client senza stato di attivita'.
        yield TurnEvent(TurnEventKind.PROCESSING_STARTED)
        yield from normalize_events(
            self.agent.run(
                text,
                stream=True,
                stream_events=True,
                yield_run_output=True,
            )
        )

    def resume(self, run_output: RunOutput) -> Iterator[TurnEvent]:
        yield TurnEvent(TurnEventKind.PROCESSING_STARTED)
        yield from normalize_events(
            self.agent.continue_run(
                run_response=run_output,
                requirements=run_output.requirements,
                stream=True,
                stream_events=True,
                yield_run_output=True,
            )
        )


def consume_events(
    events: Iterable[TurnEvent],
    on_event: Callable[[TurnEvent], None],
) -> RunOutput | None:
    """Consegna tutti gli eventi e restituisce l'ultimo output del run."""
    output = None
    for event in events:
        on_event(event)
        if event.kind is TurnEventKind.OUTPUT:
            output = event.output
    return output


def run_turn_cycle(
    agent,
    text: str,
    *,
    on_event: Callable[[TurnEvent], None],
    resolve_pause: Callable[[RunOutput], int],
) -> RunOutput | None:
    """Esegue un turno completo, comprese tutte le riprese dopo una pausa.

    ``resolve_pause`` modifica i requirement Agno chiamando ``confirm`` o
    ``reject`` e restituisce quanti ne ha risolti. Zero interrompe il ciclo:
    evita di riprendere all'infinito una pausa sconosciuta al client.
    """
    engine = TurnEngine(agent)
    output = consume_events(engine.start(text), on_event)

    while output is not None and output.is_paused:
        if resolve_pause(output) == 0:
            return output
        output = consume_events(engine.resume(output), on_event)

    return output
