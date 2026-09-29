"""Comandi locali della REPL, derivati da un'unica tabella."""

import difflib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from agno.agent import Agent
from agno.db.base import SessionType

from ares import config
from ares.agent.prompts import percorso_istruzioni
from ares.cli import cartella
from ares.cli.log import configura_log_agno
from ares.cli.ui import UI, byte_leggibili, stampa_store
from ares.config import Impostazioni, Percorsi, Politica
from ares.core.autorizzazioni import ModoNonAmmesso
from ares.core.session import SessioneAttiva, SessioneDiAltri, Sessioni
from ares.state.archivi import build_filesystem
from ares.state.git import ramo_git
from ares.state.identita import Utente
from ares.state.stores import (
    leggi_entita,
    leggi_sessioni,
    prima_domanda,
    quando_sessione,
    righe_entita,
    righe_sessione,
    testo_conversazione,
)


@dataclass
class StatoChat:
    """Cio' che la REPL tiene fra un turno e l'altro, e che un comando puo' cambiare.

    Percorsi, impostazioni e politica sono quelli della conversazione in
    corso: `/sessione` e `/modo` ricostruiscono l'agente con questi, non con
    cio' che il processo leggerebbe adesso (vedi "Dipendenze esplicite" in
    docs/architecture.md).
    """

    agent: Agent
    session_id: str
    utente: Utente
    percorsi: Percorsi
    impostazioni: Impostazioni
    politica: Politica
    debug: bool = False
    metriche: bool = False
    modo: str = config.MODO_PREDEFINITO
    # Falso senza nessuno che legga (`-p`, pipe): una ricostruzione dell'agente
    # deve ripassarlo, o riaccenderebbe l'apprendimento e le modalita' silenziose.
    presidiato: bool = True
    # Token del prompt dell'ultimo turno, per la barra sotto il prompt.
    finestra: int | None = None


class Comando(NamedTuple):
    """Una voce della tabella: il nome canonico, gli alias, l'aiuto e la funzione.

    La funzione riceve lo stato e l'argomento; `False` chiude la sessione.
    """

    nome: str
    alias: tuple[str, ...]
    descrizione: str
    funzione: Callable[[StatoChat, str], bool | None]


# ---------------------------------------------------------------------------
# I comandi
# ---------------------------------------------------------------------------
#
# Ogni comando ha la stessa firma, per stare nella tabella `COMANDI`.
# `argomento` e' cio' che segue il primo spazio; chi non lo usa lo ignora.
# Chi restituisce False chiude la sessione.


def _sessioni(stato: StatoChat) -> Sessioni:
    """Il servizio di sessione con la configurazione di questa conversazione."""
    return Sessioni(
        stato.percorsi,
        stato.impostazioni,
        stato.politica,
        stato.utente,
        presidiato=stato.presidiato,
        debug=stato.debug,
    )


def _comando_aiuto(stato: StatoChat, argomento: str) -> None:
    stampa_aiuto()


def _store(stato: StatoChat, nome: str) -> object | None:
    """Uno store della LearningMachine, o None se manca lei o manca lui: `stampa_store` lo dice."""
    return getattr(stato.agent.learning_machine, nome, None)


def _comando_profilo(stato: StatoChat, argomento: str) -> None:
    stampa_store(_store(stato, "user_profile_store"), "Profilo", user_id=stato.utente.id)


def _comando_memorie(stato: StatoChat, argomento: str) -> None:
    stampa_store(_store(stato, "user_memory_store"), "Memorie", user_id=stato.utente.id)


def _comando_contesto(stato: StatoChat, argomento: str) -> None:
    stampa_store(_store(stato, "session_context_store"), "Contesto", session_id=stato.session_id)


def _comando_sessioni(stato: StatoChat, argomento: str) -> None:
    """Le conversazioni di questa cartella; `tutte` allarga a ogni cartella.

    Quelle senza cartella registrata compaiono sempre.
    """
    parole = argomento.split()
    tutte = bool(parole) and parole[0].casefold() == "tutte"
    if tutte:
        parole = parole[1:]
    argomento = " ".join(parole)
    qui = stato.percorsi.lavoro if stato.politica.workspace.attivo and not tutte else None
    UI.heading("Sessioni" if qui is None else "Sessioni di questa cartella")
    sessioni = leggi_sessioni(stato.agent, stato.utente, query=argomento, cartella=qui)
    mostrate = sessioni[: stato.politica.mostra.sessioni]
    for s in mostrate:
        corrente = getattr(s, "session_id", None) == stato.session_id
        for riga in righe_sessione(s, corrente=corrente, con_cartella=tutte):
            UI.line(riga)
    if not mostrate:
        if argomento:
            UI.line("Nessuna sessione il cui nome contenga '" + argomento + "'.", style="ares.muted")
        else:
            UI.line("Nessuna sessione in archivio.", style="ares.muted")
    nascoste = len(sessioni) - len(mostrate)
    if nascoste:
        UI.line("(altre " + str(nascoste) + ": /sessioni <testo> filtra per nome)", style="ares.muted")
    if qui is not None:
        UI.line("(/sessioni tutte mostra anche quelle nate in altre cartelle)", style="ares.muted")
    if not argomento and all(getattr(s, "session_id", None) != stato.session_id for s in sessioni):
        # La sessione entra in archivio col primo turno salvato: lo si spiega.
        # Solo senza filtro e sull'elenco intero, altrimenti la frase sarebbe
        # falsa per una sessione filtrata o oltre il tetto.
        UI.line(
            "Questa sessione (" + stato.session_id + ") compare qui dal primo turno salvato.",
            style="ares.muted",
        )


def _comando_sessione(stato: StatoChat, argomento: str) -> None:
    """Mostra la sessione corrente o passa a un'altra senza riavviare.

    Passare ricostruisce l'agente, perche' la sessione e' fissata alla sua
    costruzione. Profilo e memorie sono per utente e restano gli stessi.
    """
    if not argomento:
        UI.pair("Sessione corrente", stato.session_id)
        UI.line(
            "/sessione <nome> passa a un'altra, /sessione nuova ne apre una; /sessioni le elenca.", style="ares.muted"
        )
        return
    nome = argomento.split()[0]
    nuova = nome == "nuova"
    if nuova:
        # `nuova` e' riservata: una sessione con quel nome si apre solo con
        # `ares --session nuova`.
        if not stato.politica.workspace.attivo:
            UI.line("Senza cartella di lavoro il nome lo scegli tu: /sessione <nome>.", style="ares.warning")
            return
        nome = _sessioni(stato).id_nuovo()
    elif nome == stato.session_id:
        UI.line("Sei gia' nella sessione '" + nome + "'.", style="ares.muted")
        return
    try:
        attiva = _sessioni(stato).apri(nome, modo=stato.modo)
    except SessioneDiAltri:
        UI.line("La sessione '" + nome + "' appartiene a un altro utente: non si apre.", style="ares.error")
        return
    stato.agent, stato.session_id = attiva.agente, attiva.id
    UI.pair("Sessione", nome + ("  (nuova)" if nuova else ""), style="ares.title")
    if nuova:
        UI.line("Contesto vuoto; profilo e memorie non cambiano.", style="ares.muted")
    else:
        UI.line("Il contesto e' quello di questa sessione; profilo e memorie non cambiano.", style="ares.muted")


def modo_senza_terminale(modo: str) -> str:
    """Il rifiuto di una modalita' che scriverebbe in silenzio senza nessuno davanti."""
    return "La modalita' " + modo + " richiede un terminale: scriverebbe o eseguirebbe senza che nessuno guardi."


def _comando_modo(stato: StatoChat, argomento: str) -> None:
    """Mostra la modalita' corrente o ne sceglie un'altra, ricostruendo l'agente sulla stessa sessione.

    Le liste degli strumenti sono fissate alla costruzione dello spazio di
    lavoro e descritte nel prompt: per cambiarle si rifa' l'agente.
    """
    if not argomento:
        # `auto` e' l'unica modalita' che ha tutti gli strumenti.
        tutti = config.MODALITA["auto"][0]
        UI.pair("Modalita' corrente", stato.modo)
        righe = []
        for nome, (silenziosi, confermati) in config.MODALITA.items():
            assenti = [a for a in tutti if a not in silenziosi and a not in confermati]
            righe.append(
                (
                    nome + ("  (questa)" if nome == stato.modo else ""),
                    ", ".join(silenziosi),
                    ", ".join(confermati) or "niente",
                    ", ".join(assenti) or "nessuno",
                )
            )
        UI.table(("modalita'", "da soli", "con conferma", "assenti"), righe)
        UI.line("/modo <nome> cambia; auto non si sceglie da qui ma con `ares --modo auto`.", style="ares.muted")
        return
    nome = argomento.split()[0]
    if nome == stato.modo:
        UI.line("Sei gia' in modalita' '" + nome + "'.", style="ares.muted")
        return
    try:
        attiva = _sessioni(stato).cambia_modo(SessioneAttiva(stato.session_id, stato.modo, stato.agent), nome)
    except ModoNonAmmesso as errore:
        if errore.motivo == "avvio":
            UI.line("La modalita' auto si sceglie solo all'avvio, con `ares --modo auto`.", style="ares.warning")
        else:
            UI.line(modo_senza_terminale(nome), style="ares.error")
        return
    except ValueError as errore:
        UI.line(str(errore), style="ares.error")
        return
    stato.agent, stato.modo = attiva.agente, attiva.modo
    UI.pair("Modalita'", nome, style="ares.title")
    UI.line("Stessa sessione, strumenti e prompt della modalita' nuova.", style="ares.muted")


def _comando_debug(stato: StatoChat, argomento: str) -> None:
    """Accende o spegne le chiamate al modello a schermo, per il resto della sessione."""
    stato.debug = not stato.debug
    # Le stesse due leve di `--debug`: log di Agno e `debug_mode` dell'agente.

    configura_log_agno(stato.debug)
    if hasattr(stato.agent, "debug_mode"):
        stato.agent.debug_mode = stato.debug
    UI.pair("Debug", "acceso" if stato.debug else "spento", style="ares.title" if stato.debug else "ares.muted")


def _comando_metriche(stato: StatoChat, argomento: str) -> None:
    """Accende o spegne la riga del costo sotto ogni risposta."""
    stato.metriche = not stato.metriche
    stile = "ares.title" if stato.metriche else "ares.muted"
    UI.pair("Metriche", "accese" if stato.metriche else "spente", style=stile)
    if stato.metriche:
        UI.line("Sotto ogni risposta: finestra occupata, token, secondi.", style="ares.muted")


def _comando_entita(stato: StatoChat, argomento: str) -> None:
    UI.heading("Entita'")
    entita = leggi_entita(stato.agent.learning_machine, stato.utente, query=argomento)
    if not entita:
        if argomento:
            # La ricerca e' testuale, non semantica: lo si dice, perche' un
            # risultato vuoto sembrerebbe un archivio vuoto.
            UI.line("Nessuna entita' per '" + argomento + "'.", style="ares.muted")
            UI.line("La ricerca e' testuale: prova una parola che ci sia scritta dentro,", style="ares.muted")
            UI.line("o /entita senza argomento per l'elenco intero.", style="ares.muted")
        else:
            UI.line("Nessuna entita' registrata.", style="ares.muted")
        return
    for e in entita:
        for riga in righe_entita(e):
            UI.line(riga)


def _comando_file(stato: StatoChat, argomento: str) -> None:
    UI.heading("Quaderno privato")
    fs = build_filesystem(stato.percorsi, stato.utente)
    elenco = fs.list()
    if not elenco:
        UI.line("Nessun file.", style="ares.muted")
        return
    UI.table(("file", ("byte", "ares.text", "right")), ((str(f.path), byte_leggibili(f.size_bytes)) for f in elenco))


def _comando_cartella(stato: StatoChat, argomento: str) -> None:
    """Dove Ares sta lavorando, e in che stato e' il progetto.

    Percorso, ramo, file modificati, `ARES.md` e gli stessi avvisi dell'avvio:
    serve prima di autorizzare un comando shell.
    """
    UI.heading("Cartella di lavoro")
    if not stato.politica.workspace.attivo:
        UI.line("Lo spazio di lavoro e' spento in config.py.", style="ares.muted")
        return
    radice = stato.percorsi.lavoro
    UI.pair("percorso", str(radice), style="ares.cyan")
    ramo = ramo_git(radice)
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
    if percorso_istruzioni(radice, stato.politica.workspace.istruzioni) is not None:
        UI.pair("istruzioni", stato.politica.workspace.istruzioni + ", letto all'avvio")
    else:
        UI.pair(
            "istruzioni",
            "nessun " + stato.politica.workspace.istruzioni + "; `ares init` ne scrive uno",
            style="ares.muted",
        )
    for motivo in cartella.rischi(radice, stato.percorsi):
        UI.line("attenzione: la cartella " + motivo, style="ares.warning")


def _comando_esporta(stato: StatoChat, argomento: str) -> None:
    """La conversazione corrente in un file Markdown.

    Senza argomento il file prende il nome della sessione nella cartella di
    lavoro, e non sovrascrive. Con un percorso esplicito sovrascrive, e lo dice.
    """
    db = getattr(stato.agent, "db", None)
    sessione = None
    if db is not None:
        sessione = db.get_session(session_id=stato.session_id, session_type=SessionType.AGENT, user_id=stato.utente.id)
    scambi = len(getattr(sessione, "runs", None) or [])
    if not scambi:
        UI.line("Niente da esportare: la sessione non ha ancora turni salvati.", style="ares.muted")
        return
    # Un percorso relativo parte dalla cartella di lavoro, non da quella del processo.
    radice = stato.percorsi.lavoro if stato.politica.workspace.attivo else Path.cwd()
    esplicito = bool(argomento)
    if esplicito:
        destinazione = radice / Path(argomento.split()[0]).expanduser()
    else:
        destinazione = radice / (stato.session_id + ".md")
    if destinazione.exists() and not esplicito:
        UI.line(str(destinazione) + " esiste gia': /esporta <file> per scegliere il nome.", style="ares.error")
        return
    esisteva = destinazione.exists()
    try:
        destinazione.write_text(testo_conversazione(sessione, modello=stato.impostazioni.principale), encoding="utf-8")
    except OSError as errore:
        UI.line("Impossibile scrivere " + str(destinazione) + ": " + str(errore), style="ares.error")
        return
    UI.pair("Esportata" if not esisteva else "Sovrascritta", str(destinazione), style="ares.title")
    UI.line(
        str(scambi) + (" scambio" if scambi == 1 else " scambi") + " in Markdown: Tu e Ares, a turni.",
        style="ares.muted",
    )


def _comando_esci(stato: StatoChat, argomento: str) -> bool:
    return False


# L'unico elenco dei comandi: da qui derivano aiuto, TAB, abbreviazioni e
# suggerimenti sui refusi. Gli alias funzionano ma non compaiono nell'aiuto
# ne' nel TAB. `/sess` resta ambiguo di proposito fra `/sessione` e `/sessioni`.
COMANDI: tuple[Comando, ...] = (
    Comando("/aiuto", ("/?",), "questo elenco", _comando_aiuto),
    Comando("/profilo", (), "il profilo utente accumulato", _comando_profilo),
    Comando("/memorie", (), "le memorie non strutturate", _comando_memorie),
    Comando("/contesto", (), "obiettivo e avanzamento della sessione", _comando_contesto),
    Comando("/sessioni", (), "le conversazioni di questa cartella; <testo> filtra, `tutte` allarga", _comando_sessioni),
    Comando(
        "/sessione",
        (),
        "la sessione corrente; /sessione <nome> passa a un'altra, `nuova` ne apre una",
        _comando_sessione,
    ),
    Comando("/entita", (), "le entita' registrate; /entita <testo> cerca fra loro", _comando_entita),
    Comando("/file", (), "i file scritti dall'agente", _comando_file),
    Comando("/cartella", ("/lavoro",), "la cartella di lavoro: percorso, git, ARES.md", _comando_cartella),
    Comando("/modo", (), "la modalita' corrente; /modo <nome> passa a manuale, modifiche o piano", _comando_modo),
    Comando("/metriche", (), "accende o spegne il costo di ogni turno", _comando_metriche),
    Comando("/debug", (), "accende o spegne le chiamate al modello a schermo", _comando_debug),
    Comando(
        "/esporta", (), "scrive la conversazione in un file Markdown; /esporta <file> sceglie il nome", _comando_esporta
    ),
    Comando("/esci", ("/quit", "/exit"), "termina la sessione", _comando_esci),
)


def nomi_comandi() -> list[str]:
    return [voce.nome for voce in COMANDI]


def _candidati_modo() -> list[tuple[str, str]]:
    """Le modalita' che `/modo` accetta, descritte come nel suo elenco."""
    voci = []
    for nome, (silenziosi, confermati) in config.MODALITA.items():
        if nome == "auto":
            continue
        descrizione = "da soli: " + ", ".join(silenziosi)
        if confermati:
            descrizione += "; con conferma: " + ", ".join(confermati)
        voci.append((nome, descrizione))
    return voci


def _candidati_sessione(stato: StatoChat) -> list[tuple[str, str]]:
    """`nuova` e le conversazioni di questa cartella, la corrente esclusa."""
    voci = [("nuova", "una conversazione nuova in questa cartella")]
    qui = stato.percorsi.lavoro if stato.politica.workspace.attivo else None
    for s in leggi_sessioni(stato.agent, stato.utente, cartella=qui)[: stato.politica.mostra.sessioni]:
        nome = str(getattr(s, "session_id", "") or "")
        if not nome or nome == stato.session_id:
            continue
        descrizione = quando_sessione(s)
        inizio = prima_domanda(s, larghezza=50)
        if inizio:
            descrizione += "  " + inizio
        voci.append((nome, descrizione))
    return voci


def candidati_argomento(stato: StatoChat) -> dict[str, Callable[[], list[tuple[str, str]]]]:
    """Cosa il TAB propone dopo lo spazio, per i comandi che hanno un argomento.

    Le chiusure leggono `stato` al momento della chiamata, non ora: dopo
    `/sessione x` la corrente e' un'altra, e l'elenco deve saperlo.
    """
    return {"/modo": _candidati_modo, "/sessione": lambda: _candidati_sessione(stato)}


def stampa_aiuto() -> None:
    UI.help((voce.nome, voce.descrizione) for voce in COMANDI)


def risolvi_comando(nome: str) -> tuple[Comando | None, list[str]]:
    """Trova la voce che l'utente intendeva. Ritorna (voce, righe da stampare).

    L'ordine conta: nome esatto o alias, poi prefisso se resta una voce sola,
    infine i suggerimenti sui refusi, che sono l'ipotesi piu' debole.
    """
    for voce in COMANDI:
        if nome == voce.nome or nome in voce.alias:
            return voce, []

    candidati = [
        voce for voce in COMANDI if voce.nome.startswith(nome) or any(alias.startswith(nome) for alias in voce.alias)
    ]
    if len(candidati) == 1:
        return candidati[0], []
    if candidati:
        # Ambiguo: si mostrano i candidati, non si indovina (`/e` potrebbe essere `/esci`).
        return None, ["Comando incompleto: " + "  ".join(voce.nome for voce in candidati)]

    # Cutoff alto: un suggerimento sbagliato costa piu' di nessun suggerimento.
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
    return voce.funzione(stato, argomento.strip()) is not False
