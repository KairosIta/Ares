"""Cio' che l'estrazione scrive deve avere un appiglio nel testo.

Un modello piccolo che estrae il profilo a volte aggiunge fatti mai detti:
una professione, uno stack di tecnologie, un nome, o un segnaposto come
«Non specificato» in un campo vuoto. Prima di salvare, ogni valore si
confronta con la fonte, cioe' la conversazione che l'estrattore ha letto
piu' cio' che lo store conteneva gia'. Il confronto e' lessicale e per
radici (le prime cinque lettere), senza accenti, perche' l'estrattore
riformula: «non ho deciso» diventa «non decisa».

Il rigore dipende dal campo. Un campo descrittivo e' fatto di parafrasi e
di qualifiche che i criteri di estrazione chiedono («avvio non
confermato»): si scarta solo se nessuna sua parola compare nella fonte. Un
elenco si giudica voce per voce, e un nome parola per parola, perche' le
invenzioni stanno li'. Lingua e fuso orario sono deduzioni (dal dialogo
stesso, da «vivo a Roma» a «Europe/Rome») e non si giudicano.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

# Campi del profilo giudicati voce per voce: lo schema li chiede separati da virgola.
CAMPI_ELENCO = frozenset({"tools_and_stack", "expertise"})

# Campi giudicati parola per parola.
CAMPI_NOME = frozenset({"name", "preferred_name"})

# Campi che non si radicano nel testo: la lingua e' quella in cui si parla, il
# fuso si deduce da un luogo che ha un altro nome.
CAMPI_ESENTI = frozenset({"language", "timezone"})

# Valori che dicono «non lo so» invece di lasciare il campo vuoto.
SEGNAPOSTO = frozenset(
    {
        "non specificato",
        "non specificata",
        "non specificati",
        "non indicato",
        "non indicata",
        "non noto",
        "non nota",
        "non dichiarato",
        "non dichiarata",
        "sconosciuto",
        "sconosciuta",
        "nessuno",
        "nessuna",
        "n d",
        "nd",
        "unknown",
        "not specified",
        "none",
        "null",
        "n a",
    }
)

# Parole frequenti che non dicono niente sul contenuto: una coincidenza su
# queste non radica un valore.
_PAROLE_VUOTE = (
    "alla alle allo agli dalla dalle dallo dagli della delle dello degli nella nelle nello negli "
    "sulla sulle sullo sugli come anche ancora sono stato stata stati essere molto quando quale "
    "quali questo questa questi queste quello quella quelli quelle ogni tutto tutti tutte senza "
    "dopo prima perche sempre usare usato usata usati usate utilizza utilizzato utente user assistant"
)
_VUOTE = frozenset(_PAROLE_VUOTE.split())

_RADICE = 5


def _parole(testo: str) -> list[str]:
    """Le parole di `testo` in minuscolo, senza accenti e senza punteggiatura, in ogni alfabeto."""
    piano = unicodedata.normalize("NFKD", testo.lower())
    piano = "".join(c for c in piano if not unicodedata.combining(c))
    return re.findall(r"[^\W_]+", piano)


def _significative(parole: Iterable[str]) -> set[str]:
    """Le radici delle parole che portano contenuto: almeno quattro lettere, o una cifra."""
    return {
        parola[:_RADICE]
        for parola in parole
        if parola not in _VUOTE and (len(parola) >= 4 or any(c.isdigit() for c in parola))
    }


@dataclass(frozen=True)
class Fonte:
    """Il testo contro cui si radica, gia' scomposto."""

    parole: frozenset[str]
    radici: frozenset[str]

    @classmethod
    def da_testi(cls, testi: Iterable[str]) -> Fonte:
        parole = [p for t in testi if t for p in _parole(t)]
        return cls(frozenset(parole), frozenset(_significative(parole)))


def segnaposto(valore: str) -> bool:
    """Vero se `valore` dice soltanto che il dato manca, o e' vuoto o sola punteggiatura."""
    parole = _parole(valore)
    return not parole or " ".join(parole) in SEGNAPOSTO


def radicato(testo: str, fonte: Fonte) -> bool:
    """Vero se almeno una parola di contenuto di `testo` compare nella fonte.

    Un testo senza parole di contenuto («Go», «C») non si puo' giudicare e
    vale radicato.
    """
    radici = _significative(_parole(testo))
    return not radici or bool(radici & fonte.radici)


def _nome_radicato(valore: str, fonte: Fonte) -> bool:
    """Vero se ogni parola del nome compare intera nella fonte."""
    return all(parola in fonte.parole for parola in _parole(valore))


# Una virgola o un punto e virgola fuori dalle parentesi separa due voci.
_SEPARATORE = re.compile(r"\s*[,;]\s*(?![^()]*\))")


def _voci(valore: str) -> tuple[list[str], list[str]]:
    """Le voci di un elenco e i separatori fra loro, nell'ordine."""
    pezzi = _SEPARATORE.split(valore)
    separatori = _SEPARATORE.findall(valore)
    return pezzi, separatori


@dataclass(frozen=True)
class Verdetto:
    """Cosa salvare di un valore e cosa scartare.

    `tenuto` e' `None` se non resta niente da salvare.
    """

    tenuto: str | None
    scartato: tuple[str, ...] = ()


def radica_campo(campo: str, valore: str, fonte: Fonte) -> Verdetto:
    """Il verdetto su un campo del profilo che l'estrattore vuole scrivere."""
    testo = valore.strip()
    if segnaposto(testo):
        return Verdetto(None, (testo,) if testo else ())
    if campo in CAMPI_ESENTI:
        return Verdetto(testo)
    if campo in CAMPI_NOME:
        return Verdetto(testo) if _nome_radicato(testo, fonte) else Verdetto(None, (testo,))
    if campo not in CAMPI_ELENCO:
        return Verdetto(testo) if radicato(testo, fonte) else Verdetto(None, (testo,))

    pezzi, separatori = _voci(testo)
    tenute: list[str] = []
    scartate: list[str] = []
    for voce in (p.strip() for p in pezzi):
        if not voce:
            continue
        if segnaposto(voce) or not radicato(voce, fonte):
            scartate.append(voce)
        else:
            tenute.append(voce)
    if not tenute:
        return Verdetto(None, tuple(scartate))
    # Le voci restano con il separatore che usava l'estrattore.
    separatore = "; " if any(";" in s for s in separatori) else ", "
    return Verdetto(separatore.join(tenute), tuple(scartate))


def radica_memoria(testo: str, fonte: Fonte) -> Verdetto:
    """Il verdetto su una memoria: un testo descrittivo, giudicato per intero."""
    pulito = testo.strip()
    if segnaposto(pulito) or not radicato(pulito, fonte):
        return Verdetto(None, (pulito,) if pulito else ())
    return Verdetto(pulito)
