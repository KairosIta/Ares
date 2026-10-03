"""Verifica offline che il runner veda cio' che una prova gli lascia.

Il runner lancia ogni prova in un processo a parte e ne legge il codice di
uscita; i controlli "non concludenti" passano da un file (`VARIABILE_ESITO`).
Qui un figlio finto ne dichiara uno: deve comparire nel riepilogo senza
cambiare il codice, e senza la variabile il figlio non scrive niente.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from _comune import VARIABILE_ESITO, esegui, esigi
from run import leggi_non_concludenti, righe_riepilogo

CARTELLA_PROVE = Path(__file__).resolve().parent

FIGLIO = """
import sys
sys.path.insert(0, sys.argv[1])
from _comune import NON_CONCLUSIVO, esegui
falliti, _ = esegui((("vero", lambda: "dimostrato"), ("vuoto", lambda: NON_CONCLUSIVO + "manca bwrap")))
sys.exit(1 if falliti else 0)
"""


def _figlio(ambiente: dict[str, str]) -> int:
    return subprocess.run(
        [sys.executable, "-c", FIGLIO, str(CARTELLA_PROVE)],
        env=ambiente,
        capture_output=True,
        text=True,
        timeout=60,
    ).returncode


def esito_del_figlio() -> str:
    """Il non concludente arriva al runner, e il codice resta zero."""
    with tempfile.TemporaryDirectory(prefix="ares-runner-") as cartella:
        file_esito = Path(cartella) / "finta.json"
        codice = _figlio({**os.environ, VARIABILE_ESITO: str(file_esito)})
        esigi(codice == 0, "un non concludente ha cambiato il codice di uscita: " + str(codice))
        esigi(leggi_non_concludenti(file_esito) == ["vuoto"], "il runner non legge il non concludente")

        senza = Path(cartella) / "senza.json"
        ambiente = {k: v for k, v in os.environ.items() if k != VARIABILE_ESITO}
        esigi(_figlio(ambiente) == 0, "senza variabile il figlio fallisce")
        esigi(not senza.exists() and len(list(Path(cartella).iterdir())) == 1, "senza variabile il figlio scrive")
        esigi(leggi_non_concludenti(senza) == [], "un esito mancante non vale come vuoto")
    return "non concludente letto dal file, codice 0, niente file senza variabile"


def riepilogo() -> str:
    """Il conto accanto alla prova e l'elenco in fondo; niente se non ce ne sono."""
    righe = righe_riepilogo(
        [("smoke", 0, 6.2, ["vram", "modello"]), ("sandbox", 0, 4.7, ["bwrap"]), ("cli", 1, 1.0, [])]
    )
    testo = "\n".join(righe)
    esigi(righe[0].endswith("2 non concludenti") and righe[1].endswith("1 non concludente"), "conto: " + testo)
    esigi(righe[2].startswith("FALLITA") and "non concludent" not in righe[2], "riga senza non concludenti: " + testo)
    esigi("  smoke: vram, modello" in righe and "  sandbox: bwrap" in righe, "elenco: " + testo)
    pulito = righe_riepilogo([("smoke", 0, 6.2, [])])
    esigi(len(pulito) == 1 and "non concludent" not in pulito[0], "riepilogo senza non concludenti: " + repr(pulito))
    return "conto per prova ed elenco finale; assenti quando non servono"


def main() -> int:
    falliti, _ = esegui((("esito del figlio", esito_del_figlio), ("riepilogo", riepilogo)))
    return 1 if falliti else 0


if __name__ == "__main__":
    raise SystemExit(main())
