"""Il servizio di sessione, usato da un client senza terminale
==============================================================

`ares.core.session` decide id, proprietario e ricostruzione dell'agente; la
CLI e' solo uno dei suoi client. Qui lo si usa come farebbe una UI senza
terminale: nessuna stampa, nessun input, solo chiamate e valori restituiti.

Offline: nessuna chiamata al modello. L'agente vero si costruisce una volta,
per verificare cio' che il servizio gli passa; altrove il costruttore e'
sostituito da uno che registra.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from _comune import chiudi, esegui, esigi, prepara_ambiente

RADICE_PROVA = prepara_ambiente("nucleo-test")

from agno.session.agent import AgentSession  # noqa: E402

from ares import config  # noqa: E402
from ares.core import session as nucleo  # noqa: E402
from ares.core.id_sessione import nuovo_id_sessione  # noqa: E402
from ares.core.session import SessioneDiAltri, Sessioni  # noqa: E402
from ares.state.identita import Utente  # noqa: E402

UTENTE = Utente.da_grezzo("prova-nucleo")
ALTRO = Utente.da_grezzo("prova-nucleo-altro")
PERCORSI = config.leggi_percorsi()
IMPOSTAZIONI = config.leggi_impostazioni()
POLITICA = config.leggi_politica()


def _servizio(*, cartella: bool = True, interattivo: bool = False) -> Sessioni:
    politica = POLITICA
    if not cartella:
        politica = replace(POLITICA, workspace=replace(POLITICA.workspace, attivo=False))
    return Sessioni(PERCORSI, IMPOSTAZIONI, politica, UTENTE, interattivo=interattivo)


class Registro:
    """Sostituto di `build_assistant` che ricorda con cosa e' stato chiamato."""

    def __init__(self) -> None:
        self.chiamate: list[dict] = []

    def __call__(self, percorsi, impostazioni, politica, utente, **opzioni):
        self.chiamate.append({"utente": utente, **opzioni})
        return object()


def id_delle_sessioni() -> str:
    """Con la cartella l'id viene da cartella e momento; senza, e' `principale`."""
    momento = datetime(2026, 9, 7, 9, 15, 30)
    esigi(
        nuovo_id_sessione(Path("/x/Mio Progetto_2"), momento, suffisso="abc123")
        == "mio-progetto-2-20260907-091530-abc123",
        "l'id non normalizza il nome della cartella",
    )
    esigi(
        nuovo_id_sessione(Path("/progetti/api"), momento) != nuovo_id_sessione(Path("/progetti/api"), momento),
        "due avvii nello stesso secondo danno lo stesso id",
    )
    con = _servizio().id_nuovo()
    esigi(con.startswith(PERCORSI.lavoro.name.casefold()[:10]), "l'id non nomina la cartella: " + con)
    esigi(_servizio(cartella=False).id_nuovo() == "principale", "senza cartella l'id non e' principale")
    return "id dalla cartella, principale senza"


def apertura_e_modo() -> str:
    """`nuova`, `apri` e `cambia_modo` passano al costruttore sessione, modo e presenza."""
    registro = Registro()
    servizio = _servizio(interattivo=False)
    with patch.object(nucleo, "build_assistant", registro):
        nuova = servizio.nuova()
        esigi(nuova.modo == config.MODO_PREDEFINITO, "la modalita' predefinita non e' quella di config")
        esigi(registro.chiamate[-1]["session_id"] == nuova.id, "l'agente non e' costruito sulla sessione nuova")
        esigi(registro.chiamate[-1]["interattivo"] is False, "un client senza terminale riceve un agente che apprende")

        piano = servizio.cambia_modo(nuova, "piano")
        esigi(piano.id == nuova.id and piano.modo == "piano", "cambiare modalita' cambia sessione")
        esigi(registro.chiamate[-1]["modo"] == "piano", "la modalita' nuova non arriva al costruttore")

        costruiti = len(registro.chiamate)
        try:
            servizio.cambia_modo(nuova, "inesistente")
        except ValueError:
            pass
        else:
            raise AssertionError("una modalita' sconosciuta viene accettata")
        esigi(len(registro.chiamate) == costruiti, "una modalita' sconosciuta costruisce l'agente")
        try:
            servizio.apri("")
        except ValueError:
            pass
        else:
            raise AssertionError("un nome vuoto apre una sessione")
    return "nuova, cambio di modalita', nomi e modalita' non validi"


def sessione_altrui() -> str:
    """La sessione di un altro utente solleva `SessioneDiAltri` senza costruire l'agente."""
    servizio = _servizio()
    servizio.db.upsert_session(AgentSession(session_id="di-altri", user_id=ALTRO.id))
    registro = Registro()
    with patch.object(nucleo, "build_assistant", registro):
        try:
            servizio.apri("di-altri")
        except SessioneDiAltri as errore:
            esigi(errore.nome == "di-altri", "l'errore non nomina la sessione")
        else:
            raise AssertionError("la sessione di un altro utente si apre")
        esigi(registro.chiamate == [], "l'agente nasce anche per una sessione altrui")
        esigi(servizio.apri("mia").id == "mia", "una sessione nuova col nome scelto non si apre")
    return "rifiutata prima di costruire l'agente"


def sessioni_della_cartella() -> str:
    """Solo quelle di questa cartella e di questo utente, dalla piu' recente."""
    servizio = _servizio()
    qui = str(PERCORSI.lavoro)
    for nome, utente, dove, quando in (
        ("qui-vecchia", UTENTE, qui, 1_000),
        ("qui-nuova", UTENTE, qui, 2_000),
        ("altrove", UTENTE, "/altrove", 3_000),
        ("qui-di-altri", ALTRO, qui, 4_000),
    ):
        servizio.db.upsert_session(
            AgentSession(
                session_id=nome, user_id=utente.id, metadata={"cartella": dove}, created_at=quando, updated_at=quando
            )
        )
    nomi = [s.session_id for s in servizio.della_cartella()]
    esigi(nomi == ["qui-nuova", "qui-vecchia"], "sessioni della cartella sbagliate: " + str(nomi))
    esigi(_servizio(cartella=False).della_cartella() == [], "senza cartella compare un elenco")
    return "filtro per cartella e utente, ordine per recenza"


def agente_vero() -> str:
    """L'agente costruito davvero porta sessione e utente, e senza terminale non apprende."""
    attiva = _servizio(interattivo=False).apri("vera")
    esigi(attiva.agente.session_id == "vera", "l'agente non e' sulla sessione aperta")
    esigi(attiva.agente.user_id == UTENTE.id, "l'agente non e' dell'utente del servizio")
    esigi(not attiva.agente.post_hooks, "senza terminale resta il post-hook di apprendimento")
    return "sessione, utente e niente post-hook"


PROVE = (
    ("id", id_delle_sessioni),
    ("apertura e modo", apertura_e_modo),
    ("sessione altrui", sessione_altrui),
    ("della cartella", sessioni_della_cartella),
    ("agente vero", agente_vero),
)


def main() -> int:
    falliti, _ = esegui(PROVE)
    return chiudi(falliti, RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
