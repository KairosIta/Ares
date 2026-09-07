"""Istruzioni dell'agente condizionate alle capacita' realmente abilitate.

In fondo, `messaggio_di_sistema` chiede ad Agno il system message intero
cosi' come lo comporrebbe per un turno: e' cio' che `ares inspect --prompt`
stampa, ed e' l'unico modo di leggere davvero cio' che il modello riceve.
"""

import os
import platform
from pathlib import Path
from typing import Any

from ares import config
from ares.state.git import ramo_git


def _shell() -> tuple[str, str]:
    """Il nome della shell di questo sistema e l'esempio per lanciarle una riga."""
    if os.name == "nt":
        return "PowerShell", "['powershell', '-Command', 'la riga']"
    return "bash", "['bash', '-lc', 'la riga']"


def _esempio_shell() -> str:
    return _shell()[1]


def _ruolo(modello: str, *, locale: str, cloud: str) -> str:
    """Il nome del modello e cio' che comporta, secondo il tag."""
    return modello + ", " + (cloud if config.e_modello_cloud(modello) else locale)


def descrizione() -> str:
    """Chi e' Ares, e dove gira davvero.

    La frase sulla privacy e' una promessa, e una promessa che il modello
    ripete all'utente deve essere vera: con un modello cloud nel `.env` la
    descrizione dice invece cosa attraversa `ollama.com`, in una riga, e che
    la scelta e' stata della persona. Il dettaglio sta nella scheda di
    `istruzioni_sull_ambiente`; qui c'e' l'identita'.
    """
    inizio = "Sei Ares, l'assistente personale di una sola persona. "
    fine = " Ricordi da una conversazione all'altra, e cio' che sai di questa persona l'hai imparato parlandole."
    conversazione = config.e_modello_cloud(config.MAIN_MODEL)
    estrazione = config.e_modello_cloud(config.LEARNING_MODEL)
    if not conversazione and not estrazione:
        return (
            inizio + "Giri interamente sulla sua macchina: nessuna delle vostre conversazioni "
            "esce di qui, e non c'e' nessun servizio remoto dietro di te." + fine
        )
    if conversazione and estrazione:
        remoto = "il modello che ti fa parlare e quello che estrae le memorie stanno"
    elif conversazione:
        remoto = "il modello che ti fa parlare sta"
    else:
        remoto = "il modello che estrae le memorie dai vostri turni sta"
    return (
        inizio + "Il tuo stato vive sulla sua macchina, ma " + remoto + " su ollama.com: cio' che passa "
        "di li' attraversa un servizio remoto, e la persona lo sa perche' l'ha scelto." + fine
    )


def istruzioni_sull_ambiente(*, user_id: str, session_id: str, radice_lavoro=None) -> list[str]:
    """La scheda di questo avvio: quali modelli, quanto contesto, quale sistema, chi e dove.

    Tutto letto da `config` e dal sistema, niente scritto a mano: una riga
    che dicesse "9B locale" resterebbe vera nel file e falsa nel `.env`. Un
    modello che sa di essere un modello cloud non rassicura l'utente sulla
    privacy; uno che sa quanti token ha in vista non promette di ricordare
    cio' che e' gia' uscito dalla finestra; uno che sa la shell non scrive
    `bash` su Windows.
    """
    sistema, _ = _shell()
    dove = ""
    if radice_lavoro is not None:
        ramo = ramo_git(Path(radice_lavoro))
        dove = " Cartella di lavoro: " + str(radice_lavoro) + (", ramo git " + ramo + "." if ramo else ".")
    righe = [
        "Dove sei e con che cosa lavori, letto dalla configurazione di questo avvio:",
        "- Il modello che ti fa parlare e' "
        + _ruolo(
            config.MAIN_MODEL,
            locale="in locale: gira su questa macchina tramite Ollama, e niente di cio' che leggi o scrivi la lascia.",
            cloud="un modello cloud: il daemon Ollama di questa macchina lo inoltra a ollama.com, quindi questo "
            "prompt, la conversazione, i file che apri, l'output dei comandi e le memorie che ti vengono "
            "mostrate passano da un server remoto.",
        ),
        "- Profilo, memorie e contesto di sessione non li scrivi tu: li estrae dopo ogni tua risposta "
        + (
            "lo stesso modello."
            if config.LEARNING_MODEL == config.MAIN_MODEL
            else _ruolo(
                config.LEARNING_MODEL,
                locale="in locale.",
                cloud="un modello cloud: il testo dei turni e le memorie gia' salvate passano da ollama.com.",
            )
        ),
        "- Le intuizioni sono indicizzate da " + config.EMBEDDER_MODEL + ", che gira sempre in locale.",
        "- La tua finestra di contesto e' di "
        + str(config.NUM_CTX)
        + " token. In vista hai gli ultimi "
        + str(config.NUM_HISTORY_RUNS)
        + " scambi di questa conversazione; il resto e' in archivio e non lo ricordi finche' non lo rileggi.",
        "- Sistema: "
        + platform.system()
        + " "
        + platform.release()
        + ", shell "
        + sistema
        + ". I comandi che lanci girano con i permessi dell'utente, senza sandbox.",
        "- Utente: " + user_id + ". Conversazione: " + session_id + "." + dove,
    ]
    return ["\n".join(righe)]


def istruzioni_sugli_strumenti(radice_lavoro=None) -> list[str]:
    """Restituisce soltanto istruzioni per strumenti presenti nel cablaggio."""
    dette = []
    if config.LEARN_USER_MEMORY and config.MEMORY_AGENT_TOOLS:
        dette.append(
            "Se una memoria sulla persona con cui parli e' sbagliata, superata o scritta in "
            "inglese, correggila con update_user_memory invece di limitarti a "
            "dirlo: descrivi a parole cosa aggiungere, cambiare o togliere."
        )
    if config.LEARN_ENTITIES:
        dette.append(
            "Su persone e progetti distingui i fatti dagli eventi quando usi "
            "remember_about, e scrivi gli uni e gli altri in italiano: un "
            "fatto e' un valore attuale che un giorno sara' sostituito, un "
            "evento e' qualcosa che e' accaduto e resta vero per sempre. "
            "Anche le opinioni e le posizioni prese sono eventi. Metti una "
            "data nel testo dell'evento solo se e' diversa da oggi: cio' che "
            "accade adesso viene datato da solo quando lo salvi, e una data "
            "scritta a mano e' un'occasione per sbagliarla."
        )
    if config.LEARN_KNOWLEDGE:
        dette.append(
            "Quando l'utente chiede esplicitamente di salvare un criterio nelle "
            "intuizioni, usa prima search_learnings per i duplicati e poi "
            "save_learning: non scriverlo nel quaderno con write_file o "
            "append_file, perche' il quaderno non viene cercato automaticamente "
            "nelle conversazioni future."
        )
    if config.SEARCH_PAST_SESSIONS:
        dette.append(
            "Per cio' che e' stato detto in un'altra conversazione: "
            "search_past_sessions elenca le sessioni e non accetta una "
            "domanda, poi read_past_session ne rilegge una per id, con "
            "num_runs se ti bastano i primi scambi."
        )
    if radice_lavoro is not None:
        dette.append(
            "Lavori nella cartella da cui l'utente ti ha avviato, " + str(radice_lavoro) + ": "
            "e' il suo progetto, con i suoi file, non uno spazio tuo. Gli "
            "strumenti che cominciano con workspace_ leggono e scrivono li' "
            "dentro, sul disco vero, ed e' l'unica parte del computer che puoi "
            "toccare. Modifica solo cio' che ti viene chiesto: non riordinare, "
            "non rinominare e non cancellare per pulizia. Gli strumenti senza "
            "prefisso - read_file, write_file, list_files - sono invece il tuo "
            "quaderno privato, che vive in un database e non esiste sul disco: "
            "non confondere i due posti. "
            "workspace_run_command vuole il comando spezzato in una lista di "
            "stringhe, una per parola: ['ls', '-la'], non ['ls -la']. Non "
            "passa da una shell, quindi per una riga intera - pipe, "
            "redirezioni, piu' comandi insieme - usa " + _esempio_shell() + ". "
            "Per leggere, elencare e cercare hai "
            "gli strumenti dedicati, che non chiedono niente a nessuno: la "
            "shell serve per cio' che loro non sanno fare. Prima di modificare un file "
            "leggilo. Cancellare, spostare ed eseguire comandi li deve "
            "autorizzare l'utente: il turno si ferma finche' non risponde. Se "
            "rifiuta, non cercare una strada diversa per fare la stessa cosa: "
            "chiedi."
        )
    if config.READ_CHAT_HISTORY:
        dette.append(
            "Per questa conversazione oltre gli ultimi turni che hai in "
            "vista usa get_chat_history, sempre con num_chats."
        )
    return dette


def istruzioni_sulle_conversazioni(sessioni, *, cartella) -> list[str]:
    """Le conversazioni precedenti nate nella stessa cartella, per id.

    `search_past_sessions` elenca le ultime venti sessioni dell'utente senza
    sapere dove sono nate: in una cartella con dieci progetti accanto, "dove
    eravamo rimasti" pesca a caso. Qui il modello riceve le poche di questo
    posto, con l'id da passare a `read_past_session`. Vuoto se non ce ne
    sono: un'istruzione che dice "nessuna" occuperebbe spazio per niente.
    """
    if not sessioni:
        return []
    from ares.state.stores import prima_domanda, quando_sessione

    righe = []
    for sessione in sessioni:
        scambi = len(getattr(sessione, "runs", None) or [])
        riga = "- " + str(getattr(sessione, "session_id", "?")) + " (" + quando_sessione(sessione)
        riga += ", " + str(scambi) + (" scambio" if scambi == 1 else " scambi") + ")"
        inizio = prima_domanda(sessione, larghezza=120)
        if inizio:
            riga += ": " + inizio
        righe.append(riga)
    return [
        "In questa cartella, " + str(cartella) + ", ci sono state altre conversazioni. "
        "Se l'utente si riferisce a lavoro gia' fatto qui - 'dove eravamo rimasti', "
        "'come avevamo deciso' - rileggile con read_past_session passando l'id, "
        "dalla piu' recente:\n" + "\n".join(righe)
    ]


def istruzioni_dalla_cartella(radice_lavoro) -> list[str]:
    """Il contenuto di `ARES.md` nella cartella di lavoro, se c'e'.

    E' il `CLAUDE.md` di Ares: regole del progetto scritte da chi ci lavora,
    che entrano nel prompt prima del primo turno. Un file oltre il tetto viene
    troncato e lo si dice al modello, cosi' non crede di aver letto tutto.
    Un file illeggibile vale come assente: un permesso negato non deve
    impedire la chat.
    """
    if radice_lavoro is None:
        return []
    percorso = Path(radice_lavoro) / config.WORKSPACE_ISTRUZIONI
    try:
        grezzo = percorso.read_bytes()
    except OSError:
        return []
    troncato = len(grezzo) > config.WORKSPACE_ISTRUZIONI_MAX_BYTE
    testo = grezzo[: config.WORKSPACE_ISTRUZIONI_MAX_BYTE].decode("utf-8", errors="replace").strip()
    if not testo:
        return []
    intestazione = (
        "Chi lavora in questa cartella ha lasciato istruzioni in "
        + config.WORKSPACE_ISTRUZIONI
        + ". Seguile finche' non contraddicono cio' che l'utente ti chiede adesso"
        + ("; il file e' piu' lungo del tetto e qui ne vedi solo l'inizio, dillo se conta" if troncato else "")
        + ":\n\n"
    )
    return [intestazione + testo]


def messaggio_di_sistema(agent: Any, *, session_id: str, user_id: str) -> str:
    """Il system message che Agno manderebbe al modello per un turno, verbatim.

    Non basta leggere `description` e `instructions`: Agno aggiunge da se' le
    istruzioni degli strumenti, quelle della macchina di apprendimento, le
    memorie e le entita' gia' salvate, la data e il nome. Il solo modo di
    vedere il testo intero e' fargli fare gli stessi passi di `run()` fino al
    messaggio, e fermarsi li': inizializzare l'agente, che e' cio' che
    aggancia gli strumenti di memoria; leggere la sessione, o costruirne una
    vuota in memoria se non esiste, senza scriverla; risolvere gli strumenti,
    perche' le loro istruzioni entrano nel messaggio; e chiedere il messaggio.

    Nessun passo chiama il modello. La sessione nuova resta in memoria:
    e' `run()` a salvarla, e qui `run()` non si chiama. `determine_tools_for_model`
    e' un interno di Agno, e per questo il vincolo su Agno nel pyproject e' stretto.
    """
    from uuid import uuid4

    from agno.agent._tools import determine_tools_for_model
    from agno.run import RunContext
    from agno.run.agent import RunOutput
    from agno.session import AgentSession

    agent.initialize_agent()
    sessione = agent.get_session(session_id=session_id, user_id=user_id) or AgentSession(
        session_id=session_id,
        agent_id=agent.id,
        user_id=user_id,
        metadata=dict(agent.metadata) if agent.metadata else None,
    )
    contesto = RunContext(run_id=str(uuid4()), session_id=session_id, user_id=user_id, metadata=agent.metadata)
    esito = RunOutput(run_id=contesto.run_id, session_id=session_id, user_id=user_id)
    strumenti = agent.get_tools(run_response=esito, run_context=contesto, session=sessione, user_id=user_id)
    funzioni = determine_tools_for_model(
        agent,
        model=agent.model,
        processed_tools=strumenti,
        run_response=esito,
        run_context=contesto,
        session=sessione,
    )
    messaggio = agent.get_system_message(session=sessione, run_context=contesto, tools=funzioni)
    if messaggio is None:
        return ""
    contenuto = messaggio.content
    return contenuto if isinstance(contenuto, str) else str(contenuto)
