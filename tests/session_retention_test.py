"""Offloading e ciclo di vita delle sessioni, senza Ollama.

La prova attraversa un vero ``Agent.run()`` con un modello deterministico,
poi usa la CLI offline per eliminare la sessione e il restore per rimetterla.
Tutto vive in una directory temporanea scelta prima di importare ``config``.
"""

import gc
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

RADICE_PROGETTO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE_PROGETTO))

RADICE_PROVA = Path(tempfile.mkdtemp(prefix="ares-session-retention-test-"))
os.environ["ARES_TMP"] = str(RADICE_PROVA / "stato")
os.environ["ARES_BACKUP_DIR"] = str(RADICE_PROVA / "backup")
os.environ["ARES_WORKSPACE"] = str(RADICE_PROVA / "lavoro")

from agno.fs import FileSystem  # noqa: E402
from agno.models.base import Model  # noqa: E402
from agno.models.message import MessageMetrics  # noqa: E402
from agno.models.response import ModelResponse  # noqa: E402
from sqlalchemy import text  # noqa: E402

import config  # noqa: E402
from assistant import build_assistant, build_db  # noqa: E402
from backup import elenco_snapshot, verifica_snapshot  # noqa: E402
from session_retention import apri_archivio  # noqa: E402
from state_lock import lock_stato  # noqa: E402

UTENTE = "prova-retention"
ALTRO_UTENTE = "prova-retention-altro"
SESSIONE_VECCHIA = "progetto-concluso"
SESSIONE_RECENTE = "principale"
SESSIONE_ALTRUI = "altrui-vecchia"
PAYLOAD = "\n".join("riga " + str(numero) + ": " + "x" * 80 for numero in range(350))


def esigi(condizione: object, messaggio: str) -> None:
    if not condizione:
        raise AssertionError(messaggio)


def ok(nome: str, nota: str) -> None:
    print("ok      ", nome.ljust(22), "-", nota)


class ModelloToolDeterministico(Model):
    """Chiede una volta ``fetch_page`` e poi conclude il turno."""

    def __init__(self) -> None:
        super().__init__(id="scripted-offload", name="scripted-offload", provider="test")
        self.chiamate = 0

    def _prossima(self) -> ModelResponse:
        self.chiamate += 1
        if self.chiamate == 1:
            return ModelResponse(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call-offload",
                        "type": "function",
                        "function": {"name": "fetch_page", "arguments": "{}"},
                    }
                ],
                response_usage=MessageMetrics(),
            )
        return ModelResponse(role="assistant", content="fatto", response_usage=MessageMetrics())

    def invoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._prossima()

    async def ainvoke(self, *args: Any, **kwargs: Any) -> ModelResponse:
        return self._prossima()

    def invoke_stream(self, *args: Any, **kwargs: Any) -> Iterator[ModelResponse]:
        yield self._prossima()

    async def ainvoke_stream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ModelResponse]:
        yield self._prossima()

    def _parse_provider_response(self, response: Any, **kwargs: Any) -> ModelResponse:
        return response

    def _parse_provider_response_delta(self, response: Any) -> ModelResponse:
        return response


def fetch_page() -> str:
    """Restituisce una pagina abbastanza grande da essere offloaded."""
    return PAYLOAD


def agente(user_id: str, session_id: str):
    costruito = build_assistant(user_id=user_id, session_id=session_id)
    costruito.model = ModelloToolDeterministico()
    costruito.tools = [*list(costruito.tools or []), fetch_page]
    return costruito


def esegui_offload(agent, session_id: str, user_id: str) -> tuple[str, dict[str, Any]]:
    agent.model = ModelloToolDeterministico()
    output = agent.run("vai", session_id=session_id, user_id=user_id)
    messaggi_tool = [messaggio for messaggio in output.messages or [] if messaggio.role == "tool"]
    esigi(len(messaggi_tool) == 1, "il run non contiene un solo risultato tool")
    envelope = str(messaggi_tool[0].content)
    trovato = re.search(r'<result id="([^"]+)"', envelope)
    esigi(trovato is not None, "il risultato grande non e' stato sostituito da un envelope")
    result_id = trovato.group(1)
    store = agent.result_store
    esigi(store is not None, "il vero Agent.run non ha inizializzato il ResultStore")
    esigi(store.ttl_seconds is None, "un TTL puo' spezzare i riferimenti di una sessione conservata")
    riga = store.get_row(result_id)
    esigi(riga is not None, "l'envelope non ha una riga indice")
    esigi(store.payload(result_id) == PAYLOAD, "il payload attraversato da Agent.run non e' lossless")
    return result_id, riga


def imposta_ultimo_uso(db, session_id: str, timestamp: int) -> None:
    with db.db_engine.begin() as connessione:
        connessione.execute(
            text("update agno_sessions set created_at = :t, updated_at = :t where session_id = :s"),
            {"t": timestamp, "s": session_id},
        )


def esegui_cli(*argomenti: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "session_maintenance.py", *argomenti],
        cwd=config.BASE_DIR,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def chiudi_engine(*oggetti: object) -> None:
    visti = set()
    for oggetto in oggetti:
        engine = getattr(oggetto, "db_engine", None)
        if engine is not None and id(engine) not in visti:
            engine.dispose()
            visti.add(id(engine))


def main() -> int:
    # L'apprendimento e LanceDB non fanno parte di questa prova. Spegnerli
    # impedisce che un test dichiarato offline accenda Ollama di nascosto.
    config.LEARN_USER_PROFILE = False
    config.LEARN_USER_MEMORY = False
    config.LEARN_SESSION_CONTEXT = False
    config.LEARN_ENTITIES = False
    config.LEARN_KNOWLEDGE = False
    config.WORKSPACE = False
    config.OLLAMA_HOST = "http://127.0.0.1:1"

    riuscita = False
    try:
        solo_db = build_db()
        esigi(not Path(config.FS_DB_FILE).exists(), "aprire il solo archivio sessioni ha creato filesystem.db")
        stato_vuoto = esegui_cli("status", "--user", UTENTE)
        esigi(stato_vuoto.returncode == 0 and "Sessioni: 0" in stato_vuoto.stdout, "status vuoto fallito")
        esigi(not Path(config.FS_DB_FILE).exists(), "uno status in sola lettura ha creato filesystem.db")
        chiudi_engine(solo_db)
        ok("status puro", "nessun payload backend creato per una lettura")

        principale = agente(UTENTE, SESSIONE_VECCHIA)
        vecchio_id, vecchia_riga = esegui_offload(principale, SESSIONE_VECCHIA, UTENTE)
        recente_id, _ = esegui_offload(principale, SESSIONE_RECENTE, UTENTE)
        altrui = agente(ALTRO_UTENTE, SESSIONE_ALTRUI)
        altrui_id, _ = esegui_offload(altrui, SESSIONE_ALTRUI, ALTRO_UTENTE)

        adesso = int(time.time())
        imposta_ultimo_uso(principale.db, SESSIONE_VECCHIA, adesso - 365 * 86_400)
        imposta_ultimo_uso(principale.db, SESSIONE_RECENTE, adesso)
        imposta_ultimo_uso(altrui.db, SESSIONE_ALTRUI, adesso - 365 * 86_400)
        principale.db.upsert_learning(
            id="session_context_" + SESSIONE_VECCHIA,
            learning_type="session_context",
            content={"summary": "contesto da ripristinare"},
            # Simula una riga storica priva del proprietario: la retention e'
            # per session_id e deve rimuoverla comunque.
            user_id=None,
            session_id=SESSIONE_VECCHIA,
        )

        sessione_salvata = principale.db.get_session(session_id=SESSIONE_VECCHIA, deserialize=False)
        esigi(
            PAYLOAD not in json.dumps(sessione_salvata, default=str),
            "la sessione persistita contiene il payload intero",
        )
        esigi(len(PAYLOAD) > config.TOOL_RESULT_THRESHOLD_CHARS, "il payload non supera piu' la soglia Ares")
        ok("Agent.run", str(len(PAYLOAD)) + " caratteri sostituiti e riletti")

        stato = esegui_cli("status", "--user", UTENTE)
        esigi(stato.returncode == 0, "status fallito: " + stato.stderr)
        esigi("Sessioni: 2" in stato.stdout and "Offload indicizzati: 2" in stato.stdout, "status incompleto")

        anteprima = esegui_cli("prune", "--user", UTENTE, "--older-than", "180")
        esigi(anteprima.returncode == 0, "anteprima prune fallita: " + anteprima.stderr)
        esigi(SESSIONE_VECCHIA in anteprima.stdout, "la sessione inattiva non compare nell'anteprima")
        esigi(SESSIONE_RECENTE not in anteprima.stdout.split("Protette:")[0], "la sessione recente e' candidata")
        esigi(
            principale.db.get_session(session_id=SESSIONE_VECCHIA) is not None, "l'anteprima ha cancellato la sessione"
        )
        esigi(not elenco_snapshot(), "l'anteprima ha creato uno snapshot")
        ok("anteprima", "una candidata, principale protetta, nessuna scrittura")

        protetta = esegui_cli("delete", SESSIONE_RECENTE, "--user", UTENTE)
        esigi(protetta.returncode == 0, "anteprima delete fallita: " + protetta.stderr)
        esigi("protetta dal prune" in protetta.stdout, "la cancellazione esatta non segnala la protezione")
        esigi(
            principale.db.get_session(session_id=SESSIONE_RECENTE) is not None,
            "delete senza --apply ha scritto",
        )
        yes_solo = esegui_cli("prune", "--user", UTENTE, "--older-than", "180", "--yes")
        esigi(
            yes_solo.returncode == 2 and "--yes richiede --apply" in yes_solo.stderr,
            "--yes da solo accettato",
        )

        with lock_stato(esclusivo=False):
            bloccata = esegui_cli(
                "prune",
                "--user",
                UTENTE,
                "--older-than",
                "180",
                "--apply",
                "--yes",
            )
        esigi(
            bloccata.returncode == 2 and "Chiudi la chat" in bloccata.stderr,
            "prune partito con una chat aperta",
        )
        esigi(
            principale.db.get_session(session_id=SESSIONE_VECCHIA) is not None,
            "prune bloccato ha scritto",
        )
        esigi(not elenco_snapshot(), "prune bloccato ha creato uno snapshot")
        ok("guardie CLI", "delete protetta esplicita, --yes vincolato e lock esclusivo")

        applicazione = esegui_cli("prune", "--user", UTENTE, "--older-than", "180", "--apply", "--yes")
        esigi(applicazione.returncode == 0, "prune fallito: " + applicazione.stderr + applicazione.stdout)
        snapshot = elenco_snapshot()
        esigi(len(snapshot) == 1, "il prune non ha creato esattamente uno snapshot")
        verifica_snapshot(snapshot[0], percorso_diretto=True)
        store = principale.result_store
        esigi(store is not None, "store perso dopo il run")
        esigi(principale.db.get_session(session_id=SESSIONE_VECCHIA) is None, "sessione inattiva ancora presente")
        esigi(principale.db.get_session(session_id=SESSIONE_RECENTE) is not None, "sessione recente eliminata")
        esigi(
            altrui.db.get_session(session_id=SESSIONE_ALTRUI, user_id=ALTRO_UTENTE) is not None, "utente altrui toccato"
        )
        esigi(store.get_row(vecchio_id) is None, "indice dell'offload eliminato ancora presente")
        fs_vecchio = FileSystem(backend=store.fs.backend, namespace=str(vecchia_riga["namespace"]))
        esigi(fs_vecchio.read(str(vecchia_riga["path"])) is None, "payload dell'offload eliminato ancora presente")
        esigi(store.get_row(recente_id) is not None, "offload della sessione conservata eliminato")
        esigi(altrui.result_store.get_row(altrui_id) is not None, "offload dell'altro utente eliminato")
        esigi(
            principale.db.get_learning(
                learning_type="session_context",
                session_id=SESSIONE_VECCHIA,
            )
            is None,
            "contesto della sessione eliminata ancora presente",
        )
        ok("cascata", "sessione, run, contesto, indice e payload eliminati")

        # Il limite per singolo risultato e' una quota Agno, non la soglia
        # Ares. Il fallback deve essere esplicito e non promettere lossless.
        from agno.offload.store import MAX_RESULT_BYTES

        rifiutato = store.offload_for_model(
            session_id="prova-quota",
            run_id="run-quota",
            tool_call_id="call-quota",
            tool_name="fetch_page",
            tool_args={},
            output="q" * (MAX_RESULT_BYTES + 1),
            user_id=UTENTE,
        )
        esigi('stored="false"' in rifiutato, "il superamento quota non e' dichiarato")
        esigi(len(rifiutato) < 5_000, "il fallback di quota gonfia comunque il contesto")
        esigi(store.live_ids("prova-quota") == [], "un risultato rifiutato ha lasciato un indice")
        ok("quota Agno", str(MAX_RESULT_BYTES) + " byte: rifiuto esplicito e compatto")

        # Il restore sostituisce la directory dello stato: chiudere gli engine
        # simula l'uso reale e rende la prova valida anche su Windows.
        chiudi_engine(
            principale.db,
            store.fs.backend,
            altrui.db,
            altrui.result_store.fs.backend,
        )
        del principale, altrui, store, fs_vecchio
        gc.collect()
        ripristino = subprocess.run(
            [sys.executable, "backup.py", "restore", snapshot[0].name, "--yes", "--skip-safety"],
            cwd=config.BASE_DIR,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        esigi(ripristino.returncode == 0, "restore fallito: " + ripristino.stderr + ripristino.stdout)
        db_ripristinato, store_ripristinato = apri_archivio(UTENTE)
        esigi(db_ripristinato.get_session(session_id=SESSIONE_VECCHIA) is not None, "sessione non ripristinata")
        esigi(store_ripristinato.payload(vecchio_id) == PAYLOAD, "payload offloaded non ripristinato")
        esigi(
            db_ripristinato.get_learning(
                learning_type="session_context",
                session_id=SESSIONE_VECCHIA,
            )
            is not None,
            "contesto della sessione non ripristinato",
        )
        chiudi_engine(db_ripristinato, store_ripristinato.fs.backend)
        ok("backup/restore", "kairos.db e filesystem.db ricongiunti senza perdita")
        riuscita = True
        return 0
    except Exception as errore:
        print("FALLITO ", type(errore).__name__ + ":", errore)
        return 1
    finally:
        if riuscita:
            shutil.rmtree(RADICE_PROVA, ignore_errors=True)
        else:
            print("Archivio della prova conservato:", RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
