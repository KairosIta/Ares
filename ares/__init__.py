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

Ogni sottopackage con un ``__main__.py`` e' un comando: ``python -m ares``
avvia la chat, ``python -m ares.backup`` gestisce gli snapshot, e cosi' via.
"""
