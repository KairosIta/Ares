"""La fabbrica delle App Cyclopts di Ares: un aspetto solo per tutti i comandi.

Ogni comando - la chat, `backup`, `sessions`, `entities`, `preflight`,
`inspect` - nasce da qui, cosi' l'aiuto ha gli stessi titoli, la stessa
console e le stesse convenzioni ovunque. Prima ogni modulo costruiva il
proprio `argparse.ArgumentParser`: sei parser scritti a mano, con `--user`
ridefinito cinque volte e nessun completamento della shell.

Cyclopts legge firma e docstring della funzione: i tipi diventano la
conversione, i default compaiono nell'aiuto, la sezione `Args:` descrive le
opzioni. La console e' quella di `UI`, quindi una pipe o `NO_COLOR` spengono
i colori anche nell'aiuto, come gia' accade nella chat.
"""

from collections.abc import Callable

from cyclopts import App, Group, Parameter

import ares
from ares.cli.ui import UI
from ares.state.lock import StatoOccupato, lock_stato

# I codici di uscita, uguali per ogni comando. Erano cinque wrapper con
# quattro idee diverse: lo stato occupato valeva 1 in chat, backup e migrate
# e 2 in sessions ed entities, una conferma sbagliata valeva 1 in una fusione
# e 2 in un restore. Uno script che lancia `ares` deve poter distinguere
# quattro cose, e sono queste:
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


def codice_di(errore: BaseException, *, rifiuti: tuple[type[BaseException], ...]) -> int:
    """Il codice per un'eccezione prevista: occupato, rifiuto o guasto."""
    if isinstance(errore, StatoOccupato):
        return ESITO_OCCUPATO
    if isinstance(errore, rifiuti):
        return ESITO_RIFIUTO
    return ESITO_GUASTO


def esegui_protetto(
    azione: Callable[[], int],
    *,
    esclusivo: bool,
    rifiuti: tuple[type[BaseException], ...] = (),
    guasti: tuple[type[BaseException], ...] = (OSError,),
    riprova: str = "Attendi che chat, backup, restore o manutenzione terminino e riprova.",
) -> int:
    """Esegue `azione` sotto il lock dello stato e traduce gli errori previsti in codici.

    E' il contorno che sessions, entities, backup e migrate scrivevano
    ognuno a modo proprio. `rifiuti` sono le eccezioni con cui l'azione dice
    di no - una manutenzione rifiutata, un argomento incoerente - e valgono
    2; `guasti` valgono 1; lo stato occupato vale 3 sempre. Tutto il resto
    non e' previsto e passa, perche' un traceback che non ci si aspetta va
    letto, non nascosto dietro un codice.
    """
    try:
        with lock_stato(esclusivo=esclusivo):
            return azione()
    except StatoOccupato as errore:
        UI.err("Impossibile usare lo stato di Ares: " + str(errore))
        UI.err(riprova, style="ares.muted")
        return ESITO_OCCUPATO
    except rifiuti as errore:
        UI.err("Rifiutato: " + str(errore))
        return ESITO_RIFIUTO
    except guasti as errore:
        UI.err("ERRORE: " + str(errore))
        return ESITO_GUASTO


def nuova_app(nome: str, aiuto: str, *, radice: bool = False) -> App:
    """Un'App con i titoli in italiano e la console di Ares.

    `result_action="return_value"` fa restituire a `app(argv)` cio' che la
    funzione restituisce, invece di chiamare `sys.exit`: i `main()` dei
    moduli continuano a restituire il codice di uscita e sono le poche righe
    di `__main__` a decidere se uscire. E' anche cio' che i test chiamano.

    `negative=""` toglie i `--no-debug`, `--no-yes` che Cyclopts aggiunge a
    ogni flag booleano: qui un flag si accende e basta, e l'aiuto resta
    leggibile.
    """
    app = App(
        name=nome,
        help=aiuto,
        version=ares.__version__ if radice else None,
        console=UI.console,
        group_commands=Group("Comandi"),
        group_parameters=Group("Parametri"),
        group_arguments=Group("Argomenti"),
        default_parameter=Parameter(negative=""),
        result_action="return_value",
    )
    # Le due voci che Cyclopts aggiunge da se' hanno la descrizione in
    # inglese; qui tutto il resto parla italiano.
    app["--help"].help = "Mostra questo aiuto ed esce."
    if radice:
        app["--version"].help = "Mostra la versione ed esce."
    return app
