# Strategia di test

La suite separa le verifiche offline dalle prove che richiedono Ollama. Ogni
test reindirizza stato, backup e workspace verso directory temporanee, senza
toccare i dati del clone in uso.

## Runner

```bash
.venv/bin/python tests/run.py             # le prove offline
.venv/bin/python tests/run.py --tutte     # anche quelle con Ollama
.venv/bin/python tests/run.py --copertura # offline, con la misura
.venv/bin/python tests/run.py --solo backup entita
```

`tests/run.py` non importa le prove: le lancia, una per processo. Non è una
preferenza di stile. Ogni prova scrive `ARES_TMP`, `ARES_BACKUP_DIR` e
`ARES_WORKSPACE` **prima** di importare `config`, che quelle variabili le
legge una volta sola all'import e non le rilegge mai più. Due prove nello
stesso interprete condividerebbero il primo `config` importato — cioè i
percorsi della prima — e il giorno in cui una sbagliasse variabile
scriverebbe nell'archivio vero senza che nessuno se ne accorga. Un processo
per prova rende quell'errore impossibile invece che improbabile.

Quel gesto, e le poche righe che ogni prova ripeteva uguali, stanno in
`tests/_comune.py`: `prepara_ambiente` sceglie i percorsi usa-e-getta e
rifiuta di farlo se `config` è già in memoria; `esigi` è l'asserzione che
`python -O` non toglie; `esegui` e `fallimento` stampano una riga per
controllo e, quando un controllo fallisce, la riga da cui viene — con il
traceback intero se non è un'asserzione ma un guasto che la prova non
prevedeva. Il modulo non importa niente di `ares`, ed è l'unica garanzia
che i percorsi vengano decisi prima che `config` li legga.

Le prove restano eseguibili una per una, come prima. L'elenco però vive in un
posto solo, la tabella `PROVE` in `tests/run.py`: la CI chiama il runner,
quindi una prova nuova entra in CI registrandola lì e non ricordandosi di
aggiungere un passo al workflow.

## Copertura

```bash
.venv/bin/python tests/run.py --copertura
.venv/bin/python tests/run.py --copertura --html   # rapporto navigabile
```

La configurazione sta in `pyproject.toml`, così il numero non dipende da come è
stato invocato il comando. La modalità parallela è obbligatoria per lo stesso
motivo per cui le prove sono processi separati: ognuna scrive il proprio file
e `coverage combine` li unisce alla fine. La misura è per ramo e non solo per
riga: qui la sostanza sono i rami — i percorsi Windows, i gestori d'errore,
i ripieghi — e una riga `if` eseguita in un verso solo è mezza provata.

Non esiste una soglia minima, e non è una dimenticanza. Una soglia si difende
scrivendo prove dove costa meno, non dove serve di più. Il rapporto serve a
rispondere a una domanda diversa: quale ramo non è mai stato eseguito.

Con le sole prove offline la misura è intorno all'88%. `cli/chat.py` era il
modulo più scoperto, al 61%, quando il turno conversazionale si attraversava
solo con Ollama; da quando `chat turno` lo percorre con un `run_turn_cycle`
finto è al 100%. Il meno coperto oggi è `cli/commands.py`, al 76%: i rami dei
comandi locali che leggono gli archivi.

La misura segue anche i processi figli, e senza questo mentirebbe in difetto:
le prove ne lanciano parecchi — la CLI di `ares.entities` sei volte,
il sondaggio LanceDB isolato di `backup/probe.py`, la rilettura da un secondo
interprete in `e2e_test.py`. `coverage` misura il processo che avvia, non i
suoi discendenti, e prima dell'aggancio `entities/maintenance.py` risultava al
68% pur avendo la propria CLI provata da sei sottoprocessi: il rapporto
mandava a scrivere prove per righe che ne avevano già una. L'aggancio è
`tests/_copertura/sitecustomize.py`, che Python importa da sé all'avvio di
ogni interprete e che il runner attiva con `COVERAGE_PROCESS_START` solo
quando misura.

Ciò che resta scoperto è quasi tutto composto da gestori d'errore e da rami
di piattaforma: i percorsi Windows su una macchina Linux, i ripieghi per un
disco in sola lettura, le eccezioni che nessuno ha mai visto sollevare. Le
righe che solo un modello vero attraversa — il salvataggio e il riuso delle
intuizioni, un turno intero contro Ollama — le coprono le prove con Ollama,
che qui non girano: `--tutte` alza il numero.

Il retry del contesto stava in quell'elenco e ne è uscito: la sua logica la
prova ora `contratto`, offline e in modo deterministico, mentre
`learning_reliability_test.py` resta a misurare ciò che solo un modello vero
può dire, cioè *quanto spesso* l'estrazione manca il colpo. È la divisione
giusta fra le due: una risponde "il retry funziona come scritto", l'altra
"serve davvero, e quanto".

## Analisi statica

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy .
```

Non eseguono il codice e non toccano nessun archivio. Mypy copre i moduli e
non le prove: quelle girano a ogni CI, quindi un errore di tipo lì diventa
subito un fallimento visibile, mentre nei moduli restano rami — i percorsi
Windows, i gestori d'errore — che nessuna prova attraversa. Ruff copre tutto.

## Verifiche offline

```bash
.venv/bin/python tests/run.py
```

Sono sette. `smoke` costruisce l'agente e semina gli store, e controlla
assemblaggio, isolamento, lock, propagazione simulata del run completo alla
macchina di apprendimento e l'eco di ciò che entra in memoria. `repl` prova
ciò che della chat gira senza l'agente: conferme lette e applicate, esito e
metriche degli strumenti, rendering Rich su pipe e su un terminale simulato
con i controlli filtrati, core del turno con eventi fabbricati, log di Agno,
cronologia privata, editor con completamento e multilinea, Ctrl-C/D e
comandi locali. Stava nello smoke, che era diventato il posto dove finiva
ogni prova offline; la divisione segue ciò che serve per girare.
`sessioni` attraversa un vero `Agent.run()` con modello deterministico e
verifica offload, quota, retention, cascata e restore dei due SQLite.
`contratto` chiede ad Agno le quattro cose che Ares dà per vere del
framework: che un turno con pausa per conferma produca una sola estrazione,
quella del post-hook sul run completo; che `run → pausa → continue_run`
riprenda lo stesso run, eseguendo lo strumento dopo la conferma e non prima e
conservando il file dopo un rifiuto; che il retry di
`AresSessionContextStore` ripeta solo l'estrazione che non ha scritto e si
fermi appena scrive, sul percorso sincrono e su quello asincrono; e che
profilo e memorie continuino a rifiutare le modalità `PROPOSE` e `HITL`, che
è il motivo per cui la memoria durevole non passa da una conferma.
Il modello è lo stesso copione deterministico. Nei primi due controlli gli
store di apprendimento sono spenti e si conta il passaggio, non ciò che
scriverebbe; il terzo lo store lo costruisce davvero, perché lì la domanda è
proprio se ha scritto.
`backup` copre snapshot, checksum, restore e prune; `entita` l'audit e la
fusione. `cli` prova i comandi con cui Ares si usa davvero: il preflight
contro un server Ollama finto nei tre esiti, l'ispezione degli archivi, i
sottocomandi di `ares.backup` con i loro annullamenti, e la REPL intera in un
processo separato con stdin da una pipe.

Nessuna genera risposte con il modello. `cli_test.py` lo rende esplicito
puntando `config.OLLAMA_HOST` a un porto chiuso: su una macchina di sviluppo
Ollama è spesso acceso, e senza quella riga una prova potrebbe usarlo di
nascosto e passare qui per fallire in CI. La leva non arriva però ai processi
figli — `OLLAMA_HOST` è una costante, non una variabile d'ambiente — quindi
la REPL provata in un processo separato riceve solo righe che cominciano con
`/`, e una asserzione verifica che nessun turno col modello sia stato aperto.

Il runner interrompe una prova offline dopo tre minuti e una prova con Ollama
dopo quindici. Non sono tempi attesi ma limiti di sicurezza: evitano che un
deadlock o una dipendenza bloccata consumino indefinitamente il terminale o
l'intero timeout della CI. Un superamento appare nel riepilogo come fallimento.

Due controlli sorvegliano un'invariante che nessun'altra prova vedrebbe:
importare `config` non deve creare niente su disco (`smoke`), e `--help` di
ognuno dei cinque comandi nemmeno (`cli`). Entrambi girano in processi nuovi,
perché un modulo si importa una volta sola. Sono la rete sotto una riga
spostata di due caratteri: `prepara_archivio()` chiamata dopo `parse_args()`
invece che prima, che è tutta la differenza fra un `--help` che lascia un
archivio e uno che non lascia niente.

La CI esegue le stesse sette prove sia su Ubuntu sia su Windows. Sul
runner Windows l'ambiente nasce direttamente da `setup.ps1 -SkipPreflight`,
così la CI verifica anche il percorso d'installazione senza richiedere
Ollama.

## Prove con Ollama

```bash
.venv/bin/python tests/run.py --tutte
```

Queste prove richiedono Ollama e i modelli dichiarati in `ares/config.py`:
`learning_reliability_test.py` misura l'estrazione del contesto e i retry,
`learned_knowledge_test.py` accende embedder e modello principale per provare
salvataggio e riuso delle intuizioni, mentre `e2e_test.py` esegue un turno
completo e verifica l'apprendimento persistente da un nuovo processo.

I comandi mostrano il percorso Linux. Su Windows sostituisci
`.venv/bin/python` con `.\.venv\Scripts\python.exe`.

## CI

GitHub Actions esegue due job. `Analisi statica` gira una volta su Ubuntu con
ruff e mypy; `tests` installa le dipendenze bloccate, verifica lo script di
setup, compila il codice e lancia `tests/run.py` su Ubuntu e Windows — con
`--copertura` solo su Ubuntu, perché la copertura di un progetto non dipende
dal sistema. Le prove con Ollama restano intenzionalmente locali perché
richiedono modelli e hardware dedicato.
