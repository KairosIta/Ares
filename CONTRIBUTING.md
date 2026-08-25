# Contribuire ad Ares

Grazie per l’interesse verso Ares. Il progetto privilegia modifiche piccole,
verificabili e motivate da un rischio o da un comportamento osservato.

## Ambiente

Servono Python 3.12, `uv` e Ollama. Dopo aver scaricato i modelli indicati nel
README:

```bash
./setup.sh
```

Le dipendenze dirette vivono in `requirements.in`; `requirements.txt` è il
lock completo. Se cambia una dipendenza diretta, rigenera il lock con:

```bash
uv pip compile requirements.in -o requirements.txt
```

## Flusso consigliato

1. Apri una issue o descrivi chiaramente il comportamento da cambiare.
2. Crea una branch breve a partire da `main`.
3. Mantieni separati refactor, funzionalità e documentazione.
4. Aggiungi una prova capace di fallire sul difetto corretto.
5. Esegui i controlli pertinenti e descrivi cosa non è stato verificato.

## Verifiche minime

```bash
.venv/bin/python tests/smoke_test.py
.venv/bin/python tests/backup_test.py
.venv/bin/python tests/entity_maintenance_test.py
```

Le modifiche al percorso conversazionale o di apprendimento richiedono anche:

```bash
.venv/bin/python -u tests/e2e_test.py
```

Tutte le prove devono usare archivi temporanei. Non leggere, copiare o
committare lo stato reale in `tmp/`, i workspace o gli snapshot locali.

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
