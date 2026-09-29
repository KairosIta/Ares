"""Identita' dell'utente: l'unico posto che decide come si scrive il suo id.

L'id deve concordare in tre posti: il namespace di entita', intuizioni e
quaderno; la chiave di profilo e memorie in Agno; il nome del lock dei
turni. Se ognuno normalizzasse per conto suo, `Demo` e `demo` diventerebbero
due archivi senza nessun errore. Per questo la forma canonica e' un tipo,
`Utente`, ottenibile solo da `Utente.da_grezzo`.

Il modulo non importa niente di Ares ne' di Agno, per non dipendere da cio'
che normalizza.
"""

import re
from dataclasses import dataclass


class UtenteNonValido(ValueError):
    """L'identificativo non e' utilizzabile come identita' di un utente."""


# I caratteri che il FileSystem di Agno lascia intatti. Gli altri li
# percent-encoda (`café` -> `caf%c3%a9`), e la barra annida i namespace.
_ALFABETO = re.compile(r"[a-z0-9._-]+")

# Il tetto di Agno per un segmento di namespace (l'id in `user/<id>`).
# Copiato e non importato; `tests/agno_contract_test.py` verifica che
# coincida.
LUNGHEZZA_MASSIMA = 128


def utente_canonico(user_id: str) -> str:
    """La forma canonica dell'identificativo, o `UtenteNonValido` se non e' valido.

    Toglie gli spazi ai bordi, porta in minuscolo e pretende `_ALFABETO`.
    Rifiuta invece di ripiegare su un default: un id vuoto sarebbe un
    contenitore condiviso, uno che Agno riscrive un archivio sdoppiato, `.` e
    `..` percorsi, uno oltre `LUNGHEZZA_MASSIMA` un errore di Agno al primo uso.
    """
    canonico = user_id.strip().lower()
    if not canonico:
        raise UtenteNonValido("l'identificativo utente non puo' essere vuoto")
    if _ALFABETO.fullmatch(canonico) is None:
        raise UtenteNonValido(
            "l'identificativo utente ammette solo lettere e cifre ASCII, punto, trattino e trattino basso: "
            + repr(user_id)
        )
    if canonico in (".", ".."):
        raise UtenteNonValido("l'identificativo utente non puo' essere '.' o '..': non e' un segmento di percorso")
    if len(canonico) > LUNGHEZZA_MASSIMA:
        raise UtenteNonValido(
            "l'identificativo utente non puo' superare "
            + str(LUNGHEZZA_MASSIMA)
            + " caratteri: e' il tetto che Agno impone ai segmenti del namespace"
        )
    return canonico


@dataclass(frozen=True)
class Utente:
    """Un identificativo gia' canonico, e percio' utilizzabile come identita'.

    Il costruttore rifiuta una forma non canonica: `Utente("Demo")` e' un
    errore, e i valori da fuori (CLI, ambiente, id riletti da Agno) entrano
    solo da `da_grezzo`. Il campo e' `id` e non `user_id`, che e' il nome
    della stringa lato Agno.
    """

    id: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or utente_canonico(self.id) != self.id:
            raise UtenteNonValido(
                "Utente vuole un identificativo gia' canonico; usa Utente.da_grezzo(" + repr(self.id) + ")"
            )

    @classmethod
    def da_grezzo(cls, grezzo: str) -> "Utente":
        """L'unica porta: normalizza e valida un valore scritto fuori.

        `UtenteNonValido` porta la ragione, da mostrare al confine invece di un
        traceback.
        """
        return cls(utente_canonico(grezzo))

    def __str__(self) -> str:
        """L'id canonico, per i posti che vogliono solo stamparlo o passarlo ad Agno."""
        return self.id
