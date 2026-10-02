"""Strumenti su richiesta: gruppi descritti con una riga, attivati quando servono.

Entita', intuizioni e la proposta di skill servono in una minoranza di
turni, ma i loro sette schemi e le loro guide arriverebbero al modello a
ogni richiesta. Qui restano fuori finche' il modello non chiama
`attiva_strumenti(gruppo)`: lo strumento restituisce la guida del gruppo, e
dalla richiesta successiva, anche nello stesso turno, gli schemi arrivano
con gli altri. Il gruppo resta attivo per il resto della sessione:
togliere uno schema a meta' sposterebbe di nuovo il prefisso del prompt,
che il daemon tiene in cache.

Il filtro sta nel modello (`OllamaConRagionamento.get_request_params`), non
in Agno: Agno conosce ed esegue tutti gli strumenti, il modello vede solo
quelli attivi. Una chiamata a uno strumento nascosto, se il modello ne
indovina il nome, funziona comunque.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from agno.run import RunContext
from agno.tools import Toolkit

# Dove la sessione conserva i gruppi attivati: `session_state`, che Agno
# salva con la sessione e rilegge prima di ogni turno.
CHIAVE_STATO = "strumenti_attivati"


@dataclass(frozen=True)
class Gruppo:
    """Un gruppo di strumenti su richiesta: il nome per attivarlo, la riga nel prompt, gli strumenti."""

    nome: str
    riga: str
    strumenti: tuple[str, ...]


ENTITA = Gruppo(
    "entita",
    "quando la persona ti chiede di segnarti, ricordare o cercare qualcosa su una persona, un progetto, "
    "un sistema o un prodotto, o quando ne impari un fatto che servira' in futuro",
    ("remember_about", "link_entities", "search_entities", "forget"),
)
INTUIZIONI = Gruppo(
    "intuizioni",
    "prima di rispondere a una domanda di metodo, di scelta o di convenzione, e quando la persona ti "
    "chiede di salvare un criterio da riusare",
    ("search_learnings", "save_learning"),
)
# Lo strumento di `agent/skill.py`: serve di rado, e il suo schema e' lungo.
# Non si chiama «skill»: un modello piccolo lo attiverebbe per usarne una.
PROPOSTE = Gruppo(
    "proposte",
    "quando la persona ti chiede di salvare un procedimento come skill, o dopo aver portato a termine "
    "una procedura in piu' passi, non ovvia, che servira' di nuovo",
    ("proponi_skill",),
)
GRUPPI = (ENTITA, INTUIZIONI, PROPOSTE)


@dataclass
class Scaffale:
    """I gruppi disponibili in una sessione, con la loro guida, e quali sono attivi.

    `guide` da' per ogni gruppo disponibile la guida da restituire
    all'attivazione; un gruppo assente non e' disponibile.
    """

    guide: dict[str, Callable[[], str]]
    attivi: set[str] = field(default_factory=set)

    @property
    def gruppi(self) -> tuple[Gruppo, ...]:
        return tuple(g for g in GRUPPI if g.nome in self.guide)

    def attivo(self, nome: str) -> bool:
        return nome in self.attivi

    def nascosti(self) -> frozenset[str]:
        """I nomi degli strumenti che il modello non deve ancora vedere."""
        return frozenset(s for g in self.gruppi if g.nome not in self.attivi for s in g.strumenti)

    def carica(self, stato: dict[str, Any] | None) -> None:
        """Riprende i gruppi attivati da `session_state`: quelli di una sessione ripresa."""
        salvati = (stato or {}).get(CHIAVE_STATO) or []
        self.attivi = {nome for nome in salvati if nome in self.guide}

    def attiva(self, nome: str, stato: dict[str, Any] | None) -> str:
        """Attiva un gruppo e restituisce cio' che il modello legge: la guida o l'errore."""
        nome = _semplice(nome)
        if nome not in self.guide:
            disponibili = ", ".join(g.nome for g in self.gruppi)
            return "Gruppo sconosciuto: " + repr(nome) + ". I gruppi sono: " + disponibili + "."
        self.attivi.add(nome)
        if stato is not None:
            stato[CHIAVE_STATO] = sorted(self.attivi)
        gruppo = next(g for g in self.gruppi if g.nome == nome)
        return (
            "Attivi da adesso, per il resto della conversazione: "
            + ", ".join(gruppo.strumenti)
            + ".\n\n"
            + self.guide[nome]()
        )

    def toolkit(self) -> Toolkit:
        """Lo strumento `attiva_strumenti`, legato a questo scaffale."""
        scaffale = self

        def attiva_strumenti(run_context: RunContext, gruppo: str) -> str:
            return scaffale.attiva(gruppo, run_context.session_state)

        # La descrizione elenca i gruppi di questa sessione con i loro
        # strumenti: e' cio' che un modello piccolo legge per decidere.
        attiva_strumenti.__doc__ = (
            "Rende disponibili gli strumenti di un gruppo e restituisce la guida per usarli.\n\n"
            "Args:\n    gruppo: "
            + "; ".join("'" + g.nome + "' (" + ", ".join(g.strumenti) + ")" for g in self.gruppi)
            + ".\n"
        )
        return Toolkit(name="scaffale", tools=[attiva_strumenti])

    def pre_hook(self) -> Callable[[RunContext], None]:
        """Il pre-hook che riprende i gruppi attivati prima di ogni turno."""

        def riprendi_strumenti(run_context: RunContext) -> None:
            self.carica(run_context.session_state)

        return riprendi_strumenti


def _semplice(nome: str) -> str:
    """Il nome di un gruppo come lo scrive un modello, ridotto: «Entita'», «entità» ed «entita» coincidono."""
    scomposto = unicodedata.normalize("NFKD", nome.strip().lower())
    return "".join(c for c in scomposto if c.isalnum() or c == "_")


def nome_strumento(voce: Any) -> str | None:
    """Il nome di uno strumento come Agno lo passa al modello: dict dell'API o `Function`."""
    if isinstance(voce, dict):
        return (voce.get("function") or {}).get("name")
    return getattr(voce, "name", None)


def visibili(strumenti: Iterable[Any], scaffale: Scaffale | None) -> list[Any]:
    """Gli strumenti meno quelli che lo scaffale tiene nascosti."""
    if scaffale is None:
        return list(strumenti)
    nascosti = scaffale.nascosti()
    return [s for s in strumenti if nome_strumento(s) not in nascosti]
