"""
Prova completa di backup e restore, senza Ollama
================================================

Crea due SQLite e una tabella LanceDB in una directory temporanea, li salva,
li modifica e ripristina lo snapshot. Prova anche checksum, lock, guardie sui
percorsi e pruning. Non legge ne' scrive tmp/ o ares-backup reali.
"""

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import closing, nullcontext, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

RADICE_PROVA = Path(tempfile.mkdtemp(prefix="ares-backup-test-"))
os.environ["ARES_TMP"] = str(RADICE_PROVA / "stato")
os.environ["ARES_BACKUP_DIR"] = str(RADICE_PROVA / "backup")
os.environ["ARES_WORKSPACE"] = str(RADICE_PROVA / "lavoro")

from ares import config  # noqa: E402
from ares.backup import files, integrity, restore, snapshots  # noqa: E402
from ares.backup.snapshots import (  # noqa: E402
    ErroreBackup,
    _installa_restore_per_copia,
    _pubblica_snapshot,
    _ultimo_snapshot_di_tipo,
    avviso_residui_restore,
    crea_snapshot,
    elenco_snapshot,
    pota_snapshot,
    promemoria_backup,
    residui_restore,
    ripristina_snapshot,
    valida_percorsi,
    verifica_snapshot,
)
from ares.state.lock import StatoOccupato, lock_stato  # noqa: E402

OPERAZIONE_LANCEDB = r"""
import json
import sys

import lancedb

azione, uri, dimensioni = sys.argv[1], sys.argv[2], int(sys.argv[3])
db = lancedb.connect(uri)
if azione == "create":
    db.create_table(
        "learned_knowledge",
        data=[{"id": "prima", "testo": "prima intuizione", "vector": [0.0] * dimensioni}],
    )
elif azione == "add":
    db.open_table("learned_knowledge").add(
        [{"id": "seconda", "testo": "seconda intuizione", "vector": [1.0] * dimensioni}]
    )
elif azione == "count":
    print(json.dumps(db.open_table("learned_knowledge").count_rows()))
else:
    raise ValueError("operazione LanceDB sconosciuta: " + azione)
"""


def esigi(condizione: object, messaggio: str) -> None:
    if not condizione:
        raise AssertionError(messaggio)


def ok(nome: str, nota: str) -> None:
    print("ok      ", nome.ljust(24), "-", nota)


def crea_sqlite(percorso: Path, valore: str) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(percorso)) as connessione, connessione:
        connessione.execute("pragma journal_mode=wal").fetchone()
        connessione.execute("create table prova (valore text not null)")
        connessione.execute("insert into prova values (?)", (valore,))


def aggiungi_sqlite(percorso: Path, valore: str) -> None:
    with closing(sqlite3.connect(percorso)) as connessione, connessione:
        connessione.execute("insert into prova values (?)", (valore,))


def valori_sqlite(percorso: Path) -> list[str]:
    with closing(sqlite3.connect(percorso)) as connessione:
        return [riga[0] for riga in connessione.execute("select valore from prova order by rowid")]


def journal_mode(percorso: Path) -> str:
    with closing(sqlite3.connect(percorso)) as connessione:
        return str(connessione.execute("pragma journal_mode").fetchone()[0]).casefold()


def esigi_errore(azione: Callable[[], object], frammento: str) -> None:
    """Richiede un ErroreBackup leggibile, non una generica eccezione."""
    try:
        azione()
    except ErroreBackup as errore:
        esigi(frammento in str(errore), "errore inatteso: " + str(errore))
    else:
        esigi(False, "operazione accettata; atteso errore contenente " + repr(frammento))


def manifest_minimo() -> dict[str, Any]:
    return {
        "format_version": integrity.FORMATO_BACKUP,
        "snapshot_id": "sintetico",
        "models": {
            "embedder": config.EMBEDDER_MODEL,
            "embedder_dimensions": config.EMBEDDER_DIMENSIONS,
        },
        "components": {
            "kairos.db": False,
            "filesystem.db": False,
            config.CRONOLOGIA_FILE.name: False,
            "lancedb": {"present": False, "tables": {}},
        },
    }


def snapshot_sintetico(radice: Path, nome: str, manifest: Any | None = None) -> Path:
    snapshot = radice / nome
    snapshot.mkdir(parents=True)
    dati = manifest_minimo() if manifest is None else manifest
    (snapshot / integrity.MANIFEST).write_text(json.dumps(dati), encoding="utf-8")
    integrity.scrivi_checksum(snapshot)
    return snapshot


def verifica_integrita_sintetica(snapshot: Path) -> dict[str, Any]:
    return integrity.verifica_snapshot(
        snapshot,
        cronologia=config.CRONOLOGIA_FILE.name,
        modello_embedder=config.EMBEDDER_MODEL,
        dimensioni_embedder=config.EMBEDDER_DIMENSIONS,
    )


def prova_errori_integrita() -> None:
    """Manifest e checksum ostili devono fallire prima di arrivare al restore."""
    radice = RADICE_PROVA / "integrita-mirata"
    radice.mkdir()

    mancante = radice / "manifest-mancante"
    mancante.mkdir()
    esigi_errore(lambda: verifica_integrita_sintetica(mancante), "manca manifest.json")

    illeggibile = radice / "manifest-illeggibile"
    illeggibile.mkdir()
    (illeggibile / integrity.MANIFEST).write_text("{", encoding="utf-8")
    esigi_errore(lambda: verifica_integrita_sintetica(illeggibile), "manifest illeggibile")

    lista = snapshot_sintetico(radice, "manifest-lista", [])
    esigi_errore(lambda: verifica_integrita_sintetica(lista), "radice deve essere un oggetto")

    formato = manifest_minimo()
    formato["format_version"] = 999
    incompatibile = snapshot_sintetico(radice, "formato-incompatibile", formato)
    esigi_errore(lambda: verifica_integrita_sintetica(incompatibile), "formato backup non supportato")

    senza_id = manifest_minimo()
    senza_id.pop("snapshot_id")
    identificativo = snapshot_sintetico(radice, "identificativo-mancante", senza_id)
    esigi_errore(lambda: verifica_integrita_sintetica(identificativo), "manca snapshot_id")

    senza_checksum = snapshot_sintetico(radice, "checksum-mancante")
    (senza_checksum / integrity.CHECKSUM).unlink()
    esigi_errore(lambda: verifica_integrita_sintetica(senza_checksum), "manca checksums.sha256")

    checksum_rotto = snapshot_sintetico(radice, "checksum-malformato")
    (checksum_rotto / integrity.CHECKSUM).write_text("senza separatore\n", encoding="utf-8")
    esigi_errore(lambda: verifica_integrita_sintetica(checksum_rotto), "riga checksum non valida")

    checksum_insicuro = snapshot_sintetico(radice, "checksum-insicuro")
    (checksum_insicuro / integrity.CHECKSUM).write_text("0" * 64 + "  ../manifest.json\n", encoding="utf-8")
    esigi_errore(lambda: verifica_integrita_sintetica(checksum_insicuro), "riga checksum non sicura")

    file_inatteso = snapshot_sintetico(radice, "file-inatteso")
    (file_inatteso / "intruso.bin").write_bytes(b"intruso")
    esigi_errore(lambda: verifica_integrita_sintetica(file_inatteso), "insieme dei file diverso")

    componenti_rotti = manifest_minimo()
    componenti_rotti["components"] = []
    componenti = snapshot_sintetico(radice, "componenti-malformati", componenti_rotti)
    esigi_errore(lambda: verifica_integrita_sintetica(componenti), "components non e' un oggetto")

    cronologia_mancante = manifest_minimo()
    cronologia_mancante["components"][config.CRONOLOGIA_FILE.name] = True
    cronologia = snapshot_sintetico(radice, "cronologia-mancante", cronologia_mancante)
    esigi_errore(lambda: verifica_integrita_sintetica(cronologia), "che manca nello snapshot")

    lancedb_malformato = manifest_minimo()
    lancedb_malformato["components"]["lancedb"] = "presente"
    lancedb = snapshot_sintetico(radice, "lancedb-malformato", lancedb_malformato)
    esigi_errore(lambda: verifica_integrita_sintetica(lancedb), "lancedb non e' un oggetto")

    database_mancante = manifest_minimo()
    database_mancante["components"]["kairos.db"] = True
    database = snapshot_sintetico(radice, "database-mancante", database_mancante)
    esigi_errore(lambda: verifica_integrita_sintetica(database), "database mancante")

    sqlite_rotto = radice / "sqlite-illeggibile.db"
    sqlite_rotto.write_bytes(b"non e' sqlite")
    esigi_errore(lambda: integrity.verifica_sqlite(sqlite_rotto), "SQLite illeggibile")

    tabelle_diverse = manifest_minimo()
    tabelle_diverse["components"]["lancedb"] = {"present": True, "tables": {"attesa": 1}}
    snapshot_tabelle = snapshot_sintetico(radice, "tabelle-diverse", tabelle_diverse)
    with patch("ares.backup.integrity.conta_tabelle_lancedb", return_value={"ottenuta": 1}):
        esigi_errore(lambda: verifica_integrita_sintetica(snapshot_tabelle), "tabelle LanceDB diverse")

    modelli_malformati = manifest_minimo()
    modelli_malformati["components"]["lancedb"] = {"present": True, "tables": {}}
    modelli_malformati["models"] = ["non-oggetto"]
    snapshot_modelli = snapshot_sintetico(radice, "modelli-malformati", modelli_malformati)
    with patch("ares.backup.integrity.conta_tabelle_lancedb", return_value={}):
        esigi_errore(lambda: verifica_integrita_sintetica(snapshot_modelli), "models non e' un oggetto")

    dimensioni_errate = manifest_minimo()
    dimensioni_errate["components"]["lancedb"] = {"present": True, "tables": {}}
    dimensioni_errate["models"]["embedder_dimensions"] = config.EMBEDDER_DIMENSIONS + 1
    snapshot_dimensioni = snapshot_sintetico(radice, "dimensioni-errate", dimensioni_errate)
    with patch("ares.backup.integrity.conta_tabelle_lancedb", return_value={}):
        esigi_errore(lambda: verifica_integrita_sintetica(snapshot_dimensioni), "dimensione embedding incompatibile")

    shutil.rmtree(radice)


def prova_errori_sonda() -> None:
    """Il confine JSON della sonda rifiuta processi e risposte ambigue."""
    percorso = RADICE_PROVA / "lancedb-sintetico"
    fallito = subprocess.CompletedProcess([], 1, stdout="", stderr="guasto nativo")
    with patch("ares.backup.integrity.subprocess.run", return_value=fallito):
        esigi_errore(lambda: integrity.conta_tabelle_lancedb(percorso), "guasto nativo")

    non_json = subprocess.CompletedProcess([], 0, stdout="non-json", stderr="")
    with patch("ares.backup.integrity.subprocess.run", return_value=non_json):
        esigi_errore(lambda: integrity.conta_tabelle_lancedb(percorso), "LanceDB illeggibile")

    forma_errata = subprocess.CompletedProcess([], 0, stdout='{"tabella": -1}', stderr="")
    with patch("ares.backup.integrity.subprocess.run", return_value=forma_errata):
        esigi_errore(lambda: integrity.conta_tabelle_lancedb(percorso), "risposta non valida")

    valida = subprocess.CompletedProcess([], 0, stdout='{"zeta": 2, "alfa": 1}', stderr="")
    with patch("ares.backup.integrity.subprocess.run", return_value=valida):
        esigi(
            list(integrity.conta_tabelle_lancedb(percorso)) == ["alfa", "zeta"],
            "la risposta della sonda non e' stata normalizzata",
        )


def prova_rollback_copia() -> None:
    """Il fallback a copia ripristina il vecchio stato, e segnala un doppio guasto."""
    copia_vera = shutil.copytree

    def scenario(nome: str, rollback_fallisce: bool) -> tuple[Path, Path, Path]:
        radice = RADICE_PROVA / nome
        staging = radice / "staging"
        destinazione = radice / "destinazione"
        precedente = radice / "precedente"
        staging.mkdir(parents=True)
        destinazione.mkdir()
        (staging / "stato.txt").write_text("nuovo\n", encoding="utf-8")
        (destinazione / "stato.txt").write_text("vecchio\n", encoding="utf-8")

        def copia_con_guasto(sorgente, destinazione_copia, *argomenti, **opzioni):
            if Path(sorgente) == staging:
                raise OSError("installazione interrotta")
            if rollback_fallisce and Path(sorgente) == precedente:
                raise OSError("rollback interrotto")
            return copia_vera(sorgente, destinazione_copia, *argomenti, **opzioni)

        with patch("ares.backup.restore.shutil.copytree", side_effect=copia_con_guasto):
            if rollback_fallisce:
                esigi_errore(
                    lambda: restore._installa_restore_per_copia(staging, destinazione, precedente),
                    "rollback fallito",
                )
            else:
                try:
                    restore._installa_restore_per_copia(staging, destinazione, precedente)
                except OSError as errore:
                    esigi("installazione interrotta" in str(errore), "errore originale perso: " + str(errore))
                else:
                    esigi(False, "il guasto di installazione non e' stato propagato")
        return staging, destinazione, precedente

    _, ripristinata, copia_precedente = scenario("rollback-copia-riuscito", False)
    esigi(
        (ripristinata / "stato.txt").read_text(encoding="utf-8") == "vecchio\n",
        "il rollback a copia non ha ripristinato lo stato precedente",
    )
    esigi(copia_precedente.is_dir(), "la copia precedente e' stata rimossa dopo un'installazione fallita")

    _, incompleta, copia_salvataggio = scenario("rollback-copia-fallito", True)
    esigi(incompleta.is_dir(), "la destinazione del doppio guasto non e' rimasta diagnosticabile")
    esigi(copia_salvataggio.is_dir(), "la copia precedente del doppio guasto e' stata persa")


def prova_rollback_rinomina() -> None:
    """Il percorso POSIX rimette al suo posto lo stato se il secondo rename fallisce."""
    radice = RADICE_PROVA / "rollback-rinomina"
    destinazione = radice / "stato-atomico"
    staging = radice / "staging"
    destinazione.mkdir(parents=True)
    staging.mkdir()
    (destinazione / "stato.txt").write_text("vecchio\n", encoding="utf-8")
    (staging / "stato.txt").write_text("nuovo\n", encoding="utf-8")
    tmp_vera = config.TMP_DIR
    config.TMP_DIR = destinazione

    operazioni = restore.OperazioniRestore(
        crea_snapshot_senza_lock=lambda _tipo: radice / "non-creato",
        risolvi_snapshot=lambda _nome: radice / "snapshot",
        stato_presente=lambda: False,
        verifica_snapshot=lambda _snapshot, _diretto: {},
    )

    def rinomina_con_guasto(sorgente: Path, destinazione_rinomina: Path) -> None:
        if sorgente == staging:
            raise OSError("seconda rinomina interrotta")
        os.rename(sorgente, destinazione_rinomina)

    try:
        with (
            patch("ares.backup.restore.lock_stato", return_value=nullcontext()),
            patch("ares.backup.restore._prepara_restore", return_value=staging),
            patch("ares.backup.restore._rinomina_directory", side_effect=rinomina_con_guasto),
            patch("ares.backup.restore.os.name", "posix"),
        ):
            try:
                restore.ripristina_snapshot("snapshot", False, operazioni)
            except OSError as errore:
                esigi("seconda rinomina interrotta" in str(errore), "errore rename perso: " + str(errore))
            else:
                esigi(False, "il fallimento del secondo rename non e' stato propagato")
    finally:
        config.TMP_DIR = tmp_vera

    esigi(
        (destinazione / "stato.txt").read_text(encoding="utf-8") == "vecchio\n",
        "il rollback atomico non ha rimesso al suo posto lo stato precedente",
    )
    esigi(not staging.exists(), "lo staging fallito non e' stato eliminato")
    esigi(
        not list(radice.glob(".stato-atomico-precedente-*")),
        "il rollback atomico ha lasciato una directory precedente",
    )


def prova_guardie_restore() -> None:
    """Una preparazione incompleta non lascia staging e una rinomina non sovrascrive."""
    esigi(snapshots._privato is files.rendi_albero_privato, "la façade non espone la primitiva dei permessi")
    esigi(
        snapshots._rinomina_directory is files.rinomina_directory_nuova,
        "la façade non espone la primitiva di rinomina",
    )
    esigi(
        restore._privato is files.rendi_albero_privato,
        "il restore non usa la primitiva condivisa dei permessi",
    )
    esigi(
        restore._rinomina_directory is files.rinomina_directory_nuova,
        "il restore non usa la primitiva condivisa di rinomina",
    )

    file_privato = RADICE_PROVA / "file-privato"
    file_privato.write_text("segreto\n", encoding="utf-8")
    if os.name == "posix":
        file_privato.chmod(0o666)
    files.rendi_albero_privato(file_privato)
    if os.name == "posix":
        esigi((file_privato.stat().st_mode & 0o777) == 0o600, "il file singolo non e' stato reso privato")
    files.rendi_albero_privato(RADICE_PROVA / "percorso-assente")

    albero_privato = RADICE_PROVA / "albero-privato"
    albero_privato.mkdir()
    bersaglio = albero_privato / "bersaglio.txt"
    collegamento = albero_privato / "collegamento.txt"
    bersaglio.write_text("segreto\n", encoding="utf-8")
    try:
        collegamento.symlink_to(bersaglio)
    except OSError as errore:
        if os.name != "nt" or getattr(errore, "winerror", None) != 1314:
            raise
    files.rendi_albero_privato(albero_privato)

    snapshot = RADICE_PROVA / "restore-incompleto"
    snapshot.mkdir()
    manifest = manifest_minimo()
    manifest["components"]["kairos.db"] = True
    parent = config.TMP_DIR.resolve().parent
    prima = set(parent.glob("." + config.TMP_DIR.name + "-restore-*"))
    try:
        restore._prepara_restore(snapshot, manifest)
    except FileNotFoundError:
        pass
    else:
        esigi(False, "una preparazione senza il database dichiarato non e' fallita")
    dopo = set(parent.glob("." + config.TMP_DIR.name + "-restore-*"))
    esigi(dopo == prima, "la preparazione fallita ha lasciato uno staging: " + repr(sorted(dopo - prima)))

    sorgente = RADICE_PROVA / "rinomina-sorgente"
    destinazione = RADICE_PROVA / "rinomina-destinazione"
    sorgente.mkdir()
    destinazione.mkdir()
    esigi_errore(
        lambda: files.rinomina_directory_nuova(sorgente, destinazione),
        "destinazione della rinomina esiste gia'",
    )
    esigi(sorgente.is_dir() and destinazione.is_dir(), "la rinomina rifiutata ha modificato le directory")


def prova_residui_restore() -> None:
    """Un restore interrotto lascia lo stato di prima accanto a tmp/, e va detto.

    I residui sono creati a mano con i nomi che `ripristina_snapshot` usa:
    `.<stato>-precedente-<hex>` per lo stato che c'era, `.<stato>-restore-<hex>`
    per la preparazione. Una directory con un altro suffisso e un link
    simbolico non lo sono: il primo perche' non e' roba del restore, il
    secondo perche' un link accanto allo stato non e' una copia dello stato.

    Lo snapshot pre-restore e' sintetico, con una data nel futuro, cosi' e'
    l'ultimo del catalogo qualunque cosa abbiano lasciato le prove prima.
    """
    esigi(residui_restore() == [], "residui segnalati senza che ce ne siano: " + repr(residui_restore()))
    esigi(avviso_residui_restore() == [], "avviso sui residui senza residui")

    stato = config.TMP_DIR.resolve()
    precedente = stato.with_name("." + stato.name + "-precedente-deadbeef")
    preparazione = stato.with_name("." + stato.name + "-restore-cafe")
    estraneo = stato.with_name("." + stato.name + "-altro")
    collegamento = stato.with_name("." + stato.name + "-precedente-link")
    sicurezza = None
    for percorso in (precedente, preparazione, estraneo):
        percorso.mkdir()
    (precedente / "kairos.db").write_bytes(b"stato di prima")
    try:
        if os.name == "posix":
            collegamento.symlink_to(precedente)
        esigi(residui_restore() == [precedente, preparazione], "residui non riconosciuti: " + repr(residui_restore()))

        righe = avviso_residui_restore()
        unite = "\n".join(righe)
        esigi("non e' stato completato" in righe[0], "l'avviso non dice cosa e' successo: " + righe[0])
        esigi(str(precedente) in unite and str(preparazione) in unite, "l'avviso non nomina i residui: " + unite)
        esigi("prima del restore" in unite and "mai installata" in unite, "l'avviso non distingue i residui")
        # Le prove precedenti possono aver lasciato un pre-restore vero nel
        # catalogo: l'avviso deve nominare quello, o dire che non ce n'e'.
        esistente = _ultimo_snapshot_di_tipo("pre-restore")
        if esistente is None:
            esigi("unica copia" in unite, "senza pre-restore l'avviso non dice che il residuo e' l'unica copia")
        else:
            esigi("restore " + esistente.name in unite, "l'avviso non nomina il pre-restore esistente: " + unite)
        esigi("Ares non tocca" in righe[-1], "l'avviso non dice che il residuo resta all'utente")

        manifest = manifest_minimo()
        manifest["snapshot_id"] = "20990101T000000Z-pre-restore"
        manifest["type"] = "pre-restore"
        manifest["created_at"] = "2099-01-01T00:00:00+00:00"
        sicurezza = snapshot_sintetico(config.BACKUP_DIR, manifest["snapshot_id"], manifest)
        righe = avviso_residui_restore()
        unite = "\n".join(righe)
        esigi("ares-backup restore " + sicurezza.name in unite, "l'avviso non nomina lo snapshot pre-restore: " + unite)
        esigi("unica copia" not in unite, "l'avviso dice 'unica copia' con uno snapshot pre-restore a disposizione")
        esigi(precedente.is_dir() and (precedente / "kairos.db").is_file(), "la lettura ha toccato un residuo")

        catturato = io.StringIO()
        with patch.object(sys, "argv", ["ares-backup", "list"]), redirect_stdout(catturato):
            esigi(snapshots.main() == 0, "list fallito con un residuo")
        testo = catturato.getvalue()
        esigi("non e' stato completato" in testo, "list non dichiara i residui: " + testo)
        esigi(testo.index(str(precedente)) < testo.index(sicurezza.name), "list non mette i residui prima del catalogo")

        # Solo la preparazione: lo stato non e' stato toccato e non c'e' niente
        # da ripristinare, quindi nessun comando di restore da suggerire.
        shutil.rmtree(precedente)
        if collegamento.is_symlink():
            collegamento.unlink()
        righe = avviso_residui_restore()
        unite = "\n".join(righe)
        esigi(residui_restore() == [preparazione], "la preparazione da sola non viene riconosciuta")
        esigi("mai installata" in unite, "la preparazione non viene descritta")
        esigi("restore " + sicurezza.name not in unite, "un restore suggerito senza motivo")
    finally:
        for percorso in (precedente, preparazione, estraneo):
            shutil.rmtree(percorso, ignore_errors=True)
        if collegamento.is_symlink():
            collegamento.unlink()
        if sicurezza is not None:
            shutil.rmtree(sicurezza, ignore_errors=True)
    esigi(avviso_residui_restore() == [], "avviso rimasto dopo la pulizia")


def _operazione_lancedb(azione: str) -> str:
    risultato = subprocess.run(
        [
            sys.executable,
            "-c",
            OPERAZIONE_LANCEDB,
            azione,
            config.LANCEDB_URI,
            str(config.EMBEDDER_DIMENSIONS),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return risultato.stdout.strip()


def crea_lancedb() -> None:
    _operazione_lancedb("create")


def aggiungi_lancedb() -> None:
    _operazione_lancedb("add")


def righe_lancedb() -> int:
    return int(json.loads(_operazione_lancedb("count")))


def main() -> int:
    avvio = time.monotonic()
    try:
        crea_sqlite(Path(config.DB_FILE), "profilo-originale")
        crea_sqlite(Path(config.FS_DB_FILE), "nota-originale")
        crea_lancedb()
        config.CRONOLOGIA_FILE.write_text("prima domanda\n", encoding="utf-8")
        ok("seme", "due SQLite, una riga LanceDB e una cronologia")

        primo = crea_snapshot()
        manifest = verifica_snapshot(primo, percorso_diretto=True)
        esigi(manifest["components"]["lancedb"]["tables"] == {"learned_knowledge": 1}, "conteggio LanceDB errato")
        esigi(manifest["components"][config.CRONOLOGIA_FILE.name] is True, "cronologia non nel manifest")
        esigi(
            (primo / config.CRONOLOGIA_FILE.name).read_text(encoding="utf-8") == "prima domanda\n",
            "cronologia non copiata nello snapshot",
        )
        for nome in ("kairos.db", "filesystem.db"):
            esigi(journal_mode(primo / nome) == "wal", "lo snapshot ha perso WAL per " + nome)
        if os.name == "posix":
            esigi((primo.stat().st_mode & 0o777) == 0o700, "directory snapshot non privata")
            esigi(
                all((f.stat().st_mode & 0o777) == 0o600 for f in primo.rglob("*") if f.is_file()),
                "file non privato",
            )
        ok("create + verify", primo.name)

        prova_errori_integrita()
        ok("integrita' ostile", "manifest, checksum, SQLite e metadati LanceDB rifiutati")

        prova_errori_sonda()
        ok("protocollo sonda", "exit code e JSON validati, ordine normalizzato")

        pubblicazione_staging = RADICE_PROVA / "pubblicazione-staging"
        pubblicazione_finale = RADICE_PROVA / "pubblicazione-finale"
        pubblicazione_staging.mkdir()
        (pubblicazione_staging / "dato.bin").write_bytes(b"completo")
        (pubblicazione_staging / "checksums.sha256").write_text("checksum\n", encoding="utf-8")
        (pubblicazione_staging / "manifest.json").write_text("{}\n", encoding="utf-8")
        with patch("ares.backup.snapshots._rinomina_directory", side_effect=PermissionError("rename negata")):
            _pubblica_snapshot(pubblicazione_staging, pubblicazione_finale)
        esigi(not pubblicazione_staging.exists(), "staging non eliminato dopo il fallback")
        esigi((pubblicazione_finale / "dato.bin").read_bytes() == b"completo", "dati fallback incompleti")
        esigi((pubblicazione_finale / "manifest.json").is_file(), "commit marker fallback mancante")
        shutil.rmtree(pubblicazione_finale)
        ok("pubblicazione Windows", "fallback completo con manifest pubblicato per ultimo")

        restore_staging = RADICE_PROVA / "restore-staging"
        restore_destinazione = RADICE_PROVA / "restore-destinazione"
        restore_precedente = RADICE_PROVA / "restore-precedente"
        restore_staging.mkdir()
        restore_destinazione.mkdir()
        (restore_staging / "stato.txt").write_text("nuovo\n", encoding="utf-8")
        (restore_destinazione / "stato.txt").write_text("vecchio\n", encoding="utf-8")
        identita_radice = restore_destinazione.stat().st_ino
        _installa_restore_per_copia(restore_staging, restore_destinazione, restore_precedente)
        esigi(
            (restore_destinazione / "stato.txt").read_text(encoding="utf-8") == "nuovo\n",
            "fallback restore non ha installato il nuovo stato",
        )
        esigi(restore_destinazione.stat().st_ino == identita_radice, "radice sostituita durante il restore")
        esigi(not restore_staging.exists() and not restore_precedente.exists(), "residui dopo fallback restore")
        shutil.rmtree(restore_destinazione)
        ok("restore Windows", "copia di rollback rimossa dopo l'installazione")

        prova_rollback_copia()
        ok("rollback Windows", "stato precedente recuperato; doppio guasto diagnosticabile")

        prova_rollback_rinomina()
        ok("rollback POSIX", "prima rinomina annullata dopo il guasto della seconda")

        prova_guardie_restore()
        ok("guardie restore", "staging fallito ripulito e destinazione esistente preservata")

        aggiungi_sqlite(Path(config.DB_FILE), "profilo-modificato")
        aggiungi_sqlite(Path(config.FS_DB_FILE), "nota-modificata")
        aggiungi_lancedb()
        config.CRONOLOGIA_FILE.write_text("prima domanda\nseconda domanda\n", encoding="utf-8")
        secondo = crea_snapshot()
        esigi(secondo != primo, "due snapshot hanno lo stesso identificativo")
        ok("secondo snapshot", secondo.name)

        sicurezza = ripristina_snapshot(primo.name)
        esigi(sicurezza is not None and sicurezza.is_dir(), "manca lo snapshot pre-restore")
        esigi(valori_sqlite(Path(config.DB_FILE)) == ["profilo-originale"], "kairos.db non ripristinato")
        esigi(valori_sqlite(Path(config.FS_DB_FILE)) == ["nota-originale"], "filesystem.db non ripristinato")
        esigi(righe_lancedb() == 1, "LanceDB non ripristinato")
        verifica_snapshot(sicurezza, percorso_diretto=True)
        # Il restore riporta indietro Ares, non chi gli parla: i database sono
        # quelli del primo snapshot, la cronologia e' rimasta quella viva.
        esigi(
            config.CRONOLOGIA_FILE.read_text(encoding="utf-8") == "prima domanda\nseconda domanda\n",
            "il restore ha riavvolto la cronologia: " + repr(config.CRONOLOGIA_FILE.read_text()),
        )
        ok("restore", "stato originale, cronologia viva conservata, pre-restore verificato")

        # tmp/ persa davvero: li' la cronologia dello snapshot e' l'unica che
        # esiste, ed e' il caso per cui un backup viene fatto.
        config.CRONOLOGIA_FILE.unlink()
        ripristina_snapshot(primo.name)
        esigi(config.CRONOLOGIA_FILE.is_file(), "cronologia non ripristinata da uno snapshot")
        esigi(
            config.CRONOLOGIA_FILE.read_text(encoding="utf-8") == "prima domanda\n",
            "cronologia ripristinata sbagliata: " + repr(config.CRONOLOGIA_FILE.read_text()),
        )
        ok("cronologia", "conservata se viva, ripristinata se persa")

        # I suffissi non sono cronologia: costruiamo due sole voci sintetiche
        # con nome e data in ordine opposto, senza alterare snapshot validi.
        vecchio = config.BACKUP_DIR / "zzzz-vecchio"
        nuovo = config.BACKUP_DIR / "aaaa-nuovo"
        try:
            vecchio.mkdir()
            nuovo.mkdir()
            (vecchio / "manifest.json").write_text(
                json.dumps({"created_at": "2099-08-21T20:00:00+00:00"}),
                encoding="utf-8",
            )
            (nuovo / "manifest.json").write_text(
                json.dumps({"created_at": "2099-08-21T20:00:01+00:00"}),
                encoding="utf-8",
            )
            esigi(elenco_snapshot()[-1].name == nuovo.name, "latest segue il nome invece della data")
        finally:
            shutil.rmtree(vecchio, ignore_errors=True)
            shutil.rmtree(nuovo, ignore_errors=True)
        ok("ordine snapshot", "data del manifest, non suffisso del nome")

        embedder_vero = config.EMBEDDER_MODEL
        config.EMBEDDER_MODEL = "embedder-incompatibile"
        try:
            verifica_snapshot(primo, percorso_diretto=True)
        except ErroreBackup as errore:
            esigi("embedder incompatibile" in str(errore), "errore embedder inatteso: " + str(errore))
        else:
            esigi(False, "un indice costruito con un altro embedder e' stato accettato")
        finally:
            config.EMBEDDER_MODEL = embedder_vero
        ok("compatibilita'", "embedder differente rifiutato")

        collegamento = config.BACKUP_DIR / "snapshot-symlink"
        symlink_disponibile = True
        try:
            try:
                collegamento.symlink_to(primo, target_is_directory=True)
            except OSError as errore:
                # Su Windows la creazione richiede un privilegio che non e'
                # garantito dai runner GitHub. La guardia resta coperta su
                # Ubuntu e viene provata anche su Windows quando disponibile.
                if os.name == "nt" and getattr(errore, "winerror", None) == 1314:
                    symlink_disponibile = False
                else:
                    raise
            if not symlink_disponibile:
                ok("symlink", "creazione non autorizzata dal runner Windows")
                collegamento = None
            else:
                try:
                    verifica_snapshot(collegamento, percorso_diretto=True)
                except ErroreBackup as errore:
                    esigi("link simbolico" in str(errore), "errore symlink inatteso: " + str(errore))
                else:
                    esigi(False, "una radice snapshot simbolica e' stata accettata")
        finally:
            if collegamento is not None:
                collegamento.unlink(missing_ok=True)
        if symlink_disponibile:
            ok("symlink", "radice snapshot simbolica rifiutata")

        corrotto = config.BACKUP_DIR / "99999999T999999Z-corrotto"
        shutil.copytree(primo, corrotto)
        database_corrotto = corrotto / "kairos.db"
        with database_corrotto.open("r+b") as file_corrotto:
            file_corrotto.seek(100)
            byte = file_corrotto.read(1)
            file_corrotto.seek(100)
            file_corrotto.write(bytes([(byte[0] if byte else 0) ^ 0xFF]))
        try:
            verifica_snapshot(corrotto, percorso_diretto=True)
        except ErroreBackup as errore:
            esigi("checksum errato" in str(errore), "la corruzione ha prodotto l'errore sbagliato: " + str(errore))
        else:
            esigi(False, "uno snapshot corrotto e' stato dichiarato valido")
        shutil.rmtree(corrotto)
        ok("corruzione", "checksum modificato rifiutato")

        with lock_stato(esclusivo=False):
            # Due chat sono lettori compatibili anche sul backend Windows;
            # solo il backup, che richiede lo scrittore esclusivo, si ferma.
            with lock_stato(esclusivo=False):
                pass
            try:
                crea_snapshot()
            except StatoOccupato:
                pass
            else:
                esigi(False, "il backup e' partito mentre la chat simulata teneva il lock")
        ok("lock", "backup fermato con Ares aperto")

        backup_vero = config.BACKUP_DIR
        config.BACKUP_DIR = config.TMP_DIR / "backup-vietato"
        try:
            valida_percorsi()
        except ErroreBackup:
            pass
        else:
            esigi(False, "un backup dentro lo stato e' stato accettato")
        finally:
            config.BACKUP_DIR = backup_vero
        ok("guardia percorsi", "backup dentro tmp/ respinto")

        prima = len(elenco_snapshot())
        eliminati = pota_snapshot(2)
        esigi(len(elenco_snapshot()) == min(prima, 2), "prune non ha conservato due snapshot")
        esigi(len(eliminati) == max(0, prima - 2), "prune ha eliminato il numero sbagliato")
        ok("prune", str(len(eliminati)) + " eliminati, 2 conservati")

        # Il promemoria: e' l'unica parte automatica di un backup che resta
        # manuale, quindi il controllo che conta e' quando tace.
        esigi(promemoria_backup(soglia_giorni=0) == [], "promemoria acceso con la soglia a zero")
        esigi(promemoria_backup(soglia_giorni=3650) == [], "promemoria acceso con uno snapshot di oggi")
        vecchio_backup = config.BACKUP_DIR
        config.BACKUP_DIR = RADICE_PROVA / "backup-mai-fatto"
        try:
            righe = promemoria_backup(soglia_giorni=7)
            esigi(righe, "nessun promemoria pur non essendoci mai stato uno snapshot")
            esigi("Nessuno snapshot" in righe[0], "il promemoria non dice che non ce n'e' nessuno")
            esigi("ares-backup create" in righe[-1], "il promemoria non dice come rimediare")
            # Una domanda non deve lasciare una directory: chiedere "ho un
            # backup?" e ottenere in cambio una cartella vuota e' esattamente
            # il tipo di effetto che questo progetto ha appena tolto altrove.
            esigi(
                not config.BACKUP_DIR.exists(),
                "il promemoria ha creato la directory dei backup: " + str(config.BACKUP_DIR),
            )
        finally:
            config.BACKUP_DIR = vecchio_backup

        # Invecchiati spostando indietro il manifest, non l'mtime: il
        # promemoria legge `created_at`, come l'ordinamento. E tutti quelli
        # rimasti, non solo l'ultimo: invecchiarne uno solo lo manda in fondo
        # all'elenco e il piu' recente resterebbe quello di oggi.
        rimasti = elenco_snapshot()
        # I byte originali, non il dizionario riserializzato: il manifest e'
        # un file dentro uno snapshot verificabile, e riscriverlo con un'altra
        # indentazione lo lascerebbe diverso da com'era.
        originali = {s: (s / "manifest.json").read_bytes() for s in rimasti}
        for snapshot, byte in originali.items():
            manifest = json.loads(byte.decode("utf-8"))
            antica = datetime.fromisoformat(manifest["created_at"]) - timedelta(days=30)
            manifest["created_at"] = antica.isoformat()
            (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        try:
            atteso = elenco_snapshot()[-1]
            righe = promemoria_backup(soglia_giorni=7)
            esigi(righe, "nessun promemoria con l'ultimo snapshot di 30 giorni fa")
            esigi("30 giorni fa" in righe[0], "il promemoria non dice quanti giorni: " + righe[0])
            esigi(atteso.name in righe[0], "il promemoria non nomina lo snapshot piu' recente")
            esigi(promemoria_backup(soglia_giorni=60) == [], "promemoria acceso sotto la propria soglia")
        finally:
            for snapshot, byte in originali.items():
                (snapshot / "manifest.json").write_bytes(byte)
        for snapshot in rimasti:
            esigi(verifica_snapshot(snapshot.name)["snapshot_id"] == snapshot.name, "snapshot alterato dalla prova")
        ok("promemoria", "tace se recente o spento, avvisa se manca o e' vecchio, non crea niente")

        prova_residui_restore()
        ok("residui restore", "tace senza residui, li nomina senza toccarli, list li dice prima del catalogo")

    except Exception as errore:
        print("FALLITO ", type(errore).__name__ + ":", errore)
        print("Dati della prova conservati:", RADICE_PROVA)
        return 1

    shutil.rmtree(RADICE_PROVA)
    print()
    print("Concluso in", round(time.monotonic() - avvio, 2), "s")
    print("Nessun fallimento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
