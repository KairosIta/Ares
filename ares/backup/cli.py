"""Interfaccia a riga di comando per gli snapshot di Ares.

E' il sotto-comando `ares backup`; `ares-backup` resta come alias. Le
operazioni vere arrivano da `snapshots.py`, che le inietta all'import per
evitare il ciclo fra i due moduli.

L'output passa da `UI`: tabelle e colori sul terminale, testo piatto in una
pipe, errori su stderr. `list` e `verify` hanno `--json` per gli script.
"""

import functools
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

from cyclopts import Parameter

from ares import config
from ares.backup import integrity
from ares.cli.comando import nuova_app
from ares.cli.conferma import conferma_scritta
from ares.cli.ui import UI, byte_leggibili
from ares.state.lock import StatoOccupato, lock_stato


@dataclass(frozen=True)
class OperazioniBackup:
    """Operazioni della façade usate dalla CLI, iniettate per evitare cicli."""

    avviso_residui: Callable[[], list[str]]
    crea_snapshot: Callable[[], Path]
    elenco_snapshot: Callable[[], list[Path]]
    pota_snapshot: Callable[[int, bool], list[Path]]
    ripristina_snapshot: Callable[[str, bool], Path | None]
    risolvi_snapshot: Callable[[str], Path]
    verifica_snapshot: Callable[[Any, bool], dict[str, Any]]


app = nuova_app("backup", "Snapshot locali dello stato di Ares")

# `--json` e non `--json_`: il nome della funzione non puo' essere `json`
# perche' il modulo lo importa, e Cyclopts prenderebbe `--come-json`.
ComeJson = Annotated[bool, Parameter(name="--json")]

_operazioni: OperazioniBackup | None = None


def imposta_operazioni(operazioni: OperazioniBackup) -> None:
    global _operazioni
    _operazioni = operazioni


def _op() -> OperazioniBackup:
    if _operazioni is None:
        raise RuntimeError("operazioni di backup non impostate: importa ares.backup.snapshots")
    return _operazioni


def _protetto(funzione):
    """Un guasto previsto diventa una riga su stderr e il codice 1, non un traceback."""

    @functools.wraps(funzione)
    def involucro(*argomenti, **opzioni):
        try:
            return funzione(*argomenti, **opzioni)
        except (integrity.ErroreBackup, StatoOccupato, OSError) as errore:
            UI.err("ERRORE: " + str(errore))
            return 1

    return involucro


def _dimensione(percorso: Path) -> int:
    return sum(voce.stat().st_size for voce in percorso.rglob("*") if voce.is_file())


def _descrivi(percorso: Path) -> dict[str, Any]:
    """Cio' che di uno snapshot si dice in elenco, letto dal manifest."""
    try:
        manifest = json.loads((percorso / integrity.MANIFEST).read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest non oggetto")
        tipo = str(manifest.get("type", "?"))
        creato = str(manifest.get("created_at", "?"))
        valido = True
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        tipo, creato, valido = "CORROTTO", "?", False
    return {
        "snapshot": percorso.name,
        "type": tipo,
        "created_at": creato,
        "bytes": _dimensione(percorso),
        "path": str(percorso),
        "manifest_ok": valido,
    }


@app.command(name="create")
@_protetto
def crea() -> int:
    """Crea e verifica uno snapshot."""
    creato = _op().crea_snapshot()
    UI.line("Snapshot creato e verificato: " + str(creato), style="ares.success")
    return 0


@app.command(name="list")
@_protetto
def elenca(*, come_json: ComeJson = False) -> int:
    """Elenca gli snapshot, dal piu' recente.

    Args:
        come_json: stampa l'elenco come JSON, per gli script.
    """
    operazioni = _op()
    residui = operazioni.avviso_residui()
    voci = [_descrivi(percorso) for percorso in reversed(operazioni.elenco_snapshot())]
    if come_json:
        UI.json({"backup_dir": str(config.BACKUP_DIR), "residui": residui, "snapshot": voci})
        return 0
    # Prima del catalogo: chi elenca gli snapshot sta decidendo se e da cosa
    # ripristinare, e un restore rimasto a meta' e' la prima cosa da sapere.
    for indice, riga in enumerate(residui):
        UI.line(riga, style="ares.error" if indice == 0 else "ares.muted")
    if residui:
        UI.blank()
    if not voci:
        UI.line("Nessuno snapshot in " + str(config.BACKUP_DIR), style="ares.muted")
        return 0
    UI.table(
        ("snapshot", "tipo", "creato", ("dimensione", "ares.text", "right")),
        ((v["snapshot"], v["type"], v["created_at"], byte_leggibili(v["bytes"])) for v in voci),
    )
    return 0


@app.command(name="verify")
@_protetto
def verifica(snapshot: str = "latest", *, come_json: ComeJson = False) -> int:
    """Verifica checksum e database di uno snapshot.

    Args:
        snapshot: nome dello snapshot, o `latest` per l'ultimo.
        come_json: stampa il manifest verificato come JSON.
    """
    manifest = _op().verifica_snapshot(snapshot, False)
    if come_json:
        UI.json(manifest)
        return 0
    UI.line("Snapshot valido: " + str(manifest["snapshot_id"]), style="ares.success")
    return 0


@app.command(name="restore")
@_protetto
def ripristina(snapshot: str, *, yes: bool = False, skip_safety: bool = False) -> int:
    """Ripristina uno snapshot, dopo averlo verificato.

    Args:
        snapshot: nome dello snapshot da ripristinare.
        yes: non chiedere conferma.
        skip_safety: ripristina senza snapshot pre-restore (solo se lo stato corrente e' irrecuperabile).
    """
    operazioni = _op()
    percorso = operazioni.risolvi_snapshot(snapshot)
    operazioni.verifica_snapshot(percorso, True)
    if not yes and not conferma_scritta(percorso.name, cosa="Lo stato attuale verra' sostituito da questo snapshot."):
        UI.line("Restore annullato.", style="ares.warning")
        return 2
    sicurezza = operazioni.ripristina_snapshot(percorso.name, not skip_safety)
    UI.line("Restore completato: " + percorso.name, style="ares.success")
    if sicurezza is not None:
        UI.pair("Stato precedente salvato in", sicurezza)
    return 0


@app.command(name="prune")
@_protetto
def pota(*, keep: int = config.BACKUP_KEEP, yes: bool = False) -> int:
    """Elimina gli snapshot piu' vecchi, conservandone un numero fisso.

    Args:
        keep: quanti snapshot recenti conservare.
        yes: non chiedere conferma.
    """
    operazioni = _op()
    if keep < 1:
        raise integrity.ErroreBackup("--keep deve essere almeno 1")
    disponibili = operazioni.elenco_snapshot()
    candidati = disponibili[:-keep] if len(disponibili) > keep else []
    if not candidati:
        UI.line("Niente da eliminare; snapshot: " + str(len(disponibili)) + ", keep: " + str(keep), style="ares.muted")
        return 0
    UI.line("Snapshot da eliminare:", style="ares.warning")
    for percorso in candidati:
        UI.line("- " + percorso.name)
    if not yes and not conferma_scritta("ELIMINA"):
        UI.line("Prune annullato.", style="ares.warning")
        return 2
    # Fra anteprima e conferma potrebbe essere nato uno snapshot. Non eliminare
    # mai qualcosa che l'utente non ha appena visto.
    nomi_visti = [percorso.name for percorso in candidati]
    with lock_stato(esclusivo=True):
        attuali = operazioni.elenco_snapshot()
        candidati_attuali = attuali[:-keep] if len(attuali) > keep else []
        if [percorso.name for percorso in candidati_attuali] != nomi_visti:
            raise integrity.ErroreBackup("l'elenco degli snapshot e' cambiato; ripeti prune")
        eliminati = operazioni.pota_snapshot(keep, False)
    UI.line("Eliminati " + str(len(eliminati)) + " snapshot; conservati " + str(keep), style="ares.success")
    return 0


def main(operazioni: OperazioniBackup, argomenti: Sequence[str] | None = None) -> int:
    """L'alias `ares-backup`: passa da `ares backup`, cosi' l'aiuto dice la forma nuova."""
    imposta_operazioni(operazioni)
    from ares.cli.app import esegui

    return esegui("backup", argomenti)
