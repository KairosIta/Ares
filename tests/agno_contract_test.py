"""
Contratto con Agno: estrazione, conferma, retry e limiti dichiarati
===================================================================
Uso:
    .venv/bin/python tests/agno_contract_test.py

Quattro cose Ares le da' per vere di Agno, e nessuna prova le chiedeva ad
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

La terza: il retry di `AresSessionContextStore`. E' la seconda superficie
di Agno che Ares sovrascrive, e finora la provava soltanto
`learning_reliability_test.py`, che vuole Ollama e quindi in CI non gira
mai: il ramo piu' delicato dell'apprendimento era verificato solo a mano.
Il retry si regge su tre fatti di Agno - `extract_and_save` esiste con
quel nome, `context_updated` viene azzerato all'inizio e acceso solo se
il modello ha eseguito uno strumento, `aextract_and_save` e' il gemello
asincrono - e se uno cadesse Ares ripeterebbe all'infinito o non
ripeterebbe mai, in silenzio. Qui il modello e' di nuovo un copione, cosi'
il caso "fallisce e poi recupera" e' deterministico invece che sperato.

La quarta: profilo e memorie non sono confermabili. `SECURITY.md` e
`docs/architecture.md` dichiarano che la memoria durevole si scrive senza
passare da una conferma, e la ragione non e' una scelta di Ares: in Agno
`UserProfileStore` e `UserMemoryStore` rifiutano PROPOSE, e HITL e'
"reserved for future use" su ogni store. E' un limite del framework, e un
limite dichiarato va sorvegliato come un'invariante: il giorno in cui Agno
lo togliesse, questa prova diventa rossa e la documentazione va riscritta
invece di restare vera per abitudine.

Niente modello e niente rete: il modello e' uno script che emette le tool
call decise dalla prova, come in `session_retention_test.py`. Nei primi due
controlli gli store di apprendimento sono spenti, e l'estrazione e' un
passaggio a vuoto: e' il passaggio che si conta, non cio' che scriverebbe.
Il terzo store lo costruisce invece davvero, perche' li' cio' che si guarda
e' proprio se ha scritto.
"""

import asyncio
import json
import shutil
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

from _comune import esigi, fallimento, ok, prepara_ambiente

# I percorsi vanno scelti prima di importare config, che li legge una volta
# sola all'import.
RADICE_PROVA = prepara_ambiente("agno-contract-test")

from agno.learn import (  # noqa: E402
    LearningMachine,
    LearningMode,
    UserMemoryConfig,
    UserProfileConfig,
)
from agno.learn.stores import UserMemoryStore, UserProfileStore  # noqa: E402
from agno.models.base import Model  # noqa: E402
from agno.models.message import Message, MessageMetrics  # noqa: E402
from agno.models.response import ModelResponse  # noqa: E402

from ares import config  # noqa: E402
from ares.agent.assistant import build_assistant  # noqa: E402
from ares.agent.learning import build_session_context_store  # noqa: E402
from ares.agent.runtime import build_db  # noqa: E402
from ares.agent.turn_core import TurnEventKind, run_turn_cycle  # noqa: E402

UTENTE = "prova-contratto"
SESSIONE = "contratto"
NOME_FILE = "da-cancellare.txt"


def tool_call(nome: str, **argomenti: Any) -> dict[str, Any]:
    return {
        "id": "call-" + nome,
        "type": "function",
        "function": {"name": nome, "arguments": json.dumps(argomenti)},
    }


class ModelloScript(Model):
    """Risponde con le tool call decise dalla prova, poi conclude con "fatto".

    Ogni chiamata consuma una voce del copione; esaurito, il modello chiude
    il turno. Lo stesso oggetto vale per `invoke` e `invoke_stream`, perche'
    `turn_core` usa lo streaming e la prova deve attraversare quella via.
    """

    def __init__(self, copione: list[list[dict[str, Any]]]) -> None:
        super().__init__(id="scripted-contract", name="scripted-contract", provider="test")
        self.copione = list(copione)
        self.chiamate = 0

    def _prossima(self) -> ModelResponse:
        self.chiamate += 1
        if self.copione:
            return ModelResponse(role="assistant", tool_calls=self.copione.pop(0), response_usage=MessageMetrics())
        return ModelResponse(role="assistant", content="fatto", response_usage=MessageMetrics())

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._prossima()

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._prossima()

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[ModelResponse]:
        yield self._prossima()

    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ModelResponse]:
        yield self._prossima()

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response


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
    """

    def __init__(self, macchina) -> None:
        self.vere: list[list[Any]] = []
        self.anticipate = 0
        self.macchina = macchina
        self._originale = LearningMachine.process

    def __enter__(self):
        contatore = self

        def process_vero(istanza, *args, **kwargs):
            contatore.vere.append(list(kwargs.get("messages") or []))
            return contatore._originale(istanza, *args, **kwargs)

        def process_anticipato(*args, **kwargs):
            contatore.anticipate += 1
            return None

        self._patch = patch.object(LearningMachine, "process", process_vero)
        self._patch.__enter__()
        self.macchina.process = process_anticipato
        return self

    def __exit__(self, *_):
        self._patch.__exit__(None, None, None)
        del self.macchina.process


def agente():
    costruito = build_assistant(user_id=UTENTE, session_id=SESSIONE)
    costruito.model = ModelloScript(copione_cancellazione())
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
    file = config.WORKSPACE_DIR / NOME_FILE
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
    return (
        "1 estrazione su "
        + str(len(messaggi_estratti))
        + " messaggi, "
        + str(contatore.anticipate)
        + " anticipata azzerata"
    )


def ciclo_hitl() -> str:
    """`run -> pausa -> continue_run` sullo stesso run, con conferma e con rifiuto."""
    agent = agente()
    file = config.WORKSPACE_DIR / NOME_FILE

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

    agent.model = ModelloScript(copione_cancellazione())
    cliente, risposta = turno(agent, "rifiuto", file)
    esigi(risposta is not None and not risposta.is_paused, "il run rifiutato e' ancora in pausa")
    esigi(file.exists(), "il file e' stato cancellato nonostante il rifiuto")
    esigi(risposta.run_id == cliente.run_id_alla_pausa[0], "dopo il rifiuto continue_run ha aperto un run diverso")
    esigi(
        any("non ora" in testo for testo in contenuti_tool(risposta.messages or [])),
        "il motivo del rifiuto non arriva al modello: " + str(contenuti_tool(risposta.messages or [])),
    )
    return "conferma cancella, rifiuto conserva, stesso run_id e motivo consegnato"


class ModelloContesto(ModelloScript):
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
        super().__init__([])
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


def store_contesto(riesce_ai: set[int]):
    return build_session_context_store(build_db(), ModelloContesto(riesce_ai))


def contesto_riprova() -> str:
    """Il retry ripete solo cio' che non ha scritto, e si ferma appena scrive."""
    esigi(
        config.SESSION_CONTEXT_RETRIES >= 1,
        "la prova vuole almeno un retry configurato: " + str(config.SESSION_CONTEXT_RETRIES),
    )
    massimo = 1 + config.SESSION_CONTEXT_RETRIES

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
    # diverso, non lo stesso corpo con un await davanti.
    store = store_contesto({2})
    asyncio.run(store.aextract_and_save(messages=MESSAGGI_CONTESTO, session_id="async", user_id=UTENTE))
    esigi(
        store.last_extraction_attempts == 2,
        "il retry asincrono non ha recuperato: " + str(store.last_extraction_attempts),
    )
    esigi(store.get(session_id="async") is not None, "il contesto asincrono non e' in archivio")

    return "1 al primo colpo, " + str(massimo) + " al tetto, 2 recuperato, sincrono e asincrono"


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


def main() -> int:
    # Gli store di apprendimento e LanceDB non servono: spegnerli impedisce
    # che una prova dichiarata offline accenda Ollama. Il porto chiuso rende
    # esplicito un eventuale tentativo.
    config.LEARN_USER_PROFILE = False
    config.LEARN_USER_MEMORY = False
    config.LEARN_SESSION_CONTEXT = False
    config.LEARN_ENTITIES = False
    config.LEARN_KNOWLEDGE = False
    config.OLLAMA_HOST = "http://127.0.0.1:1"

    riuscita = False
    try:
        ok("estrazione singola", estrazione_singola())
        ok("ciclo HITL", ciclo_hitl())
        ok("retry contesto", contesto_riprova())
        ok("memoria non confermabile", memoria_non_confermabile())
        riuscita = True
        return 0
    except Exception as errore:
        fallimento(errore)
        return 1
    finally:
        if riuscita:
            shutil.rmtree(RADICE_PROVA, ignore_errors=True)
        else:
            print("Archivio della prova conservato:", RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
