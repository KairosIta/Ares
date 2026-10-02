"""La sandbox dei comandi: `run_command` dentro bubblewrap, su Linux, se la persona la chiede.

Senza sandbox un comando gira con i permessi dell'utente (SECURITY.md). Con
`ARES_SANDBOX=bwrap` il comando parte dentro `bwrap` con:

- il filesystem in sola lettura, tranne la cartella di lavoro e una `/tmp`
  privata, che sparisce con il comando;
- lo stato di Ares, il suo `.env` e le credenziali note della home
  (`PROTETTI`) coperti da una directory vuota o da un file vuoto: il comando
  non li vede. Uno che contiene la cartella di lavoro resta visibile, perche'
  coprirlo nasconderebbe la cartella stessa;
- la directory di runtime dell'utente coperta, con l'agente SSH, D-Bus e gli
  altri socket della sessione grafica;
- nuovi namespace per tutto, rete compresa: niente rete, salvo
  `ARES_SANDBOX_RETE=1`, che la lascia intera e senza filtro per dominio;
- un namespace dei processi proprio: quando il comando finisce o scade, i
  processi che ha lasciato in background muoiono con lui.

Se la sandbox e' chiesta ma non si puo' applicare (altro sistema, `bwrap`
assente, namespace negati) l'avvio si ferma con una riga invece di
ripiegare in silenzio sul comportamento senza sandbox.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ares import config
from ares.config import Percorsi, Politica

# Le credenziali che un programma della home legge di solito, relative alla
# home. Un elenco, non una garanzia: cio' che sta altrove resta leggibile.
PROTETTI = (
    ".ssh",
    ".gnupg",
    ".aws",
    ".azure",
    ".kube",
    ".docker",
    ".config/gh",
    ".config/gcloud",
    ".netrc",
    ".git-credentials",
    ".pypirc",
    ".npmrc",
)


class SandboxNonDisponibile(ValueError):
    """La sandbox e' chiesta ma su questo sistema non si puo' applicare."""


@dataclass(frozen=True)
class Sandbox:
    """Come avvolgere un comando: dove scrive, cosa non vede, se ha la rete."""

    bwrap: str
    cartella: Path
    nascosti: tuple[Path, ...]
    rete: bool

    def argv(self, args: Sequence[str]) -> list[str]:
        """La riga di `bwrap` che esegue `args` nella cartella di lavoro."""
        cartella = str(self.cartella)
        riga = [self.bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
        riga += ["--bind", cartella, cartella]
        # Dopo la cartella: un segreto dentro la cartella resta coperto.
        for percorso in self.nascosti:
            if percorso.is_dir():
                riga += ["--tmpfs", str(percorso)]
            else:
                riga += ["--ro-bind", os.devnull, str(percorso)]
        riga += ["--unshare-all", *(["--share-net"] if self.rete else []), "--die-with-parent", "--new-session"]
        riga += ["--setenv", "TMPDIR", "/tmp", "--unsetenv", "SSH_AUTH_SOCK", "--chdir", cartella, "--"]
        return [*riga, *args]

    def descrizione(self) -> str:
        """Una riga per la persona: «bwrap, senza rete»."""
        return "bwrap, " + ("con la rete" if self.rete else "senza rete")


def _nascosti(percorsi: Percorsi, cartella: Path) -> tuple[Path, ...]:
    home = Path.home()
    candidati = [percorsi.home, percorsi.stato, percorsi.backup, config.BASE_DIR / ".env"]
    candidati += [home / voce for voce in PROTETTI]
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        candidati.append(Path(runtime))
    risolti = set()
    for candidato in candidati:
        try:
            risolti.add(candidato.resolve())
        except OSError:
            continue
    visti: list[Path] = []
    # Dal piu' corto: cio' che sta dentro un percorso gia' coperto non serve.
    for percorso in sorted(risolti, key=lambda p: len(p.parts)):
        if not percorso.exists() or any(coperto in percorso.parents for coperto in visti):
            continue
        if cartella == percorso or percorso in cartella.parents:
            continue
        visti.append(percorso)
    return tuple(visti)


@functools.cache
def _prova(bwrap: str) -> str | None:
    """`None` se `bwrap` crea i namespace, altrimenti il motivo in una riga."""
    try:
        esito = subprocess.run(
            [bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--unshare-all", "--die-with-parent", "true"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as errore:
        return str(errore)
    if esito.returncode == 0:
        return None
    righe = esito.stderr.strip().splitlines()
    return righe[0] if righe else "uscita " + str(esito.returncode)


def prepara_sandbox(percorsi: Percorsi, politica: Politica) -> Sandbox | None:
    """La sandbox della politica per la cartella di lavoro, o `None` se non e' chiesta.

    Solleva `SandboxNonDisponibile`, con il motivo, se e' chiesta e non si
    puo' applicare.
    """
    if politica.workspace.sandbox is None:
        return None
    if not sys.platform.startswith("linux"):
        raise SandboxNonDisponibile("ARES_SANDBOX=bwrap funziona solo su Linux; toglilo dal .env per continuare")
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise SandboxNonDisponibile("ARES_SANDBOX=bwrap ma bwrap non c'e': installa bubblewrap o togli l'opzione")
    motivo = _prova(bwrap)
    if motivo is not None:
        raise SandboxNonDisponibile(
            "ARES_SANDBOX=bwrap ma bwrap non riesce a isolare un comando (" + motivo + "). "
            "Su Ubuntu 24.04 serve un profilo AppArmor che gli conceda i namespace"
        )
    cartella = percorsi.lavoro.resolve()
    return Sandbox(bwrap, cartella, _nascosti(percorsi, cartella), rete=politica.workspace.sandbox_rete)
