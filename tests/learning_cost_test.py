"""Quanto costa un turno in estrazioni
===================================
Uso:
    .venv/bin/python tests/learning_cost_test.py

`LearningMachine.process` chiama ogni store una volta, e ogni store ALWAYS
estrae con una o due chiamate al modello. Dopo una tool call Agno richiama
il modello solo per sentirlo chiudere: Ares la evita su profilo e memorie
(`senza_conferma` in `agent/learning.py`), il contesto di sessione la evita
gia' con `stop_after_tool_call`. Un modello che non chiama lo strumento
costa invece i tentativi del contesto.

Il modello di apprendimento e' finto e conta: il numero di chiamate e'
proprieta' del framework e della politica, e viene asserito, cosi' un Agno
che reintroducesse la chiamata di chiusura rende rossa la prova. Accanto al
numero si misura il peso: ogni estrazione rimanda la conversazione intera.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from typing import Any
from unittest.mock import patch

from _comune import chiudi, esegui, esigi, prepara_ambiente

# I percorsi vanno scelti prima di importare config, che li legge una volta
# sola all'import.
RADICE_PROVA = prepara_ambiente("learning-cost-test")

from _doppi import tool_call  # noqa: E402
from agno.models.base import Model  # noqa: E402
from agno.models.message import Message, MessageMetrics  # noqa: E402
from agno.models.response import ModelResponse  # noqa: E402

from ares import config  # noqa: E402
from ares.agent import learning  # noqa: E402
from ares.agent.learning import build_learning_machine  # noqa: E402
from ares.agent.runtime import build_db  # noqa: E402
from ares.state.identita import Utente  # noqa: E402

PERCORSI = config.leggi_percorsi()
IMPOSTAZIONI = config.leggi_impostazioni()
POLITICA = config.leggi_politica()
UTENTE = Utente.da_grezzo("costo")
SESSIONE = "costo"

# Gli strumenti con cui scrivono gli store ALWAYS, nell'ordine in cui il
# modello finto li cerca. Uno per store: il nome dice da quale store arriva la
# chiamata.
STRUMENTI_DI_SALVATAGGIO = ("save_session_context", "update_profile", "add_memory", "update_memory")

# Gli store ALWAYS accesi dalla politica predefinita: una chiamata ciascuno per
# turno completato.
STORE_ALWAYS = ("update_profile", "add_memory", "save_session_context")
CHIAMATE_PER_TURNO = len(STORE_ALWAYS)

# Cosa risponde il modello finto quando decide di scrivere. Il profilo vuole
# almeno un campo, o la scrittura non cambia niente; le memorie vogliono il
# testo; il contesto un riepilogo. Profilo e memorie devono avere un appiglio
# nella conversazione, o il radicamento li scarta.
ARGOMENTI_DI_SALVATAGGIO: dict[str, dict[str, Any]] = {
    "update_profile": {"current_focus": "organizzare i moduli e le prove di Ares"},
    "add_memory": {"memory": "Lavora su Ares la sera, dopo le 22."},
    "save_session_context": {"summary": "Misura del costo delle estrazioni."},
}

# La prima riga del turno finto: sta in ogni copia della conversazione che le
# estrazioni rimandano al modello, e serve a verificarlo.
APERTURA = "Come organizzo i moduli di Ares?"


def nome_strumento(funzione: Any) -> str:
    """Il nome di uno strumento, come lo vede il modello.

    Agno puo' passare le funzioni come dizionari o come `Function`: si leggono
    entrambe le forme.
    """
    if isinstance(funzione, dict):
        return str(funzione.get("name") or (funzione.get("function") or {}).get("name") or "")
    return str(getattr(funzione, "name", ""))


def schema_strumento(funzione: Any) -> str:
    """Lo schema di uno strumento come testo, per misurarne il peso."""
    return json.dumps(funzione if isinstance(funzione, dict) else funzione.to_dict())


def conversazione() -> list[Message]:
    """Un turno di lunghezza credibile: due domande, due risposte.

    Serve a misurare quanto pesa rimandarlo al modello: la lunghezza e' quella
    di un turno normale, perche' i caratteri contati siano rappresentativi.
    """
    return [
        Message(role="user", content=APERTURA + " Ho aggiunto tre comandi e non so dove metterli."),
        Message(
            role="assistant",
            content=(
                "I comandi locali stanno in `ares/cli/commands.py`, il rendering in `ares/cli/render.py` e la "
                "lettura degli archivi in `ares/state/stores.py`: la divisione e' per responsabilita', non per "
                "tipo di file. Un comando nuovo che legge lo stato prende percorsi e politica come parametri."
            ),
        ),
        Message(role="user", content="Perfetto, allora tengo le prove vicino al modulo che verificano."),
        Message(
            role="assistant",
            content="Esatto: una prova per file, e ogni prova nel proprio processo perche' `config` fotografa "
            "l'ambiente all'import.",
        ),
    ]


class ModelloConta(Model):
    """Il modello di apprendimento finto: conta le chiamate e scrive una volta.

    Con `ubbidiente=True` risponde alla prima chiamata con la tool call di
    salvataggio, poi senza. Con `ubbidiente=False` non chiama mai lo strumento,
    come un modello piccolo che non ubbidisce.

    `__deepcopy__` restituisce se stesso: gli store estraggono su una copia del
    modello, e il contatore deve restare leggibile.
    """

    def __init__(self, *, ubbidiente: bool = True) -> None:
        super().__init__(id="conta-costo", name="conta-costo", provider="test")
        self.ubbidiente = ubbidiente
        self.chiamate: list[dict[str, Any]] = []
        self.scritti: set[str] = set()

    def __deepcopy__(self, memo: dict) -> ModelloConta:
        return self

    def _salvataggio(self, tools: Any) -> str | None:
        nomi = [nome_strumento(funzione) for funzione in tools or []]
        for candidato in STRUMENTI_DI_SALVATAGGIO:
            if candidato in nomi:
                return candidato
        return None

    def _risposta(self, tools: Any) -> ModelResponse:
        nome = self._salvataggio(tools) if self.ubbidiente else None
        if nome is not None and nome not in self.scritti:
            self.scritti.add(nome)
            return ModelResponse(
                role="assistant",
                tool_calls=[tool_call(nome, **ARGOMENTI_DI_SALVATAGGIO[nome])],
                response_usage=MessageMetrics(),
            )
        return ModelResponse(role="assistant", content="Niente da aggiornare.", response_usage=MessageMetrics())

    def _misura(self, messages: Any, tools: Any) -> ModelResponse:
        self.chiamate.append(
            {
                "strumenti": [nome_strumento(funzione) for funzione in tools or []],
                "schemi": sum(len(schema_strumento(funzione)) for funzione in tools or []),
                "messaggi": [str(getattr(m, "content", "") or "") for m in messages],
                "formato": getattr(self, "format", None),
            }
        )
        return self._risposta(tools)

    def invoke(self, messages: Any, tools: Any = None, **kwargs: Any) -> ModelResponse:
        return self._misura(messages, tools)

    async def ainvoke(self, messages: Any, tools: Any = None, **kwargs: Any) -> ModelResponse:
        return self._misura(messages, tools)

    def invoke_stream(self, messages: Any, tools: Any = None, **kwargs: Any) -> Iterator[ModelResponse]:
        yield self._misura(messages, tools)

    async def ainvoke_stream(self, messages: Any, tools: Any = None, **kwargs: Any) -> AsyncIterator[ModelResponse]:
        yield self._misura(messages, tools)

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response


def senza(quale: str) -> Any:
    """La politica predefinita con un solo store ALWAYS spento."""
    return replace(POLITICA, apprendimento=replace(POLITICA.apprendimento, **{quale: False}))


# Il nome del modello di estrazione decide la modalita' (vedi
# `Impostazioni.estrazione_in_parallelo` ed `estrazione_vincolata`): la prova
# la fissa, invece di ereditarla dal `.env` di chi la lancia. Le prove sulla
# tool call la vogliono anche in locale, quindi senza vincolo.
IN_SERIE = replace(IMPOSTAZIONI, apprendimento="conta-costo:9b", vincolo_estrazione=False)
IN_PARALLELO = replace(IMPOSTAZIONI, apprendimento="conta-costo:cloud")
VINCOLATA = replace(IMPOSTAZIONI, apprendimento="conta-costo:9b", vincolo_estrazione=None)


def macchina_con(
    finto: ModelloConta, politica: Any = None, impostazioni: Any = IN_SERIE, utente: Utente = UTENTE
) -> Any:
    with patch.object(learning, "build_learning_model", lambda impostazioni: finto):
        return build_learning_machine(build_db(PERCORSI), None, utente, impostazioni, politica or POLITICA)


def turno_completato(macchina: Any, utente: Utente = UTENTE) -> None:
    macchina.process_completed_run(
        messages=conversazione(),
        user_id=utente.id,
        session_id="costo-" + utente.id,
        agent_id="costo-agente",
    )


def scritto(macchina: Any, utente: Utente) -> dict[str, bool]:
    """Quali store hanno qualcosa per l'utente, riletti dall'archivio."""
    memorie = macchina.user_memory_store.get(user_id=utente.id)
    return {
        "update_profile": macchina.user_profile_store.get(user_id=utente.id) is not None,
        "add_memory": bool(getattr(memorie, "memories", None)),
        "save_session_context": macchina.session_context_store.get(session_id="costo-" + utente.id) is not None,
    }


def estrai(finto: ModelloConta, politica: Any = None, impostazioni: Any = IN_SERIE) -> Any:
    """Un turno completato, e la macchina di apprendimento che l'ha estratto.

    Le chiamate si leggono da `finto.chiamate`; la macchina serve ai controlli
    su cio' che e' finito negli store.
    """
    macchina = macchina_con(finto, politica, impostazioni)
    macchina.process_completed_run(
        messages=conversazione(),
        user_id=UTENTE.id,
        session_id=SESSIONE,
        agent_id="costo-agente",
    )
    return macchina


def chiamate_di(finto: ModelloConta, politica: Any = None) -> list[dict[str, Any]]:
    """Le sole chiamate di un turno, quando la macchina non serve."""
    estrai(finto, politica)
    return finto.chiamate


def per_store(chiamate: list[dict[str, Any]]) -> dict[str, int]:
    """Quante chiamate per store, riconosciute dagli strumenti che portano."""
    conteggi: dict[str, int] = {}
    for chiamata in chiamate:
        for candidato in STRUMENTI_DI_SALVATAGGIO:
            if candidato in chiamata["strumenti"]:
                conteggi[candidato] = conteggi.get(candidato, 0) + 1
                break
    return conteggi


def pesi(chiamate: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    """Il peso delle chiamate: istruzioni, conversazione, schemi, totale.

    Istruzioni = primo messaggio (regole di estrazione e campi); conversazione
    = il messaggio con il turno; schemi = gli strumenti chiamabili. Il totale
    include i messaggi di servizio.
    """
    istruzioni = sum(len(chiamata["messaggi"][0]) for chiamata in chiamate if chiamata["messaggi"])
    turno = sum(len(testo) for chiamata in chiamate for testo in chiamata["messaggi"] if APERTURA in testo)
    schemi = sum(chiamata["schemi"] for chiamata in chiamate)
    totale = sum(sum(len(testo) for testo in chiamata["messaggi"]) + chiamata["schemi"] for chiamata in chiamate)
    return istruzioni, turno, schemi, totale


def costo_delle_tre_estrazioni() -> str:
    """Le chiamate per store con la politica predefinita, e il loro peso.

    Il numero atteso e' una chiamata per store ALWAYS. Il peso mostra dove va il
    costo: soprattutto istruzioni dello store e schema del suo strumento.
    """
    chiamate = chiamate_di(ModelloConta())
    conteggi = per_store(chiamate)

    esigi(chiamate, "l'estrazione non ha chiamato il modello")
    esigi(
        len(chiamate) == CHIAMATE_PER_TURNO,
        "un turno costa "
        + str(len(chiamate))
        + " chiamate invece di "
        + str(CHIAMATE_PER_TURNO)
        + ": la conferma dopo la tool call e' tornata, o uno store ALWAYS non estrae piu'",
    )
    esigi(
        set(conteggi) == set(STORE_ALWAYS),
        "non tutti e tre gli store ALWAYS hanno estratto: " + str(conteggi),
    )
    for chiamata in chiamate:
        esigi(
            any(APERTURA in messaggio for messaggio in chiamata["messaggi"]),
            "un'estrazione non rimanda la conversazione: " + str(chiamata["messaggi"])[:200],
        )

    istruzioni, turno, schemi, totale = pesi(chiamate)
    dettaglio = ", ".join(nome + " " + str(quante) for nome, quante in sorted(conteggi.items()))
    return (
        str(len(chiamate))
        + " chiamate ("
        + dettaglio
        + "): "
        + str(totale)
        + " caratteri, di cui "
        + str(istruzioni)
        + " di istruzioni, "
        + str(turno)
        + " di turno rispedito e "
        + str(schemi)
        + " di schemi"
    )


def la_politica_spegne_il_costo() -> str:
    """Ogni store spento toglie una chiamata, e nessun'altra.

    Se il numero non cambiasse, la politica non governerebbe il costo. Con
    tutti e tre spenti restano entita' e intuizioni, AGENTIC: zero chiamate.
    """
    acceso = chiamate_di(ModelloConta())
    senza_profilo = chiamate_di(ModelloConta(), senza("profilo"))
    senza_memorie = chiamate_di(ModelloConta(), senza("memorie"))
    senza_contesto = chiamate_di(ModelloConta(), senza("contesto"))
    spenti = chiamate_di(
        ModelloConta(),
        replace(POLITICA, apprendimento=replace(POLITICA.apprendimento, profilo=False, memorie=False, contesto=False)),
    )
    esigi(not spenti, "con gli store ALWAYS spenti l'estrazione chiama comunque il modello: " + str(len(spenti)))
    esigi(len(acceso) == CHIAMATE_PER_TURNO, "accesi " + str(len(acceso)) + " invece di " + str(CHIAMATE_PER_TURNO))
    for nome, chiamate in (("profilo", senza_profilo), ("memorie", senza_memorie), ("contesto", senza_contesto)):
        esigi(
            len(chiamate) == CHIAMATE_PER_TURNO - 1,
            "spegnere "
            + nome
            + " toglie "
            + str(len(acceso) - len(chiamate))
            + " chiamate invece di 1: "
            + str(len(chiamate)),
        )
    return (
        "accesi "
        + str(len(acceso))
        + ", senza profilo "
        + str(len(senza_profilo))
        + ", senza memorie "
        + str(len(senza_memorie))
        + ", senza contesto "
        + str(len(senza_contesto))
        + ", tutti spenti 0"
    )


def il_modello_che_non_ubbidisce() -> str:
    """Un modello che non chiama lo strumento paga i tentativi del contesto.

    Profilo e memorie non ritentano; il contesto ritenta fino al tetto della
    politica, quindi un modello che non scrive costa piu' di uno che ubbidisce.
    """
    chiamate = chiamate_di(ModelloConta(ubbidiente=False))
    tentativi = POLITICA.apprendimento.tentativi_contesto
    attese = CHIAMATE_PER_TURNO + tentativi
    esigi(
        len(chiamate) == attese,
        "attese " + str(attese) + " chiamate da un modello che non scrive, trovate " + str(len(chiamate)),
    )
    esigi(
        len(chiamate) > CHIAMATE_PER_TURNO,
        "un modello che non scrive costa " + str(len(chiamate)) + ", quanto o meno di uno che scrive",
    )
    return str(len(chiamate)) + " chiamate, di cui " + str(1 + tentativi) + " del contesto"


def la_scrittura_arriva_negli_store() -> str:
    """Il flag toglie la chiamata di chiusura, non la scrittura.

    Se Agno interpretasse `stop_after_tool_call` come "non eseguire", il costo
    scenderebbe ma la memoria resterebbe vuota: qui si verifica che profilo e
    memorie contengano cio' che il modello finto ha scritto.
    """
    macchina = estrai(ModelloConta())
    profilo = macchina.user_profile_store.get(user_id=UTENTE.id)
    memorie = macchina.user_memory_store.get(user_id=UTENTE.id)
    esigi(profilo is not None, "il profilo e' vuoto dopo una tool call accettata")
    esigi(
        ARGOMENTI_DI_SALVATAGGIO["update_profile"]["current_focus"] in str(profilo),
        "il profilo non contiene cio' che il modello ha scritto: " + str(profilo),
    )
    esigi(memorie is not None, "le memorie sono vuote dopo una tool call accettata")
    esigi(
        ARGOMENTI_DI_SALVATAGGIO["add_memory"]["memory"] in str(memorie),
        "le memorie non contengono cio' che il modello ha scritto: " + str(memorie),
    )
    return "profilo e memorie scritti nonostante il flag sulla tool call"


class ModelloInsieme(ModelloConta):
    """Risponde solo quando tutte e tre le estrazioni lo stanno chiamando insieme.

    In serie la barriera scade e ogni store fallisce: e' la prova che il
    parallelo e' vero, non un ciclo in un altro ordine.
    """

    def __init__(self) -> None:
        super().__init__()
        self.barriera = threading.Barrier(CHIAMATE_PER_TURNO, timeout=10)
        self.fili: set[str] = set()

    def _misura(self, messages: Any, tools: Any) -> ModelResponse:
        if self._salvataggio(tools) not in self.scritti:
            self.fili.add(threading.current_thread().name)
            self.barriera.wait()
        return super()._misura(messages, tools)


class ModelloTrattenuto(ModelloConta):
    """Non risponde finche' la prova non apre `via`: un modello cloud lento."""

    def __init__(self) -> None:
        super().__init__()
        self.via = threading.Event()
        self.partiti = threading.Semaphore(0)

    def _misura(self, messages: Any, tools: Any) -> ModelResponse:
        self.partiti.release()
        self.via.wait(10)
        return super()._misura(messages, tools)


class ModelloVincolato(ModelloConta):
    """Come `ModelloConta`, ma a una richiesta senza strumenti con uno schema risponde in JSON.

    L'estrazione vincolata imposta `format` sulla copia del modello, che qui
    e' il modello stesso.
    """

    def _risposta(self, tools: Any) -> ModelResponse:
        if tools or not isinstance(getattr(self, "format", None), dict):
            return super()._risposta(tools)
        dati = {**ARGOMENTI_DI_SALVATAGGIO["save_session_context"], "goal": None, "plan": [], "progress": []}
        return ModelResponse(role="assistant", content=json.dumps(dati), response_usage=MessageMetrics())


def costo_dell_estrazione_vincolata() -> str:
    """In locale il contesto chiede un JSON con lo schema, senza strumenti.

    Le chiamate restano una per store; il contesto non porta strumenti ma lo
    schema in `format`, profilo e memorie restano con la tool call. Cio' che
    il JSON dice arriva nello store come con la tool call. Le due
    vie si misurano su utenti nuovi, perche' uno store gia' pieno allunga
    il prompt.
    """

    def turno_nuovo(finto: ModelloConta, impostazioni: Any, nome: str) -> Any:
        utente = Utente.da_grezzo(nome)
        macchina = macchina_con(finto, None, impostazioni, utente)
        turno_completato(macchina, utente)
        return macchina, utente

    finto = ModelloVincolato()
    macchina, utente = turno_nuovo(finto, VINCOLATA, "costo-vincolato")
    chiamate = finto.chiamate
    esigi(len(chiamate) == CHIAMATE_PER_TURNO, "l'estrazione vincolata costa " + str(len(chiamate)) + " chiamate")
    vincolate = [c for c in chiamate if not c["strumenti"]]
    esigi(len(vincolate) == 1, "non e' solo il contesto a chiedere un JSON: " + str(len(vincolate)))
    esigi(
        all(isinstance(c["formato"], dict) and c["formato"].get("type") == "object" for c in vincolate),
        "una richiesta senza strumenti non porta lo schema in format",
    )
    esigi(
        per_store(chiamate) == {"add_memory": 1, "update_profile": 1},
        "profilo e memorie non usano piu' la tool call: " + str(per_store(chiamate)),
    )
    esigi(
        all(any(APERTURA in m for m in c["messaggi"]) for c in chiamate),
        "un'estrazione vincolata non rimanda la conversazione",
    )
    profilo = macchina.user_profile_store.get(user_id=utente.id)
    attuale = ARGOMENTI_DI_SALVATAGGIO["update_profile"]["current_focus"]
    esigi(
        profilo is not None and profilo.current_focus == attuale,
        "il profilo con la tool call non e' salvato: " + str(profilo),
    )
    contesto = macchina.session_context_store.get(session_id="costo-" + utente.id)
    esigi(
        contesto is not None and contesto.summary == ARGOMENTI_DI_SALVATAGGIO["save_session_context"]["summary"],
        "il contesto dal JSON non e' salvato: " + str(contesto),
    )
    esigi(macchina.session_context_store.last_extraction_attempts == 1, "il contesto dal JSON ha richiesto un retry")

    con_strumenti = ModelloConta()
    turno_nuovo(con_strumenti, IN_SERIE, "costo-con-strumenti")
    *_, schemi, totale = pesi(chiamate)
    *_, schemi_prima, totale_prima = pesi(con_strumenti.chiamate)
    esigi(schemi < schemi_prima, "lo schema degli strumenti non si alleggerisce")
    return (
        str(len(chiamate))
        + " chiamate, quella del contesto con lo schema in format: "
        + str(totale)
        + " caratteri contro "
        + str(totale_prima)
        + " con le tool call; schemi di strumenti "
        + str(schemi)
        + " contro "
        + str(schemi_prima)
    )


def in_parallelo_come_in_serie() -> str:
    """In parallelo le chiamate e le scritture sono quelle della serie, e gli store estraggono insieme."""
    utente = Utente.da_grezzo("costo-parallelo")
    finto = ModelloInsieme()
    macchina = macchina_con(finto, impostazioni=IN_PARALLELO, utente=utente)
    esigi(macchina.in_parallelo, "con l'estrazione cloud la macchina resta in serie")
    esigi(not macchina_con(ModelloConta()).in_parallelo, "con l'estrazione locale la macchina va in parallelo")
    avvisi: list[str] = []
    with patch.object(learning, "log_warning", lambda testo, *a, **k: avvisi.append(testo)):
        turno_completato(macchina, utente)
    esigi(not avvisi, "l'estrazione in parallelo ha prodotto avvisi: " + repr(avvisi))
    esigi(
        per_store(finto.chiamate) == dict.fromkeys(STORE_ALWAYS, 1),
        "in parallelo le chiamate per store cambiano: " + repr(per_store(finto.chiamate)),
    )
    esigi(
        all(scritto(macchina, utente).values()),
        "in parallelo uno store non ha scritto: " + repr(scritto(macchina, utente)),
    )
    esigi(
        len(finto.fili) == CHIAMATE_PER_TURNO and all(f.startswith("ares-estrazione") for f in finto.fili),
        "le estrazioni non girano in thread propri: " + repr(finto.fili),
    )
    return str(CHIAMATE_PER_TURNO) + " estrazioni insieme, una chiamata per store, tutto scritto"


def un_errore_non_ferma_gli_altri() -> str:
    """Uno store che fallisce diventa un avviso, come in Agno, e gli altri scrivono."""

    class ModelloGuasto(ModelloConta):
        def _misura(self, messages: Any, tools: Any) -> ModelResponse:
            if "update_profile" in [nome_strumento(f) for f in tools or []]:
                raise RuntimeError("profilo guasto")
            return super()._misura(messages, tools)

    utente = Utente.da_grezzo("costo-guasto")
    macchina = macchina_con(ModelloGuasto(), impostazioni=IN_PARALLELO, utente=utente)
    avvisi: list[str] = []
    with patch.object(learning, "log_warning", lambda testo, *a, **k: avvisi.append(testo)):
        turno_completato(macchina, utente)
    stato = scritto(macchina, utente)
    esigi(not stato["update_profile"], "il profilo guasto ha scritto")
    esigi(stato["add_memory"] and stato["save_session_context"], "un guasto ha fermato gli altri store: " + repr(stato))
    esigi(
        any("user_profile" in a and "profilo guasto" in a for a in avvisi),
        "il guasto non diventa un avviso: " + repr(avvisi),
    )
    return "profilo guasto in un avviso, memorie e contesto scritti"


def ctrl_c_non_lascia_scritture() -> str:
    """Dopo un Ctrl-C nessuna estrazione scrive: il turno fotografa la memoria subito dopo."""
    utente = Utente.da_grezzo("costo-interrotto")
    finto = ModelloTrattenuto()
    macchina = macchina_con(finto, impostazioni=IN_PARALLELO, utente=utente)

    def interrotto(futuri: Any) -> None:
        # L'interruzione arriva quando tutte le estrazioni aspettano il modello.
        for _ in range(CHIAMATE_PER_TURNO):
            esigi(finto.partiti.acquire(timeout=10), "le estrazioni non sono partite")
        raise KeyboardInterrupt

    inizio = time.monotonic()
    try:
        with patch.object(learning, "wait", interrotto):
            turno_completato(macchina, utente)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("il Ctrl-C non arriva al turno")
    esigi(time.monotonic() - inizio < 5, "il Ctrl-C aspetta il modello")

    # Il modello risponde adesso: le tool call arrivano, le scritture no.
    finto.via.set()
    for filo in [f for f in threading.enumerate() if f.name.startswith("ares-estrazione")]:
        filo.join(10)
    esigi(len(finto.scritti) == CHIAMATE_PER_TURNO, "il modello non ha chiesto di scrivere: " + repr(finto.scritti))
    esigi(
        not any(scritto(macchina, utente).values()),
        "un'estrazione ha scritto dopo il Ctrl-C: " + repr(scritto(macchina, utente)),
    )

    # Il cancello vale solo per quei thread: la chat e il turno dopo scrivono.
    esigi(
        per_store(finto.chiamate) == dict.fromkeys(STORE_ALWAYS, 1),
        "dopo il Ctrl-C il contesto ha ritentato: " + repr(per_store(finto.chiamate)),
    )
    seguente = macchina_con(ModelloConta(), impostazioni=IN_PARALLELO, utente=utente)
    turno_completato(seguente, utente)
    esigi(all(scritto(seguente, utente).values()), "dopo un Ctrl-C il turno seguente non scrive")
    return "interruzione subito, nessuna scrittura tardiva, nessun nuovo tentativo, turno seguente intatto"


class ModelloAPassi(ModelloConta):
    """Il contesto vincolato risponde un JSON per turno, dal copione; gli altri store non scrivono."""

    def __init__(self, copione: list[dict[str, Any]]) -> None:
        super().__init__(ubbidiente=False)
        self.copione = list(copione)

    def _risposta(self, tools: Any) -> ModelResponse:
        if tools or not isinstance(getattr(self, "format", None), dict):
            return super()._risposta(tools)
        dati = {"summary": "Riordino dei moduli di Ares.", "goal": None, **self.copione.pop(0)}
        return ModelResponse(role="assistant", content=json.dumps(dati), response_usage=MessageMetrics())


def il_piano_vincolato_non_si_svuota() -> str:
    """Una lista vuota dal JSON vincolato non cancella piano e avanzamento accumulati.

    Lo schema li chiede sempre, e un modello piccolo risponde `[]` nei turni
    in cui non c'e' niente di nuovo: Agno sostituirebbe le liste.
    """
    piano = ["dividere i moduli", "scrivere le prove", "aggiornare la documentazione"]
    copione = [
        {"plan": piano, "progress": ["dividere i moduli"]},
        {"plan": [], "progress": []},
        {"plan": piano, "progress": ["dividere i moduli", "scrivere le prove"]},
    ]
    utente = Utente.da_grezzo("costo-piano")
    finto = ModelloAPassi(copione)
    macchina = macchina_con(finto, None, VINCOLATA, utente)
    letti = []
    for _ in copione:
        turno_completato(macchina, utente)
        contesto = macchina.session_context_store.get(session_id="costo-" + utente.id)
        letti.append((contesto.plan, contesto.progress))
    esigi(letti[1] == letti[0], "un turno con liste vuote ha cancellato piano o avanzamento: " + repr(letti[1]))
    esigi(
        letti[2] == (piano, copione[2]["progress"]),
        "un avanzamento nuovo non sostituisce il vecchio: " + repr(letti[2]),
    )
    schema = next(c for c in finto.chiamate if not c["strumenti"])["formato"]
    esigi(
        schema["properties"]["progress"]["description"].startswith("Tutti i passi completati finora"),
        "lo schema non chiede l'avanzamento intero: " + schema["properties"]["progress"]["description"],
    )
    return "3 turni: [] conserva piano e avanzamento, una lista piena li sostituisce"


def il_cancello_aspetta_chi_scrive() -> str:
    """`chiudi` aspetta la scrittura cominciata e rifiuta quelle dopo."""
    cancello = learning.Cancello()
    dentro, esci = threading.Event(), threading.Event()
    esiti: list[bool] = []

    def scrivi() -> None:
        with cancello.scrittura() as aperto:
            esiti.append(aperto)
            dentro.set()
            esci.wait(10)

    filo = threading.Thread(target=scrivi)
    filo.start()
    esigi(dentro.wait(10), "la scrittura non e' cominciata")
    chiusura = threading.Thread(target=cancello.chiudi)
    chiusura.start()
    chiusura.join(0.2)
    esigi(chiusura.is_alive(), "il cancello si chiude durante una scrittura")
    esci.set()
    chiusura.join(10)
    filo.join(10)
    esigi(not chiusura.is_alive() and cancello.chiuso, "il cancello non si chiude a scrittura finita")
    with cancello.scrittura() as aperto:
        esiti.append(aperto)
    esigi(esiti == [True, False], "il cancello chiuso lascia scrivere: " + repr(esiti))
    return "chiusura dopo la scrittura in corso, rifiuto dopo"


def main() -> int:
    falliti, _ = esegui(
        (
            ("costo estrazioni", costo_delle_tre_estrazioni),
            ("politica spegne il costo", la_politica_spegne_il_costo),
            ("modello che non ubbidisce", il_modello_che_non_ubbidisce),
            ("scrittura negli store", la_scrittura_arriva_negli_store),
            ("estrazione vincolata", costo_dell_estrazione_vincolata),
            ("piano vincolato", il_piano_vincolato_non_si_svuota),
            ("in parallelo", in_parallelo_come_in_serie),
            ("errore in parallelo", un_errore_non_ferma_gli_altri),
            ("ctrl-c in parallelo", ctrl_c_non_lascia_scritture),
            ("cancello", il_cancello_aspetta_chi_scrive),
        )
    )
    return chiudi(falliti, RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
