"""Snapshot locali dello stato appreso.

``snapshots`` e' la facciata: creazione, catalogo, verifica, restore, prune e
il promemoria per la chat. ``cli`` contiene parser, conferme e output;
``integrity`` formato, checksum e verifica; ``restore`` staging e rollback;
``files`` le primitive condivise sui permessi e sulle rinomine; ``probe`` la
lettura di LanceDB in un processo isolato.
"""
