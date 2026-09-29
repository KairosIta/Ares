"""Identificativi leggibili delle conversazioni."""

import re
import secrets
from datetime import datetime
from pathlib import Path


def nuovo_id_sessione(radice: Path, adesso: datetime | None = None, *, suffisso: str | None = None) -> str:
    """L'id di una conversazione nuova: cartella, momento e una coda casuale.

    Leggibile a colpo d'occhio (`ares-20260907-091530-4f2a91`); la coda evita
    collisioni fra progetti omonimi o avvii nello stesso secondo. `adesso` e
    `suffisso` servono alle prove.
    """
    nome = re.sub(r"[^a-z0-9]+", "-", radice.name.casefold()).strip("-") or "cartella"
    momento = (adesso or datetime.now()).strftime("%Y%m%d-%H%M%S")
    coda = secrets.token_hex(3) if suffisso is None else suffisso
    return nome[:40] + "-" + momento + "-" + coda
