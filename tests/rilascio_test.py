"""Verifica offline che la versione di Ares sia scritta allo stesso modo
ovunque il repository dice qual e' quella di adesso.

`pyproject.toml` e' la fonte, e `uv.lock` la copia che l'installazione usa
davvero. La voce piu' recente del CHANGELOG, la riga supportata di
`SECURITY.md` e i collegamenti di confronto in coda al CHANGELOG devono
concordare con lei.

Sono quattro posti che nessuno rilegge a ogni modifica, e due se ne sono gia'
dimenticati una volta: la release 0.7.1 dichiarava ancora la linea 0.6.x in
`SECURITY.md`, e la 0.8.0 e' arrivata su `main` con il collegamento di
`Unreleased` fermo alla 0.7.1. La prova e' la stessa idea di
`versione_dichiarata` in `tests/agno_contract_test.py`, applicata ad Ares:
la versione di Agno ha un guardiano, quella di Ares no, e infatti sono
invecchiati i file che nessuno controlla.

Il metro e' `pyproject.toml`, non il tag: la CI non ha i tag, e comunque il
tag si crea dopo, quando il commit e' gia' su `main`. Cio' che si puo'
pretendere prima e' che il repository concordi con se stesso.
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

    Il controllo e' qui perche' un `uv lock` dimenticato si vede in locale
    subito, senza aspettare il job che sincronizza l'ambiente.
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

    Anche la presenza di `## [Unreleased]` e' un controllo: e' la sezione in
    cui si scrive prima di rilasciare, e una release che la togliesse
    lascerebbe il prossimo cambiamento senza posto dove stare.
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

    La riga usa `x` per la patch - una correzione di sicurezza vale per tutta
    la linea - quindi il confronto e' su `major.minor`. La seconda riga dice
    cosa non e' supportato, e le due devono essere complementari: e' li' che
    la 0.7.1 e' rimasta indietro con la 0.6.x.
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

    `[Unreleased]` che punta a una versione superata mostra, nel diff, anche
    le modifiche gia' rilasciate: e' quello che e' successo alla 0.8.0, dove
    il collegamento era rimasto alla 0.7.1.
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


def main() -> int:
    falliti, _ = esegui(
        [
            ("versione nel lock", versione_nel_lock),
            ("voce del CHANGELOG", voce_del_changelog),
            ("linea di SECURITY.md", linea_di_security),
            ("collegamenti", collegamenti_di_confronto),
        ]
    )
    return 1 if falliti else 0


if __name__ == "__main__":
    raise SystemExit(main())
