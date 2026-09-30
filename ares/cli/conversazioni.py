"""Le conversazioni in archivio, rese come testo per il terminale e per `/esporta`."""

from ares.state.sessioni import Conversazione, SessioneRiferimento, quando, tronca


def conto_scambi(scambi: int) -> str:
    return str(scambi) + (" scambio" if scambi == 1 else " scambi")


def righe_sessione(sessione: SessioneRiferimento, corrente: bool = False, con_cartella: bool = False) -> list[str]:
    """Una sessione in righe pronte per la stampa.

    `con_cartella` aggiunge dove e' nata, per gli elenchi fra cartelle.
    """
    testa = "- " + (sessione.id or "?") + ("   (questa)" if corrente else "")
    righe = [testa + "   " + quando(sessione) + "   " + conto_scambi(sessione.scambi)]
    if sessione.inizio:
        righe.append("    inizio: " + tronca(sessione.inizio, 90))
    if con_cartella:
        righe.append("    cartella: " + (sessione.cartella or "nessuna"))
    return righe


def testo_markdown(conversazione: Conversazione, *, modello: str = "") -> str:
    """La conversazione in Markdown: una testata, poi ogni scambio come `Tu` e `Ares`.

    Degli strumenti riporta solo i nomi, una riga per turno.
    """
    sessione = conversazione.sessione
    righe = ["# Conversazione " + (sessione.id or "?"), ""]
    righe.append("- utente: " + (conversazione.utente or "?"))
    if sessione.cartella:
        righe.append("- cartella: " + sessione.cartella)
    if modello:
        righe.append("- modello: " + modello)
    righe.append("- ultima modifica: " + quando(sessione))
    righe.append("- scambi: " + str(len(conversazione.scambi)))
    for scambio in conversazione.scambi:
        for messaggio in scambio.messaggi:
            righe.extend(["", "## Tu" if messaggio.ruolo == "user" else "## Ares", "", messaggio.testo])
        if scambio.strumenti:
            righe.extend(["", "_strumenti: " + ", ".join(scambio.strumenti) + "_"])
    return "\n".join(righe) + "\n"
