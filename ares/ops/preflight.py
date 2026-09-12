"""
Verifica dell'ambiente prima di avviare l'agente
================================================
Uso:
    ares preflight
    ares preflight --json

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
from collections.abc import Sequence
from typing import Annotated, Any

from cyclopts import Parameter

from ares import config
from ares.cli.comando import ESITO_FATTO, ESITO_GUASTO, nuova_app
from ares.cli.ui import UI

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


def esamina() -> dict[str, Any]:
    """Il preflight come dati: cosa serve, cosa c'e', cosa manca.

    Separato dalla stampa perche' `--json` e la tabella devono dire le
    stesse cose, e perche' il verdetto - `pronto` - va deciso una volta.
    """
    esito: dict[str, Any] = {
        "server": config.OLLAMA_HOST,
        "raggiungibile": False,
        "modelli_scaricati": None,
        "modelli": [],
        "mancanti": [],
        "avviso_cloud": config.avviso_cloud(),
        "pronto": False,
        "errore": None,
    }
    try:
        presenti = modelli_disponibili(config.OLLAMA_HOST)
    except (urllib.error.URLError, OSError) as e:
        esito["errore"] = str(e)
        return esito
    esito["raggiungibile"] = True
    esito["modelli_scaricati"] = len(presenti)

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
    for modello, ruoli in richiesti.items():
        presente = any(stessa_etichetta(modello, n) for n in nomi)
        esito["modelli"].append(
            {"modello": modello, "ruoli": ruoli, "cloud": config.e_modello_cloud(modello), "presente": presente}
        )
        if not presente:
            esito["mancanti"].append(modello)
    esito["pronto"] = not esito["mancanti"]
    return esito


def _ruolo(voce: dict[str, Any]) -> str:
    ruolo = " + ".join(voce["ruoli"])
    if voce["cloud"]:
        ruolo += " (cloud, via ollama.com)"
    return ruolo


@app.default
def controlla(*, come_json: Annotated[bool, Parameter(name="--json")] = False) -> int:
    """Se avvio la chat adesso, parte? Server, modelli e avvisi sul cloud.

    Args:
        come_json: stampa l'esito come JSON, per gli script; il codice di uscita non cambia.
    """
    esito = esamina()
    if come_json:
        UI.json(esito)
        return ESITO_FATTO if esito["pronto"] else ESITO_GUASTO

    UI.pair("Server", esito["server"])
    if not esito["raggiungibile"]:
        UI.line("  non raggiungibile: " + str(esito["errore"]), style="ares.error")
        UI.blank()
        UI.line("Avvia il server con: ollama serve", style="ares.muted")
        return ESITO_GUASTO
    UI.line("  raggiungibile, " + str(esito["modelli_scaricati"]) + " modelli scaricati", style="ares.success")
    UI.blank()

    UI.table(
        (("stato", "ares.text"), "modello", ("ruolo", "ares.muted")),
        ((("ok" if voce["presente"] else "MANCANTE"), voce["modello"], _ruolo(voce)) for voce in esito["modelli"]),
    )

    mancanti = esito["mancanti"]
    if mancanti:
        UI.blank()
        UI.line("Scaricali con:", style="ares.warning")
        if any(config.e_modello_cloud(m) for m in mancanti):
            UI.line("    ollama signin")
        for modello in mancanti:
            UI.line("    ollama pull " + modello)
        return ESITO_GUASTO

    UI.blank()
    if esito["avviso_cloud"]:
        for riga in esito["avviso_cloud"]:
            UI.line(riga, style="ares.warning")
        UI.blank()
    UI.line("Ambiente pronto: " + config.comando_ares(), style="ares.success")
    return ESITO_FATTO


def main(argomenti: Sequence[str] | None = None) -> int:
    from ares.cli.app import esegui

    return esegui("preflight", argomenti)


if __name__ == "__main__":
    sys.exit(main())
