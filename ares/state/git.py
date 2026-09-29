"""Git letto dai file, senza eseguire git.

Il ramo compare nel banner e nel prompt: niente attese di un processo, e
niente errori dove git non e' installato. Basta `HEAD`; worktree e
submodule dicono in `.git` dove sta la directory vera.
"""

from pathlib import Path


def directory_git(percorso: Path) -> Path | None:
    """La `.git` del repository che contiene `percorso`, anche se e' un worktree."""
    for cartella in (percorso, *percorso.parents):
        candidata = cartella / ".git"
        if candidata.is_dir():
            return candidata
        if candidata.is_file():
            # Un worktree o un submodule: il file dice dove sta la directory.
            try:
                riga = candidata.read_text(encoding="utf-8").strip()
            except OSError:
                return None
            if riga.startswith("gitdir:"):
                return (cartella / riga[len("gitdir:") :].strip()).resolve()
            return None
    return None


def ramo_git(percorso: Path) -> str | None:
    """Il ramo corrente, o l'inizio del commit se la testa e' staccata. `None` fuori da git."""
    git = directory_git(percorso)
    if git is None:
        return None
    try:
        testa = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if testa.startswith("ref: refs/heads/"):
        return testa[len("ref: refs/heads/") :]
    return testa[:8] if testa else None
