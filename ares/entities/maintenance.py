"""
Manutenzione delle entita' di Ares
==================================

Uso:
    ares entities audit
    ares entities audit --all
    ares entities audit --all-pairs
    ares entities merge --source project/doppione --into project/canonico
    ares entities merge --source project/doppione --into project/canonico --apply

`ares-entities` e' l'alias con gli stessi sottocomandi.

La CLI coordina audit, anteprima, lock, backup e applicazione. La logica pura
vive nei moduli `entity_audit` ed `entity_merge`; gli import pubblici storici
restano disponibili da questo modulo per compatibilita'.
"""

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Annotated, Any

from agno.db.sqlite import SqliteDb
from cyclopts import Parameter

from ares import config
from ares.backup.snapshots import ErroreBackup, crea_snapshot
from ares.cli.comando import nuova_app
from ares.cli.conferma import conferma_scritta
from ares.cli.ui import UI
from ares.entities.audit import (
    PAROLE_COMUNI,
    SOGLIA_CONTENUTO_SIMILE,
    SOGLIA_NOMI_SIMILI,
    analizza,
    carica_entita,
    normalizza_testo,
    trova_candidati,
)
from ares.entities.merge import applica_piano, identita_ricordo, pianifica_fusione, verifica_piano
from ares.entities.models import (
    AggiornamentoEntita,
    CandidatoDuplicato,
    EntitaArchivio,
    ErroreManutenzione,
    EsitoAudit,
    PianoFusione,
    StatisticheFusione,
)
from ares.state.lock import StatoOccupato, lock_stato
from ares.state.stores import namespace_entita

app = nuova_app("entities", "Manutenzione offline delle entita' di Ares")

__all__ = (
    "PAROLE_COMUNI",
    "SOGLIA_CONTENUTO_SIMILE",
    "SOGLIA_NOMI_SIMILI",
    "AggiornamentoEntita",
    "CandidatoDuplicato",
    "EntitaArchivio",
    "ErroreManutenzione",
    "EsitoAudit",
    "PianoFusione",
    "StatisticheFusione",
    "analizza",
    "app",
    "applica_piano",
    "carica_entita",
    "dati_esito",
    "main",
    "normalizza_testo",
    "pianifica_fusione",
    "stampa_esito",
    "stampa_piano",
    "trova_candidati",
    "verifica_piano",
)


def _stato(voce: EntitaArchivio) -> str:
    return " (archiviata)" if voce.archiviata else ""


def _alias(voce: EntitaArchivio) -> list[str]:
    return [str(a) for a in (getattr(voce.entita, "aliases", None) or [])]


def dati_esito(esito: EsitoAudit, namespace: str) -> dict[str, Any]:
    """L'audit come dati, per `--json`: le stesse cose che la tabella mostra."""
    return {
        "namespace": namespace,
        "entita": [
            {
                "riferimento": voce.riferimento,
                "nome": voce.nome,
                "archiviata": voce.archiviata,
                "alias": _alias(voce),
            }
            for voce in esito.entita
        ],
        "righe_ignorate": list(esito.righe_ignorate),
        "candidati": [
            {
                "livello": candidato.livello,
                "prima": candidato.prima.riferimento,
                "seconda": candidato.seconda.riferimento,
                "motivi": list(candidato.motivi),
            }
            for candidato in esito.candidati
        ],
    }


def stampa_esito(esito: EsitoAudit, namespace: str, mostra_tutte: bool = False) -> None:
    attive = sum(not voce.archiviata for voce in esito.entita)
    archiviate = len(esito.entita) - attive
    UI.pair("Namespace", namespace)
    conteggio = str(len(esito.entita)) + " - attive: " + str(attive) + " archiviate: " + str(archiviate)
    UI.pair("Entita' analizzate", conteggio)

    if esito.righe_ignorate:
        UI.pair("Righe malformate ignorate", len(esito.righe_ignorate), style="ares.warning")
        for learning_id in esito.righe_ignorate:
            UI.line("  - " + learning_id, style="ares.muted")

    if mostra_tutte and esito.entita:
        UI.heading("Inventario")
        UI.table(
            ("riferimento", "nome", ("stato", "ares.muted"), ("alias", "ares.muted")),
            (
                (voce.riferimento, voce.nome, "archiviata" if voce.archiviata else "attiva", ", ".join(_alias(voce)))
                for voce in esito.entita
            ),
        )

    UI.blank()
    if not esito.candidati:
        UI.line("Nessun candidato duplicato trovato con i criteri correnti.", style="ares.success")
        return

    UI.heading("Candidati duplicati: " + str(len(esito.candidati)))
    UI.table(
        (("livello", "ares.warning"), "A", "B", ("motivi", "ares.muted")),
        (
            (
                candidato.livello,
                candidato.prima.riferimento + " - " + candidato.prima.nome + _stato(candidato.prima),
                candidato.seconda.riferimento + " - " + candidato.seconda.nome + _stato(candidato.seconda),
                "; ".join(candidato.motivi),
            )
            for candidato in esito.candidati
        ),
    )
    UI.blank()
    UI.line("L'audit non modifica nulla e non sceglie quale entita' conservare.", style="ares.muted")


def stampa_piano(piano: PianoFusione) -> None:
    statistiche = piano.statistiche
    aggiornamento_canonico = next(
        aggiornamento
        for aggiornamento in piano.aggiornamenti
        if aggiornamento.learning_id == piano.canonica.learning_id
    )
    UI.heading("Fusione proposta")
    UI.pair("  sorgente", piano.sorgente.riferimento + " - " + piano.sorgente.nome + _stato(piano.sorgente))
    UI.pair("  canonica", piano.canonica.riferimento + " - " + piano.canonica.nome)
    UI.blank()
    UI.line("Trasferimenti:", style="ares.title")
    UI.pair("  alias aggiunti", statistiche.alias_aggiunti)
    UI.pair("  fatti aggiunti/unificati", str(statistiche.fatti_aggiunti) + " / " + str(statistiche.fatti_unificati))
    UI.pair("  eventi aggiunti/unificati", str(statistiche.eventi_aggiunti) + " / " + str(statistiche.eventi_unificati))
    UI.pair("  proprieta' aggiunte", statistiche.proprieta_aggiunte)
    UI.pair(
        "  relazioni riscritte/unificate",
        str(statistiche.relazioni_riscritte) + " / " + str(statistiche.relazioni_unificate),
    )
    UI.pair("  auto-relazioni rimosse", statistiche.auto_relazioni_rimosse)
    UI.pair("  reciproche ricostruite", statistiche.reciproche_aggiunte)
    UI.pair("  righe che cambieranno", statistiche.righe_modificate)
    UI.blank()
    UI.line("Alias finali del canonico:", style="ares.title")
    alias_finali = aggiornamento_canonico.dopo.get("aliases") or []
    UI.line("  " + (", ".join(str(alias) for alias in alias_finali) if alias_finali else "(nessuno)"))

    fatti_canonici = {identita_ricordo(fatto, "fatto") for fatto in (piano.canonica.entita.facts or [])}
    if piano.sorgente.entita.facts:
        UI.blank()
        UI.line("Fatti della sorgente:", style="ares.title")
        for fatto in piano.sorgente.entita.facts:
            azione = "unifica" if identita_ricordo(fatto, "fatto") in fatti_canonici else "aggiunge"
            UI.line("  - " + azione + ": " + str(fatto.get("content")))

    eventi_canonici = {identita_ricordo(evento, "evento") for evento in (piano.canonica.entita.events or [])}
    if piano.sorgente.entita.events:
        UI.blank()
        UI.line("Eventi della sorgente:", style="ares.title")
        for evento in piano.sorgente.entita.events:
            azione = "unifica" if identita_ricordo(evento, "evento") in eventi_canonici else "aggiunge"
            data = " [" + str(evento.get("date")) + "]" if evento.get("date") else ""
            UI.line("  - " + azione + ": " + str(evento.get("content")) + data)

    UI.blank()
    UI.line("Righe aggiornate:", style="ares.title")
    for aggiornamento in piano.aggiornamenti:
        UI.line("  - " + aggiornamento.riferimento)
    if statistiche.conflitti:
        UI.blank()
        UI.line("Conflitti risolti conservando il valore canonico:", style="ares.warning")
        for conflitto in statistiche.conflitti:
            UI.line("  - " + conflitto)
    UI.blank()
    UI.line("La sorgente sara' eliminata dopo un backup verificato; il canonico restera' attivo.", style="ares.muted")


def _esegui_audit(user_id: str, mostra_tutte: bool, tutte_le_coppie: bool, come_json: bool = False) -> int:
    percorso = Path(config.DB_FILE)
    namespace = namespace_entita(user_id)
    if not percorso.is_file():
        if come_json:
            UI.json({"namespace": namespace, "entita": [], "righe_ignorate": [], "candidati": []})
            return 0
        UI.line("Nessun archivio di Ares trovato in " + str(percorso), style="ares.muted")
        return 0

    # Dopo il controllo e non prima: se l'archivio non c'e' questo comando lo
    # dice e basta, non lo crea. Se c'e', la directory esiste gia' e la
    # chiamata serve a correggerne i permessi su un clone piu' vecchio.
    config.prepara_archivio()
    db = SqliteDb(db_file=str(percorso))
    esito = analizza(db=db, namespace=namespace, includi_tutte_le_coppie=tutte_le_coppie)
    if come_json:
        UI.json(dati_esito(esito, namespace))
        return 0
    stampa_esito(esito, namespace=namespace, mostra_tutte=mostra_tutte)
    return 0


def _esegui_merge(user_id: str, source: str, canonical: str, applica: bool) -> int:
    percorso = Path(config.DB_FILE)
    if not percorso.is_file():
        raise ErroreManutenzione("nessun archivio di Ares trovato in " + str(percorso))

    config.prepara_archivio()
    namespace = namespace_entita(user_id)
    db = SqliteDb(db_file=str(percorso))
    entita, ignorate = carica_entita(db=db, namespace=namespace)
    if ignorate:
        raise ErroreManutenzione(
            "la scansione contiene righe malformate (" + ", ".join(ignorate) + "); correggile prima di fondere"
        )
    piano = pianifica_fusione(
        entita=entita,
        riferimento_sorgente=source,
        riferimento_canonico=canonical,
    )
    stampa_piano(piano)
    if not applica:
        UI.blank()
        UI.line("Anteprima soltanto: nessun dato e' stato modificato.", style="ares.warning")
        UI.line("Per applicarla, ripeti lo stesso comando aggiungendo --apply.", style="ares.muted")
        return 0

    if not conferma_scritta(piano.conferma):
        UI.line("Conferma non corrispondente: fusione annullata.", style="ares.warning")
        return 1

    snapshot = crea_snapshot(tipo="pre-merge", acquisisci_lock=False)
    UI.pair("Backup verificato", snapshot.name)
    try:
        applica_piano(db=db, piano=piano)
        verifica_piano(db=db, namespace=namespace, piano=piano)
    except Exception:
        UI.err("La fusione o la verifica finale non e' stata completata. Backup di sicurezza: " + snapshot.name)
        raise
    UI.line(
        "Fusione completata e verificata: " + piano.sorgente.riferimento + " -> " + piano.canonica.riferimento,
        style="ares.success",
    )
    UI.line("Per tornare indietro: " + config.comando_ares("backup", "restore", snapshot.name), style="ares.muted")
    return 0


@app.command
def audit(
    *,
    user: str = config.DEFAULT_USER_ID,
    mostra_tutte: Annotated[bool, Parameter(name="--all")] = False,
    tutte_le_coppie: Annotated[bool, Parameter(name="--all-pairs")] = False,
    come_json: Annotated[bool, Parameter(name="--json")] = False,
) -> int:
    """Trova possibili duplicati senza modificare lo stato.

    Args:
        user: utente di cui analizzare le entita'.
        mostra_tutte: mostra anche l'inventario completo.
        tutte_le_coppie: mostra anche ogni coppia dello stesso tipo priva di indizi automatici.
        come_json: stampa inventario e candidati come JSON, per gli script.
    """
    return _esegui(lambda: _esegui_audit(user, mostra_tutte, tutte_le_coppie, come_json), esclusivo=False)


@app.command
def merge(
    *,
    source: str,
    canonical: Annotated[str, Parameter(name=["--into", "--canonical"])],
    user: str = config.DEFAULT_USER_ID,
    apply: bool = False,
) -> int:
    """Prepara o applica una fusione esplicita fra due entita'.

    Args:
        source: entita' da assorbire, nel formato tipo/id.
        canonical: entita' canonica da conservare, nel formato tipo/id.
        user: utente proprietario delle entita'.
        apply: dopo l'anteprima chiede conferma, crea un backup e applica la fusione.
    """
    return _esegui(lambda: _esegui_merge(user, source, canonical, apply), esclusivo=apply)


def _esegui(azione: Callable[[], int], *, esclusivo: bool) -> int:
    try:
        with lock_stato(esclusivo=esclusivo):
            return azione()
    except StatoOccupato as errore:
        UI.err("Impossibile usare lo stato di Ares: " + str(errore))
        UI.err("Attendi che backup, restore o manutenzione terminino e riprova.", style="ares.muted")
        return 2
    except (ErroreManutenzione, ErroreBackup) as errore:
        UI.err("Manutenzione rifiutata: " + str(errore))
        return 2


def main(argv: Iterable[str] | None = None) -> int:
    from ares.cli.app import esegui

    return esegui("entities", list(argv) if argv is not None else None)


if __name__ == "__main__":
    raise SystemExit(main())
