"""Identita' dell'utente: un solo posto che decide come si scrive il suo id.

Perche' esiste
==============
L'identificativo dell'utente entra in tre posti che devono per forza
concordare: il namespace di entita', intuizioni e quaderno; la chiave di
profilo e User Memory, che Agno cerca per `user_id`; e il nome del lock dei
turni. Finche' ognuno normalizzava per conto proprio, `Demo` e `demo` erano
la stessa persona per il namespace - che minuscolizza - e due persone
diverse per profilo, memorie e lock. Nessuno sollevava un errore: la chat
apriva due volte lo stesso archivio con due lock, e cio' che l'una
scriveva nel profilo l'altra non lo vedeva.

Questo modulo non importa niente di Ares: la porta non puo' dipendere da
cio' che normalizza, altrimenti torna a esistere un secondo posto che
decide. Chi legge o scrive per utente passa da qui.
"""

import re


class UtenteNonValido(ValueError):
    """L'identificativo non e' utilizzabile come identita' di un utente."""


# L'alfabeto ammesso: lettere e cifre ASCII, punto, trattino basso, trattino.
# Non e' un gusto, e' l'insieme dei caratteri che il FileSystem di Agno
# lascia intatti. Fuori di qui Agno percent-encoda per rendere il namespace
# URL-safe - `café` diventa `caf%c3%a9`, uno spazio `%20` - e allora la
# stringa che Ares crede di usare non e' piu' quella che finisce
# nell'archivio. La barra e' il caso peggiore: `demo/personale` non e' un
# nome, e' un namespace annidato sotto quello di `demo`.
_ALFABETO = re.compile(r"[a-z0-9._-]+")


def utente_canonico(user_id: str) -> str:
    """La forma canonica dell'identificativo, o `UtenteNonValido` se non e' valido.

    La regola e' una sola e sta qui: togliere gli spazi ai bordi, portare in
    minuscolo, e pretendere l'alfabeto di `_ALFABETO`. Non e' cosmetica. Il
    FileSystem di Agno normalizza il namespace in minuscolo per conto suo, e
    senza questa riga `Demo` scriverebbe i file in un contenitore e le
    memorie in un altro; le varianti di maiuscole e spazi sono la stessa
    persona, e trattarle come due e' il modo in cui un archivio si sdoppia
    senza un errore.

    Un identificativo che si riduce a niente, che contiene un carattere fuori
    dall'alfabeto, o che vale `.` o `..`, e' rifiutato e non ricondotto a un
    default: un id vuoto e' un contenitore condiviso da tutti, un id che Agno
    riscrive e' un archivio che si separa in due, e `.`/`..` non sono
    namespace ma path che Agno rifiuta. Chi chiama con un valore che puo'
    essere vuoto decide cosa farne prima di arrivare qui - `build_filesystem`,
    per esempio, ripiega su `config.DEFAULT_USER_ID`.
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
        # I caratteri sono nell'alfabeto, il valore no: Agno li rifiuta come
        # segmenti di percorso, e `user/..` non e' un namespace, e' un errore
        # che arriva al primo uso e non qui.
        raise UtenteNonValido("l'identificativo utente non puo' essere '.' o '..': non e' un segmento di percorso")
    return canonico
