"""Prova della sandbox dei comandi.

La configurazione, la riga di `bwrap`, i percorsi coperti e i rifiuti
all'avvio si provano ovunque. Il comportamento vero (scrittura fuori dalla
cartella, stato di Ares, rete, processi rimasti in background) solo dove
`bwrap` c'e' e riesce a creare i namespace: altrove la prova lo dichiara
non concludente invece di fallire.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from _comune import NON_CONCLUSIVO, chiudi, esegui, esigi, prepara_ambiente

RADICE_PROVA = prepara_ambiente("sandbox-test")

from ares import config  # noqa: E402
from ares.agent import sandbox as modulo  # noqa: E402
from ares.agent.prompts import descrizione_del_comando  # noqa: E402
from ares.agent.runtime import build_workspace  # noqa: E402
from ares.agent.sandbox import Sandbox, SandboxNonDisponibile, prepara_sandbox  # noqa: E402
from ares.state.identita import Utente  # noqa: E402

PERCORSI = replace(config.leggi_percorsi(), lavoro=Path.cwd().resolve())
POLITICA = config.leggi_politica()
CON_SANDBOX = replace(POLITICA, workspace=replace(POLITICA.workspace, sandbox="bwrap"))


def configurazione() -> str:
    esigi(config.leggi_sandbox(None) is None and config.leggi_sandbox(" 0 ") is None, "assente o 0")
    esigi(config.leggi_sandbox("bwrap") == "bwrap", "bwrap")
    for sbagliato in ("1", "firejail", "BWRAP"):
        try:
            config.leggi_sandbox(sbagliato)
        except ValueError as errore:
            esigi("ARES_SANDBOX" in str(errore), "l'errore non nomina la variabile")
        else:
            raise AssertionError("accettato: " + sbagliato)
    esigi(POLITICA.workspace.sandbox is None and not POLITICA.workspace.sandbox_rete, "accesa di serie")
    return "bwrap, 0 o niente; il resto ferma l'avvio; spenta di serie"


def riga_di_bwrap() -> str:
    cartella = Path("/progetto")
    segreto_cartella = RADICE_PROVA / "segreto"
    segreto_cartella.mkdir()
    segreto_file = RADICE_PROVA / "token"
    segreto_file.write_text("x")
    riga = Sandbox("/usr/bin/bwrap", cartella, (segreto_cartella, segreto_file), rete=False).argv(["git", "status"])
    esigi(riga[0] == "/usr/bin/bwrap" and riga[-3:] == ["--", "git", "status"], "inizio o fine: " + repr(riga))
    esigi(riga[1:4] == ["--ro-bind", "/", "/"], "la radice non e' in sola lettura")
    legame = riga.index("--bind")
    esigi(riga[legame + 1 : legame + 3] == [str(cartella)] * 2, "la cartella non e' scrivibile")
    coperta = riga.index(str(segreto_cartella))
    esigi(riga[coperta - 1] == "--tmpfs" and coperta > legame, "la directory segreta non e' coperta dopo la cartella")
    file_coperto = riga.index(str(segreto_file))
    esigi(riga[file_coperto - 2 : file_coperto] == ["--ro-bind", os.devnull], "il file segreto non e' coperto")
    esigi("--unshare-all" in riga and "--share-net" not in riga, "la rete non e' tolta")
    esigi(riga[riga.index("--unsetenv") : riga.index("--unsetenv") + 2] == ["--unsetenv", "SSH_AUTH_SOCK"], "agente")
    con_rete = Sandbox("/usr/bin/bwrap", cartella, (), rete=True).argv(["true"])
    esigi("--share-net" in con_rete, "ARES_SANDBOX_RETE non da' la rete")
    return "radice in sola lettura, cartella scrivibile, segreti coperti dopo, rete solo se chiesta"


def percorsi_coperti() -> str:
    casa = RADICE_PROVA / "casa"
    (casa / ".ssh").mkdir(parents=True)
    (casa / ".netrc").write_text("x")
    runtime = RADICE_PROVA / "runtime"
    runtime.mkdir()
    stato_ares = casa / ".ares"
    (stato_ares / "stato").mkdir(parents=True)
    percorsi = replace(PERCORSI, home=stato_ares, stato=stato_ares / "stato", backup=stato_ares / "backup")
    with patch.object(Path, "home", lambda: casa), patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
        nascosti = set(modulo._nascosti(percorsi, (RADICE_PROVA / "lavoro").resolve()))
        attesi = {(casa / ".ssh").resolve(), (casa / ".netrc").resolve(), stato_ares.resolve(), runtime.resolve()}
        esigi(attesi <= nascosti, "mancano: " + repr(attesi - nascosti))
        # Lo stato sta dentro la home di Ares, gia' coperta; il backup non esiste.
        esigi((stato_ares / "stato").resolve() not in nascosti, "coperto due volte")
        esigi(not any("backup" in str(p) for p in nascosti), "coperto un percorso inesistente")
        # Una cartella di lavoro dentro un percorso protetto lo lascia visibile.
        dentro = (casa / ".ssh" / "progetto").resolve()
        dentro.mkdir()
        esigi((casa / ".ssh").resolve() not in set(modulo._nascosti(percorsi, dentro)), "cartella nascosta")
    return "credenziali, stato e runtime coperti una volta; la cartella resta visibile"


def _rifiuto(atteso: str, *sostituzioni: Any) -> None:
    """`prepara_sandbox` con la sandbox chiesta rifiuta, e il motivo contiene `atteso`."""
    with ExitStack() as pila:
        for sostituzione in sostituzioni:
            pila.enter_context(sostituzione)
        try:
            prepara_sandbox(PERCORSI, CON_SANDBOX)
        except SandboxNonDisponibile as errore:
            esigi(atteso in str(errore), "motivo: " + str(errore))
            return
    raise AssertionError("nessun rifiuto per: " + atteso)


def rifiuti_all_avvio() -> str:
    esigi(prepara_sandbox(PERCORSI, POLITICA) is None, "una sandbox non chiesta")
    linux = patch.object(sys, "platform", "linux")
    _rifiuto("solo su Linux", patch.object(sys, "platform", "win32"))
    _rifiuto("bwrap non c'e'", linux, patch.object(shutil, "which", lambda nome: None))
    _rifiuto(
        "AppArmor",
        patch.object(sys, "platform", "linux"),
        patch.object(shutil, "which", lambda nome: "/usr/bin/bwrap"),
        patch.object(modulo, "_prova", lambda bwrap: "setting up uid map: Permission denied"),
    )
    with patch.object(sys, "platform", "win32"):
        try:
            build_workspace(PERCORSI, CON_SANDBOX)
        except SandboxNonDisponibile:
            pass
        else:
            raise AssertionError("lo spazio di lavoro nasce senza la sandbox chiesta")
    return "altro sistema, bwrap assente, namespace negati: rifiuto con il motivo, anche dal nucleo"


def avvio_della_chat() -> str:
    from ares.cli import chat

    def rifiuta(percorsi, politica):
        raise SandboxNonDisponibile("ARES_SANDBOX=bwrap ma bwrap non c'e'")

    with (
        patch.object(chat.cartella, "scegli", lambda workspace, percorsi: Path.cwd().resolve()),
        patch.object(chat.cartella, "autorizza", lambda radice, percorsi, esplicito: True),
        patch.object(chat, "prepara_sandbox", rifiuta),
        patch.object(chat, "Sessioni", side_effect=AssertionError("sessioni aperte")),
    ):
        esito = chat._apri_chat(
            percorsi=PERCORSI,
            impostazioni=config.leggi_impostazioni(),
            politica=CON_SANDBOX,
            session=None,
            utente=Utente.da_grezzo("sandbox"),
            debug=False,
            metriche=False,
            workspace=None,
            riprendi=False,
            scegli=False,
            prompt=None,
            modo=config.MODO_PREDEFINITO,
            presidiato=False,
        )
    esigi(esito == 1, "uscita: " + repr(esito))
    return "la chat esce con 1 prima di aprire una sessione"


def descrizioni() -> str:
    senza = descrizione_del_comando()
    esigi("senza sandbox" in senza, "senza sandbox: " + senza)
    chiusa = descrizione_del_comando(True, False)
    esigi(chiusa.endswith("in una sandbox: scrive solo nella cartella di lavoro e in /tmp, senza rete."), chiusa)
    aperta = descrizione_del_comando(True, True)
    esigi(aperta.endswith("e in /tmp."), aperta)
    return "una frase breve: i limiti e cosa fare arrivano con l'errore"


def comportamento_vero() -> str:
    bwrap = shutil.which("bwrap")
    if not sys.platform.startswith("linux") or bwrap is None:
        return NON_CONCLUSIVO + "bwrap non c'e' su questo sistema"
    motivo = modulo._prova(bwrap)
    if motivo is not None:
        return NON_CONCLUSIVO + "bwrap non crea i namespace qui: " + motivo
    stato = PERCORSI.stato
    stato.mkdir(parents=True, exist_ok=True)
    (stato / "segreto.txt").write_text("memoria")
    spazio = build_workspace(PERCORSI, CON_SANDBOX)
    esigi(spazio.sandbox is not None, "lo spazio di lavoro non ha la sandbox")

    def esegui_comando(riga: str) -> str:
        return spazio.run_command(["bash", "-c", riga], timeout=20)

    esigi(esegui_comando("echo dentro > scritto.txt && cat scritto.txt") == "dentro", "scrittura nella cartella")
    fuori = RADICE_PROVA / "fuori.txt"
    # Fuori dalla cartella, accanto all'archivio della prova: sotto /tmp, che
    # nella sandbox e' privata, quindi il file non deve comparire sul disco.
    esegui_comando("echo x > " + str(fuori))
    esigi(not fuori.exists(), "scrittura fuori dalla cartella arrivata sul disco")
    esigi("non aggirarlo" in esegui_comando("false"), "l'errore nella sandbox non porta l'avviso")
    esigi(esegui_comando("true") == "", "un comando riuscito porta l'avviso")
    # La home e' in sola lettura: il file non nasce, e se nascesse si toglie.
    nella_home = Path.home() / (".ares-prova-sandbox-" + str(os.getpid()))
    risposta = esegui_comando("touch " + str(nella_home))
    nata = nella_home.exists()
    nella_home.unlink(missing_ok=True)
    esigi(not nata and ("sola lettura" in risposta or "Read-only" in risposta), "scrittura nella home: " + risposta)
    esigi("memoria" not in esegui_comando("cat " + str(stato / "segreto.txt") + " 2>&1"), "stato di Ares letto")
    rete = esegui_comando("exec 3<>/dev/tcp/1.1.1.1/53 && echo aperta")
    esigi("aperta" not in rete, "rete raggiungibile: " + rete)
    traccia = Path.cwd() / "sopravvissuto"
    esegui_comando("(sleep 2; touch " + str(traccia) + ") >/dev/null 2>&1 & echo lanciato")
    time.sleep(3)
    esigi(not traccia.exists(), "un processo in background e' sopravvissuto al comando")
    return "scrive nella cartella, non fuori; stato invisibile; niente rete; il background muore col comando"


def main() -> int:
    falliti, _ = esegui(
        (
            ("configurazione", configurazione),
            ("riga di bwrap", riga_di_bwrap),
            ("percorsi coperti", percorsi_coperti),
            ("rifiuti all'avvio", rifiuti_all_avvio),
            ("avvio della chat", avvio_della_chat),
            ("descrizioni", descrizioni),
            ("comportamento vero", comportamento_vero),
        )
    )
    return chiudi(falliti, RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
