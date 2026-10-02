"""Presentazione degli eventi, delle conferme e delle metriche del turno."""

import difflib
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agno.run.agent import RunOutput

from ares.agent.marcatura import smarca
from ares.agent.turn_core import TurnEvent, TurnEventKind, consume_events
from ares.cli.editor import CliInput
from ares.cli.ui import UI
from ares.config import Impostazioni, Mostra, Politica
from ares.core.autorizzazioni import Decisione, Richiesta
from ares.core.regole import Regole

# Gli eventi che aprono un'attesa, con cio' che l'indicatore dice, e quelli
# che la chiudono.
ATTESE: dict[TurnEventKind, str] = {
    TurnEventKind.PROCESSING_STARTED: "Ares sta preparando il turno...",
    TurnEventKind.PRE_HOOK_STARTED: "Ares sta preparando il turno...",
    TurnEventKind.MODEL_STARTED: "Ares sta elaborando...",
    TurnEventKind.POST_HOOK_STARTED: "Ares sta aggiornando cio' che ricorda...",
    TurnEventKind.MEMORY_STARTED: "Ares sta aggiornando la memoria...",
    TurnEventKind.SUMMARY_STARTED: "Ares sta aggiornando il riepilogo...",
}
FINI_ATTESA = frozenset(
    {
        TurnEventKind.MODEL_COMPLETED,
        TurnEventKind.PRE_HOOK_COMPLETED,
        TurnEventKind.POST_HOOK_COMPLETED,
        TurnEventKind.MEMORY_COMPLETED,
        TurnEventKind.SUMMARY_COMPLETED,
    }
)


def _strumento_avviato(flusso, evento: TurnEvent, mostra: Mostra) -> None:
    flusso.activity_stopped()
    nome = getattr(evento.tool, "tool_name", None) or "?"
    flusso.tool_started(nome)
    flusso.activity_started(nome + " in esecuzione...")


def _strumento_concluso(flusso, evento: TurnEvent, mostra: Mostra) -> None:
    flusso.activity_stopped()
    # Uno strumento fallito emette Completed **e poi** Error, non l'uno o
    # l'altro. La guardia evita un esito riuscito prima dell'errore.
    if getattr(evento.tool, "tool_call_error", False):
        return
    if mostra.esito_strumenti:
        flusso.tool_result(righe_esito(evento.tool, mostra))
    if mostra.apprendimenti:
        scrittura = righe_scrittura(evento.tool)
        if scrittura:
            flusso.tool_result(scrittura)


def _strumento_fallito(flusso, evento: TurnEvent, mostra: Mostra) -> None:
    flusso.activity_stopped()
    errore = evento.error or getattr(evento.tool, "result", None) or ""
    if mostra.esito_strumenti:
        flusso.tool_result(righe_esito(evento.tool, mostra, errore=errore), errore=True)


def _contenuto(flusso, evento: TurnEvent, mostra: Mostra) -> None:
    if isinstance(evento.content, str):
        flusso.content(evento.content)


def _errore_run(flusso, evento: TurnEvent, mostra: Mostra) -> None:
    flusso.activity_stopped()
    flusso.run_error(evento.content)


def _annullato(flusso, evento: TurnEvent, mostra: Mostra) -> None:
    flusso.activity_stopped()
    flusso.cancelled()


def _chiuso(flusso, evento: TurnEvent, mostra: Mostra) -> None:
    # Una run, una continuazione o l'output conclusivo: il commit qui
    # garantisce che un'eventuale conferma venga dopo il testo prodotto.
    flusso.activity_stopped()
    flusso.flush()


# Ogni azione riceve anche le scelte di visualizzazione, anche se solo
# alcune le usano: e' la firma comune della tabella.
AZIONI: dict[TurnEventKind, Callable[[Any, TurnEvent, Mostra], None]] = {
    TurnEventKind.TOOL_STARTED: _strumento_avviato,
    TurnEventKind.TOOL_COMPLETED: _strumento_concluso,
    TurnEventKind.TOOL_ERROR: _strumento_fallito,
    TurnEventKind.CONTENT: _contenuto,
    TurnEventKind.RUN_ERROR: _errore_run,
    TurnEventKind.RUN_CANCELLED: _annullato,
    TurnEventKind.RUN_COMPLETED: _chiuso,
    TurnEventKind.RUN_PAUSED: _chiuso,
    TurnEventKind.OUTPUT: _chiuso,
}


def mostra_evento(flusso, evento: TurnEvent, mostra: Mostra) -> None:
    """Adatta un evento neutro del core ai componenti della CLI."""
    tipo = evento.kind
    if tipo in ATTESE:
        flusso.activity_started(ATTESE[tipo])
    elif tipo in FINI_ATTESA:
        flusso.activity_stopped()
    else:
        azione = AZIONI.get(tipo)
        if azione is not None:
            azione(flusso, evento, mostra)


def mostra_flusso(eventi, *, mostra: Mostra, ui=None) -> RunOutput | None:
    """Mostra uno stream di eventi del core e restituisce il suo output.

    Adapter per i test; il ciclo completo usa `mostra_evento` su un solo stream
    attraverso tutte le continuazioni.
    """
    renderer = UI if ui is None else ui
    with renderer.stream() as flusso:
        return consume_events(eventi, lambda evento: mostra_evento(flusso, evento, mostra))


def anteprima_risultato(testo: str, mostra: Mostra) -> list:
    """Le prime righe di un risultato, tagliate in altezza e in larghezza.

    Un esito e' output gia' avvenuto e puo' essere enorme, quindi qui si tronca
    (al contrario di `righe_argomento`). Il taglio si dichiara sempre; quanto
    tagliare lo dice la politica.
    """
    righe_testo = testo.splitlines()
    rese = []
    for riga in righe_testo[: mostra.esito_righe]:
        riga = riga.rstrip()
        if len(riga) > mostra.esito_larghezza:
            riga = riga[: mostra.esito_larghezza - 3] + "..."
        rese.append("   | " + riga)
    restanti = len(righe_testo) - len(rese)
    if restanti == 1:
        rese.append("   | (+ un'altra riga)")
    elif restanti > 1:
        rese.append("   | (+ altre " + str(restanti) + " righe)")
    return rese


def righe_esito(strumento, mostra: Mostra, errore=None) -> list:
    """Come e' finita una chiamata a uno strumento.

    Il conteggio dei caratteri precede l'anteprima: e' la parte esatta, quanto
    e' entrato nella finestra del modello, delimitatori compresi; l'anteprima
    li toglie, perche' alla persona servono le righe del contenuto. La durata
    manca nel percorso di ripresa dopo una conferma, dove Agno non copia le
    metriche: un segmento assente sparisce invece di stampare zero.
    """
    if errore is not None:
        testo = str(errore).strip()
        if not testo:
            return ["   errore: senza messaggio"]
        righe_errore = testo.splitlines()
        if len(righe_errore) == 1 and len(righe_errore[0]) <= mostra.esito_larghezza:
            return ["   errore: " + righe_errore[0]]
        return ["   errore:", *anteprima_risultato(testo, mostra)]

    risultato = getattr(strumento, "result", None)
    testo = "" if risultato is None else str(risultato)
    misura = str(len(testo)) + " caratteri" if testo else "nessun contenuto"
    durata = getattr(getattr(strumento, "metrics", None), "duration", None)
    if isinstance(durata, (int, float)) and durata > 0:
        # Sotto il decimo di secondo l'arrotondamento a una cifra scriverebbe
        # "in 0.0 s", che sembra un guasto del cronometro.
        misura += " in " + ("<0.1" if durata < 0.1 else str(round(durata, 1))) + " s"
    return ["   esito: " + misura, *anteprima_risultato(smarca(testo), mostra)]


# Gli strumenti con cui il modello scrive da solo nella memoria durevole.
# Il quaderno non c'e': non torna nel prompt, e `/file` lo mostra.
STRUMENTI_DI_MEMORIA = frozenset(
    {"save_learning", "remember_about", "link_entities", "forget", "update_user_memory", "update_profile"}
)


def righe_scrittura(strumento) -> list:
    """Cosa uno strumento di memoria ha ricevuto da scrivere, per intero.

    L'esito non dice il testo salvato, gli argomenti si': per questo non si
    tronca. Vuoto per ogni altro strumento.
    """
    nome = str(getattr(strumento, "tool_name", None) or "")
    if nome not in STRUMENTI_DI_MEMORIA:
        return []
    righe = ["   in memoria: " + nome]
    for chiave, valore in (getattr(strumento, "tool_args", None) or {}).items():
        if valore is None or valore == [] or valore == "":
            continue
        if isinstance(valore, (list, tuple)):
            testo = ", ".join(str(v) for v in valore)
        else:
            testo = " ".join(str(valore).split())
        righe.append("   | " + str(chiave) + ": " + testo)
    return righe


def righe_argomento(nome: str, valore) -> list:
    """Rende un singolo argomento in righe leggibili, senza mai troncarlo.

    Una lista di stringhe e' un comando, ricomposto con `shlex.join`: le
    virgolette mostrano dove finisce un argomento, che e' cio' che va guardato
    prima di autorizzare `['bash', '-lc', 'rm -rf .']`. Non si tronca perche'
    e' esattamente cio' che l'utente sta autorizzando.
    """
    if isinstance(valore, list) and all(isinstance(v, str) for v in valore):
        return [
            "   " + nome + ": " + shlex.join(valore),
            "   "
            + " " * len(nome)
            + "  (lista di "
            + str(len(valore))
            + " elementi, ricomposta qui solo per leggerla)",
        ]
    testo = str(valore)
    if "\n" not in testo:
        return ["   " + nome + ": " + testo]
    righe = ["   " + nome + ":"]
    for riga in testo.splitlines():
        righe.append("      " + riga)
    return righe


# Cio' che in un comando merita una riga di attenzione. Non blocca niente:
# aiuta chi legge la conferma, che e' il confine vero.
INTERPRETI = frozenset(
    {"bash", "sh", "zsh", "dash", "ksh", "fish", "pwsh", "powershell", "powershell.exe", "cmd", "cmd.exe"}
)
ELEVAZIONE = frozenset({"sudo", "su", "doas", "pkexec", "runas"})
RETE = frozenset({"curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "netcat", "telnet", "ftp"})


def _parole(args: list) -> list:
    """Le parole di un comando, aprendo anche la riga passata a una shell.

    Senza aprire il terzo elemento di `['bash', '-lc', '...']` le avvertenze
    vedrebbero solo la shell. Se `shlex.split` fallisce si divide sugli spazi.
    """
    parole = []
    for indice, pezzo in enumerate(args):
        parole.append(pezzo)
        if indice and args[0].rsplit("/", 1)[-1].lower() in INTERPRETI and args[indice - 1].startswith("-"):
            try:
                parole.extend(shlex.split(pezzo))
            except ValueError:
                parole.extend(pezzo.split())
    return parole


def avvertenze_comando(args, radice=None) -> list:
    """Le righe di attenzione per un comando da autorizzare, o nessuna.

    Ogni riga nomina un fatto, non un giudizio: shell, percorsi fuori dalla
    directory, privilegi, rete, cancellazione ricorsiva.
    """
    if not isinstance(args, list) or not all(isinstance(v, str) for v in args) or not args:
        return []
    parole = _parole(args)
    comandi = {p.rsplit("/", 1)[-1].lower() for p in parole}
    avvertenze = []
    if args[0].rsplit("/", 1)[-1].lower() in INTERPRETI:
        avvertenze.append(
            "passa da una shell: la riga dentro puo' uscire dalla directory, leggere l'ambiente e usare la rete"
        )
    fuori = []
    for parola in parole:
        candidato = parola.split("=", 1)[-1] if "=" in parola else parola
        assoluto = candidato.startswith(("/", "~")) or candidato[1:3] == ":\\"
        risale = ".." in candidato.replace("\\", "/").split("/")
        if (assoluto or risale) and (radice is None or not _dentro(candidato, radice)):
            fuori.append(parola)
    if fuori:
        avvertenze.append("tocca percorsi fuori dalla directory: " + ", ".join(dict.fromkeys(fuori)))
    if comandi & ELEVAZIONE:
        avvertenze.append("chiede privilegi di amministratore: " + ", ".join(sorted(comandi & ELEVAZIONE)))
    if comandi & RETE:
        avvertenze.append("usa la rete: " + ", ".join(sorted(comandi & RETE)))
    if "rm" in comandi and any(p.startswith("-") and "r" in p.lower() for p in parole):
        avvertenze.append("cancella ricorsivamente")
    return ["   attenzione: " + riga for riga in avvertenze]


def _dentro(percorso: str, radice) -> bool:
    """Vero se un percorso assoluto sta sotto la radice del workspace."""
    if percorso.startswith("~"):
        return False
    try:
        # Relativo alla radice, non alla directory corrente: e' li' che
        # `run_command` esegue, quindi `../x` si conta da li'.
        return Path(radice, percorso).resolve().is_relative_to(Path(radice).resolve())
    except (OSError, ValueError):
        return False


def _contenuto_esistente(radice, percorso) -> tuple[bool, str | None]:
    """Se il file esiste e, se leggibile come testo, il suo contenuto.

    Distingue "non c'e'" da "c'e' ma non si legge": nel secondo caso la
    conferma deve dire che verra' sostituito.
    """
    if radice is None or not isinstance(percorso, str) or not percorso or not _dentro(percorso, radice):
        return False, None
    candidato = Path(radice, percorso)
    if not candidato.is_file():
        return False, None
    try:
        return True, candidato.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return True, None


def righe_differenza(esistente: str, nuovo: str, percorso: str) -> list:
    """Cosa cambia in un file che esiste gia', come diff unificato non troncato.

    Mostrare solo il contenuto nuovo nasconderebbe cio' che sparisce.
    """
    righe = ["   content: differenza con il file esistente"]
    for riga in difflib.unified_diff(
        esistente.splitlines(),
        nuovo.splitlines(),
        fromfile=percorso + " (ora)",
        tofile=percorso + " (dopo)",
        lineterm="",
    ):
        righe.append("      " + riga)
    if len(righe) == 1:
        righe.append("      (identico: il file non cambia)")
    return righe


def righe_richiesta(richiesta: Richiesta) -> list:
    """Descrive per intero cio' che si sta per autorizzare.

    Il confine di `Workspace` vale per i file, non per la shell: la conferma
    umana e' l'unico controllo sui comandi, quindi questo testo fa parte del
    confine. Le avvertenze seguono gli argomenti e precedono la directory.
    """
    strumento = richiesta.strumento
    radice = richiesta.radice
    righe = ["Ares chiede di eseguire: " + strumento]
    argomenti = richiesta.argomenti
    if not argomenti:
        righe.append("   (senza argomenti)")
    for nome, valore in argomenti.items():
        if nome == "content" and strumento.endswith("write_file"):
            esiste, esistente = _contenuto_esistente(radice, argomenti.get("path"))
            if esistente is not None:
                righe.extend(righe_differenza(esistente, str(valore), str(argomenti.get("path"))))
                continue
            if esiste:
                # Il file c'e' ma non si legge come testo: va detto che sara' sostituito.
                righe.append("   content: il file esiste e non si legge come testo: verra' sostituito per intero")
        righe.extend(righe_argomento(str(nome), valore))
    if strumento.endswith("run_command"):
        righe.extend(avvertenze_comando(argomenti.get("args"), radice))
    if radice is not None:
        # Per un `delete_file` il percorso e' relativo alla radice: senza
        # questa riga l'utente autorizza `note.md` senza sapere quale.
        righe.append("   nella directory: " + str(radice))
    if richiesta.regola is not None:
        righe.append(
            "   "
            + ("rifiutato" if richiesta.regola.effetto == "nega" else "concesso")
            + " senza chiedere, per la regola "
            + str(richiesta.regola)
        )
    return righe


def riga_sandbox(politica: Politica) -> str:
    """Dove arrivano i comandi: «bwrap, senza rete», o «nessuna» con i permessi dell'utente."""
    if politica.workspace.sandbox is None:
        return "nessuna: i comandi girano con i tuoi permessi"
    return politica.workspace.sandbox + (", con la rete" if politica.workspace.sandbox_rete else ", senza rete")


def riga_regole(regole: Regole) -> str:
    """Quante regole della persona valgono qui, e da quali file: «3 consenti, 2 nega  (/p/.ares/permessi.toml)»."""
    conti = [str(regole.quante(e)) + " " + e for e in ("consenti", "nega") if regole.quante(e)]
    return (", ".join(conti) if conti else "nessuna regola valida") + "  (" + ", ".join(regole.fonti) + ")"


def riga_origine(voce: dict) -> str:
    """Da dove viene una memoria: «da sessione s, cartella c, dal 2026-10-02».

    Le memorie scritte prima che Ares registrasse la provenienza non ne
    hanno: la riga lo dice invece di tacere.
    """
    pezzi = [
        etichetta + " " + str(voce[chiave])
        for chiave, etichetta in (("sessione", "sessione"), ("cartella", "cartella"))
        if voce.get(chiave)
    ]
    if voce.get("valida_dal"):
        pezzi.append("dal " + str(voce["valida_dal"])[:10])
    return "  da " + ", ".join(pezzi) if pezzi else "  provenienza non registrata"


def righe_superata(voce: dict) -> list:
    """Una memoria superata: il testo, quando e come e' stata superata, da dove veniva."""
    quando = str(voce.get("invalidata_il") or "")[:10] or "data ignota"
    come = "sostituita il " + quando if voce.get("sostituita_da") else "tolta il " + quando
    return ["- " + " ".join(str(voce.get("content") or "").split()), "  " + come, riga_origine(voce)]


def riga_concessione(richiesta: Richiesta) -> str:
    """La riga per un comando che gira senza conferma per una regola della persona: il comando intero, e la regola."""
    args = richiesta.argomenti.get("args")
    comando = shlex.join(args) if isinstance(args, list) and all(isinstance(a, str) for a in args) else str(args)
    return "Eseguo senza chiedere: " + comando + "   (regola " + str(richiesta.regola) + ")"


def chiedi_autorizzazione(richiesta: Richiesta, input_cli: CliInput) -> Decisione:
    """Mostra la richiesta e chiede il permesso a riga di comando.

    Il motivo di un no si chiede perche' arriva al modello. Questa domanda e'
    l'unica traccia a schermo del rifiuto: Agno non emette eventi per
    `reject_tool_call`.
    """
    UI.confirmation(righe_richiesta(richiesta))
    try:
        scelta = input_cli.ask("Autorizzi? [s/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        # Ctrl-C davanti a una richiesta e' un no, non un errore.
        UI.blank()
        scelta = ""
    if scelta in ("s", "si", "si'", "sì"):
        return Decisione(consenti=True)
    try:
        motivo = input_cli.ask("Motivo (invio per saltare): ", muted=True).strip()
    except (EOFError, KeyboardInterrupt):
        UI.blank()
        motivo = ""
    return Decisione(consenti=False, motivo=motivo or None)


def _conta_chiamate(elenco) -> tuple:
    """Somma token e secondi di tutte le chiamate fatte con lo stesso ruolo.

    Agno tiene una riga per modello, non per chiamata; qui si somma anche fra
    righe diverse. `total_duration` di Ollama e' in nanosecondi.
    """
    entrata = uscita = 0
    nanosecondi = 0.0
    for metriche in elenco or []:
        entrata += getattr(metriche, "input_tokens", 0) or 0
        uscita += getattr(metriche, "output_tokens", 0) or 0
        durata = (getattr(metriche, "provider_metrics", None) or {}).get("total_duration")
        if isinstance(durata, (int, float)):
            nanosecondi += durata
    return entrata, uscita, nanosecondi / 1_000_000_000


def secondi_di_prefill(elenco) -> float:
    """Secondi spesi da Ollama a leggere il prompt (`prompt_eval_duration`), sommati sulle chiamate.

    E' la parte del turno che la KV cache puo' risparmiare: con il prefisso
    del prompt stabile, dal secondo turno dovrebbe essere una frazione del
    primo. Zero se il fornitore non la riporta.
    """
    nanosecondi = 0.0
    for metriche in elenco or []:
        durata = (getattr(metriche, "provider_metrics", None) or {}).get("prompt_eval_duration")
        if isinstance(durata, (int, float)):
            nanosecondi += durata
    return nanosecondi / 1_000_000_000


def _token(quanti: int) -> str:
    """Migliaia con una cifra, in base 1024.

    `NUM_CTX` e' una potenza di due: 262144 deve leggersi `256k`, non `262.1k`.
    """
    if quanti >= 1024:
        return str(round(quanti / 1024.0, 1)) + "k"
    return str(quanti)


def finestra_occupata(risposta) -> int | None:
    """Token del prompt dell'ultima chiamata al modello principale.

    `risposta.metrics.input_tokens` somma ogni chiamata del turno, estrazioni
    comprese, e sovrastimerebbe. Il prompt dell'ultima chiamata e' affidabile
    perche' Ollama conta il prompt intero, non il solo delta fuori dalla KV
    cache.
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


def quota_finestra(prompt: int, impostazioni: Impostazioni) -> str:
    """La finestra occupata in percentuale, o vuoto se il tetto non e' noto.

    `<1` sotto l'uno per cento, perche' `0%` sembra un contatore rotto. Il
    tetto e' il contesto chiesto a Ollama per questa conversazione.
    """
    if not prompt or not impostazioni.num_ctx:
        return ""
    quota = 100.0 * prompt / impostazioni.num_ctx
    return ("<1" if quota < 1 else str(round(quota))) + "%"


def righe_metriche(risposta, impostazioni: Impostazioni) -> list:
    """Il costo del turno in una riga, o niente se non c'e' niente da dire.

    Un turno interrotto arriva senza metriche, e un segmento assente (per
    esempio l'apprendimento spento) sparisce invece di stampare zero.
    """
    metriche = getattr(risposta, "metrics", None)
    if metriche is None:
        return []
    dettagli = getattr(metriche, "details", None) or {}
    _, uscita, secondi_risposta = _conta_chiamate(dettagli.get("model"))
    _, appresi, secondi_appresi = _conta_chiamate(dettagli.get("learning_model"))

    pezzi = []
    prompt = finestra_occupata(risposta)
    if prompt and impostazioni.num_ctx:
        pezzi.append(
            "finestra "
            + _token(prompt)
            + "/"
            + _token(impostazioni.num_ctx)
            + " ("
            + quota_finestra(prompt, impostazioni)
            + ")"
        )
    prefill = secondi_di_prefill(dettagli.get("model"))
    if prefill:
        pezzi.append("prefill " + str(round(prefill, 1)) + " s")
    if uscita:
        pezzi.append("risposta " + _token(uscita) + " tok / " + str(round(secondi_risposta, 1)) + " s")
    if appresi:
        # Il costo che non si vede: arriva dopo che la risposta e' a schermo.
        pezzi.append("apprendimento " + _token(appresi) + " tok / " + str(round(secondi_appresi, 1)) + " s")
    durata = getattr(metriche, "duration", None)
    if durata:
        pezzi.append("turno " + str(round(durata, 1)) + " s")
    if not pezzi:
        return []
    return ["[" + "  ".join(pezzi) + "]"]
