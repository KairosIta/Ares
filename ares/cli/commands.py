"""Comandi locali della REPL, derivati da un'unica tabella."""

import difflib

from ares import config
from ares.agent.assistant import build_filesystem
from ares.cli.ui import UI
from ares.state.stores import leggi_entita, leggi_sessioni, righe_entita, righe_sessione, stampa_store

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
