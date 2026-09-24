# Contribuire ad Ares

Grazie per l’interesse verso Ares. Il progetto privilegia modifiche piccole,
verificabili e motivate da un rischio o da un comportamento osservato.

## Ambiente

Servono Python 3.12 — la versione che gli script di setup installano — o
3.13, `uv` e Ollama. Dopo aver scaricato i modelli indicati nel
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
6. Firma i commit e chiedi il merge con `gh pr merge --merge`: il perché, e la
   configurazione che vale solo per questo repository, stanno in
   «Firma dei commit».

## Verifiche minime

I comandi mostrano il percorso Linux. Su Windows usa
`.\.venv\Scripts\python.exe` al posto di `.venv/bin/python`.

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy . tests/run.py tests/_comune.py tests/_doppi.py
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

Quelle con Ollama non girano in CI, e non è una dimenticanza: i runner di
GitHub non hanno una GPU, e una suite che scarica un modello da 9 GB a ogni
push non sarebbe una verifica ma un costo. La conseguenza però va accettata
per intero: **tre prove su quattordici esistono solo se qualcuno le lancia**, e
nessuno se ne accorge se smette di farlo. Perciò, quando le esegui prima di
un bump di Agno o di un rilascio, **scrivilo nella voce del CHANGELOG**, con
la data e la versione di Agno su cui sono passate:

```markdown
- prove con Ollama (`--tutte`) verdi il 2026-09-05 su Agno 3.0.5, modello
  conversazionale locale.
```

Una riga, e serve a una cosa sola: distinguere «le ho eseguite» da «di solito
le eseguo». Il resto della catena — CI verde su due sistemi, lock verificato,
`main` che non accetta merge senza i tre check — è dimostrabile da fuori;
questo pezzo no, e allora va dichiarato.

La copertura si misura con `.venv/bin/python tests/run.py --copertura`
(`--html` per il rapporto navigabile). Non c'è una soglia da rispettare:
serve a sapere quale ramo non è mai stato eseguito, non a produrre un
numero da difendere.

Tutte le prove devono usare archivi temporanei. Non leggere, copiare o
committare lo stato reale in `~/.ares`, i workspace o gli snapshot locali.
La CI deve restare verde sia su Ubuntu sia su Windows prima del merge.

## Firma dei commit

Commit e tag di Ares si firmano con una chiave SSH dedicata, e la
configurazione sta **solo in questo repository**:

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
Key type: *Signing key*), non come chiave di autenticazione: sono permessi
diversi, e una chiave può essere registrata due volte se serve anche per il
push. Finché non è registrata, un oggetto firmato riporta `unknown_key` — la
firma c'è, GitHub non conosce la chiave — e non c'è fretta: appena la chiave
compare, GitHub **rivaluta anche gli oggetti già pushati** e li marca
*Verified* con la data della verifica, senza riscrivere niente. Misurato il
22 settembre 2026: il tag `v0.8.0` e il commit `6229fdf`, firmati e pushati
prima della registrazione, sono passati a `valid` da soli.

La scelta di tenerla locale è deliberata: vale per Ares e non per gli altri
repository della macchina, dove il `user.email` globale appartiene a
un'altra identità e produrrebbe commit firmati con la chiave sbagliata.

Per verificare senza rete serve un elenco di firmatari attendibili,
`.git/allowed_signers`, non tracciato come la chiave privata:

```bash
printf '%s %s\n' "<email dell'account>" "$(cat ~/.ssh/ares_signing_ed25519.pub)" > .git/allowed_signers
git config --local gpg.ssh.allowedSignersFile "$PWD/.git/allowed_signers"
```

Poi `git log --show-signature` e `git tag -v v0.8.0` rispondono
*Good signature*, con l'impronta della chiave.

**Il merge non deve riscrivere i commit.** Con «Rebase and merge» GitHub
ricrea i commit dalla copia che ha e li aggiunge **senza verificare la
firma** — non può firmarli lui, perché non ha la chiave privata di chi li ha
scritti — e la firma che c'era va persa; è
[documentato da GitHub](https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification#signature-verification-for-rebase-and-merge).
Non è un'ipotesi: il commit della release 0.8.0, firmato in locale con
*Good signature*, è arrivato su `main` riscritto e `unsigned`. I PR di Ares si
mergiano quindi con `--merge`: i commit del branch arrivano su `main`
identici, quindi ancora firmati, e GitHub aggiunge un merge commit firmato da
lui. Se `main` si è mosso, si fa rebase in locale: `commit.gpgsign` ri-firma i
commit che il rebase ricrea.

## Come si rilascia

La versione sta in `pyproject.toml`, e il repository la ripete in altri tre
posti che devono restare d'accordo: la voce nuova del `CHANGELOG`, la riga
supportata di `SECURITY.md` e i collegamenti di confronto in coda al
`CHANGELOG` (il quarto, `uv.lock`, lo allinea `uv lock`).
`tests/rilascio_test.py` li confronta tutti con `pyproject.toml` e fallisce
nominando il file da correggere. Esiste perché questo passo si dimentica:
la 0.7.1 dichiarava ancora la linea 0.6.x in `SECURITY.md` e la 0.8.0 è
arrivata su `main` con il collegamento di `Unreleased` fermo alla 0.7.1 —
entrambe le volte con la CI verde, che quei file non li legge.

1. Alza la versione in `pyproject.toml` ed esegui `uv lock`. Per una
   correzione l'ultimo numero, per una funzionalità il secondo, per un
   cambiamento incompatibile il primo: `docs/` e la CLI sono l'interfaccia,
   e quel che cambia sotto per chi importa i moduli è già stato pagato una
   volta con la 0.8.0.
2. Trasforma `## [Unreleased]` in `## [x.y.z] - aaaa-mm-gg` e lascia
   `[Unreleased]` vuota sotto. In coda aggiungi
   `[x.y.z]: https://github.com/KairosIta/Ares/compare/v<precedente>...v<x.y.z>`
   e porta `[Unreleased]` a `.../compare/v<x.y.z>...HEAD`.
3. Nella voce scrivi la riga della verifica con Ollama, con la data e la
   versione di Agno su cui è passata, come in «Verifiche minime». È l'unico
   pezzo della catena che non si può dimostrare da fuori.
4. Se il rilascio cambia la linea supportata, aggiorna `SECURITY.md` a
   `x.y.x` e la riga non supportata a `< x.y`. Una patch non sposta la riga.
5. Esegui la catena completa — `ruff check`, `ruff format --check`, `mypy`,
   `tests/run.py --copertura` — e le prove con Ollama se il rilascio tocca il
   percorso conversazionale o di apprendimento.
6. Apri il PR e mergialo con `--merge`, mai con `--rebase`: la firma dei
   commit si perde, e la versione appena scritta sarebbe l'unica cosa
   verificata di tutta la release.
7. Sul commit mergiato, crea il tag **annotato** (`tag.gpgsign` è già attivo,
   quindi firmato) e pubblica la release:

   ```bash
   git fetch origin --prune && git switch main && git pull --ff-only
   git tag -a v0.8.0 -m "Ares 0.8.0"
   git push origin v0.8.0
   gh release create v0.8.0 --title "Ares v0.8.0" --notes-file tmp/nota.md
   ```

   Il tag viene **dopo** il merge: il ruleset non accetta push diretti su
   `main`, e un tag creato prima punterebbe a un commit che la release non
   contiene. La nota segue la forma della 0.8.0 — un riassunto, «Cosa
   cambia», «Compatibilità», «Verifica», e in fondo il confronto
   `Full Changelog` — e si scrive a mano: è il testo che si legge per primo,
   non la copia della voce del `CHANGELOG`.

## Stile

- codice e identificatori Python chiari e semplici;
- interfaccia, documentazione e messaggi utente in italiano;
- commenti dedicati al perché, non alla traduzione letterale del codice;
- accenti veri nei documenti Markdown, che si leggono su GitHub: è, ciò,
  perché. Apostrofo ASCII (`e'`, `cio'`, `perche'`) nel codice, nei
  commenti, nei messaggi a terminale, nei prompt e nei file di
  configurazione, dove il repository è nato così e un diff di mille righe
  di commenti per un accento non vale niente. La riga di confine è il tipo
  di file, non l'argomento;
- commit nel formato `tipo: descrizione`, per esempio `fix:`, `feat:`,
  `test:`, `docs:` o `refactor:`.

## Sicurezza e licenza

Per vulnerabilità segui [SECURITY.md](SECURITY.md), senza aprire dettagli
pubblici. Inviando un contributo accetti che venga distribuito sotto
[Apache License 2.0](LICENSE).
