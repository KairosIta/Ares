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
from agno.learn.stores import EntityMemoryStore, LearnedKnowledgeStore, SessionContextStore, UserMemoryStore
from agno.models.ollama import Ollama
from agno.utils.log import log_warning

from ares import config
from ares.agent.runtime import build_learning_model
from ares.agent.schemas import AresMemories, AresProfile
from ares.state.stores import namespace_entita, namespace_utente

# Queste istruzioni vanno all'estrattore, che vede anche le parole
# dell'assistente: una proposta plausibile non deve diventare un fatto
# dell'utente. Il prompt conversazionale da solo non governa questo passo.
CRITERI_ESTRAZIONE = (
    "Scrivi in italiano. Estrai solo informazioni sostenute dal testo, rispettando chi le ha dette. "
    "Esempi, citazioni, ipotesi e giochi di ruolo non sono fatti sull'utente. "
    "Le proposte dell'assistente non sono decisioni accettate: serve una conferma dell'utente. "
    "Non trasformare una possibilita' in un fatto certo o una richiesta per il compito corrente "
    "in una preferenza stabile. Conserva le qualifiche e l'incertezza espresse; "
    "non aggiungere deduzioni non confermate. Una correzione esplicita sostituisce l'informazione "
    "superata, senza lasciare entrambe come attuali; conserva gli altri fatti ancora validi. "
    "La data in cui apprendi un evento non e' necessariamente la data in cui e' accaduto. "
)


class AresLearningMachine(LearningMachine):
    """Estrae apprendimenti soltanto quando il run e' davvero concluso.

    Agno, verificato fino alla 3.0.5, avvia ``LearningMachine.process`` in background prima
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


class AresUserMemoryStore(UserMemoryStore):
    """Le memorie, spiegate al modello in italiano e per una persona sola.

    Agno scrive la guida di ogni store in inglese, per un agente generico
    che puo' avere davanti una squadra. Qui la voce e' quella del resto del
    prompt, e dice cio' che il modello deve sapere per non sbagliare: che le
    memorie si aggiornano da sole dopo ogni risposta, che l'utente le vede
    e puo' annullarle, e quando invece tocca a lui usare lo strumento.
    """

    def instructions(self) -> str:
        if not self._should_expose_tools or not self.config.agent_can_update_memories:
            return ""
        return (
            "<istruzioni_memorie>\n"
            "Le memorie sono osservazioni sulla persona con cui parli: abitudini, vincoli, "
            "opinioni, cose provate e scartate. "
            "update_user_memory serve quando ti chiede esplicitamente di ricordare, correggere "
            "o dimenticare qualcosa, o quando una memoria che vedi qui sotto e' sbagliata o "
            "superata: descrivi a parole cosa aggiungere, cambiare o togliere, in italiano.\n"
            "</istruzioni_memorie>"
        )


class AresEntityMemoryStore(EntityMemoryStore):
    """Le entita', spiegate in italiano: quattro strumenti e quando usarli."""

    def instructions(self) -> str:
        if not self._should_expose_tools:
            return ""
        return (
            "<istruzioni_entita>\n"
            "Le entita' sono persone, progetti, sistemi e prodotti che contano per la persona con "
            "cui parli, con i loro fatti ed eventi. Non si aggiornano da sole. "
            "remember_about registra un fatto, un evento, una descrizione o una nota su "
            "un'entita', per nome: una correzione e' il fatto nuovo, quello contraddetto viene "
            "ritirato da solo. link_entities registra una relazione fra due entita'. "
            "search_entities le cerca, e senza query le elenca dalla piu' recente. forget ritira "
            "un fatto o archivia un'entita' intera. Registra quando impari qualcosa di sostanziale "
            "su una persona, un progetto o un sistema che servira' in una conversazione futura, "
            "e scrivilo in italiano.\n"
            "</istruzioni_entita>"
        )


class AresLearnedKnowledgeStore(LearnedKnowledgeStore):
    """Le intuizioni, spiegate in italiano e senza regole di squadra.

    La guida di Agno chiede di conservare "obiettivi e politiche del team
    perche' ne beneficino altri utenti": qui c'e' una persona sola, e quella
    regola farebbe salvare come intuizione cio' che e' una preferenza.
    """

    def instructions(self) -> str:
        # Agno restituisce le istruzioni AGENTIC anche con gli strumenti
        # spenti. In -p riapparivano cosi' ordini di salvare e regole di team.
        if not self.config.enable_agent_tools:
            return ""
        if self.config.mode != LearningMode.AGENTIC:
            return super().instructions()
        return (
            "<istruzioni_intuizioni>\n"
            "Le intuizioni sono criteri riutilizzabili imparati lavorando, cercabili per "
            "somiglianza. Non si aggiornano da sole. search_learnings(query) le cerca: usalo "
            "prima di rispondere a una domanda di metodo, di scelta o di convenzione, e sempre "
            "prima di salvarne una, per non duplicarla. Quando usi save_learning(title, learning, context, "
            "tags), scrivi titolo, intuizione e contesto in italiano, anche se la risposta "
            "richiesta e' in un'altra lingua; mantieni invariati nomi tecnici e identificativi. "
            "Salva un criterio "
            "quando la persona lo chiede esplicitamente - ricorda, salva, "
            "tieni a mente - o quando hai scoperto da solo qualcosa di non ovvio, riutilizzabile "
            "e abbastanza concreto da applicarsi. Non salvare fatti grezzi, preferenze della "
            "persona - quelle sono memorie - o doppioni. Conserva il criterio e le condizioni "
            "in cui vale, non la sola risposta al caso specifico; distingui una procedura "
            "verificata da un'idea ancora da provare. Qui c'e' una persona sola: non esistono "
            "regole di squadra da conservare per altri.\n"
            "</istruzioni_intuizioni>"
        )


def build_session_context_store(db: SqliteDb, model: Ollama) -> AresSessionContextStore:
    """Costruisce lo store di contesto con retry mirato."""
    return AresSessionContextStore(
        config=SessionContextConfig(
            db=db,
            mode=LearningMode.ALWAYS,
            model=model,
            enable_planning=True,
            max_updates_per_run=config.MAX_UPDATES_PER_RUN,
            instructions=CRITERI_ESTRAZIONE + "Nel contesto di sessione distingui obiettivo, piano proposto, "
            "decisioni accettate e avanzamento verificato. Un'azione tentata o fallita non e' completata.",
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


def build_learning_machine(
    db: SqliteDb, knowledge: Knowledge | None, user_id: str, *, strumenti: bool = True
) -> AresLearningMachine:
    """Compone gli store attivi secondo i flag in config.

    Con `strumenti=False` gli store restano - il contesto che iniettano nel
    prompt e' cio' che Ares sa dell'utente - ma non danno al modello gli
    strumenti per scriverci: e' `ares -p`, dove nessuno legge cio' che
    entrerebbe in memoria.
    """
    learning_model = build_learning_model()

    user_profile: UserProfileConfig | bool = False
    if config.LEARN_USER_PROFILE:
        user_profile = UserProfileConfig(
            mode=LearningMode.ALWAYS,
            schema=AresProfile,
            model=learning_model,
            max_updates_per_run=config.MAX_UPDATES_PER_RUN,
            instructions=(
                CRITERI_ESTRAZIONE + "Cattura solo cio' che resta vero oltre questa conversazione. "
                "Le preferenze durature e il contesto professionale vanno nel profilo; "
                "cio' che l'utente vuole in questo momento no."
            ),
        )

    # Gli store con una guida per il modello si costruiscono qui, con le
    # classi che la scrivono in italiano: la macchina accetta istanze gia'
    # fatte e non le completa, quindi db, modello e limiti vanno passati.
    user_memory: AresUserMemoryStore | bool = False
    if config.LEARN_USER_MEMORY:
        user_memory_config = UserMemoryConfig(
            db=db,
            mode=LearningMode.ALWAYS,
            model=learning_model,
            schema=AresMemories if config.DATE_MEMORIE else None,
            max_updates_per_run=config.MAX_UPDATES_PER_RUN,
            enable_agent_tools=config.MEMORY_AGENT_TOOLS and strumenti,
            instructions=(
                CRITERI_ESTRAZIONE + "Registra osservazioni che non entrano in un campo strutturato: "
                "abitudini, vincoli, opinioni espresse, cose che l'utente ha provato "
                "e scartato. Ogni memoria deve essere comprensibile da sola, senza "
                "la conversazione che l'ha generata."
            ),
        )
        user_memory = AresUserMemoryStore(config=user_memory_config)

    session_context: AresSessionContextStore | bool = False
    if config.LEARN_SESSION_CONTEXT:
        session_context = build_session_context_store(db, learning_model)

    entity_memory: AresEntityMemoryStore | bool = False
    if config.LEARN_ENTITIES:
        entity_memory = AresEntityMemoryStore(
            config=EntityMemoryConfig(
                db=db,
                model=learning_model,
                namespace=namespace_entita(user_id),
                max_updates_per_run=config.MAX_UPDATES_PER_RUN,
                enable_agent_tools=strumenti,
            )
        )

    learned_knowledge: AresLearnedKnowledgeStore | bool = False
    if config.LEARN_KNOWLEDGE:
        learned_knowledge = AresLearnedKnowledgeStore(
            config=LearnedKnowledgeConfig(
                knowledge=knowledge,
                model=learning_model,
                mode=LearningMode.AGENTIC,
                namespace=namespace_utente(user_id),
                max_updates_per_run=config.MAX_UPDATES_PER_RUN,
                enable_agent_tools=strumenti,
            )
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
