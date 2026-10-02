"""Configurazione e adattamenti del ciclo di apprendimento di Ares."""

import functools
import inspect
import json
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

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
from agno.learn.stores import (
    EntityMemoryStore,
    LearnedKnowledgeStore,
    SessionContextStore,
    UserMemoryStore,
    UserProfileStore,
)
from agno.learn.utils import to_dict_safe
from agno.models.message import Message
from agno.models.ollama import Ollama
from agno.utils.log import log_warning
from agno.utils.message import get_conversation_text

from ares.agent.agno_interni import (
    FunzioniRitoccate,
    astrumenti_di_estrazione,
    elabora,
    funzioni_di_estrazione,
    prompt_di_estrazione,
    strumenti_di_estrazione,
    strumenti_esposti,
)
from ares.agent.echo import CAMPI_DI_SERVIZIO
from ares.agent.radicamento import Fonte, radica_campo, radica_memoria
from ares.agent.runtime import build_learning_model
from ares.agent.scaffale import ENTITA, INTUIZIONI, Scaffale
from ares.agent.schemas import AresMemories, AresMemorieSenzaData, AresProfile, chiave_memoria
from ares.config import QUADERNO_PREFIX, Impostazioni, Politica
from ares.state.identita import Utente
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
    "Per ogni attivita' distingui valutazione, decisione, programma futuro, avvio e completamento. "
    "'Ho deciso di realizzare X' conferma la decisione, non che l'utente stia gia' realizzando X. "
    "'Comincero' domani' e' un programma, non un avvio avvenuto; il passare del tempo da solo "
    "non conferma l'esecuzione. Se l'avvio non e' confermato, resta sconosciuto: non dedurre "
    "nemmeno che l'attivita' non sia iniziata. Aggiorna lo stato solo con elementi che sostengono "
    "il cambiamento. Se l'avvio e' gia' noto, ribadire la decisione o l'obiettivo non lo annulla: "
    "conserva quel fatto salvo una rettifica esplicita. "
    "La data in cui apprendi un evento non e' necessariamente la data in cui e' accaduto. "
)


class Cancello:
    """Le scritture di un'estrazione in parallelo, fermate tutte insieme.

    Dopo un Ctrl-C il turno fotografa la memoria per l'eco e la conferma,
    mentre gli altri thread aspettano ancora il modello: una loro scrittura
    arriverebbe dopo, senza eco e senza conferma. `chiudi` aspetta le
    scritture gia' cominciate, che durano millisecondi, e rifiuta le altre.
    """

    def __init__(self) -> None:
        self._stato = threading.Condition()
        self._chiuso = False
        self._in_corso = 0

    @property
    def chiuso(self) -> bool:
        with self._stato:
            return self._chiuso

    @contextmanager
    def scrittura(self) -> Iterator[bool]:
        """Vero se si puo' scrivere; la chiusura aspetta che il blocco finisca."""
        with self._stato:
            aperto = not self._chiuso
            if aperto:
                self._in_corso += 1
        try:
            yield aperto
        finally:
            if aperto:
                with self._stato:
                    self._in_corso -= 1
                    self._stato.notify_all()

    def chiudi(self) -> None:
        with self._stato:
            self._chiuso = True
            self._stato.wait_for(lambda: self._in_corso == 0)


# Il cancello dell'estrazione che questo thread sta eseguendo. Solo i thread
# di `AresLearningMachine` lo impostano: la chat e gli strumenti di memoria
# del turno scrivono sempre.
_estrazione = threading.local()


def _cancello() -> Cancello | None:
    return getattr(_estrazione, "cancello", None)


class ScrittureSorvegliate:
    """Mixin per uno store di `agno.learn`: `save` passa dal cancello dell'estrazione.

    `save` e' il punto da cui passa ogni scrittura dello store. Va prima
    della classe di Agno nelle basi.
    """

    def save(self, *args: Any, **kwargs: Any) -> Any:
        cancello = _cancello()
        if cancello is None:
            return super().save(*args, **kwargs)  # type: ignore[misc]
        with cancello.scrittura() as aperto:
            return super().save(*args, **kwargs) if aperto else None  # type: ignore[misc]

    async def asave(self, *args: Any, **kwargs: Any) -> Any:
        cancello = _cancello()
        if cancello is None:
            return await super().asave(*args, **kwargs)  # type: ignore[misc]
        with cancello.scrittura() as aperto:
            return await super().asave(*args, **kwargs) if aperto else None  # type: ignore[misc]


def _interrotta() -> bool:
    """Vero se l'estrazione di questo thread e' stata fermata: ritentare non serve."""
    cancello = _cancello()
    return cancello is not None and cancello.chiuso


def _estrai(store: Any, contesto: dict[str, Any], cancello: Cancello) -> None:
    _estrazione.cancello = cancello
    try:
        elabora(store, contesto)
    finally:
        _estrazione.cancello = None


@dataclass
class AresLearningMachine(LearningMachine):
    """Estrae apprendimenti soltanto quando il run e' davvero concluso.

    Agno avvia ``process`` in background prima della chiamata al modello, su
    una fotografia parziale dei messaggi. Qui quel callback e' spento e lo
    sostituisce il post-hook `apprendi_a_run_completato` sul RunOutput
    completo; ``learning=`` resta collegato per contesto, istruzioni e
    strumenti. Il comportamento di Agno e' verificato da
    `tests/agno_contract_test.py`.

    Con `in_parallelo` gli store con `ScrittureSorvegliate` estraggono
    insieme, ciascuno in un thread, e il turno aspetta il piu' lento invece
    della somma; gli altri restano in serie. Un errore di uno store non
    ferma gli altri, come in Agno.
    """

    in_parallelo: bool = False

    def process(self, *args, **kwargs) -> None:
        return None

    async def aprocess(self, *args, **kwargs) -> None:
        return None

    def process_completed_run(self, **kwargs: Any) -> None:
        if not self.in_parallelo:
            super().process(**kwargs)
            return
        # Le chiavi che `LearningMachine.process` mette sempre nel contesto.
        contesto = {"namespace": None, **kwargs}
        insieme = {nome: s for nome, s in self.stores.items() if isinstance(s, ScrittureSorvegliate)}
        for nome, store in self.stores.items():
            if nome not in insieme:
                _elabora_o_avvisa(nome, functools.partial(elabora, store, contesto))
        if not insieme:
            return
        cancello = Cancello()
        esecutore = ThreadPoolExecutor(max_workers=len(insieme), thread_name_prefix="ares-estrazione")
        futuri = {nome: esecutore.submit(_estrai, store, contesto, cancello) for nome, store in insieme.items()}
        try:
            wait(futuri.values())
        except BaseException:
            # Non si aspettano le chiamate al modello: il cancello ferma cio'
            # che scriverebbero, e l'interruzione arriva subito al turno.
            cancello.chiudi()
            esecutore.shutdown(wait=False, cancel_futures=True)
            raise
        esecutore.shutdown()
        for nome, futuro in futuri.items():
            _elabora_o_avvisa(nome, futuro.result)


def _elabora_o_avvisa(nome: str, passo: Callable[[], Any]) -> None:
    """Il passo di uno store; un errore diventa un avviso, come in `LearningMachine.process`."""
    try:
        passo()
    except Exception as errore:
        log_warning("Error processing through " + nome + ": " + str(errore))


# Il segno di una voce d'elenco scritta a mano: trattino, asterisco, pallino
# o numero seguito da punto o parentesi.
_SEGNO_VOCE = re.compile(r"^(?:[-*\u2022]|\d+[.)])\s+")

# Gli argomenti di `save_session_context` che Agno vuole come lista di testi.
_ARGOMENTI_LISTA = ("plan", "progress")


def come_lista(valore: Any) -> Any:
    """Una lista di testi da un testo, qualunque altro valore invariato.

    Accetta un array JSON di testi o un elenco una voce per riga, senza i
    segni d'elenco; un testo vuoto e' la lista vuota. Tutto il resto lo
    giudica la validazione di Agno.
    """
    if not isinstance(valore, str):
        return valore
    testo = valore.strip()
    if testo.startswith("["):
        try:
            decodificato = json.loads(testo)
        except ValueError:
            pass
        else:
            if isinstance(decodificato, list) and all(isinstance(voce, str) for voce in decodificato):
                return decodificato
    voci = (_SEGNO_VOCE.sub("", riga.strip()).strip() for riga in testo.splitlines())
    return [voce for voce in voci if voce]


def liste_dal_testo(entrypoint: Callable[..., Any]) -> Callable[..., Any]:
    """`entrypoint` con `plan` e `progress` convertiti da `come_lista` prima della validazione.

    `functools.wraps` tiene la firma originale, che Agno legge per decidere
    quali argomenti sono del modello.
    """

    def converti(argomenti: dict[str, Any]) -> dict[str, Any]:
        return {nome: come_lista(v) if nome in _ARGOMENTI_LISTA else v for nome, v in argomenti.items()}

    if inspect.iscoroutinefunction(entrypoint):

        @functools.wraps(entrypoint)
        async def asincrono(*args: Any, **kwargs: Any) -> Any:
            return await entrypoint(*args, **converti(kwargs))

        return asincrono

    @functools.wraps(entrypoint)
    def sincrono(*args: Any, **kwargs: Any) -> Any:
        return entrypoint(*args, **converti(kwargs))

    return sincrono


# Il prompt di Agno chiede di chiamare uno strumento; con lo schema la
# grammatica di Ollama permette solo il JSON, e il modello deve saperlo.
ISTRUZIONE_JSON = (
    "\n\n## Risposta\n\n"
    "In questa estrazione non ci sono strumenti da chiamare: rispondi solo con un oggetto JSON con i "
    "campi dello schema, che sono gli argomenti che avresti passato allo strumento. Scrivi in italiano."
)

# Cosa restituisce un'estrazione che non salva niente, come in Agno.
NIENTE_DA_AGGIORNARE = "No updates needed"


def oggetto_json(testo: str | None) -> dict[str, Any] | None:
    """L'oggetto JSON della risposta, o `None` se non lo e'.

    Con `format` Ollama restituisce solo JSON; un recinto di codice resta
    tollerato perche' un modello senza grammatica (una prova, un daemon
    vecchio) potrebbe aggiungerlo.
    """
    pulito = (testo or "").strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        valore = json.loads(pulito)
    except ValueError:
        return None
    return valore if isinstance(valore, dict) else None


class EstrazioneVincolata:
    """Mixin: con `vincolata`, l'estrazione risponde con un JSON vincolato dallo schema.

    Al posto della tool call, una richiesta senza strumenti con `format`
    pari allo schema degli argomenti e temperatura 0: un modello piccolo
    non puo' piu' dimenticare la chiamata o sbagliarne la forma. Strumenti
    e `format` non stanno mai nella stessa richiesta, perche' la grammatica
    renderebbe irraggiungibile la tool call. Il JSON si applica chiamando la
    funzione che il modello avrebbe chiamato, ritoccata come per la tool
    call: liste dal testo, cancello e retry valgono uguali. Lo usa solo il
    contesto di sessione: sul profilo il vincolo fa astenere i modelli
    piccoli (vedi `AresUserProfileStore`).

    La sottoclasse dice cosa rileggere (`_esistente`), cosa chiedere
    (`_richiesta`), quale funzione chiamare (`_funzione`) e con quali
    argomenti (`_argomenti`). Va prima della classe di Agno nelle basi.
    """

    vincolata = False

    def _esistente(self, kwargs: dict[str, Any]) -> Any:
        raise NotImplementedError

    async def _aesistente(self, kwargs: dict[str, Any]) -> Any:
        raise NotImplementedError

    def _richiesta(self, kwargs: dict[str, Any], esistente: Any) -> tuple[list[Any], dict[str, Any]] | None:
        """I messaggi e lo schema JSON, o `None` se non c'e' niente da estrarre."""
        raise NotImplementedError

    def _argomenti(self, dati: dict[str, Any], esistente: Any) -> dict[str, Any] | None:
        """Gli argomenti della funzione dal JSON, o `None` se non c'e' niente da salvare."""
        raise NotImplementedError

    def _funzione(self, strumenti: list[Callable[..., Any]]) -> Any:
        raise NotImplementedError

    def _strumenti(self, kwargs: dict[str, Any], esistente: Any) -> list[Callable[..., Any]]:
        raise NotImplementedError

    def _applicata(self) -> None:
        """Dopo la chiamata alla funzione: la sottoclasse aggiorna i propri flag."""

    async def _astrumenti(self, kwargs: dict[str, Any], esistente: Any) -> list[Callable[..., Any]]:
        raise NotImplementedError

    def _modello(self, schema: dict[str, Any]) -> Any:
        modello = deepcopy(self.model)  # type: ignore[attr-defined]
        modello.format = schema
        modello.options = {**(getattr(modello, "options", None) or {}), "temperature": 0}
        return modello

    def _dati(self, risposta: Any, modello: Any, run_metrics: Any) -> dict[str, Any] | None:
        if run_metrics is not None and risposta.response_usage is not None:
            from agno.metrics import ModelType, accumulate_model_metrics

            accumulate_model_metrics(risposta, modello, ModelType.LEARNING_MODEL, run_metrics)
        dati = oggetto_json(risposta.content)
        if dati is None:
            log_warning("Estrazione vincolata: la risposta non e' un oggetto JSON")
        return dati

    def extract_and_save(self, *args: Any, **kwargs: Any) -> Any:
        if not self.vincolata:
            return super().extract_and_save(*args, **kwargs)  # type: ignore[misc]
        esistente = self._esistente(kwargs)
        richiesta = self._richiesta(kwargs, esistente)
        if richiesta is None:
            return NIENTE_DA_AGGIORNARE
        messaggi, schema = richiesta
        modello = self._modello(schema)
        dati = self._dati(modello.response(messages=messaggi), modello, kwargs.get("run_metrics"))
        argomenti = self._argomenti(dati, esistente) if dati is not None else None
        if argomenti is None:
            return NIENTE_DA_AGGIORNARE
        strumenti = self._strumenti(kwargs, esistente)
        funzione = self._funzione(funzioni_di_estrazione(self, strumenti))
        esito = str(funzione.entrypoint(**argomenti))
        self._applicata()
        return esito

    async def aextract_and_save(self, *args: Any, **kwargs: Any) -> Any:
        if not self.vincolata:
            return await super().aextract_and_save(*args, **kwargs)  # type: ignore[misc]
        esistente = await self._aesistente(kwargs)
        richiesta = self._richiesta(kwargs, esistente)
        if richiesta is None:
            return NIENTE_DA_AGGIORNARE
        messaggi, schema = richiesta
        modello = self._modello(schema)
        dati = self._dati(await modello.aresponse(messages=messaggi), modello, kwargs.get("run_metrics"))
        argomenti = self._argomenti(dati, esistente) if dati is not None else None
        if argomenti is None:
            return NIENTE_DA_AGGIORNARE
        strumenti = await self._astrumenti(kwargs, esistente)
        funzione = self._funzione(funzioni_di_estrazione(self, strumenti))
        esito = funzione.entrypoint(**argomenti)
        esito = await esito if inspect.isawaitable(esito) else esito
        self._applicata()
        return str(esito)


class AresSessionContextStore(ScrittureSorvegliate, FunzioniRitoccate, EstrazioneVincolata, SessionContextStore):
    """Riprova soltanto un'estrazione che non ha scritto nulla.

    `tentativi_contesto` sono i tentativi oltre il primo. `__init__` passa il
    resto ad Agno senza fissarne la firma, che cambia fra le versioni.

    Il successo non si legge da `context_updated`: Agno lo accende per
    qualunque esecuzione dello strumento, anche una rifiutata dalla
    validazione degli argomenti (un `plan` che non e' una lista di testi), e `save`
    inghiotte i propri errori. Conta solo un contesto riletto dall'archivio
    uguale a quello appena salvato; `context_updated` viene riallineato.

    Prima del retry, `plan` e `progress` passati come testo diventano liste
    (vedi `liste_dal_testo`): e' l'errore di argomenti piu' comune, e
    ripeterlo costa un'estrazione intera che puo' sbagliare di nuovo. Lo
    schema mostrato al modello non cambia.
    """

    last_extraction_attempts = 0

    def __init__(self, *args, tentativi_contesto: int = 0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tentativi_contesto = tentativi_contesto
        self._salvato = False

    def save(self, session_id: str, context: Any, *args: Any, **kwargs: Any) -> None:
        super().save(session_id, context, *args, **kwargs)
        self._salvato = self._salvato or self._riletto(self.get(session_id=session_id), context)

    async def asave(self, session_id: str, context: Any, *args: Any, **kwargs: Any) -> None:
        await super().asave(session_id, context, *args, **kwargs)
        self._salvato = self._salvato or self._riletto(await self.aget(session_id=session_id), context)

    def _esistente(self, kwargs: dict[str, Any]) -> Any:
        return self.get(session_id=kwargs["session_id"])

    async def _aesistente(self, kwargs: dict[str, Any]) -> Any:
        return await self.aget(session_id=kwargs["session_id"])

    def _richiesta(self, kwargs: dict[str, Any], esistente: Any) -> tuple[list[Any], dict[str, Any]]:
        testo = get_conversation_text(kwargs.get("messages") or [])
        sistema = prompt_di_estrazione(self, conversation_text=testo, existing_context=esistente)
        proprieta: dict[str, Any] = {
            "summary": {"type": "string", "description": "Lo stato della sessione, leggibile senza i messaggi"}
        }
        if self.config.enable_planning:
            proprieta["goal"] = {
                "type": ["string", "null"],
                "description": "L'obiettivo dell'utente in questa sessione, null se non e' chiaro",
            }
            proprieta["plan"] = {
                "type": "array",
                "items": {"type": "string"},
                "description": "I passi del piano, uno per voce; vuoto se un piano non c'e'",
            }
            proprieta["progress"] = {
                "type": "array",
                "items": {"type": "string"},
                "description": "I passi completati e sostenuti dal turno, uno per voce",
            }
        schema = {"type": "object", "properties": proprieta, "required": list(proprieta)}
        messaggi = [
            Message(role="system", content=str(sistema.content) + ISTRUZIONE_JSON),
            Message(role="user", content="Analizza la conversazione e restituisci il contesto di sessione aggiornato."),
        ]
        return messaggi, schema

    def _argomenti(self, dati: dict[str, Any], esistente: Any) -> dict[str, Any] | None:
        riepilogo = dati.get("summary")
        if not isinstance(riepilogo, str) or not riepilogo.strip():
            return None
        nomi = ("summary", "goal", "plan", "progress") if self.config.enable_planning else ("summary",)
        return {nome: dati[nome] for nome in nomi if nome in dati}

    def _funzione(self, funzioni: list[Any]) -> Any:
        return next(f for f in funzioni if f.name == "save_session_context")

    def _strumenti(self, kwargs: dict[str, Any], esistente: Any) -> list[Callable[..., Any]]:
        return strumenti_di_estrazione(
            self,
            session_id=kwargs["session_id"],
            user_id=kwargs.get("user_id"),
            agent_id=kwargs.get("agent_id"),
            team_id=kwargs.get("team_id"),
            existing_context=esistente,
        )

    async def _astrumenti(self, kwargs: dict[str, Any], esistente: Any) -> list[Callable[..., Any]]:
        return await astrumenti_di_estrazione(
            self,
            session_id=kwargs["session_id"],
            user_id=kwargs.get("user_id"),
            agent_id=kwargs.get("agent_id"),
            team_id=kwargs.get("team_id"),
            existing_context=esistente,
        )

    def ritocca(self, funzioni: list[Any]) -> list[Any]:
        for funzione in funzioni:
            if funzione.name == "save_session_context" and funzione.entrypoint is not None:
                funzione.entrypoint = liste_dal_testo(funzione.entrypoint)
        return funzioni

    @staticmethod
    def _riletto(riletto: Any, context: Any) -> bool:
        return riletto is not None and bool(to_dict_safe(context)) and to_dict_safe(riletto) == to_dict_safe(context)

    def _extract_once(self, *args, **kwargs) -> str:
        self._salvato = False
        risultato = super().extract_and_save(*args, **kwargs)
        self.context_updated = self._salvato
        return risultato

    async def _aextract_once(self, *args, **kwargs) -> str:
        self._salvato = False
        risultato = await super().aextract_and_save(*args, **kwargs)
        self.context_updated = self._salvato
        return risultato

    def extract_and_save(self, *args, **kwargs) -> str:
        massimo = 1 + max(0, self.tentativi_contesto)
        risultato = "No updates needed"
        self.last_extraction_attempts = 0
        for tentativo in range(1, massimo + 1):
            risultato = self._extract_once(*args, **kwargs)
            self.last_extraction_attempts = tentativo
            if self.context_updated or _interrotta():
                return risultato
            if tentativo < massimo:
                log_warning(
                    "Session context non salvato: ripeto l'estrazione "
                    + str(tentativo)
                    + "/"
                    + str(self.tentativi_contesto)
                )
        log_warning("Session context non salvato dopo " + str(self.last_extraction_attempts) + " tentativi")
        return risultato

    async def aextract_and_save(self, *args, **kwargs) -> str:
        massimo = 1 + max(0, self.tentativi_contesto)
        risultato = "No updates needed"
        self.last_extraction_attempts = 0
        for tentativo in range(1, massimo + 1):
            risultato = await self._aextract_once(*args, **kwargs)
            self.last_extraction_attempts = tentativo
            if self.context_updated or _interrotta():
                return risultato
            if tentativo < massimo:
                log_warning(
                    "Session context non salvato: ripeto l'estrazione "
                    + str(tentativo)
                    + "/"
                    + str(self.tentativi_contesto)
                )
        log_warning("Session context non salvato dopo " + str(self.last_extraction_attempts) + " tentativi")
        return risultato


def senza_conferma(funzioni: list[Any], nome: str) -> list[Any]:
    """Ferma il modello dopo la tool call `nome`, senza la chiamata di chiusura.

    Dopo una tool call Agno richiama il modello solo per sentirgli dire che
    ha finito, ma l'esito si legge gia' da `response.tool_executions`: quella
    chiamata e' costo puro (i numeri sono in docs/memory-quality.md). Cosa si
    impara non cambia. `nome` e' quello che costruisce Agno, verificato da
    `tests/learning_cost_test.py`.
    """
    for funzione in funzioni:
        if funzione.name == nome:
            funzione.stop_after_tool_call = True
    return funzioni


def _testi(valore: Any) -> Iterator[str]:
    """I testi contenuti in un valore dello store, dizionari e liste compresi."""
    if isinstance(valore, str):
        yield valore
    elif isinstance(valore, dict):
        for chiave, interno in valore.items():
            if chiave not in CAMPI_DI_SERVIZIO or chiave == "memories":
                yield from _testi(interno)
    elif isinstance(valore, (list, tuple)):
        for interno in valore:
            yield from _testi(interno)


def argomenti_filtrati(
    entrypoint: Callable[..., Any], filtro: Callable[[dict[str, Any]], dict[str, Any] | str]
) -> Callable[..., Any]:
    """`entrypoint` con gli argomenti passati prima da `filtro`.

    Se `filtro` restituisce un testo, la funzione non viene chiamata e quel
    testo e' la risposta al modello. La firma resta quella originale, che
    Agno legge per lo schema.
    """
    if inspect.iscoroutinefunction(entrypoint):

        @functools.wraps(entrypoint)
        async def asincrono(*args: Any, **kwargs: Any) -> Any:
            filtrati = filtro(kwargs)
            return filtrati if isinstance(filtrati, str) else await entrypoint(*args, **filtrati)

        return asincrono

    @functools.wraps(entrypoint)
    def sincrono(*args: Any, **kwargs: Any) -> Any:
        filtrati = filtro(kwargs)
        return filtrati if isinstance(filtrati, str) else entrypoint(*args, **filtrati)

    return sincrono


class EstrazioneRadicata:
    """Mixin per profilo e memorie: l'estrazione salva solo cio' che ha un appiglio nel testo.

    La fonte e' la conversazione passata all'estrazione piu' cio' che lo
    store conteneva gia', cosi' un valore riscritto per intero non perde le
    voci note. I criteri sono in `radicamento`. Cio' che viene scartato si
    accumula finche' `prendi_scarti` non lo legge, una riga per valore:
    l'eco del turno lo mostra. Gli strumenti agentici dati alla
    conversazione non passano di qui. Va prima della classe di Agno nelle basi.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._fonte: Fonte | None = None
        self._scarti: list[str] = []

    def prendi_scarti(self) -> list[str]:
        """Gli scarti dall'ultima lettura, svuotati."""
        scarti, self._scarti = self._scarti, []
        return scarti

    def _con_fonte(self, kwargs: dict[str, Any], esistente: Any) -> None:
        # Agno passa sempre `messages` per nome; senza, non si sa contro cosa radicare.
        if "messages" not in kwargs:
            self._fonte = None
            return
        # I ruoli che `get_conversation_text` mostra all'estrattore.
        conversazione = (
            m.get_content_string() for m in kwargs["messages"] or [] if m.role in ("user", "assistant", "model")
        )
        self._fonte = Fonte.da_testi([*conversazione, *_testi(to_dict_safe(esistente))])

    def extract_and_save(self, *args: Any, **kwargs: Any) -> Any:
        self._con_fonte(kwargs, self.get(user_id=kwargs.get("user_id")))  # type: ignore[attr-defined]
        try:
            return super().extract_and_save(*args, **kwargs)  # type: ignore[misc]
        finally:
            self._fonte = None

    async def aextract_and_save(self, *args: Any, **kwargs: Any) -> Any:
        self._con_fonte(kwargs, await self.aget(user_id=kwargs.get("user_id")))  # type: ignore[attr-defined]
        try:
            return await super().aextract_and_save(*args, **kwargs)  # type: ignore[misc]
        finally:
            self._fonte = None


class AresUserProfileStore(EstrazioneRadicata, ScrittureSorvegliate, FunzioniRitoccate, UserProfileStore):
    """Il profilo, con una sola chiamata al modello per turno (vedi `senza_conferma`), radicato.

    Resta sulla tool call anche con un estrattore locale: con lo schema
    vincolato i 9B mettono null nei campi che con lo strumento scrivevano, e
    `current_focus` non arriva piu' fra una sessione e l'altra
    (docs/memory-quality.md).
    """

    def ritocca(self, funzioni: list[Any]) -> list[Any]:
        for funzione in senza_conferma(funzioni, "update_profile"):
            if funzione.name == "update_profile" and funzione.entrypoint is not None:
                funzione.entrypoint = argomenti_filtrati(funzione.entrypoint, self._radica)
        return funzioni

    def _radica(self, campi: dict[str, Any]) -> dict[str, Any]:
        if self._fonte is None:
            return campi
        radicati = {}
        for nome, valore in campi.items():
            if not isinstance(valore, str):
                radicati[nome] = valore
                continue
            verdetto = radica_campo(nome, valore, self._fonte)
            self._scarti += ["profilo " + nome + ": " + scarto for scarto in verdetto.scartato]
            radicati[nome] = verdetto.tenuto
        return radicati


# Gli strumenti dell'estrazione che scrivono il testo di una memoria.
_SCRIVONO_MEMORIE = ("add_memory", "update_memory")


class AresUserMemoryStore(EstrazioneRadicata, ScrittureSorvegliate, FunzioniRitoccate, UserMemoryStore):
    """Le memorie, con una sola chiamata per turno, la guida in italiano e radicate.

    La guida di Agno e' inglese e pensata per un agente di squadra; questa
    dice quando tocca al modello usare lo strumento.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._note: set[str] = set()

    def ritocca(self, funzioni: list[Any]) -> list[Any]:
        for funzione in senza_conferma(funzioni, "add_memory"):
            if funzione.entrypoint is None:
                continue
            if funzione.name == "add_memory":
                funzione.entrypoint = argomenti_filtrati(funzione.entrypoint, self._nuova)
            elif funzione.name in _SCRIVONO_MEMORIE:
                funzione.entrypoint = argomenti_filtrati(funzione.entrypoint, self._radica)
        return funzioni

    def _con_fonte(self, kwargs: dict[str, Any], esistente: Any) -> None:
        super()._con_fonte(kwargs, esistente)
        voci = getattr(esistente, "memories", None) or []
        self._note = {chiave_memoria(str(v.get("content") or "")) for v in voci if isinstance(v, dict)}

    def _nuova(self, argomenti: dict[str, Any]) -> dict[str, Any] | str:
        """Una memoria nuova: radicata, e non identica a una che c'e' gia'.

        L'estrattore vede le memorie salvate, ma un modello piccolo a volte
        ne riscrive una tale e quale. Le quasi uguali restano: le fonde
        `ares memories consolidate`, con un giudizio e un'anteprima.
        """
        filtrati = self._radica(argomenti)
        testo = filtrati.get("memory") if isinstance(filtrati, dict) else None
        if not isinstance(testo, str):
            return filtrati
        # Non e' uno scarto per l'eco: la cosa e' gia' in memoria.
        if chiave_memoria(testo) in self._note:
            return "Memoria non salvata: e' gia' fra quelle che conosci."
        self._note.add(chiave_memoria(testo))
        return filtrati

    def _radica(self, argomenti: dict[str, Any]) -> dict[str, Any] | str:
        testo = argomenti.get("memory")
        if self._fonte is None or not isinstance(testo, str):
            return argomenti
        verdetto = radica_memoria(testo, self._fonte)
        if verdetto.tenuto is None:
            self._scarti += ["memoria: " + scarto for scarto in verdetto.scartato]
            return "Memoria non salvata: nessuna sua parola compare nella conversazione."
        return {**argomenti, "memory": verdetto.tenuto}

    def instructions(self) -> str:
        if not strumenti_esposti(self) or not self.config.agent_can_update_memories:
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


class StoreSuRichiesta:
    """Mixin per uno store la cui guida e i cui strumenti possono stare sullo scaffale.

    `guida` e' il testo; `instructions` lo incornicia in `TAG`, e resta vuoto
    se lo store non espone strumenti o se lo `scaffale` tiene chiuso il suo
    `GRUPPO`: allora la guida arriva al modello solo come risposta di
    `attiva_strumenti`.
    """

    TAG = ""
    GRUPPO = ""
    scaffale: Scaffale | None = None

    def esposta(self) -> bool:
        return True

    def guida(self) -> str:
        raise NotImplementedError

    def instructions(self) -> str:
        if not self.esposta():
            return ""
        if self.scaffale is not None and not self.scaffale.attivo(self.GRUPPO):
            return ""
        return "<" + self.TAG + ">\n" + self.guida() + "\n</" + self.TAG + ">"


class AresEntityMemoryStore(StoreSuRichiesta, EntityMemoryStore):
    """Le entita', spiegate in italiano: quattro strumenti e quando usarli."""

    TAG = "istruzioni_entita"
    GRUPPO = ENTITA.nome

    def esposta(self) -> bool:
        return strumenti_esposti(self)

    def guida(self) -> str:
        return (
            "Le entita' sono persone, progetti, sistemi e prodotti che contano per la persona con "
            "cui parli, con i loro fatti ed eventi. Non si aggiornano da sole. "
            "remember_about registra un fatto, un evento, una descrizione o una nota su "
            "un'entita', per nome: una correzione e' il fatto nuovo, quello contraddetto viene "
            "ritirato da solo. link_entities registra una relazione fra due entita'. "
            "search_entities le cerca, e senza query le elenca dalla piu' recente; la nota "
            "che un risultato indica, se c'e', si legge con " + QUADERNO_PREFIX + "read_file. forget ritira "
            "un fatto o archivia un'entita' intera. Registra quando impari qualcosa di sostanziale "
            "su una persona, un progetto o un sistema che servira' in una conversazione futura, "
            "e scrivilo in italiano.\n"
            "Distingui i fatti dagli eventi quando usi remember_about: un fatto e' un valore attuale "
            "che un giorno sara' sostituito, un evento e' qualcosa che e' accaduto in un momento "
            "preciso. Distingui la data dell'evento da quella in cui ne vieni a conoscenza: se il "
            "momento non e' noto, non attribuirgli la data di oggi. Anche un evento registrato puo' "
            "richiedere una correzione se la fonte era sbagliata."
        )


class AresLearnedKnowledgeStore(StoreSuRichiesta, LearnedKnowledgeStore):
    """Le intuizioni, spiegate in italiano e senza regole di squadra.

    La guida di Agno chiede di conservare "obiettivi e politiche del team
    perche' ne beneficino altri utenti": qui c'e' una persona sola, e quella
    regola farebbe salvare come intuizione cio' che e' una preferenza.
    """

    TAG = "istruzioni_intuizioni"
    GRUPPO = INTUIZIONI.nome

    def esposta(self) -> bool:
        # Agno restituisce le istruzioni AGENTIC anche con gli strumenti spenti.
        return self.config.enable_agent_tools

    def instructions(self) -> str:
        if self.esposta() and self.config.mode != LearningMode.AGENTIC:
            return LearnedKnowledgeStore.instructions(self)
        return super().instructions()

    def guida(self) -> str:
        return (
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
            "regole di squadra da conservare per altri. Non scrivere un criterio nel quaderno: "
            "il quaderno non viene cercato automaticamente nelle conversazioni future."
        )


def scaffale_della_macchina(
    macchina: LearningMachine | None, altre: Mapping[str, Callable[[], str]] | None = None
) -> Scaffale | None:
    """Lo scaffale con i gruppi che la macchina espone davvero, legato ai loro store.

    `altre` aggiunge gruppi che non vengono da uno store, con la loro guida.
    `None` se non resta nessun gruppo da tenere su richiesta.
    """
    tutti = macchina.stores.values() if macchina is not None else ()
    store = [s for s in tutti if isinstance(s, StoreSuRichiesta) and s.esposta()]
    if not store and not altre:
        return None
    scaffale = Scaffale(guide={**{s.GRUPPO: s.guida for s in store}, **(altre or {})})
    for s in store:
        s.scaffale = scaffale
    return scaffale


def build_session_context_store(db: SqliteDb, model: Ollama, politica: Politica) -> AresSessionContextStore:
    """Costruisce lo store di contesto con retry mirato, secondo la politica."""
    return AresSessionContextStore(
        config=SessionContextConfig(
            db=db,
            mode=LearningMode.ALWAYS,
            model=model,
            enable_planning=True,
            max_updates_per_run=politica.apprendimento.max_aggiornamenti,
            instructions=CRITERI_ESTRAZIONE + "Nel contesto di sessione distingui obiettivo, piano proposto, "
            "decisioni accettate e avanzamento verificato. Un'azione tentata o fallita non e' completata. "
            "Nel riepilogo descrivi un programma futuro come programma: non aggiungere 'non ancora "
            "avviata' o 'non ha iniziato' se l'utente non lo ha dichiarato. In progress inserisci "
            "solo avanzamenti sostenuti dal turno, non obiettivi o intenzioni.",
        ),
        tentativi_contesto=politica.apprendimento.tentativi_contesto,
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
    db: SqliteDb,
    knowledge: Knowledge | None,
    utente: Utente,
    impostazioni: Impostazioni,
    politica: Politica,
    *,
    strumenti: bool = True,
) -> AresLearningMachine:
    """Compone gli store attivi secondo la politica della conversazione.

    `impostazioni` dice con quale modello estrarre, `politica.apprendimento`
    quali store esistono e con quali limiti. Con `strumenti=False` (`ares -p`)
    gli store iniettano ancora il loro contesto, ma il modello non riceve gli
    strumenti per scriverci.
    """
    learning_model = build_learning_model(impostazioni)
    apprendimento = politica.apprendimento

    # La macchina accetta istanze gia' fatte e non le completa: db, modello e
    # limiti vanno passati a ogni store.
    user_profile: AresUserProfileStore | bool = False
    if apprendimento.profilo:
        user_profile = AresUserProfileStore(
            config=UserProfileConfig(
                db=db,
                mode=LearningMode.ALWAYS,
                schema=AresProfile,
                model=learning_model,
                max_updates_per_run=apprendimento.max_aggiornamenti,
                instructions=(
                    CRITERI_ESTRAZIONE + "Cattura solo cio' che resta vero oltre questa conversazione. "
                    "Le preferenze durature e il contesto professionale vanno nel profilo; "
                    "cio' che l'utente vuole in questo momento no."
                ),
            )
        )

    user_memory: AresUserMemoryStore | bool = False
    if apprendimento.memorie:
        user_memory_config = UserMemoryConfig(
            db=db,
            mode=LearningMode.ALWAYS,
            model=learning_model,
            schema=AresMemories if apprendimento.memorie_datate else AresMemorieSenzaData,
            max_updates_per_run=apprendimento.max_aggiornamenti,
            enable_agent_tools=apprendimento.strumenti_memoria and strumenti,
            instructions=(
                CRITERI_ESTRAZIONE + "Registra osservazioni che non entrano in un campo strutturato: "
                "abitudini, vincoli, opinioni espresse, cose che l'utente ha provato "
                "e scartato. Ogni memoria deve essere comprensibile da sola, senza "
                "la conversazione che l'ha generata."
            ),
        )
        user_memory = AresUserMemoryStore(config=user_memory_config)

    session_context: AresSessionContextStore | bool = False
    if apprendimento.contesto:
        session_context = build_session_context_store(db, learning_model, politica)
        session_context.vincolata = impostazioni.estrazione_vincolata

    entity_memory: AresEntityMemoryStore | bool = False
    if apprendimento.entita:
        entity_memory = AresEntityMemoryStore(
            config=EntityMemoryConfig(
                db=db,
                model=learning_model,
                namespace=namespace_entita(utente),
                max_updates_per_run=apprendimento.max_aggiornamenti,
                enable_agent_tools=strumenti,
            )
        )

    learned_knowledge: AresLearnedKnowledgeStore | bool = False
    if apprendimento.intuizioni:
        learned_knowledge = AresLearnedKnowledgeStore(
            config=LearnedKnowledgeConfig(
                knowledge=knowledge,
                model=learning_model,
                mode=LearningMode.AGENTIC,
                namespace=namespace_utente(utente),
                max_updates_per_run=apprendimento.max_aggiornamenti,
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
        namespace=namespace_utente(utente),
        max_updates_per_run=apprendimento.max_aggiornamenti,
        in_parallelo=impostazioni.estrazione_in_parallelo,
    )
