"""Ares: assistente personale locale su Agno e Ollama.

Il package e' diviso per responsabilita', non per tipo di file:

- ``config``    impostazioni versionate e percorsi dello stato, in un punto solo;
- ``agent``     composizione dell'agente, ciclo del turno, apprendimento, schemi;
- ``cli``       la REPL: editor, comandi locali, rendering;
- ``state``     lettura degli archivi, lock cooperativo, primitive di piattaforma;
- ``backup``    snapshot locali dello stato: creazione, verifica, restore, prune;
- ``entities``  audit e fusione offline delle entita' apprese;
- ``sessions``  retention offline delle sessioni e dei risultati offloaded;
- ``ops``       preflight dell'ambiente e ispezione degli archivi a modello spento.

I comandi installati nel venv da ``pyproject.toml`` - ``ares``, ``ares-backup``,
``ares-entities``, ``ares-sessions``, ``ares-preflight``, ``ares-inspect`` -
sono i ``main()`` di questi moduli; i sottopackage con un ``__main__.py``
rispondono anche a ``python -m``.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("ares")
except PackageNotFoundError:
    # Il package non e' installato nel venv: il codice gira da un checkout
    # senza `uv sync`. Funziona lo stesso, ma non sa che versione e'.
    __version__ = "0+non-installato"
