"""Composizione dell'assistente: il cablaggio finale dell'Agent Agno in un punto solo.

Riesporta anche i costruttori di `runtime` e `learning`, che prove e comandi
importano da qui.
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
from ares.agent.prompts import (
    descrizione,
    istruzioni_dalla_cartella,
    istruzioni_di_collaborazione,
    istruzioni_senza_terminale,
    istruzioni_sugli_strumenti,
    istruzioni_sul_quaderno,
    istruzioni_sull_ambiente,
    istruzioni_sulla_memoria,
    istruzioni_sulle_conversazioni,
)
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
from ares.config import Impostazioni, Percorsi, Politica
from ares.state.identita import Utente
from ares.state.sessioni import CHIAVE_CARTELLA, SessioneRiferimento, elenca

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
    percorsi: Percorsi,
    impostazioni: Impostazioni,
    politica: Politica,
    utente: Utente,
    session_id: str = "principale",
    debug: bool = False,
    interattivo: bool = True,
    modo: str | None = None,
) -> Agent:
    """Assembla l'assistente completo per un utente e una sessione.

    - `percorsi`: dove stanno stato, backup e cartella di lavoro.
    - `impostazioni`: con quali modelli parla la conversazione, e come.
    - `politica`: cosa impara, quanta cronologia vede, cosa mostra.
    - `utente`: identita' canonica; `utente.id` e' la chiave di profilo e memorie.
    - `modo`: chiave di `config.MODALITA`; vuoto vale `config.MODO_PREDEFINITO`.
    - `interattivo=False` (`ares -p`, pipe): niente post-hook ne' strumenti di
      apprendimento; il contesto gia' appreso entra comunque.

    Nessuno di questi ha un default letto da `config`: vedi "Dipendenze
    esplicite" in docs/architecture.md. Con lo spazio di lavoro acceso la
    sessione registra la sua cartella in `metadata`, letta da `ares resume` e
    `/sessioni`.
    """
    modo = modo or config.MODO_PREDEFINITO
    db = build_db(percorsi)
    # Passare Knowledge con il flag spento farebbe costruire comunque lo
    # store learned_knowledge nel namespace globale del framework.
    knowledge = build_knowledge(percorsi, impostazioni) if politica.apprendimento.intuizioni else None
    fs = build_filesystem(percorsi, utente)
    spazio = build_workspace(percorsi, politica, modo) if politica.workspace.attivo else None

    metadata = None
    precedenti: tuple[SessioneRiferimento, ...] = ()
    if spazio is not None:
        metadata = {CHIAVE_CARTELLA: str(spazio.root)}
        if politica.cronologia.sessioni_passate:
            precedenti = elenca(
                db,
                utente,
                ambito="nate_qui",
                cartella=spazio.root,
                escludi=session_id,
                limite=politica.cronologia.sessioni_nel_prompt,
            ).voci

    return Agent(
        name="Ares",
        add_name_to_context=True,
        description=descrizione(impostazioni, politica, interattivo=interattivo),
        model=build_chat_model(impostazioni),
        db=db,
        user_id=utente.id,
        session_id=session_id,
        metadata=metadata,
        tools=[fs.tools()] + ([spazio] if spazio is not None else []),
        offload_tool_results=build_result_store(fs) if config.OFFLOAD_TOOL_RESULTS else None,
        instructions=[
            *istruzioni_sull_ambiente(
                impostazioni=impostazioni,
                politica=politica,
                utente=utente,
                session_id=session_id,
                radice_lavoro=spazio.root if spazio is not None else None,
                modo=modo,
                interattivo=interattivo,
            ),
            *(
                []
                if interattivo
                else istruzioni_senza_terminale(spazio.root if spazio is not None else None, modo, politica=politica)
            ),
            *istruzioni_di_collaborazione(interattivo=interattivo),
            *istruzioni_sulla_memoria(politica=politica, interattivo=interattivo),
            *istruzioni_sugli_strumenti(
                spazio.root if spazio is not None else None, modo, politica=politica, interattivo=interattivo
            ),
            *istruzioni_dalla_cartella(spazio.root if spazio is not None else None, politica),
            *istruzioni_sulle_conversazioni(
                precedenti, cartella=spazio.root if spazio is not None else None, politica=politica
            ),
            *istruzioni_sul_quaderno(),
        ],
        learning=build_learning_machine(db, knowledge, utente, impostazioni, politica, strumenti=interattivo),
        post_hooks=[apprendi_a_run_completato] if interattivo else [],
        add_learnings_to_context=True,
        add_history_to_context=True,
        num_history_runs=politica.cronologia.turni,
        max_tool_calls_from_history=politica.cronologia.strumenti_dalla_cronologia,
        search_past_sessions=politica.cronologia.sessioni_passate,
        num_past_sessions_to_search=politica.cronologia.sessioni_ricerca,
        num_past_session_runs_in_search=politica.cronologia.sessioni_anteprima,
        read_chat_history=politica.cronologia.cronologia_chat,
        add_datetime_to_context=True,
        datetime_format=config.DATETIME_FORMAT,
        timezone_identifier="Europe/Rome",
        # Spento: l'unica cosa che accende e' la riga inglese "Use markdown to
        # format your answers", detta sopra in italiano. Il renderer della
        # CLI interpreta il Markdown comunque.
        markdown=False,
        # Ares e' local-first: nessun metadato di run deve uscire dal processo.
        telemetry=False,
        debug_mode=debug,
    )


if __name__ == "__main__":
    impostazioni = config.leggi_impostazioni()
    politica = config.leggi_politica()
    agent = build_assistant(config.leggi_percorsi(), impostazioni, politica, Utente.da_grezzo(config.DEFAULT_USER_ID))
    print("Assistente costruito.")
    print("Modello:", impostazioni.principale)
    macchina = agent.learning_machine
    assert macchina is not None
    print("Store attivi:", list(macchina.stores.keys()))
