# Strategia di test

La suite separa le verifiche offline dalle prove che richiedono Ollama. Ogni
test reindirizza stato, backup e workspace verso directory temporanee, senza
toccare i dati del clone in uso.

## Verifiche offline

```bash
.venv/bin/python tests/smoke_test.py
.venv/bin/python tests/backup_test.py
.venv/bin/python tests/entity_maintenance_test.py
.venv/bin/python tests/learning_reliability_test.py
.venv/bin/python tests/learned_knowledge_test.py
```

Queste prove controllano assemblaggio dell'agente, isolamento degli store,
lock, snapshot, restore, fusione delle entita' e propagazione del run completo
alla macchina di apprendimento. Non generano risposte con il modello.

## Prova end-to-end

```bash
.venv/bin/python -u tests/e2e_test.py
```

La prova E2E richiede Ollama e i modelli dichiarati in `config.py`. Esegue un
turno reale, verifica l'apprendimento persistente e rilegge il risultato da un
nuovo processo.

## CI

GitHub Actions installa le dipendenze bloccate, esegue il controllo degli
import e lancia la suite offline. L'E2E resta intenzionalmente locale perche'
richiede modelli di grandi dimensioni e hardware dedicato.
