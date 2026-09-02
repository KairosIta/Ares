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

Le dipendenze dirette, con il motivo di ciascuna, stanno in `pyproject.toml`;
`uv.lock` è il lock completo, con gli hash di ogni artefatto. Se cambia una
dipendenza diretta, rigenera il lock con:

```bash
uv lock
```

`uv lock` è conservativo: aggiunge o toglie ciò che il pyproject chiede e
lascia le altre versioni dove sono. Per aggiornare una dipendenza di proposito
c'è `uv lock --upgrade-package <nome>`; senza il nome aggiorna tutto, e un
lock che cambia in cinquanta righe per un pacchetto solo è il segno che è
successo per sbaglio.

Gli hash valgono per il motivo per cui esiste un lock. Un pin dice quale
versione installare; un hash dice quale artefatto. Se un account su PyPI viene
compromesso e un file ripubblicato, `agno 3.0.2` resta vero e il contenuto
cambia: con gli hash l'installazione si ferma invece di riuscire. Vale su ogni
macchina che esegue `setup.sh` e su ogni PR di Dependabot, che di aggiornamenti
automatici ne apre uno a settimana.

I vincoli nel `pyproject.toml` sono invece larghi, e la divisione dei compiti è
quella che separa un'applicazione da una libreria. Ares è un'applicazione:
`uv.lock` è committato, porta la versione esatta e il suo hash, e setup e CI
installano con `uv sync --locked`. La riproducibilità sta lì per intero, e un
`==` ripetuto nel pyproject non ne aggiunge: aggiunge una seconda copia della
stessa versione da tenere allineata a mano.

Quando aggiungi una dipendenza scrivila quindi senza versione, con accanto il
motivo per cui c'è. Un `<` si mette solo dove c'è un'incompatibilità nota, e
accanto va scritta quale: nel file oggi ce n'è uno solo, `agno>=3.0.2,<3.1`,
perché le API di `agno.learn` cambiano tra minor. Le versioni non si muovono da
sole: cambiano quando esegui `uv lock`, cioè quando lo decidi.

Ruff, mypy e coverage stanno nel gruppo `dev` del pyproject, separati perché
non si importano: si eseguono. `setup.sh` li lascia fuori con `--no-dev`; per
averli nel venv insieme al resto — è quello che fa anche la CI — basta:

```bash
uv sync --locked
```

Il venv contiene anche Ares stesso, installato in editable: i comandi `ares`,
`ares-backup`, `ares-entities`, `ares-sessions`, `ares-preflight` e
`ares-inspect` seguono le modifiche ai sorgenti senza reinstallare niente.

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
.venv/bin/python tests/run.py
```

I primi tre sono gli stessi comandi del job `Analisi statica` della CI. Le
regole attive e il motivo delle esclusioni stanno in `pyproject.toml`: se una
regola ti sembra sbagliata per questo progetto, discutila lì invece di
aggiungere `noqa` sparsi.

Le modifiche al percorso conversazionale o di apprendimento richiedono anche
le prove pertinenti con Ollama:

```bash
.venv/bin/python tests/run.py --tutte
```

Il runner elenca le prove con `--help` e ne esegue una sola con
`--solo <nome>`. Se aggiungi una prova, registrala nella tabella `PROVE` di
`tests/run.py`: è l'unico elenco, e la CI legge quello.

La copertura si misura con `.venv/bin/python tests/run.py --copertura`
(`--html` per il rapporto navigabile). Non c'è una soglia da rispettare:
serve a sapere quale ramo non è mai stato eseguito, non a produrre un
numero da difendere.

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
