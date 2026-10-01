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
├── core/           nucleo applicativo: ciclo di vita della sessione, senza interfaccia
├── agent/          composizione dell'agente, turno, apprendimento, schemi
├── cli/            il comando `ares`: App Cyclopts, REPL, comandi, rendering, editor
├── state/          lettura degli archivi, lock, primitive di piattaforma
├── backup/         snapshot locali: creazione, verifica, restore      (ares backup)
├── entities/       audit e fusione delle entità                       (ares entities)
├── sessions/       retention di sessioni e risultati tool             (ares sessions)
└── ops/            preflight, ispezione e migrazione a modello spento (ares preflight, inspect, migrate)
```

`tests/` contiene le prove e il loro runner, `docs/` questa documentazione,
la radice i file di configurazione degli strumenti e gli script di setup.

Lo stato appreso non sta nel clone: vive in `~/.ares/stato`, con gli
snapshot in `~/.ares/backup`, così `ares` lo trova da qualunque cartella e
il clone si può spostare o rifare senza perdere niente. `ARES_HOME`,
`ARES_TMP` e `ARES_BACKUP_DIR` lo spostano. `ops/migrazione.py` porta lì lo
stato che le versioni precedenti tenevano in `tmp/` dentro il clone, e la
chat non parte finché la migrazione non è avvenuta.

## Componenti

### Interfaccia (`ares/cli/`)

- `app.py` è il comando `ares`: un'App Cyclopts con la chat come default e i
  sottocomandi di manutenzione importati solo quando servono.
- `comando.py` dà a tutte le App gli stessi titoli e la console di `ui.py`,
  e tiene i codici di uscita — 0 fatto, 1 guasto, 2 rifiutato, 3 occupato —
  con `esegui_protetto`, il contorno di lock ed errori condiviso dalle
  manutenzioni: apre lo stato dal nucleo, esclusivo per chi scrive e
  condiviso per chi legge.
- `chat.py` avvia e coordina la REPL. `commands.py` contiene la tabella dei
  comandi locali, il loro dispatch e lo `StatoChat` che `/sessione`,
  `/metriche` e `/debug` modificano a metà conversazione. `render.py`
  presenta eventi, conferme e metriche del turno.
- `log.py` zittisce o accende il log di Agno (chat, `/debug`,
  `ares inspect --prompt`); non importa niente di Ares.
- `conferma.py` è la conferma scritta dei comandi di manutenzione — la frase
  esatta da riscrivere prima di un restore, un prune o una fusione — con
  l'editor della chat sul terminale e `input()` in una pipe.
- `cartella.py` decide dove Ares lavora: la directory da cui si lancia
  `ares`, o quella di `--workspace`. Se è rischiosa — la radice del disco,
  la home, una directory di sistema, una che contiene lo stato o il codice
  di Ares — la fa confermare per iscritto; senza terminale una cartella
  rischiosa passa solo se nominata con `--workspace`. Scrive anche lo
  scheletro di `ARES.md` per `ares init`, genera i nomi delle conversazioni
  nuove e presenta l'elenco di `ares resume --scegli`.
- `editor.py` gestisce editor, completamento, input multilinea e cronologia
  privata della REPL.
- `ui.py` rende streaming Markdown, pannelli e tabelle, e filtra i controlli
  di terminale contenuti nelle risposte del modello. È anche l'output dei
  comandi di manutenzione: `table` si allarga in una pipe invece di spezzare
  le celle, `line` e `pair` non vanno a capo fuori dal terminale, `err`
  scrive su stderr e `json` emette dati puri per `--json`.

### Nucleo applicativo (`ares/core/`)

Ciò che un client qualsiasi deve fare allo stesso modo, senza stampare né
chiedere niente: il ciclo di vita della sessione e il turno completo.

- `session.py`: `Sessioni` genera l'id di una conversazione nuova, elenca
  quelle della cartella, verifica il proprietario (`SessioneDiAltri`) e
  costruisce l'agente all'apertura, al cambio di sessione e al cambio di
  modalità. Restituisce una `SessioneAttiva` (id, modalità, agente); la
  sessione corrente la tiene il client.
- `id_sessione.py`: l'id leggibile, da cartella e momento.
- `stato.py`: `stato_in_uso` tiene il lock condiviso dello stato per tutta
  la vita del client, `stato_esclusivo` quello esclusivo per backup,
  restore e manutenzioni che scrivono. Entrambi rifiutano lo stato o i
  backup rimasti nel posto delle versioni vecchie (`StatoDaMigrare`): solo
  `ares migrate` li tocca, con i suoi lock.
- `autorizzazioni.py`: le regole che seguono dalla presenza dichiarata dal
  client. Senza presenza nessuna modalità scrive in silenzio
  (`verifica_modo`, anche per `/modo`) e ogni strumento in pausa è
  rifiutato senza chiedere; con presenza il client risponde a una
  `Richiesta` con una `Decisione`, e `confirm()`/`reject()` li chiama il
  nucleo (`risolvi_pausa`).
- `turn.py`: `esegui_turno` esegue un turno sotto il lock del turno
  dell'utente: fotografia di profilo e memorie, turno con le pause per gli
  strumenti, variazioni, conferma degli apprendimenti e ripristino se
  l'utente rifiuta. Il client mostra e chiede tramite il protocollo
  `ClienteTurno`; il risultato è un `EsitoTurno`.

La CLI ne è un client: decide cosa chiedere e come mostrarlo. Il piano dei
passi successivi è in [core-refactor-plan.md](core-refactor-plan.md).

### Nucleo del turno (`ares/agent/`)

- `turn_core.py` normalizza gli eventi Agno e coordina `run/continue_run`
  senza dipendere dall'interfaccia.
- `assistant.py` assembla l'agente in un punto solo e riesporta i
  costruttori; `runtime.py` costruisce modelli, indice vettoriale e
  strumenti, con un prefisso che dice dove agiscono (`quaderno_`,
  `workspace_`).
- `learning.py` configura gli store e il post-hook sul run completo, e
  riscrive in italiano, per una persona sola, la guida che Agno mette nel
  prompt in inglese per memorie, entità e intuizioni. Con l'estrazione cloud
  profilo, memorie e contesto estraggono insieme, ciascuno in un thread;
  dopo un Ctrl-C un `Cancello` ferma le loro scritture, perché nessuna
  arrivi dopo la fotografia del turno.
- `agno_interni.py` è l'unico posto che tocca nomi privati di Agno
  (`_build_functions_for_model`, `_should_expose_tools`,
  `determine_tools_for_model`, `_filter_store_kwargs`, i metodi di
  `Ollama` che formattano e leggono i messaggi); `INTERNI` li elenca e la
  prova `contratto` verifica che esistano nella versione installata.
  `OllamaConRagionamento`, il modello di conversazione ed estrazione,
  conserva il `thinking` di Ollama e lo rimanda al passo successivo.
- `prompts.py` compone il prompt solo con ciò che è davvero abilitato:
  - una scheda dell'avvio: modelli (e se sono locali o cloud), embedder,
    finestra di contesto, sistema e shell, utente, conversazione, cartella e
    ramo; la promessa sulla privacy compare solo quando è vera;
  - la modalità corrente (`manuale`, `modifiche`, `piano`, `auto`, definite
    in `config.MODALITA`) con l'elenco di ciò che gira da solo e di ciò che
    chiede conferma;
  - in `ares -p`, che nessuno risponde e gli store non apprendono;
  - come funziona la memoria: cosa si aggiorna da solo e cosa con gli
    strumenti, che l'utente vede e può annullare ciò che entra, come si
    rileggono i risultati grandi; e il quaderno privato;
  - l'`ARES.md` della cartella, delimitato e presentato come dati, non come
    ordini, troncato oltre un tetto e dichiarato tale al modello;
  - le ultime conversazioni nate nella stessa cartella, con l'id per
    `read_past_session`, perché `search_past_sessions` non sa dove una
    sessione è nata.
- `schemas.py` estende profilo e memorie con i campi e il rendering che gli
  store usano nel prompt.
- `echo.py` fotografa profilo e memorie prima e dopo un turno e ne
  restituisce la differenza, senza agganciarsi a funzioni private di Agno.
- `ares/config.py` raccoglie le impostazioni versionate e decide i percorsi
  dello stato (vedi [Configurazione](#configurazione)). Importarlo non tocca
  il disco: la directory dello stato la crea `prepara_archivio()`, chiamata
  dai costruttori e dal `main()` di ogni comando dopo aver letto gli
  argomenti.

### Stato (`ares/state/`)

- `kairos.db` conserva sessioni, run normalizzati, profilo, memorie, entità
  e indice degli offload; `filesystem.db` conserva il quaderno privato e i
  payload dei risultati tool troppo grandi per restare nel contesto.
- LanceDB conserva la conoscenza vettoriale con embedding serviti da Ollama.
- `archivi.py` apre i due SQLite, privati e con i pragma di Agno già
  materializzati, e costruisce il deposito dei risultati grandi. Sta qui e
  non nell'agente perché anche la retention delle sessioni lo usa.
- `stores.py` è l'unico punto da cui si leggono entità e intuizioni: non
  scrive, non stampa, e non accende il modello salvo l'embedding della
  query sulle intuizioni.
- `sessioni.py` legge le conversazioni come `SessioneRiferimento` e
  `Conversazione`, senza far uscire gli oggetti di Agno; i run si leggono
  solo per le voci mostrate. Ogni sessione porta nei metadati la cartella in
  cui è nata (la passa `build_assistant`, Agno la conserva nelle riprese):
  `/sessioni` mostra quelle di qui e quelle senza cartella (`qui`),
  `ares resume` e il prompt solo quelle di qui (`nate_qui`).
- `lock.py` espone il lock cooperativo condiviso/esclusivo dello stato;
  `platform_files.py` ne uniforma le primitive fra POSIX e Windows. Le chat
  tengono il lock condiviso e la manutenzione quello esclusivo, attraverso
  `core/stato.py`; `ops/migrazione.py` è l'unico che usa `lock_stato`
  direttamente. Un secondo lock esclusivo per utente copre
  ogni turno, dall'istantanea degli apprendimenti alla conferma e al
  rollback: una seconda chat dello stesso utente resta aperta, ma un turno
  occupato viene rifiutato prima di leggere l'istantanea o chiamare il
  modello. Utenti diversi restano indipendenti. I file dei lock per utente
  vivono accanto al lock di stato, con un hash dell'identità nel nome, e non
  vengono rimossi al rilascio, per non separare i processi su file diversi.
- `git.py` legge il ramo corrente da `.git/HEAD`, anche in un worktree,
  senza lanciare git: banner e prompt non aspettano un processo né
  falliscono dove git non c'è.

### Strumenti operativi

- `ops/preflight.py` verifica che Ollama risponda e che i modelli
  configurati siano scaricati, senza accendere niente e senza scrivere su
  disco. Avvisa se un modello locale già caricato con il contesto di Ares
  non sta tutto in VRAM (`/api/ps`).
- `ops/inspect_learning.py` rilegge gli archivi a modello spento; con
  `--prompt` stampa il system message intero che la chat manderebbe al
  modello da questa cartella, comprese istruzioni e memorie aggiunte da Agno.
- `ops/migrazione.py` è `ares migrate`: sposta stato e backup da `tmp/` nel
  clone (e `ares-backup` accanto) a `~/.ares`, sotto lock esclusivo e come
  rinomina di directory. È idempotente e non tocca una destinazione che
  contiene già dati. I setup lo chiamano.
- `backup/snapshots.py` coordina creazione, catalogo e restore degli
  snapshot:
  - `backup/cli.py`: parser, conferme e output;
  - `backup/integrity.py`: formato, checksum e verifica;
  - `backup/restore.py`: staging e rollback;
  - `backup/files.py`: permessi ricorsivi e rinomina protetta;
  - `backup/probe.py`: legge LanceDB in un processo dedicato, così gli
    handle nativi sono chiusi prima delle rinomine.

  Offre anche alla chat il promemoria di rifare uno snapshot quando
  l'ultimo è vecchio; la lettura non crea directory e non solleva, perché
  un avviso non deve impedire l'avvio.
- `entities/maintenance.py` espone la CLI e coordina lock e backup; l'audit
  in sola lettura vive in `entities/audit.py`, piano e transazione di
  fusione in `entities/merge.py`, i contratti condivisi in
  `entities/models.py`.
- `sessions/maintenance.py` coordina anteprima, conferma, lock e snapshot
  della retention; `sessions/retention.py` apre entrambi i backend, registra
  su Agno il filesystem dei payload e verifica la cancellazione congiunta di
  sessione, run, contesto appreso, indice e risultato offloaded.
- `setup.sh` e `setup.ps1` ricostruiscono lo stesso ambiente bloccato sui due
  sistemi verificati.

## Flusso di un turno

1. Il client consegna il messaggio a `core/turn.py`, che prende il lock del
   turno dell'utente e fotografa profilo e memorie.
2. `agent/turn_core.py` avvia Agno e pubblica eventi indipendenti
   dall'interfaccia.
3. Il modello può rispondere o richiedere uno strumento.
4. Le operazioni sensibili sospendono il run. Il nucleo chiede al client
   una decisione per ciascuna, o le rifiuta da solo se il client non è
   presidiato (`core/autorizzazioni.py`).
5. Il core esegue `continue_run` sullo stesso run dopo la decisione.
6. La macchina di apprendimento riceve l'output completo e aggiorna gli store.
7. Il nucleo confronta profilo e memorie con la fotografia; il client mostra
   le variazioni e, se l'utente rifiuta, il nucleo ripristina. Poi il lock
   si rilascia.

L'apprendimento usa sempre il run finale, quindi non perde il contenuto
prodotto dopo una conferma.

## Confini di sicurezza

### Cartella di lavoro e comandi

Gli strumenti per i file sono limitati alla cartella di lavoro, ma questo
confine non è una sandbox di processo. Una cartella troppo larga (la home,
il disco) allarga il raggio di ogni strumento: per questo `cli/cartella.py`
la fa confermare per iscritto prima del banner.

I comandi shell possono accedere alle risorse dell'host e alla rete, quindi
richiedono conferma esplicita. La conferma mostra il comando intero e, sotto,
righe di attenzione quando passa da una shell, tocca percorsi fuori dalla
cartella, chiede privilegi, usa la rete o cancella ricorsivamente
(`avvertenze_comando` in `cli/render.py`). Non è un filtro — una lista nera
si aggira con un alias — ma indica dove guardare.

Stato e backup vivono in `~/.ares`, fuori dal clone; `.env` resta nel clone
ma fuori dal controllo versione.

### Memoria durevole

La memoria durevole non chiede conferma prima di scrivere: `save_learning`,
`remember_about` e `update_user_memory` scrivono ciò che il modello decide,
e l'estrazione automatica aggiorna profilo e memorie dopo ogni risposta. Un
file o l'output di un comando con dentro un'istruzione può quindi lasciare
una traccia reiniettata in ogni sessione futura.

Non è una scelta: in Agno 3.0.11 `PROPOSE` vale solo per le intuizioni e
`HITL` per nessuno store, quindi profilo e memorie non sono confermabili a
livello di framework (dettagli in [agno.md](agno.md);
`tests/agno_contract_test.py` sorveglia il limite). Il controllo sta quindi
a turno chiuso, in due tempi:

1. con `MOSTRA_APPRENDIMENTI` gli strumenti di memoria mostrano i propri
   argomenti e la CLI stampa per intero cosa è cambiato in profilo e memorie;
2. con `CONFERMA_APPRENDIMENTI` la CLI chiede se tenerlo: un `n` riporta i
   due store all'istantanea letta prima del turno (`istantanea` e
   `ripristina` in `echo.py`) e verifica il ripristino rileggendoli.
   `tests/cli_test.py` lo prova contro store veri.

È tutto o niente per turno; per correggere una riga sola si chiede ad Ares
di usare gli strumenti di memoria. Entità e intuizioni restano fuori dal
ripristino: si scrivono solo con strumenti agentici, che il flusso mostra
uno per uno.

**Limite:** fra la scrittura e la risposta c'è una finestra. Se il processo
muore lì dentro (un `kill`, un crash, il terminale chiuso) resta in memoria
ciò che l'utente non ha ancora accettato: il lock copre l'attesa fra due
chat, non fra due vite del processo. Chiudere la finestra richiede una
scrittura differita, che Agno non offre su questi store: è la voce 3 della
[roadmap](ROADMAP.md).

### Permessi su disco

Su POSIX lo stato appreso nasce privato: `~/.ares`, `stato/` e la directory
LanceDB a 0700, i due database e la cronologia a 0600, come gli snapshot.
Il controllo che regge è la directory, perché senza il diritto di
attraversarla i file dentro non si raggiungono; i database vengono comunque
creati vuoti con i propri permessi prima che li apra SQLite, altrimenti
nascerebbero con la umask del processo. I permessi li applica
`config.prepara_archivio()` all'apertura dell'archivio, non all'import: un
comando che stampa l'aiuto non lascia niente su disco. Su Windows vale la
DACL ereditata dalla directory: un `chmod` renderebbe i file solo read-only,
senza limitarne la lettura.

### Snapshot e restore

Su POSIX gli snapshot vengono pubblicati con una rinomina di directory. Su
Windows, dove LanceDB può impedire quella rinomina anche dopo la chiusura dei
reader nativi, il manifest viene pubblicato per ultimo come commit marker e
il restore conserva stabile la directory radice con una copia di rollback.
La copia iniziale deve essere completa prima di modificare la destinazione:
se fallisce, l'originale resta intatto e la copia parziale viene scartata.
Un restore ucciso fra le rinomine può lasciare accanto allo stato la copia
`.stato-precedente-*` e uno stato ricreato vuoto: la chat all'avvio e
`ares backup list` lo segnalano, nominando il residuo e lo snapshot
pre-restore da cui tornare, senza toccare niente.

### Retention

La retention segue la sessione, non una scadenza dei singoli risultati:
finché la conversazione esiste i suoi `result_id` restano risolvibili. La
manutenzione offline seleziona sessioni inattive ma non cancella niente da
sola; applicare una selezione richiede lock esclusivo e snapshot. Prima
della cascata Agno il database principale deve conoscere il backend
separato `filesystem.db`, altrimenti il payload resterebbe orfano: è
un'invariante verificata dalla prova dedicata. La cancellazione di più
sessioni non è atomica: un guasto a metà esce come stato parziale, con
l'elenco di ciò che è sparito e lo snapshot pre-manutenzione da cui tornare.

## Configurazione

Le impostazioni versionate sono in `ares/config.py`. Identità, percorsi
locali e i due modelli (conversazione ed estrazione delle memorie) si
sovrascrivono con le variabili di `.env.example`; il `.env` del clone non
viene pubblicato.

`config.py` è la sorgente dei valori, ma il resto del codice non li legge
da lì: li riceve in tre oggetti, costruiti una volta al confine del processo.

| Oggetto | Costruito da | Contiene |
| --- | --- | --- |
| `Percorsi` | `leggi_percorsi()` | home, stato, backup, cartella di lavoro; i nomi derivati (SQLite, indice, lock, cronologia) sono proprietà |
| `Impostazioni` | `leggi_impostazioni()` | modelli di conversazione, estrazione ed embedding, host e `keep_alive` di Ollama, contesto, temperature, `think` |
| `Politica` | `leggi_politica()` | cosa si impara (`Apprendimento`), quanta cronologia (`Cronologia`), come si usa la cartella (`Workspace`), cosa si mostra (`Mostra`) |

L'identità viaggia a parte, come `Utente`.

### Dipendenze esplicite

Chi costruisce modelli, store, spazio di lavoro, prompt o l'agente intero
riceve questi oggetti come parametri, senza default letti da `config`. I
motivi:

- **Due conversazioni nello stesso processo non si confondono.** Modelli o
  politiche diverse sono due oggetti, non due mutazioni di un nome di modulo.
- **Il prompt descrive ciò che è stato costruito.** I paragrafi che
  spiegano memoria e strumenti al modello leggono la stessa `Politica` che
  ha costruito gli store, quindi non possono promettere un'eco spenta o uno
  strumento assente.
- **I default si leggono alla chiamata.** Un parametro vuoto come `modo`
  vale `config.MODO_PREDEFINITO` letto nel corpo della funzione, non nella
  firma, così una prova che lo cambia con `patch.object` viene rispettata.

Regole derivate che vivono sui tipi: il contesto dell'estrazione si stringe
(`NUM_CTX_ESTRAZIONE`) solo quando i due modelli sono diversi, altrimenti
Ollama riavvierebbe il runner fra risposta ed estrazione; `avviso_cloud()`
dice a preflight e banner quali ruoli escono dalla macchina.

Restano fuori dalla `Politica`: `modo`, già parametro a ogni confine;
`OFFLOAD_TOOL_RESULTS` e `TOOL_RESULT_THRESHOLD_CHARS`; `QUADERNO_PREFIX`,
perché il quaderno c'è sempre; i formati del client
(`FUSO_ORARIO`, `CRONOLOGIA_RIGHE`, `ENTITA_FINESTRA_RICERCA`); le
costanti di backup e retention, che sono garanzie e non politica.

L'unica eccezione deliberata è `ares/backup/snapshots.py`, che legge i nomi
di modulo per il manifesto dello snapshot e la compatibilità dell'embedder:
registra come era configurato il processo che l'ha scritto.
