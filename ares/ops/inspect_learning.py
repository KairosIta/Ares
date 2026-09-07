"""
Ispezione di cio' che l'agente ha imparato
==========================================
Uso:
    ares inspect
    ares inspect --session test_1
    ares inspect --file notes/setup.md
    ares inspect --prompt

Legge gli archivi senza avviare il modello conversazionale e non scrive
negli store. Due cose vanno dette per intero: come ogni comando che apre
l'archivio, crea la directory dello stato se manca, cosi' su un clone nuovo
lascia una `tmp/` vuota; e la ricerca fra le intuizioni vettorizza la query
con l'embedder locale, che e' l'unica inferenza di questo comando e l'unico
momento in cui un modello entra in memoria. Serve a rispondere alla domanda
che conta quando un agente dice di ricordare: dove sta questa informazione,
e la ritrovera' davvero?

`--prompt` risponde a una domanda accanto: cosa riceve il modello prima
della prima parola dell'utente? Stampa il system message intero, cosi' come
Agno lo comporrebbe per un turno in questa cartella - istruzioni di Ares,
istruzioni degli strumenti e della macchina di apprendimento, memorie ed
entita' gia' salvate - senza aprire il turno. Una modifica ai prompt si
giudica leggendo questo, non i pezzi in `prompts.py`.
"""

from pathlib import Path

from ares import config
from ares.cli.comando import nuova_app
from ares.cli.ui import UI, byte_leggibili
from ares.state.lock import StatoOccupato, lock_stato

app = nuova_app("inspect", "Ispeziona gli archivi di apprendimento senza toccarli")


def separatore(titolo: str) -> None:
    UI.blank()
    UI.heading(titolo)


def _ispeziona(user: str, session: str | None, query: str, file: str | None, prompt: bool) -> None:
    from ares.agent.assistant import build_assistant, build_filesystem
    from ares.agent.prompts import messaggio_di_sistema
    from ares.cli.cartella import nuovo_id_sessione
    from ares.cli.log import configura_log_agno
    from ares.state.stores import leggi_entita, leggi_intuizioni, righe_entita, stampa_store

    # Qui e non prima: `--help` esce dentro Cyclopts, e un comando che stampa
    # l'aiuto non deve creare l'archivio che dice di ispezionare.
    config.prepara_archivio()

    fs = build_filesystem(user)

    if file:
        contenuto = fs.read(file)
        if contenuto is None:
            UI.err("Nessun file a questo percorso: " + file)
        else:
            # Verbatim, senza passare da Rich: e' il contenuto di un file, e
            # chi lo redirige su un altro file lo vuole identico.
            print(contenuto)
        return

    if prompt:
        # La conversazione che `ares` aprirebbe adesso in questa cartella,
        # oppure quella nominata: il contesto di sessione e l'elenco delle
        # conversazioni precedenti dipendono da quale si guarda. Verbatim,
        # come `--file`: e' un testo da leggere o da confrontare con `diff`.
        # Il log INFO di Agno passa da Rich su stdout: davanti al prompt ci
        # finirebbe "Creating table" su un archivio nuovo. Warning ed errori
        # restano, ma tolti dallo stdout che qui e' il testo e basta.
        configura_log_agno(False)
        session = session or nuovo_id_sessione(Path.cwd())
        agent = build_assistant(user_id=user, session_id=session)
        print(messaggio_di_sistema(agent, session_id=session, user_id=user))
        return

    agent = build_assistant(user_id=user, session_id=session or "principale")
    if not session:
        # Senza `--session` si guarda l'ultima conversazione toccata, di
        # qualunque cartella: e' quella di cui si vuole sapere cosa e' rimasto.
        from ares.state.stores import leggi_sessioni

        recenti = leggi_sessioni(agent, user_id=user)
        session = str(recenti[0].session_id) if recenti else "principale"
    lm = agent.learning_machine
    # `build_assistant` passa sempre `learning=`, quindi la macchina c'e'. I
    # singoli store possono invece essere None se spenti in config.py, ed e'
    # `stampa_store` a dirlo invece di sollevare AttributeError.
    assert lm is not None

    separatore("PROFILO UTENTE   (per utente, sopravvive a ogni sessione)")
    stampa_store(lm.user_profile_store, "Profilo", user_id=user)

    separatore("MEMORIE   (osservazioni non strutturate, per utente)")
    stampa_store(lm.user_memory_store, "Memorie", user_id=user)

    separatore("CONTESTO DI SESSIONE   (sessione: " + session + ")")
    stampa_store(lm.session_context_store, "Contesto", session_id=session)

    separatore("ENTITA'   (persone, progetti, sistemi)")
    entita = leggi_entita(lm, user_id=user, query=query)
    if not entita:
        UI.line("Nessuna entita' registrata.", style="ares.muted")
    for e in entita:
        UI.lines(righe_entita(e))

    separatore("INTUIZIONI APPRESE   (indice vettoriale LanceDB)")
    intuizioni = leggi_intuizioni(lm, user_id=user, query=query)
    if not intuizioni:
        UI.line("Nessuna intuizione salvata.", style="ares.muted")
    for k in intuizioni:
        UI.line("- " + str(getattr(k, "title", "?")), style="ares.cyan")
        UI.line("    " + str(getattr(k, "learning", "")))

    separatore("FILE DELL'AGENTE   (scritti da lui, verbatim)")
    elenco = fs.list()
    if not elenco:
        UI.line("Nessun file.", style="ares.muted")
    else:
        UI.table(
            ("file", ("dimensione", "ares.text", "right")),
            ((str(f.path), byte_leggibili(f.size_bytes)) for f in elenco),
        )
    UI.blank()
    UI.line("Per leggerne uno: " + config.comando_ares("inspect", "--file", "<percorso>"), style="ares.muted")


@app.default
def ispeziona(
    *,
    user: str = config.DEFAULT_USER_ID,
    session: str | None = None,
    query: str = "",
    file: str | None = None,
    prompt: bool = False,
) -> None:
    """Profilo, memorie, contesto, entita', intuizioni e file dell'agente.

    Args:
        user: identificativo dell'utente.
        session: sessione di cui mostrare il contesto; senza, l'ultima toccata.
        query: filtra entita' e intuizioni; le intuizioni per somiglianza.
        file: stampa solo il contenuto di questo file del quaderno privato.
        prompt: stampa solo il system message che la chat manderebbe al modello da questa cartella.
    """
    try:
        with lock_stato(esclusivo=False):
            _ispeziona(user, session, query, file, prompt)
    except StatoOccupato as errore:
        UI.err("Impossibile leggere lo stato di Ares: " + str(errore))
        UI.err("Attendi che backup o restore terminino e riprova.", style="ares.muted")


def main() -> None:
    from ares.cli.app import esegui

    esegui("inspect")


if __name__ == "__main__":
    main()
