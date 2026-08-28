"""
Backup locale dello stato di Ares
=================================

Uso:
    .venv/bin/python backup.py create
    .venv/bin/python backup.py list
    .venv/bin/python backup.py verify latest
    .venv/bin/python backup.py restore <snapshot>
    .venv/bin/python backup.py prune --keep 20

Salva il cervello di Ares - i due SQLite e LanceDB - non il workspace. Ogni
snapshot e' una directory trasparente con manifest e checksum. Creazione e
restore richiedono il lock esclusivo: chiudere la chat prima di eseguirli.
"""

import argparse
import asyncio
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import string
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from contextlib import closing, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import config
from platform_files import rendi_privato
from state_lock import StatoOccupato, lock_stato

FORMATO_BACKUP = 1
MANIFEST = "manifest.json"
CHECKSUM = "checksums.sha256"
DATABASE = ("kairos.db", "filesystem.db")
SONDA_LANCEDB = "__lancedb-tables"

# La cronologia della riga di comando vive in tmp/ insieme al resto, ma non e'
# stato appreso: e' cio' che l'utente ha digitato. Da qui le due regole
# asimmetriche piu' sotto - lo snapshot la copia, il restore non la riporta
# indietro - che sono la stessa cosa detta due volte: un backup protegge da
# una perdita, un restore fa tornare indietro Ares, non chi gli parla.
CRONOLOGIA = config.CRONOLOGIA_FILE.name


class ErroreBackup(RuntimeError):
    """Snapshot assente, corrotto, incompatibile o collocato male."""


def _privato(percorso: Path) -> None:
    """Permessi locali: lo snapshot contiene conversazioni e profilo."""
    if percorso.is_dir():
        rendi_privato(percorso)
        for voce in percorso.rglob("*"):
            if voce.is_symlink():
                continue
            rendi_privato(voce)
    elif percorso.exists():
        rendi_privato(percorso)


def _si_sovrappongono(primo: Path, secondo: Path) -> bool:
    primo, secondo = primo.resolve(), secondo.resolve()
    return primo == secondo or primo.is_relative_to(secondo) or secondo.is_relative_to(primo)


def _rinomina_directory(sorgente: Path, destinazione: Path) -> None:
    """Pubblica una directory su un nome nuovo con una rinomina atomica.

    ``os.replace`` seleziona su Windows la semantica di sostituzione e alcuni
    filesystem la rifiutano per directory non vuote. Qui il target deve essere
    assente per contratto, quindi ``os.rename`` e' l'operazione corretta.
    """
    if os.path.lexists(destinazione):
        raise ErroreBackup("la destinazione della rinomina esiste gia': " + str(destinazione))
    os.rename(sorgente, destinazione)


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


def _sha256(percorso: Path) -> str:
    digest = hashlib.sha256()
    with percorso.open("rb") as sorgente:
        for blocco in iter(lambda: sorgente.read(1024 * 1024), b""):
            digest.update(blocco)
    return digest.hexdigest()


def _file_dello_snapshot(snapshot: Path) -> Iterable[Path]:
    for percorso in sorted(snapshot.rglob("*")):
        if percorso.is_symlink():
            raise ErroreBackup("uno snapshot non puo' contenere link simbolici: " + str(percorso))
        if percorso.is_file() and percorso.name != CHECKSUM:
            yield percorso


def _scrivi_checksum(snapshot: Path) -> None:
    righe = []
    for percorso in _file_dello_snapshot(snapshot):
        relativo = percorso.relative_to(snapshot).as_posix()
        righe.append(_sha256(percorso) + "  " + relativo)
    destinazione = snapshot / CHECKSUM
    destinazione.write_text("\n".join(righe) + "\n", encoding="utf-8")
    rendi_privato(destinazione)


def _leggi_checksum(snapshot: Path) -> dict[str, str]:
    percorso = snapshot / CHECKSUM
    if not percorso.is_file():
        raise ErroreBackup("manca " + CHECKSUM)
    risultati: dict[str, str] = {}
    try:
        righe = percorso.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as errore:
        raise ErroreBackup("checksum illeggibile: " + str(errore)) from errore
    for numero, riga in enumerate(righe, 1):
        if not riga.strip():
            continue
        try:
            digest, relativo = riga.split("  ", 1)
        except ValueError as errore:
            raise ErroreBackup("riga checksum non valida: " + str(numero)) from errore
        cammino = Path(relativo)
        if (
            cammino.is_absolute()
            or ".." in cammino.parts
            or len(digest) != 64
            or any(carattere not in string.hexdigits for carattere in digest)
            or cammino.as_posix() in risultati
        ):
            raise ErroreBackup("riga checksum non sicura: " + str(numero))
        risultati[cammino.as_posix()] = digest
    return risultati


def _integrita_sqlite(percorso: Path) -> None:
    if not percorso.is_file():
        raise ErroreBackup("database mancante: " + str(percorso))
    try:
        with closing(sqlite3.connect(str(percorso))) as connessione:
            esito = connessione.execute("pragma integrity_check").fetchone()
    except sqlite3.DatabaseError as errore:
        raise ErroreBackup("SQLite illeggibile: " + percorso.name + ": " + str(errore)) from errore
    if not esito or esito[0] != "ok":
        raise ErroreBackup("integrity_check fallito su " + percorso.name + ": " + repr(esito))


def _copia_sqlite(sorgente: Path, destinazione: Path) -> None:
    _integrita_sqlite(sorgente)
    origine = sqlite3.connect(str(sorgente))
    copia = sqlite3.connect(str(destinazione))
    try:
        origine.backup(copia)
    finally:
        copia.close()
        origine.close()
    rendi_privato(destinazione)
    _integrita_sqlite(destinazione)


async def _sonda_lancedb_locale(percorso: Path) -> dict[str, int]:
    """Conta le righe usando soltanto l'API pubblica che espone ``close``."""
    import lancedb

    conteggi = {}
    with await lancedb.connect_async(str(percorso)) as connessione:
        risultato = await connessione.list_tables()
        for nome in sorted(risultato.tables):
            with await connessione.open_table(nome) as tabella:
                conteggi[nome] = int(await tabella.count_rows())
    return conteggi


def _tabelle_lancedb(percorso: Path) -> dict[str, int]:
    """Verifica LanceDB in un processo isolato e ne conta le righe.

    Alcuni reader nativi possono conservare per poco tempo handle sui frammenti
    anche dopo ``close``. Su Windows cio' impedirebbe la successiva rinomina
    atomica della directory; la fine del processo sonda chiude gli handle a
    livello di sistema prima che il chiamante prosegua.
    """
    try:
        risultato = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), SONDA_LANCEDB, str(percorso)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if risultato.returncode != 0:
            dettaglio = (risultato.stderr or risultato.stdout).strip()
            raise ErroreBackup(dettaglio or "la sonda LanceDB non ha prodotto un risultato")
        dati = json.loads(risultato.stdout)
        if not isinstance(dati, dict) or any(
            not isinstance(nome, str) or not isinstance(righe, int) or righe < 0 for nome, righe in dati.items()
        ):
            raise ErroreBackup("risposta non valida dalla sonda LanceDB")
        return dict(sorted(dati.items()))
    except Exception as errore:
        raise ErroreBackup("LanceDB illeggibile in " + str(percorso) + ": " + str(errore)) from errore


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
    if tipo not in {"manuale", "pre-merge", "pre-restore"}:
        raise ErroreBackup("tipo di snapshot non valido: " + repr(tipo))
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_state_dir": str(config.TMP_DIR.resolve()),
            "python_version": platform.python_version(),
            "agno_version": _versione_agno(),
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


def elenco_snapshot() -> list[Path]:
    root = _root_backup()
    return sorted(
        (
            voce
            for voce in root.iterdir()
            if voce.is_dir() and not voce.is_symlink() and not voce.name.startswith(".") and (voce / MANIFEST).is_file()
        ),
        key=_ordine_snapshot,
    )


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
    if percorso.is_symlink():
        raise ErroreBackup("la radice dello snapshot non puo' essere un link simbolico")
    manifest_path = percorso / MANIFEST
    if not manifest_path.is_file():
        raise ErroreBackup("manca " + MANIFEST + " in " + str(percorso))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as errore:
        raise ErroreBackup("manifest illeggibile: " + str(errore)) from errore
    if not isinstance(manifest, dict):
        raise ErroreBackup("manifest non valido: la radice deve essere un oggetto")
    if manifest.get("format_version") != FORMATO_BACKUP:
        raise ErroreBackup("formato backup non supportato: " + repr(manifest.get("format_version")))
    if not isinstance(manifest.get("snapshot_id"), str):
        raise ErroreBackup("manifest non valido: manca snapshot_id")

    attesi = _leggi_checksum(percorso)
    presenti = {file.relative_to(percorso).as_posix(): _sha256(file) for file in _file_dello_snapshot(percorso)}
    if set(attesi) != set(presenti):
        mancanti = sorted(set(attesi) - set(presenti))
        inattesi = sorted(set(presenti) - set(attesi))
        raise ErroreBackup("insieme dei file diverso; mancanti=" + repr(mancanti) + ", inattesi=" + repr(inattesi))
    errati = [nome for nome in attesi if attesi[nome] != presenti[nome]]
    if errati:
        raise ErroreBackup("checksum errato: " + ", ".join(sorted(errati)))

    componenti = manifest.get("components")
    if not isinstance(componenti, dict):
        raise ErroreBackup("manifest non valido: components non e' un oggetto")
    for nome in DATABASE:
        if componenti.get(nome):
            _integrita_sqlite(percorso / nome)
    # Un componente che si dichiara presente e non c'e' non verrebbe visto dai
    # checksum, che guardano solo i file trovati: il manifest mentirebbe e il
    # restore fallirebbe a meta'.
    if componenti.get(CRONOLOGIA) and not (percorso / CRONOLOGIA).is_file():
        raise ErroreBackup("il manifest dichiara " + CRONOLOGIA + ", che manca nello snapshot")
    lance = componenti.get("lancedb") or {}
    if not isinstance(lance, dict):
        raise ErroreBackup("manifest non valido: lancedb non e' un oggetto")
    if lance.get("present"):
        ottenute = _tabelle_lancedb(percorso / "lancedb")
        if ottenute != lance.get("tables"):
            raise ErroreBackup("tabelle LanceDB diverse dal manifest: " + repr(ottenute))
        modelli = manifest.get("models") or {}
        if not isinstance(modelli, dict):
            raise ErroreBackup("manifest non valido: models non e' un oggetto")
        embedder = modelli.get("embedder")
        if embedder != config.EMBEDDER_MODEL:
            raise ErroreBackup(
                "embedder incompatibile: snapshot=" + repr(embedder) + ", Ares=" + repr(config.EMBEDDER_MODEL)
            )
        dimensioni = modelli.get("embedder_dimensions")
        if dimensioni != config.EMBEDDER_DIMENSIONS:
            raise ErroreBackup(
                "dimensione embedding incompatibile: snapshot="
                + repr(dimensioni)
                + ", Ares="
                + repr(config.EMBEDDER_DIMENSIONS)
            )
    return manifest


def _dimensione(percorso: Path) -> int:
    return sum(voce.stat().st_size for voce in percorso.rglob("*") if voce.is_file())


def _prepara_restore(snapshot: Path, manifest: dict[str, Any]) -> Path:
    parent = config.TMP_DIR.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="." + config.TMP_DIR.name + "-restore-", dir=parent))
    rendi_privato(staging)
    componenti = manifest.get("components") or {}
    try:
        for nome in DATABASE:
            if componenti.get(nome):
                shutil.copy2(snapshot / nome, staging / nome)
                _integrita_sqlite(staging / nome)
        # Il restore sostituisce l'intera directory dello stato, quindi cio'
        # che non entra in staging viene cancellato. La cronologia viva ha la
        # precedenza su quella dello snapshot: riportare indietro i database e'
        # il senso dell'operazione, riavvolgere cio' che l'utente ha digitato
        # no. Quella dello snapshot serve al caso per cui esiste un backup:
        # tmp/ persa, e allora non c'e' niente da conservare.
        viva = config.TMP_DIR / CRONOLOGIA
        if viva.is_file():
            shutil.copy2(viva, staging / CRONOLOGIA)
        elif componenti.get(CRONOLOGIA):
            shutil.copy2(snapshot / CRONOLOGIA, staging / CRONOLOGIA)

        lance = componenti.get("lancedb") or {}
        if lance.get("present"):
            shutil.copytree(snapshot / "lancedb", staging / "lancedb")
            if _tabelle_lancedb(staging / "lancedb") != lance.get("tables"):
                raise ErroreBackup("LanceDB cambia durante la preparazione del restore")
        _privato(staging)
        return staging
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _svuota_directory(percorso: Path) -> None:
    """Rimuove il contenuto lasciando stabile la directory radice."""
    for voce in list(percorso.iterdir()):
        if voce.is_symlink() or voce.is_file():
            voce.unlink()
        else:
            shutil.rmtree(voce)


def _installa_restore_per_copia(staging: Path, destinazione: Path, precedente: Path) -> None:
    """Fallback Windows con copia di rollback gia' pronta prima dello swap."""
    esisteva = destinazione.is_dir()
    try:
        if esisteva:
            shutil.copytree(destinazione, precedente)
        else:
            destinazione.mkdir(parents=True)
        _svuota_directory(destinazione)
        shutil.copytree(staging, destinazione, dirs_exist_ok=True)
        _privato(destinazione)
    except Exception as errore_originale:
        try:
            if destinazione.is_dir():
                _svuota_directory(destinazione)
            if esisteva and precedente.exists():
                destinazione.mkdir(parents=True, exist_ok=True)
                shutil.copytree(precedente, destinazione, dirs_exist_ok=True)
            elif destinazione.exists():
                shutil.rmtree(destinazione)
        except Exception as errore_rollback:
            raise ErroreBackup(
                "restore fallito ("
                + str(errore_originale)
                + ") e rollback fallito ("
                + str(errore_rollback)
                + "); copia precedente: "
                + str(precedente)
            ) from errore_rollback
        raise
    else:
        shutil.rmtree(precedente, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)


def ripristina_snapshot(nome: str, snapshot_sicurezza: bool = True) -> Path | None:
    """Ripristina uno snapshot verificato e ritorna l'eventuale pre-restore."""
    with lock_stato(esclusivo=True):
        snapshot = risolvi_snapshot(nome)
        manifest = verifica_snapshot(snapshot, percorso_diretto=True)
        sicurezza = None
        if snapshot_sicurezza and _stato_presente():
            sicurezza = _crea_snapshot_senza_lock(tipo="pre-restore")

        staging = _prepara_restore(snapshot, manifest)
        destinazione = config.TMP_DIR.resolve()
        precedente = destinazione.with_name("." + destinazione.name + "-precedente-" + uuid4().hex)
        if os.name == "nt":
            # Windows puo' rifiutare il rename di directory LanceDB non vuote
            # anche senza processi Ares attivi. La copia precedente permette
            # il rollback e lo snapshot pre-restore resta la rete di sicurezza
            # persistente in caso di interruzione del processo.
            _installa_restore_per_copia(staging, destinazione, precedente)
            return sicurezza

        spostato = False
        try:
            if destinazione.exists():
                _rinomina_directory(destinazione, precedente)
                spostato = True
            _rinomina_directory(staging, destinazione)
        except Exception:
            if spostato and precedente.exists() and not destinazione.exists():
                _rinomina_directory(precedente, destinazione)
            shutil.rmtree(staging, ignore_errors=True)
            raise
        else:
            if precedente.exists():
                # Il nuovo stato e' gia' installato: un residuo che non si
                # lascia rimuovere non deve trasformare un restore riuscito
                # in un falso fallimento.
                shutil.rmtree(precedente, ignore_errors=True)
        return sicurezza


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


def _stampa_elenco() -> None:
    snapshot = elenco_snapshot()
    if not snapshot:
        print("Nessuno snapshot in", config.BACKUP_DIR)
        return
    for percorso in reversed(snapshot):
        try:
            manifest = json.loads((percorso / MANIFEST).read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest non oggetto")
            tipo = manifest.get("type", "?")
            creato = manifest.get("created_at", "?")
        except (OSError, UnicodeError, json.JSONDecodeError):
            tipo, creato = "CORROTTO", "?"
        print(percorso.name, " ", tipo, " ", creato, " ", _dimensione(percorso), "byte")


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == SONDA_LANCEDB:
        try:
            print(json.dumps(asyncio.run(_sonda_lancedb_locale(Path(sys.argv[2]))), sort_keys=True))
        except Exception as errore:
            print(str(errore), file=sys.stderr)
            return 1
        return 0

    parser = argparse.ArgumentParser(description="Snapshot locali dello stato di Ares")
    sottocomandi = parser.add_subparsers(dest="comando", required=True)
    sottocomandi.add_parser("create", help="crea e verifica uno snapshot")
    sottocomandi.add_parser("list", help="elenca gli snapshot")
    verifica = sottocomandi.add_parser("verify", help="verifica checksum e database")
    verifica.add_argument("snapshot", nargs="?", default="latest")
    restore = sottocomandi.add_parser("restore", help="ripristina uno snapshot")
    restore.add_argument("snapshot")
    restore.add_argument("--yes", action="store_true", help="non chiedere conferma")
    restore.add_argument(
        "--skip-safety",
        action="store_true",
        help="ripristina senza snapshot pre-restore (solo se lo stato corrente e' irrecuperabile)",
    )
    prune = sottocomandi.add_parser("prune", help="elimina gli snapshot piu' vecchi")
    prune.add_argument("--keep", type=int, default=config.BACKUP_KEEP)
    prune.add_argument("--yes", action="store_true", help="non chiedere conferma")
    args = parser.parse_args()

    try:
        if args.comando == "create":
            creato = crea_snapshot()
            print("Snapshot creato e verificato:", creato)
        elif args.comando == "list":
            _stampa_elenco()
        elif args.comando == "verify":
            manifest = verifica_snapshot(args.snapshot)
            print("Snapshot valido:", manifest["snapshot_id"])
        elif args.comando == "restore":
            snapshot = risolvi_snapshot(args.snapshot)
            verifica_snapshot(snapshot, percorso_diretto=True)
            if not args.yes:
                conferma = input("Scrivi " + snapshot.name + " per ripristinarlo: ").strip()
                if conferma != snapshot.name:
                    print("Restore annullato.")
                    return 2
            sicurezza = ripristina_snapshot(
                snapshot.name,
                snapshot_sicurezza=not args.skip_safety,
            )
            print("Restore completato:", snapshot.name)
            if sicurezza is not None:
                print("Stato precedente salvato in:", sicurezza)
        elif args.comando == "prune":
            if args.keep < 1:
                raise ErroreBackup("--keep deve essere almeno 1")
            disponibili = elenco_snapshot()
            candidati = disponibili[: -args.keep] if len(disponibili) > args.keep else []
            if not candidati:
                print("Niente da eliminare; snapshot:", len(disponibili), " keep:", args.keep)
                return 0
            print("Snapshot da eliminare:")
            for percorso in candidati:
                print("-", percorso.name)
            if not args.yes and input("Scrivi ELIMINA per continuare: ").strip() != "ELIMINA":
                print("Prune annullato.")
                return 2
            # Fra anteprima e conferma potrebbe essere nato uno snapshot.
            # Non eliminare mai qualcosa che l'utente non ha appena visto.
            nomi_visti = [percorso.name for percorso in candidati]
            with lock_stato(esclusivo=True):
                attuali = elenco_snapshot()
                candidati_attuali = attuali[: -args.keep] if len(attuali) > args.keep else []
                if [percorso.name for percorso in candidati_attuali] != nomi_visti:
                    raise ErroreBackup("l'elenco degli snapshot e' cambiato; ripeti prune")
                eliminati = pota_snapshot(args.keep, acquisisci_lock=False)
            print("Eliminati", len(eliminati), "snapshot; conservati", args.keep)
    except (ErroreBackup, StatoOccupato, OSError) as errore:
        print("ERRORE:", errore)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
