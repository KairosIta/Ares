"""Manutenzione offline delle entita' apprese.

``maintenance`` espone la CLI e coordina lock e backup; ``audit`` e' la lettura
in sola lettura che cerca i duplicati; ``merge`` pianifica e applica la fusione
in transazione; ``models`` contiene i contratti condivisi.
"""
