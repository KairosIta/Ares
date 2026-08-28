"""
Affidabilita' dell'estrazione del contesto
==========================================

Uso:
    .venv/bin/python learning_reliability_test.py
    .venv/bin/python learning_reliability_test.py --attempts 10

Chiama soltanto il modello di apprendimento, senza costruire l'agente e senza
caricare l'embedder. Confronta due temperature sullo stesso messaggio e conta
successi immediati, retry recuperati e fallimenti. Tutto lo stato e' temporaneo.
"""

import argparse
import os
import shutil
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


def costruisci_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Misura il retry di save_session_context con Ollama")
    parser.add_argument("--attempts", type=int, default=5, help="giri per ciascuna temperatura")
    parser.add_argument(
        "--temperatures",
        nargs="+",
        type=float,
        default=[0.2, 0.0],
        help="temperature da confrontare",
    )
    parser.add_argument("--conserva", action="store_true", help="non cancella il database temporaneo")
    return parser


def modello_presente() -> bool:
    presenti = [modello.get("name", "") for modello in modelli_disponibili(config.OLLAMA_HOST)]
    return any(stessa_etichetta(config.LEARNING_MODEL, nome) for nome in presenti)


def prova_temperatura(temperatura: float, tentativi: int, gruppo: int) -> dict:
    modello = build_learning_model()
    modello.options = {**(modello.options or {}), "temperature": temperatura}
    store = build_session_context_store(build_db(), modello)
    risultati = {"immediati": 0, "recuperati": 0, "falliti": 0, "secondi": 0.0}

    for indice in range(1, tentativi + 1):
        # Il gruppo rende univoci anche due valori di temperatura identici:
        # una riga del gruppo precedente non puo' mascherare un fallimento.
        sessione = "affidabilita-" + str(gruppo) + "-" + str(indice)
        avvio = time.monotonic()
        store.extract_and_save(
            messages=MESSAGGI,
            session_id=sessione,
            user_id="prova-affidabilita",
        )
        durata = time.monotonic() - avvio
        salvato = store.get(session_id=sessione) is not None
        quanti = store.last_extraction_attempts
        risultati["secondi"] += durata
        if not salvato:
            risultati["falliti"] += 1
            esito = "FALLITO"
        elif quanti == 1:
            risultati["immediati"] += 1
            esito = "immediato"
        else:
            risultati["recuperati"] += 1
            esito = "recuperato"
        print(
            "temperatura",
            temperatura,
            "giro",
            str(indice) + "/" + str(tentativi),
            "-",
            esito,
            "in",
            round(durata, 1),
            "s, estrazioni:",
            quanti,
            flush=True,
        )
    return risultati


def main(args) -> int:
    try:
        if not modello_presente():
            print("SALTATA: modello assente:", config.LEARNING_MODEL)
            if not args.conserva:
                shutil.rmtree(RADICE_PROVA, ignore_errors=True)
            return 2
    except (urllib.error.URLError, OSError) as errore:
        print("SALTATA: Ollama non raggiungibile:", errore)
        if not args.conserva:
            shutil.rmtree(RADICE_PROVA, ignore_errors=True)
        return 2

    fallimenti = 0
    print("Archivio temporaneo:", RADICE_PROVA)
    print("Retry disponibili:", config.SESSION_CONTEXT_RETRIES)
    print()
    try:
        for gruppo, temperatura in enumerate(args.temperatures, 1):
            risultati = prova_temperatura(temperatura, args.attempts, gruppo)
            fallimenti += risultati["falliti"]
            print(
                "totale",
                temperatura,
                "- immediati:",
                risultati["immediati"],
                "recuperati:",
                risultati["recuperati"],
                "falliti:",
                risultati["falliti"],
                "tempo:",
                round(risultati["secondi"], 1),
                "s",
            )
            print()
    except Exception as errore:
        print("FALLITO", type(errore).__name__ + ":", errore)
        print("Archivio della prova conservato:", RADICE_PROVA)
        return 1

    if args.conserva or fallimenti:
        print("Archivio della prova conservato:", RADICE_PROVA)
    else:
        shutil.rmtree(RADICE_PROVA, ignore_errors=True)
        print("Archivio della prova cancellato.")

    if fallimenti:
        print("FALLITI dopo il retry:", fallimenti)
        return 1
    print("Nessun fallimento dopo il retry.")
    return 0


if __name__ == "__main__":
    parser = costruisci_parser()
    argomenti = parser.parse_args()
    if argomenti.attempts < 1:
        parser.error("--attempts deve essere almeno 1")

    # Le opzioni vengono elaborate prima di creare lo stato: anche --help e
    # un parametro non valido escono senza lasciare directory in /tmp.
    RADICE_PROVA = Path(tempfile.mkdtemp(prefix="ares-learning-reliability-"))
    os.environ["ARES_TMP"] = str(RADICE_PROVA / "stato")

    from agno.models.message import Message

    import config
    from assistant import build_db, build_learning_model, build_session_context_store
    from preflight import modelli_disponibili, stessa_etichetta

    MESSAGGI = [
        Message(
            role="user",
            content=(
                "Sto costruendo Ares, un agente locale. Abbiamo completato il backup "
                "e ora voglio rendere affidabile il contesto di sessione."
            ),
        ),
        Message(
            role="assistant",
            content=(
                "Il prossimo passo e' verificare il retry di save_session_context; "
                "il backup locale e' gia' completato e collaudato."
            ),
        ),
    ]

    sys.exit(main(argomenti))
