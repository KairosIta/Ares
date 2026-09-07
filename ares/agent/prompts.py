"""Istruzioni dell'agente condizionate alle capacita' realmente abilitate.

In fondo, `messaggio_di_sistema` chiede ad Agno il system message intero
cosi' come lo comporrebbe per un turno: e' cio' che `ares inspect --prompt`
stampa, ed e' l'unico modo di leggere davvero cio' che il modello riceve.
"""

from pathlib import Path
from typing import Any

from ares import config


def istruzioni_sugli_strumenti(radice_lavoro=None) -> list[str]:
    """Restituisce soltanto istruzioni per strumenti presenti nel cablaggio."""
    dette = []
    if config.LEARN_USER_MEMORY and config.MEMORY_AGENT_TOOLS:
        dette.append(
            "Se una memoria su di lui e' sbagliata, superata o scritta in "
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
            "redirezioni, piu' comandi insieme - usa "
            "['bash', '-lc', 'la riga']. Per leggere, elencare e cercare hai "
            "gli strumenti dedicati, che non chiedono niente a nessuno: la "
            "shell serve per cio' che loro non sanno fare. Prima di modificare un file "
            "leggilo. Cancellare, spostare ed eseguire comandi li deve "
            "autorizzare l'utente: il turno si ferma e lui decide. Se "
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
