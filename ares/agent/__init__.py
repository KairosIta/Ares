"""Nucleo dell'agente, indipendente dall'interfaccia.

``assistant`` e' la facciata che assembla l'agente Agno e conserva gli import
pubblici; ``runtime`` costruisce modelli, archivi e strumenti; ``learning``
configura gli store e il post-hook sul run completo; ``prompts`` compone le
istruzioni coerenti con i flag; ``turn_core`` normalizza gli eventi e coordina
``run``/``continue_run``; ``schemas`` estende profilo e memorie con i campi che
gli store usano nel prompt.
"""
