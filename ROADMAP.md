# Roadmap

Ares è sviluppato come assistente locale reale, non come demo generica. La
roadmap privilegia affidabilità, comprensibilità e controllo dell’utente.

## Stato attuale

La versione corrente usa Agno 3.0.5 e comprende memoria persistente,
apprendimento dopo `continue_run`, workspace controllato, backup locale,
manutenzione delle entita' e delle sessioni, REPL Rich/Prompt Toolkit e
installazione riproducibile. I risultati tool grandi vengono conservati
fuori dal prompt e riletti a pagine; la loro retention segue l'intera
conversazione. Assistente, REPL, backup ed entita' sono divisi per
responsabilita'. Lock, CLI, backup e suite principale sono verificati
automaticamente su Ubuntu e Windows.

## Evoluzione

- **conferma o annullamento di ciò che entra in memoria durevole.** Oggi
  profilo e memorie si scrivono fuori dal ciclo di conferma, e il controllo è
  la visibilità a posteriori dell'eco. Agno non offre una via: `PROPOSE` vale
  per il solo store delle intuizioni e `HITL` per nessuno, quindi la conferma
  va costruita qui. La strada più corta parte da `agent/echo.py`, che il
  diffo fra prima e dopo il turno lo calcola già: manca la decisione
  dell'utente e la riscrittura dei valori precedenti quando la risposta è no.
  È il punto della roadmap con il peso maggiore sul modello di sicurezza;
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
