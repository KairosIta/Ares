"""
Smoke test dell'agente
======================
Uso:
    .venv/bin/python tests/smoke_test.py
    .venv/bin/python tests/smoke_test.py --user prova --session lavoro

Costruisce l'agente, si scrive da solo i dati che gli servono e controlla che
tornino indietro. Non chiede niente al modello: nessun peso entra in VRAM.
Cio' che della REPL si prova senza costruire l'agente - conferme, rendering,
editor, comandi - sta in `repl_test.py`: era qui, e questo file era
diventato il posto dove finiva ogni prova offline.

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

Il preflight (`ops/preflight.py`) risponde a una domanda diversa: se avvio adesso,
il server e i modelli ci sono? Qui il server non serve. E `e2e_test.py` a una
terza: l'agente risponde e impara? Quella costa un turno vero.
"""

import argparse
import contextlib
import importlib
import io
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from _comune import NON_CONCLUSIVO, esegui, esigi, prepara_ambiente

# I percorsi vanno scelti prima di importare config, che crea TMP_DIR
# all'import; e `build_workspace` crea la directory di lavoro, che senza
# questa riga comparirebbe accanto al progetto.
RADICE_PROVA = prepara_ambiente("smoke")
ARCHIVIO_PROVA = str(RADICE_PROVA / "stato")
SPAZIO_PROVA = str(RADICE_PROVA / "lavoro")

# Normalizzatore privato di Agno, importato di proposito invece di
# riscritto: una copia locale verificherebbe la copia, non il
# comportamento del FileSystem su cui i namespace finiscono davvero.
from agno.fs._paths import normalize_namespace  # noqa: E402
from agno.tools.workspace import Workspace  # noqa: E402

from ares import config  # noqa: E402
from ares.agent.assistant import (  # noqa: E402
    AresLearningMachine,
    AresSessionContextStore,
    apprendi_a_run_completato,
    build_assistant,
    build_db,
    build_filesystem,
    build_workspace,
)
from ares.agent.echo import Fotografia, Istantanea, fotografa, istantanea, riduci, ripristina, variazioni  # noqa: E402
from ares.agent.schemas import AresProfile  # noqa: E402
from ares.cli.chat import (  # noqa: E402
    gestisci_comando,
)
from ares.state.stores import (  # noqa: E402
    leggi_entita,
    leggi_intuizioni,
    leggi_sessioni,
    namespace_entita,
    namespace_utente,
    righe_entita,
    righe_sessione,
    stampa_store,
)

# Utente che non esiste in nessun archivio: serve solo a controllare che i
# file di chi esiste non gli si vedano. Non viene mai scritto.
UTENTE_DI_CONTROLLO = "utente-di-controllo"

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

    from ares.agent import learning as modulo_apprendimento

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
    log_originale = modulo_apprendimento.log_warning
    avvisi = []
    modulo_apprendimento.log_warning = avvisi.append
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
        modulo_apprendimento.log_warning = log_originale

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
    """Niente esce dalla macchina, salvo il modello conversazionale se e' cloud.

    Se OLLAMA_API_KEY e' nell'ambiente e host non e' impostato, Agno manda
    le conversazioni a https://ollama.com. Qui si controlla che ogni
    modello e l'embedder abbiano l'host esplicito, e che quell'host sia
    locale: un progetto che promette che niente esce non puo' dipendere da
    una variabile d'ambiente per mantenere la promessa.

    Un modello cloud di Ollama passa comunque dal daemon locale, quindi
    l'host non lo distingue: lo distingue il nome. L'unico ruolo a cui e'
    concesso e' l'agente; gli store di apprendimento e l'embedder ricevono
    profilo, memorie e intuizioni e devono restare locali per nome.
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
        if nome != "agente":
            esigi(
                not config.e_modello_cloud(componente.id),
                nome + " usa un modello cloud: " + componente.id,
            )
    esigi(
        "localhost" in config.OLLAMA_HOST or "127.0.0.1" in config.OLLAMA_HOST,
        "config.OLLAMA_HOST non e' locale: " + config.OLLAMA_HOST,
    )
    # La chiave non serve: e' il daemon, dopo `ollama signin`, a inoltrare i
    # modelli cloud. Nell'ambiente di Ares farebbe solo aggiungere un header
    # a ogni chiamata locale, e resta il segno di una configurazione che
    # questo progetto non vuole.
    esigi("OLLAMA_API_KEY" not in os.environ, "OLLAMA_API_KEY e' nell'ambiente: non serve e non deve esserci")
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
    esito = str(len(componenti)) + " componenti su " + config.OLLAMA_HOST + ", telemetria spenta"
    if config.e_modello_cloud(agent.model.id):
        esito += ", agente cloud"
    return esito


def ruoli_locali() -> str:
    """Il confine fra locale e cloud e' nel nome, e il codice lo fa rispettare.

    Ollama scrive il tag cloud in due forme e le usa entrambe; un nome di
    repository che contiene "cloud" non basta. E un modello cloud dato a
    estrazione o embedding deve fermare la costruzione, non un turno.
    """
    from ares.agent import runtime

    for nome in ("glm-5.3-flash:cloud", "gpt-oss:120b-cloud"):
        esigi(config.e_modello_cloud(nome), nome + " non e' riconosciuto come cloud")
    for nome in ("qwen3.5:latest", "hf.co/cloud-lab/modello:Q8_0", "nomic-embed-text-v2-moe", "cloud"):
        esigi(not config.e_modello_cloud(nome), nome + " e' scambiato per cloud")

    for attributo, costruttore in (
        ("LEARNING_MODEL", runtime.build_learning_model),
        ("EMBEDDER_MODEL", runtime.build_knowledge),
    ):
        with patch.object(config, attributo, "glm-5.3-flash:cloud"):
            try:
                costruttore()
            except ValueError as errore:
                esigi(attributo in str(errore), "l'errore non nomina " + attributo + ": " + str(errore))
            else:
                raise AssertionError(attributo + " cloud non ha fermato la costruzione")
    return "due forme di tag riconosciute, estrazione ed embedding rifiutano il cloud"


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
    # Stesso modello nei due ruoli, stesso num_ctx: altrimenti Ollama riavvia
    # il runner a ogni passaggio fra risposta ed estrazione e perde la cache
    # del prompt. Con modelli diversi l'estrazione puo' stare sotto NUM_CTX,
    # mai sopra: il tetto lo decide comunque il modello conversazionale.
    if config.MAIN_MODEL == config.LEARNING_MODEL:
        esigi(len(set(valori)) == 1, "stesso modello con num_ctx diversi: " + str(sorted(set(valori))))
    esigi(max(valori) == config.NUM_CTX, "un num_ctx supera NUM_CTX: " + str(sorted(set(valori))))
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
    # Come initialize_agent() all'inizio di un run: risolve il ResultStore e
    # rende disponibili read_result/search_result prima di comporre i tool.
    _ = agent.result_store
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
    if config.OFFLOAD_TOOL_RESULTS:
        attesi["read_result"] = "OFFLOAD_TOOL_RESULTS"
        attesi["search_result"] = "OFFLOAD_TOOL_RESULTS"
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


def protezione_contesto(agent, user_id: str) -> str:
    """I risultati grandi sono lossless, locali e non gonfiano il prompt."""
    esigi(
        agent.max_tool_calls_from_history == config.MAX_TOOL_CALLS_FROM_HISTORY,
        "il limite delle tool call storiche non arriva all'agente",
    )
    if not config.OFFLOAD_TOOL_RESULTS:
        esigi(agent.result_store is None, "l'offloading e' spento ma il ResultStore esiste")
        return NON_CONCLUSIVO + "OFFLOAD_TOOL_RESULTS e' spento in config.py"

    store = agent.result_store
    esigi(store is not None, "OFFLOAD_TOOL_RESULTS e' acceso ma il ResultStore manca")
    esigi(
        store.threshold_chars == config.TOOL_RESULT_THRESHOLD_CHARS,
        "soglia offload diversa dalla configurazione",
    )
    esigi(
        Path(str(store.fs.backend.db_engine.url.database)).resolve() == Path(config.FS_DB_FILE).resolve(),
        "i payload non usano filesystem.db gia' incluso nei backup",
    )

    payload = "\n".join("riga " + str(numero) + " " + "x" * 80 for numero in range(300))
    esigi(len(payload) > config.TOOL_RESULT_THRESHOLD_CHARS, "il payload di prova non supera la soglia")
    envelope = store.offload_for_model(
        session_id="prova-offload",
        run_id="run-offload",
        tool_call_id="call-offload",
        tool_name=config.WORKSPACE_PREFIX + "read_file",
        tool_args={"file_path": "grande.txt"},
        output=payload,
        user_id=user_id,
    )
    riferimenti = store.live_ids("prova-offload")
    esigi(len(riferimenti) == 1, "il risultato non e' indicizzato una volta sola")
    esigi(store.payload(riferimenti[0].result_id) == payload, "il payload riletto non e' lossless")
    esigi(riferimenti[0].result_id in envelope, "l'anteprima non punta al risultato salvato")
    esigi(len(envelope) < len(payload) // 4, "l'anteprima resta troppo grande per proteggere il contesto")
    return (
        str(len(payload))
        + " caratteri conservati lossless, envelope di "
        + str(len(envelope))
        + ", ultime "
        + str(config.MAX_TOOL_CALLS_FROM_HISTORY)
        + " tool call nel prompt"
    )


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
    oggi = datetime.now(UTC).strftime("%Y-%m-%d")
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


def eco_apprendimenti(agent, lm, user_id: str) -> str:
    """La fotografia legge cio' che e' in archivio e la differenza dice cosa e' cambiato.

    Due meta'. La prima usa l'archivio seminato: la fotografia deve
    contenere esattamente i campi e le memorie del seme, e una memoria
    aggiunta con la stessa API che usa l'estrazione deve comparire come
    nuova, con il suo testo intero. La seconda e' la differenza da sola, su
    fotografie costruite a mano, perche' i rami che contano - una memoria
    riscritta, una tolta, un campo del profilo svuotato, niente da dire -
    non si producono seminando.
    """
    if "user_profile" not in lm.stores or "user_memory" not in lm.stores:
        return NON_CONCLUSIVO + "profilo o memorie spenti in config: la fotografia non ha niente da leggere"

    prima = fotografa(agent)
    esigi(
        prima.profilo == PROFILO_SEMINATO,
        "la fotografia del profilo non e' il seme: " + repr(prima.profilo),
    )
    esigi(
        sorted(prima.memorie.values()) == sorted(MEMORIE_SEMINATE),
        "la fotografia delle memorie non e' il seme: " + repr(prima.memorie),
    )
    esigi(variazioni(prima, fotografa(agent)) == [], "due fotografie uguali producono righe")

    # Una scrittura vera in entrambi gli store, poi il ripristino dall'istantanea
    # letta prima: e' il percorso del "no" alla domanda "Tenere in memoria?",
    # e le prove dopo questa contano le memorie seminate.
    stato = istantanea(agent)
    esigi(riduci(stato) == prima, "l'istantanea ridotta non e' la fotografia")
    lm.user_memory_store.add_memory(user_id=user_id, memory="Memoria  aggiunta\ndall'eco.")
    profilo_toccato = lm.user_profile_store.get(user_id=user_id)
    profilo_toccato.occupation = "collaudatore dell'eco"
    lm.user_profile_store.save(user_id=user_id, profile=profilo_toccato)
    try:
        righe = variazioni(prima, fotografa(agent))
    finally:
        tornato = ripristina(agent, stato)
    esigi(righe[:1] == ["   appreso: profilo 1 campo, memorie +1"], "la sintesi e' " + repr(righe[:1]))
    esigi("   | + Memoria aggiunta dall'eco." in righe, "la memoria nuova non e' resa intera: " + repr(righe[1:]))
    esigi(tornato, "il ripristino non si dichiara riuscito")
    esigi(variazioni(prima, fotografa(agent)) == [], "l'archivio non e' tornato com'era dopo il ripristino")

    # Un utente che prima non aveva niente: il ripristino cancella le righe
    # che il turno ha creato, invece di riscriverle vuote.
    class AgenteNuovo:
        learning_machine = lm
        user_id = "eco-nuovo"
        id = None

    nuovo = AgenteNuovo()
    esigi(istantanea(nuovo) == Istantanea(), "un utente mai visto non ha un'istantanea vuota")
    lm.user_memory_store.add_memory(user_id=nuovo.user_id, memory="Non deve restare.")
    lm.user_profile_store.save(user_id=nuovo.user_id, profile=AresProfile(user_id=nuovo.user_id, name="Effimero"))
    esigi(fotografa(nuovo) != Fotografia(), "le scritture per l'utente nuovo non si vedono")
    esigi(ripristina(nuovo, Istantanea()), "il ripristino a vuoto non si dichiara riuscito")
    esigi(lm.user_memory_store.get(user_id=nuovo.user_id) is None, "le memorie dell'utente nuovo non sono cancellate")
    esigi(lm.user_profile_store.get(user_id=nuovo.user_id) is None, "il profilo dell'utente nuovo non e' cancellato")

    # Un agente senza macchina di apprendimento - `object()` nelle prove
    # della CLI - deve dare una fotografia vuota, non un AttributeError.
    esigi(fotografa(object()) == Fotografia(), "un agente senza apprendimento non da' una fotografia vuota")

    vecchia = Fotografia(
        profilo={"name": "Prova", "occupation": "collaudo", "timezone": "Europe/Rome"},
        memorie={"a": "resta", "b": "viene riscritta", "c": "viene tolta"},
    )
    nuova = Fotografia(
        profilo={"name": "Prova", "occupation": "collaudatore", "language": "it"},
        memorie={"a": "resta", "b": "riscritta", "d": "nuova"},
    )
    righe = variazioni(vecchia, nuova)
    esigi(righe[0] == "   appreso: profilo 3 campi, memorie +1 ~1 -1", "la sintesi e' " + repr(righe[0]))
    attese = [
        "   | profilo language: it",
        "   | profilo occupation: collaudatore",
        "   | profilo timezone: (tolto)",
        "   | ~ riscritta",
        "   | + nuova",
        "   | - viene tolta",
    ]
    esigi(righe[1:] == attese, "le righe della differenza sono " + repr(righe[1:]))
    esigi("resta" not in " ".join(righe), "una memoria invariata compare fra le variazioni")
    solo_profilo = variazioni(Fotografia(), Fotografia(profilo={"name": "Prova"}))
    esigi(solo_profilo[0] == "   appreso: profilo 1 campo", "un campo solo e' al plurale: " + repr(solo_profilo[0]))
    return "seme letto, scritture vere viste e ripristinate, e i sei rami della differenza"


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
        sessione = _sessione_finta(nome, creata, domanda, user_id)
        agent.db.upsert_session(sessione)
        agent.db.upsert_run(sessione.runs[0], session_id=nome, user_id=user_id, run_index=0)
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
    leggere una costante produceva un effetto: `preflight` importava
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
        "import os; from ares import config;"
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
            ("ruoli locali        ", ruoli_locali),
            ("contesto esteso     ", lambda: contesto_esteso(agent, lm)),
            ("ragionamento modelli", lambda: ragionamento_modelli(agent, lm)),
            ("schemi importabili  ", lambda: schemi_importabili(lm)),
            ("lettori tolleranti  ", lambda: lettori_tolleranti(args.user)),
            ("identita            ", lambda: identita(agent)),
            ("strumenti           ", lambda: strumenti(agent, args.user)),
            ("protezione contesto ", lambda: protezione_contesto(agent, args.user)),
            ("spazio di lavoro    ", lambda: spazio_di_lavoro(agent, args.user)),
            ("tempo               ", lambda: tempo(agent, lm, args.user)),
            ("profilo rileggibile ", lambda: profilo_rileggibile(lm, args.user)),
            ("memorie rileggibili ", lambda: memorie_rileggibili(lm, args.user)),
            ("contesto rileggibile", lambda: contesto_rileggibile(lm, args.session)),
            ("eco apprendimenti   ", lambda: eco_apprendimenti(agent, lm, args.user)),
            ("entita complete     ", lambda: entita_complete(lm, args.user)),
            ("fatti leggibili     ", lambda: fatti_leggibili(lm, args.user)),
            ("entita cercate      ", lambda: entita_cercate(agent, args.user)),
            ("sessioni elencate   ", lambda: sessioni_elencate(agent, args.user, args.session)),
            ("file isolati        ", lambda: file_isolati(args.user)),
            ("indice vettoriale   ", lambda: indice_vettoriale(lm)),
            ("archivio privato    ", lambda: archivio_privato()),
            ("import senza effetti", lambda: import_senza_effetti()),
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
        shutil.rmtree(RADICE_PROVA, ignore_errors=True)
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
