"""
Manutenzione delle entita' di Ares
==================================

Uso:
    .venv/bin/python -m ares.entities audit
    .venv/bin/python -m ares.entities audit --all
    .venv/bin/python -m ares.entities audit --all-pairs
    .venv/bin/python -m ares.entities merge --source project/doppione --into project/canonico
    .venv/bin/python -m ares.entities merge --source project/doppione --into project/canonico --apply

La CLI coordina audit, anteprima, lock, backup e applicazione. La logica pura
vive nei moduli `entity_audit` ed `entity_merge`; gli import pubblici storici
restano disponibili da questo modulo per compatibilita'.
"""

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

from agno.db.sqlite import SqliteDb

from ares import config
from ares.backup.snapshots import ErroreBackup, crea_snapshot
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
    "applica_piano",
    "carica_entita",
    "costruisci_parser",
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


def stampa_esito(esito: EsitoAudit, namespace: str, mostra_tutte: bool = False) -> None:
    attive = sum(not voce.archiviata for voce in esito.entita)
    archiviate = len(esito.entita) - attive
    print("Namespace:", namespace)
    print("Entita' analizzate:", len(esito.entita), "- attive:", attive, "archiviate:", archiviate)

    if esito.righe_ignorate:
        print("Righe malformate ignorate:", len(esito.righe_ignorate))
        for learning_id in esito.righe_ignorate:
            print("  -", learning_id)

    if mostra_tutte and esito.entita:
        print()
        print("Inventario:")
        for voce in esito.entita:
            alias = list(getattr(voce.entita, "aliases", None) or [])
            suffisso = " - alias: " + ", ".join(str(a) for a in alias) if alias else ""
            print("  -", voce.riferimento, "-", voce.nome + _stato(voce) + suffisso)

    print()
    if not esito.candidati:
        print("Nessun candidato duplicato trovato con i criteri correnti.")
        return

    print("Candidati duplicati:", len(esito.candidati))
    for candidato in esito.candidati:
        print()
        print("[" + candidato.livello + "]")
        print("  A:", candidato.prima.riferimento, "-", candidato.prima.nome + _stato(candidato.prima))
        print("  B:", candidato.seconda.riferimento, "-", candidato.seconda.nome + _stato(candidato.seconda))
        for motivo in candidato.motivi:
            print("  -", motivo)

    print()
    print("L'audit non modifica nulla e non sceglie quale entita' conservare.")


def stampa_piano(piano: PianoFusione) -> None:
    statistiche = piano.statistiche
    aggiornamento_canonico = next(
        aggiornamento
        for aggiornamento in piano.aggiornamenti
        if aggiornamento.learning_id == piano.canonica.learning_id
    )
    print("Fusione proposta:")
    print("  sorgente:", piano.sorgente.riferimento, "-", piano.sorgente.nome + _stato(piano.sorgente))
    print("  canonica:", piano.canonica.riferimento, "-", piano.canonica.nome)
    print()
    print("Trasferimenti:")
    print("  alias aggiunti:", statistiche.alias_aggiunti)
    print(
        "  fatti aggiunti/unificati:",
        statistiche.fatti_aggiunti,
        "/",
        statistiche.fatti_unificati,
    )
    print(
        "  eventi aggiunti/unificati:",
        statistiche.eventi_aggiunti,
        "/",
        statistiche.eventi_unificati,
    )
    print("  proprieta' aggiunte:", statistiche.proprieta_aggiunte)
    print("  relazioni riscritte/unificate:", statistiche.relazioni_riscritte, "/", statistiche.relazioni_unificate)
    print("  auto-relazioni rimosse:", statistiche.auto_relazioni_rimosse)
    print("  reciproche ricostruite:", statistiche.reciproche_aggiunte)
    print("  righe che cambieranno:", statistiche.righe_modificate)
    print()
    print("Alias finali del canonico:")
    alias_finali = aggiornamento_canonico.dopo.get("aliases") or []
    print("  " + (", ".join(str(alias) for alias in alias_finali) if alias_finali else "(nessuno)"))

    fatti_canonici = {identita_ricordo(fatto, "fatto") for fatto in (piano.canonica.entita.facts or [])}
    if piano.sorgente.entita.facts:
        print()
        print("Fatti della sorgente:")
        for fatto in piano.sorgente.entita.facts:
            azione = "unifica" if identita_ricordo(fatto, "fatto") in fatti_canonici else "aggiunge"
            print("  -", azione + ":", fatto.get("content"))

    eventi_canonici = {identita_ricordo(evento, "evento") for evento in (piano.canonica.entita.events or [])}
    if piano.sorgente.entita.events:
        print()
        print("Eventi della sorgente:")
        for evento in piano.sorgente.entita.events:
            azione = "unifica" if identita_ricordo(evento, "evento") in eventi_canonici else "aggiunge"
            data = " [" + str(evento.get("date")) + "]" if evento.get("date") else ""
            print("  -", azione + ":", str(evento.get("content")) + data)

    print()
    print("Righe aggiornate:")
    for aggiornamento in piano.aggiornamenti:
        print("  -", aggiornamento.riferimento)
    if statistiche.conflitti:
        print()
        print("Conflitti risolti conservando il valore canonico:")
        for conflitto in statistiche.conflitti:
            print("  -", conflitto)
    print()
    print("La sorgente sara' eliminata dopo un backup verificato; il canonico restera' attivo.")


def costruisci_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ares.entities", description="Manutenzione offline delle entita' di Ares"
    )
    sottocomandi = parser.add_subparsers(dest="comando", required=True)
    audit = sottocomandi.add_parser("audit", help="trova possibili duplicati senza modificare lo stato")
    audit.add_argument("--user", default=config.DEFAULT_USER_ID, help="utente di cui analizzare le entita'")
    audit.add_argument("--all", action="store_true", help="mostra anche l'inventario completo")
    audit.add_argument(
        "--all-pairs",
        action="store_true",
        help="mostra anche ogni coppia dello stesso tipo priva di indizi automatici",
    )
    merge = sottocomandi.add_parser("merge", help="prepara o applica una fusione esplicita")
    merge.add_argument("--user", default=config.DEFAULT_USER_ID, help="utente proprietario delle entita'")
    merge.add_argument("--source", required=True, help="entita' da assorbire, nel formato tipo/id")
    merge.add_argument(
        "--into",
        "--canonical",
        dest="canonical",
        required=True,
        help="entita' canonica da conservare, nel formato tipo/id",
    )
    merge.add_argument(
        "--apply",
        action="store_true",
        help="dopo l'anteprima chiede conferma, crea un backup e applica la fusione",
    )
    return parser


def _esegui_audit(user_id: str, mostra_tutte: bool, tutte_le_coppie: bool) -> int:
    percorso = Path(config.DB_FILE)
    if not percorso.is_file():
        print("Nessun archivio di Ares trovato in", percorso)
        return 0

    # Dopo il controllo e non prima: se l'archivio non c'e' questo comando lo
    # dice e basta, non lo crea. Se c'e', la directory esiste gia' e la
    # chiamata serve a correggerne i permessi su un clone piu' vecchio.
    config.prepara_archivio()
    namespace = namespace_entita(user_id)
    db = SqliteDb(db_file=str(percorso))
    esito = analizza(db=db, namespace=namespace, includi_tutte_le_coppie=tutte_le_coppie)
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
        print()
        print("Anteprima soltanto: nessun dato e' stato modificato.")
        print("Per applicarla, ripeti lo stesso comando aggiungendo --apply.")
        return 0

    print()
    print("Per confermare scrivi esattamente:")
    print(piano.conferma)
    try:
        conferma = input("> ").strip()
    except EOFError:
        conferma = ""
    if conferma != piano.conferma:
        print("Conferma non corrispondente: fusione annullata.")
        return 1

    snapshot = crea_snapshot(tipo="pre-merge", acquisisci_lock=False)
    print("Backup verificato:", snapshot.name)
    try:
        applica_piano(db=db, piano=piano)
        verifica_piano(db=db, namespace=namespace, piano=piano)
    except Exception:
        print(
            "La fusione o la verifica finale non e' stata completata. Backup di sicurezza:",
            snapshot.name,
            file=sys.stderr,
        )
        raise
    print("Fusione completata e verificata:", piano.sorgente.riferimento, "->", piano.canonica.riferimento)
    python_venv = r".venv\Scripts\python.exe" if sys.platform == "win32" else ".venv/bin/python"
    print("Per tornare indietro:", python_venv, "-m ares.backup restore", snapshot.name)
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    args = costruisci_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.comando == "audit":
            with lock_stato(esclusivo=False):
                return _esegui_audit(
                    user_id=args.user,
                    mostra_tutte=args.all,
                    tutte_le_coppie=args.all_pairs,
                )
        if args.comando == "merge":
            with lock_stato(esclusivo=args.apply):
                return _esegui_merge(
                    user_id=args.user,
                    source=args.source,
                    canonical=args.canonical,
                    applica=args.apply,
                )
    except StatoOccupato as errore:
        print("Impossibile usare lo stato di Ares:", errore, file=sys.stderr)
        print("Attendi che backup, restore o manutenzione terminino e riprova.", file=sys.stderr)
        return 2
    except (ErroreManutenzione, ErroreBackup) as errore:
        print("Manutenzione rifiutata:", errore, file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
