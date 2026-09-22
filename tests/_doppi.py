"""
I doppi che piu' prove costruivano uguali
=========================================

Un modello finto e la tool call che gli si mette in bocca. Non stanno in
`_comune.py`, che promette di non importare niente oltre la libreria standard:
qui c'e' un pezzo di Agno - `Model`, `ModelResponse` - e il posto giusto e'
accanto alle prove che lo usano. La regola di `_comune.py` vale anche qui, e
per la stessa ragione: questo modulo non importa `ares` e non tocca `config`,
quindi si puo' importare prima di `prepara_ambiente`.

Agno chiama il modello in quattro modi - `invoke`, `ainvoke`,
`invoke_stream`, `ainvoke_stream` - a seconda del percorso, e il corpo di
quei metodi non cambia da una prova all'altra: cambia il copione, che arriva
dal costruttore. `agno_contract_test.py` e `session_retention_test.py` ne
avevano due copie, identiche in tutto tranne il nome.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

from agno.models.base import Model
from agno.models.message import MessageMetrics
from agno.models.response import ModelResponse


def tool_call(nome: str, **argomenti: Any) -> dict[str, Any]:
    """Una tool call nella forma in cui Agno la mette nei messaggi."""
    return {
        "id": "call-" + nome,
        "type": "function",
        "function": {"name": nome, "arguments": json.dumps(argomenti)},
    }


class ModelloACopione(Model):
    """Risponde con le tool call decise dalla prova, poi conclude con "fatto".

    Ogni chiamata consuma una voce del copione; esaurito, il modello chiude
    il turno. Lo stesso oggetto vale per `invoke` e per `invoke_stream`,
    perche' `turn_core` usa lo streaming e la prova deve attraversare quella
    via: un doppio che rispondesse solo al primo lascerebbe scoperto il
    secondo.
    """

    def __init__(self, nome: str, copione: list[list[dict[str, Any]]] | None = None) -> None:
        super().__init__(id=nome, name=nome, provider="test")
        self.copione = list(copione or [])
        self.chiamate = 0

    def _prossima(self) -> ModelResponse:
        self.chiamate += 1
        if self.copione:
            return ModelResponse(role="assistant", tool_calls=self.copione.pop(0), response_usage=MessageMetrics())
        return ModelResponse(role="assistant", content="fatto", response_usage=MessageMetrics())

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._prossima()

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._prossima()

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[ModelResponse]:
        yield self._prossima()

    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ModelResponse]:
        yield self._prossima()

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response
