"""
Contratto con Agno: estrazione, conferma, retry e limiti dichiarati
===================================================================
Uso:
    .venv/bin/python tests/agno_contract_test.py

Cinque cose Ares le da' per vere di Agno, e nessuna prova le chiedeva ad
Agno.

La prima: l'apprendimento avviene una volta per turno, sul run completo.
Agno avvia `LearningMachine.process` in un thread prima ancora di chiamare
il modello, su una fotografia dei messaggi; Ares lo azzera in
`AresLearningMachine.process` e rifa' l'estrazione nel post-hook, che Agno
esegue solo quando il run non e' in pausa. Se una minor di Agno spostasse
la chiamata anticipata su un altro nome, o eseguisse i post-hook anche su
un run in pausa, si avrebbero due estrazioni per turno o una sul run
troncato, e nessuna prova se ne accorgerebbe: `smoke` verifica il post-hook
con un run costruito a mano, non con Agno che lo chiama.

La seconda: il ciclo `run -> pausa -> continue_run` di `turn_core` combacia
con la firma e il comportamento di Agno. `chat turno` lo prova con un
`run_turn_cycle` finto; qui il ciclo e' quello vero, con un `Agent.run`
reale e uno strumento del workspace che chiede conferma. Cio' che si
afferma e' l'effetto: il file viene cancellato dopo la conferma e non
prima, resta al suo posto dopo un rifiuto, e in entrambi i casi il run
riprende e finisce.

La terza: il retry di `AresSessionContextStore`. E' la seconda delle tre
superfici di Agno che Ares sovrascrive, e finora la provava soltanto
`learning_reliability_test.py`, che vuole Ollama e quindi in CI non gira
mai: il ramo piu' delicato dell'apprendimento era verificato solo a mano.
Il retry si regge su tre fatti di Agno - `extract_and_save` esiste con
quel nome, `context_updated` viene azzerato all'inizio e acceso solo se
il modello ha eseguito uno strumento, `aextract_and_save` e' il gemello
asincrono, che e' codice diverso e non lo stesso corpo con un `await` davanti -
e se uno cadesse Ares ripeterebbe all'infinito o non ripeterebbe mai, in
silenzio. Qui il modello e' di nuovo un copione, cosi' il caso "fallisce e poi
recupera" e' deterministico invece che sperato, e i tre casi - primo colpo,
tetto, recupero - si attraversano su entrambi i percorsi. Nello stesso punto si
attraversa l'`aprocess` che Ares azzera per il percorso asincrono: Ares non usa
`arun`, quindi quella riga nessun'altra prova la esegue.
La terza superficie, il flag `stop_after_tool_call` su profilo e memorie, non
si controlla qui: `learning_cost_test.py` la esercita con un modello finto e
conta le chiamate, che e' una prova piu' diretta di un controllo di firma.

La quarta: profilo e memorie non sono confermabili. `SECURITY.md` e
`docs/architecture.md` dichiarano che la memoria durevole si scrive senza
passare da una conferma, e la ragione non e' una scelta di Ares: in Agno
`UserProfileStore` e `UserMemoryStore` rifiutano PROPOSE, e HITL e'
"reserved for future use" su ogni store. E' un limite del framework, e un
limite dichiarato va sorvegliato come un'invariante: il giorno in cui Agno
lo togliesse, questa prova diventa rossa e la documentazione va riscritta
invece di restare vera per abitudine.

La quinta: la versione di Agno che i documenti dichiarano e' quella
installata. Il numero sta a mano in piu' posti - il badge del README, la
roadmap, `docs/agno.md`, il commento di `AresLearningMachine` - e la 3.0.5
era rimasta in uno di essi col lock gia' alla 3.0.9. Un numero vecchio non
fa fallire niente: e' una pagina che descrive un altro programma. Qui
l'installato e' il metro, e la prova dice quali file allineare.

Niente modello e niente rete: il modello e' uno script che emette le tool
call decise dalla prova, come in `session_retention_test.py`. Nei primi due
controlli gli store di apprendimento sono spenti, e l'estrazione e' un
passaggio a vuoto: e' il passaggio che si conta, non cio' che scriverebbe.
Il terzo store lo costruisce invece davvero, perche' li' cio' che si guarda
e' proprio se ha scritto.
"""

import asyncio
import re
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path
from typing import Any
from unittest.mock import patch

from _comune import esegui, esigi, prepara_ambiente, pulisci

# I percorsi vanno scelti prima di importare config, che li legge una volta
# sola all'import.
RADICE_PROVA = prepara_ambiente("agno-contract-test")
RADICE = Path(__file__).resolve().parent.parent

from _doppi import ModelloACopione, tool_call  # noqa: E402
from agno.learn import (  # noqa: E402
    LearningMachine,
    LearningMode,
    UserMemoryConfig,
    UserProfileConfig,
)
from agno.learn.stores import UserMemoryStore, UserProfileStore  # noqa: E402
from agno.models.message import Message, MessageMetrics  # noqa: E402
from agno.models.response import ModelResponse  # noqa: E402

from ares import config  # noqa: E402

# I percorsi e le impostazioni della prova, letti una volta dopo
# `prepara_ambiente`: `config` non tiene piu' nomi propri per nessuno dei due,
# quindi la prova se li porta dietro e li passa a chi ne ha bisogno. La
# politica si legge invece in `main()`, dopo aver spento gli store: quella di
# questo import avrebbe ancora tutti gli apprendimenti accesi.
PERCORSI = config.leggi_percorsi()
IMPOSTAZIONI = config.leggi_impostazioni()
from ares.agent.assistant import build_assistant  # noqa: E402
from ares.agent.learning import build_session_context_store  # noqa: E402
from ares.agent.runtime import build_db  # noqa: E402
from ares.agent.turn_core import TurnEventKind, run_turn_cycle  # noqa: E402
from ares.state.identita import Utente  # noqa: E402

UTENTE = "prova-contratto"
SESSIONE = "contratto"
NOME_FILE = "da-cancellare.txt"


def copione_cancellazione() -> list[list[dict[str, Any]]]:
    """Legge il file e poi chiede di cancellarlo: la lettura e' obbligatoria.

    `WORKSPACE_READ_BEFORE_WRITE` vale anche per la cancellazione, e passare
    da una lettura libera a una scrittura a conferma nello stesso turno e'
    la sequenza che un turno vero attraversa.
    """
    prefisso = config.WORKSPACE_PREFIX
    return [
        [tool_call(prefisso + "read_file", path=NOME_FILE)],
        [tool_call(prefisso + "delete_file", path=NOME_FILE)],
    ]


class ClienteFinto:
    """Il client di `run_turn_cycle`: raccoglie gli eventi e decide alla pausa.

    `decisione` e' cio' che fa alla pausa: conferma o rifiuta ogni requisito.
    Alla pausa registra anche se il file esiste ancora, perche' e' l'unico
    momento in cui si puo' affermare che lo strumento non e' stato eseguito
    prima della decisione.
    """

    def __init__(self, decisione: str, file: Path) -> None:
        self.decisione = decisione
        self.file = file
        self.eventi: list[TurnEventKind] = []
        self.pause: list[Any] = []
        self.file_presente_alla_pausa: list[bool] = []
        # Fotografie prese alla pausa: `continue_run` prosegue sullo stesso
        # `RunOutput` e lo riempie, quindi guardarlo a turno finito direbbe
        # com'e' finito, non com'era quando si e' fermato.
        self.run_id_alla_pausa: list[str] = []
        self.esiti_tool_alla_pausa: list[list[str]] = []

    def on_event(self, evento) -> None:
        self.eventi.append(evento.kind)

    def resolve_pause(self, risposta) -> int:
        self.pause.append(risposta)
        self.file_presente_alla_pausa.append(self.file.exists())
        self.run_id_alla_pausa.append(str(risposta.run_id))
        self.esiti_tool_alla_pausa.append(contenuti_tool(risposta.messages or []))
        risolti = 0
        for requisito in risposta.active_requirements or []:
            if not requisito.needs_confirmation:
                continue
            if self.decisione == "conferma":
                requisito.confirm()
            else:
                requisito.reject("non ora")
            risolti += 1
        return risolti


class ContatoreEstrazioni:
    """Conta le estrazioni vere e quelle anticipate, e conserva i messaggi.

    L'estrazione vera e' `LearningMachine.process` di Agno, che Ares chiama
    solo da `process_completed_run`: si intercetta sulla classe base, cosi'
    la chiamata anticipata - che arriva all'override di Ares - non ci passa.
    Quella anticipata si conta sull'istanza, perche' e' li' che Agno la
    cerca: se sparisse dal framework, l'override di Ares sarebbe codice
    morto e la prova lo direbbe.

    Il gemello asincrono si conta allo stesso modo, su `vere_async` e sulla
    classe base: Ares non usa `arun`, quindi l'unico modo di sapere che la
    sua `aprocess` non e' codice morto - e che non estrae - e' attraversarla.
    """

    def __init__(self, macchina) -> None:
        self.vere: list[list[Any]] = []
        self.vere_async: list[list[Any]] = []
        self.anticipate = 0
        self.macchina = macchina
        self._originale = LearningMachine.process
        self._originale_async = LearningMachine.aprocess

    def __enter__(self):
        contatore = self

        def process_vero(istanza, *args, **kwargs):
            contatore.vere.append(list(kwargs.get("messages") or []))
            return contatore._originale(istanza, *args, **kwargs)

        async def aprocess_vero(istanza, *args, **kwargs):
            contatore.vere_async.append(list(kwargs.get("messages") or []))
            return await contatore._originale_async(istanza, *args, **kwargs)

        def process_anticipato(*args, **kwargs):
            contatore.anticipate += 1
            return None

        self._patch = patch.object(LearningMachine, "process", process_vero)
        self._patch_async = patch.object(LearningMachine, "aprocess", aprocess_vero)
        self._patch.__enter__()
        self._patch_async.__enter__()
        self.macchina.process = process_anticipato
        return self

    def __exit__(self, *_):
        self._patch.__exit__(None, None, None)
        self._patch_async.__exit__(None, None, None)
        del self.macchina.process


def agente():
    costruito = build_assistant(PERCORSI, IMPOSTAZIONI, POLITICA, utente=Utente.da_grezzo(UTENTE), session_id=SESSIONE)
    costruito.model = ModelloACopione("scripted-contract", copione_cancellazione())
    return costruito


def turno(agent, decisione: str, file: Path):
    file.write_text("da cancellare dopo conferma\n")
    cliente = ClienteFinto(decisione, file)
    risposta = run_turn_cycle(
        agent, "cancella " + NOME_FILE, on_event=cliente.on_event, resolve_pause=cliente.resolve_pause
    )
    return cliente, risposta


def ruoli(messaggi) -> list[str]:
    return [str(getattr(messaggio, "role", "")) for messaggio in messaggi]


def contenuti_tool(messaggi) -> list[str]:
    return [str(messaggio.content) for messaggio in messaggi if getattr(messaggio, "role", "") == "tool"]


def estrazione_singola() -> str:
    """Un turno con pausa produce una sola estrazione, sul run completo."""
    agent = agente()
    file = PERCORSI.lavoro / NOME_FILE
    with ContatoreEstrazioni(agent.learning_machine) as contatore:
        cliente, risposta = turno(agent, "conferma", file)
    esigi(risposta is not None and not risposta.is_paused, "il turno non e' arrivato in fondo")
    esigi(len(cliente.pause) == 1, "il turno non e' passato da una pausa: " + str(len(cliente.pause)))
    esigi(contatore.anticipate >= 1, "Agno non chiama piu' la `process` anticipata: l'override di Ares e' morto")
    esigi(len(contatore.vere) == 1, "estrazioni vere per un turno: " + str(len(contatore.vere)) + ", attese 1")

    # Il run in pausa non contiene l'esito della cancellazione: un'estrazione
    # fatta li' avrebbe imparato da un turno a meta'. Quella vera lo contiene,
    # insieme alla risposta finale.
    esigi(
        not any(testo.startswith("Deleted") for testo in cliente.esiti_tool_alla_pausa[0]),
        "il run in pausa contiene gia' l'esito dello strumento a conferma",
    )
    messaggi_estratti = contatore.vere[0]
    esiti = contenuti_tool(messaggi_estratti)
    esigi(
        any(testo.startswith("Deleted") for testo in esiti), "l'estrazione non vede l'esito dello strumento confermato"
    )
    esigi(
        messaggi_estratti and getattr(messaggi_estratti[-1], "content", None) == "fatto",
        "l'estrazione non finisce con la risposta finale: " + str(ruoli(messaggi_estratti)),
    )
    esigi(
        len(messaggi_estratti) == len(risposta.messages or []),
        "l'estrazione riceve un run diverso da quello restituito",
    )

    # Il gemello asincrono, fuori dal contatore di sopra: qui si attraversa la
    # riga di Ares, non quella di Agno. Ares non usa `arun`, quindi senza
    # questa chiamata l'override asincrono resterebbe l'unica riga mai
    # eseguita di learning.py, e un Agno che estraesse dal percorso asincrono
    # imparerebbe da un run a meta' senza che la prova sincrona se ne accorga.
    with ContatoreEstrazioni(agent.learning_machine) as contatore_asincrono:
        asyncio.run(agent.learning_machine.aprocess(messages=[], user_id=UTENTE, session_id=SESSIONE))
    esigi(
        not contatore_asincrono.vere_async,
        "la `aprocess` di Ares non azzera l'estrazione asincrona: "
        + str(len(contatore_asincrono.vere_async))
        + " estrazioni",
    )
    return (
        "1 estrazione su "
        + str(len(messaggi_estratti))
        + " messaggi, "
        + str(contatore.anticipate)
        + " anticipata azzerata, asincrona ferma"
    )


def ciclo_hitl() -> str:
    """`run -> pausa -> continue_run` sullo stesso run, con conferma e con rifiuto."""
    agent = agente()
    file = PERCORSI.lavoro / NOME_FILE

    cliente, risposta = turno(agent, "conferma", file)
    esigi(risposta is not None and not risposta.is_paused, "il run confermato e' ancora in pausa")
    esigi(cliente.file_presente_alla_pausa == [True], "lo strumento e' stato eseguito prima della conferma")
    esigi(not file.exists(), "il file c'e' ancora dopo la conferma")
    esigi(risposta.run_id == cliente.run_id_alla_pausa[0], "continue_run ha aperto un run diverso da quello in pausa")
    esigi(
        any(testo.startswith("Deleted") for testo in contenuti_tool(risposta.messages or [])),
        "il risultato dello strumento confermato non e' nel run",
    )
    eventi = cliente.eventi
    esigi(TurnEventKind.RUN_PAUSED in eventi, "nessun evento di pausa: " + str(eventi))
    esigi(eventi.count(TurnEventKind.PROCESSING_STARTED) == 2, "il ciclo non ha ripreso esattamente una volta")
    esigi(
        eventi.index(TurnEventKind.RUN_PAUSED) < eventi.index(TurnEventKind.RUN_COMPLETED),
        "il run risulta completato prima della pausa",
    )
    esigi(eventi.count(TurnEventKind.TOOL_COMPLETED) == 2, "attesi due strumenti completati: " + str(eventi))
    esigi(agent.model.chiamate == 3, "chiamate al modello: " + str(agent.model.chiamate) + ", attese 3")

    agent.model = ModelloACopione("scripted-contract", copione_cancellazione())
    cliente, risposta = turno(agent, "rifiuto", file)
    esigi(risposta is not None and not risposta.is_paused, "il run rifiutato e' ancora in pausa")
    esigi(file.exists(), "il file e' stato cancellato nonostante il rifiuto")
    esigi(risposta.run_id == cliente.run_id_alla_pausa[0], "dopo il rifiuto continue_run ha aperto un run diverso")
    esigi(
        any("non ora" in testo for testo in contenuti_tool(risposta.messages or [])),
        "il motivo del rifiuto non arriva al modello: " + str(contenuti_tool(risposta.messages or [])),
    )
    return "conferma cancella, rifiuto conserva, stesso run_id e motivo consegnato"


class ModelloContesto(ModelloACopione):
    """Salva il contesto soltanto ai tentativi elencati; agli altri tace.

    `SessionContextStore.extract_and_save` fa `model_copy = deepcopy(self.model)`
    a ogni tentativo. Un contatore sull'istanza vivrebbe percio' una vita sola
    per tentativo e ogni giro ripeterebbe il primo: il caso "fallisce e poi
    recupera" non sarebbe esprimibile. `__deepcopy__` restituisce quindi se
    stesso, e la copia condivide il contatore con l'originale.

    Tacere significa rispondere senza tool call: lo store accende
    `context_updated` solo se il modello ha *eseguito* uno strumento, quindi
    una risposta di solo testo e' esattamente l'estrazione che non ha scritto
    niente - il difetto intermittente che `SESSION_CONTEXT_RETRIES` esiste per
    assorbire.
    """

    def __init__(self, riesce_ai: set[int]) -> None:
        super().__init__("scripted-contesto")
        self.riesce_ai = set(riesce_ai)
        self.tentativi = 0
        self.deve_chiudere = False

    def __deepcopy__(self, memo: dict) -> "ModelloContesto":
        return self

    def _prossima(self) -> ModelResponse:
        self.chiamate += 1

        # Un tentativo puo' chiamare il modello due volte - la tool call e la
        # riga che segue il suo esito - e il confine fra i tentativi non si
        # conta sulle chiamate: solo il tentativo che *ha* emesso lo strumento
        # ne ha due. Contarle a coppie faceva scivolare il conto di uno, e il
        # tentativo che doveva riuscire non arrivava mai.
        if self.deve_chiudere:
            self.deve_chiudere = False
            return ModelResponse(role="assistant", content="salvato", response_usage=MessageMetrics())

        self.tentativi += 1
        if self.tentativi in self.riesce_ai:
            self.deve_chiudere = True
            return ModelResponse(
                role="assistant",
                tool_calls=[tool_call("save_session_context", summary="riassunto della prova")],
                response_usage=MessageMetrics(),
            )
        return ModelResponse(role="assistant", content="niente da aggiornare", response_usage=MessageMetrics())


MESSAGGI_CONTESTO = [
    Message(role="user", content="stiamo provando il retry del contesto di sessione"),
    Message(role="assistant", content="va bene"),
]


def store_contesto(riesce_ai: set[int], tentativi: int | None = None):
    """Lo store di contesto della prova.

    `tentativi` esiste per provare che il numero viaggia come parametro e non
    viene riletto da `config` dentro il ciclo: la prova ne chiede uno diverso
    da quello configurato e pretende che sia quello a valere.
    """
    politica = POLITICA
    if tentativi is not None:
        politica = replace(POLITICA, apprendimento=replace(POLITICA.apprendimento, tentativi_contesto=tentativi))
    return build_session_context_store(
        build_db(
            PERCORSI,
        ),
        ModelloContesto(riesce_ai),
        politica,
    )


def contesto_riprova() -> str:
    """Il retry ripete solo cio' che non ha scritto, e si ferma appena scrive."""
    esigi(
        POLITICA.apprendimento.tentativi_contesto >= 1,
        "la prova vuole almeno un retry configurato: " + str(POLITICA.apprendimento.tentativi_contesto),
    )
    massimo = 1 + POLITICA.apprendimento.tentativi_contesto

    # Al primo colpo: nessuna ripetizione, e il contesto e' davvero in archivio.
    store = store_contesto({1})
    store.extract_and_save(messages=MESSAGGI_CONTESTO, session_id="subito", user_id=UTENTE)
    esigi(
        store.last_extraction_attempts == 1, "tentativi con successo immediato: " + str(store.last_extraction_attempts)
    )
    esigi(store.was_updated, "`was_updated` e' falso dopo un salvataggio riuscito")
    esigi(store.context_updated is store.was_updated, "`context_updated` non e' piu' cio' che `was_updated` legge")
    esigi(store.get(session_id="subito") is not None, "il contesto non e' in archivio dopo il salvataggio")

    # Mai: si ripete fino al tetto e non oltre, e non resta niente scritto.
    store = store_contesto(set())
    store.extract_and_save(messages=MESSAGGI_CONTESTO, session_id="mai", user_id=UTENTE)
    esigi(
        store.last_extraction_attempts == massimo,
        "tentativi senza mai salvare: " + str(store.last_extraction_attempts) + ", atteso " + str(massimo),
    )
    esigi(not store.was_updated, "`was_updated` e' vero senza che il modello abbia eseguito lo strumento")
    esigi(store.get(session_id="mai") is None, "un'estrazione che non ha scritto ha lasciato un contesto")

    # Il caso che il retry esiste per coprire: fallisce, poi recupera.
    store = store_contesto({2})
    store.extract_and_save(messages=MESSAGGI_CONTESTO, session_id="recuperato", user_id=UTENTE)
    esigi(
        store.last_extraction_attempts == 2,
        "il retry non ha recuperato al secondo tentativo: " + str(store.last_extraction_attempts),
    )
    esigi(store.get(session_id="recuperato") is not None, "il contesto recuperato non e' in archivio")

    # Il gemello asincrono: stessa logica, e va attraversata perche' e' codice
    # diverso, non lo stesso corpo con un await davanti. I tre casi sono gli
    # stessi del sincrono: senza il primo e il tetto, il ramo che esce dal
    # ciclo resterebbe scoperto proprio nel corpo asincrono.
    store = store_contesto({1})
    asyncio.run(store.aextract_and_save(messages=MESSAGGI_CONTESTO, session_id="async-subito", user_id=UTENTE))
    esigi(
        store.last_extraction_attempts == 1,
        "tentativi asincroni con successo immediato: " + str(store.last_extraction_attempts),
    )
    esigi(store.get(session_id="async-subito") is not None, "il contesto asincrono del primo colpo non e' in archivio")

    store = store_contesto(set())
    asyncio.run(store.aextract_and_save(messages=MESSAGGI_CONTESTO, session_id="async-mai", user_id=UTENTE))
    esigi(
        store.last_extraction_attempts == massimo,
        "tentativi asincroni senza mai salvare: " + str(store.last_extraction_attempts) + ", atteso " + str(massimo),
    )
    esigi(not store.was_updated, "`was_updated` e' vero sul percorso asincrono senza scrittura")
    esigi(
        store.get(session_id="async-mai") is None,
        "un'estrazione asincrona che non ha scritto ha lasciato un contesto",
    )

    store = store_contesto({2})
    asyncio.run(store.aextract_and_save(messages=MESSAGGI_CONTESTO, session_id="async", user_id=UTENTE))
    esigi(
        store.last_extraction_attempts == 2,
        "il retry asincrono non ha recuperato: " + str(store.last_extraction_attempts),
    )
    esigi(store.get(session_id="async") is not None, "il contesto asincrono non e' in archivio")

    # Il numero viene dalla politica ricevuta, non da un nome di modulo: con
    # tre tentativi chiesti all'oggetto, il ciclo si ferma a tre anche se il
    # `.env` ne configura un altro.
    store = store_contesto(set(), tentativi=3)
    store.extract_and_save(messages=MESSAGGI_CONTESTO, session_id="parametro", user_id=UTENTE)
    esigi(
        store.last_extraction_attempts == 4,
        "il tetto dei tentativi non segue la politica: " + str(store.last_extraction_attempts),
    )

    return (
        "1 al primo colpo, "
        + str(massimo)
        + " al tetto e 2 recuperato, nei due percorsi sincrono e asincrono, 4 dal parametro"
    )


def memoria_non_confermabile() -> str:
    """Profilo e memorie rifiutano PROPOSE e HITL: il limite e' di Agno."""
    avvisi: list[str] = []

    def raccogli(messaggio, *args, **kwargs):
        avvisi.append(str(messaggio))

    casi = (
        ("user_profile", UserProfileStore, UserProfileConfig),
        ("user_memory", UserMemoryStore, UserMemoryConfig),
    )
    for nome, classe, configurazione in casi:
        for modalita in (LearningMode.PROPOSE, LearningMode.HITL):
            avvisi.clear()
            modulo = "agno.learn.stores." + nome
            with patch(modulo + ".log_warning", raccogli):
                classe(config=configurazione(mode=modalita))
            atteso = modalita.name + " mode"
            esigi(
                any(atteso in avviso for avviso in avvisi),
                "Agno non rifiuta piu' " + atteso + " su " + nome + ": " + str(avvisi),
            )

    # Il rovescio: cio' che Ares usa davvero non deve emettere l'avviso, o il
    # controllo qui sopra passerebbe anche con uno store che si lamenta sempre.
    avvisi.clear()
    with patch("agno.learn.stores.user_profile.log_warning", raccogli):
        UserProfileStore(config=UserProfileConfig(mode=LearningMode.ALWAYS))
    esigi(not avvisi, "ALWAYS non e' piu' una modalita' accettata dal profilo: " + str(avvisi))

    return "PROPOSE e HITL rifiutati da profilo e memorie, ALWAYS accettata"


# Dove la versione di Agno e' dichiarata: le pagine e il commento che la
# nominano per dire cosa Ares usa adesso. `CHANGELOG.md` e
# `docs/memory-quality.md` sono fuori di proposito - il primo racconta cosa e'
# cambiato, il secondo misure datate con la versione di allora - e le prove
# non si controllano da sole. La lista e' esplicita perche' una pagina che
# smettesse di nominare la versione non deve sparire dal controllo in
# silenzio: se una di queste non la cita piu', la prova e' rossa e si decide
# se toglierla dall'elenco.
FILE_CHE_DICHIARANO = (
    "README.md",
    "ROADMAP.md",
    "SECURITY.md",
    "ares/agent/learning.py",
    "docs/agno.md",
    "docs/architecture.md",
    "docs/core-contract.md",
)
VERSIONE_AGNO = re.compile(r"Agno (\d+\.\d+\.\d+)")
CARTELLE_DICHIARANTI = ("ares", "docs", "evals")
FILE_DI_RADICE = ("README.md", "ROADMAP.md", "SECURITY.md")
VERSIONI_STORICHE = ("docs/memory-quality.md",)


def _testi_dichiaranti() -> list[tuple[str, str]]:
    """I file in cui una versione di Agno puo' comparire, e il loro testo."""
    percorsi = [RADICE / nome for nome in FILE_DI_RADICE]
    for cartella in CARTELLE_DICHIARANTI:
        for modello in ("*.py", "*.md"):
            percorsi.extend(sorted((RADICE / cartella).rglob(modello)))
    testi = []
    for percorso in percorsi:
        if not percorso.is_file() or "__pycache__" in percorso.parts:
            continue
        relativo = percorso.relative_to(RADICE).as_posix()
        if relativo in VERSIONI_STORICHE:
            continue
        testi.append((relativo, percorso.read_text(encoding="utf-8")))
    return testi


def versione_dichiarata() -> str:
    """La versione di Agno nei documenti e' quella installata.

    Il numero e' scritto a mano in piu' posti e nessuno di essi si accorge di
    invecchiare: il commento di `AresLearningMachine` citava la 3.0.5 mentre
    il lock era gia' alla 3.0.9, e una pagina che descrive la versione
    sbagliata resta verde finche' qualcuno non la rilegge.

    Il metro e' l'installato, non una copia: `uv.lock` decide la patch, la CI
    installa quella, e le prove di contratto qui sopra girano su quella. Due
    controlli: le pagine di `FILE_CHE_DICHIARANO` devono nominare la versione
    installata, e nessun altro file deve nominarne una diversa. Salire di
    patch senza allineare le dichiarazioni rende questa prova rossa, e il
    messaggio nomina il file da correggere.
    """
    installata = version("agno")
    testi = dict(_testi_dichiaranti())
    for relativo in FILE_CHE_DICHIARANO:
        esigi(installata in testi.get(relativo, ""), relativo + " non dichiara Agno " + installata)
    for relativo, testo in testi.items():
        for citata in sorted(set(VERSIONE_AGNO.findall(testo))):
            esigi(
                citata == installata,
                relativo + " cita Agno " + citata + " mentre l'installato e' " + installata,
            )
    return "Agno " + installata + " in " + str(len(FILE_CHE_DICHIARANO)) + " dichiarazioni"


def main() -> int:
    global POLITICA
    # Gli store di apprendimento e LanceDB non servono: spegnerli impedisce
    # che una prova dichiarata offline accenda Ollama. Il porto chiuso rende
    # esplicito un eventuale tentativo.
    config.LEARN_USER_PROFILE = False
    config.LEARN_USER_MEMORY = False
    config.LEARN_SESSION_CONTEXT = False
    config.LEARN_ENTITIES = False
    config.LEARN_KNOWLEDGE = False
    config.OLLAMA_HOST = "http://127.0.0.1:1"
    # Dopo i flag, non all'import: l'agente di questa prova deve nascere senza
    # store di apprendimento, o costruirebbe LanceDB e chiamerebbe l'embedder.
    POLITICA = config.leggi_politica()

    falliti, _ = esegui(
        (
            ("estrazione singola", estrazione_singola),
            ("ciclo HITL", ciclo_hitl),
            ("retry contesto", contesto_riprova),
            ("memoria non confermabile", memoria_non_confermabile),
            ("versione dichiarata", versione_dichiarata),
        )
    )
    if falliti:
        print("Archivio della prova conservato:", RADICE_PROVA)
        return 1
    pulisci(RADICE_PROVA)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
