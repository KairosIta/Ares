"""
Ispezione di cio' che l'agente ha imparato
==========================================
Uso:
    .venv/bin/python inspect_learning.py
    .venv/bin/python inspect_learning.py --session test_1
    .venv/bin/python inspect_learning.py --file notes/setup.md

Legge gli archivi senza avviare il modello, quindi non consuma VRAM e non
scrive nulla. Serve a rispondere alla domanda che conta quando un agente
dice di ricordare: dove sta questa informazione, e la ritrovera' davvero?
"""

import argparse
import sys

import config
from assistant import build_assistant, build_filesystem
from state_lock import StatoOccupato, lock_stato
from stores import leggi_entita, leggi_intuizioni, righe_entita, stampa_store


def separatore(titolo: str) -> None:
    print()
    print("=" * 70)
    print(titolo)
    print("=" * 70)


def _ispeziona() -> None:
    parser = argparse.ArgumentParser(description="Ispeziona gli archivi di apprendimento")
    parser.add_argument("--user", default=config.DEFAULT_USER_ID)
    parser.add_argument("--session", default="principale")
    parser.add_argument("--query", default="", help="Query per entita' e intuizioni")
    parser.add_argument("--file", default=None, help="Stampa il contenuto di un file dell'agente")
    args = parser.parse_args()

    fs = build_filesystem(args.user)

    if args.file:
        contenuto = fs.read(args.file)
        if contenuto is None:
            print("Nessun file a questo percorso:", args.file)
        else:
            print(contenuto)
        return

    agent = build_assistant(user_id=args.user, session_id=args.session)
    lm = agent.learning_machine

    separatore("PROFILO UTENTE   (per utente, sopravvive a ogni sessione)")
    stampa_store(lm.user_profile_store, "Profilo", user_id=args.user)

    separatore("MEMORIE   (osservazioni non strutturate, per utente)")
    stampa_store(lm.user_memory_store, "Memorie", user_id=args.user)

    separatore("CONTESTO DI SESSIONE   (sessione: " + args.session + ")")
    stampa_store(lm.session_context_store, "Contesto", session_id=args.session)

    separatore("ENTITA'   (persone, progetti, sistemi)")
    entita = leggi_entita(lm, user_id=args.user, query=args.query)
    if not entita:
        print("Nessuna entita' registrata.")
    for e in entita:
        for riga in righe_entita(e):
            print(riga)

    separatore("INTUIZIONI APPRESE   (indice vettoriale LanceDB)")
    intuizioni = leggi_intuizioni(lm, user_id=args.user, query=args.query)
    if not intuizioni:
        print("Nessuna intuizione salvata.")
    for k in intuizioni:
        titolo = getattr(k, "title", "?")
        testo = getattr(k, "learning", "")
        print("-", titolo)
        print("   ", testo)

    separatore("FILE DELL'AGENTE   (scritti da lui, verbatim)")
    elenco = fs.list()
    if not elenco:
        print("Nessun file.")
    for f in elenco:
        print("-", f.path, "  ", f.size_bytes, "byte")
    print()
    python_venv = r".venv\Scripts\python.exe" if sys.platform == "win32" else ".venv/bin/python"
    print("Per leggerne uno:", python_venv, "inspect_learning.py --file <percorso>")


def main() -> None:
    try:
        with lock_stato(esclusivo=False):
            _ispeziona()
    except StatoOccupato as errore:
        print("Impossibile leggere lo stato di Ares:", errore)
        print("Attendi che backup o restore terminino e riprova.")


if __name__ == "__main__":
    main()
