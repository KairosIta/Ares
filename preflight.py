"""
Verifica dell'ambiente prima di avviare l'agente
================================================
Uso:
    .venv/bin/python preflight.py

Risponde a una domanda sola: se avvio la chat adesso, parte? Controlla che
il server Ollama risponda e che i modelli nominati in `config.py` siano
davvero scaricati. Sono i due modi in cui l'avvio fallisce, e il secondo
non da' errore finche' non arriva il primo messaggio.

Usa solo la libreria standard, quindi si puo' eseguire con qualsiasi
interprete anche prima di aver installato le dipendenze.
"""

import json
import sys
import urllib.error
import urllib.request

import config


def modelli_disponibili(host: str, timeout: int = 10) -> list:
    """Elenca i modelli scaricati sul server Ollama.

    Solleva urllib.error.URLError se il server non risponde: distinguere
    "server spento" da "modello mancante" e' meta' del valore di questo
    controllo.
    """
    with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=timeout) as r:
        return json.load(r).get("models", [])


def stessa_etichetta(richiesto: str, presente: str) -> bool:
    """Confronta due nomi di modello ignorando il tag implicito.

    Ollama elenca `nomic-embed-text-v2-moe` come `nomic-embed-text-v2-moe:latest`,
    quindi il confronto letterale darebbe un falso negativo su ogni modello
    scritto senza tag.
    """
    normalizza = lambda n: n if ":" in n else n + ":latest"
    return normalizza(richiesto) == normalizza(presente)


def main() -> int:
    print("Server:", config.OLLAMA_HOST)
    try:
        presenti = modelli_disponibili(config.OLLAMA_HOST)
    except (urllib.error.URLError, OSError) as e:
        print("  non raggiungibile:", e)
        print()
        print("Avvia il server con: ollama serve")
        return 1
    print("  raggiungibile,", len(presenti), "modelli scaricati")
    print()

    # I modelli davvero usati a ogni turno, che da quando l'embedder di
    # ingestion e' stato rimosso sono tutti quelli nominati in config.py.
    # I ruoli si accumulano invece di sovrascriversi: MAIN_MODEL e
    # LEARNING_MODEL sono lo stesso modello di proposito, e vanno mostrati
    # come tale, non come un modello con un ruolo solo.
    richiesti = {}
    for modello, ruolo in (
        (config.MAIN_MODEL, "conversazione"),
        (config.LEARNING_MODEL, "estrazione delle memorie"),
        (config.EMBEDDER_MODEL, "embedding delle intuizioni"),
    ):
        richiesti.setdefault(modello, []).append(ruolo)

    nomi = [m.get("name", "") for m in presenti]
    mancanti = []
    for modello, ruoli in richiesti.items():
        ruolo = " + ".join(ruoli)
        if any(stessa_etichetta(modello, n) for n in nomi):
            print("ok       ", modello, " -", ruolo)
        else:
            print("MANCANTE ", modello, " -", ruolo)
            mancanti.append(modello)

    if mancanti:
        print()
        print("Scaricali con:")
        for modello in mancanti:
            print("    ollama pull", modello)
        return 1

    print()
    python_venv = r".venv\Scripts\python.exe" if sys.platform == "win32" else ".venv/bin/python"
    print("Ambiente pronto:", python_venv, "chat.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
