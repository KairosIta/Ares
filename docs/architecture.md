# Architettura

Ares e' un'applicazione Python local-first. La CLI costruisce un agente Agno
collegato a Ollama, agli store persistenti e a un insieme limitato di
strumenti. Nessun servizio cloud e' necessario; su scelta, il modello
conversazionale puo' essere un modello cloud di Ollama, che il daemon locale
inoltra a `ollama.com`, mentre estrazione delle memorie ed embedding restano
locali per costruzione.

## Struttura del codice

Il codice vive nel package `ares/`, diviso per responsabilita'. I comandi
fra parentesi sono quelli che `uv sync` installa nel venv; ogni sottopackage
con un `__main__.py` risponde anche a `python -m`.

```text
ares/
├── config.py       impostazioni versionate e percorsi dello stato
├── agent/          composizione dell'agente, turno, apprendimento, schemi
├── cli/            la REPL: chat, comandi locali, rendering, editor   (ares)
├── state/          lettura degli archivi, lock, primitive di piattaforma
├── backup/         snapshot locali: creazione, verifica, restore      (ares-backup)
├── entities/       audit e fusione delle entita'                      (ares-entities)
├── sessions/       retention di sessioni e risultati tool             (ares-sessions)
└── ops/            preflight e ispezione a modello spento             (ares-preflight, ares-inspect)
```

`tests/` contiene le prove e il loro runner, `docs/` questa documentazione,
la radice i file di configurazione degli strumenti e gli script di setup. Lo
stato appreso sta in `tmp/`, fuori dal controllo versione.

## Componenti

### Interfaccia (`ares/cli/`)

- `chat.py` avvia e coordina la REPL; `commands.py` contiene la tabella dei
  comandi locali e il loro dispatch, mentre `render.py` presenta eventi,
  conferme e metriche del turno;
- `editor.py` gestisce editor, completamento, input multilinea e cronologia
  privata della REPL;
- `ui.py` rende streaming Markdown, pannelli e tabelle, e filtra i controlli
  di terminale contenuti nelle risposte del modello.

### Nucleo del turno (`ares/agent/`)

- `turn_core.py` normalizza gli eventi Agno e coordina `run/continue_run`
  senza dipendere dall'interfaccia;
- `assistant.py` e' la facciata che assembla l'agente e conserva gli import
  pubblici; `runtime.py` costruisce modelli, archivi e strumenti,
  `learning.py` configura gli store e il post-hook sul run completo,
  `prompts.py` compone soltanto le istruzioni coerenti con i flag;
- `schemas.py` estende profilo e memorie con i campi e il rendering che gli
  store usano nel prompt;
- `echo.py` fotografa profilo e memorie prima e dopo un turno e ne
  restituisce la differenza: e' l'unico modo di vedere cosa l'estrazione
  automatica ha scritto senza agganciarsi a funzioni private di Agno;
- `ares/config.py` raccoglie le impostazioni versionate e decide, in un punto
  solo, i percorsi dello stato. Importarlo non tocca il disco: la directory
  dello stato la crea `prepara_archivio()`, che chiamano i costruttori di
  `assistant.py` e il `main()` di ogni comando, dopo aver letto gli
  argomenti.

### Stato (`ares/state/`)

- `kairos.db` conserva sessioni, run normalizzati, profilo, memorie, entita'
  e indice degli offload; `filesystem.db` conserva il quaderno privato e i
  payload dei risultati tool troppo grandi per restare nel contesto;
- LanceDB conserva la conoscenza vettoriale con embedding serviti da Ollama;
- `stores.py` e' l'unico punto da cui si leggono entita', intuizioni e
  sessioni: non scrive mai, e non accende il modello salvo l'embedding della
  query sulle intuizioni;
- `lock.py` espone il lock cooperativo condiviso/esclusivo dello stato, su
  cui `platform_files.py` uniforma le primitive fra POSIX e Windows.

### Strumenti operativi

- `ops/preflight.py` verifica che il server Ollama risponda e che i modelli
  nominati in `config.py` siano scaricati, senza accendere niente e senza
  lasciare niente su disco;
- `ops/inspect_learning.py` rilegge gli archivi a modello spento;
- `backup/snapshots.py` coordina creazione, catalogo e restore degli snapshot
  locali; parser, conferme e output vivono in `backup/cli.py`, formato,
  checksum e verifica in `backup/integrity.py`, staging e rollback in
  `backup/restore.py`; `backup/files.py` raccoglie permessi ricorsivi e
  rinomina protetta condivisi dai due flussi, mentre `backup/probe.py` isola
  in un processo dedicato la lettura di LanceDB, così gli handle nativi sono
  chiusi prima delle rinomine. La façade offre anche alla chat il promemoria
  di rifare uno snapshot quando l'ultimo e' vecchio: la lettura non crea la
  directory dei backup e non solleva, perche' un avviso non deve poter
  impedire l'avvio;
- `entities/maintenance.py` espone la CLI e coordina lock e backup; l'audit
  in sola lettura vive in `entities/audit.py`, il piano e la transazione di
  fusione in `entities/merge.py`, i contratti condivisi in
  `entities/models.py`;
- `sessions/maintenance.py` coordina anteprima, conferma, lock e snapshot
  della retention; `sessions/retention.py` apre entrambi i backend, registra
  su Agno il filesystem dei payload e verifica la cancellazione congiunta di
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
risorse dell'host e alla rete, quindi richiedono conferma esplicita. La
conferma mostra il comando intero e, sotto, righe di attenzione per cio' che
va oltre la directory: passa da una shell, tocca percorsi fuori dalla
directory, chiede privilegi, usa la rete, cancella ricorsivamente
(`cli/render.py`, `avvertenze_comando`). Non e' un filtro - una lista nera
si aggira con un alias - ma il pezzo della conferma che dice dove guardare.
Stato, workspace, backup e `.env` restano fuori dal controllo versione.

La memoria durevole non chiede conferma prima di scrivere: `save_learning`,
`remember_about` e `update_user_memory` scrivono cio' che il modello decide,
e l'estrazione automatica aggiorna profilo e memorie dopo ogni risposta. Un
file del workspace o l'output di un comando con dentro un'istruzione puo'
quindi lasciare una traccia che viene reiniettata in ogni sessione futura.
Il controllo sta a turno chiuso, in due tempi: con `MOSTRA_APPRENDIMENTI`
gli strumenti di memoria mostrano i propri argomenti e la CLI stampa cosa e'
cambiato in profilo e memorie, con il testo intero; con
`CONFERMA_APPRENDIMENTI` chiede poi se tenerlo, e un `n` riporta i due store
all'istantanea letta prima del turno (`echo.py`: `istantanea`,
`ripristina`), verificando di esserci riuscito con una rilettura. E' tutto o
niente per turno; la correzione di una riga sola passa dagli stessi
strumenti di memoria, chiedendo ad Ares di correggere o cancellare. Entita'
e intuizioni restano fuori dal ripristino: si scrivono solo con strumenti
agentici, che il flusso mostra gia' uno per uno.

Che la conferma stia a valle della scrittura e non a monte non e' una scelta
fra due possibilita' disponibili. Le modalita' di apprendimento di Agno 3.0.5 sono
quattro, ma non valgono per tutti gli store: `PROPOSE` e' supportata dal solo
store delle intuizioni, `UserProfileStore` e `UserMemoryStore` la rifiutano
con un warning, e `HITL` non e' implementata da nessuno. Profilo e memorie
non sono percio' confermabili a livello di framework, e una conferma vera va
costruita in Ares: e' la voce corrispondente della `ROADMAP.md`. Il limite e'
sorvegliato da `tests/agno_contract_test.py`, cosi' il giorno in cui Agno lo
togliesse questa pagina diventerebbe falsa con una prova rossa invece che in
silenzio.

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
Un restore ucciso fra le rinomine puo' lasciare accanto allo stato la copia
`.tmp-precedente-*` e nessuna `tmp/`: la chat all'avvio e `ares-backup list`
lo dicono, nominando il residuo e lo snapshot pre-restore da cui tornare,
senza toccare niente.

La retention segue la sessione invece di una scadenza dei singoli risultati:
finché la conversazione esiste i suoi `result_id` restano risolvibili. La
manutenzione offline seleziona sessioni inattive ma non cancella niente
automaticamente; applicare una selezione richiede lock esclusivo e snapshot.
Il database principale deve conoscere il backend separato `filesystem.db`
prima di chiamare la cascata Agno, altrimenti il payload diventerebbe orfano:
questa registrazione è un'invariante verificata dalla prova dedicata. La
cancellazione di piu' sessioni non e' atomica: un guasto a meta' esce come
stato parziale, con l'elenco di cio' che e' sparito letto dall'archivio e lo
snapshot pre-manutenzione da cui tornare.

## Configurazione

Le impostazioni versionate sono in `ares/config.py`. Identita' e percorsi locali
possono essere sovrascritti con le variabili mostrate in `.env.example`; il
file `.env` del clone non viene pubblicato.
