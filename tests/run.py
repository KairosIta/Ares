"""
Runner unico delle prove
========================
Uso:
    .venv/bin/python tests/run.py                 le prove offline
    .venv/bin/python tests/run.py --tutte         anche quelle con Ollama
    .venv/bin/python tests/run.py --copertura     offline, con la misura
    .venv/bin/python tests/run.py --solo backup entita

Ogni prova resta uno script eseguibile da solo: questo file non le importa,
le lancia. Non e' una preferenza di stile. Ognuna prepara il proprio ambiente
scrivendo `ARES_TMP`, `ARES_BACKUP_DIR` e `ARES_WORKSPACE` *prima* di
importare `config`, che quelle variabili le legge una volta sola all'import e
non le rilegge mai piu'. Due prove nello stesso interprete condividerebbero
il primo `config` importato, cioe' i percorsi della prima: la seconda
scriverebbe dove ha preparato la prima, e il giorno in cui una delle due
sbagliasse variabile scriverebbe nell'archivio vero senza che nessuno se ne
accorga. Un processo per prova rende quell'errore impossibile invece che
improbabile.

Per lo stesso motivo la misura di copertura gira in modalita' parallela: un
file per processo, uniti da `coverage combine` alla fine. La configurazione
sta in `.coveragerc`, cosi' il numero non dipende da come si e' invocato il
comando.

Le prove girano una alla volta. Quelle con Ollama si contendono la stessa
GPU, e le offline durano in tutto meno di un minuto: parallelizzarle
comprerebbe poco al prezzo di un output intrecciato.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent

# Nome breve, file, se serve Ollama, cosa dimostra. L'ordine e' quello in cui
# conviene leggere un fallimento: se il cablaggio e' rotto, il resto e'
# rumore.
PROVE = (
    ("smoke", "smoke_test.py", False, "assemblaggio, store, lock, REPL simulata"),
    ("sessioni", "session_retention_test.py", False, "offload, retention, cascata e restore"),
    ("backup", "backup_test.py", False, "snapshot, checksum, restore, prune"),
    ("entita", "entity_maintenance_test.py", False, "audit e fusione delle entita'"),
    ("cli", "cli_test.py", False, "preflight, ispezione, backup e REPL a riga di comando"),
    ("affidabilita", "learning_reliability_test.py", True, "retry dell'estrazione del contesto"),
    ("intuizioni", "learned_knowledge_test.py", True, "salvataggio e riuso delle intuizioni"),
    ("e2e", "e2e_test.py", True, "un turno completo e la rilettura da un altro processo"),
)

# Un test bloccato e' diverso da un test lento: senza un limite il runner non
# arriva mai al riepilogo e in CI consuma l'intero timeout del job. Le prove
# offline normalmente finiscono in secondi; quelle con Ollama hanno piu'
# margine per caricamento del modello, inferenza e GPU meno veloci.
TIMEOUT_OFFLINE_SECONDI = 180
TIMEOUT_OLLAMA_SECONDI = 900


def costruisci_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Esegue le prove di Ares, ciascuna nel proprio processo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Prove disponibili:\n"
        + "\n".join("    " + n.ljust(14) + ("[Ollama] " if o else "         ") + d for n, _, o, d in PROVE),
    )
    parser.add_argument(
        "--tutte",
        action="store_true",
        help="include le prove che richiedono Ollama e i modelli scaricati",
    )
    parser.add_argument(
        "--solo",
        nargs="+",
        metavar="NOME",
        help="esegue solo le prove nominate, anche se richiedono Ollama",
    )
    parser.add_argument(
        "--copertura",
        action="store_true",
        help="misura la copertura dei moduli e stampa il rapporto",
    )
    parser.add_argument(
        "--html",
        action="store_true",
        help="con --copertura, scrive anche il rapporto navigabile in htmlcov/",
    )
    return parser


def selezione(args: argparse.Namespace) -> list[tuple[str, str, bool, str]]:
    """Le prove da eseguire, o un errore che nomina quelle sbagliate.

    Un nome inesistente non viene ignorato: `--solo backupp` che esegue zero
    prove e stampa "nessun fallimento" e' peggio di un errore, perche' e'
    verde.
    """
    if args.solo:
        per_nome = {nome: prova for prova in PROVE for nome in [prova[0]]}
        ignoti = [n for n in args.solo if n not in per_nome]
        if ignoti:
            disponibili = ", ".join(p[0] for p in PROVE)
            raise SystemExit("prove inesistenti: " + ", ".join(ignoti) + "\nDisponibili: " + disponibili)
        return [per_nome[n] for n in args.solo]
    return [p for p in PROVE if args.tutte or not p[2]]


def pulisci_dati_copertura() -> None:
    """Toglie di mezzo le misure precedenti.

    `coverage combine` unisce tutto cio' che trova: un file rimasto da un giro
    con `--solo` gonfierebbe in silenzio il rapporto di questo.

    I nomi sono elencati e non presi con un glob `.coverage*`, che raccoglie
    anche `.coveragerc`. Cancellarlo non rompe niente in modo visibile: la
    misura prosegue con le impostazioni predefinite, senza modalita'
    parallela, e i processi si sovrascrivono a vicenda lasciando il rapporto
    dell'ultima prova al posto di quello di tutte.
    """
    dati = RADICE / ".coverage"
    if dati.exists():
        dati.unlink()
    for residuo in RADICE.glob(".coverage.*"):
        residuo.unlink()
    htmlcov = RADICE / "htmlcov"
    if htmlcov.is_dir():
        shutil.rmtree(htmlcov)


def ambiente_figli() -> dict[str, str]:
    """Variabili che estendono la misura ai processi avviati dalle prove.

    Le prove non sono foglie: la CLI di `entity_maintenance.py` viene lanciata
    sei volte come sottoprocesso, `backup_probe.py` sonda LanceDB in un interprete
    isolato, `e2e_test.py` rilegge l'archivio da un processo nuovo. Quel codice
    e' provato, e senza queste due variabili risulta scoperto: un rapporto che
    sbaglia in difetto manda a scrivere prove dove ce ne sono gia'.

    `COVERAGE_PROCESS_START` dice a `coverage.process_startup()` quale
    configurazione usare; `PYTHONPATH` mette a portata il `sitecustomize.py`
    che quella funzione la chiama. Le prove copiano `os.environ` quando
    lanciano un figlio, quindi la coppia si propaga anche ai nipoti.
    """
    aggiunta = str(RADICE / "tests" / "_copertura")
    esistente = os.environ.get("PYTHONPATH", "")
    return {
        "COVERAGE_PROCESS_START": str(RADICE / ".coveragerc"),
        "PYTHONPATH": aggiunta + os.pathsep + esistente if esistente else aggiunta,
    }


def coverage_disponibile() -> bool:
    return (
        subprocess.run(
            [sys.executable, "-c", "import coverage"],
            cwd=RADICE,
            capture_output=True,
        ).returncode
        == 0
    )


def esegui(prova: tuple[str, str, bool, str], copertura: bool) -> tuple[int, float]:
    """Lancia una prova e restituisce esito e durata.

    L'output non viene catturato: passa a schermo mentre arriva. Le prove con
    Ollama durano minuti e stampano avanzamento; raccoglierlo per mostrarlo
    alla fine trasformerebbe un'attesa informata in un silenzio.
    """
    percorso = Path("tests") / prova[1]
    comando = [sys.executable]
    ambiente = dict(os.environ)
    if copertura:
        # `-m coverage run` invece di un wrapper: la prova resta lo stesso
        # script con lo stesso argv, e cio' che si misura e' quello che gira
        # anche senza misura.
        comando += ["-m", "coverage", "run"]
        ambiente.update(ambiente_figli())
    comando.append(str(percorso))
    avvio = time.monotonic()
    timeout = TIMEOUT_OLLAMA_SECONDI if prova[2] else TIMEOUT_OFFLINE_SECONDI
    try:
        esito = subprocess.run(comando, cwd=RADICE, env=ambiente, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        print(f"TIMEOUT: {prova[0]} non e' terminata entro {timeout} secondi.")
        esito = 124
    return esito, time.monotonic() - avvio


def main(argomenti: list[str] | None = None) -> int:
    # I processi figli scrivono sul terminale senza passare di qui. Quando
    # l'output e' un file o un log di CI il buffer del padre trattiene le
    # proprie righe fino alla fine, e le intestazioni compaiono staccate dalla
    # prova che annunciano - o dopo il rapporto che dovrebbero precedere.
    sys.stdout.reconfigure(line_buffering=True)

    args = costruisci_parser().parse_args(argomenti)
    prove = selezione(args)

    copertura = args.copertura
    if copertura and not coverage_disponibile():
        print("coverage non e' installato in questo interprete.")
        print("Installalo con: uv pip sync --python .venv/bin/python requirements.txt requirements-dev.txt")
        return 1
    if args.html and not copertura:
        print("--html richiede --copertura.")
        return 1
    if copertura:
        pulisci_dati_copertura()

    esiti: list[tuple[str, int, float]] = []
    avvio = time.monotonic()
    for prova in prove:
        print()
        print("=" * 72)
        print(prova[0].upper(), "-", prova[3])
        print("=" * 72)
        esito, durata = esegui(prova, copertura)
        esiti.append((prova[0], esito, durata))

    print()
    print("=" * 72)
    print("RIEPILOGO")
    print("=" * 72)
    for nome, esito, durata in esiti:
        stato = "ok      " if esito == 0 else "FALLITA "
        print(stato, nome.ljust(14), format(durata, "6.1f"), "s")
    falliti = [nome for nome, esito, _ in esiti if esito != 0]
    print()
    print(len(esiti), "prove in", round(time.monotonic() - avvio, 1), "s")

    if copertura:
        print()
        # Gli esiti si guardano. Senza `fail_under` in `.coveragerc` un codice
        # diverso da zero qui significa che la misura non c'e' - nessun dato
        # raccolto, configurazione illeggibile - e una misura mancante che
        # stampa "Nessun fallimento" e' il modo esatto in cui questo runner ha
        # gia' mentito una volta.
        guasti = []
        for passo in ("combine", "report"):
            if subprocess.run([sys.executable, "-m", "coverage", passo], cwd=RADICE).returncode != 0:
                guasti.append(passo)
        if args.html:
            if subprocess.run([sys.executable, "-m", "coverage", "html"], cwd=RADICE).returncode != 0:
                guasti.append("html")
            else:
                print("Rapporto navigabile:", RADICE / "htmlcov" / "index.html")
        if guasti:
            print()
            print("La misura di copertura e' fallita:", ", ".join(guasti))
            falliti.append("copertura")
        if not args.tutte and not args.solo:
            # Una percentuale senza il suo perimetro e' un numero che invita
            # a inseguirlo: meta' di cio' che resta scoperto sta nei percorsi
            # che solo le prove con Ollama attraversano.
            print()
            print("Misura delle sole prove offline: --tutte copre anche i percorsi con il modello.")

    if falliti:
        print()
        print("FALLITE:", ", ".join(falliti))
        return 1
    print("Nessun fallimento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
