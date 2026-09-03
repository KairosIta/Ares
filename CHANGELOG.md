# Changelog

Le modifiche rilevanti di Ares sono raccolte in questo file. Il formato segue
[Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/) e il progetto
adotta il versionamento semantico a partire dal primo rilascio pubblico.

## [Unreleased]

### Changed

- Agno passa da 3.0.1 a 3.0.2; il pin resta esatto e `uv.lock` ne porta gli
  hash. La suite offline e' verde sulla nuova versione;
- `SECURITY.md` dichiara supportata la linea 0.4.x: diceva ancora 0.3.x, e un
  segnalatore ci leggeva che la versione corrente non e' coperta;
- i vincoli delle dipendenze nel `pyproject.toml` diventano larghi e uniformi.
  Erano meta' con `==` e meta' senza, senza che la differenza fosse scritta da
  nessuna parte. Ares e' un'applicazione: `uv.lock` e' committato con le
  versioni esatte e i loro hash, e setup e CI installano con
  `uv sync --locked` - la riproducibilita' stava gia' li' per intero, e il
  `==` nel pyproject ne era una seconda copia da tenere allineata a mano.
  Resta un solo vincolo stretto, `agno>=3.0.2,<3.1`, con accanto
  l'incompatibilita' nota che lo motiva. Nessuna versione risolta si e' mossa:
  il diff di `uv.lock` tocca i soli metadati. `CONTRIBUTING.md` dice cosa
  scrivere quando si aggiunge una dipendenza.

### Added

- eco di cio' che entra in memoria (`MOSTRA_APPRENDIMENTI`, acceso di
  default). Il modello scrive nella memoria durevole senza conferma, per due
  strade: gli strumenti che chiama (`save_learning`, `remember_about`,
  `update_user_memory`) e l'estrazione automatica dopo la risposta, che
  aggiorna profilo e memorie senza passare da nessuno strumento visibile.
  Cio' che entra viene reiniettato in ogni sessione futura, e finora l'unica
  traccia era l'esito di un tool - "Learning saved: titolo" - o niente. Ora
  gli strumenti di memoria mostrano gli argomenti che hanno ricevuto, e
  sotto la risposta compare la differenza fra profilo e memorie prima e
  dopo il turno, con il testo intero; tace quando il turno non ha scritto
  niente. `agent/echo.py` legge gli store con le loro API pubbliche prima e
  dopo, invece di agganciarsi alle funzioni private di Agno che scrivono.
  Il contesto di sessione resta fuori, perche' cambia a ogni turno per
  costruzione. Prove in `smoke` (`eco apprendimenti`, `scritture in
  memoria`) e in `cli` (`chat turno`);
- prove del ciclo della REPL in questo processo (`chat turno`, `chat ciclo`,
  `chat avvio` in `tests/cli_test.py`). `chat_repl` prova la REPL da fuori e
  per restare offline puo' mandarle solo comandi: restava scoperta la meta'
  che un utente attraversa a ogni frase. Con una `run_turn_cycle` finta al
  posto del modello sono ora provati i quattro esiti di `esegui_turno`
  (turno, pausa irrisolta, Ctrl-C, guasto), la riga vuota che non apre un
  turno, la riga delle metriche, gli avvisi d'avvio - cronologia degradata,
  modello cloud, promemoria di backup - e i due modi di uscire dal prompt.
  `ares/cli/chat.py` passa dal 61% al 100% di righe e rami, il totale
  dall'86% all'88%;
- due prove di contratto con Agno, offline, in `tests/agno_contract_test.py`
  (`contratto` nel runner). Ares da' per vere due cose del framework che
  nessuna prova gli chiedeva: che l'estrazione avvenga una volta per turno,
  sul run completo - Agno avvia `LearningMachine.process` prima della
  chiamata al modello, Ares la azzera e la rifa' nel post-hook, che Agno
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

### Fixed

- i controlli di terminale non passano piu' da nessuna via che mostra testo
  scelto dal modello o letto dal workspace. Il filtro ANSI copriva solo lo
  stream della risposta: il pannello di conferma, il nome e l'anteprima
  dell'esito di uno strumento, l'errore di un run e le righe dell'eco
  arrivavano a Rich intatti, e Rich lascia passare `ESC` anche verso una
  pipe. Un `ESC [2K ESC [1G` in un argomento poteva cancellare la riga che
  chiedeva di confermare proprio quell'argomento. Ora `_testo` in
  `cli/ui.py`, la via di ogni testo letterale, toglie sequenze e caratteri
  di controllo con lo stesso parser dello stream; `tool_started` e
  `run_error`, che non passavano di li', lo fanno esplicitamente. La prova
  `renderer Rich` passa un controllo in ognuna di queste vie.

## [0.4.0] - 2026-09-02

### Added

- il modello conversazionale puo' essere un modello cloud di Ollama
  (`MODELLO_CLOUD`, predefinito `glm-5.3-flash:cloud`), inoltrato dal daemon
  locale dopo `ollama signin`: nessuna chiave API nell'ambiente e host
  invariato. Estrazione delle memorie ed embedding restano locali e
  `assistant_runtime` rifiuta un nome cloud per quei ruoli; preflight e
  banner della chat segnalano quando la conversazione esce dalla macchina;
- prove offline sul riconoscimento del tag cloud, sul rifiuto dei ruoli
  locali e sul rimedio suggerito dal preflight per un modello cloud mancante.

### Changed

- `MAIN_MODEL` e `LEARNING_MODEL` non coincidono piu' di default: la
  conversazione usa il modello cloud, l'estrazione il 9B locale
  (`MODELLO_LOCALE`, ora Qwen3.8-9B-Distill Q8_0);
- lo smoke test `chiamate locali` verifica il confine per nome oltre che per
  host, e pretende che `OLLAMA_API_KEY` non sia nell'ambiente;
- l'estrazione delle memorie usa un contesto proprio (`LEARNING_NUM_CTX`,
  32k) quando `LEARNING_MODEL` e' un modello diverso da `MAIN_MODEL`: il 9B
  Q8_0 scende da 14 a 9,3 GB di VRAM. Con lo stesso modello nei due ruoli il
  contesto resta `NUM_CTX`, e lo smoke test `contesto esteso` lo verifica;
- README, architettura, nota Agno e SECURITY descrivono cosa esce dalla
  macchina con un modello cloud e cosa dichiara la privacy policy di Ollama;
- i moduli lasciano la radice ed entrano nel package `ares/`, diviso per
  responsabilita': `agent`, `cli`, `state`, `backup`, `entities`,
  `sessions`, `ops`. I comandi si lanciano con `python -m`: `-m ares` avvia
  la chat, `-m ares.backup`, `-m ares.entities` e `-m ares.sessions`
  sostituiscono i vecchi script, `-m ares.ops.preflight` e
  `-m ares.ops.inspect_learning` gli strumenti a modello spento. Nessun
  percorso dello stato cambia: `tmp/`, `.env`, backup e workspace restano
  dove erano;
- gli `__init__.py` dei sottopackage descrivono in poche righe cosa contiene
  ciascuno, e `docs/architecture.md` apre con la mappa del package.
- Ares e' un progetto Python con `pyproject.toml` e `uv.lock`: sette file
  (`requirements*.in`, `requirements*.txt`, `ruff.toml`, `mypy.ini`,
  `.coveragerc`) diventano due, con gli stessi pin, gli stessi hash e gli
  stessi commenti. `setup.sh`, `setup.ps1` e la CI installano con
  `uv sync --locked`; Dependabot segue `uv.lock`;
- il venv contiene i comandi `ares`, `ares-backup`, `ares-entities`,
  `ares-sessions`, `ares-preflight` e `ares-inspect`, e Ares stesso in
  editable: le prove non toccano piu' `sys.path`. `python -m ares` resta
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
  sulla copertura non incorpora piu' un conteggio destinato a diventare
  obsoleto;
- la CI puo' essere avviata manualmente oltre a verificare pull request e
  commit pubblicati su `main`;
- setup Linux/Windows, CI e istruzioni di sviluppo usano
  `uv pip sync --require-hashes`, rendendo obbligatorio l'hash per ogni
  dipendenza oltre a verificare quelli gia' presenti;
- documentazione post-release allineata alla linea supportata 0.3.x, ad Agno
  3.0.1, al ResultStore nel diagramma architetturale e alla suite offline
  unificata; roadmap e misura di copertura riflettono lo stato verificato.

## [0.3.0] - 2026-08-30

### Added

- `docs/agno.md` separa le capacita' Agno gia' usate da Ares da quelle
  disponibili ma non abilitate, e documenta benefici e limiti dell'upgrade;
- i risultati degli strumenti oltre 16.000 caratteri usano il `ResultStore`
  di Agno 3: l'indice resta nel database principale, il payload resta lossless
  nel FileSystem gia' coperto dai backup entro la quota del framework e il
  modello riceve strumenti paginati per leggerlo o cercarlo. Il prompt
  conserva al massimo le ultime dieci tool call storiche senza cancellarle
  dall'archivio;
- retention delle sessioni legata al loro ciclo di vita, senza TTL capaci di
  spezzare i riferimenti agli offload. `session_maintenance.py` offre status,
  prune per inattivita' e cancellazione esatta con anteprima, sessioni
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
- `assistant.py` e' ora una facciata di composizione: modelli, archivi e
  workspace vivono in `assistant_runtime.py`, store e workaround del ciclo di
  apprendimento in `assistant_learning.py`, istruzioni condizionali in
  `assistant_prompts.py`. Gli import pubblici precedenti restano compatibili;
- le istruzioni distinguono esplicitamente un'intuizione dal quaderno: una
  richiesta di conservarla usa `search_learnings` e `save_learning`, cosi'
  resta ricercabile nelle sessioni future invece di finire in un file;
- la prova E2E non importa piu' lo smoke test, che preparava un proprio
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

[Unreleased]: https://github.com/KairosIta/Ares/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/KairosIta/Ares/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/KairosIta/Ares/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/KairosIta/Ares/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/KairosIta/Ares/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/KairosIta/Ares/releases/tag/v0.1.0
