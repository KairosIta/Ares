"""Ciclo di vita della sessione: quale conversazione aprire e con quale agente.

E' l'unico punto che decide l'id di una conversazione nuova, verifica il
proprietario di una sessione esistente e ricostruisce l'agente quando cambia
la sessione o la modalita'. Il client (la CLI, o uno senza terminale) sceglie
cosa chiedere e come mostrarlo; qui non si stampa niente.

Il servizio non ha stato proprio oltre la configurazione: la sessione attiva
la tiene il client, che riceve una `SessioneAttiva` a ogni apertura.
"""

from dataclasses import dataclass
from typing import Any

from agno.agent import Agent

from ares import config
from ares.agent.assistant import build_assistant
from ares.config import Impostazioni, Percorsi, Politica
from ares.core.autorizzazioni import verifica_modo
from ares.core.id_sessione import nuovo_id_sessione
from ares.state.archivi import build_db
from ares.state.identita import Utente
from ares.state.stores import con_run, sessione_di_altri, sessioni_della_cartella


class SessioneDiAltri(PermissionError):
    """La sessione esiste, ma appartiene a un altro utente."""

    def __init__(self, nome: str) -> None:
        super().__init__("La sessione '" + nome + "' appartiene a un altro utente.")
        self.nome = nome


@dataclass(frozen=True)
class SessioneAttiva:
    """Una sessione aperta, con l'agente costruito su di essa."""

    id: str
    modo: str
    agente: Agent


class Sessioni:
    """Apre, riprende e cambia le conversazioni di un utente.

    `percorsi.lavoro` e' la cartella a cui le sessioni si legano quando lo
    spazio di lavoro e' acceso. `presidiato` e' la presenza dichiarata dal
    client e vale per ogni agente costruito: senza, niente apprendimento
    (nessuno leggerebbe l'eco) e niente modalita' che scrivono in silenzio.

    Il client lo costruisce con lo stato gia' in uso (`core/stato.py`);
    costruirlo prepara la directory dello stato.
    """

    def __init__(
        self,
        percorsi: Percorsi,
        impostazioni: Impostazioni,
        politica: Politica,
        utente: Utente,
        *,
        presidiato: bool,
        debug: bool = False,
    ) -> None:
        self.percorsi = percorsi
        self.impostazioni = impostazioni
        self.politica = politica
        self.utente = utente
        self.debug = debug
        self.presidiato = presidiato
        self._db: Any = None
        config.prepara_archivio(percorsi)

    @property
    def db(self) -> Any:
        """Il database delle sessioni, aperto alla prima lettura."""
        if self._db is None:
            self._db = build_db(self.percorsi)
        return self._db

    @property
    def con_cartella(self) -> bool:
        """Vero se le sessioni si legano a una cartella di lavoro."""
        return self.politica.workspace.attivo

    def id_nuovo(self) -> str:
        """L'id di una conversazione nuova: dalla cartella, o `principale` senza."""
        if self.con_cartella:
            return nuovo_id_sessione(self.percorsi.lavoro)
        return "principale"

    def della_cartella(self) -> list[Any]:
        """Le sessioni nate nella cartella di lavoro, dalla piu' recente, senza i run.

        Vuoto senza spazio di lavoro. Per contare gli scambi o leggere la
        prima domanda serve `con_scambi`.
        """
        if not self.con_cartella:
            return []
        return sessioni_della_cartella(self.db, self.utente, self.percorsi.lavoro)

    def con_scambi(self, sessioni: list[Any]) -> list[Any]:
        """Le stesse sessioni rilette con i loro run."""
        return [con_run(self.db, sessione) for sessione in sessioni]

    def apri(self, nome: str, *, modo: str | None = None) -> SessioneAttiva:
        """Apre la sessione `nome`, nuova o esistente, nella modalita' data.

        Solleva `SessioneDiAltri` se appartiene a un altro utente e
        `ModoNonAmmesso` se la modalita' non e' ammessa senza presenza: in
        entrambi i casi l'agente non viene costruito. `modo` vuoto vale
        `config.MODO_PREDEFINITO`.
        """
        if not nome:
            raise ValueError("Il nome della sessione e' vuoto.")
        if sessione_di_altri(self.db, nome, self.utente):
            raise SessioneDiAltri(nome)
        modo = modo or config.MODO_PREDEFINITO
        verifica_modo(modo, presidiato=self.presidiato)
        agente = build_assistant(
            self.percorsi,
            self.impostazioni,
            self.politica,
            self.utente,
            session_id=nome,
            debug=self.debug,
            interattivo=self.presidiato,
            modo=modo,
        )
        return SessioneAttiva(id=nome, modo=modo, agente=agente)

    def nuova(self, *, modo: str | None = None) -> SessioneAttiva:
        """Apre una conversazione nuova con un id generato."""
        return self.apri(self.id_nuovo(), modo=modo)

    def cambia_modo(self, attiva: SessioneAttiva, modo: str) -> SessioneAttiva:
        """La stessa sessione in un'altra modalita'.

        Gli strumenti sono fissati alla costruzione dello spazio di lavoro e
        descritti nel prompt: cambiare modalita' vuol dire ricostruire l'agente.
        Solleva `ValueError` per una modalita' sconosciuta e `ModoNonAmmesso`
        per una che a sessione aperta non si sceglie.
        """
        verifica_modo(modo, presidiato=self.presidiato, in_corso=True)
        return self.apri(attiva.id, modo=modo)
