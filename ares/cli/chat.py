"""
REPL interattivo
================
Uso:
    ares                       nella cartella corrente, conversazione nuova
    ares resume                riprende l'ultima conversazione di questa cartella
    ares resume --scegli       la sceglie da un elenco
    ares -p "domanda"          una risposta e basta; stdin in pipe si aggiunge
    ares --workspace ~/prog    su un'altra cartella
    ares --session progetto-x  una sessione con un nome fisso
    ares --debug               mostra le chiamate al modello
    ares --metriche            costo di ogni turno

Le opzioni le dichiara `cli/app.py`, che e' il comando `ares` intero: qui
c'e' il corpo della chat, che si importa solo quando la chat parte.

La cartella da cui si lancia `ares` e' quella su cui Ares lavora. Prima di
aprirla `cli/cartella.py` la guarda: se e' la home, il disco intero o una
directory che contiene lo stato di Ares lo dice e chiede una conferma
scritta. E' il primo dei due punti in cui l'avvio puo' fermarsi prima del
banner; il secondo e' un `resume` senza niente da riprendere.

Ogni conversazione nasce nella cartella e la ricorda: il contesto -
obiettivo, piano, avanzamento - e' suo, e `ares resume` lo riapre. Il
profilo e le memorie invece sono per utente, quindi attraversano tutte le
conversazioni, anche una nuova.

Frecce su e giu' ripercorrono cio' che hai gia' scritto, anche di una
sessione precedente; le frecce laterali correggono la riga senza riscriverla.
Invio spedisce il messaggio, Alt+Invio aggiunge una nuova riga.

Comandi durante la chat: lo slash apre il menu, `/aiuto` lo descrive, il TAB
completa e bastano le iniziali finche' restano uniche. L'elenco vive in
`chat_commands.COMANDI`: era scritto in due posti e le copie erano gia'
divergite.
"""

import sys
from pathlib import Path

from agno.run.agent import RunOutput

from ares import config
from ares.agent.assistant import build_assistant
from ares.agent.echo import fotografa, istantanea, riduci, ripristina, variazioni
from ares.agent.runtime import build_db
from ares.agent.turn_core import run_turn_cycle
from ares.backup.snapshots import avviso_residui_restore, promemoria_backup
from ares.cli import cartella
from ares.cli.commands import COMANDI, StatoChat, gestisci_comando, nomi_comandi, risolvi_comando, stampa_aiuto
from ares.cli.editor import CliInput
from ares.cli.log import AGNO_LOGGER_NAMES, configura_log_agno
from ares.cli.render import (
    anteprima_risultato,
    chiedi_conferme,
    finestra_occupata,
    mostra_evento,
    mostra_flusso,
    righe_argomento,
    righe_esito,
    righe_metriche,
    righe_richiesta,
    righe_scrittura,
)
from ares.cli.ui import UI
from ares.ops import migrazione
from ares.state.lock import StatoOccupato, lock_stato
from ares.state.stores import con_run, prima_domanda, quando_sessione, sessioni_della_cartella

__all__ = (
    "AGNO_LOGGER_NAMES",
    "COMANDI",
    "StatoChat",
    "anteprima_risultato",
    "avvia",
    "chiedi_conferme",
    "configura_log_agno",
    "esegui_turno",
    "finestra_occupata",
    "gestisci_comando",
    "main",
    "mostra_evento",
    "mostra_flusso",
    "nomi_comandi",
    "righe_argomento",
    "righe_esito",
    "righe_metriche",
    "righe_richiesta",
    "righe_scrittura",
    "risolvi_comando",
    "stampa_aiuto",
)


def _turno(agent, testo: str, input_cli: CliInput) -> RunOutput | None:
    """Il turno vero, senza le difese: le pause per autorizzare uno strumento."""
    with UI.stream() as flusso:
        risposta = run_turn_cycle(
            agent,
            testo,
            on_event=lambda evento: mostra_evento(flusso, evento),
            resolve_pause=lambda output: chiedi_conferme(output, input_cli),
        )

    if risposta is not None and risposta.is_paused:
        UI.line(
            "Il turno e' in pausa per qualcosa che non so chiedere. Lo lascio li'.",
            style="ares.warning",
        )
    return risposta


def esegui_turno(agent, testo: str, input_cli: CliInput) -> RunOutput | None:
    """Un turno intero, con una rete sotto per cio' che Agno non prende.

    Questa rete cattura molto meno di quanto sembri, e vale la pena dire cosa
    resta fuori. Agno gestisce da se' sia `KeyboardInterrupt` sia le
    eccezioni dentro i propri generatori di streaming - `_run_stream` alle
    righe 1220 e 1243, `_continue_run_stream` alla 4061 - e non le rilancia:
    un Ctrl-C mentre Ollama genera diventa un evento `RunCancelled`, che il
    client stampa, e un guasto diventa un evento `RunError`, idem.
    Quando il generatore termina, la REPL torna autonomamente al prompt.

    Restano fuori i pezzi che non stanno dentro quei generatori: costruire gli
    argomenti della chiamata, risolvere le conferme, e qualunque cosa
    sollevino `confirm()` o `reject()`. Nessuno di questi e' stato visto
    fallire; la rete c'e' perche' il prezzo di un'eccezione che sfugge e' la
    sessione intera, e il prezzo della rete sono sei righe.

    Due rami separati perche' un Ctrl-C e' una decisione e un guasto e' un
    imprevisto: al primo non serve mostrare niente oltre la conferma che si e'
    fermato, al secondo serve l'errore, altrimenti sparisce.
    """
    # La fotografia precede il turno e non il post-hook: `update_user_memory`
    # scrive durante il run, e una lettura fatta dopo la risposta non lo
    # vedrebbe. Spenta in config, non si legge niente.
    stato = istantanea(agent) if config.MOSTRA_APPRENDIMENTI else None
    try:
        risposta = _turno(agent, testo, input_cli)
    except KeyboardInterrupt:
        # Il context manager del renderer ha gia' chiuso l'anteprima e reso
        # permanente l'eventuale Markdown parziale.
        UI.blank()
        UI.line("Interrotto fuori dal turno. Non e' stato appreso.", style="ares.warning")
        return None
    except Exception as errore:
        UI.blank()
        UI.line(
            "Il turno e' fallito - " + type(errore).__name__ + ": " + str(errore),
            style="ares.error",
        )
        UI.line(
            "La sessione resta aperta: quello che Ares sapeva prima e' ancora li'.",
            style="ares.muted",
        )
        return None

    if stato is not None:
        # Anche dopo una pausa lasciata li': cio' che e' stato scritto e'
        # stato scritto, e tacerlo perche' il turno non e' finito bene
        # sarebbe il caso in cui l'eco serve di piu'.
        righe = variazioni(riduci(stato), fotografa(agent))
        UI.learned(righe)
        if righe and config.CONFERMA_APPRENDIMENTI:
            _conferma_apprendimenti(agent, stato, input_cli)
    return risposta


def _conferma_apprendimenti(agent, stato, input_cli: CliInput) -> None:
    """Chiede se tenere cio' che il turno ha scritto; un no lo riporta indietro.

    Invio, Ctrl-C e fine dell'input tengono: la scrittura c'e' gia' stata e
    l'eco l'ha mostrata, quindi non fare niente lascia le cose come sono e
    come si sono viste. Solo un `n` esplicito riscrive gli store.
    """
    try:
        scelta = input_cli.ask("Tenere in memoria? [S/n] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        UI.blank()
        scelta = ""
    if scelta not in ("n", "no"):
        return
    if ripristina(agent, stato):
        UI.line("   ripristinato: profilo e memorie come prima del turno", style="ares.muted")
    else:
        UI.line(
            "   ripristino incompleto: profilo o memorie non corrispondono a prima del turno",
            style="ares.error",
        )
        UI.line("   controlla con /profilo e /memorie, o correggi con gli strumenti di memoria", style="ares.muted")


def _sessione_da_aprire(user: str, radice: Path | None, *, riprendi: bool, scegli: bool) -> tuple[str | None, str]:
    """Quale conversazione aprire quando `--session` non lo dice, e come chiamarla nel banner.

    Senza `resume` ogni avvio e' una conversazione nuova, nominata dalla
    cartella e dal momento: profilo e memorie ci sono comunque, perche' sono
    per utente; il contesto - obiettivo, piano, avanzamento - parte vuoto.
    Con `resume` si torna all'ultima nata in questa cartella, o a una scelta
    dall'elenco. `None` vuol dire che non c'e' niente da riprendere, ed e'
    gia' stato detto.
    """
    if radice is None:
        # Senza spazio di lavoro non c'e' una cartella a cui legarsi: resta
        # il nome di prima, e `resume` non ha da dove riprendere.
        if riprendi:
            UI.line("Senza cartella di lavoro non c'e' niente da riprendere: usa --session.", style="ares.error")
            return None, ""
        return "principale", ""
    if not riprendi:
        return cartella.nuovo_id_sessione(radice), "nuova"
    precedenti = sessioni_della_cartella(build_db(), user, radice)
    if not precedenti:
        UI.line("Nessuna conversazione in questa cartella: `ares` da solo ne apre una nuova.", style="ares.warning")
        return None, ""
    if scegli:
        UI.heading("Conversazioni in " + str(radice))
        scelta = cartella.scegli_sessione([con_run(build_db(), s) for s in precedenti[: config.SESSIONI_ELENCO]])
        if scelta is None:
            UI.line("Nessuna conversazione ripresa.", style="ares.muted")
        return scelta, "ripresa"
    ultima = con_run(build_db(), precedenti[0])
    scambi = len(getattr(ultima, "runs", None) or [])
    conto = str(scambi) + (" scambio" if scambi == 1 else " scambi")
    UI.pair("Riprendo", str(ultima.session_id) + "   " + quando_sessione(ultima) + "   " + conto)
    inizio = prima_domanda(ultima)
    if inizio:
        UI.line("    inizio: " + inizio, style="ares.muted")
    if len(precedenti) > 1:
        altre = "    altre " + str(len(precedenti) - 1) + " in questa cartella: ares resume --scegli"
        UI.line(altre, style="ares.muted")
    return str(ultima.session_id), "ripresa"


def _colpo_singolo(stato: StatoChat, testo: str) -> int:
    """`ares -p "..."`: un turno, la risposta, fine. Stdin in pipe si aggiunge al testo.

    Niente banner e niente avvisi d'avvio: in una pipe conta la risposta.
    L'apprendimento avviene come in chat; le conferme non hanno nessuno che
    risponda e valgono no, cosi' uno strumento sensibile non passa mai da un
    comando lanciato da uno script.
    """
    if not sys.stdin.isatty():
        try:
            dati = sys.stdin.read().strip()
        except OSError:
            dati = ""
        if dati:
            testo = testo + "\n\n" + dati
    input_cli = CliInput(
        comandi=[],
        cronologia_file=config.CRONOLOGIA_FILE,
        cronologia_righe=config.CRONOLOGIA_RIGHE,
        interactive=False,
        fallback_input=lambda _etichetta: "",
    )
    risposta = esegui_turno(stato.agent, testo, input_cli)
    if stato.metriche and risposta is not None:
        for riga in righe_metriche(risposta):
            UI.metrics(riga)
    return 0 if risposta is not None else 1


def _esegui_chat(
    *,
    session: str | None = None,
    user: str,
    debug: bool = False,
    metriche: bool = False,
    workspace: Path | None = None,
    riprendi: bool = False,
    scegli: bool = False,
    prompt: str | None = None,
) -> int:
    """La chat. Restituisce il codice di uscita: 1 se stato, cartella o sessione non si aprono, 0 altrimenti."""
    # Lo stato ancora nel posto di prima ferma tutto: aprire un archivio
    # vuoto accanto a uno pieno di mesi di memorie li sdoppierebbe, e Ares
    # risponderebbe come al primo giorno senza che si capisca perche'.
    ancora_di_la = migrazione.avviso()
    if ancora_di_la:
        UI.line(ancora_di_la[0], style="ares.warning")
        for riga in ancora_di_la[1:]:
            UI.line(riga, style="ares.muted")
        return 1

    # Poi la cartella, perche' e' l'altro passo che puo' dire no: un avvio
    # rifiutato non deve aver toccato niente, nemmeno la directory dello
    # stato. `workspace` e' `--workspace`; senza, e' quella da cui si e'
    # lanciato `ares`, e `config` la ha gia' letta.
    radice: Path | None = None
    if config.WORKSPACE:
        try:
            radice = cartella.scegli(workspace)
        except ValueError as errore:
            UI.line(str(errore), style="ares.error")
            return 1
        if not cartella.autorizza(radice, esplicito=workspace is not None):
            return 1
        config.WORKSPACE_DIR = radice

    # Poi cio' che scrive: la cronologia della REPL nasce dentro tmp/, che
    # quindi deve esistere gia' privata quando `CliInput` ci scrive. `--help`
    # non arriva qui: esce dentro Cyclopts.
    config.prepara_archivio()

    etichetta = ""
    if session is None:
        session, etichetta = _sessione_da_aprire(user, radice, riprendi=riprendi, scegli=scegli)
        if session is None:
            return 1

    configura_log_agno(debug)
    agent = build_assistant(user_id=user, session_id=session, debug=debug)

    # Il flag di config e' il default, l'opzione lo accende per una sessione
    # sola: guardare il costo dei turni e' quasi sempre una cosa che si fa
    # per un pomeriggio, non una preferenza permanente. `/metriche`,
    # `/debug` e `/sessione` cambiano questo stato a meta' conversazione.
    stato = StatoChat(
        agent=agent,
        session_id=session,
        user_id=user,
        debug=debug,
        metriche=config.MOSTRA_METRICHE or metriche,
    )

    if prompt is not None:
        return _colpo_singolo(stato, prompt)

    input_cli = CliInput(
        comandi=[(nome, descrizione) for nome, _alias, descrizione, _funzione in COMANDI],
        cronologia_file=config.CRONOLOGIA_FILE,
        cronologia_righe=config.CRONOLOGIA_RIGHE,
    )
    if input_cli.history_warning:
        UI.line(
            "Cronologia non disponibile; resta solo per questa sessione: " + input_cli.history_warning,
            style="ares.warning",
        )

    istruzioni = None
    if radice is not None and cartella.file_istruzioni(radice).is_file():
        istruzioni = config.WORKSPACE_ISTRUZIONI
    UI.banner(
        modello=config.MAIN_MODEL,
        sessione=session + ("  (" + etichetta + ")" if etichetta else ""),
        utente=user,
        cartella=str(radice) if radice is not None else None,
        ramo=cartella.ramo_git(radice) if radice is not None else None,
        istruzioni=istruzioni,
    )

    # Un modello cloud si vede dal nome, ma il nome non dice cosa comporta.
    # Ogni sessione, non solo la prima: e' la stessa logica del promemoria
    # di backup, e un avviso che riguarda dove finiscono le parole non e'
    # una preferenza da ricordare. Vale per la conversazione e per
    # l'estrazione delle memorie, che il `.env` puo' mandare in cloud
    # separatamente: le righe sono le stesse del preflight.
    avviso_cloud = config.avviso_cloud()
    if avviso_cloud:
        UI.line(" ".join(avviso_cloud), style="ares.warning")
        UI.line(
            "Ollama dichiara nessuna conservazione e nessun addestramento.",
            style="ares.muted",
        )

    # All'avvio e non all'uscita: qui l'utente c'e' e puo' decidere, mentre
    # chi scrive `/esci` ha gia' finito e legge un avviso che rimandera'.
    # L'elenco e' vuoto quasi sempre - vedi `promemoria_backup`.
    promemoria = promemoria_backup()
    if promemoria:
        UI.blank()
        UI.line(promemoria[0], style="ares.warning")
        for riga in promemoria[1:]:
            UI.line(riga, style="ares.muted")
    # Un restore interrotto lascia lo stato di prima accanto a uno stato
    # ricreato vuoto: Ares risponderebbe come al primo giorno, e senza questo
    # avviso l'utente lo scoprirebbe da una risposta che non ricorda niente.
    residui = avviso_residui_restore()
    if residui:
        UI.blank()
        UI.line(residui[0], style="ares.error")
        for riga in residui[1:]:
            UI.line(riga, style="ares.muted")
    UI.blank()

    while True:
        try:
            testo = input_cli.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            UI.blank()
            break

        if not testo:
            continue

        if testo.startswith("/"):
            if not gestisci_comando(testo, stato):
                break
            UI.blank()
            continue

        risposta = esegui_turno(stato.agent, testo, input_cli)
        if stato.metriche and risposta is not None:
            for riga in righe_metriche(risposta):
                UI.metrics(riga)
        UI.blank()

    UI.line("A presto.", style="ares.title")
    return 0


def avvia(
    *,
    session: str | None = None,
    user: str,
    debug: bool = False,
    metriche: bool = False,
    workspace: Path | None = None,
    riprendi: bool = False,
    scegli: bool = False,
    prompt: str | None = None,
) -> int:
    """La chat con la rete intorno: il lock e i tre modi in cui l'avvio non parte.

    Restituisce il codice di uscita. Una cartella rifiutata, niente da
    riprendere e un archivio occupato valgono 1, perche' uno script che
    lancia `ares` deve poterlo vedere; un Ctrl-C durante l'avvio vale 0,
    perche' l'ha deciso l'utente.
    """
    try:
        # Lock condiviso per tutta la vita del processo. Piu' chat possono
        # convivere; backup e restore, che chiedono il lock esclusivo, no.
        with lock_stato(esclusivo=False):
            esito = _esegui_chat(
                session=session,
                user=user,
                debug=debug,
                metriche=metriche,
                workspace=workspace,
                riprendi=riprendi,
                scegli=scegli,
                prompt=prompt,
            )
            return esito if isinstance(esito, int) else 0
    except StatoOccupato as errore:
        UI.line("Impossibile avviare Ares: " + str(errore), style="ares.error")
        UI.line("Attendi che backup o restore terminino e riprova.", style="ares.muted")
        return 1
    except KeyboardInterrupt:
        # Dentro la chat il Ctrl-C e' gia' gestito - dal prompt esce, da un
        # turno lo interrompe. Resta scoperta la costruzione dell'agente, che
        # apre database e indice: li' un traceback sarebbe l'unica traccia.
        UI.blank()
        UI.line("Avvio interrotto.", style="ares.warning")
        return 0


def main() -> None:
    from ares.cli.app import main as radice

    radice()


if __name__ == "__main__":
    main()
