# Architettura

Ares e' un'applicazione Python local-first. La CLI costruisce un agente Agno
collegato a Ollama, agli store persistenti e a un insieme limitato di
strumenti. Nessun servizio cloud e' necessario durante l'uso ordinario.

## Componenti

### Interfaccia

- `chat.py` e' il client CLI: tabella dei comandi, rendering del turno e
  richieste di conferma;
- `cli_input.py` gestisce editor, completamento, input multilinea e cronologia
  privata della REPL;
- `cli_ui.py` rende streaming Markdown, pannelli e tabelle, e filtra i
  controlli di terminale contenuti nelle risposte del modello.

### Nucleo del turno

- `turn_core.py` normalizza gli eventi Agno e coordina `run/continue_run`
  senza dipendere dall'interfaccia;
- `assistant.py` assembla modello, istruzioni, strumenti e store; contiene
  anche la `AresLearningMachine` e il post-hook che spostano l'apprendimento
  sul run completo, perche' sono decisioni di cablaggio dell'agente e non un
  sottosistema a se';
- `schemas.py` estende profilo e memorie con i campi e il rendering che gli
  store usano nel prompt;
- `config.py` raccoglie le impostazioni versionate e decide, in un punto solo,
  i percorsi dello stato.

### Stato

- SQLite conserva sessioni, profilo, memorie ed entita', su un database
  distinto da quello del quaderno privato dell'agente;
- LanceDB conserva la conoscenza vettoriale con embedding serviti da Ollama;
- `stores.py` e' l'unico punto da cui si leggono entita', intuizioni e
  sessioni: non scrive mai, e non accende il modello salvo l'embedding della
  query sulle intuizioni;
- `state_lock.py` espone il lock cooperativo condiviso/esclusivo dello stato,
  su cui `platform_files.py` uniforma le primitive fra POSIX e Windows.

### Strumenti operativi

- `preflight.py` verifica che il server Ollama risponda e che i modelli
  nominati in `config.py` siano scaricati;
- `inspect_learning.py` rilegge gli archivi a modello spento;
- `backup.py` crea, verifica e ripristina snapshot locali dello stato;
- `entity_maintenance.py` rileva e fonde entita' duplicate;
- `setup.sh` e `setup.ps1` ricostruiscono lo stesso ambiente bloccato sui due
  sistemi verificati.

## Flusso di un turno

1. Il client consegna il messaggio al core del turno.
2. Il core avvia Agno e pubblica eventi indipendenti dall'interfaccia.
3. Il modello puo' rispondere o richiedere uno strumento.
4. Le operazioni sensibili sospendono il run in attesa di conferma del client.
5. Il core esegue `continue_run` sullo stesso run dopo la decisione.
6. La macchina di apprendimento riceve l'output completo e aggiorna gli store.

L'ultimo passaggio e' separato dall'interfaccia: l'apprendimento usa sempre il
run finale, evitando di perdere il contenuto prodotto dopo una conferma.

## Confini di sicurezza

Gli strumenti per i file sono limitati a una directory di lavoro, ma questo
confine non e' una sandbox di processo. I comandi shell possono accedere alle
risorse dell'host e alla rete, quindi richiedono conferma esplicita. Stato,
workspace, backup e `.env` restano fuori dal controllo versione.

Su POSIX lo stato appreso nasce privato: `tmp/` e la directory LanceDB a 0700,
i due database e la cronologia a 0600, come gli snapshot. La directory e' il
controllo che regge, perche' senza il diritto di attraversarla i modi dei file
dentro non si raggiungono; i database vengono comunque creati vuoti e con i
propri permessi prima che li apra SQLite, perche' altrimenti nascerebbero con
la umask del processo. Su Windows vale la DACL ereditata dalla directory: un
`chmod` renderebbe i file soltanto read-only senza limitarne la lettura.

Su POSIX gli snapshot vengono pubblicati con una rinomina di directory. Su
Windows, dove LanceDB può impedire quella rinomina anche dopo la chiusura dei
reader nativi, il manifest viene pubblicato per ultimo come commit marker e
il restore conserva stabile la directory radice con una copia di rollback.

## Configurazione

Le impostazioni versionate sono in `config.py`. Identita' e percorsi locali
possono essere sovrascritti con le variabili mostrate in `.env.example`; il
file `.env` del clone non viene pubblicato.
