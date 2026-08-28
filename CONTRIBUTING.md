# Contribuire ad Ares

Grazie per l’interesse verso Ares. Il progetto privilegia modifiche piccole,
verificabili e motivate da un rischio o da un comportamento osservato.

## Ambiente

Servono Python 3.12, `uv` e Ollama. Dopo aver scaricato i modelli indicati nel
README, prepara l’ambiente su Linux con:

```bash
./setup.sh
```

oppure su Windows PowerShell con:

```powershell
.\setup.ps1
```

Le dipendenze dirette vivono in `requirements.in`; `requirements.txt` è il
lock completo. Se cambia una dipendenza diretta, rigenera il lock con:

```bash
uv pip compile --universal requirements.in -o requirements.txt
```

Ruff e mypy stanno in `requirements-dev.in`, separati perché non si importano:
si eseguono. Per averli nel venv insieme al resto — è quello che fa anche la
CI, e `uv pip sync` rimuove ciò che non è nei file che gli passi:

```bash
uv pip sync --python .venv/bin/python requirements.txt requirements-dev.txt
uv pip compile --universal requirements-dev.in -o requirements-dev.txt
```

## Flusso consigliato

1. Apri una issue o descrivi chiaramente il comportamento da cambiare.
2. Crea una branch breve a partire da `main`.
3. Mantieni separati refactor, funzionalità e documentazione.
4. Aggiungi una prova capace di fallire sul difetto corretto.
5. Esegui i controlli pertinenti e descrivi cosa non è stato verificato.

## Verifiche minime

I comandi mostrano il percorso Linux. Su Windows usa
`.\.venv\Scripts\python.exe` al posto di `.venv/bin/python`.

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy .
.venv/bin/python tests/smoke_test.py
.venv/bin/python tests/backup_test.py
.venv/bin/python tests/entity_maintenance_test.py
```

I primi tre sono gli stessi comandi del job `Analisi statica` della CI. Le
regole attive e il motivo delle esclusioni stanno in `ruff.toml` e `mypy.ini`:
se una regola ti sembra sbagliata per questo progetto, discutila lì invece di
aggiungere `noqa` sparsi.

Le modifiche al percorso conversazionale o di apprendimento richiedono anche
le prove pertinenti con Ollama:

```bash
.venv/bin/python tests/learning_reliability_test.py
.venv/bin/python -u tests/learned_knowledge_test.py
.venv/bin/python -u tests/e2e_test.py
```

Tutte le prove devono usare archivi temporanei. Non leggere, copiare o
committare lo stato reale in `tmp/`, i workspace o gli snapshot locali.
La CI deve restare verde sia su Ubuntu sia su Windows prima del merge.

## Stile

- codice e identificatori Python chiari e semplici;
- interfaccia, documentazione e messaggi utente in italiano;
- commenti dedicati al perché, non alla traduzione letterale del codice;
- commit nel formato `tipo: descrizione`, per esempio `fix:`, `feat:`,
  `test:`, `docs:` o `refactor:`.

## Sicurezza e licenza

Per vulnerabilità segui [SECURITY.md](SECURITY.md), senza aprire dettagli
pubblici. Inviando un contributo accetti che venga distribuito sotto
[Apache License 2.0](LICENSE).
