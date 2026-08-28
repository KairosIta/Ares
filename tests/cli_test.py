"""
Prova dei comandi a riga di comando, senza Ollama
=================================================
Uso:
    .venv/bin/python tests/cli_test.py

I moduli di Ares erano provati; i comandi con cui si usano, no. La misura di
copertura lo diceva senza ambiguita': `preflight.py` e `inspect_learning.py`
allo 0%, il `main()` di `backup.py` all'1%. Sono le righe che un utente
attraversa per prime - il preflight e' letteralmente il primo comando che si
esegue su un clone nuovo - ed erano le uniche mai eseguite da nessuno tranne
che a mano.

Cosa viene affermato: che ogni comando termini con il codice di uscita giusto
e stampi cio' su cui l'utente decide il passo dopo. Non che il testo sia
formulato bene: quello si legge, non si prova.

Niente modello e niente rete verso l'esterno. Il preflight interroga un
server HTTP finto in ascolto su localhost, che risponde con l'elenco di
modelli deciso dalla prova: e' l'unico modo di provare i tre esiti - pronto,
modello mancante, server spento - senza dipendere da cosa c'e' scaricato
sulla macchina che esegue la prova.
"""

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

# Le prove stanno in tests/, i moduli del progetto in radice: lanciata come
# script, `sys.path[0]` e' tests/ e `import config` non troverebbe niente.
# Va prima di qualunque import del progetto.
RADICE_PROGETTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE_PROGETTO))

# I percorsi vanno scelti prima di importare config, che crea TMP_DIR
# all'import: importarlo e correggere dopo lascerebbe comunque una tmp/ vuota
# accanto ai dati veri.
RADICE_PROVA = Path(tempfile.mkdtemp(prefix="ares-cli-test-"))
os.environ["ARES_TMP"] = str(RADICE_PROVA / "stato")
os.environ["ARES_BACKUP_DIR"] = str(RADICE_PROVA / "backup")
os.environ["ARES_WORKSPACE"] = str(RADICE_PROVA / "lavoro")

import backup  # noqa: E402
import config  # noqa: E402
import inspect_learning  # noqa: E402
import preflight  # noqa: E402
from assistant import build_assistant, build_filesystem  # noqa: E402

UTENTE = "prova-cli"
SESSIONE = "cli"

# Nessuna di queste prove deve accendere un modello, e su una macchina di
# sviluppo Ollama e' spesso acceso: senza questa riga la prova passerebbe qui
# usandolo di nascosto e fallirebbe in CI, dove non c'e'. Il porto e' chiuso di
# proposito, cosi' un tentativo di embedding si vede subito invece di
# funzionare.
config.OLLAMA_HOST = "http://127.0.0.1:1"


def esigi(condizione: object, messaggio: str) -> None:
    if not condizione:
        raise AssertionError(messaggio)


def ok(nome: str, nota: str) -> None:
    print("ok      ", nome.ljust(20), "-", nota)


# ---------------------------------------------------------------------------
# Server Ollama finto
# ---------------------------------------------------------------------------


class OllamaFinto(BaseHTTPRequestHandler):
    """Risponde a /api/tags con l'elenco deciso dalla prova.

    L'elenco vive sulla classe e non sull'istanza perche' HTTPServer costruisce
    un handler nuovo per ogni richiesta.
    """

    modelli: ClassVar[list[str]] = []

    # Il nome in CamelCase non e' una scelta: BaseHTTPRequestHandler cerca
    # `do_` piu' il metodo HTTP.
    def do_GET(self) -> None:
        if not self.path.startswith("/api/tags"):
            self.send_response(404)
            self.end_headers()
            return
        corpo = json.dumps({"models": [{"name": nome} for nome in type(self).modelli]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *_argomenti: object) -> None:
        """Silenzio: il log di default sporca l'output della prova."""


def porta_libera() -> int:
    """Una porta che il sistema dichiara libera adesso.

    Chiedere al sistema invece di fissarne una: una porta scelta a mano e'
    occupata prima o poi, e il fallimento che ne segue sembra un difetto del
    preflight.
    """
    with socket.socket() as presa:
        presa.bind(("127.0.0.1", 0))
        return int(presa.getsockname()[1])


def esegui_preflight(modelli: list[str] | None) -> tuple[int, str]:
    """Lancia il preflight contro un server finto, o contro nessun server.

    Con `modelli=None` non avvia niente e punta a una porta chiusa: e' il caso
    "Ollama non gira", che vale la pena provare quanto gli altri due perche'
    e' quello in cui l'utente si trova per primo.
    """
    porta = porta_libera()
    host = "http://127.0.0.1:" + str(porta)
    servitore = None
    if modelli is not None:
        OllamaFinto.modelli = modelli
        servitore = HTTPServer(("127.0.0.1", porta), OllamaFinto)
        threading.Thread(target=servitore.serve_forever, daemon=True).start()
    try:
        uscita = io.StringIO()
        with patch.object(config, "OLLAMA_HOST", host), redirect_stdout(uscita):
            esito = preflight.main()
        return esito, uscita.getvalue()
    finally:
        if servitore is not None:
            servitore.shutdown()
            servitore.server_close()


# ---------------------------------------------------------------------------
# Le prove
# ---------------------------------------------------------------------------


def preflight_pronto() -> str:
    # Il server annuncia i nomi come li scrive Ollama: con il tag esplicito
    # dove c'e', con `:latest` aggiunto dove config.py non ne scrive uno. E'
    # il falso negativo che `stessa_etichetta` esiste per evitare, e la
    # differenza fra i due modelli lo mette alla prova in entrambi i versi.
    richiesti = {config.MAIN_MODEL, config.LEARNING_MODEL, config.EMBEDDER_MODEL}
    modelli = [nome if ":" in nome else nome + ":latest" for nome in richiesti]
    esigi(
        any(":" not in nome for nome in richiesti),
        "nessun modello e' senza tag: la prova non sta piu' verificando la normalizzazione",
    )
    esito, testo = esegui_preflight(modelli)
    esigi(esito == 0, "preflight con tutti i modelli presenti non e' uscito con 0: " + testo)
    esigi("Ambiente pronto" in testo, "il preflight non dichiara l'ambiente pronto")
    esigi("MANCANTE" not in testo, "il preflight segnala un modello mancante che c'e'")
    # I ruoli si accumulano: main e learning sono lo stesso modello e vanno
    # mostrati su una riga sola.
    if config.MAIN_MODEL == config.LEARNING_MODEL:
        esigi(" + " in testo, "i due ruoli dello stesso modello non sono stati uniti")
    return "tag :latest riconosciuto, ruoli accumulati"


def preflight_modello_mancante() -> str:
    # Presente il modello di conversazione, assente l'embedder: e' il caso
    # tipico, perche' l'embedder si scarica dopo e non serve al primo turno.
    esito, testo = esegui_preflight([config.MAIN_MODEL])
    esigi(esito == 1, "un modello mancante non ha prodotto uscita 1")
    esigi("MANCANTE" in testo, "il modello mancante non e' segnalato")
    esigi("ollama pull" in testo, "manca il comando per scaricare il modello")
    esigi("Ambiente pronto" not in testo, "l'ambiente e' dichiarato pronto senza l'embedder")
    return "segnalato con il comando per rimediare"


def preflight_server_spento() -> str:
    esito, testo = esegui_preflight(None)
    esigi(esito == 1, "un server irraggiungibile non ha prodotto uscita 1")
    esigi("non raggiungibile" in testo, "il server spento non e' distinto")
    esigi("ollama serve" in testo, "manca il comando per avviare il server")
    return "distinto da un modello mancante"


def backup_cli(_archivio: Path) -> str:
    """Il `main()` di backup.py, sottocomando per sottocomando.

    Le funzioni sotto sono gia' provate da `backup_test.py`. Qui si prova lo
    strato che le sceglie: l'analisi degli argomenti, le conferme testuali e i
    codici di uscita, che sono cio' su cui uno script chiamante decide.
    """

    def comando(*argomenti: str, risposta: str | None = None) -> tuple[int, str]:
        uscita = io.StringIO()
        with ExitStack() as pila:
            pila.enter_context(patch.object(sys, "argv", ["backup.py", *argomenti]))
            pila.enter_context(redirect_stdout(uscita))
            if risposta is not None:
                # `input` viene sostituito solo dove la conferma serve: nei
                # comandi che non la chiedono, una risposta pronta
                # nasconderebbe una richiesta comparsa per errore.
                pila.enter_context(patch("builtins.input", lambda _prompt="": risposta))
            esito = backup.main()
        return esito, uscita.getvalue()

    esito, testo = comando("create")
    esigi(esito == 0, "create non riuscito: " + testo)
    esigi("Snapshot creato e verificato" in testo, "create non nomina lo snapshot")
    primo = backup.elenco_snapshot()[-1].name

    esito, testo = comando("list")
    esigi(esito == 0, "list non riuscito: " + testo)
    esigi(primo in testo, "list non elenca lo snapshot appena creato")

    esito, testo = comando("verify", "latest")
    esigi(esito == 0, "verify latest non riuscito: " + testo)
    esigi("Snapshot valido" in testo, "verify non conferma la validita'")

    esito, testo = comando("verify", "non-esiste")
    esigi(esito == 1, "verify di uno snapshot inesistente non e' uscito con 1")
    esigi("ERRORE:" in testo, "verify non spiega perche' ha rifiutato")

    # La conferma sbagliata non e' un errore: e' un annullamento, e ha un
    # codice suo perche' uno script deve poterlo distinguere da un guasto.
    esito, testo = comando("restore", primo, risposta="qualcos-altro")
    esigi(esito == 2, "un restore annullato non e' uscito con 2, ma con " + str(esito))
    esigi("Restore annullato" in testo, "il restore annullato non lo dice")

    esito, testo = comando("restore", primo, "--yes")
    esigi(esito == 0, "restore non riuscito: " + testo)
    esigi("Restore completato" in testo, "restore non conferma")
    esigi("Stato precedente salvato in" in testo, "il restore non nomina lo snapshot di sicurezza")

    esito, testo = comando("prune", "--keep", "0", "--yes")
    esigi(esito == 1, "prune con --keep 0 non e' stato rifiutato")
    esigi("almeno 1" in testo, "prune non spiega il rifiuto")

    esito, testo = comando("prune", "--keep", "99")
    esigi(esito == 0, "prune senza candidati non e' uscito con 0")
    esigi("Niente da eliminare" in testo, "prune non dice che non c'e' niente da fare")

    esito, testo = comando("prune", "--keep", "1", risposta="no")
    esigi(esito == 2, "un prune annullato non e' uscito con 2")
    esigi("Prune annullato" in testo, "il prune annullato non lo dice")

    prima = len(backup.elenco_snapshot())
    esito, testo = comando("prune", "--keep", "1", "--yes")
    esigi(esito == 0, "prune non riuscito: " + testo)
    esigi(len(backup.elenco_snapshot()) == 1, "prune non ha conservato esattamente uno snapshot")
    esigi("Eliminati " + str(prima - 1) in testo, "prune non riporta quanti ne ha eliminati")
    return "create, list, verify, restore, prune con annullamenti e codici distinti"


def inspect_learning_cli() -> str:
    """L'ispezione degli archivi, che non deve scrivere niente.

    Prende il lock condiviso e legge cinque store. Il controllo che conta non
    e' che stampi: e' che l'archivio sia identico prima e dopo, perche' questo
    comando esiste per guardare senza toccare.
    """
    file_db = Path(config.DB_FILE)
    prima = file_db.stat().st_mtime_ns, file_db.stat().st_size

    uscita = io.StringIO()
    argv = ["inspect_learning.py", "--user", UTENTE, "--session", SESSIONE]
    with patch.object(sys, "argv", argv), redirect_stdout(uscita):
        inspect_learning.main()
    testo = uscita.getvalue()

    for sezione in ("PROFILO UTENTE", "MEMORIE", "CONTESTO DI SESSIONE", "ENTITA'", "FILE DELL'AGENTE"):
        esigi(sezione in testo, "sezione assente dall'ispezione: " + sezione)

    dopo = file_db.stat().st_mtime_ns, file_db.stat().st_size
    esigi(prima == dopo, "l'ispezione ha modificato l'archivio")

    # Il ramo --file esce prima di costruire l'agente: e' l'unica lettura che
    # non accende nemmeno gli store.
    uscita = io.StringIO()
    argv = ["inspect_learning.py", "--user", UTENTE, "--file", "non/esiste.md"]
    with patch.object(sys, "argv", argv), redirect_stdout(uscita):
        inspect_learning.main()
    esigi("Nessun file a questo percorso" in uscita.getvalue(), "un file assente non viene segnalato")

    percorso, contenuto = "note/appunto.md", "riga di prova"
    build_filesystem(UTENTE).write(percorso, contenuto)
    uscita = io.StringIO()
    argv = ["inspect_learning.py", "--user", UTENTE, "--file", percorso]
    with patch.object(sys, "argv", argv), redirect_stdout(uscita):
        inspect_learning.main()
    esigi(contenuto in uscita.getvalue(), "il contenuto del file non viene stampato")
    return "cinque sezioni, archivio invariato, --file presente e assente"


def chat_repl() -> str:
    """La REPL intera in un processo separato, con stdin da una pipe.

    Senza terminale `CliInput` ripiega su `input()`, quindi la conversazione si
    puo' scrivere in anticipo. Il giro che si prova e' quello che nessuna prova
    attraversava: banner, ciclo, dispatch dei comandi, uscita pulita.

    Il figlio non eredita `config.OLLAMA_HOST` chiuso di questa prova - e' una
    costante e non una variabile d'ambiente, per una scelta che `config.py`
    motiva. Resta offline per costruzione: `/entita` senza argomento chiama
    `list_entities`, che e' una query SQL, `/file` elenca il filesystem, e
    `/aiuto` stampa. L'unica lettura del progetto che accenderebbe un modello
    e' `leggi_intuizioni`, che nessun comando qui attraversa.
    """
    figlio = subprocess.run(
        [sys.executable, "chat.py", "--user", UTENTE, "--session", SESSIONE],
        cwd=config.BASE_DIR,
        env=os.environ.copy(),
        input="/aiuto\n\n/entita\n/file\nsconosciuto/comando\n/esci\n",
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    esigi(figlio.returncode == 0, "la REPL non e' uscita con 0: " + figlio.stderr[-800:])
    testo = figlio.stdout
    esigi("A presto" in testo, "la REPL non saluta all'uscita")
    esigi("/aiuto" in testo, "l'elenco dei comandi non compare")
    esigi("appunto.md" in testo, "/file non elenca il file scritto dall'agente")
    return "banner, comandi, riga vuota, comando ignoto e uscita"


def aiuto_senza_effetti() -> str:
    """`--help` non crea l'archivio, per nessuno dei cinque comandi.

    Ogni comando chiama `config.prepara_archivio()` dopo `parse_args()` e non
    prima, perche' `--help` esce li' in mezzo. Non e' un dettaglio estetico:
    un archivio a 0700 creato da un comando che stampa l'aiuto e' comunque un
    archivio che non c'era, e su una macchina condivisa e' la traccia che
    qualcuno ha guardato. La differenza fra prima e dopo quella riga sono
    due caratteri, e nessun'altra prova la vedrebbe.
    """
    comandi = ("chat.py", "backup.py", "entity_maintenance.py", "inspect_learning.py")
    for comando in comandi:
        pulita = Path(tempfile.mkdtemp(prefix="ares-aiuto-"))
        stato = pulita / "stato"
        ambiente = os.environ.copy()
        ambiente["ARES_TMP"] = str(stato)
        try:
            figlio = subprocess.run(
                [sys.executable, comando, "--help"],
                cwd=config.BASE_DIR,
                env=ambiente,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            esigi(figlio.returncode == 0, comando + " --help non e' uscito con 0: " + figlio.stderr[-300:])
            esigi("usage" in figlio.stdout, comando + " --help non stampa l'uso")
            esigi(not stato.exists(), comando + " --help ha creato l'archivio in " + str(stato))
        finally:
            shutil.rmtree(pulita, ignore_errors=True)

    # `preflight.py` non ha argparse: non ha argomenti da leggere, e non e'
    # una mancanza da colmare qui. Su di lui vale la stessa invariante presa
    # dal verso giusto - un'esecuzione intera non deve lasciare l'archivio -
    # e l'esito dipende da cosa gira sulla macchina, quindi non si controlla.
    pulita = Path(tempfile.mkdtemp(prefix="ares-aiuto-"))
    stato = pulita / "stato"
    ambiente = os.environ.copy()
    ambiente["ARES_TMP"] = str(stato)
    try:
        subprocess.run(
            [sys.executable, "preflight.py"],
            cwd=config.BASE_DIR,
            env=ambiente,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        esigi(not stato.exists(), "preflight.py ha creato l'archivio in " + str(stato))
    finally:
        shutil.rmtree(pulita, ignore_errors=True)
    return str(len(comandi)) + " aiuti e un preflight intero senza creare l'archivio"


def main() -> int:
    avvio = time.monotonic()
    print("Archivio della prova:", RADICE_PROVA)
    print()
    try:
        build_assistant(user_id=UTENTE, session_id=SESSIONE)
        ok("archivio", "costruito con database, indice e filesystem")

        for nome, prova in (
            ("preflight pronto", preflight_pronto),
            ("preflight mancante", preflight_modello_mancante),
            ("preflight spento", preflight_server_spento),
        ):
            ok(nome, prova())

        ok("inspect_learning", inspect_learning_cli())
        # Dopo l'ispezione, cosi' lo snapshot contiene anche il file scritto
        # sopra: uno snapshot di un archivio vuoto proverebbe meno.
        ok("backup CLI", backup_cli(RADICE_PROVA))
        ok("chat REPL", chat_repl())
        ok("aiuto puro", aiuto_senza_effetti())
    except Exception as errore:
        print()
        print("FALLITO ", type(errore).__name__ + ":", errore)
        print("Archivio della prova conservato:", RADICE_PROVA)
        return 1

    shutil.rmtree(RADICE_PROVA, ignore_errors=True)
    print()
    print("Concluso in", round(time.monotonic() - avvio, 2), "s")
    print("Nessun fallimento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
