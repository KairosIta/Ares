"""Doppi condivisi fra piu' prove
==============================

Un modello finto e la tool call che gli si mette in bocca. Stanno qui e non
in `_comune.py` perche' importano Agno; come `_comune.py`, non importano
`ares` ne' `config`, quindi si possono importare prima di
`prepara_ambiente`.

Il corpo dei quattro metodi con cui Agno chiama il modello (`invoke`,
`ainvoke`, `invoke_stream`, `ainvoke_stream`) e' sempre uguale: cambia il
copione, che arriva dal costruttore.
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

    Ogni chiamata consuma una voce del copione; esaurito, il modello chiude il
    turno. Risponde anche in streaming, perche' `turn_core` usa quella via.
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
