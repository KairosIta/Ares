"""
Smoke test dell'agente
======================
Uso:
    .venv/bin/python tests/smoke_test.py
    .venv/bin/python tests/smoke_test.py --user prova --session lavoro

Costruisce l'agente, si scrive da solo i dati che gli servono e controlla che
tornino indietro. Non chiede niente al modello: nessun peso entra in VRAM.

Prima questa prova leggeva l'archivio vero e dichiarava `n.c.` quando non ci
trovava dati. Su un clone appena scaricato sei controlli su quattordici non
dimostravano piu' niente e uscivano verdi lo stesso: un test che aspetta che
qualcun altro gli prepari le precondizioni non e' un test, e' un ispettore con
una firma. Ora le precondizioni se le costruisce, in una directory temporanea
che cancella alla fine.

Da qui discendono due cose. I controlli valgono su qualunque macchina, anche
appena clonata. E l'archivio vero non viene ne' letto ne' scritto: l'ultimo
controllo lo dimostra, ed e' il motivo per cui e' sparito il vecchio
`nessuna scrittura`, che sorvegliava un rischio nato dal fatto che la prova
girava sui dati veri. Per guardare dentro l'archivio vero c'e'
`inspect_learning.py`.

Il seme tocca solo gli store su SQLite. `learned_knowledge` resta fuori
apposta: scriverci sopra chiamerebbe l'embedder, e questa prova promette di
non caricare pesi. Tutti e sei i controlli che dipendevano dai dati stanno su
SQLite, quindi la promessa costa zero copertura.

Ogni prova riporta uno di tre esiti:

    ok        il controllo e' passato
    n.c.      non concludente: non c'e' abbastanza per dimostrare qualcosa
    FALLITO   il controllo non e' passato

`n.c.` resta per i pochi controlli che nemmeno un seme puo' rendere
significativi, per esempio se un giorno nessuno store usasse uno schema
personalizzato. Solo un FALLITO cambia il codice di uscita.

Il preflight (`preflight.py`) risponde a una domanda diversa: se avvio adesso,
il server e i modelli ci sono? Qui il server non serve. E `e2e_test.py` a una
terza: l'agente risponde e impara? Quella costa un turno vero.
"""

import argparse
import contextlib
import importlib
import io
import logging
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

# Le prove stanno in tests/, i moduli del progetto in radice: lanciata come
# script, `sys.path[0]` e' tests/ e `import config` non troverebbe niente.
# Va prima di qualunque import del progetto.
RADICE_PROGETTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE_PROGETTO))

# L'archivio della prova va scelto prima di importare config, perche' config
# crea TMP_DIR all'import: importarlo e correggere i percorsi dopo lascerebbe
# comunque una tmp/ vuota accanto ai dati veri.
ARCHIVIO_PROVA = tempfile.mkdtemp(prefix="ares-smoke-")
os.environ["ARES_TMP"] = ARCHIVIO_PROVA
# Stesso motivo per lo spazio di lavoro: `build_workspace` crea la directory,
# e senza questa riga la prova ne farebbe comparire una vera accanto al
# progetto. Anche questa va decisa prima dell'import.
SPAZIO_PROVA = tempfile.mkdtemp(prefix="ares-lavoro-")
os.environ["ARES_WORKSPACE"] = SPAZIO_PROVA

# Normalizzatore privato di Agno, importato di proposito invece di
# riscritto: una copia locale verificherebbe la copia, non il
# comportamento del FileSystem su cui i namespace finiscono davvero.
from agno.fs._paths import normalize_namespace  # noqa: E402
from agno.metrics import MessageMetrics, ModelMetrics, RunMetrics, ToolCallMetrics  # noqa: E402
from agno.models.response import ToolExecution  # noqa: E402
from agno.run.agent import RunOutput  # noqa: E402
from agno.run.base import RunStatus  # noqa: E402
from agno.tools.workspace import Workspace  # noqa: E402
from prompt_toolkit.completion import CompleteEvent  # noqa: E402
from prompt_toolkit.document import Document  # noqa: E402
from prompt_toolkit.input.defaults import create_pipe_input  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.text import Text  # noqa: E402

import config  # noqa: E402
import platform_files  # noqa: E402
from assistant import (  # noqa: E402
    AresLearningMachine,
    AresSessionContextStore,
    apprendi_a_run_completato,
    build_assistant,
    build_db,
    build_filesystem,
    build_workspace,
)
from chat import (  # noqa: E402
    AGNO_LOGGER_NAMES,
    COMANDI,
    configura_log_agno,
    finestra_occupata,
    gestisci_comando,
    mostra_flusso,
    righe_argomento,
    righe_esito,
    righe_metriche,
    righe_richiesta,
    risolvi_comando,
    stampa_aiuto,
)
from cli_input import (  # noqa: E402
    CRONOLOGIA_INTESTAZIONE,
    CliInput,
    CompletamentoComandi,
    CronologiaSicura,
)
from cli_ui import CliRenderer, RichRunStream  # noqa: E402
from schemas import AresProfile  # noqa: E402
from stores import (  # noqa: E402
    leggi_entita,
    leggi_intuizioni,
    leggi_sessioni,
    namespace_entita,
    namespace_utente,
    righe_entita,
    righe_sessione,
    stampa_store,
)
from turn_core import (  # noqa: E402
    TurnEngine,
    TurnEventKind,
    normalize_events,
    run_turn_cycle,
)

# Utente che non esiste in nessun archivio: serve solo a controllare che i
# file di chi esiste non gli si vedano. Non viene mai scritto.
UTENTE_DI_CONTROLLO = "utente-di-controllo"

NON_CONCLUSIVO = "non concludente: "

# Il seme. Valori scritti a mano, perche' un controllo che si aspetta il
# numero che ha appena letto dall'archivio non confronta niente: deve
# aspettarsi cio' che qualcuno ha deciso, e accorgersi se torna altro.
PROFILO_SEMINATO = {
    "name": "Prova",
    "preferred_name": "Prova",
    "timezone": "Europe/Rome",
    "language": "it",
    "occupation": "collaudo",
    "communication_style": "concisa",
}
# Solo i quattro campi che `save_session_context` sa scrivere: seminarne altri
# verificherebbe che SQLite conserva cio' che gli dai, non che il contesto di
# sessione funzioni.
CONTESTO_SEMINATO = {
    "summary": "Sessione di collaudo",
    "goal": "verificare il cablaggio",
    "plan": ["seminare", "rileggere"],
    "progress": ["seminato"],
}
MEMORIE_SEMINATE = ("La prima memoria del seme.", "La seconda memoria del seme.")
ENTITA_SEMINATE = (
    ("Entita Uno", "project", ["fatto uno", "fatto due", "fatto tre"]),
    ("Entita Due", "person", ["fatto quattro"]),
)
FILE_SEMINATO = ("note/seme.md", "# Seme\n\nScritto dal collaudo.\n")

FATTI_SEMINATI = sum(len(fatti) for _, _, fatti in ENTITA_SEMINATE)


# Campi di servizio degli schemi di Agno: identificativi e date, popolati
# sempre. Contarli come "campi popolati" gonfierebbe il numero e lo
# renderebbe diverso da quello che mostra /profilo.
CAMPI_DI_SERVIZIO = ("user_id", "agent_id", "team_id", "session_id", "created_at", "updated_at")


def campi_popolati(schema) -> list:
    """Campi con un valore, esclusi quelli di servizio."""
    return [c for c, valore in vars(schema).items() if valore and c not in CAMPI_DI_SERVIZIO]


def esigi(condizione: object, messaggio: str) -> None:
    """assert esplicito: `assert` sparisce con `python -O`, questo no."""
    if not condizione:
        raise AssertionError(messaggio)


def stato_archivio_reale() -> list:
    """Fotografia dell'archivio vero, per dimostrare che la prova non lo tocca.

    Prende il posto del vecchio `nessuna scrittura`, che contava le righe
    dell'archivio vero prima e dopo. Contarle non serve piu': ora la prova
    scrive per mestiere, ma in un'altra directory. Cio' che va dimostrato non
    e' che non scriva, e' che non scriva li'.
    """
    reale = config.BASE_DIR / "tmp"
    if not reale.exists():
        return []
    return sorted(
        (str(percorso.relative_to(reale)), percorso.stat().st_size, percorso.stat().st_mtime_ns)
        for percorso in reale.rglob("*")
        if percorso.is_file()
    )


def conta_apprendimenti(learning_type: str, namespace: str) -> int:
    """Righe di un tipo di apprendimento in un namespace, lette da SQLite.

    Di proposito non passa dagli store: un controllo che usa lo stesso
    percorso di lettura del difetto non puo' rilevarlo. `/entita` mostrava
    zero entita' con tre in archivio, e solo un conteggio indipendente
    rendeva visibile la differenza.
    """
    with contextlib.closing(sqlite3.connect(config.DB_FILE)) as connessione:
        try:
            righe = connessione.execute(
                "select count(*) from agno_learnings where learning_type = ? and namespace = ?",
                (learning_type, namespace),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
    return righe[0] if righe else 0


def semina(lm, fs, user_id: str, session_id: str) -> str:
    """Scrive nell'archivio della prova i dati che i controlli si aspettano.

    Nessuna di queste chiamate passa dal modello: sono le stesse API che
    l'agente usa attraverso i propri strumenti, invocate direttamente. Le
    entita' non ricevono il namespace, apposta: cosi' finiscono dove lo store
    ha deciso di scrivere, e il controllo che le riconta dimostra qualcosa
    sulla configurazione invece che sull'argomento appena passato.
    """
    seminato = []
    # Solo per gli store accesi: spegnerne uno e' lecito, e un seme che dia
    # per scontato che ci siano tutti farebbe fallire la prova per una
    # configurazione valida invece che per un difetto.
    if "user_profile" in lm.stores:
        lm.user_profile_store.save(user_id=user_id, profile=AresProfile(user_id=user_id, **PROFILO_SEMINATO))
        seminato.append("1 profilo")
    if "user_memory" in lm.stores:
        for memoria in MEMORIE_SEMINATE:
            lm.user_memory_store.add_memory(user_id=user_id, memory=memoria)
        seminato.append(str(len(MEMORIE_SEMINATE)) + " memorie")
    if "session_context" in lm.stores:
        lm.session_context_store.save(
            session_id=session_id,
            context=lm.stores["session_context"].schema(session_id=session_id, **CONTESTO_SEMINATO),
            user_id=user_id,
        )
        seminato.append("1 contesto")
    if "entity_memory" in lm.stores:
        for nome, tipo, fatti in ENTITA_SEMINATE:
            lm.entity_memory_store.remember_about(entity=nome, entity_type=tipo, facts=list(fatti), user_id=user_id)
        seminato.append(str(len(ENTITA_SEMINATE)) + " entita' con " + str(FATTI_SEMINATI) + " fatti")
    fs.write(*FILE_SEMINATO)
    seminato.append("1 file")
    return ", ".join(seminato)


# ---------------------------------------------------------------------------
# Le prove
# ---------------------------------------------------------------------------


def store_attivi(lm) -> str:
    """Gli store costruiti sono esattamente quelli accesi in config."""
    attesi = {
        "user_profile": config.LEARN_USER_PROFILE,
        "user_memory": config.LEARN_USER_MEMORY,
        "session_context": config.LEARN_SESSION_CONTEXT,
        "entity_memory": config.LEARN_ENTITIES,
        "learned_knowledge": config.LEARN_KNOWLEDGE,
    }
    presenti = set(lm.stores.keys())
    for nome, acceso in attesi.items():
        esigi(
            (nome in presenti) == acceso,
            nome
            + (
                " e' acceso in config ma non e' stato costruito"
                if acceso
                else " e' spento in config ma e' stato costruito"
            ),
        )
    return str(len(presenti)) + " store attivi: " + ", ".join(sorted(presenti))


def apprendimento_post_run(agent) -> str:
    """L'estrazione anticipata e' spenta e il post-hook usa il run completo.

    La prova usa una macchina minimale senza store reali: chiamare il metodo
    della LearningMachine costruita dall'agente accenderebbe il modello. Conta
    invece le invocazioni allo stesso percorso di base che il post-hook usa in
    produzione.
    """
    from types import SimpleNamespace

    chiamate = []

    class StoreFinto:
        was_updated = False

        def process(self, **kwargs):
            chiamate.append(kwargs)

    macchina = object.__new__(AresLearningMachine)
    macchina._stores = {"prova": StoreFinto()}
    macchina.model = None

    messaggi = [SimpleNamespace(role="user", content="prima"), SimpleNamespace(role="assistant", content="dopo")]
    argomenti = {
        "messages": messaggi,
        "user_id": "prova",
        "session_id": "prova",
    }

    macchina.process(**argomenti)
    esigi(not chiamate, "il callback anticipato ha ancora eseguito l'estrazione")

    agente_finto = SimpleNamespace(learning_machine=macchina, id="ares", team_id=None)
    sessione_finta = SimpleNamespace(session_id="prova")
    output_finto = SimpleNamespace(messages=messaggi, user_id="prova", session_id="prova")
    contesto_finto = SimpleNamespace(metadata=None, dependencies=None, session_state=None)
    apprendi_a_run_completato(
        run_output=output_finto,
        agent=agente_finto,
        session=sessione_finta,
        user_id="prova",
        run_context=contesto_finto,
    )

    esigi(len(chiamate) == 1, "il post-hook ha eseguito " + str(len(chiamate)) + " estrazioni invece di una")
    esigi(chiamate[0]["messages"] == messaggi, "il post-hook non ha ricevuto tutti i messaggi del run")
    esigi(isinstance(agent.learning_machine, AresLearningMachine), "l'agente non usa AresLearningMachine")
    esigi(
        apprendi_a_run_completato in (agent.post_hooks or []),
        "il post-hook di apprendimento non e' collegato all'agente",
    )
    return "callback anticipato spento, un post-hook sui " + str(len(messaggi)) + " messaggi completi"


def retry_contesto(lm) -> str:
    """Un fallimento senza tool ritenta una volta, un successo mai."""
    import asyncio

    import assistant as modulo_assistant

    class StoreFinto(AresSessionContextStore):
        def __init__(self, esiti):
            self.esiti = iter(esiti)
            self.chiamate = 0
            self.context_updated = False

        def _extract_once(self, *args, **kwargs):
            self.chiamate += 1
            self.context_updated = next(self.esiti)
            return "prova"

        async def _aextract_once(self, *args, **kwargs):
            self.chiamate += 1
            self.context_updated = next(self.esiti)
            return "prova"

    retry_originali = config.SESSION_CONTEXT_RETRIES
    log_originale = modulo_assistant.log_warning
    avvisi = []
    modulo_assistant.log_warning = avvisi.append
    try:
        config.SESSION_CONTEXT_RETRIES = 1

        immediato = StoreFinto([True])
        immediato.extract_and_save()
        esigi(immediato.chiamate == 1, "un contesto riuscito e' stato estratto due volte")

        recuperato = StoreFinto([False, True])
        recuperato.extract_and_save()
        esigi(recuperato.chiamate == 2, "il contesto fallito non ha fatto un solo retry")
        esigi(recuperato.context_updated, "il retry riuscito non risulta aggiornato")

        esaurito = StoreFinto([False, False, True])
        esaurito.extract_and_save()
        esigi(esaurito.chiamate == 2, "il contesto ha superato il limite di un retry")
        esigi(not esaurito.context_updated, "due fallimenti risultano aggiornati")

        config.SESSION_CONTEXT_RETRIES = 0
        spento = StoreFinto([False, True])
        spento.extract_and_save()
        esigi(spento.chiamate == 1, "SESSION_CONTEXT_RETRIES=0 non spegne il retry")

        config.SESSION_CONTEXT_RETRIES = 1
        asincrono = StoreFinto([False, True])
        asyncio.run(asincrono.aextract_and_save())
        esigi(asincrono.chiamate == 2, "il percorso asincrono non ha fatto un solo retry")
        esigi(asincrono.context_updated, "il retry asincrono riuscito non risulta aggiornato")
    finally:
        config.SESSION_CONTEXT_RETRIES = retry_originali
        modulo_assistant.log_warning = log_originale

    if config.LEARN_SESSION_CONTEXT:
        esigi(
            isinstance(lm.session_context_store, AresSessionContextStore),
            "l'agente non usa AresSessionContextStore",
        )
    else:
        esigi(lm.session_context_store is None, "il contesto e' costruito nonostante il flag spento")
    esigi(len(avvisi) == 5, "il retry ha prodotto " + str(len(avvisi)) + " avvisi invece di 5")
    return "sync e async: successo=1 tentativo, recupero=2, limite=2, zero disabilita"


def namespace_coerenti(lm, fs, user_id: str) -> str:
    """Entita', intuizioni e file finiscono nei contenitori dell'utente.

    Profilo, memorie e contesto non compaiono qui perche' non hanno
    namespace: sono per user_id e le loro config non accettano nemmeno il
    parametro.
    """
    utente = namespace_utente(user_id)
    entita = namespace_entita(user_id)
    coppie = [("LearningMachine", lm.namespace, utente), ("FileSystem", fs.namespace, utente)]
    # Solo gli store accesi: uno spento non ha un namespace sbagliato, non ce
    # l'ha proprio, e pretenderlo trasformerebbe una configurazione lecita in
    # un FALLITO.
    if "entity_memory" in lm.stores:
        coppie.append(("entity_memory", lm.stores["entity_memory"].config.namespace, entita))
    if "learned_knowledge" in lm.stores:
        coppie.append(("learned_knowledge", lm.stores["learned_knowledge"].config.namespace, utente))
    for nome, ottenuto, atteso in coppie:
        esigi(ottenuto == atteso, nome + " punta a " + repr(ottenuto) + " invece che a " + repr(atteso))
    return utente + " per i file e le intuizioni, " + entita + " per le entita'"


def namespace_stabili(user_id: str) -> str:
    """I namespace attraversano la normalizzazione del FileSystem intatti.

    E' il controllo che rende sicura la scelta della barra: se qualcuno
    tornasse ai due punti, `user:demo` diventerebbe `user%3ademo` solo
    dal lato dei file, e le due meta' dell'archivio si separerebbero senza
    un errore.
    """
    for costruito in (namespace_utente(user_id), namespace_entita(user_id)):
        normalizzato = normalize_namespace(costruito)
        esigi(
            normalizzato == costruito,
            "il FileSystem riscriverebbe " + repr(costruito) + " come " + repr(normalizzato),
        )
    return "invariati sotto normalize_namespace"


def chiamate_locali(agent, lm) -> str:
    """Niente di cio' che gira per un turno puo' uscire dalla macchina.

    Se OLLAMA_API_KEY e' nell'ambiente e host non e' impostato, Agno manda
    le conversazioni a https://ollama.com. Qui si controlla che ogni
    modello e l'embedder abbiano l'host esplicito, e che quell'host sia
    locale: un progetto che promette che niente esce non puo' dipendere da
    una variabile d'ambiente per mantenere la promessa.
    """
    componenti = [("agente", agent.model)]
    for nome, store in lm.stores.items():
        modello = getattr(store.config, "model", None)
        if modello is not None:
            componenti.append((nome, modello))
    if lm.knowledge is not None:
        componenti.append(("embedder", lm.knowledge.vector_db.embedder))

    for nome, componente in componenti:
        host = getattr(componente, "host", None)
        esigi(host == config.OLLAMA_HOST, nome + " ha host " + repr(host) + " invece di " + repr(config.OLLAMA_HOST))
    esigi(
        "localhost" in config.OLLAMA_HOST or "127.0.0.1" in config.OLLAMA_HOST,
        "config.OLLAMA_HOST non e' locale: " + config.OLLAMA_HOST,
    )
    # L'host giusto non basta: Agno manda un evento di telemetria a
    # os-api.agno.com alla fine di ogni run, e il default e' acceso.
    esigi(agent.telemetry is False, "la telemetria di Agno e' attiva: ogni turno esce dalla macchina")
    # `telemetry=False` nel codice non basta: prima di ogni invio Agno rilegge
    # AGNO_TELEMETRY dall'ambiente e ci sovrascrive il valore
    # (`agno/agent/_init.py`, set_telemetry). E' la stessa leva di
    # OLLAMA_API_KEY: una variabile di troppo e la promessa salta.
    esigi(
        os.environ.get("AGNO_TELEMETRY", "").lower() != "true",
        "AGNO_TELEMETRY=true nell'ambiente riaccende la telemetria nonostante telemetry=False",
    )
    return str(len(componenti)) + " componenti su " + config.OLLAMA_HOST + ", telemetria spenta"


def contesto_esteso(agent, lm) -> str:
    """Ogni modello ha num_ctx esplicito e sopra il default di Ollama.

    Ollama tronca a 4096 token senza dirlo, e il troncamento si manifesta
    come un agente che dimentica invece che come un errore.
    """
    modelli = [("agente", agent.model)]
    for nome, store in lm.stores.items():
        modello = getattr(store.config, "model", None)
        if modello is not None:
            modelli.append((nome, modello))

    valori = []
    for nome, modello in modelli:
        num_ctx = (getattr(modello, "options", None) or {}).get("num_ctx")
        esigi(num_ctx is not None, nome + " non passa num_ctx: Ollama userebbe 4096 in silenzio")
        esigi(num_ctx > 4096, nome + " passa num_ctx=" + str(num_ctx) + ", sotto o pari al default di Ollama")
        valori.append(num_ctx)
    return "num_ctx da " + str(min(valori)) + " a " + str(max(valori)) + " su " + str(len(modelli)) + " modelli"


def ragionamento_modelli(agent, lm) -> str:
    """Il pensiero resta acceso in chat e spento nelle estrazioni.

    `think` e' un parametro top-level di Ollama: se finisse per errore nelle
    options o sparisse durante un refactor, il modello tornerebbe al proprio
    default e ogni store pagherebbe un blocco di ragionamento dopo il turno.
    """
    chat_params = getattr(agent.model, "request_params", None) or {}
    esigi(
        chat_params.get("think") is config.MAIN_THINK,
        "l'agente passa think=" + repr(chat_params.get("think")) + " invece di " + repr(config.MAIN_THINK),
    )

    estrattori = []
    for nome, store in lm.stores.items():
        modello = getattr(store.config, "model", None)
        if modello is None:
            continue
        params = getattr(modello, "request_params", None) or {}
        esigi(
            params.get("think") is config.LEARNING_THINK,
            nome + " passa think=" + repr(params.get("think")) + " invece di " + repr(config.LEARNING_THINK),
        )
        estrattori.append(nome)

    esigi(estrattori, "nessun estrattore disponibile per verificare think")
    return "chat=" + str(config.MAIN_THINK) + ", " + str(len(estrattori)) + " estrattori=" + str(config.LEARNING_THINK)


def schemi_importabili(lm) -> str:
    """Gli schemi custom vivono in un modulo importabile, non in __main__.

    Agno li serializza per percorso di import: definiti in __main__
    sopravvivono al processo corrente ma non alla rilettura da database.
    """
    trovati = []
    for nome, store in lm.stores.items():
        schema = getattr(store.config, "schema", None)
        if schema is None:
            continue
        modulo = schema.__module__
        esigi(modulo != "__main__", nome + ": " + schema.__name__ + " e' definito in __main__")
        esigi(
            getattr(importlib.import_module(modulo), schema.__name__, None) is schema,
            nome + ": " + modulo + "." + schema.__name__ + " non riporta alla stessa classe",
        )
        trovati.append(modulo + "." + schema.__name__)
    if not trovati:
        return NON_CONCLUSIVO + "nessuno store usa uno schema personalizzato"
    return ", ".join(trovati)


class _MessaggioFinto:
    """Un messaggio con i soli metrics che il rendering guarda."""

    def __init__(self, role: str, input_tokens: int):
        self.role = role
        self.metrics = MessageMetrics(input_tokens=input_tokens)


class _RunFinto:
    """Un RunOutput ridotto ai due campi da cui si leggono le metriche.

    Fabbricato invece di prodotto da un turno vero perche' questa prova
    promette di non caricare pesi. I valori riproducono metriche plausibili.
    """

    def __init__(self, metrics, messages):
        self.metrics = metrics
        self.messages = messages


class _MacchinaSenzaStore:
    """Una LearningMachine con tutti gli store spenti, quanto basta ai lettori.

    Costruire un agente con i flag a False servirebbe a poco: il controllo
    deve valere per qualunque combinazione, non per quella scelta oggi in
    config.
    """

    stores: ClassVar[dict] = {}
    entity_memory_store = None
    learned_knowledge_store = None


def identita(agent) -> str:
    """L'agente ha un nome e il modello lo sa.

    Sono due cose separate: `name` finisce nel database e nelle intestazioni,
    ma resta un'etichetta per chi legge finche' `add_name_to_context` e'
    spento - ed e' spento di default. Un agente che si chiama Ares in
    `assistant.py` e non lo sa quando gli chiedi come si chiama e' il tipo di
    scollamento che non solleva errori.

    Il controllo guarda il system message costruito davvero, non i due
    attributi: e' l'unico posto dove si vede se il nome e la descrizione
    arrivano al modello.
    """
    from agno.agent import _messages
    from agno.run.base import RunContext
    from agno.session.agent import AgentSession

    esigi(bool(agent.name), "l'agente non ha un nome")
    esigi(bool(agent.description), "l'agente non ha una descrizione: il system message parte dalle istruzioni")
    prompt = _messages.get_system_message(
        agent=agent,
        session=AgentSession(session_id="prova-identita", user_id="prova-identita"),
        run_context=RunContext(run_id="prova", user_id="prova-identita", session_id="prova-identita"),
        tools=[],
    ).content
    esigi(agent.description[:40] in prompt, "la descrizione non arriva al modello")
    # Il nome va cercato fuori dalla descrizione: li' dentro c'e' comunque,
    # perche' la descrizione lo pronuncia, e il controllo passerebbe anche con
    # add_name_to_context spento. E' successo alla prima stesura.
    senza_descrizione = prompt.replace(agent.description, "")
    esigi(
        agent.name in senza_descrizione,
        "il nome " + repr(agent.name) + " arriva al modello solo dentro la descrizione: manca add_name_to_context",
    )
    return agent.name + " si presenta in " + str(len(prompt)) + " caratteri di system message"


def strumenti(agent, user_id: str) -> str:
    """Gli strumenti che Ares dovrebbe avere arrivano davvero al modello.

    Ognuno e' acceso da un flag diverso e nessuno protesta se manca:
    un agente senza `search_past_sessions` non ha modo di dire che non puo'
    guardare in un'altra sessione, semplicemente risponde di non saperlo.

    Il controllo risolve la lista come la risolve Agno all'inizio di un turno,
    invece di rileggere i flag: e' l'unico punto in cui si vede la differenza
    tra "configurato" e "consegnato". Due cose che qui dentro divergono:
    `agent._learning` resta None finche' non si tocca `learning_machine`, e
    `UserMemoryStore.get_tools` restituisce una lista vuota se `user_id` e'
    falso - senza passarlo, `update_user_memory` manca anche quando la
    configurazione e' giusta, e si finirebbe a riparare cio' che funziona.
    """
    from agno.agent import _tools
    from agno.run.agent import RunOutput
    from agno.run.base import RunContext
    from agno.session.agent import AgentSession

    # Non e' una riga inutile: leggere la proprieta' risolve `agent._learning`,
    # che resta None finche' nessuno la tocca, e senza quello `get_tools` salta
    # in blocco gli strumenti di apprendimento.
    assert agent.learning_machine is not None
    sessione = AgentSession(session_id="prova-strumenti", user_id=user_id)
    voci = _tools.get_tools(
        agent=agent,
        run_response=RunOutput(run_id="prova-strumenti"),
        run_context=RunContext(run_id="prova-strumenti", user_id=user_id, session_id="prova-strumenti"),
        session=sessione,
        user_id=user_id,
    )
    nomi = set()
    for voce in voci:
        if hasattr(voce, "functions"):
            nomi.update(voce.functions.keys())
        else:
            nomi.add(getattr(voce, "name", None) or getattr(voce, "__name__", ""))

    attesi = {}
    if config.SEARCH_PAST_SESSIONS:
        attesi["search_past_sessions"] = "SEARCH_PAST_SESSIONS"
        attesi["read_past_session"] = "SEARCH_PAST_SESSIONS"
    if config.READ_CHAT_HISTORY:
        attesi["get_chat_history"] = "READ_CHAT_HISTORY"
    if config.LEARN_USER_MEMORY and config.MEMORY_AGENT_TOOLS:
        attesi["update_user_memory"] = "MEMORY_AGENT_TOOLS"
    if config.LEARN_KNOWLEDGE:
        attesi["search_learnings"] = "LEARN_KNOWLEDGE"
        attesi["save_learning"] = "LEARN_KNOWLEDGE"
    # Il verso opposto, e va provato per primo: nessuna istruzione deve
    # nominare uno strumento non consegnato, o il modello legge un ordine di
    # chiamare qualcosa che non ha, e un 9B ci prova. Sta prima del ritorno
    # non concludente perche' il caso peggiore e' proprio quello in cui gli
    # strumenti sono tutti spenti e le istruzioni sono rimaste indietro.
    istruzioni = " ".join(t for t in agent.instructions if isinstance(t, str))
    for nome in (
        "update_user_memory",
        "search_past_sessions",
        "read_past_session",
        "get_chat_history",
        "remember_about",
        "search_learnings",
        "save_learning",
        config.WORKSPACE_PREFIX + "run_command",
        config.WORKSPACE_PREFIX + "read_file",
    ):
        if nome in istruzioni:
            esigi(nome in nomi, "le istruzioni nominano " + nome + ", che non arriva al modello")

    if not attesi:
        return NON_CONCLUSIVO + "gli strumenti opzionali sono spenti in config.py, e nessuna istruzione li nomina"

    for nome, flag in sorted(attesi.items()):
        esigi(nome in nomi, nome + " non arriva al modello benche' " + flag + " sia acceso")

    return str(len(attesi)) + " strumenti su " + str(len(nomi)) + " consegnati: " + ", ".join(sorted(attesi))


def spazio_di_lavoro(agent, user_id: str) -> str:
    """Lo spazio sul disco arriva al modello con i propri nomi e i propri permessi.

    Tre cose che non sollevano errori da sole. I nomi: senza prefisso Agno ne
    scarta cinque su otto con un WARNING, e il modello resta con un
    `read_file` che crede legga il disco. I permessi: `requires_confirmation`
    viene deciso alla registrazione e sopravvive alla rinomina, ma se
    smettesse di farlo la shell partirebbe senza chiedere niente e la prova
    successiva sarebbe l'utente. La radice: e' l'unica cosa che tiene Ares
    fuori dal proprio codice e dall'archivio.

    Gli strumenti si risolvono fino allo schema per il modello, non alla
    lista del toolkit: e' li' che si vedono sia i nomi consegnati sia i flag
    di conferma che li accompagnano.
    """
    from agno.agent import _tools
    from agno.run.agent import RunOutput
    from agno.run.base import RunContext
    from agno.session.agent import AgentSession

    istruzioni = " ".join(t for t in agent.instructions if isinstance(t, str))
    if not config.WORKSPACE:
        esigi(
            config.WORKSPACE_PREFIX not in istruzioni,
            "le istruzioni parlano dello spazio di lavoro, che e' spento in config.py",
        )
        return NON_CONCLUSIVO + "WORKSPACE e' spento in config.py, e nessuna istruzione lo nomina"

    esigi(
        str(config.WORKSPACE_DIR).startswith(tempfile.gettempdir()),
        "la prova sta usando lo spazio di lavoro vero: " + str(config.WORKSPACE_DIR),
    )
    esigi(config.WORKSPACE_DIR.is_dir(), "lo spazio di lavoro non e' stato creato")

    assert agent.learning_machine is not None
    sessione = AgentSession(session_id="prova-spazio", user_id=user_id)
    voci = _tools.get_tools(
        agent=agent,
        run_response=RunOutput(run_id="prova-spazio"),
        run_context=RunContext(run_id="prova-spazio", user_id=user_id, session_id="prova-spazio"),
        session=sessione,
        user_id=user_id,
    )
    funzioni = _tools.determine_tools_for_model(
        agent,
        agent.model,
        voci,
        RunOutput(run_id="prova-spazio"),
        RunContext(run_id="prova-spazio", user_id=user_id, session_id="prova-spazio"),
        sessione,
        async_mode=False,
    )
    consegnati = {f.name: f for f in funzioni if hasattr(f, "name")}

    attesi = {
        config.WORKSPACE_PREFIX + Workspace._ALIASES[alias]: alias in config.WORKSPACE_CONFIRM
        for alias in list(config.WORKSPACE_ALLOWED) + list(config.WORKSPACE_CONFIRM)
    }
    for nome, va_confermato in sorted(attesi.items()):
        esigi(nome in consegnati, nome + " non arriva al modello")
        esigi(
            bool(consegnati[nome].requires_confirmation) == va_confermato,
            nome + (" gira senza chiedere niente" if va_confermato else " chiede il permesso e non dovrebbe"),
        )

    # La collisione e' silenziosa per costruzione: Agno tiene il primo nome
    # arrivato e scrive un WARNING. Qui si guarda l'intersezione, non i log.
    del_quaderno = set(build_filesystem(user_id).tools().functions)
    comuni = del_quaderno & set(attesi)
    esigi(not comuni, "lo spazio di lavoro e il quaderno privato si contendono: " + ", ".join(sorted(comuni)))

    # La guardia sulla radice, provata facendola scattare: una radice che
    # contiene il progetto deve fermare la costruzione, non passare.
    scelta_vera = config.WORKSPACE_DIR
    config.WORKSPACE_DIR = config.BASE_DIR
    try:
        build_workspace()
    except ValueError:
        pass
    else:
        esigi(False, "una radice che contiene il progetto non ha fermato build_workspace")
    finally:
        config.WORKSPACE_DIR = scelta_vera

    return (
        str(len(attesi))
        + " strumenti su "
        + str(len(consegnati))
        + ", "
        + str(sum(1 for v in attesi.values() if v))
        + " da confermare, nessun nome in comune col quaderno"
    )


def tempo(agent, lm, user_id: str) -> str:
    """Ares sa che ora e', e da quando sa le cose che sa.

    Due canali distinti e nessuno dei due si lamenta se manca. L'ora corrente
    la mette Agno a ogni turno; le date delle memorie ci sono in archivio da
    sempre ma il rendering di serie le scarta, quindi la loro presenza nel
    prompt dipende solo dallo schema personalizzato. Il controllo guarda il
    system message costruito davvero: e' l'unico posto in cui si vede se una
    data e' arrivata o si e' fermata in archivio.
    """
    from agno.agent import _messages
    from agno.run.base import RunContext
    from agno.session.agent import AgentSession

    prompt = _messages.get_system_message(
        agent=agent,
        session=AgentSession(session_id="prova-tempo", user_id=user_id),
        run_context=RunContext(run_id="prova", user_id=user_id, session_id="prova-tempo"),
        tools=[],
    ).content

    esigi("current time" in prompt.lower(), "l'ora corrente non arriva al modello: manca add_datetime_to_context")
    # I microsecondi sono il segno che datetime_format non e' passato: Agno
    # ripiega su str(datetime), che li porta con se'.
    riga_ora = next(r for r in prompt.splitlines() if "current time" in r.lower())
    esigi(
        "." not in riga_ora.split("current time is")[-1].split(",")[0],
        "l'ora corrente arriva grezza, con i microsecondi: " + riga_ora.strip(),
    )

    if "user_memory" not in lm.stores:
        return NON_CONCLUSIVO + "user_memory e' spento: resta verificata solo l'ora corrente"
    if not config.DATE_MEMORIE:
        return NON_CONCLUSIVO + "DATE_MEMORIE e' spento: le memorie arrivano senza data, come di serie"

    blocco = prompt.split("<user_memory>", 1)[-1].split("</user_memory>", 1)[0]
    oggi = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    esigi(
        blocco.count("[" + oggi + "]") == len(MEMORIE_SEMINATE),
        "le memorie arrivano senza la data di oggi: lo schema non e' quello di schemas.py",
    )
    return "ora corrente formattata, " + str(len(MEMORIE_SEMINATE)) + " memorie datate nel prompt"


def lettori_tolleranti(user_id: str) -> str:
    """I lettori sopravvivono a uno store spento invece di morire.

    `config.py` invita a spegnere gli store per guadagnare latenza, e la
    LearningMachine non li costruisce affatto: `lm.user_profile_store`
    diventa None e `/profilo` moriva con un AttributeError su un'opzione
    documentata.
    """
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        stampa_store(None, "Profilo", user_id=user_id)
    detto = catturato.getvalue().strip()
    esigi("spento" in detto, "uno store spento non viene annunciato: " + repr(detto))

    vuota = _MacchinaSenzaStore()
    esigi(leggi_entita(vuota, user_id=user_id) == [], "leggi_entita non regge uno store di entita' spento")
    esigi(leggi_intuizioni(vuota, user_id=user_id) == [], "leggi_intuizioni non regge uno store spento")
    return "store spento annunciato, letture vuote invece di eccezioni"


def profilo_rileggibile(lm, user_id: str) -> str:
    """Il profilo torna dal database nella classe e con i campi con cui e' stato scritto."""
    if "user_profile" not in lm.stores:
        return NON_CONCLUSIVO + "user_profile" + " e' spento in config: non c'e' niente da seminare"
    schema = lm.stores["user_profile"].schema
    profilo = lm.user_profile_store.get(user_id=user_id)
    esigi(profilo is not None, "il profilo seminato non si rilegge per " + user_id)
    esigi(
        isinstance(profilo, schema),
        "il profilo riletto e' " + type(profilo).__name__ + " invece di " + schema.__name__,
    )
    for campo, atteso in PROFILO_SEMINATO.items():
        ottenuto = getattr(profilo, campo, None)
        esigi(ottenuto == atteso, campo + " e' tornato " + repr(ottenuto) + " invece di " + repr(atteso))
    popolati = campi_popolati(profilo)
    esigi(
        len(popolati) == len(PROFILO_SEMINATO),
        "campi popolati: "
        + str(len(popolati))
        + " invece di "
        + str(len(PROFILO_SEMINATO))
        + " ("
        + ", ".join(popolati)
        + ")",
    )
    return type(profilo).__name__ + ", " + str(len(popolati)) + " campi come seminati"


def memorie_rileggibili(lm, user_id: str) -> str:
    """Le memorie si rileggono tutte, con la chiave giusta e il testo giusto."""
    if "user_memory" not in lm.stores:
        return NON_CONCLUSIVO + "user_memory" + " e' spento in config: non c'e' niente da seminare"
    contenitore = lm.user_memory_store.get(user_id=user_id)
    memorie = getattr(contenitore, "memories", None) or []
    esigi(
        len(memorie) == len(MEMORIE_SEMINATE),
        str(len(memorie)) + " memorie rilette su " + str(len(MEMORIE_SEMINATE)) + " seminate",
    )
    testi = set()
    for memoria in memorie:
        testo = memoria.get("content")
        esigi(bool(testo), "una memoria e' senza content: " + repr(memoria)[:80])
        testi.add(testo)
    mancanti = set(MEMORIE_SEMINATE) - testi
    esigi(not mancanti, "memorie seminate e non rilette: " + str(sorted(mancanti)))
    return str(len(memorie)) + " memorie con il testo seminato"


def contesto_rileggibile(lm, session_id: str) -> str:
    """Il contesto della sessione torna nella classe e con i campi con cui e' stato scritto."""
    if "session_context" not in lm.stores:
        return NON_CONCLUSIVO + "session_context e' spento in config: non c'e' niente da seminare"
    contesto = lm.session_context_store.get(session_id=session_id)
    esigi(contesto is not None, "il contesto seminato non si rilegge per la sessione " + session_id)
    schema = lm.stores["session_context"].schema
    esigi(
        isinstance(contesto, schema),
        "il contesto riletto e' " + type(contesto).__name__ + " invece di " + schema.__name__,
    )
    for campo, atteso in CONTESTO_SEMINATO.items():
        ottenuto = getattr(contesto, campo, None)
        esigi(ottenuto == atteso, campo + " e' tornato " + repr(ottenuto) + " invece di " + repr(atteso))
    return type(contesto).__name__ + ", " + str(len(campi_popolati(contesto))) + " campi come seminati"


def entita_complete(lm, user_id: str) -> str:
    """Lo store restituisce tutte le entita' che stanno in archivio.

    Il confronto e' con il conteggio grezzo in SQLite, non con il numero
    seminato: e' il controllo che avrebbe visto `/entita` stampare "Nessuna
    entita' registrata" con tre entita' salvate, e passare dallo stesso
    percorso di lettura del difetto non lo avrebbe mai rilevato. Il numero
    seminato serve come terza voce: se archivio e store concordano su un
    valore sbagliato, lo dice questa.
    """
    if "entity_memory" not in lm.stores:
        return NON_CONCLUSIVO + "entity_memory e' spento in config: non c'e' niente da seminare"
    namespace = namespace_entita(user_id)
    in_archivio = conta_apprendimenti("entity_memory", namespace)
    esigi(
        in_archivio == len(ENTITA_SEMINATE),
        "seminate " + str(len(ENTITA_SEMINATE)) + " entita' ma in " + namespace + " ce ne sono " + str(in_archivio),
    )
    # Limite alto e non il default: un elenco tagliato dalla paginazione
    # sembrerebbe un difetto di lettura.
    rilette = leggi_entita(lm, user_id=user_id, limit=1000)
    esigi(
        len(rilette) == in_archivio,
        "in archivio ci sono " + str(in_archivio) + " entita' ma lo store ne restituisce " + str(len(rilette)),
    )
    return str(len(rilette)) + " entita' rilette su " + str(in_archivio) + " in archivio"


def fatti_leggibili(lm, user_id: str) -> str:
    """I fatti delle entita' si leggono con la chiave giusta.

    Sono dict con chiave `content`: leggere `fact` restituisce None per
    ogni fatto senza sollevare niente, e le entita' si stampano vuote.
    """
    if "entity_memory" not in lm.stores:
        return NON_CONCLUSIVO + "entity_memory e' spento in config: non c'e' nessun fatto da leggere"
    righe = []
    for entita in leggi_entita(lm, user_id=user_id, limit=1000):
        righe.extend(righe_entita(entita, max_fatti=100))
    fatti = [riga for riga in righe if riga.strip().startswith("fatto:")]
    for riga in fatti:
        esigi("fatto: None" not in riga, "un fatto e' None: la chiave del dizionario non e' piu' `content`")
    esigi(
        len(fatti) == FATTI_SEMINATI,
        str(len(fatti)) + " fatti letti su " + str(FATTI_SEMINATI) + " seminati",
    )
    return str(len(fatti)) + " fatti letti, nessuno vuoto"


def entita_cercate(agent, user_id: str) -> str:
    """`/entita <testo>` filtra davvero, invece di restituire l'archivio.

    Il difetto che questo controllo esiste per prendere non e' un errore ma
    una risposta larga: la ricerca di Agno verifica la query contro tutti i
    valori dell'entita', namespace e date comprese, e qui il namespace e'
    `user/<utente>/personale`. Cercare "person" restituiva le entita' di ogni
    tipo, e chi legge conclude che il filtro sia decorativo.
    """
    lm = agent.learning_machine
    if "entity_memory" not in lm.stores:
        return NON_CONCLUSIVO + "entity_memory e' spento in config: non c'e' niente da cercare"
    intero = leggi_entita(lm, user_id=user_id, limit=1000)
    esigi(len(intero) > 1, "serve piu' di un'entita' in archivio perche' un filtro voglia dire qualcosa")

    # Il nome: il caso facile, ed e' l'unico che un filtro rotto supera lo stesso.
    per_nome = [nome_entita(e) for e in leggi_entita(lm, user_id=user_id, query="Uno")]
    esigi(per_nome == ["Entita Uno"], "cercare un nome non restituisce quell'entita': " + repr(per_nome))

    # Un fatto: la ricerca guarda dentro, non solo il nome.
    per_fatto = [nome_entita(e) for e in leggi_entita(lm, user_id=user_id, query="quattro")]
    esigi(per_fatto == ["Entita Due"], "cercare un fatto non trova la sua entita': " + repr(per_fatto))

    # Il caso che conta: "person" e' dentro `personale`, cioe' dentro il
    # namespace di ogni entita' di questo archivio. Deve restare il tipo.
    per_tipo = sorted(nome_entita(e) for e in leggi_entita(lm, user_id=user_id, query="person"))
    esigi(
        per_tipo == ["Entita Due"],
        "una parola del namespace pesca entita' che non la contengono: " + repr(per_tipo),
    )

    esigi(leggi_entita(lm, user_id=user_id, query="pipppo") == [], "una parola inventata trova qualcosa")

    # Il filtro esiste anche a monte: l'argomento del comando deve arrivare
    # fino allo store. Un `/entita` che lo ignora stampa l'archivio intero e
    # sembra rispondere.
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        gestisci_comando("/entita Uno", agent, "sessione", user_id)
    stampato = catturato.getvalue()
    esigi("Entita Uno" in stampato, "/entita con un argomento non trova l'entita' cercata")
    esigi("Entita Due" not in stampato, "/entita ignora l'argomento e stampa l'archivio intero")
    return "nome, fatto e tipo trovano una sola entita'; il namespace non ne pesca nessuna"


def nome_entita(entita) -> str:
    return str(getattr(entita, "name", "?"))


def _sessione_finta(nome: str, creata: int, domanda: str, user_id: str):
    """Una conversazione di una domanda sola, pronta per il database.

    `agent_id` sul RunOutput non e' decorativo: senza,
    `AgentSession.from_dict` scarta il run in silenzio e la sessione si
    rilegge senza scambi.
    """
    from agno.models.message import Message
    from agno.run.agent import RunOutput
    from agno.session.agent import AgentSession

    run = RunOutput(
        run_id="run-" + nome,
        agent_id="ares-prova",
        messages=[Message(role="user", content=domanda), Message(role="assistant", content="risposta")],
    )
    return AgentSession(session_id=nome, agent_id="ares-prova", user_id=user_id, created_at=creata, runs=[run])


def semina_sessioni(agent, user_id: str) -> list:
    """Quattro conversazioni finte, con date scelte per distinguere gli ordini.

    `upsert_session` scrive `updated_at = created_at` all'inserimento e
    `updated_at = adesso` su un aggiornamento: da qui la ri-scrittura di una
    sola sessione, che e' cio' che separa "ordinata per ultima modifica" da
    "ordinata per creazione" e da "nell'ordine in cui e' stata scritta".
    """
    semi = [
        ("prova-alfa", 1000, "domanda di alfa"),
        ("prova-beta", 3000, "domanda di beta"),
        ("lavoro-gamma", 2000, "domanda di gamma"),
        ("lavoro-delta", 1500, "domanda di delta"),
    ]
    for nome, creata, domanda in semi:
        agent.db.upsert_session(_sessione_finta(nome, creata, domanda, user_id))
    # Ri-scritta per ultima: la sua ultima modifica e' adesso, la sua
    # creazione resta la terza delle quattro.
    agent.db.upsert_session(_sessione_finta("lavoro-gamma", 2000, "domanda di gamma", user_id))
    return ["lavoro-gamma", "prova-beta", "lavoro-delta", "prova-alfa"]


def sessioni_elencate(agent, user_id: str, session_id: str) -> str:
    """L'elenco delle sessioni e' ordinato per ultima modifica, filtra e si annota.

    L'ordine atteso non coincide con nessuno degli ordini sbagliati
    plausibili - ne' quello di scrittura, ne' il suo rovescio, ne' la data di
    creazione nei due versi - perche' un `sort_by` che il database non
    riconosce non solleva niente: lascia la query senza ORDER BY e le righe
    escono nell'ordine in cui stanno sul disco.
    """
    atteso = semina_sessioni(agent, user_id)
    lette = [s.session_id for s in leggi_sessioni(agent, user_id=user_id)]
    esigi(lette == atteso, "ordine per ultima modifica non rispettato: " + repr(lette))

    # Il filtro guarda il nome, e taglia dopo aver filtrato: una sessione che
    # corrisponde ma e' vecchia deve restare visibile.
    filtrate = [s.session_id for s in leggi_sessioni(agent, user_id=user_id, query="LAVORO")]
    esigi(filtrate == ["lavoro-gamma", "lavoro-delta"], "il filtro sul nome non funziona: " + repr(filtrate))
    esigi(leggi_sessioni(agent, user_id=user_id, query="pipppo") == [], "un filtro inventato trova qualcosa")

    # Nessuna sessione di un altro utente.
    esigi(
        leggi_sessioni(agent, user_id=UTENTE_DI_CONTROLLO) == [],
        "le sessioni di un utente si vedono da un altro utente",
    )

    prima = leggi_sessioni(agent, user_id=user_id)[0]
    righe = " ".join(righe_sessione(prima, corrente=True))
    esigi("(questa)" in righe, "la sessione in corso non e' marcata: " + repr(righe))
    esigi("1 scambio" in righe, "il numero di scambi e' sbagliato: " + repr(righe))
    esigi("domanda di gamma" in righe, "la prima domanda non compare: " + repr(righe))
    esigi("(questa)" not in " ".join(righe_sessione(prima)), "ogni sessione risulta quella in corso")

    # Si filtra prima e si taglia dopo. Al contrario, una sessione piu'
    # vecchia delle prime mostrate sarebbe irraggiungibile proprio quando la
    # si cerca per nome. Il tetto si abbassa qui invece di seminare venti
    # conversazioni per superarlo.
    tetto = config.SESSIONI_ELENCO
    config.SESSIONI_ELENCO = 2
    try:
        catturato = io.StringIO()
        with contextlib.redirect_stdout(catturato):
            gestisci_comando("/sessioni", agent, session_id, user_id)
        troncato = catturato.getvalue()
        esigi("altre 2" in troncato, "l'elenco tagliato non dice quante ne restano: " + repr(troncato))
        esigi(atteso[3] not in troncato, "il tetto non taglia niente")
        catturato = io.StringIO()
        with contextlib.redirect_stdout(catturato):
            gestisci_comando("/sessioni " + atteso[3], agent, session_id, user_id)
        esigi(
            atteso[3] in catturato.getvalue(),
            "una sessione oltre il tetto non si trova nemmeno cercandola: si taglia prima di filtrare",
        )
    finally:
        config.SESSIONI_ELENCO = tetto

    # La nota sulla sessione in corso va detta quando e' vera e taciuta
    # quando non lo e'. Tre stati, in fila, perche' e' il passaggio da uno
    # all'altro a distinguerli: prima assente davvero, poi in archivio, poi
    # in archivio ma esclusa da un filtro.
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        gestisci_comando("/sessioni", agent, session_id, user_id)
    stampato = catturato.getvalue()
    esigi(session_id in stampato, "l'assenza della sessione in corso non viene spiegata")
    for nome in atteso:
        esigi(nome in stampato, "/sessioni non stampa " + nome)

    # Ora la sessione in corso e' in archivio: la piu' vecchia, cosi' l'ordine
    # gia' verificato non cambia sopra di lei.
    agent.db.upsert_session(_sessione_finta(session_id, 500, "domanda di questa", user_id))
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        gestisci_comando("/sessioni", agent, session_id, user_id)
    presente = catturato.getvalue()
    esigi("(questa)" in presente, "la sessione in corso non e' marcata nell'elenco: " + repr(presente))
    esigi(
        "dal primo turno salvato" not in presente,
        "l'elenco dice che la sessione in corso manca mentre la sta stampando",
    )

    # In archivio ma oltre il tetto: la nota sarebbe falsa anche qui, e la
    # sessione va contata fra quelle che restano, non dichiarata mancante.
    tetto = config.SESSIONI_ELENCO
    config.SESSIONI_ELENCO = 2
    try:
        catturato = io.StringIO()
        with contextlib.redirect_stdout(catturato):
            gestisci_comando("/sessioni", agent, session_id, user_id)
        oltre = catturato.getvalue()
        esigi("(questa)" not in oltre, "il tetto non taglia la sessione in corso: " + repr(oltre))
        esigi(
            "dal primo turno salvato" not in oltre,
            "oltre il tetto l'elenco dichiara mancante una sessione che ha in archivio",
        )
    finally:
        config.SESSIONI_ELENCO = tetto

    # Esclusa da un filtro, non assente dall'archivio: la nota qui sarebbe
    # falsa, ed e' il caso che il primo controllo da solo lasciava passare.
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        gestisci_comando("/sessioni lavoro", agent, session_id, user_id)
    filtrato = catturato.getvalue()
    esigi(
        "dal primo turno salvato" not in filtrato,
        "sotto un filtro l'elenco dichiara mancante una sessione che ha in archivio",
    )
    return str(len(atteso)) + " sessioni ordinate per ultima modifica, filtro, marcatore e nota verificati"


def file_isolati(user_id: str) -> str:
    """I file di un utente non si vedono da un altro utente.

    Col namespace `default` di Agno erano condivisi da chiunque, mentre le
    memorie erano gia' segregate: la segregazione a meta' e' peggio di
    nessuna, perche' non si nota.
    """
    esigi(
        user_id != UTENTE_DI_CONTROLLO,
        "l'utente in prova e' lo stesso di controllo: non c'e' niente da confrontare",
    )
    miei = {f.path for f in build_filesystem(user_id).list()}
    altrui = {f.path for f in build_filesystem(UTENTE_DI_CONTROLLO).list()}
    esigi(FILE_SEMINATO[0] in miei, "il file seminato non si rilegge: " + str(sorted(miei)))
    condivisi = miei & altrui
    esigi(not condivisi, "un altro utente vede " + str(sorted(condivisi)))
    return str(len(miei)) + " file di " + user_id + ", nessuno visibile a un altro utente"


def indice_vettoriale(lm) -> str:
    """La tabella LanceDB si apre e la ricerca ibrida ha il suo motore.

    Nessun embedding viene calcolato: l'embedder resta fuori dalla VRAM.
    Della meta' testuale della ricerca ibrida si controlla solo che
    `tantivy` sia importabile, perche' senza si degrada alla sola
    similarita' vettoriale.
    """
    if lm.knowledge is None:
        return NON_CONCLUSIVO + "le intuizioni sono spente in config: non c'e' nessun indice da aprire"
    vdb = lm.knowledge.vector_db
    esigi(vdb.exists(), "la tabella " + vdb.table_name + " non esiste in " + vdb.uri)
    if vdb.search_type.value == "hybrid":
        importlib.import_module("tantivy")
    return "tabella " + vdb.table_name + " aperta, ricerca " + vdb.search_type.value


def archivio_privato() -> str:
    """Lo stato appreso non e' leggibile dagli altri utenti della macchina.

    La cronologia accanto nasce a 0600 e gli snapshot a 0700/0600 da sempre;
    i due database e l'indice vettoriale, che contengono le stesse
    conversazioni, nascevano invece con la umask del processo - 0644 e 0755 su
    un'installazione tipica. Si proteggeva la copia e non l'originale.

    La directory e' il controllo che regge davvero, perche' senza il diritto di
    attraversarla i modi dei file dentro non si raggiungono; i database sono
    comunque verificati uno a uno, perche' un archivio esce da tmp/ ogni volta
    che qualcuno lo copia altrove.

    Su Windows non c'e' niente da verificare: `rendi_privato` non tocca la
    DACL ereditata, e chmod renderebbe i file soltanto read-only.
    """
    if os.name != "posix":
        return NON_CONCLUSIVO + "i permessi numerici sono una proprieta' POSIX"

    def modo(percorso: Path) -> str:
        return oct(percorso.stat().st_mode)[-3:]

    directory = [("archivio", Path(config.TMP_DIR)), ("indice vettoriale", Path(config.LANCEDB_URI))]
    for etichetta, percorso in directory:
        esigi(percorso.is_dir(), etichetta + " assente: " + str(percorso))
        esigi(modo(percorso) == "700", etichetta + " attraversabile da altri: " + modo(percorso))

    database = [Path(config.DB_FILE), Path(config.FS_DB_FILE)]
    for percorso in database:
        esigi(percorso.is_file(), "database assente: " + str(percorso))
        esigi(
            modo(percorso) == "600",
            "database leggibile da altri: " + percorso.name + " " + modo(percorso),
        )

    # Un file gia' scritto con i permessi larghi deve essere corretto alla
    # costruzione successiva, altrimenti la protezione varrebbe solo per i
    # cloni nuovi e lascerebbe scoperti proprio gli archivi con dentro
    # qualcosa.
    database[0].chmod(0o644)
    build_db()
    esigi(
        modo(database[0]) == "600",
        "archivio preesistente non corretto: " + modo(database[0]),
    )

    return str(len(directory)) + " directory a 700 e " + str(len(database)) + " database a 600, anche se preesistenti"


def import_senza_effetti() -> str:
    """Importare `config` non tocca il disco.

    Prima il modulo creava la directory dello stato nel proprio corpo, e
    leggere una costante produceva un effetto: `preflight.py` importava
    `config` per tre nomi di modello e si lasciava dietro un archivio, e ogni
    prova ha dovuto imparare a scrivere `ARES_TMP` prima dell'import, con un
    commento che spiega perche'. La creazione ora e' esplicita, e questa prova
    e' cio' che impedisce che torni: un `mkdir` rimesso nel corpo del modulo
    non romperebbe niente e non se ne accorgerebbe nessuno.

    In un processo separato perche' qui `config` e' importato da un pezzo, e
    un modulo si importa una volta sola.
    """
    prova = Path(tempfile.mkdtemp(prefix="ares-import-"))
    ambiente = os.environ.copy()
    ambiente["ARES_TMP"] = str(prova / "stato")
    codice = (
        "import os, config;"
        "print('dopo-import', os.path.exists(config.TMP_DIR));"
        "config.prepara_archivio();"
        "print('dopo-prepara', os.path.exists(config.TMP_DIR))"
    )
    try:
        figlio = subprocess.run(
            [sys.executable, "-c", codice],
            cwd=config.BASE_DIR,
            env=ambiente,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        esigi(figlio.returncode == 0, "l'import di config e' fallito: " + figlio.stderr[-400:])
        esigi("dopo-import False" in figlio.stdout, "importare config ha creato la directory dello stato")
        esigi("dopo-prepara True" in figlio.stdout, "prepara_archivio() non ha creato la directory dello stato")
    finally:
        shutil.rmtree(prova, ignore_errors=True)
    return "nessuna directory creata all'import, creata da prepara_archivio()"


def conferme_leggibili() -> str:
    """Un comando lungo arriva intero e con i confini visibili alla conferma.

    `Workspace` non e' una sandbox di processo - lo dice il suo docstring e
    `run_command` risponde su `/etc/hostname` - quindi la conferma umana e'
    l'unico confine che regge. Cio' che l'utente legge in quel momento e'
    parte del confine: se un comando venisse troncato, l'autorizzazione
    riguarderebbe qualcosa di diverso da cio' che viene eseguito.

    Il caso lungo verifica anche comandi distribuiti su piu' righe.
    """
    comando = [
        "bash",
        "-lc",
        "find . -name '*.tmp' -newer riferimento.txt -print0 | xargs -0 rm -f",
    ] + ["--opzione-" + str(n) for n in range(17)]
    esecuzione = ToolExecution(
        tool_name=config.WORKSPACE_PREFIX + "run_command",
        tool_args={"args": comando, "timeout": 120},
    )
    righe = righe_richiesta(esecuzione, radice=config.WORKSPACE_DIR)
    testo = "\n".join(righe)

    citato = [r for r in righe if r.strip().startswith("args:")]
    esigi(len(citato) == 1, "la riga del comando non e' una sola: " + repr(citato))
    ricomposto = citato[0].split("args: ", 1)[1]

    # Il controllo vero e' il giro di ritorno: se la riga a schermo si
    # rilegge come la lista di partenza, allora niente e' stato perso,
    # niente aggiunto e le virgolette cadono dove separano davvero un
    # argomento dal successivo. Confrontare i pezzi uno per uno non
    # basterebbe: `shlex.join` cita, quindi l'elemento con gli spazi dentro
    # non compare mai verbatim, ed e' proprio quello che va reso bene.
    # Il troncamento si controlla per primo: e' la regressione piu' probabile,
    # e su una riga tagliata `shlex.split` morirebbe con "No closing
    # quotation", che dice cosa e' successo alla stringa e non al comando.
    esigi("..." not in testo, "la conferma tronca il comando: " + repr(testo))
    esigi(
        shlex.split(ricomposto) == comando,
        "la riga ricomposta non torna al comando originale: " + repr(ricomposto),
    )
    esigi(str(config.WORKSPACE_DIR) in testo, "la conferma non dice in quale directory si esegue")
    esigi(any("timeout: 120" in r for r in righe), "un argomento semplice non compare")

    # Un valore multiriga non deve schiacciarsi su una riga sola.
    multiriga = righe_argomento("content", "prima\nseconda")
    esigi(len(multiriga) == 3, "un valore multiriga non viene aperto: " + repr(multiriga))

    return str(len(comando)) + " elementi resi per intero, citati e con la directory"


def metriche_del_turno() -> str:
    """La finestra mostrata e' il prompt vero, non la somma delle chiamate.

    `accumulate_model_metrics` somma dentro la riga del modello, quindi
    `metrics.input_tokens` comprende anche le estrazioni delle memorie.
    Presentarlo come occupazione della finestra sarebbe sbagliato in
    silenzio: i numeri qui sotto distinguono il totale del run dal prompt.
    """
    principale = ModelMetrics(
        id=config.MAIN_MODEL,
        input_tokens=7097,
        output_tokens=25,
        provider_metrics={"total_duration": 1197811950},
    )
    apprendimento = ModelMetrics(
        id=config.MAIN_MODEL,
        input_tokens=3902,
        output_tokens=959,
        provider_metrics={"total_duration": 16064666271},
    )
    risposta = _RunFinto(
        metrics=RunMetrics(
            input_tokens=10999,
            output_tokens=984,
            duration=20.1,
            details={"model": [principale], "learning_model": [apprendimento]},
        ),
        messages=[
            _MessaggioFinto("system", 0),
            _MessaggioFinto("user", 0),
            _MessaggioFinto("assistant", 6872),
            _MessaggioFinto("user", 0),
            _MessaggioFinto("assistant", 7097),
        ],
    )
    esigi(
        finestra_occupata(risposta) == 7097,
        "la finestra e' " + str(finestra_occupata(risposta)) + " invece di 7097",
    )
    riga = righe_metriche(risposta)[0]
    # Il tetto atteso si calcola da config con la stessa base 1024 del
    # renderer, cosi' il test segue ogni modifica di NUM_CTX.
    tetto = str(round(config.NUM_CTX / 1024.0, 1)) + "k"
    esigi("6.9k/" + tetto in riga, "la finestra non e' resa sul tetto di config: " + repr(riga))
    esigi("10.7k" not in riga and "10999" not in riga, "la riga mostra la somma del run: " + repr(riga))
    # I secondi dell'apprendimento vengono da total_duration, in nanosecondi:
    # senza la divisione uscirebbero sedici miliardi.
    esigi("16.1 s" in riga, "i secondi di apprendimento non sono resi: " + repr(riga))

    # Un turno interrotto o fallito arriva senza metriche, e la riga non si
    # inventa: e' il ramo che `esegui_turno` produce dopo un Ctrl-C.
    esigi(righe_metriche(_RunFinto(metrics=None, messages=[])) == [], "un turno senza metriche produce una riga")
    esigi(righe_metriche(_RunFinto(metrics=RunMetrics(), messages=[])) == [], "un turno a zero produce una riga")
    return "finestra 7097 distinta dai 10999 del run, nanosecondi convertiti"


class _EventoFinto:
    """Evento Agno ridotto ai campi letti dal normalizzatore del core."""

    def __init__(self, event, tool=None, error=None, content=None):
        self.event = event
        self.tool = tool
        self.error = error
        self.content = content


def esito_strumenti() -> str:
    """L'esito di uno strumento si vede, e un fallimento si vede una volta sola.

    Il rischio non e' che manchi una riga: e' che ne compaiano due. Un tool
    fallito emette `ToolCallCompleted` con dentro il testo dell'errore e
    **poi** `ToolCallError`, in entrambi i percorsi di Agno che li producono.
    Trattarli come alternative stampa l'errore due volte, e la prima volta
    lo presenta come un esito riuscito.
    """
    riuscito = ToolExecution(
        tool_name=config.WORKSPACE_PREFIX + "read_file",
        result="prima riga\nseconda riga\nterza riga\nquarta riga\nquinta riga",
        metrics=ToolCallMetrics(duration=0.42),
    )
    righe = righe_esito(riuscito)
    esigi(righe[0].strip().startswith("esito: "), "l'esito non si annuncia: " + repr(righe))
    esigi("58 caratteri" in righe[0], "i caratteri non sono contati: " + repr(righe[0]))
    esigi("0.4 s" in righe[0], "la durata non e' resa: " + repr(righe[0]))
    esigi(
        len(righe) == 1 + config.ESITO_RIGHE + 1,
        "l'anteprima non si ferma a " + str(config.ESITO_RIGHE) + " righe: " + repr(righe),
    )
    esigi("(+ altre 2 righe)" in righe[-1], "il taglio in altezza non si dichiara: " + repr(righe[-1]))

    # Una riga sola avanzata: "altre 1 righe" e' comparso in una prova vera.
    quattro = righe_esito(ToolExecution(result="a\nb\nc\nd"))
    esigi("un'altra riga" in quattro[-1], "il singolare non e' reso: " + repr(quattro[-1]))
    esatte = righe_esito(ToolExecution(result="a\nb\nc"))
    esigi(
        "+" not in esatte[-1],
        "un taglio viene annunciato dove non c'e': " + repr(esatte[-1]),
    )

    # Larghezza: una riga sola, lunghissima, come la restituisce un comando.
    lunga = righe_esito(ToolExecution(result="x" * 400))
    esigi(len(lunga[1]) <= config.ESITO_LARGHEZZA + 6, "il taglio in larghezza non avviene: " + str(len(lunga[1])))
    esigi(lunga[1].endswith("..."), "il taglio in larghezza non si dichiara: " + repr(lunga[1]))
    esigi("400 caratteri" in lunga[0], "la misura vera si perde nel troncamento: " + repr(lunga[0]))

    # La durata manca sul percorso di ripresa dopo una conferma: il segmento
    # deve sparire, non stampare zero.
    senza = righe_esito(ToolExecution(result="ok"))
    esigi(" s" not in senza[0], "senza metriche compare comunque una durata: " + repr(senza[0]))
    vuoto = righe_esito(ToolExecution(result=None))
    esigi(vuoto == ["   esito: nessun contenuto"], "un risultato vuoto non e' detto: " + repr(vuoto))

    # Il conto vero: quante volte compare l'errore attraversando i due eventi
    # nell'ordine in cui Agno li emette.
    fallito = ToolExecution(
        tool_name=config.WORKSPACE_PREFIX + "read_file",
        result="FileNotFoundError: pippo.md",
        tool_call_error=True,
    )
    flusso = [
        _EventoFinto("ToolCallStarted", tool=fallito),
        _EventoFinto("ToolCallCompleted", tool=fallito, content=fallito.result),
        _EventoFinto("ToolCallError", tool=fallito, error=fallito.result),
    ]
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        mostra_flusso(normalize_events(flusso))
    reso = catturato.getvalue()
    esigi(reso.count("pippo.md") == 1, "l'errore compare " + str(reso.count("pippo.md")) + " volte:\n" + reso)
    esigi("esito:" not in reso, "un tool fallito viene annunciato come riuscito:\n" + reso)
    esigi("errore: FileNotFoundError: pippo.md" in reso, "l'errore non e' reso:\n" + reso)

    # Un evento di errore senza testo non deve passare per riuscito.
    muto = righe_esito(ToolExecution(tool_call_error=True), errore="")
    esigi(muto == ["   errore: senza messaggio"], "un errore muto sparisce: " + repr(muto))

    return "errore reso una volta sola, anteprima tagliata in righe e larghezza"


def renderer_rich() -> str:
    """Il renderer e' sicuro anche fuori da un terminale interattivo.

    Test e pipe vedono una sola copia del testo, senza ANSI e senza
    interpretare come markup parentesi quadre provenienti da modello, path o
    argomenti. ``RichRunStream`` usa ora la stessa via append-only anche su un
    TTY; il controllo successivo verifica in piu' i comandi del cursore.
    """
    catturato = io.StringIO()
    renderer = CliRenderer(Console(file=catturato, color_system=None, force_terminal=False, width=120))
    renderer.line("[red]testo del modello[/red]")
    renderer.banner(modello="modello[Q8_0]", sessione="sessione[prova]", utente="utente[prova]")
    renderer.confirmation(
        [
            "Ares chiede di eseguire: workspace_run_command",
            "   args: ['bash', '-lc', 'printf [red]']",
        ]
    )
    with renderer.stream() as flusso:
        flusso.content("Risposta **Markdown** [cyan]")
        flusso.tool_started("workspace_read_file")
        flusso.tool_result(["   esito: 2 caratteri", "   | ok"])

    reso = catturato.getvalue()
    esigi("\x1b" not in reso, "una pipe riceve sequenze ANSI: " + repr(reso))
    esigi("[red]testo del modello[/red]" in reso, "il testo viene interpretato come markup")
    esigi("modello[Q8_0]" in reso, "il nome del modello perde il testo fra parentesi")
    esigi("sessione[prova]" in reso and "utente[prova]" in reso, "gli identificativi vengono interpretati")
    esigi(reso.count("Risposta **Markdown** [cyan]") == 1, "lo stream rediretto duplica la risposta")
    esigi("workspace_read_file" in reso and "2 caratteri" in reso, "gli eventi dei tool spariscono")
    return "markup letterale, zero ANSI, stream singolo e identificativi integri"


def renderer_tty_markdown_sicuro() -> str:
    """Anteprima a una riga, controlli filtrati e Markdown finale unico."""
    catturato = io.StringIO()
    console = Console(
        file=catturato,
        color_system="standard",
        force_terminal=True,
        width=120,
    )
    renderer = CliRenderer(console)
    ora = [100.0]
    with RichRunStream(renderer, clock=lambda: ora[0], auto_activity=False) as flusso:
        flusso.content("prima \x1b]")
        flusso.content("52;c;ZXNjaGVk\x07 dopo \x1b[")
        flusso.content("2J Markdown **letterale** ")
        esigi(flusso._activity_live is not None, "manca l'anteprima dello stream")
        esigi(
            flusso._activity_live._live_render._shape[1] == 1,
            "l'anteprima occupa piu' di una riga",
        )

        console.width = 1
        ora[0] += 0.125
        flusso.content("x")
        esigi(
            flusso._activity_live._live_render._shape[1] == 1,
            "il resize fa rifluire l'anteprima",
        )
        console.width = 120
        for _ in range(2_000):
            flusso.content("x")
        flusso.content(" fine \x1b]0;titolo\x1b")
        flusso.content("\\ C1 \x9b")
        flusso.content("2J")
        # Un comando non terminato non deve far uscire ne' il controllo ne'
        # il suo payload quando il turno si chiude.
        flusso.content(" coda-visibile \x1b]0;titolo-incompleto")

    reso = catturato.getvalue()
    esigi("\x1b]52" not in reso and "\x1b]0" not in reso, "un comando OSC raggiunge il terminale")
    esigi("\x1b[2J" not in reso and "\x9b2J" not in reso, "un comando CSI raggiunge il terminale")
    esigi(
        "ZXNjaGVk" not in reso and "titolo" not in reso,
        "resta il contenuto di un comando terminale",
    )

    # Tutto cio' che segue l'ultimo erase dell'anteprima e' output
    # permanente. Text.from_ansi rimuove soltanto gli stili Rich.
    finale = Text.from_ansi(reso.rsplit("\x1b[2K", 1)[-1]).plain
    esigi(
        all(finale.count(parola) == 1 for parola in ("prima", "dopo", "fine", "coda-visibile")),
        "il commit Markdown elimina o duplica testo: " + repr(finale),
    )
    esigi(finale.count("x") == 2_001, "i frammenti non arrivano tutti al commit finale")
    esigi("**letterale**" not in finale, "il Markdown resta sorgente letterale")
    esigi("Markdown letterale" in finale, "il Markdown finale perde contenuto")
    esigi("\x1b[2A" not in reso, "l'anteprima risale piu' di una riga")
    return "2.008 frammenti, anteprima a una riga e Markdown finale unico"


def indicatore_attivita() -> str:
    """L'attesa usa una sola riga e segue tutto il ciclo degli eventi.

    L'orologio e il pulse manuali evitano sleep fragili. La larghezza cambia
    fra due refresh: il testo ``no_wrap`` deve conservare altezza uno, che e'
    l'invariante da cui dipende la sicurezza del resize.
    """
    catturato = io.StringIO()
    console = Console(
        file=catturato,
        color_system="standard",
        force_terminal=True,
        width=80,
    )
    renderer = CliRenderer(console)
    ora = [100.0]
    with RichRunStream(
        renderer,
        clock=lambda: ora[0],
        auto_activity=False,
    ) as flusso:
        flusso.activity_started("Ares sta elaborando...")
        ora[0] = 101.9
        flusso.pulse_activity()
        esigi(flusso._activity_live is None, "l'indicatore compare prima della soglia")

        ora[0] = 102.0
        flusso.pulse_activity()
        esigi(flusso._activity_live is not None, "l'attesa lunga resta senza indicatore")
        esigi(
            flusso._activity_live._live_render._shape[1] == 1,
            "l'indicatore occupa piu' di una riga",
        )

        console.width = 1
        ora[0] = 165.0
        flusso.pulse_activity()
        esigi(
            flusso._activity_live._live_render._shape[1] == 1,
            "il resize cambia l'altezza dell'indicatore",
        )

        flusso.content("risposta-visibile ")
        esigi(flusso._activity_live is not None, "lo stream non aggiorna l'anteprima")
        esigi(not flusso._activity_waiting, "l'anteprima continua a sembrare in attesa")
        ora[0] = 167.0
        flusso.pulse_activity()
        esigi(flusso._activity_waiting, "l'indicatore non riparte dopo una nuova attesa")
        flusso.activity_stopped()
        console.width = 80
        flusso.content("finale")

    reso = catturato.getvalue()
    finale = Text.from_ansi(reso.rsplit("\x1b[2K", 1)[-1]).plain
    esigi(
        "risposta-visibile finale" in finale,
        "l'indicatore inserisce un ritorno a capo permanente: " + repr(finale),
    )
    esigi(
        reso.count("Ares sta elaborando...") == 0,
        "l'etichetta temporanea resta nello scrollback",
    )
    esigi("\x1b[2A" not in reso, "l'indicatore risale piu' di una riga")

    class FlussoFinto:
        def __init__(self):
            self.chiamate = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def activity_started(self, label):
            self.chiamate.append(("start", label))

        def activity_stopped(self):
            self.chiamate.append(("stop", None))

        def content(self, content):
            self.chiamate.append(("content", content))

        def flush(self):
            self.chiamate.append(("flush", None))

        def tool_started(self, nome):
            self.chiamate.append(("tool", nome))

        def tool_result(self, _righe, *, errore=False):
            self.chiamate.append(("result", errore))

        def run_error(self, messaggio):
            self.chiamate.append(("error", messaggio))

        def cancelled(self):
            self.chiamate.append(("cancelled", None))

    class UiFinta:
        def __init__(self, flusso):
            self.flusso = flusso

        def stream(self):
            return self.flusso

    registrato = FlussoFinto()
    strumento = ToolExecution(tool_name="workspace_read_file", result="ok")
    mostra_flusso(
        normalize_events(
            [
                _EventoFinto("ModelRequestStarted"),
                _EventoFinto("RunContent", content="ciao"),
                _EventoFinto("ModelRequestCompleted"),
                _EventoFinto("ToolCallStarted", tool=strumento),
                _EventoFinto("ToolCallCompleted", tool=strumento),
                _EventoFinto("PostHookStarted"),
                _EventoFinto("PostHookCompleted"),
                _EventoFinto("RunCompleted"),
                _EventoFinto("RunError", content="guasto"),
                _EventoFinto("RunCancelled"),
            ]
        ),
        ui=UiFinta(registrato),
    )
    etichette = [valore for azione, valore in registrato.chiamate if azione == "start"]
    esigi("Ares sta elaborando..." in etichette, "il modello non attiva l'indicatore")
    esigi(
        "workspace_read_file in esecuzione..." in etichette,
        "un tool lungo non attiva l'indicatore",
    )
    esigi(
        "Ares sta aggiornando cio' che ricorda..." in etichette,
        "il post-hook non attiva l'indicatore",
    )
    azioni = [azione for azione, _valore in registrato.chiamate]
    indice_errore = azioni.index("error")
    esigi(
        azioni[indice_errore - 1] == "stop",
        "l'errore precede la chiusura dello stato",
    )
    esigi(azioni[-2:] == ["stop", "cancelled"], "l'annullamento lascia l'indicatore attivo")
    return "soglia 2 s, resize a una riga, nessun newline e lifecycle chiuso"


def core_del_turno() -> str:
    """Il core normalizza eventi e possiede run/continue senza dipendere dalla UI."""
    pausa = RunOutput(run_id="pausa", status=RunStatus.paused, requirements=[])
    completato = RunOutput(run_id="fine", status=RunStatus.completed)

    class AgenteFinto:
        def __init__(self):
            self.chiamate = []

        def run(self, testo, **opzioni):
            self.chiamate.append(("run", testo, opzioni))
            return iter(
                [
                    _EventoFinto("ModelRequestStarted"),
                    _EventoFinto("RunContent", content="prima"),
                    _EventoFinto("RunPaused"),
                    pausa,
                ]
            )

        def continue_run(self, run_response, requirements, **opzioni):
            self.chiamate.append(("continue", run_response, requirements, opzioni))
            return iter(
                [
                    _EventoFinto("EventoFuturo", content="conservato"),
                    _EventoFinto("RunContent", content="seconda"),
                    _EventoFinto("RunCompleted"),
                    completato,
                ]
            )

    # Il primo evento precede anche la chiamata al provider: copre il lavoro
    # che Agno svolge prima di ModelRequestStarted.
    pigro = AgenteFinto()
    stream = TurnEngine(pigro).start("ciao")
    primo = next(stream)
    esigi(primo.kind is TurnEventKind.PROCESSING_STARTED, "manca lo stato iniziale del core")
    esigi(pigro.chiamate == [], "agent.run parte prima che il client veda l'attivita'")
    secondo = next(stream)
    esigi(secondo.kind is TurnEventKind.MODEL_STARTED, "l'evento modello non e' normalizzato")
    esigi(pigro.chiamate[0][0] == "run", "il core non avvia il run dopo lo stato iniziale")

    agente = AgenteFinto()
    eventi = []
    pause_risolte = []

    def risolvi(output):
        pause_risolte.append(output.run_id)
        return 1

    risultato = run_turn_cycle(
        agente,
        "domanda",
        on_event=eventi.append,
        resolve_pause=risolvi,
    )
    esigi(risultato is completato, "il ciclo non restituisce l'output della continuazione")
    esigi([c[0] for c in agente.chiamate] == ["run", "continue"], "sequenza run/continue errata")
    esigi(pause_risolte == ["pausa"], "la pausa non attraversa il resolver iniettato")
    esigi(
        sum(e.kind is TurnEventKind.PROCESSING_STARTED for e in eventi) == 2,
        "run e continuazione non annunciano entrambi la preparazione",
    )
    sconosciuto = next(e for e in eventi if e.source_name == "EventoFuturo")
    esigi(sconosciuto.kind is TurnEventKind.OTHER, "un evento futuro viene perso dal core")
    esigi(sconosciuto.content == "conservato", "l'evento futuro perde il proprio payload")
    return "eventi neutri, avvio anticipato e ciclo run/continue indipendente"


def log_cli_puliti() -> str:
    """La CLI normale filtra INFO di Agno; --debug lo riabilita."""
    logger = logging.getLogger("agno")
    snapshot = {
        nome: (
            logging.getLogger(nome).level,
            list(logging.getLogger(nome).handlers),
            [handler.level for handler in logging.getLogger(nome).handlers],
        )
        for nome in AGNO_LOGGER_NAMES
    }
    catturato = io.StringIO()
    handler_prova = logging.StreamHandler(catturato)

    try:
        # Isola l'asserzione dall'handler Rich reale, che altrimenti
        # stamperebbe il warning di prova nel resoconto della smoke suite.
        logger.handlers = [handler_prova]
        logger.setLevel(logging.DEBUG)

        configura_log_agno(False)
        logger.info("Found 0 documents")
        logger.warning("warning-visibile")
        normale = catturato.getvalue()
        esigi("Found 0 documents" not in normale, "un INFO Agno compare nella CLI normale")
        esigi("warning-visibile" in normale, "la pulizia nasconde anche i warning Agno")

        catturato.seek(0)
        catturato.truncate(0)
        configura_log_agno(True)
        logger.debug("debug-visibile")
        logger.info("info-visibile")
        debug = catturato.getvalue()
        esigi("debug-visibile" in debug and "info-visibile" in debug, "--debug filtra i log Agno")
    finally:
        for nome, (livello, handlers, livelli_handler) in snapshot.items():
            logger_originale = logging.getLogger(nome)
            logger_originale.handlers = handlers
            logger_originale.setLevel(livello)
            for handler, livello_handler in zip(handlers, livelli_handler, strict=True):
                handler.setLevel(livello_handler)

    return "INFO interni nascosti, warning preservati e --debug completo"


def cronologia_persistente() -> str:
    """Migrazione, multilinea, concorrenza, permessi e retention."""
    percorso = config.CRONOLOGIA_FILE
    if percorso.exists():
        percorso.unlink()

    # Un clone aggiornato puo' avere ancora il formato GNU Readline: una voce
    # per riga e nessuna intestazione. La prima scrittura lo migra.
    percorso.write_text("prima domanda\n", encoding="utf-8")
    percorso.chmod(0o644)
    prima_chat = CronologiaSicura(percorso, 4)
    seconda_chat = CronologiaSicura(percorso, 4)
    esigi(
        list(prima_chat.load_history_strings()) == ["prima domanda"],
        "la cronologia Readline precedente non viene riletta",
    )

    prima_chat.append_string("seconda domanda")
    # La seconda istanza e' nata prima della scrittura: store_string deve
    # rileggere il disco sotto lock, non sovrascrivere dalla propria cache.
    seconda_chat.append_string("riga di un'altra chat")
    seconda_chat.append_string("domanda su due\nrighe")

    rilette = list(CronologiaSicura(percorso, 4).load_history_strings())
    esigi(
        rilette == ["domanda su due\nrighe", "riga di un'altra chat", "seconda domanda", "prima domanda"],
        "migrazione o intreccio delle chat errato: " + repr(rilette),
    )
    esigi(
        percorso.read_text(encoding="utf-8").splitlines()[0] == CRONOLOGIA_INTESTAZIONE,
        "la cronologia non e' stata migrata al formato multilinea",
    )
    if os.name == "posix":
        esigi(
            oct(percorso.stat().st_mode)[-3:] == "600",
            "cronologia leggibile da altri: " + oct(percorso.stat().st_mode)[-3:],
        )
        esigi(
            oct(prima_chat.lock_file.stat().st_mode)[-3:] == "600",
            "lock della cronologia leggibile da altri",
        )

    limitata = CronologiaSicura(percorso, 2)
    limitata.append_string("quarta domanda")
    ultime = list(CronologiaSicura(percorso, 2).load_history_strings())
    esigi(
        ultime == ["quarta domanda", "domanda su due\nrighe"],
        "retention applicata dalla parte sbagliata: " + repr(ultime),
    )
    return "formato precedente migrato, multilinea e due chat, retention a 2"


def input_repl() -> str:
    """Prompt reale su pipe: completamento, multilinea, segnali e fallback."""
    metadati = [(nome, descrizione) for nome, _alias, descrizione, _funzione in COMANDI]
    percorso = Path(ARCHIVIO_PROVA) / "cronologia_input_test.txt"

    with create_pipe_input() as pipe:
        input_cli = CliInput(
            comandi=metadati,
            cronologia_file=percorso,
            cronologia_righe=20,
            interactive=True,
            input=pipe,
            output=DummyOutput(),
        )
        pipe.send_text("/me\t\r")
        esigi(input_cli.prompt() == "/memorie", "TAB non completa nel prompt reale")

        pipe.send_text("prima riga\x1b\rseconda riga\r")
        esigi(
            input_cli.prompt() == "prima riga\nseconda riga",
            "Alt+Invio non inserisce una nuova riga",
        )

        pipe.send_text("s\r")
        esigi(input_cli.ask("Autorizzi? ") == "s", "il prompt breve non restituisce la scelta")

        pipe.send_text("\x03")
        try:
            input_cli.prompt()
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("Ctrl-C non interrompe il prompt")

        pipe.send_text("\x04")
        try:
            input_cli.prompt()
        except EOFError:
            pass
        else:
            raise AssertionError("Ctrl-D non chiude il prompt")

    rilette = list(CronologiaSicura(percorso, 20).load_history_strings())
    esigi("s" not in rilette, "una risposta di autorizzazione finisce in cronologia")
    esigi(
        rilette[:2] == ["prima riga\nseconda riga", "/memorie"],
        "il prompt non salva le domande: " + repr(rilette),
    )

    risposte = iter(["testo da pipe", "no"])
    etichette = []

    def fallback(etichetta: str) -> str:
        etichette.append(etichetta)
        return next(risposte)

    fallback_cli = CliInput(
        comandi=metadati,
        cronologia_file=Path(ARCHIVIO_PROVA) / "cronologia_fallback_test.txt",
        cronologia_righe=20,
        interactive=False,
        fallback_input=fallback,
    )
    esigi(fallback_cli.prompt() == "testo da pipe", "il fallback non legge il messaggio")
    esigi(fallback_cli.ask("Scelta: ") == "no", "il fallback non legge la scelta")
    esigi(etichette == ["Tu › ", "Scelta: "], "prompt del fallback inattesi: " + repr(etichette))

    ostacolo = Path(ARCHIVIO_PROVA) / "non-e-una-directory"
    ostacolo.write_text("file", encoding="utf-8")
    degradata = CliInput(
        comandi=metadati,
        cronologia_file=ostacolo / "cronologia.txt",
        cronologia_righe=20,
        interactive=False,
        fallback_input=lambda _etichetta: "continua",
    )
    esigi(degradata.history_warning is not None, "il guasto della cronologia non viene annunciato")
    esigi(degradata.prompt() == "continua", "un guasto della cronologia blocca la chat")

    lock_originale = platform_files.portalocker.lock
    try:

        def lock_guasto(_file, _operazione):
            raise platform_files.portalocker.LockException("guasto simulato")

        platform_files.portalocker.lock = lock_guasto
        lock_degradato = CliInput(
            comandi=metadati,
            cronologia_file=Path(ARCHIVIO_PROVA) / "cronologia-lock-guasto.txt",
            cronologia_righe=20,
            interactive=False,
            fallback_input=lambda _etichetta: "ancora disponibile",
        )
    finally:
        platform_files.portalocker.lock = lock_originale
    esigi(lock_degradato.history_warning is not None, "il guasto del lock non viene annunciato")
    esigi(
        lock_degradato.prompt() == "ancora disponibile",
        "un guasto del backend di lock blocca la chat",
    )
    return "menu e TAB, multilinea, Ctrl-C/D, fallback pipe e cronologia in memoria"


def comandi() -> str:
    """Un comando si risolve, un refuso si dichiara, un troncamento non indovina.

    Il difetto che questo controllo esiste per prendere e' il silenzio: prima
    un comando inesistente stampava l'aiuto, cioe' la stessa cosa che stampa
    chi l'aiuto lo ha chiesto, e un refuso sembrava una risposta. L'altro e'
    la deriva: l'elenco a schermo era una stringa scritta a mano e aveva gia'
    perso `/lavoro`.
    """
    nomi = [voce[0] for voce in COMANDI]
    alias = [alias for voce in COMANDI for alias in voce[1]]
    esigi(len(set(nomi + alias)) == len(nomi + alias), "due comandi con lo stesso nome")

    # Un nome scritto per intero non viene mai reinterpretato.
    for nome in nomi:
        voce, righe = risolvi_comando(nome)
        esigi(voce is not None and voce[0] == nome, "il comando " + nome + " non si risolve in se'")
    for scritto in alias:
        voce, _ = risolvi_comando(scritto)
        esigi(voce is not None, "alias non riconosciuto: " + scritto)

    voce, _ = risolvi_comando("/mem")
    esigi(voce is not None and voce[0] == "/memorie", "un troncamento unico non si espande")

    # Ambiguo: si mostrano i candidati. Indovinare fra /entita e /esci
    # chiuderebbe la sessione al posto di leggere un archivio.
    voce, righe = risolvi_comando("/e")
    esigi(voce is None, "un troncamento ambiguo viene eseguito lo stesso")
    esigi(
        "/entita" in righe[0] and "/esci" in righe[0],
        "i candidati ambigui non sono elencati: " + repr(righe),
    )

    voce, righe = risolvi_comando("/fiel")
    esigi(voce is None, "un refuso viene eseguito")
    esigi("/fiel" in righe[0], "il refuso non viene ripetuto a schermo: " + repr(righe))
    esigi("/file" in " ".join(righe), "nessun suggerimento per un refuso vicino")

    voce, righe = risolvi_comando("/pipppo")
    esigi(voce is None, "una parola inventata viene eseguita")
    esigi(
        "/aiuto" in " ".join(righe) and "Forse" not in " ".join(righe),
        "una parola inventata non manda all'aiuto: " + repr(righe),
    )

    # L'elenco a schermo si deriva dalla tabella: se un comando nuovo non
    # compare qui, la stringa e' tornata a mano.
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        stampa_aiuto()
    aiuto = catturato.getvalue()
    for nome in nomi:
        esigi(nome in aiuto, "l'aiuto non nomina " + nome)

    # Il dispatcher: un comando sconosciuto non chiude la sessione, /esci si'.
    # Nessuno dei due tocca l'agente, quindi None basta.
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        vive = gestisci_comando("/pipppo", None, "sessione", "utente")
    esigi(vive is True, "un comando sconosciuto chiude la sessione")
    esigi(catturato.getvalue().strip() != "", "un comando sconosciuto non dice niente")
    with contextlib.redirect_stdout(io.StringIO()):
        esigi(gestisci_comando("/esci", None, "sessione", "utente") is False, "/esci non chiude")
        esigi(gestisci_comando("/qu", None, "sessione", "utente") is False, "/qu non chiude")

    completatore = CompletamentoComandi([(nome, descrizione) for nome, _alias, descrizione, _funzione in COMANDI])

    def completa(testo: str) -> list[str]:
        return [voce.text for voce in completatore.get_completions(Document(testo), CompleteEvent())]

    esigi(completa("/me") == ["/memorie"], "il menu non completa un comando")
    esigi(completa("/") == nomi, "lo slash non elenca tutti i comandi")
    esigi(completa("ricordami /mem") == [], "un comando dentro una frase apre il menu")
    esigi(completa("scrivo 23/08") == [], "una data dentro una frase apre il menu")
    esigi(completa("/sessioni lavoro") == [], "il menu copre l'argomento di un comando")

    return str(len(nomi)) + " comandi dalla tabella, refusi e troncamenti distinti"


def archivio_vero_intatto(prima: list) -> str:
    """La prova non ha letto ne' scritto l'archivio vero."""
    esigi(
        not config.DB_FILE.startswith(str(config.BASE_DIR / "tmp")),
        "l'archivio della prova coincide con quello vero: " + config.DB_FILE,
    )
    esigi(stato_archivio_reale() == prima, "l'archivio vero e' cambiato durante la prova")
    return str(len(prima)) + " file in tmp/, invariati"


# ---------------------------------------------------------------------------
# Esecuzione
# ---------------------------------------------------------------------------


def esegui(prove) -> tuple:
    """Esegue le prove in ordine e stampa una riga per ciascuna."""
    falliti, non_conclusivi = [], []
    for nome, controllo in prove:
        try:
            nota = controllo()
        except Exception as errore:
            print("FALLITO ", nome, "-", type(errore).__name__ + ":", errore)
            falliti.append(nome)
            continue
        if nota.startswith(NON_CONCLUSIVO):
            print("n.c.    ", nome, "-", nota[len(NON_CONCLUSIVO) :])
            non_conclusivi.append(nome)
        else:
            print("ok      ", nome, "-", nota)
    return falliti, non_conclusivi


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica il cablaggio dell'agente senza chiamare il modello")
    parser.add_argument("--user", default="prova", help="Identificativo con cui seminare l'archivio")
    parser.add_argument("--session", default="prova", help="Sessione con cui seminare l'archivio")
    parser.add_argument("--conserva", action="store_true", help="non cancella l'archivio della prova")
    args = parser.parse_args()

    print("Archivio della prova:", config.DB_FILE)
    print("Utente:", args.user, "  Sessione:", args.session)
    print()

    avvio = time.monotonic()
    reale_prima = stato_archivio_reale()

    try:
        agent = build_assistant(user_id=args.user, session_id=args.session)
    except Exception as errore:
        print("FALLITO  costruzione -", type(errore).__name__ + ":", errore)
        return 1
    lm = agent.learning_machine
    fs = build_filesystem(args.user)
    print("ok       costruzione - agente costruito in", round(time.monotonic() - avvio, 2), "s")

    try:
        print("ok       seme        -", semina(lm, fs, args.user, args.session))
    except Exception as errore:
        print("FALLITO  seme        -", type(errore).__name__ + ":", errore)
        return 1

    falliti, non_conclusivi = esegui(
        (
            ("store attivi        ", lambda: store_attivi(lm)),
            ("apprendimento post-run", lambda: apprendimento_post_run(agent)),
            ("retry contesto      ", lambda: retry_contesto(lm)),
            ("namespace coerenti  ", lambda: namespace_coerenti(lm, fs, args.user)),
            ("namespace stabili   ", lambda: namespace_stabili(args.user)),
            ("chiamate locali     ", lambda: chiamate_locali(agent, lm)),
            ("contesto esteso     ", lambda: contesto_esteso(agent, lm)),
            ("ragionamento modelli", lambda: ragionamento_modelli(agent, lm)),
            ("schemi importabili  ", lambda: schemi_importabili(lm)),
            ("lettori tolleranti  ", lambda: lettori_tolleranti(args.user)),
            ("identita            ", lambda: identita(agent)),
            ("strumenti           ", lambda: strumenti(agent, args.user)),
            ("spazio di lavoro    ", lambda: spazio_di_lavoro(agent, args.user)),
            ("tempo               ", lambda: tempo(agent, lm, args.user)),
            ("profilo rileggibile ", lambda: profilo_rileggibile(lm, args.user)),
            ("memorie rileggibili ", lambda: memorie_rileggibili(lm, args.user)),
            ("contesto rileggibile", lambda: contesto_rileggibile(lm, args.session)),
            ("entita complete     ", lambda: entita_complete(lm, args.user)),
            ("fatti leggibili     ", lambda: fatti_leggibili(lm, args.user)),
            ("entita cercate      ", lambda: entita_cercate(agent, args.user)),
            ("sessioni elencate   ", lambda: sessioni_elencate(agent, args.user, args.session)),
            ("file isolati        ", lambda: file_isolati(args.user)),
            ("indice vettoriale   ", lambda: indice_vettoriale(lm)),
            ("archivio privato    ", lambda: archivio_privato()),
            ("import senza effetti", lambda: import_senza_effetti()),
            ("conferme leggibili  ", lambda: conferme_leggibili()),
            ("metriche del turno  ", lambda: metriche_del_turno()),
            ("esito strumenti     ", lambda: esito_strumenti()),
            ("renderer Rich       ", lambda: renderer_rich()),
            ("renderer TTY        ", lambda: renderer_tty_markdown_sicuro()),
            ("indicatore attivita ", lambda: indicatore_attivita()),
            ("core del turno      ", lambda: core_del_turno()),
            ("log CLI             ", lambda: log_cli_puliti()),
            ("cronologia          ", lambda: cronologia_persistente()),
            ("input REPL          ", lambda: input_repl()),
            ("comandi             ", lambda: comandi()),
            ("archivio vero intatto", lambda: archivio_vero_intatto(reale_prima)),
        )
    )

    print()
    print("Concluso in", round(time.monotonic() - avvio, 2), "s")
    # Conservato anche quando fallisce, non solo su richiesta: e' il caso in
    # cui serve guardarci dentro, ed e' l'unico momento in cui la prova lo
    # cancellava.
    if args.conserva or falliti:
        print("Archivio della prova conservato:", ARCHIVIO_PROVA)
    else:
        shutil.rmtree(ARCHIVIO_PROVA, ignore_errors=True)
        shutil.rmtree(SPAZIO_PROVA, ignore_errors=True)
    if non_conclusivi:
        print()
        print("Non concludenti:")
        for nome in non_conclusivi:
            print("   ", nome.strip())
    if falliti:
        print()
        print("FALLITE:", ", ".join(nome.strip() for nome in falliti))
        return 1
    print("Nessun fallimento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
