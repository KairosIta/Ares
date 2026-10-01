"""Smoke test dell'agente
======================
Uso:
    .venv/bin/python tests/smoke_test.py
    .venv/bin/python tests/smoke_test.py --user prova --session lavoro

Costruisce l'agente, semina da se' i dati che servono in una directory
temporanea e controlla che tornino indietro. Non chiama il modello: nessun
peso entra in VRAM. Vale su qualunque macchina, anche appena clonata, e non
legge ne' scrive l'archivio vero (l'ultimo controllo lo dimostra; per
guardarci dentro c'e' `inspect_learning.py`).

Il seme tocca solo gli store su SQLite: `learned_knowledge` chiamerebbe
l'embedder.

Ogni prova riporta uno di tre esiti:

    ok        il controllo e' passato
    n.c.      non concludente: non c'e' abbastanza per dimostrare qualcosa
    FALLITO   il controllo non e' passato

Solo un FALLITO cambia il codice di uscita.

Altre prove: `repl_test.py` per cio' che della REPL non richiede l'agente,
`ops/preflight.py` per server e modelli, `e2e_test.py` per un turno vero.
"""

import argparse
import contextlib
import importlib
import io
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch
from urllib.parse import urlsplit

from _comune import NON_CONCLUSIVO, esegui, esigi, prepara_ambiente, pulisci

# I percorsi vanno scelti prima di importare config, che li legge quando
# `leggi_percorsi` viene chiamata; e `build_workspace` apre la directory di
# lavoro, che senza questa riga sarebbe quella del clone.
RADICE_PROVA = prepara_ambiente("smoke")
ARCHIVIO_PROVA = str(RADICE_PROVA / "stato")
SPAZIO_PROVA = str(RADICE_PROVA / "lavoro")

# Normalizzatore privato di Agno, importato di proposito invece di
# riscritto: una copia locale verificherebbe la copia, non il
# comportamento del FileSystem su cui i namespace finiscono davvero.
from agno.fs._paths import normalize_namespace  # noqa: E402
from agno.tools.workspace import Workspace  # noqa: E402

from ares import config  # noqa: E402

# Percorsi, impostazioni e politica letti una volta dopo `prepara_ambiente`.
# Dove la prova cambia un flag con `patch.object` la politica si rilegge dentro
# il `with`.
PERCORSI = config.leggi_percorsi()
IMPOSTAZIONI = config.leggi_impostazioni()
POLITICA = config.leggi_politica()
from ares.agent.assistant import (  # noqa: E402
    AresLearningMachine,
    AresSessionContextStore,
    apprendi_a_run_completato,
    build_assistant,
    build_db,
    build_filesystem,
    build_quaderno,
    build_workspace,
)
from ares.agent.echo import Fotografia, Istantanea, fotografa, istantanea, riduci, ripristina, variazioni  # noqa: E402
from ares.agent.prompts import descrizione_del_comando, strumenti_spazio  # noqa: E402
from ares.agent.schemas import AresMemories, AresProfile  # noqa: E402
from ares.cli.commands import StatoChat, gestisci_comando  # noqa: E402
from ares.cli.conversazioni import righe_sessione  # noqa: E402
from ares.cli.ui import stampa_store  # noqa: E402
from ares.state.identita import Utente  # noqa: E402
from ares.state.sessioni import elenca, riferimento, tronca  # noqa: E402
from ares.state.stores import (  # noqa: E402
    leggi_entita,
    leggi_intuizioni,
    namespace_entita,
    namespace_utente,
    righe_entita,
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

    La prova scrive per mestiere: va dimostrato che non scriva li'.
    """
    reale = PERCORSI.home / "stato"
    if not reale.exists():
        return []
    return sorted(
        (str(percorso.relative_to(reale)), percorso.stat().st_size, percorso.stat().st_mtime_ns)
        for percorso in reale.rglob("*")
        if percorso.is_file()
    )


def conta_apprendimenti(learning_type: str, namespace: str) -> int:
    """Righe di un tipo di apprendimento in un namespace, lette da SQLite.

    Non passa dagli store: un controllo con lo stesso percorso di lettura del
    difetto non potrebbe rilevarlo.
    """
    with contextlib.closing(sqlite3.connect(PERCORSI.db_file)) as connessione:
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

    Sono le API che l'agente usa con i suoi strumenti, chiamate direttamente,
    senza modello. Le entita' non ricevono il namespace apposta: finiscono dove
    lo store decide, e il riconteggio prova la configurazione.
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

    Una macchina minimale conta le invocazioni del percorso di base usato dal
    post-hook: quella dell'agente accenderebbe il modello.
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

    # Le due meta' della guardia: un run senza messaggi e un post-hook senza
    # agente non estraggono (passerebbero comunque dal modello).
    prima = len(chiamate)
    apprendi_a_run_completato(
        run_output=SimpleNamespace(messages=[], user_id="prova", session_id="prova"),
        agent=agente_finto,
        session=sessione_finta,
        user_id="prova",
        run_context=contesto_finto,
    )
    apprendi_a_run_completato(
        run_output=output_finto,
        agent=None,
        session=sessione_finta,
        user_id="prova",
        run_context=contesto_finto,
    )
    esigi(len(chiamate) == prima, "il post-hook ha estratto da un run vuoto o senza agente")
    return (
        "callback anticipato spento, un post-hook sui "
        + str(len(messaggi))
        + " messaggi completi, niente da un run vuoto o senza agente"
    )


def retry_contesto(lm) -> str:
    """Un fallimento senza tool ritenta una volta, un successo mai."""
    import asyncio

    from ares.agent import learning as modulo_apprendimento

    class StoreFinto(AresSessionContextStore):
        def __init__(self, esiti):
            self.esiti = iter(esiti)
            self.chiamate = 0
            self.context_updated = False
            # La finta non attraversa `super().__init__`: prende il tetto dei
            # tentativi dalla politica come il costruttore vero, cosi' il ciclo
            # prova il valore configurato.
            self.tentativi_contesto = config.leggi_politica().apprendimento.tentativi_contesto

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

    if POLITICA.apprendimento.contesto:
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

    Profilo, memorie e contesto non hanno namespace: sono per `user_id`.
    """
    utente = namespace_utente(Utente.da_grezzo(user_id))
    entita = namespace_entita(Utente.da_grezzo(user_id))
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

    Con i due punti `user:demo` diventerebbe `user%3ademo` solo nei file, e le
    due meta' dell'archivio si separerebbero senza errori.
    """
    for costruito in (namespace_utente(Utente.da_grezzo(user_id)), namespace_entita(Utente.da_grezzo(user_id))):
        normalizzato = normalize_namespace(costruito)
        esigi(
            normalizzato == costruito,
            "il FileSystem riscriverebbe " + repr(costruito) + " come " + repr(normalizzato),
        )
    return "invariati sotto normalize_namespace"


def chiamate_locali(agent, lm) -> str:
    """Niente esce dalla macchina, salvo i modelli cloud scelti nel `.env`.

    Con OLLAMA_API_KEY nell'ambiente e senza host esplicito Agno parlerebbe con
    https://ollama.com: ogni modello e l'embedder devono avere un host
    esplicito e locale. Un modello cloud passa comunque dal daemon locale, e lo
    distingue il nome: conversazione ed estrazione lo accettano, l'embedder no.
    Tutti gli store devono usare LEARNING_MODEL.
    """
    componenti = [("agente", agent.model)]
    for nome, store in lm.stores.items():
        modello = getattr(store.config, "model", None)
        if modello is not None:
            componenti.append((nome, modello))
    if lm.knowledge is not None:
        componenti.append(("embedder", lm.knowledge.vector_db.embedder))

    # L'agente e' stato costruito con queste impostazioni: se i modelli non
    # sono quelli, il confine del processo non e' arrivato fino in fondo.
    esigi(
        agent.model.id == IMPOSTAZIONI.principale,
        "l'agente usa " + str(agent.model.id) + " invece di " + IMPOSTAZIONI.principale,
    )
    esigi(agent.model.host == IMPOSTAZIONI.host, "l'agente non usa l'host delle impostazioni")
    for nome, componente in componenti:
        host = getattr(componente, "host", None)
        esigi(
            host == IMPOSTAZIONI.host,
            nome + " ha host " + repr(host) + " invece di " + repr(IMPOSTAZIONI.host),
        )
        if nome == "embedder":
            esigi(
                not config.e_modello_cloud(componente.id),
                nome + " usa un modello cloud: " + componente.id,
            )
        elif nome != "agente":
            esigi(
                componente.id == IMPOSTAZIONI.apprendimento,
                nome + " usa " + componente.id + " invece di " + IMPOSTAZIONI.apprendimento,
            )
    # Il nome dell'host e non una sottostringa: `127.0.0.1.example.com`
    # conterrebbe "127.0.0.1" senza essere locale, e questa prova deve
    # accorgersene invece di lasciar passare un daemon remoto.
    esigi(
        urlsplit(IMPOSTAZIONI.host).hostname in ("localhost", "127.0.0.1", "::1"),
        "l'host della conversazione non e' locale: " + IMPOSTAZIONI.host,
    )
    # La chiave non serve: dopo `ollama signin` e' il daemon a inoltrare i
    # modelli cloud.
    esigi("OLLAMA_API_KEY" not in os.environ, "OLLAMA_API_KEY e' nell'ambiente: non serve e non deve esserci")
    # L'host giusto non basta: Agno manda un evento di telemetria a
    # os-api.agno.com alla fine di ogni run, e il default e' acceso.
    esigi(agent.telemetry is False, "la telemetria di Agno e' attiva: ogni turno esce dalla macchina")
    # `telemetry=False` non basta: prima di ogni invio Agno rilegge
    # AGNO_TELEMETRY dall'ambiente e sovrascrive il valore.
    esigi(
        os.environ.get("AGNO_TELEMETRY", "").lower() != "true",
        "AGNO_TELEMETRY=true nell'ambiente riaccende la telemetria nonostante telemetry=False",
    )
    esito = str(len(componenti)) + " componenti su " + IMPOSTAZIONI.host + ", telemetria spenta"
    if config.e_modello_cloud(agent.model.id):
        esito += ", agente cloud"
    if config.e_modello_cloud(IMPOSTAZIONI.apprendimento):
        esito += ", estrazione cloud"
    return esito


def ruoli_locali() -> str:
    """Il confine fra locale e cloud e' nel nome, e il codice lo fa rispettare.

    Ollama scrive il tag cloud in due forme; un repository che contiene "cloud"
    non basta. Un modello cloud per l'embedder ferma la costruzione; per
    l'estrazione passa, con un avviso che dice che le memorie escono.
    """
    from ares.agent import runtime

    for nome in ("glm-5.3-flash:cloud", "gpt-oss:120b-cloud"):
        esigi(config.e_modello_cloud(nome), nome + " non e' riconosciuto come cloud")
    for nome in ("qwen3.5:latest", "hf.co/cloud-lab/modello:Q8_0", "nomic-embed-text-v2-moe", "cloud"):
        esigi(not config.e_modello_cloud(nome), nome + " e' scambiato per cloud")

    with patch.object(config, "EMBEDDER_MODEL", "glm-5.3-flash:cloud"):
        try:
            runtime.build_knowledge(PERCORSI, config.leggi_impostazioni())
        except ValueError as errore:
            esigi("EMBEDDER_MODEL" in str(errore), "l'errore non nomina EMBEDDER_MODEL: " + str(errore))
        else:
            raise AssertionError("EMBEDDER_MODEL cloud non ha fermato la costruzione")

    with patch.object(config, "LEARNING_MODEL", "glm-5.3-flash:cloud"):
        modello = runtime.build_learning_model(config.leggi_impostazioni())
        esigi(modello.id == "glm-5.3-flash:cloud", "LEARNING_MODEL cloud non e' stato costruito com'e'")
        esigi(modello.host == config.OLLAMA_HOST, "LEARNING_MODEL cloud non passa dal daemon locale")
        avviso = " ".join(config.leggi_impostazioni().avviso_cloud())
        esigi("memorie" in avviso and "escono dalla macchina" in avviso, "l'avviso non dice che le memorie escono")
        with patch.object(config, "MAIN_MODEL", "glm-5.3-flash:cloud"):
            avviso = " ".join(config.leggi_impostazioni().avviso_cloud())
            esigi("Solo l'embedding resta locale" in avviso, "con entrambi i ruoli cloud non resta solo l'embedding")
    with patch.object(config, "MAIN_MODEL", "qwen3:9b"), patch.object(config, "LEARNING_MODEL", "qwen3:9b"):
        esigi(config.leggi_impostazioni().avviso_cloud() == [], "l'avviso compare con soli modelli locali")
    return "due forme di tag riconosciute, l'embedder rifiuta il cloud, l'estrazione lo accetta e lo dice"


def contesto_esteso(agent, lm) -> str:
    """Ogni modello ha num_ctx esplicito e sopra il default di Ollama.

    Ollama tronca a 4096 token senza dirlo: l'agente sembrerebbe dimenticare.
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
    # il runner fra risposta ed estrazione. Con modelli diversi l'estrazione
    # puo' stare sotto NUM_CTX, mai sopra.
    if IMPOSTAZIONI.principale == IMPOSTAZIONI.apprendimento:
        esigi(len(set(valori)) == 1, "stesso modello con num_ctx diversi: " + str(sorted(set(valori))))
    esigi(max(valori) == IMPOSTAZIONI.num_ctx, "un num_ctx supera NUM_CTX: " + str(sorted(set(valori))))
    return "num_ctx da " + str(min(valori)) + " a " + str(max(valori)) + " su " + str(len(modelli)) + " modelli"


def ragionamento_modelli(agent, lm) -> str:
    """Il pensiero resta acceso in chat e spento nelle estrazioni.

    `think` e' un parametro top-level di Ollama: se finisse nelle options o
    sparisse, ogni store pagherebbe un blocco di ragionamento.
    """
    chat_params = getattr(agent.model, "request_params", None) or {}
    esigi(
        chat_params.get("think") is IMPOSTAZIONI.think,
        "l'agente passa think=" + repr(chat_params.get("think")) + " invece di " + repr(IMPOSTAZIONI.think),
    )

    estrattori = []
    for nome, store in lm.stores.items():
        modello = getattr(store.config, "model", None)
        if modello is None:
            continue
        params = getattr(modello, "request_params", None) or {}
        esigi(
            params.get("think") is IMPOSTAZIONI.think_apprendimento,
            nome + " passa think=" + repr(params.get("think")) + " invece di " + repr(IMPOSTAZIONI.think_apprendimento),
        )
        estrattori.append(nome)

    esigi(estrattori, "nessun estrattore disponibile per verificare think")
    return (
        "chat="
        + str(IMPOSTAZIONI.think)
        + ", "
        + str(len(estrattori))
        + " estrattori="
        + str(IMPOSTAZIONI.think_apprendimento)
    )


def schemi_importabili(lm) -> str:
    """Gli schemi custom vivono in un modulo importabile, non in __main__.

    Agno li serializza per percorso di import.
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

    Il controllo deve valere per qualunque combinazione di flag.
    """

    stores: ClassVar[dict] = {}
    entity_memory_store = None
    learned_knowledge_store = None


def identita(agent) -> str:
    """L'agente ha un nome e il modello lo sa, detto una volta sola.

    Il nome arriva nella descrizione, in italiano. `add_name_to_context` resta
    spento: acceso, Agno lo ripeterebbe in inglese in coda. Si guarda il
    system message costruito davvero, non gli attributi.
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
    esigi("Sei " + agent.name + "," in agent.description, "la descrizione non dice il nome " + repr(agent.name))
    esigi("Your name is" not in prompt, "il nome e' ripetuto in inglese: add_name_to_context e' acceso")
    return agent.name + " si presenta in " + str(len(prompt)) + " caratteri di system message"


def ambiente_nel_prompt(agent, user_id: str, session_id: str) -> str:
    """Il modello sa quali modelli e', quanto contesto ha, su che sistema gira e chi ha davanti.

    Ogni valore della scheda deve arrivare dalle impostazioni e dal sistema, e
    descrizione e scheda devono cambiare con un modello cloud: "nessuna
    conversazione esce di qui" deve comparire solo quando e' vero.
    """
    import platform

    from ares.agent import prompts

    scheda = next((t for t in agent.instructions if t.startswith("<ambiente>")), "")
    avvio = agent.instructions[-1]
    esigi(avvio.startswith("<questo_avvio>"), "la sezione dell'avvio non e' l'ultima istruzione")
    for valore, nome in (
        (IMPOSTAZIONI.principale, "MAIN_MODEL"),
        (IMPOSTAZIONI.embedder, "EMBEDDER_MODEL"),
        (str(IMPOSTAZIONI.num_ctx), "NUM_CTX"),
        (str(config.NUM_HISTORY_RUNS), "NUM_HISTORY_RUNS"),
        (platform.system(), "il sistema"),
    ):
        esigi(valore in scheda, "la scheda non dice " + nome + ": " + repr(valore))
    for valore, nome in ((user_id, "l'utente"), (session_id, "la sessione")):
        esigi(valore in avvio, "l'avvio non dice " + nome + ": " + repr(valore))
    if IMPOSTAZIONI.apprendimento == IMPOSTAZIONI.principale:
        esigi("lo stesso modello" in scheda, "con un modello solo la scheda non lo dice")
    else:
        esigi(IMPOSTAZIONI.apprendimento in scheda, "la scheda non dice LEARNING_MODEL")
    if POLITICA.workspace.attivo:
        esigi(str(PERCORSI.lavoro.resolve()) in avvio, "l'avvio non dice la cartella di lavoro")

    # Locale e cloud, a prescindere dal `.env` di questa macchina.
    with patch.object(config, "MAIN_MODEL", "qwen3:9b"), patch.object(config, "LEARNING_MODEL", "qwen3:9b"):
        impostazioni_locali = config.leggi_impostazioni()
        locale = prompts.descrizione(impostazioni_locali, POLITICA)
        scheda_locale = prompts.istruzioni_sull_ambiente(impostazioni=impostazioni_locali, politica=POLITICA)[0]
    esigi("esce di qui" in locale and "ollama.com" not in locale, "in locale la descrizione parla di cloud")
    esigi("in locale" in scheda_locale and "in cloud" not in scheda_locale, "in locale la scheda parla di cloud")
    with patch.object(config, "MAIN_MODEL", "glm-5.3-flash:cloud"), patch.object(config, "LEARNING_MODEL", "qwen3:9b"):
        impostazioni_cloud = config.leggi_impostazioni()
        cloud = prompts.descrizione(impostazioni_cloud, POLITICA)
        scheda_cloud = prompts.istruzioni_sull_ambiente(impostazioni=impostazioni_cloud, politica=POLITICA)[0]
    esigi(
        "esce di qui" not in cloud and "ti fa parlare sta su ollama.com" in cloud and "l'output dei comandi" in cloud,
        "con la conversazione in cloud la descrizione promette privacy, o non dice cosa esce",
    )
    esigi("in cloud" in scheda_cloud and "qwen3:9b, in locale" in scheda_cloud, "la scheda non distingue i ruoli")
    # La privacy la dice la descrizione, una volta: la scheda nomina solo il posto.
    esigi("servizio remoto" not in scheda_cloud, "la scheda ripete la privacy della descrizione")
    with patch.object(config, "MAIN_MODEL", "qwen3:9b"), patch.object(config, "LEARNING_MODEL", "gpt-oss:120b-cloud"):
        estrazione = prompts.descrizione(config.leggi_impostazioni(), POLITICA)
    esigi("estrae le memorie dai vostri turni sta su ollama.com" in estrazione, "l'estrazione in cloud non e' detta")

    # La shell segue il sistema: `bash -lc` non esiste su Windows. La dicono
    # la scheda e la descrizione di `run_command`, non la docstring di Agno.
    with patch.object(os, "name", "nt"):
        finestre = prompts.istruzioni_sull_ambiente(impostazioni=IMPOSTAZIONI, politica=POLITICA)[0]
        comando_nt = prompts.descrizione_del_comando()
    esigi(
        "shell PowerShell" in finestre and "'powershell', '-Command'" in comando_nt and "bash" not in comando_nt,
        "su Windows il prompt parla di bash",
    )
    with patch.object(os, "name", "posix"):
        comando_posix = prompts.descrizione_del_comando()
    esigi("'bash', '-lc'" in comando_posix, "su POSIX la descrizione del comando non suggerisce bash")
    return "modelli e sistema nella scheda, utente e cartella nell'avvio; descrizione e scheda seguono il cloud"


def strumenti(agent, user_id: str) -> str:
    """Gli strumenti che Ares dovrebbe avere arrivano davvero al modello.

    Uno strumento mancante non protesta: il modello risponde di non sapere.
    La lista si risolve come fa Agno a inizio turno, per vedere il consegnato e
    non il configurato. Due trappole: `agent._learning` e' None finche' non si
    tocca `learning_machine`, e `UserMemoryStore.get_tools` e' vuoto senza
    `user_id`.
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
    if POLITICA.cronologia.sessioni_passate:
        attesi["search_past_sessions"] = "SEARCH_PAST_SESSIONS"
        attesi["read_past_session"] = "SEARCH_PAST_SESSIONS"
    if POLITICA.cronologia.cronologia_chat:
        attesi["get_chat_history"] = "READ_CHAT_HISTORY"
    if POLITICA.apprendimento.memorie and POLITICA.apprendimento.strumenti_memoria:
        attesi["update_user_memory"] = "MEMORY_AGENT_TOOLS"
    if POLITICA.apprendimento.intuizioni:
        attesi["search_learnings"] = "LEARN_KNOWLEDGE"
        attesi["save_learning"] = "LEARN_KNOWLEDGE"
    if config.OFFLOAD_TOOL_RESULTS:
        attesi["read_result"] = "OFFLOAD_TOOL_RESULTS"
        attesi["search_result"] = "OFFLOAD_TOOL_RESULTS"
    # Il verso opposto, provato per primo: nessuna istruzione deve nominare uno
    # strumento non consegnato, o il modello provera' a chiamarlo. Sta prima
    # del ritorno non concludente perche' il caso peggiore e' con tutti gli
    # strumenti spenti.
    istruzioni = " ".join(t for t in agent.instructions if isinstance(t, str))
    silenziosi, confermati = config.liste_modalita(config.MODO_PREDEFINITO)
    for nome in (
        "update_user_memory",
        "search_past_sessions",
        "read_past_session",
        "get_chat_history",
        "remember_about",
        "search_learnings",
        "save_learning",
        *(nome for nome, _ in strumenti_spazio([*silenziosi, *confermati], POLITICA)),
    ):
        if nome in istruzioni:
            esigi(nome in nomi, "le istruzioni nominano " + nome + ", che non arriva al modello")

    if not attesi:
        return NON_CONCLUSIVO + "gli strumenti opzionali sono spenti in config.py, e nessuna istruzione li nomina"

    for nome, flag in sorted(attesi.items()):
        esigi(nome in nomi, nome + " non arriva al modello benche' " + flag + " sia acceso")

    return str(len(attesi)) + " strumenti su " + str(len(nomi)) + " consegnati: " + ", ".join(sorted(attesi))


# Parole che in italiano non esistono: due diverse sulla stessa riga la
# dicono inglese. I nomi degli strumenti non contano, `\b` non spezza `_`.
_PAROLE_INGLESI = re.compile(r"\b(the|is|are|you|your|to|of|and|with|when|use|this|that)\b", re.IGNORECASE)


def fuori_dai_dati(prompt: str) -> str:
    """Il system message senza i blocchi di dati, cioe' la parte scritta dal prompt.

    Sono dati i blocchi XML che non sono sezioni di Ares (`prompts.SEZIONI`),
    guide degli store (`istruzioni_*`) o `additional_information`: memorie,
    entita', profilo. Come il testo fra i delimitatori di `ARES.md` e l'inizio
    delle conversazioni precedenti, lingua e impaginazione non dipendono dal
    prompt.
    """
    from ares.agent.prompts import SEZIONI

    nostri = "|".join([*SEZIONI, "istruzioni_", "additional_information"])
    senza_dati = re.sub(r"<(?!(?:" + nostri + r"))(\w+)>.*?</\1>", "", prompt, flags=re.DOTALL)
    senza_dati = re.sub(r"^- \S+ \(.*?\): .*$", "", senza_dati, flags=re.MULTILINE)
    return re.sub(r"--- inizio di .*? ---.*?--- fine di .*? ---", "", senza_dati, flags=re.DOTALL)


def righe_inglesi(prompt: str) -> list[str]:
    """Le righe del system message scritte in inglese, fuori dai blocchi di dati."""
    return [
        riga
        for riga in fuori_dai_dati(prompt).splitlines()
        if len({p.lower() for p in _PAROLE_INGLESI.findall(riga)}) >= 2
    ]


def prompt_in_italiano(agent, user_id: str, session_id: str) -> str:
    """Il system message intero e' in italiano, compresa la parte che scrive Agno.

    Ares sostituisce le guide inglesi di Agno (store, quaderno, Markdown, nome,
    ora, risultati lunghi): qui si cerca sul messaggio composto qualunque riga
    inglese, non un elenco di frasi note, e si verifica che gli strumenti
    restino nominati.
    """
    from ares.agent.prompts import messaggio_di_sistema

    prompt = messaggio_di_sistema(agent, session_id=session_id, utente=Utente.da_grezzo(user_id))
    inglesi = righe_inglesi(prompt)
    esigi(not inglesi, "il prompt contiene righe in inglese: " + repr(inglesi[:3]))
    doppio = re.search(r"\S {2,}\S", fuori_dai_dati(prompt))
    esigi(doppio is None, "il prompt contiene un doppio spazio: " + repr(doppio and doppio.group()))
    attesi = ["quaderno privato", "Formatta le risposte in Markdown", "La tua memoria, e chi la scrive", "- Adesso: "]
    if config.LEARN_KNOWLEDGE:
        attesi += [
            "<istruzioni_intuizioni>",
            "search_learnings",
            "una persona sola",
            "titolo, intuizione e contesto in italiano",
        ]
    if config.LEARN_ENTITIES:
        attesi += ["<istruzioni_entita>", "remember_about"]
    if config.LEARN_USER_MEMORY and config.MEMORY_AGENT_TOOLS:
        attesi += ["<istruzioni_memorie>", "update_user_memory"]
    if config.OFFLOAD_TOOL_RESULTS:
        attesi += ["read_result", str(config.TOOL_RESULT_THRESHOLD_CHARS)]
        esigi(prompt.count("read_result") == 1, "la guida ai risultati lunghi e' ripetuta")
    for atteso in attesi:
        esigi(atteso in prompt, "manca dal prompt: " + atteso)
    return str(len(attesi)) + " blocchi italiani presenti, nessuna riga inglese fuori dai dati"


def struttura_del_prompt(agent, user_id: str, session_id: str) -> str:
    """Il prompt e' fatto di sezioni note, in ordine, con cio' che cambia in fondo.

    Due sessioni con la stessa configurazione devono avere le stesse sezioni
    fisse: cio' che distingue una sessione (utente, id, cartella, ora) sta
    tutto in `questo_avvio`, l'ultima.
    """
    from zoneinfo import ZoneInfo

    from ares.agent import prompts

    tag = [t[1 : t.index(">")] for t in agent.instructions]
    esigi(all(t in prompts.SEZIONI for t in tag), "istruzioni fuori dalle sezioni note: " + repr(tag))
    esigi(tag == sorted(tag, key=prompts.SEZIONI.index), "sezioni fuori ordine: " + repr(tag))
    esigi(tag[-1] == "questo_avvio", "l'ultima sezione non e' quella dell'avvio")

    prompt = prompts.messaggio_di_sistema(agent, session_id=session_id, utente=Utente.da_grezzo(user_id))
    posizioni = [prompt.index("<" + t + ">") for t in tag]
    esigi(posizioni == sorted(posizioni), "nel system message le sezioni non seguono l'ordine")

    def composte(utente: str, sessione: str):
        return prompts.istruzioni(
            impostazioni=IMPOSTAZIONI,
            politica=POLITICA,
            utente=Utente.da_grezzo(utente),
            session_id=sessione,
            radice_lavoro=PERCORSI.lavoro if POLITICA.workspace.attivo else None,
        )

    una, altra = composte("prova-a", "sessione-a"), composte("prova-b", "sessione-b")
    esigi(una.fisse == altra.fisse, "due sessioni con la stessa configurazione hanno sezioni fisse diverse")
    esigi(una.avvio != altra.avvio, "l'avvio non distingue le sessioni")
    chiamate = una()
    esigi(chiamate[:-1] == una.fisse, "chiamate, le istruzioni cambiano oltre l'avvio")
    esigi("- Adesso: " in chiamate[-1] and "- Adesso: " not in una[-1], "l'ora non e' aggiunta solo a ogni turno")

    ora = prompts.riga_dell_ora(datetime(2026, 9, 30, 22, 4, tzinfo=ZoneInfo(config.FUSO_ORARIO)))
    esigi(ora == "- Adesso: mercoledi' 30 settembre 2026, 22:04 CEST.", "l'ora non e' in italiano: " + ora)

    esigi(
        "ARES.md" in prompts.istruzioni_sulla_fiducia(regole="ARES.md")[0]
        and "regole del progetto" not in prompts.istruzioni_sulla_fiducia(regole=None)[0],
        "la fiducia nomina le regole del progetto quando non ci sono, o le tace quando ci sono",
    )
    return str(len(tag)) + " sezioni in ordine, fisse uguali fra due sessioni, ora solo nell'avvio"


def istruzioni_fuori_modalita() -> str:
    """Fuori da AGENTIC le istruzioni italiane delle intuizioni non entrano.

    Ares usa solo `AGENTIC`: questo ripiego protegge chi costruisse lo store
    con un'altra modalita'.
    """
    from agno.learn import LearnedKnowledgeConfig, LearningMode
    from agno.learn.stores import LearnedKnowledgeStore

    from ares.agent.learning import AresLearnedKnowledgeStore

    store = AresLearnedKnowledgeStore(
        config=LearnedKnowledgeConfig(knowledge=None, model=None, mode=LearningMode.ALWAYS, enable_agent_tools=True)
    )
    testo = store.instructions()
    esigi(
        testo == LearnedKnowledgeStore.instructions(store),
        "fuori da AGENTIC Ares non lascia passare le istruzioni di Agno: " + testo[:80],
    )
    esigi("<istruzioni_intuizioni>" not in testo, "la guida AGENTIC entra in uno store che non e' AGENTIC")
    return "fuori da AGENTIC passano le istruzioni di Agno, non quelle italiane"


def modalita() -> str:
    """Le quattro modalita' sono partizioni degli otto strumenti, e il prompt le segue.

    Per ognuna: cosa chiede conferma e cosa manca nello spazio di lavoro, e le
    istruzioni corrispondenti; in `piano` nessuno strumento che scriva.
    """
    from agno.tools.workspace import Workspace

    from ares.agent import prompts

    tutti = set(Workspace._ALIASES)
    esiti = []
    for nome, (silenziosi, confermati) in config.MODALITA.items():
        spazio = build_workspace(PERCORSI, POLITICA, nome)
        consegnati = {f.name: f for f in spazio.functions.values()}
        for alias in tutti:
            strumento = config.WORKSPACE_PREFIX + Workspace._ALIASES[alias]
            if alias in silenziosi or alias in confermati:
                esigi(strumento in consegnati, nome + ": " + strumento + " non arriva al modello")
                esigi(
                    bool(consegnati[strumento].requires_confirmation) == (alias in confermati),
                    nome + ": " + strumento + " ha la conferma sbagliata",
                )
            else:
                esigi(strumento not in consegnati, nome + ": " + strumento + " arriva benche' escluso")
        scheda = prompts.istruzioni_sull_ambiente(
            impostazioni=IMPOSTAZIONI, politica=POLITICA, radice_lavoro=spazio.root, modo=nome
        )[0]
        esigi("Modalita' " + nome in scheda, nome + ": la scheda non la nomina")
        paragrafo = " ".join(prompts.istruzioni_sugli_strumenti(spazio.root, nome, politica=POLITICA))
        for alias in tutti:
            strumento = config.WORKSPACE_PREFIX + Workspace._ALIASES[alias]
            esigi(
                (strumento in paragrafo) == (alias in silenziosi or alias in confermati),
                nome + ": " + strumento + " nel paragrafo sugli strumenti quando non dovrebbe, o viceversa",
            )
        esiti.append(nome + " " + str(len(silenziosi)) + "+" + str(len(confermati)))
    esigi("Proponi" in prompts.istruzioni_sulla_modalita("piano"), "piano non chiede di proporre")
    try:
        config.liste_modalita("turbo")
    except ValueError as errore:
        esigi("manuale" in str(errore), "l'errore non elenca le modalita' valide")
    else:
        esigi(False, "una modalita' sconosciuta viene accettata")
    return "consegna, conferme e prompt coerenti per " + ", ".join(esiti)


def colpo_singolo(user_id: str, session_id: str) -> str:
    """`ares -p`: cio' che Ares sa entra nel prompt, ma niente puo' scriverci.

    Con `interattivo=False` mancano il post-hook e gli strumenti di scrittura
    degli store, e il prompt lo dice; il contesto gia' appreso resta.
    """
    from agno.agent import _tools
    from agno.run.agent import RunOutput
    from agno.run.base import RunContext
    from agno.session.agent import AgentSession

    from ares.agent.prompts import messaggio_di_sistema

    muto = build_assistant(
        PERCORSI,
        IMPOSTAZIONI,
        POLITICA,
        utente=Utente.da_grezzo(user_id),
        session_id=session_id + "-p",
        interattivo=False,
    )
    esigi(not muto.post_hooks, "in -p il post-hook di apprendimento e' agganciato")
    assert muto.learning_machine is not None
    _ = muto.result_store
    sessione = AgentSession(session_id="prova-colpo", user_id=user_id)
    voci = _tools.get_tools(
        agent=muto,
        run_response=RunOutput(run_id="prova-colpo"),
        run_context=RunContext(run_id="prova-colpo", user_id=user_id, session_id="prova-colpo"),
        session=sessione,
        user_id=user_id,
    )
    nomi = set()
    for voce in voci:
        if hasattr(voce, "functions"):
            nomi.update(voce.functions.keys())
        else:
            nomi.add(getattr(voce, "name", None) or getattr(voce, "__name__", ""))
    scrittori = {"update_user_memory", "remember_about", "link_entities", "forget", "save_learning"}
    esigi(
        not (scrittori & nomi),
        "in -p arrivano strumenti che scrivono in memoria: " + ", ".join(sorted(scrittori & nomi)),
    )
    istruzioni = " ".join(t for t in muto.instructions if isinstance(t, str))
    esigi(
        "ares -p" in istruzioni and "L'apprendimento e' disattivato" in istruzioni,
        "il prompt non dice che e' -p",
    )
    # La strada per autorizzare e' una chat nel terminale: `/modo` e le
    # modalita' silenziose qui non esistono, e il modello non deve proporle.
    esigi("chat di Ares aperta in un terminale" in istruzioni, "il prompt di -p non dice dove si autorizza")
    esigi("/modo" not in istruzioni, "il prompt di -p propone /modo")
    for nome, _ in strumenti_spazio(config.liste_modalita(config.MODO_PREDEFINITO)[1], POLITICA):
        esigi(nome in istruzioni, "il prompt di -p non nomina " + nome + " fra gli strumenti rifiutati")
    prompt = messaggio_di_sistema(muto, session_id=session_id + "-p", utente=Utente.da_grezzo(user_id))
    esigi("<user_memory>" in prompt or "<user_profile>" in prompt, "in -p il contesto di memoria non entra nel prompt")
    for nome in scrittori | {"search_learnings", "search_entities"}:
        esigi(nome not in prompt, "in -p il prompt ordina di usare uno strumento assente: " + nome)
    esigi("CRITICAL RULES" not in prompt, "in -p riappare la guida inglese delle intuizioni")
    esigi("Si aggiornano da soli" not in prompt, "in -p il prompt promette estrazione automatica")
    esigi(
        config.QUADERNO_PREFIX + "write_file" in nomi and "quaderno resta persistente" in prompt,
        "in -p il quaderno e' descritto male",
    )
    return (
        "senza post-hook e senza "
        + str(len(scrittori))
        + " strumenti di scrittura, con il contesto e l'avviso nel prompt"
    )


def prompt_e_capacita() -> str:
    """Il prompt composto non prescrive strumenti assenti, anche a flag spenti.

    Ogni utente ha una conversazione precedente nella cartella: a store vuoti
    il blocco storico non entrerebbe mai, e il controllo sarebbe vuoto.
    """
    from contextlib import ExitStack

    from agno.run.agent import RunOutput
    from agno.run.base import RunContext
    from agno.session.agent import AgentSession

    from ares.agent.prompts import messaggio_di_sistema

    opzionali = {
        "update_user_memory",
        "remember_about",
        "link_entities",
        "forget",
        "search_entities",
        "search_learnings",
        "save_learning",
        "search_past_sessions",
        "read_past_session",
        "get_chat_history",
        "read_result",
        "search_result",
        *(
            nome
            for nome, _ in strumenti_spazio(
                ["read", "list", "search", "write", "edit", "move", "delete", "shell"], POLITICA
            )
        ),
    }
    flag = (
        "LEARN_USER_PROFILE",
        "LEARN_USER_MEMORY",
        "LEARN_SESSION_CONTEXT",
        "LEARN_ENTITIES",
        "LEARN_KNOWLEDGE",
        "MEMORY_AGENT_TOOLS",
        "WORKSPACE",
        "OFFLOAD_TOOL_RESULTS",
        "SEARCH_PAST_SESSIONS",
        "READ_CHAT_HISTORY",
    )
    casi = [(modo, interattivo, ()) for modo in config.MODALITA for interattivo in (True, False)]
    casi += [("manuale", True, (nome,)) for nome in flag]
    casi.append(("manuale", True, flag))
    db = build_db(
        PERCORSI,
    )
    for indice, (modo, interattivo, spenti) in enumerate(casi):
        with ExitStack() as stack:
            for nome in spenti:
                stack.enter_context(patch.object(config, nome, False))
            utente = "prompt-capacita-" + str(indice)
            precedente = _sessione_finta(
                "precedente-" + utente, 1234567890, "Decidiamo come organizzare il progetto.", utente
            )
            precedente.metadata = {"cartella": str(PERCORSI.lavoro.resolve())}
            db.upsert_session(precedente)
            # Dentro l'ExitStack: gli agenti di questo ciclo devono nascere
            # con i flag spenti dal caso, e la politica li fotografa qui.
            politica = config.leggi_politica()
            agente = build_assistant(
                PERCORSI,
                IMPOSTAZIONI,
                politica,
                utente=Utente.da_grezzo(utente),
                session_id=utente,
                modo=modo,
                interattivo=interattivo,
            )
            prompt = messaggio_di_sistema(agente, session_id=utente, utente=Utente.da_grezzo(utente))
            esigi(
                (precedente.session_id in prompt)
                == (politica.workspace.attivo and politica.cronologia.sessioni_passate),
                f"caso {indice}: il blocco delle conversazioni non segue la disponibilita' della ricerca",
            )
            voci = agente.get_tools(
                run_response=RunOutput(run_id=utente),
                run_context=RunContext(run_id=utente, user_id=utente, session_id=utente),
                session=AgentSession(session_id=utente, user_id=utente),
                user_id=utente,
            )
            disponibili = set()
            for voce in voci:
                if hasattr(voce, "functions"):
                    disponibili.update(voce.functions)
                else:
                    disponibili.add(getattr(voce, "name", None) or getattr(voce, "__name__", ""))
            for nome in opzionali - disponibili:
                esigi(nome not in prompt, f"caso {indice} {modo}: istruzioni per {nome} assente")
            esigi("CRITICAL RULES" not in prompt, f"caso {indice}: guida inglese di Agno")
            if not interattivo:
                esigi("Si aggiornano da soli" not in prompt, f"caso {indice}: estrazione promessa in -p")
                esigi("/modo" not in prompt, f"caso {indice} {modo}: senza terminale il prompt propone /modo")
            elif politica.workspace.attivo:
                esigi("/modo" in prompt, f"caso {indice} {modo}: il prompt non dice come cambiare modalita'")
            if modo == "piano" and politica.workspace.attivo:
                esigi("Memoria e quaderno seguono le regole" in prompt, "piano promette sola lettura globale")
            if not politica.apprendimento.intuizioni:
                esigi("Le intuizioni sono indicizzate" not in prompt, "indice annunciato con flag spento")
    return str(len(casi)) + " prompt composti coerenti con modalita', interattivita' e strumenti consegnati"


def protezione_contesto(agent, user_id: str) -> str:
    """I risultati grandi sono lossless, locali e non gonfiano il prompt."""
    esigi(
        agent.max_tool_calls_from_history == POLITICA.cronologia.strumenti_dalla_cronologia,
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
        Path(str(store.fs.backend.db_engine.url.database)).resolve() == Path(PERCORSI.fs_db_file).resolve(),
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

    - Nomi: senza prefisso Agno ne scarterebbe cinque su otto, e `read_file`
      sembrerebbe leggere il disco.
    - Permessi: `requires_confirmation` deve sopravvivere alla rinomina, o la
      shell partirebbe senza chiedere.
    - Radice: tiene Ares fuori dal proprio codice e dall'archivio.

    Gli strumenti si risolvono fino allo schema per il modello, dove si vedono
    nomi e flag di conferma.
    """
    from agno.agent import _tools
    from agno.run.agent import RunOutput
    from agno.run.base import RunContext
    from agno.session.agent import AgentSession

    istruzioni = " ".join(t for t in agent.instructions if isinstance(t, str))
    if not POLITICA.workspace.attivo:
        esigi(
            config.WORKSPACE_PREFIX not in istruzioni,
            "le istruzioni parlano dello spazio di lavoro, che e' spento in config.py",
        )
        return NON_CONCLUSIVO + "WORKSPACE e' spento in config.py, e nessuna istruzione lo nomina"

    # Tutti e due risolti: su Windows la temp ha il nome corto (`RUNNER~1`)
    # e `config` la conserva espansa.
    esigi(
        PERCORSI.lavoro.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()),
        "la prova sta usando lo spazio di lavoro vero: " + str(PERCORSI.lavoro),
    )
    esigi(PERCORSI.lavoro.is_dir(), "lo spazio di lavoro non e' stato creato")
    # La cartella viaggia con la sessione: Agno copia `metadata` nella
    # sessione nuova, ed e' cio' che `ares resume` e `/sessioni` rileggono.
    esigi(
        agent.metadata == {"cartella": str(PERCORSI.lavoro.resolve())},
        "l'agente non registra la cartella nei metadati: " + repr(agent.metadata),
    )

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

    silenziosi, confermati = config.liste_modalita(config.MODO_PREDEFINITO)
    attesi = {
        config.WORKSPACE_PREFIX + Workspace._ALIASES[alias]: alias in confermati
        for alias in list(silenziosi) + list(confermati)
    }
    for nome, va_confermato in sorted(attesi.items()):
        esigi(nome in consegnati, nome + " non arriva al modello")
        esigi(
            bool(consegnati[nome].requires_confirmation) == va_confermato,
            nome + (" gira senza chiedere niente" if va_confermato else " chiede il permesso e non dovrebbe"),
        )

    # La collisione e' silenziosa per costruzione: Agno tiene il primo nome
    # arrivato e scrive un WARNING. Qui si guarda l'intersezione, non i log.
    del_quaderno = set(build_quaderno(build_filesystem(PERCORSI, Utente.da_grezzo(user_id))).functions)
    comuni = del_quaderno & set(attesi)
    esigi(not comuni, "lo spazio di lavoro e il quaderno privato si contendono: " + ", ".join(sorted(comuni)))
    # Ogni strumento del quaderno arriva col suo prefisso: un `read_file` nudo
    # farebbe leggere al modello il quaderno al posto della cartella.
    for nome in del_quaderno:
        esigi(nome.startswith(config.QUADERNO_PREFIX), nome + " del quaderno arriva senza prefisso")
        esigi(nome in consegnati, nome + " non arriva al modello")
    comando = consegnati.get(config.WORKSPACE_PREFIX + "run_command")
    if comando is not None:
        esigi(
            comando.description == descrizione_del_comando(),
            "run_command arriva al modello con la descrizione di Agno",
        )

    # La cartella e' quella dell'utente e non si crea: una che non esiste e'
    # un refuso, e costruirci sopra un workspace vuoto lo nasconderebbe.
    scelta_vera = PERCORSI.lavoro
    inesistente = replace(PERCORSI, lavoro=scelta_vera / "non-esiste")
    try:
        build_workspace(inesistente, POLITICA)
    except ValueError:
        pass
    else:
        esigi(False, "una cartella inesistente non ha fermato build_workspace")

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

    L'ora la mette `prompts.Istruzioni` a ogni turno; le date delle memorie
    arrivano solo grazie allo schema personalizzato. Si guarda il system
    message costruito davvero, due volte: l'ora dev'essere quella del turno.
    """
    from agno.agent import _messages
    from agno.run.base import RunContext
    from agno.session.agent import AgentSession

    from ares.agent import prompts

    def costruisci() -> str:
        return _messages.get_system_message(
            agent=agent,
            session=AgentSession(session_id="prova-tempo", user_id=user_id),
            run_context=RunContext(run_id="prova", user_id=user_id, session_id="prova-tempo"),
            tools=[],
        ).content

    esigi(not agent.add_datetime_to_context, "add_datetime_to_context e' acceso: l'ora arriva anche in inglese")
    prima = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)
    with patch.object(prompts, "riga_dell_ora", lambda: "- Adesso: " + prima.isoformat() + "."):
        vecchio = costruisci()
    prompt = costruisci()
    esigi(prima.isoformat() in vecchio, "l'ora non viene da riga_dell_ora")
    riga_ora = next((r for r in prompt.splitlines() if r.startswith("- Adesso: ")), "")
    esigi(bool(riga_ora) and prima.isoformat() not in riga_ora, "l'ora non e' ricalcolata a ogni system message")
    esigi(re.search(r", \d\d:\d\d \S+\.$", riga_ora) is not None, "l'ora arriva senza minuti o fuso: " + riga_ora)

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

    Con uno store spento la LearningMachine non lo costruisce, e
    `lm.user_profile_store` e' None.
    """
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        stampa_store(None, "Profilo", user_id=user_id)
    detto = catturato.getvalue().strip()
    esigi("spento" in detto, "uno store spento non viene annunciato: " + repr(detto))

    vuota = _MacchinaSenzaStore()
    esigi(leggi_entita(vuota, Utente.da_grezzo(user_id)) == [], "leggi_entita non regge uno store di entita' spento")
    esigi(leggi_intuizioni(vuota, Utente.da_grezzo(user_id)) == [], "leggi_intuizioni non regge uno store spento")
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

    Prima sull'archivio seminato (fotografia uguale al seme, memoria aggiunta
    vista come nuova e intera), poi sulla sola differenza con fotografie a mano,
    per i rami che il seme non produce: memoria riscritta o tolta, campo
    svuotato, nessuna variazione.
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

    Il confronto e' con il conteggio grezzo in SQLite; il numero seminato e' la
    terza voce, se archivio e store concordassero su un valore sbagliato.
    """
    if "entity_memory" not in lm.stores:
        return NON_CONCLUSIVO + "entity_memory e' spento in config: non c'e' niente da seminare"
    namespace = namespace_entita(Utente.da_grezzo(user_id))
    in_archivio = conta_apprendimenti("entity_memory", namespace)
    esigi(
        in_archivio == len(ENTITA_SEMINATE),
        "seminate " + str(len(ENTITA_SEMINATE)) + " entita' ma in " + namespace + " ce ne sono " + str(in_archivio),
    )
    # Limite alto e non il default: un elenco tagliato dalla paginazione
    # sembrerebbe un difetto di lettura.
    rilette = leggi_entita(lm, Utente.da_grezzo(user_id), limit=1000)
    esigi(
        len(rilette) == in_archivio,
        "in archivio ci sono " + str(in_archivio) + " entita' ma lo store ne restituisce " + str(len(rilette)),
    )
    return str(len(rilette)) + " entita' rilette su " + str(in_archivio) + " in archivio"


def fatti_leggibili(lm, user_id: str) -> str:
    """I fatti delle entita' si leggono con la chiave giusta.

    Sono dict con chiave `content`: leggere `fact` darebbe None in silenzio.
    """
    if "entity_memory" not in lm.stores:
        return NON_CONCLUSIVO + "entity_memory e' spento in config: non c'e' nessun fatto da leggere"
    righe = []
    for entita in leggi_entita(lm, Utente.da_grezzo(user_id), limit=1000):
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

    La ricerca di Agno confronta la query con tutti i valori, namespace
    compreso (`user/<utente>/personale`): "person" troverebbe ogni entita'.
    """
    lm = agent.learning_machine
    if "entity_memory" not in lm.stores:
        return NON_CONCLUSIVO + "entity_memory e' spento in config: non c'e' niente da cercare"
    intero = leggi_entita(lm, Utente.da_grezzo(user_id), limit=1000)
    esigi(len(intero) > 1, "serve piu' di un'entita' in archivio perche' un filtro voglia dire qualcosa")

    # Il nome: il caso facile, ed e' l'unico che un filtro rotto supera lo stesso.
    per_nome = [nome_entita(e) for e in leggi_entita(lm, Utente.da_grezzo(user_id), query="Uno")]
    esigi(per_nome == ["Entita Uno"], "cercare un nome non restituisce quell'entita': " + repr(per_nome))

    # Un fatto: la ricerca guarda dentro, non solo il nome.
    per_fatto = [nome_entita(e) for e in leggi_entita(lm, Utente.da_grezzo(user_id), query="quattro")]
    esigi(per_fatto == ["Entita Due"], "cercare un fatto non trova la sua entita': " + repr(per_fatto))

    # Il caso che conta: "person" e' dentro `personale`, cioe' dentro il
    # namespace di ogni entita' di questo archivio. Deve restare il tipo.
    per_tipo = sorted(nome_entita(e) for e in leggi_entita(lm, Utente.da_grezzo(user_id), query="person"))
    esigi(
        per_tipo == ["Entita Due"],
        "una parola del namespace pesca entita' che non la contengono: " + repr(per_tipo),
    )

    esigi(leggi_entita(lm, Utente.da_grezzo(user_id), query="pipppo") == [], "una parola inventata trova qualcosa")

    # Il filtro esiste anche a monte: l'argomento del comando deve arrivare
    # fino allo store. Un `/entita` che lo ignora stampa l'archivio intero e
    # sembra rispondere.
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        gestisci_comando(
            "/entita Uno",
            StatoChat(
                agent=agent,
                session_id="sessione",
                utente=Utente.da_grezzo(user_id),
                percorsi=PERCORSI,
                impostazioni=IMPOSTAZIONI,
                politica=config.leggi_politica(),
            ),
        )
    stampato = catturato.getvalue()
    esigi("Entita Uno" in stampato, "/entita con un argomento non trova l'entita' cercata")
    esigi("Entita Due" not in stampato, "/entita ignora l'argomento e stampa l'archivio intero")
    return "nome, fatto e tipo trovano una sola entita'; il namespace non ne pesca nessuna"


def nome_entita(entita) -> str:
    return str(getattr(entita, "name", "?"))


def _sessione_finta(nome: str, creata: int, domanda: str, user_id: str):
    """Una conversazione di una domanda sola, pronta per il database.

    Senza `agent_id` sul RunOutput, `AgentSession.from_dict` scarta il run in
    silenzio.
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

    `upsert_session` pone `updated_at = created_at` all'inserimento e `adesso`
    all'aggiornamento: riscrivere una sessione separa ultima modifica, creazione
    e ordine di scrittura.
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

    L'ordine atteso non coincide con nessun ordine sbagliato plausibile: un
    `sort_by` non riconosciuto non solleva niente e lascia l'ordine del disco.
    """
    atteso = semina_sessioni(agent, user_id)
    lette = list(elenca(agent.db, Utente.da_grezzo(user_id), ambito="tutte").nomi)
    esigi(lette == atteso, "ordine per ultima modifica non rispettato: " + repr(lette))

    # Il filtro guarda il nome, e taglia dopo aver filtrato: una sessione che
    # corrisponde ma e' vecchia deve restare visibile.
    filtrate = list(elenca(agent.db, Utente.da_grezzo(user_id), ambito="tutte", testo="LAVORO").nomi)
    esigi(filtrate == ["lavoro-gamma", "lavoro-delta"], "il filtro sul nome non funziona: " + repr(filtrate))
    esigi(
        elenca(agent.db, Utente.da_grezzo(user_id), ambito="tutte", testo="pipppo").totale == 0,
        "un filtro inventato trova qualcosa",
    )

    # Nessuna sessione di un altro utente.
    esigi(
        elenca(agent.db, Utente.da_grezzo(UTENTE_DI_CONTROLLO), ambito="tutte").totale == 0,
        "le sessioni di un utente si vedono da un altro utente",
    )

    prima = elenca(agent.db, Utente.da_grezzo(user_id), ambito="tutte", limite=1).voci[0]
    righe = " ".join(righe_sessione(prima, corrente=True))
    esigi("(questa)" in righe, "la sessione in corso non e' marcata: " + repr(righe))
    esigi("1 scambio" in righe, "il numero di scambi e' sbagliato: " + repr(righe))
    esigi("domanda di gamma" in righe, "la prima domanda non compare: " + repr(righe))
    esigi("(questa)" not in " ".join(righe_sessione(prima)), "ogni sessione risulta quella in corso")

    # Si filtra prima e si taglia dopo, o una sessione vecchia sarebbe
    # irraggiungibile proprio cercandola per nome. Il tetto si abbassa qui
    # invece di seminare venti conversazioni.
    tetto = config.SESSIONI_ELENCO
    config.SESSIONI_ELENCO = 2
    try:
        catturato = io.StringIO()
        with contextlib.redirect_stdout(catturato):
            gestisci_comando(
                "/sessioni",
                StatoChat(
                    agent=agent,
                    session_id=session_id,
                    utente=Utente.da_grezzo(user_id),
                    percorsi=PERCORSI,
                    impostazioni=IMPOSTAZIONI,
                    politica=config.leggi_politica(),
                ),
            )
        troncato = catturato.getvalue()
        esigi("altre 2" in troncato, "l'elenco tagliato non dice quante ne restano: " + repr(troncato))
        esigi(atteso[3] not in troncato, "il tetto non taglia niente")
        catturato = io.StringIO()
        with contextlib.redirect_stdout(catturato):
            gestisci_comando(
                "/sessioni " + atteso[3],
                StatoChat(
                    agent=agent,
                    session_id=session_id,
                    utente=Utente.da_grezzo(user_id),
                    percorsi=PERCORSI,
                    impostazioni=IMPOSTAZIONI,
                    politica=config.leggi_politica(),
                ),
            )
        esigi(
            atteso[3] in catturato.getvalue(),
            "una sessione oltre il tetto non si trova nemmeno cercandola: si taglia prima di filtrare",
        )
    finally:
        config.SESSIONI_ELENCO = tetto

    # La nota sulla sessione in corso: tre stati in fila (assente, in archivio,
    # in archivio ma esclusa da un filtro).
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        gestisci_comando(
            "/sessioni",
            StatoChat(
                agent=agent,
                session_id=session_id,
                utente=Utente.da_grezzo(user_id),
                percorsi=PERCORSI,
                impostazioni=IMPOSTAZIONI,
                politica=config.leggi_politica(),
            ),
        )
    stampato = catturato.getvalue()
    esigi(session_id in stampato, "l'assenza della sessione in corso non viene spiegata")
    for nome in atteso:
        esigi(nome in stampato, "/sessioni non stampa " + nome)

    # Ora la sessione in corso e' in archivio: la piu' vecchia, cosi' l'ordine
    # gia' verificato non cambia sopra di lei.
    agent.db.upsert_session(_sessione_finta(session_id, 500, "domanda di questa", user_id))
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        gestisci_comando(
            "/sessioni",
            StatoChat(
                agent=agent,
                session_id=session_id,
                utente=Utente.da_grezzo(user_id),
                percorsi=PERCORSI,
                impostazioni=IMPOSTAZIONI,
                politica=config.leggi_politica(),
            ),
        )
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
            gestisci_comando(
                "/sessioni",
                StatoChat(
                    agent=agent,
                    session_id=session_id,
                    utente=Utente.da_grezzo(user_id),
                    percorsi=PERCORSI,
                    impostazioni=IMPOSTAZIONI,
                    politica=config.leggi_politica(),
                ),
            )
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
        gestisci_comando(
            "/sessioni lavoro",
            StatoChat(
                agent=agent,
                session_id=session_id,
                utente=Utente.da_grezzo(user_id),
                percorsi=PERCORSI,
                impostazioni=IMPOSTAZIONI,
                politica=config.leggi_politica(),
            ),
        )
    filtrato = catturato.getvalue()
    esigi(
        "dal primo turno salvato" not in filtrato,
        "sotto un filtro l'elenco dichiara mancante una sessione che ha in archivio",
    )
    return str(len(atteso)) + " sessioni ordinate per ultima modifica, filtro, marcatore e nota verificati"


def comandi_sull_archivio(agent, user_id: str, session_id: str) -> str:
    """Ogni comando di lettura della REPL passa sull'archivio seminato.

    `/profilo`, `/memorie`, `/entita` sono il modo in cui l'utente verifica cosa
    Ares sa, anche dopo un ripristino: devono leggere davvero l'archivio.
    """

    def esegui_comando(riga: str) -> str:
        catturato = io.StringIO()
        with contextlib.redirect_stdout(catturato):
            vive = gestisci_comando(
                riga,
                StatoChat(
                    agent=agent,
                    session_id=session_id,
                    utente=Utente.da_grezzo(user_id),
                    percorsi=PERCORSI,
                    impostazioni=IMPOSTAZIONI,
                    politica=config.leggi_politica(),
                ),
            )
        esigi(vive is True, riga + " chiude la sessione")
        return catturato.getvalue()

    uscita = esegui_comando("/profilo")
    esigi(PROFILO_SEMINATO["occupation"] in uscita, "/profilo non mostra il seme: " + repr(uscita))
    uscita = esegui_comando("/memorie")
    esigi(all(m in uscita for m in MEMORIE_SEMINATE), "/memorie non mostra il seme: " + repr(uscita))
    uscita = esegui_comando("/contesto")
    esigi("Sessione di collaudo" in uscita, "/contesto non mostra il riassunto del seme: " + repr(uscita))

    uscita = esegui_comando("/entita")
    esigi(all(nome in uscita for nome, _, _ in ENTITA_SEMINATE), "/entita non elenca il seme: " + repr(uscita))
    uscita = esegui_comando("/entita parolachenonesiste")
    esigi("Nessuna entita' per" in uscita, "una ricerca a vuoto non lo dice: " + repr(uscita))
    esigi("testuale" in uscita, "una ricerca a vuoto non spiega che e' testuale: " + repr(uscita))

    uscita = esegui_comando("/file")
    esigi(FILE_SEMINATO[0] in uscita, "/file non elenca il quaderno: " + repr(uscita))
    esigi("byte" in uscita, "/file non dice la dimensione: " + repr(uscita))

    uscita = esegui_comando("/cartella")
    esigi(str(PERCORSI.lavoro) in uscita, "/cartella non nomina la directory: " + repr(uscita))
    esigi("nessun ARES.md" in uscita, "/cartella non dice che manca ARES.md: " + repr(uscita))
    uscita = esegui_comando("/lavoro")
    esigi(str(PERCORSI.lavoro) in uscita, "/lavoro, il vecchio nome, non e' piu' un alias: " + repr(uscita))
    acceso = config.WORKSPACE
    config.WORKSPACE = False
    try:
        uscita = esegui_comando("/cartella")
    finally:
        config.WORKSPACE = acceso
    esigi("spento" in uscita, "/cartella con il workspace spento non lo dice: " + repr(uscita))

    uscita = esegui_comando("/aiuto")
    esigi("/profilo" in uscita and "/esci" in uscita, "/aiuto non elenca i comandi: " + repr(uscita))

    # Le funzioni pure dietro `/sessioni`, sui rami che il seme non tocca: un
    # messaggio il cui contenuto e' una lista di parti e una sessione senza
    # data.
    class Messaggio:
        def __init__(self, role, content):
            self.role = role
            self.content = content

    class Run:
        def __init__(self, messages):
            self.messages = messages

    class Sessione:
        session_id = "a-parti"
        updated_at = None
        created_at = None
        runs = (Run([Messaggio("assistant", "x"), Messaggio("user", ["prima ", {"text": "parte"}, {"altro": 1}])]),)

    a_parti = riferimento(Sessione()).inizio
    esigi(a_parti == "prima parte", "un contenuto a parti non si legge: " + repr(a_parti))
    righe = righe_sessione(riferimento(Sessione()))
    esigi("data ignota" in righe[0], "una sessione senza data non lo dice: " + repr(righe))
    esigi(tronca(a_parti, 5) == "prima...", "il troncamento della domanda non avviene")

    # Le memorie come testo per il prompt: la legenda in testa, la data fra
    # quadre, una voce che non e' un dict resa com'e', una vuota saltata.
    memorie = AresMemories(
        user_id=user_id,
        memories=[
            {"content": "Con data.", "updated_at": "2026-09-05T10:00:00"},
            {"content": "Senza data."},
            {"content": ""},
            "testo nudo",
        ],
    )
    testo = memorie.get_memories_text()
    righe = testo.splitlines()
    esigi(righe[0].startswith("(fra parentesi quadre"), "la legenda non apre il testo: " + repr(righe))
    esigi("- Con data. [2026-09-05]" in righe, "la data non e' resa a giorno: " + repr(righe))
    esigi("- Senza data." in righe, "una memoria senza data non compare: " + repr(righe))
    esigi("- testo nudo" in righe, "una voce non dict non compare: " + repr(righe))
    esigi(len(righe) == 4, "una memoria vuota occupa una riga: " + repr(righe))
    esigi(AresMemories(user_id=user_id, memories=[]).get_memories_text() == "", "senza memorie il testo non e' vuoto")
    solo_vuote = AresMemories(user_id=user_id, memories=[{"content": ""}])
    esigi(solo_vuote.get_memories_text() == "", "sole memorie vuote producono la legenda")
    return "otto comandi sul seme, contenuti a parti, data ignota e testo delle memorie"


def file_isolati(user_id: str) -> str:
    """I file di un utente non si vedono da un altro utente."""
    esigi(
        user_id != UTENTE_DI_CONTROLLO,
        "l'utente in prova e' lo stesso di controllo: non c'e' niente da confrontare",
    )
    miei = {f.path for f in build_filesystem(PERCORSI, Utente.da_grezzo(user_id)).list()}
    altrui = {f.path for f in build_filesystem(PERCORSI, Utente.da_grezzo(UTENTE_DI_CONTROLLO)).list()}
    esigi(FILE_SEMINATO[0] in miei, "il file seminato non si rilegge: " + str(sorted(miei)))
    condivisi = miei & altrui
    esigi(not condivisi, "un altro utente vede " + str(sorted(condivisi)))
    return str(len(miei)) + " file di " + user_id + ", nessuno visibile a un altro utente"


def indice_vettoriale(lm) -> str:
    """La tabella LanceDB si apre e la ricerca ibrida ha il suo motore.

    Nessun embedding viene calcolato. Senza `tantivy` la ricerca ibrida si
    ridurrebbe alla sola similarita' vettoriale.
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

    La directory e' il controllo che regge (senza attraversarla i file non si
    raggiungono), ma i database si verificano uno a uno perche' un archivio
    puo' essere copiato altrove. Su Windows `rendi_privato` non tocca la DACL
    ereditata, quindi non c'e' niente da verificare.
    """
    if os.name != "posix":
        return NON_CONCLUSIVO + "i permessi numerici sono una proprieta' POSIX"

    def modo(percorso: Path) -> str:
        return oct(percorso.stat().st_mode)[-3:]

    directory = [("archivio", Path(PERCORSI.stato)), ("indice vettoriale", Path(PERCORSI.lancedb_uri))]
    for etichetta, percorso in directory:
        esigi(percorso.is_dir(), etichetta + " assente: " + str(percorso))
        esigi(modo(percorso) == "700", etichetta + " attraversabile da altri: " + modo(percorso))

    database = [Path(PERCORSI.db_file), Path(PERCORSI.fs_db_file)]
    for percorso in database:
        esigi(percorso.is_file(), "database assente: " + str(percorso))
        esigi(
            modo(percorso) == "600",
            "database leggibile da altri: " + percorso.name + " " + modo(percorso),
        )

    # Un file gia' scritto con permessi larghi va corretto alla costruzione
    # successiva, non solo nei cloni nuovi.
    database[0].chmod(0o644)
    build_db(
        PERCORSI,
    )
    esigi(
        modo(database[0]) == "600",
        "archivio preesistente non corretto: " + modo(database[0]),
    )

    return str(len(directory)) + " directory a 700 e " + str(len(database)) + " database a 600, anche se preesistenti"


def percorsi_a_runtime() -> str:
    """I percorsi sono un oggetto costruito quando serve, e non esistono come nomi di modulo.

    `leggi_percorsi` legge un ambiente dato: `ARES_HOME` sposta stato e backup
    insieme, `ARES_TMP` una parte sola, i nomi derivati seguono. La prova
    pretende che nessun nome di percorso stia in `config`.
    """
    from ares.config import Percorsi, leggi_percorsi

    radice = RADICE_PROVA / "percorsi"
    casa = radice / "casa"
    tutto = leggi_percorsi({"ARES_HOME": str(casa)}, cwd=radice)
    esigi(tutto.stato == casa / "stato" and tutto.backup == casa / "backup", "ARES_HOME non sposta stato e backup")
    esigi(tutto.lavoro == radice, "la cartella di lavoro non viene letta")
    esigi(tutto.db_file == str(casa / "stato" / "kairos.db"), "il database non deriva dallo stato")
    esigi(tutto.lock_file == casa / "stato.lock", "il lock non e' accanto allo stato")
    esigi(tutto.cronologia_file == casa / "stato" / "cronologia_chat.txt", "la cronologia non e' nello stato")
    parte = leggi_percorsi({"ARES_TMP": str(radice / "solo-stato")}, cwd=radice)
    esigi(
        parte.stato == radice / "solo-stato" and parte.backup == Path.home() / ".ares" / "backup",
        "ARES_TMP sposta troppo",
    )
    esigi(isinstance(tutto, Percorsi), "leggi_percorsi non restituisce un Percorsi")
    esigi(not hasattr(tutto, "utente"), "l'identita' e' ancora un campo dei percorsi")
    # Nessuno di questi nomi esiste piu': chi ne rimettesse uno solo
    # ricostruirebbe il canale che nessuna firma lascia vedere.
    for nome in (
        "PERCORSI",
        "TMP_DIR",
        "DB_FILE",
        "FS_DB_FILE",
        "LANCEDB_URI",
        "BACKUP_DIR",
        "STATE_LOCK_FILE",
        "CRONOLOGIA_FILE",
        "WORKSPACE_DIR",
        "ARES_HOME",
        "imposta_percorsi",
    ):
        esigi(not hasattr(config, nome), "config espone ancora " + nome + ": e' un canale invisibile")

    # Il `.env` resta fuori da `os.environ`: arriva ai nomi di `config`, una
    # variabile d'ambiente vince, e un sottoprocesso di `run_command` non lo
    # eredita. Maiuscole solo su Windows, dove `os.environ` non distingue.
    from ares.config import leggi_ambiente

    radice.mkdir(parents=True, exist_ok=True)
    file_env = radice / "env-di-prova"
    file_env.write_text("ARES_MAIN_MODEL=dal-file\nSEGRETO_DI_PROVA=non-nell-ambiente\n", encoding="utf-8")
    letto = leggi_ambiente(file_env, {"ARES_MAIN_MODEL": "dalla-shell"})
    esigi(letto["ARES_MAIN_MODEL"] == "dalla-shell", "la riga del .env vince sull'ambiente")
    esigi(letto["SEGRETO_DI_PROVA"] == "non-nell-ambiente", "una riga del .env non arriva")
    esigi("SEGRETO_DI_PROVA" not in os.environ, "leggere il .env lo mette in os.environ")
    esigi(leggi_ambiente(radice / "assente", {"X": "1"}) == {"X": "1"}, "un .env assente non e' un ambiente vuoto")
    file_env.write_text("ares_minuscolo=1\n", encoding="utf-8")
    with patch.object(os, "name", "nt"):
        esigi("ARES_MINUSCOLO" in leggi_ambiente(file_env, {}), "su Windows la chiave non sale in maiuscolo")
    with patch.object(os, "name", "posix"):
        esigi("ares_minuscolo" in leggi_ambiente(file_env, {}), "su POSIX la chiave cambia")

    return "letti da un ambiente dato, derivati coerenti, nessun nome di modulo che li nasconda"


def impostazioni_a_runtime() -> str:
    """I modelli sono un oggetto costruito quando serve, non nomi riletti a meta' strada.

    Un `Impostazioni` costruito a mano deve arrivare fino ai costruttori e
    all'avviso sul cloud, e nessuna firma puo' fissare `MODO_PREDEFINITO` come
    default.
    """
    import inspect
    from dataclasses import fields

    from ares.agent import prompts, runtime
    from ares.config import NUM_CTX_ESTRAZIONE, Impostazioni, leggi_impostazioni, leggi_politica

    mia = Impostazioni(
        principale="prova-conversazione:cloud",
        apprendimento="prova-estrazione:9b",
        embedder="prova-embedder",
        embedder_dimensioni=64,
        host="http://127.0.0.1:9",
        keep_alive="1m",
        num_ctx=4096,
        temperatura=0.1,
        temperatura_apprendimento=0.05,
        think=False,
        think_apprendimento=False,
    )

    # Il tipo e' immutabile, e la modalita' non ne fa parte: e' gia' un
    # parametro a ogni confine, e ficcarla qui la farebbe sembrare una
    # proprieta' del modello.
    esigi("modo" not in {campo.name for campo in fields(Impostazioni)}, "la modalita' e' finita dentro Impostazioni")
    try:
        mia.num_ctx = 8192  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("Impostazioni non e' immutabile")

    # I due costruttori seguono l'oggetto, non il modulo: nome, host, opzioni
    # e pensiero vengono tutti da li'.
    conversazione = runtime.build_chat_model(mia)
    esigi(
        conversazione.id == mia.principale and conversazione.host == mia.host,
        "build_chat_model non usa le impostazioni ricevute",
    )
    esigi(
        conversazione.options == mia.opzioni and conversazione.options["num_ctx"] == 4096,
        "build_chat_model non usa il contesto ricevuto: " + repr(conversazione.options),
    )
    esigi(
        conversazione.request_params == {"think": False},
        "il pensiero della conversazione non viene dalle impostazioni",
    )
    estrazione = runtime.build_learning_model(mia)
    esigi(
        estrazione.id == mia.apprendimento and estrazione.host == mia.host,
        "build_learning_model non usa le impostazioni ricevute",
    )
    # Due modelli diversi: l'estrazione scende al contesto dell'estrazione, e
    # la regola sta sul tipo perche' dipende dalla coppia. Mai sopra quello
    # della conversazione: qui 4096 resta 4096.
    esigi(
        mia.num_ctx_apprendimento == mia.num_ctx,
        "l'estrazione chiede piu' contesto della conversazione: " + str(mia.num_ctx_apprendimento),
    )
    ampia = replace(mia, num_ctx=262144)
    esigi(
        ampia.num_ctx_apprendimento == NUM_CTX_ESTRAZIONE,
        "con due modelli diversi l'estrazione non usa il contesto suo",
    )
    esigi(
        runtime.build_learning_model(ampia).options == {**ampia.opzioni_apprendimento, "num_ctx": NUM_CTX_ESTRAZIONE}
        and ampia.opzioni_apprendimento["temperature"] == 0.05,
        "build_learning_model non usa il contesto dell'estrazione: " + repr(estrazione.options),
    )
    esigi(estrazione.request_params == {"think": False}, "il pensiero dell'estrazione non viene dalle impostazioni")
    # Stesso modello in entrambi i ruoli: il contesto torna uno solo, o
    # Ollama riavvierebbe il runner a ogni passaggio perdendo la cache.
    sola = replace(mia, apprendimento=mia.principale)
    esigi(sola.num_ctx_apprendimento == sola.num_ctx, "con un modello solo l'estrazione cambia contesto")

    # L'avviso segue l'oggetto e non il `.env` di questa macchina, quindi la
    # prova vale ovunque: locale tace, cloud dice cosa esce.
    locale = replace(mia, principale="prova-conversazione:9b", apprendimento="prova-estrazione:9b")
    esigi(locale.avviso_cloud() == [], "l'avviso compare con modelli locali costruiti a mano")
    esigi("memorie" in " ".join(mia.avviso_cloud()), "con l'estrazione cloud l'avviso non nomina le memorie")
    entrambi = replace(mia, apprendimento="prova-estrazione:cloud")
    esigi(
        "Solo l'embedding resta locale" in " ".join(entrambi.avviso_cloud()),
        "con due ruoli cloud non resta solo l'embedding",
    )
    # La descrizione segue le impostazioni in entrambe le direzioni. Si
    # guarda cosa attraversa - un servizio remoto, o niente - e non il
    # dominio: un confronto su una sottostringa non direbbe nulla di piu'.
    politica = leggi_politica()
    descrizione_cloud = prompts.descrizione(mia, politica)
    esigi(
        "servizio remoto" in descrizione_cloud and "esce di qui" not in descrizione_cloud,
        "con la conversazione cloud la descrizione non parla di servizio remoto",
    )
    esigi(
        "esce di qui" in prompts.descrizione(locale, politica),
        "con modelli locali la descrizione non promette che nulla esce",
    )

    # Nessuna firma deve piu' fotografare `MODO_PREDEFINITO` all'import: chi lo
    # cambia dopo deve essere ascoltato, come lo e' gia' in build_workspace.
    for funzione in (
        build_assistant,
        prompts.istruzioni_sull_ambiente,
        prompts.istruzioni_sugli_strumenti,
        prompts.istruzioni_senza_terminale,
    ):
        default = inspect.signature(funzione).parameters["modo"].default
        esigi(default is None, funzione.__name__ + " fotografa ancora MODO_PREDEFINITO nella firma")

    # I nomi sorgente restano, come `AMBIENTE` per i percorsi; quello che non
    # deve restare e' un nome derivato che qualcuno possa leggere al posto
    # dell'oggetto.
    for nome in ("OLLAMA_OPTIONS", "LEARNING_OPTIONS", "LEARNING_NUM_CTX"):
        esigi(not hasattr(config, nome), "config espone ancora " + nome + ": e' un canale invisibile")
    esigi(isinstance(leggi_impostazioni(), Impostazioni), "leggi_impostazioni non restituisce un Impostazioni")

    # In parallelo solo con l'estrazione cloud: in locale Ollama serve una
    # richiesta alla volta.
    esigi(mia.estrazione_in_parallelo is False, "l'estrazione locale va in parallelo")
    esigi(entrambi.estrazione_in_parallelo is True, "l'estrazione cloud resta in serie")
    esigi(
        replace(mia, principale="prova:cloud", apprendimento="prova:9b").estrazione_in_parallelo is False,
        "la conversazione cloud decide per l'estrazione",
    )

    # Il contesto dal `.env`: un intero da NUM_CTX_MINIMO in su, il default
    # se manca, un rifiuto per tutto il resto.
    esigi(config.leggi_num_ctx(None) == 131072 and config.leggi_num_ctx("  ") == 131072, "il default non e' 128k")
    esigi(config.leggi_num_ctx(" 65536 ") == 65536, "ARES_NUM_CTX non viene letto")
    esigi(config.leggi_num_ctx(str(config.NUM_CTX_MINIMO)) == config.NUM_CTX_MINIMO, "il minimo e' rifiutato")
    for valore in ("abc", "4096", str(config.NUM_CTX_MINIMO - 1), "-1", "1e5"):
        try:
            config.leggi_num_ctx(valore)
        except ValueError as errore:
            esigi("ARES_NUM_CTX" in str(errore), "il rifiuto non nomina la variabile: " + str(errore))
        else:
            raise AssertionError("ARES_NUM_CTX=" + valore + " accettato")
    # All'avvio un valore sbagliato e' una riga e un'uscita 1, non un traceback.
    rifiutato = subprocess.run(
        [sys.executable, "-c", "import ares.config"],
        env={**os.environ, "ARES_NUM_CTX": "abc"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    esigi(
        rifiutato.returncode == 1 and "ARES_NUM_CTX" in rifiutato.stderr and "Traceback" not in rifiutato.stderr,
        "un ARES_NUM_CTX sbagliato non ferma l'avvio con una riga: " + rifiutato.stderr,
    )
    letto = subprocess.run(
        [sys.executable, "-c", "from ares import config; print(config.leggi_impostazioni().num_ctx)"],
        env={**os.environ, "ARES_NUM_CTX": "65536"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    esigi(letto.stdout.strip() == "65536", "ARES_NUM_CTX non arriva alle impostazioni: " + letto.stdout + letto.stderr)

    # Il campionamento dal `.env`: i default di Ollama se manca, il valore se
    # c'e', un rifiuto che nomina la variabile per cio' che e' fuori intervallo.
    predefiniti = {
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 40,
        "min_p": 0.0,
        "repeat_penalty": 1.1,
        "presence_penalty": 0.0,
    }
    esigi(config.leggi_campionamento({}) == predefiniti, "i default del campionamento non sono quelli di Ollama")
    scheda = config.leggi_campionamento({"ARES_TEMPERATURE": "1", "ARES_TOP_K": " 20 ", "ARES_PRESENCE_PENALTY": "1.5"})
    esigi(
        scheda["temperature"] == 1.0
        and scheda["top_k"] == 20
        and isinstance(scheda["top_k"], int)
        and scheda["presence_penalty"] == 1.5
        and scheda["top_p"] == 0.9,
        "il campionamento dal .env non viene letto: " + str(scheda),
    )
    for variabile, valore in (
        ("ARES_TEMPERATURE", "abc"),
        ("ARES_TEMPERATURE", "2.5"),
        ("ARES_TOP_P", "1.5"),
        ("ARES_TOP_K", "2.5"),
        ("ARES_TOP_K", "-1"),
        ("ARES_MIN_P", "-0.1"),
        ("ARES_REPEAT_PENALTY", "0"),
        ("ARES_PRESENCE_PENALTY", "3"),
    ):
        try:
            config.leggi_campionamento({variabile: valore})
        except ValueError as errore:
            esigi(variabile in str(errore), "il rifiuto non nomina la variabile: " + str(errore))
        else:
            raise AssertionError(variabile + "=" + valore + " accettato")
    rifiutato = subprocess.run(
        [sys.executable, "-c", "import ares.config"],
        env={**os.environ, "ARES_TOP_K": "abc"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    esigi(
        rifiutato.returncode == 1 and "ARES_TOP_K" in rifiutato.stderr and "Traceback" not in rifiutato.stderr,
        "un ARES_TOP_K sbagliato non ferma l'avvio con una riga: " + rifiutato.stderr,
    )
    # Le opzioni mandate a Ollama portano tutto il campionamento, e
    # l'estrazione condivide tutto tranne la temperatura, che ha sua.
    esigi(
        set(mia.opzioni) == {"num_ctx", *predefiniti},
        "le opzioni della conversazione non portano il campionamento: " + str(mia.opzioni),
    )
    comuni = lambda opzioni: {k: v for k, v in opzioni.items() if k not in ("num_ctx", "temperature")}  # noqa: E731
    esigi(
        comuni(mia.opzioni) == comuni(mia.opzioni_apprendimento)
        and mia.opzioni_apprendimento["temperature"] == mia.temperatura_apprendimento
        and mia.opzioni["temperature"] == mia.temperatura,
        "l'estrazione non condivide il campionamento, o ha perso la sua temperatura",
    )
    letto = subprocess.run(
        [sys.executable, "-c", "from ares import config; print(config.leggi_impostazioni().opzioni['top_k'])"],
        env={**os.environ, "ARES_TOP_K": "20"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    esigi(letto.stdout.strip() == "20", "ARES_TOP_K non arriva alle opzioni: " + letto.stdout + letto.stderr)

    return (
        "modelli dall'oggetto, contesto e campionamento dal .env, parallelo solo in cloud,"
        " nessun default fotografato all'import"
    )


def politica_a_runtime() -> str:
    """La politica viaggia come oggetto, e il prompt descrive quella che c'e'.

    Una fotografia non vede cambiamenti successivi; i gruppi sono immutabili e
    non contengono cio' che non e' politica (modalita', offload, formati); un
    prompt composto con una politica scelta a mano descrive quella.
    """
    from dataclasses import fields

    from ares.agent import prompts
    from ares.config import Politica, leggi_politica

    mia = leggi_politica()
    esigi(isinstance(mia, Politica), "leggi_politica non restituisce un Politica")

    # I quattro gruppi sono un vocabolario chiuso, e i loro nomi dicono cosa
    # non e' politica: `modo` e' gia' un parametro a ogni confine, l'offload e
    # i formati sono configurazione dell'indice e del client.
    esigi(
        [campo.name for campo in fields(Politica)] == ["apprendimento", "cronologia", "workspace", "mostra"],
        "Politica non e' fatta dei quattro gruppi dichiarati",
    )
    fuori = {"modo", "offload_tool_results", "offload", "datetime_format", "cronologia_righe", "sessioni_protette"}
    esigi(
        not fuori & {campo.name for campo in fields(Politica)},
        "Politica ha assorbito configurazione che non le appartiene",
    )
    try:
        mia.mostra.metriche = not mia.mostra.metriche  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("Mostra non e' immutabile")

    # Le proprieta' derivate seguono i campi, non una seconda lista da tenere
    # allineata: sono cio' che il prompt usa al posto dei tre `or`.
    esigi(
        mia.apprendimento.automatici
        == (mia.apprendimento.profilo or mia.apprendimento.memorie or mia.apprendimento.contesto),
        "`automatici` non deriva dai tre store ALWAYS",
    )
    esigi(
        mia.apprendimento.agentici == (mia.apprendimento.entita or mia.apprendimento.intuizioni),
        "`agentici` non deriva dai due store agentici",
    )

    # La fotografia e' un'istantanea: un flag cambiato dopo non la tocca, e
    # una fotografia nuova lo vede. E' l'opposto di un nome di modulo.
    with patch.object(config, "MOSTRA_APPRENDIMENTI", not mia.mostra.apprendimenti):
        dentro = leggi_politica()
    esigi(
        dentro.mostra.apprendimenti != mia.mostra.apprendimenti,
        "un flag cambiato non entra in una fotografia nuova",
    )
    esigi(leggi_politica() == mia, "la fotografia non torna com'era dopo il patch")

    # Un prompt composto su una politica scelta a mano descrive quella: con
    # gli store tutti spenti non promette estrazioni automatiche, e senza eco
    # non dice che le scritture compaiono sotto la risposta.
    muta = replace(
        mia,
        apprendimento=replace(
            mia.apprendimento, profilo=False, memorie=False, contesto=False, entita=False, intuizioni=False
        ),
        cronologia=replace(mia.cronologia, sessioni_passate=False, cronologia_chat=False),
        mostra=replace(mia.mostra, apprendimenti=True, conferma_apprendimenti=True),
    )
    memoria = " ".join(prompts.istruzioni_sulla_memoria(politica=muta, interattivo=True))
    esigi("Si aggiornano da soli" not in memoria, "il prompt promette store automatici che la politica non ha")
    esigi("Si aggiornano solo con gli strumenti" not in memoria, "il prompt promette store agentici spenti")
    esigi(
        "compare sotto la risposta" not in memoria,
        "con nessun profilo ne' memoria da mostrare il prompt promette comunque l'eco",
    )

    # I due store che l'eco riguarda, accesi: la frase segue `Mostra`, e la
    # conferma cambia il verbo. Con l'eco spenta la frase non compare affatto.
    con_eco = replace(mia, apprendimento=replace(mia.apprendimento, profilo=True, memorie=True))
    con_rifiuto = " ".join(
        prompts.istruzioni_sulla_memoria(
            politica=replace(con_eco, mostra=replace(con_eco.mostra, apprendimenti=True, conferma_apprendimenti=True)),
            interattivo=True,
        )
    )
    senza_rifiuto = " ".join(
        prompts.istruzioni_sulla_memoria(
            politica=replace(con_eco, mostra=replace(con_eco.mostra, apprendimenti=True, conferma_apprendimenti=False)),
            interattivo=True,
        )
    )
    spenta = " ".join(
        prompts.istruzioni_sulla_memoria(
            politica=replace(con_eco, mostra=replace(con_eco.mostra, apprendimenti=False)), interattivo=True
        )
    )
    esigi("puo' rifiutarlo" in con_rifiuto, "con la conferma accesa il prompt non offre il rifiuto")
    esigi(
        "lo legge." in senza_rifiuto and "puo' rifiutarlo" not in senza_rifiuto,
        "con la conferma spenta il prompt minaccia un rifiuto che non c'e'",
    )
    esigi("compare sotto la risposta" not in spenta, "con l'eco spenta il prompt dice che le scritture si vedono")

    # Gli strumenti della cronologia: nominati con la politica accesa, taciuti
    # con quella spenta. E' l'invito a chiamare il vuoto che questa prova
    # impedisce.
    acceso = " ".join(prompts.istruzioni_sugli_strumenti(None, politica=mia, interattivo=True))
    spento = " ".join(prompts.istruzioni_sugli_strumenti(None, politica=muta, interattivo=True))
    esigi("read_past_session" in acceso and "get_chat_history" in acceso, "il prompt non nomina la cronologia accesa")
    esigi(
        "read_past_session" not in spento and "get_chat_history" not in spento,
        "il prompt nomina strumenti della cronologia che la politica spegne",
    )

    # E l'oggetto arriva fino all'agente: con il workspace spento la sessione
    # non porta una cartella, e la ricerca fra le conversazioni non parte.
    senza_spazio = replace(muta, workspace=replace(mia.workspace, attivo=False))
    agent = build_assistant(PERCORSI, IMPOSTAZIONI, senza_spazio, utente=Utente.da_grezzo("prova-politica"))
    esigi(agent.metadata is None, "con il workspace spento l'agente registra comunque una cartella")
    esigi(
        agent.search_past_sessions is False,
        "la ricerca fra le conversazioni non segue la politica ricevuta",
    )
    esigi(
        agent.num_history_runs == senza_spazio.cronologia.turni,
        "i turni di cronologia non seguono la politica ricevuta",
    )

    return "fotografia, immutabilita', campi esclusi e prompt coerente con la politica ricevuta"


def import_senza_effetti() -> str:
    """Importare `config` non tocca il disco.

    La creazione dello stato e' esplicita: un `mkdir` rimesso nel corpo del
    modulo non romperebbe niente, e solo questa prova lo vedrebbe. Gira in un
    processo separato, perche' qui `config` e' gia' importato.
    """
    prova = Path(tempfile.mkdtemp(prefix="ares-import-"))
    ambiente = os.environ.copy()
    ambiente["ARES_TMP"] = str(prova / "stato")
    codice = (
        "import os; from ares import config;"
        "p = config.leggi_percorsi();"
        "print('dopo-import', os.path.exists(p.stato));"
        "config.prepara_archivio(p);"
        "print('dopo-prepara', os.path.exists(p.stato))"
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


def lancedb_silenzioso() -> str:
    """LanceDB non stampa i propri WARN in chat, salvo richiesta.

    Il primo indice creato su uno stato nuovo faceva comparire una riga
    `WARN lance::dataset::write::insert` sopra la chat. Processo separato: il
    logger nativo legge LANCEDB_LOG una volta sola, all'import.
    """
    prova = Path(tempfile.mkdtemp(prefix="ares-lancedb-"))
    codice = (
        "import sys, ares, lancedb, pyarrow as pa;"
        "lancedb.connect(sys.argv[1]).create_table("
        "'t', schema=pa.schema([('a', pa.int64())]), mode='overwrite', exist_ok=True)"
    )

    def crea(indice: str, **variabili: str) -> subprocess.CompletedProcess:
        ambiente = {k: v for k, v in os.environ.items() if k != "LANCEDB_LOG"} | variabili
        return subprocess.run(
            [sys.executable, "-c", codice, str(prova / indice)],
            cwd=config.BASE_DIR,
            env=ambiente,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    try:
        zitto = crea("predefinito")
        esigi(zitto.returncode == 0, "la creazione dell'indice e' fallita: " + zitto.stderr[-400:])
        esigi("WARN" not in zitto.stderr, "LanceDB stampa i propri avvisi: " + zitto.stderr[-400:])
        # Il controllo e' che ci sia davvero un avviso da zittire, e che chi
        # lo chiede lo riceva.
        chiesto = crea("richiesto", LANCEDB_LOG="warn")
        esigi("No existing dataset" in chiesto.stderr, "LANCEDB_LOG=warn non vince: " + chiesto.stderr[-400:])
    finally:
        shutil.rmtree(prova, ignore_errors=True)
    return "nessun WARN di LanceDB per default, LANCEDB_LOG dell'utente rispettato"


def archivio_vero_intatto(prima: list) -> str:
    """La prova non ha letto ne' scritto l'archivio vero."""
    esigi(
        not PERCORSI.db_file.startswith(str(PERCORSI.home / "stato")),
        "l'archivio della prova coincide con quello vero: " + PERCORSI.db_file,
    )
    esigi(stato_archivio_reale() == prima, "l'archivio vero e' cambiato durante la prova")
    return str(len(prima)) + " file nello stato vero, invariati"


# ---------------------------------------------------------------------------
# Esecuzione
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica il cablaggio dell'agente senza chiamare il modello")
    parser.add_argument("--user", default="prova", help="Identificativo con cui seminare l'archivio")
    parser.add_argument("--session", default="prova", help="Sessione con cui seminare l'archivio")
    parser.add_argument("--conserva", action="store_true", help="non cancella l'archivio della prova")
    args = parser.parse_args()

    print("Archivio della prova:", PERCORSI.db_file)
    print("Utente:", args.user, "  Sessione:", args.session)
    print()

    avvio = time.monotonic()
    reale_prima = stato_archivio_reale()

    try:
        agent = build_assistant(
            PERCORSI, IMPOSTAZIONI, POLITICA, utente=Utente.da_grezzo(args.user), session_id=args.session
        )
    except Exception as errore:
        print("FALLITO  costruzione -", type(errore).__name__ + ":", errore)
        return 1
    lm = agent.learning_machine
    fs = build_filesystem(PERCORSI, Utente.da_grezzo(args.user))
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
            ("ambiente nel prompt ", lambda: ambiente_nel_prompt(agent, args.user, args.session)),
            ("strumenti           ", lambda: strumenti(agent, args.user)),
            ("prompt in italiano  ", lambda: prompt_in_italiano(agent, args.user, args.session)),
            ("struttura del prompt", lambda: struttura_del_prompt(agent, args.user, args.session)),
            ("istruzioni modalita'", istruzioni_fuori_modalita),
            ("modalita            ", modalita),
            ("colpo singolo       ", lambda: colpo_singolo(args.user, args.session)),
            ("prompt e capacita'  ", prompt_e_capacita),
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
            ("comandi su archivio ", lambda: comandi_sull_archivio(agent, args.user, args.session)),
            ("file isolati        ", lambda: file_isolati(args.user)),
            ("indice vettoriale   ", lambda: indice_vettoriale(lm)),
            ("archivio privato    ", lambda: archivio_privato()),
            ("percorsi a runtime  ", percorsi_a_runtime),
            ("impostazioni a runtime", impostazioni_a_runtime),
            ("politica a runtime  ", politica_a_runtime),
            ("import senza effetti", lambda: import_senza_effetti()),
            ("lancedb silenzioso  ", lancedb_silenzioso),
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
        pulisci(RADICE_PROVA)
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
