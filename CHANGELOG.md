# Changelog

Le modifiche rilevanti di Ares sono raccolte in questo file. Il formato segue
[Keep a Changelog](https://keepachangelog.com/it-IT/1.1.0/) e il progetto
adotta il versionamento semantico a partire dal primo rilascio pubblico.

## [Unreleased]

## [0.8.2] - 2026-09-25

Le due patch di Agno successive alla 3.0.9 entrano dal lock, come la politica
del progetto prevede. Non portano nulla che Ares usi e nulla che lo rompa, e
le superfici che Ares attraversa — `agno.learn`, il filesystem privato, il
`ResultStore`, gli strumenti del workspace, le sessioni — sono ferme fra le
due versioni. Release di sola dipendenza: nessuna riga del codice di Ares
cambia, nessun formato su disco, nessuna migrazione. Serve comunque una
release, e non un commit su `main`, perché la versione installata si legge
dalla pagina pubblica: chi installa da un tag la riceve solo con un tag nuovo.

Verifica locale del 2026-09-25: undici prove offline verdi con copertura al
91% (4.436 istruzioni, 306 non eseguite, 1.364 rami, 179 parziali) su
Linux/Python 3.12.3; `ruff check`, `ruff format --check` (84 file) e `mypy`
(56 file) puliti. Con Ollama, `affidabilita`, `intuizioni` e `e2e` verdi con
`deepseek-v4.1-flash:cloud` per conversazione ed estrazione ed embedder
locale; nel turno di `e2e` l'apprendimento ha speso 5.256 token di ingresso e
406 di uscita, e l'archivio vero è rimasto invariato (20 file).
`tests/run.py --tutte`: 14 prove in 103,0 s.

### Changed

- **Agno 3.0.11.** Due patch dopo la 3.0.9. La 3.0.10 aggiunge un vector db
  Elasticsearch, `AzureOpenAIResponses`, un transform Markdown per le pagine
  e il routing per hostname di MCP; la 3.0.11 sposta il reranker dal vectordb
  a `Knowledge` (con MMR e recency), aggiunge lo spostamento delle sorgenti
  pagina, il provider Y-API e il campo `cancellation_stage` sui run. Nulla di
  tutto questo è adottato. Le due modifiche incompatibili non ci toccano:
  `CodingTools.run_shell` diventa opt-in e la sua modalità ristretta non passa
  più da una shell — Ares usa `agno.tools.workspace.Workspace`, un toolkit
  diverso, i cui strumenti non cambiano affatto — e `PublicSurface(mcp=True)`
  accetta solo localhost, mentre Ares non espone AgentOS né MCP. Fra le
  correzioni una arriva nel percorso di Ares: `clean_text` non appiattisce più
  righe e tabulazioni prima del chunking, quindi le intuizioni salvate da ora
  conservano la loro forma, mentre quelle già indicizzate restano com'erano.
  Restano fuori percorso la conservazione dei risultati falsy degli strumenti,
  l'estrazione JSON dalle risposte e il fix degli hook sincroni in catena
  asincrona; `cancellation_stage` è additivo. `agno.learn` non cambia in
  nessuno dei suoi quindici file, e con lui `agno.fs`, `agno.offload`,
  `agno.session`, `agno.metrics` e gli strumenti del workspace; in `SqliteDb`,
  nel provider Ollama e in LanceDB cambiano solo docstring. La riscrittura di
  `Knowledge.search`, l'unico punto sostanziale nel percorso di Ares, è
  coperta dalla prova `intuizioni`. Cambia solo `agno` nel lock: nessuna
  dipendenza transitiva, perché i due extra aggiunti a monte
  (`elasticsearch`, e `fonttools` per il PDF) non si installano.

- **Le dichiarazioni della versione di Agno sono allineate.** Il badge del
  `README.md`, la `ROADMAP.md`, `SECURITY.md`, il commento di
  `agent/learning.py`, `docs/agno.md`, `docs/architecture.md`,
  `docs/core-contract.md` e `docs/project-scopes.md` dichiaravano la 3.0.9. La
  prova di contratto confronta le dichiarazioni con l'installato, quindi
  ognuna nomina ora la 3.0.11; `docs/agno.md` porta anche la data della nuova
  verifica. Le tre misure offline di `docs/project-scopes.md` §2.3 sono state
  rieseguite sulla 3.0.11 e danno gli stessi esiti, compresa la sovrascrittura
  silenziosa del profilo fra due progetti.

## [0.8.1] - 2026-09-25

Un giro di ricognizione ha corretto ciò che la suite non copriva: numeri
rimasti indietro nei documenti pubblici, confini che una pipe poteva
scavalcare, una migrazione non atomica fra filesystem e quattro difetti minori
di conferma, backup e restore. Release di correzione: nessun formato su disco
cambia e nessuna funzione nasce. Cambiano però due comportamenti della CLI, ed
è bene dirlo: senza un terminale le modalità silenziose sono rifiutate e gli
store di apprendimento non vengono più scritti, come già valeva con `-p`.

Verifica locale del 2026-09-25: undici prove offline verdi con copertura al
91% su Linux/Python 3.12.3; `ruff check`, `ruff format --check` e `mypy` (56
file) puliti. Con Ollama, `e2e`, `affidabilita` e `intuizioni` verdi su
`deepseek-v4.1-flash:cloud` (47,6 s), embedder locale; nel turno di `e2e`
l'apprendimento ha speso 5.280 token di ingresso e 482 di uscita.
`tests/run.py --tutte`: 14 prove in 103,3 s.

### Added

- `tests/scoping_test.py` (`ambiti`), l'undicesima prova offline: tiene ferme
  le due premesse dello studio sugli ambiti da cui dipendono la provenienza
  delle memorie e la composizione a mano del blocco delle intuizioni. Con il
  modello a copione verifica che una chiave in più in una voce di memoria si
  scriva con le API pubbliche, sopravviva a un `update_memory`, si riallinei e
  non arrivi al modello; con un embedder a vettori fissi verifica che il filtro
  per namespace sia applicato dopo il limite — zero risultati in ambito con
  limite 5 e 30 su trenta documenti fuori ambito — mentre quello dell'owner è
  una clausola del motore. Se Agno cambia una delle due premesse, il
  fallimento arriva qui e non in produzione.
- `docs/project-scopes.md`, lo studio sui primi due punti della roadmap:
  identità stabile e memoria per progetto, ambiti applicati dal codice. Contiene
  le evidenze nel codice e in Agno 3.0.9, le misure offline riproducibili, le
  proposte con i limiti accertati e le prove di accettazione da implementare.
  Non introduce codice. `ROADMAP.md` lo collega ai due approfondimenti. Le due
  verifiche preliminari chieste dal §8 sono al §3.4 e sono misure: una
  provenienza di progetto nelle memorie si scrive, sopravvive a una
  riscrittura e va riallineata quando il turno tocca la voce; il filtro per
  namespace delle intuizioni è applicato dopo il limite, e con due ambiti può
  restituire zero intuizioni del progetto. Le decisioni che ne dipendevano
  sono chiuse al §9.
- `tests/rilascio_test.py`, la decima prova offline: confronta la versione di
  `pyproject.toml` con il lock, con la voce più recente di questo file, con la
  riga supportata di `SECURITY.md` e con i collegamenti di confronto in coda.
  È la stessa idea della prova sulla versione di Agno, applicata al repository.
- `agent/learning.py` arriva al 100%: `smoke` attraversa le due guardie del
  post-hook (run senza messaggi, post-hook senza agente) e le istruzioni fuori
  da `AGENTIC`, `contratto` i due percorsi asincroni — l'`aprocess` anticipata
  di Ares, che Ares non usa ma che un Agno asincrono chiamerebbe, e il retry
  del contesto nei tre casi, primo colpo e tetto compresi. Il corpo asincrono
  del retry era provato a metà: solo il recupero al secondo tentativo.

### Fixed

- **Il banner e `/cartella` non nominano più un `ARES.md` che punta fuori.**
  La regola di contenimento valeva solo per la lettura: banner e `/cartella`
  usavano `is_file()`, che segue i link, e dicevano «ARES.md, letto all'avvio»
  di un link che il prompt ignorava. Ora entrambi passano da
  `percorso_istruzioni` (`agent/prompts.py`), la stessa funzione che decide
  cosa legge `istruzioni_dalla_cartella`.
- **La conferma di `write_file` dice quando il file esiste ma non si legge.**
  Un file binario o in un'altra codifica veniva trattato come assente, e la
  conferma mostrava soltanto il contenuto nuovo: proprio la cosa che spariva
  restava nascosta. Ora lo dice, e mostra comunque il testo che lo
  sostituirà.
- **Il rollback del restore non maschera più l'errore vero.** Se la rinomina
  di ripristino falliva a sua volta, la sua eccezione sostituiva quella del
  restore e non nominava `.stato-precedente-*`, cioè l'unica copia dello
  stato. Ora il residuo è nominato e la causa originale resta incatenata.
- **Uno snapshot senza manifest non resta invisibile per sempre.** La
  pubblicazione di riserva lo lascia se un processo viene ucciso fra la copia
  e la scrittura del manifest; `backup list` ora lo nomina (in `--json` sotto
  `incompleti`), mentre resta fuori dal catalogo e da `prune`.
- **`_copia_sqlite` chiude la sorgente anche se la destinazione non si apre.**
  La prima connessione stava fuori dal `try`: un guasto sulla seconda la
  lasciava aperta fino alla raccolta dei rifiuti.
- **Un avvio rifiutato non lascia `~/.ares` leggibile a tutti.** Il lock
  dello stato si prende prima delle guardie e crea la casa se manca, ma il
  `chmod 0700` della politica sta in `config.prepara_archivio`, che su un
  rifiuto non arriva: `ares -p --modo auto` lasciava una `~/.ares` a 0755,
  contro la promessa del commento in `_guardie_di_avvio`. Ora `lock_file`
  rende privato il genitore quando lo crea, e non tocca un genitore che
  esiste già - quello di un lock può essere una cartella di lavoro, come
  nel lock vecchio di `migrate`.
- **Il lock vecchio della migrazione si toglie mentre è ancora tenuto.**
  Prima l'`unlink` arrivava dopo il rilascio: fra il `close` e l'`unlink` un
  processo della versione precedente poteva prendere il lock su quel file, e
  l'`unlink` lo staccava dall'inode - il processo dopo ne creava uno nuovo, e
  i due si credevano soli. Ora si toglie dentro il lock; su Windows, dove un
  file aperto non si cancella, resta il ripiego dopo la chiusura.
  `tests/cli_test.py` osserva l'istante del rilascio (su POSIX).
- **`ares migrate` non lascia mai uno stato a metà.** Fra filesystem diversi
  `shutil.move` degrada a copia più cancellazione: un guasto a metà lasciava
  in `~/.ares` uno stato incompleto che la chat apriva senza dirlo, con la
  copia buona ancora nel vecchio posto. Ora `_sposta` copia in una sorella
  temporanea del nuovo e la rinomina: il nuovo c'è tutto o non c'è per
  niente, e nel secondo caso `avviso` ferma la chat. `tests/cli_test.py`
  simula l'`EXDEV` e un guasto a metà copia.
- **Un id utente oltre 128 caratteri viene rifiutato prima di Agno.** Agno
  taglia i segmenti del namespace - l'id in `user/<id>` è uno di essi - a
  `MAX_SEGMENT_CHARS` e solleva `InvalidPathError` oltre. `Utente` non lo
  controllava: `ares --user <id lungo>` moriva con un traceback che nessun
  confine catturava, invece del rifiuto leggibile che la porta promette. Ora
  il tetto è `LUNGHEZZA_MASSIMA` in `state/identita.py`, e
  `agno_contract_test.py` lo confronta con quello installato.
- **Un `ARES.md` che è un link fuori dalla cartella vale come assente.** Il
  file delle regole si leggeva direttamente, quindi un `ARES.md` che puntava a
  `~/.ssh/id_ed25519` - o a qualunque file fuori dal workspace - entrava nel
  system message a ogni avvio, e con un modello cloud usciva dalla macchina.
  Ora il percorso si risolve e si pretende il contenimento, come fanno gli
  strumenti di Agno; un link che resta dentro la cartella si legge ancora.
  `tests/repl_test.py` tiene fermo il caso.
- **Una sessione dal nome fisso appartiene a chi l'ha creata.** `--session
  <nome>` e `/sessione <nome>` scavalcano gli elenchi per cartella, già
  filtrati per utente: un secondo utente poteva aprire la sessione del primo,
  e i suoi run - che Agno carica per solo `session_id` - entravano nella
  cronologia del primo. Ora l'apertura di una sessione di un altro utente
  viene rifiutata prima di costruire l'agente, in chat e in `/sessione`; un
  nome mai visto resta una conversazione nuova. Il controllo sta in
  `sessione_di_altri` (`state/stores.py`) e legge solo l'intestazione della
  riga, non la conversazione.
- Le pagine dicevano numeri che il codice non confermava: `CONTRIBUTING.md`
  contava tredici prove invece di quattordici, `docs/testing.md` due job di
  CI invece di tre (`Cosa cambia` compreso), `SECURITY.md` un `Audit` a ogni
  push quando ha un filtro sui percorsi, e `docs/project-scopes.md` con
  `agent/prompts.py` «le ultime venti sessioni» dove il tetto è
  `PAST_SESSIONS_LIMIT`. Allineati anche il nome del residuo di un restore
  (`.stato-precedente-*`, non `.tmp-precedente-*`, in `docs/architecture.md`
  e in `backup/snapshots.py`), la firma di `build_assistant` in
  `docs/core-contract.md` (mancava `politica`) e il docstring di
  `ares/ops/inspect_learning.py` (lo stato non è più in `tmp/`).
- `docs/testing.md` diceva che «`--tutte` alza il numero» e `tests/run.py`
  ripeteva che metà di ciò che resta scoperto sta nei percorsi che solo le
  prove con Ollama attraversano. Misurato il 22 settembre 2026: i due rapporti
  sono identici, riga per riga e ramo per ramo — 4.355 istruzioni, 304
  scoperte, 1.340 rami, 91%. Le prove con Ollama verificano il comportamento
  del modello, non aggiungono righe coperte.
- I numeri di copertura in `docs/testing.md` erano quelli del 13 settembre.
  Ora sono quelli del 22, con la data accanto.
- In `docs/memory-quality.md` il totale della colonna «conversazione» (2.474)
  non comprendeva il messaggio di sistema del contesto, che porta la
  conversazione dentro le proprie istruzioni: le chiamate che rimandano il
  turno ne portano 7.017, e quella colonna si sovrappone a quella delle
  istruzioni. La prova offline stampa il numero giusto, la pagina no.
- `SECURITY.md` dichiarava supportata la linea 0.7.x dopo il rilascio della
  0.8.0; i collegamenti in coda a questo file erano ancora fermi alla 0.7.1, e
  la voce `[0.8.0]` mancava. La CI era verde lo stesso: quei file non li legge
  nessuno. Ora li legge `rilascio`, che li confronta con `pyproject.toml`.

### Changed

- **Senza terminale, e senza `-p`, l'apprendimento è spento come in `-p`.**
  `interattivo` era `prompt is None`: con stdin da una pipe ma senza `-p`
  l'agente nasceva come se qualcuno leggesse, e scriveva memorie mentre le
  conferme, per la correzione precedente, valevano no. Ora `interattivo` è
  `prompt is None and presidiato`, ed è uno solo: `StatoChat` lo porta con
  sé, così anche `/sessione` e `/modo`, che ricostruiscono l'agente, non lo
  riaccendono.
- **Senza terminale le conferme non si leggono dalla pipe.** Il rifiuto
  delle modalità silenziose era legato a `-p`: con stdin da una pipe ma senza
  `-p`, la stessa pipe che portava l'istruzione rispondeva anche ad
  `Autorizzi?`. Ora «non presidiato» si decide da
  `stdin` non-terminale, come `-p` ma senza il flag; i turni restano
  leggibili dal flusso, le domande no. `tests/cli_test.py` (`chat non presidiato`) tiene ferme le
  guardie e il cablaggio di `_apri_input`.
- `CONTRIBUTING.md` spiega come si rilascia — versione, voce del `CHANGELOG`,
  riga di `SECURITY.md`, merge con `--merge`, tag annotato e nota della
  release — perché i due punti che erano stati saltati sono proprio quelli
  che nessuna verifica automatica copriva.
- L'apprendimento non paga più la chiamata di conferma su profilo e memorie:
  `agent/learning.py` costruisce i due store da `AresUserProfileStore` e
  `AresUserMemoryStore`, che impostano `stop_after_tool_call` sulla loro tool
  call come Agno fa già per il contesto di sessione. Un turno passa da cinque
  a tre chiamate e da 33.649 a 19.970 caratteri spediti (-41%); con
  `deepseek-v4.1-flash:cloud`, sullo stesso turno che scrive in entrambi gli
  store, da 9.533 a 5.388 token di ingresso. La scrittura non cambia:
  `learning_cost_test.py` legge profilo e memorie dopo il turno e verifica che
  contengano ciò che il modello ha passato.
- La suite di prove non ripete più ciò che può stare in un posto solo: i
  `main()` fatti di controlli indipendenti chiudono con `esegui` e `chiudi`
  invece di ricostruire a mano l'epilogo del runner, e i doppi condivisi — il
  modello a copione e la tool call — stanno in `tests/_doppi.py`. Misurati con
  la stessa regola che li aveva contati, i blocchi di sei righe ripetuti fra
  file diversi scendono da 29 a 14; `session_retention_test.py` passa da una
  sequenza dentro il `main()` a una prova sola, con un nome nel rapporto.
- Il controllo dei tipi copre anche il cablaggio delle prove: `mypy` nomina
  `tests/run.py`, `tests/_comune.py` e `tests/_doppi.py` accanto ai moduli, e
  sono 56 file invece di 53. Sono i file che nessuna prova esegue — il runner
  e ciò che le prove importano — quindi un errore lì non lo vedrebbe nessuno;
  le singole prove restano fuori per la ragione scritta in `pyproject.toml`.
- `_apri_chat` non è più la funzione più complessa del progetto: era 198
  righe con complessità 38, ora è 87 righe con 9. I quattro pezzi erano già
  distinti dentro di lei — i rifiuti che precedono ogni effetto (`-p` con una
  modalità che scrive, `-p` con `--scegli`, lo stato ancora nel posto di
  prima), la riga interattiva, banner e avvisi di apertura, il ciclo dei
  turni — e ora sono quattro funzioni con un nome. Nessun comportamento
  cambia: le prove non sono state toccate e i percorsi che attraversavano
  quelle righe sono gli stessi.
- Anche `pianifica_fusione` scende sotto la soglia: era 174 righe con
  complessità 37, ora è 70 righe con 9. Teneva in un blocco solo la fusione di
  alias, descrizione e proprietà, quella di fatti ed eventi, la riscrittura
  delle relazioni verso la sorgente, le reciproche mancanti, le righe da
  aggiornare e i contatori; ora sono sette funzioni con un nome, e la fusione
  resta quella di prima — `entita` la verifica sugli stessi casi, compreso il
  rollback.

## [0.8.0] - 2026-09-22

Il contratto del nucleo è completo: percorsi, impostazioni, identità e
politica attraversano i costruttori come parametri, e le globali mutate da
`imposta_percorsi` non esistono più. Due workspace nello stesso processo — o
una UI desktop — ora ci stanno sopra. Insieme arriva la coerenza della
memoria: `Demo` e `demo` sono lo stesso utente ovunque, gli id di sessione
non collidono più, e due finestre note (la conferma della memoria e il
ripristino) vengono dichiarate invece che taciute. Chiude il giro la misura
del costo dell'apprendimento, che entra nella suite come prova.
Release minor: nessuna migrazione dei dati e nessun cambio d'uso della CLI,
ma chi importa i moduli di Ares trova `leggi_percorsi()`,
`leggi_impostazioni()` e `leggi_politica()` al posto delle globali
riassegnate, e `Utente` al posto della stringa da normalizzare.

Verifica locale del 2026-09-22: nove prove offline verdi con copertura al 90%
su Linux/Python 3.12; `ruff check`, `ruff format --check` e `mypy ares` (53
file) puliti. Con Ollama, `e2e`, `affidabilita` e `intuizioni` verdi su
`deepseek-v4.1-flash:cloud` (70,9 s); nel turno di `e2e` l'apprendimento ha
speso 5.602 token di ingresso e 424 di uscita.

### Added

- **La misura del costo dell'apprendimento.** `tests/learning_cost_test.py`
  costruisce la macchina di apprendimento con un modello finto che conta le
  chiamate e risponde scrivendo, e misura cosa costa un turno: **cinque**
  chiamate al modello di apprendimento, non le tre che il numero degli store
  `ALWAYS` lascerebbe credere. Profilo e memorie scrivono e poi richiamano il
  modello per una conferma che Agno scarta, mentre il contesto di sessione la
  salta con `stop_after_tool_call`; con un turno di 650 caratteri le cinque
  chiamate rimandano al modello 33.649 caratteri, due terzi dei quali sono le
  istruzioni dei tre store. Spegnere uno store toglie le sue chiamate, e un
  modello che non chiama lo strumento costa i tentativi del contesto.
  `tests/e2e_test.py` stampa la stessa misura con i modelli veri — 5.602 token
  di ingresso e 424 di uscita per un turno di una riga — accanto alla riga che
  il client mostra sotto la risposta. I conti e le quattro opzioni di
  mitigazione stanno in `docs/memory-quality.md`; la suite offline passa da
  otto a nove prove.

### Changed

- **Anche la politica viaggia come parametro: nasce `Politica`.**
  I flag operativi e di apprendimento in `ares/config.py` — `LEARN_USER_PROFILE`,
  `LEARN_USER_MEMORY`, `LEARN_SESSION_CONTEXT`, `LEARN_ENTITIES`,
  `LEARN_KNOWLEDGE`, `MEMORY_AGENT_TOOLS`, `MAX_UPDATES_PER_RUN`,
  `SESSION_CONTEXT_RETRIES`, `DATE_MEMORIE`, `SEARCH_PAST_SESSIONS`,
  `READ_CHAT_HISTORY`, `NUM_HISTORY_RUNS`, `MAX_TOOL_CALLS_FROM_HISTORY`,
  `PAST_SESSIONS_LIMIT`, `PAST_SESSION_RUNS_PREVIEW`,
  `SESSIONI_RECENTI_NEL_PROMPT`, `WORKSPACE`, `WORKSPACE_ISTRUZIONI`,
  `WORKSPACE_ISTRUZIONI_MAX_BYTE`, `WORKSPACE_PREFIX`,
  `WORKSPACE_READ_BEFORE_WRITE`, `MOSTRA_METRICHE`, `MOSTRA_ESITO_STRUMENTI`,
  `ESITO_RIGHE`, `ESITO_LARGHEZZA`, `MOSTRA_APPRENDIMENTI`,
  `CONFERMA_APPRENDIMENTI`, `SESSIONI_ELENCO` — erano l'ultimo pezzo di
  configurazione che i costruttori rileggevano da soli. Ora
  `config.leggi_politica()` li fotografa in una `Politica` congelata al confine
  del processo, fatta di quattro gruppi: `Apprendimento`, `Cronologia`,
  `Workspace`, `Mostra`. Da lì in poi l'oggetto si passa:
  `build_learning_machine(db, knowledge, utente, impostazioni, politica)`,
  `build_session_context_store(db, modello, politica)`,
  `build_workspace(percorsi, politica, modo)`,
  `build_assistant(percorsi, impostazioni, politica, utente, ...)`, le funzioni
  dei prompt, il client della CLI e gli `StatoChat`. Il caso che rende la cosa
  concreta è il prompt: `istruzioni_sulla_memoria` descriveva store ed eco da
  `config`, quindi poteva promettere che una scrittura sarebbe comparsa sotto
  la risposta mentre l'eco era spenta; adesso descrive la stessa fotografia che
  ha costruito gli store, e non può descriverne altri. Il tetto dei tentativi
  di `save_session_context`, prima riletto dentro il ciclo di estrazione,
  viaggia con l'istanza dello store. `Apprendimento.automatici` e
  `Apprendimento.agentici` sono proprietà derivate, perché il prompt le usa al
  posto di tre `or` allineati a mano. Restano fuori `modo` (già parametro a
  ogni confine), `OFFLOAD_TOOL_RESULTS` e `TOOL_RESULT_THRESHOLD_CHARS`
  (configurazione dell'indice), `DATETIME_FORMAT`, `CRONOLOGIA_RIGHE` ed
  `ENTITA_FINESTRA_RICERCA` (formato del client) e le costanti di backup e
  retention (garanzie, non politica). I nomi restano in `config.py` come
  sorgente, quindi continuano a funzionare `.env` e le sostituzioni delle
  prove, purché la fotografia avvenga dentro la sostituzione. Nessun
  comportamento visibile cambia.

- **Anche i modelli viaggiano come parametro: nasce `Impostazioni`.**
  I nomi del tuning in `ares/config.py` — `MAIN_MODEL`, `LEARNING_MODEL`,
  `EMBEDDER_MODEL`, `EMBEDDER_DIMENSIONS`, `OLLAMA_HOST`, `KEEP_ALIVE`,
  `NUM_CTX`, `TEMPERATURE`, i due `think` — erano l'ultimo pezzo di
  configurazione che i costruttori rileggevano da soli: `build_chat_model()`,
  `build_learning_model()`, `build_knowledge(percorsi)` e
  `build_assistant(percorsi, utente, ...)` decidevano a chi parlare guardando
  un nome di modulo, e una firma non lo diceva. Ora `config.leggi_impostazioni()`
  li fotografa in un `Impostazioni` congelato al confine del processo — la chat
  e i suoi comandi nel proprio corpo, preflight all'inizio, le prove
  all'import — e da lì in poi l'oggetto si passa: `build_chat_model(impostazioni)`,
  `build_learning_model(impostazioni)`,
  `build_learning_machine(db, knowledge, utente, impostazioni)`,
  `build_assistant(percorsi, impostazioni, utente, ...)`. I costruttori dei
  prompt (`descrizione`, `istruzioni_sull_ambiente`), il preflight e la riga
  delle metriche ricevono le stesse impostazioni, quindi l'avviso sul cloud e
  la percentuale di finestra descrivono la conversazione che si sta avviando e
  non quella che il processo aveva in mente all'import. Due conversazioni con
  modelli diversi nello stesso processo sono due oggetti, non due mutazioni a
  distanza. `OLLAMA_OPTIONS` e `LEARNING_OPTIONS` spariscono come nomi e
  diventano proprietà del tipo, perché il contesto dell'estrazione dipende
  dalla coppia di modelli: si stringe a `NUM_CTX_ESTRAZIONE` solo quando sono
  diversi, altrimenti Ollama riavvierebbe il runner a ogni passaggio perdendo
  la cache del prompt. La modalità resta fuori dal tipo — è già un parametro a
  ogni confine — ma `build_assistant` e le tre funzioni dei prompt smettono di
  fotografare `config.MODO_PREDEFINITO` nella firma e lo risolvono alla
  chiamata, come faceva già `build_workspace`. I nomi del tuning restano in
  `config.py` come sorgente, quindi continuano a funzionare i `.env`,
  `ARES_MAIN_MODEL` e le sostituzioni delle prove. Eccezione deliberata:
  `ares/backup/snapshots.py` legge ancora i nomi di modulo, perché il
  manifesto dello snapshot deve registrare com'era configurato *questo*
  processo e il controllo di compatibilità dell'embedder è una garanzia
  esistente. Nessun comportamento visibile cambia.

- **I percorsi viaggiano come parametro, non come nomi di modulo.**
  `ares/config.py` teneva un `PERCORSI` corrente e le dieci viste che lo
  nascondevano — `ARES_HOME`, `TMP_DIR`, `DB_FILE`, `FS_DB_FILE`,
  `LANCEDB_URI`, `BACKUP_DIR`, `STATE_LOCK_FILE`, `CRONOLOGIA_FILE`,
  `WORKSPACE_DIR` — rilegate insieme da `imposta_percorsi`. Chiunque leggesse
  lo stato poteva prenderlo da lì, e la chat cambiava cartella di lavoro
  riassegnando un globale a metà esecuzione: due insiemi di percorsi nello
  stesso processo erano possibili e indistinguibili. Ora c'è un solo modo di
  costruirli, `config.leggi_percorsi()`, e sta al confine del processo — i
  comandi della CLI nel proprio corpo, le prove all'import dopo
  `prepara_ambiente`. Chi tocca lo stato riceve un `Percorsi` come primo
  parametro, le facciate iniettate (`OperazioniBackup`, `OperazioniRestore`)
  lo dichiarano nella firma, e `--workspace` è una sostituzione locale che da
  lì in poi viaggia come gli altri. Due eccezioni deliberate: `lock_stato`
  prende un `Path`, perché la migrazione tiene due lock e uno dei due non è un
  `Percorsi`; `chiedi_conferme` lo prende in coda, perché il resto della firma
  è ciò che si mostra all'utente. L'identità non è un campo di `Percorsi`: è
  un asse suo, e viaggia come `Utente` accanto. Nessun comportamento visibile
  cambia.

- **L'identità dell'utente è un tipo, non una stringa da normalizzare.**
  `utente_canonico` era la regola giusta, ma chiamata in otto punti — la chat,
  il quaderno, le sessioni, il prompt, la manutenzione — e bastava un lettore
  nuovo che se ne dimenticasse perché l'archivio si sdoppiasse in silenzio:
  namespace in un contenitore, profilo e lock in un altro, nessun errore.
  Ora c'è `Utente`, e la forma canonica è il tipo invece di una convenzione:
  il costruttore rifiuta una scrittura non canonica, `da_grezzo` è l'unica
  porta, e namespace, lock, quaderno, store, sessioni, prompt e manutenzione
  lo ricevono già risolto. La normalizzazione avviene una volta sola al
  confine — l'opzione della riga di comando, l'ambiente, il `user_id` riletto
  da Agno — dove si può ancora dire perché un id è invalido. Spariscono i
  default nella firma dei costruttori che fotografavano
  `config.DEFAULT_USER_ID` al momento dell'import: erano una seconda risposta
  alla domanda "per conto di chi". Nessun comportamento visibile cambia: è la
  stessa regola, con un tipo che la tiene.

### Fixed

- **Identità utente canonica in un solo punto.** `Demo` e `demo` erano la
  stessa persona per il namespace — che lo minuscolizza — e due persone
  diverse per profilo, User Memory e lock dei turni: due chat sullo stesso
  archivio scrivevano lo stesso profilo credendo di essere sole, e ciò che
  l'una scriveva l'altra non lo vedeva. La forma dell'id — spazi ai bordi e
  maiuscole — ora si decide in `ares/state/identita.py`, e namespace,
  profilo/memorie (via `build_assistant`), lock, manutenzione e ispezione la
  usano tutti. Un id che si riduce a vuoto è rifiutato invece di diventare un
  contenitore condiviso. L'alfabeto ammesso è ristretto a lettere e cifre
  ASCII, punto, trattino e trattino basso: un id con `/`, spazi o caratteri
  accentati è rifiutato all'ingresso, invece di finire in un namespace che
  Agno percent-encoda (`café` → `caf%c3%a9`) o che si annida sotto quello di
  un altro utente. Con gli id già in minuscolo e ASCII, come `kairos` nel
  `.env`, nessun archivio cambia posto. **Un id non canonico già in uso,
  invece, cambia posto:** profilo, memorie e sessioni scritte con la grafia
  vecchia non compaiono più sotto la nuova, mentre entità, intuizioni e
  quaderno erano già minuscoli e restano. Non c'è migrazione: la premessa
  della roadmap è che non ci siano dati da conservare.

- **L'ID di sessione non collide più.** Il nome della cartella troncato più i
  secondi non distingueva due progetti omonimi — due `api/` in due posti — né
  due avvii nello stesso secondo: la seconda conversazione riusava la riga
  della prima. L'id conserva il prefisso leggibile e aggiunge una coda
  casuale; `tests/cli_test.py` lo prova su entrambi i casi.

- **La conferma della memoria dichiara la sua finestra.** Il commento di
  `agent/echo.py` presentava il ripristino come una conferma "con lo stesso
  effetto di una a priori": non è vero per intero, perché la scrittura avviene
  durante il turno e il ripristino alla risposta, e fra le due c'è una finestra
  in cui un processo che muore — un `kill`, un crash, il terminale chiuso —
  lascia la scrittura dov'è. Il lock dell'utente copre l'attesa fra due chat,
  non fra due vite del processo. La frase ora dice cosa la conferma copre
  davvero — *se il processo sopravvive al turno*, ciò che si rifiuta non
  sopravvive — e la stessa formulazione sta in `config.py`, in
  `docs/architecture.md` e nella `ROADMAP.md`. Chiudere la finestra
  (scrittura provvisoria, riversata dopo il consenso) resta una scelta della
  voce 3 della roadmap: qui non cambia nessun comportamento.

- **La versione di Agno dichiarata è quella installata.** Il commento di
  `AresLearningMachine` citava ancora la 3.0.5 mentre `uv.lock` era alla
  3.0.9: un numero vecchio non fa fallire niente, è una pagina che descrive un
  altro programma. `tests/agno_contract_test.py` ora confronta con
  l'installato le dichiarazioni esplicite — badge del `README.md`,
  `ROADMAP.md`, `SECURITY.md`, il commento in `agent/learning.py`,
  `docs/agno.md`, `docs/architecture.md`, `docs/core-contract.md` — e nessun
  altro file può citarne una diversa. Una patch di Agno che sale senza
  allineare le pagine rende la prova rossa, e il messaggio nomina il file da
  correggere; `CHANGELOG.md` e `docs/memory-quality.md` restano fuori, perché
  citano le versioni di allora.

## [0.7.1] - 2026-09-13

Correzioni di integrità dei dati e sicurezza operativa: restore Windows,
memoria fra chat concorrenti, interruzioni e output in pipe. Include anche
le correzioni dell'audit del 12 settembre e l'aggiornamento ad Agno 3.0.9.
Release patch: nessuna nuova API e nessuna migrazione dei dati. Le chat già
aperte devono essere riavviate per adottare il lock per utente.

Verifica del 2026-09-13: otto suite offline verdi con copertura al 90%
su Linux/Python 3.12.3, più le tre prove con Ollama (`--solo affidabilita
intuizioni e2e`) verdi su Agno 3.0.9 e LanceDB 0.38.0. Conversazione con
`deepseek-v4.1-flash:cloud`, estrazione con `glm-5.3-flash:cloud`, embedding
locale con `nomic-embed-text-v2-moe`; tutti gli archivi delle prove sono
temporanei. Il modello conversazionale locale non è stato verificato in
questo giro. La PR #71 è passata anche in CI su Windows/Python 3.12 e su
Ubuntu/Python 3.12 e 3.13, con analisi statica, CodeQL e audit dipendenze verdi.

### Changed

- **Agno 3.0.9.** Quattro patch dopo la 3.0.5, quasi tutte su AgentOS, MCP,
  Workflow e Knowledge, che Ares non usa. Tre toccano Ares e sono tutte
  correzioni: le sessioni in SQLite non vengono più codificate due volte,
  così `session_data` in `kairos.db` diventa un oggetto JSON e non una
  stringa che lo contiene, con le righe vecchie lette come prima e senza
  migrazione; l'upsert in blocco delle sessioni applica lo stesso controllo
  sul proprietario di quello singolo; su Python 3.13 gli strumenti in cache
  non tengono più vivo il frame che li ha avvolti, e con lui agente,
  sessione e run. I moduli `agno.learn` non cambiano: il limite sulla
  conferma di profilo e memorie descritto in `SECURITY.md` resta. Cambia
  solo `agno` nel lock, nessuna dipendenza transitiva.

- **`ares -p` rifiuta anche `modifiche`.** Il controllo copriva solo `auto`,
  ma `modifiche` scrive e modifica file senza conferma: con un testo ostile
  da una pipe avrebbe potuto riscrivere `ARES.md`, uno script o un Makefile
  senza che nessuno guardasse. La regola ora legge la tabella delle
  modalità — è rifiutata ogni modalità che mette fra i silenziosi uno
  strumento che lascia traccia, e l'insieme di quegli strumenti è la lista
  con conferma di `manuale`, non una seconda copia — e la prova chiede il
  rifiuto di `modifiche` oltre ad `auto` e il turno con `piano`.
- **Il `.env` non entra più in `os.environ`.** `run_command` lancia il
  sottoprocesso con l'ambiente del processo, e un `env` da una shell avrebbe
  stampato le righe del `.env` da qualunque cartella di lavoro, con l'output
  che torna nel contesto del modello. Il file si legge in un dizionario e le
  variabili `ARES_*` vengono da lì; una variabile già nell'ambiente vince
  come prima, e su Windows i nomi si confrontano senza distinguere le
  maiuscole, come fa `os.environ` lì. Il file sul disco resta leggibile da un
  comando se la cartella di lavoro è il clone di Ares: è l'avviso che
  `cartella.py` dà all'avvio, e non cambia. La prova scrive un `.env` a
  parte e verifica precedenza e isolamento.
- `ares init` e `ares preflight` prendono i codici di uscita dalla tabella
  di `cli/comando.py` invece di scrivere `0` e `1`.

### Fixed

- **Diagnostica di contesa su stderr anche con `ares -p`.** Il gestore
  esterno usa `UI.err()` dopo che il contesto di output della pipe è stato
  chiuso. I test verificano stdout vuoto, diagnostica su stderr, codice 3 e
  nessuna inferenza sia per il lock per utente sia per quello dello stato.
- **Restore Windows: originale intatto se la copia di sicurezza fallisce.**
  La copia iniziale ora precede il blocco di installazione e rollback: un
  errore prima o durante la copia non svuota più lo stato per recuperarlo da
  una copia incompleta. Due regressioni verificano entrambi i casi.
- **Un turno per utente fino alla conferma della memoria.** Le chat
  condividono ancora il lock dello stato, ma ogni turno prende anche un
  lock esclusivo per utente prima dell'istantanea e lo rilascia dopo la
  conferma e l'eventuale rollback. Un rifiuto non può così cancellare una
  memoria scritta da una chat concorrente. La REPL segnala la contesa e
  resta aperta; `ares -p` termina con codice 3. Utenti diversi restano
  indipendenti. La prova usa SQLite reale e processi separati.
- **Gli apprendimenti vengono mostrati anche dopo un errore o Ctrl-C fuori
  dal generatore.** Le scritture già avvenute passano dalla stessa conferma
  del turno normale; eliminata la rassicurazione falsa «Non è stato
  appreso». Il test scrive nello store prima di provocare entrambi gli esiti
  e verifica che il rifiuto conservi soltanto le memorie precedenti.
- **Test TTY indipendenti dall'ambiente della shell.** Il terminale
  simulato ha un ambiente Rich esplicito; una prova separata copre
  `TERM=dumb` e l'assenza di controlli ANSI.
- `ROADMAP.md` riporta Agno 3.0.9; la policy di sicurezza distingue le
  autorizzazioni delle quattro modalità, gli store di apprendimento e il
  quaderno privato persistente anche in pipe. Documentati lock per utente
  e limiti in caso di arresto forzato.
- `SECURITY.md` dichiarava supportata la linea 0.6.x: ora dice 0.7.x.

## [0.7.0] - 2026-09-12

La versione della riga di comando. Un esame della CLI del 12 settembre ha
trovato tre difetti che nessuno aveva visto — l'indicatore d'attesa che non
diceva mai cosa aspettava, `ares -p` che sporcava stdout, un codice d'uscita
sbagliato nell'aiuto — e una decina di margini fra uso quotidiano e forma
del codice; sono entrati in tre pull request, nell'ordine: prima ciò che era
rotto, poi ciò che cambia la mano sulla tastiera, poi il resto. Prima
ancora erano entrate le quattro lacune dell'audit del 10 settembre: due
promesse senza una prova che le difendesse — la tabella dei codici d'uscita
e il protocollo della sonda LanceDB — il vincolo su Python senza la ragione
scritta accanto, e un parametro che nessun chiamante poteva usare.

Cambia il comportamento di Ctrl-C al prompt e la forma dell'aiuto: per
questo è una minor e non una patch.

Verifica locale del 2026-09-12 su Agno 3.0.5: `tests/run.py --tutte` supera
11 suite su 11 in 109,5 s, con `deepseek-v4.1-flash:cloud` come modello
conversazionale, `gemma4:31b-cloud` per l'apprendimento e l'embedder locale.
`ruff check`, `ruff format --check` e `mypy` non hanno rilievi; le prove
offline misurano il 90% fra righe e rami. Le tre pull request della
revisione e le quattro lacune dell'audit sono entrate con la CI verde su
Ubuntu, Python 3.12 e 3.13, e su Windows.

### Added

- **Il TAB completa anche l'argomento.** Dopo lo spazio, `/modo ` propone
  le tre modalità che la REPL accetta con la stessa descrizione del suo
  elenco, e `/sessione ` le conversazioni di questa cartella con data e
  prima domanda, più `nuova`. I candidati si leggono una volta per prompt e
  non a ogni tasto, perché `/sessione` li chiede al database e il
  completamento mentre si scrive chiama il completer a ogni carattere.
- **`/sessione nuova` apre una conversazione senza uscire.** L'id viene
  dalla cartella e dal momento, come un altro `ares` lanciato qui; contesto
  vuoto, profilo e memorie invariati. `nuova` è quindi una parola riservata
  in quel comando; `ares --session nuova` resta possibile.
- **`/esporta` scrive la conversazione in Markdown.** Una testata con
  sessione, utente, cartella, modello e numero di scambi, poi `## Tu` e
  `## Ares` a turni e una riga con gli strumenti chiamati. Senza argomento
  il file prende il nome della sessione nella cartella di lavoro e uno che
  esiste già non si tocca; `/esporta <file>` sovrascrive e lo dice. Agno
  rimette la storia precedente nei messaggi di ogni run, marcata
  `from_history`: il filtro c'è, e la prova conta che ogni scambio compaia
  una volta.
- **`ares resume -p "..."`.** Un turno solo sull'ultima conversazione nata
  in questa cartella, con il suo contesto, e poi esce: prima si poteva solo
  con `--session <nome>`, cioè sapendo l'id. Vale ciò che vale per ogni
  `-p`: niente memoria da scrivere, conferme rifiutate, su stdout la sola
  risposta. `--scegli` con `-p` è rifiutato con 2, perché chiede un numero e
  stdin è la domanda.
- **I codici d'uscita 1 e 2 hanno una prova che li produce.** La tabella di
  `cli/comando.py` — 0 fatto, 1 guasto, 2 rifiutato, 3 occupato — è il
  contratto su cui uno script chiamante decide se riprovare o fermarsi, ed è
  documentata in [Architettura](docs/architecture.md). Il 3 era già asserito
  da `entita` e `sessioni` con il lock preso; il 2 solo dove nasce da un
  `return`, mai dove arriva come eccezione; il 1 da nessuna parte. Ora
  `sessioni` chiede la cancellazione di una sessione inesistente, che è un
  rifiuto sollevato sotto il lock, e un prune con la directory dei backup
  occupata da un file, che è un guasto del disco: la prova verifica il codice
  e, soprattutto, che il prune si fermi prima di cancellare, perché lo
  snapshot che non è riuscito era la rete. `cli` chiede uno snapshot con lo
  stato già preso da un'altra finestra. `cli/comando.py` passa dal 76% al
  100%.
- **La sonda LanceDB viene eseguita davvero.** Il protocollo fra `integrity`
  e `backup/probe.py` era provato con `subprocess.run` sostituito: dimostrava
  come il genitore traduce ciò che riceve, non che il figlio dica davvero
  quello. Ora `backup` lancia `-m ares.backup.probe` e verifica ciò che il
  figlio decide da sé: 2 per un uso sbagliato, `{}` e 0 per una directory
  assente, e un'eccezione durante la lettura tradotta in 1 con il motivo su
  stderr e stdout muto. Se un giorno il modulo non fosse più avviabile in un
  altro interprete, le prove con la sostituzione resterebbero verdi.
  `backup/probe.py` passa dal 79% al 100%.

  Come fallisce LanceDB resta invece fuori dalle asserzioni, e non per
  pigrizia: la prima versione della prova chiedeva alla sonda di aprire un
  file al posto di una directory e pretendeva un guasto. Su Linux arriva; su
  Windows no, dove il motore risponde con nessuna tabella ed esce 0. L'ha
  trovato il runner Windows in CI. La sicurezza non ci perde — un elenco
  vuoto dove il manifest dichiara delle tabelle è una discordanza, e il
  genitore la rifiuta lo stesso — ma un'asserzione sul comportamento del
  motore nativo prova il motore, non Ares.
- **Python 3.13 nella matrice della CI.** Su Ubuntu, accanto alla 3.12, con
  le stesse otto prove e la stessa misura. Non è fra i controlli obbligatori
  del ruleset, come CodeQL e `Audit` e per lo stesso motivo: è un canarino
  sull'interprete, e ciò che trova va letto quando compare.

### Changed

- **`ares --help` mette prima ciò che si usa.** I comandi erano in ordine
  alfabetico e `resume` era settimo; ora vengono `resume` e `init`, poi la
  manutenzione nell'ordine in cui la si incontra. La riga sotto l'uso non è
  più una frase sola: è il docstring della chat, con la descrizione e sette
  esempi, perché l'App radice non ha più un `help` proprio e Cyclopts mostra
  quello del comando di default. I flag spenti non dicono più `[default:
  False]`, in nessun comando: gli altri default restano, perché `--modo
  manuale` e `--keep 20` sono informazioni.
- **La barra sotto il prompt dice modalità, sessione e finestra occupata.**
  Prima era l'elenco fisso dei tasti; ora a sinistra c'è lo stato, che si
  aggiorna da sé dopo `/modo`, `/sessione` e ogni turno, e la finestra in
  percentuale è quella dell'ultimo turno, così non serve accendere
  `/metriche` per sapere quanto contesto resta. I tasti stanno a destra
  finché ci stanno, poi si accorciano, poi restano nel banner.
- **`/modo` è una tabella.** Le righe erano composte a mano e a cento
  colonne quella di `piano` si spezzava dentro l'elenco degli strumenti
  assenti; la lista degli otto strumenti era scritta una seconda volta nel
  comando, e ora viene da `config.MODALITA`, dalla modalità che li ha tutti.
- **Il banner parla italiano:** "assistente locale" al posto dell'unica
  frase inglese dell'interfaccia.
- **La tabella dei comandi della REPL ha i nomi dei campi.** `COMANDI` era
  una tupla di tuple letta per indice — `voce[0]`, `voce[1]`, `voce[3]` — e
  `risolvi_comando` restituiva `tuple`; ora è una tupla di `Comando`, un
  NamedTuple con nome, alias, descrizione e funzione, e le firme dicono cosa
  restituiscono. `StatoChat.agent` è un `Agent`, non `Any`, e ha un campo
  `finestra` per la barra.
- **`chat` e `resume` condividono le opzioni.** Le cinque comuni — utente,
  cartella, modalità, debug, metriche — erano ripetute con le stesse
  docstring; ora sono un dataclass `OpzioniChat` che Cyclopts appiattisce
  nell'aiuto, indistinguibile da prima per chi legge.
- **`mostra_evento` è una tabella, non venti `elif`.** Sei eventi aprono
  un'attesa con la loro etichetta, cinque la chiudono, nove hanno un'azione
  propria: tre dizionari al posto della catena, con lo stesso comportamento
  provato dalle stesse prove.
- **`cli/chat.py` non riesporta più venti nomi** di `render`, `commands` e
  `log` per compatibilità delle prove: le prove importano dai moduli veri.
- **Ctrl-C al prompt svuota la riga invece di chiudere la chat.** Chiudere
  per un riflesso, con un messaggio lungo a metà, costava il messaggio. Ora
  la riga finisce in cronologia — la freccia in su la riporta — e il prompt
  resta; chiudono `Ctrl-D` e `/esci`. Durante un turno Ctrl-C resta
  l'interruzione. Banner e barra in basso lo dicono.
- **`requires-python` diventa `>=3.12,<3.14`.** Era `<3.13`, ed era l'unico
  vincolo del file senza il motivo accanto — proprio la regola che il
  `pyproject.toml` enuncia per le dipendenze due righe più sotto. La verifica
  ha detto che non c'era un motivo: su 3.13 le otto prove offline passano,
  ruff e mypy non hanno rilievi, e rigenerare il lock per l'intervallo
  allargato non cambia una sola versione risolta. Il pavimento resta 3.12,
  che è ciò che `setup.sh`, `setup.ps1` e `.python-version` installano e su
  cui mypy controlla i tipi; il tetto ora dice una cosa vera, cioè che la
  3.14 non l'ha provata nessuno.
- **`codice_di` perde il parametro `rifiuti`.** L'unico chiamante gli passava
  sempre una tupla vuota, e `isinstance(errore, ())` è falso sempre: quel
  ramo non poteva essere preso. Non era un caso d'uso da riempire — nel
  backup un rifiuto non è mai un'eccezione, ma una risposta sbagliata alla
  conferma, che la funzione traduce da sé. Chi ha rifiuti che arrivano come
  eccezioni passa da `esegui_protetto`, che li nomina.

### Fixed

- **L'indicatore d'attesa dice cosa sta aspettando.** `render.py` passava
  allo stream sei etichette — "Ares sta elaborando...", "Ares sta
  aggiornando ciò che ricorda...", il nome dello strumento in esecuzione —
  e lo stream le riceveva, le provava e non le disegnava mai: il
  renderable dell'indicatore componeva solo il frame e la coda
  dell'anteprima, dal commit che ha introdotto lo streaming stabile. Ora
  l'etichetta compare accanto al frame mentre si aspetta, e sparisce con
  lui: la prova verifica che sia disegnata e che dopo l'ultimo erase non ci
  sia più.
- **`ares -p` in una pipe scrive su stdout solo la risposta.** Prima uno
  script che leggeva stdout ci trovava, insieme alla risposta, la riga
  "Ares", ogni strumento chiamato con l'anteprima del suo esito e la riga
  delle metriche. `UI.solo_risposte` separa le due uscite per la durata del
  comando: la risposta del modello resta su stdout, tutto il resto —
  avvisi d'avvio, rifiuti, strumenti, conferme, metriche — va su stderr,
  anche quando stdout è un terminale, perché la regola non dipende da chi
  ascolta. Verificato con un turno vero e una pipe: `ares -p ... > out
  2> err` lascia in `out` la sola riga di risposta.
- **L'aiuto di `ares resume` dichiara il codice giusto.** Diceva "esce con
  1" senza conversazioni da riprendere; il codice restituisce 2, rifiutato,
  come dice la tabella di `cli/comando.py`. Il docstring di `cli/chat.py`
  citava anche `chat_commands.COMANDI`, un modulo che non esiste dal
  passaggio al package.

## [0.6.1] - 2026-09-10

Verifica locale del 2026-09-10 su Agno 3.0.5: `tests/run.py --tutte` supera
11 suite su 11 in 131,6 s, con `glm-5.3-flash:cloud` come modello
conversazionale, `gemma4:31b-cloud` per l'apprendimento e l'embedder locale.
`ruff check`, `ruff format --check` e `mypy` non hanno rilievi; le prove
offline misurano il 90% fra righe e rami. La CI è verde su Ubuntu e Windows.

Il giro dell'8 settembre ne superava 10 su 11: con
`hf.co/empero-ai/Qwen3.8-9B-Distill-GGUF:Q8_0` in entrambi i ruoli,
`intuizioni` falliva sulla lingua dell'intuizione salvata. Fra i due giri non
c'è un commit di codice - solo documentazione - quindi quel fallimento è del
modello locale, non del percorso di salvataggio: vale come limite del modello
scelto e va riprovato quando cambia, non come difetto aperto di questa
versione.

Resta invece dichiarato, e rimisurato il 10 settembre, il limite della
qualità della memoria con il modello locale di serie. Sugli stessi due casi e
con lo stesso codice: **3 fasi superate su 6 con i modelli cloud, 0 su 6 con
`hf.co/empero-ai/Qwen3.8-9B-Distill-GGUF:Q8_0` nei due ruoli**. Con quel
modello il profilo attribuisce all'utente una professione e uno stack che il
dialogo non nomina, e l'avvio già confermato non sopravvive a una decisione
ribadita. Il rapporto locale dell'8 settembre, rivalutato con il valutatore
corretto di questa versione, dà verdetti identici: il limite è del modello e
non dello strumento. Una ripetizione non stabilisce una tendenza. Evidenze,
tabelle e limiti in [Qualità della memoria](docs/memory-quality.md).

### Added

- **Benchmark della qualità della memoria.** Nove scenari sintetici coprono
  ipotesi, personaggi inventati, proposte, correzioni, preferenze temporanee,
  recupero, abbandono di idee e piani e distinzione fra decisione e avvio.
  L'estrazione reale e il recupero da sessioni nuove usano archivi isolati;
  diciannove fasi per ripetizione producono rapporti JSON/Markdown con
  evidenze, modelli e hash dei sorgenti. I controlli sulle citazioni leggono
  le voci originali; le prove di aggiornamento richiedono il ricordo
  precedente pertinente. Timeout e Ctrl+C conservano i risultati parziali.
  La suite offline `valutazione` comprende 29 controlli. Protocollo, misure
  e limiti sono in [Qualità della memoria](docs/memory-quality.md).
- **Ricerca degli advisory sulle dipendenze bloccate.** Il workflow `Audit`
  esporta dall'`uv.lock` l'elenco esatto delle dipendenze, gruppo di sviluppo
  compreso, e lo confronta con le vulnerabilità note, a ogni push e pull
  request e il lunedì mattina. Versione bloccata e hash verificato non dicono
  se quella versione ha un avviso pubblicato, e né Dependabot né CodeQL
  rispondono a quella domanda. Come CodeQL resta fuori dai controlli
  obbligatori: un avviso è una notizia sul mondo, non una regressione del
  commit che lo incontra. Alla prima esecuzione: nessuna vulnerabilità nota
  su 66 pacchetti.

### Fixed

- **Decisioni e programmi non diventano attività già iniziate.** Estrazione,
  profilo, riepiloghi e recupero distinguono lo stato dichiarato. Un avvio
  sconosciuto non diventa una mancata partenza certa; ribadire una decisione
  non cancella un avvio già confermato. Nessuna migrazione degli archivi.
- **Prompt coerenti con gli strumenti disponibili.** Workspace, memoria e
  quaderno hanno istruzioni distinte; in `-p` non compaiono strumenti degli
  store assenti. La ricerca nelle sessioni precedenti rispetta il proprio
  flag. Le intuizioni mantengono titolo, contenuto e contesto in italiano
  anche quando la conversazione è in un'altra lingua.
- **Il valutatore non scarta più una citazione corretta dell'avvio.** Il
  legame fra valore atteso e citazione pretendeva il termine alla lettera,
  mentre una memoria che dice «ha confermato l'avvio dei lavori» sostiene il
  valore `iniziato` senza contenerlo: la risposta usciva non conclusiva pur
  citando verbatim lo store, e la stessa frase era già riconosciuta come
  prova d'avvio dalla precondizione. Ogni fase dichiara ora la forma ammessa
  per il proprio valore; le altre diciassette fasi continuano a pretendere il
  termine, e verbatim, ambiguità, lunghezza minima e frammento restano
  invariati. Rivalutati offline i dieci rapporti salvati, 116 fasi: cambiano
  undici verdetti, tutti da non conclusivo a superato sulle due fasi
  interessate, e la misura cloud dell'8 settembre passa a 12 superate su 12,
  raggiungendo la lettura manuale già registrata. Il rapporto salva il testo
  dell'espressione dichiarata: era un oggetto compilato e il worker moriva
  scrivendo il JSON, perdendo le fasi già misurate.

### Changed

- **Criteri espliciti per collaborazione e memoria.** Il prompt chiarisce
  capacità, persistenza e verifica degli esiti; l'apprendimento distingue
  fatti, ipotesi, proposte accettate e correzioni. Lo smoke verifica il
  prompt completo e gli strumenti in 19 combinazioni. Dettagli in
  [Prompt di Ares](docs/prompt.md).

- **I percorsi sono un oggetto costruito a runtime.** `config.Percorsi` -
  home, stato, backup, cartella di lavoro, utente, con i nomi derivati come
  proprietà - nasce da `leggi_percorsi`, che legge un ambiente e una
  directory dati o quelli veri quando viene chiamata, non quando il modulo
  viene importato. I nomi di sempre, `TMP_DIR`, `DB_FILE`, `BACKUP_DIR`,
  `WORKSPACE_DIR` e gli altri, restano e sono viste dell'oggetto corrente;
  `imposta_percorsi` è l'unica porta da cui si sostituisce e li rilega tutti
  insieme, così non esiste un istante con uno stato nuovo e un lock vecchio.
  La chat ci passa con la cartella scelta, dove prima assegnava
  `config.WORKSPACE_DIR` e cinque punti del codice lo trovavano cambiato
  senza vederne il motivo. Una prova può ora costruire i propri percorsi
  nello stesso interprete; il runner continua a lanciare un processo per
  prova, per scelta e non per necessità.

## [0.6.0] - 2026-09-07

Prove con Ollama (`tests/run.py --tutte`) verdi il 2026-09-07 su Agno 3.0.5 e
LanceDB 0.38.0, 10 prove su 10, sul codice di questa versione, con
`glm-5.3-flash:cloud` in conversazione e `deepseek-v4-pro:cloud` in
estrazione dal `.env`; il modello conversazionale locale non è stato provato
in questo giro.

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

[Unreleased]: https://github.com/KairosIta/Ares/compare/v0.8.2...HEAD
[0.8.2]: https://github.com/KairosIta/Ares/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/KairosIta/Ares/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/KairosIta/Ares/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/KairosIta/Ares/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/KairosIta/Ares/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/KairosIta/Ares/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/KairosIta/Ares/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/KairosIta/Ares/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/KairosIta/Ares/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/KairosIta/Ares/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/KairosIta/Ares/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/KairosIta/Ares/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/KairosIta/Ares/releases/tag/v0.1.0
