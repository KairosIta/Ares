"""Revisione delle skill di Ares
============================

Uso:
    ares skills list
    ares skills adopt NOME
    ares skills adopt NOME --apply
    ares skills discard NOME --apply

Le skill attive sono quelle in `~/.ares/skills` e in `.ares/skills` della
cartella di lavoro. Le proposte che Ares scrive con `proponi_skill` stanno in
`~/.ares/skills/proposte/` e non si caricano: `adopt` le mostra e, con
`--apply` e la conferma scritta, le sposta fra le attive; `discard` le
cancella. Una skill attiva con lo stesso nome non si perde: va in
`~/.ares/skills/.precedenti/`.
"""

import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from ares import config
from ares.agent.skill import (
    PRECEDENTI,
    Scartata,
    Skill,
    adotta,
    carica_proposta,
    carica_skill,
    errore_nome,
    proposte,
)
from ares.cli.comando import ESITO_FATTO, ESITO_GUASTO, ESITO_RIFIUTO, nuova_app
from ares.cli.conferma import conferma_scritta
from ares.cli.ui import UI
from ares.config import Percorsi, Politica

app = nuova_app("skills", "Le skill attive e le proposte da rivedere")


def _radice(percorsi: Percorsi, politica: Politica) -> Path | None:
    return percorsi.lavoro if politica.workspace.attivo else None


def stampa_skill(percorsi: Percorsi, politica: Politica, radice: Path | None) -> None:
    """Le skill attive, quelle scartate con il motivo e le proposte in attesa."""
    if not politica.apprendimento.skill:
        UI.line("Le skill sono spente: ARES_SKILL=0.", style="ares.muted")
        return
    skills = carica_skill(percorsi, politica, radice)
    UI.heading("Skill attive")
    for skill in skills.attive:
        UI.line("- " + skill.nome + ("  (del progetto)" if skill.origine == "progetto" else ""), style="ares.title")
        UI.line("  " + skill.descrizione)
        UI.line("  " + str(skill.cartella), style="ares.muted")
    if not skills.attive:
        UI.line(
            "Nessuna. Una skill e' una cartella con un SKILL.md in "
            + str(percorsi.skill)
            + (" o in " + str(radice / config.SKILL_PROGETTO) if radice is not None else "")
            + ".",
            style="ares.muted",
        )
    for scartata in skills.scartate:
        UI.line("non caricata: " + str(scartata.cartella) + ": " + scartata.motivo, style="ares.warning")
    in_attesa = proposte(percorsi)
    if in_attesa:
        UI.blank()
        UI.heading("Proposte da rivedere")
        for voce in in_attesa:
            if isinstance(voce, Skill):
                UI.line("- " + voce.nome, style="ares.title")
                UI.line("  " + voce.descrizione)
            else:
                UI.line("- " + voce.cartella.name + ": " + voce.motivo, style="ares.warning")
        UI.line(
            "Per leggerne una e adottarla: " + config.comando_ares("skills", "adopt", "<nome>"),
            style="ares.muted",
        )


def _proposta(percorsi: Percorsi, nome: str) -> Skill | Scartata | None:
    """La proposta `nome`, caricata come lo sarebbe da attiva; `None` se non c'e'.

    Un nome fuori dalla specifica non c'e' per costruzione: non diventa un
    percorso fuori da `proposte/`.
    """
    cartella = percorsi.skill / config.SKILL_PROPOSTE / nome
    if errore_nome(nome) is not None or not (cartella / "SKILL.md").is_file():
        return None
    return carica_proposta(cartella)


def _adotta(percorsi: Percorsi, nome: str, applica: bool) -> int:
    proposta = _proposta(percorsi, nome)
    if proposta is None:
        UI.err("Nessuna proposta " + repr(nome) + " in " + str(percorsi.skill / config.SKILL_PROPOSTE))
        return ESITO_RIFIUTO
    if isinstance(proposta, Scartata):
        UI.err("La proposta " + nome + " non si caricherebbe: " + proposta.motivo)
        return ESITO_RIFIUTO
    UI.heading("Proposta " + nome)
    UI.line(proposta.file.read_text(encoding="utf-8").rstrip())
    UI.blank()
    sostituisce = (percorsi.skill / nome).exists()
    if sostituisce:
        UI.line(
            "Sostituisce la skill attiva " + nome + ", che andra' in " + str(percorsi.skill / PRECEDENTI) + ".",
            style="ares.warning",
        )
    if not applica:
        UI.line("Anteprima soltanto: la proposta non e' attiva.", style="ares.warning")
        UI.line("Per adottarla, ripeti lo stesso comando aggiungendo --apply.", style="ares.muted")
        return ESITO_FATTO
    if not conferma_scritta("ADOTTA " + nome):
        UI.line("Conferma non corrispondente: niente adottato.", style="ares.warning")
        return ESITO_RIFIUTO
    destinazione, precedente = adotta(percorsi, nome)
    UI.line("Skill adottata: " + str(destinazione) + ". Vale dalla prossima conversazione.", style="ares.success")
    if precedente is not None:
        UI.line("La versione di prima e' in " + str(precedente), style="ares.muted")
    return ESITO_FATTO


def _scarta(percorsi: Percorsi, nome: str, applica: bool) -> int:
    cartella = percorsi.skill / config.SKILL_PROPOSTE / nome
    if errore_nome(nome) is not None or not cartella.is_dir():
        UI.err("Nessuna proposta " + repr(nome) + " in " + str(percorsi.skill / config.SKILL_PROPOSTE))
        return ESITO_RIFIUTO
    UI.line("Da cancellare: " + str(cartella))
    if not applica:
        UI.line("Anteprima soltanto: niente e' stato cancellato.", style="ares.warning")
        UI.line("Per scartarla, ripeti lo stesso comando aggiungendo --apply.", style="ares.muted")
        return ESITO_FATTO
    if not conferma_scritta("SCARTA " + nome):
        UI.line("Conferma non corrispondente: niente cancellato.", style="ares.warning")
        return ESITO_RIFIUTO
    shutil.rmtree(cartella)
    UI.line("Proposta scartata.", style="ares.success")
    return ESITO_FATTO


def _protetto(azione: Callable[[], int]) -> int:
    try:
        return azione()
    except OSError as errore:
        UI.err("ERRORE: " + str(errore))
        return ESITO_GUASTO


@app.command(name="list")
def elenco() -> int:
    """Le skill attive, quelle non caricate con il motivo e le proposte da rivedere."""
    percorsi = config.leggi_percorsi()
    politica = config.leggi_politica()

    def azione() -> int:
        stampa_skill(percorsi, politica, _radice(percorsi, politica))
        return ESITO_FATTO

    return _protetto(azione)


@app.command
def adopt(nome: str, *, apply: bool = False) -> int:
    """Mostra una proposta e, con --apply, la sposta fra le skill attive.

    Args:
        nome: il nome della proposta, come lo mostra `ares skills list`.
        apply: dopo l'anteprima chiede conferma e adotta la proposta.
    """
    return _protetto(lambda: _adotta(config.leggi_percorsi(), nome, apply))


@app.command
def discard(nome: str, *, apply: bool = False) -> int:
    """Cancella una proposta che non serve.

    Args:
        nome: il nome della proposta, come lo mostra `ares skills list`.
        apply: chiede conferma e cancella la cartella della proposta.
    """
    return _protetto(lambda: _scarta(config.leggi_percorsi(), nome, apply))


def main(argv: Sequence[str] | None = None) -> int:
    from ares.cli.app import esegui

    return esegui("skills", list(argv) if argv is not None else None)


if __name__ == "__main__":
    raise SystemExit(main())
