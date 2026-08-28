"""
REPL interattivo
================
Uso:
    .venv/bin/python chat.py                       sessione predefinita
    .venv/bin/python chat.py --session progetto-x  sessione separata
    .venv/bin/python chat.py --debug               mostra le chiamate al modello
    .venv/bin/python chat.py --metriche            costo di ogni turno

Ogni sessione ha il proprio contesto: obiettivo, piano, avanzamento. Il
profilo e le memorie invece sono per utente, quindi attraversano tutte le
sessioni. Usa sessioni diverse per lavori diversi.

Frecce su e giu' ripercorrono cio' che hai gia' scritto, anche di una
sessione precedente; le frecce laterali correggono la riga senza riscriverla.
Invio spedisce il messaggio, Alt+Invio aggiunge una nuova riga.

Comandi durante la chat: lo slash apre il menu, `/aiuto` lo descrive, il TAB
completa e bastano le iniziali finche' restano uniche. L'elenco vive in
`COMANDI`, qui sotto: era scritto anche qui e le due copie erano gia'
divergite.
"""

import argparse
import difflib
import logging
import shlex
from typing import Optional

from agno.run.agent import RunOutput

import config
from assistant import build_assistant, build_filesystem
from cli_input import CliInput
from cli_ui import UI
from state_lock import StatoOccupato, lock_stato
from stores import leggi_entita, leggi_sessioni, righe_entita, righe_sessione, stampa_store
from turn_core import TurnEvent, TurnEventKind, consume_events, run_turn_cycle


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


# ---------------------------------------------------------------------------
# I comandi
# ---------------------------------------------------------------------------
#
# Ogni comando e' una funzione con la stessa firma, anche quando non usa tutti
# gli argomenti: e' il prezzo di avere una tabella invece di una catena di if,
# e la tabella e' cio' che tiene allineati aiuto, completamento e abbreviazioni.
# `argomento` e' tutto cio' che segue il primo spazio. Lo leggono `/entita` e
# `/sessioni`; per gli altri resta vuoto, e un argomento passato a un comando
# che non lo usa viene ignorato in silenzio.
#
# Chi restituisce False chiude la sessione. Gli altri non restituiscono niente.


def _comando_aiuto(agent, session_id, user_id, argomento):
    stampa_aiuto()


def _comando_profilo(agent, session_id, user_id, argomento):
    stampa_store(agent.learning_machine.user_profile_store, "Profilo", user_id=user_id)


def _comando_memorie(agent, session_id, user_id, argomento):
    stampa_store(agent.learning_machine.user_memory_store, "Memorie", user_id=user_id)


def _comando_contesto(agent, session_id, user_id, argomento):
    stampa_store(agent.learning_machine.session_context_store, "Contesto", session_id=session_id)


def _comando_sessioni(agent, session_id, user_id, argomento):
    UI.heading("Sessioni")
    sessioni = leggi_sessioni(agent, user_id=user_id, query=argomento)
    mostrate = sessioni[: config.SESSIONI_ELENCO]
    for s in mostrate:
        for riga in righe_sessione(s, corrente=(getattr(s, "session_id", None) == session_id)):
            UI.line(riga)
    if not mostrate:
        if argomento:
            UI.line("Nessuna sessione il cui nome contenga '" + argomento + "'.", style="ares.muted")
        else:
            UI.line("Nessuna sessione in archivio.", style="ares.muted")
    nascoste = len(sessioni) - len(mostrate)
    if nascoste:
        UI.line("(altre " + str(nascoste) + ": /sessioni <testo> filtra per nome)", style="ares.muted")
    if not argomento and all(getattr(s, "session_id", None) != session_id for s in sessioni):
        # La sessione in corso entra in archivio col primo turno salvato.
        # Prima di allora manca dall'elenco, e un'assenza non spiegata si
        # legge come un difetto.
        #
        # Si guarda l'elenco intero e solo senza filtro, perche' altrimenti la
        # frase e' falsa in due casi raggiungibili: sotto un filtro che la
        # esclude, e quando e' in archivio ma oltre il tetto. In tutti e due
        # la sessione c'e', e dire che manca e' peggio del silenzio.
        UI.line(
            "Questa sessione (" + session_id + ") compare qui dal primo turno salvato.",
            style="ares.muted",
        )


def _comando_entita(agent, session_id, user_id, argomento):
    UI.heading("Entita'")
    entita = leggi_entita(agent.learning_machine, user_id=user_id, query=argomento)
    if not entita:
        if argomento:
            # La ricerca delle entita' e' testuale, non semantica: senza una
            # parola che compaia davvero nel nome o nei fatti non trova
            # niente, e da fuori e' indistinguibile da un archivio vuoto.
            UI.line("Nessuna entita' per '" + argomento + "'.", style="ares.muted")
            UI.line("La ricerca e' testuale: prova una parola che ci sia scritta dentro,", style="ares.muted")
            UI.line("o /entita senza argomento per l'elenco intero.", style="ares.muted")
        else:
            UI.line("Nessuna entita' registrata.", style="ares.muted")
        return
    for e in entita:
        for riga in righe_entita(e):
            UI.line(riga)


def _comando_file(agent, session_id, user_id, argomento):
    UI.heading("Quaderno privato")
    fs = build_filesystem(user_id)
    elenco = fs.list()
    if not elenco:
        UI.line("Nessun file.", style="ares.muted")
    for f in elenco:
        UI.line("- " + str(f.path) + "   " + str(f.size_bytes) + " byte")


def _comando_lavoro(agent, session_id, user_id, argomento):
    UI.heading("Workspace")
    if not config.WORKSPACE:
        UI.line("Lo spazio di lavoro e' spento in config.py.", style="ares.muted")
        return
    radice = config.WORKSPACE_DIR
    UI.line(str(radice), style="ares.cyan")
    voci = sorted(radice.iterdir()) if radice.exists() else []
    if not voci:
        UI.line("(vuota)", style="ares.muted")
    for voce in voci:
        UI.line("- " + voce.name + ("/" if voce.is_dir() else ""))


def _comando_esci(agent, session_id, user_id, argomento):
    return False


# Un elenco solo. Prima ce n'erano due - la catena di if e la stringa
# dell'aiuto - e avevano gia' divergito: `/lavoro` esisteva da giorni e
# nell'aiuto non compariva. Da qui si derivano l'aiuto, i candidati del TAB,
# le abbreviazioni e i suggerimenti sui refusi: quattro cose che non possono
# piu' contraddirsi.
#
# Gli alias restano fuori dall'elenco a schermo e dal TAB, ma si scrivono e si
# abbreviano come gli altri: sono superstiti inglesi, non comandi da imparare.
COMANDI = (
    ("/aiuto", ("/?",), "questo elenco", _comando_aiuto),
    ("/profilo", (), "il profilo utente accumulato", _comando_profilo),
    ("/memorie", (), "le memorie non strutturate", _comando_memorie),
    ("/contesto", (), "obiettivo e avanzamento della sessione", _comando_contesto),
    ("/sessioni", (), "le conversazioni in archivio; /sessioni <testo> filtra", _comando_sessioni),
    ("/entita", (), "le entita' registrate; /entita <testo> cerca fra loro", _comando_entita),
    ("/file", (), "i file scritti dall'agente", _comando_file),
    ("/lavoro", (), "la directory di lavoro sul disco", _comando_lavoro),
    ("/esci", ("/quit", "/exit"), "termina la sessione", _comando_esci),
)


def nomi_comandi() -> list:
    return [voce[0] for voce in COMANDI]


def stampa_aiuto() -> None:
    UI.help(COMANDI)


def risolvi_comando(nome: str) -> tuple:
    """Trova la voce che l'utente intendeva. Ritorna (voce, righe da stampare).

    Tre passaggi in quest'ordine, e l'ordine conta: un nome esatto non deve
    mai essere reinterpretato, un troncamento vale solo se resta una voce
    sola, e i suggerimenti sui refusi arrivano per ultimi perche' sono
    l'ipotesi piu' debole.
    """
    for voce in COMANDI:
        if nome == voce[0] or nome in voce[1]:
            return voce, []

    candidati = [
        voce for voce in COMANDI if voce[0].startswith(nome) or any(alias.startswith(nome) for alias in voce[1])
    ]
    if len(candidati) == 1:
        return candidati[0], []
    if candidati:
        # Ambiguo: si mostrano i candidati e non si indovina. Fra `/entita` e
        # `/esci` una scelta sbagliata chiuderebbe la sessione.
        return None, ["Comando incompleto: " + "  ".join(voce[0] for voce in candidati)]

    # cutoff alto di proposito: a 0.6 un `/fiel` proponeva anche `/profilo`,
    # e un suggerimento sbagliato costa piu' di nessun suggerimento.
    vicini = difflib.get_close_matches(nome, nomi_comandi(), n=2, cutoff=0.7)
    righe = ["Comando sconosciuto: " + nome]
    if vicini:
        righe.append("Forse intendevi " + " o ".join(vicini) + "?")
    else:
        righe.append("Scrivi /aiuto per l'elenco.")
    return None, righe


def gestisci_comando(comando: str, agent, session_id: str, user_id: str) -> bool:
    """Esegue un comando locale. Ritorna False se la sessione deve terminare."""
    nome, _, argomento = comando.partition(" ")
    voce, righe = risolvi_comando(nome)
    if voce is None:
        UI.command_problem(righe)
        return True
    return voce[3](agent, session_id, user_id, argomento.strip()) is not False


def mostra_evento(flusso, evento: TurnEvent) -> None:
    """Adatta un evento neutro del core ai componenti della CLI."""
    tipo = evento.kind
    if tipo is TurnEventKind.PROCESSING_STARTED:
        flusso.activity_started("Ares sta preparando il turno...")
    elif tipo is TurnEventKind.MODEL_STARTED:
        flusso.activity_started("Ares sta elaborando...")
    elif tipo is TurnEventKind.MODEL_COMPLETED:
        flusso.activity_stopped()
    elif tipo is TurnEventKind.PRE_HOOK_STARTED:
        flusso.activity_started("Ares sta preparando il turno...")
    elif tipo is TurnEventKind.PRE_HOOK_COMPLETED:
        flusso.activity_stopped()
    elif tipo is TurnEventKind.POST_HOOK_STARTED:
        flusso.activity_started("Ares sta aggiornando cio' che ricorda...")
    elif tipo is TurnEventKind.POST_HOOK_COMPLETED:
        flusso.activity_stopped()
    elif tipo is TurnEventKind.MEMORY_STARTED:
        flusso.activity_started("Ares sta aggiornando la memoria...")
    elif tipo is TurnEventKind.MEMORY_COMPLETED:
        flusso.activity_stopped()
    elif tipo is TurnEventKind.SUMMARY_STARTED:
        flusso.activity_started("Ares sta aggiornando il riepilogo...")
    elif tipo is TurnEventKind.SUMMARY_COMPLETED:
        flusso.activity_stopped()
    elif tipo is TurnEventKind.TOOL_STARTED:
        flusso.activity_stopped()
        nome = getattr(evento.tool, "tool_name", None) or "?"
        flusso.tool_started(nome)
        flusso.activity_started(nome + " in esecuzione...")
    elif tipo is TurnEventKind.TOOL_COMPLETED:
        flusso.activity_stopped()
        # Uno strumento fallito emette Completed **e poi** Error, non l'uno
        # o l'altro. La guardia evita un esito riuscito prima dell'errore.
        if config.MOSTRA_ESITO_STRUMENTI and not getattr(evento.tool, "tool_call_error", False):
            flusso.tool_result(righe_esito(evento.tool))
    elif tipo is TurnEventKind.TOOL_ERROR:
        flusso.activity_stopped()
        errore = evento.error or getattr(evento.tool, "result", None) or ""
        if config.MOSTRA_ESITO_STRUMENTI:
            flusso.tool_result(
                righe_esito(evento.tool, errore=errore),
                errore=True,
            )
    elif tipo is TurnEventKind.CONTENT:
        if isinstance(evento.content, str):
            flusso.content(evento.content)
    elif tipo is TurnEventKind.RUN_ERROR:
        flusso.activity_stopped()
        flusso.run_error(evento.content)
    elif tipo is TurnEventKind.RUN_CANCELLED:
        flusso.activity_stopped()
        flusso.cancelled()
    elif tipo in (TurnEventKind.RUN_COMPLETED, TurnEventKind.RUN_PAUSED):
        flusso.activity_stopped()
        flusso.flush()
    elif tipo is TurnEventKind.OUTPUT:
        flusso.activity_stopped()
        # L'output conclude una singola run o continuazione. Il commit qui
        # garantisce che un'eventuale conferma venga dopo il testo prodotto.
        flusso.flush()


def mostra_flusso(eventi, *, ui=None) -> Optional[RunOutput]:
    """Mostra uno stream di eventi del core e restituisce il suo output.

    E' il piccolo adapter riusabile nei test. Il ciclo completo usa la stessa
    funzione ``mostra_evento`` mantenendo un solo stream attraverso tutte le
    eventuali continuazioni.
    """
    renderer = UI if ui is None else ui
    with renderer.stream() as flusso:
        return consume_events(eventi, lambda evento: mostra_evento(flusso, evento))


def anteprima_risultato(testo: str) -> list:
    """Le prime righe di un risultato, tagliate in altezza e in larghezza.

    Qui si tronca, e altrove no: `righe_argomento` non taglia mai perche'
    rende cio' che si sta autorizzando, dove la coda di un comando e' la
    parte che decide. Un esito e' l'opposto - e' output gia' avvenuto, e
    `get_chat_history` sa restituire una sessione intera. Non troncare qui
    vorrebbe dire far scorrere via la risposta dell'agente.

    Il taglio si dichiara sempre, in righe e in caratteri: un'anteprima che
    non dice di essere un'anteprima e' peggio di nessuna anteprima.
    """
    righe_testo = testo.splitlines()
    rese = []
    for riga in righe_testo[: config.ESITO_RIGHE]:
        riga = riga.rstrip()
        if len(riga) > config.ESITO_LARGHEZZA:
            riga = riga[: config.ESITO_LARGHEZZA - 3] + "..."
        rese.append("   | " + riga)
    restanti = len(righe_testo) - len(rese)
    if restanti == 1:
        rese.append("   | (+ un'altra riga)")
    elif restanti > 1:
        rese.append("   | (+ altre " + str(restanti) + " righe)")
    return rese


def righe_esito(strumento, errore=None) -> list:
    """Come e' finita una chiamata a uno strumento.

    Il conteggio dei caratteri sta prima dell'anteprima perche' e' l'unica
    parte esatta: dice quanto e' entrato nella finestra del modello, che e'
    la domanda a cui l'anteprima non risponde.

    La durata puo' mancare. Nel percorso normale Agno riempie
    `tool.metrics` prima di emettere l'evento; nel percorso di ripresa dopo
    una conferma copia solo `result` e `tool_call_error` sull'oggetto
    (`agno/agent/_tools.py`, righe 739-740). Come per le metriche del turno,
    un segmento assente sparisce invece di stampare zero.
    """
    if errore is not None:
        testo = str(errore).strip()
        if not testo:
            return ["   errore: senza messaggio"]
        righe_errore = testo.splitlines()
        if len(righe_errore) == 1 and len(righe_errore[0]) <= config.ESITO_LARGHEZZA:
            return ["   errore: " + righe_errore[0]]
        return ["   errore:"] + anteprima_risultato(testo)

    risultato = getattr(strumento, "result", None)
    testo = "" if risultato is None else str(risultato)
    misura = str(len(testo)) + " caratteri" if testo else "nessun contenuto"
    durata = getattr(getattr(strumento, "metrics", None), "duration", None)
    if isinstance(durata, (int, float)) and durata > 0:
        # Sotto il decimo di secondo l'arrotondamento a una cifra scriverebbe
        # "in 0.0 s", che sembra un guasto del cronometro.
        misura += " in " + ("<0.1" if durata < 0.1 else str(round(durata, 1))) + " s"
    return ["   esito: " + misura] + anteprima_risultato(testo)


def righe_argomento(nome: str, valore) -> list:
    """Rende un singolo argomento in righe leggibili, senza mai troncarlo.

    Una lista di stringhe e' un comando. `workspace_run_command` non passa da
    una shell - la lista arriva a `subprocess` elemento per elemento - ma per
    leggerla la forma naturale resta la riga ricomposta. Le virgolette che
    `shlex.join` aggiunge non sono decorazione: mostrano dove finisce un
    argomento e ne comincia un altro, che e' precisamente cio' che va
    guardato prima di autorizzare `['bash', '-lc', 'rm -rf .']`.

    Niente troncamento, in nessun ramo. Gli strumenti che passano di qui sono
    quelli di `WORKSPACE_CONFIRM` - spostare, cancellare, eseguire - e i loro
    argomenti sono percorsi e comandi, mai il contenuto di un file. Tagliare
    la coda di un comando in una richiesta di autorizzazione toglie proprio la
    parte che decide.
    """
    if isinstance(valore, list) and all(isinstance(v, str) for v in valore):
        return [
            "   " + nome + ": " + shlex.join(valore),
            "   "
            + " " * len(nome)
            + "  (lista di "
            + str(len(valore))
            + " elementi, ricomposta qui solo per leggerla)",
        ]
    testo = str(valore)
    if "\n" not in testo:
        return ["   " + nome + ": " + testo]
    righe = ["   " + nome + ":"]
    for riga in testo.splitlines():
        righe.append("      " + riga)
    return righe


def righe_richiesta(esecuzione, radice=None) -> list:
    """Descrive per intero cio' che si sta per autorizzare.

    Prima qui finiva `tool_args` cosi' com'e', cioe' il dict di Python. Il
    docstring di `Workspace` dice che il suo confine vale per i file e non per
    la shell, e `run_command` risponde su `/etc/hostname`: la conferma umana e'
    quindi l'unico controllo che resta davvero. Se e' l'unico, quello che
    l'utente legge in quel momento e' un pezzo del confine, non presentazione.
    """
    righe = ["Ares chiede di eseguire: " + str(esecuzione.tool_name)]
    argomenti = esecuzione.tool_args or {}
    if not argomenti:
        righe.append("   (senza argomenti)")
    for nome, valore in argomenti.items():
        righe.extend(righe_argomento(str(nome), valore))
    if radice is not None:
        # Per un `delete_file` il percorso e' relativo alla radice: senza
        # questa riga l'utente autorizza `note.md` senza sapere quale.
        righe.append("   nella directory: " + str(radice))
    return righe


def chiedi_conferme(risposta, input_cli: CliInput) -> int:
    """Chiede il permesso per gli strumenti in pausa. Ritorna quanti ne ha risolti.

    Il conto serve a non restare appesi: se il turno e' in pausa per un
    motivo che qui non si sa gestire, nessun requisito viene risolto e
    `continue_run` si rifermerebbe allo stesso punto, all'infinito.
    """
    risolti = 0
    for requisito in risposta.active_requirements or []:
        if not requisito.needs_confirmation:
            continue
        esecuzione = requisito.tool_execution
        nome = str(esecuzione.tool_name or "")
        radice = config.WORKSPACE_DIR if nome.startswith(config.WORKSPACE_PREFIX) else None
        UI.confirmation(righe_richiesta(esecuzione, radice=radice))
        try:
            scelta = input_cli.ask("Autorizzi? [s/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # Ctrl-C davanti a una richiesta e' un no, non un errore.
            UI.blank()
            scelta = ""
        if scelta in ("s", "si", "si'", "sì"):
            requisito.confirm()
        else:
            try:
                motivo = input_cli.ask("Motivo (invio per saltare): ", muted=True).strip()
            except (EOFError, KeyboardInterrupt):
                UI.blank()
                motivo = ""
            # Il motivo arriva al modello: senza, un rifiuto e' muto e lui
            # ritenta con una variante dello stesso comando.
            #
            # Cio' che l'utente vede di un rifiuto lo dice questa riga e
            # nient'altro: `reject_tool_call` (`agno/agent/_tools.py`, riga
            # 791) accoda il risultato negativo senza passare da
            # `handle_event`, quindi nel flusso non arriva ne' Completed ne'
            # Error. Non e' un ramo dimenticato in `mostra_flusso`.
            requisito.reject(motivo or None)
        risolti += 1
    return risolti


def _conta_chiamate(elenco) -> tuple:
    """Somma token e secondi di tutte le chiamate fatte con lo stesso ruolo.

    Un turno con strumenti chiama il modello piu' volte, e Agno tiene una riga
    per modello, non per chiamata: `accumulate_model_metrics` somma dentro
    quella riga. Qui si somma allo stesso modo fra righe diverse, che possono
    esserci se un giorno l'apprendimento usasse un modello a parte.

    `total_duration` di Ollama e' in nanosecondi.
    """
    entrata = uscita = 0
    nanosecondi = 0.0
    for metriche in elenco or []:
        entrata += getattr(metriche, "input_tokens", 0) or 0
        uscita += getattr(metriche, "output_tokens", 0) or 0
        fornitore = getattr(metriche, "provider_metrics", None) or {}
        durata = fornitore.get("total_duration")
        if isinstance(durata, (int, float)):
            nanosecondi += durata
    return entrata, uscita, nanosecondi / 1_000_000_000


def _token(quanti: int) -> str:
    """Migliaia con una cifra, perche' a cinque cifre il numero non si legge.

    La `k` vale 1024, non 1000: `NUM_CTX` e' una potenza di due (262144,
    cioe' la finestra che tutti chiamano 256k). Dividendo per 1000
    uscirebbe `262.1k`, un numero esatto che nessuno riconosce come il
    proprio tetto. La stessa base vale per il numeratore, perche' due unita'
    diverse ai lati di una barra sono peggio di entrambe le convenzioni.
    """
    if quanti >= 1024:
        return str(round(quanti / 1024.0, 1)) + "k"
    return str(quanti)


def finestra_occupata(risposta) -> Optional[int]:
    """Token del prompt dell'ultima chiamata al modello principale.

    Non si usa `risposta.metrics.input_tokens`: quello e' la somma di **ogni**
    chiamata del turno, le tre estrazioni delle memorie comprese
    (`accumulate_model_metrics` in `agno/metrics.py` somma dentro la riga del
    modello). Mostrarlo come occupazione della finestra produrrebbe quindi un
    valore sovrastimato.

    L'occupazione vera e' il prompt dell'ultima chiamata, che sta nei metrics
    del messaggio. Regge perche' Ollama conta il prompt intero e non il solo
    delta lasciato scoperto dalla KV cache: fra due turni consecutivi il
    conteggio puo' salire mentre la durata di valutazione scende grazie alla
    cache. Se contasse i soli token
    valutati, il secondo turno avrebbe detto qualche centinaio.
    """
    ultimo = None
    for messaggio in getattr(risposta, "messages", None) or []:
        if getattr(messaggio, "role", None) != "assistant":
            continue
        metriche = getattr(messaggio, "metrics", None)
        token = getattr(metriche, "input_tokens", 0) or 0
        if token:
            ultimo = token
    return ultimo


def righe_metriche(risposta) -> list:
    """Il costo del turno in una riga, o niente se non c'e' niente da dire.

    Un turno interrotto o fallito arriva senza metriche: la riga non si
    inventa. Vale anche per i singoli pezzi, perche' spegnere gli store
    ALWAYS in `config.py` fa sparire davvero il segmento dell'apprendimento
    invece di stamparlo a zero.
    """
    metriche = getattr(risposta, "metrics", None)
    if metriche is None:
        return []
    dettagli = getattr(metriche, "details", None) or {}
    _, uscita, secondi_risposta = _conta_chiamate(dettagli.get("model"))
    _, appresi, secondi_appresi = _conta_chiamate(dettagli.get("learning_model"))

    pezzi = []
    prompt = finestra_occupata(risposta)
    if prompt and config.NUM_CTX:
        quota = 100.0 * prompt / config.NUM_CTX
        pezzi.append(
            "finestra "
            + _token(prompt)
            + "/"
            + _token(config.NUM_CTX)
            + " ("
            + ("<1" if quota < 1 else str(round(quota)))
            + "%)"
        )
    if uscita:
        pezzi.append("risposta " + _token(uscita) + " tok / " + str(round(secondi_risposta, 1)) + " s")
    if appresi:
        # Il segmento che giustifica la riga: e' il costo che non si vede,
        # perche' arriva dopo che la risposta e' gia' a schermo.
        pezzi.append("apprendimento " + _token(appresi) + " tok / " + str(round(secondi_appresi, 1)) + " s")
    durata = getattr(metriche, "duration", None)
    if durata:
        pezzi.append("turno " + str(round(durata, 1)) + " s")
    if not pezzi:
        return []
    return ["[" + "  ".join(pezzi) + "]"]


def _turno(agent, testo: str, input_cli: CliInput) -> Optional[RunOutput]:
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


def esegui_turno(agent, testo: str, input_cli: CliInput) -> Optional[RunOutput]:
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
    try:
        return _turno(agent, testo, input_cli)
    except KeyboardInterrupt:
        # Il context manager del renderer ha gia' chiuso l'anteprima e reso
        # permanente l'eventuale Markdown parziale.
        UI.blank()
        UI.line("Interrotto fuori dal turno. Non e' stato appreso.", style="ares.warning")
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


def _esegui_chat() -> None:
    parser = argparse.ArgumentParser(description="Assistente personale locale")
    parser.add_argument("--session", default="principale", help="Identificativo della sessione")
    parser.add_argument("--user", default=config.DEFAULT_USER_ID, help="Identificativo dell'utente")
    parser.add_argument("--debug", action="store_true", help="Mostra le chiamate al modello")
    parser.add_argument(
        "--metriche",
        action="store_true",
        help="Mostra il costo di ogni turno: finestra occupata, token, secondi",
    )
    args = parser.parse_args()

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
