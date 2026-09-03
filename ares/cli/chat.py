"""
REPL interattivo
================
Uso:
    .venv/bin/ares                       sessione predefinita
    .venv/bin/ares --session progetto-x  sessione separata
    .venv/bin/ares --debug               mostra le chiamate al modello
    .venv/bin/ares --metriche            costo di ogni turno

Ogni sessione ha il proprio contesto: obiettivo, piano, avanzamento. Il
profilo e le memorie invece sono per utente, quindi attraversano tutte le
sessioni. Usa sessioni diverse per lavori diversi.

Frecce su e giu' ripercorrono cio' che hai gia' scritto, anche di una
sessione precedente; le frecce laterali correggono la riga senza riscriverla.
Invio spedisce il messaggio, Alt+Invio aggiunge una nuova riga.

Comandi durante la chat: lo slash apre il menu, `/aiuto` lo descrive, il TAB
completa e bastano le iniziali finche' restano uniche. L'elenco vive in
`chat_commands.COMANDI`: era scritto in due posti e le copie erano gia'
divergite.
"""

import argparse
import logging

from agno.run.agent import RunOutput

from ares import config
from ares.agent.assistant import build_assistant
from ares.agent.echo import fotografa, variazioni
from ares.agent.turn_core import run_turn_cycle
from ares.backup.snapshots import promemoria_backup
from ares.cli.commands import COMANDI, gestisci_comando, nomi_comandi, risolvi_comando, stampa_aiuto
from ares.cli.editor import CliInput
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
from ares.state.lock import StatoOccupato, lock_stato

AGNO_LOGGER_NAMES = ("agno", "agno-team", "agno-workflow")


def configura_log_agno(debug: bool) -> None:
    """Nasconde il rumore INFO di Agno, salvo quando si chiede il debug.

    Agno riporta il livello del proprio logger a INFO all'inizio di ogni run.
    La soglia sugli handler non viene invece riscritta e continua quindi a
    filtrare messaggi interni come ``Found 0 documents`` per tutta la REPL.
    Warning ed errori restano sempre visibili.
    """
    livello = logging.DEBUG if debug else logging.WARNING
    for nome in AGNO_LOGGER_NAMES:
        for handler in logging.getLogger(nome).handlers:
            handler.setLevel(livello)


__all__ = (
    "AGNO_LOGGER_NAMES",
    "COMANDI",
    "anteprima_risultato",
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
    prima = fotografa(agent) if config.MOSTRA_APPRENDIMENTI else None
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

    if prima is not None:
        # Anche dopo una pausa lasciata li': cio' che e' stato scritto e'
        # stato scritto, e tacerlo perche' il turno non e' finito bene
        # sarebbe il caso in cui l'eco serve di piu'.
        UI.learned(variazioni(prima, fotografa(agent)))
    return risposta


def _esegui_chat() -> None:
    parser = argparse.ArgumentParser(prog="ares", description="Assistente personale locale")
    parser.add_argument("--session", default="principale", help="Identificativo della sessione")
    parser.add_argument("--user", default=config.DEFAULT_USER_ID, help="Identificativo dell'utente")
    parser.add_argument("--debug", action="store_true", help="Mostra le chiamate al modello")
    parser.add_argument(
        "--metriche",
        action="store_true",
        help="Mostra il costo di ogni turno: finestra occupata, token, secondi",
    )
    args = parser.parse_args()

    # Dopo `parse_args` e non prima: `--help` esce qui, e un comando che
    # stampa l'aiuto non deve lasciarsi dietro un archivio. La cronologia
    # della REPL nasce dentro tmp/, che quindi deve esistere gia' privata
    # quando `CliInput` ci scrive.
    config.prepara_archivio()

    configura_log_agno(args.debug)
    agent = build_assistant(user_id=args.user, session_id=args.session, debug=args.debug)

    # Il flag di config e' il default, l'opzione lo accende per una sessione
    # sola: guardare il costo dei turni e' quasi sempre una cosa che si fa
    # per un pomeriggio, non una preferenza permanente.
    mostra_metriche = config.MOSTRA_METRICHE or args.metriche

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

    UI.banner(modello=config.MAIN_MODEL, sessione=args.session, utente=args.user)

    # Un modello cloud si vede dal nome, ma il nome non dice cosa comporta.
    # Ogni sessione, non solo la prima: e' la stessa logica del promemoria
    # di backup, e un avviso che riguarda dove finiscono le parole non e'
    # una preferenza da ricordare.
    if config.e_modello_cloud(config.MAIN_MODEL):
        UI.line(
            "Modello cloud: i messaggi di questa sessione escono dalla macchina verso ollama.com.",
            style="ares.warning",
        )
        UI.line(
            "Ollama dichiara nessuna conservazione e nessun addestramento; memorie ed embedding restano locali.",
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
            if not gestisci_comando(testo, agent, args.session, args.user):
                break
            UI.blank()
            continue

        risposta = esegui_turno(agent, testo, input_cli)
        if mostra_metriche and risposta is not None:
            for riga in righe_metriche(risposta):
                UI.metrics(riga)
        UI.blank()

    UI.line("A presto.", style="ares.title")


def main() -> None:
    try:
        # Lock condiviso per tutta la vita del processo. Piu' chat possono
        # convivere; backup e restore, che chiedono il lock esclusivo, no.
        with lock_stato(esclusivo=False):
            _esegui_chat()
    except StatoOccupato as errore:
        UI.line("Impossibile avviare Ares: " + str(errore), style="ares.error")
        UI.line("Attendi che backup o restore terminino e riprova.", style="ares.muted")
    except KeyboardInterrupt:
        # Dentro la chat il Ctrl-C e' gia' gestito - dal prompt esce, da un
        # turno lo interrompe. Resta scoperta la costruzione dell'agente, che
        # apre database e indice: li' un traceback sarebbe l'unica traccia.
        UI.blank()
        UI.line("Avvio interrotto.", style="ares.warning")


if __name__ == "__main__":
    main()
