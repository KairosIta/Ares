"""
Prova della REPL senza l'agente
===============================
Uso:
    .venv/bin/python tests/repl_test.py

Cio' che della chat si puo' provare senza costruire l'agente e senza
modello: le conferme lette e applicate, le metriche e l'esito degli
strumenti, il rendering Rich su pipe e su un terminale simulato,
l'indicatore di attivita', il core del turno con eventi fabbricati, i log
di Agno, la cronologia privata, l'editor con completamento e multilinea, i
comandi locali.

Stava tutto in `smoke_test.py`, che era diventato il posto dove finiva
ogni prova offline: duemilacinquecento righe in cui l'assemblaggio
dell'agente e il comportamento di un widget stavano nello stesso elenco.
La divisione segue cio' che serve per girare: lo smoke costruisce
l'agente e semina gli store, qui non si apre nessun database di Ares e un
fallimento non puo' venire dal cablaggio.

Le prove che passano per la REPL intera - il ciclo di `esegui_turno`, il
processo separato con stdin da una pipe - restano in `cli_test.py`, che
copre i comandi con cui Ares si usa davvero.
"""

import contextlib
import io
import logging
import os
import shlex
import shutil
import sys
import time
from pathlib import Path
from unittest.mock import patch

from _comune import esegui, esigi, prepara_ambiente

# La cronologia privata e i lock della REPL vivono nell'archivio: anche
# senza agente i percorsi vanno decisi prima di importare config.
RADICE_PROVA = prepara_ambiente("repl")
ARCHIVIO_PROVA = str(RADICE_PROVA / "stato")

from agno.metrics import MessageMetrics, ModelMetrics, RunMetrics, ToolCallMetrics  # noqa: E402
from agno.models.response import ToolExecution  # noqa: E402
from agno.run.agent import RunOutput  # noqa: E402
from agno.run.base import RunStatus  # noqa: E402
from prompt_toolkit.completion import CompleteEvent  # noqa: E402
from prompt_toolkit.document import Document  # noqa: E402
from prompt_toolkit.input.defaults import create_pipe_input  # noqa: E402
from prompt_toolkit.output import DummyOutput  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.text import Text  # noqa: E402

from ares import config  # noqa: E402
from ares.agent.turn_core import (  # noqa: E402
    TurnEngine,
    TurnEvent,
    TurnEventKind,
    normalize_events,
    run_turn_cycle,
)
from ares.cli import render  # noqa: E402
from ares.cli.chat import (  # noqa: E402
    AGNO_LOGGER_NAMES,
    COMANDI,
    chiedi_conferme,
    configura_log_agno,
    finestra_occupata,
    gestisci_comando,
    mostra_flusso,
    righe_argomento,
    righe_esito,
    righe_metriche,
    righe_richiesta,
    righe_scrittura,
    risolvi_comando,
    stampa_aiuto,
)
from ares.cli.editor import (  # noqa: E402
    CRONOLOGIA_INTESTAZIONE,
    CliInput,
    CompletamentoComandi,
    CronologiaSicura,
)
from ares.cli.ui import CliRenderer, RichRunStream  # noqa: E402
from ares.state import platform_files  # noqa: E402


class _MessaggioFinto:
    """Un messaggio con i soli metrics che il rendering guarda."""

    def __init__(self, role: str, input_tokens: int):
        self.role = role
        self.metrics = MessageMetrics(input_tokens=input_tokens)


class _RunFinto:
    """Un RunOutput ridotto ai due campi da cui si leggono le metriche.

    Fabbricato invece di prodotto da un turno vero perche' questa prova
    promette di non caricare pesi. I valori riproducono metriche plausibili.
    """

    def __init__(self, metrics, messages):
        self.metrics = metrics
        self.messages = messages


def scritture_in_memoria() -> str:
    """Gli strumenti di memoria mostrano cosa hanno ricevuto; gli altri no.

    L'esito di `save_learning` e' "Learning saved: <titolo>": il testo
    dell'intuizione, che e' cio' che entra nel prompt di ogni sessione
    futura, sta solo negli argomenti. Un `read_file` non deve invece
    produrre niente qui, altrimenti l'eco raddoppia l'esito di ogni
    strumento.
    """
    salvataggio = ToolExecution(
        tool_name="save_learning",
        tool_args={
            "title": "Config prima dei flag",
            "learning": "Le impostazioni  durature vanno\nin config.py.",
            "context": None,
            "tags": ["configurazione", "stile"],
        },
        result="Learning saved: Config prima dei flag (namespace: user/prova)",
    )
    righe = righe_scrittura(salvataggio)
    esigi(righe[0] == "   in memoria: save_learning", "la prima riga e' " + repr(righe[0]))
    esigi("   | title: Config prima dei flag" in righe, "il titolo non compare: " + repr(righe))
    esigi(
        "   | learning: Le impostazioni durature vanno in config.py." in righe,
        "il testo dell'intuizione non e' reso su una riga: " + repr(righe),
    )
    esigi("   | tags: configurazione, stile" in righe, "la lista dei tag non e' resa: " + repr(righe))
    esigi(not any("context" in r for r in righe), "un argomento assente occupa una riga: " + repr(righe))

    lettura = ToolExecution(tool_name=config.WORKSPACE_PREFIX + "read_file", tool_args={"path": "x"}, result="ok")
    esigi(righe_scrittura(lettura) == [], "uno strumento che non scrive in memoria produce righe")
    esigi(righe_scrittura(ToolExecution()) == [], "uno strumento senza nome produce righe")

    # Nel flusso: con l'eco acceso le righe seguono l'esito, spento no.
    class FlussoFinto:
        def __init__(self):
            self.gruppi = []

        def activity_stopped(self):
            pass

        def tool_result(self, righe, *, errore=False):
            self.gruppi.append(list(righe))

    evento = TurnEvent(kind=TurnEventKind.TOOL_COMPLETED, tool=salvataggio)
    flusso = FlussoFinto()
    with patch.object(config, "MOSTRA_APPRENDIMENTI", True), patch.object(config, "MOSTRA_ESITO_STRUMENTI", True):
        render.mostra_evento(flusso, evento)
    esigi(len(flusso.gruppi) == 2, "esito ed eco non sono due gruppi: " + repr(flusso.gruppi))
    esigi(flusso.gruppi[1][0] == "   in memoria: save_learning", "l'eco non segue l'esito")
    flusso = FlussoFinto()
    with patch.object(config, "MOSTRA_APPRENDIMENTI", False), patch.object(config, "MOSTRA_ESITO_STRUMENTI", True):
        render.mostra_evento(flusso, evento)
    esigi(len(flusso.gruppi) == 1, "con l'eco spento le righe compaiono lo stesso")
    return "argomenti interi per save_learning, niente per read_file, e il flag li accende"


def conferme_leggibili() -> str:
    """Un comando lungo arriva intero e con i confini visibili alla conferma.

    `Workspace` non e' una sandbox di processo - lo dice il suo docstring e
    `run_command` risponde su `/etc/hostname` - quindi la conferma umana e'
    l'unico confine che regge. Cio' che l'utente legge in quel momento e'
    parte del confine: se un comando venisse troncato, l'autorizzazione
    riguarderebbe qualcosa di diverso da cio' che viene eseguito.

    Il caso lungo verifica anche comandi distribuiti su piu' righe.
    """
    comando = [
        "bash",
        "-lc",
        "find . -name '*.tmp' -newer riferimento.txt -print0 | xargs -0 rm -f",
    ] + ["--opzione-" + str(n) for n in range(17)]
    esecuzione = ToolExecution(
        tool_name=config.WORKSPACE_PREFIX + "run_command",
        tool_args={"args": comando, "timeout": 120},
    )
    righe = righe_richiesta(esecuzione, radice=config.WORKSPACE_DIR)
    testo = "\n".join(righe)

    citato = [r for r in righe if r.strip().startswith("args:")]
    esigi(len(citato) == 1, "la riga del comando non e' una sola: " + repr(citato))
    ricomposto = citato[0].split("args: ", 1)[1]

    # Il controllo vero e' il giro di ritorno: se la riga a schermo si
    # rilegge come la lista di partenza, allora niente e' stato perso,
    # niente aggiunto e le virgolette cadono dove separano davvero un
    # argomento dal successivo. Confrontare i pezzi uno per uno non
    # basterebbe: `shlex.join` cita, quindi l'elemento con gli spazi dentro
    # non compare mai verbatim, ed e' proprio quello che va reso bene.
    # Il troncamento si controlla per primo: e' la regressione piu' probabile,
    # e su una riga tagliata `shlex.split` morirebbe con "No closing
    # quotation", che dice cosa e' successo alla stringa e non al comando.
    esigi("..." not in testo, "la conferma tronca il comando: " + repr(testo))
    esigi(
        shlex.split(ricomposto) == comando,
        "la riga ricomposta non torna al comando originale: " + repr(ricomposto),
    )
    esigi(str(config.WORKSPACE_DIR) in testo, "la conferma non dice in quale directory si esegue")
    esigi(any("timeout: 120" in r for r in righe), "un argomento semplice non compare")

    # Un valore multiriga non deve schiacciarsi su una riga sola.
    multiriga = righe_argomento("content", "prima\nseconda")
    esigi(len(multiriga) == 3, "un valore multiriga non viene aperto: " + repr(multiriga))

    return str(len(comando)) + " elementi resi per intero, citati e con la directory"


def conferme_applicate() -> str:
    """Il consenso e il rifiuto risolvono davvero i requirement in pausa."""

    class RequisitoFinto:
        def __init__(self, nome: str, *, da_confermare: bool = True):
            self.needs_confirmation = da_confermare
            self.tool_execution = ToolExecution(tool_name=nome, tool_args={"path": "note.txt"})
            self.confermato = False
            self.rifiutato = "mai"

        def confirm(self):
            self.confermato = True

        def reject(self, motivo=None):
            self.rifiutato = motivo

    class RispostaFinta:
        def __init__(self, requisiti):
            self.active_requirements = requisiti

    class InputFinto:
        def __init__(self, *risposte):
            self.risposte = list(risposte)
            self.chiamate = []

        def ask(self, etichetta: str, *, muted: bool = False) -> str:
            self.chiamate.append((etichetta, muted))
            risposta = self.risposte.pop(0)
            if isinstance(risposta, BaseException):
                raise risposta
            return risposta

    class UiFinta:
        def __init__(self):
            self.richieste = []
            self.righe_vuote = 0

        def confirmation(self, righe):
            self.richieste.append(righe)

        def blank(self):
            self.righe_vuote += 1

    ui = UiFinta()
    ui_originale = render.UI
    render.UI = ui
    try:
        ignorato = RequisitoFinto("interno", da_confermare=False)
        accettato = RequisitoFinto(config.WORKSPACE_PREFIX + "delete_file")
        input_si = InputFinto("sì")
        risolti = chiedi_conferme(RispostaFinta([ignorato, accettato]), input_si)
        esigi(risolti == 1, "un requisito che non chiede conferma viene contato")
        esigi(accettato.confermato and accettato.rifiutato == "mai", "il sì non conferma il requisito")
        esigi(not ignorato.confermato and ignorato.rifiutato == "mai", "un requisito interno viene modificato")
        esigi(len(ui.richieste) == 1, "la richiesta di autorizzazione non viene mostrata una volta sola")
        esigi(str(config.WORKSPACE_DIR) in "\n".join(ui.richieste[0]), "la richiesta non mostra la radice")

        rifiutato = RequisitoFinto(config.WORKSPACE_PREFIX + "run_command")
        input_no = InputFinto("no", "comando troppo ampio")
        risolti = chiedi_conferme(RispostaFinta([rifiutato]), input_no)
        esigi(risolti == 1, "un rifiuto non risolve il requisito")
        esigi(not rifiutato.confermato, "un no conferma comunque il requisito")
        esigi(rifiutato.rifiutato == "comando troppo ampio", "il motivo del rifiuto non arriva al requirement")
        esigi(input_no.chiamate[-1][1], "il motivo del rifiuto non usa l'input attenuato")

        interrotto = RequisitoFinto(config.WORKSPACE_PREFIX + "move_file")
        risolti = chiedi_conferme(
            RispostaFinta([interrotto]),
            InputFinto(KeyboardInterrupt(), EOFError()),
        )
        esigi(risolti == 1, "Ctrl-C lascia irrisolto il requisito")
        esigi(interrotto.rifiutato is None, "Ctrl-C inventa un motivo di rifiuto")
        esigi(ui.righe_vuote == 2, "Ctrl-C/EOF non chiudono pulitamente le due richieste")
        esigi(chiedi_conferme(RispostaFinta([]), InputFinto()) == 0, "una pausa ignota risulta risolta")
    finally:
        render.UI = ui_originale

    return "sì, no con motivo, Ctrl-C/EOF e pausa ignota risolvono i requirement attesi"


def metriche_del_turno() -> str:
    """La finestra mostrata e' il prompt vero, non la somma delle chiamate.

    `accumulate_model_metrics` somma dentro la riga del modello, quindi
    `metrics.input_tokens` comprende anche le estrazioni delle memorie.
    Presentarlo come occupazione della finestra sarebbe sbagliato in
    silenzio: i numeri qui sotto distinguono il totale del run dal prompt.
    """
    principale = ModelMetrics(
        id=config.MAIN_MODEL,
        input_tokens=7097,
        output_tokens=25,
        provider_metrics={"total_duration": 1197811950},
    )
    apprendimento = ModelMetrics(
        id=config.MAIN_MODEL,
        input_tokens=3902,
        output_tokens=959,
        provider_metrics={"total_duration": 16064666271},
    )
    risposta = _RunFinto(
        metrics=RunMetrics(
            input_tokens=10999,
            output_tokens=984,
            duration=20.1,
            details={"model": [principale], "learning_model": [apprendimento]},
        ),
        messages=[
            _MessaggioFinto("system", 0),
            _MessaggioFinto("user", 0),
            _MessaggioFinto("assistant", 6872),
            _MessaggioFinto("user", 0),
            _MessaggioFinto("assistant", 7097),
        ],
    )
    esigi(
        finestra_occupata(risposta) == 7097,
        "la finestra e' " + str(finestra_occupata(risposta)) + " invece di 7097",
    )
    riga = righe_metriche(risposta)[0]
    # Il tetto atteso si calcola da config con la stessa base 1024 del
    # renderer, cosi' il test segue ogni modifica di NUM_CTX.
    tetto = str(round(config.NUM_CTX / 1024.0, 1)) + "k"
    esigi("6.9k/" + tetto in riga, "la finestra non e' resa sul tetto di config: " + repr(riga))
    esigi("10.7k" not in riga and "10999" not in riga, "la riga mostra la somma del run: " + repr(riga))
    # I secondi dell'apprendimento vengono da total_duration, in nanosecondi:
    # senza la divisione uscirebbero sedici miliardi.
    esigi("16.1 s" in riga, "i secondi di apprendimento non sono resi: " + repr(riga))

    # Un turno interrotto o fallito arriva senza metriche, e la riga non si
    # inventa: e' il ramo che `esegui_turno` produce dopo un Ctrl-C.
    esigi(righe_metriche(_RunFinto(metrics=None, messages=[])) == [], "un turno senza metriche produce una riga")
    esigi(righe_metriche(_RunFinto(metrics=RunMetrics(), messages=[])) == [], "un turno a zero produce una riga")
    return "finestra 7097 distinta dai 10999 del run, nanosecondi convertiti"


class _EventoFinto:
    """Evento Agno ridotto ai campi letti dal normalizzatore del core."""

    def __init__(self, event, tool=None, error=None, content=None):
        self.event = event
        self.tool = tool
        self.error = error
        self.content = content


def esito_strumenti() -> str:
    """L'esito di uno strumento si vede, e un fallimento si vede una volta sola.

    Il rischio non e' che manchi una riga: e' che ne compaiano due. Un tool
    fallito emette `ToolCallCompleted` con dentro il testo dell'errore e
    **poi** `ToolCallError`, in entrambi i percorsi di Agno che li producono.
    Trattarli come alternative stampa l'errore due volte, e la prima volta
    lo presenta come un esito riuscito.
    """
    riuscito = ToolExecution(
        tool_name=config.WORKSPACE_PREFIX + "read_file",
        result="prima riga\nseconda riga\nterza riga\nquarta riga\nquinta riga",
        metrics=ToolCallMetrics(duration=0.42),
    )
    righe = righe_esito(riuscito)
    esigi(righe[0].strip().startswith("esito: "), "l'esito non si annuncia: " + repr(righe))
    esigi("58 caratteri" in righe[0], "i caratteri non sono contati: " + repr(righe[0]))
    esigi("0.4 s" in righe[0], "la durata non e' resa: " + repr(righe[0]))
    esigi(
        len(righe) == 1 + config.ESITO_RIGHE + 1,
        "l'anteprima non si ferma a " + str(config.ESITO_RIGHE) + " righe: " + repr(righe),
    )
    esigi("(+ altre 2 righe)" in righe[-1], "il taglio in altezza non si dichiara: " + repr(righe[-1]))

    # Una riga sola avanzata: "altre 1 righe" e' comparso in una prova vera.
    quattro = righe_esito(ToolExecution(result="a\nb\nc\nd"))
    esigi("un'altra riga" in quattro[-1], "il singolare non e' reso: " + repr(quattro[-1]))
    esatte = righe_esito(ToolExecution(result="a\nb\nc"))
    esigi(
        "+" not in esatte[-1],
        "un taglio viene annunciato dove non c'e': " + repr(esatte[-1]),
    )

    # Larghezza: una riga sola, lunghissima, come la restituisce un comando.
    lunga = righe_esito(ToolExecution(result="x" * 400))
    esigi(len(lunga[1]) <= config.ESITO_LARGHEZZA + 6, "il taglio in larghezza non avviene: " + str(len(lunga[1])))
    esigi(lunga[1].endswith("..."), "il taglio in larghezza non si dichiara: " + repr(lunga[1]))
    esigi("400 caratteri" in lunga[0], "la misura vera si perde nel troncamento: " + repr(lunga[0]))

    # La durata manca sul percorso di ripresa dopo una conferma: il segmento
    # deve sparire, non stampare zero.
    senza = righe_esito(ToolExecution(result="ok"))
    esigi(" s" not in senza[0], "senza metriche compare comunque una durata: " + repr(senza[0]))
    vuoto = righe_esito(ToolExecution(result=None))
    esigi(vuoto == ["   esito: nessun contenuto"], "un risultato vuoto non e' detto: " + repr(vuoto))

    # Il conto vero: quante volte compare l'errore attraversando i due eventi
    # nell'ordine in cui Agno li emette.
    fallito = ToolExecution(
        tool_name=config.WORKSPACE_PREFIX + "read_file",
        result="FileNotFoundError: pippo.md",
        tool_call_error=True,
    )
    flusso = [
        _EventoFinto("ToolCallStarted", tool=fallito),
        _EventoFinto("ToolCallCompleted", tool=fallito, content=fallito.result),
        _EventoFinto("ToolCallError", tool=fallito, error=fallito.result),
    ]
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        mostra_flusso(normalize_events(flusso))
    reso = catturato.getvalue()
    esigi(reso.count("pippo.md") == 1, "l'errore compare " + str(reso.count("pippo.md")) + " volte:\n" + reso)
    esigi("esito:" not in reso, "un tool fallito viene annunciato come riuscito:\n" + reso)
    esigi("errore: FileNotFoundError: pippo.md" in reso, "l'errore non e' reso:\n" + reso)

    # Un evento di errore senza testo non deve passare per riuscito.
    muto = righe_esito(ToolExecution(tool_call_error=True), errore="")
    esigi(muto == ["   errore: senza messaggio"], "un errore muto sparisce: " + repr(muto))

    return "errore reso una volta sola, anteprima tagliata in righe e larghezza"


def renderer_rich() -> str:
    """Il renderer e' sicuro anche fuori da un terminale interattivo.

    Test e pipe vedono una sola copia del testo, senza ANSI e senza
    interpretare come markup parentesi quadre provenienti da modello, path o
    argomenti. ``RichRunStream`` usa ora la stessa via append-only anche su un
    TTY; il controllo successivo verifica in piu' i comandi del cursore.

    I controlli di terminale non devono passare da nessuna delle vie che
    mostrano testo scelto dal modello o letto dal workspace: il pannello di
    conferma, il nome e l'anteprima di uno strumento, l'eco. Rich lascia
    ``ESC`` intatto anche verso una pipe, quindi la cattura basta a vederlo
    passare: un ``ESC [2K ESC [1G`` in un argomento cancellerebbe la riga
    che chiede di confermare proprio quell'argomento.
    """
    catturato = io.StringIO()
    renderer = CliRenderer(Console(file=catturato, color_system=None, force_terminal=False, width=120))
    renderer.line("[red]testo del modello[/red]")
    renderer.banner(modello="modello[Q8_0]", sessione="sessione[prova]", utente="utente[prova]")
    renderer.confirmation(
        [
            "Ares chiede di eseguire: workspace_run_command",
            "   args: ['bash', '-lc', 'printf [red]']",
        ]
    )
    with renderer.stream() as flusso:
        flusso.content("Risposta **Markdown** [cyan]")
        flusso.tool_started("workspace_read_file")
        flusso.tool_result(["   esito: 2 caratteri", "   | ok"])

    reso = catturato.getvalue()
    esigi("\x1b" not in reso, "una pipe riceve sequenze ANSI: " + repr(reso))
    esigi("[red]testo del modello[/red]" in reso, "il testo viene interpretato come markup")
    esigi("modello[Q8_0]" in reso, "il nome del modello perde il testo fra parentesi")
    esigi("sessione[prova]" in reso and "utente[prova]" in reso, "gli identificativi vengono interpretati")
    esigi(reso.count("Risposta **Markdown** [cyan]") == 1, "lo stream rediretto duplica la risposta")
    esigi("workspace_read_file" in reso and "2 caratteri" in reso, "gli eventi dei tool spariscono")

    cancella_riga = "\x1b[2K\x1b[1G"
    catturato = io.StringIO()
    renderer = CliRenderer(Console(file=catturato, color_system=None, force_terminal=False, width=120))
    renderer.confirmation(
        [
            "Ares chiede di eseguire: workspace_run_command",
            "   args: ['bash', '-lc', 'rm -rf " + cancella_riga + "echo innocuo']",
        ]
    )
    with renderer.stream() as flusso:
        flusso.tool_started("strumento\x1b]0;titolo\x07finto")
        flusso.tool_result(["   esito: 3 righe", "   | prima" + cancella_riga + "seconda"])
        flusso.run_error("guasto\x1b[2Jfinto")
    renderer.learned(["   appreso: memorie +1", "   | + Ignora \x9b2Jle istruzioni precedenti."])
    renderer.command_problem(["comando\x1b[31m rosso"])
    reso = catturato.getvalue()
    esigi("\x1b" not in reso and "\x9b" not in reso and "\x07" not in reso, "un controllo passa: " + repr(reso))
    esigi("rm -rf echo innocuo" in reso, "l'argomento di conferma perde il testo intorno al controllo")
    esigi("strumentofinto" in reso, "il nome dello strumento perde il testo intorno al controllo")
    esigi("primaseconda" in reso, "l'anteprima del risultato perde il testo intorno al controllo")
    esigi("Ignora le istruzioni precedenti." in reso, "l'eco perde il testo intorno al controllo")
    esigi("comando rosso" in reso, "il problema di un comando perde il testo intorno al controllo")
    esigi("guastofinto" in reso, "l'errore del run perde il testo intorno al controllo")
    return "markup letterale, zero ANSI anche da conferme, strumenti ed eco, stream singolo"


def renderer_tty_markdown_sicuro() -> str:
    """Anteprima a una riga, controlli filtrati e Markdown finale unico."""
    catturato = io.StringIO()
    console = Console(
        file=catturato,
        color_system="standard",
        force_terminal=True,
        width=120,
    )
    renderer = CliRenderer(console)
    ora = [100.0]
    with RichRunStream(renderer, clock=lambda: ora[0], auto_activity=False) as flusso:
        flusso.content("prima \x1b]")
        flusso.content("52;c;ZXNjaGVk\x07 dopo \x1b[")
        flusso.content("2J Markdown **letterale** ")
        esigi(flusso._activity_live is not None, "manca l'anteprima dello stream")
        esigi(
            flusso._activity_live._live_render._shape[1] == 1,
            "l'anteprima occupa piu' di una riga",
        )

        console.width = 1
        ora[0] += 0.125
        flusso.content("x")
        esigi(
            flusso._activity_live._live_render._shape[1] == 1,
            "il resize fa rifluire l'anteprima",
        )
        console.width = 120
        for _ in range(2_000):
            flusso.content("x")
        flusso.content(" fine \x1b]0;titolo\x1b")
        flusso.content("\\ C1 \x9b")
        flusso.content("2J")
        # Un comando non terminato non deve far uscire ne' il controllo ne'
        # il suo payload quando il turno si chiude.
        flusso.content(" coda-visibile \x1b]0;titolo-incompleto")

    reso = catturato.getvalue()
    esigi("\x1b]52" not in reso and "\x1b]0" not in reso, "un comando OSC raggiunge il terminale")
    esigi("\x1b[2J" not in reso and "\x9b2J" not in reso, "un comando CSI raggiunge il terminale")
    esigi(
        "ZXNjaGVk" not in reso and "titolo" not in reso,
        "resta il contenuto di un comando terminale",
    )

    # Tutto cio' che segue l'ultimo erase dell'anteprima e' output
    # permanente. Text.from_ansi rimuove soltanto gli stili Rich.
    finale = Text.from_ansi(reso.rsplit("\x1b[2K", 1)[-1]).plain
    esigi(
        all(finale.count(parola) == 1 for parola in ("prima", "dopo", "fine", "coda-visibile")),
        "il commit Markdown elimina o duplica testo: " + repr(finale),
    )
    esigi(finale.count("x") == 2_001, "i frammenti non arrivano tutti al commit finale")
    esigi("**letterale**" not in finale, "il Markdown resta sorgente letterale")
    esigi("Markdown letterale" in finale, "il Markdown finale perde contenuto")
    esigi("\x1b[2A" not in reso, "l'anteprima risale piu' di una riga")
    return "2.008 frammenti, anteprima a una riga e Markdown finale unico"


def indicatore_attivita() -> str:
    """L'attesa usa una sola riga e segue tutto il ciclo degli eventi.

    L'orologio e il pulse manuali evitano sleep fragili. La larghezza cambia
    fra due refresh: il testo ``no_wrap`` deve conservare altezza uno, che e'
    l'invariante da cui dipende la sicurezza del resize.
    """
    catturato = io.StringIO()
    console = Console(
        file=catturato,
        color_system="standard",
        force_terminal=True,
        width=80,
    )
    renderer = CliRenderer(console)
    ora = [100.0]
    with RichRunStream(
        renderer,
        clock=lambda: ora[0],
        auto_activity=False,
    ) as flusso:
        flusso.activity_started("Ares sta elaborando...")
        ora[0] = 101.9
        flusso.pulse_activity()
        esigi(flusso._activity_live is None, "l'indicatore compare prima della soglia")

        ora[0] = 102.0
        flusso.pulse_activity()
        esigi(flusso._activity_live is not None, "l'attesa lunga resta senza indicatore")
        esigi(
            flusso._activity_live._live_render._shape[1] == 1,
            "l'indicatore occupa piu' di una riga",
        )

        console.width = 1
        ora[0] = 165.0
        flusso.pulse_activity()
        esigi(
            flusso._activity_live._live_render._shape[1] == 1,
            "il resize cambia l'altezza dell'indicatore",
        )

        flusso.content("risposta-visibile ")
        esigi(flusso._activity_live is not None, "lo stream non aggiorna l'anteprima")
        esigi(not flusso._activity_waiting, "l'anteprima continua a sembrare in attesa")
        ora[0] = 167.0
        flusso.pulse_activity()
        esigi(flusso._activity_waiting, "l'indicatore non riparte dopo una nuova attesa")
        flusso.activity_stopped()
        console.width = 80
        flusso.content("finale")

    reso = catturato.getvalue()
    finale = Text.from_ansi(reso.rsplit("\x1b[2K", 1)[-1]).plain
    esigi(
        "risposta-visibile finale" in finale,
        "l'indicatore inserisce un ritorno a capo permanente: " + repr(finale),
    )
    esigi(
        reso.count("Ares sta elaborando...") == 0,
        "l'etichetta temporanea resta nello scrollback",
    )
    esigi("\x1b[2A" not in reso, "l'indicatore risale piu' di una riga")

    class FlussoFinto:
        def __init__(self):
            self.chiamate = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def activity_started(self, label):
            self.chiamate.append(("start", label))

        def activity_stopped(self):
            self.chiamate.append(("stop", None))

        def content(self, content):
            self.chiamate.append(("content", content))

        def flush(self):
            self.chiamate.append(("flush", None))

        def tool_started(self, nome):
            self.chiamate.append(("tool", nome))

        def tool_result(self, _righe, *, errore=False):
            self.chiamate.append(("result", errore))

        def run_error(self, messaggio):
            self.chiamate.append(("error", messaggio))

        def cancelled(self):
            self.chiamate.append(("cancelled", None))

    class UiFinta:
        def __init__(self, flusso):
            self.flusso = flusso

        def stream(self):
            return self.flusso

    registrato = FlussoFinto()
    strumento = ToolExecution(tool_name="workspace_read_file", result="ok")
    mostra_flusso(
        normalize_events(
            [
                _EventoFinto("ModelRequestStarted"),
                _EventoFinto("RunContent", content="ciao"),
                _EventoFinto("ModelRequestCompleted"),
                _EventoFinto("ToolCallStarted", tool=strumento),
                _EventoFinto("ToolCallCompleted", tool=strumento),
                _EventoFinto("PostHookStarted"),
                _EventoFinto("PostHookCompleted"),
                _EventoFinto("RunCompleted"),
                _EventoFinto("RunError", content="guasto"),
                _EventoFinto("RunCancelled"),
            ]
        ),
        ui=UiFinta(registrato),
    )
    etichette = [valore for azione, valore in registrato.chiamate if azione == "start"]
    esigi("Ares sta elaborando..." in etichette, "il modello non attiva l'indicatore")
    esigi(
        "workspace_read_file in esecuzione..." in etichette,
        "un tool lungo non attiva l'indicatore",
    )
    esigi(
        "Ares sta aggiornando cio' che ricorda..." in etichette,
        "il post-hook non attiva l'indicatore",
    )
    azioni = [azione for azione, _valore in registrato.chiamate]
    indice_errore = azioni.index("error")
    esigi(
        azioni[indice_errore - 1] == "stop",
        "l'errore precede la chiusura dello stato",
    )
    esigi(azioni[-2:] == ["stop", "cancelled"], "l'annullamento lascia l'indicatore attivo")
    return "soglia 2 s, resize a una riga, nessun newline e lifecycle chiuso"


def core_del_turno() -> str:
    """Il core normalizza eventi e possiede run/continue senza dipendere dalla UI."""
    pausa = RunOutput(run_id="pausa", status=RunStatus.paused, requirements=[])
    completato = RunOutput(run_id="fine", status=RunStatus.completed)

    class AgenteFinto:
        def __init__(self):
            self.chiamate = []

        def run(self, testo, **opzioni):
            self.chiamate.append(("run", testo, opzioni))
            return iter(
                [
                    _EventoFinto("ModelRequestStarted"),
                    _EventoFinto("RunContent", content="prima"),
                    _EventoFinto("RunPaused"),
                    pausa,
                ]
            )

        def continue_run(self, run_response, requirements, **opzioni):
            self.chiamate.append(("continue", run_response, requirements, opzioni))
            return iter(
                [
                    _EventoFinto("EventoFuturo", content="conservato"),
                    _EventoFinto("RunContent", content="seconda"),
                    _EventoFinto("RunCompleted"),
                    completato,
                ]
            )

    # Il primo evento precede anche la chiamata al provider: copre il lavoro
    # che Agno svolge prima di ModelRequestStarted.
    pigro = AgenteFinto()
    stream = TurnEngine(pigro).start("ciao")
    primo = next(stream)
    esigi(primo.kind is TurnEventKind.PROCESSING_STARTED, "manca lo stato iniziale del core")
    esigi(pigro.chiamate == [], "agent.run parte prima che il client veda l'attivita'")
    secondo = next(stream)
    esigi(secondo.kind is TurnEventKind.MODEL_STARTED, "l'evento modello non e' normalizzato")
    esigi(pigro.chiamate[0][0] == "run", "il core non avvia il run dopo lo stato iniziale")

    agente = AgenteFinto()
    eventi = []
    pause_risolte = []

    def risolvi(output):
        pause_risolte.append(output.run_id)
        return 1

    risultato = run_turn_cycle(
        agente,
        "domanda",
        on_event=eventi.append,
        resolve_pause=risolvi,
    )
    esigi(risultato is completato, "il ciclo non restituisce l'output della continuazione")
    esigi([c[0] for c in agente.chiamate] == ["run", "continue"], "sequenza run/continue errata")
    esigi(pause_risolte == ["pausa"], "la pausa non attraversa il resolver iniettato")
    esigi(
        sum(e.kind is TurnEventKind.PROCESSING_STARTED for e in eventi) == 2,
        "run e continuazione non annunciano entrambi la preparazione",
    )
    sconosciuto = next(e for e in eventi if e.source_name == "EventoFuturo")
    esigi(sconosciuto.kind is TurnEventKind.OTHER, "un evento futuro viene perso dal core")
    esigi(sconosciuto.content == "conservato", "l'evento futuro perde il proprio payload")
    return "eventi neutri, avvio anticipato e ciclo run/continue indipendente"


def log_cli_puliti() -> str:
    """La CLI normale filtra INFO di Agno; --debug lo riabilita."""
    logger = logging.getLogger("agno")
    snapshot = {
        nome: (
            logging.getLogger(nome).level,
            list(logging.getLogger(nome).handlers),
            [handler.level for handler in logging.getLogger(nome).handlers],
        )
        for nome in AGNO_LOGGER_NAMES
    }
    catturato = io.StringIO()
    handler_prova = logging.StreamHandler(catturato)

    try:
        # Isola l'asserzione dall'handler Rich reale, che altrimenti
        # stamperebbe il warning di prova nel resoconto della smoke suite.
        logger.handlers = [handler_prova]
        logger.setLevel(logging.DEBUG)

        configura_log_agno(False)
        logger.info("Found 0 documents")
        logger.warning("warning-visibile")
        normale = catturato.getvalue()
        esigi("Found 0 documents" not in normale, "un INFO Agno compare nella CLI normale")
        esigi("warning-visibile" in normale, "la pulizia nasconde anche i warning Agno")

        catturato.seek(0)
        catturato.truncate(0)
        configura_log_agno(True)
        logger.debug("debug-visibile")
        logger.info("info-visibile")
        debug = catturato.getvalue()
        esigi("debug-visibile" in debug and "info-visibile" in debug, "--debug filtra i log Agno")
    finally:
        for nome, (livello, handlers, livelli_handler) in snapshot.items():
            logger_originale = logging.getLogger(nome)
            logger_originale.handlers = handlers
            logger_originale.setLevel(livello)
            for handler, livello_handler in zip(handlers, livelli_handler, strict=True):
                handler.setLevel(livello_handler)

    return "INFO interni nascosti, warning preservati e --debug completo"


def cronologia_persistente() -> str:
    """Migrazione, multilinea, concorrenza, permessi e retention."""
    percorso = config.CRONOLOGIA_FILE
    if percorso.exists():
        percorso.unlink()

    # Un clone aggiornato puo' avere ancora il formato GNU Readline: una voce
    # per riga e nessuna intestazione. La prima scrittura lo migra.
    percorso.write_text("prima domanda\n", encoding="utf-8")
    percorso.chmod(0o644)
    prima_chat = CronologiaSicura(percorso, 4)
    seconda_chat = CronologiaSicura(percorso, 4)
    esigi(
        list(prima_chat.load_history_strings()) == ["prima domanda"],
        "la cronologia Readline precedente non viene riletta",
    )

    prima_chat.append_string("seconda domanda")
    # La seconda istanza e' nata prima della scrittura: store_string deve
    # rileggere il disco sotto lock, non sovrascrivere dalla propria cache.
    seconda_chat.append_string("riga di un'altra chat")
    seconda_chat.append_string("domanda su due\nrighe")

    rilette = list(CronologiaSicura(percorso, 4).load_history_strings())
    esigi(
        rilette == ["domanda su due\nrighe", "riga di un'altra chat", "seconda domanda", "prima domanda"],
        "migrazione o intreccio delle chat errato: " + repr(rilette),
    )
    esigi(
        percorso.read_text(encoding="utf-8").splitlines()[0] == CRONOLOGIA_INTESTAZIONE,
        "la cronologia non e' stata migrata al formato multilinea",
    )
    if os.name == "posix":
        esigi(
            oct(percorso.stat().st_mode)[-3:] == "600",
            "cronologia leggibile da altri: " + oct(percorso.stat().st_mode)[-3:],
        )
        esigi(
            oct(prima_chat.lock_file.stat().st_mode)[-3:] == "600",
            "lock della cronologia leggibile da altri",
        )

    limitata = CronologiaSicura(percorso, 2)
    limitata.append_string("quarta domanda")
    ultime = list(CronologiaSicura(percorso, 2).load_history_strings())
    esigi(
        ultime == ["quarta domanda", "domanda su due\nrighe"],
        "retention applicata dalla parte sbagliata: " + repr(ultime),
    )
    return "formato precedente migrato, multilinea e due chat, retention a 2"


def input_repl() -> str:
    """Prompt reale su pipe: completamento, multilinea, segnali e fallback."""
    metadati = [(nome, descrizione) for nome, _alias, descrizione, _funzione in COMANDI]
    percorso = Path(ARCHIVIO_PROVA) / "cronologia_input_test.txt"

    with create_pipe_input() as pipe:
        input_cli = CliInput(
            comandi=metadati,
            cronologia_file=percorso,
            cronologia_righe=20,
            interactive=True,
            input=pipe,
            output=DummyOutput(),
        )
        pipe.send_text("/me\t\r")
        esigi(input_cli.prompt() == "/memorie", "TAB non completa nel prompt reale")

        pipe.send_text("prima riga\x1b\rseconda riga\r")
        esigi(
            input_cli.prompt() == "prima riga\nseconda riga",
            "Alt+Invio non inserisce una nuova riga",
        )

        pipe.send_text("s\r")
        esigi(input_cli.ask("Autorizzi? ") == "s", "il prompt breve non restituisce la scelta")

        pipe.send_text("\x03")
        try:
            input_cli.prompt()
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("Ctrl-C non interrompe il prompt")

        pipe.send_text("\x04")
        try:
            input_cli.prompt()
        except EOFError:
            pass
        else:
            raise AssertionError("Ctrl-D non chiude il prompt")

    rilette = list(CronologiaSicura(percorso, 20).load_history_strings())
    esigi("s" not in rilette, "una risposta di autorizzazione finisce in cronologia")
    esigi(
        rilette[:2] == ["prima riga\nseconda riga", "/memorie"],
        "il prompt non salva le domande: " + repr(rilette),
    )

    risposte = iter(["testo da pipe", "no"])
    etichette = []

    def fallback(etichetta: str) -> str:
        etichette.append(etichetta)
        return next(risposte)

    fallback_cli = CliInput(
        comandi=metadati,
        cronologia_file=Path(ARCHIVIO_PROVA) / "cronologia_fallback_test.txt",
        cronologia_righe=20,
        interactive=False,
        fallback_input=fallback,
    )
    esigi(fallback_cli.prompt() == "testo da pipe", "il fallback non legge il messaggio")
    esigi(fallback_cli.ask("Scelta: ") == "no", "il fallback non legge la scelta")
    esigi(etichette == ["Tu › ", "Scelta: "], "prompt del fallback inattesi: " + repr(etichette))

    ostacolo = Path(ARCHIVIO_PROVA) / "non-e-una-directory"
    ostacolo.write_text("file", encoding="utf-8")
    degradata = CliInput(
        comandi=metadati,
        cronologia_file=ostacolo / "cronologia.txt",
        cronologia_righe=20,
        interactive=False,
        fallback_input=lambda _etichetta: "continua",
    )
    esigi(degradata.history_warning is not None, "il guasto della cronologia non viene annunciato")
    esigi(degradata.prompt() == "continua", "un guasto della cronologia blocca la chat")

    lock_originale = platform_files.portalocker.lock
    try:

        def lock_guasto(_file, _operazione):
            raise platform_files.portalocker.LockException("guasto simulato")

        platform_files.portalocker.lock = lock_guasto
        lock_degradato = CliInput(
            comandi=metadati,
            cronologia_file=Path(ARCHIVIO_PROVA) / "cronologia-lock-guasto.txt",
            cronologia_righe=20,
            interactive=False,
            fallback_input=lambda _etichetta: "ancora disponibile",
        )
    finally:
        platform_files.portalocker.lock = lock_originale
    esigi(lock_degradato.history_warning is not None, "il guasto del lock non viene annunciato")
    esigi(
        lock_degradato.prompt() == "ancora disponibile",
        "un guasto del backend di lock blocca la chat",
    )
    return "menu e TAB, multilinea, Ctrl-C/D, fallback pipe e cronologia in memoria"


def comandi() -> str:
    """Un comando si risolve, un refuso si dichiara, un troncamento non indovina.

    Il difetto che questo controllo esiste per prendere e' il silenzio: prima
    un comando inesistente stampava l'aiuto, cioe' la stessa cosa che stampa
    chi l'aiuto lo ha chiesto, e un refuso sembrava una risposta. L'altro e'
    la deriva: l'elenco a schermo era una stringa scritta a mano e aveva gia'
    perso `/lavoro`.
    """
    nomi = [voce[0] for voce in COMANDI]
    alias = [alias for voce in COMANDI for alias in voce[1]]
    esigi(len(set(nomi + alias)) == len(nomi + alias), "due comandi con lo stesso nome")

    # Un nome scritto per intero non viene mai reinterpretato.
    for nome in nomi:
        voce, righe = risolvi_comando(nome)
        esigi(voce is not None and voce[0] == nome, "il comando " + nome + " non si risolve in se'")
    for scritto in alias:
        voce, _ = risolvi_comando(scritto)
        esigi(voce is not None, "alias non riconosciuto: " + scritto)

    voce, _ = risolvi_comando("/mem")
    esigi(voce is not None and voce[0] == "/memorie", "un troncamento unico non si espande")

    # Ambiguo: si mostrano i candidati. Indovinare fra /entita e /esci
    # chiuderebbe la sessione al posto di leggere un archivio.
    voce, righe = risolvi_comando("/e")
    esigi(voce is None, "un troncamento ambiguo viene eseguito lo stesso")
    esigi(
        "/entita" in righe[0] and "/esci" in righe[0],
        "i candidati ambigui non sono elencati: " + repr(righe),
    )

    voce, righe = risolvi_comando("/fiel")
    esigi(voce is None, "un refuso viene eseguito")
    esigi("/fiel" in righe[0], "il refuso non viene ripetuto a schermo: " + repr(righe))
    esigi("/file" in " ".join(righe), "nessun suggerimento per un refuso vicino")

    voce, righe = risolvi_comando("/pipppo")
    esigi(voce is None, "una parola inventata viene eseguita")
    esigi(
        "/aiuto" in " ".join(righe) and "Forse" not in " ".join(righe),
        "una parola inventata non manda all'aiuto: " + repr(righe),
    )

    # L'elenco a schermo si deriva dalla tabella: se un comando nuovo non
    # compare qui, la stringa e' tornata a mano.
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        stampa_aiuto()
    aiuto = catturato.getvalue()
    for nome in nomi:
        esigi(nome in aiuto, "l'aiuto non nomina " + nome)

    # Il dispatcher: un comando sconosciuto non chiude la sessione, /esci si'.
    # Nessuno dei due tocca l'agente, quindi None basta.
    catturato = io.StringIO()
    with contextlib.redirect_stdout(catturato):
        vive = gestisci_comando("/pipppo", None, "sessione", "utente")
    esigi(vive is True, "un comando sconosciuto chiude la sessione")
    esigi(catturato.getvalue().strip() != "", "un comando sconosciuto non dice niente")
    with contextlib.redirect_stdout(io.StringIO()):
        esigi(gestisci_comando("/esci", None, "sessione", "utente") is False, "/esci non chiude")
        esigi(gestisci_comando("/qu", None, "sessione", "utente") is False, "/qu non chiude")

    completatore = CompletamentoComandi([(nome, descrizione) for nome, _alias, descrizione, _funzione in COMANDI])

    def completa(testo: str) -> list[str]:
        return [voce.text for voce in completatore.get_completions(Document(testo), CompleteEvent())]

    esigi(completa("/me") == ["/memorie"], "il menu non completa un comando")
    esigi(completa("/") == nomi, "lo slash non elenca tutti i comandi")
    esigi(completa("ricordami /mem") == [], "un comando dentro una frase apre il menu")
    esigi(completa("scrivo 23/08") == [], "una data dentro una frase apre il menu")
    esigi(completa("/sessioni lavoro") == [], "il menu copre l'argomento di un comando")

    return str(len(nomi)) + " comandi dalla tabella, refusi e troncamenti distinti"


def main() -> int:
    avvio = time.monotonic()
    # La cronologia privata sta nell'archivio, che nella chat esiste perche'
    # `build_assistant` lo prepara prima di aprirla. Qui l'agente non c'e'.
    config.prepara_archivio()
    falliti, non_conclusivi = esegui(
        (
            ("conferme leggibili  ", conferme_leggibili),
            ("conferme applicate  ", conferme_applicate),
            ("metriche del turno  ", metriche_del_turno),
            ("esito strumenti     ", esito_strumenti),
            ("scritture in memoria", scritture_in_memoria),
            ("renderer Rich       ", renderer_rich),
            ("renderer TTY        ", renderer_tty_markdown_sicuro),
            ("indicatore attivita ", indicatore_attivita),
            ("core del turno      ", core_del_turno),
            ("log CLI             ", log_cli_puliti),
            ("cronologia          ", cronologia_persistente),
            ("input REPL          ", input_repl),
            ("comandi             ", comandi),
        )
    )
    print()
    print("Concluso in", round(time.monotonic() - avvio, 2), "s")
    if falliti:
        print("Archivio della prova conservato:", ARCHIVIO_PROVA)
        print()
        print("FALLITE:", ", ".join(nome.strip() for nome in falliti))
        return 1
    shutil.rmtree(RADICE_PROVA, ignore_errors=True)
    if non_conclusivi:
        print("Non concludenti:", ", ".join(nome.strip() for nome in non_conclusivi))
    print("Nessun fallimento.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
