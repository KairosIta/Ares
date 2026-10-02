"""Misura quanto aspetta la persona, turno per turno, con i modelli configurati.

Uso: .venv/bin/python -m evals.latenza
Sei turni fissi in una conversazione sola, su uno stato nuovo, con
l'apprendimento acceso: presentazione, preferenze, un piano in tre passi,
due avanzamenti, un vincolo da ricordare. Per ogni turno il rapporto dice
quanto e' durata la risposta, quanto l'estrazione che la segue, quanti
strumenti il modello ha usato e quanto e' piena la finestra. Nessun
verdetto: e' una misura, e si legge accanto a quelle gia' in
docs/memory-quality.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evals.memory_quality import scrivi_json

if TYPE_CHECKING:
    from ares.config import Impostazioni

RADICE = Path(__file__).resolve().parents[1]

# (nome, messaggio dell'utente). Stessa trama della misura del 30 settembre
# 2026; i testi non ricalcano i casi degli altri due eval.
TURNI: tuple[tuple[str, str], ...] = (
    (
        "presentazione",
        "Ciao, sono Marco, faccio il sistemista in un piccolo studio a Verona e uso Debian sul portatile.",
    ),
    ("preferenze", "Preferisco risposte brevi, con gli esempi in codice quando servono, e dammi del tu."),
    (
        "piano",
        "Voglio mettere in ordine i backup dello studio: prima l'inventario dei dischi, poi uno script rsync "
        "notturno, infine una verifica mensile dei ripristini. Tienimi il piano.",
    ),
    ("avanzamento_1", "Ho finito l'inventario: tre dischi esterni da 4 TB, uno e' quasi pieno."),
    ("avanzamento_2", "Lo script rsync e' scritto e provato una volta a mano; stanotte parte da solo."),
    ("vincolo", "Ricordati che il disco rosso non va mai scollegato di giorno: ci lavora la contabilita'."),
)

MOTIVO_RIFIUTO = "Misura di latenza: nessuna azione viene autorizzata in questa prova."


def _secondi_e_token(elenco: Any) -> tuple[float, int, int]:
    """Secondi di Ollama (`total_duration`, in nanosecondi) e token di una lista di metriche Agno."""
    nanosecondi = 0.0
    entrata = uscita = 0
    for metriche in elenco or []:
        durata = (getattr(metriche, "provider_metrics", None) or {}).get("total_duration")
        if isinstance(durata, (int, float)):
            nanosecondi += durata
        entrata += getattr(metriche, "input_tokens", 0) or 0
        uscita += getattr(metriche, "output_tokens", 0) or 0
    return nanosecondi / 1e9, entrata, uscita


def _secondi_di_prefill(elenco: Any) -> float:
    """Secondi di `prompt_eval_duration` sommati: la lettura del prompt che la KV cache puo' risparmiare."""
    nanosecondi = 0.0
    for metriche in elenco or []:
        durata = (getattr(metriche, "provider_metrics", None) or {}).get("prompt_eval_duration")
        if isinstance(durata, (int, float)):
            nanosecondi += durata
    return nanosecondi / 1e9


def _finestra(risposta: Any) -> int:
    """Token del prompt dell'ultima chiamata al modello principale, come `cli/render.finestra_occupata`."""
    ultimo = 0
    for messaggio in getattr(risposta, "messages", None) or []:
        if getattr(messaggio, "role", None) != "assistant":
            continue
        token = getattr(getattr(messaggio, "metrics", None), "input_tokens", 0) or 0
        if token:
            ultimo = token
    return ultimo


def misura_turno(nome: str, risposta: Any, *, strumenti: int, appreso: int, secondi_turno: float) -> dict[str, Any]:
    """La riga del rapporto per un turno: tempi dei due modelli, token, finestra, strumenti.

    `risposta` e' il `RunOutput` del turno, o `None` se il turno non e'
    arrivato in fondo: allora i tempi dei modelli restano a zero e il turno
    intero li contiene comunque.
    """
    metriche = getattr(risposta, "metrics", None) if risposta is not None else None
    dettagli = getattr(metriche, "details", None) or {}
    risposta_s, _, risposta_out = _secondi_e_token(dettagli.get("model"))
    estrazione_s, estrazione_in, estrazione_out = _secondi_e_token(dettagli.get("learning_model"))
    return {
        "turno": nome,
        "risposta_s": round(risposta_s, 1),
        "prefill_s": round(_secondi_di_prefill(dettagli.get("model")), 1),
        "estrazione_s": round(estrazione_s, 1),
        "turno_s": round(secondi_turno, 1),
        "finestra_tok": _finestra(risposta),
        "risposta_tok_out": risposta_out,
        "estrazione_tok_in": estrazione_in,
        "estrazione_tok_out": estrazione_out,
        "strumenti": strumenti,
        "appreso": appreso,
        "testo": (getattr(risposta, "content", None) or "")[:120].replace("\n", " ") if risposta is not None else "",
    }


def _media(righe: list[dict[str, Any]], chiave: str) -> float | None:
    return round(sum(r[chiave] for r in righe) / len(righe), 1) if righe else None


def aggrega(turni: list[dict[str, Any]]) -> dict[str, Any]:
    """Le medie: su tutti i turni, sui soli turni senza strumenti, su quelli con.

    La distinzione conta: un turno in cui il modello scrive nel quaderno
    costa dieci volte una risposta, e una media sola li confonderebbe.
    """
    senza = [t for t in turni if not t["strumenti"]]
    con = [t for t in turni if t["strumenti"]]
    return {
        "turni": len(turni),
        "risposta_s": _media(turni, "risposta_s"),
        "estrazione_s": _media(turni, "estrazione_s"),
        "turno_s": _media(turni, "turno_s"),
        "senza_strumenti": {"turni": len(senza), "risposta_s": _media(senza, "risposta_s")},
        "con_strumenti": {"turni": len(con), "risposta_s": _media(con, "risposta_s")},
        "finestra_finale_tok": turni[-1]["finestra_tok"] if turni else 0,
    }


def markdown(rapporto: dict[str, Any]) -> str:
    m = rapporto["metadati"]
    righe = [
        "# Latenza per turno",
        "",
        f"Conversazione: {m['modello_conversazione']}. Estrazione: {m['modello_estrazione']}. "
        f"Contesto: {m['opzioni_conversazione']['num_ctx']}.",
        "",
        "| Turno | Risposta | Prefill | Estrazione | Turno intero | Finestra | Token in uscita | Strumenti | Appreso |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for t in rapporto["turni"]:
        righe.append(
            f"| {t['turno']} | {t['risposta_s']} s | {t['prefill_s']} s | {t['estrazione_s']} s | {t['turno_s']} s | "
            f"{t['finestra_tok']} | {t['risposta_tok_out']} | {t['strumenti']} | {t['appreso']} |"
        )
    r = rapporto["riepilogo"]

    def s(valore: float | None) -> str:
        return "—" if valore is None else f"{valore} s"

    righe += [
        "",
        f"Media su {r['turni']} turni: risposta {s(r['risposta_s'])}, estrazione {s(r['estrazione_s'])}, "
        f"turno {s(r['turno_s'])}.",
        f"Senza strumenti ({r['senza_strumenti']['turni']} turni): risposta {s(r['senza_strumenti']['risposta_s'])}. "
        f"Con strumenti ({r['con_strumenti']['turni']} turni): risposta {s(r['con_strumenti']['risposta_s'])}.",
        f"Finestra alla fine: {r['finestra_finale_tok']} token.",
    ]
    if rapporto.get("errore"):
        righe += ["", "Errore: " + rapporto["errore"]]
    return "\n".join(righe) + "\n"


def metadati(impostazioni: Impostazioni) -> dict[str, Any]:
    percorsi = [
        "ares/agent/prompts.py",
        "ares/agent/learning.py",
        "ares/agent/assistant.py",
        "ares/config.py",
        "evals/latenza.py",
        "uv.lock",
    ]
    return {
        "modello_conversazione": impostazioni.principale,
        "modello_estrazione": impostazioni.apprendimento,
        "opzioni_conversazione": impostazioni.opzioni,
        "opzioni_estrazione": impostazioni.opzioni_apprendimento,
        "think_conversazione": impostazioni.think,
        "think_estrazione": impostazioni.think_apprendimento,
        "estrazione_in_parallelo": impostazioni.estrazione_in_parallelo,
        "agno": version("agno"),
        "python": sys.version,
        "schema_rapporto": 1,
        "sorgenti_sha256": {p: hashlib.sha256((RADICE / p).read_bytes()).hexdigest() for p in percorsi},
        "limiti": "Una conversazione sola, sei turni fissi, nessuna conferma concessa. I tempi sono quelli "
        "di Ollama per le chiamate ai modelli; il turno intero comprende anche Agno e gli strumenti. "
        "Quanti strumenti il modello usa cambia da un giro all'altro: confrontare le medie senza e con.",
    }


class _ClienteMuto:
    """Un client di `esegui_turno` che non stampa, rifiuta ogni azione e conta gli strumenti."""

    presidiato = True

    def __init__(self) -> None:
        self.strumenti = 0

    @contextmanager
    def flusso(self):
        from ares.agent.turn_core import TurnEventKind

        def evento(e: Any) -> None:
            if e.kind is TurnEventKind.TOOL_STARTED:
                self.strumenti += 1

        yield evento

    def autorizza(self, richiesta: Any) -> Any:
        from ares.core.autorizzazioni import Decisione

        self.strumenti += 1
        return Decisione(False, MOTIVO_RIFIUTO)

    def negata(self, richiesta: Any) -> None:
        pass

    def concessa(self, richiesta: Any) -> None:
        pass

    def pausa_irrisolta(self) -> None:
        pass

    def rifiuti_esauriti(self, quanti: int) -> None:
        pass

    def interrotto(self) -> None:
        pass

    def guasto(self, errore: Exception) -> None:
        print("guasto nel turno:", errore, file=sys.stderr)

    def apprendimenti(self, righe: list[str], *, chiedi: bool) -> bool:
        return True


def worker(risultato: Path, quanti: int) -> None:
    """I turni, in una home temporanea preparata prima di importare Ares."""
    if "ares.config" in sys.modules:
        raise RuntimeError("Il worker richiede un processo senza ares.config gia' importato.")
    risultato = risultato.resolve()
    with tempfile.TemporaryDirectory(
        prefix="ares-latenza-worker-", dir=risultato.parent, ignore_cleanup_errors=True
    ) as cartella:
        root = Path(cartella)
        os.environ.update(
            ARES_HOME=str(root / "home"),
            ARES_TMP=str(root / "stato"),
            ARES_BACKUP_DIR=str(root / "backup"),
        )
        lavoro = root / "lavoro"
        lavoro.mkdir()
        os.chdir(lavoro)
        _worker(risultato, quanti)


def _worker(risultato: Path, quanti: int) -> None:
    from ares import config
    from ares.cli.log import configura_log_agno
    from ares.core.session import Sessioni
    from ares.core.turn import esegui_turno
    from ares.state.identita import Utente

    configura_log_agno(False)
    percorsi = config.leggi_percorsi()
    impostazioni = config.leggi_impostazioni()
    politica = config.leggi_politica()
    dati: dict[str, Any] = {"metadati": metadati(impostazioni), "turni": []}
    scrivi_json(risultato, dati)
    attiva = Sessioni(percorsi, impostazioni, politica, Utente.da_grezzo("eval-latenza"), presidiato=True).nuova(
        modo="manuale"
    )
    for nome, testo in TURNI[:quanti]:
        cliente = _ClienteMuto()
        inizio = time.monotonic()
        esito = esegui_turno(percorsi, politica, attiva.agente, testo, cliente)
        riga = misura_turno(
            nome,
            esito.risposta,
            strumenti=cliente.strumenti,
            appreso=len(esito.appreso),
            secondi_turno=time.monotonic() - inizio,
        )
        dati["turni"].append(riga)
        print(json.dumps(riga, ensure_ascii=False), flush=True)
        # Il rapporto parziale sopravvive a un timeout del padre.
        scrivi_json(risultato, dati)


def esegui(percorso: Path, quanti: int, timeout: int) -> dict[str, Any]:
    """Il worker in un processo nuovo; il rapporto parziale vale anche se il figlio non finisce."""
    with tempfile.TemporaryDirectory(prefix="ares-latenza-") as cartella:
        root = Path(cartella)
        risultato = root / "risultato.json"
        ambiente = {**os.environ, "PYTHONPATH": str(RADICE)}
        comando = [
            sys.executable,
            "-m",
            "evals.latenza",
            "--worker",
            "--turni",
            str(quanti),
            "--report",
            str(risultato),
        ]
        errore = None
        try:
            figlio = subprocess.run(comando, cwd=root, env=ambiente, capture_output=True, text=True, timeout=timeout)
            log = (figlio.stdout + figlio.stderr)[-8000:]
            if figlio.returncode:
                errore = f"Processo terminato con codice {figlio.returncode}."
        except subprocess.TimeoutExpired:
            log, errore = "", f"Timeout dopo {timeout} secondi."
        try:
            dati = json.loads(risultato.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            dati = {"metadati": {}, "turni": []}
            errore = errore or "Rapporto del processo mancante o non valido."
        if len(dati.get("turni", [])) < quanti:
            errore = errore or f"Completati {len(dati.get('turni', []))} turni su {quanti}."
        dati.update(log=log, errore=errore)
        return dati


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m evals.latenza",
        description=__doc__,
        epilog="Codice d'uscita: 0 misura completa, 1 un errore o un turno mancante.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--turni",
        type=int,
        default=len(TURNI),
        help=f"quanti dei {len(TURNI)} turni eseguire, dal primo; tutti se omesso",
    )
    p.add_argument("--timeout", type=int, default=3600, help="secondi massimi per l'intera conversazione")
    p.add_argument(
        "--report",
        type=Path,
        help="nuovo file .json, accanto il .md; di serie in artifacts/latenza/ con data e ora",
    )
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    return p


def main() -> int:
    p = parser()
    args = p.parse_args()
    if not 1 <= args.turni <= len(TURNI) or args.timeout < 1:
        p.error(f"--turni va da 1 a {len(TURNI)} e --timeout deve essere positivo")
    if args.worker:
        if args.report is None:
            p.error("--worker richiede --report")
        worker(args.report, args.turni)
        return 0

    percorso = (
        args.report or RADICE / "artifacts" / "latenza" / (datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json")
    ).resolve()
    if percorso.suffix != ".json":
        p.error("--report deve avere estensione .json")
    if percorso.exists() or percorso.with_suffix(".md").exists():
        p.error("il rapporto esiste gia': scegli un nuovo percorso")
    percorso.parent.mkdir(parents=True, exist_ok=True)
    print(f"{args.turni} turni...", flush=True)
    dati = esegui(percorso, args.turni, args.timeout)
    rapporto: dict[str, Any] = {
        "avvio_utc": datetime.now(UTC).isoformat(),
        "metadati": dati["metadati"],
        "turni": dati["turni"],
        "riepilogo": aggrega(dati["turni"]),
        "errore": dati["errore"],
        "log": dati["log"],
    }
    scrivi_json(percorso, rapporto)
    if rapporto["metadati"]:
        testo = markdown(rapporto)
        percorso.with_suffix(".md").write_text(testo, encoding="utf-8")
        print(testo)
    print("Rapporto:", percorso)
    if dati["errore"]:
        print("Errore:", dati["errore"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
