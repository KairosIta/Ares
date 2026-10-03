"""Gli interni di Agno che Ares usa, in un posto solo.

Ogni nome privato di Agno passa da qui, e `INTERNI` li elenca: aggiornando
Agno si rilegge questo file, e `tests/agno_contract_test.py` fallisce col
nome esatto se uno manca. Il vincolo stretto su Agno nel pyproject esiste per
questi nomi.
"""

from typing import Any

from agno.models.message import Message
from agno.models.ollama import Ollama
from agno.models.response import ModelResponse

from ares.agent.scaffale import visibili

# (modulo, oggetto, attributo): cio' che deve esistere nella versione di Agno
# installata. Attributo vuoto: basta l'oggetto.
INTERNI: tuple[tuple[str, str, str], ...] = (
    ("agno.learn.stores.session_context", "SessionContextStore", "_build_functions_for_model"),
    ("agno.learn.stores.user_profile", "UserProfileStore", "_build_functions_for_model"),
    ("agno.learn.stores.user_memory", "UserMemoryStore", "_build_functions_for_model"),
    ("agno.learn.stores.user_memory", "UserMemoryStore", "_should_expose_tools"),
    ("agno.learn.stores.session_context", "SessionContextStore", "_get_system_message"),
    ("agno.learn.stores.session_context", "SessionContextStore", "_get_extraction_tools"),
    ("agno.learn.stores.session_context", "SessionContextStore", "_aget_extraction_tools"),
    ("agno.learn.stores.entity_memory", "EntityMemoryStore", "_should_expose_tools"),
    ("agno.agent._tools", "determine_tools_for_model", ""),
    ("agno.learn.machine", "_filter_store_kwargs", ""),
    ("agno.offload.tools", "OFFLOAD_INSTRUCTION", ""),
    ("agno.tools.workspace", "Workspace", "_check_read_before_write"),
    ("agno.models.ollama", "Ollama", "get_request_params"),
    ("agno.models.ollama", "Ollama", "_format_message"),
    ("agno.models.ollama", "Ollama", "_parse_provider_response"),
    ("agno.models.ollama", "Ollama", "_parse_provider_response_delta"),
)


class FunzioniRitoccate:
    """Mixin per uno store di `agno.learn`: `ritocca` le `Function` date al modello.

    Agno le costruisce in `_build_functions_for_model`, privato, e le usa
    subito per l'estrazione: e' l'unico punto in cui cambiarle. Va prima
    della classe di Agno nelle basi.
    """

    def _build_functions_for_model(self, *args: Any, **kwargs: Any) -> list[Any]:
        funzioni = super()._build_functions_for_model(*args, **kwargs)  # type: ignore[misc]
        return self.ritocca(funzioni)

    def ritocca(self, funzioni: list[Any]) -> list[Any]:
        return funzioni


def prompt_di_estrazione(store: Any, **kwargs: Any) -> Message:
    """Il system message con cui uno store di Agno estrae: `_get_system_message`."""
    return store._get_system_message(**kwargs)


def strumenti_di_estrazione(store: Any, **kwargs: Any) -> list[Any]:
    """Le funzioni con cui uno store di Agno salva cio' che estrae: `_get_extraction_tools`."""
    return store._get_extraction_tools(**kwargs)


async def astrumenti_di_estrazione(store: Any, **kwargs: Any) -> list[Any]:
    """Come `strumenti_di_estrazione`, nella versione asincrona dello store."""
    return await store._aget_extraction_tools(**kwargs)


def funzioni_di_estrazione(store: Any, strumenti: list[Any]) -> list[Any]:
    """Le `Function` date al modello, ritoccate se lo store usa `FunzioniRitoccate`."""
    return store._build_functions_for_model(tools=strumenti)


def strumenti_esposti(store: Any) -> bool:
    """Vero se lo store da' strumenti all'agente, secondo la propria configurazione."""
    return bool(store._should_expose_tools)


def elabora(store: Any, contesto: dict[str, Any]) -> None:
    """Un passo di `LearningMachine.process`: `store.process` con gli argomenti che accetta."""
    from agno.learn.machine import _filter_store_kwargs

    store.process(**_filter_store_kwargs(store.process, contesto))


def funzioni_per_modello(agent: Any, **kwargs: Any) -> Any:
    """Gli strumenti di un turno come li riceve il modello: `determine_tools_for_model`."""
    from agno.agent._tools import determine_tools_for_model

    return determine_tools_for_model(agent, **kwargs)


def sostituisci_istruzione_risultati(testo: str) -> None:
    """Mette `testo` al posto della riga che Agno aggiunge quando c'e' l'offload.

    Agno non ha un'opzione per toglierla: la legge dal modulo a ogni system
    message, quindi basta cambiarla li'. Vale per tutto il processo, ed e'
    idempotente.
    """
    import agno.offload.tools

    agno.offload.tools.OFFLOAD_INSTRUCTION = testo


def _pensiero(risposta: Any) -> str | None:
    messaggio = risposta.get("message") if risposta is not None else None
    return (messaggio.get("thinking") if messaggio is not None else None) or None


class OllamaConRagionamento(Ollama):
    """`Ollama` che conserva il ragionamento del modello e glielo rimanda.

    Agno legge da Ollama solo testo e tool call: il campo `thinking` va perso
    e, quando il turno prosegue dopo uno strumento, il renderer del modello
    mostra un ragionamento vuoto. Qwen3.8 9B lo imita e risponde dentro il
    ragionamento, lasciando vuota la risposta. Qui il ragionamento diventa
    `reasoning_content` del messaggio e torna nel campo `thinking`
    dell'API; se usarlo lo decide il renderer di ciascun modello.

    Con uno `scaffale` (`agent/scaffale.py`) gli strumenti che tiene
    nascosti non arrivano al modello. Agno chiama `get_request_params` a
    ogni richiesta, anche fra una chiamata di strumento e l'altra dello
    stesso turno: un gruppo attivato compare alla richiesta successiva.
    """

    scaffale: Any = None

    def get_request_params(self, tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        return super().get_request_params(tools=visibili(tools, self.scaffale) if tools else tools)

    def _format_message(self, message: Message, compress_tool_results: bool = False) -> dict[str, Any]:
        formattato = super()._format_message(message, compress_tool_results)
        if message.role == "assistant" and message.reasoning_content:
            formattato["thinking"] = message.reasoning_content
        return formattato

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        risposta = super()._parse_provider_response(response, **kwargs)
        if risposta.reasoning_content is None:
            risposta.reasoning_content = _pensiero(response)
        return risposta

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        risposta = super()._parse_provider_response_delta(response)
        risposta.reasoning_content = _pensiero(response)
        return risposta
