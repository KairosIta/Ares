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
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

from agno.run.agent import RunOutput

from ares import config
from ares.agent.echo import fotografa, istantanea, riduci, ripristina, variazioni
from ares.agent.prompts import percorso_istruzioni
from ares.agent.turn_core import run_turn_cycle
from ares.backup.snapshots import avviso_residui_restore, promemoria_backup
from ares.cli import cartella
from ares.cli.comando import ESITO_FATTO, ESITO_GUASTO, ESITO_OCCUPATO, ESITO_RIFIUTO
from ares.cli.commands import COMANDI, StatoChat, candidati_argomento, gestisci_comando
from ares.cli.editor import CliInput
from ares.cli.log import configura_log_agno
from ares.cli.render import chiedi_conferme, finestra_occupata, mostra_evento, quota_finestra, righe_metriche
from ares.cli.ui import UI
from ares.config import Impostazioni, Percorsi, Politica
from ares.core.session import SessioneDiAltri, Sessioni
from ares.ops import migrazione
from ares.state.git import ramo_git
from ares.state.identita import Utente, UtenteNonValido
from ares.state.lock import StatoOccupato, lock_stato, lock_turno
from ares.state.stores import prima_domanda, quando_sessione


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


def _turno(percorsi: Percorsi, agent, testo: str, input_cli: CliInput, politica: Politica) -> RunOutput | None:
    """Il turno vero, senza le difese: le pause per autorizzare uno strumento."""
    with UI.stream() as flusso:
        risposta = run_turn_cycle(
            agent,
            testo,
            on_event=lambda evento: mostra_evento(flusso, evento, politica.mostra),
            resolve_pause=lambda output: chiedi_conferme(output, input_cli, percorsi, politica),
        )

    if risposta is not None and risposta.is_paused:
        UI.line(
            "Il turno e' in pausa per qualcosa che non so chiedere. Lo lascio li'.",
            style="ares.warning",
        )
    return risposta


def esegui_turno(percorsi: Percorsi, agent, testo: str, input_cli: CliInput, politica: Politica) -> RunOutput | None:
    """Serializza il turno e la conferma con le altre chat dello stesso utente."""
    identita = Utente.da_grezzo(getattr(agent, "user_id", None) or config.DEFAULT_USER_ID)
    with lock_turno(percorsi, identita):
        return _esegui_turno_protetto(percorsi, agent, testo, input_cli, politica)


def _esegui_turno_protetto(
    percorsi: Percorsi, agent, testo: str, input_cli: CliInput, politica: Politica
) -> RunOutput | None:
    """Un turno intero, con una rete sotto per cio' che Agno non prende.

    Agno gestisce da se' Ctrl-C e guasti dentro i propri generatori di
    streaming, trasformandoli negli eventi `RunCancelled` e `RunError`. Questa
    rete copre il resto: costruire la chiamata, risolvere le conferme, e cio'
    che sollevano `confirm()` o `reject()`. Anche dopo un errore le memorie
    gia' scritte passano da eco e conferma, sotto il lock dell'utente.

    Ctrl-C e guasto hanno rami separati: il primo e' una decisione e basta
    confermarlo, il secondo va mostrato.
    """
    # La fotografia precede il turno, non il post-hook: `update_user_memory`
    # scrive durante il run.
    stato = istantanea(agent) if politica.mostra.apprendimenti else None
    risposta = None
    try:
        risposta = _turno(percorsi, agent, testo, input_cli, politica)
    except KeyboardInterrupt:
        # Il context manager del renderer ha gia' chiuso l'anteprima e reso
        # permanente l'eventuale Markdown parziale.
        UI.blank()
        UI.line("Interrotto fuori dal turno.", style="ares.warning")
    except Exception as errore:
        UI.blank()
        UI.line(
            "Il turno e' fallito - " + type(errore).__name__ + ": " + str(errore),
            style="ares.error",
        )
        UI.line(
            "La sessione resta aperta.",
            style="ares.muted",
        )

    if stato is not None:
        # Anche dopo una pausa: cio' che e' stato scritto va mostrato comunque.
        righe = variazioni(riduci(stato), fotografa(agent))
        UI.learned(righe)
        if righe and politica.mostra.conferma_apprendimenti:
            _conferma_apprendimenti(agent, stato, input_cli)
    return risposta


def _conferma_apprendimenti(agent, stato, input_cli: CliInput) -> None:
    """Chiede se tenere cio' che il turno ha scritto; un no lo riporta indietro.

    Invio, Ctrl-C e fine dell'input tengono: la scrittura e' gia' avvenuta ed
    e' stata mostrata. Solo un `n` esplicito riscrive gli store.
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
    precedenti = sessioni.della_cartella()
    if not precedenti:
        UI.line("Nessuna conversazione in questa cartella: `ares` da solo ne apre una nuova.", style="ares.warning")
        return None, ""
    if scegli:
        UI.heading("Conversazioni in " + str(sessioni.percorsi.lavoro))
        scelta = cartella.scegli_sessione(sessioni.con_scambi(precedenti[: sessioni.politica.mostra.sessioni]))
        if scelta is None:
            UI.line("Nessuna conversazione ripresa.", style="ares.muted")
        return scelta, "ripresa"
    ultima = sessioni.con_scambi(precedenti[:1])[0]
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

    Niente banner ne' avvisi. Le conferme valgono no, cosi' uno strumento
    sensibile non passa mai da uno script, e niente entra in memoria perche'
    nessuno puo' leggere l'eco: l'agente nasce con `interattivo=False`.
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
    risposta = esegui_turno(stato.percorsi, stato.agent, testo, input_cli, stato.politica)
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
    with UI.solo_risposte() if prompt is not None else nullcontext():
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
        )


def _guardie_di_avvio(
    *, prompt: str | None, scegli: bool, modo: str, percorsi: Percorsi, presidiato: bool
) -> int | None:
    """I rifiuti che vengono prima di ogni effetto.

    Restituisce l'esito se la chat non puo' partire, `None` se puo'. Sta prima
    di ogni scrittura: un avvio rifiutato non lascia niente dietro di se'.

    Senza nessuno che guardi (`-p` o stdin da una pipe) sono ammesse solo
    modalita' in cui niente lascia traccia senza conferma: un testo ostile
    nella stessa pipe non deve poter eseguire comandi ne' scrivere file. La
    regola legge la tabella delle modalita', non i nomi.
    """
    if (prompt is not None or not presidiato) and config.modalita_scrive_in_silenzio(modo):
        UI.line(
            "La modalita' " + modo + " richiede un terminale: scriverebbe o eseguirebbe senza che nessuno guardi.",
            style="ares.error",
        )
        return ESITO_RIFIUTO
    if prompt is not None and scegli:
        # `--scegli` non autorizza niente, quindi e' ammesso anche da una pipe;
        # con `-p` no, perche' stdin e' la domanda.
        UI.line("--scegli non si combina con -p: nessuno sceglierebbe. Usa --session <nome>.", style="ares.error")
        return ESITO_RIFIUTO
    # Lo stato ancora nel posto di prima ferma tutto: aprirne uno vuoto accanto
    # sdoppierebbe l'archivio.
    ancora_di_la = migrazione.avviso(percorsi)
    if ancora_di_la:
        UI.line(ancora_di_la[0], style="ares.warning")
        for riga in ancora_di_la[1:]:
            UI.line(riga, style="ares.muted")
        return ESITO_GUASTO
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
        UI.line("Modalita' auto: nessuna conferma, ogni strumento gira subito.", style="ares.warning")

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
            risposta = esegui_turno(stato.percorsi, stato.agent, testo, input_cli, stato.politica)
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
) -> int:
    # Si guarda stdin, non stdout: `ares > file` con la tastiera davanti resta
    # presidiato.
    presidiato = sys.stdin is not None and sys.stdin.isatty()
    rifiuto = _guardie_di_avvio(prompt=prompt, scegli=scegli, modo=modo, percorsi=percorsi, presidiato=presidiato)
    if rifiuto is not None:
        return rifiuto

    # Poi la cartella, l'altro passo che puo' dire no prima di toccare lo stato.
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

    # Poi cio' che scrive: la cronologia della REPL vive nello stato, che deve
    # gia' esistere privato.
    config.prepara_archivio(percorsi)

    # Senza terminale nessuno legge l'eco: apprendimento spento come in `-p`.
    # `interattivo` passa anche alle ricostruzioni tramite `StatoChat`.
    interattivo = prompt is None and presidiato
    sessioni = Sessioni(percorsi, impostazioni, politica, utente, debug=debug, interattivo=interattivo)

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
        interattivo=interattivo,
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
    """La chat con la rete intorno: il lock e i tre modi in cui l'avvio non parte.

    Codici di `cli/comando.py`: archivio occupato 3, cartella rifiutata o
    niente da riprendere 2, Ctrl-C durante l'avvio 0 (l'ha deciso l'utente).
    """
    # Il lock condiviso vale per tutta la vita del processo, sullo stato
    # (`--workspace` cambia `lavoro`, non `stato`).
    percorsi = config.leggi_percorsi()
    try:
        # Piu' chat convivono; backup e restore, che chiedono il lock esclusivo, no.
        with lock_stato(percorsi.lock_file, esclusivo=False):
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
