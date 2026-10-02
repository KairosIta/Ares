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

Un modello locale col ragionamento acceso che non dichiara un `RENDERER`, e
il cui template non legge `.Thinking`, e' un altro avviso: Ollama non gli
rimanda il ragionamento dei passi precedenti, e dopo uno strumento il 9B di
serie risponde dentro il ragionamento lasciando vuota la risposta. Capita ai
GGUF importati da Hugging Face, che arrivano senza renderer.

Con `ARES_SANDBOX=bwrap` controlla anche che la sandbox si possa applicare:
se no la chat non parte, quindi l'ambiente non e' pronto.
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


def scheda_modello(host: str, modello: str, timeout: int = 10) -> dict:
    """Modelfile, template e capacita' di un modello scaricato (`/api/show`)."""
    richiesta = urllib.request.Request(
        host.rstrip("/") + "/api/show",
        data=json.dumps({"model": modello}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(richiesta, timeout=timeout) as r:
        return json.load(r)


def rimanda_il_ragionamento(scheda: dict) -> bool:
    """Vero se Ollama da' al modello il ragionamento dei passi precedenti.

    Lo fa un renderer di Ollama, o un template Go che legge `.Thinking`; il
    template jinja di un GGUF importato cerca invece `reasoning_content`, che
    Ollama non gli passa.
    """
    righe = str(scheda.get("modelfile") or "").splitlines()
    return any(riga.startswith("RENDERER ") for riga in righe) or ".Thinking" in str(scheda.get("template") or "")


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
        "sandbox": _sandbox(),
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
    esito["pronto"] = not esito["mancanti"] and esito["sandbox"]["motivo"] is None
    esito["memoria"] = _memoria(impostazioni)
    esito["senza_renderer"] = _senza_renderer(impostazioni, esito["mancanti"])
    return esito


def _senza_renderer(impostazioni: Impostazioni, mancanti: list[str]) -> list[str]:
    """I modelli locali col ragionamento acceso a cui Ollama non lo rimanda.

    Un modello mancante o una scheda illeggibile non aggiungono niente: il
    preflight non afferma cio' che non ha visto.
    """
    accesi: list[str] = []
    for modello, acceso in (
        (impostazioni.principale, impostazioni.think),
        (impostazioni.apprendimento, impostazioni.think_apprendimento),
    ):
        if acceso and not config.e_modello_cloud(modello) and modello not in mancanti and modello not in accesi:
            accesi.append(modello)
    senza: list[str] = []
    for modello in accesi:
        try:
            scheda = scheda_modello(impostazioni.host, modello)
        except (urllib.error.URLError, OSError, ValueError):
            continue
        if not rimanda_il_ragionamento(scheda):
            senza.append(modello)
    return senza


def _sandbox() -> dict[str, Any]:
    """La sandbox dei comandi: se e' chiesta e, se si', se si puo' applicare qui."""
    from ares.agent.sandbox import SandboxNonDisponibile, prepara_sandbox

    politica = config.leggi_politica()
    voce: dict[str, Any] = {"chiesta": politica.workspace.sandbox, "rete": politica.workspace.sandbox_rete}
    try:
        prepara_sandbox(config.leggi_percorsi(), politica)
        voce["motivo"] = None
    except SandboxNonDisponibile as errore:
        voce["motivo"] = str(errore)
    return voce


def righe_ragionamento(senza_renderer: list[str]) -> list[tuple[str, str]]:
    """L'avviso sui modelli senza renderer, con il suo stile; vuoto se non ce ne sono."""
    righe: list[tuple[str, str]] = []
    for modello in senza_renderer:
        righe.append(
            (
                modello + " non dichiara un RENDERER: Ollama non gli rimanda il ragionamento, "
                "e dopo uno strumento la risposta puo' restare vuota.",
                "ares.warning",
            )
        )
    if senza_renderer:
        righe.append(('Crea una copia con RENDERER e PARSER: vedi "Modello locale" nel README.', "ares.muted"))
    return righe


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
    sandbox = esito["sandbox"]
    if sandbox["chiesta"]:
        UI.pair("Sandbox", sandbox["chiesta"] + (", con la rete" if sandbox["rete"] else ", senza rete"))
        if sandbox["motivo"] is not None:
            UI.line("  " + sandbox["motivo"], style="ares.error")
            return ESITO_GUASTO
        UI.line("  applicabile", style="ares.success")
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
    avvisi = righe_memoria(esito["memoria"]) + righe_ragionamento(esito["senza_renderer"])
    for riga, stile in avvisi:
        UI.line(riga, style=stile)
    if avvisi:
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
