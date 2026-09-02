# Architettura

Ares e' un'applicazione Python local-first. La CLI costruisce un agente Agno
collegato a Ollama, agli store persistenti e a un insieme limitato di
strumenti. Nessun servizio cloud e' necessario; su scelta, il modello
conversazionale puo' essere un modello cloud di Ollama, che il daemon locale
inoltra a `ollama.com`, mentre estrazione delle memorie ed embedding restano
locali per costruzione.

## Componenti

### Interfaccia

- `chat.py` avvia e coordina la REPL; `chat_commands.py` contiene la tabella
  dei comandi locali e il loro dispatch, mentre `chat_render.py` presenta
  eventi, conferme e metriche del turno;
- `cli_input.py` gestisce editor, completamento, input multilinea e cronologia
  privata della REPL;
- `cli_ui.py` rende streaming Markdown, pannelli e tabelle, e filtra i
  controlli di terminale contenuti nelle risposte del modello.

### Nucleo del turno

- `turn_core.py` normalizza gli eventi Agno e coordina `run/continue_run`
  senza dipendere dall'interfaccia;
- `assistant.py` e' la facciata che assembla l'agente e conserva gli import
  pubblici; `assistant_runtime.py` costruisce modelli, archivi e strumenti,
  `assistant_learning.py` configura gli store e il post-hook sul run completo,
  `assistant_prompts.py` compone soltanto le istruzioni coerenti con i flag;
- `schemas.py` estende profilo e memorie con i campi e il rendering che gli
  store usano nel prompt;
- `config.py` raccoglie le impostazioni versionate e decide, in un punto solo,
  i percorsi dello stato. Importarlo non tocca il disco: la directory dello
  stato la crea `prepara_archivio()`, che chiamano i costruttori di
  `assistant.py` e il `main()` di ogni comando, dopo aver letto gli
  argomenti.

### Stato

- `kairos.db` conserva sessioni, run normalizzati, profilo, memorie, entita'
  e indice degli offload; `filesystem.db` conserva il quaderno privato e i
  payload dei risultati tool troppo grandi per restare nel contesto;
- LanceDB conserva la conoscenza vettoriale con embedding serviti da Ollama;
- `stores.py` e' l'unico punto da cui si leggono entita', intuizioni e
  sessioni: non scrive mai, e non accende il modello salvo l'embedding della
  query sulle intuizioni;
- `state_lock.py` espone il lock cooperativo condiviso/esclusivo dello stato,
  su cui `platform_files.py` uniforma le primitive fra POSIX e Windows.

### Strumenti operativi

- `preflight.py` verifica che il server Ollama risponda e che i modelli
  nominati in `config.py` siano scaricati, senza accendere niente e senza
  lasciare niente su disco;
- `inspect_learning.py` rilegge gli archivi a modello spento;
- `backup.py` coordina creazione, catalogo e restore degli snapshot locali;
  parser, conferme e output vivono in `backup_cli.py`, formato, checksum e
  verifica in `backup_integrity.py`, staging e rollback in
  `backup_restore.py`; `backup_files.py` raccoglie permessi ricorsivi e
  rinomina protetta condivisi dai due flussi, mentre `backup_probe.py` isola
  in un processo dedicato la lettura di LanceDB, così gli handle nativi sono
  chiusi prima delle rinomine. La façade `backup.py` offre anche a `chat.py` il
  promemoria di rifarne uno quando l'ultimo e' vecchio: la lettura non crea la
  directory dei backup e non solleva,
  perche' un avviso non deve poter impedire l'avvio;
- `entity_maintenance.py` espone la CLI e coordina lock e backup; l'audit in
  sola lettura vive in `entity_audit.py`, il piano e la transazione di fusione
  in `entity_merge.py`, i contratti condivisi in `entity_models.py`;
- `session_maintenance.py` coordina anteprima, conferma, lock e snapshot della
  retention; `session_retention.py` apre entrambi i backend, registra su Agno
  il filesystem dei payload e verifica la cancellazione congiunta di
  sessione, run, contesto appreso, indice e risultato offloaded;
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
la umask del processo. I permessi della directory li applica
`config.prepara_archivio()`, chiamata da chi apre l'archivio e non
dall'import: un comando che stampa soltanto l'aiuto non lascia niente
indietro. Su Windows vale la DACL ereditata dalla directory: un `chmod`
renderebbe i file soltanto read-only senza limitarne la lettura.

Su POSIX gli snapshot vengono pubblicati con una rinomina di directory. Su
Windows, dove LanceDB può impedire quella rinomina anche dopo la chiusura dei
reader nativi, il manifest viene pubblicato per ultimo come commit marker e
il restore conserva stabile la directory radice con una copia di rollback.

La retention segue la sessione invece di una scadenza dei singoli risultati:
finché la conversazione esiste i suoi `result_id` restano risolvibili. La
manutenzione offline seleziona sessioni inattive ma non cancella niente
automaticamente; applicare una selezione richiede lock esclusivo e snapshot.
Il database principale deve conoscere il backend separato `filesystem.db`
prima di chiamare la cascata Agno, altrimenti il payload diventerebbe orfano:
questa registrazione è un'invariante verificata dalla prova dedicata.

## Configurazione

Le impostazioni versionate sono in `config.py`. Identita' e percorsi locali
possono essere sovrascritti con le variabili mostrate in `.env.example`; il
file `.env` del clone non viene pubblicato.
