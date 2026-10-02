"""Il piano di consolidamento delle memorie: quali ritirare, e a favore di quali.

Il percorso caldo salva una memoria per volta e non rilegge l'archivio: un
modello piccolo riscrive cosi' cose gia' note con parole appena diverse, e
una preferenza cambiata convive con quella vecchia. Il consolidamento lo
rimedia a freddo, in tre passi:

1. le **candidate**: coppie con lo stesso testo a meno di maiuscole e spazi,
   piu', per ogni memoria, le vicine per embedding sopra `SOGLIA_VICINE`.
   L'embedding da solo non decide: due doppioni scritti con parole diverse
   possono stare sotto due memorie distinte sullo stesso tema (le misure
   sono in docs/memory-quality.md), quindi la soglia e' larga;
2. il **giudizio**, una coppia alla volta: doppione, superata o distinte.
   E' una scelta fra tre parole, il compito piu' stretto che si possa dare
   a un modello piccolo; i testi identici non passano dal modello;
3. il **piano**: di una coppia superata si ritira la memoria piu' vecchia
   a favore della piu' recente; di un doppione resta la piu' completa,
   cioe' la piu' lunga, e a pari lunghezza la piu' recente. Quella che
   resta e' valida con la sua provenienza. Una memoria ritirata non si
   giudica piu', e un rimando a una memoria a sua volta ritirata segue la
   catena fino a una valida.

Ritirare vuol dire spostare fra le superate (`AresMemories.ritira`): niente
si cancella, `/memorie superate` le mostra e un backup precede ogni scrittura.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from ares.agent.schemas import chiave_memoria, scritta_il

Relazione = Literal["doppione", "superata", "distinte"]
RELAZIONI: tuple[Relazione, ...] = ("doppione", "superata", "distinte")

# Coseno minimo fra due memorie perche' il modello le confronti. Con
# nomic-embed-text-v2-moe i doppioni con parole diverse scendono fino a
# 0.51, le superate a 0.69, e memorie distinte sullo stesso tema salgono a
# 0.66: la soglia lascia passare le prime e affida il resto al giudizio.
SOGLIA_VICINE = 0.5
# Quante vicine per memoria: tiene lineare il numero di giudizi.
VICINE_PER_MEMORIA = 3

Incorpora = Callable[[Sequence[str]], Sequence[Sequence[float]]]
Giudica = Callable[[dict[str, Any], dict[str, Any]], Relazione]


@dataclass(frozen=True)
class Ritiro:
    """Una memoria da ritirare, quella che resta al suo posto e perche'."""

    ritirata: dict[str, Any]
    resta: dict[str, Any]
    relazione: Relazione


@dataclass
class PianoConsolida:
    """Cosa il consolidamento farebbe, e quanto lavoro ha fatto per deciderlo."""

    ritiri: list[Ritiro] = field(default_factory=list)
    memorie: int = 0
    giudicate: int = 0

    @property
    def sostituzioni(self) -> dict[str, str]:
        """Per ogni id ritirato, l'id della memoria valida che lo sostituisce."""
        return {r.ritirata["id"]: r.resta["id"] for r in self.ritiri}

    @property
    def conferma(self) -> str:
        return "CONSOLIDA " + str(len(self.ritiri))


def _completezza(voce: dict[str, Any]) -> int:
    """Quanto dice una memoria, per scegliere fra due doppioni: la lunghezza del testo ridotto."""
    return len(chiave_memoria(str(voce.get("content") or "")))


def _coseno(primo: Sequence[float], secondo: Sequence[float]) -> float:
    norma = math.sqrt(sum(x * x for x in primo) * sum(y * y for y in secondo))
    return sum(x * y for x, y in zip(primo, secondo, strict=True)) / norma if norma else 0.0


def candidate(voci: Sequence[dict[str, Any]], incorpora: Incorpora) -> list[tuple[int, int, float | None]]:
    """Le coppie da confrontare, come indici in `voci`, dalla piu' simile.

    Il terzo elemento e' il coseno, o `None` per i testi identici, che
    vengono per primi.
    """
    chiavi = [chiave_memoria(str(v.get("content") or "")) for v in voci]
    identiche = [(i, j, None) for i in range(len(voci)) for j in range(i + 1, len(voci)) if chiavi[i] == chiavi[j]]
    gia = {(i, j) for i, j, _ in identiche}

    vettori = incorpora([str(v.get("content") or "") for v in voci]) if len(voci) > 1 else []
    vicine: dict[tuple[int, int], float] = {}
    for i, vettore in enumerate(vettori):
        punteggi = sorted(
            ((_coseno(vettore, altro), j) for j, altro in enumerate(vettori) if j != i),
            reverse=True,
        )
        for coseno, j in punteggi[:VICINE_PER_MEMORIA]:
            coppia = (min(i, j), max(i, j))
            if coseno >= SOGLIA_VICINE and coppia not in gia:
                vicine[coppia] = coseno
    ordinate = sorted(vicine.items(), key=lambda voce: voce[1], reverse=True)
    return [*identiche, *((i, j, coseno) for (i, j), coseno in ordinate)]


def pianifica(voci: Sequence[dict[str, Any]], incorpora: Incorpora, giudica: Giudica) -> PianoConsolida:
    """Il piano per le memorie valide `voci`; non scrive niente.

    `giudica(vecchia, recente)` riceve la coppia in ordine di data. Le voci
    senza id non si toccano: non si saprebbe come rimandarvi.
    """
    valide = [v for v in voci if isinstance(v, dict) and v.get("id") and str(v.get("content") or "").strip()]
    piano = PianoConsolida(memorie=len(valide))
    ritirate: dict[str, tuple[str, Relazione]] = {}

    for i, j, coseno in candidate(valide, incorpora):
        # A pari data vale l'ordine dell'archivio, dove Agno aggiunge in coda.
        vecchia, recente = (
            (valide[i], valide[j]) if scritta_il(valide[i]) <= scritta_il(valide[j]) else (valide[j], valide[i])
        )
        if vecchia["id"] in ritirate or recente["id"] in ritirate:
            continue
        if coseno is None:
            relazione: Relazione = "doppione"
        else:
            relazione = giudica(vecchia, recente)
            piano.giudicate += 1
        if relazione == "distinte":
            continue
        # Un doppione con un dettaglio in piu' resta anche se e' il piu' vecchio.
        if relazione == "doppione" and _completezza(vecchia) > _completezza(recente):
            ritirate[recente["id"]] = (vecchia["id"], relazione)
        else:
            ritirate[vecchia["id"]] = (recente["id"], relazione)

    per_id = {v["id"]: v for v in valide}
    for id_ritirata, (destinazione, relazione) in ritirate.items():
        while destinazione in ritirate:
            destinazione = ritirate[destinazione][0]
        piano.ritiri.append(Ritiro(per_id[id_ritirata], per_id[destinazione], relazione))
    return piano


ISTRUZIONI_GIUDIZIO = (
    "Ricevi due memorie su una persona: A e' la piu' vecchia, B la piu' recente. Scegli la relazione:\n"
    "- doppione: dicono la stessa cosa, anche con parole diverse o con un dettaglio in piu';\n"
    "- superata: B cambia o smentisce A, che non vale piu';\n"
    "- distinte: dicono cose diverse, che possono essere vere insieme.\n"
    "Nel dubbio scegli distinte."
)

SCHEMA_GIUDIZIO = {
    "type": "object",
    "properties": {"relazione": {"type": "string", "enum": list(RELAZIONI)}},
    "required": ["relazione"],
}


def messaggi_giudizio(vecchia: dict[str, Any], recente: dict[str, Any], *, vincolato: bool) -> list[dict[str, str]]:
    """La richiesta per una coppia: senza vincolo, la risposta e' una parola sola."""
    sistema = ISTRUZIONI_GIUDIZIO if vincolato else ISTRUZIONI_GIUDIZIO + "\nRispondi con una sola parola."
    coppia = "A: " + str(vecchia.get("content") or "") + "\nB: " + str(recente.get("content") or "")
    return [{"role": "system", "content": sistema}, {"role": "user", "content": coppia}]


def leggi_relazione(testo: str | None, *, vincolato: bool) -> Relazione:
    """La relazione dalla risposta del modello; una risposta illeggibile vale distinte.

    Distinte e' l'unica risposta che non tocca niente: un errore del modello
    lascia una memoria in piu', non ne toglie una.
    """
    pulito = (testo or "").strip()
    if vincolato:
        try:
            dati = json.loads(pulito)
        except ValueError:
            dati = None
        pulito = str(dati.get("relazione") or "") if isinstance(dati, dict) else ""
    parola = pulito.casefold().strip(" .*`'\"\n")
    return next((r for r in RELAZIONI if parola == r), "distinte")
