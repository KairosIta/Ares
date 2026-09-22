"""
Gli ambiti, come li tratta il framework
=======================================

Due premesse dello studio sugli ambiti (`docs/project-scopes.md`, §3.4): che una
chiave in piu' in una voce di memoria sopravviva alle riscritture di Agno senza
mai arrivare al modello, e che il filtro per namespace delle intuizioni sia
applicato sui metadati *dopo* il limite, mentre quello dell'owner e' un
prefiltro del motore. Sono le due cose da cui dipendono la provenienza delle
memorie e la composizione a mano del blocco delle intuizioni: se cambiano,
cambia la proposta, e il cambiamento deve farsi sentire qui invece che in
produzione.

Queste prove non provano la proposta. L'ambito di progetto, il registro e il
blocco composto non esistono ancora: provano le premesse, e rendono permanenti
le misure che nello studio erano due script usa-e-getta.

Offline per costruzione: la prima usa il modello a copione di `_doppi.py`, la
seconda un embedder a vettori fissi, e i documenti arrivano a LanceDB con il
vettore gia' dentro, quindi Ollama non viene mai acceso. Il nome del file
segue la cosa misurata, non il modulo che la contiene: quando l'ambito di
progetto esistera', le prove di comportamento stanno qui accanto.
"""

from __future__ import annotations

import json
from typing import Any

from _comune import chiudi, esegui, esigi, prepara_ambiente

# I percorsi vanno scelti prima di importare qualunque cosa di `ares`, che
# all'import fotografa l'ambiente. Questa prova non legge `config` - non le
# serve - ma la regola vale comunque: la radice usa-e-getta e' anche la
# cartella in cui LanceDB scrive le sue tabelle.
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
    """Il copione, piu' i messaggi della prima chiamata.

    `extract_and_save` lavora su una copia del modello (`deepcopy`), quindi
    quello che la prova vuole leggere finirebbe sull'oggetto copiato:
    `__deepcopy__` che restituisce se stesso lo lascia dove si puo' leggere.
    E' la stessa ragione di `ModelloContesto` in `agno_contract_test.py`.

    Solo la prima chiamata interessa: e' il prompt di estrazione. La seconda,
    quando c'e', riporta l'esito dello strumento, cioe' cio' che il modello ha
    appena scritto.
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

    Serve un embedder perche' LanceDB vettorizza la query, ma nessun modello:
    i documenti arrivano con il vettore gia' dentro e la query ha il suo qui.
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
    # LanceDB annuncia su stdout la tabella che crea e i documenti che trova,
    # e quelle righe finirebbero in mezzo all'esito della prova. La soglia
    # sugli handler, non sul logger: Agno riporta il livello del logger a INFO
    # all'inizio di ogni run.
    configura_log_agno(False)
    falliti, _ = esegui(
        (
            ("provenienza memorie", provenienza_memorie),
            ("filtro intuizioni", filtro_intuizioni),
        )
    )
    return chiudi(falliti, RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
