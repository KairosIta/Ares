"""Facciata di composizione dell'assistente personale Ares.

I componenti runtime, il ciclo di apprendimento e le istruzioni vivono in
moduli separati. Questo file conserva gli import pubblici storici e rende
visibile in un solo punto il cablaggio finale dell'Agent Agno.
"""

from agno.agent import Agent

from ares import config
from ares.agent.learning import (
    AresLearningMachine,
    AresSessionContextStore,
    apprendi_a_run_completato,
    build_learning_machine,
    build_session_context_store,
)
from ares.agent.prompts import istruzioni_sugli_strumenti
from ares.agent.runtime import (
    AresWorkspace,
    build_chat_model,
    build_db,
    build_filesystem,
    build_knowledge,
    build_learning_model,
    build_result_store,
    build_workspace,
)

__all__ = [
    "AresLearningMachine",
    "AresSessionContextStore",
    "AresWorkspace",
    "apprendi_a_run_completato",
    "build_assistant",
    "build_chat_model",
    "build_db",
    "build_filesystem",
    "build_knowledge",
    "build_learning_machine",
    "build_learning_model",
    "build_result_store",
    "build_session_context_store",
    "build_workspace",
    "istruzioni_sugli_strumenti",
]


def build_assistant(
    user_id: str = config.DEFAULT_USER_ID,
    session_id: str = "principale",
    debug: bool = False,
) -> Agent:
    """Assembla l'assistente completo senza nascondere dipendenze globali."""
    db = build_db()
    # Passare Knowledge con il flag spento farebbe costruire comunque lo
    # store learned_knowledge nel namespace globale del framework.
    knowledge = build_knowledge() if config.LEARN_KNOWLEDGE else None
    fs = build_filesystem(user_id)
    spazio = build_workspace() if config.WORKSPACE else None

    return Agent(
        name="Ares",
        add_name_to_context=True,
        description=(
            "Sei Ares, l'assistente personale di una sola persona. Giri "
            "interamente sulla sua macchina: nessuna delle vostre conversazioni "
            "esce di qui, e non c'e' nessun servizio remoto dietro di te. "
            "Ricordi da una conversazione all'altra, e cio' che sai di lei l'hai "
            "imparato parlandole."
        ),
        model=build_chat_model(),
        db=db,
        user_id=user_id,
        session_id=session_id,
        tools=[fs.tools()] + ([spazio] if spazio is not None else []),
        offload_tool_results=build_result_store(fs) if config.OFFLOAD_TOOL_RESULTS else None,
        instructions=[
            "Rispondi in italiano, sempre, qualunque sia la lingua della domanda.",
            "Adatta il livello di dettaglio a cio' che sai dell'utente: non "
            "spiegare le basi di un ambito in cui e' gia' competente.",
            "Quando una risposta dipende da qualcosa che l'utente ti ha detto "
            "in passato, dillo esplicitamente. Vedere da dove viene una "
            "risposta e' quello che rende la memoria affidabile.",
            "Se non sai una cosa, dillo invece di ricostruirla per verosimiglianza.",
            *istruzioni_sugli_strumenti(spazio.root if spazio is not None else None),
            "Quando salvi un'intuizione, scrivila in italiano, e salvala solo se "
            "sara' utile in una conversazione futura su un argomento diverso. Una "
            "risposta a una domanda specifica non e' un'intuizione; il criterio che "
            "ha portato a quella risposta lo e'.",
            fs.instructions(),
        ],
        learning=build_learning_machine(db=db, knowledge=knowledge, user_id=user_id),
        post_hooks=[apprendi_a_run_completato],
        add_learnings_to_context=True,
        add_history_to_context=True,
        num_history_runs=config.NUM_HISTORY_RUNS,
        max_tool_calls_from_history=config.MAX_TOOL_CALLS_FROM_HISTORY,
        search_past_sessions=config.SEARCH_PAST_SESSIONS,
        num_past_sessions_to_search=config.PAST_SESSIONS_LIMIT,
        num_past_session_runs_in_search=config.PAST_SESSION_RUNS_PREVIEW,
        read_chat_history=config.READ_CHAT_HISTORY,
        add_datetime_to_context=True,
        datetime_format=config.DATETIME_FORMAT,
        timezone_identifier="Europe/Rome",
        markdown=True,
        # Ares e' local-first: nessun metadato di run deve uscire dal processo.
        telemetry=False,
        debug_mode=debug,
    )


if __name__ == "__main__":
    agent = build_assistant()
    print("Assistente costruito.")
    print("Modello:", config.MAIN_MODEL)
    macchina = agent.learning_machine
    assert macchina is not None
    print("Store attivi:", list(macchina.stores.keys()))
