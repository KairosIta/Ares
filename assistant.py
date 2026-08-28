"""
L'assistente personale
======================
Un agente che gira interamente in locale e che accumula quattro forme
distinte di stato. La distinzione non e' accademica: decide dove finisce
ogni informazione e quindi se la ritrovi tra un mese.

    Memoria      cio' che riguarda te            curata dal modello
    Sessione     cio' che muore con la chat      gestita dal framework
    Conoscenza   materiale scritto da altri      indicizzata a parte
    FileSystem   note che l'agente scrive per se' salvate testuali

La regola pratica: se riguarda te e' memoria; se muore con la conversazione
e' stato di sessione; se l'ha scritto qualcun altro e' conoscenza; se l'ha
scritto l'agente per il proprio futuro e' FileSystem.
"""

from pathlib import Path

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.fs import FileSystem
from agno.knowledge.embedder.ollama import OllamaEmbedder
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

# Questo import trascina il pacchetto openai, che qui non serve a niente:
# l'__init__ di agno.models.ollama importa anche OllamaResponses, che discende
# dal client OpenAI. Importare il sottomodulo `agno.models.ollama.chat` non
# aiuta - Python esegue comunque l'__init__ del package - quindi in 2.9.0 la
# dipendenza e' necessaria anche in un progetto solo-Ollama.
from agno.models.ollama import Ollama
from agno.tools.workspace import Workspace
from agno.utils.log import log_warning
from agno.vectordb.lancedb import LanceDb
from agno.vectordb.search import SearchType

import config
from platform_files import rendi_privato
from schemas import AresMemories, AresProfile
from stores import namespace_entita, namespace_utente

# ---------------------------------------------------------------------------
# Modelli
# ---------------------------------------------------------------------------


def build_chat_model() -> Ollama:
    """Modello conversazionale, con il contesto esteso oltre il default di Ollama."""
    return Ollama(
        id=config.MAIN_MODEL,
        host=config.OLLAMA_HOST,
        options=config.OLLAMA_OPTIONS,
        keep_alive=config.KEEP_ALIVE,
        # `think` non e' una option di Ollama ma un parametro top-level
        # dell'API, e Agno non lo espone: request_params viene fuso nei
        # kwargs di ogni chiamata al client, streaming compreso.
        request_params={"think": config.MAIN_THINK},
    )


def build_learning_model() -> Ollama:
    """Modello per l'estrazione delle memorie.

    Stesso contesto dell'agente (un num_ctx diverso farebbe ricaricare i
    pesi, vedi config), temperatura bassa e niente pensiero: estrarre fatti
    da una conversazione e' trascrizione strutturata, non ragionamento.
    """
    return Ollama(
        id=config.LEARNING_MODEL,
        host=config.OLLAMA_HOST,
        options=config.LEARNING_OPTIONS,
        keep_alive=config.KEEP_ALIVE,
        request_params={"think": config.LEARNING_THINK},
    )


# ---------------------------------------------------------------------------
# Archivi
# ---------------------------------------------------------------------------


def _archivio_privato(percorso: str) -> str:
    """Prepara un database prima che lo apra SQLite, gia' con i suoi permessi.

    SQLAlchemy crea il file alla prima connessione con la umask del processo,
    cioe' 0644 su un'installazione tipica: dentro ci sono le conversazioni, il
    profilo e le memorie. Toccarlo dopo lascerebbe una finestra fra la
    creazione e la correzione, e nessun punto del codice sa quando avviene la
    prima connessione.

    Un file di lunghezza zero e' un database SQLite vuoto valido, quindi
    crearlo qui non cambia niente per chi lo apre dopo. Sui cloni esistenti
    corregge anche il file gia' scritto con i permessi larghi.
    """
    file_db = Path(percorso)
    file_db.parent.mkdir(parents=True, exist_ok=True)
    if not file_db.exists():
        file_db.touch()
    rendi_privato(file_db)
    return percorso


def build_db() -> SqliteDb:
    """Stato dell'agente: sessioni, profilo, memorie, entita'."""
    return SqliteDb(db_file=_archivio_privato(config.DB_FILE))


def build_knowledge() -> Knowledge:
    """Indice vettoriale per le intuizioni che l'agente decide di conservare.

    LanceDB e' incorporato nel processo: nessun container, nessun servizio da
    avviare. La ricerca ibrida unisce similarita' vettoriale e ricerca
    testuale, che su collezioni piccole conta piu' della sola distanza
    coseno, perche' con pochi documenti i vicini vettoriali sono rumorosi.
    """
    # LanceDB crea la propria directory alla prima scrittura, con la umask
    # del processo. Vale qui la stessa regola di tmp/: si rende privata la
    # directory, che e' cio' che si attraversa, e non i frammenti dentro, che
    # nascono e muoiono con le versioni della tabella.
    indice = Path(config.LANCEDB_URI)
    indice.mkdir(parents=True, exist_ok=True)
    rendi_privato(indice)
    return Knowledge(
        vector_db=LanceDb(
            uri=config.LANCEDB_URI,
            table_name="learned_knowledge",
            search_type=SearchType.hybrid,
            embedder=OllamaEmbedder(
                id=config.EMBEDDER_MODEL,
                host=config.OLLAMA_HOST,
                dimensions=config.EMBEDDER_DIMENSIONS,
            ),
        ),
    )


def build_filesystem(user_id: str = config.DEFAULT_USER_ID) -> FileSystem:
    """Il quaderno privato dell'agente, su un database separato e per utente.

    Separato di proposito: le memorie sono curate dal modello e possono
    essere riscritte o superate, i file no. Quello che l'agente scrive qui
    resta verbatim finche' non lo cancella lui.

    Il namespace e' quello dell'utente perche' senza il default di Agno e'
    `default`, condiviso: le memorie sarebbero segregate e i file no, e un
    secondo utente leggerebbe le note del primo. Namespace concreto e non
    templato (`user/{user_id}`): l'agente viene costruito per un utente
    preciso, quindi non c'e' niente da risolvere a runtime.
    """
    return FileSystem(
        SqliteDb(db_file=_archivio_privato(config.FS_DB_FILE)),
        namespace=namespace_utente(user_id),
    )


# ---------------------------------------------------------------------------
# Spazio di lavoro
# ---------------------------------------------------------------------------


class AresWorkspace(Workspace):
    """Lo spazio di lavoro sul disco, con gli strumenti rinominati.

    Agno registra gli strumenti per nome e ne tiene uno solo: il FileSystem
    privato espone gia' `read_file`, `write_file`, `list_files`, `move_file` e
    `search_content`, quindi mettendo `Workspace` accanto cinque strumenti su
    otto non arrivano mai al modello. Non e' un errore, e' un WARNING - e il
    modello resta convinto di avere un `read_file` che legge il disco mentre
    quello che ha legge il quaderno nel database. E' la collisione fra due
    contenitori diversi che rispondono allo stesso nome, la stessa forma dei
    namespace di `stores.py`, in un posto nuovo.

    Si rinomina questa superficie e non l'altra perche' le istruzioni del
    FileSystem le scrive Agno (`FileSystem.instructions()`) e nominano i
    propri strumenti: rinominare quelli lascerebbe nel prompt un testo che
    ordina di chiamare cose che non esistono.

    `Function.requires_confirmation` viene deciso alla registrazione e vive
    sull'oggetto, quindi sopravvive alla rinomina; `requires_confirmation_tools`
    e' una lista di nomi che nessuno rilegge dopo, ma viene aggiornata lo
    stesso per non lasciare in giro due verita' diverse. Al momento di
    eseguire lo strumento confermato Agno lo cerca per nome fra le funzioni
    dell'agente vivo (`handle_tool_call_updates`), non in un registro globale:
    per questo un nome nostro non rompe la ripresa del turno.
    """

    def __init__(self, root, prefisso: str, **kwargs):
        super().__init__(root, **kwargs)

        for elenco in (self.functions, self.async_functions):
            for nome in list(elenco):
                funzione = elenco.pop(nome)
                funzione.name = prefisso + nome
                elenco[funzione.name] = funzione
        self.requires_confirmation_tools = [prefisso + n for n in self.requires_confirmation_tools]

        # Workspace monta di suo un'istruzione in inglese che nomina read_file
        # ed edit_file, cioe' due nomi che qui non esistono piu'. Non si puo'
        # sostituire passando `instructions=`: il suo __init__ lo passa gia' a
        # Toolkit quando `edit` e' registrato, e due volte lo stesso argomento
        # e' un TypeError. Si spegne dopo, e il testo in italiano sta fra le
        # istruzioni dell'agente, dietro il flag, come tutti gli altri.
        self.instructions = None
        self.add_instructions = False


def build_workspace() -> AresWorkspace:
    """La directory di lavoro dell'agente, creata se manca.

    Le due guardie non sono formalita': se lo spazio di lavoro contenesse il
    progetto, Ares potrebbe riscrivere il proprio codice; se contenesse
    `tmp/`, un `workspace_delete_file` potrebbe portarsi via tutto cio' che ha
    imparato, che non ha backup. Sono entrambe conseguenze di una riga di
    configurazione, e nessuna delle due solleverebbe un errore da sola.
    """
    radice = config.WORKSPACE_DIR.resolve()

    for nome, percorso in (("il progetto", config.BASE_DIR), ("l'archivio", config.TMP_DIR)):
        if percorso.resolve().is_relative_to(radice):
            raise ValueError(
                "WORKSPACE_DIR contiene " + nome + " (" + str(percorso) + "): "
                "scegliere una directory che non lo comprenda."
            )

    radice.mkdir(parents=True, exist_ok=True)
    return AresWorkspace(
        radice,
        prefisso=config.WORKSPACE_PREFIX,
        allowed=config.WORKSPACE_ALLOWED,
        confirm=config.WORKSPACE_CONFIRM,
        require_read_before_write=config.WORKSPACE_READ_BEFORE_WRITE,
    )


# ---------------------------------------------------------------------------
# Apprendimento
# ---------------------------------------------------------------------------


class AresLearningMachine(LearningMachine):
    """Una LearningMachine che estrae solo quando il run e' davvero finito.

    Agno 2.9 avvia ``LearningMachine.process`` prima della chiamata al modello,
    su una fotografia dei messaggi presa dal thread di sfondo. Se il turno va
    in pausa per una conferma, quella fotografia termina prima del risultato
    dello strumento e della risposta conclusiva; ``continue_run`` non avvia una
    seconda estrazione.

    La macchina deve restare collegata con ``learning=``: e' quel collegamento
    che monta nel prompt contesto, istruzioni e strumenti di apprendimento. Si
    disinnesca quindi solo il callback anticipato e si espone un ingresso
    esplicito per il post-hook, che riceve i messaggi completi del RunOutput.
    """

    def process(self, *args, **kwargs) -> None:
        """Ignora l'estrazione anticipata avviata internamente da Agno."""
        return None

    async def aprocess(self, *args, **kwargs) -> None:
        """Stessa guardia per un eventuale uso futuro di ``arun``."""
        return None

    def process_completed_run(self, *args, **kwargs) -> None:
        """Esegue l'estrazione reale sul run completo, una volta sola."""
        super().process(*args, **kwargs)


class AresSessionContextStore(SessionContextStore):
    """Riprova soltanto una tool call di contesto che non ha scritto nulla.

    I retry del modello coprono gli errori del provider. Il JSON troncato di
    una tool call Ollama, invece, arriva ad Agno come risposta conclusa senza
    ``tool_executions``: nessuna eccezione e quindi nessun retry. Lo store di
    serie espone quel caso con ``context_updated=False``.

    Il secondo giro non puo' duplicare una scrittura: parte esclusivamente se
    il precedente non ha eseguito il tool. Nei turni normali il costo resta
    una sola inferenza.
    """

    last_extraction_attempts = 0

    def _extract_once(self, *args, **kwargs) -> str:
        """Punto sostituibile dallo smoke test senza chiamare il modello."""
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
    """Costruisce lo store robusto usato sia da Ares sia dalla prova mirata."""
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
    """Post-hook sincrono: conserva il turno solo dopo l'ultima continuazione.

    I post-hook non vengono eseguiti quando Agno restituisce un run in pausa;
    vengono eseguiti invece dal percorso normale e da ``continue_run`` quando
    non restano strumenti sospesi. Questo rende il punto di aggancio una
    proprieta' del ciclo di vita del run, non un controllo fragile sul tipo di
    requisito che lo aveva fermato.

    Sincrono di proposito: quando torna il prompt della REPL, gli store devono
    essere gia' aggiornati. Il ``capture_hook`` fornito da Agno usa invece un
    future senza attenderlo, quindi il turno successivo potrebbe rileggere il
    vecchio contesto.
    """
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
    """Compone gli store attivi secondo i flag in config.

    Gli store ALWAYS estraggono dopo ogni risposta, uno per volta, ciascuno
    con una chiamata al modello. Gli store AGENTIC non costano nulla finche'
    l'agente non decide di usare i propri strumenti, quindi si possono
    lasciare accesi senza penalita' fissa.
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
            # Cambia solo come le memorie diventano testo nel prompt: ognuna
            # arriva con la data in cui e' stata appresa, che in archivio c'e'
            # gia' ma che il rendering di Agno butta via. Nessun campo nuovo.
            schema=AresMemories if config.DATE_MEMORIE else None,
            max_updates_per_run=config.MAX_UPDATES_PER_RUN,
            # Espone `update_user_memory`, uno strumento solo: dice al modello
            # cosa fare in linguaggio naturale e la stessa estrazione della
            # modalita' ALWAYS traduce la richiesta in aggiunta, modifica o
            # cancellazione. Passa da `extract_and_save`, quindi le istruzioni
            # qui sotto valgono anche per questa strada: una memoria corretta a
            # voce non torna in inglese.
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
        # Nessuno schema custom: `save_session_context` ha una firma fissa
        # (summary, goal, plan, progress) scritta a mano e non derivata dallo
        # schema, quindi un campo aggiunto qui non arriverebbe mai al modello.
        session_context = build_session_context_store(db, learning_model)

    entity_memory: EntityMemoryConfig | bool = False
    if config.LEARN_ENTITIES:
        entity_memory = EntityMemoryConfig(
            model=learning_model,
            namespace=namespace_entita(user_id),
        )

    learned_knowledge: LearnedKnowledgeConfig | bool = False
    if config.LEARN_KNOWLEDGE:
        # Nessun `instructions` qui, a differenza degli altri store: la config
        # lo accetta ma LearnedKnowledgeStore non lo legge mai - ne' lui ne'
        # `system_message` ne' `additional_instructions` - e monta duemila
        # caratteri di istruzioni proprie, in inglese. Le istruzioni dedicate
        # vivono quindi sull'agente: in modalita'
        # AGENTIC salva l'agente, chiamando `save_learning` di sua iniziativa.
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


# ---------------------------------------------------------------------------
# Agente
# ---------------------------------------------------------------------------


def istruzioni_sugli_strumenti(radice_lavoro=None) -> list:
    """Le istruzioni che nominano uno strumento, ognuna dietro il suo flag.

    Un'istruzione che ordina di chiamare `update_user_memory` con
    MEMORY_AGENT_TOOLS spento e' peggio del silenzio: lo strumento non viene
    consegnato, ma il modello ha appena letto che deve usarlo e ci prova. E'
    lo stesso scollamento tra configurazione e cablaggio che il controllo
    `strumenti` cerca nell'altro verso, e config.py invita a spegnerli.
    """
    dette = []
    if config.LEARN_USER_MEMORY and config.MEMORY_AGENT_TOOLS:
        dette.append(
            "Se una memoria su di lui e' sbagliata, superata o scritta in "
            "inglese, correggila con update_user_memory invece di limitarti a "
            "dirlo: descrivi a parole cosa aggiungere, cambiare o togliere."
        )
    if config.LEARN_ENTITIES:
        # `remember_about` accetta gia' `events`, datati, e li rende con la
        # data quando l'agente cerca un'entita'. Senza dirglielo il modello
        # mette tutto in `facts`, dove una cosa accaduta a marzo diventa uno
        # stato presente e non invecchia mai. La distinzione e' nella
        # docstring dello strumento, in inglese e in mezzo ad altro: qui e'
        # in italiano e in evidenza.
        dette.append(
            "Su persone e progetti distingui i fatti dagli eventi quando usi "
            "remember_about, e scrivi gli uni e gli altri in italiano: un "
            "fatto e' un valore attuale che un giorno sara' sostituito, un "
            "evento e' qualcosa che e' accaduto e resta vero per sempre. "
            "Anche le opinioni e le posizioni prese sono eventi. Metti una "
            "data nel testo dell'evento solo se e' diversa da oggi: cio' che "
            "accade adesso viene datato da solo quando lo salvi, e una data "
            "scritta a mano e' un'occasione per sbagliarla."
        )
    if config.SEARCH_PAST_SESSIONS:
        # Il nome search_past_sessions promette una ricerca che non esiste: la
        # funzione non ha parametri, elenca e basta. Detto qui perche' un
        # modello da 9B, letto il nome, prova a passarci una query e si prende
        # un errore. Il tetto su num_runs serve al contesto: una sessione
        # intera arriva in un colpo solo.
        dette.append(
            "Per cio' che e' stato detto in un'altra conversazione: "
            "search_past_sessions elenca le sessioni e non accetta una "
            "domanda, poi read_past_session ne rilegge una per id, con "
            "num_runs se ti bastano i primi scambi."
        )
    if radice_lavoro is not None:
        # Due superfici di file, e il modello non ha modo di distinguerle se
        # non glielo si dice: il prefisso separa i nomi, questa frase separa i
        # posti. La riga sulla lista serve perche' `run_command` non passa da
        # una shell - un 9B che scrive "git clone x && cd x" ottiene un errore
        # che non spiega perche'.
        dette.append(
            "Hai una directory di lavoro tutta tua, " + str(radice_lavoro) + ": "
            "gli strumenti che cominciano con workspace_ lavorano li' dentro, "
            "sul disco vero, ed e' l'unica parte del computer che puoi "
            "toccare. Gli strumenti senza prefisso - read_file, write_file, "
            "list_files - sono invece il tuo quaderno privato, che vive in un "
            "database e non esiste sul disco: non confondere i due posti. "
            "workspace_run_command vuole il comando spezzato in una lista di "
            "stringhe, una per parola: ['ls', '-la'], non ['ls -la']. Non "
            "passa da una shell, quindi per una riga intera - pipe, "
            "redirezioni, piu' comandi insieme - usa "
            "['bash', '-lc', 'la riga']. Per leggere, elencare e cercare hai "
            "gli strumenti dedicati, che non chiedono niente a nessuno: la "
            "shell serve per cio' che loro non sanno fare. Prima di modificare un file "
            "leggilo. Cancellare, spostare ed eseguire comandi li deve "
            "autorizzare l'utente: il turno si ferma e lui decide. Se "
            "rifiuta, non cercare una strada diversa per fare la stessa cosa: "
            "chiedi."
        )
    if config.READ_CHAT_HISTORY:
        # Senza num_chats la sessione intera - tutti i ruoli, tool call
        # comprese - torna in una sola risposta di tool. E conta gli ultimi
        # messaggi, al contrario di num_runs che da' i primi scambi.
        dette.append(
            "Per questa conversazione oltre gli ultimi turni che hai in "
            "vista usa get_chat_history, sempre con num_chats."
        )
    return dette


def build_assistant(
    user_id: str = config.DEFAULT_USER_ID,
    session_id: str = "principale",
    debug: bool = False,
) -> Agent:
    """Assemblea l'assistente completo."""
    db = build_db()
    # Nessun indice se le intuizioni sono spente. Non e' solo economia: la
    # LearningMachine costruisce lo store `learned_knowledge` quando riceve un
    # knowledge, qualunque cosa dica il suo flag (`agno/learn/machine.py`,
    # `if self.learned_knowledge or self.knowledge is not None`), e con la
    # config di default finisce sul namespace `global` invece che su quello
    # dell'utente. Spegnere il flag e passare comunque l'indice non spegneva
    # niente: peggiorava, e in silenzio.
    knowledge = build_knowledge() if config.LEARN_KNOWLEDGE else None
    fs = build_filesystem(user_id)
    spazio = build_workspace() if config.WORKSPACE else None

    return Agent(
        name="Ares",
        # Il nome nell'oggetto non arriva al modello da solo: senza
        # add_name_to_context resta un'etichetta per chi legge il database, e
        # l'agente non sa come si chiama. Sono due cose separate e la seconda
        # e' spenta di default.
        add_name_to_context=True,
        # Va in testa al system message, prima delle istruzioni. Dice cos'e',
        # le istruzioni dicono come comportarsi: tenerli separati evita che la
        # persona si sfaldi ogni volta che si aggiunge una regola.
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
        # Agno avvia il learning automatico prima della risposta e non lo
        # riavvia dopo continue_run. La nostra LearningMachine rende quel giro
        # un no-op; questo hook estrae una volta sola, dai messaggi completi.
        post_hooks=[apprendi_a_run_completato],
        add_learnings_to_context=True,
        add_history_to_context=True,
        num_history_runs=config.NUM_HISTORY_RUNS,
        # Due strumenti, non uno: `search_past_sessions` elenca le sessioni
        # dell'utente con un'anteprima, `read_past_session` ne rilegge una per
        # id. Sono l'unico ponte tra sessioni, che per costruzione non si
        # vedono. Nessuna inferenza finche' non li chiama lui, ma i loro schemi
        # stanno nel prompt di ogni turno: il conto sta in config.py.
        # I due limiti stanno li' perche' l'anteprima entra nel contesto
        # tutta insieme, e il default di Agno ne mangia un quinto.
        search_past_sessions=config.SEARCH_PAST_SESSIONS,
        num_past_sessions_to_search=config.PAST_SESSIONS_LIMIT,
        num_past_session_runs_in_search=config.PAST_SESSION_RUNS_PREVIEW,
        # Recupera i turni di questa sessione usciti dalla finestra qui sopra.
        # Senza, oltre il quinto turno indietro l'agente non ha modo di sapere
        # cosa e' stato detto: la cronologia scorre via in silenzio.
        read_chat_history=config.READ_CHAT_HISTORY,
        add_datetime_to_context=True,
        # Ricalcolato a ogni turno dentro get_system_message, quindi una
        # sessione che attraversa la mezzanotte non mente. E' l'unico canale
        # con l'ora fresca: le istruzioni si costruiscono una volta sola qui,
        # e una data scritta li' dentro invecchierebbe in silenzio.
        datetime_format=config.DATETIME_FORMAT,
        timezone_identifier="Europe/Rome",
        markdown=True,
        # Agno registra ogni run su https://os-api.agno.com: session_id, run_id,
        # id del modello, tipo di database. Non il contenuto della
        # conversazione, ma resta una chiamata di rete per turno in un progetto
        # che promette di non farne, e il default e' True.
        # Esiste anche AGNO_TELEMETRY=false, ma una variabile d'ambiente e' la
        # stessa leva fragile gia' scartata per OLLAMA_HOST. Vale anche il
        # rovescio, ed e' peggio: Agno rilegge AGNO_TELEMETRY prima di ogni
        # invio e ci sovrascrive questo False, quindi la riga qui sotto si
        # difende da sola solo finche' nessuno mette quella variabile
        # nell'ambiente. Lo smoke test controlla anche quello.
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
