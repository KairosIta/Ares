"""
Backup locale dello stato di Ares
=================================

Uso:
    .venv/bin/ares-backup create
    .venv/bin/ares-backup list
    .venv/bin/ares-backup verify latest
    .venv/bin/ares-backup restore <snapshot>
    .venv/bin/ares-backup prune --keep 20

Salva il cervello di Ares - i due SQLite e LanceDB - non il workspace. Ogni
snapshot e' una directory trasparente con manifest e checksum. Creazione e
restore richiedono il lock esclusivo: chiudere la chat prima di eseguirli.
"""

import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import ares
from ares import config
from ares.backup import cli, files, integrity, restore
from ares.state.lock import lock_stato
from ares.state.platform_files import rendi_privato

# Contratti storicamente importabili da backup.py. La façade li conserva
# mentre implementazione e formato vivono nel modulo dedicato.
CHECKSUM = integrity.CHECKSUM
DATABASE = integrity.DATABASE
FORMATO_BACKUP = integrity.FORMATO_BACKUP
MANIFEST = integrity.MANIFEST
ErroreBackup = integrity.ErroreBackup

_integrita_sqlite = integrity.verifica_sqlite
_scrivi_checksum = integrity.scrivi_checksum
_tabelle_lancedb = integrity.conta_tabelle_lancedb
_verifica_integrita_snapshot = integrity.verifica_snapshot

_privato = files.rendi_albero_privato
_rinomina_directory = files.rinomina_directory_nuova

# Primitive e helper storicamente usati anche dalle prove mirate.
_installa_restore_per_copia = restore._installa_restore_per_copia
_prepara_restore = restore._prepara_restore
_svuota_directory = restore._svuota_directory

# La cronologia della riga di comando vive in tmp/ insieme al resto, ma non e'
# stato appreso: e' cio' che l'utente ha digitato. Da qui le due regole
# asimmetriche piu' sotto - lo snapshot la copia, il restore non la riporta
# indietro - che sono la stessa cosa detta due volte: un backup protegge da
# una perdita, un restore fa tornare indietro Ares, non chi gli parla.
CRONOLOGIA = config.CRONOLOGIA_FILE.name


def _si_sovrappongono(primo: Path, secondo: Path) -> bool:
    primo, secondo = primo.resolve(), secondo.resolve()
    return primo == secondo or primo.is_relative_to(secondo) or secondo.is_relative_to(primo)


def _pubblica_snapshot(staging: Path, definitivo: Path) -> None:
    """Rende visibile lo snapshot con rename o con un commit marker.

    Il manifest e' il requisito usato da ``elenco_snapshot`` per riconoscere
    una directory come snapshot. Nel fallback viene pubblicato per ultimo,
    dopo dati e checksum, tramite una rinomina di file atomica.
    """
    try:
        _rinomina_directory(staging, definitivo)
        return
    except PermissionError:
        pass

    temporaneo_manifest = definitivo / ("." + MANIFEST + ".pending")

    def ignora_manifest_radice(directory: str, nomi: list[str]) -> list[str]:
        if Path(directory) == staging and MANIFEST in nomi:
            return [MANIFEST]
        return []

    try:
        shutil.copytree(staging, definitivo, ignore=ignora_manifest_radice)
        shutil.copy2(staging / MANIFEST, temporaneo_manifest)
        rendi_privato(temporaneo_manifest)
        _privato(definitivo)
        os.replace(temporaneo_manifest, definitivo / MANIFEST)
    except Exception:
        shutil.rmtree(definitivo, ignore_errors=True)
        raise
    else:
        shutil.rmtree(staging, ignore_errors=True)


def valida_percorsi() -> None:
    """Il backup non puo' contenere o essere contenuto da cio' che protegge."""
    backup = config.BACKUP_DIR.resolve()
    vietati = [("lo stato", config.TMP_DIR), ("il progetto", config.BASE_DIR)]
    if config.WORKSPACE:
        vietati.append(("il workspace", config.WORKSPACE_DIR))
    for nome, percorso in vietati:
        if _si_sovrappongono(backup, Path(percorso)):
            raise ErroreBackup("BACKUP_DIR si sovrappone con " + nome + " (" + str(Path(percorso).resolve()) + ")")


def _root_backup() -> Path:
    valida_percorsi()
    root = config.BACKUP_DIR.resolve()
    root.mkdir(parents=True, exist_ok=True)
    # Non ripercorrere tutti gli snapshot a ogni list/verify: ogni snapshot
    # viene gia' reso privato quando nasce.
    rendi_privato(root)
    return root


def _copia_sqlite(sorgente: Path, destinazione: Path) -> None:
    _integrita_sqlite(sorgente)
    origine = sqlite3.connect(str(sorgente))
    copia = sqlite3.connect(str(destinazione))
    try:
        journal_mode = str(origine.execute("pragma journal_mode").fetchone()[0]).casefold()
        origine.backup(copia)
        # L'API backup copia pagine e dati ma il database di destinazione
        # nasce in DELETE mode. Agno 3 usa WAL: perderlo nello snapshot
        # costringerebbe la prima lettura dopo un restore a riscrivere
        # l'header del database. La copia deve conservare anche questa
        # proprieta' persistente, non soltanto tabelle e righe.
        if journal_mode == "wal":
            copia.execute("pragma journal_mode=wal").fetchone()
    finally:
        copia.close()
        origine.close()
    rendi_privato(destinazione)
    _integrita_sqlite(destinazione)


def _stato_presente() -> bool:
    if any((config.TMP_DIR / nome).is_file() for nome in DATABASE):
        return True
    lance = Path(config.LANCEDB_URI)
    return lance.is_dir() and any(voce.is_file() for voce in lance.rglob("*"))


def _git() -> dict[str, Any]:
    dati: dict[str, Any] = {"commit": None, "dirty": None}
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=config.BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        stato = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=config.BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        dati = {"commit": commit.stdout.strip(), "dirty": bool(stato.stdout.strip())}
    except (OSError, subprocess.SubprocessError):
        pass
    return dati


def _versione_agno() -> str | None:
    try:
        import agno

        return getattr(agno, "__version__", None)
    except ImportError:
        return None


def _id_snapshot(tipo: str) -> str:
    if tipo not in {"manuale", "pre-merge", "pre-restore", "pre-session-prune"}:
        raise ErroreBackup("tipo di snapshot non valido: " + repr(tipo))
    base = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffisso = "" if tipo == "manuale" else "-" + tipo
    candidato = base + suffisso
    root = config.BACKUP_DIR.resolve()
    contatore = 1
    while (root / candidato).exists():
        candidato = base + suffisso + "-" + str(contatore).zfill(2)
        contatore += 1
    return candidato


def _crea_snapshot_senza_lock(tipo: str = "manuale") -> Path:
    if not _stato_presente():
        raise ErroreBackup("nessuno stato di Ares da salvare in " + str(config.TMP_DIR))

    root = _root_backup()
    identificativo = _id_snapshot(tipo)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
    rendi_privato(staging)
    componenti: dict[str, Any] = {}

    try:
        for nome in DATABASE:
            sorgente = config.TMP_DIR / nome
            if sorgente.is_file():
                _copia_sqlite(sorgente, staging / nome)
                componenti[nome] = True
            else:
                componenti[nome] = False

        sorgente_cronologia = config.TMP_DIR / CRONOLOGIA
        if sorgente_cronologia.is_file():
            # Copia semplice e non `_copia_sqlite`: e' un file di testo, e il
            # lock esclusivo garantisce che nessuna chat lo stia scrivendo.
            shutil.copy2(sorgente_cronologia, staging / CRONOLOGIA)
            componenti[CRONOLOGIA] = True
        else:
            componenti[CRONOLOGIA] = False

        lance_sorgente = Path(config.LANCEDB_URI)
        if lance_sorgente.is_dir() and any(voce.is_file() for voce in lance_sorgente.rglob("*")):
            lance_destinazione = staging / "lancedb"
            shutil.copytree(lance_sorgente, lance_destinazione)
            componenti["lancedb"] = {"present": True, "tables": _tabelle_lancedb(lance_destinazione)}
        else:
            componenti["lancedb"] = {"present": False, "tables": {}}

        manifest = {
            "format_version": FORMATO_BACKUP,
            "snapshot_id": identificativo,
            "type": tipo,
            "created_at": datetime.now(UTC).isoformat(),
            "source_state_dir": str(config.TMP_DIR.resolve()),
            "python_version": platform.python_version(),
            "agno_version": _versione_agno(),
            "ares_version": ares.__version__,
            "git": _git(),
            "models": {
                "main": config.MAIN_MODEL,
                "learning": config.LEARNING_MODEL,
                "embedder": config.EMBEDDER_MODEL,
                "embedder_dimensions": config.EMBEDDER_DIMENSIONS,
            },
            "namespace_format": "user/<id>",
            "components": componenti,
        }
        percorso_manifest = staging / MANIFEST
        percorso_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _privato(staging)
        _scrivi_checksum(staging)
        verifica_snapshot(staging, percorso_diretto=True)

        definitivo = root / identificativo
        _pubblica_snapshot(staging, definitivo)
        return definitivo
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def crea_snapshot(tipo: str = "manuale", acquisisci_lock: bool = True) -> Path:
    """Crea uno snapshot atomico. Ritorna la directory definitiva."""
    contesto = lock_stato(esclusivo=True) if acquisisci_lock else nullcontext()
    with contesto:
        return _crea_snapshot_senza_lock(tipo=tipo)


def _ordine_snapshot(percorso: Path) -> tuple[float, str]:
    """Ordina per istante reale, non per suffissi manuale/pre-restore."""
    try:
        manifest = json.loads((percorso / MANIFEST).read_text(encoding="utf-8"))
        creato = datetime.fromisoformat(manifest["created_at"])
        return creato.timestamp(), percorso.name
    except (KeyError, OSError, TypeError, UnicodeError, ValueError, json.JSONDecodeError):
        # Un manifest rotto deve restare visibile e verificabile. L'mtime e'
        # soltanto un ripiego per collocarlo nell'elenco.
        return percorso.stat().st_mtime, percorso.name


def _snapshot_dentro(root: Path) -> list[Path]:
    """Gli snapshot validi in una directory che esiste gia'.

    Separata da `elenco_snapshot` perche' quella passa da `_root_backup`, che
    la directory dei backup la crea. Va bene per chi sta per scriverci; non va
    bene per chi vuole soltanto sapere se ci sia qualcosa, e che altrimenti si
    lascerebbe dietro una directory vuota per aver fatto una domanda.
    """
    return sorted(
        (
            voce
            for voce in root.iterdir()
            if voce.is_dir() and not voce.is_symlink() and not voce.name.startswith(".") and (voce / MANIFEST).is_file()
        ),
        key=_ordine_snapshot,
    )


def elenco_snapshot() -> list[Path]:
    return _snapshot_dentro(_root_backup())


def promemoria_backup(soglia_giorni: int | None = None) -> list[str]:
    """Le righe da mostrare all'avvio se e' ora di rifare un backup.

    Elenco vuoto quando non c'e' niente da dire: nessuno stato da perdere,
    promemoria spento in `config.py`, o uno snapshot abbastanza recente. Un
    avviso che compare a ogni avvio smette di essere letto entro una
    settimana, ed e' peggio di nessun avviso perche' occupa il posto di
    quello vero.

    Legge soltanto: non crea la directory dei backup, non verifica i
    checksum, non apre i database. Costa una `iterdir` e la lettura di un
    manifest per snapshot, ed e' sul percorso di avvio della chat.

    Non solleva: un promemoria che impedisce di parlare con Ares ha invertito
    il rapporto fra la cosa e il suo promemoria. Se qualcosa va storto qui,
    tace - e il backup resta un comando esplicito, che e' come lo si e'
    voluto.
    """
    soglia = config.BACKUP_PROMEMORIA_GIORNI if soglia_giorni is None else soglia_giorni
    if soglia <= 0:
        return []
    try:
        if not _stato_presente():
            return []
        valida_percorsi()
        root = config.BACKUP_DIR
        disponibili = _snapshot_dentro(root) if root.is_dir() else []
        comando = "    " + (r".venv\Scripts\ares-backup" if os.name == "nt" else ".venv/bin/ares-backup") + " create"
        if not disponibili:
            return [
                "Nessuno snapshot: profilo, memorie ed entita' esistono in una copia sola.",
                comando,
            ]
        ultimo = disponibili[-1]
        adesso = datetime.now(UTC).timestamp()
        giorni = (adesso - _ordine_snapshot(ultimo)[0]) / 86400
        if giorni < soglia:
            return []
        return [
            "Ultimo snapshot " + str(int(giorni)) + " giorni fa (" + ultimo.name + ").",
            comando,
        ]
    except (ErroreBackup, OSError, ValueError):
        return []


def residui_restore() -> list[Path]:
    """Le directory che un restore interrotto puo' lasciare accanto allo stato.

    Su POSIX il restore e' due rinomine: lo stato corrente diventa
    `.tmp-precedente-<hex>` e la preparazione `.tmp-restore-<hex>` prende il
    suo posto. Un processo ucciso fra le due lascia il residuo e nessuna
    `tmp/`: al riavvio Ares ricrea uno stato vuoto e, senza questa lettura,
    riparte da zero senza dirlo. Un residuo resta anche dopo un restore
    riuscito, quando la copia precedente non si lascia rimuovere, e su
    Windows quando il rollback per copia fallisce a sua volta.

    Il residuo `-precedente-` e' lo stato che c'era prima del restore, e se
    il restore e' partito con `--skip-safety` ne e' l'unica copia; quello
    `-restore-` e' una preparazione che non e' mai stata installata. Nessuno
    dei due viene rimosso da qui: e' una decisione che spetta a chi puo'
    guardarci dentro.

    Legge soltanto e non solleva, per la ragione di `promemoria_backup`: e'
    sul percorso di avvio della chat.
    """
    try:
        stato = config.TMP_DIR.resolve()
        radice = stato.parent
        if not radice.is_dir():
            return []
        prefissi = ("." + stato.name + "-precedente-", "." + stato.name + "-restore-")
        return sorted(
            voce
            for voce in radice.iterdir()
            if voce.is_dir() and not voce.is_symlink() and voce.name.startswith(prefissi)
        )
    except OSError:
        return []


def _ultimo_snapshot_di_tipo(tipo: str) -> Path | None:
    """Lo snapshot piu' recente di un tipo, senza creare ne' verificare niente."""
    root = config.BACKUP_DIR
    if not root.is_dir():
        return None
    for percorso in reversed(_snapshot_dentro(root)):
        try:
            manifest = json.loads((percorso / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(manifest, dict) and manifest.get("type") == tipo:
            return percorso
    return None


def avviso_residui_restore() -> list[str]:
    """Le righe con cui dire che un restore e' rimasto a meta', o nessuna.

    La prima riga dice cosa e' successo, le altre dove sta il residuo e da
    dove si torna indietro. Lo snapshot pre-restore, se c'e', e' il rimedio
    che non richiede di capire il residuo: si nomina per intero, con il
    comando, perche' chi legge questo avviso ha appena scoperto che il suo
    archivio potrebbe essere vuoto e non ha voglia di cercare.
    """
    residui = residui_restore()
    if not residui:
        return []
    try:
        stato = config.TMP_DIR.resolve()
        precedenti = [r for r in residui if r.name.startswith("." + stato.name + "-precedente-")]
        sicurezza = _ultimo_snapshot_di_tipo("pre-restore") if precedenti else None
    except OSError:
        precedenti, sicurezza = residui, None
    comando = r".venv\Scripts\ares-backup" if os.name == "nt" else ".venv/bin/ares-backup"
    righe = ["Un restore non e' stato completato: accanto allo stato sono rimasti dei residui."]
    for residuo in residui:
        if residuo in precedenti:
            righe.append("    " + str(residuo) + "  (lo stato che c'era prima del restore)")
        else:
            righe.append("    " + str(residuo) + "  (preparazione mai installata: lo stato non e' stato toccato)")
    if precedenti:
        if sicurezza is not None:
            righe.append(
                "Se lo stato attuale e' incompleto, ripristina lo snapshot di sicurezza: "
                + comando
                + " restore "
                + sicurezza.name
            )
        else:
            righe.append("Nessuno snapshot pre-restore: quel residuo e' l'unica copia dello stato precedente.")
    righe.append("Ares non tocca i residui: controlla cosa contengono, poi eliminali o rimettili a posto a mano.")
    return righe


def risolvi_snapshot(nome: str) -> Path:
    disponibili = elenco_snapshot()
    if nome == "latest":
        if not disponibili:
            raise ErroreBackup("nessuno snapshot disponibile")
        return disponibili[-1]
    if Path(nome).name != nome or nome.startswith("."):
        raise ErroreBackup("identificativo snapshot non valido: " + repr(nome))
    candidato = _root_backup() / nome
    if not candidato.is_dir():
        raise ErroreBackup("snapshot inesistente: " + nome)
    return candidato


def verifica_snapshot(snapshot: Any, percorso_diretto: bool = False) -> dict[str, Any]:
    """Verifica manifest, insieme dei file, checksum e formati dei database."""
    percorso = Path(snapshot) if percorso_diretto else risolvi_snapshot(str(snapshot))
    return _verifica_integrita_snapshot(
        percorso,
        cronologia=CRONOLOGIA,
        modello_embedder=config.EMBEDDER_MODEL,
        dimensioni_embedder=config.EMBEDDER_DIMENSIONS,
    )


def ripristina_snapshot(nome: str, snapshot_sicurezza: bool = True) -> Path | None:
    return restore.ripristina_snapshot(
        nome,
        snapshot_sicurezza,
        restore.OperazioniRestore(
            crea_snapshot_senza_lock=_crea_snapshot_senza_lock,
            risolvi_snapshot=risolvi_snapshot,
            stato_presente=_stato_presente,
            verifica_snapshot=verifica_snapshot,
        ),
    )


def pota_snapshot(da_tenere: int, acquisisci_lock: bool = True) -> list[Path]:
    if da_tenere < 1:
        raise ErroreBackup("--keep deve essere almeno 1")
    contesto = lock_stato(esclusivo=True) if acquisisci_lock else nullcontext()
    with contesto:
        snapshot = elenco_snapshot()
        candidati = snapshot[:-da_tenere] if len(snapshot) > da_tenere else []
        for percorso in candidati:
            shutil.rmtree(percorso)
        return candidati


def main() -> int:
    return cli.main(
        cli.OperazioniBackup(
            avviso_residui=avviso_residui_restore,
            crea_snapshot=crea_snapshot,
            elenco_snapshot=elenco_snapshot,
            pota_snapshot=pota_snapshot,
            ripristina_snapshot=ripristina_snapshot,
            risolvi_snapshot=risolvi_snapshot,
            verifica_snapshot=verifica_snapshot,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
