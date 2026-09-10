# Qualità della memoria

Il benchmark misura **che cosa Ares conserva e recupera**, con dialoghi
sintetici fissi e i modelli configurati per conversazione ed estrazione.
È separato dalla suite di regressione: un risultato del modello può cambiare
fra esecuzioni, mentre i controlli offline del valutatore devono restare
deterministici.

## Esecuzione

Dalla radice del repository, con l'ambiente di sviluppo e Ollama disponibili:

```bash
.venv/bin/python -m evals.memory_quality --ripetizioni 3

# Misura mirata; il percorso deve essere nuovo.
.venv/bin/python -m evals.memory_quality --casi correzione temporanea \
  --ripetizioni 3 --timeout 180 --report artifacts/memory-quality/confronto.json

# Ciclo di vita di un'idea e di un piano, fra sessioni diverse.
.venv/bin/python -m evals.memory_quality --casi abbandono_ipotesi abbandono_piano \
  --ripetizioni 3 --report artifacts/memory-quality/abbandono.json

# Decisione, programma futuro, avvio esplicito e decisione ribadita.
.venv/bin/python -m evals.memory_quality --casi avvio \
  --ripetizioni 3 --report artifacts/memory-quality/avvio.json

# Solo i controlli offline del valutatore, inclusi anche nel runner ordinario.
.venv/bin/python tests/run.py --solo valutazione
```

Su Windows usa `.\.venv\Scripts\python.exe` e scrivi il comando su una riga.
Il timeout vale per un caso e una ripetizione, comprese tutte le sue fasi.
I modelli sono quelli scelti nella configurazione di Ares: se sono cloud,
anche questa misura usa il cloud. I dati inviati sono sintetici.

Ogni coppia caso/ripetizione gira in un processo dedicato con stato, home,
backup e directory corrente temporanei. Le fasi dello stesso caso
condividono gli apprendimenti, così una correzione incontra davvero il fatto
precedente. Il processo elimina gli archivi temporanei al termine; il
rapporto conserva le fotografie necessarie alla verifica. Non importa né
copia lo stato personale di Ares.

## Protocollo

1. Consegna i messaggi fissi di utente e assistente alla macchina di
   apprendimento usata da Ares dopo un turno completato.
2. Salva profilo, memorie e contesto prima e dopo l'estrazione.
3. Costruisce Ares in una sessione nuova, in modalità `-p`, e pone una
   domanda senza includere la risposta attesa. Profilo e memorie vengono
   caricati normalmente; il contesto della sessione precedente, la
   cronologia, le entità, le intuizioni e il workspace sono esclusi.
   Il quaderno è disponibile ma inizialmente vuoto.
4. Controlla risposta, evidenza negli store ed eventuali modifiche della
   memoria durante la sonda. Registra strumenti chiamati, avvisi, errori e
   durata delle due operazioni.

La sonda chiede un JSON con valore, certezza ed evidenza. Il valutatore
accetta anche un singolo blocco Markdown `json`; testo ulteriore rende la
prova non conclusiva. Le citazioni possono includere le date aggiunte dal
renderer di Agno. Il campo `source` delle memorie e il contesto della vecchia
sessione non valgono come evidenza durevole.

Per una risposta confermata la citazione deve essere riconducibile anche
al contenuto originale di un campo del profilo o di una memoria. Il
valutatore esamina l'intera voce originale, comprese eventuali altre voci
che contengono lo stesso valore: una citazione ritagliata non può nascondere
una negazione presente nel contesto. Negazioni, segnali di incertezza o
domande richiedono revisione; così anche citazioni di meno di tre parole.
Un identificativo con trattino conta come una parola. I segnali sono
lessicali e conservativi: una negazione riferita a un fatto diverso nella
stessa memoria può richiedere revisione. Non costituiscono una verifica
semantica generale del linguaggio naturale.

| Caso | Fasi | Comportamento atteso |
| --- | ---: | --- |
| Ipotesi | 1 | Un possibile trasferimento non diventa la residenza attuale. |
| Personaggio | 1 | Il nome di un personaggio inventato non diventa quello dell'utente. |
| Proposta | 2 | Un orario proposto diventa una decisione solo dopo l'accettazione. |
| Correzione | 2 | La preferenza corretta sostituisce quella precedente nel recupero. |
| Temporanea | 2 | Un'eccezione per una risposta non sostituisce la preferenza generale. |
| Recupero | 1 | Il nome confermato del progetto è recuperabile in una nuova sessione. |
| Abbandono ipotesi | 3 | Un'idea scartata non torna attuale, neppure dopo un dialogo su altro. |
| Abbandono piano | 3 | Un progetto confermato e poi abbandonato viene sostituito nel recupero dal progetto attuale. |
| Avvio | 4 | Una decisione o un programma futuro non dimostrano l'avvio; un avvio esplicito rimane noto quando la decisione viene ribadita. |

Sono diciannove fasi per ripetizione, cinquantasette con le impostazioni predefinite.
I messaggi e i criteri esatti sono in `evals/memory_quality.py` e vengono
copiati nel rapporto.

Nei due casi di abbandono le estrazioni avvengono nelle sessioni distinte
`idea`, `scelta` e `appunti`. La prima presenta ORIONE-42 come possibilità
oppure piano confermato; la seconda lo abbandona esplicitamente e sceglie
VEGA-19 come unico progetto attuale; la terza riguarda soltanto il formato
degli appunti. Ogni sonda usa un'ulteriore sessione nuova e non riceve i
messaggi precedenti né il nome del progetto nella domanda.

Per superare la fase di abbandono deve essere presente una memoria durevole
precedente di ORIONE-42: senza di essa il risultato corretto resta **non
conclusivo** per l'aggiornamento di un ricordo. Analogamente, l'ultima fase
richiede che VEGA-19 fosse già memorizzato prima del dialogo sugli appunti.
Un ricordo di ORIONE-42 mantenuto come storia non è automaticamente un
errore: la sua presenza richiede revisione del testo, anche quando il
recupero del progetto attuale è corretto.

Il caso `avvio` usa quattro sessioni di apprendimento distinte. La stessa
sonda chiede se il lavoro è già iniziato: dopo decisione e programma futuro
il valore atteso è nullo, perché l'avvio non è confermato. Anche rispondere
«non iniziato» è un errore: l'assenza di una conferma non prova il contrario.
Dopo l'avvio esplicito, il valore atteso è «iniziato», sostenuto da una
citazione della memoria. Ribadire successivamente la decisione non deve
far perdere il fatto già noto che il lavoro è iniziato.

Quest'ultima fase richiede nello snapshot precedente una prova affermativa
dell'avvio di ORIONE-42, non soltanto il suo nome. Il controllo riconosce
formulazioni esplicite di lavoro iniziato o avvio confermato e le etichette
`ORIONE-42: iniziato` / `ORIONE-42 (iniziato)` del profilo. Formulazioni
incerte, programmi futuri, contraddizioni o riferimenti a più progetti
rendono la precondizione non conclusiva. Anche una formulazione valida ma
non riconosciuta richiede revisione; contesto di sessione, `source` e testo
del solo renderer non valgono come prova dell'avvio precedente.

Nelle prime due fasi, formulazioni nelle memorie che richiamano un avvio
o un'attività in corso richiedono revisione anche se la risposta è incerta.
Il controllo usa frammenti di testo: segnala anche frasi corrette come
«avvio non confermato». La revisione deve quindi distinguere il significato
della frase; il numero di segnalazioni da solo non misura un peggioramento.

## Lettura dei risultati

| Esito | Significato |
| --- | --- |
| `superato` | Risposta attesa, evidenza verificabile dove richiesta, nessun termine segnalato per revisione. |
| `fallito` | Dato confermato non recuperato o dato non confermato presentato come personale. |
| `da_revisionare` | Risposta corretta, ma nello store compare un termine che può indicare ipotesi, vecchio dato o eccezione: va letto nel contesto. |
| `non_conclusivo` | Formato, citazione, memoria precedente mancante o avvisi imprevisti impediscono un verdetto automatico affidabile. |
| `errore` | Estrazione del contesto non completata, eccezione, timeout, rapporto mancante o memoria modificata dalla sonda. |

Il codice d'uscita è **0** solo se tutte le fasi sono superate, **2** se
rimangono fallimenti o ambiguità, **1** per guasti o esecuzioni incomplete.
Un timeout conserva le fasi già scritte e aggiunge un errore di processo;
gli errori di processo non sono fasi semantiche aggiuntive.
Anche Ctrl+C recupera il checkpoint del caso corrente prima di eliminare
gli archivi temporanei: lo aggiunge ai rapporti JSON/Markdown, segna
l'esecuzione `interrotto`, esce con codice **1** e non avvia altri casi.

Il rapporto JSON viene aggiornato dopo ogni caso completato; quello Markdown
è un indice degli esiti. Entrambi stanno per default in
`artifacts/memory-quality/`, ignorata da Git. Il JSON registra anche modelli,
opzioni, versione Agno, hash dei sorgenti e del prompt composto per il
recupero. Una revisione manuale va annotata separatamente: non riscrive i
verdetti automatici e non trasforma retroattivamente una misura in verde.
Lo schema 2 aggiunge a ciascuna fase la sessione di apprendimento e le
memorie richieste prima del turno; i rapporti della prima misura mantengono
lo schema originale.
Lo schema 3 aggiunge il progetto per cui è richiesto un avvio precedente
esplicito e applica i controlli sulle citazioni originali descritti sopra.

## Prima misura, 7 settembre 2026

Agno **3.0.5**, conversazione **glm-5.3-flash:cloud**, estrazione
**gemma4:31b-cloud**. Tre ripetizioni dei sei casi:

| Caso | Fasi | Superate | Da revisionare | Non conclusive |
| --- | ---: | ---: | ---: | ---: |
| Ipotesi | 3 | 2 | 1 | 0 |
| Personaggio | 3 | 2 | 0 | 1 |
| Proposta | 6 | 6 | 0 | 0 |
| Correzione | 6 | 6 | 0 | 0 |
| Temporanea | 6 | 6 | 0 | 0 |
| Recupero | 3 | 3 | 0 | 0 |
| Totale | 27 | 25 | 1 | 1 |

Nessun fallimento semantico automatico, errore di esecuzione o avviso Agno;
codice d'uscita **2**. Tempo cumulato delle fasi circa 191 secondi, esclusi
avvio dei processi e scrittura dei rapporti. Artefatti locali:
`artifacts/memory-quality/baseline-20260907.json` e `.md`.

La lettura delle due fasi non verdi mostra:

- **Ipotesi, terza ripetizione.** La memoria contiene «Sta valutando la
  possibilità di trasferirsi a Milano, ma non ha ancora preso una
  decisione». La sonda risponde con valore nullo e certezza sconosciuta.
  L'incertezza è quindi preservata e Milano non viene presentata come
  residenza. Resta una scelta di prodotto: quanto a lungo conservare una
  possibilità non confermata nella memoria durevole.
- **Personaggio, terza ripetizione.** Profilo e memorie sono vuoti; il
  personaggio compare soltanto nel contesto della vecchia sessione. La
  risposta JSON è corretta, ma Ares aggiunge una spiegazione fuori dal
  blocco e il valutatore la classifica non conclusiva. Chiama anche
  `list_files` e due volte `search_content` sul quaderno vuoto: un costo
  osservabile, pur senza attribuire il personaggio all'utente.

Le otto suite offline passano, inclusi gli undici controlli del valutatore;
ruff, formattazione e mypy passano. Questa misura costituisce un punto di
partenza: non confronta il prompt precedente, non prova il modello locale
e non stima l'affidabilità generale della memoria.

L'8 settembre è stato verificato anche il cleanup aggiornato: lo stato del
worker resta sotto la directory temporanea del padre, che può eliminarlo
anche dopo un timeout. I controlli offline di isolamento e timeout passano;
un'ulteriore esecuzione reale di `recupero` è superata, nel rapporto locale
`worker-check-20260908.json`. Questa verifica aggiuntiva non entra nelle
ventisette fasi della prima misura, il cui rapporto rimane invariato.

## Abbandono di idee e piani, 8 settembre 2026

Tre ripetizioni per ciascuno dei due nuovi casi, con gli stessi modelli
della prima misura e Agno 3.0.5. Rapporto locale:
`artifacts/memory-quality/abbandono-20260908.json` e `.md`.

| Caso | Fasi | Superate automaticamente | Da revisionare |
| --- | ---: | ---: | ---: |
| Abbandono ipotesi | 9 | 0 | 9 |
| Abbandono piano | 9 | 5 | 4 |
| Totale | 18 | 5 | 13 |

Nessun fallimento automatico, risultato non conclusivo, guasto o avviso
Agno. Il codice d'uscita rimane **2**: le tredici segnalazioni per revisione
non sono state riclassificate. Tempo cumulato delle fasi circa 101 secondi.
Il contesto precedente è vuoto all'inizio di tutte le diciotto estrazioni,
come atteso per le sessioni distinte; gli apprendimenti durevoli passano
invece da una fase alla successiva.

La revisione dei testi chiarisce le segnalazioni:

- Nelle tre fasi iniziali di `abbandono_ipotesi`, ORIONE-42 è conservato
  come idea ancora da decidere. Ares risponde con valore nullo e certezza
  `non_confermato`, senza trasformarlo in un progetto deciso.
- Nelle sei fasi successive dello stesso caso, la memoria originale è
  aggiornata: VEGA-19 è l'unico progetto attuale e ORIONE-42 è
  «definitivamente scartato». La vecchia formulazione che lo descriveva
  ancora in valutazione non rimane nelle memorie recuperabili.
- In `abbandono_piano`, il profilo passa da ORIONE-42 a VEGA-19 in tutte
  le ripetizioni. Nella prima la memoria elimina il vecchio riferimento;
  nelle altre due lo conserva come progetto definitivamente scartato:
  da qui le altre quattro segnalazioni per revisione.

**Tutte le dodici risposte dopo l'abbandono indicano VEGA-19 come progetto
attuale**, comprese quelle dopo il dialogo sugli appunti. Le memorie
precedenti richieste sono presenti: il risultato non dipende da un'idea
che non era mai stata salvata. In questo campione il recupero distingue
quindi il progetto attuale dal ricordo storico di quello scartato.

È emersa anche una distinzione che il verdetto sul progetto attuale non
misura: in `abbandono_piano`, «ho deciso di realizzare» viene salvato come
«sta realizzando», sia per ORIONE-42 sia per VEGA-19. L'utente non aveva
confermato l'avvio del lavoro. È un punto da approfondire con una prova
che distingua intenzione, decisione e attività effettivamente iniziata;
questa misura non lo trasforma in un fallimento dell'abbandono.

Verifiche: **15 controlli del valutatore e 8 suite offline verdi**, ruff,
formattazione e mypy verdi. Rivalutando offline il rapporto del 7 settembre
con il valutatore esteso, tutti i 27 verdetti precedenti rimangono identici.
Il prompt e il codice di apprendimento di Ares non sono cambiati in questo
passo; la nuova misura documenta il comportamento esistente.

## Decisione e avvio: confronto dell'8 settembre 2026

Il caso `avvio` è stato aggiunto prima di modificare Ares e misurato tre
volte su archivi nuovi. Dopo la modifica sono stati usati gli stessi
dialoghi, domande, criteri di valutazione, modelli e parametri: Agno 3.0.5,
`glm-5.3-flash:cloud` in conversazione e `gemma4:31b-cloud` in estrazione.
Gli hash del benchmark nei due rapporti coincidono; cambiano le istruzioni
in `learning.py`, `prompts.py` e la descrizione del campo `current_focus`
in `schemas.py`.

| Misura | Fasi | Superate | Fallite | Da revisionare | Non conclusive | Errori |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Prima | 12 | 3 | 4 | 3 | 2 | 0 |
| Finale | 12 | 8 | 0 | 0 | 4 | 0 |

Rapporti locali conservati, ciascuno in JSON e Markdown:

- `artifacts/memory-quality/avvio-prima-20260908.json`;
- `artifacts/memory-quality/avvio-dopo-20260908.json` (misura intermedia);
- `artifacts/memory-quality/avvio-finale-20260908.json`.

Il primo rapporto riproduce due difetti. In tutte e tre le ripetizioni,
la sola decisione viene memorizzata come «Sta realizzando ORIONE-42» e
recuperata come avvio confermato. Inoltre, ribadire la decisione dopo un
avvio esplicito sostituisce la memoria con la sola decisione: tutte e tre
le risposte successive perdono la conferma dell'avvio. Due di queste
risposte aggiungono testo fuori dal JSON, quindi il verdetto automatico
rimane non conclusivo anziché fallito.

La revisione di profilo, memorie e risposte della misura finale mostra:

- dopo la decisione, `current_focus` conserva «deciso» e la risposta
  sull'avvio è sconosciuta, in tutte e tre le ripetizioni;
- dopo il programma futuro, conserva «programmato» senza dedurre che il
  lavoro sia iniziato o che sicuramente non sia iniziato;
- dopo l'avvio esplicito, conserva «iniziato» e una memoria dell'avvio;
- ribadire la decisione conserva quell'avvio, sia nel profilo sia nelle
  memorie, e le tre risposte lo recuperano correttamente.

Le quattro risposte finali non conclusive sono corrette alla lettura:
citano «ha confermato l'avvio dei lavori», presente nelle memorie. Il
valutatore richiede però anche che la citazione contenga letteralmente il
valore «iniziato». È un limite del controllo sulle citazioni; i verdetti
non sono stati riscritti né le regole allentate dopo aver visto gli esiti.
Il codice d'uscita di entrambi i rapporti resta **2**.

La misura intermedia aveva già corretto profilo e memorie, ma uno dei tre
riepiloghi del programma futuro aggiungeva «non ancora avviata». Sono
state quindi precisate le istruzioni del contesto di sessione: descrivere
un programma come programma, senza aggiungere una mancata partenza non
dichiarata. Nella misura finale i tre riepiloghi conservano il programma
futuro senza quell'affermazione. Anche questi testi sono disponibili nel
rapporto, benché il verdetto automatico misuri profilo e memorie.

Le otto suite offline e le tre con Ollama (`affidabilita`, `intuizioni`,
`e2e`) sono passate. Dopo l'ultima precisazione del contesto sono passati
anche `contratto` e `valutazione` (18 controlli), oltre a ruff,
formattazione e mypy. La misura finale ha completato le dodici estrazioni
e i relativi recuperi senza avvisi Agno o guasti.

Con la formulazione finale sono stati ripetuti anche `correzione`,
`temporanea` e `abbandono_piano`, tre volte ciascuno: **21 fasi su 21
superate**, senza segnalazioni o guasti. Il rapporto
`artifacts/memory-quality/avvio-regressioni-20260908.json` conserva questa
verifica dei comportamenti che condividono i criteri di estrazione.

Il campione mostra un miglioramento sui due difetti riprodotti, non una
garanzia su tutte le formulazioni possibili. Non verifica attività sospese
o completate, né conferme esplicite di mancato avvio; non confronta i
modelli locali e non riscrive ricordi reali preesistenti.

## Correzioni del valutatore dopo la review, 8 settembre 2026

I tre findings P2 sono coperti da prove offline che riproducono i difetti:
una citazione negativa o ritagliata non viene più promossa automaticamente;
la conservazione dell'avvio richiede un avvio già presente per lo stesso
progetto; Ctrl+C trasferisce le fasi già salvate nel rapporto finale prima
della pulizia delle directory temporanee.

I controlli del valutatore sono ora **25**, inclusa una prova POSIX che
invia SIGINT al gruppo padre/figlio, verifica JSON e Markdown, controlla la
pulizia e accerta che il caso successivo non parta. Su Windows questa prova
del segnale è saltata; le prove portabili dell'eccezione e del recupero del
checkpoint restano attive. Le otto suite offline, ruff, formattazione e
mypy passano.

È stata effettuata anche una rivalutazione offline dei sei rapporti
principali già salvati, per **102 fasi complessive**: tutti i verdetti
rimangono identici. Nessuna nuova inferenza è stata eseguita per questa
verifica. L'artefatto separato
`artifacts/memory-quality/p2-rivalutazione-20260908.json` registra gli hash
dei rapporti originali e del valutatore, i vecchi e nuovi conteggi e le
valutazioni per fase. I rapporti storici e le tabelle precedenti restano
invariati.

## Correzione del legame valore-citazione, 10 settembre 2026

Il valutatore chiedeva che la citazione contenesse il valore atteso alla
lettera. Per il caso `avvio` il valore è `iniziato`, e una memoria che dice
«ha confermato l'avvio dei lavori» lo sostiene senza contenerlo: la risposta
usciva **non conclusiva** pur essendo corretta e pur citando verbatim lo
store. La stessa frase era però già riconosciuta come prova d'avvio da
`AVVIO_AFFERMATO`, che serve alla precondizione di `avvio_confermato`. Lo
strumento si contraddiceva: prova per la precondizione, non prova per la
citazione. È il limite annotato nel confronto dell'8 settembre («È un limite
del controllo sulle citazioni»).

La correzione non allenta la regola generale. Ogni fase dichiara come il
proprio valore può essere citato, con il campo `evidenza_equivalente`, e solo
le due fasi che attendono `iniziato` lo dichiarano, riusando l'espressione che
il file già conteneva. Le altre diciassette fasi delle nove casistiche
continuano a pretendere il termine. Restano intatte tutte le altre guardie: la citazione deve trovarsi
verbatim negli store, il contesto originale non deve negare o dubitare, tre
parole sono il minimo, e il valore non può comparire come frammento di
un'altra parola. I testi su cui si cerca ambiguità sono ora quelli che
sostengono il valore e non i soli che lo contengono alla lettera: con una
forma equivalente quella lista restava vuota e la guardia non guardava nulla.

Tre prove offline nuove coprono la modifica: che la forma equivalente conti
come prova, che non scavalchi le altre guardie, e che i casi che non la
dichiarano continuino a pretendere il termine. Mutando la correzione cadono
cinque asserzioni.

Una quarta prova nasce da un difetto che le prime tre non vedevano.
L'espressione dichiarata è un oggetto compilato, e il rapporto la salva
insieme al resto del dialogo: alla prima fase che la dichiarava il worker
moriva con codice 1 mentre scriveva il JSON, perdendo anche le fasi già
misurate. Le prove sul verdetto non passano dal rapporto, quindi restavano
verdi. Il rapporto conserva ora il testo dell'espressione - è evidenza, e va
riletta - e la prova nuova serializza ogni fase di ogni caso. I controlli del
valutatore passano da 25 a **29**.

### Rivalutazione dei rapporti salvati

Come per le correzioni dell'8 settembre, i dieci rapporti già salvati sono
stati rivalutati offline con il valutatore nuovo, per **116 fasi**. Nessuna
nuova inferenza. Undici verdetti cambiano, tutti nella stessa direzione e
tutti sulle fasi `iniziato` e `decisione_ribadita`; nessun `fallito` e nessun
`da_revisionare` si muove, e nessun `superato` regredisce.

| Misura | Fasi | Superate prima | Superate dopo | Non conclusive prima | Non conclusive dopo |
| --- | ---: | ---: | ---: | ---: | ---: |
| `avvio-finale-20260908` (cloud) | 12 | 8 | 12 | 4 | 0 |
| `avvio-dopo-20260908` (cloud) | 12 | 5 | 11 | 7 | 1 |
| `audit-20260910` (cloud) | 6 | 2 | 3 | 3 | 2 |
| `v061-locale-20260908` (locale) | 6 | 1 | 1 | 4 | 4 |
| Altri sei rapporti | 80 | 55 | 55 | 4 | 4 |
| Totale | 116 | 71 | 82 | 22 | 11 |

Il primo risultato è la verifica della correzione: la misura finale dell'8
settembre passa a 12 fasi superate su 12, cioè il verdetto automatico
raggiunge la lettura manuale che quel confronto aveva già registrato, invece
di essere allineato a mano. Il rapporto locale della v0.6.1 resta **identico**:
i suoi quattro esiti non conclusivi hanno un'altra causa, quindi il limite del
modello locale non è stato toccato da questa modifica.

Le undici fasi ancora non conclusive hanno due cause, entrambe della sonda e
non della memoria:

- **nove** perché il modello aggiunge prosa attorno al JSON, mentre `valuta`
  lo accetta solo come blocco recintato e isolato;
- **due** perché la citazione omette il punto finale che il renderer di Agno
  mette prima della data: lo store dice «...ha confermato l'avvio dei
  lavori. [2026-09-10]» e la sonda cita «...ha confermato l'avvio dei lavori
  [2026-09-10]». Il controllo verbatim è la garanzia contro le citazioni
  inventate e non è stato modificato per questo.

Artefatto: `artifacts/memory-quality/equivalenza-rivalutazione-20260910.json`,
con gli hash dei rapporti originali e del valutatore, i conteggi vecchi e
nuovi e il verdetto per fase. I rapporti storici e le tabelle precedenti
restano invariati.

## Modello locale dopo la correzione, 10 settembre 2026

Misura di registrazione, non un tentativo di correzione: gli stessi due casi
dell'8 settembre, una ripetizione, timeout di 600 secondi per caso, con
`hf.co/empero-ai/Qwen3.8-9B-Distill-GGUF:Q8_0` nei due ruoli e Agno 3.0.5.
Serve a separare ciò che è del codice da ciò che è del modello, ora che il
valutatore non produce più falsi non conclusivi sull'avvio. Rapporto:
`artifacts/memory-quality/locale-post-equivalenza-20260910.json`.

| Caso | Fasi | Superate | Fallite | Non conclusive | Errori |
| --- | ---: | ---: | ---: | ---: | ---: |
| Avvio | 4 | 0 | 2 | 2 | 0 |
| Correzione | 2 | 0 | 0 | 2 | 0 |
| Totale | 6 | 0 | 2 | 4 | 0 |

L'esito è peggiore del giro dell'8 settembre, che sugli stessi casi dava 1
superata, 1 fallita e 4 non conclusive. Una ripetizione sola non stabilisce
una tendenza - la variabilità del modello resta il limite dichiarato del
protocollo - ma i due difetti già documentati si ripresentano, e il primo in
forma più netta:

- **contenuti non dichiarati nel profilo.** In tutte e quattro le fasi
  dell'avvio il profilo attribuisce all'utente una professione
  («Sviluppatore software») e uno stack di nove tecnologie («Python,
  JavaScript, React, Node.js, Docker, Kubernetes, AWS, Git, Linux»). Il
  dialogo sintetico non nomina nessuna delle due cose: parla solo di
  ORIONE-42. Sono contenuti inventati che entrano in memoria durevole e
  verrebbero reiniettati in ogni sessione futura. Come nelle misure
  precedenti questa è una constatazione manuale sullo store, non una fase
  del benchmark: i conteggi automatici non la vedono;
- **l'avvio confermato non sopravvive alla decisione ribadita.**
  `current_focus` arriva a `ORIONE-42: iniziato` dopo l'avvio esplicito e
  torna a `ORIONE-42: deciso` quando la decisione viene ribadita. Le due fasi
  escono **fallite**, non conclusive: la sonda non recupera un dato che era
  confermato. È lo stesso comportamento descritto per l'8 settembre.

Le due fasi della correzione, superate con i modelli cloud, qui restano non
conclusive perché la citazione non è verificabile negli store, e il profilo
resta `Unknown`: con questo modello l'estrazione non ha scritto nulla di
utile su cui interrogare.

Il confronto con la misura cloud dello stesso giorno - stesso codice, stessi
dialoghi, stessi criteri - è quindi di 3 fasi superate su 6 contro 0 su 6. La
conclusione dell'8 settembre resta valida e va rafforzata: **i risultati
cloud non si estendono al modello locale di serie**, e la qualità della
memoria con quel modello non è allo stato in cui la si possa dichiarare
verificata.

## Limiti del protocollo

La misura attuale isola estrazione e recupero su dialoghi brevi. Non copre
conversazioni libere complete, contraddizioni distribuite su molti turni,
archivi voluminosi, ricerche nella cronologia o attacchi alle istruzioni.
Le sonde sull'abbandono chiedono il progetto attuale; non verificano ancora
se una risposta libera di pianificazione riproponga spontaneamente il
progetto scartato, né cosa avvenga dopo un lungo intervallo di tempo. Le
sessioni distinte misurano il passaggio fra contesti, senza simulare giorni
trascorsi.
Il confronto fra modifiche va fatto mantenendo gli stessi casi, modelli e
opzioni, conservando entrambi i rapporti e confrontando anche le prove
testuali: poche ripetizioni non eliminano la variabilità dei modelli.

## Verifica locale prima della v0.6.1, 8 settembre 2026

Il modello locale di serie, `hf.co/empero-ai/Qwen3.8-9B-Distill-GGUF:Q8_0`,
è stato usato sia per la conversazione sia per l'estrazione, con Agno 3.0.5
ed embedder locale `nomic-embed-text-v2-moe`. La CI della PR #62 e del merge
`a7a593b` è verde su Ubuntu e Windows. La verifica locale ha invece
rilevato limiti che impediscono di dichiarare superati i controlli di
rilascio.

### Suite con Ollama

`tests/run.py --tutte` ha superato dieci suite su undici in 133,4 secondi.
La suite `intuizioni` fallisce perché, davanti alla richiesta di salvare
un criterio durante una conversazione inglese, il modello conserva il
contenuto in inglese. Il messaggio dell'utente non suggerisce la lingua
interna dell'archivio: la regola deve provenire dalle istruzioni di Ares.

Sono stati provati chiarimenti nella descrizione dei parametri dello
strumento e nella sequenza operativa del prompt. Hanno mostrato anche una
ricerca nel quaderno al posto di `search_learnings` e l'omissione della
ricerca nella sessione di riuso. Una prova intermedia ha salvato il criterio
in italiano, ma il giro completo finale è nuovamente fallito sulla lingua:
dieci suite su undici in 125,2 secondi. Questi ritocchi non sono stati
integrati, perché non hanno risolto il comportamento. I test non sono
stati indeboliti né i tentativi falliti riclassificati.

Log locali: `/tmp/ares-v061-local-suites-20260908.log`,
`/tmp/ares-v061-local-intuizioni-fix-20260908.log`,
`/tmp/ares-v061-local-intuizioni-guida-20260908.log` e
`/tmp/ares-v061-local-suites-finale-20260908.log`. La variante finale è
conservata in `artifacts/memory-quality/v061-prompt-experiments-20260908.patch`.

### Decisione, avvio e correzione

Una ripetizione dei casi `avvio` e `correzione`, con timeout di 600 secondi
per caso, ha prodotto il rapporto locale
`artifacts/memory-quality/v061-locale-20260908.json` e il relativo Markdown.
La misura è stata eseguita con i ritocchi sperimentali alle intuizioni
ancora presenti nei sorgenti: questo benchmark disattiva quello store e
usa una sonda non interattiva, quindi quelle istruzioni non entrano nel
percorso misurato. Gli hash nel rapporto identificano la variante eseguita.

| Caso | Fasi | Superate | Fallite | Non conclusive | Errori |
| --- | ---: | ---: | ---: | ---: | ---: |
| Avvio | 4 | 0 | 1 | 3 | 0 |
| Correzione | 2 | 1 | 0 | 1 | 0 |
| Totale | 6 | 1 | 1 | 4 | 0 |

Il codice d'uscita è **2**. La lettura delle evidenze mostra:

- dopo l'avvio esplicito, `current_focus` contiene `ORIONE-42: iniziato`;
  ribadire la decisione lo sostituisce con `ORIONE-42: deciso`. La sonda
  finale risponde con valore nullo e perde così l'avvio già confermato;
- i quattro esiti non conclusivi derivano da risposte vuote oppure testo
  aggiunto al JSON. Non sono successi: dopo l'avvio esplicito e nella prima
  preferenza, anche la parte JSON non recupera il fatto disponibile;
- già nella prima estrazione, il profilo attribuisce all'utente il nome del
  progetto, una professione e uno stack tecnologico non dichiarati. Il
  riepilogo del programma futuro aggiunge che il lavoro non è iniziato,
  senza una dichiarazione che lo sostenga;
- la correzione da risposte sintetiche a dettagliate viene conservata e
  recuperata correttamente.

I conteggi automatici non misurano tutti i contenuti non supportati dello
store: gli elementi inventati nel profilo sono una constatazione manuale,
non ulteriori fasi del benchmark. Questa prova non confronta il modello
locale con il codice precedente e non consente di attribuire tutti i limiti
alle modifiche della v0.6.1. Dimostra però che i risultati cloud non possono
essere estesi al modello locale senza una verifica specifica. Il rilascio
resta da rivalutare alla luce di questi risultati.
