"""Verifica dell'ambiente prima di avviare l'agente
================================================

Uso:
    ares preflight
    ares preflight --json

Risponde a una domanda: se avvio la chat adesso, parte? Controlla che
Ollama risponda e che i modelli configurati siano scaricati; il secondo
guasto altrimenti emerge solo al primo messaggio.

I modelli cloud sono marcati come tali, perche' quel ruolo esce dalla
macchina; se mancano, il rimedio include `ollama signin`. Un modello locale
gia' caricato con il contesto di Ares che non sta tutto in VRAM e' un
avviso: gira in parte sulla CPU, e risposta ed estrazione rallentano di
molto. Non accende modelli e non scrive su disco, quindi un modello spento
non si puo' misurare.
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


def modelli_caricati(host: str, timeout: int = 10) -> list:
    """I modelli che Ollama tiene in memoria adesso, con quanto ne sta in VRAM (`/api/ps`)."""
    with urllib.request.urlopen(host.rstrip("/") + "/api/ps", timeout=timeout) as r:
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
    esito["memoria"] = _memoria(impostazioni)
    return esito


def _memoria(impostazioni: Impostazioni) -> dict[str, Any]:
    """Dove stanno i modelli locali caricati con il contesto che Ares chiede.

    Un modello caricato con un altro contesto, da un altro programma o da
    un'altra configurazione, non dice niente su questa: conta come spento.
    Un Ollama che non risponde a `/api/ps` non aggiunge niente.
    """
    chiesti: dict[str, int] = {}
    for modello, contesto in (
        (impostazioni.principale, impostazioni.num_ctx),
        (impostazioni.apprendimento, impostazioni.num_ctx_apprendimento),
    ):
        if not config.e_modello_cloud(modello):
            chiesti.setdefault(modello, contesto)
    memoria: dict[str, Any] = {"caricati": [], "spenti": []}
    try:
        caricati = modelli_caricati(impostazioni.host)
    except (urllib.error.URLError, OSError, ValueError):
        return memoria
    for modello, contesto in chiesti.items():
        voce = next(
            (
                m
                for m in caricati
                if stessa_etichetta(modello, m.get("name", "")) and m.get("context_length") == contesto
            ),
            None,
        )
        if voce is None:
            memoria["spenti"].append(modello)
            continue
        totale, in_vram = int(voce.get("size") or 0), int(voce.get("size_vram") or 0)
        memoria["caricati"].append(
            {
                "modello": modello,
                "contesto": contesto,
                "byte": totale,
                "byte_in_vram": in_vram,
                "tutto_in_vram": in_vram >= totale,
            }
        )
    return memoria


def righe_memoria(memoria: dict[str, Any]) -> list[tuple[str, str]]:
    """Gli avvisi sulla VRAM, con il loro stile; vuoto se tutto sta in scheda o non si sa."""
    righe: list[tuple[str, str]] = []
    for voce in memoria["caricati"]:
        if voce["tutto_in_vram"]:
            continue
        if voce["byte_in_vram"] == 0:
            righe.append((voce["modello"] + " gira tutto sulla CPU: le risposte saranno lente.", "ares.warning"))
            continue
        fuori = round(100 * (1 - voce["byte_in_vram"] / voce["byte"]))
        righe.append(
            (
                voce["modello"]
                + ": il "
                + str(fuori)
                + "% gira sulla CPU con "
                + str(voce["contesto"])
                + " token di contesto.",
                "ares.warning",
            )
        )
        righe.append(("Abbassa ARES_NUM_CTX nel .env finche' sta tutto in VRAM.", "ares.muted"))
    if memoria["spenti"]:
        righe.append(("La VRAM si misura a modello acceso: rilancia il preflight dopo un turno di chat.", "ares.muted"))
    return righe


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
    memoria = righe_memoria(esito["memoria"])
    for riga, stile in memoria:
        UI.line(riga, style=stile)
    if memoria:
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
