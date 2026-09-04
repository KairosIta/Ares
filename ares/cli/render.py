"""Presentazione degli eventi, delle conferme e delle metriche del turno."""

import shlex

from agno.run.agent import RunOutput

from ares import config
from ares.agent.turn_core import TurnEvent, TurnEventKind, consume_events
from ares.cli.editor import CliInput
from ares.cli.ui import UI


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
        if not getattr(evento.tool, "tool_call_error", False):
            if config.MOSTRA_ESITO_STRUMENTI:
                flusso.tool_result(righe_esito(evento.tool))
            if config.MOSTRA_APPRENDIMENTI:
                scrittura = righe_scrittura(evento.tool)
                if scrittura:
                    flusso.tool_result(scrittura)
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


def mostra_flusso(eventi, *, ui=None) -> RunOutput | None:
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
        return ["   errore:", *anteprima_risultato(testo)]

    risultato = getattr(strumento, "result", None)
    testo = "" if risultato is None else str(risultato)
    misura = str(len(testo)) + " caratteri" if testo else "nessun contenuto"
    durata = getattr(getattr(strumento, "metrics", None), "duration", None)
    if isinstance(durata, (int, float)) and durata > 0:
        # Sotto il decimo di secondo l'arrotondamento a una cifra scriverebbe
        # "in 0.0 s", che sembra un guasto del cronometro.
        misura += " in " + ("<0.1" if durata < 0.1 else str(round(durata, 1))) + " s"
    return ["   esito: " + misura, *anteprima_risultato(testo)]


# Gli strumenti con cui il modello scrive da solo nella memoria durevole.
# Sono i nomi che Agno da' alle funzioni degli store; `write_file` del
# quaderno non c'e' perche' il quaderno non viene reiniettato nel prompt, e
# `/file` lo mostra per intero.
STRUMENTI_DI_MEMORIA = frozenset(
    {"save_learning", "remember_about", "link_entities", "forget", "update_user_memory", "update_profile"}
)


def righe_scrittura(strumento) -> list:
    """Cosa uno strumento di memoria ha ricevuto da scrivere, per intero.

    L'esito di `save_learning` dice il titolo e quello di `remember_about`
    quanti fatti ha registrato: il testo che e' entrato lo dicono solo gli
    argomenti. Niente troncamento, per la ragione di `righe_argomento`: qui
    il contenuto non e' il rumore intorno alla decisione, e' la cosa da
    leggere. Vuoto per ogni altro strumento.
    """
    nome = str(getattr(strumento, "tool_name", None) or "")
    if nome not in STRUMENTI_DI_MEMORIA:
        return []
    righe = ["   in memoria: " + nome]
    for chiave, valore in (getattr(strumento, "tool_args", None) or {}).items():
        if valore is None or valore == [] or valore == "":
            continue
        if isinstance(valore, (list, tuple)):
            testo = ", ".join(str(v) for v in valore)
        else:
            testo = " ".join(str(valore).split())
        righe.append("   | " + str(chiave) + ": " + testo)
    return righe


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


def finestra_occupata(risposta) -> int | None:
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
