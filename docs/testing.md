# Strategia di test

La suite separa le verifiche offline dalle prove che richiedono Ollama. Ogni
test reindirizza stato, backup e workspace verso directory temporanee, senza
toccare i dati del clone in uso.

## Verifiche offline

```bash
.venv/bin/python tests/smoke_test.py
.venv/bin/python tests/backup_test.py
.venv/bin/python tests/entity_maintenance_test.py
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
.venv/bin/python tests/learning_reliability_test.py
.venv/bin/python -u tests/learned_knowledge_test.py
.venv/bin/python -u tests/e2e_test.py
```

Queste prove richiedono Ollama e i modelli dichiarati in `config.py`:
`learning_reliability_test.py` misura l'estrazione del contesto e i retry,
`learned_knowledge_test.py` accende embedder e modello principale per provare
salvataggio e riuso delle intuizioni, mentre `e2e_test.py` esegue un turno
completo e verifica l'apprendimento persistente da un nuovo processo.

I comandi mostrano il percorso Linux. Su Windows sostituisci
`.venv/bin/python` con `.\.venv\Scripts\python.exe`.

## CI

GitHub Actions installa le dipendenze bloccate, verifica lo script di setup,
compila il codice e lancia la suite offline. Le prove con Ollama restano
intenzionalmente locali perché richiedono modelli e hardware dedicato.
