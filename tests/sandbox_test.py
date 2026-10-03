"""Prova della sandbox dei comandi.

La configurazione, la riga di `bwrap`, i percorsi coperti e protetti, i
rifiuti all'avvio e l'avviso si provano ovunque. Il comportamento vero
(scrittura fuori dalla cartella, `.git` e regole del progetto, `/run`, stato
di Ares, rete, processi rimasti in background) solo dove `bwrap` c'e' e
riesce a creare i namespace: altrove la prova lo dichiara non concludente
invece di fallire.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
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
from ares.agent.marcatura import FINE, ConNota, marca_risultati  # noqa: E402
from ares.agent.prompts import (  # noqa: E402
    AVVISO_SANDBOX,
    descrizione_del_comando,
    istruzioni_sugli_strumenti,
    istruzioni_sull_ambiente,
    limiti_dei_comandi,
)
from ares.agent.runtime import build_workspace  # noqa: E402
from ares.agent.sandbox import Sandbox, SandboxNonDisponibile, prepara_sandbox  # noqa: E402
from ares.cli import cartella as modulo_cartella  # noqa: E402
from ares.state.identita import Utente  # noqa: E402

PERCORSI = replace(config.leggi_percorsi(), lavoro=Path.cwd().resolve())
POLITICA = config.leggi_politica()
CON_SANDBOX = replace(POLITICA, workspace=replace(POLITICA.workspace, sandbox="bwrap"))
# Per i rifiuti che vengono dopo i controlli sul sistema.
SISTEMA_PRONTO = (
    patch.object(sys, "platform", "linux"),
    patch.object(shutil, "which", lambda nome: "/usr/bin/bwrap"),
    patch.object(modulo, "_prova", lambda bwrap: None),
)


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
    # Risolti come li risolve la sandbox: su Windows la temp ha nomi brevi (RUNNER~1).
    segreto_cartella = RADICE_PROVA.resolve() / "segreto"
    segreto_cartella.mkdir()
    segreto_file = RADICE_PROVA.resolve() / "token"
    segreto_file.write_text("x")
    riga = Sandbox("/usr/bin/bwrap", cartella, (segreto_cartella, segreto_file), rete=False).argv(["git", "status"])
    esigi(riga[0] == "/usr/bin/bwrap" and riga[-3:] == ["--", "git", "status"], "inizio o fine: " + repr(riga))
    esigi(riga[1:4] == ["--ro-bind", "/", "/"], "la radice non e' in sola lettura")
    legame = riga.index("--bind")
    esigi(riga[legame + 1 : legame + 3] == [str(cartella)] * 2, "la cartella non e' scrivibile")
    run = riga.index("/run")
    esigi(riga[run - 1] == "--tmpfs" and run < legame, "/run non e' vuota, o e' vuotata dopo la cartella")
    coperta = riga.index(str(segreto_cartella))
    esigi(riga[coperta - 1] == "--tmpfs" and coperta > legame, "la directory segreta non e' coperta dopo la cartella")
    file_coperto = riga.index(str(segreto_file))
    esigi(riga[file_coperto - 2 : file_coperto] == ["--ro-bind", os.devnull], "il file segreto non e' coperto")
    esigi("--unshare-all" in riga and "--share-net" not in riga, "la rete non e' tolta")
    esigi(riga[riga.index("--unsetenv") : riga.index("--unsetenv") + 2] == ["--unsetenv", "SSH_AUTH_SOCK"], "agente")
    con_rete = Sandbox("/usr/bin/bwrap", cartella, (), rete=True).argv(["true"])
    esigi("--share-net" in con_rete, "ARES_SANDBOX_RETE non da' la rete")
    # Con systemd-resolved /etc/resolv.conf porta sotto /run: con la rete si rimonta.
    with patch.object(modulo, "_risoluzione_dei_nomi", lambda: ("/run/systemd/resolve",)):
        con_nomi = Sandbox("/usr/bin/bwrap", cartella, (), rete=True).argv(["true"])
        senza_nomi = Sandbox("/usr/bin/bwrap", cartella, (), rete=False).argv(["true"])
    indice = con_nomi.index("/run/systemd/resolve")
    esigi(con_nomi[indice - 1] == "--ro-bind-try" and indice > con_nomi.index("/run"), "risoluzione dei nomi")
    esigi("/run/systemd/resolve" not in senza_nomi, "risoluzione dei nomi senza la rete")
    return "radice in sola lettura, /run vuota, cartella scrivibile, segreti coperti dopo, rete solo se chiesta"


def percorsi_coperti() -> str:
    casa = RADICE_PROVA / "casa-coperta"
    (casa / ".ssh").mkdir(parents=True)
    (casa / ".netrc").write_text("x")
    (casa / ".ollama").mkdir()
    (casa / ".ollama" / "id_ed25519").write_text("chiave")
    runtime = RADICE_PROVA / "runtime"
    runtime.mkdir()
    stato_ares = casa / ".ares"
    (stato_ares / "stato").mkdir(parents=True)
    percorsi = replace(PERCORSI, home=stato_ares, stato=stato_ares / "stato", backup=stato_ares / "backup")
    lavoro = (RADICE_PROVA / "lavoro").resolve()
    with patch.object(Path, "home", lambda: casa), patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}):
        nascosti = set(modulo._nascosti(percorsi, lavoro))
        attesi = {
            (casa / ".ssh").resolve(),
            (casa / ".netrc").resolve(),
            (casa / ".ollama" / "id_ed25519").resolve(),
            stato_ares.resolve(),
            runtime.resolve(),
        }
        esigi(attesi <= nascosti, "mancano: " + repr(attesi - nascosti))
        # Lo stato sta dentro la home di Ares, gia' coperta; il backup non esiste.
        esigi((stato_ares / "stato").resolve() not in nascosti, "coperto due volte")
        esigi(not any("backup" in str(p) for p in nascosti), "coperto un percorso inesistente")
        # Una cartella di lavoro dentro un percorso protetto lo lascia visibile.
        dentro = (casa / ".ssh" / "progetto").resolve()
        dentro.mkdir()
        esigi((casa / ".ssh").resolve() not in set(modulo._nascosti(percorsi, dentro)), "cartella nascosta")
        # Un percorso protetto nato dopo l'avvio si copre dal comando dopo.
        sandbox = Sandbox("/usr/bin/bwrap", lavoro, modulo._candidati(percorsi), rete=False)
        esigi((casa / ".aws").resolve() not in sandbox.nascosti(), "coperto prima di esistere")
        (casa / ".aws").mkdir()
        esigi((casa / ".aws").resolve() in sandbox.nascosti(), "un percorso nato dopo l'avvio non e' coperto")
    if not hasattr(os, "getuid"):
        return "credenziali, stato e runtime coperti una volta e se nascono dopo (niente /run/user qui)"
    # Senza XDG_RUNTIME_DIR (su, cron, ssh) la directory di runtime c'e' lo stesso.
    di_serie = Path("/run/user") / str(os.getuid())
    ambiente = {k: v for k, v in os.environ.items() if k != "XDG_RUNTIME_DIR"}
    with patch.dict(os.environ, ambiente, clear=True):
        esigi(di_serie in modulo._candidati(percorsi), "senza XDG_RUNTIME_DIR /run/user/<uid> resta visibile")
        if di_serie.exists():
            esigi(di_serie.resolve() in modulo._nascosti(percorsi, lavoro), "/run/user/<uid> non coperta")
    return "credenziali, stato e runtime coperti una volta, anche senza XDG_RUNTIME_DIR e se nascono dopo"


def sola_lettura_nella_cartella() -> str:
    progetto = (RADICE_PROVA / "progetto").resolve()
    (progetto / ".git").mkdir(parents=True)
    (progetto / "ARES.md").write_text("regole")
    nomi = modulo._sola_lettura(CON_SANDBOX)
    esigi(set(nomi) == {".git", "ARES.md", ".ares"}, "nomi protetti: " + repr(nomi))
    sandbox = Sandbox("/usr/bin/bwrap", progetto, (), rete=False, sola_lettura=nomi)
    esigi(set(sandbox.protetti()) == {progetto / ".git", progetto / "ARES.md"}, repr(sandbox.protetti()))
    # Chi nasce dopo e' protetto dal comando successivo.
    (progetto / ".ares").mkdir()
    esigi(progetto / ".ares" in sandbox.protetti(), ".ares nata dopo non e' protetta")
    riga = sandbox.argv(["true"])
    legame = riga.index("--bind")
    for percorso in sandbox.protetti():
        indice = riga.index(str(percorso))
        esigi(riga[indice - 1] == "--ro-bind-try" and indice > legame, "non in sola lettura: " + str(percorso))
    # Un worktree: il `.git` e' un file, e la directory a cui punta va protetta se e' nella cartella.
    albero = (RADICE_PROVA / "albero").resolve()
    (albero / "interno" / "comune").mkdir(parents=True)
    (albero / "interno" / "dir").mkdir()
    (albero / "interno" / "dir" / "commondir").write_text("../comune\n")
    (albero / ".git").write_text("gitdir: interno/dir\n")
    protetti = set(Sandbox("/usr/bin/bwrap", albero, (), rete=False).protetti())
    attesi = {albero / ".git", albero / "interno" / "dir", albero / "interno" / "comune"}
    esigi(protetti == attesi, "worktree: " + repr(protetti))
    esterno = (RADICE_PROVA / "esterno-git").resolve()
    esterno.mkdir()
    (albero / ".git").write_text("gitdir: " + str(esterno) + "\n")
    esigi(
        set(Sandbox("/usr/bin/bwrap", albero, (), rete=False).protetti()) == {albero / ".git"},
        "una directory git fuori dalla cartella non serve proteggerla: e' gia' in sola lettura",
    )
    return ".git, ARES.md e .ares in sola lettura, anche se nascono dopo; worktree seguito nella cartella"


def _rifiuto(atteso: str, *sostituzioni: Any, percorsi: Any = None) -> None:
    """`prepara_sandbox` con la sandbox chiesta rifiuta, e il motivo contiene `atteso`."""
    with ExitStack() as pila:
        for sostituzione in sostituzioni:
            pila.enter_context(sostituzione)
        try:
            prepara_sandbox(percorsi or PERCORSI, CON_SANDBOX)
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
    # La home, o una cartella che la contiene, sarebbe tutta scrivibile.
    for larga in (Path.home(), Path.home().parent):
        _rifiuto("contiene la tua home", *SISTEMA_PRONTO, percorsi=replace(PERCORSI, lavoro=larga))
    with ExitStack() as pila:
        for sostituzione in SISTEMA_PRONTO:
            pila.enter_context(sostituzione)
        esigi(prepara_sandbox(PERCORSI, CON_SANDBOX) is not None, "rifiutata una cartella fuori dalla home")
    with patch.object(sys, "platform", "win32"):
        try:
            build_workspace(PERCORSI, CON_SANDBOX)
        except SandboxNonDisponibile:
            pass
        else:
            raise AssertionError("lo spazio di lavoro nasce senza la sandbox chiesta")
    return "altro sistema, bwrap assente, namespace negati, home scrivibile: rifiuto con il motivo, anche dal nucleo"


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
    esigi(senza.endswith(limiti_dei_comandi(False, False)) and "senza sandbox" in senza, "senza sandbox: " + senza)
    chiusa = limiti_dei_comandi(True, False)
    esigi(
        "si svuota dopo ogni comando" in chiusa and ".git, ARES.md e .ares" in chiusa and chiusa.endswith("rete."),
        chiusa,
    )
    esigi(limiti_dei_comandi(True, True).endswith("sola lettura."), limiti_dei_comandi(True, True))
    esigi(descrizione_del_comando(True, False).endswith(chiusa), "la descrizione non usa la frase comune")
    # Una sola fonte: la stessa frase nella descrizione, nella scheda dell'ambiente e negli strumenti.
    strumenti = " ".join(istruzioni_sugli_strumenti(Path.cwd(), politica=CON_SANDBOX))
    esigi(chiusa in strumenti, "sezione strumenti: " + strumenti)
    ambiente = " ".join(istruzioni_sull_ambiente(impostazioni=config.leggi_impostazioni(), politica=CON_SANDBOX))
    esigi(chiusa in ambiente, "scheda dell'ambiente: " + ambiente)
    esigi(limiti_dei_comandi(False, False) in " ".join(istruzioni_sugli_strumenti(Path.cwd(), politica=POLITICA)), "")
    return "una frase sola per i tre posti, con la /tmp che si svuota e i file in sola lettura"


def avviso() -> str:
    cartella = (RADICE_PROVA / "lavoro").resolve()
    chiusa = Sandbox("/usr/bin/bwrap", cartella, (), rete=False)
    aperta = Sandbox("/usr/bin/bwrap", cartella, (), rete=True)
    colpa = (
        "touch: cannot touch '/home/x/f': Read-only file system",
        "bash: riga 1: /home/x/f: File system di sola lettura",
        "cat: /root/segreto: Permission denied",
        "curl: (6) Could not resolve host: pypi.org",
        "ping: connect: Network is unreachable",
        "ping: connect: La rete non \u00e8 raggiungibile",
        "touch: impossibile fare touch '/home/x/f': File system in sola lettura",
    )
    for errore in colpa:
        esigi(chiusa.forse_colpa_sua(errore), "non riconosciuto: " + errore)
    innocenti = (
        "",
        "FAILED tests/test_x.py::test_y - AssertionError",
        "cat: " + str(cartella / "privato") + ": Permission denied",
        "grep: nessuna corrispondenza",
    )
    for errore in innocenti:
        esigi(not chiusa.forse_colpa_sua(errore), "avviso per un errore qualunque: " + errore)
    esigi(not aperta.forse_colpa_sua("Could not resolve host: x"), "la rete accusata quando c'e'")
    # La nota resta fuori dal blocco dei dati.
    nota = AVVISO_SANDBOX.format(rete="")
    risultato = ConNota("Errore (uscita 1).\nRead-only file system", nota)
    esigi(str(risultato).endswith("\n" + nota), "senza marcatura la nota si perde")
    marcatore = marca_risultati(POLITICA.workspace.prefisso)
    marcato = marcatore(
        function_name=POLITICA.workspace.prefisso + "run_command",
        function_call=lambda **argomenti: risultato,
        arguments={"args": ["touch", "/home/x/f"]},
    )
    chiusura = marcato.index(FINE)
    esigi(marcato.endswith("\n" + nota) and marcato.index(nota) > chiusura, "nota nel blocco: " + marcato)
    esigi("Read-only" in marcato[:chiusura], "i dati fuori dal blocco")
    return "nota solo per sola lettura, permessi fuori dalla cartella e rete spenta; fuori dal blocco dei dati"


def git_dell_host() -> str:
    """Il `git status` di `/cartella` non esegue l'fsmonitor che un comando puo' aver scritto."""
    if shutil.which("git") is None:
        return NON_CONCLUSIVO + "git non c'e'"
    repo = (RADICE_PROVA / "repo-ostile").resolve()
    repo.mkdir()
    traccia = RADICE_PROVA / "fsmonitor-eseguito"
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "core.fsmonitor", "touch " + str(traccia) + "; false"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / ".git" / "hooks" / "post-index-change").write_text("#!/bin/sh\ntouch " + str(traccia) + "\n")
    (repo / ".git" / "hooks" / "post-index-change").chmod(0o755)
    (repo / "nuovo.txt").write_text("x")
    esigi(modulo_cartella.file_modificati(repo) == 1, "il conteggio e' sbagliato")
    esigi(not traccia.exists(), "git status ha eseguito fsmonitor o un hook del repository")
    return "fsmonitor e hook spenti sulla riga di git"


def comportamento_vero() -> str:
    bwrap = shutil.which("bwrap")
    if not sys.platform.startswith("linux") or bwrap is None:
        return NON_CONCLUSIVO + "bwrap non c'e' su questo sistema"
    motivo = modulo._prova(bwrap)
    if motivo is not None:
        return NON_CONCLUSIVO + "bwrap non crea i namespace qui: " + motivo
    # Fuori da /tmp, che nella sandbox e' gia' privata: altrimenti lo stato
    # nascosto e la scrittura fuori dalla cartella non proverebbero niente.
    cache = Path.home() / ".cache"
    cache.mkdir(exist_ok=True)
    esterno = Path(tempfile.mkdtemp(prefix="ares-sandbox-prova-", dir=cache)).resolve()
    try:
        return _comportamento_vero(esterno)
    finally:
        shutil.rmtree(esterno, ignore_errors=True)


def _comportamento_vero(esterno: Path) -> str:
    stato = esterno / "stato"
    stato.mkdir()
    (stato / "segreto.txt").write_text("memoria")
    percorsi = replace(PERCORSI, stato=stato)
    lavoro = PERCORSI.lavoro
    (lavoro / "ARES.md").write_text("regole vere")
    spazio = build_workspace(percorsi, CON_SANDBOX)
    sandbox = spazio.sandbox
    esigi(sandbox is not None, "lo spazio di lavoro non ha la sandbox")
    assert sandbox is not None

    def esegui_comando(riga: str) -> str:
        return spazio.run_command(["bash", "-c", riga], timeout=20)

    esigi(esegui_comando("echo dentro > scritto.txt && cat scritto.txt") == "dentro", "scrittura nella cartella")
    fuori = esterno / "fuori.txt"
    risposta = esegui_comando("echo x > " + str(fuori))
    esigi(not fuori.exists(), "scrittura fuori dalla cartella arrivata sul disco")
    esigi(isinstance(risposta, ConNota), "la scrittura fuori dalla cartella non porta l'avviso: " + risposta)
    esigi(not isinstance(esegui_comando("false"), ConNota), "un errore qualunque porta l'avviso")
    esigi(not isinstance(esegui_comando("grep -q assente scritto.txt"), ConNota), "un grep vuoto porta l'avviso")
    esigi(esegui_comando("true") == "", "un comando riuscito porta l'avviso")
    # La home e' in sola lettura: il file non nasce, e se nascesse si toglie.
    nella_home = Path.home() / (".ares-prova-sandbox-" + str(os.getpid()))
    risposta = esegui_comando("touch " + str(nella_home))
    nata = nella_home.exists()
    nella_home.unlink(missing_ok=True)
    esigi(not nata and isinstance(risposta, ConNota), "scrittura nella home: " + risposta)
    esigi("memoria" not in esegui_comando("cat " + str(stato / "segreto.txt") + " 2>&1"), "stato di Ares letto")
    # Le regole del progetto e .git: in sola lettura, anche se .git nasce durante la sessione.
    esegui_comando("echo ordine >> ARES.md")
    esigi((lavoro / "ARES.md").read_text() == "regole vere", "ARES.md scritto da un comando")
    esegui_comando("mkdir -p .git/hooks")
    esegui_comando("echo '[core]' > .git/config; touch .git/hooks/post-index-change")
    esigi(not (lavoro / ".git" / "config").exists(), ".git scritta da un comando dopo essere nata")
    esegui_comando("mkdir .ares")
    esegui_comando("echo 'consenti = [\"bash\"]' > .ares/permessi.toml")
    esigi(not (lavoro / ".ares" / "permessi.toml").exists(), ".ares scritta da un comando dopo essere nata")
    # /run e' vuota: niente bus di sistema, niente socket della sessione, anche senza XDG_RUNTIME_DIR.
    sociali = "/run/dbus/system_bus_socket /run/user/" + str(os.getuid()) + "/bus /run/docker.sock"
    visibili = esegui_comando("for s in " + sociali + "; do test -e $s && echo $s; done; true")
    esigi(visibili == "", "socket raggiungibili: " + visibili)
    rete = esegui_comando("exec 3<>/dev/tcp/1.1.1.1/53 && echo aperta")
    esigi("aperta" not in rete, "rete raggiungibile: " + rete)
    traccia = lavoro / "sopravvissuto"
    esegui_comando("(sleep 2; touch " + str(traccia) + ") >/dev/null 2>&1 & echo lanciato")
    time.sleep(3)
    esigi(not traccia.exists(), "un processo in background e' sopravvissuto al comando")
    if shutil.which("git") is not None:
        # Un repository nato nella sessione, con un fsmonitor ostile: il `git status`
        # di `/cartella` gira nella sandbox e non lo esegue fuori.
        shutil.rmtree(lavoro / ".git")
        segno = esterno / "fsmonitor"
        esegui_comando("git init -q && git config core.fsmonitor 'touch " + str(segno) + "; false'")
        esigi(modulo_cartella.file_modificati(lavoro, sandbox) is not None, "git status nella sandbox non risponde")
        esigi(not segno.exists(), "git status ha eseguito fuori il fsmonitor scritto da un comando")
    return (
        "scrive nella cartella, non fuori ne' in .git, ARES.md e .ares; stato e /run invisibili; niente rete; "
        "il background muore col comando; avviso solo quando serve"
    )


def main() -> int:
    falliti, _ = esegui(
        (
            ("configurazione", configurazione),
            ("riga di bwrap", riga_di_bwrap),
            ("percorsi coperti", percorsi_coperti),
            ("sola lettura nella cartella", sola_lettura_nella_cartella),
            ("rifiuti all'avvio", rifiuti_all_avvio),
            ("avvio della chat", avvio_della_chat),
            ("descrizioni", descrizioni),
            ("avviso", avviso),
            ("git dell'host", git_dell_host),
            ("comportamento vero", comportamento_vero),
        )
    )
    return chiudi(falliti, RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
