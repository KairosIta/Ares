"""Funzioni comuni alle prove
==========================

Non un framework: le funzioni che le prove ripetevano identiche (padding,
formato del fallimento, traceback). Ogni prova resta uno script autonomo.

Regola: questo modulo non importa `config` ne' niente di `ares`. `config`
fotografa `ARES_HOME`, `ARES_TMP`, `ARES_BACKUP_DIR` e il `.env` all'import, quindi
`prepara_ambiente` deve poter girare prima: una prova che importasse
`config` troppo presto scriverebbe accanto ai dati veri.
"""

from __future__ import annotations

import json
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

# Il file in cui `esegui` lascia l'esito per il runner, che lancia ogni prova
# in un processo a parte e ne vede solo il codice di uscita. Senza, una prova
# lanciata da sola non scrive niente.
VARIABILE_ESITO = "ARES_PROVA_ESITO"


# Le variabili con cui Rich tratta una pipe come un terminale.
RICH_FORZATURE = ("FORCE_COLOR", "TTY_COMPATIBLE", "TTY_INTERACTIVE")


def prepara_ambiente(prefisso: str, *, workspace: bool = True, backup: bool = True) -> Path:
    """Sceglie i percorsi usa-e-getta della prova, prima che `config` li legga.

    Restituisce la radice temporanea, con `casa/`, `stato/`, `backup/` e `lavoro/`.
    Fallisce se `config` e' gia' in memoria, perche' i percorsi sarebbero gia'
    decisi. La cartella di lavoro e' la directory corrente, come per `ares` in
    un progetto: per questo la prova ci entra con `chdir`.
    """
    if "ares.config" in sys.modules:
        raise RuntimeError("prepara_ambiente va chiamata prima di importare ares.config")
    radice = Path(tempfile.mkdtemp(prefix="ares-" + prefisso + "-"))
    # Le prove leggono il testo che Ares stampa su una pipe: un terminale che
    # forza i colori (`FORCE_COLOR`, ereditato anche dai processi figli)
    # lo riempirebbe di sequenze ANSI.
    for variabile in RICH_FORZATURE:
        os.environ.pop(variabile, None)
    # Anche la casa: le skill e le regole personali che vi stanno sono della
    # persona, e una prova non deve ne' leggerle ne' scriverci.
    os.environ["ARES_HOME"] = str(radice / "casa")
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

    Prima esce dalla cartella di lavoro: su Windows la directory corrente non si
    cancella, e `rmtree` con gli errori ignorati la lascerebbe sul disco.
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

    Per un'asserzione basta la riga da cui viene. Per un'eccezione imprevista
    serve il traceback: `KeyError: 'id'` da solo non dice dove e' nato.
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

    Restituisce i nomi dei falliti e dei non concludenti. Lasciarle girare dice
    se un guasto e' isolato o il primo di una catena.
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
    destinazione = os.environ.get(VARIABILE_ESITO)
    if destinazione:
        dati = {"falliti": falliti, "non_concludenti": non_conclusivi}
        Path(destinazione).write_text(json.dumps(dati, ensure_ascii=False), encoding="utf-8")
    return falliti, non_conclusivi


def chiudi(falliti: list[str], radice: Path) -> int:
    """Il codice di uscita di una prova, e che fine fa la sua directory.

    Un fallimento conserva la radice usa-e-getta, che e' l'unica traccia di
    cosa e' andato storto.
    """
    if falliti:
        print("Archivio della prova conservato:", radice)
        return 1
    pulisci(radice)
    return 0
