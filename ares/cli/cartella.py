"""La cartella in cui Ares lavora: sceglierla, guardarla, autorizzarla.

`ares` lanciato in una cartella la rende la directory di lavoro, come Claude
Code o Codex. E' comodo ed e' anche il modo piu' facile di aprire Ares in un
posto sbagliato: la home intera, la radice del disco, la cartella che
contiene il suo stesso database. Qui si guarda il percorso prima di aprirlo
e, se e' rischioso, lo si dice e si chiede di riscriverlo. Non e' un filtro:
tutto si puo' aprire, ma consapevolmente.

Ci stanno anche le due letture che il banner e `/cartella` fanno della
cartella - il ramo git e il file di istruzioni `ARES.md` - e lo scheletro
che `ares init` scrive.
"""

import os
import re
import subprocess
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ares import config
from ares.cli.conferma import conferma_scritta, domanda
from ares.cli.ui import UI
from ares.state.stores import prima_domanda, quando_sessione

# Le directory di sistema dove un `workspace_delete` o un `bash -lc` hanno
# un raggio che nessun progetto ha. Su Windows si leggono dall'ambiente, che
# e' l'unico posto in cui stanno scritte.
_SISTEMA_POSIX = ("/usr", "/etc", "/bin", "/sbin", "/lib", "/lib64", "/var", "/opt", "/boot", "/root")
_SISTEMA_WINDOWS = ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")


def scegli(percorso: Path | None) -> Path:
    """La cartella di lavoro risolta: quella data, o quella da cui si e' partiti.

    Una cartella che non esiste e' un errore e non una da creare: qui si
    lavora sui file di un progetto, e un refuso in `--workspace` che crea
    una directory vuota si scoprirebbe al primo file che manca.
    """
    scelta = (percorso if percorso is not None else config.WORKSPACE_DIR).expanduser()
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


def rischi(percorso: Path) -> list[str]:
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
        ("lo stato di Ares", config.TMP_DIR),
        ("i backup di Ares", config.BACKUP_DIR),
        ("il codice di Ares", config.BASE_DIR),
    ):
        if _stesso_o_sotto(Path(dentro), percorso):
            motivi.append("contiene " + nome + " (" + str(dentro) + ")")
    return motivi


def autorizza(percorso: Path, *, esplicito: bool) -> bool:
    """Vero se si puo' lavorare qui: subito, o dopo una conferma scritta.

    `esplicito` dice che il percorso viene da `--workspace` e non dalla
    directory corrente. Cambia una cosa sola: senza terminale, dove nessuno
    puo' rispondere, un percorso rischioso nominato apposta passa con
    l'avviso, uno ereditato dalla shell si rifiuta.
    """
    motivi = rischi(percorso)
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

    Qui git si lancia davvero, ma solo su richiesta: e' `/cartella` che lo
    chiede, non l'avvio.
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


def nuovo_id_sessione(radice: Path, adesso: datetime | None = None) -> str:
    """L'identificativo di una conversazione nuova: la cartella e il momento.

    Leggibile in `/sessioni` e in `ares sessions status` senza decodificare
    niente: `ares-20260907-091530` dice dove e quando. I secondi bastano a
    distinguere due avvii nella stessa cartella; il nome viene ridotto a
    lettere, cifre e trattini perche' finisce in una riga di comando.
    """
    nome = re.sub(r"[^a-z0-9]+", "-", radice.name.casefold()).strip("-") or "cartella"
    momento = (adesso or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return nome[:40] + "-" + momento


def scegli_sessione(sessioni: Sequence[Any]) -> str | None:
    """Un elenco numerato delle conversazioni, e il numero scelto. None se si rinuncia.

    Riga vuota, Ctrl-C e un numero che non c'e' valgono rinuncia: e' `ares
    resume --scegli`, e chi non trova quello che cerca deve poter uscire
    senza aprire una conversazione a caso.
    """
    righe = []
    for indice, sessione in enumerate(sessioni, start=1):
        scambi = len(getattr(sessione, "runs", None) or [])
        righe.append(
            (
                str(indice),
                str(getattr(sessione, "session_id", "?")),
                quando_sessione(sessione),
                str(scambi),
                prima_domanda(sessione, larghezza=60),
            )
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
    return str(getattr(sessioni[int(risposta) - 1], "session_id", ""))


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


def file_istruzioni(percorso: Path) -> Path:
    return percorso / config.WORKSPACE_ISTRUZIONI


def scrivi_scheletro(percorso: Path) -> Path:
    """Scrive `ARES.md` nella cartella, se non c'e' gia'.

    Un file che esiste non si tocca: contiene le regole di qualcuno, e
    uno scheletro al suo posto sarebbe una perdita silenziosa.
    """
    destinazione = file_istruzioni(percorso)
    if destinazione.exists():
        raise FileExistsError(str(destinazione) + " esiste gia'.")
    destinazione.write_text(SCHELETRO, encoding="utf-8")
    return destinazione
