"""
Ispezione di cio' che l'agente ha imparato
==========================================
Uso:
    ares inspect
    ares inspect --session test_1
    ares inspect --file notes/setup.md

Legge gli archivi senza avviare il modello conversazionale e non scrive
negli store. Due cose vanno dette per intero: come ogni comando che apre
l'archivio, crea la directory dello stato se manca, cosi' su un clone nuovo
lascia una `tmp/` vuota; e la ricerca fra le intuizioni vettorizza la query
con l'embedder locale, che e' l'unica inferenza di questo comando e l'unico
momento in cui un modello entra in memoria. Serve a rispondere alla domanda
che conta quando un agente dice di ricordare: dove sta questa informazione,
e la ritrovera' davvero?
"""

from ares import config
from ares.cli.comando import nuova_app
from ares.state.lock import StatoOccupato, lock_stato

app = nuova_app("inspect", "Ispeziona gli archivi di apprendimento senza toccarli")


def separatore(titolo: str) -> None:
    print()
    print("=" * 70)
    print(titolo)
    print("=" * 70)


def _ispeziona(user: str, session: str, query: str, file: str | None) -> None:
    from ares.agent.assistant import build_assistant, build_filesystem
    from ares.state.stores import leggi_entita, leggi_intuizioni, righe_entita, stampa_store

    # Qui e non prima: `--help` esce dentro Cyclopts, e un comando che stampa
    # l'aiuto non deve creare l'archivio che dice di ispezionare.
    config.prepara_archivio()

    fs = build_filesystem(user)

    if file:
        contenuto = fs.read(file)
        if contenuto is None:
            print("Nessun file a questo percorso:", file)
        else:
            print(contenuto)
        return

    agent = build_assistant(user_id=user, session_id=session)
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
        print("Nessuna entita' registrata.")
    for e in entita:
        for riga in righe_entita(e):
            print(riga)

    separatore("INTUIZIONI APPRESE   (indice vettoriale LanceDB)")
    intuizioni = leggi_intuizioni(lm, user_id=user, query=query)
    if not intuizioni:
        print("Nessuna intuizione salvata.")
    for k in intuizioni:
        titolo = getattr(k, "title", "?")
        testo = getattr(k, "learning", "")
        print("-", titolo)
        print("   ", testo)

    separatore("FILE DELL'AGENTE   (scritti da lui, verbatim)")
    elenco = fs.list()
    if not elenco:
        print("Nessun file.")
    for f in elenco:
        print("-", f.path, "  ", f.size_bytes, "byte")
    print()
    print("Per leggerne uno:", config.comando_ares("inspect", "--file", "<percorso>"))


@app.default
def ispeziona(
    *,
    user: str = config.DEFAULT_USER_ID,
    session: str = "principale",
    query: str = "",
    file: str | None = None,
) -> None:
    """Profilo, memorie, contesto, entita', intuizioni e file dell'agente.

    Args:
        user: identificativo dell'utente.
        session: sessione di cui mostrare il contesto.
        query: filtra entita' e intuizioni; le intuizioni per somiglianza.
        file: stampa solo il contenuto di questo file del quaderno privato.
    """
    try:
        with lock_stato(esclusivo=False):
            _ispeziona(user, session, query, file)
    except StatoOccupato as errore:
        print("Impossibile leggere lo stato di Ares:", errore)
        print("Attendi che backup o restore terminino e riprova.")


def main() -> None:
    from ares.cli.app import esegui

    esegui("inspect")


if __name__ == "__main__":
    main()
