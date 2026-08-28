"""
Prova delle intuizioni riutilizzabili
=====================================

Uso:
    .venv/bin/python -u learned_knowledge_test.py
    .venv/bin/python -u learned_knowledge_test.py --conserva

Verifica il solo store ``learned_knowledge`` su uno stato usa-e-getta. La
prima meta' chiama direttamente gli stessi strumenti che Agno consegna al
modello: dimostra salvataggio, idempotenza, namespace e rilettura da un altro
processo. La seconda usa Ares con Ollama: pretende ``search_learnings`` prima
di ``save_learning``, poi apre una nuova sessione e controlla che l'intuizione
venga cercata e applicata.

Non fa parte dello smoke test: scrivere e cercare in LanceDB accende
l'embedder, mentre i due turni agentici accendono anche il modello principale.
Tutti gli altri store e il workspace vengono spenti nella prova per isolare
il comportamento e non pagare estrazioni estranee al risultato.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
from pathlib import Path

# Le prove stanno in tests/, i moduli del progetto in radice: lanciata come
# script, `sys.path[0]` e' tests/ e `import config` non troverebbe niente.
# Va prima di qualunque import del progetto.
RADICE_PROGETTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE_PROGETTO))

UTENTE_STORE = "prova-intuizioni-store"
UTENTE_ALTRO = "prova-intuizioni-altro"
UTENTE_AGENTE = "prova-intuizioni-agente"
SESSIONE_SALVATAGGIO = "salvataggio"
SESSIONE_RIUSO = "riuso"
MARCATORE_STORE = "ARES-STORE-22"
MARCATORE_AGENTE = "ARES-INTUIZIONE-22"

TITOLO_STORE = MARCATORE_STORE + " manutenzione reversibile"
TESTO_STORE = (
    "Prima di modificare uno stato persistente, creare un'anteprima, un backup "
    "verificato e una transazione; dopo la modifica verificare le invarianti."
)

PROMPT_SALVATAGGIO = (
    "Durante la manutenzione delle entita' duplicate abbiamo ricavato un criterio "
    "generale e riutilizzabile: prima di fondere record persistenti bisogna eseguire "
    "un audit, mostrare un'anteprima, creare e verificare un backup, applicare tutto "
    "in una transazione e infine verificare le invarianti e le relazioni. "
    "Salva ora questo criterio nelle tue intuizioni per conversazioni future. "
    "Cerca prima eventuali duplicati come richiedono i tuoi strumenti; usa come "
    "titolo esatto '" + MARCATORE_AGENTE + " manutenzione reversibile' e scrivi "
    "titolo, intuizione e contesto in italiano."
)

PROMPT_RIUSO = (
    "Sto progettando una manutenzione che unira' record persistenti collegati fra "
    "loro. Quale sequenza rende l'operazione sicura e reversibile? Se la risposta "
    "deriva da un'intuizione che hai conservato, dichiaralo esplicitamente."
)

RILETTURA = """
import json
import sys

import config

config.LEARN_USER_PROFILE = False
config.LEARN_USER_MEMORY = False
config.LEARN_SESSION_CONTEXT = False
config.LEARN_ENTITIES = False
config.MEMORY_AGENT_TOOLS = False
config.SEARCH_PAST_SESSIONS = False
config.READ_CHAT_HISTORY = False
config.WORKSPACE = False

from assistant import build_assistant
from stores import leggi_intuizioni

utente, sessione, query = sys.argv[1:4]
lm = build_assistant(user_id=utente, session_id=sessione).learning_machine
risultati = leggi_intuizioni(lm, user_id=utente, query=query, limit=20)
dati = [
    {
        "title": getattr(voce, "title", ""),
        "learning": getattr(voce, "learning", ""),
        "context": getattr(voce, "context", ""),
        "namespace": getattr(voce, "namespace", ""),
    }
    for voce in risultati
]
print("ARES_RILETTURA=" + json.dumps(dati, ensure_ascii=False))
"""


def esigi(condizione: object, messaggio: str) -> None:
    if not condizione:
        raise AssertionError(messaggio)


def ok(nome: str, nota: str) -> None:
    print("ok      ", nome.ljust(23), "-", nota, flush=True)


def nomi_strumenti(risposta) -> list[str]:
    return [str(getattr(strumento, "tool_name", "") or "") for strumento in (getattr(risposta, "tools", None) or [])]


def testo_intuizione(voce) -> str:
    return " ".join(str(getattr(voce, campo, "") or "") for campo in ("title", "learning", "context"))


def fotografia(percorso: Path) -> list[tuple[str, int, int]]:
    if not percorso.exists():
        return []
    return sorted(
        (str(voce.relative_to(percorso)), voce.stat().st_size, voce.stat().st_mtime_ns)
        for voce in percorso.rglob("*")
        if voce.is_file()
    )


def strumenti_learning(lm, user_id: str, session_id: str) -> dict:
    strumenti = lm.get_tools(user_id=user_id, session_id=session_id)
    return {getattr(voce, "__name__", ""): voce for voce in strumenti}


def cerca(lm, user_id: str, query: str) -> list:
    from stores import leggi_intuizioni

    return leggi_intuizioni(lm, user_id=user_id, query=query, limit=20)


def rileggi_in_processo_nuovo(user_id: str, session_id: str, query: str) -> list[dict]:
    figlio = subprocess.run(
        [sys.executable, "-c", RILETTURA, user_id, session_id, query],
        cwd=str(config.BASE_DIR),
        env={
            **os.environ,
            "ARES_TMP": str(RADICE_STATO),
            "ARES_WORKSPACE": str(RADICE_WORKSPACE),
            "ARES_BACKUP_DIR": str(RADICE_BACKUP),
        },
        capture_output=True,
        text=True,
        timeout=240,
    )
    esigi(
        figlio.returncode == 0,
        "rilettura in un processo nuovo fallita: " + (figlio.stderr or figlio.stdout).strip()[-500:],
    )
    prefisso = "ARES_RILETTURA="
    righe = [riga for riga in figlio.stdout.splitlines() if riga.startswith(prefisso)]
    esigi(len(righe) == 1, "output di rilettura ambiguo: " + repr(figlio.stdout[-500:]))
    return json.loads(righe[0][len(prefisso) :])


def modelli_pronti() -> tuple[bool, str]:
    from preflight import modelli_disponibili, stessa_etichetta

    presenti = [modello.get("name", "") for modello in modelli_disponibili(config.OLLAMA_HOST)]
    richiesti = (config.MAIN_MODEL, config.EMBEDDER_MODEL)
    mancanti = [
        richiesto for richiesto in richiesti if not any(stessa_etichetta(richiesto, presente) for presente in presenti)
    ]
    if mancanti:
        return False, "modelli assenti: " + ", ".join(mancanti)
    return True, ", ".join(richiesti)


def prova_store() -> None:
    agente = build_assistant(user_id=UTENTE_STORE, session_id="store")
    lm = agente.learning_machine
    esigi(lm is not None, "LearningMachine assente")
    strumenti = strumenti_learning(lm, UTENTE_STORE, "store")
    esigi("search_learnings" in strumenti, "search_learnings non consegnato")
    esigi("save_learning" in strumenti, "save_learning non consegnato")
    ok("strumenti", "search_learnings e save_learning consegnati")

    vuoto = strumenti["search_learnings"](query=MARCATORE_STORE)
    esigi("No relevant" in vuoto, "un namespace nuovo non e' vuoto: " + repr(vuoto))

    conteggio_prima = lm.knowledge.vector_db.get_count()
    risultato = strumenti["save_learning"](
        title=TITOLO_STORE,
        learning=TESTO_STORE,
        context="Manutenzioni di database, indici o grafi persistenti.",
        tags=["manutenzione", "sicurezza", "rollback"],
    )
    esigi("Learning saved" in risultato, "save_learning ha fallito: " + repr(risultato))
    conteggio_dopo = lm.knowledge.vector_db.get_count()
    esigi(conteggio_dopo == conteggio_prima + 1, "il salvataggio non ha aggiunto esattamente una riga")
    ok("salvataggio", "una riga reale aggiunta a LanceDB")

    duplicato = strumenti["save_learning"](
        title=TITOLO_STORE,
        learning=TESTO_STORE,
        context="Manutenzioni di database, indici o grafi persistenti.",
        tags=["manutenzione", "sicurezza", "rollback"],
    )
    esigi("Learning saved" in duplicato, "il secondo salvataggio ha sollevato un falso errore")
    esigi(lm.knowledge.vector_db.get_count() == conteggio_dopo, "skip_if_exists ha creato un duplicato identico")
    ok("idempotenza", "lo stesso documento non crea una seconda riga")

    risultati = cerca(lm, UTENTE_STORE, MARCATORE_STORE)
    esigi(len(risultati) == 1, "la ricerca non ritrova l'unica intuizione: " + str(len(risultati)))
    esigi(MARCATORE_STORE in testo_intuizione(risultati[0]), "il risultato non e' quello salvato")
    ok("ricerca", "contenuto strutturato ricostruito dalla ricerca ibrida")

    altro = build_assistant(user_id=UTENTE_ALTRO, session_id="isolamento").learning_machine
    esigi(altro is not None, "LearningMachine del secondo utente assente")
    esigi(not cerca(altro, UTENTE_ALTRO, MARCATORE_STORE), "il secondo utente vede l'intuizione privata")
    ok("namespace", UTENTE_ALTRO + " non vede " + UTENTE_STORE)

    riletti = rileggi_in_processo_nuovo(UTENTE_STORE, "riapertura", MARCATORE_STORE)
    esigi(len(riletti) == 1, "un nuovo processo non rilegge l'intuizione")
    esigi(MARCATORE_STORE in " ".join(str(v) for v in riletti[0].values()), "riletta l'intuizione sbagliata")
    ok("riapertura", "intuizione riletta da un interprete nuovo")


def prova_agente() -> None:
    agente = build_assistant(user_id=UTENTE_AGENTE, session_id=SESSIONE_SALVATAGGIO)
    avvio = time.monotonic()
    risposta = agente.run(PROMPT_SALVATAGGIO)
    durata = round(time.monotonic() - avvio, 1)
    strumenti = nomi_strumenti(risposta)
    esigi("search_learnings" in strumenti, "Ares non ha cercato duplicati: " + repr(strumenti))
    esigi("save_learning" in strumenti, "Ares non ha chiamato save_learning: " + repr(strumenti))
    esigi(
        strumenti.index("search_learnings") < strumenti.index("save_learning"),
        "Ares ha salvato prima di cercare: " + repr(strumenti),
    )
    errori = [
        strumento.tool_name
        for strumento in (risposta.tools or [])
        if strumento.tool_name in {"search_learnings", "save_learning"} and strumento.tool_call_error
    ]
    esigi(not errori, "strumenti falliti: " + repr(errori))
    ok("Ares salva", " -> ".join(strumenti) + " in " + str(durata) + " s")

    lm = agente.learning_machine
    salvate = cerca(lm, UTENTE_AGENTE, MARCATORE_AGENTE)
    esigi(len(salvate) == 1, "Ares non ha lasciato una sola intuizione ricercabile: " + str(len(salvate)))
    contenuto = testo_intuizione(salvate[0]).lower()
    esigi(MARCATORE_AGENTE.lower() in contenuto, "Ares non ha rispettato il titolo richiesto")
    indicatori = {"prima", "audit", "anteprima", "backup", "transazione", "verificare", "relazioni"}
    presenti = {parola for parola in indicatori if parola in contenuto}
    esigi(len(presenti) >= 4, "intuizione non riconoscibile come italiana e completa: " + contenuto)
    ok("contenuto", "italiano e operativo: " + ", ".join(sorted(presenti)))

    nuovo = build_assistant(user_id=UTENTE_AGENTE, session_id=SESSIONE_RIUSO)
    avvio = time.monotonic()
    riuso = nuovo.run(PROMPT_RIUSO)
    durata = round(time.monotonic() - avvio, 1)
    strumenti_riuso = nomi_strumenti(riuso)
    esigi("search_learnings" in strumenti_riuso, "nella nuova sessione Ares non ha cercato le intuizioni")
    testo = str(riuso.content or "").lower()
    concetti = {"audit", "anteprima", "backup", "transaz", "verific", "relaz"}
    applicati = {concetto for concetto in concetti if concetto in testo}
    esigi(len(applicati) >= 4, "Ares non ha applicato l'intuizione: " + testo[:500])
    provenienza = {"intuizion", "memori", "conservat", "appres"}
    esigi(
        any(indizio in testo for indizio in provenienza),
        "Ares ha usato il criterio senza dichiararne la provenienza: " + testo[:500],
    )
    ok("riuso", "nuova sessione, ricerca e " + str(len(applicati)) + " concetti applicati in " + str(durata) + " s")


def prova_backup() -> None:
    from backup import crea_snapshot, ripristina_snapshot, verifica_snapshot

    snapshot = crea_snapshot()
    manifest = verifica_snapshot(snapshot, percorso_diretto=True)
    tabelle = manifest.get("components", {}).get("lancedb", {}).get("tables", {})
    esigi(tabelle.get("learned_knowledge", 0) >= 2, "lo snapshot non contiene le intuizioni")
    ok("backup", snapshot.name + " verificato con " + str(tabelle["learned_knowledge"]) + " righe")

    shutil.rmtree(RADICE_STATO)
    RADICE_STATO.mkdir(parents=True)
    ripristina_snapshot(snapshot.name, snapshot_sicurezza=False)
    riletti = rileggi_in_processo_nuovo(UTENTE_AGENTE, "dopo-restore", MARCATORE_AGENTE)
    esigi(len(riletti) == 1, "l'intuizione non sopravvive al restore")
    ok("restore", "intuizione di Ares riletta dopo il ripristino")


def main(args) -> int:
    print("Stato temporaneo:", RADICE_STATO)
    print("Backup temporaneo:", RADICE_BACKUP)
    print()

    reale_prima = fotografia(config.BASE_DIR / "tmp")
    avvio = time.monotonic()
    try:
        esigi(RADICE_STATO != config.BASE_DIR / "tmp", "la prova punta allo stato reale")
        try:
            pronti, dettaglio = modelli_pronti()
        except (urllib.error.URLError, OSError) as errore:
            print("SALTATA  Ollama non raggiungibile:", errore)
            if not args.conserva:
                shutil.rmtree(RADICE_PROVA, ignore_errors=True)
            return 2
        if not pronti:
            print("SALTATA ", dettaglio)
            if not args.conserva:
                shutil.rmtree(RADICE_PROVA, ignore_errors=True)
            return 2
        ok("modelli", dettaglio)

        prova_store()
        prova_agente()
        prova_backup()
        esigi(fotografia(config.BASE_DIR / "tmp") == reale_prima, "lo stato reale e' cambiato durante la prova")
        ok("stato reale", str(len(reale_prima)) + " file invariati")
    except AssertionError as errore:
        print("FALLITO ", errore)
        print("Stato conservato:", RADICE_PROVA)
        return 1
    except Exception as errore:
        print("FALLITO ", type(errore).__name__ + ":", errore)
        print("Stato conservato:", RADICE_PROVA)
        return 1
    finally:
        print()
        print("Concluso in", round(time.monotonic() - avvio, 1), "s")

    if args.conserva:
        print("Stato conservato:", RADICE_PROVA)
    else:
        shutil.rmtree(RADICE_PROVA, ignore_errors=True)
        print("Stato temporaneo cancellato.")
    print("Nessun fallimento.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Salvataggio e riuso delle intuizioni di Ares")
    parser.add_argument("--conserva", action="store_true", help="non cancella lo stato temporaneo")
    argomenti = parser.parse_args()

    RADICE_PROVA = Path(tempfile.mkdtemp(prefix="ares-intuizioni-"))
    RADICE_STATO = RADICE_PROVA / "stato"
    RADICE_WORKSPACE = RADICE_PROVA / "workspace"
    RADICE_BACKUP = RADICE_PROVA / "backup"
    os.environ["ARES_TMP"] = str(RADICE_STATO)
    os.environ["ARES_WORKSPACE"] = str(RADICE_WORKSPACE)
    os.environ["ARES_BACKUP_DIR"] = str(RADICE_BACKUP)

    import config

    # Isoliamo learned_knowledge: nessuna estrazione ALWAYS, nessun altro
    # strumento agentico e nessun workspace durante i due turni reali.
    config.LEARN_USER_PROFILE = False
    config.LEARN_USER_MEMORY = False
    config.LEARN_SESSION_CONTEXT = False
    config.LEARN_ENTITIES = False
    config.MEMORY_AGENT_TOOLS = False
    config.SEARCH_PAST_SESSIONS = False
    config.READ_CHAT_HISTORY = False
    config.WORKSPACE = False

    from assistant import build_assistant

    sys.exit(main(argomenti))
