"""Configurazione e adattamenti del ciclo di apprendimento di Ares."""

from agno.db.sqlite import SqliteDb
from agno.knowledge.knowledge import Knowledge
from agno.learn import (
    EntityMemoryConfig,
    LearnedKnowledgeConfig,
    LearningMachine,
    LearningMode,
    SessionContextConfig,
    UserMemoryConfig,
    UserProfileConfig,
)
from agno.learn.stores import SessionContextStore
from agno.models.ollama import Ollama
from agno.utils.log import log_warning

import config
from assistant_runtime import build_learning_model
from schemas import AresMemories, AresProfile
from stores import namespace_entita, namespace_utente


class AresLearningMachine(LearningMachine):
    """Estrae apprendimenti soltanto quando il run e' davvero concluso.

    Anche Agno 3.0.1 avvia ``LearningMachine.process`` in background prima
    della chiamata al modello, usando una fotografia dei messaggi. Un run in
    pausa per conferma non genera una seconda estrazione dopo
    ``continue_run``. Il collegamento ``learning=`` resta necessario per
    contesto, istruzioni e strumenti; qui si disattiva solo il callback
    anticipato, sostituito dal post-hook sul RunOutput completo.
    """

    def process(self, *args, **kwargs) -> None:
        return None

    async def aprocess(self, *args, **kwargs) -> None:
        return None

    def process_completed_run(self, *args, **kwargs) -> None:
        super().process(*args, **kwargs)


class AresSessionContextStore(SessionContextStore):
    """Riprova soltanto una tool call di contesto che non ha scritto nulla."""

    last_extraction_attempts = 0

    def _extract_once(self, *args, **kwargs) -> str:
        return super().extract_and_save(*args, **kwargs)

    async def _aextract_once(self, *args, **kwargs) -> str:
        return await super().aextract_and_save(*args, **kwargs)

    def extract_and_save(self, *args, **kwargs) -> str:
        massimo = 1 + max(0, config.SESSION_CONTEXT_RETRIES)
        risultato = "No updates needed"
        self.last_extraction_attempts = 0
        for tentativo in range(1, massimo + 1):
            risultato = self._extract_once(*args, **kwargs)
            self.last_extraction_attempts = tentativo
            if self.context_updated:
                return risultato
            if tentativo < massimo:
                log_warning(
                    "Session context non salvato: ripeto l'estrazione "
                    + str(tentativo)
                    + "/"
                    + str(config.SESSION_CONTEXT_RETRIES)
                )
        log_warning("Session context non salvato dopo " + str(self.last_extraction_attempts) + " tentativi")
        return risultato

    async def aextract_and_save(self, *args, **kwargs) -> str:
        massimo = 1 + max(0, config.SESSION_CONTEXT_RETRIES)
        risultato = "No updates needed"
        self.last_extraction_attempts = 0
        for tentativo in range(1, massimo + 1):
            risultato = await self._aextract_once(*args, **kwargs)
            self.last_extraction_attempts = tentativo
            if self.context_updated:
                return risultato
            if tentativo < massimo:
                log_warning(
                    "Session context non salvato: ripeto l'estrazione "
                    + str(tentativo)
                    + "/"
                    + str(config.SESSION_CONTEXT_RETRIES)
                )
        log_warning("Session context non salvato dopo " + str(self.last_extraction_attempts) + " tentativi")
        return risultato


def build_session_context_store(db: SqliteDb, model: Ollama) -> AresSessionContextStore:
    """Costruisce lo store di contesto con retry mirato."""
    return AresSessionContextStore(
        config=SessionContextConfig(
            db=db,
            mode=LearningMode.ALWAYS,
            model=model,
            enable_planning=True,
            max_updates_per_run=config.MAX_UPDATES_PER_RUN,
            instructions="Scrivi ogni campo in italiano, qualunque sia la lingua di questa istruzione.",
        )
    )


def apprendi_a_run_completato(
    run_output=None,
    agent=None,
    session=None,
    user_id=None,
    run_context=None,
) -> None:
    """Post-hook sincrono che conserva una volta il turno completo."""
    messaggi = list(getattr(run_output, "messages", None) or [])
    if not messaggi or agent is None:
        return

    macchina = agent.learning_machine
    macchina.process_completed_run(
        messages=messaggi,
        user_id=user_id or getattr(run_output, "user_id", None),
        session_id=(
            getattr(session, "session_id", None) if session is not None else getattr(run_output, "session_id", None)
        ),
        agent_id=getattr(agent, "id", None),
        team_id=getattr(agent, "team_id", None),
        run_metrics=getattr(run_output, "metrics", None),
        run_context=run_context,
        metadata=getattr(run_context, "metadata", None),
        dependencies=getattr(run_context, "dependencies", None),
        session_state=getattr(run_context, "session_state", None),
    )


def build_learning_machine(db: SqliteDb, knowledge: Knowledge | None, user_id: str) -> AresLearningMachine:
    """Compone gli store attivi secondo i flag in config."""
    learning_model = build_learning_model()

    user_profile: UserProfileConfig | bool = False
    if config.LEARN_USER_PROFILE:
        user_profile = UserProfileConfig(
            mode=LearningMode.ALWAYS,
            schema=AresProfile,
            model=learning_model,
            max_updates_per_run=config.MAX_UPDATES_PER_RUN,
            instructions=(
                "Scrivi ogni campo in italiano, qualunque sia la lingua di questa istruzione. "
                "Cattura solo cio' che resta vero oltre questa conversazione. "
                "Le preferenze durature e il contesto professionale vanno nel profilo; "
                "cio' che l'utente vuole in questo momento no."
            ),
        )

    user_memory: UserMemoryConfig | bool = False
    if config.LEARN_USER_MEMORY:
        user_memory = UserMemoryConfig(
            mode=LearningMode.ALWAYS,
            model=learning_model,
            schema=AresMemories if config.DATE_MEMORIE else None,
            max_updates_per_run=config.MAX_UPDATES_PER_RUN,
            enable_agent_tools=config.MEMORY_AGENT_TOOLS,
            instructions=(
                "Scrivi ogni memoria in italiano, qualunque sia la lingua di questa istruzione. "
                "Registra osservazioni che non entrano in un campo strutturato: "
                "abitudini, vincoli, opinioni espresse, cose che l'utente ha provato "
                "e scartato. Ogni memoria deve essere comprensibile da sola, senza "
                "la conversazione che l'ha generata."
            ),
        )

    session_context: AresSessionContextStore | bool = False
    if config.LEARN_SESSION_CONTEXT:
        session_context = build_session_context_store(db, learning_model)

    entity_memory: EntityMemoryConfig | bool = False
    if config.LEARN_ENTITIES:
        entity_memory = EntityMemoryConfig(
            model=learning_model,
            namespace=namespace_entita(user_id),
        )

    learned_knowledge: LearnedKnowledgeConfig | bool = False
    if config.LEARN_KNOWLEDGE:
        learned_knowledge = LearnedKnowledgeConfig(
            knowledge=knowledge,
            model=learning_model,
            mode=LearningMode.AGENTIC,
            namespace=namespace_utente(user_id),
        )

    return AresLearningMachine(
        db=db,
        model=learning_model,
        knowledge=knowledge,
        user_profile=user_profile,
        user_memory=user_memory,
        session_context=session_context,
        entity_memory=entity_memory,
        learned_knowledge=learned_knowledge,
        namespace=namespace_utente(user_id),
        max_updates_per_run=config.MAX_UPDATES_PER_RUN,
    )
