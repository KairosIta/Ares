"""Runner unico delle prove
========================
Uso:
    .venv/bin/python tests/run.py                 le prove offline
    .venv/bin/python tests/run.py --tutte         anche quelle con Ollama
    .venv/bin/python tests/run.py --copertura     offline, con la misura
    .venv/bin/python tests/run.py --solo backup entita

Ogni prova e' uno script eseguibile da solo, e il runner la lancia in un
processo separato invece di importarla. Ognuna prepara `ARES_HOME`,
`ARES_TMP`, `ARES_BACKUP_DIR` e la propria cartella di lavoro *prima* di importare
`config`, che fotografa l'ambiente all'import: due prove nello stesso
interprete condividerebbero la prima fotografia, e la seconda potrebbe
scrivere nell'archivio vero. Per questo `prepara_ambiente` rifiuta di
partire se `ares.config` e' gia' in memoria.

La copertura gira in modalita' parallela (un file per processo, uniti da
`coverage combine`), configurata in `pyproject.toml`.

Le prove girano una alla volta: quelle con Ollama si contendono la GPU, e
le offline durano poco piu' di un minuto su Linux (su Windows `cli` da sola
ne prende quasi tre).
"""

import argparse
import io
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
    ("smoke", "smoke_test.py", False, "assemblaggio, store, lock e apprendimento simulato"),
    ("repl", "repl_test.py", False, "conferme, rendering, editor e comandi, senza l'agente"),
    ("nucleo", "core_test.py", False, "sessioni, modalita' e autorizzazioni usate da un client senza terminale"),
    ("sessioni", "session_retention_test.py", False, "offload, retention, cascata e restore"),
    ("contratto", "agno_contract_test.py", False, "estrazione, conferma, retry del contesto e limiti dichiarati"),
    ("ambiti", "scoping_test.py", False, "provenienza delle memorie e filtro per namespace delle intuizioni"),
    ("costo", "learning_cost_test.py", False, "quante inferenze costa un turno, e quanto pesano"),
    ("radicamento", "radicamento_test.py", False, "l'estrazione salva solo cio' che ha un appiglio nel testo"),
    ("backup", "backup_test.py", False, "snapshot, checksum, restore, prune"),
    ("entita", "entity_maintenance_test.py", False, "audit e fusione delle entita'"),
    ("consolidamento", "consolidamento_test.py", False, "memorie doppie o superate ritirate, con backup e ripristino"),
    ("sandbox", "sandbox_test.py", False, "comandi dentro bwrap: rifiuti all'avvio e isolamento vero su Linux"),
    ("skill", "skill_test.py", False, "skill: solo metadati nel prompt, lettura confinata, proposte e adozione"),
    ("cli", "cli_test.py", False, "preflight, ispezione, backup e REPL a riga di comando"),
    ("valutazione", "memory_quality_test.py", False, "verdetti, prove, isolamento e guasti del benchmark"),
    ("conversazione", "conversazione_eval_test.py", False, "controlli dell'eval sugli strumenti in conversazione"),
    ("latenza", "latenza_eval_test.py", False, "le misure dell'eval della latenza su metriche scritte a mano"),
    ("rilascio", "rilascio_test.py", False, "versione concorde fra lock, CHANGELOG e SECURITY; link fra documenti"),
    ("affidabilita", "learning_reliability_test.py", True, "retry dell'estrazione del contesto"),
    ("intuizioni", "learned_knowledge_test.py", True, "salvataggio e riuso delle intuizioni"),
    ("e2e", "e2e_test.py", True, "un turno completo e la rilettura da un altro processo"),
)

# Un tetto per distinguere una prova bloccata da una lenta. `cli` su Windows in
# CI sta fra 150 e 165 s: il tetto offline e' circa il doppio. Quelle con
# Ollama hanno margine per caricamento e inferenza.
TIMEOUT_OFFLINE_SECONDI = 360
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

    Un nome inesistente non viene ignorato: zero prove eseguite con esito verde
    sarebbe peggio di un errore.
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
    con `--solo` gonfierebbe il rapporto. I nomi sono elencati invece di un
    glob largo, per non cancellare file di configurazione.
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

    Le prove lanciano sottoprocessi (la CLI di `ares.entities`, il sondaggio
    LanceDB, la rilettura di `e2e_test.py`): senza queste variabili quel codice
    risulterebbe scoperto.

    - `COVERAGE_PROCESS_START`: la configurazione per `coverage.process_startup()`.
    - `PYTHONPATH`: rende visibile `sitecustomize.py`, che chiama quella funzione.
    - `COVERAGE_FILE`: percorso assoluto, perche' un figlio lanciato da una
      cartella usa-e-getta non lasci i dati dove `combine` non guarda.
    """
    aggiunta = str(RADICE / "tests" / "_copertura")
    esistente = os.environ.get("PYTHONPATH", "")
    return {
        "COVERAGE_PROCESS_START": str(RADICE / "pyproject.toml"),
        "COVERAGE_FILE": str(RADICE / ".coverage"),
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

    L'output passa a schermo mentre arriva: le prove con Ollama durano minuti
    e stampano l'avanzamento.
    """
    percorso = Path("tests") / prova[1]
    comando = [sys.executable]
    ambiente = dict(os.environ)
    if copertura:
        # `-m coverage run` invece di un wrapper: si misura lo stesso script
        # con lo stesso argv.
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
    # I figli scrivono sul terminale direttamente: senza flush le intestazioni
    # del padre, in un file o in CI, comparirebbero staccate dalla prova.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)

    args = costruisci_parser().parse_args(argomenti)
    prove = selezione(args)

    copertura = args.copertura
    if copertura and not coverage_disponibile():
        print("coverage non e' installato in questo interprete.")
        print("Installalo con: uv sync --locked")
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
        # Senza `fail_under` un codice diverso da zero qui significa che la
        # misura manca (nessun dato, configurazione illeggibile): non deve
        # finire in "Nessun fallimento".
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
            # Misurato il 3 ottobre 2026: `--tutte --copertura` copre quattro
            # righe in piu', la risposta di Ollama non in streaming
            # (`OllamaConRagionamento._parse_provider_response`). Le prove con
            # Ollama verificano cio' che un modello finto non puo' dire, non
            # allargano il perimetro.
            print()
            print("Misura delle sole prove offline: --tutte aggiunge solo la risposta di Ollama non in streaming.")

    if falliti:
        print()
        print("FALLITE:", ", ".join(falliti))
        return 1
    print("Nessun fallimento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
