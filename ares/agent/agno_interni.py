"""Gli interni di Agno che Ares usa, in un posto solo.

Ogni nome privato di Agno passa da qui, e `INTERNI` li elenca: aggiornando
Agno si rilegge questo file, e `tests/agno_contract_test.py` fallisce col
nome esatto se uno manca. Il vincolo stretto su Agno nel pyproject esiste per
questi nomi.
"""

from typing import Any

# (modulo, oggetto, attributo): cio' che deve esistere nella versione di Agno
# installata. Attributo vuoto: basta l'oggetto.
INTERNI: tuple[tuple[str, str, str], ...] = (
    ("agno.learn.stores.session_context", "SessionContextStore", "_build_functions_for_model"),
    ("agno.learn.stores.user_profile", "UserProfileStore", "_build_functions_for_model"),
    ("agno.learn.stores.user_memory", "UserMemoryStore", "_build_functions_for_model"),
    ("agno.learn.stores.user_memory", "UserMemoryStore", "_should_expose_tools"),
    ("agno.learn.stores.entity_memory", "EntityMemoryStore", "_should_expose_tools"),
    ("agno.agent._tools", "determine_tools_for_model", ""),
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


def strumenti_esposti(store: Any) -> bool:
    """Vero se lo store da' strumenti all'agente, secondo la propria configurazione."""
    return bool(store._should_expose_tools)


def funzioni_per_modello(agent: Any, **kwargs: Any) -> Any:
    """Gli strumenti di un turno come li riceve il modello: `determine_tools_for_model`."""
    from agno.agent._tools import determine_tools_for_model

    return determine_tools_for_model(agent, **kwargs)
