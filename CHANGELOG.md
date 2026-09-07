# Changelog

Le modifiche rilevanti di Ares sono raccolte in questo file. Il formato segue
[Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/) e il progetto
adotta il versionamento semantico a partire dal primo rilascio pubblico.

## [Unreleased]

Prove con Ollama (`tests/run.py --tutte`) verdi il 2026-09-07 su Agno 3.0.5,
3 prove su 3, con `glm-5.3-flash:cloud` in conversazione e
`deepseek-v4-pro:cloud` in estrazione dal `.env`; il modello conversazionale
locale non è stato provato in questo giro.

### Added

- **Ares sa dove si trova e che cosa è.** Il prompt si apre con una scheda
  letta dalla configurazione di questo avvio e dal sistema, non scritta a
  mano: quale modello lo fa parlare e se gira in locale o su `ollama.com`,
  quale modello estrae profilo e memorie dopo ogni risposta, l'embedder che
  resta locale, la finestra di contesto in token e quanti scambi ha in vista,
  sistema operativo e shell, utente, conversazione, cartella di lavoro e
  ramo git. La descrizione segue i modelli: "nessuna conversazione esce di
  qui" compare solo se è vera, altrimenti dice in una riga quale ruolo sta
  su `ollama.com` e che la persona l'ha scelto. Su Windows l'esempio per
  lanciare una riga intera è `['powershell', '-Command', ...]` e non
  `bash -lc`. La lettura del ramo git da `HEAD` passa da `state/git.py`,
  così `agent/` non importa `cli/`; le istruzioni non danno più un genere
  all'utente;
- **`ares inspect --prompt`** stampa il system message intero che la chat
  manderebbe al modello da questa cartella, senza aprire un turno: la
  descrizione e le istruzioni di Ares, poi ciò che Agno aggiunge da sé -
  le convenzioni del quaderno, le regole della macchina di apprendimento,
  memorie ed entità già salvate, data e nome. Senza `--session` è la
  conversazione nuova che `ares` aprirebbe adesso; con `--session` quella
  nominata. Prima, l'unico modo di sapere cosa riceveva il modello era
  leggere i pezzi in `prompts.py` e immaginare il resto; una modifica ai
  prompt si giudica ora leggendo questo, e due versioni si confrontano con
  `diff`. Il testo mostra anche quanto del prompt è scritto da Agno in
  inglese, ed è il punto di partenza per riscriverlo.

- **La conferma di una scrittura mostra la differenza.** Un `write_file` su
  un file che esiste già lo sostituisce da capo, e il contenuto nuovo per
  intero diceva tutto tranne la cosa da guardare: cosa sparisce. La
  richiesta mostra ora il diff unificato riga per riga; un file nuovo resta
  intero, e un percorso fuori dalla radice non viene letto;
- `ares inspect --prompt --modo piano` stampa il prompt di quella modalità;
- **Le modalità, come in Claude Code.** `manuale` chiede conferma per
  tutto ciò che lascia una traccia sul disco ed è il valore distribuito;
  `modifiche` scrive e modifica file da sola e chiede per spostare,
  cancellare ed eseguire; `piano` legge soltanto, e gli strumenti che
  scrivono non arrivano nemmeno al modello, a cui il prompt chiede di
  proporre; `auto` non chiede mai, si sceglie solo con `ares --modo auto`,
  il banner lo dice in rosso e non si combina con `-p`. Ogni modalità è
  una partizione degli otto strumenti in `config.MODALITA`, e il paragrafo
  del prompt e la riga nella scheda si generano da lì. `ares --modo` la
  sceglie per una sessione, `/modo` la cambia a metà conversazione
  ricostruendo l'agente sulla stessa sessione, e `/sessione` la porta con
  sé. `WORKSPACE_ALLOWED` e `WORKSPACE_CONFIRM` non esistono più;
- **Il prompt è tutto in italiano, e spiega la memoria.** Agno aggiunge
  da sé le guide degli store di apprendimento, del quaderno e del
  Markdown, in inglese e per un agente di squadra: "conserva gli obiettivi
  del team perché ne beneficino altri utenti". Ares deriva memorie, entità
  e intuizioni con una guida in italiano per una persona sola, scrive la
  propria per il quaderno privato e la riga sul Markdown, e prima degli
  strumenti dice come funziona la memoria: quali archivi si aggiornano da
  soli dopo ogni risposta e quali solo con gli strumenti, che ciò che entra
  in profilo e memorie compare sotto la risposta e si può annullare, che
  ciò che sa viene da conversazioni passate e l'utente di oggi ha la
  precedenza, e che un risultato oltre la soglia si rilegge a pagine. Le
  tre righe che restano in inglese - ora, nome, anteprima dei risultati -
  sono di Agno e non passano da qui;
- **Ares sa cosa può fare da solo e cosa no.** Il paragrafo sullo spazio
  di lavoro è generato dalle due liste di `config.py`: nomina uno per uno
  gli strumenti che girano in silenzio e quelli che fermano il turno, così
  una lista che cambia non lascia indietro le istruzioni. In `ares -p` un
  avviso dice che nessuno risponde, quali strumenti verrebbero rifiutati e
  che niente entra in memoria.

### Changed

- **I documenti usano gli accenti.** `CHANGELOG.md`, `ROADMAP.md`,
  `docs/architecture.md` e `docs/agno.md` erano scritti con l'apostrofo al
  posto dell'accento, come il codice, mentre README, CONTRIBUTING, SECURITY
  e la guida ai test avevano gli accenti veri: ora la regola è scritta in
  CONTRIBUTING - accenti nei Markdown, apostrofo ASCII in codice, commenti,
  messaggi, prompt e configurazione - e i quattro documenti la seguono;
- **Le dipendenze fra pacchetti vanno in un verso solo.** `state/archivi.py`
  apre i due SQLite e il deposito dei risultati grandi: stavano in
  `agent/runtime.py`, e `sessions` dipendeva dall'agente per aprire un
  database. `stampa_store` sta in `cli/ui.py`: era in `state/stores.py`, che
  dichiara di non scrivere niente e importava l'interfaccia per stampare.
  `lock_stato`, `build_filesystem` e `build_workspace` leggono il default da
  `config` quando vengono chiamati e non quando vengono definiti;
- **Un solo significato per ogni codice di uscita.** Erano cinque wrapper
  con quattro idee diverse: lo stato occupato valeva 1 in chat, backup e
  migrate e 2 in sessions ed entities; una conferma sbagliata valeva 1 in
  una fusione e 2 in un restore. Ora la tabella sta in `cli/comando.py` e
  vale ovunque: 0 fatto, 1 guasto, 2 rifiutato - argomenti incoerenti,
  conferma negata o sbagliata, manutenzione rifiutata, cartella rifiutata,
  niente da riprendere, `-p` con `auto` - e 3 stato occupato da un altro
  processo, che uno script può riprovare. `esegui_protetto` è il contorno
  di lock ed errori che sessions, entities e backup usano al posto dei
  propri. Chi leggeva questi codici da uno script deve aggiornarsi: è il
  motivo per cui questa versione è una minor;
- **Scrivere e modificare un file chiedono conferma.** Stavano fra gli
  strumenti silenziosi, con l'idea che un file nuovo nella propria cartella
  non fosse distruttivo. Ma ciò che il modello legge - un file del
  progetto, l'output di un comando, lo stesso `ARES.md` - può contenere
  un'istruzione, e una scrittura che nessuno guarda può riscrivere
  `ARES.md`, uno script o un Makefile: non distrugge oggi, esegue domani.
  La richiesta mostra il contenuto per intero, come già faceva con i
  comandi. `WORKSPACE_ALLOWED` tiene leggere, elencare e cercare;
  `WORKSPACE_CONFIRM` tutto ciò che lascia una traccia sul disco;
- **`ares -p` non scrive in memoria.** Prima l'apprendimento avveniva come
  in chat, e la domanda "tenere in memoria?" senza nessuno che rispondesse
  valeva sì: uno script o una pipe con un testo ostile scrivevano profilo
  e memorie in modo durevole. Ora l'agente nasce senza il post-hook che
  estrae e senza gli strumenti che scrivono negli store
  (`build_assistant(interattivo=False)`); ciò che Ares sa già entra nel
  contesto come sempre;
- **`ARES.md` entra come dati, non come ordini.** L'intestazione lo
  presenta come le regole del progetto scritte da chi ci lavora, da
  applicare finché non contraddicono l'utente e mai per eseguire scritture
  o comandi che l'utente non ha chiesto; il testo sta fra una riga di
  inizio e una di fine. "Seguile" davanti a un testo altrui era la forma
  esatta di un'iniezione.

### Fixed

- **La sonda LanceDB è misurata.** `backup/probe.py` risultava allo 0% pur
  girando a ogni `create` e `verify`: la misura di copertura segue il
  package `ares`, e un file lanciato per percorso è `__main__` e basta.
  `integrity.py` la lancia come modulo, `-m ares.backup.probe`, e il runner
  passa `COVERAGE_FILE` assoluto così un figlio non lascia la misura nella
  cartella usa-e-getta da cui parte;
- **L'avviso sul cloud dice tutto ciò che esce.** Con la conversazione in
  cloud non escono solo "prompt e risposte": escono i file letti dal
  workspace, l'output dei comandi, profilo e memorie iniettati nel prompt,
  le conversazioni passate rilette. Preflight, banner, SECURITY.md,
  `.env.example` e README lo dicono per intero. `docs/architecture.md` e
  `docs/agno.md` dicevano ancora che l'estrazione delle memorie resta locale
  per costruzione: da `ARES_LEARNING_MODEL` non è più vero;
- allineamenti documentali: `docs/testing.md` diceva che la copertura in CI
  gira solo su Ubuntu e che il tetto di una prova offline è di tre minuti
  (è su entrambi i sistemi, e sono sei); il README ripeteva per Windows un
  blocco identico a quello Linux e non nominava `ARES_BIN_DIR`;
  `CONTRIBUTING.md` chiamava `tmp/` lo stato reale; due refusi nel
  changelog di 0.5.0. Le due prove con Ollama impostavano `ARES_WORKSPACE`,
  che nessuno legge più, e rilanciavano il processo figlio con il clone
  come directory corrente: ora la cartella di prova. `--help` è provato
  su tutti e sette i comandi, `preflight` e `migrate` compresi;
- **Su Windows Ares riceve l'ora con il fuso.** `zoneinfo` non ha un
  database dei fusi su Windows e `ZoneInfo("Europe/Rome")` falliva: Agno lo
  segnalava con un WARNING e metteva nel prompt un'ora senza fuso. `tzdata`
  entra fra le dipendenze. Il WARNING, stampato da Rich su stdout, è stato
  anche ciò che ha fatto fallire la prova di `--prompt` sul runner Windows.

- `configura_log_agno` e i nomi dei logger di Agno stanno in `cli/log.py`,
  che non importa niente di Ares: `chat.py` e `commands.py` lo importano da
  lì invece che l'uno dall'altro, e `ares inspect --prompt` lo usa per
  tenere il log di Agno fuori dal testo che stampa. `cyclopts` compariva due
  volte in `pyproject.toml`; resta la riga con il pavimento.

## [0.5.0] - 2026-09-07

Prove con Ollama (`tests/run.py --tutte`) verdi il 2026-09-07 su Agno 3.0.5 e
LanceDB 0.38.0, 10 prove su 10, sia con il modello conversazionale locale -
cioè la configurazione che questa versione distribuisce - sia con
`glm-5.3-flash:cloud` nei due ruoli dal `.env`.

### Added

- **Ares lavora nella cartella da cui viene lanciato.** Si entra in un
  progetto e si scrive `ares`, come con Claude Code o Codex: la cartella
  corrente è lo spazio di lavoro, oppure lo è quella di
  `--workspace PERCORSO`. Non c'è più una directory fissa accanto al clone e
  `ARES_WORKSPACE` sparisce dal `.env`; una cartella che non esiste è un
  errore, non una da creare. Prima di aprire, `cli/cartella.py` elenca i
  motivi per cui un percorso è rischioso - radice del disco, home intera,
  directory di sistema, una cartella che contiene lo stato, i backup o il
  codice di Ares - e sul terminale chiede di riscriverlo con la stessa
  conferma scritta della manutenzione; senza terminale una cartella così
  passa solo se nominata con `--workspace`, altrimenti l'avvio esce con 1. Le
  istruzioni dicono al modello che la cartella è il progetto dell'utente,
  non uno spazio suo: modifica solo ciò che gli viene chiesto. Il banner
  mostra cartella e ramo git, letto da `.git/HEAD` senza lanciare git;
- **`ARES.md`**: se c'è nella cartella entra nelle istruzioni di ogni turno,
  troncato oltre 32 KB e dichiarato tale al modello. `ares init` ne scrive
  uno scheletro e non tocca un file esistente. `/cartella` sostituisce
  `/lavoro`, che resta alias: percorso, ramo, file modificati, `ARES.md` e
  gli stessi avvisi dell'avvio;
- **conversazioni legate alla cartella.** Ogni `ares` apre una conversazione
  nuova, nominata dalla cartella e dal momento (`progetto-20260907-103127`):
  profilo e memorie ci sono comunque perché sono per utente, obiettivo,
  piano e avanzamento partono vuoti. La cartella viaggia con la sessione nei
  `metadata` dell'agente, che Agno copia nella sessione nuova e lascia com'è
  in una ripresa. **`ares resume`** riapre l'ultima conversazione nata in
  questa cartella e dice data, scambi e prima domanda; **`ares resume
  --scegli`** le elenca numerate e ne fa scegliere una. `/sessioni` mostra
  quelle di questa cartella più quelle senza cartella, nate prima di questa
  versione; `/sessioni tutte` allarga alle altre cartelle e dice dove sono
  nate. All'avvio il modello riceve le ultime conversazioni della stessa
  cartella con l'id da passare a `read_past_session`, così "dove eravamo
  rimasti" funziona anche in una conversazione nuova. `--session nome` resta
  per chi vuole un nome fisso; `principale` è il ripiego senza spazio di
  lavoro e resta protetto dalla retention;
- **`ares -p "domanda"`** fa un turno ed esce: niente banner, lo stdin in
  pipe si aggiunge alla domanda, le conferme valgono no perché nessuno può
  rispondere. `git diff | ares -p "scrivi il messaggio di commit"`;
- **`ares` da qualunque cartella, con lo stato in `~/.ares`.** `setup.sh`
  crea un link in `~/.local/bin` al comando del venv, `setup.ps1` uno shim
  `ares.cmd` in `%USERPROFILE%\.local\bin`; `ARES_BIN_DIR` sceglie un'altra
  directory e, se non è nel PATH, il setup dice la riga da aggiungere. Non
  è un `uv tool install`, che risolverebbe le dipendenze da capo senza
  guardare `uv.lock`. Lo stato vive in `~/.ares/stato`, gli snapshot in
  `~/.ares/backup`, fuori dal clone, che si può spostare o rifare senza
  perdere niente; `ARES_HOME` sposta tutto, `ARES_TMP` e `ARES_BACKUP_DIR`
  una parte sola. **`ares migrate`** porta lì, una volta sola, ciò che una
  versione precedente teneva in `tmp/` nel clone e in `../ares-backup`:
  rinomina sotto lock esclusivo, idempotente, e non tocca una destinazione
  che contiene già dei dati. I setup lo chiamano, e la chat si ferma con
  codice 1 finché lo stato è ancora nel posto di prima, perché un archivio
  vuoto accanto a uno pieno li sdoppierebbe;
- **un solo comando `ares`** (`cli/app.py`, su Cyclopts): la chat come
  default, `backup`, `sessions`, `entities`, `preflight`, `inspect` e
  `migrate` come sottocomandi, `--help` in italiano disegnato con Rich,
  `--version`. I sottocomandi si registrano per nome di modulo, così
  `ares backup list` non importa Agno e parte in un decimo di secondo. Sei
  `argparse.ArgumentParser` scritti a mano spariscono; gli alias
  `ares-backup`, `ares-sessions`... e `python -m ares.*` restano e passano
  dalla stessa App, con firma e codici di uscita di prima;
- **tabelle Rich e `--json` nei comandi di manutenzione.** Elenchi di
  snapshot, sessioni, entità, modelli del preflight e file del quaderno in
  tabella, riepiloghi `chiave: valore`, colori del tema; gli errori vanno su
  stderr. In una pipe la console si allarga alla tabella invece di spezzare
  le celle a 80 colonne e le righe non vanno a capo, così un nome di
  snapshot si trova con `grep`. `--json` sui comandi che leggono soltanto:
  `backup list`, `backup verify`, `sessions status`, `entities audit`,
  `preflight`;
- **conferme uniformi** (`cli/conferma.py`): restore, prune, retention delle
  sessioni e fusione delle entità chiedono la frase esatta nello stesso
  modo. Sul terminale con l'editor della chat, in una pipe con `input()`,
  così la frase si passa da stdin; Ctrl-C e fine dell'input valgono no;
- **`/sessione`**, **`/metriche`** e **`/debug`** nella chat: la prima mostra
  la sessione corrente e con un nome ricostruisce l'agente su quella senza
  riavviare, le altre due accendono e spengono la riga del costo e i log di
  Agno. I comandi ricevono uno `StatoChat` che il ciclo della REPL rilegge a
  ogni giro; `/me` è ora ambiguo fra `/memorie` e `/metriche` e il menu
  mostra entrambi;
- `ares inspect` senza `--session` guarda l'ultima conversazione toccata;
- prove: `cartella di lavoro`, `conversazioni`, `stato della chat` e
  `conferme scritte` in `repl`; `chat cartella`, `chat sessioni`,
  `migrazione` e le varianti `--json` in `cli`; l'agente registra la cartella
  nei metadati in `smoke`. Le prove entrano nella propria cartella con
  `chdir` invece di una variabile d'ambiente e ne escono prima di
  cancellarla, perché su Windows la directory corrente non si cancella;
- anche il modello per l'estrazione delle memorie si sceglie dal `.env`:
  `ARES_LEARNING_MODEL` col tag `:cloud` manda a `ollama.com` il testo dei
  turni e le memorie già salvate, così chi vuole solo modelli cloud può
  averli, con il solo embedder in scheda. È una riga separata da
  `ARES_MAIN_MODEL`, perché affidare fuori ciò che Ares ricorda è una
  scelta diversa dal parlare con un modello remoto, e il valore distribuito
  resta locale. `assistant_runtime` continua a rifiutare un nome cloud per
  l'embedder; preflight e banner della chat leggono lo stesso avviso
  (`config.avviso_cloud`) e dicono quali ruoli escono dalla macchina;
- prove offline sull'estrazione in cloud: costruzione del modello, avviso
  del preflight con la sola estrazione e con entrambi i ruoli cloud, avviso
  della chat;
- **righe di attenzione nella conferma di un comando.** `run_command` è il
  solo strumento che esce dal recinto, e la conferma umana è il suo unico
  confine; il comando era già mostrato intero, ma riconoscere un `bash -lc`
  in coda a venti argomenti o un `/etc/hostname` dentro una riga citata era
  lasciato a chi legge. Sotto gli argomenti compaiono ora, quando ci sono,
  righe che nominano un fatto: passa da una shell, tocca percorsi fuori
  dalla directory, chiede privilegi di amministratore, usa la rete,
  cancella ricorsivamente. La riga passata a una shell viene aperta con
  `shlex` per guardarci dentro. Non è un filtro e non blocca niente: una
  lista nera si aggira con un alias, e la decisione resta a chi legge
  (`cli/render.py`, `avvertenze_comando`);
- **conferma di ciò che entra in memoria durevole.** Quando un turno ha
  scritto in profilo o memorie, dopo l'eco la CLI chiede "Tenere in memoria?
  [S/n]": Invio tiene, `n` riporta i due store a com'erano prima del turno.
  Agno non offre una conferma su questi store - `PROPOSE` vale per le sole
  intuizioni, `HITL` per nessuno - quindi è costruita a valle: `echo.py`
  leggeva già i due store prima del turno per calcolare l'eco, e ora
  conserva gli oggetti come li restituiscono gli store (`istantanea`) e li
  riscrive con `save`, o li cancella con `delete` se prima non c'erano
  (`ripristina`). Le API degli store inghiottono i propri errori, quindi
  l'esito non si presume: si rilegge, e un ripristino che non torna uguale
  viene detto come tale. Tutto o niente per turno, `CONFERMA_APPRENDIMENTI`
  in `config.py` lo spegne. Era la prima voce della `ROADMAP.md` e quella
  con il peso maggiore sul modello di sicurezza; `SECURITY.md`,
  `docs/architecture.md` e `docs/agno.md` descrivono ora questo;
- workflow **CodeQL** (`.github/workflows/codeql.yml`), query
  `security-extended` su push, PR e una volta a settimana. Ruff e mypy
  leggono ogni riga ma cercano altro; CodeQL segue il dato da dove entra a
  dove viene usato, che è la classe pertinente a un progetto che apre
  archivi, compone percorsi e lancia sottoprocessi. Volutamente fuori dai
  check obbligatori del ruleset su `main`: un suo risultato è un'ipotesi da
  leggere, e pretenderlo verde prima di ogni merge trasformerebbe un falso
  positivo in un blocco che si impara ad aggirare;
- `tests/agno_contract_test.py` sale da due controlli a quattro, e prende le
  due superfici di Agno che restavano scoperte. Il **retry del contesto di
  sessione** - la seconda classe che Ares sovrascrive - era provato dalla
  sola `learning_reliability_test.py`, che vuole Ollama e quindi in CI non
  gira mai: il ramo più delicato dell'apprendimento era verificato solo a
  mano. Ora un modello a copione lo attraversa offline nei tre casi che
  contano - riuscito al primo colpo, mai riuscito fino al tetto, fallito e
  poi recuperato - sul percorso sincrono e su quello asincrono, e afferma i
  tre fatti di Agno su cui il retry si regge: `extract_and_save` col suo
  nome, `context_updated` azzerato e acceso solo dopo un'esecuzione, e il
  gemello `aextract_and_save`. La divisione con la prova che usa Ollama
  diventa netta: qui "il retry funziona come scritto", lì "serve davvero, e
  quanto";
- e, nella stessa prova, **il limite dichiarato sulla memoria durevole
  diventa un'invariante sorvegliata**. `SECURITY.md` e
  `docs/architecture.md` dicono che profilo e memorie si scrivono fuori dal
  ciclo di conferma; la ragione, verificata sull'API installata, è che Agno
  3.0.5 non offre la modalità: `PROPOSE` vale per il solo store delle
  intuizioni, `UserProfileStore` e `UserMemoryStore` la rifiutano con un
  warning e `HITL` è "reserved for future use" su ogni store. La prova lo
  afferma, così il giorno in cui Agno cambiasse idea la documentazione
  diventerebbe falsa con una prova rossa invece che in silenzio. La
  correzione dei documenti che lasciavano intendere il contrario -
  `docs/agno.md` proponeva `PROPOSE` come candidato per gli apprendimenti da
  approvare, senza dire su quale store - e la voce di `ROADMAP.md` per la
  conferma da costruire in Ares partono da qui;
- eco di ciò che entra in memoria (`MOSTRA_APPRENDIMENTI`, acceso di
  default). Il modello scrive nella memoria durevole senza conferma, per due
  strade: gli strumenti che chiama (`save_learning`, `remember_about`,
  `update_user_memory`) e l'estrazione automatica dopo la risposta, che
  aggiorna profilo e memorie senza passare da nessuno strumento visibile.
  Ciò che entra viene reiniettato in ogni sessione futura, e finora l'unica
  traccia era l'esito di un tool - "Learning saved: titolo" - o niente. Ora
  gli strumenti di memoria mostrano gli argomenti che hanno ricevuto, e
  sotto la risposta compare la differenza fra profilo e memorie prima e
  dopo il turno, con il testo intero; tace quando il turno non ha scritto
  niente. `agent/echo.py` legge gli store con le loro API pubbliche prima e
  dopo, invece di agganciarsi alle funzioni private di Agno che scrivono.
  Il contesto di sessione resta fuori, perché cambia a ogni turno per
  costruzione. Prove in `smoke` (`eco apprendimenti`, `scritture in
  memoria`) e in `cli` (`chat turno`);
- prove del ciclo della REPL in questo processo (`chat turno`, `chat ciclo`,
  `chat avvio` in `tests/cli_test.py`). `chat_repl` prova la REPL da fuori e
  per restare offline può mandarle solo comandi: restava scoperta la metà
  che un utente attraversa a ogni frase. Con una `run_turn_cycle` finta al
  posto del modello sono ora provati i quattro esiti di `esegui_turno`
  (turno, pausa irrisolta, Ctrl-C, guasto), la riga vuota che non apre un
  turno, la riga delle metriche, gli avvisi d'avvio - cronologia degradata,
  modello cloud, promemoria di backup - e i due modi di uscire dal prompt.
  `ares/cli/chat.py` passa dal 61% al 100% di righe e rami, il totale
  dall'86% all'88%;
- due prove di contratto con Agno, offline, in `tests/agno_contract_test.py`
  (`contratto` nel runner). Ares dà per vere due cose del framework che
  nessuna prova gli chiedeva: che l'estrazione avvenga una volta per turno,
  sul run completo - Agno avvia `LearningMachine.process` prima della
  chiamata al modello, Ares la azzera e la rifà nel post-hook, che Agno
  esegue solo a run non in pausa - e che `run → pausa → continue_run` di
  `turn_core` combaci con la firma e il comportamento di Agno. La prima
  conta le estrazioni vere su un turno con pausa per conferma e verifica
  che l'unica riceva il run finale, esito dello strumento e risposta
  compresi, mentre il run in pausa non lo conteneva. La seconda attraversa
  il ciclo vero con `workspace_delete_file`: il file sparisce dopo la
  conferma e non prima, resta dopo un rifiuto con il motivo consegnato al
  modello, e in entrambi i casi il run riprende con lo stesso `run_id` e
  finisce. `smoke` provava il post-hook con un run costruito a mano e
  `chat turno` il ciclo con un `run_turn_cycle` finto: mancava Agno.

### Changed

- il backup non vieta più di sovrapporsi allo spazio di lavoro: con la
  cartella corrente come workspace, `ares backup create` dalla home deve
  funzionare;
- `config.comando_ares` suggerisce `ares` quando è sul PATH e il percorso nel
  venv altrimenti; il README usa `ares` ovunque, e i documenti dicono dove
  vivono stato e snapshot;
- dipendenze aggiornate con i PR di Dependabot: lancedb 0.38.0, openai 3.8.0,
  ruff 0.16.6. La 0.38 di LanceDB dichiara due rotture - l'esistenza di una
  tabella si legge dal manifest e pydantic deve essere v2 - e nessuna tocca
  Ares: la suite offline e le tre prove con Ollama, che scrivono e rileggono
  l'indice delle intuizioni, passano sul lock nuovo;
- la CI misura la copertura anche su Windows. Il runner Windows nasce da
  `setup.ps1`, che installa senza il gruppo dev, e la suite ci girava senza
  misura: i rami Windows di `backup` e `platform_files` risultavano
  scoperti anche quando quel runner li attraversava, e una regressione lì
  non l'avrebbe detta nessun numero. Un secondo `uv sync --locked` sullo
  stesso venv aggiunge `coverage` dopo lo script, che resta provato com'è;
  il passo delle prove torna uno solo per i due sistemi;
- i comandi di lettura della REPL passano sull'archivio seminato dello
  smoke (`comandi su archivio`): `/profilo`, `/memorie`, `/contesto`,
  `/entita` con e senza risultati, `/file`, `/lavoro` acceso e spento,
  `/aiuto`. `repl_test` provava che un comando si risolve, nessuna prova
  che, risolto, leggesse davvero l'archivio; e dopo un no alla conferma
  della memoria sono questi comandi il modo di controllare il ripristino.
  Con loro i rami di `stores.py` sui contenuti a parti e sulle date assenti
  e `get_memories_text` di `AresMemories`: `cli/commands.py` dal 76% al
  92%, `state/stores.py` dal 79% al 96%, `agent/schemas.py` al 100%;
- Agno passa da 3.0.1 a 3.0.5. La 3.0.2 con il vincolo `>=3.0.2,<3.1` in
  `pyproject.toml`, le patch successive dal solo `uv.lock`, che ne porta gli
  hash: ogni patch è provata sulle superfici che Ares usa e sul ciclo REPL
  completo prima di entrare nel lock. Badge, `docs/agno.md` e `ROADMAP.md`
  dicono la versione del lock, e i commenti che citavano un file rinominato
  o un comportamento verificato su una versione precedente sono allineati;
  la docstring di `ares-inspect` dice per intero cosa fa - crea la directory
  dello stato se manca e accende l'embedder per la ricerca fra le
  intuizioni - invece di promettere che non scrive nulla;
- `SECURITY.md` dichiara supportata la linea 0.4.x: diceva ancora 0.3.x, e un
  segnalatore ci leggeva che la versione corrente non è coperta;
- i vincoli delle dipendenze nel `pyproject.toml` diventano larghi e uniformi.
  Erano metà con `==` e metà senza, senza che la differenza fosse scritta da
  nessuna parte. Ares è un'applicazione: `uv.lock` è committato con le
  versioni esatte e i loro hash, e setup e CI installano con
  `uv sync --locked` - la riproducibilità stava già lì per intero, e il
  `==` nel pyproject ne era una seconda copia da tenere allineata a mano.
  Resta un solo vincolo stretto, `agno>=3.0.2,<3.1`, con accanto
  l'incompatibilità nota che lo motiva. Nessuna versione risolta si è mossa:
  il diff di `uv.lock` tocca i soli metadati. `CONTRIBUTING.md` dice cosa
  scrivere quando si aggiunge una dipendenza;
- le prove condividono `tests/_comune.py`: `prepara_ambiente`, che sceglie i
  percorsi usa-e-getta e rifiuta di farlo se `config` è già importato,
  `esigi`, `ok`, `esegui` e `fallimento`. Erano otto copie che divergevano un
  poco per volta; il modulo non importa niente di `ares`, ed è la sola
  garanzia che i percorsi vengano decisi prima che `config` li legga. Un
  fallimento mostra ora la riga da cui viene, e un'eccezione che non è
  un'asserzione porta il traceback: `KeyError: 'id'` senza la riga che l'ha
  sollevato era un fallimento da riprodurre a mano invece che da leggere;
- `tests/smoke_test.py` si divide: ciò che della REPL si prova senza
  costruire l'agente - conferme, rendering Rich e TTY, indicatore di
  attività, core del turno, log, cronologia, editor, comandi - sta in
  `tests/repl_test.py` (`repl` nel runner). Lo smoke era diventato il posto
  dove finiva ogni prova offline, duemilacinquecento righe in cui
  l'assemblaggio dell'agente e il comportamento di un widget stavano nello
  stesso elenco; la divisione segue ciò che serve per girare.
- **il modello conversazionale distribuito torna locale.** `MAIN_MODEL` era
  `MODELLO_CLOUD` in `config.py`: chi clonava un progetto che si annuncia
  local-first mandava le proprie conversazioni a `ollama.com` senza averlo
  scelto. Il default è ora `MODELLO_LOCALE`, e la scelta opposta non passa
  più da una modifica al file versionato - che tornerebbe a divergere a ogni
  `git pull` - ma da `ARES_MAIN_MODEL` nel `.env`, accanto alle variabili di
  percorso che seguivano già quella divisione: qui le decisioni versionate,
  lì ciò che cambia da una macchina all'altra. Il confine non si sposta:
  `assistant_runtime` continua a rifiutare un nome cloud per estrazione ed
  embedding, qualunque cosa dica l'ambiente. README, `SECURITY.md` e
  `.env.example` dicono ora qual è il valore di serie invece di lasciarlo
  dedurre;
- il messaggio d'errore della sonda LanceDB non si avvolge più due volte.
  `conta_tabelle_lancedb` rilanciava dal proprio gestore anche gli
  `ErroreBackup` che sollevava lei, e "LanceDB illeggibile in /percorso:
  risposta non valida dalla sonda LanceDB" diceva due volte la stessa cosa
  mettendo il dettaglio in fondo. I due errori interni nominano ora il
  percorso da sé e passano intatti;
- `CONTRIBUTING.md` chiede di annotare nel CHANGELOG quando le prove con
  Ollama sono state eseguite. Non girano in CI e non possono - i runner di
  GitHub non hanno una GPU - quindi tre prove su dieci esistono solo se
  qualcuno le lancia, e nessuno se ne accorge se smette. Il resto della
  catena è dimostrabile da fuori; questo pezzo no, e allora si dichiara.

### Fixed

- il tetto di tempo delle prove offline in `tests/run.py` sale da 180 a 360
  secondi. La prova `cli` lancia una REPL per sottoprocesso e ognuno importa
  Agno: sul runner Windows di GitHub sta fra 150 e 165 secondi, e un runner
  appena più lento la dichiarava bloccata, come è successo alla CI del
  bump di ruff. Il tetto resta per distinguere una prova bloccata da una
  lenta, e ora è il doppio della misura;
- i controlli di terminale non passano più da nessuna via che mostra testo
  scelto dal modello o letto dal workspace. Il filtro ANSI copriva solo lo
  stream della risposta: il pannello di conferma, il nome e l'anteprima
  dell'esito di uno strumento, l'errore di un run e le righe dell'eco
  arrivavano a Rich intatti, e Rich lascia passare `ESC` anche verso una
  pipe. Un `ESC [2K ESC [1G` in un argomento poteva cancellare la riga che
  chiedeva di confermare proprio quell'argomento. Ora `_testo` in
  `cli/ui.py`, la via di ogni testo letterale, toglie sequenze e caratteri
  di controllo con lo stesso parser dello stream; `tool_started` e
  `run_error`, che non passavano di lì, lo fanno esplicitamente. La prova
  `renderer Rich` passa un controllo in ognuna di queste vie;
- un restore interrotto viene detto. Su POSIX il restore è due rinomine, e
  un processo ucciso fra le due lascia accanto allo stato la copia
  `.tmp-precedente-<hex>` e nessuna `tmp/`: al riavvio Ares ricreava uno
  stato vuoto e rispondeva come al primo giorno, senza dirlo. Ora la chat
  all'avvio e `ares-backup list` elencano i residui - la copia precedente e
  una preparazione mai installata sono distinte - e nominano lo snapshot
  pre-restore con cui tornare indietro, o dicono che il residuo è l'unica
  copia. Nessun residuo viene rimosso da Ares. Nello stesso spirito
  `ares-sessions` dichiara lo stato parziale quando una cancellazione
  fallisce a metà: quante sessioni sono sparite e quali, lette
  dall'archivio a guasto avvenuto, quali restano, e lo snapshot
  pre-manutenzione da cui tornare. Prove `residui restore` in `backup`,
  `chat residui` e `sessioni parziale` in `cli`.

## [0.4.0] - 2026-09-02

### Added

- il modello conversazionale può essere un modello cloud di Ollama
  (`MODELLO_CLOUD`, predefinito `glm-5.3-flash:cloud`), inoltrato dal daemon
  locale dopo `ollama signin`: nessuna chiave API nell'ambiente e host
  invariato. Estrazione delle memorie ed embedding restano locali e
  `assistant_runtime` rifiuta un nome cloud per quei ruoli; preflight e
  banner della chat segnalano quando la conversazione esce dalla macchina;
- prove offline sul riconoscimento del tag cloud, sul rifiuto dei ruoli
  locali e sul rimedio suggerito dal preflight per un modello cloud mancante.

### Changed

- `MAIN_MODEL` e `LEARNING_MODEL` non coincidono più di default: la
  conversazione usa il modello cloud, l'estrazione il 9B locale
  (`MODELLO_LOCALE`, ora Qwen3.8-9B-Distill Q8_0);
- lo smoke test `chiamate locali` verifica il confine per nome oltre che per
  host, e pretende che `OLLAMA_API_KEY` non sia nell'ambiente;
- l'estrazione delle memorie usa un contesto proprio (`LEARNING_NUM_CTX`,
  32k) quando `LEARNING_MODEL` è un modello diverso da `MAIN_MODEL`: il 9B
  Q8_0 scende da 14 a 9,3 GB di VRAM. Con lo stesso modello nei due ruoli il
  contesto resta `NUM_CTX`, e lo smoke test `contesto esteso` lo verifica;
- README, architettura, nota Agno e SECURITY descrivono cosa esce dalla
  macchina con un modello cloud e cosa dichiara la privacy policy di Ollama;
- i moduli lasciano la radice ed entrano nel package `ares/`, diviso per
  responsabilità: `agent`, `cli`, `state`, `backup`, `entities`,
  `sessions`, `ops`. I comandi si lanciano con `python -m`: `-m ares` avvia
  la chat, `-m ares.backup`, `-m ares.entities` e `-m ares.sessions`
  sostituiscono i vecchi script, `-m ares.ops.preflight` e
  `-m ares.ops.inspect_learning` gli strumenti a modello spento. Nessun
  percorso dello stato cambia: `tmp/`, `.env`, backup e workspace restano
  dove erano;
- gli `__init__.py` dei sottopackage descrivono in poche righe cosa contiene
  ciascuno, e `docs/architecture.md` apre con la mappa del package.
- Ares è un progetto Python con `pyproject.toml` e `uv.lock`: sette file
  (`requirements*.in`, `requirements*.txt`, `ruff.toml`, `mypy.ini`,
  `.coveragerc`) diventano due, con gli stessi pin, gli stessi hash e gli
  stessi commenti. `setup.sh`, `setup.ps1` e la CI installano con
  `uv sync --locked`; Dependabot segue `uv.lock`;
- il venv contiene i comandi `ares`, `ares-backup`, `ares-entities`,
  `ares-sessions`, `ares-preflight` e `ares-inspect`, e Ares stesso in
  editable: le prove non toccano più `sys.path`. `python -m ares` resta
  disponibile;
- il banner della chat e il manifest degli snapshot riportano la versione del
  package (`ares.__version__`).
- dipendenze aggiornate con i PR di Dependabot rimasti aperti sui vecchi
  requirements: portalocker 4.3.0, openai 3.7.0, ruff 0.16.5, mypy 2.3.1,
  coverage 7.15.4. `TurnEventKind` diventa uno `StrEnum`, come chiede il
  nuovo ruff per Python 3.12.

## [0.3.1] - 2026-08-30

### Added

- lo smoke test attraversa consenso, rifiuto motivato, Ctrl-C/EOF e pause
  sconosciute del flusso di conferma degli strumenti sensibili.

### Changed

- su POSIX `setup.sh` rende `.env` privato a 0600 quando esiste; il commento
  sulla copertura non incorpora più un conteggio destinato a diventare
  obsoleto;
- la CI può essere avviata manualmente oltre a verificare pull request e
  commit pubblicati su `main`;
- setup Linux/Windows, CI e istruzioni di sviluppo usano
  `uv pip sync --require-hashes`, rendendo obbligatorio l'hash per ogni
  dipendenza oltre a verificare quelli già presenti;
- documentazione post-release allineata alla linea supportata 0.3.x, ad Agno
  3.0.1, al ResultStore nel diagramma architetturale e alla suite offline
  unificata; roadmap e misura di copertura riflettono lo stato verificato.

## [0.3.0] - 2026-08-30

### Added

- `docs/agno.md` separa le capacità Agno già usate da Ares da quelle
  disponibili ma non abilitate, e documenta benefici e limiti dell'upgrade;
- i risultati degli strumenti oltre 16.000 caratteri usano il `ResultStore`
  di Agno 3: l'indice resta nel database principale, il payload resta lossless
  nel FileSystem già coperto dai backup entro la quota del framework e il
  modello riceve strumenti paginati per leggerlo o cercarlo. Il prompt
  conserva al massimo le ultime dieci tool call storiche senza cancellarle
  dall'archivio;
- retention delle sessioni legata al loro ciclo di vita, senza TTL capaci di
  spezzare i riferimenti agli offload. `session_maintenance.py` offre status,
  prune per inattività e cancellazione esatta con anteprima, sessioni
  protette, lock esclusivo, snapshot e verifica della cascata su run, contesto
  appreso, indice e payload. Una prova offline attraversa un vero `Agent.run`,
  il limite Agno di 8 MB e il restore con `kairos.db` e `filesystem.db`;

- `turn_core.py`: il ciclo di un turno — eventi normalizzati e sequenza
  `run → conferma → continue_run` — non dipende più dal terminale, e un
  client diverso dalla CLI può riusarlo senza duplicarlo;
- indicatore di attività nella REPL dopo due secondi di attesa, per i turni in
  cui il modello non ha ancora emesso niente;
- prova dedicata ai permessi dello stato appreso nello smoke test, che copre
  anche la correzione di un archivio preesistente con permessi larghi;
- analisi statica con ruff e mypy, in `requirements-dev.in` separato e in un
  job di CI dedicato. Mypy gira senza `ignore_missing_imports`, perché
  l'intero albero delle dipendenze è tipizzato, e copre i moduli e non le
  prove; ruff copre tutto, con `PTH` escluso perché litigherebbe con la
  scelta motivata fra `os.rename` e `os.replace` in `backup.py`;
- `tests/run.py`, runner unico delle otto prove, e misura di copertura con
  `--copertura`. Il runner non importa le prove, le lancia una per processo:
  ognuna scrive `ARES_TMP` e le altre variabili prima di importare `config`,
  che le legge una volta sola all'import, quindi due prove nello stesso
  interprete condividerebbero l'archivio della prima. L'elenco delle prove
  vive ora in un posto solo e la CI legge quello;
- `tests/cli_test.py`: i comandi con cui si usa Ares erano l'unica parte mai
  eseguita da una prova. Copre il preflight contro un server Ollama finto nei
  tre esiti (pronto, modello mancante, server spento), l'ispezione degli
  archivi e la sua promessa di non scrivere, tutti i sottocomandi di
  `backup.py` con i loro annullamenti e codici di uscita distinti, e la REPL
  intera in un processo separato con stdin da una pipe. Niente modello:
  `preflight.py` passa dallo 0% al 100%, `inspect_learning.py` dallo 0%
  all'85%, la CLI di `backup.py` dall'1% e quella di `chat.py` dal 3%;
- la misura di copertura segue i processi figli
  (`tests/_copertura/sitecustomize.py`). Senza, `entity_maintenance.py`
  risultava al 68% pur avendo la propria CLI provata da sei sottoprocessi:
  un rapporto che sbaglia in difetto manda a scrivere prove dove ce ne sono
  già. La misura è per ramo e non per sola riga, perché una `if` eseguita in
  un verso solo è mezza provata: con l'aggancio e le prove nuove le sole
  prove offline coprono l'85%, e il modulo più scoperto è `chat.py`, che
  contiene il turno conversazionale, cioè ciò che senza Ollama non gira;
- promemoria di backup all'avvio della chat: se l'ultimo snapshot ha più di
  `BACKUP_PROMEMORIA_GIORNI` giorni — sette per default, zero spegne tutto —
  la REPL lo dice e mostra il comando, altrimenti tace. Il backup resta
  manuale di proposito: farlo partire da solo significherebbe una decina di
  secondi e il lock esclusivo dello stato mentre qualcuno aspetta un prompt,
  e un backup che parte da sé è anche un backup che può fallire da sé, in un
  momento in cui nessuno sta guardando. All'avvio e non all'uscita perché lì
  l'utente c'è ancora e può decidere. `promemoria_backup()` non crea la
  directory dei backup nemmeno quando manca del tutto, e non solleva: un
  avviso non deve poter impedire l'avvio.

### Changed

- Agno aggiornato da 2.9.0 a 3.0.1 con lock universale rigenerato. I run
  vivono nella tabella normalizzata `agno_runs`; lo smoke test usa l'API
  `upsert_run` e continua a verificare elenco e anteprima delle sessioni;
- `assistant.py` è ora una facciata di composizione: modelli, archivi e
  workspace vivono in `assistant_runtime.py`, store e workaround del ciclo di
  apprendimento in `assistant_learning.py`, istruzioni condizionali in
  `assistant_prompts.py`. Gli import pubblici precedenti restano compatibili;
- le istruzioni distinguono esplicitamente un'intuizione dal quaderno: una
  richiesta di conservarla usa `search_learnings` e `save_learning`, così
  resta ricercabile nelle sessioni future invece di finire in un file;
- la prova E2E non importa più lo smoke test, che preparava un proprio
  `ARES_TMP` all'import e faceva scrivere e rileggere due archivi differenti;

- formato, checksum e verifica degli snapshot vivono in
  `backup_integrity.py`, mentre la sonda LanceDB usata da snapshot e restore
  gira nel processo dedicato `backup_probe.py`; parser, conferme, output e
  dispatch della riga di comando sono in `backup_cli.py`, preparazione,
  installazione POSIX/Windows e rollback in `backup_restore.py`, mentre
  `backup_files.py` contiene permessi ricorsivi e rinomina protetta condivisi
  da creazione e restore. `backup.py` conserva API, helper testati ed entry
  point compatibili e non contiene più un sottocomando interno nascosto oltre
  alla propria CLI pubblica. Le prove mirate rifiutano manifest, checksum e
  risposte della sonda malformati e
  verificano il rollback sia dopo il guasto dell'installazione a copia sia
  dopo il guasto della seconda rinomina atomica;
- la REPL è divisa per responsabilità: `chat.py` conserva avvio e ciclo della
  sessione, `chat_commands.py` contiene tabella e dispatch dei comandi locali,
  `chat_render.py` presenta eventi, conferme e metriche. Gli import pubblici
  precedenti restano disponibili dalla façade `chat.py`;
- la manutenzione delle entità non vive più in un unico modulo da oltre mille
  righe: `entity_maintenance.py` conserva CLI e API compatibile,
  `entity_audit.py` contiene il rilevamento in sola lettura,
  `entity_merge.py` pianifica e applica le fusioni e `entity_models.py` ospita
  i contratti condivisi;
- il runner delle prove applica ora un timeout per singolo processo (tre
  minuti offline, quindici con Ollama), così un deadlock arriva al riepilogo
  come fallimento invece di bloccare terminale e CI; `setup.sh` verifica anche
  la coerenza delle dipendenze installate, come già faceva `setup.ps1`;
- `chat.py` traduce eventi neutri invece di consumare direttamente lo stream
  di Agno: rendering e ciclo del turno sono separati;
- il filtro dei controlli di terminale è un parser a stati che consuma i
  frammenti mentre arrivano, invece di rifiltrare a ogni frammento la risposta
  ricomposta. La garanzia non cambia — una sequenza spezzata a metà non passa
  né prima né adesso — cambia il fatto che `Live` non deve più ridisegnare
  tutto a ogni token;
- i messaggi INFO interni di Agno non compaiono più durante la REPL; warning
  ed errori restano sempre visibili e `--debug` riporta tutto;
- classi interne rinominate da `Kairos*` ad `Ares*`, residuo del nome che il
  progetto aveva prima del rilascio pubblico. Il file `kairos.db` resta e ora
  lo dichiara: quel nome è nell'insieme di file che `verifica_snapshot`
  pretende in ogni snapshot già creato, quindi cambiarlo è una migrazione del
  formato di backup e non una rinomina;
- `docs/architecture.md` elencava `learning.py`, che non esiste, e ometteva
  cinque moduli; la voce su `stores.py` descriveva store che quel modulo non
  legge;
- codice riformattato con ruff, riga a 120: il limite descrive lo stile già
  presente invece di riscriverlo. Nessun cambio di comportamento;
- `backup.py` riusava `snapshot` per una `Path` in un ramo e per una lista in
  un altro, `entity_maintenance.py` riusava `chiave` per il nome di una
  proprietà e per la chiave a due campi di un'entità: rami distinti, quindi
  nessun difetto a runtime, ma due nomi per quattro cose;
- il terzo parametro di `_continuazione` era annotato `bool`, mentre
  prompt_toolkit ci passa `wrap_count`, che è un intero; `esigi` nelle prove
  dichiarava `bool` e riceve da sempre anche tuple e liste;
- la CI esegue le prove con un passo solo invece di tre, e compila il codice
  per esclusione invece che per elenco: l'elenco scritto a mano si era già
  perso `turn_core.py`, aggiunto dopo;
- importare `config.py` non scrive più su disco. La directory dello stato la
  crea `prepara_archivio()`, chiamata da chi l'archivio lo apre davvero: i
  costruttori di `assistant.py`, e i comandi **dopo** la lettura degli
  argomenti, così `--help` non lascia niente indietro. `backup.py` non la
  chiama affatto — legge `tmp/` e sa dire che non c'è — e l'audit delle
  entità la chiama dopo aver verificato che l'archivio esista. Prima leggere
  una costante produceva un effetto: `preflight.py` importava `config` per
  tre nomi di modello e si lasciava dietro un archivio. Le prove continuano a
  scrivere `ARES_TMP` prima dell'import, che resta il modo giusto di
  spostare i percorsi, ma non è più una precauzione contro una scrittura;
- la docstring di `preflight.py` prometteva di usare «solo la libreria
  standard, quindi si può eseguire con qualsiasi interprete anche prima di
  aver installato le dipendenze». Era falsa — `config` importa dotenv, e
  `platform_files` portalocker — e non serviva a nessuno: `setup.sh` e
  `setup.ps1` chiamano il preflight dal venv, dopo aver installato. Ora dice
  cosa vale davvero.

### Security

- su POSIX lo stato appreso nasce privato: `tmp/` e la directory LanceDB a
  `0700`, i due database a `0600`. Nascevano invece con la umask del processo,
  cioè `0755` e `0644` su un'installazione tipica, mentre la cronologia
  accanto e gli snapshot erano privati da sempre: era protetta la copia e non
  l'originale. I database vengono creati vuoti e con i propri permessi prima
  che li apra SQLite, così non esiste una finestra fra creazione e correzione,
  e un archivio già scritto viene corretto alla costruzione successiva;
- i lock delle dipendenze portano gli hash SHA-256 di ogni artefatto, e
  `uv pip sync` rifiuta un pacchetto che non corrisponda. Le versioni sono
  invariate: il pin diceva già quale versione installare, l'hash aggiunge
  quale file, cioè la parte che una ripubblicazione su PyPI cambierebbe senza
  toccare il numero di versione. Vale per `setup.sh`, `setup.ps1`, la CI e le
  PR settimanali di Dependabot.

## [0.2.0] - 2026-08-26

### Added

- identità visiva del repository con marchio e social preview;
- interfaccia Rich con streaming Markdown, pannelli e output sicuro per pipe;
- editor Prompt Toolkit con menu dei comandi, suggerimenti, input multilinea
  e cronologia privata coordinata fra sessioni concorrenti;
- backend cooperativo dei lock condivisi/esclusivi per POSIX e Windows, con
  lock delle dipendenze universale e matrice CI sui due sistemi;
- setup PowerShell idempotente e documentazione d'installazione per Windows;
- pubblicazione e restore transazionali dei backup sui filesystem Windows.

### Security

- sequenze di controllo del terminale filtrate dalle risposte del modello;
- cronologia della chat atomica, limitata e creata con permessi `0600`.

## [0.1.0] - 2026-08-25

### Added

- assistente locale basato su Agno e Ollama;
- cinque store di memoria persistente e quaderno privato;
- strumenti per cronologia, sessioni, entità e conoscenza riutilizzabile;
- workspace controllato con conferme per le operazioni sensibili;
- snapshot locali verificati con restore e retention;
- audit e fusione reversibile delle entità duplicate;
- suite isolata e prove E2E contro il modello locale.
- documentazione pubblica, policy di sicurezza e guida ai contributi;
- CI senza GPU e aggiornamenti automatici delle dipendenze.

### Security

- telemetria Agno disabilitata;
- namespace isolati e lock cooperativo dello stato;
- dati persistenti, snapshot e configurazione locale esclusi dal repository.

[Unreleased]: https://github.com/KairosIta/Ares/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/KairosIta/Ares/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/KairosIta/Ares/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/KairosIta/Ares/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/KairosIta/Ares/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/KairosIta/Ares/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/KairosIta/Ares/releases/tag/v0.1.0
