"""
Verifica dell'ambiente prima di avviare l'agente
================================================
Uso:
    ares preflight

Risponde a una domanda sola: se avvio la chat adesso, parte? Controlla che
il server Ollama risponda e che i modelli nominati in `config.py` siano
davvero scaricati. Sono i due modi in cui l'avvio fallisce, e il secondo
non da' errore finche' non arriva il primo messaggio.

Un modello cloud compare nell'elenco del daemon come gli altri, dopo un
`ollama pull` che scarica solo il manifesto; qui viene marcato come tale,
perche' chi legge "ok" deve sapere che quel ruolo esce dalla macchina. Se
manca, il comando per rimediare include `ollama signin`: senza l'accesso il
pull riesce ma la prima richiesta no.

Non accende nessun modello e non lascia niente su disco: legge da `config`,
il cui import non crea piu' nulla, e non chiama `prepara_archivio()`. Un
comando che deve dire se l'ambiente funziona non e' il posto giusto per
creare l'archivio.
"""

import json
import sys
import urllib.error
import urllib.request

from ares import config
from ares.cli.comando import nuova_app

app = nuova_app("preflight", "Controlla che Ollama risponda e che i modelli ci siano")


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

    def normalizza(nome: str) -> str:
        return nome if ":" in nome else nome + ":latest"

    return normalizza(richiesto) == normalizza(presente)


@app.default
def controlla() -> int:
    """Se avvio la chat adesso, parte? Server, modelli e avvisi sul cloud."""
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
    # I ruoli si accumulano invece di sovrascriversi: con MAIN_MODEL locale
    # conviene che LEARNING_MODEL sia lo stesso modello, e allora va mostrato
    # con entrambi i ruoli, non come un modello con un ruolo solo. Lo stesso
    # vale con lo stesso modello cloud in entrambi.
    richiesti: dict[str, list[str]] = {}
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
        if config.e_modello_cloud(modello):
            ruolo += " (cloud, via ollama.com)"
        if any(stessa_etichetta(modello, n) for n in nomi):
            print("ok       ", modello, " -", ruolo)
        else:
            print("MANCANTE ", modello, " -", ruolo)
            mancanti.append(modello)

    if mancanti:
        print()
        print("Scaricali con:")
        if any(config.e_modello_cloud(m) for m in mancanti):
            print("    ollama signin")
        for modello in mancanti:
            print("    ollama pull", modello)
        return 1

    print()
    avviso = config.avviso_cloud()
    if avviso:
        for riga in avviso:
            print(riga)
        print()
    print("Ambiente pronto:", config.comando_ares())
    return 0


def main() -> int:
    from ares.cli.app import esegui

    return esegui("preflight")


if __name__ == "__main__":
    sys.exit(main())
