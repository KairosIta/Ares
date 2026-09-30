"""La fabbrica delle App Cyclopts di Ares: un aspetto solo per tutti i comandi.

Ogni comando nasce da qui, con gli stessi titoli, la stessa console e gli
stessi codici di uscita. Cyclopts legge firma e docstring della funzione:
i tipi diventano la conversione e la sezione `Args:` descrive le opzioni.
La console e' quella di `UI`, quindi pipe e `NO_COLOR` valgono anche
nell'aiuto.
"""

from collections.abc import Callable

from cyclopts import App, Group, Parameter

import ares
from ares.cli.ui import UI
from ares.config import Percorsi
from ares.core.stato import StatoDaMigrare, stato_esclusivo, stato_in_uso
from ares.state.lock import StatoOccupato

# I codici di uscita, uguali per ogni comando:
#
#   0  fatto
#   1  guasto: lo stato, il disco o l'operazione hanno fallito
#   2  rifiutato: argomenti incoerenti, conferma negata o sbagliata,
#      manutenzione rifiutata, cartella rifiutata, niente da riprendere
#   3  occupato: lo stato e' in uso da un altro processo, e si riprova
ESITO_FATTO = 0
ESITO_GUASTO = 1
ESITO_RIFIUTO = 2
ESITO_OCCUPATO = 3


def codice_di(errore: BaseException) -> int:
    """Il codice di un'eccezione gia' riconosciuta come prevista: occupato o guasto.

    Il rifiuto non passa di qui: chi rifiuta con un'eccezione usa
    `esegui_protetto`, e nel backup un rifiuto e' un valore di ritorno.
    """
    return ESITO_OCCUPATO if isinstance(errore, StatoOccupato) else ESITO_GUASTO


def avvisa_da_migrare(errore: StatoDaMigrare) -> int:
    """L'avviso di `ares migrate` su stderr; vale 1, come per la chat."""
    # Qui e non in cima: `ops/migrazione.py` importa questo modulo.
    from ares.ops.migrazione import righe_avviso

    righe = righe_avviso(errore.parti)
    UI.err(righe[0], style="ares.warning")
    for riga in righe[1:]:
        UI.err(riga, style="ares.muted")
    return ESITO_GUASTO


def esegui_protetto(
    percorsi: Percorsi,
    azione: Callable[[], int],
    *,
    esclusivo: bool,
    rifiuti: tuple[type[BaseException], ...] = (),
    guasti: tuple[type[BaseException], ...] = (OSError,),
    riprova: str = "Attendi che chat, backup, restore o manutenzione terminino e riprova.",
) -> int:
    """Esegue `azione` con lo stato indicato da `percorsi` aperto dal nucleo.

    `esclusivo` per chi scrive (`stato_esclusivo`), condiviso per chi legge.
    `rifiuti` valgono 2, `guasti` 1, lo stato occupato 3, lo stato da migrare
    1. Ogni altra eccezione passa: un traceback inatteso va letto, non
    nascosto dietro un codice.
    """
    try:
        with (stato_esclusivo if esclusivo else stato_in_uso)(percorsi):
            return azione()
    except StatoOccupato as errore:
        UI.err("Impossibile usare lo stato di Ares: " + str(errore))
        UI.err(riprova, style="ares.muted")
        return ESITO_OCCUPATO
    except StatoDaMigrare as errore:
        return avvisa_da_migrare(errore)
    except rifiuti as errore:
        UI.err("Rifiutato: " + str(errore))
        return ESITO_RIFIUTO
    except guasti as errore:
        UI.err("ERRORE: " + str(errore))
        return ESITO_GUASTO


def _mostra_default(valore: object) -> object:
    """Il default nell'aiuto, tranne per i flag spenti: `[default: False]` e' rumore."""
    return None if valore is False else valore


def nuova_app(nome: str, aiuto: str | None, *, radice: bool = False) -> App:
    """Un'App con i titoli in italiano e la console di Ares.

    - `aiuto` e' la riga sotto l'uso; `None` per la radice, cosi' Cyclopts
      mostra il docstring del comando di default (la chat).
    - `result_action="return_value"`: `app(argv)` restituisce il codice invece
      di chiamare `sys.exit`; decide `__main__`, e i test lo leggono.
    - `negative=""` toglie i `--no-<flag>` e un flag spento non mostra
      `[default: False]`.
    """
    app = App(
        name=nome,
        help=aiuto,
        version=ares.__version__ if radice else None,
        console=UI.console,
        group_commands=Group("Comandi"),
        group_parameters=Group("Parametri"),
        group_arguments=Group("Argomenti"),
        default_parameter=Parameter(negative="", show_default=_mostra_default),
        result_action="return_value",
    )
    # Le due voci aggiunte da Cyclopts, tradotte.
    app["--help"].help = "Mostra questo aiuto ed esce."
    if radice:
        app["--version"].help = "Mostra la versione ed esce."
    return app
