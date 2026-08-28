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

Con le sole prove offline la misura è intorno al 71%. Ciò che resta scoperto
non è distribuito: è quasi tutto lo strato a riga di comando. `preflight.py` e
`inspect_learning.py` non vengono eseguiti da nessuna prova, e i `main()` con
argparse di `backup.py`, `chat.py` ed `entity_maintenance.py` sono coperti
sotto il 10%. Le librerie sotto stanno invece fra l'83% e il 100%:
`pianifica_fusione`, la funzione più lunga del progetto, è al 95%.

I processi che una prova avvia per conto suo — il sondaggio LanceDB isolato di
`backup.py`, la rilettura da un secondo interprete in `e2e_test.py` — non
vengono misurati: `coverage` misura il processo che lancia, non i suoi nipoti.
Il codice che eseguono è comunque coperto dai percorsi diretti, ma il numero
lo sottostima.

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

Queste prove controllano assemblaggio dell'agente, isolamento degli store,
lock, snapshot, restore, fusione delle entita' e propagazione simulata del run
completo alla macchina di apprendimento. Lo smoke test usa inoltre un terminale
simulato per verificare streaming Rich, completamento, multilinea, Ctrl-C/D e
cronologia senza richiedere interazione umana. Non generano risposte con il
modello.

La CI esegue smoke test, backup/restore e manutenzione delle entita' sia su
Ubuntu sia su Windows. Sul runner Windows crea l'ambiente direttamente con
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
