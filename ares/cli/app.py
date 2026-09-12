"""Il comando `ares`, con la chat come default e la manutenzione sotto.

    ares                      una conversazione nuova nella cartella corrente
    ares resume               riprende l'ultima conversazione di questa cartella
    ares -p "domanda"         una risposta e basta, anche in una pipe
    ares resume -p "domanda"  la stessa cosa, sull'ultima conversazione di qui
    ares --workspace ~/prog   apre la chat su un'altra cartella
    ares --session progetto   una sessione con un nome fisso
    ares init                 scrive un ARES.md di partenza nella cartella
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
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from ares import config
from ares.cli.comando import ESITO_FATTO, ESITO_GUASTO, nuova_app
from ares.cli.ui import UI

# Senza `help`: l'aiuto di `ares` e' il docstring della chat qui sotto, con
# la descrizione e gli esempi, e non una riga che nasconde entrambi.
app = nuova_app("ares", None, radice=True)

# La descrizione e' ripetuta qui perche' il modulo non e' ancora importato
# quando `ares --help` la stampa: e' il prezzo del caricamento pigro.
SOTTOCOMANDI = (
    ("backup", "ares.backup.snapshots:app", "Snapshot locali dello stato di Ares"),
    ("sessions", "ares.sessions.maintenance:app", "Retention delle sessioni e dei risultati tool"),
    ("entities", "ares.entities.maintenance:app", "Audit e fusione delle entita' duplicate"),
    ("preflight", "ares.ops.preflight:app", "Controlla che Ollama risponda e che i modelli ci siano"),
    ("inspect", "ares.ops.inspect_learning:app", "Ispeziona gli archivi di apprendimento senza toccarli"),
    ("migrate", "ares.ops.migrazione:app", "Sposta stato e backup di prima in ~/.ares"),
)
# L'ordine dell'aiuto e' questo, non l'alfabetico: prima cio' che si usa
# ogni giorno, poi la manutenzione nell'ordine in cui la si incontra.
for _posizione, (_nome, _modulo, _aiuto) in enumerate(SOTTOCOMANDI, start=2):
    app.command(_modulo, name=_nome, help=_aiuto, sort_key=_posizione)


# Le opzioni che `ares` e `ares resume` condividono, scritte una volta.
# `name="*"` le appiattisce nell'aiuto accanto alle altre: chi legge non sa
# che sono un oggetto, e non deve saperlo.
@Parameter(name="*")
@dataclass
class OpzioniChat:
    """Le opzioni comuni alla chat nuova e a quella ripresa.

    Args:
        user: identificativo dell'utente.
        workspace: la cartella su cui lavorare, se non e' quella corrente.
        modo: quanto Ares fa da solo: manuale chiede per ogni traccia sul disco, modifiche scrive da solo,
            piano legge soltanto, auto non chiede mai.
        debug: mostra le chiamate al modello.
        metriche: mostra il costo di ogni turno: finestra occupata, token, secondi.
    """

    user: str = config.DEFAULT_USER_ID
    workspace: Path | None = None
    modo: config.Modo = config.MODO_PREDEFINITO
    debug: bool = False
    metriche: bool = False


@app.default
def chat(
    *,
    prompt: Annotated[str | None, Parameter(name=("--prompt", "-p"))] = None,
    session: str | None = None,
    opzioni: OpzioniChat | None = None,
) -> int:
    """Assistente personale locale su Agno e Ollama.

    `ares` apre una conversazione nuova nella cartella da cui lo lanci, e
    lavora sui suoi file come un collaboratore che si siede nel tuo
    progetto. Ogni conversazione nasce li' e ha il proprio contesto -
    obiettivo, piano, avanzamento - che `ares resume` riapre. Il profilo e
    le memorie sono per utente e ci sono in ogni conversazione.

    Esempi:

        ares                      una conversazione nuova, qui
        ares resume               riprende l'ultima di questa cartella
        ares -p "domanda"         una risposta e basta, anche in una pipe
        ares resume -p "domanda"  la stessa cosa, sull'ultima di qui
        ares --workspace ~/prog   apre la chat su un'altra cartella
        ares --modo modifiche     scrive da solo, chiede per il resto
        ares preflight            l'ambiente e' pronto?

    Args:
        prompt: una domanda sola: risponde ed esce; in una pipe, stdin si aggiunge alla domanda.
        session: un nome fisso per la sessione, invece di una conversazione nuova.
    """
    from ares.cli.chat import avvia

    o = opzioni or OpzioniChat()
    return avvia(
        session=session,
        user=o.user,
        workspace=o.workspace,
        debug=o.debug,
        metriche=o.metriche,
        prompt=prompt,
        modo=o.modo,
    )


@app.command(sort_key=0)
def resume(
    *,
    prompt: Annotated[str | None, Parameter(name=("--prompt", "-p"))] = None,
    scegli: bool = False,
    opzioni: OpzioniChat | None = None,
) -> int:
    """Riprende l'ultima conversazione nata in questa cartella.

    Tornare su un progetto vuol dire ritrovare obiettivo, piano e avanzamento
    dove li avevi lasciati. Senza conversazioni in questa cartella esce con 2,
    rifiutato: `ares` da solo ne apre una nuova.

    Args:
        prompt: una domanda sola sull'ultima conversazione: risponde ed esce; stdin in pipe si aggiunge.
        scegli: mostra le conversazioni di questa cartella e ne fa scegliere una.
    """
    from ares.cli.chat import avvia

    o = opzioni or OpzioniChat()
    return avvia(
        user=o.user,
        workspace=o.workspace,
        debug=o.debug,
        metriche=o.metriche,
        riprendi=True,
        scegli=scegli,
        prompt=prompt,
        modo=o.modo,
    )


@app.command(sort_key=1)
def init() -> int:
    """Scrive un ARES.md di partenza nella cartella corrente.

    Ares lo legge all'avvio quando lavora qui: convenzioni del progetto, cosa
    non toccare, come si lanciano le prove. Un file che esiste gia' non viene
    toccato.
    """
    from ares.cli import cartella

    try:
        destinazione = cartella.scrivi_scheletro(Path.cwd())
    except FileExistsError as errore:
        UI.err("ERRORE: " + str(errore))
        return ESITO_GUASTO
    except OSError as errore:
        UI.err("ERRORE: impossibile scrivere " + config.WORKSPACE_ISTRUZIONI + ": " + str(errore))
        return ESITO_GUASTO
    UI.pair("Scritto", str(destinazione), style="ares.title")
    UI.line("Compilalo con le regole del progetto: Ares lo leggera' al prossimo avvio qui.", style="ares.muted")
    return ESITO_FATTO


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
