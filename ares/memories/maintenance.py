"""Consolidamento delle memorie di Ares
=====================================

Uso:
    ares memories consolidate
    ares memories consolidate --apply

Senza `--apply` mostra soltanto il piano: quali memorie sono doppioni o
superate, e quale resta al loro posto. Con `--apply` chiede di riscrivere la
conferma, crea un backup verificato, ritira le memorie e rilegge l'archivio
per verificare. Le ritirate passano fra le superate, con il rimando a quella
che resta: `/memorie superate` le mostra.

Il piano chiede all'embedder locale le coppie vicine e al modello di
apprendimento un giudizio per coppia; la logica e' in `consolida.py`.
"""

from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from ares import config
from ares.agent.schemas import AresMemories, note_memoria
from ares.backup.snapshots import ErroreBackup, crea_snapshot
from ares.cli.comando import ESITO_RIFIUTO, esegui_protetto, nuova_app
from ares.cli.conferma import conferma_scritta
from ares.cli.ui import UI
from ares.config import Impostazioni, Percorsi
from ares.entities.models import ErroreManutenzione
from ares.memories.consolida import (
    SCHEMA_GIUDIZIO,
    Giudica,
    PianoConsolida,
    Relazione,
    leggi_relazione,
    messaggi_giudizio,
    pianifica,
)
from ares.state.identita import Utente, UtenteNonValido

app = nuova_app("memories", "Consolidamento delle memorie doppie o superate")


def _store(percorsi: Percorsi) -> Any:
    from agno.db.sqlite import SqliteDb
    from agno.learn.config import UserMemoryConfig
    from agno.learn.stores.user_memory import UserMemoryStore

    return UserMemoryStore(config=UserMemoryConfig(db=SqliteDb(db_file=percorsi.db_file), schema=AresMemories))


def incorpora_con(impostazioni: Impostazioni) -> Any:
    """I vettori delle memorie dall'embedder locale; un vettore vuoto e' un errore."""
    from ares.agent.runtime import build_embedder

    embedder = build_embedder(impostazioni)

    def incorpora(testi: Sequence[str]) -> list[list[float]]:
        vettori = [embedder.get_embedding(testo) for testo in testi]
        if any(not vettore for vettore in vettori):
            raise ErroreManutenzione("l'embedder " + impostazioni.embedder + " non ha risposto: Ollama e' avviato?")
        return vettori

    return incorpora


def giudice(impostazioni: Impostazioni) -> Giudica:
    """Il giudizio di una coppia con il modello di apprendimento, a temperatura 0.

    In locale la risposta e' vincolata dallo schema; il cloud di Ollama non
    applica lo schema, e risponde con una parola.
    """
    from agno.models.message import Message

    from ares.agent.runtime import build_learning_model

    modello = deepcopy(build_learning_model(impostazioni))
    vincolato = not config.e_modello_cloud(impostazioni.apprendimento)
    if vincolato:
        modello.format = SCHEMA_GIUDIZIO
    modello.options = {**(modello.options or {}), "temperature": 0}

    def giudica(vecchia: dict[str, Any], recente: dict[str, Any]) -> Relazione:
        richiesta = messaggi_giudizio(vecchia, recente, vincolato=vincolato)
        messaggi = [Message(role=m["role"], content=m["content"]) for m in richiesta]
        return leggi_relazione(modello.response(messages=messaggi).content, vincolato=vincolato)

    return giudica


def _riga(voce: dict[str, Any]) -> str:
    note = note_memoria(voce, ignota="data ignota")
    return " ".join(str(voce.get("content") or "").split()) + "  [" + ", ".join(note) + "]"


def stampa_piano(piano: PianoConsolida) -> None:
    UI.heading("Consolidamento delle memorie")
    UI.pair("Memorie valide", str(piano.memorie))
    UI.pair("Coppie giudicate dal modello", str(piano.giudicate))
    UI.pair("Da ritirare", str(len(piano.ritiri)))
    for ritiro in piano.ritiri:
        UI.blank()
        UI.line(ritiro.relazione, style="ares.title")
        UI.line("  ritira  " + _riga(ritiro.ritirata), style="ares.muted")
        UI.line("  resta   " + _riga(ritiro.resta))
    if piano.ritiri:
        UI.blank()
        UI.line(
            "Le ritirate passano fra le superate, con il rimando a quella che resta; niente si cancella.",
            style="ares.muted",
        )


def verifica(store: Any, user_id: str, piano: PianoConsolida, valide_prima: int) -> None:
    """Rilegge l'archivio: ogni ritirata e' fra le superate, e le valide sono quante previsto."""
    contenitore = store.get(user_id=user_id)
    voci = [v for v in getattr(contenitore, "memories", None) or [] if isinstance(v, dict)]
    valide = {v.get("id") for v in voci}
    superate = {
        v.get("id"): v.get("sostituita_da")
        for v in getattr(contenitore, "superate", None) or []
        if isinstance(v, dict) and v.get("sostituita_da")
    }
    for ritirata, resta in piano.sostituzioni.items():
        if ritirata in valide or superate.get(ritirata) != resta or resta not in valide:
            raise ErroreManutenzione("la verifica non torna sulla memoria " + ritirata)
    if len(voci) != valide_prima - len(piano.ritiri):
        raise ErroreManutenzione("la verifica non torna sul numero delle memorie valide")


def _esegui_consolida(percorsi: Percorsi, impostazioni: Impostazioni, user: str, applica: bool) -> int:
    utente = Utente.da_grezzo(user)
    if not Path(percorsi.db_file).is_file():
        UI.line("Nessun archivio di Ares trovato in " + percorsi.db_file, style="ares.muted")
        return 0
    config.prepara_archivio(percorsi)
    store = _store(percorsi)
    try:
        return _consolida(store, percorsi, impostazioni, utente, applica)
    finally:
        # Chi chiama puo' poi ripristinare o spostare lo stato nello stesso
        # processo: su Windows una connessione aperta glielo impedirebbe.
        store.db.db_engine.dispose()


def _consolida(store: Any, percorsi: Percorsi, impostazioni: Impostazioni, utente: Utente, applica: bool) -> int:
    contenitore = store.get(user_id=utente.id)
    voci = [v for v in getattr(contenitore, "memories", None) or [] if isinstance(v, dict)]
    if len(voci) < 2:
        UI.line("Meno di due memorie: niente da consolidare.", style="ares.muted")
        return 0

    piano = pianifica(voci, incorpora_con(impostazioni), giudice(impostazioni))
    stampa_piano(piano)
    if not piano.ritiri:
        UI.blank()
        UI.line("Nessun doppione e nessuna memoria superata.", style="ares.success")
        return 0
    if not applica:
        UI.blank()
        UI.line("Anteprima soltanto: nessun dato e' stato modificato.", style="ares.warning")
        UI.line("Per applicarla, ripeti lo stesso comando aggiungendo --apply.", style="ares.muted")
        return 0

    if not conferma_scritta(piano.conferma):
        UI.line("Conferma non corrispondente: consolidamento annullato.", style="ares.warning")
        return ESITO_RIFIUTO

    snapshot = crea_snapshot(percorsi, tipo="pre-consolidate", acquisisci_lock=False)
    UI.pair("Backup verificato", snapshot.name)
    try:
        contenitore.ritira(piano.sostituzioni)
        store.save(utente.id, contenitore)
        verifica(store, utente.id, piano, valide_prima=len(voci))
    except Exception:
        UI.err("Il consolidamento o la verifica finale non e' stato completato. Backup di sicurezza: " + snapshot.name)
        raise
    UI.line("Consolidamento completato e verificato, memorie ritirate: " + str(len(piano.ritiri)), style="ares.success")
    UI.line("Per tornare indietro: " + config.comando_ares("backup", "restore", snapshot.name), style="ares.muted")
    return 0


@app.command
def consolidate(*, user: str = config.DEFAULT_USER_ID, apply: bool = False) -> int:
    """Trova le memorie doppie o superate e propone di ritirarle.

    L'embedder locale propone le coppie vicine, il modello di apprendimento
    giudica ciascuna: doppione, superata o distinte. Di una superata si
    ritira la piu' vecchia, di un doppione la meno completa.

    Args:
        user: utente di cui consolidare le memorie.
        apply: dopo l'anteprima chiede conferma, crea un backup e ritira le memorie.
    """
    percorsi = config.leggi_percorsi()
    impostazioni = config.leggi_impostazioni()
    return esegui_protetto(
        percorsi,
        lambda: _esegui_consolida(percorsi, impostazioni, user, apply),
        esclusivo=apply,
        rifiuti=(ErroreManutenzione, ErroreBackup, UtenteNonValido),
    )


def main(argv: Sequence[str] | None = None) -> int:
    from ares.cli.app import esegui

    return esegui("memories", list(argv) if argv is not None else None)


if __name__ == "__main__":
    raise SystemExit(main())
