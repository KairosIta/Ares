"""Aggancia la misura di copertura ai processi figli delle prove.

`coverage` misura il processo che avvia, non i suoi discendenti. Le prove pero'
lanciano parecchio: la CLI di `ares.entities` sei volte, il sondaggio
LanceDB isolato di `ares.backup`, la rilettura da un secondo interprete in
`e2e_test.py`. Senza questo file quel codice risulta scoperto pur essendo
provato, e un rapporto che mente in difetto e' peggio di nessun rapporto: manda
a scrivere prove per righe che ne hanno gia' una.

Python importa `sitecustomize` da solo all'avvio di ogni interprete, se lo
trova sul path. Il runner mette questa directory in `PYTHONPATH` e definisce
`COVERAGE_PROCESS_START` solo quando misura, quindi fuori da `--copertura` il
file resta inerte anche se venisse importato.
"""

import os

if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage

        coverage.process_startup()
    except ImportError:
        # Un figlio potrebbe girare su un interprete diverso da quello del
        # venv. Non e' una ragione per farlo fallire: la prova sta misurando
        # altro, e la misura e' un di piu'.
        pass
