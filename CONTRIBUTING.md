# Contribuire ad Ares

Grazie per l’interesse verso Ares. Il progetto privilegia modifiche piccole,
verificabili e motivate da un rischio o da un comportamento osservato.

## Ambiente

Servono Python 3.12 (quella installata dagli script di setup) o 3.13, `uv` e
Ollama. Dopo aver scaricato i modelli indicati nel README, prepara
l’ambiente su Linux con:

```bash
./setup.sh
```

oppure su Windows PowerShell con:

```powershell
.\setup.ps1
```

`setup.sh` installa senza il gruppo `dev`. Per avere anche ruff, mypy e
coverage nel venv, come fa la CI:

```bash
uv sync --locked
```

Il venv contiene anche Ares stesso, installato in editable: i comandi `ares`,
`ares-backup`, `ares-entities`, `ares-sessions`, `ares-preflight` e
`ares-inspect` seguono le modifiche ai sorgenti senza reinstallare niente.

### Dipendenze

Ares è un'applicazione, quindi la riproducibilità sta tutta nel lock:

- **`uv.lock`** è committato, con versione esatta e hash SHA-256 di ogni
  artefatto; setup e CI installano con `uv sync --locked`. Un pin dice quale
  versione, un hash dice quale file: se un artefatto viene ripubblicato su
  PyPI l'installazione si ferma invece di riuscire.
- **`pyproject.toml`** elenca le dipendenze dirette senza versione, ognuna
  con il motivo per cui c'è. Un `<` si mette solo per un'incompatibilità
  nota, scritta accanto: oggi c'è solo `agno>=3.0.2,<3.1`, perché le API di
  `agno.learn` cambiano tra minor.

Le versioni cambiano solo quando esegui `uv lock`, che è conservativo:
aggiunge o toglie ciò che il pyproject chiede e lascia il resto dov'è. Per
aggiornare un pacchetto di proposito usa `uv lock --upgrade-package <nome>`;
senza il nome aggiorna tutto, e un lock che cambia in cinquanta righe per
un pacchetto solo è il segno di un errore.

## Flusso consigliato

1. Apri una issue o descrivi chiaramente il comportamento da cambiare.
2. Crea una branch breve a partire da `main`.
3. Mantieni separati refactor, funzionalità e documentazione.
4. Aggiungi una prova capace di fallire sul difetto corretto.
5. Esegui i controlli pertinenti e descrivi cosa non è stato verificato.
6. Firma i commit e chiedi il merge con `gh pr merge --merge` (vedi
   [Firma dei commit](#firma-dei-commit)).

## Verifiche minime

I comandi mostrano il percorso Linux. Su Windows usa
`.\.venv\Scripts\python.exe` al posto di `.venv/bin/python`.

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy . tests/run.py tests/_comune.py tests/_doppi.py
.venv/bin/python tests/run.py
```

I primi tre sono il job `Analisi statica` della CI. Regole attive ed
esclusioni, con il loro motivo, stanno in `pyproject.toml`: se una regola ti
sembra sbagliata, discutila lì invece di aggiungere `noqa` sparsi.

Le modifiche al percorso conversazionale o di apprendimento richiedono anche
le prove con Ollama:

```bash
.venv/bin/python tests/run.py --tutte
```

Il runner elenca le prove con `--help` e ne esegue una sola con
`--solo <nome>`. Una prova nuova va registrata nella tabella `PROVE` di
`tests/run.py`: è l'unico elenco, e la CI legge quello.

Le prove con Ollama non girano in CI: i runner di GitHub non hanno una GPU.
**Tre prove su quattordici esistono quindi solo se qualcuno le lancia.**
Quando le esegui prima di un bump di Agno o di un rilascio, scrivilo nella
voce del CHANGELOG, con data e versione di Agno:

```markdown
- prove con Ollama (`--tutte`) verdi il 2026-09-05 su Agno 3.0.5, modello
  conversazionale locale.
```

È l'unico pezzo della catena che non si può dimostrare da fuori, quindi va
dichiarato.

La copertura si misura con `.venv/bin/python tests/run.py --copertura`
(`--html` per il rapporto navigabile). Non c'è una soglia: serve a sapere
quale ramo non è mai stato eseguito.

Tutte le prove devono usare archivi temporanei. Non leggere, copiare o
committare lo stato reale in `~/.ares`, i workspace o gli snapshot locali.
La CI deve restare verde sia su Ubuntu sia su Windows prima del merge.

## Firma dei commit

Commit e tag di Ares si firmano con una chiave SSH dedicata, configurata
**solo in questo repository**, perché il `user.email` globale della
macchina può appartenere a un'altra identità:

```bash
ssh-keygen -t ed25519 -N "" -C "<email dell'account>" -f ~/.ssh/ares_signing_ed25519
git config --local gpg.format ssh
git config --local user.signingkey ~/.ssh/ares_signing_ed25519.pub
git config --local commit.gpgsign true
git config --local tag.gpgsign true
```

La chiave è senza passphrase perché la firma deve avvenire senza prompt; con
una passphrase serve `ssh-agent` sbloccato. La chiave pubblica va registrata
su GitHub come **Signing key** (Settings → SSH and GPG keys → New SSH key →
Key type: *Signing key*), non come chiave di autenticazione. Finché non è
registrata, un oggetto firmato risulta `unknown_key`; dopo la registrazione
GitHub rivaluta anche gli oggetti già pushati e li marca *Verified*.

Per verificare senza rete serve `.git/allowed_signers`, non tracciato:

```bash
printf '%s %s\n' "<email dell'account>" "$(cat ~/.ssh/ares_signing_ed25519.pub)" > .git/allowed_signers
git config --local gpg.ssh.allowedSignersFile "$PWD/.git/allowed_signers"
```

Poi `git log --show-signature` e `git tag -v v0.8.0` rispondono
*Good signature*, con l'impronta della chiave.

**Il merge non deve riscrivere i commit.** «Rebase and merge» ricrea i
commit lato GitHub, che non ha la chiave privata, e la firma va persa
([documentato da GitHub](https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification#signature-verification-for-rebase-and-merge)).
I PR si mergiano quindi con `--merge`: i commit arrivano su `main` identici
e GitHub aggiunge un merge commit firmato da lui. Se `main` si è mosso, fai
rebase in locale: `commit.gpgsign` ri-firma i commit ricreati.

## Come si rilascia

La versione sta in `pyproject.toml` e va ripetuta nella voce nuova del
`CHANGELOG`, nella riga supportata di `SECURITY.md` e nei collegamenti di
confronto in coda al `CHANGELOG` (`uv.lock` lo allinea `uv lock`).
`tests/rilascio_test.py` li confronta tutti con `pyproject.toml` e nomina
il file da correggere.

1. Alza la versione in `pyproject.toml` ed esegui `uv lock`: l'ultimo
   numero per una correzione, il secondo per una funzionalità, il primo per
   un cambiamento incompatibile. L'interfaccia sono `docs/` e la CLI, non i
   moduli interni.
2. Trasforma `## [Unreleased]` in `## [x.y.z] - aaaa-mm-gg` e lascia
   `[Unreleased]` vuota sotto. In coda aggiungi
   `[x.y.z]: https://github.com/KairosIta/Ares/compare/v<precedente>...v<x.y.z>`
   e porta `[Unreleased]` a `.../compare/v<x.y.z>...HEAD`.
3. Nella voce scrivi la riga della verifica con Ollama, come in
   [Verifiche minime](#verifiche-minime).
4. Se cambia la linea supportata, aggiorna `SECURITY.md` a `x.y.x` e la riga
   non supportata a `< x.y`. Una patch non sposta la riga.
5. Esegui la catena completa — `ruff check`, `ruff format --check`, `mypy`,
   `tests/run.py --copertura` — e le prove con Ollama se il rilascio tocca il
   percorso conversazionale o di apprendimento.
6. Apri il PR e mergialo con `--merge`, mai con `--rebase`.
7. Sul commit mergiato crea il tag **annotato** (firmato, grazie a
   `tag.gpgsign`) e pubblica la release:

   ```bash
   git fetch origin --prune && git switch main && git pull --ff-only
   git tag -a v0.8.0 -m "Ares 0.8.0"
   git push origin v0.8.0
   gh release create v0.8.0 --title "Ares v0.8.0" --notes-file tmp/nota.md
   ```

   Il tag viene **dopo** il merge: il ruleset non accetta push diretti su
   `main`, e un tag creato prima punterebbe a un commit che la release non
   contiene. La nota si scrive a mano, nella forma della 0.8.0: un
   riassunto, «Cosa cambia», «Compatibilità», «Verifica» e in fondo il
   confronto `Full Changelog`.

## Stile

- codice e identificatori Python chiari e semplici;
- interfaccia, documentazione e messaggi utente in italiano;
- commenti dedicati al perché, non alla traduzione letterale del codice.
  Una docstring dice il contratto — cosa fa, i parametri non ovvi, le
  invarianti — e il perché solo quando non è ovvio. La storia di una
  modifica («prima era…») va nel commit e nel CHANGELOG, non nel codice;
- accenti veri nei documenti Markdown: è, ciò, perché. Apostrofo ASCII
  (`e'`, `cio'`, `perche'`) nel codice, nei commenti, nei messaggi a
  terminale, nei prompt e nei file di configurazione. Il confine è il tipo
  di file, non l'argomento;
- commit nel formato `tipo: descrizione`, per esempio `fix:`, `feat:`,
  `test:`, `docs:` o `refactor:`.

## Sicurezza e licenza

Per vulnerabilità segui [SECURITY.md](SECURITY.md), senza aprire dettagli
pubblici. Inviando un contributo accetti che venga distribuito sotto
[Apache License 2.0](LICENSE).
