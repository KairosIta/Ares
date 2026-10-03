"""Cio' che l'estrazione scrive ha un appiglio nel testo
=====================================================
Uso:
    .venv/bin/python tests/radicamento_test.py

Due parti. La tabella prova i verdetti di `ares/agent/radicamento.py` su
valori presi dai rapporti degli eval: le invenzioni dei modelli piccoli
(uno stack mai nominato, una professione, un nome, i segnaposto) e le
parafrasi giuste che non devono cadere. Poi un estrattore finto scrive
profilo e memorie attraverso gli store veri: cio' che non si radica non
arriva all'archivio, gli scarti si leggono una volta, e un valore riscritto
per intero conserva le voci che lo store conteneva gia'.
"""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from typing import Any
from unittest.mock import patch

from _comune import chiudi, esegui, esigi, prepara_ambiente

# I percorsi vanno scelti prima di importare config, che li legge una volta
# sola all'import.
RADICE_PROVA = prepara_ambiente("radicamento-test")

from _doppi import tool_call  # noqa: E402
from agno.models.base import Model  # noqa: E402
from agno.models.message import Message, MessageMetrics  # noqa: E402
from agno.models.response import ModelResponse  # noqa: E402

from ares import config  # noqa: E402
from ares.agent import learning  # noqa: E402
from ares.agent.echo import prendi_scarti  # noqa: E402
from ares.agent.learning import build_learning_machine  # noqa: E402
from ares.agent.radicamento import Fonte, radica_campo, radica_memoria  # noqa: E402
from ares.agent.runtime import build_db  # noqa: E402
from ares.agent.schemas import AresMemories, AresProfile  # noqa: E402
from ares.state.identita import Utente  # noqa: E402

PERCORSI = config.leggi_percorsi()
IMPOSTAZIONI = config.leggi_impostazioni()
# Il contesto di sessione con la tool call invece che dal `.env`: l'estrattore
# finto risponde solo con tool call.
CON_STRUMENTI = replace(IMPOSTAZIONI, apprendimento="estrattore-finto:9b", vincolo_estrazione=False)
POLITICA = config.leggi_politica()

# Campo, valore proposto, testo della conversazione, valore atteso (None se
# tutto scartato), voci scartate.
CAMPI = (
    (
        "tools_and_stack",
        "Python, JavaScript, React, Helix",
        "Ho cambiato idea sull'editor: ora uso Helix.",
        "Helix",
        ("Python", "JavaScript", "React"),
    ),
    (
        "expertise",
        "Sviluppo software; uso di Vim come editor di testo preferito",
        "Il mio editor di testo preferito e' Vim, lo uso per tutto.",
        "uso di Vim come editor di testo preferito",
        ("Sviluppo software",),
    ),
    (
        "expertise",
        "Vim come editor preferito, usato per tutto",
        "Il mio editor preferito e' Vim, lo uso per tutto.",
        "Vim come editor preferito, usato per tutto",
        (),
    ),
    (
        "tools_and_stack",
        "Markdown (per appunti, note), Docker",
        "Per tenere in ordine gli appunti uso file Markdown.",
        "Markdown (per appunti, note)",
        ("Docker",),
    ),
    ("tools_and_stack", "Go, C", "Ciao.", "Go, C", ()),
    ("name", "Gym member", "Il numero della mia tessera della palestra e' GYM-5521.", None, ("Gym member",)),
    ("name", "Prova", "Mi chiamo Prova e uso Linux.", "Prova", ()),
    ("occupation", "Sconosciuto", "Mi chiamo Prova.", None, ("Sconosciuto",)),
    ("timezone", "Non specificato", "Mi chiamo Prova.", None, ("Non specificato",)),
    ("occupation", "Sviluppatore software", "Ho deciso di realizzare ORIONE-42.", None, ("Sviluppatore software",)),
    (
        "occupation",
        "Lavora in un ufficio con stampante condivisa",
        "La stampante dell'ufficio e' condivisa con altri due colleghi.",
        "Lavora in un ufficio con stampante condivisa",
        (),
    ),
    (
        "current_focus",
        "Possibilita' di trasferimento a Milano, ancora in valutazione e non decisa",
        "Potrei trasferirmi a Milano, ma non ho deciso.",
        "Possibilita' di trasferimento a Milano, ancora in valutazione e non decisa",
        (),
    ),
    (
        "current_focus",
        "ORIONE-42: progetto personale attuale, decisione confermata; avvio non confermato",
        "Ho deciso di realizzare ORIONE-42.",
        "ORIONE-42: progetto personale attuale, decisione confermata; avvio non confermato",
        (),
    ),
    ("language", "italiano", "Ciao, come stai?", "italiano", ()),
    ("language", "Non specificato", "Ciao, come stai?", None, ("Non specificato",)),
    ("timezone", "Europe/Rome", "Vivo a Roma.", "Europe/Rome", ()),
    ("name", "王伟", "Mi chiamo 王伟.", "王伟", ()),
    ("name", "Δημήτρης", "Sono Δημήτρης, piacere.", "Δημήτρης", ()),
    ("name", "Δημήτρης", "Ciao.", None, ("Δημήτρης",)),
    ("occupation", "—", "Ciao.", None, ("—",)),
)

# Memoria, testo della conversazione, se resta.
MEMORIE = (
    ("Lavora su Ares la sera, dopo le 22.", "Lavoro su Ares quasi sempre la sera.", True),
    ("Il numero della tessera della palestra e' GYM-5521.", "La mia tessera e' GYM-5521.", True),
    ("Possiede un gatto di nome Micio.", "Ho cambiato editor: ora uso Helix.", False),
    ("Non specificato", "Ho cambiato editor: ora uso Helix.", False),
    ("Il suo amico si chiama Δημήτρης.", "Sono uscito con Δημήτρης.", True),
)


def tabella_dei_campi() -> str:
    for campo, valore, testo, atteso, scartato in CAMPI:
        verdetto = radica_campo(campo, valore, Fonte.da_testi([testo]))
        esigi(
            verdetto.tenuto == atteso and verdetto.scartato == scartato,
            campo + " " + repr(valore) + ": " + repr(verdetto) + ", atteso " + repr((atteso, scartato)),
        )
    return str(len(CAMPI)) + " valori: elenchi per voce, nomi per parola in ogni alfabeto, segnaposto, parafrasi"


def tabella_delle_memorie() -> str:
    for memoria, testo, resta in MEMORIE:
        verdetto = radica_memoria(memoria, Fonte.da_testi([testo]))
        esigi((verdetto.tenuto is not None) == resta, repr(memoria) + ": " + repr(verdetto))
        esigi(resta or verdetto.scartato == (memoria,), "lo scarto non riporta la memoria: " + repr(verdetto))
    return str(len(MEMORIE)) + " memorie: una parola in comune basta, nessuna no"


class EstrattoreFinto(Model):
    """Risponde a ogni store con la tool call scritta per il suo strumento, una volta.

    `__deepcopy__` restituisce se stesso: gli store estraggono su una copia
    del modello, e cio' che e' gia' stato scritto deve restare noto.
    """

    def __init__(self, argomenti: dict[str, dict[str, Any]]) -> None:
        super().__init__(id="estrattore-finto", name="estrattore-finto", provider="test")
        self.argomenti = argomenti
        self.scritti: set[str] = set()

    def __deepcopy__(self, memo: dict) -> EstrattoreFinto:
        return self

    def _risposta(self, tools: Any) -> ModelResponse:
        for funzione in tools or []:
            nome = funzione.get("function", {}).get("name") if isinstance(funzione, dict) else funzione.name
            if nome in self.argomenti and nome not in self.scritti:
                self.scritti.add(nome)
                return ModelResponse(
                    role="assistant",
                    tool_calls=[tool_call(nome, **self.argomenti[nome])],
                    response_usage=MessageMetrics(),
                )
        return ModelResponse(role="assistant", content="Niente da aggiornare.", response_usage=MessageMetrics())

    def invoke(self, messages: Any, tools: Any = None, **kwargs: Any) -> ModelResponse:
        return self._risposta(tools)

    async def ainvoke(self, messages: Any, tools: Any = None, **kwargs: Any) -> ModelResponse:
        return self._risposta(tools)

    def invoke_stream(self, messages: Any, tools: Any = None, **kwargs: Any) -> Iterator[ModelResponse]:
        yield self._risposta(tools)

    async def ainvoke_stream(self, messages: Any, tools: Any = None, **kwargs: Any) -> AsyncIterator[ModelResponse]:
        yield self._risposta(tools)

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response


class AgenteFinto:
    """Quanto `prendi_scarti` legge di un agente: utente e macchina di apprendimento."""

    def __init__(self, macchina: Any, utente: Utente) -> None:
        self.learning_machine = macchina
        self.user_id = utente.id


def estrai(utente: Utente, argomenti: dict[str, dict[str, Any]], testo: str, impostazioni: Any = CON_STRUMENTI) -> Any:
    """La macchina di apprendimento dopo un turno estratto dall'estrattore finto."""
    # Il contesto di sessione non si radica: scrive sempre, e il retry tace.
    finto = EstrattoreFinto({"save_session_context": {"summary": "Un turno di prova."}, **argomenti})
    with patch.object(learning, "build_learning_model", lambda impostazioni: finto):
        macchina = build_learning_machine(build_db(PERCORSI), None, utente, CON_STRUMENTI, POLITICA)
    macchina.process_completed_run(
        messages=[Message(role="user", content=testo), Message(role="assistant", content="Va bene.")],
        user_id=utente.id,
        session_id="radicamento-" + utente.id,
        agent_id="radicamento",
    )
    return macchina


def l_archivio_riceve_solo_il_radicato() -> str:
    utente = Utente.da_grezzo("radicamento")
    macchina = estrai(
        utente,
        {
            "update_profile": {
                "tools_and_stack": "Helix, Kubernetes",
                "occupation": "Sviluppatore software",
                "communication_style": "risposte brevi",
            },
            "add_memory": {"memory": "Possiede un gatto di nome Micio."},
        },
        "Ho cambiato editor: ora uso Helix. Rispondimi breve.",
    )
    profilo = macchina.user_profile_store.get(user_id=utente.id)
    esigi(profilo is not None and profilo.tools_and_stack == "Helix", "stack nel profilo: " + repr(profilo))
    esigi(not profilo.occupation, "la professione inventata e' nel profilo: " + repr(profilo.occupation))
    esigi(profilo.communication_style == "risposte brevi", "lo stile detto non e' nel profilo: " + repr(profilo))
    memorie = macchina.user_memory_store.get(user_id=utente.id)
    esigi(not getattr(memorie, "memories", None), "la memoria inventata e' nell'archivio: " + repr(memorie))

    agente = AgenteFinto(macchina, utente)
    scarti = prendi_scarti(agente)
    attesi = {
        "profilo tools_and_stack: Kubernetes",
        "profilo occupation: Sviluppatore software",
        "memoria: Possiede un gatto di nome Micio.",
    }
    esigi(set(scarti) == attesi, "scarti: " + repr(scarti))
    esigi(prendi_scarti(agente) == [], "gli scarti si rileggono due volte")
    return "Kubernetes, professione e gatto restano fuori; gli scarti si leggono una volta"


def il_gia_noto_resta() -> str:
    """Un elenco riscritto per intero tiene le voci che lo store conteneva gia'."""
    utente = Utente.da_grezzo("radicamento-noto")
    store = estrai(utente, {}, "Ciao.").user_profile_store
    store.save(user_id=utente.id, profile=AresProfile(user_id=utente.id, tools_and_stack="Python"))
    macchina = estrai(utente, {"update_profile": {"tools_and_stack": "Python, Helix"}}, "Ora uso anche Helix.")
    profilo = macchina.user_profile_store.get(user_id=utente.id)
    esigi(profilo.tools_and_stack == "Python, Helix", "stack dopo la riscrittura: " + repr(profilo.tools_and_stack))
    esigi(macchina.user_profile_store.prendi_scarti() == [], "una voce gia' nota e' stata scartata")
    return "Python, gia' nel profilo e non nominato nel turno, resta accanto a Helix"


def il_doppione_identico_non_entra() -> str:
    """Una memoria che c'e' gia', a meno di maiuscole e punto finale, non si salva una seconda volta."""
    utente = Utente.da_grezzo("radicamento-doppione")
    store = estrai(utente, {}, "Ciao.").user_memory_store
    gia = AresMemories(user_id=utente.id)
    gia.add_memory("Usa Helix come editor.")
    store.save(utente.id, gia)
    testo = "Uso sempre Helix: usa helix come editor, ormai."
    macchina = estrai(utente, {"add_memory": {"memory": "usa  Helix come editor"}}, testo)
    memorie = macchina.user_memory_store.get(user_id=utente.id).memories
    esigi([m["content"] for m in memorie] == ["Usa Helix come editor."], "memorie: " + repr(memorie))
    scarti = macchina.user_memory_store.prendi_scarti()
    esigi(scarti == [], "un doppione e' finito fra gli scarti dell'eco: " + repr(scarti))
    return "la riscrittura identica resta fuori, senza figurare fra gli scarti"


def la_fonte_e_solo_il_testo_valido() -> str:
    """Superate, date, `source` e provenienza delle memorie non radicano una memoria nuova."""
    utente = Utente.da_grezzo("radicamento-fonte")
    store = estrai(utente, {}, "Ciao.").user_memory_store
    gia = AresMemories(user_id=utente.id)
    ritirata = gia.add_memory("Lavora come medico a Torino.")
    gia.delete_memory(ritirata)
    gia.add_memory("Usa Helix come editor.", source="Ciao, lavoro come medico a Torino.")
    gia.memories[0].update(
        updated_at="2026-10-10T10:10:10+00:00", sessione="s-10", turno="r-10", cartella="/home/prova/office"
    )
    store.save(utente.id, gia)
    for memoria in ("Lavora come medico.", "Corre 10 km.", "Lavora in home office."):
        macchina = estrai(utente, {"add_memory": {"memory": memoria}}, "Ciao.")
        scarti = macchina.user_memory_store.prendi_scarti()
        esigi(scarti == ["memoria: " + memoria], memoria + " si radica fuori dal testo valido: " + repr(scarti))
    restano = [m["content"] for m in macchina.user_memory_store.get(user_id=utente.id).memories]
    esigi(restano == ["Usa Helix come editor."], "memorie: " + repr(restano))
    return "una memoria ritirata, una data, un source e una cartella non radicano niente"


def la_richiesta_e_la_fonte() -> str:
    """`update_user_memory` passa dal radicamento: la fonte e' il testo della richiesta."""
    utente = Utente.da_grezzo("radicamento-richiesta")
    store = estrai(utente, {}, "Ciao.").user_memory_store
    finto = EstrattoreFinto({"add_memory": {"memory": "Possiede un gatto di nome Micio."}})
    store.config.model = finto
    store.run_memories_update(task="Ricorda che usa Helix come editor.", user_id=utente.id)
    esigi(store.get(user_id=utente.id) is None, "la memoria inventata e' nell'archivio")
    esigi(store.prendi_scarti() == ["memoria: Possiede un gatto di nome Micio."], "lo scarto non arriva all'eco")
    finto.scritti.clear()
    finto.argomenti = {"add_memory": {"memory": "Usa Helix come editor."}}
    store.run_memories_update(task="Ricorda che usa Helix come editor.", user_id=utente.id)
    restano = [m["content"] for m in store.get(user_id=utente.id).memories]
    esigi(restano == ["Usa Helix come editor."], "la memoria richiesta non e' salvata: " + repr(restano))
    return "dalla richiesta passa cio' che dice, non un'invenzione"


class EstrattoreTrattenuto(EstrattoreFinto):
    """La prima tool call aspetta `via` prima di arrivare allo store; le altre chiamate no."""

    def __init__(self, argomenti: dict[str, dict[str, Any]]) -> None:
        super().__init__(argomenti)
        self.fermo, self.via = threading.Event(), threading.Event()

    def invoke(self, messages: Any, tools: Any = None, **kwargs: Any) -> ModelResponse:
        risposta = self._risposta(tools)
        if risposta.tool_calls and not self.fermo.is_set():
            self.fermo.set()
            self.via.wait(10)
        return risposta


def estrazioni_intrecciate() -> str:
    """Un'estrazione che finisce mentre un'altra aspetta il modello non le toglie la fonte."""
    utente = Utente.da_grezzo("radicamento-fili")
    store = estrai(utente, {}, "Ciao.").user_memory_store
    finto = EstrattoreTrattenuto({"add_memory": {"memory": "Possiede un gatto di nome Micio."}})
    store.config.model = finto

    def estrazione(testo: str) -> None:
        store.extract_and_save(messages=[Message(role="user", content=testo)], user_id=utente.id)

    lenta = threading.Thread(target=estrazione, args=("Ho cambiato editor: ora uso Helix.",))
    lenta.start()
    esigi(finto.fermo.wait(10), "l'estrazione lenta non e' arrivata al modello")
    estrazione("Ciao.")
    finto.via.set()
    lenta.join(10)
    esigi(store.get(user_id=utente.id) is None, "la memoria inventata e' passata senza fonte")
    esigi(store.prendi_scarti() == ["memoria: Possiede un gatto di nome Micio."], "lo scarto della lenta e' perso")

    # Dopo un Ctrl-C il cancello e' chiuso: cio' che il filo rimasto appeso
    # scarta non arriva all'eco del turno dopo.
    cancello = learning.Cancello()
    cancello.chiudi()
    finto.scritti.clear()
    contesto = {"messages": [Message(role="user", content="Ciao.")], "user_id": utente.id}
    learning._estrai(store, contesto, cancello)
    esigi(store.prendi_scarti() == [], "gli scarti di un'estrazione interrotta arrivano all'eco")
    return "la fonte resta a chi la usa; gli scarti dopo un Ctrl-C non arrivano al turno dopo"


def main() -> int:
    falliti, _ = esegui(
        (
            ("tabella dei campi", tabella_dei_campi),
            ("tabella delle memorie", tabella_delle_memorie),
            ("archivio radicato", l_archivio_riceve_solo_il_radicato),
            ("gia' noto", il_gia_noto_resta),
            ("doppione identico", il_doppione_identico_non_entra),
            ("fonte valida", la_fonte_e_solo_il_testo_valido),
            ("richiesta", la_richiesta_e_la_fonte),
            ("estrazioni intrecciate", estrazioni_intrecciate),
        )
    )
    return chiudi(falliti, RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
