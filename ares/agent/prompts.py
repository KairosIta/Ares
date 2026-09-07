"""Istruzioni dell'agente condizionate alle capacita' realmente abilitate."""

from pathlib import Path

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
