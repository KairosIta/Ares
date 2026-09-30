"""Ares: assistente personale locale su Agno e Ollama.

Il package e' diviso per responsabilita', non per tipo di file:

- ``config``    impostazioni versionate e percorsi dello stato, in un punto solo;
- ``agent``     composizione dell'agente, ciclo del turno, apprendimento, schemi;
- ``cli``       il comando ``ares``: App Cyclopts, REPL, comandi locali, rendering;
- ``state``     lettura degli archivi, lock cooperativo, primitive di piattaforma;
- ``backup``    snapshot locali dello stato: creazione, verifica, restore, prune;
- ``entities``  audit e fusione offline delle entita' apprese;
- ``sessions``  retention offline delle sessioni e dei risultati offloaded;
- ``ops``       preflight, ispezione degli archivi e migrazione, a modello spento.

Il comando ``ares`` (``cli/app.py``) li riunisce: da solo apre la chat, e
``ares backup``, ``ares sessions``, ``ares entities``, ``ares preflight``,
``ares inspect`` e ``ares migrate`` sono i sottocomandi. Gli alias ``ares-backup``... di
``pyproject.toml`` passano dalla stessa App; i sottopackage con un
``__main__.py`` rispondono anche a ``python -m``.
"""

import os
from importlib.metadata import PackageNotFoundError, version

# Il logger in Rust di LanceDB legge LANCEDB_LOG una volta, all'import del
# modulo nativo, e di default stampa in chat i WARN interni (per esempio alla
# creazione dell'indice). Va quindi fissato prima di qualsiasi sottomodulo;
# un valore gia' nell'ambiente vince, per chi vuole quei log.
os.environ.setdefault("LANCEDB_LOG", "error")

try:
    __version__ = version("ares")
except PackageNotFoundError:
    # Il package non e' installato nel venv: il codice gira da un checkout
    # senza `uv sync`. Funziona lo stesso, ma non sa che versione e'.
    __version__ = "0+non-installato"
