"""pass^k: la probabilita' che tutte le k ripetizioni di un caso riescano.

E' la metrica di affidabilita' di tau^2-bench: con il 75% di successi per
prova, pass^3 e' il 42%. Qui si stima dalle ripetizioni osservate, come
fanno loro: su n prove con c successi, pass^k e' C(c, k) / C(n, k), cioe' la
frequenza con cui k prove scelte a caso fra le n riescono tutte. Con n uguale
a k vale 1 o 0.
"""

from math import comb

# Il k dei rapporti: tre ripetizioni sono il default dei due eval.
K_AFFIDABILITA = 3


def pass_k(successi: int, prove: int, k: int = K_AFFIDABILITA) -> float | None:
    """C(successi, k) / C(prove, k), o `None` se le prove sono meno di k o k non e' positivo."""
    if k < 1 or prove < k or successi < 0 or successi > prove:
        return None
    return comb(successi, k) / comb(prove, k)


def riga_pass_k(valore: float | None) -> str:
    """`0.33` o `—` quando non si puo' stimare."""
    return "—" if valore is None else f"{valore:.2f}"
