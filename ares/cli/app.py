"""Il comando `ares`, con la chat come default e la manutenzione sotto.

    ares                      apre la chat
    ares --session progetto   una sessione separata
    ares backup list          gli snapshot
    ares sessions status      le sessioni in archivio
    ares entities audit       i duplicati fra le entita'
    ares preflight            l'ambiente e' pronto?
    ares inspect              cosa Ares ha imparato

I sottocomandi si registrano per nome di modulo e non per import: Cyclopts
carica `ares.backup.snapshots` solo quando qualcuno scrive `ares backup`, e
`ares preflight` non paga l'import di Agno che serve alla chat. E' anche il
motivo per cui il default della chat sta qui con la firma e non con il corpo:
il corpo vive in `chat.py`, che importa l'agente, e si carica al primo turno.

I sei script di `pyproject.toml` restano: `ares-backup list` e' `ares backup
list`, e passa dalla stessa App perche' l'aiuto dica la forma nuova.
"""

import sys
from collections.abc import Sequence

from ares import config
from ares.cli.comando import nuova_app

app = nuova_app("ares", "Assistente personale locale su Agno e Ollama", radice=True)

# La descrizione e' ripetuta qui perche' il modulo non e' ancora importato
# quando `ares --help` la stampa: e' il prezzo del caricamento pigro.
SOTTOCOMANDI = (
    ("backup", "ares.backup.snapshots:app", "Snapshot locali dello stato di Ares"),
    ("sessions", "ares.sessions.maintenance:app", "Retention delle sessioni e dei risultati tool"),
    ("entities", "ares.entities.maintenance:app", "Audit e fusione delle entita' duplicate"),
    ("preflight", "ares.ops.preflight:app", "Controlla che Ollama risponda e che i modelli ci siano"),
    ("inspect", "ares.ops.inspect_learning:app", "Ispeziona gli archivi di apprendimento senza toccarli"),
)
for _nome, _modulo, _aiuto in SOTTOCOMANDI:
    app.command(_modulo, name=_nome, help=_aiuto)


@app.default
def chat(
    *,
    session: str = "principale",
    user: str = config.DEFAULT_USER_ID,
    debug: bool = False,
    metriche: bool = False,
) -> None:
    """Apre la chat con Ares.

    Ogni sessione ha il proprio contesto: obiettivo, piano, avanzamento. Il
    profilo e le memorie sono per utente e attraversano tutte le sessioni.

    Args:
        session: identificativo della sessione.
        user: identificativo dell'utente.
        debug: mostra le chiamate al modello.
        metriche: mostra il costo di ogni turno: finestra occupata, token, secondi.
    """
    from ares.cli.chat import avvia

    avvia(session=session, user=user, debug=debug, metriche=metriche)


def esegui(sottocomando: str, argomenti: Sequence[str] | None = None) -> int:
    """Lancia un sottocomando come farebbe `ares <sottocomando> ...`.

    E' cio' che gli alias `ares-backup`, `ares-sessions`... chiamano: la
    stessa App, cosi' aiuto ed errori hanno una forma sola. Restituisce il
    codice di uscita; `None` vale zero.
    """
    resto = list(argomenti) if argomenti is not None else sys.argv[1:]
    esito = app([sottocomando, *resto])
    return int(esito) if isinstance(esito, int) else 0


def main() -> None:
    esito = app()
    if isinstance(esito, int) and esito:
        sys.exit(esito)
