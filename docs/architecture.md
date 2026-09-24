# Architettura

Ares è un'applicazione Python local-first. La CLI costruisce un agente Agno
collegato a Ollama, agli store persistenti e a un insieme limitato di
strumenti. Nessun servizio cloud è necessario; su scelta, nel `.env`, il
modello conversazionale e quello che estrae le memorie possono essere modelli
cloud di Ollama, che il daemon locale inoltra a `ollama.com`, insieme o
separatamente. L'embedding resta locale per costruzione.

## Struttura del codice

Il codice vive nel package `ares/`, diviso per responsabilità. Fra
parentesi il sottocomando di `ares` che ogni package espone: `cli/app.py` li
registra per nome di modulo, così `ares backup list` non importa Agno e
`ares --help` li elenca tutti. Gli alias `ares-backup`, `ares-sessions`...
passano dalla stessa App, e ogni sottopackage con un `__main__.py` risponde
anche a `python -m`.

```text
ares/
├── config.py       impostazioni versionate e percorsi dello stato
├── agent/          composizione dell'agente, turno, apprendimento, schemi
├── cli/            il comando `ares`: App Cyclopts, REPL, comandi, rendering, editor
├── state/          lettura degli archivi, lock, primitive di piattaforma
├── backup/         snapshot locali: creazione, verifica, restore      (ares backup)
├── entities/       audit e fusione delle entità                       (ares entities)
├── sessions/       retention di sessioni e risultati tool             (ares sessions)
└── ops/            preflight, ispezione e migrazione a modello spento (ares preflight, inspect, migrate)
```

`tests/` contiene le prove e il loro runner, `docs/` questa documentazione,
la radice i file di configurazione degli strumenti e gli script di setup. Lo
stato appreso non sta nel clone: vive in `~/.ares/stato`, con gli snapshot in
`~/.ares/backup`, così `ares` sul PATH lo trova da qualunque cartella e un
clone si può spostare o rifare senza perdere niente. `ARES_HOME`, `ARES_TMP`
e `ARES_BACKUP_DIR` lo spostano; `ops/migrazione.py` porta lì, una volta
sola, lo stato che una versione precedente teneva in `tmp/` dentro il clone,
e la chat si ferma finché non è successo.

## Componenti

### Interfaccia (`ares/cli/`)

- `app.py` è il comando `ares`: un'App Cyclopts con la chat come default e i
  sottocomandi di manutenzione registrati per nome di modulo, così si
  importano solo quando servono; `comando.py` è la fabbrica che dà a tutte
  le App gli stessi titoli e la console di `ui.py`, e tiene la tabella dei
  codici di uscita - 0 fatto, 1 guasto, 2 rifiutato, 3 occupato - con
  `esegui_protetto`, il contorno di lock ed errori che le manutenzioni condividono;
- `chat.py` avvia e coordina la REPL; `commands.py` contiene la tabella dei
  comandi locali, il loro dispatch e lo `StatoChat` che `/sessione`,
  `/metriche` e `/debug` modificano a metà conversazione, mentre
  `render.py` presenta eventi, conferme e metriche del turno;
- `log.py` zittisce o accende il log di Agno, per la chat, `/debug` e
  `ares inspect --prompt`; non importa niente di Ares;
- `conferma.py` è la conferma scritta dei comandi di manutenzione - la
  frase esatta da riscrivere prima di un restore, un prune o una fusione -
  con l'editor della chat sul terminale e `input()` in una pipe;
- `cartella.py` decide dove Ares lavora: la directory da cui si lancia
  `ares`, o quella di `--workspace`. Prima di aprirla ne elenca i rischi -
  la radice del disco, la home, una directory di sistema, una che contiene
  lo stato o il codice di Ares - e li fa confermare con la stessa conferma
  scritta; senza terminale una cartella rischiosa passa solo se nominata
  con `--workspace`. Scrive lo scheletro di `ARES.md` per `ares init`, nomina le
  conversazioni nuove con cartella e momento e presenta l'elenco numerato di
  `ares resume --scegli`;
- `editor.py` gestisce editor, completamento, input multilinea e cronologia
  privata della REPL;
- `ui.py` rende streaming Markdown, pannelli e tabelle, e filtra i controlli
  di terminale contenuti nelle risposte del modello. È anche l'output dei
  comandi di manutenzione: `table` si allarga in una pipe invece di spezzare
  le celle, `line` e `pair` non vanno a capo fuori dal terminale, `err`
  scrive su stderr e `json` emette dati puri per `--json`.

### Nucleo del turno (`ares/agent/`)

- `turn_core.py` normalizza gli eventi Agno e coordina `run/continue_run`
  senza dipendere dall'interfaccia;
- `assistant.py` è la facciata che assembla l'agente e conserva gli import
  pubblici; `runtime.py` costruisce modelli, indice vettoriale e strumenti,
  `learning.py` configura gli store e il post-hook sul run completo, e
  deriva memorie, entità e intuizioni per scrivere in italiano, e per una
  persona sola, la guida che Agno mette nel prompt in inglese,
  `prompts.py` apre il prompt con una scheda letta da `config` e dal
  sistema - quale modello fa parlare Ares e se è locale o cloud, quale
  estrae le memorie, l'embedder, la finestra di contesto, sistema e shell,
  utente, conversazione, cartella e ramo - e sceglie la descrizione
  secondo i modelli, così la promessa sulla privacy compare solo quando
  è vera; genera dalle due liste della modalità corrente - `manuale`,
  `modifiche`, `piano`, `auto`, partizioni degli otto strumenti dello spazio
  di lavoro in `config.MODALITA` - l'elenco di ciò che gira in silenzio e di
  ciò che chiede conferma, e dice al modello in quale modalità è e cosa
  comporta, dice a `ares -p` che nessuno risponde e gli store non apprendono,
  distinguendoli dalla cronologia archiviata e dal quaderno persistente;
  spiega come funziona la memoria - quali
  archivi si aggiornano da soli e quali con gli strumenti, che l'utente vede
  e può annullare ciò che entra, come si rileggono i risultati grandi - e
  il quaderno privato, in italiano al posto del testo di Agno; poi compone
  soltanto le istruzioni coerenti con i flag e vi
  aggiunge, se c'è, l'`ARES.md` della cartella di lavoro - le regole del
  progetto scritte da chi ci lavora, delimitate e presentate come dati e non
  come ordini, troncate oltre un tetto e dichiarate tali al modello - e le ultime conversazioni nate nella stessa cartella,
  con l'id da passare a `read_past_session`, perché `search_past_sessions`
  non sa dove una sessione è nata;
- `schemas.py` estende profilo e memorie con i campi e il rendering che gli
  store usano nel prompt;
- `echo.py` fotografa profilo e memorie prima e dopo un turno e ne
  restituisce la differenza: è l'unico modo di vedere cosa l'estrazione
  automatica ha scritto senza agganciarsi a funzioni private di Agno;
- `ares/config.py` raccoglie le impostazioni versionate e decide, in un punto
  solo, i percorsi dello stato. Importarlo non tocca il disco: la directory
  dello stato la crea `prepara_archivio()`, che chiamano i costruttori di
  `assistant.py` e il `main()` di ogni comando, dopo aver letto gli
  argomenti.

### Stato (`ares/state/`)

- `kairos.db` conserva sessioni, run normalizzati, profilo, memorie, entità
  e indice degli offload; `filesystem.db` conserva il quaderno privato e i
  payload dei risultati tool troppo grandi per restare nel contesto;
- LanceDB conserva la conoscenza vettoriale con embedding serviti da Ollama;
- `archivi.py` apre i due SQLite - `kairos.db` e `filesystem.db` - come
  vanno aperti, privati e con i pragma di Agno già materializzati, e
  costruisce il deposito dei risultati grandi; stava in `agent/runtime.py`,
  e la retention delle sessioni dipendeva dall'agente per aprire un database;
- `stores.py` è l'unico punto da cui si leggono entità, intuizioni e
  sessioni: non scrive mai, non stampa mai, e non accende il modello salvo
  l'embedding della query sulle intuizioni. Le sessioni portano nei metadati la cartella in
  cui sono nate - la scrive `build_assistant` passando `metadata=`
  all'agente, Agno la copia nella sessione nuova e la lascia com'è in una
  ripresa - e `stores.py` le filtra per cartella: `/sessioni` tiene quelle
  di qui e quelle senza cartella, `ares resume` solo quelle di qui;
- `lock.py` espone il lock cooperativo condiviso/esclusivo dello stato, su
  cui `platform_files.py` uniforma le primitive fra POSIX e Windows. Le chat
  tengono quello condiviso; un secondo lock esclusivo per utente copre ogni
  turno, dall'istantanea degli apprendimenti alla conferma e al rollback.
  Le chat dello stesso utente possono restare aperte, ma un turno occupato
  viene rifiutato prima di leggere l'istantanea o chiamare il modello;
  utenti diversi restano indipendenti. I file dei lock per utente vivono
  accanto al lock di stato, con un hash dell'identità nel nome, e non vengono
  rimossi al rilascio per non separare i processi su file diversi.
- `git.py` legge il ramo corrente da `.git/HEAD`, anche in un worktree, senza
  lanciare git: serve al banner e alla scheda del prompt, che non devono
  aspettare un processo né fallire dove git non c'è.

### Strumenti operativi

- `ops/preflight.py` verifica che il server Ollama risponda e che i modelli
  nominati in `config.py` siano scaricati, senza accendere niente e senza
  lasciare niente su disco;
- `ops/inspect_learning.py` rilegge gli archivi a modello spento; con
  `--prompt` stampa il system message intero che la chat manderebbe al
  modello da questa cartella, così com'è dopo che Agno ha aggiunto le
  proprie istruzioni e le memorie salvate;
- `ops/migrazione.py` è `ares migrate`: sposta stato e backup dal posto di
  prima - `tmp/` nel clone, `ares-backup` accanto - a `~/.ares`, sotto lock
  esclusivo e come rinomina di directory. Idempotente, e non tocca una
  destinazione che contiene già dei dati. I setup lo chiamano;
- `backup/snapshots.py` coordina creazione, catalogo e restore degli snapshot
  locali; parser, conferme e output vivono in `backup/cli.py`, formato,
  checksum e verifica in `backup/integrity.py`, staging e rollback in
  `backup/restore.py`; `backup/files.py` raccoglie permessi ricorsivi e
  rinomina protetta condivisi dai due flussi, mentre `backup/probe.py` isola
  in un processo dedicato la lettura di LanceDB, così gli handle nativi sono
  chiusi prima delle rinomine. La façade offre anche alla chat il promemoria
  di rifare uno snapshot quando l'ultimo è vecchio: la lettura non crea la
  directory dei backup e non solleva, perché un avviso non deve poter
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
3. Il modello può rispondere o richiedere uno strumento.
4. Le operazioni sensibili sospendono il run in attesa di conferma del client.
5. Il core esegue `continue_run` sullo stesso run dopo la decisione.
6. La macchina di apprendimento riceve l'output completo e aggiorna gli store.

L'ultimo passaggio è separato dall'interfaccia: l'apprendimento usa sempre il
run finale, evitando di perdere il contenuto prodotto dopo una conferma.

## Confini di sicurezza

Gli strumenti per i file sono limitati alla cartella di lavoro, che è la
directory da cui si lancia `ares`, ma questo confine non è una sandbox di
processo. Una cartella troppo larga - la home, il disco - allarga il raggio
di ogni strumento, ed è per questo che `cli/cartella.py` la fa confermare
per iscritto prima del banner. I comandi shell possono accedere alle
risorse dell'host e alla rete, quindi richiedono conferma esplicita. La
conferma mostra il comando intero e, sotto, righe di attenzione per ciò che
va oltre la directory: passa da una shell, tocca percorsi fuori dalla
directory, chiede privilegi, usa la rete, cancella ricorsivamente
(`cli/render.py`, `avvertenze_comando`). Non è un filtro - una lista nera
si aggira con un alias - ma il pezzo della conferma che dice dove guardare.
Stato e backup vivono in `~/.ares`, fuori dal clone; `.env` resta nel clone
ma fuori dal controllo versione.

La memoria durevole non chiede conferma prima di scrivere: `save_learning`,
`remember_about` e `update_user_memory` scrivono ciò che il modello decide,
e l'estrazione automatica aggiorna profilo e memorie dopo ogni risposta. Un
file del workspace o l'output di un comando con dentro un'istruzione può
quindi lasciare una traccia che viene reiniettata in ogni sessione futura.
Il controllo sta a turno chiuso, in due tempi: con `MOSTRA_APPRENDIMENTI`
gli strumenti di memoria mostrano i propri argomenti e la CLI stampa cosa è
cambiato in profilo e memorie, con il testo intero; con
`CONFERMA_APPRENDIMENTI` chiede poi se tenerlo, e un `n` riporta i due store
all'istantanea letta prima del turno (`echo.py`: `istantanea`,
`ripristina`), verificando di esserci riuscito con una rilettura. È tutto o
niente per turno; la correzione di una riga sola passa dagli stessi
strumenti di memoria, chiedendo ad Ares di correggere o cancellare. Entità
e intuizioni restano fuori dal ripristino: si scrivono solo con strumenti
agentici, che il flusso mostra già uno per uno.

Che la conferma sia a valle ha un prezzo, e va detto: fra la scrittura e la
risposta c'è una finestra, e un processo che muore lì dentro — un `kill`, un
crash, il terminale chiuso — lascia in memoria ciò che l'utente non ha ancora
accettato. Il lock dell'utente copre l'attesa fra due chat, non fra due vite
del processo. La garanzia è perciò condizionata: *se il processo sopravvive al
turno*, ciò che si rifiuta non sopravvive. Chiudere la finestra vuol dire
scrivere in una copia provvisoria e riversarla dopo il consenso, cioè una
scrittura differita che Agno non offre su questi store: è la voce 3 della
`ROADMAP.md`, e si decide lì. La metà che invece è già garantita — un `n`
riporta i due store all'istantanea di prima del turno, verificandolo con una
rilettura — è quella che `tests/cli_test.py` prova contro store veri.

Che la conferma stia a valle della scrittura e non a monte non è una scelta
fra due possibilità disponibili. Le modalità di apprendimento di Agno 3.0.9 sono
quattro, ma non valgono per tutti gli store: `PROPOSE` è supportata dal solo
store delle intuizioni, `UserProfileStore` e `UserMemoryStore` la rifiutano
con un warning, e `HITL` non è implementata da nessuno. Profilo e memorie
non sono perciò confermabili a livello di framework, e una conferma vera va
costruita in Ares: è la voce corrispondente della `ROADMAP.md`. Il limite è
sorvegliato da `tests/agno_contract_test.py`, così il giorno in cui Agno lo
togliesse questa pagina diventerebbe falsa con una prova rossa invece che in
silenzio.

Su POSIX lo stato appreso nasce privato: `~/.ares`, `stato/` e la directory
LanceDB a 0700,
i due database e la cronologia a 0600, come gli snapshot. La directory è il
controllo che regge, perché senza il diritto di attraversarla i modi dei file
dentro non si raggiungono; i database vengono comunque creati vuoti e con i
propri permessi prima che li apra SQLite, perché altrimenti nascerebbero con
la umask del processo. I permessi della directory li applica
`config.prepara_archivio()`, chiamata da chi apre l'archivio e non
dall'import: un comando che stampa soltanto l'aiuto non lascia niente
indietro. Su Windows vale la DACL ereditata dalla directory: un `chmod`
renderebbe i file soltanto read-only senza limitarne la lettura.

Su POSIX gli snapshot vengono pubblicati con una rinomina di directory. Su
Windows, dove LanceDB può impedire quella rinomina anche dopo la chiusura dei
reader nativi, il manifest viene pubblicato per ultimo come commit marker e
il restore conserva stabile la directory radice con una copia di rollback.
La copia iniziale deve essere completa prima di modificare la destinazione:
se fallisce, l'originale resta intatto e la copia parziale viene scartata.
Un restore ucciso fra le rinomine può lasciare accanto allo stato la copia
`.stato-precedente-*` e uno stato ricreato vuoto: la chat all'avvio e `ares backup list`
lo dicono, nominando il residuo e lo snapshot pre-restore da cui tornare,
senza toccare niente.

La retention segue la sessione invece di una scadenza dei singoli risultati:
finché la conversazione esiste i suoi `result_id` restano risolvibili. La
manutenzione offline seleziona sessioni inattive ma non cancella niente
automaticamente; applicare una selezione richiede lock esclusivo e snapshot.
Il database principale deve conoscere il backend separato `filesystem.db`
prima di chiamare la cascata Agno, altrimenti il payload diventerebbe orfano:
questa registrazione è un'invariante verificata dalla prova dedicata. La
cancellazione di più sessioni non è atomica: un guasto a metà esce come
stato parziale, con l'elenco di ciò che è sparito letto dall'archivio e lo
snapshot pre-manutenzione da cui tornare.

## Configurazione

Le impostazioni versionate sono in `ares/config.py`. Identità, percorsi
locali e i due modelli - conversazione ed estrazione delle memorie - possono
essere sovrascritti con le variabili mostrate in `.env.example`; il file
`.env` del clone non viene pubblicato.

I percorsi sono un oggetto, `Percorsi`: home, stato, backup e cartella di
lavoro, con i nomi derivati - i due SQLite, l'indice, il lock, la cronologia -
come proprietà. L'identità non sta qui: è un asse suo, e viaggia come `Utente`
accanto ai percorsi invece di essere uno dei campi. `leggi_percorsi` lo
costruisce da un ambiente e una directory dati, o da quelli veri, quando viene
chiamata; i nomi di sempre - `TMP_DIR`, `DB_FILE`, `BACKUP_DIR`,
`WORKSPACE_DIR`... - non esistono più, e con loro è sparita
`imposta_percorsi`: chi legge lo stato riceve un `Percorsi` come primo
parametro, e l'unico punto in cui se ne costruisce uno è il confine del
processo. La chat ne costruisce uno con la cartella scelta da `--workspace`,
che da lì in poi è quello che i costruttori ricevono; ed è così che due
insiemi di percorsi nello stesso processo non si confondono.

I modelli sono l'altro oggetto, `Impostazioni`: modello della conversazione,
modello dell'estrazione, embedder e sue dimensioni, indirizzo e `keep_alive`
di Ollama, contesto, temperature e i due `think`. I nomi del tuning restano in
`config.py` come sorgente - `.env`, ambiente e predefiniti si incontrano una
volta sola all'import - ed è `leggi_impostazioni()` a fotografarli quando
serve: la chat e i suoi comandi nel proprio corpo, preflight all'inizio, le
prove all'import. Da lì in poi `build_chat_model`, `build_learning_model`,
`build_knowledge`, `build_learning_machine`, `build_assistant` e le funzioni
dei prompt ricevono l'oggetto, quindi due conversazioni con modelli diversi
nello stesso processo sono due `Impostazioni` e non due mutazioni a distanza.
`OLLAMA_OPTIONS` e `LEARNING_OPTIONS` non esistono più come nomi: sono
proprietà del tipo, perché il contesto dell'estrazione dipende da quali due
modelli sono - si stringe solo quando sono diversi, altrimenti Ollama
riavvierebbe il runner a ogni passaggio perdendo la cache del prompt.
`avviso_cloud()` è un metodo, così preflight e banner dicono quello che la
conversazione che stanno per avviare farà uscire davvero. L'eccezione
deliberata è `ares/backup/snapshots.py`, che legge ancora i nomi di modulo per
il manifesto dello snapshot e per la compatibilità dell'embedder: quello è il
resoconto di come era configurato questo processo, ed è una garanzia
esistente, non una lettura di comodo.

La politica è il terzo oggetto, `Politica`: cosa una conversazione impara
(`Apprendimento`), quanto contesto storico vede (`Cronologia`), come si muove
nella cartella (`Workspace`) e cosa mostra di ciò che ha imparato (`Mostra`).
Anche qui i nomi - `LEARN_*`, `MOSTRA_*`, `CONFERMA_APPRENDIMENTI`, `WORKSPACE*`,
`SEARCH_PAST_SESSIONS`, `READ_CHAT_HISTORY`, `NUM_HISTORY_RUNS`,
`SESSIONI_ELENCO` - restano in `config.py` come sorgente, ed è
`leggi_politica()` a fotografarli alla porta del processo. Da lì in poi
`build_learning_machine`, `build_session_context_store`, `build_workspace`,
`build_assistant`, le funzioni dei prompt e il client della CLI ricevono
l'oggetto. Il motivo è lo stesso dei modelli, ma qui si vede meglio: il
paragrafo che spiega la memoria al modello descriveva store ed eco da `config`,
quindi poteva promettere che una scrittura sarebbe comparsa sotto la risposta
mentre l'eco era spenta; adesso descrive la stessa fotografia che ha costruito
gli store. `Apprendimento.automatici` e `Apprendimento.agentici` sono proprietà
derivate, perché il prompt le usa al posto di tre `or` allineati a mano.
Restano fuori `modo` (già parametro a ogni confine), `OFFLOAD_TOOL_RESULTS` e
`TOOL_RESULT_THRESHOLD_CHARS` (configurazione dell'indice), `DATETIME_FORMAT`,
`CRONOLOGIA_RIGHE` ed `ENTITA_FINESTRA_RICERCA` (formato del client), e le
costanti di backup e retention (garanzie, non politica).
