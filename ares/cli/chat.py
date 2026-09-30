"""REPL interattivo
================
Uso:
    ares                       nella cartella corrente, conversazione nuova
    ares resume                riprende l'ultima conversazione di questa cartella
    ares resume --scegli       la sceglie da un elenco
    ares -p "domanda"          una risposta e basta; stdin in pipe si aggiunge
    ares resume -p "domanda"   la stessa cosa, sull'ultima conversazione di qui
    ares --workspace ~/prog    su un'altra cartella
    ares --session progetto-x  una sessione con un nome fisso
    ares --debug               mostra le chiamate al modello
    ares --metriche            costo di ogni turno

Le opzioni le dichiara `cli/app.py`; qui c'e' il corpo della chat, importato
solo quando la chat parte.

La cartella di lancio e' quella di lavoro, vagliata da `cli/cartella.py`.
Il contesto di sessione (obiettivo, piano, avanzamento) e' per conversazione
e `ares resume` lo riapre; profilo e memorie sono per utente.

Tasti: frecce su/giu' per la cronologia, Invio spedisce, Alt+Invio va a
capo, Ctrl-C svuota la riga, Ctrl-D chiude come `/esci`. Lo slash apre il
menu dei comandi, definiti in `COMANDI` di `cli/commands.py`.
"""

import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path

from agno.run.agent import RunOutput

from ares import config
from ares.agent.prompts import percorso_istruzioni
from ares.agent.turn_core import TurnEvent
from ares.backup.snapshots import avviso_residui_restore, promemoria_backup
from ares.cli import cartella
from ares.cli.comando import ESITO_FATTO, ESITO_GUASTO, ESITO_OCCUPATO, ESITO_RIFIUTO
from ares.cli.commands import COMANDI, StatoChat, candidati_argomento, gestisci_comando, modo_senza_terminale
from ares.cli.conversazioni import conto_scambi
from ares.cli.editor import CliInput
from ares.cli.log import configura_log_agno
from ares.cli.render import (
    chiedi_autorizzazione,
    finestra_occupata,
    mostra_evento,
    quota_finestra,
    righe_metriche,
    righe_richiesta,
)
from ares.cli.ui import UI
from ares.config import Impostazioni, Percorsi, Politica
from ares.core import turn
from ares.core.autorizzazioni import Decisione, ModoNonAmmesso, Richiesta, verifica_modo
from ares.core.session import SessioneDiAltri, Sessioni
from ares.core.stato import StatoDaMigrare, stato_in_uso
from ares.ops import migrazione
from ares.state.git import ramo_git
from ares.state.identita import Utente, UtenteNonValido
from ares.state.lock import StatoOccupato
from ares.state.sessioni import quando, tronca


def riga_stato(stato: StatoChat) -> str:
    """Cio' che la barra sotto il prompt dice: modalita', sessione e finestra occupata.

    La finestra e' la quota di contesto usata dall'ultimo turno; manca finche'
    non c'e' stato un turno.
    """
    pezzi = [stato.modo, stato.session_id]
    quota = quota_finestra(stato.finestra or 0, stato.impostazioni)
    if quota:
        pezzi.append("finestra " + quota)
    return " · ".join(pezzi)


class ClienteCli:
    """Il turno nel terminale: stream Rich, conferme a riga di comando e l'eco della memoria."""

    def __init__(self, politica: Politica, input_cli: CliInput, *, presidiato: bool) -> None:
        self.politica = politica
        self.input_cli = input_cli
        self.presidiato = presidiato

    @contextmanager
    def flusso(self) -> Iterator[Callable[[TurnEvent], None]]:
        # Su Ctrl-C il renderer chiude l'anteprima e rende permanente il
        # Markdown parziale prima che l'eccezione arrivi al nucleo.
        with UI.stream() as flusso:
            yield lambda evento: mostra_evento(flusso, evento, self.politica.mostra)

    def autorizza(self, richiesta: Richiesta) -> Decisione:
        return chiedi_autorizzazione(richiesta, self.input_cli)

    def negata(self, richiesta: Richiesta) -> None:
        # La richiesta resta nel log di una pipe anche se nessuno poteva rispondere.
        UI.confirmation(righe_richiesta(richiesta))

    def pausa_irrisolta(self) -> None:
        UI.line("Il turno e' in pausa per qualcosa che non so chiedere. Lo lascio li'.", style="ares.warning")

    def interrotto(self) -> None:
        UI.blank()
        UI.line("Interrotto fuori dal turno.", style="ares.warning")

    def guasto(self, errore: Exception) -> None:
        UI.blank()
        UI.line("Il turno e' fallito - " + type(errore).__name__ + ": " + str(errore), style="ares.error")
        UI.line("La sessione resta aperta.", style="ares.muted")

    def apprendimenti(self, righe: list[str], *, chiedi: bool) -> bool:
        """Mostra l'eco; con `chiedi`, solo un `n` esplicito rifiuta.

        Invio, Ctrl-C e fine dell'input tengono: la scrittura e' gia'
        avvenuta ed e' stata mostrata.
        """
        UI.learned(righe)
        if not chiedi:
            return True
        try:
            scelta = self.input_cli.ask("Tenere in memoria? [S/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            UI.blank()
            scelta = ""
        return scelta not in ("n", "no")


def esegui_turno(
    percorsi: Percorsi, agent, testo: str, input_cli: CliInput, politica: Politica, *, presidiato: bool
) -> RunOutput | None:
    """Un turno nel terminale, con le garanzie del nucleo (`core/turn.py`)."""
    cliente = ClienteCli(politica, input_cli, presidiato=presidiato)
    esito = turn.esegui_turno(percorsi, politica, agent, testo, cliente)
    if esito.ripristino is True:
        UI.line("   ripristinato: profilo e memorie come prima del turno", style="ares.muted")
    elif esito.ripristino is False:
        UI.line(
            "   ripristino incompleto: profilo o memorie non corrispondono a prima del turno",
            style="ares.error",
        )
        UI.line("   controlla con /profilo e /memorie, o correggi con gli strumenti di memoria", style="ares.muted")
    return esito.risposta


def _sessione_da_aprire(sessioni: Sessioni, *, riprendi: bool, scegli: bool) -> tuple[str | None, str]:
    """Quale conversazione aprire quando `--session` non lo dice, e come chiamarla nel banner.

    Senza `resume` e' una conversazione nuova. Con `resume` e' l'ultima di
    questa cartella, o una scelta dall'elenco. `None`: niente da riprendere,
    e l'utente e' gia' stato avvisato.
    """
    if not sessioni.con_cartella:
        if riprendi:
            UI.line("Senza cartella di lavoro non c'e' niente da riprendere: usa --session.", style="ares.error")
            return None, ""
        return sessioni.id_nuovo(), ""
    if not riprendi:
        return sessioni.id_nuovo(), "nuova"
    precedenti = sessioni.elenco(ambito="nate_qui", limite=None if scegli else 1)
    if not precedenti.totale:
        UI.line("Nessuna conversazione in questa cartella: `ares` da solo ne apre una nuova.", style="ares.warning")
        return None, ""
    if scegli:
        UI.heading("Conversazioni in " + str(sessioni.percorsi.lavoro))
        scelta = cartella.scegli_sessione(precedenti.voci)
        if scelta is None:
            UI.line("Nessuna conversazione ripresa.", style="ares.muted")
        return scelta, "ripresa"
    ultima = precedenti.voci[0]
    UI.pair("Riprendo", ultima.id + "   " + quando(ultima) + "   " + conto_scambi(ultima.scambi))
    if ultima.inizio:
        UI.line("    inizio: " + tronca(ultima.inizio, 90), style="ares.muted")
    if precedenti.totale > 1:
        altre = "    altre " + str(precedenti.totale - 1) + " in questa cartella: ares resume --scegli"
        UI.line(altre, style="ares.muted")
    return ultima.id, "ripresa"


def _colpo_singolo(stato: StatoChat, testo: str) -> int:
    """`ares -p "..."`: un turno, la risposta, fine. Stdin in pipe si aggiunge al testo.

    Niente banner ne' avvisi. Le conferme valgono no, cosi' uno strumento
    sensibile non passa mai da uno script, e niente entra in memoria perche'
    nessuno puo' leggere l'eco: la chat si apre senza presenza.
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
        cronologia_file=stato.percorsi.cronologia_file,
        cronologia_righe=config.CRONOLOGIA_RIGHE,
        interactive=False,
        fallback_input=lambda _etichetta: "",
    )
    risposta = esegui_turno(stato.percorsi, stato.agent, testo, input_cli, stato.politica, presidiato=stato.presidiato)
    if stato.metriche and risposta is not None:
        for riga in righe_metriche(risposta, stato.impostazioni):
            UI.metrics(riga)
    return ESITO_FATTO if risposta is not None else ESITO_GUASTO


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
    modo: str = config.MODO_PREDEFINITO,
) -> int:
    """La chat. Restituisce il codice di uscita secondo la tabella di `cli/comando.py`.

    1 se lo stato non e' pronto o la cartella non esiste; 2 se la cartella e'
    rifiutata, l'utente non e' valido, non c'e' niente da riprendere o `-p`
    chiede una modalita' che agisce senza conferma (`auto`, `modifiche`).

    Con `-p` su stdout esce solo la risposta; tutto il resto va su stderr.
    """
    # L'identita' si risolve una volta sola: sessione, profilo e lock devono
    # parlare dello stesso utente (`Demo` e `demo` sono la stessa persona).
    try:
        utente = Utente.da_grezzo(user)
    except UtenteNonValido as errore:
        UI.line("Utente non valido: " + str(errore) + ".", style="ares.error")
        return ESITO_RIFIUTO
    # I percorsi si leggono al confine del comando e poi viaggiano per parametro.
    percorsi = config.leggi_percorsi()
    impostazioni = config.leggi_impostazioni()
    politica = config.leggi_politica()
    # Si guarda stdin, non stdout: `ares > file` con la tastiera davanti resta
    # presidiato. Con `-p` no: stdin e' la domanda, e nessuno legge l'eco.
    presidiato = prompt is None and sys.stdin is not None and sys.stdin.isatty()
    with UI.solo_risposte() if prompt is not None else nullcontext():
        rifiuto = _guardie_di_avvio(prompt=prompt, scegli=scegli, modo=modo, presidiato=presidiato)
        if rifiuto is not None:
            return rifiuto
        try:
            # Il lock condiviso vale per tutta la chat, sullo stato (`--workspace`
            # cambia `lavoro`, non `stato`). `StatoOccupato` lo gestisce `avvia`.
            with stato_in_uso(percorsi):
                return _apri_chat(
                    percorsi=percorsi,
                    impostazioni=impostazioni,
                    politica=politica,
                    session=session,
                    utente=utente,
                    debug=debug,
                    metriche=metriche,
                    workspace=workspace,
                    riprendi=riprendi,
                    scegli=scegli,
                    prompt=prompt,
                    modo=modo,
                    presidiato=presidiato,
                )
        except StatoDaMigrare as errore:
            righe = migrazione.righe_avviso(errore.parti)
            UI.line(righe[0], style="ares.warning")
            for riga in righe[1:]:
                UI.line(riga, style="ares.muted")
            return ESITO_GUASTO


def _guardie_di_avvio(*, prompt: str | None, scegli: bool, modo: str, presidiato: bool) -> int | None:
    """I rifiuti che dipendono solo dagli argomenti, prima di toccare lo stato.

    Restituisce l'esito se la chat non puo' partire, `None` se puo'. Le
    modalita' ammesse senza nessuno che guardi (`-p` o stdin da una pipe) le
    decide `verifica_modo`, la stessa regola di `/modo`.
    """
    try:
        verifica_modo(modo, presidiato=presidiato)
    except ModoNonAmmesso:
        UI.line(modo_senza_terminale(modo), style="ares.error")
        return ESITO_RIFIUTO
    if prompt is not None and scegli:
        # `--scegli` non autorizza niente, quindi e' ammesso anche da una pipe;
        # con `-p` no, perche' stdin e' la domanda.
        UI.line("--scegli non si combina con -p: nessuno sceglierebbe. Usa --session <nome>.", style="ares.error")
        return ESITO_RIFIUTO
    return None


def _nessuno(_etichetta: str) -> str:
    """Nessuno a rispondere: una domanda senza terminale vale no."""
    return ""


def _apri_input(stato: StatoChat, *, presidiato: bool) -> CliInput:
    """La riga interattiva: comandi, cronologia, argomenti e riga di stato."""
    input_cli = CliInput(
        comandi=[(voce.nome, voce.descrizione) for voce in COMANDI],
        cronologia_file=stato.percorsi.cronologia_file,
        cronologia_righe=config.CRONOLOGIA_RIGHE,
        argomenti=candidati_argomento(stato),
        stato=lambda: riga_stato(stato),
        # Senza terminale i turni restano leggibili dal fallback, ma le domande
        # valgono no: la stessa pipe non autorizza cio' che chiede.
        fallback_ask=None if presidiato else _nessuno,
    )
    if input_cli.history_warning:
        UI.line(
            "Cronologia non disponibile; resta solo per questa sessione: " + input_cli.history_warning,
            style="ares.warning",
        )
    return input_cli


def _accoglienza(stato: StatoChat, *, session: str, etichetta: str, radice: Path | None) -> None:
    """Banner e avvisi di apertura, tutti prima del primo turno.

    All'avvio e non all'uscita, quando l'utente puo' ancora decidere: cosa
    comporta un modello cloud, un restore interrotto che ha lasciato lo stato
    vecchio accanto a uno vuoto.
    """
    politica = stato.politica
    istruzioni = None
    if percorso_istruzioni(radice, politica.workspace.istruzioni) is not None:
        istruzioni = politica.workspace.istruzioni
    UI.banner(
        modello=stato.impostazioni.principale,
        sessione=session + ("  (" + etichetta + ")" if etichetta else ""),
        utente=stato.utente.id,
        cartella=str(radice) if radice is not None else None,
        ramo=ramo_git(radice) if radice is not None else None,
        istruzioni=istruzioni,
        modo=stato.modo if radice is not None else None,
    )
    if stato.modo == "auto":
        UI.line(
            "Modalita' auto: nessuna conferma, ogni strumento gira subito. I comandi non restano nella cartella.",
            style="ares.warning",
        )

    # Le stesse righe del preflight.
    avviso_cloud = stato.impostazioni.avviso_cloud()
    if avviso_cloud:
        UI.line(" ".join(avviso_cloud), style="ares.warning")
        UI.line(
            "Ollama dichiara nessuna conservazione e nessun addestramento.",
            style="ares.muted",
        )

    # L'elenco e' vuoto quasi sempre - vedi `promemoria_backup`.
    promemoria = promemoria_backup(stato.percorsi)
    if promemoria:
        UI.blank()
        UI.line(promemoria[0], style="ares.warning")
        for riga in promemoria[1:]:
            UI.line(riga, style="ares.muted")
    residui = avviso_residui_restore(stato.percorsi)
    if residui:
        UI.blank()
        UI.line(residui[0], style="ares.error")
        for riga in residui[1:]:
            UI.line(riga, style="ares.muted")
    UI.blank()


def _ciclo(input_cli: CliInput, stato: StatoChat) -> None:
    """I turni, dalla riga letta all'uscita.

    Un turno occupato non e' un guasto: la chat e' aperta altrove e questa
    aspetta. `/esci` e la fine dell'input escono di qui; il saluto e' del
    chiamante.
    """
    while True:
        try:
            testo = input_cli.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            UI.blank()
            return

        if not testo:
            continue

        if testo.startswith("/"):
            if not gestisci_comando(testo, stato):
                return
            UI.blank()
            continue

        try:
            risposta = esegui_turno(
                stato.percorsi, stato.agent, testo, input_cli, stato.politica, presidiato=stato.presidiato
            )
        except StatoOccupato as errore:
            UI.line(str(errore), style="ares.warning")
            UI.blank()
            continue
        if risposta is not None:
            stato.finestra = finestra_occupata(risposta) or stato.finestra
            if stato.metriche:
                for riga in righe_metriche(risposta, stato.impostazioni):
                    UI.metrics(riga)
        UI.blank()


def _apri_chat(
    *,
    percorsi: Percorsi,
    impostazioni: Impostazioni,
    politica: Politica,
    session: str | None,
    utente: Utente,
    debug: bool,
    metriche: bool,
    workspace: Path | None,
    riprendi: bool,
    scegli: bool,
    prompt: str | None,
    modo: str,
    presidiato: bool,
) -> int:
    """La chat con lo stato gia' in uso: cartella, sessione, agente e turni.

    La cartella e' l'ultimo passo che puo' dire no prima di scrivere: un avvio
    rifiutato non lascia niente dietro di se'.
    """
    radice: Path | None = None
    if politica.workspace.attivo:
        try:
            radice = cartella.scegli(workspace, percorsi)
        except ValueError as errore:
            UI.line(str(errore), style="ares.error")
            return ESITO_GUASTO
        if not cartella.autorizza(radice, percorsi, esplicito=workspace is not None):
            return ESITO_RIFIUTO
        # Il `replace` e' locale: chi ha bisogno della cartella la riceve per parametro.
        percorsi = replace(percorsi, lavoro=radice)

    # Il servizio prepara la directory dello stato, dove vive anche la
    # cronologia della REPL. La presenza passa anche alle ricostruzioni
    # tramite `StatoChat`.
    sessioni = Sessioni(percorsi, impostazioni, politica, utente, presidiato=presidiato, debug=debug)

    etichetta = ""
    if session is None:
        session, etichetta = _sessione_da_aprire(sessioni, riprendi=riprendi, scegli=scegli)
        if session is None:
            return ESITO_RIFIUTO

    configura_log_agno(debug)
    try:
        agent = sessioni.apri(session, modo=modo).agente
    except SessioneDiAltri:
        # Il nome esplicito scavalca gli elenchi per cartella: una sessione di
        # un altro utente non si apre.
        UI.line(
            "La sessione '" + session + "' appartiene a un altro utente: non si apre.",
            style="ares.error",
        )
        return ESITO_RIFIUTO

    # Il flag di config e' il default; l'opzione lo accende per questa sessione.
    stato = StatoChat(
        agent=agent,
        session_id=session,
        utente=utente,
        percorsi=percorsi,
        impostazioni=impostazioni,
        politica=politica,
        debug=debug,
        metriche=politica.mostra.metriche or metriche,
        modo=modo,
        presidiato=presidiato,
    )

    if prompt is not None:
        return _colpo_singolo(stato, prompt)

    input_cli = _apri_input(stato, presidiato=presidiato)
    _accoglienza(stato, session=session, etichetta=etichetta, radice=radice)
    _ciclo(input_cli, stato)
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
    modo: str = config.MODO_PREDEFINITO,
) -> int:
    """La chat con la rete intorno: lo stato occupato e il Ctrl-C durante l'avvio.

    Codici di `cli/comando.py`: archivio occupato 3, cartella rifiutata o
    niente da riprendere 2, Ctrl-C durante l'avvio 0 (l'ha deciso l'utente).
    """
    try:
        esito = _esegui_chat(
            session=session,
            user=user,
            debug=debug,
            metriche=metriche,
            workspace=workspace,
            riprendi=riprendi,
            scegli=scegli,
            prompt=prompt,
            modo=modo,
        )
        return esito if isinstance(esito, int) else 0
    except StatoOccupato as errore:
        UI.err("Impossibile avviare Ares: " + str(errore))
        UI.err("Attendi che l'operazione in corso termini e riprova.", style="ares.muted")
        return ESITO_OCCUPATO
    except KeyboardInterrupt:
        # Un Ctrl-C durante la costruzione dell'agente, l'unico non gestito altrove.
        UI.blank()
        UI.line("Avvio interrotto.", style="ares.warning")
        return 0


def main() -> None:
    from ares.cli.app import main as radice

    radice()


if __name__ == "__main__":
    main()
