# Roadmap

Ares è sviluppato come assistente locale reale, non come demo generica. La
roadmap privilegia affidabilità, comprensibilità e controllo dell’utente.

## Stato attuale

La versione corrente usa Agno 3.0.5 e comprende memoria persistente,
apprendimento dopo `continue_run`, backup locale, manutenzione delle entità
e delle sessioni, REPL Rich/Prompt Toolkit e installazione riproducibile.
`ares` si lancia da qualunque cartella e lavora lì, con le conversazioni
legate alla cartella (`ares resume`) e lo stato in `~/.ares`, in quattro
modalità — `manuale`, `modifiche`, `piano`, `auto` — che decidono cosa fa
da solo e cosa chiede. Il prompt è tutto in italiano e dice al modello quali
modelli usa e se sono locali o cloud, quanto contesto ha, dove si trova e
come funziona la propria memoria; `ares inspect --prompt` lo stampa. I
risultati tool grandi vengono conservati fuori dal prompt e riletti a
pagine; la loro retention segue l'intera conversazione. I codici di uscita
hanno un significato solo per ogni comando. Lock, CLI, backup e suite
principale sono verificati automaticamente su Ubuntu e Windows.

## Evoluzione

- **conferma per singola riga di ciò che entra in memoria durevole.** Oggi
  la conferma c'è ma è tutto o niente per turno: un `n` riporta profilo e
  memorie a prima del turno. Scegliere quale memoria tenere e quale no
  richiede di riscrivere lo store voce per voce, e va provato sul modello di
  dati di Agno prima di promettere che una memoria modificata torni al testo
  precedente e non sparisca;
- prove in-process: `config.imposta_percorsi` lo permette, il runner lancia
  ancora un processo per prova per l'isolamento che garantisce;
- **sonda del benchmark robusta alla prosa attorno al JSON.** Dopo la
  correzione del legame valore-citazione, delle undici fasi ancora non
  conclusive nei rapporti salvati nove lo sono perché il modello accompagna
  il JSON con una frase, e il valutatore lo accetta solo come blocco isolato.
  Nove fasi su 116 non misurano quindi la memoria ma la forma della risposta.
  Estrarre il primo oggetto JSON dal testo va deciso guardando cosa si perde:
  una risposta che contraddice in prosa ciò che afferma nel JSON non è un
  successo, e oggi quel caso esce non conclusivo invece che silenziosamente
  superato;
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
