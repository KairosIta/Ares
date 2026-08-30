"""Sonda LanceDB eseguita in un processo isolato dal backup.

I reader nativi possono trattenere brevemente handle sui frammenti anche
dopo ``close``. Terminare il processo garantisce che il chiamante possa
rinominare la directory dello snapshot anche su Windows.
"""

import asyncio
import json
import sys
from pathlib import Path


async def conta_tabelle(percorso: Path) -> dict[str, int]:
    """Conta le righe di ogni tabella usando le API asincrone richiudibili."""
    import lancedb

    conteggi = {}
    with await lancedb.connect_async(str(percorso)) as connessione:
        risultato = await connessione.list_tables()
        for nome in sorted(risultato.tables):
            with await connessione.open_table(nome) as tabella:
                conteggi[nome] = int(await tabella.count_rows())
    return conteggi


def main(argomenti: list[str] | None = None) -> int:
    argomenti = list(sys.argv[1:] if argomenti is None else argomenti)
    if len(argomenti) != 1:
        print("uso: backup_probe.py <directory-lancedb>", file=sys.stderr)
        return 2
    try:
        print(json.dumps(asyncio.run(conta_tabelle(Path(argomenti[0]))), sort_keys=True))
    except Exception as errore:
        print(str(errore), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
