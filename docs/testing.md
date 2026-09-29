# Strategia di test

La suite separa le verifiche offline dalle prove che richiedono Ollama. Ogni
prova reindirizza stato, backup e workspace verso directory temporanee,
senza toccare i dati del clone in uso.

La [valutazione semantica della memoria](memory-quality.md) è un comando
separato dalle regressioni: usa i modelli reali su dialoghi fissi e conserva
le prove dei verdetti. I suoi controlli offline sono registrati nel runner
come `valutazione`; la misura con Ollama si avvia esplicitamente con
`python -m evals.memory_quality`, anche quando si usa `tests/run.py --tutte`.

## Runner

```bash
.venv/bin/python tests/run.py             # le prove offline
.venv/bin/python tests/run.py --tutte     # anche quelle con Ollama
.venv/bin/python tests/run.py --copertura # offline, con la misura
.venv/bin/python tests/run.py --solo backup entita
```

L'elenco delle prove vive in un posto solo, la tabella `PROVE` in
`tests/run.py`: la CI chiama il runner, quindi una prova nuova entra in CI
registrandola lì. Ogni prova resta eseguibile anche da sola.

### Un processo per prova

Il runner non importa le prove: le lancia, una per processo. Ogni prova
imposta `ARES_TMP` e `ARES_BACKUP_DIR` ed entra nella propria cartella di
lavoro usa-e-getta (con `chdir`, perché nel prodotto la cartella di lavoro è
la directory corrente) **prima** di importare `config`, che all'import
fotografa l'ambiente in `AMBIENTE`.

Percorsi, impostazioni e politica si derivano da quella fotografia con
`leggi_percorsi()`, `leggi_impostazioni()` e `leggi_politica()`. Una prova
che sostituisce un nome di `config` — `MAIN_MODEL`, `NUM_CTX`,
`MOSTRA_APPRENDIMENTI`, `SESSIONI_ELENCO`… — deve costruire l'oggetto
*dentro* la sostituzione e passarlo a chi ne ha bisogno: uno costruito prima
non vedrebbe il cambiamento. `smoke` lo verifica (`politica a runtime`).

Il processo separato serve comunque, perché un lock, uno store aperto o un
percorso copiato in una variabile non si rileggono. Due prove nello stesso
interprete condividerebbero i percorsi della prima, e una variabile sbagliata
scriverebbe nell'archivio vero: un processo per prova rende l'errore
impossibile invece che improbabile.

### Moduli condivisi

`tests/_comune.py` non importa niente di `ares`, ed è la garanzia che i
percorsi vengano decisi prima che `config` fotografi l'ambiente:

- `prepara_ambiente` sceglie i percorsi usa-e-getta, e rifiuta se `config` è
  già in memoria;
- `pulisci` li cancella uscendo prima dalla cartella di lavoro (su Windows la
  directory corrente non si cancella);
- `esigi` è l'asserzione che `python -O` non toglie;
- `esegui` esegue i controlli in ordine; `fallimento` stampa una riga per
  controllo e, se fallisce, la riga d'origine o il traceback intero di un
  guasto imprevisto;
- `chiudi` conserva l'archivio quando qualcosa è andato storto, e lo
  cancella quando è andato tutto bene.

`tests/_doppi.py` raccoglie i doppi di Agno usati da più prove — il modello
a copione, la tool call nella forma in cui Agno la mette nei messaggi. Come
`_comune.py` non tocca `ares` né `config`, quindi si importa prima di
`prepara_ambiente`.

## Verifiche offline

```bash
.venv/bin/python tests/run.py
```

Sono undici. Nessuna genera risposte con il modello.

| Prova | Cosa verifica |
| --- | --- |
| `smoke` | costruzione dell'agente e semina degli store; isolamento, lock, propagazione del run completo alla macchina di apprendimento, eco di ciò che entra in memoria; le guardie del post-hook (run senza messaggi, post-hook senza agente); istruzioni delle intuizioni assenti fuori da `AGENTIC`; il prompt in 19 combinazioni (vedi [prompt](prompt.md#verifica-e-limiti)) |
| `repl` | la chat senza agente: conferme, esito e metriche degli strumenti, rendering Rich su pipe e su terminale simulato con i controlli filtrati, core del turno con eventi fabbricati, log di Agno, cronologia, editor, Ctrl-C/D, comandi locali; cartella di lavoro (percorsi rischiosi, esiti dell'autorizzazione, ramo da `.git/HEAD`, `ARES.md`, `ares init`); conversazioni per cartella e `ares resume --scegli` |
| `nucleo` | `ares/core/` usato come un client senza terminale: id dalla cartella o `principale`, apertura e cambio di modalità, modalità e nomi non validi, modalità ammesse senza presenza sulla tabella intera e `auto` solo all'apertura, stato in uso (lock condiviso finché serve, migrazione in sospeso rifiutata senza scrivere), sessione di un altro utente rifiutata prima di costruire l'agente, sessioni della cartella, agente vero senza post-hook; autorizzazioni: sì, no con motivo, cartella e quaderno distinti, rifiuto senza presenza che non interroga il client; il turno di `core/turn.py` con un client che non stampa: eventi, eco, rifiuto che ripristina, conferma spenta, senza presenza, guasto |
| `sessioni` | un vero `Agent.run()` con modello deterministico: offload, quota, retention, cascata e restore dei due SQLite |
| `contratto` | ciò che Ares dà per vero di Agno (vedi sotto) |
| `ambiti` | le due premesse dello studio sugli ambiti (vedi sotto) |
| `costo` | il costo delle estrazioni `ALWAYS` (vedi sotto) |
| `backup` | snapshot, checksum, restore e prune; il protocollo della sonda LanceDB, simulato e vero |
| `entita` | audit e fusione delle entità |
| `valutazione` | il benchmark della memoria senza modello (vedi sotto) |
| `cli` | i comandi reali: preflight contro un Ollama finto nei tre esiti, ispezione degli archivi, sottocomandi di backup con annullamenti, la REPL intera in un processo con stdin da pipe, l'avvio senza `--session` (sessione nuova, `resume` a vuoto e sull'ultima, `--scegli`, `-p` con stdin in pipe) |
| `rilascio` | la versione di Ares concorda fra `pyproject.toml`, lock, `CHANGELOG` e `SECURITY.md` (procedura in [CONTRIBUTING](../CONTRIBUTING.md#come-si-rilascia)) |

### `contratto`

Chiede ad Agno le cinque cose su cui Ares si regge:

1. un turno con pausa per conferma produce una sola estrazione, quella del
   post-hook sul run completo;
2. `run → pausa → continue_run` riprende lo stesso run, esegue lo strumento
   dopo la conferma e non prima, e conserva il file dopo un rifiuto;
3. il retry di `AresSessionContextStore` ripete solo l'estrazione che non ha
   scritto e si ferma appena scrive, nei percorsi sincrono e asincrono e nei
   tre casi — primo colpo, tetto raggiunto, recupero — e l'`aprocess`
   anticipata di Ares non estrae nulla;
4. profilo e memorie rifiutano `PROPOSE` e `HITL`, motivo per cui la memoria
   durevole non passa da una conferma;
5. la versione di Agno citata nei documenti (`FILE_CHE_DICHIARANO`) è quella
   installata; `CHANGELOG.md` e `docs/memory-quality.md` sono esclusi perché
   citano le versioni di allora.

Il retry mostra come si divide il lavoro con le prove con Ollama: qui si
prova, in modo deterministico, che funziona come scritto;
`learning_reliability_test.py` misura quanto spesso serve davvero.

### `ambiti`

Tiene ferme le due premesse dello
[studio sugli ambiti](project-scopes.md#34-esito-delle-due-verifiche-preliminari).
Non prova la proposta, che non esiste ancora: se Agno cambia una premessa, il
fallimento arriva qui e il messaggio nomina la pagina da aggiornare.

- **Provenienza delle memorie.** Una chiave in più in una voce di memoria si
  scrive con le API pubbliche, sopravvive a un `update_memory`, si
  riallinea con la stessa scrittura e **non** arriva al modello: il prompt di
  estrazione porta solo `{id, content}`, quindi la classificazione non può
  essere del modello.
- **Filtro per namespace delle intuizioni.** Con un embedder a vettori
  fissi, il filtro per namespace è applicato sui metadati *dopo* il limite:
  con trenta documenti fuori ambito più vicini di cinque in ambito, la
  ricerca restituisce zero con limite 5 e con limite 30, e tutti solo quando
  il limite supera i fuori ambito. Il filtro dell'owner è invece una
  clausola del motore e restituisce cinque su cinque.

### `costo`

Con un modello finto che conta le chiamate e scrive: tre chiamate al modello
di apprendimento per turno, una per store, per 19.970 caratteri, due terzi
dei quali istruzioni. Spegnere uno store toglie esattamente una chiamata; un
modello che non chiama lo strumento costa i tentativi del contesto, quindi
più di uno che scrive. Un quarto controllo verifica che profilo e memorie
vengano comunque scritti: `stop_after_tool_call` toglie la chiamata di
chiusura, non la scrittura. Numeri e opzioni in
[qualità della memoria](memory-quality.md#costo-delle-estrazioni-22-settembre-2026);
la misura con i modelli veri la stampa `e2e`.

### `backup`

Oltre al protocollo della sonda LanceDB simulato, esegue la sonda vera con
`-m ares.backup.probe`: è l'unico modo di sapere che il figlio dica ciò che
il genitore crede e che il modulo sia avviabile in un altro interprete. La
prova verifica ciò che il figlio decide da sé (uso sbagliato, traduzione di
un'eccezione in codice e messaggio), non come fallisce LanceDB, che si
comporta diversamente su Linux e Windows.

### `valutazione`

Ventinove controlli sui verdetti del benchmark: una citazione negativa,
ritagliata o contraddetta non passa; un recupero pretende un'evidenza
durevole e non il contesto della sessione; un dato inventato fallisce invece
di restare non conclusivo. In più l'isolamento degli archivi del worker e i
guasti (timeout, Ctrl+C, processo senza rapporto), che devono conservare le
fasi già scritte. Gira in due decimi di secondo perché i dialoghi sono già
scritti.

### Garanzie trasversali

- **Nessun modello acceso di nascosto.** `cli_test.py` punta
  `config.OLLAMA_HOST` a una porta chiusa: su una macchina di sviluppo
  Ollama è spesso acceso, e una prova potrebbe usarlo e poi fallire in CI.
  La leva non arriva ai processi figli, quindi la REPL in un processo
  separato riceve solo comandi `/`, e un'asserzione verifica che nessun
  turno col modello sia stato aperto.
- **Nessuna scrittura su disco all'import.** Importare `config` (`smoke`) e
  `--help` di ognuno dei sette comandi (`cli`) non creano niente. Girano in
  processi nuovi, perché un modulo si importa una volta sola.
- **Terminale.** Le prove del terminale simulato impostano un ambiente Rich
  proprio, così `TERM=dumb`, `NO_COLOR` o la CI non cambiano le
  precondizioni; una prova separata verifica l'assenza di controlli ANSI su
  un terminale `dumb`.
- **Conservazione dei dati.** Il fallimento della copia iniziale del restore
  Windows, prima e dopo aver copiato un file, lascia intatto l'originale.
  La CLI usa store Agno reali su SQLite temporaneo per verificare che un
  rifiuto conservi le memorie precedenti, anche dopo errori e Ctrl-C;
  processi figli verificano la contesa dello stesso utente e l'indipendenza
  di utenti diversi durante istantanea, turno, conferma e ripristino, il
  rifiuto prima dell'inferenza, il rilascio del lock e gli esiti della
  contesa nella REPL e in pipe.
- **Timeout.** Il runner interrompe una prova offline dopo sei minuti e una
  con Ollama dopo quindici: sono limiti di sicurezza contro deadlock, non
  tempi attesi. Un superamento appare nel riepilogo come fallimento.

## Copertura

```bash
.venv/bin/python tests/run.py --copertura
.venv/bin/python tests/run.py --copertura --html   # rapporto navigabile
```

La configurazione sta in `pyproject.toml`, così il numero non dipende da come
è stato invocato il comando. La modalità parallela è obbligatoria: ogni
processo scrive il proprio file e `coverage combine` li unisce. La misura è
per ramo, perché qui la sostanza sono i rami — percorsi Windows, gestori
d'errore, ripieghi.

Non esiste una soglia minima: una soglia si difende scrivendo prove dove
costa meno, non dove serve di più. Il rapporto serve a sapere quale ramo non
è mai stato eseguito.

**Processi figli.** Le prove lanciano molti sottoprocessi (la CLI di
`ares.entities`, la sonda LanceDB, la rilettura di `e2e_test.py`), e
`coverage` di suo misura solo il processo che avvia. L'aggancio è
`tests/_copertura/sitecustomize.py`, che il runner attiva con
`COVERAGE_PROCESS_START` solo quando misura, insieme a un `COVERAGE_FILE`
assoluto, perché un figlio scriverebbe la misura nella propria cartella
usa-e-getta. Un figlio va lanciato come modulo (`-m ares.…`), non per
percorso: un file eseguito per percorso è `__main__` e la misura, che segue
il package `ares`, non lo vede.

**Misura del 25 settembre 2026** (solo offline, Linux, Python 3.12.3): 91%,
con 4.436 istruzioni, 306 scoperte e 1.364 rami. Fra i moduli: `cli/chat.py`
95%, `agent/echo.py` 96%, `state/lock.py` e `cli/comando.py` 100%,
`backup/restore.py` 87%, `cli/ui.py` 84%, `cli/commands.py` 80%,
`cli/conferma.py` 77% (resta scoperta la domanda con l'editor di Prompt
Toolkit, disponibile solo su un terminale vero; le prove passano dal ripiego
`input()`). Sono misure, non soglie: una percentuale alta non dimostra che
siano coperti tutti gli interleaving fra chat o tutti i punti in cui una
copia può fallire.

Lo scoperto è quasi tutto gestori d'errore e rami di piattaforma. Nessuna
di quelle righe richiede un modello vero: `--tutte --copertura` produce un
rapporto identico, riga per riga e ramo per ramo. Le prove con Ollama
verificano ciò che un modello finto non può dire, non allargano la
copertura.

## Analisi statica

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy . tests/run.py tests/_comune.py tests/_doppi.py
```

Non eseguono il codice e non toccano nessun archivio. Ruff copre tutto.
Mypy copre i moduli e il cablaggio delle prove, non le singole prove: quelle
girano a ogni CI, quindi un errore di tipo lì fallisce subito, mentre nei
moduli restano rami che nessuna prova attraversa. Il cablaggio (runner e
moduli condivisi) non lo esegue nessuna prova, e un errore lì si vedrebbe
solo quando una prova non parte più.

## Prove con Ollama

```bash
.venv/bin/python tests/run.py --tutte
```

Richiedono Ollama e i modelli dichiarati in `ares/config.py`:

- `learning_reliability_test.py` misura l'estrazione del contesto e i retry;
- `learned_knowledge_test.py` accende embedder e modello principale per
  provare salvataggio e riuso delle intuizioni;
- `e2e_test.py` esegue un turno completo e verifica l'apprendimento
  persistente da un nuovo processo.

I comandi mostrano il percorso Linux. Su Windows sostituisci
`.venv/bin/python` con `.\.venv\Scripts\python.exe`.

## CI

GitHub Actions esegue tre job:

- **`Cosa cambia`** guarda quali file tocca il commit e salta i passi
  pesanti quando sono solo documenti;
- **`Analisi statica`** gira una volta su Ubuntu con ruff e mypy;
- **`tests`** installa le dipendenze bloccate, verifica lo script di setup,
  compila il codice e lancia `tests/run.py --copertura` su Ubuntu con
  Python 3.12 e 3.13 e su Windows con la 3.12.

Su Windows l'ambiente nasce da `setup.ps1 -SkipPreflight`, così la CI
verifica anche l'installazione senza Ollama; un secondo `uv sync --locked`
aggiunge `coverage`, che lo script di proposito non installa. La misura
anche su Windows serve perché i rami Windows di `backup` e `platform_files`,
misurati solo su Ubuntu, risulterebbero scoperti.

La variante 3.13 è l'altro estremo di `requires-python` e non è fra i
controlli obbligatori del ruleset: è un canarino sull'interprete, come CodeQL
e `Audit` lo sono sul codice e sulle dipendenze. Le prove con Ollama restano
locali perché richiedono modelli e hardware dedicato.
