"""Aggancia la misura di copertura ai processi figli delle prove.

`coverage` misura solo il processo che avvia, ma le prove lanciano
sottoprocessi: senza questo file quel codice risulterebbe scoperto. Python
importa `sitecustomize` all'avvio di ogni interprete; il runner lo mette in
`PYTHONPATH` e definisce `COVERAGE_PROCESS_START` solo con `--copertura`,
quindi altrimenti il file e' inerte.
"""

import os

if os.environ.get("COVERAGE_PROCESS_START"):
    try:
        import coverage

        coverage.process_startup()
    except ImportError:
        # Un figlio su un interprete senza coverage non deve fallire: la
        # misura e' un di piu'.
        pass
