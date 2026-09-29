"""Verifica dell'ambiente prima di avviare l'agente
================================================

Uso:
    ares preflight
    ares preflight --json

Risponde a una domanda: se avvio la chat adesso, parte? Controlla che
Ollama risponda e che i modelli configurati siano scaricati; il secondo
guasto altrimenti emerge solo al primo messaggio.

I modelli cloud sono marcati come tali, perche' quel ruolo esce dalla
macchina; se mancano, il rimedio include `ollama signin`. Non accende
modelli e non scrive su disco.
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
from ares.config import Impostazioni

app = nuova_app("preflight", "Controlla che Ollama risponda e che i modelli ci siano")


def modelli_disponibili(host: str, timeout: int = 10) -> list:
    """Elenca i modelli scaricati sul server Ollama.

    Solleva `urllib.error.URLError` se il server non risponde, per distinguere
    "server spento" da "modello mancante".
    """
    with urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=timeout) as r:
        return json.load(r).get("models", [])


def stessa_etichetta(richiesto: str, presente: str) -> bool:
    """Confronta due nomi di modello ignorando il tag implicito `:latest`."""

    def normalizza(nome: str) -> str:
        return nome if ":" in nome else nome + ":latest"

    return normalizza(richiesto) == normalizza(presente)


def esamina(impostazioni: Impostazioni) -> dict[str, Any]:
    """Il preflight come dati: cosa serve, cosa c'e', cosa manca.

    Separato dalla stampa perche' `--json` e la tabella dicano le stesse cose
    e il verdetto `pronto` si decida una volta. `impostazioni` e' la
    conversazione che si sta per avviare.
    """
    esito: dict[str, Any] = {
        "server": impostazioni.host,
        "raggiungibile": False,
        "modelli_scaricati": None,
        "modelli": [],
        "mancanti": [],
        "avviso_cloud": impostazioni.avviso_cloud(),
        "pronto": False,
        "errore": None,
    }
    try:
        presenti = modelli_disponibili(impostazioni.host)
    except (urllib.error.URLError, OSError) as e:
        esito["errore"] = str(e)
        return esito
    esito["raggiungibile"] = True
    esito["modelli_scaricati"] = len(presenti)

    # I ruoli si accumulano: lo stesso modello per conversazione ed
    # estrazione si mostra con entrambi.
    richiesti: dict[str, list[str]] = {}
    for modello, ruolo in (
        (impostazioni.principale, "conversazione"),
        (impostazioni.apprendimento, "estrazione delle memorie"),
        (impostazioni.embedder, "embedding delle intuizioni"),
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
    esito = esamina(config.leggi_impostazioni())
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
