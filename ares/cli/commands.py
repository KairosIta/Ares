"""Comandi locali della REPL, derivati da un'unica tabella."""

import difflib
from dataclasses import dataclass
from typing import Any

from ares import config
from ares.agent.assistant import build_assistant, build_filesystem
from ares.cli import cartella
from ares.cli.ui import UI, byte_leggibili
from ares.state.stores import leggi_entita, leggi_sessioni, righe_entita, righe_sessione, stampa_store


@dataclass
class StatoChat:
    """Cio' che la REPL tiene fra un turno e l'altro, e che un comando puo' cambiare.

    Prima i comandi ricevevano agente, sessione e utente come tre argomenti
    e non potevano toccare niente: `/debug`, `/metriche` e `/sessione` hanno
    bisogno di scrivere, e il ciclo della chat di rileggere. Un oggetto solo,
    mutabile, e' il modo piu' corto di dirlo.
    """

    agent: Any
    session_id: str
    user_id: str
    debug: bool = False
    metriche: bool = False


# ---------------------------------------------------------------------------
# I comandi
# ---------------------------------------------------------------------------
#
# Ogni comando e' una funzione con la stessa firma, anche quando non usa tutti
# gli argomenti: e' il prezzo di avere una tabella invece di una catena di if,
# e la tabella e' cio' che tiene allineati aiuto, completamento e abbreviazioni.
# `argomento` e' tutto cio' che segue il primo spazio. Lo leggono `/entita`,
# `/sessioni` e `/sessione`; per gli altri resta vuoto, e un argomento passato
# a un comando che non lo usa viene ignorato in silenzio.
#
# Chi restituisce False chiude la sessione. Gli altri non restituiscono niente.


def _comando_aiuto(stato: StatoChat, argomento: str):
    stampa_aiuto()


def _comando_profilo(stato: StatoChat, argomento: str):
    stampa_store(stato.agent.learning_machine.user_profile_store, "Profilo", user_id=stato.user_id)


def _comando_memorie(stato: StatoChat, argomento: str):
    stampa_store(stato.agent.learning_machine.user_memory_store, "Memorie", user_id=stato.user_id)


def _comando_contesto(stato: StatoChat, argomento: str):
    stampa_store(stato.agent.learning_machine.session_context_store, "Contesto", session_id=stato.session_id)


def _comando_sessioni(stato: StatoChat, argomento: str):
    UI.heading("Sessioni")
    sessioni = leggi_sessioni(stato.agent, user_id=stato.user_id, query=argomento)
    mostrate = sessioni[: config.SESSIONI_ELENCO]
    for s in mostrate:
        for riga in righe_sessione(s, corrente=(getattr(s, "session_id", None) == stato.session_id)):
            UI.line(riga)
    if not mostrate:
        if argomento:
            UI.line("Nessuna sessione il cui nome contenga '" + argomento + "'.", style="ares.muted")
        else:
            UI.line("Nessuna sessione in archivio.", style="ares.muted")
    nascoste = len(sessioni) - len(mostrate)
    if nascoste:
        UI.line("(altre " + str(nascoste) + ": /sessioni <testo> filtra per nome)", style="ares.muted")
    if not argomento and all(getattr(s, "session_id", None) != stato.session_id for s in sessioni):
        # La sessione in corso entra in archivio col primo turno salvato.
        # Prima di allora manca dall'elenco, e un'assenza non spiegata si
        # legge come un difetto.
        #
        # Si guarda l'elenco intero e solo senza filtro, perche' altrimenti la
        # frase e' falsa in due casi raggiungibili: sotto un filtro che la
        # esclude, e quando e' in archivio ma oltre il tetto. In tutti e due
        # la sessione c'e', e dire che manca e' peggio del silenzio.
        UI.line(
            "Questa sessione (" + stato.session_id + ") compare qui dal primo turno salvato.",
            style="ares.muted",
        )


def _comando_sessione(stato: StatoChat, argomento: str):
    """Mostra la sessione corrente o passa a un'altra senza riavviare.

    Passare vuol dire ricostruire l'agente: la sessione e' fissata alla sua
    costruzione, e il contesto appreso - obiettivo, piano, avanzamento - e'
    per sessione. Profilo e memorie sono per utente e restano gli stessi.
    """
    if not argomento:
        UI.pair("Sessione corrente", stato.session_id)
        UI.line("/sessione <nome> passa a un'altra; /sessioni le elenca.", style="ares.muted")
        return
    nome = argomento.split()[0]
    if nome == stato.session_id:
        UI.line("Sei gia' nella sessione '" + nome + "'.", style="ares.muted")
        return
    stato.agent = build_assistant(user_id=stato.user_id, session_id=nome, debug=stato.debug)
    stato.session_id = nome
    UI.pair("Sessione", nome, style="ares.title")
    UI.line("Il contesto e' quello di questa sessione; profilo e memorie non cambiano.", style="ares.muted")


def _comando_debug(stato: StatoChat, argomento: str):
    """Accende o spegne le chiamate al modello a schermo, per il resto della sessione."""
    stato.debug = not stato.debug
    # Le due leve che `--debug` muove all'avvio: il livello dei log di Agno e
    # la modalita' dell'agente, che decide se stampare i propri passaggi.
    from ares.cli.chat import configura_log_agno

    configura_log_agno(stato.debug)
    if hasattr(stato.agent, "debug_mode"):
        stato.agent.debug_mode = stato.debug
    UI.pair("Debug", "acceso" if stato.debug else "spento", style="ares.title" if stato.debug else "ares.muted")


def _comando_metriche(stato: StatoChat, argomento: str):
    """Accende o spegne la riga del costo sotto ogni risposta."""
    stato.metriche = not stato.metriche
    stile = "ares.title" if stato.metriche else "ares.muted"
    UI.pair("Metriche", "accese" if stato.metriche else "spente", style=stile)
    if stato.metriche:
        UI.line("Sotto ogni risposta: finestra occupata, token, secondi.", style="ares.muted")


def _comando_entita(stato: StatoChat, argomento: str):
    UI.heading("Entita'")
    entita = leggi_entita(stato.agent.learning_machine, user_id=stato.user_id, query=argomento)
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


def _comando_file(stato: StatoChat, argomento: str):
    UI.heading("Quaderno privato")
    fs = build_filesystem(stato.user_id)
    elenco = fs.list()
    if not elenco:
        UI.line("Nessun file.", style="ares.muted")
        return
    UI.table(("file", ("byte", "ares.text", "right")), ((str(f.path), byte_leggibili(f.size_bytes)) for f in elenco))


def _comando_cartella(stato: StatoChat, argomento: str):
    """Dove Ares sta lavorando, e in che stato e' il progetto.

    Serve prima di dire si' a un comando shell: il percorso, il ramo, quanti
    file sono gia' modificati, se c'e' un ARES.md che il modello sta seguendo
    e gli stessi avvisi dell'avvio, perche' una cartella rischiosa lo resta
    anche dopo la conferma.
    """
    UI.heading("Cartella di lavoro")
    if not config.WORKSPACE:
        UI.line("Lo spazio di lavoro e' spento in config.py.", style="ares.muted")
        return
    radice = config.WORKSPACE_DIR
    UI.pair("percorso", str(radice), style="ares.cyan")
    ramo = cartella.ramo_git(radice)
    if ramo:
        modificati = cartella.file_modificati(radice)
        if modificati is None:
            stato_git = "stato non leggibile"
        elif modificati:
            stato_git = str(modificati) + (" file modificato" if modificati == 1 else " file modificati")
        else:
            stato_git = "pulito"
        UI.pair("git", ramo + ", " + stato_git)
    else:
        UI.pair("git", "non e' un repository", style="ares.muted")
    if cartella.file_istruzioni(radice).is_file():
        UI.pair("istruzioni", config.WORKSPACE_ISTRUZIONI + ", letto all'avvio")
    else:
        UI.pair(
            "istruzioni", "nessun " + config.WORKSPACE_ISTRUZIONI + "; `ares init` ne scrive uno", style="ares.muted"
        )
    for motivo in cartella.rischi(radice):
        UI.line("attenzione: la cartella " + motivo, style="ares.warning")


def _comando_esci(stato: StatoChat, argomento: str):
    return False


# Un elenco solo. Prima ce n'erano due - la catena di if e la stringa
# dell'aiuto - e avevano gia' divergito: `/lavoro` esisteva da giorni e
# nell'aiuto non compariva. Da qui si derivano l'aiuto, i candidati del TAB,
# le abbreviazioni e i suggerimenti sui refusi: quattro cose che non possono
# piu' contraddirsi.
#
# Gli alias restano fuori dall'elenco a schermo e dal TAB, ma si scrivono e si
# abbreviano come gli altri: sono superstiti inglesi, o il nome che un comando
# aveva prima, non comandi da imparare.
#
# `/sessione` e `/sessioni` condividono il prefisso fino all'ultima lettera:
# `/sess` e' ambiguo e lo resta di proposito, perche' uno elenca e l'altro
# cambia sessione. Il TAB li mostra entrambi.
COMANDI = (
    ("/aiuto", ("/?",), "questo elenco", _comando_aiuto),
    ("/profilo", (), "il profilo utente accumulato", _comando_profilo),
    ("/memorie", (), "le memorie non strutturate", _comando_memorie),
    ("/contesto", (), "obiettivo e avanzamento della sessione", _comando_contesto),
    ("/sessioni", (), "le conversazioni in archivio; /sessioni <testo> filtra", _comando_sessioni),
    ("/sessione", (), "la sessione corrente; /sessione <nome> passa a un'altra", _comando_sessione),
    ("/entita", (), "le entita' registrate; /entita <testo> cerca fra loro", _comando_entita),
    ("/file", (), "i file scritti dall'agente", _comando_file),
    ("/cartella", ("/lavoro",), "la cartella di lavoro: percorso, git, ARES.md", _comando_cartella),
    ("/metriche", (), "accende o spegne il costo di ogni turno", _comando_metriche),
    ("/debug", (), "accende o spegne le chiamate al modello a schermo", _comando_debug),
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


def gestisci_comando(comando: str, stato: StatoChat) -> bool:
    """Esegue un comando locale. Ritorna False se la sessione deve terminare."""
    nome, _, argomento = comando.partition(" ")
    voce, righe = risolvi_comando(nome)
    if voce is None:
        UI.command_problem(righe)
        return True
    return voce[3](stato, argomento.strip()) is not False
