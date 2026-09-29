"""Ispezione di cio' che l'agente ha imparato
==========================================

Uso:
    ares inspect
    ares inspect --session test_1
    ares inspect --file notes/setup.md
    ares inspect --prompt

Legge gli archivi senza avviare il modello conversazionale e senza scrivere
negli store: dove sta un'informazione, e verra' ritrovata? Due eccezioni:
crea la directory dello stato se manca, e la ricerca fra le intuizioni usa
l'embedder locale.

`--prompt` stampa il system message intero che Agno comporrebbe per un
turno in questa cartella, senza aprire il turno: e' li' che si giudica una
modifica ai prompt.
"""

from collections.abc import Sequence
from pathlib import Path

from ares import config
from ares.cli.comando import ESITO_FATTO, ESITO_GUASTO, ESITO_OCCUPATO, ESITO_RIFIUTO, nuova_app
from ares.cli.ui import UI, byte_leggibili
from ares.config import Impostazioni, Percorsi, Politica
from ares.core.stato import StatoDaMigrare, stato_in_uso
from ares.ops.migrazione import righe_avviso
from ares.state.identita import Utente, UtenteNonValido
from ares.state.lock import StatoOccupato

app = nuova_app("inspect", "Ispeziona gli archivi di apprendimento senza toccarli")


def separatore(titolo: str) -> None:
    UI.blank()
    UI.heading(titolo)


def _ispeziona(
    percorsi: Percorsi,
    impostazioni: Impostazioni,
    politica: Politica,
    utente: Utente,
    session: str | None,
    query: str,
    file: str | None,
    prompt: bool,
    modo: str,
) -> None:
    from ares.agent.assistant import build_assistant
    from ares.agent.prompts import messaggio_di_sistema
    from ares.cli.log import configura_log_agno
    from ares.cli.ui import stampa_store
    from ares.core.id_sessione import nuovo_id_sessione
    from ares.state.archivi import build_filesystem
    from ares.state.stores import leggi_entita, leggi_intuizioni, righe_entita

    # Qui e non prima: `--help` esce dentro Cyclopts, e un comando che stampa
    # l'aiuto non deve creare l'archivio che dice di ispezionare.
    config.prepara_archivio(percorsi)

    fs = build_filesystem(percorsi, utente)

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
        # Stampato verbatim, per poterlo confrontare con `diff`. Il log INFO
        # di Agno andrebbe su stdout ("Creating table" su un archivio nuovo):
        # si spegne, warning ed errori restano.
        configura_log_agno(False)
        session = session or nuovo_id_sessione(Path.cwd())
        agent = build_assistant(percorsi, impostazioni, politica, utente, session_id=session, modo=modo)
        print(messaggio_di_sistema(agent, session_id=session, utente=utente))
        return

    agent = build_assistant(percorsi, impostazioni, politica, utente, session_id=session or "principale")
    if not session:
        # Senza `--session` si guarda l'ultima conversazione toccata, di
        # qualunque cartella: e' quella di cui si vuole sapere cosa e' rimasto.
        from ares.state.stores import leggi_sessioni

        recenti = leggi_sessioni(agent, utente=utente)
        session = str(recenti[0].session_id) if recenti else "principale"
    lm = agent.learning_machine
    # La macchina c'e' sempre; i singoli store spenti sono None e li
    # segnala `stampa_store`.
    assert lm is not None

    separatore("PROFILO UTENTE   (per utente, sopravvive a ogni sessione)")
    stampa_store(lm.user_profile_store, "Profilo", user_id=utente.id)

    separatore("MEMORIE   (osservazioni non strutturate, per utente)")
    stampa_store(lm.user_memory_store, "Memorie", user_id=utente.id)

    separatore("CONTESTO DI SESSIONE   (sessione: " + session + ")")
    stampa_store(lm.session_context_store, "Contesto", session_id=session)

    separatore("ENTITA'   (persone, progetti, sistemi)")
    entita = leggi_entita(lm, utente, query=query)
    if not entita:
        UI.line("Nessuna entita' registrata.", style="ares.muted")
    for e in entita:
        UI.lines(righe_entita(e))

    separatore("INTUIZIONI APPRESE   (indice vettoriale LanceDB)")
    intuizioni = leggi_intuizioni(lm, utente, query=query)
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
    modo: config.Modo = config.MODO_PREDEFINITO,
) -> int:
    """Profilo, memorie, contesto, entita', intuizioni e file dell'agente.

    Args:
        user: identificativo dell'utente.
        session: sessione di cui mostrare il contesto; senza, l'ultima toccata.
        query: filtra entita' e intuizioni; le intuizioni per somiglianza.
        file: stampa solo il contenuto di questo file del quaderno privato.
        prompt: stampa solo il system message che la chat manderebbe al modello da questa cartella.
        modo: con --prompt, la modalita' del prompt da stampare: manuale, modifiche, piano o auto.
    """
    # La stessa forma canonica della chat, o l'archivio sembrerebbe vuoto.
    try:
        utente = Utente.da_grezzo(user)
    except UtenteNonValido as errore:
        UI.err("Rifiutato: " + str(errore))
        return ESITO_RIFIUTO
    percorsi = config.leggi_percorsi()
    impostazioni = config.leggi_impostazioni()
    politica = config.leggi_politica()
    try:
        with stato_in_uso(percorsi):
            _ispeziona(percorsi, impostazioni, politica, utente, session, query, file, prompt, modo)
    except StatoOccupato as errore:
        UI.err("Impossibile leggere lo stato di Ares: " + str(errore))
        UI.err("Attendi che backup o restore terminino e riprova.", style="ares.muted")
        return ESITO_OCCUPATO
    except StatoDaMigrare as errore:
        # Altrimenti mostrerebbe un archivio vuoto senza dire perche'.
        righe = righe_avviso(errore.parti)
        UI.err(righe[0], style="ares.warning")
        for riga in righe[1:]:
            UI.err(riga, style="ares.muted")
        return ESITO_GUASTO
    return ESITO_FATTO


def main(argv: Sequence[str] | None = None) -> int:
    from ares.cli.app import esegui

    return esegui("inspect", argv)


if __name__ == "__main__":
    raise SystemExit(main())
