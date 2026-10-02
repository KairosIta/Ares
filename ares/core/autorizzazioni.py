"""Chi puo' autorizzare cosa: le modalita' ammesse e le conferme degli strumenti.

La presenza la dichiara il client: vero se qualcuno legge le richieste e
risponde. Senza presenza nessuna modalita' scrive in silenzio e ogni
conferma vale no, senza chiedere niente al client: un testo ostile arrivato
dallo stesso flusso dell'input non deve poter autorizzare cio' che chiede.

Il client decide solo come mostrare una richiesta e come raccogliere la
risposta; `confirm()` e `reject()` sui requirement di Agno li chiama il
nucleo. Prima di chiedere, il nucleo consulta le regole che la persona ha
scritto per i comandi (`core/regole.py`): una regola `consenti` tace la
conferma, una `nega` rifiuta senza chiedere, e il client vede entrambe.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol

from agno.run.agent import RunOutput

from ares import config
from ares.config import Percorsi, Politica
from ares.core.regole import Regola, Regole, leggi_regole


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
    lavoro, `None` per quelli del quaderno. `regola` e' la regola della
    persona che ha deciso al posto suo, se una l'ha fatto.
    """

    strumento: str
    argomenti: Mapping[str, Any] = field(default_factory=dict)
    radice: Path | None = None
    regola: Regola | None = None


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
        """Mostra una richiesta rifiutata dal nucleo da se': senza presenza, o per la regola in `richiesta.regola`."""
        ...

    def concessa(self, richiesta: Richiesta) -> None:
        """Mostra una richiesta che il nucleo ha concesso da se', per la regola in `richiesta.regola`."""
        ...


def richiesta_di(esecuzione: Any, percorsi: Percorsi, politica: Politica) -> Richiesta:
    """La `Richiesta` per una tool execution di Agno.

    Il prefisso dalla politica distingue un file della cartella da uno del
    quaderno.
    """
    nome = str(esecuzione.tool_name or "")
    radice = percorsi.lavoro if nome.startswith(politica.workspace.prefisso) else None
    return Richiesta(strumento=nome, argomenti=dict(esecuzione.tool_args or {}), radice=radice)


def avviso_ultimo_rifiuto(tetto: int) -> str:
    """La coda al motivo del rifiuto numero `tetto`: il modello deve rispondere, non riprovare."""
    return (
        "Sono " + str(tetto) + " rifiuti di seguito: non chiedere altri strumenti, rispondi alla persona "
        "con cio' che hai e di' che cosa resta da fare."
    )


MOTIVO_TURNO_CHIUSO = "Turno chiuso: troppe richieste rifiutate di seguito."


def motivo_della_regola(regola: Regola) -> str:
    """Il motivo che arriva al modello per un comando negato da una regola: non e' un no di oggi, non si riprova."""
    return (
        "Comando negato da una regola di autorizzazione della persona (" + regola.fonte + "): "
        "non riprovare con una variante, di' alla persona che cosa serviva."
    )


def _per_regola(richiesta: Richiesta, cliente: Autorizzatore, politica: Politica, regole: Regole) -> Decisione | None:
    """La decisione presa dalle regole della persona, gia' mostrata al client; `None` se tocca al client.

    Solo i comandi hanno regole. `nega` vale sempre; `consenti` solo con
    qualcuno davanti: senza presenza le regole non concedono niente.
    """
    if not regole or richiesta.strumento != politica.workspace.prefisso + "run_command":
        return None
    regola = regole.decidi(richiesta.argomenti.get("args"))
    if regola is None or (regola.effetto == "consenti" and not cliente.presidiato):
        return None
    decisa = replace(richiesta, regola=regola)
    if regola.effetto == "nega":
        cliente.negata(decisa)
        return Decisione(consenti=False, motivo=motivo_della_regola(regola))
    cliente.concessa(decisa)
    return Decisione(consenti=True)


def _risolvi(
    output: RunOutput,
    cliente: Autorizzatore,
    percorsi: Percorsi,
    politica: Politica,
    coda: str | None,
    regole: Regole | None = None,
) -> tuple[int, int]:
    """Conferma o rifiuta gli strumenti in pausa: (risolti, rifiutati). `coda` si aggiunge al motivo dei rifiuti."""
    risolti = rifiutati = 0
    for requisito in output.active_requirements or []:
        if not requisito.needs_confirmation:
            continue
        richiesta = richiesta_di(requisito.tool_execution, percorsi, politica)
        decisione = _per_regola(richiesta, cliente, politica, regole) if regole else None
        if decisione is not None:
            pass
        elif cliente.presidiato:
            decisione = cliente.autorizza(richiesta)
        else:
            cliente.negata(richiesta)
            decisione = Decisione(consenti=False)
        if decisione.consenti:
            requisito.confirm()
        else:
            # Il motivo arriva al modello: senza, ritenterebbe una variante
            # dello stesso comando.
            requisito.reject(" ".join(parte for parte in (decisione.motivo, coda) if parte) or None)
            rifiutati += 1
        risolti += 1
    return risolti, rifiutati


def risolvi_pausa(output: RunOutput, cliente: Autorizzatore, percorsi: Percorsi, politica: Politica) -> int:
    """Conferma o rifiuta gli strumenti in pausa, senza regole della persona. Restituisce quanti ne ha risolti.

    Zero ferma il ciclo: una pausa che qui non si sa gestire farebbe fermare
    `continue_run` allo stesso punto all'infinito. Le regole le applica
    l'`Arbitro`, che e' cio' che il turno usa.
    """
    return _risolvi(output, cliente, percorsi, politica, coda=None)[0]


class Arbitro:
    """Risolve le pause di un turno con il client, e chiude il turno dopo `tetto` rifiuti di seguito.

    Un modello che insiste - un comando rifiutato, poi una variante, poi
    un'altra - farebbe durare il turno finche' la persona non cede o il tempo
    scade. Al rifiuto numero `tetto` il motivo dice al modello di rispondere
    con cio' che ha; se chiede ancora, `esauriti` diventa vero, le richieste
    sono rifiutate senza chiedere niente al client e il turno non riprende
    (zero ferma il ciclo, e Agno tiene il run in pausa fuori dalla
    cronologia). Una pausa in cui almeno una richiesta e' concessa azzera il
    conto. Senza presenza ogni pausa e' un rifiuto, e il tetto vale lo stesso.

    Le regole della persona (`core/regole.py`) si leggono qui, a ogni turno,
    cosi' un file cambiato vale dal turno dopo; una concessa azzera il conto
    e una negata lo aumenta, come se avesse risposto lei.
    """

    def __init__(
        self,
        cliente: Autorizzatore,
        percorsi: Percorsi,
        politica: Politica,
        *,
        tetto: int | None = None,
        regole: Regole | None = None,
    ) -> None:
        self.cliente = cliente
        self.percorsi = percorsi
        self.politica = politica
        self.tetto = config.RIFIUTI_CONSECUTIVI if tetto is None else tetto
        self.regole = leggi_regole(percorsi, politica) if regole is None else regole
        self.consecutivi = 0
        self.esauriti = False

    def __call__(self, output: RunOutput) -> int:
        if self.consecutivi >= self.tetto:
            for requisito in output.active_requirements or []:
                if requisito.needs_confirmation:
                    requisito.reject(MOTIVO_TURNO_CHIUSO)
            self.esauriti = True
            return 0
        coda = avviso_ultimo_rifiuto(self.tetto) if self.consecutivi == self.tetto - 1 else None
        risolti, rifiutati = _risolvi(output, self.cliente, self.percorsi, self.politica, coda=coda, regole=self.regole)
        if risolti:
            self.consecutivi = self.consecutivi + 1 if rifiutati == risolti else 0
        return risolti
