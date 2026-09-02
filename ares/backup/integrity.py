"""Formato e verifiche di integrità degli snapshot di Ares."""

import hashlib
import json
import sqlite3
import string
import subprocess
import sys
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Any

from ares.state.platform_files import rendi_privato

FORMATO_BACKUP = 1
MANIFEST = "manifest.json"
CHECKSUM = "checksums.sha256"
DATABASE = ("kairos.db", "filesystem.db")


class ErroreBackup(RuntimeError):
    """Snapshot assente, corrotto, incompatibile o collocato male."""


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


def scrivi_checksum(snapshot: Path) -> None:
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


def verifica_sqlite(percorso: Path) -> None:
    if not percorso.is_file():
        raise ErroreBackup("database mancante: " + str(percorso))
    try:
        with closing(sqlite3.connect(str(percorso))) as connessione:
            esito = connessione.execute("pragma integrity_check").fetchone()
    except sqlite3.DatabaseError as errore:
        raise ErroreBackup("SQLite illeggibile: " + percorso.name + ": " + str(errore)) from errore
    if not esito or esito[0] != "ok":
        raise ErroreBackup("integrity_check fallito su " + percorso.name + ": " + repr(esito))


def conta_tabelle_lancedb(percorso: Path) -> dict[str, int]:
    """Verifica LanceDB in un processo isolato e ne conta le righe."""
    try:
        risultato = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("probe.py")), str(percorso)],
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


def _leggi_manifest(percorso: Path) -> dict[str, Any]:
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
    return manifest


def _verifica_file(percorso: Path) -> None:
    attesi = _leggi_checksum(percorso)
    presenti = {file.relative_to(percorso).as_posix(): _sha256(file) for file in _file_dello_snapshot(percorso)}
    if set(attesi) != set(presenti):
        mancanti = sorted(set(attesi) - set(presenti))
        inattesi = sorted(set(presenti) - set(attesi))
        raise ErroreBackup("insieme dei file diverso; mancanti=" + repr(mancanti) + ", inattesi=" + repr(inattesi))
    errati = [nome for nome in attesi if attesi[nome] != presenti[nome]]
    if errati:
        raise ErroreBackup("checksum errato: " + ", ".join(sorted(errati)))


def _verifica_lancedb(
    percorso: Path,
    manifest: dict[str, Any],
    lance: dict[str, Any],
    modello_embedder: str,
    dimensioni_embedder: int,
) -> None:
    ottenute = conta_tabelle_lancedb(percorso / "lancedb")
    if ottenute != lance.get("tables"):
        raise ErroreBackup("tabelle LanceDB diverse dal manifest: " + repr(ottenute))
    modelli = manifest.get("models") or {}
    if not isinstance(modelli, dict):
        raise ErroreBackup("manifest non valido: models non e' un oggetto")
    embedder = modelli.get("embedder")
    if embedder != modello_embedder:
        raise ErroreBackup("embedder incompatibile: snapshot=" + repr(embedder) + ", Ares=" + repr(modello_embedder))
    dimensioni = modelli.get("embedder_dimensions")
    if dimensioni != dimensioni_embedder:
        raise ErroreBackup(
            "dimensione embedding incompatibile: snapshot=" + repr(dimensioni) + ", Ares=" + repr(dimensioni_embedder)
        )


def _verifica_componenti(
    percorso: Path,
    manifest: dict[str, Any],
    cronologia: str,
    modello_embedder: str,
    dimensioni_embedder: int,
) -> None:
    componenti = manifest.get("components")
    if not isinstance(componenti, dict):
        raise ErroreBackup("manifest non valido: components non e' un oggetto")
    for nome in DATABASE:
        if componenti.get(nome):
            verifica_sqlite(percorso / nome)
    # Un componente che si dichiara presente e non c'e' non verrebbe visto dai
    # checksum, che guardano solo i file trovati: il manifest mentirebbe e il
    # restore fallirebbe a meta'.
    if componenti.get(cronologia) and not (percorso / cronologia).is_file():
        raise ErroreBackup("il manifest dichiara " + cronologia + ", che manca nello snapshot")
    lance = componenti.get("lancedb") or {}
    if not isinstance(lance, dict):
        raise ErroreBackup("manifest non valido: lancedb non e' un oggetto")
    if lance.get("present"):
        _verifica_lancedb(percorso, manifest, lance, modello_embedder, dimensioni_embedder)


def verifica_snapshot(
    percorso: Path,
    *,
    cronologia: str,
    modello_embedder: str,
    dimensioni_embedder: int,
) -> dict[str, Any]:
    """Verifica manifest, insieme dei file, checksum e formati dei database."""
    if percorso.is_symlink():
        raise ErroreBackup("la radice dello snapshot non puo' essere un link simbolico")
    manifest = _leggi_manifest(percorso)
    _verifica_file(percorso)
    _verifica_componenti(percorso, manifest, cronologia, modello_embedder, dimensioni_embedder)
    return manifest
