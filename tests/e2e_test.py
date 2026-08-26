"""
Prova di funzionamento
======================
Uso:
    .venv/bin/python e2e_test.py
    .venv/bin/python e2e_test.py --conserva   non cancella l'archivio della prova

Un turno di conversazione vero, con il modello acceso, su un archivio
usa-e-getta. Risponde alla domanda che `smoke_test.py` non pone: l'agente
risponde e impara?

Le due prove non si sostituiscono. `smoke_test.py` verifica il cablaggio in un
secondo e mezzo senza toccare la VRAM, e va rifatto dopo ogni modifica al
codice. Questa costa un turno intero e Ollama acceso, e va fatta prima di un
commit che tocca la composizione dell'agente o gli store: e' l'unica che
dimostra che il giro si chiude davvero.

**Cosa viene affermato e cosa no.** Che le righe compaiano e che si rileggano
da un processo diverso: si'. Il contesto di sessione deve sempre scrivere,
perche' il suo prompt lo ordina esplicitamente; profilo, memorie ed entita'
possono invece non trovare fatti durevoli. Il contenuto e la lingua restano
fuori: sono materiale generato e un controllo su quello sarebbe intermittente
invece che severo. Anche le intuizioni apprese restano fuori: quello store e'
AGENTIC e scrive quando il modello decide.

Il giro si chiude in un processo nuovo e non in questo: rileggere dagli
oggetti che hanno appena scritto proverebbe che una variabile e' ancora in
memoria, non che l'archivio conservi qualcosa.
"""

import argparse
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
from contextlib import closing
from pathlib import Path

# Le prove stanno in tests/, i moduli del progetto in radice: lanciata come
# script, `sys.path[0]` e' tests/ e `import config` non troverebbe niente.
# Va prima di qualunque import del progetto.
RADICE_PROGETTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE_PROGETTO))

# L'archivio della prova va scelto prima di importare config, perche' config
# crea TMP_DIR all'import: importarlo e correggere i percorsi dopo lascerebbe
# comunque una tmp/ vuota accanto ai dati veri.
ARCHIVIO_PROVA = tempfile.mkdtemp(prefix="ares-prova-")
os.environ["ARES_TMP"] = ARCHIVIO_PROVA
# Anche lo spazio di lavoro: `build_workspace` crea la directory al momento
# di comporre l'agente, quindi senza questa riga un turno di prova ne farebbe
# comparire una vera accanto al progetto.
SPAZIO_PROVA = tempfile.mkdtemp(prefix="ares-prova-lavoro-")
os.environ["ARES_WORKSPACE"] = SPAZIO_PROVA

import config  # noqa: E402
from preflight import modelli_disponibili, stessa_etichetta  # noqa: E402
from smoke_test import esigi  # noqa: E402

UTENTE = "prova-e2e"
SESSIONE = "prova-e2e"

# La domanda porta un fatto personale esplicito, perche' i tre store in
# modalita' ALWAYS estraggono da cio' che l'utente dice di se': una domanda di
# aritmetica pura darebbe una risposta corretta e niente da imparare, e la
# prova non distinguerebbe un agente che impara da uno che risponde e basta.
# "In una riga" tiene corto il turno: qui si misura che il giro si chiuda, non
# quanto bene scriva il modello.
DOMANDA = (
    "Mi chiamo Prova e uso Linux. "
    "In una riga: a cosa serve un file di lock delle dipendenze?"
)

# Il processo figlio rilegge l'archivio da zero. Vive come stringa e non come
# funzione importata perche' il punto e' proprio che sia un interprete diverso,
# con gli stessi percorsi e nessun oggetto ereditato.
RILETTURA = """
import sys
from assistant import build_assistant
from stores import leggi_entita

utente, sessione = sys.argv[1], sys.argv[2]
lm = build_assistant(user_id=utente, session_id=sessione).learning_machine

print("user_profile", lm.user_profile_store.get(user_id=utente) is not None)
memorie = getattr(lm.user_memory_store.get(user_id=utente), "memories", None) or []
print("user_memory", len(memorie) > 0)
print("session_context", lm.session_context_store.get(session_id=sessione) is not None)
print("entity_memory", len(leggi_entita(lm, user_id=utente, limit=1000)) > 0)
"""


class RaccoglitoreAvvisi(logging.Handler):
    """Raccoglie gli avvisi che Agno emette durante il turno.

    Gli errori di estrazione non fermano il turno e non tornano al chiamante:
    Agno li registra come WARNING e va avanti. Il primo giro di questa prova ne
    ha prodotto uno - `save_session_context` con argomenti di tool non validi -
    e senza questo raccoglitore sarebbe rimasto una riga di log in mezzo alla
    risposta, cioe' invisibile. Uno store in modalita' ALWAYS che fallisce in
    silenzio costa un'inferenza per turno e non conserva niente.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.avvisi = []

    def emit(self, record) -> None:
        self.avvisi.append(record.getMessage().replace("\n", " ").strip())


def logger_di_agno() -> list:
    """I logger di Agno, che non propagano alla radice: vanno presi per nome."""
    return [
        logging.getLogger(nome)
        for nome in list(logging.root.manager.loggerDict)
        if nome == "agno" or nome.startswith(("agno-", "agno."))
    ]


def store_sempre_attivi() -> list:
    """Gli store in modalita' ALWAYS, che estraggono a ogni turno."""
    accesi = (
        ("user_profile", config.LEARN_USER_PROFILE),
        ("user_memory", config.LEARN_USER_MEMORY),
        ("session_context", config.LEARN_SESSION_CONTEXT),
    )
    return [nome for nome, acceso in accesi if acceso]


def conta_per_tipo() -> dict:
    """Righe di apprendimento per tipo, lette da SQLite invece che dagli store.

    Stessa ragione dello smoke test: un conteggio che passa dallo stesso
    codice di lettura del difetto non puo' vederlo.
    """
    conteggi = {}
    with closing(sqlite3.connect(config.DB_FILE)) as connessione:
        try:
            righe = connessione.execute("select learning_type, count(*) from agno_learnings group by 1")
        except sqlite3.OperationalError:
            return conteggi
        for tipo, quante in righe:
            conteggi[tipo] = quante
    return conteggi


def conta_sessioni() -> int:
    with closing(sqlite3.connect(config.DB_FILE)) as connessione:
        try:
            return connessione.execute("select count(*) from agno_sessions").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def stato_archivio_reale() -> list:
    """Fotografia dell'archivio vero, per dimostrare che questa prova non lo tocca.

    E' il controllo che rende sicuro lanciare una prova col modello acceso:
    senza, l'unico modo di sapere che il turno non ha scritto tra i dati veri
    sarebbe fidarsi della variabile d'ambiente.
    """
    reale = config.BASE_DIR / "tmp"
    if not reale.exists():
        return []
    return sorted(
        (str(percorso.relative_to(reale)), percorso.stat().st_size, percorso.stat().st_mtime_ns)
        for percorso in reale.rglob("*")
        if percorso.is_file()
    )


def ok(nome: str, nota: str) -> None:
    print("ok      ", nome, "-", nota)


def main() -> int:
    parser = argparse.ArgumentParser(description="Un turno vero su un archivio usa-e-getta")
    parser.add_argument("--conserva", action="store_true", help="non cancella l'archivio della prova")
    args = parser.parse_args()

    print("Archivio della prova:", ARCHIVIO_PROVA)
    print("Utente:", UTENTE, "  Sessione:", SESSIONE)
    print()

    reale_prima = stato_archivio_reale()
    raccoglitore = RaccoglitoreAvvisi()
    avvio = time.monotonic()

    try:
        esigi(
            not config.DB_FILE.startswith(str(config.BASE_DIR / "tmp")),
            "l'archivio della prova coincide con quello vero: " + config.DB_FILE,
        )
        ok("archivio separato   ", "i dati veri non vengono ne' letti ne' scritti")

        # Ollama spento non e' un difetto dell'agente, ed e' l'unico esito di
        # questa prova che non va contato come fallimento del codice.
        try:
            presenti = [m.get("name", "") for m in modelli_disponibili(config.OLLAMA_HOST)]
        except (urllib.error.URLError, OSError) as errore:
            print("SALTATA  ollama -", errore)
            print()
            print("Questa prova ha bisogno del modello. Avvia il server con: ollama serve")
            return 2
        esigi(
            any(stessa_etichetta(config.MAIN_MODEL, nome) for nome in presenti),
            config.MAIN_MODEL + " non e' scaricato: ollama pull " + config.MAIN_MODEL,
        )
        ok("modello presente    ", config.MAIN_MODEL + " su " + config.OLLAMA_HOST)

        from assistant import build_assistant

        costruzione = time.monotonic()
        agent = build_assistant(user_id=UTENTE, session_id=SESSIONE)
        ok("costruzione         ", "agente costruito in " + str(round(time.monotonic() - costruzione, 2)) + " s")

        print()
        print("Domanda:", DOMANDA)
        logger = logger_di_agno()
        for singolo in logger:
            singolo.addHandler(raccoglitore)
        turno = time.monotonic()
        try:
            risposta = agent.run(DOMANDA)
        finally:
            for singolo in logger:
                singolo.removeHandler(raccoglitore)
        durata_turno = round(time.monotonic() - turno, 1)
        testo = (getattr(risposta, "content", None) or "").strip()
        print("Risposta:", testo[:300] + ("..." if len(testo) > 300 else ""))
        print()

        esigi(bool(testo), "il modello ha risposto con un contenuto vuoto")
        ok("turno completato    ", str(len(testo)) + " caratteri in " + str(durata_turno) + " s")

        # La sessione la scrive il framework, non il modello: se manca questa
        # riga il difetto e' nel cablaggio, non nell'estrazione.
        esigi(conta_sessioni() >= 1, "il turno non ha lasciato nessuna sessione in archivio")
        ok("sessione registrata ", str(conta_sessioni()) + " sessione in archivio")

        scritti = conta_per_tipo()
        esigi(
            sum(scritti.values()) > 0,
            "nessuna riga di apprendimento dopo un turno con tre store in modalita' ALWAYS",
        )
        ok(
            "apprendimento scritto",
            ", ".join(tipo + "=" + str(quante) for tipo, quante in sorted(scritti.items())),
        )

        # Il contesto, a differenza di profilo e memorie, riceve sempre
        # l'ordine esplicito di salvare un riepilogo del turno. Dopo il retry
        # un'assenza non e' piu' una scelta legittima del modello: e' il
        # difetto che questa prova deve rendere rosso.
        if config.LEARN_SESSION_CONTEXT:
            esigi(
                scritti.get("session_context", 0) >= 1,
                "session_context non ha scritto nemmeno dopo il retry",
            )
            tentativi_contesto = agent.learning_machine.session_context_store.last_extraction_attempts
            ok(
                "contesto affidabile  ",
                "scritto in " + str(tentativi_contesto) + " tentativo/i",
            )

        # Profilo e memorie possono legittimamente non trovare fatti durevoli.
        # Il contesto e' gia' stato preteso sopra e quindi non entra nei muti.
        muti = [nome for nome in store_sempre_attivi() if nome not in scritti]
        muti = [nome for nome in muti if nome != "session_context"]
        if muti:
            print("         store ALWAYS senza scritture:", ", ".join(muti))

        # Il giro si chiude qui: interprete nuovo, stesso archivio, nessun
        # oggetto ereditato da chi ha scritto.
        figlio = subprocess.run(
            [sys.executable, "-c", RILETTURA, UTENTE, SESSIONE],
            cwd=str(config.BASE_DIR),
            env={**os.environ, "ARES_TMP": ARCHIVIO_PROVA, "ARES_WORKSPACE": SPAZIO_PROVA},
            capture_output=True,
            text=True,
            timeout=180,
        )
        esigi(
            figlio.returncode == 0,
            "la rilettura in un processo nuovo e' fallita: " + (figlio.stderr or "").strip()[-300:],
        )
        riletti = dict(
            (riga.split()[0], riga.split()[1] == "True")
            for riga in figlio.stdout.splitlines()
            if len(riga.split()) == 2
        )
        # Si pretende la rilettura di cio' che ha scritto, non di tutto: quali
        # store scrivano in un turno lo decide il modello, che rileggano cio'
        # che hanno scritto no.
        for tipo in scritti:
            if tipo not in riletti:
                continue
            esigi(riletti[tipo], tipo + " ha scritto in archivio ma non si rilegge da un processo nuovo")
        ok(
            "riletto da un altro processo",
            ", ".join(sorted(tipo for tipo in scritti if riletti.get(tipo))) + " rileggibili",
        )

        esigi(stato_archivio_reale() == reale_prima, "l'archivio vero e' cambiato durante la prova")
        ok("archivio vero intatto", str(len(reale_prima)) + " file, invariati")

    except AssertionError as errore:
        print("FALLITO ", errore)
        print()
        print("Archivio della prova conservato per l'esame:", ARCHIVIO_PROVA)
        return 1
    except Exception as errore:
        print("FALLITO ", type(errore).__name__ + ":", errore)
        print()
        print("Archivio della prova conservato per l'esame:", ARCHIVIO_PROVA)
        return 1
    finally:
        print()
        print("Concluso in", round(time.monotonic() - avvio, 1), "s")

    if raccoglitore.avvisi:
        print()
        print("Avvisi di Agno durante il turno, da leggere:")
        for avviso in raccoglitore.avvisi:
            print("   ", avviso[:200])

    if args.conserva:
        print("Archivio della prova conservato:", ARCHIVIO_PROVA)
    else:
        shutil.rmtree(ARCHIVIO_PROVA, ignore_errors=True)
        shutil.rmtree(SPAZIO_PROVA, ignore_errors=True)
        print("Archivio della prova cancellato.")
    print("Nessun fallimento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
