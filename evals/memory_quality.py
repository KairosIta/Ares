"""Misura estrazione e recupero con dialoghi fissi e stato isolato per ripetizione.

Uso: .venv/bin/python -m evals.memory_quality --ripetizioni 3
Il rapporto JSON contiene le prove; il Markdown e' un indice leggibile.
Nessun modello giudice: i controlli sono espliciti e le ambiguita' restano tali.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

RADICE = Path(__file__).resolve().parents[1]
STATI = ("superato", "fallito", "da_revisionare", "non_conclusivo", "errore")


@dataclass(frozen=True)
class Fase:
    nome: str
    utente: str
    assistente: str
    domanda: str
    attesi: tuple[str, ...] = ()
    da_rivedere: tuple[str, ...] = ()
    sessione: str = "apprendimento"
    memoria_precedente: tuple[str, ...] = ()
    avvio_precedente: str | None = None
    # Come il valore atteso puo' essere citato, quando non lo si cita alla
    # lettera. Il legame fra valore e citazione chiede di norma il termine
    # esatto: e' cio' che impedisce di spacciare per prova una frase che parla
    # d'altro. Per l'avvio quel termine non e' l'unico modo di dirlo - una
    # memoria che riporta "ha confermato l'avvio dei lavori" sostiene il
    # valore "iniziato" senza contenerlo - e senza questo campo una risposta
    # corretta con la sua citazione verbatim restava non conclusiva. Va
    # dichiarato sulla fase, non allentato per tutti: le altre diciassette fasi
    # continuano a pretendere il termine, e la forma ammessa qui e' scritta
    # accanto al caso invece di essere dedotta dal valutatore.
    evidenza_equivalente: re.Pattern[str] | None = None


# Le due traiettorie divergono solo all'inizio: una possibilita' puo' non
# essere conservata, mentre un piano confermato deve poter essere recuperato.
DOPO_ABBANDONO = (
    Fase(
        "abbandono",
        "Ho scartato definitivamente il progetto ORIONE-42: non intendo piu' portarlo avanti. "
        "Ho invece deciso di realizzare VEGA-19, che e' il mio unico progetto personale attuale.",
        "ORIONE-42 e' abbandonato. Il progetto personale attuale e' VEGA-19.",
        "Qual e' il nome del progetto personale che intendo portare avanti adesso?",
        ("VEGA-19",),
        ("ORIONE-42",),
        sessione="scelta",
        memoria_precedente=("ORIONE-42",),
    ),
    Fase(
        "dopo_altro_dialogo",
        "Per tenere in ordine gli appunti uso file Markdown. E' il mio formato abituale.",
        "Il tuo formato abituale per gli appunti e' Markdown.",
        "Qual e' il nome del progetto personale che intendo portare avanti adesso?",
        ("VEGA-19",),
        ("ORIONE-42",),
        sessione="appunti",
        memoria_precedente=("VEGA-19",),
    ),
)

DOMANDA_AVVIO = (
    "Ho gia' iniziato a lavorare al mio progetto personale? Nel valore usa 'iniziato' se l'avvio "
    "e' confermato, 'non iniziato' se e' confermato che non ho iniziato, oppure null se non lo sai."
)

# Le forme con cui uno store dichiara un avvio avvenuto. Sta qui, sopra i
# casi, perche' serve a due cose che devono restare d'accordo: la precondizione
# di `avvio_confermato`, che chiede se l'avvio risulta dallo stato di prima, e
# la citazione ammessa dalle fasi che attendono "iniziato". Quando le due
# vivevano separate la stessa frase era prova per la prima e non per la
# seconda, e una risposta corretta usciva non conclusiva.
AVVIO_AFFERMATO = re.compile(
    r"\bha\s+(?:(?:gia'|già|effettivamente)\s+)?iniziato\s+a\s+(?:lavorare|lavorarci|realizzare)\b"
    r"|\bha\s+(?:iniziato|avviato)\s+(?:il\s+)?(?:lavoro|progetto|[a-z]+-\d+)\b"
    r"|\bha\s+(?:confermato|dichiarato|comunicato)\s+l['’]avvio\s+(?:dei\s+lavori|del\s+lavoro|del\s+progetto)\b"
    r"|\bha\s+(?:confermato|dichiarato|comunicato)\s+di\s+aver\s+iniziato\s+a\s+(?:lavorare|lavorarci|realizzare)\b"
)


CASI = {
    "ipotesi": (
        Fase(
            "possibilita",
            "Potrei trasferirmi a Milano, ma non ho deciso e non ti ho detto dove abito ora.",
            "Possiamo valutare questa possibilita' senza dare per deciso il trasferimento.",
            "In quale citta' abito attualmente?",
            da_rivedere=("Milano",),
        ),
    ),
    "personaggio": (
        Fase(
            "finzione",
            "Questo e' un dialogo inventato, non autobiografico: il personaggio dice 'Mi chiamo Livia Vesper "
            "e sono un'astronauta'. Non sono informazioni su di me.",
            "Il personaggio del dialogo e' Livia Vesper, astronauta.",
            "Qual e' il mio nome completo?",
            da_rivedere=("Livia", "Vesper", "astronaut"),
        ),
    ),
    "proposta": (
        Fase(
            "non_accettata",
            "Sto valutando quando fare i backup, non ho ancora deciso.",
            "Ti propongo le 22:15 ogni giorno. E' una proposta da valutare, non una decisione.",
            "A quale ora ho deciso di fare i backup?",
            da_rivedere=("22:15",),
        ),
        Fase(
            "accettata",
            "Accetto la proposta: ho deciso di fare i backup ogni giorno alle 22:15.",
            "La decisione e' fare i backup ogni giorno alle 22:15.",
            "A quale ora ho deciso di fare i backup? Riporta solo l'orario nel valore.",
            ("22:15",),
        ),
    ),
    "correzione": (
        Fase(
            "iniziale",
            "In generale preferisco risposte sintetiche.",
            "Tengo conto della tua preferenza.",
            "Come preferisco normalmente le risposte? Usa l'aggettivo originale nel valore.",
            ("sintetiche",),
        ),
        Fase(
            "corretta",
            "Correggo la preferenza precedente: in generale preferisco risposte dettagliate.",
            "La preferenza attuale e' per risposte dettagliate.",
            "Come preferisco normalmente le risposte? Usa l'aggettivo originale nel valore.",
            ("dettagliate",),
            ("sintetiche",),
        ),
    ),
    "temporanea": (
        Fase(
            "stabile",
            "In generale preferisco risposte dettagliate.",
            "Tengo conto della tua preferenza.",
            "Come preferisco normalmente le risposte? Usa l'aggettivo originale nel valore.",
            ("dettagliate",),
        ),
        Fase(
            "eccezione",
            "Solo per il prossimo messaggio usa tre parole; la mia preferenza generale non cambia.",
            "Ricevuto, solo ora.",
            "Come preferisco normalmente le risposte? Usa l'aggettivo originale nel valore.",
            ("dettagliate",),
            ("tre parole",),
        ),
    ),
    "recupero": (
        Fase(
            "nuova_sessione",
            "Il mio progetto personale si chiama AURORA-73. E' il nome definitivo.",
            "Il nome definitivo del tuo progetto e' AURORA-73.",
            "Come si chiama il mio progetto personale?",
            ("AURORA-73",),
        ),
    ),
    "abbandono_ipotesi": (
        Fase(
            "ipotesi",
            "Sto valutando un progetto personale chiamato ORIONE-42. "
            "E' solo un'idea: non ho deciso di realizzarlo e non ho altri progetti personali in corso.",
            "ORIONE-42 e' una possibilita' da valutare, non un progetto deciso.",
            "Qual e' il nome del progetto personale che ho deciso di realizzare?",
            da_rivedere=("ORIONE-42",),
            sessione="idea",
        ),
        *DOPO_ABBANDONO,
    ),
    "abbandono_piano": (
        Fase(
            "piano_confermato",
            "Ho deciso di realizzare ORIONE-42. E' il mio unico progetto personale attuale.",
            "Il progetto personale che hai deciso di realizzare e' ORIONE-42.",
            "Qual e' il nome del progetto personale che ho deciso di realizzare?",
            ("ORIONE-42",),
            sessione="idea",
        ),
        *DOPO_ABBANDONO,
    ),
    "avvio": (
        Fase(
            "decisione",
            "Ho deciso di realizzare ORIONE-42. E' il mio unico progetto personale attuale.",
            "Il progetto personale che hai deciso di realizzare e' ORIONE-42.",
            DOMANDA_AVVIO,
            da_rivedere=("inizi", "avvi", "sta lavor", "sta realizz", "in corso"),
            sessione="decisione",
        ),
        Fase(
            "avvio_futuro",
            "Ho pianificato di cominciare a lavorare al progetto ORIONE-42 domani.",
            "Il programma e' cominciare a lavorare a ORIONE-42 domani.",
            DOMANDA_AVVIO,
            da_rivedere=("inizi", "avvi", "sta lavor", "sta realizz", "in corso"),
            sessione="programma",
            memoria_precedente=("ORIONE-42",),
        ),
        Fase(
            "iniziato",
            "Ho iniziato a lavorare al progetto ORIONE-42.",
            "Hai iniziato a lavorare a ORIONE-42.",
            DOMANDA_AVVIO,
            ("iniziato",),
            sessione="lavoro",
            memoria_precedente=("ORIONE-42",),
            evidenza_equivalente=AVVIO_AFFERMATO,
        ),
        Fase(
            "decisione_ribadita",
            "Confermo la decisione di realizzare ORIONE-42. Il mio obiettivo resta lo stesso.",
            "L'obiettivo di realizzare ORIONE-42 resta confermato.",
            DOMANDA_AVVIO,
            ("iniziato",),
            sessione="conferma",
            memoria_precedente=("ORIONE-42",),
            avvio_precedente="ORIONE-42",
            evidenza_equivalente=AVVIO_AFFERMATO,
        ),
    ),
}

SONDA = (
    " Rispondi usando solo cio' che sai gia' di me. Non indovinare. Restituisci solo un oggetto JSON "
    "con queste chiavi: valore (risposta breve, oppure null se il dato non e' confermato), "
    "certezza (confermato, non_confermato o sconosciuto), evidenza (citazione testuale breve dalla "
    "memoria che sostiene il valore, oppure null). Un'ipotesi non e' un dato confermato."
)


def normalizza(testo: str) -> str:
    return " ".join(testo.casefold().split())


def contenuti_durevoli(stato: dict) -> list[str]:
    """Unita' originali, senza source, istruzioni del renderer o contesto di sessione."""
    profilo = stato.get("profilo") or {}
    valori = [
        str(v)
        for k, v in profilo.items()
        if k
        not in {
            "user_id",
            "agent_id",
            "session_id",
            "created_at",
            "updated_at",
            "team_id",
        }
        and v
    ]
    valori += [str(m.get("content", "")) for m in (stato.get("memorie") or {}).get("memories", [])]
    return valori


def testi_durevoli(stato: dict) -> str:
    # La citazione puo' comprendere la data aggiunta dal renderer di Agno.
    # Conserviamo anche quel testo, mai il campo source con il dialogo grezzo.
    return "\n".join([*contenuti_durevoli(stato), stato.get("contesto_durevole", "")])


# Segnali di cautela, non un classificatore semantico: anche una negazione
# riferita a un altro fatto nella stessa memoria richiede lettura umana.
AMBIGUITA = re.compile(
    r"\b(?:non|mai|nessun\w*|senza|forse|se|potrebb\w*|ipot\w*|eventual\w*|"
    r"incert\w*|sconosciut\w*|ignot\w*|probabilmente|possibil\w*|presum\w*|quasi|"
    r"nega\w*|sment\w*|esclud\w*|fals\w*|sbagliat\w*|dovrebbe|avrebbe|sarebbe|avra|avrà|sara|sarà|"
    r"not|never|unknown|might|may|would)\b"
)
FUTURO_AVVIO = re.compile(r"\b(?:domani|pianificat\w*|programmat\w*|previst\w*|futur\w*|intenzion\w*)\b")


def contiene(testo: str, valore: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(valore) + r"(?!\w)", testo) is not None


def cita_il_valore(testo: str, valore: str, equivalente: re.Pattern[str] | None, *, intero: bool = False) -> bool:
    """Il testo sostiene il valore: lo contiene, o ne contiene la forma dichiarata.

    `intero` distingue le due domande che si fanno alla stessa citazione. La
    prima e' se il valore ci sia; la seconda, dopo aver risalito la memoria
    originale, e' se ci sia come parola e non come pezzo di un'altra - il
    valore "inizi" dentro "iniziativa" non prova niente. L'equivalenza
    dichiarata e' la stessa in entrambe: e' un'espressione con i propri
    confini di parola, quindi non ha bisogno della distinzione.
    """
    letterale = contiene(testo, valore) if intero else valore in testo
    return letterale or bool(equivalente and equivalente.search(testo))


def ambiguo(testo: str) -> bool:
    return bool(AMBIGUITA.search(testo) or "?" in testo)


def problema_citazione(
    evidenza: str, valore: str, stato: dict, equivalente: re.Pattern[str] | None = None
) -> str | None:
    # Il controllo verbatim ha gia' verificato anche l'eventuale data del
    # renderer. Per risalire alla memoria originale togliamo solo quel suffisso.
    nucleo = re.sub(r"\s+\[\d{4}-\d{2}-\d{2}\]$", "", evidenza)
    originali = [normalizza(t) for t in contenuti_durevoli(stato)]
    if not any(nucleo in t for t in originali):
        return "La citazione non e' riconducibile a un contenuto originale dello store."
    # I testi su cui si cerca ambiguita' sono quelli che sostengono il valore,
    # non i soli che lo contengono alla lettera: con una forma equivalente la
    # lista restava vuota e la guardia sulle negazioni non guardava niente.
    pertinenti = [t for t in originali if cita_il_valore(t, valore, equivalente, intero=True)]
    if any(ambiguo(t) for t in pertinenti):
        return "Il contesto originale del valore contiene negazioni, incertezza o domande."
    if len(re.findall(r"[\w]+(?:[-'][\w]+)*", nucleo)) < 3:
        return "La citazione e' troppo breve per sostenere da sola l'affermazione."
    if not cita_il_valore(nucleo, valore, equivalente, intero=True):
        return "Il valore compare solo come frammento di un'altra parola o identificativo."
    return None


def avvio_confermato(stato: dict, progetto: str) -> bool:
    """Riconosce prove esplicite d'avvio; i casi non riconosciuti restano non conclusivi."""
    progetto = normalizza(progetto)
    prove = []
    for originale in contenuti_durevoli(stato):
        testo = normalizza(originale)
        if not contiene(testo, progetto) or not re.search(r"\b(?:inizi\w*|avvi\w*)\b", testo):
            continue
        # I progetti del benchmark hanno identificativi NOME-numero. Una
        # memoria con piu' progetti non dimostra a quale si riferisca l'avvio.
        progetti = set(re.findall(r"\b[a-z][a-z0-9]*-\d+\b", testo))
        if ambiguo(testo) or FUTURO_AVVIO.search(testo) or progetti - {progetto}:
            return False
        etichetta = re.fullmatch(
            re.escape(progetto) + r"\s*(?::\s*(?:iniziato|avviato)|\(\s*(?:iniziato|avviato)\s*\))\.?", testo
        )
        prove.append(bool(AVVIO_AFFERMATO.search(testo) or etichetta))
    return any(prove)


def dialogo_serializzabile(fase: Fase) -> dict:
    """La fase come dizionario, con l'equivalenza ammessa scritta per esteso.

    `asdict` restituirebbe l'oggetto compilato di `evidenza_equivalente`, che
    `json.dumps` non sa scrivere: il rapporto e' evidenza e deve contenere cio'
    che la fase ha dichiarato, non un riferimento a un oggetto in memoria. Ne
    conserviamo percio' il testo, che e' anche l'unica forma leggibile da chi
    rilegge il rapporto.
    """
    dati = asdict(fase)
    ammessa = dati.get("evidenza_equivalente")
    dati["evidenza_equivalente"] = ammessa.pattern if ammessa is not None else None
    return dati


def valuta(fase: Fase, risposta: str, stato: dict, prima: dict | None = None) -> dict:
    """Un dato atteso senza evidenza non passa; citare un termine ambiguo non prova un errore."""
    try:
        testo_json = risposta.strip()
        if testo_json.startswith("```json\n") and testo_json.endswith("\n```"):
            testo_json = testo_json[len("```json\n") : -len("\n```")]
        dati = json.loads(testo_json)
        if not isinstance(dati, dict) or set(dati) != {"valore", "certezza", "evidenza"}:
            raise ValueError("campi JSON mancanti o inattesi")
        if dati["certezza"] not in ("confermato", "non_confermato", "sconosciuto"):
            raise ValueError("certezza non valida")
        if any(dati[k] is not None and not isinstance(dati[k], str) for k in ("valore", "evidenza")):
            raise ValueError("valore o evidenza non testuali")
    except (ValueError, TypeError) as errore:
        return {"stato": "non_conclusivo", "motivo": "Formato della sonda: " + str(errore)}
    testo = normalizza(testi_durevoli(stato))
    valore = dati["valore"]
    problema = None
    if fase.attesi:
        if dati["certezza"] != "confermato" or normalizza(valore or "") not in map(normalizza, fase.attesi):
            return {"stato": "fallito", "motivo": "Il dato confermato non viene recuperato correttamente."}
        evidenza = normalizza(dati["evidenza"] or "")
        if (
            not evidenza
            or evidenza not in testo
            or not cita_il_valore(evidenza, normalizza(valore), fase.evidenza_equivalente)
        ):
            return {
                "stato": "non_conclusivo",
                "motivo": "La risposta attesa non ha una citazione verificabile negli store.",
            }
        problema = problema_citazione(evidenza, normalizza(valore), stato, fase.evidenza_equivalente)
    elif valore is not None or dati["certezza"] == "confermato":
        return {"stato": "fallito", "motivo": "La sonda restituisce un dato personale non confermato."}
    precedenti = normalizza(testi_durevoli(prima or {}))
    mancanti = [t for t in fase.memoria_precedente if normalizza(t) not in precedenti]
    if mancanti:
        return {
            "stato": "non_conclusivo",
            "motivo": "Risposta corretta, ma manca la memoria precedente necessaria alla prova: " + ", ".join(mancanti),
        }
    if fase.avvio_precedente and not avvio_confermato(prima or {}, fase.avvio_precedente):
        return {
            "stato": "non_conclusivo",
            "motivo": "Manca una prova non ambigua dell'avvio precedente di " + fase.avvio_precedente + ".",
        }
    if problema:
        return {"stato": "da_revisionare", "motivo": problema}
    ambigui = [termine for termine in fase.da_rivedere if normalizza(termine) in testo]
    if ambigui:
        return {
            "stato": "da_revisionare",
            "motivo": "Risposta corretta, ma verificare qualifiche/storia nello store: " + ", ".join(ambigui),
        }
    return {"stato": "superato", "motivo": "Controlli di recupero ed evidenza soddisfatti per questo caso."}


def aggrega(risultati: list[dict]) -> dict:
    conteggi = Counter(fase["valutazione"]["stato"] for r in risultati for fase in r.get("fasi", []))
    return {stato: conteggi[stato] for stato in STATI}


def scrivi_json(percorso: Path, dati: dict) -> None:
    temporaneo = percorso.with_suffix(percorso.suffix + ".tmp")
    temporaneo.write_text(json.dumps(dati, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporaneo.replace(percorso)


def markdown(rapporto: dict) -> str:
    righe = [
        "# Qualità della memoria di Ares",
        "",
        f"Stato esecuzione: {rapporto['stato']}",
        "",
        "Dialoghi sintetici fissi; recupero da sessioni nuove senza cronologia. "
        "Si misurano profilo e memorie, senza entità, intuizioni o workspace.",
        "",
        "| Caso | Ripetizione | Fase | Esito | Secondi |",
        "| --- | --- | --- | --- | ---: |",
    ]
    for risultato in rapporto["risultati"]:
        for fase in risultato["fasi"]:
            righe.append(
                f"| {risultato['caso']} | {risultato['ripetizione']} | {fase['nome']} | "
                f"{fase['valutazione']['stato']} | {fase.get('secondi', 0):.2f} |"
            )
    righe += [
        "",
        "Conteggi per fase (non percentuali di affidabilità generale):",
        "",
        *[f"- {k}: {v}" for k, v in aggrega(rapporto["risultati"]).items()],
        "",
        "Il JSON accanto conserva messaggi, archivi, risposte, errori e motivi dei verdetti. "
        "I casi da revisionare e non conclusivi non sono successi. Nessun modello giudice viene usato.",
        "",
    ]
    return "\n".join(righe)


def metadati() -> dict:
    from ares import config

    percorsi = [
        "ares/agent/prompts.py",
        "ares/agent/learning.py",
        "ares/agent/assistant.py",
        "ares/agent/schemas.py",
        "evals/memory_quality.py",
        "ares/config.py",
        "uv.lock",
    ]
    return {
        "modello_conversazione": config.MAIN_MODEL,
        "modello_estrazione": config.LEARNING_MODEL,
        "opzioni_conversazione": config.OLLAMA_OPTIONS,
        "opzioni_estrazione": config.LEARNING_OPTIONS,
        "think_conversazione": config.MAIN_THINK,
        "think_estrazione": config.LEARNING_THINK,
        "agno": version("agno"),
        "python": sys.version,
        "schema_rapporto": 3,
        "sorgenti_sha256": {p: hashlib.sha256((RADICE / p).read_bytes()).hexdigest() for p in percorsi},
        "limiti": "Test di estrazione su dialoghi fissi e recupero. Nessuna valutazione di entita', "
        "intuizioni, cronologia o dialogo end-to-end. Il contenuto ambiguo richiede revisione umana.",
    }


def worker(caso: str, risultato: Path) -> None:
    """Anche l'ingresso interno isola lo stato prima di importare Ares."""
    if "ares.config" in sys.modules:
        raise RuntimeError("Il worker richiede un processo senza ares.config gia' importato.")
    risultato = risultato.resolve()
    # Dentro la directory del padre: anche un timeout che uccide il figlio
    # lascia lo stato sotto il controllo del suo cleanup. Su Windows i DB
    # ancora aperti possono rimandare la cancellazione all'uscita del figlio.
    with tempfile.TemporaryDirectory(
        prefix="ares-memory-worker-", dir=risultato.parent, ignore_cleanup_errors=True
    ) as cartella:
        root = Path(cartella)
        ambiente = dict(os.environ)
        cwd = Path.cwd()
        os.environ.update(
            ARES_HOME=str(root / "home"),
            ARES_TMP=str(root / "stato"),
            ARES_BACKUP_DIR=str(root / "backup"),
        )
        os.chdir(root)
        try:
            _worker(caso, risultato)
        finally:
            os.chdir(cwd)
            os.environ.clear()
            os.environ.update(ambiente)


class RaccoglitoreAvvisi(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.avvisi: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.avvisi.append({"livello": record.levelname, "messaggio": record.getMessage()})


def _worker(caso: str, risultato: Path) -> None:
    from agno.models.message import Message

    from ares import config
    from ares.agent.assistant import build_assistant
    from ares.agent.learning import build_learning_machine
    from ares.agent.prompts import messaggio_di_sistema
    from ares.cli.log import configura_log_agno
    from ares.state.archivi import build_db

    configura_log_agno(False)
    for flag in ("LEARN_ENTITIES", "LEARN_KNOWLEDGE", "WORKSPACE", "SEARCH_PAST_SESSIONS", "READ_CHAT_HISTORY"):
        setattr(config, flag, False)
    config.LEARN_USER_PROFILE = config.LEARN_USER_MEMORY = config.LEARN_SESSION_CONTEXT = True
    config.MEMORY_AGENT_TOOLS = True
    macchina: Any = build_learning_machine(build_db(), None, "eval-user")
    raccoglitore = RaccoglitoreAvvisi()
    for nome in list(logging.root.manager.loggerDict):
        if nome == "agno" or nome.startswith(("agno-", "agno.")):
            logging.getLogger(nome).addHandler(raccoglitore)
    dati: dict[str, Any] = {"fasi": [], "metadati": metadati()}
    scrivi_json(risultato, dati)

    def fotografia(sessione: str) -> dict:
        profilo = macchina.user_profile_store.get(user_id="eval-user")
        memorie = macchina.user_memory_store.get(user_id="eval-user")
        stato = {
            nome: asdict(valore) if valore is not None else None
            for nome, valore in (
                ("profilo", profilo),
                ("memorie", memorie),
                ("contesto", macchina.session_context_store.get(session_id=sessione)),
            )
        }
        stato["contesto_durevole"] = (
            macchina.user_profile_store.build_context(profilo)
            + "\n"
            + macchina.user_memory_store.build_context(memorie)
        )
        return stato

    for indice, fase in enumerate(CASI[caso]):
        raccoglitore.avvisi.clear()
        avvio = time.monotonic()
        voce: dict[str, Any] = {
            "nome": fase.nome,
            "dialogo": dialogo_serializzabile(fase),
            "prima": fotografia(fase.sessione),
            "valutazione": {"stato": "errore", "motivo": "Fase interrotta prima del completamento."},
        }
        dati["fasi"].append(voce)
        scrivi_json(risultato, dati)
        try:
            macchina.process_completed_run(
                messages=[
                    Message(role="user", content=fase.utente),
                    Message(role="assistant", content=fase.assistente),
                ],
                user_id="eval-user",
                session_id=fase.sessione,
                agent_id="ares-eval",
            )
            voce["secondi_estrazione"] = round(time.monotonic() - avvio, 3)
            voce["dopo"] = fotografia(fase.sessione)
            voce["tentativi_contesto"] = macchina.session_context_store.last_extraction_attempts
            if not macchina.session_context_store.context_updated:
                raise RuntimeError("Il contesto di sessione non e' stato salvato.")
            scrivi_json(risultato, dati)
            sessione = "recupero-" + str(indice)
            agente = build_assistant(user_id="eval-user", session_id=sessione, interattivo=False)
            prompt = messaggio_di_sistema(agente, user_id="eval-user", session_id=sessione)
            voce["prompt_recupero_sha256"] = hashlib.sha256(prompt.encode()).hexdigest()
            voce["domanda_recupero"] = fase.domanda + SONDA
            inizio_recupero = time.monotonic()
            risposta = agente.run(voce["domanda_recupero"])
            voce["secondi_recupero"] = round(time.monotonic() - inizio_recupero, 3)
            voce["risposta"] = str(risposta.content or "")
            voce["strumenti_recupero"] = [t.tool_name for t in (risposta.tools or [])]
            if risposta.status.value.lower() != "completed" or any(t.tool_call_error for t in (risposta.tools or [])):
                raise RuntimeError("La sonda o uno dei suoi strumenti non ha completato il turno.")
            voce["valutazione"] = valuta(fase, voce["risposta"], voce["dopo"], voce["prima"])
            if fotografia(fase.sessione) != voce["dopo"]:
                voce["valutazione"] = {"stato": "errore", "motivo": "Il recupero ha modificato gli apprendimenti."}
        except Exception as errore:
            voce["valutazione"] = {"stato": "errore", "motivo": type(errore).__name__ + ": " + str(errore)}
        voce["avvisi"] = list(raccoglitore.avvisi)
        # Agno intercetta alcuni errori e li registra senza sollevarli. Solo
        # il retry recuperato del contesto e' un avviso previsto e verificato.
        imprevisti = [
            a
            for a in voce["avvisi"]
            if not a["messaggio"].startswith("Session context non salvato: ripeto l'estrazione")
        ]
        if imprevisti and voce["valutazione"]["stato"] != "errore":
            voce["valutazione_semantica"] = voce["valutazione"]
            voce["valutazione"] = {
                "stato": "non_conclusivo",
                "motivo": "Avvisi imprevisti durante estrazione o recupero: consultare il log della fase.",
            }
        voce["secondi"] = round(time.monotonic() - avvio, 3)
        scrivi_json(risultato, dati)


class CasoInterrotto(KeyboardInterrupt):
    def __init__(self, risultato: dict) -> None:
        super().__init__("Caso interrotto; prove parziali recuperate.")
        self.risultato = risultato


def esegui_caso(caso: str, ripetizione: int, timeout: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="ares-memory-quality-") as cartella:
        root = Path(cartella)
        risultato = root / "risultato.json"
        ambiente = {
            **os.environ,
            "ARES_HOME": str(root / "home"),
            "ARES_TMP": str(root / "home" / "stato"),
            "ARES_BACKUP_DIR": str(root / "backup"),
            "PYTHONPATH": str(RADICE),
        }
        comando = [sys.executable, "-m", "evals.memory_quality", "--worker", caso, "--report", str(risultato)]
        errore = None
        interrotto = False
        try:
            figlio = subprocess.run(comando, cwd=root, env=ambiente, capture_output=True, text=True, timeout=timeout)
            log = (figlio.stdout + figlio.stderr)[-8000:]
            if figlio.returncode:
                errore = f"Processo terminato con codice {figlio.returncode}."
        except subprocess.TimeoutExpired:
            log = ""
            errore = f"Timeout dopo {timeout} secondi."
        except KeyboardInterrupt:
            # subprocess.run ha gia' terminato e atteso il figlio. Recupera
            # il checkpoint prima che TemporaryDirectory elimini lo stato.
            log = ""
            errore = "Esecuzione interrotta dall'utente."
            interrotto = True
        try:
            dati = json.loads(risultato.read_text(encoding="utf-8"))
            if not isinstance(dati, dict) or not isinstance(dati.get("fasi"), list):
                raise ValueError("struttura non valida")
        except (OSError, ValueError) as guasto:
            dati = {"fasi": []}
            errore = errore or "Rapporto del processo mancante o non valido: " + str(guasto)
        if len(dati["fasi"]) != len(CASI[caso]):
            errore = errore or "Il processo non ha misurato tutte le fasi previste."
        if errore:
            dati["fasi"].append({"nome": "processo", "valutazione": {"stato": "errore", "motivo": errore}})
        dati.update(caso=caso, ripetizione=ripetizione, log=log)
        if interrotto:
            raise CasoInterrotto(dati)
        return dati


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ripetizioni", type=int, default=3)
    p.add_argument("--casi", nargs="+", choices=tuple(CASI), default=list(CASI))
    p.add_argument("--timeout", type=int, default=180, help="secondi massimi per caso e ripetizione")
    p.add_argument("--report", type=Path, help="nuovo file JSON; accanto viene scritto un Markdown")
    p.add_argument("--worker", choices=tuple(CASI), help=argparse.SUPPRESS)
    return p


def main() -> int:
    p = parser()
    args = p.parse_args()
    if args.ripetizioni < 1 or args.timeout < 1:
        p.error("ripetizioni e timeout devono essere positivi")
    if args.worker:
        if args.report is None:
            p.error("--worker richiede --report")
        worker(args.worker, args.report)
        return 0
    percorso = (
        args.report
        or RADICE / "artifacts" / "memory-quality" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    ).resolve()
    if percorso.suffix != ".json":
        p.error("--report deve avere estensione .json")
    if percorso.exists() or percorso.with_suffix(".md").exists():
        p.error("il rapporto esiste gia': scegli un nuovo percorso")
    percorso.parent.mkdir(parents=True, exist_ok=True)
    rapporto: dict[str, Any] = {
        "stato": "in_corso",
        "avvio_utc": datetime.now(UTC).isoformat(),
        "metadati": metadati(),
        "ripetizioni": args.ripetizioni,
        "casi": args.casi,
        "risultati": [],
    }

    def salva() -> None:
        rapporto["riepilogo"] = aggrega(rapporto["risultati"])
        scrivi_json(percorso, rapporto)
        percorso.with_suffix(".md").write_text(markdown(rapporto), encoding="utf-8")

    salva()
    print("Rapporto:", percorso, flush=True)
    try:
        for ripetizione in range(1, args.ripetizioni + 1):
            for caso in args.casi:
                print(f"{caso}, ripetizione {ripetizione}/{args.ripetizioni}...", flush=True)
                risultato = esegui_caso(caso, ripetizione, args.timeout)
                rapporto["risultati"].append(risultato)
                salva()
                print(
                    "  " + ", ".join(f["nome"] + ": " + f["valutazione"]["stato"] for f in risultato["fasi"]),
                    flush=True,
                )
        rapporto["stato"] = "completato"
    except CasoInterrotto as interruzione:
        rapporto["risultati"].append(interruzione.risultato)
        rapporto["stato"] = "interrotto"
    except KeyboardInterrupt:
        rapporto["stato"] = "interrotto"
    except Exception as errore:
        rapporto["stato"] = "errore"
        rapporto["errore"] = type(errore).__name__ + ": " + str(errore)
    finally:
        salva()
    print(json.dumps(rapporto["riepilogo"], ensure_ascii=False), flush=True)
    # Completare la misura non significa aver superato i casi. Anche i
    # risultati ambigui hanno un codice non verde, distinto dai guasti.
    if rapporto["stato"] != "completato" or rapporto["riepilogo"]["errore"]:
        return 1
    return 2 if any(rapporto["riepilogo"][s] for s in ("fallito", "da_revisionare", "non_conclusivo")) else 0


if __name__ == "__main__":
    sys.exit(main())
