"""La sandbox dei comandi: `run_command` dentro bubblewrap, su Linux, se la persona la chiede.

Senza sandbox un comando gira con i permessi dell'utente (SECURITY.md). Con
`ARES_SANDBOX=bwrap` il comando parte dentro `bwrap` con:

- il filesystem in sola lettura, tranne la cartella di lavoro e una `/tmp`
  privata, che sparisce con il comando;
- dentro la cartella, `.git`, `ARES.md` e `.ares` in sola lettura: git ne
  esegue la configurazione e gli hook, Ares rilegge le regole e le skill del
  progetto. Chi manca quando il comando parte si puo' creare (un `git init`
  e' legittimo), e dal comando dopo e' in sola lettura anche lui;
- `/run` vuota: niente D-Bus di sistema, niente socket di servizi (Docker,
  libvirt, la sessione dell'utente). Con la rete resta solo cio' che serve a
  risolvere i nomi;
- lo stato di Ares, il suo `.env`, le credenziali note della home
  (`PROTETTI`) e la directory di runtime dell'utente coperti da una
  directory vuota o da un file vuoto: il comando non li vede. Uno che
  contiene la cartella di lavoro resta visibile, perche' coprirlo
  nasconderebbe la cartella stessa;
- nuovi namespace per tutto, rete compresa: niente rete, salvo
  `ARES_SANDBOX_RETE=1`, che la lascia intera e senza filtro per dominio;
- un namespace dei processi proprio: quando il comando finisce o scade, i
  processi che ha lasciato in background muoiono con lui.

Cosa coprire e cosa proteggere si ricalcola a ogni comando: vale anche per
cio' che nasce dopo l'avvio, e costa qualche `stat`.

Se la sandbox e' chiesta ma non si puo' applicare (altro sistema, `bwrap`
assente, namespace negati, una cartella di lavoro che contiene la home)
l'avvio si ferma con una riga invece di ripiegare in silenzio sul
comportamento senza sandbox.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
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
    ".config/rclone",
    ".netrc",
    ".git-credentials",
    ".pypirc",
    ".npmrc",
    ".cargo/credentials",
    ".cargo/credentials.toml",
    ".cache/huggingface/token",
    ".ollama/id_ed25519",
    ".password-store",
    ".local/share/keyrings",
    ".Xauthority",
    ".mozilla",
    ".config/google-chrome",
    ".config/chromium",
    ".config/BraveSoftware",
)

# Cio' che di `/run` serve ai programmi: su NixOS i programmi stanno li'.
_RUN_NECESSARI = ("/run/current-system",)

# I messaggi di un errore che la sandbox puo' aver causato, in inglese e in italiano.
_SOLA_LETTURA = re.compile(r"read-only file system|EROFS|sola lettura", re.IGNORECASE)
_PERMESSO = re.compile(r"permission denied|permesso negato|EACCES", re.IGNORECASE)
_RETE = re.compile(
    r"network is unreachable|rete non (?:\S+ )?raggiungibile|temporary failure in name resolution|"
    r"errore temporaneo nella risoluzione|could not resolve|name or service not known|"
    r"nodename nor servname|getaddrinfo|failed to establish a new connection|ENETUNREACH",
    re.IGNORECASE,
)
_PERCORSO = re.compile(r"(?<![\w.~])/[^\s:'\"`]+")


class SandboxNonDisponibile(ValueError):
    """La sandbox e' chiesta ma qui non si puo' applicare."""


@dataclass(frozen=True)
class Sandbox:
    """Come avvolgere un comando: dove scrive, cosa non vede, se ha la rete.

    `candidati` sono i percorsi da nascondere, `sola_lettura` i nomi da
    proteggere dentro la cartella: contano quelli che esistono quando il
    comando parte.
    """

    bwrap: str
    cartella: Path
    candidati: tuple[Path, ...]
    rete: bool
    sola_lettura: tuple[str, ...] = (".git",)

    def nascosti(self) -> tuple[Path, ...]:
        """I `candidati` che esistono ora, senza doppioni e senza chi contiene la cartella."""
        return _coperti(self.candidati, self.cartella)

    def protetti(self) -> tuple[Path, ...]:
        """I percorsi della cartella da montare in sola lettura, fra quelli che esistono ora.

        Un `.git` file (worktree, submodule) porta con se' la directory a cui
        punta, se sta anch'essa nella cartella: fuori e' gia' in sola lettura.
        """
        trovati: list[Path] = []
        for nome in self.sola_lettura:
            percorso = self.cartella / nome
            if not os.path.lexists(percorso):
                continue
            trovati.append(percorso)
            if nome == ".git" and percorso.is_file():
                trovati += [p for p in _directory_di_git(percorso) if _dentro(p, self.cartella)]
        return tuple(trovati)

    def argv(self, args: Sequence[str]) -> list[str]:
        """La riga di `bwrap` che esegue `args` nella cartella di lavoro."""
        cartella = str(self.cartella)
        riga = [self.bwrap, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
        # Prima della cartella, che puo' stare sotto /run (una chiavetta in /run/media).
        riga += ["--tmpfs", "/run"]
        for necessario in (*_RUN_NECESSARI, *(_risoluzione_dei_nomi() if self.rete else ())):
            riga += ["--ro-bind-try", necessario, necessario]
        riga += ["--bind", cartella, cartella]
        # `-try`: un collegamento simbolico rotto non deve far fallire ogni comando.
        for percorso in self.protetti():
            riga += ["--ro-bind-try", str(percorso), str(percorso)]
        # Dopo la cartella: un segreto dentro la cartella resta coperto.
        for percorso in self.nascosti():
            if percorso.is_dir():
                riga += ["--tmpfs", str(percorso)]
            else:
                riga += ["--ro-bind", os.devnull, str(percorso)]
        riga += ["--unshare-all", *(["--share-net"] if self.rete else []), "--die-with-parent", "--new-session"]
        riga += ["--setenv", "TMPDIR", "/tmp", "--unsetenv", "SSH_AUTH_SOCK", "--chdir", cartella, "--"]
        return [*riga, *args]

    def forse_colpa_sua(self, errore: str) -> bool:
        """Vero se l'errore di un comando puo' venire dai limiti della sandbox.

        Un disco in sola lettura, un permesso negato su un percorso fuori dalla
        cartella o protetto, un errore di rete senza la rete. Un `grep` senza
        risultati o una prova fallita no: l'avviso sarebbe rumore.
        """
        if _SOLA_LETTURA.search(errore):
            return True
        if not self.rete and _RETE.search(errore):
            return True
        protetti = self.protetti()
        for riga in errore.splitlines():
            if not _PERMESSO.search(riga):
                continue
            for percorso in map(Path, _PERCORSO.findall(riga)):
                if not _dentro(percorso, self.cartella) or _dentro(percorso, *protetti):
                    return True
        return False


def _dentro(percorso: Path, *radici: Path) -> bool:
    return any(percorso == radice or radice in percorso.parents for radice in radici)


def _directory_di_git(file_git: Path) -> list[Path]:
    """La directory a cui punta un `.git` file, e quella comune dei worktree se c'e'."""
    try:
        riga = file_git.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return []
    if not riga.startswith("gitdir:"):
        return []
    directory = (file_git.parent / riga[len("gitdir:") :].strip()).resolve()
    if not directory.is_dir():
        return []
    trovate = [directory]
    try:
        comune = (directory / "commondir").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return trovate
    if comune:
        trovate.append((directory / comune).resolve())
    return trovate


def _risoluzione_dei_nomi() -> tuple[str, ...]:
    """Cio' che serve sotto `/run` per risolvere i nomi: dove porta `/etc/resolv.conf`, se e' li'.

    Con systemd-resolved e' `/run/systemd/resolve`; con un file vero, niente.
    """
    try:
        reale = Path("/etc/resolv.conf").resolve()
    except OSError:
        return ()
    run = Path("/run")
    if run not in reale.parents:
        return ()
    return (str(reale.parent if reale.parent != run else reale),)


def _runtime_dell_utente() -> list[Path]:
    """La directory di runtime dell'utente: quella di serie e quella di `XDG_RUNTIME_DIR`.

    Senza la variabile (su, sudo -u, cron, ssh senza pam_systemd) la directory
    c'e' lo stesso, con il bus della sessione: si copre comunque.
    """
    runtime = [Path("/run/user") / str(os.getuid())]
    variabile = os.environ.get("XDG_RUNTIME_DIR")
    if variabile:
        runtime.append(Path(variabile))
    return runtime


def _candidati(percorsi: Percorsi) -> tuple[Path, ...]:
    home = Path.home()
    candidati = [percorsi.home, percorsi.stato, percorsi.backup, config.BASE_DIR / ".env"]
    candidati += [home / voce for voce in PROTETTI]
    candidati += _runtime_dell_utente()
    return tuple(candidati)


def _coperti(candidati: Iterable[Path], cartella: Path) -> tuple[Path, ...]:
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


def _nascosti(percorsi: Percorsi, cartella: Path) -> tuple[Path, ...]:
    """I percorsi che un comando lanciato adesso nella `cartella` non vede."""
    return _coperti(_candidati(percorsi), cartella)


def _sola_lettura(politica: Politica) -> tuple[str, ...]:
    """I nomi della cartella che i comandi non scrivono: `.git` e i file del progetto che Ares rilegge."""
    nomi = [".git", politica.workspace.istruzioni]
    nomi += [Path(nome).parts[0] for nome in (politica.workspace.regole_progetto, config.SKILL_PROGETTO)]
    return tuple(dict.fromkeys(nomi))


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
    puo' applicare, anche perche' la cartella e' la home o la contiene: la
    renderebbe scrivibile, con `.bashrc` e `~/.local/bin`.
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
    if _dentro(Path.home().resolve(), cartella):
        raise SandboxNonDisponibile(
            "ARES_SANDBOX=bwrap ma la cartella " + str(cartella) + " contiene la tua home, che i comandi "
            "potrebbero scrivere: apri Ares in una sottocartella"
        )
    return Sandbox(
        bwrap,
        cartella,
        _candidati(percorsi),
        rete=politica.workspace.sandbox_rete,
        sola_lettura=_sola_lettura(politica),
    )
