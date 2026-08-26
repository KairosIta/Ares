"""
Prova completa di backup e restore, senza Ollama
================================================

Crea due SQLite e una tabella LanceDB in una directory temporanea, li salva,
li modifica e ripristina lo snapshot. Prova anche checksum, lock, guardie sui
percorsi e pruning. Non legge ne' scrive tmp/ o ares-backup reali.
"""

import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path


# Le prove stanno in tests/, i moduli del progetto in radice: lanciata come
# script, `sys.path[0]` e' tests/ e `import config` non troverebbe niente.
# Va prima di qualunque import del progetto.
RADICE_PROGETTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE_PROGETTO))

RADICE_PROVA = Path(tempfile.mkdtemp(prefix="ares-backup-test-"))
os.environ["ARES_TMP"] = str(RADICE_PROVA / "stato")
os.environ["ARES_BACKUP_DIR"] = str(RADICE_PROVA / "backup")
os.environ["ARES_WORKSPACE"] = str(RADICE_PROVA / "lavoro")

import config  # noqa: E402
from backup import (  # noqa: E402
    ErroreBackup,
    crea_snapshot,
    elenco_snapshot,
    pota_snapshot,
    ripristina_snapshot,
    valida_percorsi,
    verifica_snapshot,
)
from state_lock import StatoOccupato, lock_stato  # noqa: E402


def esigi(condizione: bool, messaggio: str) -> None:
    if not condizione:
        raise AssertionError(messaggio)


def ok(nome: str, nota: str) -> None:
    print("ok      ", nome.ljust(24), "-", nota)


def crea_sqlite(percorso: Path, valore: str) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(percorso) as connessione:
        connessione.execute("create table prova (valore text not null)")
        connessione.execute("insert into prova values (?)", (valore,))


def aggiungi_sqlite(percorso: Path, valore: str) -> None:
    with sqlite3.connect(percorso) as connessione:
        connessione.execute("insert into prova values (?)", (valore,))


def valori_sqlite(percorso: Path) -> list[str]:
    with sqlite3.connect(percorso) as connessione:
        return [riga[0] for riga in connessione.execute("select valore from prova order by rowid")]


def crea_lancedb() -> None:
    import lancedb

    async def crea() -> None:
        with await lancedb.connect_async(config.LANCEDB_URI) as db:
            with await db.create_table(
                "learned_knowledge",
                data=[
                    {
                        "id": "prima",
                        "testo": "prima intuizione",
                        "vector": [0.0] * config.EMBEDDER_DIMENSIONS,
                    }
                ],
            ):
                pass

    asyncio.run(crea())


def aggiungi_lancedb() -> None:
    import lancedb

    async def aggiungi() -> None:
        with await lancedb.connect_async(config.LANCEDB_URI) as db:
            with await db.open_table("learned_knowledge") as tabella:
                await tabella.add(
                    [
                        {
                            "id": "seconda",
                            "testo": "seconda intuizione",
                            "vector": [1.0] * config.EMBEDDER_DIMENSIONS,
                        }
                    ]
                )

    asyncio.run(aggiungi())


def righe_lancedb() -> int:
    import lancedb

    async def conta() -> int:
        with await lancedb.connect_async(config.LANCEDB_URI) as db:
            with await db.open_table("learned_knowledge") as tabella:
                return int(await tabella.count_rows())

    return asyncio.run(conta())


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
        if os.name == "posix":
            esigi((primo.stat().st_mode & 0o777) == 0o700, "directory snapshot non privata")
            esigi(
                all((f.stat().st_mode & 0o777) == 0o600 for f in primo.rglob("*") if f.is_file()),
                "file non privato",
            )
        ok("create + verify", primo.name)

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
            esigi(elenco_snapshot()[-1] == nuovo, "latest segue il nome invece della data")
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
