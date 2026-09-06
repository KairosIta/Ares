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

from cyclopts import App, Group, Parameter

import ares
from ares.cli.ui import UI


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
