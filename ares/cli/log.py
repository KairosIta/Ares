"""Il log di Agno, zittito o acceso.

Non importa niente di Ares, cosi' la chat, `/debug` e `ares inspect
--prompt` lo usano senza cicli di import.
"""

import logging

AGNO_LOGGER_NAMES = ("agno", "agno-team", "agno-workflow")


def configura_log_agno(debug: bool) -> None:
    """Nasconde il rumore INFO di Agno, salvo quando si chiede il debug.

    Agno riporta il proprio logger a INFO a ogni run, ma non tocca la soglia
    degli handler: per questo si agisce su quella. Warning ed errori restano
    visibili.
    """
    livello = logging.DEBUG if debug else logging.WARNING
    for nome in AGNO_LOGGER_NAMES:
        for handler in logging.getLogger(nome).handlers:
            handler.setLevel(livello)
