"""Prova dei comandi a riga di comando, senza Ollama
=================================================
Uso:
    .venv/bin/python tests/cli_test.py

I comandi con cui Ares si usa: preflight, ispezione, backup, migrazione,
sessioni e la REPL. Si afferma che ogni comando esca con il codice giusto e
stampi cio' su cui l'utente decide il passo dopo, non che il testo sia
formulato bene.

Niente modello e niente rete verso l'esterno. Il preflight interroga un
server HTTP finto su localhost, con l'elenco di modelli deciso dalla prova:
cosi' si provano i tre esiti (pronto, modello mancante, server spento)
senza dipendere da cosa c'e' sulla macchina.
"""

import errno
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
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

# I percorsi vanno scelti prima di importare config, che li legge quando
# `leggi_percorsi` viene chiamata: importarlo e correggere dopo non basta
# piu', perche' nessun nome di modulo li tiene.
from _comune import esigi, fallimento, ok, prepara_ambiente, pulisci

RADICE_PROVA = prepara_ambiente("cli-test")

from ares import config  # noqa: E402

# I percorsi della prova, letti una volta dopo `prepara_ambiente`: `config`
# non li tiene piu' in nomi propri, quindi la prova se li porta dietro e li
# passa a chi ne ha bisogno.
PERCORSI = config.leggi_percorsi()
from ares.agent.echo import Fotografia, Istantanea  # noqa: E402
from ares.agent.turn_core import TurnEvent, TurnEventKind  # noqa: E402
from ares.backup import snapshots  # noqa: E402
from ares.cli import cartella, chat  # noqa: E402
from ares.cli.ui import UI  # noqa: E402
from ares.ops import inspect_learning, preflight  # noqa: E402
from ares.sessions import maintenance  # noqa: E402
from ares.state.identita import Utente, UtenteNonValido, utente_canonico  # noqa: E402
from ares.state.lock import StatoOccupato, lock_stato, lock_turno  # noqa: E402
from ares.state.stores import namespace_entita, namespace_utente  # noqa: E402

UTENTE = "prova-cli"
SESSIONE = "cli"
# Il file che l'agente scrive nel proprio quaderno: lo crea il figlio che
# costruisce l'archivio, lo rilegge l'ispezione e lo elenca la REPL.
FILE_AGENTE = "note/appunto.md"
CONTENUTO_FILE = "riga di prova"

# Ollama e' spesso acceso sulla macchina di sviluppo: la prova punta a una
# porta chiusa, cosi' un uso nascosto del modello fallisce subito qui invece
# che solo in CI.
config.OLLAMA_HOST = "http://127.0.0.1:1"

# Impostazioni e politica si leggono dopo, perche' devono fotografare anche
# quel porto chiuso: sono quelle che il confine del processo costruira', e una
# prova che le fissasse prima confronterebbe due cose diverse.
IMPOSTAZIONI = config.leggi_impostazioni()
POLITICA = config.leggi_politica()


# ---------------------------------------------------------------------------
# Server Ollama finto
# ---------------------------------------------------------------------------


class OllamaFinto(BaseHTTPRequestHandler):
    """Risponde a /api/tags con l'elenco deciso dalla prova.

    L'elenco sta sulla classe perche' HTTPServer crea un handler per richiesta.
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
    """Una porta che il sistema dichiara libera adesso: una fissa prima o poi sarebbe occupata."""
    with socket.socket() as presa:
        presa.bind(("127.0.0.1", 0))
        return int(presa.getsockname()[1])


def esegui_preflight(modelli: list[str] | None, argomenti: list[str] | None = None) -> tuple[int, str]:
    """Lancia il preflight contro un server finto, o contro nessun server.

    Con `modelli=None` punta a una porta chiusa: il caso "Ollama non gira".
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
            esito = preflight.main(argomenti or [])
        return esito, uscita.getvalue()
    finally:
        if servitore is not None:
            servitore.shutdown()
            servitore.server_close()


COSTRUZIONE = """
import sys

from ares import config
from ares.agent.assistant import build_assistant, build_filesystem
from ares.state.identita import Utente

percorsi = config.leggi_percorsi()
utente = Utente.da_grezzo(sys.argv[1])
build_assistant(percorsi, config.leggi_impostazioni(), config.leggi_politica(), utente, session_id=sys.argv[2])
build_filesystem(percorsi, utente).write(sys.argv[3], sys.argv[4])
"""


def costruisci_archivio() -> str:
    """Crea l'archivio in un processo figlio, che poi muore.

    `build_assistant` lascia aperti i SQLite per tutta la vita del processo, e
    su Windows un file aperto non si sostituisce: il `restore` piu' sotto
    fallirebbe. E' anche l'uso reale: si ripristina con Ares chiuso. Il figlio
    scrive anche un file dell'agente, perche' lo snapshot non sia vuoto.
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
    esigi(Path(PERCORSI.db_file).is_file(), "il database dell'agente non e' stato creato")
    esigi(Path(PERCORSI.fs_db_file).is_file(), "il database del filesystem non e' stato creato")
    return "costruito in un processo separato, che non lo tiene aperto"


# ---------------------------------------------------------------------------
# Identita' e sessioni
# ---------------------------------------------------------------------------


COSTRUZIONE_AGENTE = """
from ares import config
from ares.agent.assistant import build_assistant
from ares.state.identita import Utente

agente = build_assistant(
    config.leggi_percorsi(),
    config.leggi_impostazioni(),
    config.leggi_politica(),
    Utente.da_grezzo("  Demo  "),
    session_id="identita",
)
print(agente.user_id)
"""


def identita_canonica() -> str:
    """Una sola forma dell'utente per namespace, entita', lock e profilo.

    `Demo` e `demo` devono essere la stessa persona ovunque; un id fuori
    alfabeto e' rifiutato invece di diventare un contenitore condiviso; una
    forma non canonica non puo' diventare un'identita'.
    """
    esigi(utente_canonico("  Kairos ") == "kairos", "spazi e maiuscole non normalizzati")
    demo = Utente.da_grezzo("Demo")
    stessa = Utente.da_grezzo("demo")
    esigi(demo == stessa, "due grafie non danno lo stesso utente")
    esigi(demo.id == "demo" and str(demo) == "demo", "l'id canonico non e' quello atteso: " + repr(demo.id))
    esigi(
        namespace_utente(demo) == namespace_utente(stessa) == "user/demo",
        "il namespace non usa la forma canonica: " + repr(namespace_utente(demo)),
    )
    esigi(namespace_entita(demo) == namespace_entita(stessa), "le entita' non seguono la stessa identita'")

    # `da_grezzo` e' l'unica porta: una grafia sporca arrivata a uno store
    # scriverebbe in un contenitore che nessun lettore canonico interroga,
    # sdoppiando l'archivio senza errori.
    for scrittura in ("Demo", "  demo  ", "DEMO"):
        try:
            Utente(scrittura)
        except UtenteNonValido:
            pass
        else:
            esigi(False, "Utente ha accettato una forma non canonica: " + repr(scrittura))

    # Fuori dall'alfabeto: un separatore anniderebbe il namespace, un
    # carattere accentato verrebbe percent-encodato da Agno, uno spazio pure.
    # `.` e `..` sono nell'alfabeto ma Agno li rifiuta come segmenti di path.
    for vietato in ("   ", "demo/personale", "café", "a b", "a:b", "demo%", ".", ".."):
        try:
            Utente.da_grezzo(vietato)
        except UtenteNonValido:
            pass
        else:
            esigi(False, "un id non valido non e' rifiutato: " + repr(vietato))
    # L'alfabeto esiste per questo: Agno non deve riscrivere il namespace.
    from agno.fs._paths import normalize_namespace

    for ammesso in ("demo", "prova_cli", "a.b-c", "utente-1"):
        identita = Utente.da_grezzo(ammesso)
        esigi(identita.id == ammesso, "un id valido e' stato rifiutato: " + repr(ammesso))
        esigi(
            normalize_namespace(namespace_utente(identita)) == namespace_utente(identita),
            "Agno riscriverebbe il namespace di " + repr(ammesso),
        )
    # La stessa regola vista dalla chat: un id vuoto esce "rifiutato" (2)
    # prima di toccare l'archivio, non con un traceback.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        codice = chat.avvia(session=SESSIONE, user="   ")
    esigi(codice == 2, "la chat non rifiuta un utente vuoto: " + repr(codice))

    # Anche `inspect` segue la tabella dei codici: "rifiutato" e non zero,
    # sia dalla funzione sia dall'alias `ares-inspect`.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        codice_inspect = inspect_learning.ispeziona(user="   ")
        codice_alias = inspect_learning.main(["--user", "   "])
    esigi(codice_inspect == 2, "inspect non rifiuta un utente vuoto: " + repr(codice_inspect))
    esigi(codice_alias == 2, "l'alias ares-inspect perde il codice di uscita: " + repr(codice_alias))

    # Il lock si prova fra processi, come il resto della contesa: lo stesso
    # archivio, due grafie, un solo turno ammesso.
    codice = (
        "from ares import config\n"
        "from ares.state.identita import Utente\n"
        "from ares.state.lock import StatoOccupato, lock_turno\n"
        "percorsi = config.leggi_percorsi()\n"
        "try:\n"
        "    with lock_turno(percorsi, Utente.da_grezzo('demo')): pass\n"
        "except StatoOccupato: pass\n"
        "else: raise RuntimeError('due grafie dello stesso utente non si contendono il lock')\n"
    )
    with lock_turno(PERCORSI, Utente.da_grezzo("Demo")):
        figlio = subprocess.run(
            [sys.executable, "-c", codice],
            cwd=config.BASE_DIR,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=30,
        )
    esigi(figlio.returncode == 0, "lock non allineato alla forma canonica: " + figlio.stderr[-400:])
    return "namespace, entita' e lock concordano su Demo/demo; il tipo rifiuta le forme non canoniche"


def identita_agente() -> str:
    """L'agente porta la forma canonica a profilo e User Memory.

    Quegli store non hanno namespace: la chiave e' `user_id`. Il figlio
    costruisce l'agente da `"  Demo  "` e riporta la chiave che conserva.
    """
    figlio = subprocess.run(
        [sys.executable, "-c", COSTRUZIONE_AGENTE],
        cwd=config.BASE_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=180,
    )
    esigi(figlio.returncode == 0, "costruzione dell'agente fallita: " + figlio.stderr[-800:])
    esigi(
        figlio.stdout.strip() == "demo",
        "l'agente conserva la grafia non canonica: " + repr(figlio.stdout.strip()),
    )
    return "l'agente porta alla chiave di profilo e memorie la forma canonica"


def id_sessione_univoci() -> str:
    """Due cartelle omonime, o lo stesso istante, non producono lo stesso id.

    Nome della cartella piu' secondi non distingue `/a/api` da `/b/api`.
    """
    quando = datetime(2026, 9, 21, 12, 0, 0)
    a = cartella.nuovo_id_sessione(Path("/progetti/a/api"), quando, suffisso="aaa111")
    b = cartella.nuovo_id_sessione(Path("/progetti/b/api"), quando, suffisso="bbb222")
    esigi(a == "api-20260921-120000-aaa111", "formato dell'id inatteso: " + repr(a))
    esigi(a != b, "cartelle omonime producono lo stesso id")
    primo = cartella.nuovo_id_sessione(Path("/progetti/a/api"), quando)
    secondo = cartella.nuovo_id_sessione(Path("/progetti/a/api"), quando)
    esigi(primo != secondo, "due avvii nello stesso istante producono lo stesso id")
    return "id distinti per cartelle omonime e nello stesso istante"


# ---------------------------------------------------------------------------
# Le prove
# ---------------------------------------------------------------------------


LOCALE = "qwen3:9b"


def preflight_pronto() -> str:
    # Modelli fissati qui e non letti dal `.env`, perche' la prova dica lo
    # stesso con configurazioni cloud o locali. Il server annuncia i nomi come
    # Ollama (`:latest` dove config.py non ha tag): e' il falso negativo che
    # `stessa_etichetta` evita, provato nei due versi.
    richiesti = {LOCALE, config.EMBEDDER_MODEL}
    modelli = [nome if ":" in nome else nome + ":latest" for nome in richiesti]
    esigi(
        any(":" not in nome for nome in richiesti),
        "nessun modello e' senza tag: la prova non sta piu' verificando la normalizzazione",
    )
    with patch.object(config, "MAIN_MODEL", LOCALE), patch.object(config, "LEARNING_MODEL", LOCALE):
        esito, testo = esegui_preflight(modelli)
    esigi(esito == 0, "preflight con tutti i modelli presenti non e' uscito con 0: " + testo)
    esigi("Ambiente pronto" in testo, "il preflight non dichiara l'ambiente pronto")
    esigi("MANCANTE" not in testo, "il preflight segnala un modello mancante che c'e'")
    # I ruoli si accumulano: main e learning sono lo stesso modello e vanno
    # mostrati su una riga sola.
    esigi(" + " in testo, "i due ruoli dello stesso modello non sono stati uniti")
    esigi("cloud" not in testo, "il preflight parla di cloud con soli modelli locali")
    return "tag :latest riconosciuto, ruoli accumulati"


def stessa_riga(testo: str, *parti: str) -> bool:
    """Vero se una riga dell'output contiene tutte le parti, in qualunque colonna."""
    return any(all(parte in riga for parte in parti) for riga in testo.splitlines())


def preflight_estrazione_cloud() -> str:
    # L'estrazione in cloud e' una scelta del `.env` separata dalla
    # conversazione: l'avviso deve dire che escono le memorie, non i prompt,
    # e con entrambi i ruoli in cloud deve restare da nominare solo l'embedder.
    cloud = "glm-5.3-flash:cloud"
    with patch.object(config, "MAIN_MODEL", "qwen3:9b"), patch.object(config, "LEARNING_MODEL", cloud):
        esito, testo = esegui_preflight(["qwen3:9b", cloud, config.EMBEDDER_MODEL])
    esigi(esito == 0, "preflight con l'estrazione cloud presente non e' uscito con 0: " + testo)
    esigi(stessa_riga(testo, cloud, "estrazione delle memorie (cloud, via ollama.com)"), "ruolo cloud non marcato")
    esigi("memorie gia' salvate" in testo, "l'avviso non dice che le memorie escono")
    esigi("escono dalla macchina" in testo, "l'avviso non dice che qualcosa esce")
    esigi("Conversazione ed embedding restano locali" in testo, "l'avviso non dice cosa resta locale")

    with patch.object(config, "MAIN_MODEL", cloud), patch.object(config, "LEARNING_MODEL", cloud):
        esito, testo = esegui_preflight([cloud, config.EMBEDDER_MODEL])
    esigi(esito == 0, "preflight con entrambi i ruoli cloud non e' uscito con 0: " + testo)
    esigi(
        "conversazione + estrazione delle memorie (cloud, via ollama.com)" in testo, "i due ruoli cloud non sono uniti"
    )
    esigi("Solo l'embedding resta locale" in testo, "con entrambi i ruoli cloud l'avviso non isola l'embedding")
    return "avviso sulle memorie, poi sul solo embedding locale"


def preflight_modello_mancante() -> str:
    # Presente il modello di conversazione, assente l'embedder: e' il caso
    # tipico, perche' l'embedder si scarica dopo e non serve al primo turno.
    # Tutto locale per costruzione: cio' che manca non deve chiedere accessi.
    with patch.object(config, "MAIN_MODEL", LOCALE), patch.object(config, "LEARNING_MODEL", LOCALE):
        esito, testo = esegui_preflight([LOCALE])
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
    with patch.object(config, "MAIN_MODEL", "glm-5.3-flash:cloud"), patch.object(config, "LEARNING_MODEL", LOCALE):
        esito, testo = esegui_preflight([LOCALE, config.EMBEDDER_MODEL])
    esigi(esito == 1, "un modello cloud mancante non ha prodotto uscita 1")
    esigi(stessa_riga(testo, "MANCANTE", "glm-5.3-flash:cloud"), "il modello cloud mancante non e' segnalato")
    esigi(testo.index("ollama signin") < testo.index("ollama pull"), "signin non precede il pull")
    return "signin suggerito prima del pull"


def preflight_json() -> str:
    # Stessi dati della tabella, stesso codice di uscita, niente testo intorno.
    with patch.object(config, "MAIN_MODEL", LOCALE), patch.object(config, "LEARNING_MODEL", LOCALE):
        esito, testo = esegui_preflight([LOCALE], argomenti=["--json"])
    dati = json.loads(testo)
    esigi(esito == 1 and dati["pronto"] is False, "il verdetto JSON non concorda con il codice di uscita")
    esigi(dati["mancanti"] == [config.EMBEDDER_MODEL], "i mancanti JSON non sono l'embedder: " + testo)
    ruoli = {voce["modello"]: voce["ruoli"] for voce in dati["modelli"]}
    esigi(ruoli[LOCALE] == ["conversazione", "estrazione delle memorie"], "i ruoli JSON non si accumulano: " + testo)
    esigi(all(not voce["cloud"] for voce in dati["modelli"]), "un modello locale e' marcato cloud nel JSON")
    return "verdetto, mancanti e ruoli come dati"


def preflight_server_spento() -> str:
    esito, testo = esegui_preflight(None)
    esigi(esito == 1, "un server irraggiungibile non ha prodotto uscita 1")
    esigi("non raggiungibile" in testo, "il server spento non e' distinto")
    esigi("ollama serve" in testo, "manca il comando per avviare il server")
    return "distinto da un modello mancante"


def backup_cli(_archivio: Path) -> str:
    """Il `main()` di `ares.backup`, sottocomando per sottocomando.

    Le funzioni le prova `backup_test.py`; qui si prova lo strato che le
    sceglie: argomenti, conferme testuali e codici di uscita.
    """

    def comando(*argomenti: str, risposta: str | None = None) -> tuple[int, str]:
        # stdout e stderr insieme: gli errori vanno su stderr, e qui si prova
        # che compaiano, non dove.
        uscita = io.StringIO()
        with ExitStack() as pila:
            pila.enter_context(patch.object(sys, "argv", ["ares-backup", *argomenti]))
            pila.enter_context(redirect_stdout(uscita))
            pila.enter_context(redirect_stderr(uscita))
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
    primo = snapshots.elenco_snapshot(PERCORSI)[-1].name

    esito, testo = comando("list")
    esigi(esito == 0, "list non riuscito: " + testo)
    esigi(primo in testo, "list non elenca lo snapshot appena creato")

    # `--json` e' per gli script: deve essere JSON puro, con gli stessi dati
    # della tabella.
    esito, testo = comando("list", "--json")
    esigi(esito == 0, "list --json non riuscito: " + testo)
    dati = json.loads(testo)
    esigi([voce["snapshot"] for voce in dati["snapshot"]] == [primo], "list --json non elenca lo snapshot: " + testo)
    esigi(dati["snapshot"][0]["type"] == "manuale", "list --json non riporta il tipo: " + testo)

    esito, testo = comando("verify", "latest")
    esigi(esito == 0, "verify latest non riuscito: " + testo)
    esigi("Snapshot valido" in testo, "verify non conferma la validita'")

    esito, testo = comando("verify", "latest", "--json")
    esigi(esito == 0, "verify --json non riuscito: " + testo)
    esigi(json.loads(testo)["snapshot_id"] == primo, "verify --json non restituisce il manifest: " + testo)

    esito, testo = comando("verify", "non-esiste")
    esigi(esito == 1, "verify di uno snapshot inesistente non e' uscito con 1")
    esigi("ERRORE:" in testo, "verify non spiega perche' ha rifiutato")

    # Uno stato occupato non e' un guasto: 3 si puo' riprovare, 1 no (lo decide
    # `codice_di`). Il lock condiviso simula la chat aperta in un'altra
    # finestra.
    quanti = len(snapshots.elenco_snapshot(PERCORSI))
    with lock_stato(PERCORSI.lock_file, esclusivo=False):
        esito, testo = comando("create")
    esigi(esito == 3, "un create con lo stato occupato non e' uscito con 3, ma con " + str(esito))
    esigi("ERRORE:" in testo, "il create bloccato non dice perche'")
    esigi(len(snapshots.elenco_snapshot(PERCORSI)) == quanti, "un create bloccato ha creato uno snapshot")

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
    esigi(esito == 2, "prune con --keep 0 non e' stato rifiutato con 2: " + str(esito))
    esigi("almeno 1" in testo, "prune non spiega il rifiuto")

    esito, testo = comando("prune", "--keep", "99")
    esigi(esito == 0, "prune senza candidati non e' uscito con 0")
    esigi("Niente da eliminare" in testo, "prune non dice che non c'e' niente da fare")

    esito, testo = comando("prune", "--keep", "1", risposta="no")
    esigi(esito == 2, "un prune annullato non e' uscito con 2")
    esigi("Prune annullato" in testo, "il prune annullato non lo dice")

    prima = len(snapshots.elenco_snapshot(PERCORSI))
    esito, testo = comando("prune", "--keep", "1", "--yes")
    esigi(esito == 0, "prune non riuscito: " + testo)
    esigi(len(snapshots.elenco_snapshot(PERCORSI)) == 1, "prune non ha conservato esattamente uno snapshot")
    esigi("Eliminati " + str(prima - 1) in testo, "prune non riporta quanti ne ha eliminati")
    return "create, list, verify, restore, prune con annullamenti e i codici 0, 1, 2 e 3"


def inspect_learning_cli() -> str:
    """L'ispezione degli archivi, che non deve scrivere niente.

    Il controllo che conta e' che l'archivio sia identico prima e dopo.
    """
    file_db = Path(PERCORSI.db_file)
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
    with patch.object(sys, "argv", argv), redirect_stderr(uscita):
        inspect_learning.main()
    esigi("Nessun file a questo percorso" in uscita.getvalue(), "un file assente non viene segnalato")

    uscita = io.StringIO()
    argv = ["ares-inspect", "--user", UTENTE, "--file", FILE_AGENTE]
    with patch.object(sys, "argv", argv), redirect_stdout(uscita):
        inspect_learning.main()
    esigi(CONTENUTO_FILE in uscita.getvalue(), "il contenuto del file non viene stampato")

    # `--prompt` chiede ad Agno il system message senza aprire un turno: deve
    # contenere sia la parte di Ares (con la cartella corrente) sia quella che
    # Agno aggiunge, e l'archivio deve restare com'era.
    uscita = io.StringIO()
    argv = ["ares-inspect", "--user", UTENTE, "--prompt"]
    with patch.object(sys, "argv", argv), redirect_stdout(uscita):
        inspect_learning.main()
    testo = uscita.getvalue()
    esigi(testo.startswith("Sei Ares"), "il prompt non comincia con la descrizione di Ares: " + repr(testo[:200]))
    # Risolta come la conserva `config`: su Windows la temp arriva col nome
    # corto (`RUNNER~1`) e il prompt porta quello espanso.
    esigi(str(PERCORSI.lavoro.resolve()) in testo, "il prompt non nomina la cartella corrente")
    esigi("<istruzioni_" in testo, "il prompt non contiene le guide degli store di apprendimento")
    esigi(prima == (file_db.stat().st_mtime_ns, file_db.stat().st_size), "--prompt ha modificato l'archivio")
    uscita = io.StringIO()
    argv = ["ares-inspect", "--user", UTENTE, "--prompt", "--modo", "piano"]
    with patch.object(sys, "argv", argv), redirect_stdout(uscita):
        inspect_learning.main()
    esigi("Modalita' piano" in uscita.getvalue(), "--prompt --modo piano non mostra il prompt di piano")
    return "cinque sezioni, archivio invariato, --file presente e assente, --prompt intero"


def chat_repl() -> str:
    """La REPL intera in un processo separato, con stdin da una pipe.

    Senza terminale `CliInput` ripiega su `input()`: si provano banner, ciclo,
    dispatch dei comandi e uscita pulita.

    Il figlio non eredita l'host Ollama chiuso di questa prova, quindi resta
    offline solo se ogni riga comincia con `/`: il resto sarebbe un messaggio
    per il modello. L'ultima asserzione verifica che nessun turno sia partito,
    perche' in CI un Ollama irraggiungibile diventa un errore stampato e la
    prova resterebbe verde per il motivo sbagliato.
    """
    # Il figlio parte dalla cartella di lavoro della prova, come un utente nel
    # proprio progetto. `BASE_DIR` contiene il codice di Ares: senza terminale
    # la REPL la rifiuterebbe.
    figlio = subprocess.run(
        [sys.executable, "-m", "ares", "--user", UTENTE, "--session", SESSIONE],
        cwd=PERCORSI.lavoro,
        env=os.environ.copy(),
        input="/aiuto\n\n/entita\n/file\n/cartella\n/sconosciuto comando\n/esci\n",
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
    esigi(str(PERCORSI.lavoro.resolve()) in testo, "il banner o /cartella non nominano la cartella di lavoro")
    esigi("Comando sconosciuto: /sconosciuto" in testo, "il comando ignoto non e' stato riconosciuto come tale")
    # Una riga senza `/` in testa sarebbe un messaggio al modello. `Ares` a
    # schermo compare solo nell'intestazione di una risposta del modello,
    # quindi la sua assenza prova che nessun turno e' partito.
    esigi("Ares" not in testo, "la REPL ha aperto un turno col modello: " + testo[-400:])
    return "banner, comandi, riga vuota, comando ignoto e uscita"


# ---------------------------------------------------------------------------
# Il ciclo della REPL, in questo processo
# ---------------------------------------------------------------------------
# `chat_repl` da fuori puo' mandare solo comandi. Qui una `run_turn_cycle`
# finta prende il posto del modello, per provare cio' che la REPL fa intorno
# alla risposta: un'eccezione non chiude la sessione, un Ctrl-C e' distinto
# da un guasto, una pausa irrisolta viene detta.


def _piatto(testo: str) -> str:
    """Il testo a schermo senza gli a-capo della larghezza del terminale.

    In prova Rich manda a capo a 80 colonne: senza questo, una frase cercata per
    intero fallirebbe per la larghezza invece che per il contenuto.
    """
    return " ".join(testo.split())


class FintoInput:
    """`CliInput` ridotto a cio' che il ciclo usa: una coda di righe.

    Ogni elemento e' una riga oppure un'eccezione da sollevare: EOF e Ctrl-C
    sono le due uscite del prompt.
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

    Un dict passerebbe senza errori e conterebbe zero.
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

    Turno normale, pausa che la CLI non sa chiedere, Ctrl-C e guasto. Si
    verifica che Ctrl-C e guasto restino distinti: se `except Exception`
    inghiottisse il `KeyboardInterrupt`, nessun'altra prova lo direbbe.
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
        risposta = chat.esegui_turno(PERCORSI, object(), "ciao", input_cli, config.leggi_politica())
    esigi(risposta is not None, "un turno riuscito non restituisce la risposta")

    # Pausa che il client non sa risolvere: il ciclo si ferma e lo dice.
    uscita = io.StringIO()
    with (
        patch.object(chat, "run_turn_cycle", lambda *a, **k: FintaRisposta(is_paused=True)),
        redirect_stdout(uscita),
    ):
        chat.esegui_turno(PERCORSI, object(), "ciao", input_cli, config.leggi_politica())
    esigi("in pausa" in _piatto(uscita.getvalue()), "una pausa irrisolta resta muta")

    # Ctrl-C fuori dal turno: nessun apprendimento, e non e' un errore.
    def ciclo_interrotto(*a, **k):
        raise KeyboardInterrupt

    uscita = io.StringIO()
    with patch.object(chat, "run_turn_cycle", ciclo_interrotto), redirect_stdout(uscita):
        esigi(
            chat.esegui_turno(PERCORSI, object(), "ciao", input_cli, config.leggi_politica()) is None,
            "un'interruzione non restituisce None",
        )
    testo = _piatto(uscita.getvalue())
    esigi("Interrotto" in testo, "l'interruzione non viene detta")
    esigi("fallito" not in testo, "un Ctrl-C viene presentato come un guasto")

    # Guasto: il tipo dell'eccezione a schermo, e la sessione resta aperta.
    def ciclo_rotto(*a, **k):
        raise RuntimeError("archivio irraggiungibile")

    uscita = io.StringIO()
    with patch.object(chat, "run_turn_cycle", ciclo_rotto), redirect_stdout(uscita):
        esigi(
            chat.esegui_turno(PERCORSI, object(), "ciao", input_cli, config.leggi_politica()) is None,
            "un guasto non restituisce None",
        )
    testo = _piatto(uscita.getvalue())
    esigi("RuntimeError" in testo, "il tipo dell'errore non compare")
    esigi("archivio irraggiungibile" in testo, "il messaggio dell'errore non compare")
    esigi("sessione resta aperta" in testo, "non viene detto che la sessione sopravvive")

    # L'eco: la differenza fra le fotografie compare sotto la risposta. La
    # prima lettura deve precedere il turno (`update_user_memory` scrive
    # durante il run), quindi la finta registra quando viene chiamata.
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
            chat.esegui_turno(
                PERCORSI, object(), "ricorda che preferisco config.py", input_cli, config.leggi_politica()
            )
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
        chat.esegui_turno(
            PERCORSI, object(), "ricorda che preferisco config.py", InputInterrotto([]), config.leggi_politica()
        )
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
        chat.esegui_turno(PERCORSI, object(), "ciao", input_cli, config.leggi_politica())
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
        chat.esegui_turno(PERCORSI, object(), "ciao", input_cli, config.leggi_politica())
    esigi(letture == [], "con l'eco spento l'archivio viene letto lo stesso")
    esigi("appreso" not in _piatto(uscita.getvalue()), "con l'eco spento compare una riga di eco")
    return (
        "turno, pausa irrisolta, Ctrl-C e guasto restano quattro esiti distinti; "
        "l'eco avvolge il turno e un no lo riporta indietro"
    )


def chat_memoria_protetta() -> str:
    """Store SQLite reali: contesa fra processi, rollback e scritture prima di un errore."""
    from agno.db.sqlite import SqliteDb
    from agno.learn import UserMemoryConfig

    from ares.agent.learning import AresUserMemoryStore

    db = SqliteDb(db_file=str(RADICE_PROVA / "memoria-turni.db"))
    store = AresUserMemoryStore(config=UserMemoryConfig(db=db))
    utente = "utente-turni"
    agent = SimpleNamespace(
        user_id=utente,
        id="chat-a",
        learning_machine=SimpleNamespace(user_profile_store=None, user_memory_store=store),
    )
    store.add_memory(user_id=utente, memory="confermata nella chat B")
    fotografia_vera = chat.istantanea
    ripristino_vero = chat.ripristina
    fasi = []

    def esigi_contesa(fase):
        fasi.append(fase)
        # Due processi, non due descrittori nella stessa chat: stesso utente
        # occupato, altro utente libero nello stesso archivio.
        codice = (
            "from ares import config\n"
            "from ares.state.identita import Utente\n"
            "from ares.state.lock import lock_turno, StatoOccupato\n"
            "percorsi = config.leggi_percorsi()\n"
            "try:\n"
            "    with lock_turno(percorsi, Utente.da_grezzo('utente-turni')): pass\n"
            "except StatoOccupato: pass\n"
            "else: raise RuntimeError('turno concorrente ammesso')\n"
            "with lock_turno(percorsi, Utente.da_grezzo('altro-utente')): pass\n"
        )
        figlio = subprocess.run([sys.executable, "-c", codice], capture_output=True, text=True, timeout=30)
        esigi(figlio.returncode == 0, fase + ": " + figlio.stderr)

    def fotografia(agent):
        esigi_contesa("istantanea")
        return fotografia_vera(agent)

    def ripristino(agent, stato):
        esigi_contesa("ripristino")
        return ripristino_vero(agent, stato)

    class InputProtetto(FintoInput):
        def ask(self, etichetta, *, muted=False):
            esigi_contesa("conferma")
            return "n"

    try:
        for errore in (None, KeyboardInterrupt, RuntimeError):
            fasi.clear()

            def ciclo(*args, errore=errore, **kwargs):
                esigi_contesa("turno")
                store.add_memory(user_id=utente, memory="da rifiutare nella chat A")
                if errore is not None:
                    raise errore("guasto sintetico")
                return FintaRisposta()

            uscita = io.StringIO()
            with (
                patch.object(chat, "istantanea", fotografia),
                patch.object(chat, "ripristina", ripristino),
                patch.object(chat, "run_turn_cycle", ciclo),
                patch.object(config, "MOSTRA_APPRENDIMENTI", True),
                patch.object(config, "CONFERMA_APPRENDIMENTI", True),
                redirect_stdout(uscita),
            ):
                risposta = chat.esegui_turno(PERCORSI, agent, "ricorda", InputProtetto([]), config.leggi_politica())
            esigi((risposta is None) == (errore is not None), "esito del turno errato")
            esigi(fasi == ["istantanea", "turno", "conferma", "ripristino"], "lock incompleto: " + repr(fasi))
            contenuto = [m["content"] for m in store.get(user_id=utente).memories]
            esigi(contenuto == ["confermata nella chat B"], "memoria precedente persa o nuova non annullata")
            esigi("Non e' stato appreso" not in uscita.getvalue(), "rassicurazione falsa dopo scrittura")
            with lock_turno(PERCORSI, Utente.da_grezzo(utente)):
                pass  # Anche dopo errore o Ctrl-C il lock deve essere libero.

        # La contesa si rileva prima di qualsiasi lettura o inferenza,
        # anche quando l'eco e' disabilitato.
        with (
            lock_turno(PERCORSI, Utente.da_grezzo(utente)),
            patch.object(config, "MOSTRA_APPRENDIMENTI", False),
            patch.object(chat, "run_turn_cycle") as ciclo_spia,
            patch.object(chat, "istantanea") as lettura_spia,
        ):
            try:
                chat.esegui_turno(PERCORSI, agent, "non deve partire", FintoInput([]), config.leggi_politica())
            except StatoOccupato:
                pass
            else:
                esigi(False, "turno occupato avviato")
            esigi(not ciclo_spia.called and not lettura_spia.called, "contesa rilevata troppo tardi")

        # La REPL resta aperta; il comando senza terminale usa invece il
        # codice condiviso "occupato", senza avviare il modello.
        with (
            lock_turno(PERCORSI, Utente.da_grezzo(utente)),
            patch.object(chat, "build_assistant", lambda *a, **k: agent),
            patch.object(chat, "CliInput", lambda **k: FintoInput(["riprova piu' tardi"])),
            patch.object(chat, "run_turn_cycle") as ciclo_spia,
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            esigi(chat.avvia(session=SESSIONE, user=utente) == 0, "la contesa ha chiuso la REPL con errore")
            uscita_pipe, errori_pipe = io.StringIO(), io.StringIO()
            with redirect_stdout(uscita_pipe), redirect_stderr(errori_pipe):
                codice = chat.avvia(session=SESSIONE, user=utente, prompt="ciao")
            esigi(codice == 3, "la pipe non esce con occupato")
            esigi(uscita_pipe.getvalue() == "", "la contesa per utente sporca stdout")
            esigi("turno in corso" in errori_pipe.getvalue(), "la contesa per utente manca su stderr")
            esigi(not ciclo_spia.called, "la contesa della CLI ha avviato un turno")
    finally:
        db.db_engine.dispose()
    return "lock fra processi fino al rollback; errori e Ctrl-C mostrano e annullano le scritture reali"


def chat_ciclo() -> str:
    """Il giro completo di `_esegui_chat` con un modello finto.

    Copre cio' che `chat_repl` non raggiunge da fuori: riga vuota, messaggio,
    `--metriche`, avvisi su cronologia degradata e modello cloud, promemoria di
    backup e le due uscite dal prompt.
    """
    turni: list[str] = []

    def ciclo(agent, testo, *, on_event, resolve_pause):
        turni.append(testo)
        return FintaRisposta(metriche=FinteMetriche())

    input_cli = FintoInput(["", "  ", "ciao Ares", KeyboardInterrupt], history_warning="disco in sola lettura")

    uscita = io.StringIO()
    with (
        patch.object(chat, "build_assistant", lambda *a, **k: object()),
        patch.object(chat, "CliInput", lambda **k: input_cli),
        patch.object(chat, "run_turn_cycle", ciclo),
        patch.object(chat, "promemoria_backup", lambda *a, **k: ["Ultimo backup: mai", "Esegui ares backup create"]),
        patch.object(config, "MAIN_MODEL", "glm-5.3-flash:cloud"),
        redirect_stdout(uscita),
    ):
        chat._esegui_chat(session=SESSIONE, user=UTENTE, metriche=True)

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
        patch.object(chat, "build_assistant", lambda *a, **k: object()),
        patch.object(chat, "CliInput", lambda **k: input_cli),
        patch.object(chat, "run_turn_cycle", ciclo),
        patch.object(chat, "promemoria_backup", lambda *a, **k: []),
        patch.object(config, "MAIN_MODEL", "qwen3:9b"),
        patch.object(config, "LEARNING_MODEL", "qwen3:9b"),
        patch.object(config, "MOSTRA_METRICHE", False),
        redirect_stdout(uscita),
    ):
        chat._esegui_chat(session=SESSIONE, user=UTENTE)
    testo = _piatto(uscita.getvalue())
    esigi("escono dalla macchina" not in testo, "un modello locale mostra l'avviso del cloud")
    esigi("tok" not in testo, "le metriche compaiono senza che siano state chieste")

    # L'estrazione in cloud con la conversazione locale e' l'altro avviso:
    # deve nominare le memorie, perche' sono quelle a uscire, non i prompt.
    input_cli = FintoInput(["ciao Ares"])
    uscita = io.StringIO()
    with (
        patch.object(chat, "build_assistant", lambda *a, **k: object()),
        patch.object(chat, "CliInput", lambda **k: input_cli),
        patch.object(chat, "run_turn_cycle", ciclo),
        patch.object(chat, "promemoria_backup", lambda *a, **k: []),
        patch.object(config, "MAIN_MODEL", "qwen3:9b"),
        patch.object(config, "LEARNING_MODEL", "glm-5.3-flash:cloud"),
        patch.object(config, "MOSTRA_METRICHE", False),
        redirect_stdout(uscita),
    ):
        chat._esegui_chat(session=SESSIONE, user=UTENTE)
    testo = _piatto(uscita.getvalue())
    esigi("memorie gia' salvate" in testo, "l'estrazione cloud non avvisa che le memorie escono")
    esigi("escono dalla macchina" in testo, "l'estrazione cloud non avvisa che qualcosa esce")
    return "riga vuota, turno, metriche, avvisi d'avvio e le due uscite dal prompt"


def chat_cartella() -> str:
    """La cartella si decide prima di tutto: se non va, niente agente e niente banner.

    Tre avvii: cartella inesistente, cartella rifiutata da `autorizza`, cartella
    buona con `--workspace`. Nel terzo i percorsi ricevuti da `build_assistant`
    devono puntare alla cartella scelta, e il banner nominarla con il suo
    ARES.md.
    """
    originale = PERCORSI.lavoro
    costruiti: list[dict] = []

    def costruisci(percorsi, impostazioni, politica, utente, **argomenti):
        argomenti["percorsi"] = percorsi
        argomenti["impostazioni"] = impostazioni
        argomenti["politica"] = politica
        costruiti.append(argomenti)
        return object()

    uscita = io.StringIO()
    with patch.object(chat, "build_assistant", costruisci), redirect_stdout(uscita):
        esito = chat._esegui_chat(session=SESSIONE, user=UTENTE, workspace=Path(originale) / "non-esiste")
    testo = _piatto(uscita.getvalue())
    esigi(esito == 1, "una cartella inesistente non esce con 1: " + str(esito))
    esigi("non esiste" in testo, "una cartella inesistente non viene detta: " + repr(testo))
    esigi(not costruiti, "l'agente e' stato costruito su una cartella inesistente")

    uscita = io.StringIO()
    with (
        patch.object(chat, "build_assistant", costruisci),
        patch.object(chat.cartella, "autorizza", lambda percorso, percorsi, *, esplicito: False),
        redirect_stdout(uscita),
    ):
        esito = chat._esegui_chat(session=SESSIONE, user=UTENTE)
    esigi(esito == 2, "una cartella rifiutata non esce con 2: " + str(esito))
    esigi(not costruiti, "l'agente e' stato costruito su una cartella rifiutata")
    esigi("ARES" not in uscita.getvalue(), "il banner compare dopo un rifiuto")

    progetto = RADICE_PROVA / "progetto-chat"
    progetto.mkdir()
    (progetto / "ARES.md").write_text("Regole del progetto.\n", encoding="utf-8")
    # `/cartella` stampa il percorso su una riga sola anche in una pipe; il
    # banner a 80 colonne lo manda a capo se e' lungo, come la temp di Windows.
    input_cli = FintoInput(["/cartella", KeyboardInterrupt])
    uscita = io.StringIO()
    with (
        patch.object(chat, "build_assistant", costruisci),
        patch.object(chat, "CliInput", lambda **k: input_cli),
        patch.object(chat, "promemoria_backup", lambda *a, **k: []),
        redirect_stdout(uscita),
    ):
        chat._esegui_chat(session=SESSIONE, user=UTENTE, workspace=progetto)
    scelta = costruiti[0]["percorsi"].lavoro
    testo = _piatto(uscita.getvalue())
    esigi(scelta == progetto.resolve(), "--workspace non ha cambiato la cartella di lavoro: " + str(scelta))
    esigi(len(costruiti) == 1, "l'agente non e' stato costruito una volta sola: " + str(len(costruiti)))
    esigi(
        costruiti[0]["impostazioni"] == IMPOSTAZIONI,
        "l'agente non ha ricevuto le impostazioni della conversazione",
    )
    esigi(
        costruiti[0]["politica"] == POLITICA,
        "l'agente non ha ricevuto la politica della conversazione",
    )
    esigi(str(progetto.resolve()) in testo, "/cartella non nomina la cartella scelta: " + repr(testo))
    esigi(progetto.name in testo.split("Cartella di lavoro")[0], "il banner non nomina la cartella: " + repr(testo))
    esigi("ARES.md" in testo, "il banner non dice che c'e' un ARES.md: " + repr(testo))

    # Un ARES.md che e' un link fuori dalla cartella non entra nel prompt,
    # quindi non va nominato nemmeno dal banner o da `/cartella`: la regola e'
    # una sola (`percorso_istruzioni`). Il link si prova dove si puo' creare.
    fuori = RADICE_PROVA / "regole-fuori.txt"
    fuori.write_text("roba d'altri\n", encoding="utf-8")
    try:
        (progetto / "ARES.md").unlink()
        (progetto / "ARES.md").symlink_to(fuori)
    except OSError:
        pass
    else:
        input_cli = FintoInput(["/cartella", KeyboardInterrupt])
        uscita = io.StringIO()
        with (
            patch.object(chat, "build_assistant", costruisci),
            patch.object(chat, "CliInput", lambda **k: input_cli),
            patch.object(chat, "promemoria_backup", lambda *a, **k: []),
            redirect_stdout(uscita),
        ):
            chat._esegui_chat(session=SESSIONE, user=UTENTE, workspace=progetto)
        testo = _piatto(uscita.getvalue())
        esigi(
            "ARES.md" not in testo.split("Cartella di lavoro")[0],
            "il banner nomina un ARES.md che punta fuori: " + repr(testo),
        )
        esigi("nessun ARES.md" in testo, "/cartella non dice che l'ARES.md e' fuori: " + repr(testo))
    return "cartella inesistente, rifiutata e scelta con --workspace, ARES.md fuori dalla cartella"


def chat_sessioni() -> str:
    """Senza `--session`: una conversazione nuova, `resume`, `--scegli` e `-p`.

    L'agente e' finto ma il database e' vero, con le sessioni seminate come le
    scrive `build_assistant`. Una sessione nata altrove non va ripresa.
    """
    from agno.session.agent import AgentSession

    from ares.agent.runtime import build_db

    costruiti: list[dict] = []

    def costruisci(percorsi, impostazioni, politica, utente, **argomenti):
        argomenti["percorsi"] = percorsi
        argomenti["impostazioni"] = impostazioni
        argomenti["politica"] = politica
        costruiti.append(argomenti)
        return object()

    def avvio(**argomenti) -> tuple[int, str]:
        uscita = io.StringIO()
        with (
            patch.object(chat, "build_assistant", costruisci),
            patch.object(chat, "CliInput", lambda **k: FintoInput([KeyboardInterrupt])),
            patch.object(chat, "promemoria_backup", lambda *a, **k: []),
            patch.object(sys, "stdin", io.StringIO()),
            redirect_stdout(uscita),
            redirect_stderr(uscita),
        ):
            esito = chat._esegui_chat(user=UTENTE, **argomenti)
        return esito, _piatto(uscita.getvalue())

    esito, testo = avvio()
    nome = costruiti[-1]["session_id"]
    esigi(esito == 0 and nome.startswith(PERCORSI.lavoro.name + "-"), "l'id nuovo non viene dalla cartella: " + nome)
    esigi("(nuova)" in testo, "il banner non dice che la conversazione e' nuova: " + repr(testo))

    esito, testo = avvio(riprendi=True)
    esigi(esito == 2 and len(costruiti) == 1, "resume senza conversazioni ha aperto qualcosa o non esce con 2")
    esigi("Nessuna conversazione in questa cartella" in testo, "resume a vuoto non lo dice: " + repr(testo))

    # Seminate nel database della suite e tolte alla fine: `sessioni parziale`,
    # piu' avanti, conta le sessioni dell'archivio e non deve trovarle.
    db = build_db(PERCORSI)
    qui = str(PERCORSI.lavoro)
    seminate = (
        ("ripresa-vecchia", 1000, qui),
        ("ripresa-nuova", 2000, qui),
        ("altrove", 3000, "/un/altro/progetto"),
    )
    try:
        for identificativo, quando, dove in seminate:
            db.upsert_session(
                AgentSession(session_id=identificativo, user_id=UTENTE, metadata={"cartella": dove}, created_at=quando)
            )
        esito, testo = avvio(riprendi=True)
        esigi(esito == 0 and costruiti[-1]["session_id"] == "ripresa-nuova", "resume non riapre l'ultima di qui")
        esigi("Riprendo" in testo and "(ripresa)" in testo, "resume non dice cosa riprende: " + repr(testo))
        esigi("altre 1" in testo, "resume non conta le altre conversazioni della cartella: " + repr(testo))

        with patch("builtins.input", lambda _etichetta="": "2"):
            esito, testo = avvio(riprendi=True, scegli=True)
        esigi(esito == 0 and costruiti[-1]["session_id"] == "ripresa-vecchia", "--scegli non apre la scelta")
        with patch("builtins.input", lambda _etichetta="": ""):
            esito, testo = avvio(riprendi=True, scegli=True)
        esigi(esito == 2 and "Nessuna conversazione ripresa" in testo, "rinunciare alla scelta apre qualcosa")

        # `resume -p`: un turno solo sull'ultima conversazione di qui, senza
        # memoria da scrivere come ogni `-p`; con `--scegli` non parte.
        ripresi: list[str] = []

        def un_turno(agent, testo, *, on_event, resolve_pause):
            ripresi.append(testo)
            return FintaRisposta()

        with patch.object(chat, "run_turn_cycle", un_turno):
            esito, testo = avvio(riprendi=True, prompt="continua")
        esigi(
            esito == 0 and ripresi == ["continua"] and costruiti[-1]["session_id"] == "ripresa-nuova",
            "resume -p non fa il turno sull'ultima conversazione di qui: " + repr((esito, ripresi)),
        )
        esigi(costruiti[-1]["interattivo"] is False, "resume -p costruisce un agente che scrive in memoria")
        prima = len(costruiti)
        esito, testo = avvio(riprendi=True, scegli=True, prompt="continua")
        esigi(esito == 2 and "--scegli" in testo and len(costruiti) == prima, "resume --scegli -p non viene rifiutato")
    finally:
        db.delete_sessions([identificativo for identificativo, _, _ in seminate], user_id=UTENTE)

    turni: list[str] = []

    def ciclo(agent, testo, *, on_event, resolve_pause):
        turni.append(testo)
        # Un turno con uno strumento e una risposta: in una pipe la risposta
        # e' l'unica cosa che uno script vuole trovare su stdout.
        on_event(TurnEvent(kind=TurnEventKind.TOOL_STARTED, tool=SimpleNamespace(tool_name="workspace_list_files")))
        on_event(TurnEvent(kind=TurnEventKind.CONTENT, content="risposta per la pipe"))
        return FintaRisposta(metriche=FinteMetriche())

    uscita, errori = io.StringIO(), io.StringIO()
    with (
        patch.object(chat, "build_assistant", costruisci),
        patch.object(chat, "run_turn_cycle", ciclo),
        patch.object(sys, "stdin", io.StringIO("dati dalla pipe\n")),
        redirect_stdout(uscita),
        redirect_stderr(errori),
    ):
        esito = chat._esegui_chat(user=UTENTE, prompt="riassumi", metriche=True)
    esigi(esito == 0 and turni == ["riassumi\n\ndati dalla pipe"], "-p non unisce domanda e stdin: " + repr(turni))
    esigi("ARES" not in uscita.getvalue() + errori.getvalue(), "-p stampa il banner")
    esigi(
        uscita.getvalue() == "risposta per la pipe\n",
        "-p mette altro oltre la risposta su stdout: " + repr(uscita.getvalue()),
    )
    contorno = errori.getvalue()
    esigi(
        "Ares" in contorno and "workspace_list_files" in contorno and "turno " in contorno,
        "-p non manda chi parla, strumenti e metriche su stderr: " + repr(contorno),
    )
    esigi(UI.console is not UI.stderr, "-p lascia la console dirottata su stderr dopo il turno")
    # In questa prova stdin e' sempre una pipe, quindi ogni costruzione nasce
    # senza memoria da scrivere, `-p` compreso: il caso con terminale e' in
    # `chat non presidiato`.
    esigi(costruiti[-1]["interattivo"] is False, "-p costruisce un agente che scrive in memoria")
    esigi(costruiti[0]["interattivo"] is False, "una chat senza terminale costruisce un agente che scrive memorie")
    esigi(costruiti[-1]["modo"] == config.MODO_PREDEFINITO, "-p non passa la modalita' predefinita")
    # `piano` non lascia tracce: con `-p` passa, ed e' l'altra meta' della
    # regola che rifiuta `auto` e `modifiche`.
    with (
        patch.object(chat, "build_assistant", costruisci),
        patch.object(chat, "run_turn_cycle", ciclo),
        patch.object(sys, "stdin", io.StringIO()),
        redirect_stdout(io.StringIO()),
        redirect_stderr(io.StringIO()),
    ):
        esito_piano = chat._esegui_chat(user=UTENTE, prompt="riassumi", modo="piano")
    esigi(esito_piano == 0 and costruiti[-1]["modo"] == "piano", "-p --modo piano viene rifiutato")
    # `auto` con `-p` non parte, e non tocca niente: esce prima della cartella.
    prima = len(costruiti)
    esito, testo = avvio(prompt="riassumi", modo="auto")
    esigi(esito == 2 and "auto" in testo and len(costruiti) == prima, "-p --modo auto non viene rifiutato con 2")
    # `modifiche` scrive file senza conferma: con `-p` e' la stessa porta
    # aperta, solo un giorno dopo. Rifiutata anche lei, prima della cartella.
    esito, testo = avvio(prompt="riassumi", modo="modifiche")
    esigi(
        esito == 2 and "modifiche" in testo and len(costruiti) == prima, "-p --modo modifiche non viene rifiutato con 2"
    )
    esito, testo = avvio(modo="piano")
    esigi(esito == 0 and costruiti[-1]["modo"] == "piano" and "piano" in testo, "--modo piano non arriva al banner")
    return "id dalla cartella, resume a vuoto e sull'ultima di qui, --scegli, -p con stdin senza memoria"


def migrazione_stato() -> str:
    """`ares migrate`: lo stato di un clone precedente passa in ~/.ares, e la chat aspetta.

    Si provano spostamento, idempotenza, rifiuto di una destinazione piena, e
    che la chat non parta finche' lo stato e' ancora nel vecchio posto.
    """
    from ares.ops import migrazione

    radice = RADICE_PROVA / "migrazione"
    vecchio_tmp = radice / "clone" / "tmp"
    vecchio_backup = radice / "ares-backup"
    casa = radice / "casa" / ".ares"
    (vecchio_tmp / "lancedb").mkdir(parents=True)
    (vecchio_tmp / "kairos.db").write_text("db", encoding="utf-8")
    (vecchio_backup / "snap").mkdir(parents=True)
    (vecchio_backup / "snap" / "manifest.json").write_text("{}", encoding="utf-8")

    costruiti: list[dict] = []

    def costruisci(percorsi, impostazioni, politica, utente, **argomenti):
        argomenti["percorsi"] = percorsi
        argomenti["impostazioni"] = impostazioni
        argomenti["politica"] = politica
        costruiti.append(argomenti)
        return object()

    def migra() -> tuple[int, str]:
        uscita = io.StringIO()
        with redirect_stdout(uscita), redirect_stderr(uscita):
            esito = migrazione.migra()
        return esito, _piatto(uscita.getvalue())

    # Si sostituiscono i percorsi al confine del processo: da qui anche la chat
    # li legge, come con `ARES_HOME` puntato alla casa della prova.
    nuovo = replace(PERCORSI, home=casa, stato=casa / "stato", backup=casa / "backup")
    with (
        patch.multiple(config, VECCHIO_TMP_DIR=vecchio_tmp, VECCHIO_BACKUP_DIR=vecchio_backup),
        patch.object(config, "leggi_percorsi", lambda: nuovo),
    ):
        righe = migrazione.avviso(nuovo)
        esigi(
            len(righe) == 4 and "migrate" in righe[-1], "l'avviso non elenca le due parti e il rimedio: " + repr(righe)
        )

        uscita = io.StringIO()
        with patch.object(chat, "build_assistant", costruisci), redirect_stdout(uscita):
            esito = chat._esegui_chat(user=UTENTE)
        esigi(esito == 1 and not costruiti, "la chat e' partita con lo stato ancora nel posto di prima")
        esigi("migrate" in uscita.getvalue(), "la chat non dice come spostare lo stato: " + repr(uscita.getvalue()))

        # Il vecchio lock si toglie mentre e' ancora tenuto: fra `close` e
        # `unlink` un altro processo potrebbe prenderlo. La sonda guarda se il
        # file c'e' ancora al rilascio. Su Windows un file aperto non si
        # cancella, quindi li' non si prova.
        vecchio_lock = vecchio_tmp.with_name(vecchio_tmp.name + ".lock")
        esistenza_al_rilascio: dict[str, bool] = {}
        lock_originale = migrazione.lock_stato

        @contextmanager
        def lock_spia(percorso: Path, *, esclusivo: bool, bloccante: bool = False):
            with lock_originale(percorso, esclusivo=esclusivo, bloccante=bloccante):
                yield
                esistenza_al_rilascio[str(percorso)] = Path(percorso).exists()

        with patch.object(migrazione, "lock_stato", lock_spia):
            esito, testo = migra()
        esigi(esito == 0, "la migrazione non e' riuscita: " + testo)
        if os.name == "posix":
            esigi(
                esistenza_al_rilascio.get(str(vecchio_lock)) is False,
                "il vecchio lock era ancora li' quando il lock e' stato rilasciato",
            )
        esigi("Spostato lo stato" in testo and "Spostato i backup" in testo, "non dice cosa ha spostato: " + testo)
        esigi((casa / "stato" / "kairos.db").read_text(encoding="utf-8") == "db", "il database non e' arrivato")
        esigi((casa / "stato" / "lancedb").is_dir(), "l'indice non e' arrivato")
        esigi((casa / "backup" / "snap" / "manifest.json").is_file(), "gli snapshot non sono arrivati")
        esigi(not vecchio_tmp.exists() and not vecchio_backup.exists(), "il vecchio posto non e' vuoto")
        esigi(not (vecchio_tmp.parent / "tmp.lock").exists(), "il vecchio lock e' rimasto")
        if os.name == "posix":
            esigi((casa.stat().st_mode & 0o777) == 0o700, "~/.ares non e' privata")
            esigi(((casa / "stato").stat().st_mode & 0o777) == 0o700, "lo stato spostato non e' privato")
        esigi(migrazione.avviso(nuovo) == [], "l'avviso resta dopo la migrazione")

        esito, testo = migra()
        esigi(esito == 0 and "Niente da spostare" in testo, "la seconda migrazione non e' un no-op: " + testo)

        # Il vecchio posto si riempie di nuovo mentre il nuovo e' pieno: non
        # si tocca niente e lo si dice, e la chat non si ferma.
        vecchio_tmp.mkdir(parents=True)
        (vecchio_tmp / "kairos.db").write_text("altro", encoding="utf-8")
        esito, testo = migra()
        esigi(esito == 0 and "contiene gia' dei dati" in testo, "un conflitto non viene detto: " + testo)
        esigi((vecchio_tmp / "kairos.db").exists(), "un conflitto ha spostato o cancellato qualcosa")
        esigi((casa / "stato" / "kairos.db").read_text(encoding="utf-8") == "db", "un conflitto ha sovrascritto")
        esigi(migrazione.avviso(nuovo) == [], "un conflitto ferma la chat")

        # Fra filesystem diversi `os.rename` non unisce: `_sposta` passa da una
        # sorella temporanea e solo la rinomina la rende visibile. Si simula
        # l'EXDEV, perche' la prova gira su un filesystem solo.
        ponte = radice / "ponte"
        vecchio_ponte = ponte / "vecchio"
        vecchio_ponte.mkdir(parents=True)
        (vecchio_ponte / "kairos.db").write_text("ponte", encoding="utf-8")
        casa_ponte = ponte / "casa" / ".ares"
        nuovi = replace(PERCORSI, home=casa_ponte, stato=casa_ponte / "stato", backup=casa_ponte / "backup")
        with (
            patch.multiple(config, VECCHIO_TMP_DIR=vecchio_ponte, VECCHIO_BACKUP_DIR=ponte / "backup-vuoto"),
            patch.object(config, "leggi_percorsi", lambda: nuovi),
            # `os.rename` dirottato: la via veloce fallisce e tocca alla copia.
            patch.object(migrazione.os, "rename", side_effect=OSError(errno.EXDEV, "cross-device")),
        ):
            esito, testo = migra()
        esigi(esito == 0, "la migrazione fra filesystem non riesce: " + testo)
        esigi((casa_ponte / "stato" / "kairos.db").read_text(encoding="utf-8") == "ponte", "la copia non e' arrivata")
        esigi(not vecchio_ponte.exists(), "il vecchio non e' stato rimosso dopo la copia")
        esigi(not (casa_ponte / ".stato-migrazione").exists(), "la sorella temporanea e' rimasta")

        # Un guasto a meta' copia lascia intatto il vecchio, non crea il nuovo,
        # e `avviso` ferma la chat: niente stato dimezzato.
        vecchio_rotto = ponte / "rotto"
        vecchio_rotto.mkdir()
        (vecchio_rotto / "kairos.db").write_text("rotto", encoding="utf-8")
        casa_rotta = ponte / "casa-rottura" / ".ares"
        rotti = replace(PERCORSI, home=casa_rotta, stato=casa_rotta / "stato", backup=casa_rotta / "backup")

        def copia_a_meta(src: Path, dst: Path, **kwargs: object) -> None:
            Path(dst).mkdir(parents=True, exist_ok=True)
            (Path(dst) / "kairos.db").write_text("meta", encoding="utf-8")
            raise OSError("disco pieno")

        with (
            patch.multiple(config, VECCHIO_TMP_DIR=vecchio_rotto, VECCHIO_BACKUP_DIR=ponte / "backup-vuoto2"),
            patch.object(config, "leggi_percorsi", lambda: rotti),
            patch.object(migrazione.os, "rename", side_effect=OSError(errno.EXDEV, "cross-device")),
            patch.object(migrazione.shutil, "copytree", copia_a_meta),
        ):
            esito, testo = migra()
        esigi(esito == 1, "un guasto a meta' copia non viene detto: " + testo)
        esigi(not (casa_rotta / "stato").exists(), "un guasto a meta' copia ha lasciato uno stato incompleto")
        esigi((vecchio_rotto / "kairos.db").read_text(encoding="utf-8") == "rotto", "il vecchio e' stato perso")
        esigi(migrazione.avviso(rotti) != [], "dopo il guasto la chat non si ferma")
    return "spostamento sotto lock, idempotenza, conflitto non toccato, chat ferma finche' serve, copia fra filesystem"


def chat_residui() -> str:
    """Un restore rimasto a meta' viene detto all'avvio, e solo allora.

    Il residuo e' una directory vera con il nome usato dal restore. Si prova
    anche senza residuo: un avviso sempre presente smette di essere letto.
    """
    stato = PERCORSI.stato.resolve()
    residuo = stato.with_name("." + stato.name + "-precedente-deadbeef")

    def avvia() -> str:
        uscita = io.StringIO()
        with (
            patch.object(chat, "build_assistant", lambda *a, **k: object()),
            patch.object(chat, "CliInput", lambda **k: FintoInput([])),
            patch.object(chat, "promemoria_backup", lambda *a, **k: []),
            redirect_stdout(uscita),
        ):
            chat._esegui_chat(session=SESSIONE, user=UTENTE)
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

    La cancellazione non e' atomica (payload, contesti e verifiche vengono dopo
    la transazione di Agno). Due guasti, prima e dopo la cancellazione: il
    conteggio deve venire dall'archivio, non da dove ci si e' fermati.
    """
    from agno.session.agent import AgentSession

    db = maintenance.build_db(PERCORSI)
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
    esigi("pre-session-prune" in errori and "ares backup restore" in errori, "lo snapshot da cui tornare non compare")
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

    Un backup in corso o un Ctrl-C mentre si apre l'archivio devono produrre un
    messaggio, non un traceback.
    """
    uscita = io.StringIO()
    codice = 0
    with (
        patch.object(chat, "lock_stato", lambda *_, **__: (_ for _ in ()).throw(StatoOccupato("backup in corso"))),
        redirect_stderr(uscita),
    ):
        try:
            chat.main()
        except SystemExit as fine:
            codice = int(fine.code or 0)
    testo = _piatto(uscita.getvalue())
    esigi(codice == 3, "un archivio occupato non esce con 3: " + str(codice))
    esigi("Impossibile avviare Ares" in testo, "l'archivio occupato non viene detto")
    esigi("backup in corso" in testo, "il motivo dell'occupazione non compare")
    esigi("riprova" in testo, "non viene suggerito di riprovare")

    # Il lock dello stato si acquisisce prima del contesto di output della
    # pipe: anche questo rifiuto deve lasciare stdout vuoto.
    uscita_pipe, errori_pipe = io.StringIO(), io.StringIO()
    with (
        lock_stato(PERCORSI.lock_file, esclusivo=True),
        patch.object(chat, "run_turn_cycle") as ciclo_spia,
        patch.object(chat, "build_assistant") as costruzione_spia,
        redirect_stdout(uscita_pipe),
        redirect_stderr(errori_pipe),
    ):
        codice = chat.avvia(user=UTENTE, prompt="ciao")
    esigi(codice == 3, "la pipe con stato occupato non esce con 3")
    esigi(uscita_pipe.getvalue() == "", "la contesa dello stato sporca stdout")
    esigi("stato di Ares e' in uso" in errori_pipe.getvalue(), "la contesa dello stato manca su stderr")
    esigi(not ciclo_spia.called and not costruzione_spia.called, "stato occupato: agente avviato")

    def avvio_interrotto(**_argomenti: object) -> None:
        raise KeyboardInterrupt

    uscita = io.StringIO()
    with patch.object(chat, "_esegui_chat", avvio_interrotto), redirect_stdout(uscita):
        # Nessun SystemExit: un Ctrl-C all'avvio e' una scelta, e vale 0.
        chat.main()
    esigi("Avvio interrotto" in _piatto(uscita.getvalue()), "un Ctrl-C durante l'avvio non viene detto")
    return "lock condiviso, archivio occupato con uscita 3 e Ctrl-C prima della REPL"


def aiuto_senza_effetti() -> str:
    """`--help` non crea l'archivio, per nessuno dei sette comandi.

    `config.prepara_archivio()` si chiama nel comando, non all'import, perche'
    `--help` esce prima in Cyclopts. Un archivio creato stampando l'aiuto e'
    comunque una traccia su una macchina condivisa.
    """
    comandi = (
        "ares",
        "ares.backup",
        "ares.entities",
        "ares.sessions",
        "ares.ops.inspect_learning",
        "ares.ops.preflight",
        "ares.ops.migrazione",
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
            esigi("usage" in figlio.stdout.lower(), comando + " --help non stampa l'uso")
            esigi(not stato.exists(), comando + " --help ha creato l'archivio in " + str(stato))
        finally:
            shutil.rmtree(pulita, ignore_errors=True)

    # `preflight` non ha argomenti: vale la stessa invariante (non lasciare
    # l'archivio), mentre l'esito dipende dalla macchina e non si controlla.
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


def chat_non_presidiato() -> str:
    """Senza terminale nessuno legge cio' che il modello propone.

    Anche con stdin da una pipe senza `-p` la conferma arriverebbe dallo stesso
    flusso dell'istruzione: le guardie lo trattano come `-p`.
    """

    def guardia(*, modo: str, scegli: bool, presidiato: bool) -> int | None:
        return chat._guardie_di_avvio(prompt=None, scegli=scegli, modo=modo, percorsi=PERCORSI, presidiato=presidiato)

    for modo in ("auto", "modifiche"):
        esigi(
            guardia(modo=modo, scegli=False, presidiato=False) == chat.ESITO_RIFIUTO,
            "senza terminale la modalita' " + modo + " non viene rifiutata",
        )
        esigi(
            guardia(modo=modo, scegli=False, presidiato=True) is None,
            "presidiato: la modalita' " + modo + " viene rifiutata lo stesso",
        )
    esigi(
        guardia(modo="manuale", scegli=False, presidiato=False) is None,
        "senza terminale una modalita' che chiede conferma viene rifiutata",
    )

    # Un solo attributo basta a `_apri_input`: percorsi, candidati e riga di
    # stato si leggono dopo, e il resto e' chiuso in una lambda.
    finto = SimpleNamespace(percorsi=PERCORSI)
    senza = chat._apri_input(finto, presidiato=False)
    esigi(senza.ask("Autorizzi? ") == "", "senza terminale la conferma non vale no")
    esigi(senza.fallback_ask is chat._nessuno, "senza terminale la conferma legge dal flusso")
    esigi(
        chat._apri_input(finto, presidiato=True).fallback_ask is not chat._nessuno,
        "presidiato: la conferma non puo' leggere l'input",
    )

    # Senza terminale, e senza `-p`, l'agente nasce come quello di `-p`:
    # nessun post-hook e nessuno strumento degli store. Con un terminale torna
    # a scrivere memorie.
    costruiti: list[dict] = []

    def costruisci(percorsi, impostazioni, politica, utente, **argomenti):
        costruiti.append(argomenti)
        return object()

    def avvio(*, presidiato: bool) -> int:
        uscita = io.StringIO()
        with (
            patch.object(chat, "build_assistant", costruisci),
            patch.object(chat, "CliInput", lambda **k: FintoInput([KeyboardInterrupt])),
            patch.object(chat, "promemoria_backup", lambda *a, **k: []),
            patch.object(sys, "stdin", SimpleNamespace(isatty=lambda: presidiato)),
            redirect_stdout(uscita),
            redirect_stderr(uscita),
        ):
            return chat._esegui_chat(user=UTENTE)

    esigi(
        avvio(presidiato=False) == 0 and costruiti[-1]["interattivo"] is False,
        "senza terminale l'agente scrive memorie",
    )
    esigi(
        avvio(presidiato=True) == 0 and costruiti[-1]["interattivo"] is True,
        "con terminale l'agente non scrive memorie",
    )
    return "modalita' silenziose rifiutate, conferme a vuoto e niente memorie senza terminale"


def chat_sessione_altrui() -> str:
    """Una sessione di un altro utente non si apre, e l'agente non nasce.

    `--session` e `/sessione` scavalcano gli elenchi filtrati per utente. Agno
    carica i run per solo `session_id`: senza controllo, i run di un utente
    finirebbero nella cronologia dell'altro.
    """
    from agno.session.agent import AgentSession

    from ares.agent.runtime import build_db

    db = build_db(PERCORSI)
    altrui = "sessione-di-altrui"
    mia = "sessione-mia"
    db.upsert_session(AgentSession(session_id=altrui, user_id="bob", created_at=1000))
    db.upsert_session(AgentSession(session_id=mia, user_id=UTENTE, created_at=2000))

    costruiti: list[dict] = []

    def costruisci(percorsi, impostazioni, politica, utente, **argomenti):
        argomenti["percorsi"] = percorsi
        argomenti["impostazioni"] = impostazioni
        argomenti["politica"] = politica
        costruiti.append(argomenti)
        return object()

    def avvio(**argomenti) -> tuple[int, str]:
        uscita = io.StringIO()
        with (
            patch.object(chat, "build_assistant", costruisci),
            patch.object(chat, "CliInput", lambda **k: FintoInput([KeyboardInterrupt])),
            patch.object(chat, "promemoria_backup", lambda *a, **k: []),
            patch.object(sys, "stdin", io.StringIO()),
            redirect_stdout(uscita),
            redirect_stderr(uscita),
        ):
            esito = chat._esegui_chat(user=UTENTE, **argomenti)
        return esito, _piatto(uscita.getvalue())

    try:
        esito, testo = avvio(session=altrui)
        esigi(esito == chat.ESITO_RIFIUTO, "la sessione di un altro utente non viene rifiutata")
        esigi(not costruiti, "l'agente nasce su una sessione di un altro utente")
        esigi(altrui in testo, "il rifiuto non nomina la sessione: " + repr(testo))

        esito, _ = avvio(session=mia)
        esigi(esito == 0 and costruiti[-1]["session_id"] == mia, "la propria sessione non si apre")

        esito, _ = avvio(session="nome-mai-visto")
        esigi(esito == 0 and costruiti[-1]["session_id"] == "nome-mai-visto", "un nome nuovo viene rifiutato")
    finally:
        db.delete_sessions([altrui], user_id="bob")
        db.delete_sessions([mia], user_id=UTENTE)
    return "sessione di un altro utente rifiutata senza costruire l'agente; la propria e una nuova si aprono"


def main() -> int:
    avvio = time.monotonic()
    print("Archivio della prova:", RADICE_PROVA)
    print()
    try:
        ok("archivio", costruisci_archivio())
        ok("identita' utente", identita_canonica())
        ok("identita' agente", identita_agente())
        ok("id sessione", id_sessione_univoci())

        for nome, prova in (
            ("preflight pronto", preflight_pronto),
            ("preflight mancante", preflight_modello_mancante),
            ("preflight cloud mancante", preflight_cloud_mancante),
            ("preflight estrazione cloud", preflight_estrazione_cloud),
            ("preflight json", preflight_json),
            ("preflight spento", preflight_server_spento),
        ):
            ok(nome, prova())

        # Il backup prima dell'ispezione: `inspect_learning.main()` lascia
        # aperti i database, e su Windows il `restore` fallirebbe.
        ok("backup CLI", backup_cli(RADICE_PROVA))
        ok("inspect_learning", inspect_learning_cli())
        ok("chat REPL", chat_repl())
        ok("chat turno", chat_turno())
        ok("memoria protetta", chat_memoria_protetta())
        ok("chat ciclo", chat_ciclo())
        ok("chat cartella", chat_cartella())
        ok("chat sessioni", chat_sessioni())
        ok("migrazione", migrazione_stato())
        ok("chat avvio", chat_avvio())
        ok("chat non presidiato", chat_non_presidiato())
        ok("chat sessione altrui", chat_sessione_altrui())
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

    pulisci(RADICE_PROVA)
    print()
    print("Concluso in", round(time.monotonic() - avvio, 2), "s")
    print("Nessun fallimento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
