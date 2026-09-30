"""La cartella in cui Ares lavora: sceglierla, guardarla, autorizzarla.

`ares` rende di lavoro la cartella da cui e' lanciato. Se e' rischiosa (la
home, la radice del disco, una che contiene lo stato di Ares) lo dice e
chiede di riscrivere il percorso: tutto si puo' aprire, ma consapevolmente.

Contiene anche le letture per il banner e `/cartella` e lo scheletro
scritto da `ares init`.
"""

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from ares import config
from ares.cli.conferma import conferma_scritta, domanda
from ares.cli.ui import UI
from ares.config import Percorsi
from ares.state.sessioni import SessioneRiferimento, quando, tronca

# Directory di sistema: su Windows si leggono dall'ambiente.
_SISTEMA_POSIX = ("/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64", "/var", "/opt", "/boot", "/root")
_SISTEMA_WINDOWS = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")


def scegli(percorso: Path | None, percorsi: Percorsi) -> Path:
    """La cartella di lavoro risolta: quella data, o `percorsi.lavoro`.

    Una cartella inesistente e' un errore, non una da creare: un refuso in
    `--workspace` non deve produrre una directory vuota.
    """
    scelta = (percorso if percorso is not None else percorsi.lavoro).expanduser()
    try:
        scelta = scelta.resolve(strict=True)
    except (FileNotFoundError, RuntimeError):
        raise ValueError("La cartella " + str(scelta) + " non esiste.") from None
    if not scelta.is_dir():
        raise ValueError(str(scelta) + " non e' una directory.")
    return scelta


def _sistema() -> list[Path]:
    if os.name == "nt":
        return [Path(os.environ[nome]) for nome in _SISTEMA_WINDOWS if os.environ.get(nome)]
    return [Path(p) for p in _SISTEMA_POSIX]


def _stesso_o_sotto(percorso: Path, radice: Path) -> bool:
    try:
        return percorso.resolve().is_relative_to(radice.resolve())
    except OSError:
        return False


def rischi(percorso: Path, percorsi: Percorsi) -> list[str]:
    """I motivi per cui aprire Ares in questa cartella merita un pensiero.

    Vuota quasi sempre. Ogni riga e' scritta per essere letta sotto
    "Attenzione: la cartella ...", quindi comincia con un verbo.
    """
    percorso = percorso.resolve()
    home = Path.home().resolve()
    motivi = []
    if percorso == Path(percorso.anchor):
        motivi.append("e' la radice del disco")
    if percorso == home:
        motivi.append("e' la tua home intera")
    elif _stesso_o_sotto(home, percorso):
        motivi.append("contiene la tua home")
    if any(_stesso_o_sotto(percorso, d) for d in _sistema()):
        motivi.append("e' una directory di sistema")
    for nome, dentro in (
        ("lo stato di Ares", percorsi.stato),
        ("i backup di Ares", percorsi.backup),
        ("il codice di Ares", config.BASE_DIR),
    ):
        if _stesso_o_sotto(Path(dentro), percorso):
            motivi.append("contiene " + nome + " (" + str(dentro) + ")")
    return motivi


def autorizza(percorso: Path, percorsi: Percorsi, *, esplicito: bool) -> bool:
    """Vero se si puo' lavorare qui: subito, o dopo una conferma scritta.

    `esplicito` dice che il percorso viene da `--workspace`. Conta solo senza
    terminale: un percorso rischioso nominato apposta passa con l'avviso, uno
    ereditato dalla shell si rifiuta.
    """
    motivi = rischi(percorso, percorsi)
    if not motivi:
        return True
    UI.line("Attenzione: la cartella " + str(percorso), style="ares.warning")
    for motivo in motivi:
        UI.line("  - " + motivo, style="ares.warning")
    UI.line(
        "Gli strumenti sui file di Ares potranno leggere e scrivere ovunque qui dentro.",
        style="ares.muted",
    )
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        if esplicito:
            UI.line("Proseguo perche' --workspace la nomina esplicitamente.", style="ares.muted")
            return True
        UI.err("Senza un terminale una cartella rischiosa non si apre: passa --workspace per confermarla.")
        return False
    return conferma_scritta(str(percorso), cosa="Per lavorare qui riscrivi il percorso.")


# ---------------------------------------------------------------------------
# Git, eseguito solo su richiesta: il ramo lo legge `state/git.py` dai file
# ---------------------------------------------------------------------------


def file_modificati(percorso: Path) -> int | None:
    """Quante voci `git status` elenca, o `None` se git non risponde.

    Git si lancia solo su richiesta di `/cartella`, mai all'avvio.
    """
    try:
        esito = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=percorso,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if esito.returncode != 0:
        return None
    return sum(1 for riga in esito.stdout.splitlines() if riga.strip())


# ---------------------------------------------------------------------------
# Le conversazioni di una cartella
# ---------------------------------------------------------------------------


def scegli_sessione(sessioni: Sequence[SessioneRiferimento]) -> str | None:
    """Un elenco numerato delle conversazioni, e il numero scelto. None se si rinuncia.

    Riga vuota, Ctrl-C e un numero inesistente valgono rinuncia.
    """
    righe = []
    for indice, sessione in enumerate(sessioni, start=1):
        righe.append(
            (str(indice), sessione.id or "?", quando(sessione), str(sessione.scambi), tronca(sessione.inizio, 60))
        )
    UI.table(
        (("n", "ares.cyan", "right"), "sessione", "ultima modifica", ("scambi", "ares.text", "right"), "inizio"),
        righe,
    )
    UI.blank()
    risposta = domanda("Quale riprendo? (numero, vuoto per rinunciare) > ").strip()
    if not risposta.isdigit() or not 1 <= int(risposta) <= len(sessioni):
        if risposta:
            UI.line("Nessuna conversazione con quel numero.", style="ares.muted")
        return None
    return sessioni[int(risposta) - 1].id


# ---------------------------------------------------------------------------
# ARES.md
# ---------------------------------------------------------------------------

SCHELETRO = """# Istruzioni per Ares

Ares legge questo file quando lavora in questa cartella, prima del primo
turno. Scrivi qui cio' che deve sapere prima di toccare un file: e' il posto
delle regole del progetto, non delle cose da fare oggi.

## Il progetto

- Cosa e' e a cosa serve.
- Dove sta cio' che conta: codice, prove, documentazione.

## Come si lavora

- Come si lanciano le prove e i controlli.
- Convenzioni: lingua, stile, cosa non toccare senza chiedere.
"""


def file_istruzioni(percorso: Path, nome: str) -> Path:
    """Il file delle regole dentro la cartella.

    `nome` viene dalla politica: vedi "Dipendenze esplicite" in
    docs/architecture.md.
    """
    return percorso / nome


def scrivi_scheletro(percorso: Path, nome: str) -> Path:
    """Scrive `ARES.md` nella cartella, se non c'e' gia'; un file esistente non si tocca."""
    destinazione = file_istruzioni(percorso, nome)
    if destinazione.exists():
        raise FileExistsError(str(destinazione) + " esiste gia'.")
    destinazione.write_text(SCHELETRO, encoding="utf-8")
    return destinazione
