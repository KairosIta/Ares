"""Chi puo' autorizzare cosa: le modalita' ammesse e le conferme degli strumenti.

La presenza la dichiara il client: vero se qualcuno legge le richieste e
risponde. Senza presenza nessuna modalita' scrive in silenzio e ogni
conferma vale no, senza chiedere niente al client: un testo ostile arrivato
dallo stesso flusso dell'input non deve poter autorizzare cio' che chiede.

Il client decide solo come mostrare una richiesta e come raccogliere la
risposta; `confirm()` e `reject()` sui requirement di Agno li chiama il
nucleo.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from agno.run.agent import RunOutput

from ares import config
from ares.config import Percorsi, Politica


class ModoNonAmmesso(ValueError):
    """La modalita' esiste, ma qui non si puo' scegliere.

    `motivo` e' `"presenza"` se scriverebbe in silenzio senza nessuno che
    guardi, `"avvio"` se si sceglie solo aprendo la sessione.
    """

    def __init__(self, modo: str, motivo: Literal["presenza", "avvio"]) -> None:
        super().__init__("modalita' non ammessa: " + modo + " (" + motivo + ")")
        self.modo = modo
        self.motivo = motivo


def verifica_modo(modo: str, *, presidiato: bool, in_corso: bool = False) -> None:
    """Solleva `ModoNonAmmesso` se `modo` qui non si puo' usare, `ValueError` se non esiste.

    `auto` si sceglie solo all'apertura (`in_corso` falso): passarci a
    sessione aperta toglierebbe ogni conferma a meta' di un lavoro. Senza
    presenza la regola legge la tabella delle modalita', non i nomi.
    """
    config.liste_modalita(modo)
    if in_corso and modo == "auto":
        raise ModoNonAmmesso(modo, "avvio")
    if not presidiato and config.modalita_scrive_in_silenzio(modo):
        raise ModoNonAmmesso(modo, "presenza")


@dataclass(frozen=True)
class Richiesta:
    """Uno strumento che aspetta il permesso, descritto senza oggetti Agno.

    `radice` e' la cartella di lavoro per gli strumenti dello spazio di
    lavoro, `None` per quelli del quaderno.
    """

    strumento: str
    argomenti: Mapping[str, Any] = field(default_factory=dict)
    radice: Path | None = None


@dataclass(frozen=True)
class Decisione:
    """La risposta a una `Richiesta`. Il motivo di un rifiuto arriva al modello."""

    consenti: bool
    motivo: str | None = None


class Autorizzatore(Protocol):
    """La parte di un client che risponde alle richieste."""

    # Vero se qualcuno legge le richieste e risponde.
    presidiato: bool

    def autorizza(self, richiesta: Richiesta) -> Decisione:
        """Mostra la richiesta e raccoglie la risposta. Chiamato solo con presenza."""
        ...

    def negata(self, richiesta: Richiesta) -> None:
        """Mostra una richiesta che il nucleo ha rifiutato perche' nessuno puo' rispondere."""
        ...


def richiesta_di(esecuzione: Any, percorsi: Percorsi, politica: Politica) -> Richiesta:
    """La `Richiesta` per una tool execution di Agno.

    Il prefisso dalla politica distingue un file della cartella da uno del
    quaderno.
    """
    nome = str(esecuzione.tool_name or "")
    radice = percorsi.lavoro if nome.startswith(politica.workspace.prefisso) else None
    return Richiesta(strumento=nome, argomenti=dict(esecuzione.tool_args or {}), radice=radice)


def risolvi_pausa(output: RunOutput, cliente: Autorizzatore, percorsi: Percorsi, politica: Politica) -> int:
    """Conferma o rifiuta gli strumenti in pausa. Restituisce quanti ne ha risolti.

    Zero ferma il ciclo: una pausa che qui non si sa gestire farebbe fermare
    `continue_run` allo stesso punto all'infinito.
    """
    risolti = 0
    for requisito in output.active_requirements or []:
        if not requisito.needs_confirmation:
            continue
        richiesta = richiesta_di(requisito.tool_execution, percorsi, politica)
        if cliente.presidiato:
            decisione = cliente.autorizza(richiesta)
        else:
            cliente.negata(richiesta)
            decisione = Decisione(consenti=False)
        if decisione.consenti:
            requisito.confirm()
        else:
            # Il motivo arriva al modello: senza, ritenterebbe una variante
            # dello stesso comando.
            requisito.reject(decisione.motivo or None)
        risolti += 1
    return risolti
