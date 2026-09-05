"""
Prova dei comandi a riga di comando, senza Ollama
=================================================
Uso:
    .venv/bin/python tests/cli_test.py

I moduli di Ares erano provati; i comandi con cui si usano, no. La misura di
copertura lo diceva senza ambiguita': `preflight` e `inspect_learning`
allo 0%, il `main()` di `backup/snapshots.py` all'1%. Sono le righe che un utente
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
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

# I percorsi vanno scelti prima di importare config, che crea TMP_DIR
# all'import: importarlo e correggere dopo lascerebbe comunque una tmp/ vuota
# accanto ai dati veri.
from _comune import esigi, fallimento, ok, prepara_ambiente

RADICE_PROVA = prepara_ambiente("cli-test")

from ares import config  # noqa: E402
from ares.agent.echo import Fotografia, Istantanea  # noqa: E402
from ares.agent.turn_core import TurnEvent, TurnEventKind  # noqa: E402
from ares.backup import snapshots  # noqa: E402
from ares.cli import chat  # noqa: E402
from ares.ops import inspect_learning, preflight  # noqa: E402
from ares.sessions import maintenance  # noqa: E402
from ares.state.lock import StatoOccupato  # noqa: E402

UTENTE = "prova-cli"
SESSIONE = "cli"
# Il file che l'agente scrive nel proprio quaderno: lo crea il figlio che
# costruisce l'archivio, lo rilegge l'ispezione e lo elenca la REPL.
FILE_AGENTE = "note/appunto.md"
CONTENUTO_FILE = "riga di prova"

# Nessuna di queste prove deve accendere un modello, e su una macchina di
# sviluppo Ollama e' spesso acceso: senza questa riga la prova passerebbe qui
# usandolo di nascosto e fallirebbe in CI, dove non c'e'. Il porto e' chiuso di
# proposito, cosi' un tentativo di embedding si vede subito invece di
# funzionare.
config.OLLAMA_HOST = "http://127.0.0.1:1"


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


COSTRUZIONE = """
import sys

from ares.agent.assistant import build_assistant, build_filesystem

build_assistant(user_id=sys.argv[1], session_id=sys.argv[2])
build_filesystem(sys.argv[1]).write(sys.argv[3], sys.argv[4])
"""


def costruisci_archivio() -> str:
    """Crea l'archivio in un processo figlio, che poi muore.

    In-process sarebbe piu' breve, e su Linux funzionerebbe. Non su Windows:
    `build_assistant` lascia aperti i due SQLite per tutta la vita del
    processo, e li' un file aperto non si sostituisce, quindi il `restore`
    provato piu' sotto falliva con WinError 32 - su `filesystem.db`, dentro
    la directory che il restore stava rimpiazzando.

    Non e' un dettaglio della prova: e' il modo in cui il restore si usa
    davvero. Si ripristina con Ares chiuso, ed e' per questo che la chat
    tiene un lock condiviso e il restore ne chiede uno esclusivo. Una prova
    che ripristina tenendo l'archivio aperto sta provando una situazione che
    il prodotto vieta.

    Il figlio scrive anche il file dell'agente, cosi' lo snapshot creato
    dopo lo contiene: uno snapshot di un archivio vuoto proverebbe meno.
    """
    figlio = subprocess.run(
        [
            sys.executable,
            "-c",
            COSTRUZIONE,
            UTENTE,
            SESSIONE,
            FILE_AGENTE,
            CONTENUTO_FILE,
        ],
        cwd=config.BASE_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    esigi(figlio.returncode == 0, "costruzione dell'archivio fallita: " + figlio.stderr[-800:])
    esigi(Path(config.DB_FILE).is_file(), "il database dell'agente non e' stato creato")
    esigi(Path(config.FS_DB_FILE).is_file(), "il database del filesystem non e' stato creato")
    return "costruito in un processo separato, che non lo tiene aperto"


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
    # Un "ok" su un modello cloud deve dire che quel ruolo esce dalla
    # macchina, sulla riga del modello e in coda, dove si legge l'esito.
    if config.e_modello_cloud(config.MAIN_MODEL):
        esigi("(cloud, via ollama.com)" in testo, "il modello cloud non e' marcato come tale")
        esigi("escono dalla macchina" in testo, "manca l'avviso sul modello conversazionale cloud")
    else:
        esigi("cloud" not in testo, "il preflight parla di cloud con soli modelli locali")
    return "tag :latest riconosciuto, ruoli accumulati"


def preflight_modello_mancante() -> str:
    # Presente il modello di conversazione, assente l'embedder: e' il caso
    # tipico, perche' l'embedder si scarica dopo e non serve al primo turno.
    esito, testo = esegui_preflight([config.MAIN_MODEL])
    esigi(esito == 1, "un modello mancante non ha prodotto uscita 1")
    esigi("MANCANTE" in testo, "il modello mancante non e' segnalato")
    esigi("ollama pull" in testo, "manca il comando per scaricare il modello")
    esigi("Ambiente pronto" not in testo, "l'ambiente e' dichiarato pronto senza l'embedder")
    # L'embedder e' locale: il rimedio non deve chiedere un accesso a
    # ollama.com che non serve.
    esigi("ollama signin" not in testo, "signin suggerito per un modello locale mancante")
    return "segnalato con il comando per rimediare"


def preflight_cloud_mancante() -> str:
    # Il pull di un modello cloud riesce anche senza accesso, ma la prima
    # richiesta no: il rimedio deve nominare `ollama signin` prima del pull.
    with patch.object(config, "MAIN_MODEL", "glm-5.3-flash:cloud"):
        esito, testo = esegui_preflight([config.LEARNING_MODEL, config.EMBEDDER_MODEL])
    esigi(esito == 1, "un modello cloud mancante non ha prodotto uscita 1")
    esigi("MANCANTE  glm-5.3-flash:cloud" in testo, "il modello cloud mancante non e' segnalato")
    esigi(testo.index("ollama signin") < testo.index("ollama pull"), "signin non precede il pull")
    return "signin suggerito prima del pull"


def preflight_server_spento() -> str:
    esito, testo = esegui_preflight(None)
    esigi(esito == 1, "un server irraggiungibile non ha prodotto uscita 1")
    esigi("non raggiungibile" in testo, "il server spento non e' distinto")
    esigi("ollama serve" in testo, "manca il comando per avviare il server")
    return "distinto da un modello mancante"


def backup_cli(_archivio: Path) -> str:
    """Il `main()` di `ares.backup`, sottocomando per sottocomando.

    Le funzioni sotto sono gia' provate da `backup_test.py`. Qui si prova lo
    strato che le sceglie: l'analisi degli argomenti, le conferme testuali e i
    codici di uscita, che sono cio' su cui uno script chiamante decide.
    """

    def comando(*argomenti: str, risposta: str | None = None) -> tuple[int, str]:
        uscita = io.StringIO()
        with ExitStack() as pila:
            pila.enter_context(patch.object(sys, "argv", ["ares-backup", *argomenti]))
            pila.enter_context(redirect_stdout(uscita))
            if risposta is not None:
                # `input` viene sostituito solo dove la conferma serve: nei
                # comandi che non la chiedono, una risposta pronta
                # nasconderebbe una richiesta comparsa per errore.
                pila.enter_context(patch("builtins.input", lambda _prompt="": risposta))
            esito = snapshots.main()
        return esito, uscita.getvalue()

    esito, testo = comando("create")
    esigi(esito == 0, "create non riuscito: " + testo)
    esigi("Snapshot creato e verificato" in testo, "create non nomina lo snapshot")
    primo = snapshots.elenco_snapshot()[-1].name

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

    prima = len(snapshots.elenco_snapshot())
    esito, testo = comando("prune", "--keep", "1", "--yes")
    esigi(esito == 0, "prune non riuscito: " + testo)
    esigi(len(snapshots.elenco_snapshot()) == 1, "prune non ha conservato esattamente uno snapshot")
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
    argv = ["ares-inspect", "--user", UTENTE, "--session", SESSIONE]
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
    argv = ["ares-inspect", "--user", UTENTE, "--file", "non/esiste.md"]
    with patch.object(sys, "argv", argv), redirect_stdout(uscita):
        inspect_learning.main()
    esigi("Nessun file a questo percorso" in uscita.getvalue(), "un file assente non viene segnalato")

    uscita = io.StringIO()
    argv = ["ares-inspect", "--user", UTENTE, "--file", FILE_AGENTE]
    with patch.object(sys, "argv", argv), redirect_stdout(uscita):
        inspect_learning.main()
    esigi(CONTENUTO_FILE in uscita.getvalue(), "il contenuto del file non viene stampato")
    return "cinque sezioni, archivio invariato, --file presente e assente"


def chat_repl() -> str:
    """La REPL intera in un processo separato, con stdin da una pipe.

    Senza terminale `CliInput` ripiega su `input()`, quindi la conversazione si
    puo' scrivere in anticipo. Il giro che si prova e' quello che nessuna prova
    attraversava: banner, ciclo, dispatch dei comandi, uscita pulita.

    Il figlio non eredita `config.OLLAMA_HOST` chiuso di questa prova - e' una
    costante e non una variabile d'ambiente, per una scelta che `config.py`
    motiva. Resta offline soltanto se ogni riga che gli si manda comincia con
    `/`: quello che non comincia con `/` non e' un comando, e' un messaggio, e
    la REPL lo manda al modello. Le righe qui sono comandi, la riga vuota e un
    comando inesistente; l'ultima asserzione verifica che nessun turno sia
    stato aperto, perche' e' un errore che passerebbe inosservato - in CI un
    Ollama irraggiungibile diventa un evento di errore che la REPL stampa,
    e la prova resterebbe verde per il motivo sbagliato.
    """
    figlio = subprocess.run(
        [sys.executable, "-m", "ares", "--user", UTENTE, "--session", SESSIONE],
        cwd=config.BASE_DIR,
        env=os.environ.copy(),
        input="/aiuto\n\n/entita\n/file\n/sconosciuto comando\n/esci\n",
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
    esigi("Comando sconosciuto: /sconosciuto" in testo, "il comando ignoto non e' stato riconosciuto come tale")
    # La riga che tiene in piedi la promessa del modulo. Una riga che non
    # comincia con `/` non e' un comando: e' un messaggio, e la REPL lo manda
    # al modello. Qui era gia' successo per un `/` messo in mezzo invece che
    # in testa, e non se n'era accorto nessuno: la prova passava lo stesso,
    # accendeva Ollama, e in CI sarebbe passata di nuovo perche' un modello
    # irraggiungibile diventa un evento di errore che la REPL stampa e basta.
    # `Ares` a schermo significa una cosa sola: l'intestazione che apre una
    # risposta del modello. Il banner non la contiene, i comandi nemmeno.
    esigi("Ares" not in testo, "la REPL ha aperto un turno col modello: " + testo[-400:])
    return "banner, comandi, riga vuota, comando ignoto e uscita"


# ---------------------------------------------------------------------------
# Il ciclo della REPL, in questo processo
# ---------------------------------------------------------------------------
# `chat_repl` prova la REPL da fuori, con stdin da una pipe, e per restare
# offline puo' mandarle soltanto comandi: tutto cio' che non comincia con `/`
# e' un messaggio e vorrebbe il modello. Restava percio' scoperta proprio la
# meta' che conta - il turno, i suoi due gestori d'errore, le metriche, gli
# avvisi d'avvio - cioe' le righe che un utente attraversa a ogni frase che
# scrive.
#
# Qui il modello non serve: al suo posto c'e' una `run_turn_cycle` finta.
# Quello che si prova non e' cosa risponde Ares, che dipende dal modello, ma
# cio' che la REPL fa intorno alla risposta e che dal modello non dipende:
# che un'eccezione non chiuda la sessione, che un Ctrl-C sia distinto da un
# guasto, che una pausa irrisolta venga detta invece di restare appesa.


def _piatto(testo: str) -> str:
    """Il testo a schermo senza gli a-capo che ci mette la larghezza del terminale.

    Rich manda a capo sulla colonna della console, che in una prova non e' un
    terminale e vale 80. Una frase cercata per intero cadrebbe percio' a
    seconda di dove si spezza, e la prova fallirebbe per la larghezza invece
    che per il contenuto.
    """
    return " ".join(testo.split())


class FintoInput:
    """`CliInput` ridotto a cio' che il ciclo usa: una coda di righe.

    Ogni elemento e' una riga da restituire oppure un'eccezione da sollevare,
    perche' le due uscite del prompt - EOF e Ctrl-C - sono esattamente ciò
    che chiude la REPL e vanno provate dalla stessa coda.
    """

    def __init__(self, righe, history_warning=None, risposte=()):
        self.righe = list(righe)
        self.history_warning = history_warning
        self.domande: list[str] = []
        # Le risposte alle domande, in ordine; esaurite, ogni domanda riceve
        # una riga vuota, cioe' il default di ogni [S/n] e [s/N].
        self.risposte = list(risposte)

    def prompt(self) -> str:
        if not self.righe:
            raise EOFError
        voce = self.righe.pop(0)
        if isinstance(voce, BaseException) or (isinstance(voce, type) and issubclass(voce, BaseException)):
            raise voce
        return voce

    def ask(self, etichetta: str, *, muted: bool = False) -> str:
        self.domande.append(etichetta)
        return self.risposte.pop(0) if self.risposte else ""


class FintaChiamata:
    """Una chiamata al modello come la legge `_conta_chiamate`: per attributi.

    Non un dict: `_conta_chiamate` usa `getattr`, quindi un dizionario passa
    senza errori e conta zero, e la prova resterebbe verde su una riga di
    metriche vuota.
    """

    input_tokens = 4000
    output_tokens = 120
    provider_metrics: ClassVar[dict] = {"total_duration": 1_500_000_000}


class FinteMetriche:
    """Il minimo che `righe_metriche` legge: la durata e le chiamate per ruolo."""

    duration = 2.0
    details: ClassVar[dict] = {"model": [FintaChiamata()]}


class FintaRisposta:
    def __init__(self, is_paused=False, metriche=None):
        self.is_paused = is_paused
        self.metrics = metriche
        self.active_requirements: list = []


def chat_turno() -> str:
    """I quattro esiti di `esegui_turno`, che e' la rete sotto ogni frase.

    Un turno normale, uno che resta in pausa per un motivo che la CLI non sa
    chiedere, un Ctrl-C e un guasto. Gli ultimi due sono rami separati nel
    codice per una ragione dichiarata nel docstring - una decisione non e' un
    imprevisto - e qui si verifica che restino distinti: se un giorno
    l'`except Exception` inghiottisse anche il `KeyboardInterrupt`, un Ctrl-C
    comincerebbe a somigliare a un errore e nessun'altra prova lo direbbe.
    """
    input_cli = FintoInput([])

    # Turno normale. La finta `run_turn_cycle` chiama entrambe le callback
    # che il ciclo le passa: senza, `mostra_evento` e `chiedi_conferme` non
    # verrebbero attraversate mai e le due lambda resterebbero non provate.
    def ciclo_ok(agent, testo, *, on_event, resolve_pause):
        esigi(testo == "ciao", "il testo non arriva al ciclo del turno")
        on_event(TurnEvent(kind=TurnEventKind.CONTENT, content="risposta"))
        on_event(TurnEvent(kind=TurnEventKind.RUN_COMPLETED))
        esigi(resolve_pause(FintaRisposta()) == 0, "una pausa senza requisiti non deve risolversi")
        return FintaRisposta()

    uscita = io.StringIO()
    with patch.object(chat, "run_turn_cycle", ciclo_ok), redirect_stdout(uscita):
        risposta = chat.esegui_turno(object(), "ciao", input_cli)
    esigi(risposta is not None, "un turno riuscito non restituisce la risposta")

    # Pausa che il client non sa risolvere: il ciclo si ferma e lo dice.
    uscita = io.StringIO()
    with (
        patch.object(chat, "run_turn_cycle", lambda *a, **k: FintaRisposta(is_paused=True)),
        redirect_stdout(uscita),
    ):
        chat.esegui_turno(object(), "ciao", input_cli)
    esigi("in pausa" in _piatto(uscita.getvalue()), "una pausa irrisolta resta muta")

    # Ctrl-C fuori dal turno: nessun apprendimento, e non e' un errore.
    def ciclo_interrotto(*a, **k):
        raise KeyboardInterrupt

    uscita = io.StringIO()
    with patch.object(chat, "run_turn_cycle", ciclo_interrotto), redirect_stdout(uscita):
        esigi(chat.esegui_turno(object(), "ciao", input_cli) is None, "un'interruzione non restituisce None")
    testo = _piatto(uscita.getvalue())
    esigi("Interrotto" in testo, "l'interruzione non viene detta")
    esigi("fallito" not in testo, "un Ctrl-C viene presentato come un guasto")

    # Guasto: il tipo dell'eccezione a schermo, e la sessione resta aperta.
    def ciclo_rotto(*a, **k):
        raise RuntimeError("archivio irraggiungibile")

    uscita = io.StringIO()
    with patch.object(chat, "run_turn_cycle", ciclo_rotto), redirect_stdout(uscita):
        esigi(chat.esegui_turno(object(), "ciao", input_cli) is None, "un guasto non restituisce None")
    testo = _piatto(uscita.getvalue())
    esigi("RuntimeError" in testo, "il tipo dell'errore non compare")
    esigi("archivio irraggiungibile" in testo, "il messaggio dell'errore non compare")
    esigi("sessione resta aperta" in testo, "non viene detto che la sessione sopravvive")

    # L'eco: la fotografia prima del turno e quella dopo sono diverse, e la
    # differenza compare sotto la risposta. L'ordine conta - la prima lettura
    # deve precedere il turno, perche' `update_user_memory` scrive durante il
    # run - quindi la finta registra quando viene chiamata.
    letture: list[str] = []
    turni: list[str] = []
    ripristini: list[Istantanea] = []

    def istantanea_finta(agent):
        letture.append("turno" if turni else "prima")
        return Istantanea()

    def fotografa_finta(agent):
        letture.append("turno" if turni else "prima")
        return Fotografia(memorie={"m1": "Preferisce config.py ai flag."})

    def ciclo_che_scrive(agent, testo, *, on_event, resolve_pause):
        turni.append(testo)
        return FintaRisposta()

    def ripristina_finto(agent, stato):
        ripristini.append(stato)
        return esito_ripristino

    esito_ripristino = True

    def turno_che_scrive(risposte, *, conferma=True):
        letture.clear()
        turni.clear()
        ripristini.clear()
        input_cli = FintoInput([], risposte=risposte)
        uscita = io.StringIO()
        with (
            patch.object(chat, "run_turn_cycle", ciclo_che_scrive),
            patch.object(chat, "istantanea", istantanea_finta),
            patch.object(chat, "fotografa", fotografa_finta),
            patch.object(chat, "ripristina", ripristina_finto),
            patch.object(config, "MOSTRA_APPRENDIMENTI", True),
            patch.object(config, "CONFERMA_APPRENDIMENTI", conferma),
            redirect_stdout(uscita),
        ):
            chat.esegui_turno(object(), "ricorda che preferisco config.py", input_cli)
        return _piatto(uscita.getvalue()), input_cli.domande

    testo, domande = turno_che_scrive([""])
    esigi(letture == ["prima", "turno"], "le letture non avvolgono il turno: " + repr(letture))
    esigi("appreso: memorie +1" in testo, "la sintesi dell'eco non compare: " + testo)
    esigi("Preferisce config.py ai flag." in testo, "il testo della memoria non compare: " + testo)
    # La domanda segue l'eco, e Invio tiene: nessun ripristino.
    esigi(domande == ["Tenere in memoria? [S/n] "], "la domanda sull'apprendimento e' " + repr(domande))
    esigi(ripristini == [], "un Invio ha ripristinato")
    esigi("ripristinato" not in testo, "con un Invio compare il ripristino")

    # Un no riporta gli store all'istantanea di prima del turno e lo dice.
    testo, domande = turno_che_scrive(["n"])
    esigi(ripristini == [Istantanea()], "il no non ripristina l'istantanea di prima: " + repr(ripristini))
    esigi("ripristinato: profilo e memorie come prima del turno" in testo, "il ripristino non viene detto: " + testo)

    # Un ripristino che non torna uguale viene detto come tale, non taciuto.
    esito_ripristino = False
    testo, domande = turno_che_scrive(["no"])
    esigi(ripristini == [Istantanea()], "il no non ripristina")
    esigi("ripristino incompleto" in testo, "un ripristino non riuscito passa per riuscito: " + testo)
    esito_ripristino = True

    # Ctrl-C davanti alla domanda tiene, come un Invio.
    class InputInterrotto(FintoInput):
        def ask(self, etichetta, *, muted=False):
            raise KeyboardInterrupt

    letture.clear()
    turni.clear()
    ripristini.clear()
    uscita = io.StringIO()
    with (
        patch.object(chat, "run_turn_cycle", ciclo_che_scrive),
        patch.object(chat, "istantanea", istantanea_finta),
        patch.object(chat, "fotografa", fotografa_finta),
        patch.object(chat, "ripristina", ripristina_finto),
        patch.object(config, "MOSTRA_APPRENDIMENTI", True),
        patch.object(config, "CONFERMA_APPRENDIMENTI", True),
        redirect_stdout(uscita),
    ):
        chat.esegui_turno(object(), "ricorda che preferisco config.py", InputInterrotto([]))
    esigi(ripristini == [], "un Ctrl-C alla domanda ha ripristinato")

    # Con la conferma spenta l'eco compare e la domanda no.
    testo, domande = turno_che_scrive(["n"], conferma=False)
    esigi("appreso: memorie +1" in testo, "senza conferma l'eco sparisce")
    esigi(domande == [], "con la conferma spenta la domanda viene fatta lo stesso: " + repr(domande))
    esigi(ripristini == [], "con la conferma spenta si ripristina")

    # Un turno che non ha scritto niente non fa domande.
    letture.clear()
    turni.clear()
    input_cli = FintoInput([], risposte=["n"])
    uscita = io.StringIO()
    with (
        patch.object(chat, "run_turn_cycle", ciclo_ok),
        patch.object(chat, "istantanea", lambda agent: Istantanea()),
        patch.object(chat, "fotografa", lambda agent: Fotografia()),
        patch.object(config, "MOSTRA_APPRENDIMENTI", True),
        patch.object(config, "CONFERMA_APPRENDIMENTI", True),
        redirect_stdout(uscita),
    ):
        chat.esegui_turno(object(), "ciao", input_cli)
    esigi(input_cli.domande == [], "un turno senza scritture fa una domanda: " + repr(input_cli.domande))

    # Spento in config non si legge nemmeno l'archivio.
    letture.clear()
    turni.clear()
    uscita = io.StringIO()
    with (
        patch.object(chat, "run_turn_cycle", ciclo_ok),
        patch.object(chat, "istantanea", istantanea_finta),
        patch.object(chat, "fotografa", fotografa_finta),
        patch.object(config, "MOSTRA_APPRENDIMENTI", False),
        redirect_stdout(uscita),
    ):
        chat.esegui_turno(object(), "ciao", input_cli)
    esigi(letture == [], "con l'eco spento l'archivio viene letto lo stesso")
    esigi("appreso" not in _piatto(uscita.getvalue()), "con l'eco spento compare una riga di eco")
    return (
        "turno, pausa irrisolta, Ctrl-C e guasto restano quattro esiti distinti; "
        "l'eco avvolge il turno e un no lo riporta indietro"
    )


def chat_ciclo() -> str:
    """Il giro completo di `_esegui_chat` con un modello finto.

    Copre cio' che `chat_repl` non puo' raggiungere da fuori: la riga vuota
    che non apre un turno, il messaggio che lo apre, la riga delle metriche
    chiesta con `--metriche`, l'avviso sulla cronologia degradata, l'avviso
    del modello cloud, il promemoria di backup e le due uscite dal prompt.
    """
    turni: list[str] = []

    def ciclo(agent, testo, *, on_event, resolve_pause):
        turni.append(testo)
        return FintaRisposta(metriche=FinteMetriche())

    input_cli = FintoInput(["", "  ", "ciao Ares", KeyboardInterrupt], history_warning="disco in sola lettura")

    uscita = io.StringIO()
    with (
        patch.object(sys, "argv", ["ares", "--user", UTENTE, "--session", SESSIONE, "--metriche"]),
        patch.object(chat, "build_assistant", lambda **k: object()),
        patch.object(chat, "CliInput", lambda **k: input_cli),
        patch.object(chat, "run_turn_cycle", ciclo),
        patch.object(chat, "promemoria_backup", lambda: ["Ultimo backup: mai", "Esegui ares-backup create"]),
        patch.object(config, "MAIN_MODEL", "glm-5.3-flash:cloud"),
        redirect_stdout(uscita),
    ):
        chat._esegui_chat()

    testo = _piatto(uscita.getvalue())
    esigi(turni == ["ciao Ares"], "le righe vuote hanno aperto un turno: " + repr(turni))
    esigi("Cronologia non disponibile" in testo, "la cronologia degradata non viene segnalata")
    esigi("sola lettura" in testo, "il motivo della cronologia degradata non compare")
    esigi("escono dalla macchina" in testo, "l'avviso del modello cloud non compare")
    esigi("Ultimo backup: mai" in testo, "il promemoria di backup non compare")
    esigi("tok" in testo and "turno" in testo, "le metriche non compaiono con --metriche")
    esigi("A presto" in testo, "la REPL non saluta dopo un Ctrl-C al prompt")

    # Senza `--metriche` e con un modello locale la riga del costo non c'e' e
    # l'avviso del cloud nemmeno: sono le due condizioni che li accendono, e
    # provarle solo accese non direbbe che dipendono da qualcosa.
    input_cli = FintoInput(["ciao Ares"])
    uscita = io.StringIO()
    with (
        patch.object(sys, "argv", ["ares", "--user", UTENTE, "--session", SESSIONE]),
        patch.object(chat, "build_assistant", lambda **k: object()),
        patch.object(chat, "CliInput", lambda **k: input_cli),
        patch.object(chat, "run_turn_cycle", ciclo),
        patch.object(chat, "promemoria_backup", list),
        patch.object(config, "MAIN_MODEL", "qwen3:9b"),
        patch.object(config, "MOSTRA_METRICHE", False),
        redirect_stdout(uscita),
    ):
        chat._esegui_chat()
    testo = _piatto(uscita.getvalue())
    esigi("escono dalla macchina" not in testo, "un modello locale mostra l'avviso del cloud")
    esigi("tok" not in testo, "le metriche compaiono senza che siano state chieste")
    return "riga vuota, turno, metriche, avvisi d'avvio e le due uscite dal prompt"


def chat_residui() -> str:
    """Un restore rimasto a meta' viene detto all'avvio, e solo allora.

    Il residuo e' una directory vera accanto allo stato, con il nome che il
    restore usa: e' la funzione di lettura a trovarlo, non una finta. Si
    prova prima con il residuo e poi senza, perche' un avviso che compare
    sempre e' quello che smette di essere letto.
    """
    stato = config.TMP_DIR.resolve()
    residuo = stato.with_name("." + stato.name + "-precedente-deadbeef")

    def avvia() -> str:
        uscita = io.StringIO()
        with (
            patch.object(sys, "argv", ["ares", "--user", UTENTE, "--session", SESSIONE]),
            patch.object(chat, "build_assistant", lambda **k: object()),
            patch.object(chat, "CliInput", lambda **k: FintoInput([])),
            patch.object(chat, "promemoria_backup", list),
            redirect_stdout(uscita),
        ):
            chat._esegui_chat()
        return _piatto(uscita.getvalue())

    residuo.mkdir()
    try:
        testo = avvia()
    finally:
        shutil.rmtree(residuo, ignore_errors=True)
    esigi("restore non e' stato completato" in testo, "il residuo del restore non viene detto: " + testo)
    # Il percorso e' una parola sola piu' larga delle 80 colonne della console
    # di prova su Windows, e Rich la spezza dove capita: si confronta senza
    # spazi, perche' l'a-capo non e' cio' che si prova.
    esigi(residuo.name in "".join(testo.split()), "l'avviso non nomina il residuo: " + testo)
    esigi("Ares non tocca" in testo, "l'avviso non dice che il residuo resta all'utente")
    esigi("restore non e' stato completato" not in avvia(), "l'avviso compare senza residui")
    return "con il residuo l'avviso nomina la directory, senza residui tace"


def sessioni_parziale() -> str:
    """`ares-sessions` con un guasto a meta': il rendiconto dice cosa e' rimasto.

    La cancellazione non e' atomica - Agno elimina sessioni e run in una
    transazione, ma payload, contesti e verifiche vengono dopo, un passo
    alla volta - e un guasto li' non e' un rifiuto: qualcosa e' gia' sparito.
    Due guasti iniettati, uno prima della cancellazione e uno dopo, perche'
    il conteggio deve venire dall'archivio e non da dove ci si e' fermati:
    nel primo caso le sessioni ci sono ancora tutte, nel secondo nessuna.
    """
    from agno.session.agent import AgentSession

    db = maintenance.build_db()
    antico = int(time.time()) - 100 * 86_400
    sessioni = ["cli-inattiva-a", "cli-inattiva-b"]
    for session_id in sessioni:
        db.upsert_session(AgentSession(session_id=session_id, user_id=UTENTE, created_at=antico, updated_at=antico))

    def prune(*patches) -> tuple[int, str, str]:
        uscita, errori = io.StringIO(), io.StringIO()
        with ExitStack() as pila:
            for p in patches:
                pila.enter_context(p)
            pila.enter_context(redirect_stdout(uscita))
            pila.enter_context(redirect_stderr(errori))
            esito = maintenance.main(["prune", "--user", UTENTE, "--older-than", "30", "--apply", "--yes"])
        return esito, _piatto(uscita.getvalue()), _piatto(errori.getvalue())

    guasto = patch("agno.db.sqlite.SqliteDb.delete_sessions", side_effect=RuntimeError("disco pieno"))
    esito, testo, errori = prune(guasto)
    esigi(esito == 1, "un guasto a meta' non esce con 1: " + str(esito) + " " + errori)
    esigi("Backup verificato" in testo, "lo snapshot pre-manutenzione non e' stato fatto prima del guasto")
    esigi("Cancellazione interrotta: disco pieno" in errori, "la causa del guasto non compare: " + errori)
    esigi("Stato parziale: eliminate 0 sessioni su 2, ancora presenti 2." in errori, "conteggio sbagliato: " + errori)
    esigi("Ancora presenti: cli-inattiva-a, cli-inattiva-b" in errori, "le sessioni rimaste non sono nominate")
    esigi("Eliminate senza verifica" not in errori, "dichiarate eliminate sessioni che ci sono ancora")
    esigi("pre-session-prune" in errori and "ares-backup restore" in errori, "lo snapshot da cui tornare non compare")
    esigi("Manutenzione rifiutata" not in errori, "un guasto a meta' presentato come rifiuto")

    guasto = patch("ares.sessions.retention._contesto_sessione_presente", return_value=True)
    esito, testo, errori = prune(guasto)
    esigi(esito == 1, "un guasto nella verifica non esce con 1: " + str(esito) + " " + errori)
    esigi("contesto di sessione non eliminato" in errori, "la causa del guasto non compare: " + errori)
    esigi("Stato parziale: eliminate 2 sessioni su 2, ancora presenti 0." in errori, "conteggio sbagliato: " + errori)
    esigi("Eliminate senza verifica: cli-inattiva-a, cli-inattiva-b" in errori, "le eliminate non sono nominate")
    esigi("orfani" in errori, "non viene detto che possono restare contesti o payload orfani")
    esigi("Ancora presenti:" not in errori, "dichiarate presenti sessioni che non ci sono piu'")
    for session_id in sessioni:
        rimasta = db.get_session(session_id=session_id, user_id=UTENTE)
        esigi(rimasta is None, "sessione ancora nell'archivio: " + session_id)
    return "guasto prima e dopo la cancellazione: conteggio letto dall'archivio e snapshot da cui tornare"


def chat_avvio() -> str:
    """`main()`: il lock condiviso, l'archivio occupato e il Ctrl-C all'avvio.

    Sono le righe che si attraversano prima che la REPL esista. Un backup in
    corso deve produrre un messaggio e non un traceback, e un Ctrl-C mentre
    Ares apre database e indice pure: e' la finestra in cui la costruzione
    dell'agente non e' ancora protetta da nulla.
    """
    uscita = io.StringIO()
    with (
        patch.object(chat, "lock_stato", lambda esclusivo: (_ for _ in ()).throw(StatoOccupato("backup in corso"))),
        redirect_stdout(uscita),
    ):
        chat.main()
    testo = _piatto(uscita.getvalue())
    esigi("Impossibile avviare Ares" in testo, "l'archivio occupato non viene detto")
    esigi("backup in corso" in testo, "il motivo dell'occupazione non compare")
    esigi("riprova" in testo, "non viene suggerito di riprovare")

    def avvio_interrotto() -> None:
        raise KeyboardInterrupt

    uscita = io.StringIO()
    with patch.object(chat, "_esegui_chat", avvio_interrotto), redirect_stdout(uscita):
        chat.main()
    esigi("Avvio interrotto" in _piatto(uscita.getvalue()), "un Ctrl-C durante l'avvio non viene detto")
    return "lock condiviso, archivio occupato e Ctrl-C prima della REPL"


def aiuto_senza_effetti() -> str:
    """`--help` non crea l'archivio, per nessuno dei cinque comandi.

    Ogni comando chiama `config.prepara_archivio()` dopo `parse_args()` e non
    prima, perche' `--help` esce li' in mezzo. Non e' un dettaglio estetico:
    un archivio a 0700 creato da un comando che stampa l'aiuto e' comunque un
    archivio che non c'era, e su una macchina condivisa e' la traccia che
    qualcuno ha guardato. La differenza fra prima e dopo quella riga sono
    due caratteri, e nessun'altra prova la vedrebbe.
    """
    comandi = (
        "ares",
        "ares.backup",
        "ares.entities",
        "ares.sessions",
        "ares.ops.inspect_learning",
    )
    for comando in comandi:
        pulita = Path(tempfile.mkdtemp(prefix="ares-aiuto-"))
        stato = pulita / "stato"
        ambiente = os.environ.copy()
        ambiente["ARES_TMP"] = str(stato)
        try:
            figlio = subprocess.run(
                [sys.executable, "-m", comando, "--help"],
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

    # `preflight` non ha argparse: non ha argomenti da leggere, e non e'
    # una mancanza da colmare qui. Su di lui vale la stessa invariante presa
    # dal verso giusto - un'esecuzione intera non deve lasciare l'archivio -
    # e l'esito dipende da cosa gira sulla macchina, quindi non si controlla.
    pulita = Path(tempfile.mkdtemp(prefix="ares-aiuto-"))
    stato = pulita / "stato"
    ambiente = os.environ.copy()
    ambiente["ARES_TMP"] = str(stato)
    try:
        subprocess.run(
            [sys.executable, "-m", "ares.ops.preflight"],
            cwd=config.BASE_DIR,
            env=ambiente,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        esigi(not stato.exists(), "preflight ha creato l'archivio in " + str(stato))
    finally:
        shutil.rmtree(pulita, ignore_errors=True)
    return str(len(comandi)) + " aiuti e un preflight intero senza creare l'archivio"


def main() -> int:
    avvio = time.monotonic()
    print("Archivio della prova:", RADICE_PROVA)
    print()
    try:
        ok("archivio", costruisci_archivio())

        for nome, prova in (
            ("preflight pronto", preflight_pronto),
            ("preflight mancante", preflight_modello_mancante),
            ("preflight cloud mancante", preflight_cloud_mancante),
            ("preflight spento", preflight_server_spento),
        ):
            ok(nome, prova())

        # Il backup prima dell'ispezione, e non e' indifferente: il `restore`
        # sostituisce la directory dello stato, e `inspect_learning.main()`
        # gira qui dentro e lascia aperti i database che apre. Su Windows
        # basta questo a far fallire il restore.
        ok("backup CLI", backup_cli(RADICE_PROVA))
        ok("inspect_learning", inspect_learning_cli())
        ok("chat REPL", chat_repl())
        ok("chat turno", chat_turno())
        ok("chat ciclo", chat_ciclo())
        ok("chat avvio", chat_avvio())
        ok("chat residui", chat_residui())
        # Per ultima fra quelle sull'archivio: lascia due sessioni in meno e
        # apre i database in questo processo.
        ok("sessioni parziale", sessioni_parziale())
        ok("aiuto puro", aiuto_senza_effetti())
    except Exception as errore:
        print()
        fallimento(errore)
        print("Archivio della prova conservato:", RADICE_PROVA)
        return 1

    shutil.rmtree(RADICE_PROVA, ignore_errors=True)
    print()
    print("Concluso in", round(time.monotonic() - avvio, 2), "s")
    print("Nessun fallimento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
