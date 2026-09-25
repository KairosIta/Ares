# Strategia di test

La suite separa le verifiche offline dalle prove che richiedono Ollama. Ogni
test reindirizza stato, backup e workspace verso directory temporanee, senza
toccare i dati del clone in uso.

La [valutazione semantica della memoria](memory-quality.md) è un comando
separato dalle regressioni: usa i modelli reali su dialoghi fissi e conserva
le prove dei verdetti. I suoi controlli offline sono registrati nel runner
come `valutazione`; la misura con Ollama si avvia esplicitamente con
`python -m evals.memory_quality`, anche quando si usa `tests/run.py --tutte`.

## Runner

```bash
.venv/bin/python tests/run.py             # le prove offline
.venv/bin/python tests/run.py --tutte     # anche quelle con Ollama
.venv/bin/python tests/run.py --copertura # offline, con la misura
.venv/bin/python tests/run.py --solo backup entita
```

`tests/run.py` non importa le prove: le lancia, una per processo. Non è una
preferenza di stile. Ogni prova scrive `ARES_TMP` e `ARES_BACKUP_DIR` ed
entra nella propria cartella di lavoro usa-e-getta **prima** di importare
`config`, che all'import fotografa l'ambiente in cui è nato — `AMBIENTE` è
quel dizionario, e non cambia più. La cartella di lavoro non ha una variabile
d'ambiente perché nel prodotto è la directory corrente, quella da cui si
scrive `ares`: le prove fanno lo stesso gesto con `chdir`.

I percorsi si derivano da quella fotografia a ogni `config.leggi_percorsi()`,
e nessun nome di modulo li tiene: chi legge lo stato se li vede passare come
primo parametro. Vale lo stesso per i modelli, che `config.leggi_impostazioni()`
fotografa in un `Impostazioni`: le prove se li portano dietro in un globale di
modulo, accanto ai percorsi, e le poche che sostituiscono un nome — `MAIN_MODEL`,
`EMBEDDER_MODEL`, `NUM_CTX` — costruiscono le impostazioni dentro la
sostituzione, perché è lì che il confine del processo le leggerebbe. Chi
sostituisse il nome dopo averle già lette non cambierebbe la conversazione, ed
è esattamente il difetto che il passaggio a parametro ha tolto. Lo stesso vale
per la politica, che `config.leggi_politica()` fotografa in una `Politica`: una
prova che accende o spegne un flag — `MOSTRA_APPRENDIMENTI`,
`CONFERMA_APPRENDIMENTI`, `SESSIONI_ELENCO`, `WORKSPACE_ISTRUZIONI_MAX_BYTE` —
passa l'oggetto costruito dentro il `with` a chi ne ha bisogno, o una
fotografia presa prima non vedrebbe il cambiamento. `tests/smoke_test.py`
custodisce la prova che lo pretende: `politica a runtime`. Il processo
separato resta comunque, perché ciò che conta
non è solo dove si legge ma cosa è già aperto: un lock, uno store, un
percorso copiato in una variabile non si rileggono. Due prove nello stesso
interprete condividerebbero la prima fotografia dell'ambiente — cioè i
percorsi della prima — e il giorno in cui una sbagliasse variabile
scriverebbe nell'archivio vero senza che nessuno se ne accorga. Un processo
per prova rende quell'errore impossibile invece che improbabile.

Quel gesto, e le poche righe che ogni prova ripeteva uguali, stanno in
`tests/_comune.py`: `prepara_ambiente` sceglie i percorsi usa-e-getta e
rifiuta di farlo se `config` è già in memoria; `pulisci` li cancella alla
fine uscendo prima dalla cartella di lavoro, perché su Windows la directory
corrente non si cancella; `esigi` è l'asserzione che `python -O` non toglie;
`esegui` esegue le prove in ordine e `fallimento` stampa una riga per
controllo e, quando un controllo fallisce, la riga da cui viene — con il
traceback intero se non è un'asserzione ma un guasto che la prova non
prevedeva; `chiudi` è l'ultima riga, quella che conserva l'archivio quando
qualcosa è andato storto — senza, l'unica traccia di cosa è successo se ne
andrebbe con lui — e lo cancella quando invece è andato tutto bene. Il
modulo non importa niente di `ares`, ed è l'unica garanzia che i percorsi
vengano decisi prima che `config` fotografi l'ambiente.

I doppi che più prove costruivano uguali — il modello che risponde a
copione, la tool call nella forma in cui Agno la mette nei messaggi — stanno
invece in `tests/_doppi.py`: sono un pezzo di Agno, e `_comune.py` promette
di non importarne. La regola però è la stessa, e per la stessa ragione:
anche `_doppi.py` non tocca `ares` né `config`, quindi si può importare
prima di `prepara_ambiente`.

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

Con le sole prove offline la misura del 22 settembre 2026 su Linux e
Python 3.12.3 è al 91%. `cli/chat.py` arriva al 95%, `agent/echo.py` al 96%
e `state/lock.py` al 100%; `backup/restore.py` resta all'86%, `cli/ui.py`
all'84% e `cli/commands.py` all'81%. Sono misure di questa esecuzione, non
soglie: una percentuale alta non dimostra che siano coperti tutti gli
interleaving fra chat o tutti i punti in cui una copia può fallire.
`cli/conferma.py` è al 77%: fra i percorsi non attraversati resta la domanda
con l'editor di Prompt Toolkit, disponibile solo quando stdin e stdout sono
un terminale. Le prove di conferma scritta passano dal ripiego `input()`,
usato anche dagli script.

`cli/comando.py` era il caso opposto, al 76%: lì le sei righe scoperte erano
la tabella dei codici d'uscita, cioè i rami che traducono un'eccezione
prevista in 1, 2 o 3. Il 3 lo provocavano già `sessioni` ed `entita` con il
lock; il 2 era asserito solo dove nasce da un `return` e non da un'eccezione,
e il 1 da nessuna parte. Ora `sessioni` chiede la cancellazione di una
sessione inesistente — un rifiuto che arriva come eccezione — e un prune con
la directory dei backup occupata da un file, che è un guasto del disco e vale
1; `cli` chiede uno snapshot con lo stato già preso. Il modulo è al 100%, e
ognuno dei quattro codici ha una prova che lo produce per la sua strada.

La misura segue anche i processi figli, e senza questo mentirebbe in difetto:
le prove ne lanciano parecchi — la CLI di `ares.entities` sei volte,
il sondaggio LanceDB isolato di `backup/probe.py`, la rilettura da un secondo
interprete in `e2e_test.py`. `coverage` misura il processo che avvia, non i
suoi discendenti, e prima dell'aggancio `entities/maintenance.py` risultava al
68% pur avendo la propria CLI provata da sei sottoprocessi: il rapporto
mandava a scrivere prove per righe che ne avevano già una. L'aggancio è
`tests/_copertura/sitecustomize.py`, che Python importa da sé all'avvio di
ogni interprete e che il runner attiva con `COVERAGE_PROCESS_START` solo
quando misura, insieme a `COVERAGE_FILE` assoluto, perché un figlio scrive
la propria misura nella directory corrente e da una cartella usa-e-getta la
lascerebbe lì. Un figlio va lanciato come modulo, non per percorso: la
misura segue il package `ares`, e un file eseguito per percorso è `__main__`
e basta — la sonda LanceDB di `backup` risultava allo 0% pur girando a ogni
`create` e `verify`, finché `integrity.py` non l'ha lanciata con
`-m ares.backup.probe`.

Ciò che resta scoperto è quasi tutto composto da gestori d'errore e da rami
di piattaforma: i percorsi Windows su una macchina Linux, i ripieghi per un
disco in sola lettura, le eccezioni che nessuno ha mai visto sollevare.
Nessuna di quelle righe sta in un percorso che solo un modello vero
attraversa: misurato il 22 settembre 2026, `--tutte --copertura` produce un
rapporto **identico** a quello delle sole prove offline, riga per riga e ramo
per ramo — 4.355 istruzioni, 304 scoperte, 1.340 rami, 91%. Le prove con
Ollama verificano ciò che un modello finto non può dire, non allargano la
copertura: le stesse righe le attraversano le prove offline con il modello
deterministico. `tests/run.py` diceva il contrario in un commento, e la
correzione sta là dove si legge il numero.

Il retry del contesto è l'esempio di come si divide il lavoro fra le due: la
sua logica la prova `contratto`, offline e in modo deterministico, mentre
`learning_reliability_test.py` resta a misurare ciò che solo un modello vero
può dire, cioè *quanto spesso* l'estrazione manca il colpo. Una risponde "il
retry funziona come scritto", l'altra "serve davvero, e quanto".

Le prove del terminale simulato impostano un ambiente Rich proprio, così
`TERM=dumb`, `NO_COLOR` o le impostazioni della CI non cambiano le
precondizioni del rendering TTY. Una prova separata verifica la risposta
senza controlli ANSI su un terminale `dumb`.

Le regressioni sulla conservazione dei dati includono il fallimento della
copia iniziale del restore Windows, prima e dopo aver copiato un file:
l'originale deve restare intatto. La CLI usa store Agno reali su SQLite
temporaneo per verificare che un rifiuto conservi le memorie precedenti,
anche dopo errori e Ctrl-C; processi figli verificano la contesa dello stesso
utente e l'indipendenza di utenti diversi durante istantanea, turno,
conferma e ripristino. Sono coperti anche il rifiuto prima dell'inferenza,
il rilascio del lock e gli esiti della contesa nella REPL e in pipe.

## Analisi statica

```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m mypy . tests/run.py tests/_comune.py tests/_doppi.py
```

Non eseguono il codice e non toccano nessun archivio. Mypy copre i moduli e
il cablaggio delle prove, non le singole prove: quelle girano a ogni CI,
quindi un errore di tipo lì diventa subito un fallimento visibile, mentre nei
moduli restano rami — i percorsi Windows, i gestori d'errore — che nessuna
prova attraversa. Il cablaggio è il caso di mezzo: il runner e ciò che le
prove importano non li esegue nessuna prova, perché sono loro a eseguirle, e
un errore lì si vedrebbe solo quando una prova non parte più. Ruff copre
tutto.

## Verifiche offline

```bash
.venv/bin/python tests/run.py
```

Sono undici. `smoke` costruisce l'agente e semina gli store, e controlla
assemblaggio, isolamento, lock, propagazione simulata del run completo alla
macchina di apprendimento e l'eco di ciò che entra in memoria. Del post-hook
controlla anche le due guardie: un run senza messaggi e un post-hook senza
agente non estraggono, e un'estrazione da un run vuoto non ha niente da
imparare ma passerebbe comunque dal modello. Nello stesso giro verifica che
fuori da `AGENTIC` le istruzioni italiane delle intuizioni non entrino nello
store. `repl` prova
ciò che della chat gira senza l'agente: conferme lette e applicate, esito e
metriche degli strumenti, rendering Rich su pipe e su un terminale simulato
con i controlli filtrati, core del turno con eventi fabbricati, log di Agno,
cronologia privata, editor con completamento e multilinea, Ctrl-C/D,
comandi locali e la cartella di lavoro: i percorsi rischiosi, i tre esiti
dell'autorizzazione, il ramo letto da `.git/HEAD`, `ARES.md` e `ares init`;
e le conversazioni per cartella su un database finto: il filtro che tiene
quelle senza cartella, l'id nuovo, l'istruzione al modello e la scelta
numerata di `ares resume --scegli`.
Stava nello smoke, che era diventato il posto dove finiva
ogni prova offline; la divisione segue ciò che serve per girare.
`sessioni` attraversa un vero `Agent.run()` con modello deterministico e
verifica offload, quota, retention, cascata e restore dei due SQLite.
`contratto` chiede ad Agno le cinque cose che Ares dà per vere del
framework: che un turno con pausa per conferma produca una sola estrazione,
quella del post-hook sul run completo; che `run → pausa → continue_run`
riprenda lo stesso run, eseguendo lo strumento dopo la conferma e non prima e
conservando il file dopo un rifiuto; che il retry di
`AresSessionContextStore` ripeta solo l'estrazione che non ha scritto e si
fermi appena scrive, nei due percorsi sincrono e asincrono e in tutti e tre i
casi — primo colpo, tetto raggiunto, recupero — e che l'`aprocess` anticipata
di Ares non estragga nulla, perché Ares non usa `arun` e quella riga si
attraversa solo così; che
profilo e memorie continuino a rifiutare le modalità `PROPOSE` e `HITL`, che
è il motivo per cui la memoria durevole non passa da una conferma; e che la
versione di Agno dichiarata nei documenti sia quella installata, con
`FILE_CHE_DICHIARANO` come elenco esplicito delle pagine e dei commenti che
la nominano. Il numero era scritto a mano in sette posti e uno era rimasto
alla 3.0.5 mentre il lock era alla 3.0.9: adesso una patch che sale senza
allineare le dichiarazioni rende rossa questa prova, e il messaggio nomina il
file. Restano fuori `CHANGELOG.md` e `docs/memory-quality.md`, che citano le
versioni di allora.
`ambiti` tiene ferme le due premesse dello studio sugli ambiti
([project-scopes](project-scopes.md#34-esito-delle-due-verifiche-preliminari))
da cui dipendono la provenienza delle memorie e la composizione a mano del
blocco delle intuizioni. Con il modello a copione dell'altra prova verifica
che una chiave in più in una voce di memoria — la provenienza — si scriva con
le API pubbliche degli store, sopravviva a un `update_memory` che cambia il
contenuto, si riallinei con la stessa scrittura, e **non** arrivi al modello:
il prompt di estrazione porta solo `{id, content}`, ed è per questo che la
classificazione non può essere del modello. Con un embedder a vettori fissi
— nessun modello, i documenti arrivano a LanceDB con il vettore già dentro —
verifica che il filtro per namespace sia applicato sui metadati *dopo* il
limite: con trenta documenti fuori ambito più vicini alla domanda di cinque in
ambito, la ricerca del progetto restituisce zero con limite 5 e con limite 30,
e li restituisce tutti solo quando il limite supera i documenti fuori ambito;
nella stessa tabella il filtro dell'owner è invece una clausola del motore e
restituisce cinque righe su cinque. Le due prove non provano la proposta — il
registro dei progetti e il blocco composto non esistono — provano le premesse:
se Agno cambia l'una o l'altra, il fallimento arriva qui invece che in
produzione, e le misure del §3.4 dello studio smettono di essere due script
usa-e-getta. I messaggi nominano la pagina da aggiornare.
`costo` misura le tre estrazioni `ALWAYS` con lo stesso genere di modello
finto, che però conta le chiamate e risponde scrivendo: tre chiamate al
modello di apprendimento per turno, una per store — profilo e memorie non
pagano più la seconda chiamata di conferma, che Agno produce e scarta e che il
contesto evitava già con `stop_after_tool_call` — e 19.970 caratteri, due
terzi dei quali istruzioni. Spegnere uno store toglie esattamente una
chiamata, e un modello che non chiama lo strumento costa i tentativi del
contesto, quindi più di uno che scrive. Una quarta prova legge profilo e
memorie dopo il turno: il flag toglie la conferma, non la scrittura. I numeri
e le opzioni stanno in
[qualità della memoria](memory-quality.md#costo-delle-estrazioni-22-settembre-2026);
la stessa misura con i modelli veri la stampa `e2e`, che accanto alla riga
del client riporta i token di ingresso dell'apprendimento.
Il modello è lo stesso copione deterministico. Nei primi due controlli gli
store di apprendimento sono spenti e si conta il passaggio, non ciò che
scriverebbe; il terzo lo store lo costruisce davvero, perché lì la domanda è
proprio se ha scritto.
`backup` copre snapshot, checksum, restore e prune, e insieme al protocollo
della sonda LanceDB — simulato, per provare come il genitore traduce ciò che
riceve — esegue anche la sonda vera con `-m ares.backup.probe`: è l'unico
modo di sapere che il figlio dica davvero ciò che il genitore crede, e che il
modulo sia ancora avviabile in un altro interprete. Ciò che prova è ciò che
il figlio decide da sé — l'uso sbagliato, la traduzione di un'eccezione in un
codice e un messaggio — e non come fallisce LanceDB: la prima versione
chiedeva alla sonda di aprire un file al posto di una directory e pretendeva
un guasto, che su Linux arriva e su Windows no, dove il motore risponde con
nessuna tabella. Lo ha trovato il runner Windows, ed è il motivo per cui la
matrice esiste. `entita` copre l'audit e la fusione. `valutazione` prova il benchmark della qualità della memoria senza
accendere un modello: ventinove controlli sui verdetti — che una citazione
negativa, ritagliata o contraddetta non passi, che un recupero pretenda
un'evidenza durevole e non il contesto della sessione, che un dato inventato
fallisca invece di restare non conclusivo — più l'isolamento degli archivi
del worker e i guasti, cioè timeout, Ctrl+C e processo senza rapporto, che
devono conservare le fasi già scritte. È la prova che il misuratore non
produce successi senza prove, e gira in due decimi di secondo perché i
dialoghi sono già scritti. `cli` prova i comandi con cui Ares si usa davvero: il preflight
contro un server Ollama finto nei tre esiti, l'ispezione degli archivi, i
sottocomandi di `ares.backup` con i loro annullamenti, la REPL intera in un
processo separato con stdin da una pipe, e l'avvio senza `--session`: la
conversazione nuova nominata dalla cartella, `resume` a vuoto e sull'ultima
di qui con sessioni seminate nel database vero, `--scegli`, e `-p` con stdin
in pipe.
`rilascio` è il più breve e non accende niente: confronta `pyproject.toml` con
il lock, con la voce più recente del `CHANGELOG`, con la riga supportata di
`SECURITY.md` e con i due collegamenti di confronto in coda al `CHANGELOG`,
e fallisce nominando il file da correggere. La versione di Ares era l'unica
cosa scritta a mano che nessuno controllava: la 0.7.1 dichiarava ancora la
linea 0.6.x in `SECURITY.md` e la 0.8.0 è arrivata su `main` con `Unreleased`
fermo alla 0.7.1, entrambe le volte con la CI verde perché quei file non li
legge nessuno. È la stessa idea della prova sulla versione di Agno, applicata
al repository invece che al framework. La procedura sta in
[CONTRIBUTING](../CONTRIBUTING.md#come-si-rilascia).

Nessuna genera risposte con il modello. `cli_test.py` lo rende esplicito
puntando `config.OLLAMA_HOST` a un porto chiuso: su una macchina di sviluppo
Ollama è spesso acceso, e senza quella riga una prova potrebbe usarlo di
nascosto e passare qui per fallire in CI. La leva non arriva però ai processi
figli — `OLLAMA_HOST` è una costante, non una variabile d'ambiente — quindi
la REPL provata in un processo separato riceve solo righe che cominciano con
`/`, e una asserzione verifica che nessun turno col modello sia stato aperto.

Il runner interrompe una prova offline dopo sei minuti e una prova con Ollama
dopo quindici. Non sono tempi attesi ma limiti di sicurezza: evitano che un
deadlock o una dipendenza bloccata consumino indefinitamente il terminale o
l'intero timeout della CI. Un superamento appare nel riepilogo come fallimento.

Due controlli sorvegliano un'invariante che nessun'altra prova vedrebbe:
importare `config` non deve creare niente su disco (`smoke`), e `--help` di
ognuno dei sette comandi nemmeno (`cli`). Entrambi girano in processi nuovi,
perché un modulo si importa una volta sola. Sono la rete sotto una riga
spostata di due caratteri: `prepara_archivio()` chiamata dopo `parse_args()`
invece che prima, che è tutta la differenza fra un `--help` che lascia un
archivio e uno che non lascia niente.

La CI esegue le stesse undici prove, con la misura, su Ubuntu con Python 3.12
e 3.13 e su Windows con la 3.12. Sul runner Windows l'ambiente nasce
direttamente da `setup.ps1 -SkipPreflight`, così la CI verifica anche il
percorso d'installazione senza richiedere Ollama; un secondo `uv sync --locked` sullo
stesso venv aggiunge poi `coverage`, che lo script di proposito non installa.
Misurare anche lì non è ridondante: i rami Windows di `backup` e
`platform_files` esistono per quel sistema, e misurati solo su Ubuntu
risultavano scoperti anche quando il runner Windows li attraversava.

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

GitHub Actions esegue tre job, l'ultimo in tre varianti. `Cosa cambia` guarda
quali file tocca il commit e salta i passi pesanti quando sono solo documenti;
`Analisi statica` gira una volta su Ubuntu con ruff e mypy; `tests` installa le
dipendenze bloccate, verifica lo script di setup, compila il codice e lancia
`tests/run.py --copertura` su Ubuntu con Python 3.12 e 3.13 e su Windows con
la 3.12: la copertura di un progetto non dipende dal sistema, ma i rami
Windows di `backup` e `platform_files` esistono per quel sistema e misurati
solo su Ubuntu risultavano scoperti. La variante 3.13 è l'altro estremo di
`requires-python` e non è fra i controlli obbligatori del ruleset: è un
canarino sull'interprete, come CodeQL e `Audit` lo sono sul codice e sulle
dipendenze. Le prove con Ollama restano intenzionalmente locali perché
richiedono modelli e hardware dedicato.
