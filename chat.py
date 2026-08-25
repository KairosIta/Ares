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

Comandi durante la chat: lo slash apre l'elenco, `/aiuto` lo descrive, il TAB
completa e bastano le iniziali finche' restano uniche. L'elenco vive in `COMANDI`, qui sotto: era
scritto anche qui e le due copie erano gia' divergite.
"""

import argparse
import difflib
import readline
import shlex
from typing import Optional

from agno.run.agent import RunOutput

import config
from assistant import build_assistant, build_filesystem
from cli_ui import UI
from state_lock import StatoOccupato, lock_stato
from stores import leggi_entita, leggi_sessioni, righe_entita, righe_sessione, stampa_store

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
    stampa_store(
        agent.learning_machine.session_context_store, "Contesto", session_id=session_id
    )


def _comando_sessioni(agent, session_id, user_id, argomento):
    UI.heading("Sessioni")
    sessioni = leggi_sessioni(agent, user_id=user_id, query=argomento)
    mostrate = sessioni[:config.SESSIONI_ELENCO]
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
        voce
        for voce in COMANDI
        if voce[0].startswith(nome) or any(alias.startswith(nome) for alias in voce[1])
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


def candidati_comando(riga: str, testo: str) -> list:
    """I candidati per il TAB, separati da readline per poterli verificare.

    Guarda la riga intera e non solo la parola: un TAB dentro un messaggio
    normale non deve proporre niente, e un comando comincia sempre a inizio
    riga.
    """
    if not riga.lstrip().startswith("/"):
        return []
    return [nome for nome in nomi_comandi() if nome.startswith(testo)]


def _completa(testo: str, stato: int):
    candidati = candidati_comando(readline.get_line_buffer(), testo)
    if stato < len(candidati):
        # Lo spazio in coda apre la strada a un argomento senza doverlo
        # battere, e la REPL lo toglie.
        return candidati[stato] + " "
    return None


def accendi_completamento() -> None:
    readline.set_completer(_completa)
    # Lo slash e' fra i separatori di parola di serie: senza questa riga il
    # completatore riceverebbe `mem` invece di `/mem` e non troverebbe niente.
    readline.set_completer_delims(" ")
    readline.parse_and_bind("tab: complete")
    # Un TAB solo mostra i candidati, invece di chiederne un secondo.
    readline.parse_and_bind("set show-all-if-ambiguous on")
    # La campanella suonerebbe a ogni slash scritto in una frase normale: la
    # macro qui sotto tenta un completamento che non trova niente, e senza
    # candidati readline suona ripetutamente. Non si perde niente a
    # spegnerla, perche' l'unico
    # completamento di questa REPL sono i comandi.
    readline.parse_and_bind("set bell-style none")
    # Lo slash apre l'elenco senza premere altro. La forma ovvia - `"/": "/\t"`
    # - manda readline in ricorsione infinita, perche' lo slash della macro
    # ri-attiva la macro: `\C-q` e' `quoted-insert` e inserisce il carattere
    # saltando le associazioni. In una frase normale la macro parte lo stesso e
    # non produce niente, perche' il completatore guarda la riga intera.
    readline.parse_and_bind(r'"/": "\C-q/\t"')


def gestisci_comando(comando: str, agent, session_id: str, user_id: str) -> bool:
    """Esegue un comando locale. Ritorna False se la sessione deve terminare."""
    nome, _, argomento = comando.partition(" ")
    voce, righe = risolvi_comando(nome)
    if voce is None:
        UI.command_problem(righe)
        return True
    return voce[3](agent, session_id, user_id, argomento.strip()) is not False


def apri_cronologia() -> int:
    """Carica la cronologia dei turni e dice quante righe erano gia' li'.

    L'import di readline in cima al file basta a far funzionare frecce e
    correzione di riga: senza, `input()` non interpreta i tasti e li lascia
    nel testo. Una freccia in su per riprendere la domanda precedente non
    recupera niente, entra nel messaggio come sequenza di escape, va al
    modello e finisce in archivio come turno.

    Il file viene creato da `write_history_file` e non da un touch: readline
    lo apre a 0600, mentre `open(..., "a")` segue la umask e a 0644 lascia
    leggere a chiunque abbia un account sulla macchina tutto cio' che si e'
    detto ad Ares. Deve comunque esistere prima dell'append, che su un file
    mancante solleva FileNotFoundError.
    """
    percorso = str(config.CRONOLOGIA_FILE)
    # Il tetto vale anche in coda a un append: readline tronca il file dopo
    # averci scritto, quindi non serve nessuna potatura nostra.
    readline.set_history_length(config.CRONOLOGIA_RIGHE)
    try:
        readline.read_history_file(percorso)
    except FileNotFoundError:
        try:
            readline.write_history_file(percorso)
        except OSError as errore:
            UI.line("Cronologia non creata: " + str(errore), style="ares.warning")
    except OSError as errore:
        # Una cronologia illeggibile non vale una chat che non parte.
        UI.line("Cronologia non caricata: " + str(errore), style="ares.warning")
    return readline.get_current_history_length()


def salva_cronologia(gia_presenti: int) -> None:
    """Accoda le sole righe di questa sessione.

    `write_history_file` riscriverebbe il file intero, e il lock condiviso
    ammette apposta piu' chat insieme: l'ultima che esce cancellerebbe cio'
    che le altre hanno scritto mentre era aperta. `append_history_file`
    scrive solo la coda nuova e regge l'intreccio.
    """
    nuove = readline.get_current_history_length() - gia_presenti
    if nuove <= 0:
        return
    try:
        readline.append_history_file(nuove, str(config.CRONOLOGIA_FILE))
    except OSError as errore:
        UI.line("Cronologia non salvata: " + str(errore), style="ares.warning")


def mostra_flusso(eventi) -> Optional[RunOutput]:
    """Stampa un turno mentre arriva e restituisce l'esito.

    Sostituisce `print_response`, che qui non e' utilizzabile: e' annotato
    `-> None` e davanti a una pausa disegna un pannello e ritorna, quindi chi
    chiama non ha in mano niente da confermare e il turno finisce li'. Con
    `yield_run_output` l'ultimo elemento del flusso e' il RunOutput, pausa
    compresa.
    """
    risposta = None
    with UI.stream() as flusso:
        for evento in eventi:
            if isinstance(evento, RunOutput):
                risposta = evento
                continue
            tipo = getattr(evento, "event", "")
            if tipo == "ToolCallStarted":
                strumento = getattr(evento, "tool", None)
                nome = getattr(strumento, "tool_name", None) or "?"
                flusso.tool_started(nome)
            elif tipo == "ToolCallCompleted":
                strumento = getattr(evento, "tool", None)
                # Uno strumento fallito emette Completed **e poi** Error, non
                # l'uno o l'altro. Senza questa guardia l'errore comparirebbe
                # due volte, la prima presentato come un esito riuscito.
                if config.MOSTRA_ESITO_STRUMENTI and not getattr(strumento, "tool_call_error", False):
                    flusso.tool_result(righe_esito(strumento))
            elif tipo == "ToolCallError":
                strumento = getattr(evento, "tool", None)
                # Agno costruisce l'evento con `error=str(tool.result)`, ma il
                # risultato sullo strumento e' la fonte: se un giorno l'evento
                # arrivasse senza, un errore muto sarebbe letto come un successo.
                errore = getattr(evento, "error", None) or getattr(strumento, "result", None) or ""
                if config.MOSTRA_ESITO_STRUMENTI:
                    flusso.tool_result(righe_esito(strumento, errore=errore), errore=True)
            elif tipo == "RunContent":
                contenuto = getattr(evento, "content", None)
                if isinstance(contenuto, str):
                    flusso.content(contenuto)
            elif tipo == "RunError":
                # print_response mostrava gli errori da solo. Qui il flusso e'
                # nostro: senza questa riga un turno fallito uscirebbe muto.
                flusso.run_error(getattr(evento, "content", None))
            elif tipo == "RunCancelled":
                # Agno conserva il run annullato ma non raggiunge i post-hook:
                # le due conseguenze restano entrambe esplicite a schermo.
                flusso.cancelled()
    return risposta


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
            "   " + " " * len(nome) + "  (lista di " + str(len(valore))
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


def chiedi_conferme(risposta) -> int:
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
            scelta = UI.ask("Autorizzi? [s/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # Ctrl-C davanti a una richiesta e' un no, non un errore.
            UI.blank()
            scelta = ""
        if scelta in ("s", "si", "si'", "sì"):
            requisito.confirm()
        else:
            try:
                motivo = UI.ask("Motivo (invio per saltare): ", muted=True).strip()
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
            "finestra " + _token(prompt) + "/" + _token(config.NUM_CTX)
            + " (" + ("<1" if quota < 1 else str(round(quota))) + "%)"
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


def _turno(agent, testo: str) -> Optional[RunOutput]:
    """Il turno vero, senza le difese: le pause per autorizzare uno strumento."""
    risposta = mostra_flusso(
        agent.run(testo, stream=True, stream_events=True, yield_run_output=True)
    )

    while risposta is not None and risposta.is_paused:
        if chiedi_conferme(risposta) == 0:
            UI.line(
                "Il turno e' in pausa per qualcosa che non so chiedere. Lo lascio li'.",
                style="ares.warning",
            )
            return risposta
        risposta = mostra_flusso(
            agent.continue_run(
                run_response=risposta,
                requirements=risposta.requirements,
                stream=True,
                stream_events=True,
                yield_run_output=True,
            )
        )
    return risposta


def esegui_turno(agent, testo: str) -> Optional[RunOutput]:
    """Un turno intero, con una rete sotto per cio' che Agno non prende.

    Questa rete cattura molto meno di quanto sembri, e vale la pena dire cosa
    resta fuori. Agno gestisce da se' sia `KeyboardInterrupt` sia le
    eccezioni dentro i propri generatori di streaming - `_run_stream` alle
    righe 1220 e 1243, `_continue_run_stream` alla 4061 - e non le rilancia:
    un Ctrl-C mentre Ollama genera diventa un evento `RunCancelled`, che
    `mostra_flusso` stampa, e un guasto diventa un evento `RunError`, idem.
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
        return _turno(agent, testo)
    except KeyboardInterrupt:
        # A capo nostro: `mostra_flusso` stava scrivendo la risposta e il suo
        # print finale non e' stato raggiunto.
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

    agent = build_assistant(user_id=args.user, session_id=args.session, debug=args.debug)

    # Il flag di config e' il default, l'opzione lo accende per una sessione
    # sola: guardare il costo dei turni e' quasi sempre una cosa che si fa
    # per un pomeriggio, non una preferenza permanente.
    mostra_metriche = config.MOSTRA_METRICHE or args.metriche

    gia_presenti = apri_cronologia()
    accendi_completamento()

    UI.banner(modello=config.MAIN_MODEL, sessione=args.session, utente=args.user)
    UI.blank()

    try:
        while True:
            try:
                testo = UI.prompt().strip()
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

            risposta = esegui_turno(agent, testo)
            if mostra_metriche and risposta is not None:
                for riga in righe_metriche(risposta):
                    UI.metrics(riga)
            UI.blank()
    finally:
        # Anche su una via d'uscita che non passa dal break: cio' che si e'
        # scritto in una sessione finita male e' precisamente cio' che si
        # vorra' riprendere alla successiva.
        salva_cronologia(gia_presenti)

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
