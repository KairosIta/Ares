# Roadmap

Ares è sviluppato come assistente locale reale, non come demo generica. La
roadmap privilegia affidabilità, comprensibilità e controllo dell’utente.

## Stato attuale

La versione corrente comprende memoria persistente, apprendimento dopo
`continue_run`, workspace controllato, backup locale, manutenzione delle
entita', REPL Rich/Prompt Toolkit e installazione riproducibile. Lock, CLI,
backup e suite principale sono verificati automaticamente su Ubuntu e
Windows.

## Evoluzione

- profili di configurazione per hardware e finestre di contesto differenti;
- benchmark ripetibili di latenza, VRAM e affidabilità degli store;
- copertura automatica più ampia del percorso asincrono;
- interfaccia opzionale oltre alla CLI, senza perdere il funzionamento locale;
- documentazione inglese completa;
- valutazione esplicita di macOS e distribuzioni Linux fuori dalla matrice CI.

## Non obiettivi attuali

- dipendenza obbligatoria da servizi cloud;
- sincronizzazione remota automatica delle memorie;
- esecuzione shell presentata come sandbox sicura;
- supporto garantito per qualunque modello o configurazione hardware.
