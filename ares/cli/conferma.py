"""Le conferme scritte dei comandi di manutenzione, in un posto solo.

Restore, prune, retention e fusione chiedono di riscrivere una frase esatta
(il nome dello snapshot, `ELIMINA`, `FONDI a IN b`) prima di toccare lo
stato. Con un terminale si usa l'editor della chat; senza (pipe, test,
script) si ripiega su `input()`, che i test sostituiscono con
`patch("builtins.input")`.
"""

import sys

from ares.cli.ui import UI


def domanda(etichetta: str) -> str:
    """Una riga dall'utente, o la stringa vuota se non c'e' piu' nessuno.

    Ctrl-C e fine dell'input valgono come no: la stringa vuota non coincide
    mai con la frase attesa.
    """
    try:
        if sys.stdin.isatty() and sys.stdout.isatty():
            return _domanda_interattiva(etichetta)
        return input(etichetta)
    except (EOFError, KeyboardInterrupt):
        UI.blank()
        return ""


def _domanda_interattiva(etichetta: str) -> str:
    # Importato qui: i comandi di manutenzione non caricano prompt_toolkit
    # finche' non devono davvero chiedere qualcosa.
    from prompt_toolkit import PromptSession

    from ares.cli.editor import ARES_INPUT_STYLE

    sessione: PromptSession[str] = PromptSession(style=ARES_INPUT_STYLE, include_default_pygments_style=False)
    return sessione.prompt([("class:prompt.ask", etichetta)])


def conferma_scritta(attesa: str, *, cosa: str | None = None) -> bool:
    """Vero solo se l'utente riscrive `attesa` tale e quale.

    `cosa` dice cosa si sta autorizzando, e puo' mancare se l'anteprima lo ha
    gia' detto. La frase attesa sta da sola su una riga, per copiarla.
    """
    UI.blank()
    if cosa:
        UI.line(cosa, style="ares.warning")
    UI.line("Per confermare scrivi esattamente:", style="ares.warning")
    UI.line("    " + attesa, style="ares.title")
    return domanda("> ").strip() == attesa
