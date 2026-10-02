"""Gli ambiti, come li tratta il framework
=======================================

Due premesse dello studio sugli ambiti (`docs/project-scopes.md`, par. 3.4):

- una chiave in piu' in una voce di memoria sopravvive alle riscritture di
  Agno senza arrivare al modello;
- il filtro per namespace delle intuizioni si applica sui metadati *dopo*
  il limite, mentre quello dell'owner e' un prefiltro del motore.

Non provano la proposta, che non esiste ancora: ne provano le premesse, e
se Agno le cambia la prova se ne accorge.

Offline: il modello e' il copione di `_doppi.py`, l'embedder ha vettori
fissi, e i documenti arrivano a LanceDB gia' vettorizzati.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from _comune import chiudi, esegui, esigi, prepara_ambiente

# Percorsi scelti prima di importare `ares`: la radice usa-e-getta e' anche
# dove LanceDB scrive le sue tabelle.
RADICE_PROVA = prepara_ambiente("ambiti-test")

from _doppi import ModelloACopione, tool_call  # noqa: E402
from agno.db.sqlite import SqliteDb  # noqa: E402
from agno.knowledge.document import Document  # noqa: E402
from agno.knowledge.embedder.base import Embedder  # noqa: E402
from agno.learn import LearningMode, UserMemoryConfig  # noqa: E402
from agno.learn.stores import UserMemoryStore  # noqa: E402
from agno.models.message import Message  # noqa: E402
from agno.vectordb.lancedb.lance_db import LanceDb  # noqa: E402
from agno.vectordb.search import SearchType  # noqa: E402

from ares.agent.echo import Istantanea, annota_provenienza, istantanea, ripristina  # noqa: E402
from ares.agent.schemas import AresMemories  # noqa: E402
from ares.cli.log import configura_log_agno  # noqa: E402

UTENTE = "prova-ambiti"

# Il valore della provenienza e' distintivo apposta: la prova lo cerca dentro
# i messaggi che il modello riceve, e un valore comune - "alfa" - potrebbe
# comparire per caso nelle istruzioni di Agno.
PROVENIENZA = "progetto-alfa-9f3a"

MESSAGGI_ALFA = [
    Message(role="user", content="In questo repository le migrazioni si scrivono a mano."),
    Message(role="assistant", content="Annotato."),
]
MESSAGGI_BETA = [
    Message(role="user", content="Qui invece le migrazioni sono automatiche."),
    Message(role="assistant", content="Annotato."),
]


class ModelloCheRicorda(ModelloACopione):
    """Il copione, piu' i messaggi della prima chiamata (il prompt di estrazione).

    `__deepcopy__` restituisce se stesso perche' `extract_and_save` lavora su
    una copia del modello, come per `ModelloContesto` in
    `agno_contract_test.py`.
    """

    def __init__(self, nome: str, copione: list[list[dict[str, Any]]] | None = None) -> None:
        super().__init__(nome, copione)
        self.messaggi: list[Any] = []

    def __deepcopy__(self, memo: dict[int, Any]) -> ModelloCheRicorda:
        return self

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        if not self.messaggi:
            self.messaggi = list(kwargs.get("messages") or (args[0] if args else []))
        return super().invoke(*args, **kwargs)


def _memorie(db: SqliteDb) -> list[dict[str, Any]]:
    """Le voci di memoria dell'utente come stanno in archivio."""
    riga = db.get_learning(learning_type="user_memory", user_id=UTENTE)
    if not riga:
        return []
    contenuto = riga["content"]
    if isinstance(contenuto, str):
        contenuto = json.loads(contenuto)
    return list(contenuto["memories"])


def _testo_dei_messaggi(messaggi: list[Any]) -> str:
    return "\n".join(str(getattr(messaggio, "content", "")) for messaggio in messaggi)


def provenienza_memorie() -> str:
    """Una chiave in piu' sopravvive, non arriva al modello, e si riallinea."""
    db = SqliteDb(db_file=str(RADICE_PROVA / "memo.db"))
    modello = ModelloCheRicorda("memoria", [[tool_call("add_memory", memory="Le migrazioni si scrivono a mano.")]])
    store = UserMemoryStore(
        config=UserMemoryConfig(
            db=db,
            model=modello,
            mode=LearningMode.ALWAYS,
            schema=AresMemories,
        )
    )
    store.extract_and_save(messages=MESSAGGI_ALFA, user_id=UTENTE, agent_id="ares")

    dopo = _memorie(db)
    esigi(len(dopo) == 1, "l'estrazione non ha scritto una memoria: " + str(dopo))
    esigi(
        "source" in dopo[0] and "added_by_agent" in dopo[0],
        "Agno non scrive piu' le sue chiavi nella voce: " + str(sorted(dopo[0])) + ". La prova presume che una "
        "chiave in piu' sia la forma normale della voce, non un'eccezione.",
    )
    esigi(
        PROVENIENZA not in _testo_dei_messaggi(modello.messaggi),
        "il prompt di estrazione vede la provenienza: il modello potrebbe classificare da solo, e la proposta del "
        "§6.2 va rivista invece che riallineata in codice.",
    )

    # Ares scrive la provenienza dopo il turno, con le stesse API che
    # `ares/agent/echo.py` usa per l'istantanea e il ripristino.
    dati = store.get(user_id=UTENTE)
    for voce in dati.memories:
        voce["progetto"] = PROVENIENZA
    store.save(user_id=UTENTE, memories=dati)
    esigi(_memorie(db)[0].get("progetto") == PROVENIENZA, "la scrittura di Ares non e' arrivata in archivio")

    # Una seconda estrazione, da un altro progetto, riscrive il contenuto:
    # la provenienza sopravvive, ed e' proprio il difetto che il
    # riallineamento di Ares deve coprire.
    identificativo = _memorie(db)[0]["id"]
    store.config.model = ModelloCheRicorda(
        "memoria",
        [
            [
                tool_call(
                    "update_memory", memory_id=identificativo, memory="Le migrazioni si scrivono a mano e si provano."
                )
            ]
        ],
    )
    store.extract_and_save(messages=MESSAGGI_BETA, user_id=UTENTE, agent_id="ares")
    riscritta = _memorie(db)[0]
    esigi(
        riscritta["content"] == "Le migrazioni si scrivono a mano e si provano.",
        "la riscrittura non e' avvenuta: " + str(riscritta.get("content")),
    )
    esigi(
        riscritta.get("progetto") == PROVENIENZA,
        "la provenienza non e' sopravvissuta alla riscrittura: " + str(riscritta.get("progetto")) + ". Se Agno "
        "rifacesse la voce da capo, il riallineamento di Ares sarebbe l'unico modo di tenerla, non un di piu'.",
    )

    dati = store.get(user_id=UTENTE)
    dati.memories[0]["progetto"] = "progetto-beta-4c1d"
    store.save(user_id=UTENTE, memories=dati)
    esigi(
        _memorie(db)[0].get("progetto") == "progetto-beta-4c1d",
        "il riallineamento non ha cambiato la provenienza: " + str(_memorie(db)[0].get("progetto")),
    )

    # La resa di Ares riceve le voci come stanno in archivio: e' il punto in
    # cui il filtro per ambito si puo' applicare.
    letto = AresMemories.from_dict(db.get_learning(learning_type="user_memory", user_id=UTENTE)["content"])
    esigi(
        letto is not None and letto.memories[0].get("progetto") == "progetto-beta-4c1d",
        "AresMemories.from_dict perde la provenienza: il filtro in resa non avrebbe niente da filtrare",
    )
    esigi(
        "progetto-beta-4c1d" not in letto.get_memories_text(),
        "la resa stampa la chiave nel prompt: e' un dato di servizio, e non deve arrivare al modello",
    )

    return "provenienza scritta, sopravvissuta alla riscrittura, riallineata e invisibile al modello"


def _store_memorie(nome: str, copione: list[list[dict[str, Any]]]) -> tuple[UserMemoryStore, SqliteDb]:
    db = SqliteDb(db_file=str(RADICE_PROVA / (nome + ".db")))
    store = UserMemoryStore(
        config=UserMemoryConfig(
            db=db, model=ModelloCheRicorda(nome, copione), mode=LearningMode.ALWAYS, schema=AresMemories
        )
    )
    return store, db


def _estrai_con(store: UserMemoryStore, chiamata: dict[str, Any]) -> ModelloCheRicorda:
    modello = ModelloCheRicorda("memoria", [[chiamata]])
    store.config.model = modello
    store.extract_and_save(messages=MESSAGGI_BETA, user_id=UTENTE, agent_id="ares")
    return modello


def memorie_superate() -> str:
    """Una correzione o una cancellazione lascia la vecchia fra le superate, fuori dal prompt."""
    store, _ = _store_memorie("superate", [[tool_call("add_memory", memory="Le migrazioni si scrivono a mano.")]])
    store.extract_and_save(messages=MESSAGGI_ALFA, user_id=UTENTE, agent_id="ares")
    identificativo = store.get(user_id=UTENTE).memories[0]["id"]

    modello = _estrai_con(
        store, tool_call("update_memory", memory_id=identificativo, memory="Le migrazioni sono automatiche.")
    )
    dati = store.get(user_id=UTENTE)
    esigi([m["content"] for m in dati.memories] == ["Le migrazioni sono automatiche."], "valide: " + str(dati.memories))
    esigi(len(dati.superate) == 1, "la correzione non lascia una superata: " + str(dati.superate))
    vecchia = dati.superate[0]
    esigi(
        vecchia["content"] == "Le migrazioni si scrivono a mano."
        and vecchia["sostituita_da"] == identificativo
        and vecchia.get("invalidata_il"),
        "la superata non dice quando e da chi: " + str(vecchia),
    )
    esigi("si scrivono a mano" not in dati.get_memories_text(), "la superata arriva nel prompt")

    # La superata non arriva nemmeno all'estrattore: alla prossima estrazione
    # vede solo la valida.
    modello = _estrai_con(store, tool_call("delete_memory", memory_id=identificativo))
    prompt = _testo_dei_messaggi(modello.messaggi)
    esigi("si scrivono a mano" not in prompt, "l'estrattore vede la superata")
    esigi("sono automatiche" in prompt, "l'estrattore non vede la valida")
    dati = store.get(user_id=UTENTE)
    esigi(dati.memories == [], "la cancellazione lascia una valida: " + str(dati.memories))
    esigi(
        len(dati.superate) == 2 and "sostituita_da" not in dati.superate[1],
        "la cancellazione non diventa una superata senza sostituta: " + str(dati.superate),
    )

    # Riscrivere lo stesso testo non e' una correzione.
    stesso = AresMemories(user_id=UTENTE)
    voce = stesso.add_memory("Usa Debian.")
    stesso.update_memory(voce, "Usa Debian.")
    esigi(stesso.superate == [], "una riscrittura identica lascia una superata")
    return "correzione e cancellazione lasciano due superate, fuori dal prompt e dall'estrattore"


class AgenteDellaProva:
    """Quanto `echo` legge di un agente: macchina, utente, sessione, id."""

    def __init__(self, store: UserMemoryStore) -> None:
        self.learning_machine = type("Macchina", (), {"user_profile_store": None, "user_memory_store": store})()
        self.user_id = UTENTE
        self.session_id = "sessione-prova"
        self.id = "ares"


def provenienza_del_turno() -> str:
    """Il turno annota solo le memorie che ha scritto, e un ripristino toglie le annotazioni."""
    store, _ = _store_memorie("turno", [[tool_call("add_memory", memory="Le migrazioni si scrivono a mano.")]])
    store.extract_and_save(messages=MESSAGGI_ALFA, user_id=UTENTE, agent_id="ares")
    agente = AgenteDellaProva(store)
    prima = istantanea(agente)

    dal = datetime.now(UTC)
    _estrai_con(store, tool_call("add_memory", memory="Le migrazioni sono automatiche."))
    toccate = annota_provenienza(agente, dal=dal, turno="run-7", cartella="/progetti/alfa")
    voci = {m["content"]: m for m in store.get(user_id=UTENTE).memories}
    nuova, vecchia = voci["Le migrazioni sono automatiche."], voci["Le migrazioni si scrivono a mano."]
    esigi(toccate == 1, "annotate " + str(toccate) + " memorie invece di una")
    esigi(
        (nuova.get("sessione"), nuova.get("turno"), nuova.get("cartella"))
        == ("sessione-prova", "run-7", "/progetti/alfa")
        and nuova.get("valida_dal"),
        "la nuova non porta la provenienza: " + str(nuova),
    )
    esigi("sessione" not in vecchia, "una memoria non toccata dal turno e' stata annotata: " + str(vecchia))
    testo = store.get(user_id=UTENTE).get_memories_text()
    esigi("conversazione sessione-prova]" in testo, "la conversazione d'origine non arriva nel prompt: " + testo)
    esigi("run-7" not in testo and "/progetti/alfa" not in testo, "turno o cartella arrivano nel prompt: " + testo)

    esigi(ripristina(agente, prima), "il ripristino non riporta le memorie a prima")
    esigi(
        [m["content"] for m in store.get(user_id=UTENTE).memories] == ["Le migrazioni si scrivono a mano."],
        "dopo il ripristino resta la memoria del turno",
    )

    # Prima del turno c'erano solo superate: rifiutare il turno non le cancella.
    _estrai_con(store, tool_call("delete_memory", memory_id=store.get(user_id=UTENTE).memories[0]["id"]))
    solo_superate = istantanea(agente)
    _estrai_con(store, tool_call("add_memory", memory="Le migrazioni sono automatiche."))
    esigi(ripristina(agente, solo_superate), "il ripristino con sole superate fallisce")
    dati = store.get(user_id=UTENTE)
    esigi(dati is not None and len(dati.superate) == 1 and not dati.memories, "il ripristino perde le superate")
    esigi(ripristina(agente, Istantanea()) and store.get(user_id=UTENTE) is None, "il ripristino a vuoto lascia dati")
    return "annotata solo la memoria del turno, col prompt che ne cita la sessione; il ripristino tiene le superate"


# ---------------------------------------------------------------------------
# Intuizioni: il filtro del namespace e' dopo il limite, quello dell'owner no
# ---------------------------------------------------------------------------

QUERY = "domanda"
VETTORI = {
    "domanda": [0.0, 1.0, 0.0, 0.0],
    "personale": [0.0, 1.0, 0.0, 0.0],
    "progetto": [0.0, 0.9, 0.1, 0.0],
}
FUORI = "user/demo"
DENTRO = "user/demo/progetti/alfa"


class EmbedderAFisso(Embedder):
    """Vettori fissi: il documento personale e' identico alla query, il progetto vicino.

    LanceDB vettorizza la query, quindi serve un embedder, ma nessun modello.
    """

    def __init__(self) -> None:
        super().__init__(dimensions=4)

    def get_embedding(self, testo: str) -> list[float]:
        return VETTORI[testo.split()[0]]

    async def async_get_embedding(self, testo: str) -> list[float]:
        return self.get_embedding(testo)


def _documenti(quanti: int, genere: str, namespace: str) -> list[Document]:
    return [
        Document(
            name=genere + "-" + str(indice),
            content=genere + " " + str(indice),
            meta_data={"namespace": namespace},
            embedding=VETTORI[genere],
        )
        for indice in range(quanti)
    ]


def _apri(nome: str) -> LanceDb:
    db = LanceDb(
        uri=str(RADICE_PROVA / nome),
        table_name="intuizioni",
        embedder=EmbedderAFisso(),
        search_type=SearchType.vector,
    )
    db.create()
    return db


def filtro_intuizioni() -> str:
    """Con due ambiti il filtro del namespace puo' restituire zero, e il pool spiega perche'."""
    # Trenta documenti fuori ambito piu' vicini alla domanda di cinque in
    # ambito: il gruppo iniziale e' tutto dell'altro ambito.
    db = _apri("namespace")
    db.insert(content_hash="fuori", documents=_documenti(30, "personale", FUORI))
    db.insert(content_hash="dentro", documents=_documenti(5, "progetto", DENTRO))

    for limite in (5, 30):
        esiti = db.search(query=QUERY, limit=limite, filters={"namespace": DENTRO})
        esigi(
            len(esiti) == 0,
            "il filtro del namespace non e' piu' applicato dopo il limite: con limit="
            + str(limite)
            + " sono tornati "
            + str(len(esiti))
            + " risultati in ambito. `docs/project-scopes.md` §3.4 va aggiornato, e la composizione a mano del "
            "blocco delle intuizioni potrebbe non servire piu'.",
        )

    # Il progetto riappare solo quando il limite supera i documenti fuori
    # ambito: non e' un filtro che perde qualcosa, e' un pool che finisce.
    esiti = db.search(query=QUERY, limit=35, filters={"namespace": DENTRO})
    esigi(
        len(esiti) == 5,
        "con un limite che copre tutta la tabella i cinque documenti in ambito dovrebbero tornare tutti: "
        + str(len(esiti)),
    )

    # L'ambito unico di oggi: il filtro non toglie niente, ed e' la ragione
    # per cui il difetto non si e' mai visto.
    esiti = db.search(query=QUERY, limit=5, filters={"namespace": FUORI})
    esigi(len(esiti) == 5, "con un namespace solo il filtro non dovrebbe togliere niente: " + str(len(esiti)))

    # L'owner invece e' una clausola del motore, applicata prima del gruppo
    # iniziale: nella stessa tabella, cinque righe su cinque.
    mio = _apri("owner")
    mio.insert(content_hash="altro", documents=_documenti(30, "personale", FUORI), user_id="altro")
    mio.insert(content_hash="mio", documents=_documenti(5, "progetto", DENTRO), user_id="demo")
    esiti = mio.search(query=QUERY, limit=5, user_id="demo")
    esigi(
        len(esiti) == 5,
        "l'owner non e' piu' un prefiltro del motore: " + str(len(esiti)) + " righe su 5 con limit=5. Se il "
        "namespace diventasse un prefiltro come questo, la ricerca per ambito non perderebbe risultati.",
    )

    return "namespace post-limite (0 su 5 con limit=5 e 30), owner prefiltro (5 su 5)"


def main() -> int:
    # LanceDB stampa su stdout tabelle e documenti, in mezzo all'esito. La
    # soglia va sugli handler, non sul logger: Agno riporta il logger a INFO a
    # ogni run.
    configura_log_agno(False)
    falliti, _ = esegui(
        (
            ("provenienza memorie", provenienza_memorie),
            ("memorie superate", memorie_superate),
            ("provenienza del turno", provenienza_del_turno),
            ("filtro intuizioni", filtro_intuizioni),
        )
    )
    return chiudi(falliti, RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
