"""Le conferme scritte dei comandi di manutenzione, in un posto solo.

Restore, prune, retention e fusione chiedono all'utente di riscrivere una
frase esatta prima di toccare lo stato: il nome dello snapshot, `ELIMINA`,
`FONDI a IN b`. Prima ognuno lo faceva con un `input()` nudo, con quattro
inviti scritti in quattro modi. Qui la domanda ha una forma sola - cosa
sta per succedere, cosa scrivere, il prompt - e lo stesso editor della
chat quando c'e' un terminale.

Senza terminale - una pipe, un test, uno script - si ripiega su `input()`,
come fa `CliInput`: e' cio' che permette di passare la conferma da stdin, e
cio' che i test sostituiscono con `patch("builtins.input")`.
"""

import sys

from ares.cli.ui import UI


def domanda(etichetta: str) -> str:
    """Una riga dall'utente, o la stringa vuota se non c'e' piu' nessuno.

    Ctrl-C e fine dell'input non sono errori: davanti a una richiesta di
    conferma sono un no, e chi chiama confronta la risposta con la frase
    attesa, che vuota non e' mai.
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

    `cosa` e' la frase che dice che cosa si sta autorizzando; puo' mancare
    quando l'anteprima appena stampata lo ha gia' detto. La frase attesa
    compare da sola su una riga, cosi' si copia senza cercarla nel testo.
    """
    UI.blank()
    if cosa:
        UI.line(cosa, style="ares.warning")
    UI.line("Per confermare scrivi esattamente:", style="ares.warning")
    UI.line("    " + attesa, style="ares.title")
    return domanda("> ").strip() == attesa
