"""Prova del consolidamento delle memorie, con embedder e giudice finti.

Il piano si verifica su un archivio sintetico con doppioni, una memoria
superata, una catena e memorie distinte sullo stesso tema; il comando
ritira, verifica, rifiuta la conferma sbagliata e torna indietro dal backup.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from _comune import chiudi, esegui, esigi, prepara_ambiente

RADICE_PROVA = prepara_ambiente("consolidamento-test")

from agno.db.sqlite import SqliteDb  # noqa: E402
from agno.learn.config import UserMemoryConfig  # noqa: E402
from agno.learn.stores.user_memory import UserMemoryStore  # noqa: E402

from ares import config  # noqa: E402
from ares.agent.schemas import AresMemories, chiave_memoria  # noqa: E402
from ares.backup.snapshots import elenco_snapshot, ripristina_snapshot, verifica_snapshot  # noqa: E402
from ares.cli.app import esegui as esegui_ares  # noqa: E402
from ares.memories import maintenance  # noqa: E402
from ares.memories.consolida import candidate, leggi_relazione, pianifica  # noqa: E402

PERCORSI = config.leggi_percorsi()
UTENTE = "consolida"

# I vettori finti: memorie dello stesso tema puntano nella stessa direzione,
# con un piccolo scarto per distinguerle; temi diversi sono ortogonali.
TEMI = {"editor": (1.0, 0.0, 0.0), "risposte": (0.0, 1.0, 0.0), "gatto": (0.0, 0.0, 1.0)}

# Testo, tema, giorno. Le date fanno l'ordine: la piu' recente resta.
MEMORIE = (
    ("Usa Vim come editor.", "editor", "01"),
    ("Usa Vim come editor", "editor", "02"),
    ("Ora usa Helix come editor, ha lasciato Vim.", "editor", "03"),
    ("Preferisce risposte brevi.", "risposte", "04"),
    ("Vuole risposte corte, senza preamboli.", "risposte", "05"),
    ("Preferisce gli esempi in Python nelle risposte.", "risposte", "06"),
    ("Ha un gatto di nome Otto.", "gatto", "07"),
)

# Le relazioni che il giudice finto da', per coppia di testi (vecchia, recente).
GIUDIZI = {
    ("Usa Vim come editor", "Ora usa Helix come editor, ha lasciato Vim."): "superata",
    ("Preferisce risposte brevi.", "Vuole risposte corte, senza preamboli."): "doppione",
}


def voci() -> list[dict[str, Any]]:
    return [
        {"id": "m" + str(i), "content": testo, "updated_at": "2026-09-" + giorno + "T10:00:00Z", "tema": tema}
        for i, (testo, tema, giorno) in enumerate(MEMORIE)
    ]


def incorpora(testi: Any) -> list[list[float]]:
    per_testo = {testo: TEMI[tema] for testo, tema, _ in MEMORIE}
    return [[x + 0.01 * i for x in per_testo[testo]] for i, testo in enumerate(testi)]


class GiudiceFinto:
    def __init__(self) -> None:
        self.chiamate: list[tuple[str, str]] = []

    def __call__(self, vecchia: dict[str, Any], recente: dict[str, Any]) -> Any:
        coppia = (vecchia["content"], recente["content"])
        self.chiamate.append(coppia)
        return GIUDIZI.get(coppia, "distinte")


def chiavi() -> str:
    esigi(chiave_memoria("  Usa  Vim come EDITOR. ") == chiave_memoria("usa vim come editor"), "spazi e maiuscole")
    esigi(chiave_memoria("Usa C++") != chiave_memoria("Usa C#"), "C++ e C# coincidono")
    return "maiuscole, spazi e punto finale non contano; i simboli si'"


def piano_sintetico() -> str:
    giudice = GiudiceFinto()
    piano = pianifica(voci(), incorpora, giudice)
    sostituzioni = piano.sostituzioni
    # m0 e m1 sono identiche: ritirata m0 senza giudizio, a favore di m1, che
    # a sua volta e' superata da m2: la catena porta m0 su m2.
    esigi(sostituzioni == {"m0": "m2", "m1": "m2", "m3": "m4"}, "sostituzioni: " + repr(sostituzioni))
    relazioni = {r.ritirata["id"]: r.relazione for r in piano.ritiri}
    esigi(relazioni == {"m0": "doppione", "m1": "superata", "m3": "doppione"}, "relazioni: " + repr(relazioni))
    esigi(all(chiamata[0] != "Usa Vim come editor." for chiamata in giudice.chiamate), "un'identica e' stata giudicata")
    esigi(
        ("Vuole risposte corte, senza preamboli.", "Preferisce gli esempi in Python nelle risposte.")
        in giudice.chiamate,
        "le vicine distinte non sono state giudicate: " + repr(giudice.chiamate),
    )
    esigi(not any("gatto" in a + b for a, b in giudice.chiamate), "il gatto e' stato confrontato con altri temi")
    esigi(piano.conferma == "CONSOLIDA 3", "conferma: " + piano.conferma)
    return "3 ritirate su 7: identiche senza modello, catena risolta, temi diversi mai confrontati"


def candidate_ordinate() -> str:
    coppie = candidate(voci(), incorpora)
    esigi(coppie[0] == (0, 1, None), "le identiche non vengono prima: " + repr(coppie[:2]))
    esigi(all(coseno is None or coseno >= 0.5 for _, _, coseno in coppie), "sotto soglia: " + repr(coppie))
    senza_id = [{"content": "Usa Vim come editor."}, *voci()]
    esigi(pianifica(senza_id, incorpora, GiudiceFinto()).sostituzioni.keys() == {"m0", "m1", "m3"}, "voce senza id")
    return str(len(coppie)) + " coppie, identiche in testa; una voce senza id non si tocca"


def risposte_del_modello() -> str:
    esigi(leggi_relazione('{"relazione": "superata"}', vincolato=True) == "superata", "JSON vincolato")
    esigi(leggi_relazione("Doppione.", vincolato=False) == "doppione", "parola con punto")
    esigi(leggi_relazione("forse doppione", vincolato=False) == "distinte", "frase libera")
    esigi(leggi_relazione("non json", vincolato=True) == "distinte", "JSON rotto")
    esigi(leggi_relazione(None, vincolato=False) == "distinte", "risposta vuota")
    return "una risposta illeggibile vale distinte, che non tocca niente"


def ritira_sposta_fra_le_superate() -> str:
    memorie = AresMemories(user_id=UTENTE, memories=voci())
    memorie.ritira({"m0": "m2", "m3": "m4"})
    restano = [v["id"] for v in memorie.memories]
    esigi(restano == ["m1", "m2", "m4", "m5", "m6"], "valide: " + repr(restano))
    superate = {v["id"]: v.get("sostituita_da") for v in memorie.superate}
    esigi(superate == {"m0": "m2", "m3": "m4"}, "superate: " + repr(superate))
    esigi(all(v.get("invalidata_il") for v in memorie.superate), "manca invalidata_il")
    return "ritirate con sostituita_da, registrate una volta sola"


def _store() -> UserMemoryStore:
    return UserMemoryStore(config=UserMemoryConfig(db=SqliteDb(db_file=PERCORSI.db_file), schema=AresMemories))


def _comando(*argomenti: str, risposta: str = "") -> int:
    with (
        patch.object(maintenance, "incorpora_con", lambda impostazioni: incorpora),
        patch.object(maintenance, "giudice", lambda impostazioni: GiudiceFinto()),
        patch("builtins.input", lambda etichetta: risposta),
    ):
        return esegui_ares("memories", ["consolidate", "--user", UTENTE, *argomenti])


def comando_completo() -> str:
    config.prepara_archivio(PERCORSI)
    store = _store()
    store.save(UTENTE, AresMemories(user_id=UTENTE, memories=voci()))

    esigi(_comando() == 0, "l'anteprima non esce con 0")
    esigi(len(store.get(user_id=UTENTE).memories) == 7, "l'anteprima ha scritto")
    esigi(_comando("--apply", risposta="CONSOLIDA 2") == 2, "una conferma sbagliata non rifiuta")
    esigi(len(store.get(user_id=UTENTE).memories) == 7 and not elenco_snapshot(PERCORSI), "scritto senza conferma")

    esigi(_comando("--apply", risposta="CONSOLIDA 3") == 0, "il consolidamento non esce con 0")
    dopo = store.get(user_id=UTENTE)
    esigi([v["id"] for v in dopo.memories] == ["m2", "m4", "m5", "m6"], "valide: " + repr(dopo.memories))
    esigi({v["id"] for v in dopo.superate} == {"m0", "m1", "m3"}, "superate: " + repr(dopo.superate))
    snapshot = elenco_snapshot(PERCORSI)
    esigi(len(snapshot) == 1, "backup: " + repr(snapshot))
    manifest = verifica_snapshot(PERCORSI, snapshot[0], percorso_diretto=True)
    esigi(manifest.get("type") == "pre-consolidate", "tipo del backup: " + repr(manifest.get("type")))
    esigi(_comando() == 0 and len(store.get(user_id=UTENTE).memories) == 4, "la seconda passata cambia qualcosa")

    # Su Windows il ripristino non sostituisce un archivio ancora aperto.
    store.db.db_engine.dispose()
    ripristina_snapshot(PERCORSI, snapshot[0].name)
    ripristinato = _store()
    memorie = ripristinato.get(user_id=UTENTE)
    ripristinato.db.db_engine.dispose()
    esigi(len(memorie.memories) == 7 and not memorie.superate, "ripristino: " + repr(memorie))
    return "anteprima e conferma sbagliata non scrivono; 3 ritirate, backup verificato, ripristino a 7"


def main() -> int:
    falliti, _ = esegui(
        (
            ("chiavi", chiavi),
            ("candidate", candidate_ordinate),
            ("piano", piano_sintetico),
            ("risposte", risposte_del_modello),
            ("ritiro", ritira_sposta_fra_le_superate),
            ("comando", comando_completo),
        )
    )
    return chiudi(falliti, RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
