"""
Quanto costa un turno in estrazioni
===================================
Uso:
    .venv/bin/python tests/learning_cost_test.py

Il report di analisi dice "tre store ALWAYS, tre inferenze in piu' per
turno". La prima meta' e' vera per costruzione; la seconda no, ed e' la
misura che serve prima di decidere se il prezzo vale la qualita'.

`LearningMachine.process` chiama `store.process` una volta per store, e ogni
store ALWAYS estrae con una o due chiamate al modello a seconda di cosa
risponde. Agno richiama il modello dopo la tool call per sentirgli dire che ha
finito, e la risposta di quella chiamata non la legge nessuno: era una
chiamata in piu' per profilo e per memorie, due delle cinque di un turno. Il
contesto di sessione la evitava gia' con `stop_after_tool_call`, e ora la
evitano anche loro (`senza_conferma` in `agent/learning.py`). Un
modello che non chiama affatto lo strumento, invece, costa i tentativi del
contesto.

Qui il modello di apprendimento e' finto e conta: nessuna rete e nessun
modello scaricato, cosi' il numero di chiamate e' una proprieta' del
framework e della politica invece che di un copione. Accanto al numero si
misura il peso: ogni estrazione rimanda al modello la conversazione intera, e
questa prova lo verifica invece di darlo per scontato. Il numero di chiamate
e' asserito, non solo stampato: un Agno che reintroducesse la conferma deve
rendere rossa la prova, non alzare una cifra nel rapporto.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from typing import Any
from unittest.mock import patch

from _comune import esigi, fallimento, ok, prepara_ambiente, pulisci

# I percorsi vanno scelti prima di importare config, che li legge una volta
# sola all'import.
RADICE_PROVA = prepara_ambiente("learning-cost-test")

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

# Gli strumenti con cui uno store ALWAYS scrive, e l'ordine in cui il modello
# finto li cerca. Sono i nomi che Agno da' alle funzioni di estrazione: uno
# per store, quindi il nome dice anche da quale store arriva la chiamata. La
# conferma di profilo e memorie rimanda gli stessi strumenti, quindi il
# secondo invio si riconosce allo stesso modo del primo.
STRUMENTI_DI_SALVATAGGIO = ("save_session_context", "update_profile", "add_memory", "update_memory")

# Gli store che la politica predefinita tiene accesi in `ALWAYS`, uno per
# strumento, e quindi le chiamate che un turno completato deve costare: dal
# 22 settembre 2026 la conferma dopo la tool call non si paga piu' nemmeno su
# profilo e memorie.
STORE_ALWAYS = ("update_profile", "add_memory", "save_session_context")
CHIAMATE_PER_TURNO = len(STORE_ALWAYS)

# Cosa risponde il modello finto quando decide di scrivere. Il profilo vuole
# almeno un campo, o la scrittura non cambia niente; le memorie vogliono il
# testo; il contesto un riepilogo.
ARGOMENTI_DI_SALVATAGGIO: dict[str, dict[str, Any]] = {
    "update_profile": {"communication_style": "risposte brevi, senza preamboli"},
    "add_memory": {"memory": "Lavora su Ares la sera, dopo le 22."},
    "save_session_context": {"summary": "Misura del costo delle estrazioni."},
}

# La prima riga del turno finto: sta in ogni copia della conversazione che le
# estrazioni rimandano al modello, e serve a verificarlo.
APERTURA = "Come organizzo i moduli di Ares?"


def tool_call(nome: str, **argomenti: Any) -> dict[str, Any]:
    return {
        "id": "call-" + nome,
        "type": "function",
        "function": {"name": nome, "arguments": json.dumps(argomenti)},
    }


def nome_strumento(funzione: Any) -> str:
    """Il nome di uno strumento, come lo vede il modello.

    Agno formatta le funzioni in dizioni prima di passarle al provider, ma
    accetta anche gli oggetti `Function`: la prova legge entrambi perche' non
    dipende da quale delle due forme arrivi.
    """
    if isinstance(funzione, dict):
        return str(funzione.get("name") or (funzione.get("function") or {}).get("name") or "")
    return str(getattr(funzione, "name", ""))


def schema_strumento(funzione: Any) -> str:
    """Lo schema di uno strumento come testo, per misurarne il peso."""
    return json.dumps(funzione if isinstance(funzione, dict) else funzione.to_dict())


def conversazione() -> list[Message]:
    """Un turno di lunghezza credibile: due domande, due risposte.

    Non e' una conversazione vera: e' il testo su cui si misura quanto pesa
    rimandarlo al modello piu' volte. La lunghezza sta nell'ordine di un turno
    normale, cosi' i caratteri contati sono rappresentativi invece che comodi.
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

    Con `ubbidiente=True` la prima chiamata che porta uno strumento di
    salvataggio risponde con la tool call, come farebbe un modello che segue
    le istruzioni; la seconda, se arriva, e' la conferma e non ha tool call.
    Con `ubbidiente=False` non chiama mai lo strumento: e' il caso del modello
    piccolo che non ubbidisce, e il contesto lo paga con i suoi tentativi.

    `__deepcopy__` restituisce se stesso: gli store estraggono su una copia
    del modello, e un contatore copiato conterebbe su un oggetto che nessuno
    legge.
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


def estrai(finto: ModelloConta, politica: Any = None) -> Any:
    """Un turno completato, e la macchina di apprendimento che l'ha estratto.

    Le chiamate restano su `finto.chiamate`: il contatore e' del modello, non
    della macchina. La macchina si restituisce per i controlli che leggono cio'
    che e' finito negli store, non solo quante volte il modello e' stato
    chiamato.
    """
    with patch.object(learning, "build_learning_model", lambda impostazioni: finto):
        macchina = build_learning_machine(build_db(PERCORSI), None, UTENTE, IMPOSTAZIONI, politica or POLITICA)
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

    Le istruzioni sono il primo messaggio di ogni invio - le regole di
    estrazione e la descrizione dei campi -, la conversazione e' il messaggio
    che porta il turno, e gli schemi sono gli strumenti che il modello puo'
    chiamare. Il totale conta anche i messaggi di servizio della conferma.
    """
    istruzioni = sum(len(chiamata["messaggi"][0]) for chiamata in chiamate if chiamata["messaggi"])
    turno = sum(len(testo) for chiamata in chiamate for testo in chiamata["messaggi"] if APERTURA in testo)
    schemi = sum(chiamata["schemi"] for chiamata in chiamate)
    totale = sum(sum(len(testo) for testo in chiamata["messaggi"]) + chiamata["schemi"] for chiamata in chiamate)
    return istruzioni, turno, schemi, totale


def costo_delle_tre_estrazioni() -> str:
    """Le chiamate per store con la politica predefinita, e il loro peso.

    Il numero che conta e' il primo: una per store ALWAYS, perche' la
    conferma che Agno scarta e' stata tolta anche a profilo e memorie. Il
    peso dice l'altra meta': ogni estrazione rimanda la conversazione intera,
    e paga soprattutto le istruzioni dello store e lo schema del suo
    strumento.
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

    E' il rovescio della misura: se il numero restasse lo stesso, la politica
    non starebbe decidendo niente e il costo non sarebbe governabile. Dopo la
    mitigazione ogni store costa esattamente una chiamata, quindi il conto e'
    esatto e non un "meno di prima". Con tutti e tre spenti restano entita' e
    intuizioni, che sono AGENTIC e non estraggono da sole: zero chiamate.
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

    Profilo e memorie non ritentano: una chiamata a vuoto e basta, e senza
    tool call non c'e' nemmeno una conferma da saltare. Il contesto di
    sessione invece ritenta fino al tetto della politica, quindi un modello
    piccolo che non scrive costa piu' di uno che ubbidisce: e' l'unico caso
    in cui la mitigazione non cambia niente.
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
    """Il flag toglie la conferma, non la scrittura.

    `stop_after_tool_call` ferma il ciclo del modello *dopo* che la tool call
    e' stata eseguita: se un giorno Agno lo interpretasse come "non eseguire
    affatto", il costo scenderebbe e la memoria resterebbe vuota, e la prova
    del costo qui sopra sarebbe verde lo stesso. Qui si legge cio' che il
    modello finto ha scritto: il profilo con il campo che ha passato, le
    memorie con il loro testo.
    """
    macchina = estrai(ModelloConta())
    profilo = macchina.user_profile_store.get(user_id=UTENTE.id)
    memorie = macchina.user_memory_store.get(user_id=UTENTE.id)
    esigi(profilo is not None, "il profilo e' vuoto dopo una tool call accettata")
    esigi(
        ARGOMENTI_DI_SALVATAGGIO["update_profile"]["communication_style"] in str(profilo),
        "il profilo non contiene cio' che il modello ha scritto: " + str(profilo),
    )
    esigi(memorie is not None, "le memorie sono vuote dopo una tool call accettata")
    esigi(
        ARGOMENTI_DI_SALVATAGGIO["add_memory"]["memory"] in str(memorie),
        "le memorie non contengono cio' che il modello ha scritto: " + str(memorie),
    )
    return "profilo e memorie scritti nonostante il flag sulla tool call"


def main() -> int:
    riuscita = False
    try:
        ok("costo estrazioni", costo_delle_tre_estrazioni())
        ok("politica spegne il costo", la_politica_spegne_il_costo())
        ok("modello che non ubbidisce", il_modello_che_non_ubbidisce())
        ok("scrittura negli store", la_scrittura_arriva_negli_store())
        riuscita = True
        return 0
    except Exception as errore:
        fallimento(errore)
        return 1
    finally:
        if riuscita:
            pulisci(RADICE_PROVA)
        else:
            print("Archivio della prova conservato:", RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
