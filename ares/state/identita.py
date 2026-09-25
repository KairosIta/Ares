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

La regola, da sola, non bastava. `utente_canonico` era chiamata in otto
punti - chi apriva la chat, chi apriva il quaderno, chi contava le sessioni,
chi componeva il prompt - e ognuno doveva ricordarsi di chiamarla: la
stringa grezza continuava a viaggiare, e bastava un lettore nuovo che se ne
dimenticasse perche' l'archivio si sdoppiasse in silenzio. `Utente` chiude
quel varco: la forma canonica non e' una convenzione da rispettare, e' il
tipo. Un id grezzo non entra in uno store, perche' non e' un `Utente`, e
l'unico modo di ottenerne uno e' passare dalla porta.
"""

import re
from dataclasses import dataclass


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

# Il tetto che il FileSystem di Agno impone a ogni segmento del namespace:
# l'id in `user/<id>` e in `user/<id>/personale` e' un segmento, e oltre questa
# misura `normalize_namespace` solleva `InvalidPathError` invece di accettare
# il nome. Il numero sta scritto qui e non importato da `agno.fs._paths`,
# perche' la porta non deve dipendere da cio' che normalizza; un test di
# contratto confronta i due valori, cosi' una modifica di Agno non lo lascia
# indietro in silenzio.
LUNGHEZZA_MASSIMA = 128


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
    dall'alfabeto, che vale `.` o `..`, o che supera `LUNGHEZZA_MASSIMA`
    caratteri, e' rifiutato e non ricondotto a un default: un id vuoto e' un
    contenitore condiviso da tutti, un id che Agno riscrive e' un archivio che
    si separa in due, `.`/`..` non sono namespace ma path che Agno rifiuta, e
    un id oltre il tetto farebbe fallire la costruzione del FileSystem con un
    `InvalidPathError` che nessun confine legge. Chi ha un valore che puo'
    essere vuoto decide cosa farne prima di arrivare qui, al confine del
    programma; a valle si passa un `Utente`, che di valori vuoti non ne
    ammette.
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
    if len(canonico) > LUNGHEZZA_MASSIMA:
        # Il namespace di Agno e' `user/<id>`: l'id e' un segmento, e oltre
        # questo tetto `normalize_namespace` solleva `InvalidPathError`, che
        # nessun confine di Ares cattura.
        raise UtenteNonValido(
            "l'identificativo utente non puo' superare "
            + str(LUNGHEZZA_MASSIMA)
            + " caratteri: e' il tetto che Agno impone ai segmenti del namespace"
        )
    return canonico


@dataclass(frozen=True)
class Utente:
    """Un identificativo gia' canonico, e percio' utilizzabile come identita'.

    Il costruttore rifiuta una forma non canonica invece di accettarla: e' cio'
    che distingue il tipo da una stringa con un commento sopra. `Utente("Demo")`
    non e' un utente con la maiuscola, e' un errore, e l'unico modo di
    ottenerne uno e' `da_grezzo`, che e' la porta da cui entra tutto cio' che
    arriva da fuori - la riga di comando, l'ambiente, un `user_id` riletto da
    Agno. Da li' in poi l'id non ha una seconda forma con cui scriversi, e
    nessun lettore deve piu' chiedersi se ha gia' normalizzato.

    Il campo si chiama `id` e non `user_id` di proposito: `user_id` e' il nome
    che usa Agno per la chiave di profilo e User Memory, ed e' una stringa.
    Qui dentro c'e' l'identita' di Ares, che gli store ricevono in questa
    forma e traducono nel nome che il framework pretende.
    """

    id: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or utente_canonico(self.id) != self.id:
            raise UtenteNonValido(
                "Utente vuole un identificativo gia' canonico; usa Utente.da_grezzo(" + repr(self.id) + ")"
            )

    @classmethod
    def da_grezzo(cls, grezzo: str) -> "Utente":
        """L'unica porta: normalizza e valida il valore come e' scritto fuori.

        Solleva `UtenteNonValido` con la ragione - vuoto, alfabeto, segmento di
        percorso, lunghezza - perche' chi sta al confine la deve poter mostrare:
        un `--user café` e' un errore da leggere, non un traceback.
        """
        return cls(utente_canonico(grezzo))

    def __str__(self) -> str:
        """L'id canonico, per i posti che vogliono solo stamparlo o passarlo ad Agno."""
        return self.id
