"""Verifica offline che la versione di Ares sia la stessa ovunque il
repository la dichiara.

La fonte e' `pyproject.toml`; devono concordare `uv.lock`, la voce piu'
recente del CHANGELOG, la linea supportata di `SECURITY.md` e i
collegamenti di confronto in coda al CHANGELOG. Sono posti che nessuno
rilegge a ogni modifica, e sono gia' rimasti indietro. E' l'equivalente di
`versione_dichiarata` in `tests/agno_contract_test.py`, applicato ad Ares.

Il metro non e' il tag: la CI non ha i tag, e il tag si crea dopo il merge.

Gli stessi documenti si collegano fra loro con percorsi relativi, che uno
spostamento rompe in silenzio: anche quelli si controllano qui, perche'
questa prova gira pure sulle PR di soli documenti.
"""

import re
import tomllib
from pathlib import Path

from _comune import esegui, esigi

RADICE = Path(__file__).resolve().parents[1]

# `## [0.8.0] - 2026-09-22`: la prima voce datata dopo `## [Unreleased]`.
VOCE_CHANGELOG = re.compile(r"^## \[(\d+\.\d+\.\d+)\] - (\d{4}-\d{2}-\d{2})$", re.M)
SEZIONE_UNRELEASED = re.compile(r"^## \[Unreleased\]$", re.M)
# `| 0.8.x | Sì |` e `| < 0.8 | No |`: la forma delle due righe di SECURITY.md.
LINEA_SUPPORTATA = re.compile(r"^\|\s*(\d+\.\d+)\.x\s*\|", re.M)
LINEA_NON_SUPPORTATA = re.compile(r"^\|\s*<\s*(\d+\.\d+)\s*\|", re.M)
COLLEGAMENTO_UNRELEASED = re.compile(r"^\[Unreleased\]:\s*(\S+)$", re.M)
# `[testo](percorso)` e `![alt](percorso)`, con un titolo facoltativo.
LINK_MARKDOWN = re.compile(r"\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
BLOCCO_DI_CODICE = re.compile(r"^```.*?^```", re.M | re.S)
CODICE_IN_LINEA = re.compile(r"`[^`\n]*`")
# Cartelle che non sono documentazione del repository.
FUORI_DAI_DOCUMENTI = {".git", ".venv", "artifacts", "htmlcov", "node_modules", "__pycache__"}


def testo(nome: str) -> str:
    """Il contenuto di un file del repository, o un fallimento che lo nomina."""
    percorso = RADICE / nome
    esigi(percorso.is_file(), "manca " + nome)
    return percorso.read_text(encoding="utf-8")


def versione() -> str:
    """La versione dichiarata da `pyproject.toml`, che e' la fonte."""
    with (RADICE / "pyproject.toml").open("rb") as file:
        dati = tomllib.load(file)
    valore = dati["project"]["version"]
    esigi(isinstance(valore, str), "la versione in pyproject.toml non e' una stringa")
    return valore


def versione_nel_lock() -> str:
    """Il lock blocca la versione del progetto, e `uv sync --locked` lo pretende.

    Controllato qui perche' un `uv lock` dimenticato si veda subito in locale.
    """
    attesa = versione()
    with (RADICE / "uv.lock").open("rb") as file:
        dati = tomllib.load(file)
    pacchetti = [p for p in dati.get("package", []) if p.get("name") == "ares"]
    esigi(len(pacchetti) == 1, "uv.lock non ha esattamente un pacchetto 'ares': " + str(len(pacchetti)))
    bloccata = pacchetti[0].get("version")
    esigi(
        bloccata == attesa,
        "uv.lock blocca ares " + str(bloccata) + " mentre pyproject.toml dice " + attesa + ": esegui `uv lock`",
    )
    return "ares " + attesa + " nel lock"


def voce_del_changelog() -> str:
    """La voce piu' recente del CHANGELOG e' quella di `pyproject.toml`.

    Deve esistere anche `## [Unreleased]`, dove si scrive prima del rilascio.
    """
    atteso = versione()
    changelog = testo("CHANGELOG.md")
    esigi(SEZIONE_UNRELEASED.search(changelog), "CHANGELOG.md non ha piu' la sezione [Unreleased]")
    voci = VOCE_CHANGELOG.findall(changelog)
    esigi(voci, "CHANGELOG.md non ha nessuna voce datata nella forma `## [x.y.z] - aaaa-mm-gg`")
    dichiarata, data = voci[0]
    esigi(
        dichiarata == atteso,
        "la voce piu' recente del CHANGELOG e' la " + dichiarata + " mentre pyproject.toml dice " + atteso,
    )
    return "voce " + dichiarata + " del " + data


def linea_di_security() -> str:
    """`SECURITY.md` supporta la linea della versione corrente, e non un'altra.

    La patch e' `x`, quindi il confronto e' su `major.minor`. Le righe
    "supportata" e "non supportata" devono essere complementari.
    """
    atteso = ".".join(versione().split(".")[:2])
    security = testo("SECURITY.md")
    supportate = LINEA_SUPPORTATA.findall(security)
    non_supportate = LINEA_NON_SUPPORTATA.findall(security)
    esigi(len(supportate) == 1, "SECURITY.md non ha una sola riga `| x.y.x |`: " + str(supportate))
    esigi(len(non_supportate) == 1, "SECURITY.md non ha una sola riga `| < x.y |`: " + str(non_supportate))
    esigi(
        supportate[0] == atteso,
        "SECURITY.md supporta la linea " + supportate[0] + ".x mentre la versione corrente e' " + atteso + ".x",
    )
    esigi(
        non_supportate[0] == atteso,
        "SECURITY.md dichiara non supportato < " + non_supportate[0] + " mentre la linea corrente e' " + atteso,
    )
    return "linea " + atteso + ".x supportata"


def collegamenti_di_confronto() -> str:
    """I link in coda al CHANGELOG confrontano a partire dall'ultima release.

    Un `[Unreleased]` fermo a una versione superata mostrerebbe nel diff anche
    le modifiche gia' rilasciate.
    """
    atteso = versione()
    changelog = testo("CHANGELOG.md")
    unreleased = COLLEGAMENTO_UNRELEASED.findall(changelog)
    esigi(unreleased, "CHANGELOG.md non ha il collegamento `[Unreleased]:`")
    confronto = "compare/v" + atteso + "...HEAD"
    esigi(
        unreleased[0].endswith(confronto),
        "[Unreleased] confronta da " + unreleased[0].rsplit("/", 1)[-1] + " invece che da " + confronto,
    )
    proprio = re.findall(r"^\[" + re.escape(atteso) + r"\]:\s*(\S+)$", changelog, re.M)
    esigi(proprio, "CHANGELOG.md non ha il collegamento `[" + atteso + "]:`")
    esigi(
        proprio[0].endswith("v" + atteso),
        "il collegamento [" + atteso + "] non termina con v" + atteso + ": " + proprio[0],
    )
    return "Unreleased da v" + atteso + ", e " + atteso + " collegata"


def documenti() -> list[Path]:
    """I Markdown del repository, fuori da venv, artefatti e cache."""
    return sorted(
        percorso
        for percorso in RADICE.rglob("*.md")
        if not FUORI_DAI_DOCUMENTI.intersection(percorso.relative_to(RADICE).parts)
    )


def destinazioni_rotte(percorso: Path) -> list[str]:
    """I link relativi di un Markdown che non portano a un file o a una cartella."""
    corpo = CODICE_IN_LINEA.sub("", BLOCCO_DI_CODICE.sub("", percorso.read_text(encoding="utf-8")))
    rotte = []
    for destinazione in LINK_MARKDOWN.findall(corpo):
        if destinazione.startswith(("#", "http://", "https://", "mailto:")):
            continue
        locale = destinazione.split("#", 1)[0]
        if not (percorso.parent / locale).exists():
            rotte.append(destinazione)
    return rotte


def link_relativi() -> str:
    """Ogni link relativo nei Markdown porta a qualcosa che esiste.

    L'ancora dopo `#` non si controlla: dipende da come GitHub costruisce gli
    id dei titoli, non dal repository.
    """
    tutti = documenti()
    rotti = []
    for percorso in tutti:
        rotte = destinazioni_rotte(percorso)
        rotti.extend(percorso.relative_to(RADICE).as_posix() + " -> " + r for r in rotte)
    esigi(not rotti, "link rotti: " + "; ".join(rotti))
    return str(len(tutti)) + " documenti, nessun link relativo rotto"


def main() -> int:
    falliti, _ = esegui(
        [
            ("versione nel lock", versione_nel_lock),
            ("voce del CHANGELOG", voce_del_changelog),
            ("linea di SECURITY.md", linea_di_security),
            ("collegamenti", collegamenti_di_confronto),
            ("link relativi", link_relativi),
        ]
    )
    return 1 if falliti else 0


if __name__ == "__main__":
    raise SystemExit(main())
