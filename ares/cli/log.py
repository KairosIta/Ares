"""Il log di Agno, zittito o acceso: una cosa sola, in un posto solo.

Stava in `chat.py`, e `commands.py` lo importava da li' dentro una funzione
per non chiudere il ciclo fra i due moduli: `chat` importa `commands` per la
tabella dei comandi, `commands` aveva bisogno di `chat` per questo. Qui non
importa niente di Ares, e lo usano la chat, `/debug` e `ares inspect
--prompt`, che deve stampare il prompt e nient'altro.
"""

import logging

AGNO_LOGGER_NAMES = ("agno", "agno-team", "agno-workflow")


def configura_log_agno(debug: bool) -> None:
    """Nasconde il rumore INFO di Agno, salvo quando si chiede il debug.

    Agno riporta il livello del proprio logger a INFO all'inizio di ogni run.
    La soglia sugli handler non viene invece riscritta e continua quindi a
    filtrare messaggi interni come ``Found 0 documents`` per tutta la REPL.
    Warning ed errori restano sempre visibili.
    """
    livello = logging.DEBUG if debug else logging.WARNING
    for nome in AGNO_LOGGER_NAMES:
        for handler in logging.getLogger(nome).handlers:
            handler.setLevel(livello)
