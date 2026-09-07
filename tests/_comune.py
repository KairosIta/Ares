"""
Cio' che ogni prova ripeteva uguale
===================================

Non un framework: quattro funzioni e una regola. Ogni prova resta uno script
che si lancia da solo, decide i propri percorsi e stampa una riga per
controllo; qui c'e' soltanto cio' che era copiato identico in otto file, e
che divergeva un poco per volta - il padding del nome, il formato del
fallimento, se il traceback si vedeva o no.

La regola: questo modulo non importa `config` ne' niente di `ares`. E' la
sola garanzia che `prepara_ambiente` funzioni, perche' `config` legge
`ARES_TMP`, `ARES_BACKUP_DIR` e la directory corrente quando viene importato
e li tiene finche' qualcuno non passa da `config.imposta_percorsi`. Una prova
che importasse `config` prima di aver scelto i percorsi, senza poi passare da
quella porta, scriverebbe accanto ai dati veri, e questo modulo non puo'
diventare la via da cui succede. Un processo per prova resta la scelta del
runner: la porta rende possibile una prova in-process, non la impone.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback
from collections.abc import Callable, Iterable
from pathlib import Path

# Prefisso delle note "non concludenti": il controllo e' passato ma non ha
# potuto dimostrare niente. Solo un FALLITO cambia il codice di uscita.
NON_CONCLUSIVO = "non concludente: "


def prepara_ambiente(prefisso: str, *, workspace: bool = True, backup: bool = True) -> Path:
    """Sceglie i percorsi usa-e-getta della prova, prima che `config` li legga.

    Restituisce la radice temporanea: `stato/` per l'archivio, `backup/` e
    `lavoro/` accanto. Va chiamata prima di importare `config`, e il
    controllo iniziale lo pretende: se `config` e' gia' in memoria i percorsi
    sono gia' decisi, e la prova starebbe per scrivere dove non deve.

    La cartella di lavoro non ha una variabile: e' la directory corrente, come
    quando si scrive `ares` in un progetto. Per questo la prova ci entra con
    `chdir`, e `config` la legge da li'.
    """
    if "ares.config" in sys.modules:
        raise RuntimeError("prepara_ambiente va chiamata prima di importare ares.config")
    radice = Path(tempfile.mkdtemp(prefix="ares-" + prefisso + "-"))
    os.environ["ARES_TMP"] = str(radice / "stato")
    if backup:
        os.environ["ARES_BACKUP_DIR"] = str(radice / "backup")
    if workspace:
        lavoro = radice / "lavoro"
        lavoro.mkdir()
        os.chdir(lavoro)
    return radice


def pulisci(radice: Path) -> None:
    """Cancella la radice usa-e-getta a fine prova.

    Prima esce dalla cartella di lavoro, se la prova ci sta ancora dentro:
    su Windows la directory corrente di un processo non si cancella, e un
    `rmtree` con gli errori ignorati lascerebbe la radice sul disco senza
    dirlo.
    """
    try:
        if Path.cwd().resolve().is_relative_to(radice.resolve()):
            os.chdir(radice.parent)
    except OSError:
        pass
    shutil.rmtree(radice, ignore_errors=True)


def esigi(condizione: object, messaggio: str) -> None:
    """assert esplicito: `assert` sparisce con `python -O`, questo no."""
    if not condizione:
        raise AssertionError(messaggio)


def ok(nome: str, nota: str) -> None:
    print("ok      ", nome.ljust(20), "-", nota)


def fallimento(errore: BaseException, nome: str = "") -> None:
    """Stampa un fallimento in modo che si capisca dove guardare.

    Un'asserzione dice gia' cosa si aspettava: basta la riga da cui viene,
    perche' lo stesso messaggio puo' stare in due controlli. Un'eccezione
    di altro tipo e' un guasto che la prova non prevedeva, e senza il
    traceback resta un nome di classe e un messaggio - `KeyError: 'id'` -
    che non dice quale delle cento righe attraversate l'ha sollevato. Era
    la differenza fra un fallimento in CI che si legge e uno che si
    riproduce a mano.
    """
    # Il messaggio puo' contenere l'output catturato, con i bordi Rich dentro:
    # sulla console Windows in cp1252 `print` fallirebbe e il fallimento
    # perderebbe proprio la riga che lo spiega.
    codifica = getattr(sys.stdout, "encoding", None) or "utf-8"
    messaggio = str(errore).encode(codifica, "replace").decode(codifica, "replace")
    print("FALLITO ", nome.ljust(20), "-", type(errore).__name__ + ":", messaggio)
    if isinstance(errore, AssertionError):
        quadri = [q for q in traceback.extract_tb(errore.__traceback__) if not q.filename.endswith("_comune.py")]
        if quadri:
            ultimo = quadri[-1]
            print("         ", Path(ultimo.filename).name + ":" + str(ultimo.lineno), "in", ultimo.name)
    else:
        # Sullo stdout come il resto: su stderr finirebbe prima o dopo la
        # riga che lo annuncia, a seconda dei buffer.
        traceback.print_exception(errore, file=sys.stdout)


def esegui(prove: Iterable[tuple[str, Callable[[], str]]]) -> tuple[list[str], list[str]]:
    """Esegue le prove in ordine, una riga per ciascuna; niente ferma le altre.

    Restituisce i nomi dei falliti e dei non concludenti. Un fallimento non
    interrompe la sequenza: il controllo dopo puo' dire se il guasto e' uno
    o e' il primo di una catena, e questo si vede solo lasciandoli girare.
    """
    falliti, non_conclusivi = [], []
    for nome, controllo in prove:
        try:
            nota = controllo()
        except Exception as errore:
            fallimento(errore, nome)
            falliti.append(nome)
            continue
        if nota.startswith(NON_CONCLUSIVO):
            print("n.c.    ", nome.ljust(20), "-", nota[len(NON_CONCLUSIVO) :])
            non_conclusivi.append(nome)
        else:
            ok(nome, nota)
    return falliti, non_conclusivi
