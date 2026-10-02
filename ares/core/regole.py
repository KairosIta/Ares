"""Le regole di autorizzazione della persona sui comandi: quali non chiedono conferma, quali non girano mai.

Sono file TOML letti dal nucleo e mai dal modello: `.ares/permessi.toml`
nella cartella di lavoro per il progetto, `permessi.toml` in `~/.ares` per
la persona (i nomi sono in `Politica.workspace`). Il formato:

    [comandi]
    consenti = ["git status", "git log", "git diff", "ls", "uv run pytest"]
    nega = ["rm -rf", "git push", "curl", "wget"]

Una regola e' un prefisso sulle parole del comando. Un comando composto
(`&&`, `||`, `;`, `|`) si spezza e ogni parte deve essere coperta; `nega`
vince su `consenti`; una parte scoperta, una redirezione, una sostituzione o
una riga di PowerShell fanno chiedere come oggi. Una regola non concede
cio' che la modalita' vieta: tace una conferma che la modalita' chiederebbe,
e solo con qualcuno davanti. `nega` vale anche in `auto`, dove nessuna
conferma c'e': lo applica `AresWorkspace.run_command` prima di eseguire.

Il prefisso e' una convenzione, non un confine: `git -c core.pager=x status`
non e' coperto da `git status` e chiede, ma un alias o uno script nella
cartella possono chiamarsi come un comando consentito. Il file del progetto
arriva con il clone, come `ARES.md`: il banner dice quante regole ha letto.
"""

import re
import shlex
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Any, Literal

from ares.config import Percorsi, Politica

Effetto = Literal["consenti", "nega"]

# Le shell POSIX la cui riga `-c` si sa leggere, e le opzioni con cui la ricevono.
_SHELL = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_OPZIONI_RIGA = frozenset({"-c", "-lc", "-cl", "-ec", "-ce", "-lec", "-elc"})
# Gli interpreti la cui riga non si sa leggere: una regola non li copre mai.
_INTERPRETI_OPACHI = frozenset({"powershell", "powershell.exe", "pwsh", "pwsh.exe", "cmd", "cmd.exe"})
# I wrapper che non cambiano il comando: si tolgono, con le assegnazioni `X=y` davanti.
_WRAPPER = frozenset({"env", "time", "nohup", "command"})
_SEPARATORI = frozenset({"&&", "||", ";", ";;", "|", "&", "\n"})
_ASSEGNAZIONE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _base(parola: str) -> str:
    return PurePath(parola).name.lower()


@dataclass(frozen=True)
class Regola:
    """Un prefisso di comando con il suo effetto e il file da cui viene."""

    effetto: Effetto
    parole: tuple[str, ...]
    fonte: str

    def copre(self, comando: Sequence[str]) -> bool:
        """Vero se `comando` comincia con le parole della regola; la prima si confronta per nome, senza percorso."""
        if len(comando) < len(self.parole):
            return False
        return _base(comando[0]) == _base(self.parole[0]) and tuple(comando[1 : len(self.parole)]) == self.parole[1:]

    def __str__(self) -> str:
        return self.effetto + " «" + " ".join(self.parole) + "» in " + self.fonte


@dataclass(frozen=True)
class Regole:
    """Le regole lette, i file da cui vengono e gli avvisi su cio' che non si e' potuto leggere."""

    regole: tuple[Regola, ...] = ()
    fonti: tuple[str, ...] = ()
    avvisi: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.regole)

    def quante(self, effetto: Effetto) -> int:
        return sum(1 for regola in self.regole if regola.effetto == effetto)

    def decidi(self, args: Any) -> Regola | None:
        """La regola che decide il comando `args`, o `None` se si chiede come oggi.

        `nega` su una parte qualunque vince; `consenti` solo se ogni parte e'
        coperta (si riporta la regola della prima). Un comando che non si sa
        spezzare non e' deciso da nessuna regola.
        """
        pezzi = spezza(args)
        if pezzi is None:
            return None
        for pezzo in pezzi:
            for regola in self.regole:
                if regola.effetto == "nega" and regola.copre(pezzo):
                    return regola
        scelte = []
        for pezzo in pezzi:
            scelta = next((r for r in self.regole if r.effetto == "consenti" and r.copre(pezzo)), None)
            if scelta is None:
                return None
            scelte.append(scelta)
        return scelte[0]


def _pulisci(parole: Sequence[str]) -> tuple[str, ...] | None:
    """Il comando senza assegnazioni e wrapper davanti; `None` se resta vuoto o il wrapper ha opzioni."""
    indice = 0
    while indice < len(parole):
        parola = parole[indice]
        if _ASSEGNAZIONE.match(parola):
            indice += 1
            continue
        if _base(parola) in _WRAPPER:
            indice += 1
            if indice < len(parole) and parole[indice].startswith("-"):
                return None
            continue
        break
    resto = tuple(parole[indice:])
    return resto or None


def _spezza_riga(riga: str) -> list[tuple[str, ...]] | None:
    """I comandi semplici di una riga di shell, o `None` se contiene cio' che qui non si legge.

    Redirezioni, sottoshell e sostituzioni (`$(...)`, backtick, variabili)
    nascondono che cosa gira davvero: si lascia chiedere.
    """
    lexer = shlex.shlex(riga, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        token = list(lexer)
    except ValueError:
        return None
    pezzi: list[list[str]] = []
    corrente: list[str] = []
    for parola in token:
        if parola in _SEPARATORI:
            if corrente:
                pezzi.append(corrente)
            corrente = []
            continue
        if all(carattere in "();<>|&" for carattere in parola):
            return None
        if "$" in parola or "`" in parola:
            return None
        corrente.append(parola)
    if corrente:
        pezzi.append(corrente)
    puliti = [_pulisci(pezzo) for pezzo in pezzi]
    if not puliti or any(pezzo is None for pezzo in puliti):
        return None
    return [pezzo for pezzo in puliti if pezzo is not None]


def spezza(args: Any) -> list[tuple[str, ...]] | None:
    """I comandi semplici dentro `args` di `run_command`, o `None` se non si sa leggerlo.

    `['git', 'status']` e' un comando; `['bash', '-lc', 'git add . && git push']`
    sono due. Una shell con altri argomenti, un interprete opaco o una riga
    con redirezioni o sostituzioni non si decidono per regola.
    """
    if not isinstance(args, list) or not args or not all(isinstance(parola, str) for parola in args):
        return None
    nome = _base(args[0])
    if nome in _INTERPRETI_OPACHI:
        return None
    if nome in _SHELL:
        if len(args) != 3 or args[1] not in _OPZIONI_RIGA:
            return None
        return _spezza_riga(args[2])
    pulito = _pulisci(args)
    return None if pulito is None else [pulito]


def analizza(testo: str, fonte: str) -> tuple[list[Regola], list[str]]:
    """Le regole di un file TOML e gli avvisi; un file malformato vale vuoto, con l'avviso."""
    try:
        dati = tomllib.loads(testo)
    except tomllib.TOMLDecodeError as errore:
        return [], [fonte + " non e' TOML valido, regole ignorate: " + str(errore)]
    avvisi = [fonte + ": chiave sconosciuta [" + chiave + "], ignorata" for chiave in dati if chiave != "comandi"]
    comandi = dati.get("comandi", {})
    if not isinstance(comandi, dict):
        return [], [*avvisi, fonte + ": [comandi] deve essere una tabella, regole ignorate"]
    regole: list[Regola] = []
    for chiave, valore in comandi.items():
        if chiave not in ("consenti", "nega"):
            avvisi.append(fonte + ": chiave sconosciuta comandi." + chiave + ", ignorata")
            continue
        if not isinstance(valore, list) or not all(isinstance(voce, str) for voce in valore):
            avvisi.append(fonte + ": comandi." + chiave + " deve essere una lista di stringhe, ignorata")
            continue
        for voce in valore:
            try:
                parole = shlex.split(voce)
            except ValueError:
                parole = []
            if not parole or any(parola in _SEPARATORI or all(c in "();<>|&" for c in parola) for parola in parole):
                avvisi.append(fonte + ": la regola " + repr(voce) + " non e' un comando semplice, ignorata")
                continue
            effetto: Effetto = "nega" if chiave == "nega" else "consenti"
            regole.append(Regola(effetto, tuple(parole), fonte))
    return regole, avvisi


def _file_del_progetto(percorsi: Percorsi, nome: str) -> Path | None:
    """Il file delle regole del progetto, solo se e' un file vero dentro la cartella (come `ARES.md`)."""
    radice = percorsi.lavoro.resolve()
    try:
        reale = (radice / nome).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return reale if reale.is_file() and reale.is_relative_to(radice) else None


def leggi_regole(percorsi: Percorsi, politica: Politica) -> Regole:
    """Le regole del progetto e quelle personali insieme; un file assente o illeggibile non e' un errore.

    I due file si sommano: `nega` di uno vale per entrambi, e cosi' `consenti`.
    """
    candidati = [
        _file_del_progetto(percorsi, politica.workspace.regole_progetto),
        percorsi.home / politica.workspace.regole_personali,
    ]
    regole: list[Regola] = []
    fonti: list[str] = []
    avvisi: list[str] = []
    for percorso in candidati:
        if percorso is None or not percorso.is_file():
            continue
        try:
            testo = percorso.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as errore:
            avvisi.append(str(percorso) + " non si legge, regole ignorate: " + str(errore))
            continue
        lette, note = analizza(testo, str(percorso))
        regole.extend(lette)
        avvisi.extend(note)
        fonti.append(str(percorso))
    return Regole(tuple(regole), tuple(fonti), tuple(avvisi))
