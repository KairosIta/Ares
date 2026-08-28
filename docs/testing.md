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
legge una volta sola all'import e in base a quelle crea directory su disco.
Due prove nello stesso interprete condividerebbero il primo `config`
importato — cioè l'archivio della prima — e il giorno in cui una sbagliasse
variabile scriverebbe nell'archivio vero senza che nessuno se ne accorga. Un
processo per prova rende quell'errore impossibile invece che improbabile.

Le prove restano eseguibili una per una, come prima. L'elenco però vive in un
posto solo, la tabella `PROVE` in `tests/run.py`: la CI chiama il runner,
quindi una prova nuova entra in CI registrandola lì e non ricordandosi di
aggiungere un passo al workflow.

## Copertura

```bash
.venv/bin/python tests/run.py --copertura
.venv/bin/python tests/run.py --copertura --html   # rapporto navigabile
```

La configurazione sta in `.coveragerc`, così il numero non dipende da come è
stato invocato il comando. La modalità parallela è obbligatoria per lo stesso
motivo per cui le prove sono processi separati: ognuna scrive il proprio file
e `coverage combine` li unisce alla fine.

Non esiste una soglia minima, e non è una dimenticanza. Una soglia si difende
scrivendo prove dove costa meno, non dove serve di più. Il rapporto serve a
rispondere a una domanda diversa: quale ramo non è mai stato eseguito.

Con le sole prove offline la misura è intorno all'88%, e nessun modulo sta
sotto il 79%.

La misura segue anche i processi figli, e senza questo mentirebbe in difetto:
le prove ne lanciano parecchi — la CLI di `entity_maintenance.py` sei volte,
il sondaggio LanceDB isolato di `backup.py`, la rilettura da un secondo
interprete in `e2e_test.py`. `coverage` misura il processo che avvia, non i
suoi discendenti, e prima dell'aggancio `entity_maintenance.py` risultava al
68% pur avendo la propria CLI provata da sei sottoprocessi: il rapporto
mandava a scrivere prove per righe che ne avevano già una. L'aggancio è
`tests/_copertura/sitecustomize.py`, che Python importa da sé all'avvio di
ogni interprete e che il runner attiva con `COVERAGE_PROCESS_START` solo
quando misura.

Ciò che resta scoperto è quasi tutto composto da gestori d'errore e da rami
di piattaforma: i percorsi Windows su una macchina Linux, i ripieghi per un
disco in sola lettura, le eccezioni che nessuno ha mai visto sollevare. Il
turno conversazionale di `chat.py` è coperto dalle prove con Ollama, che qui
non girano: `--tutte` alza il numero.

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

Sono quattro. `smoke` controlla assemblaggio dell'agente, isolamento degli
store, lock e propagazione simulata del run completo alla macchina di
apprendimento, e usa un terminale simulato per verificare streaming Rich,
completamento, multilinea, Ctrl-C/D e cronologia senza interazione umana.
`backup` copre snapshot, checksum, restore e prune; `entita` l'audit e la
fusione. `cli` prova i comandi con cui Ares si usa davvero: il preflight
contro un server Ollama finto nei tre esiti, l'ispezione degli archivi, i
sottocomandi di `backup.py` con i loro annullamenti, e la REPL intera in un
processo separato con stdin da una pipe.

Nessuna genera risposte con il modello. `cli_test.py` lo rende esplicito
puntando `config.OLLAMA_HOST` a un porto chiuso: su una macchina di sviluppo
Ollama è spesso acceso, e senza quella riga una prova potrebbe usarlo di
nascosto e passare qui per fallire in CI.

La CI esegue le stesse quattro prove sia su Ubuntu sia su Windows. Sul runner Windows crea l'ambiente direttamente con
`setup.ps1 -SkipPreflight`, verificando il percorso d'installazione senza
richiedere Ollama.

## Prove con Ollama

```bash
.venv/bin/python tests/run.py --tutte
```

Queste prove richiedono Ollama e i modelli dichiarati in `config.py`:
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
