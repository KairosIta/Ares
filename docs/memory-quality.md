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
| Astensione | 1 | Una domanda su un dato mai detto, senza alcun indizio nel dialogo, ha valore nullo e certezza sconosciuta. |
| Dimenticanza | 2 | Un dato annotato e poi ritirato («non ricordare più…») non torna dalla sonda e non resta fra le memorie valide: passa fra le superate, che prompt e benchmark non leggono. |
| Aggiornamento lungo | 2 | La correzione di una preferenza arriva dopo quattro scambi su altro, estratti uno per uno, e sostituisce il valore iniziale. |

Sono ventiquattro fasi per ripetizione, settantadue con le impostazioni
predefinite. Il rapporto porta anche **pass^3** per caso e per fase: la
probabilità che tre ripetizioni scelte a caso riescano tutte, stimata con
C(c, 3) / C(n, 3) su n ripetizioni e c successi (`evals/affidabilita.py`).
Un caso riesce in una ripetizione solo se tutte le sue fasi sono superate:
da revisionare e non conclusivo non contano, come nel resto del rapporto.
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
Lo schema 4 aggiunge a ciascuna fase i valori che il radicamento ha
scartato (`scartati`), elencati anche nel riepilogo Markdown.

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

## Costo delle estrazioni, 22 settembre 2026

Il report di analisi contava "tre store `ALWAYS`, tre inferenze in più per
turno". La prima metà è vera per costruzione; la seconda no: misurata il 22
settembre 2026 prima della mitigazione raccontata in fondo alla sezione, era
di cinque. Il costo andava misurato prima di decidere se il prezzo valeva la
qualità.

**Quante chiamate, prima.** `tests/learning_cost_test.py` costruisce la
macchina con un modello finto che conta e scrive: cinque chiamate al modello di
apprendimento per ogni turno completato, non tre.

| store | chiamate | perché |
| --- | --- | --- |
| profilo | 2 | scrive, poi richiama il modello per la conferma |
| memorie | 2 | idem |
| contesto di sessione | 1 | `stop_after_tool_call`, Agno salta la conferma |

La seconda chiamata di profilo e memorie non serve a niente: Agno la fa
perché il risultato della tool call non viene consumato, e la risposta di
conferma è scartata. È la stessa ragione per cui lo store del contesto la
evita, con un commento nel sorgente di Agno. Recuperarla vale due chiamate su
cinque, cioè il 40% delle estrazioni — ed è quello che è stato fatto.

**Quanto pesa.** Le stesse cinque chiamate, con un turno di quattro messaggi
(650 caratteri circa), rimandano al modello 33.649 caratteri:

| chiamata | istruzioni | turno rispedito | schemi | totale |
| --- | --- | --- | --- | --- |
| profilo, scrittura | 3.516 | 624 | 1.858 | 5.998 |
| profilo, conferma | 3.516 | 624 | 1.858 | 6.066 |
| memorie, scrittura | 5.123 | 613 | 1.828 | 7.564 |
| memorie, conferma | 5.123 | 613 | 1.828 | 7.613 |
| contesto | 4.543, con la conversazione dentro | — dentro le istruzioni | 1.776 | 6.408 |
| **totale** | **21.821** | **7.017** | **9.148** | **33.649** |

Il totale comprende anche i 206 caratteri dei messaggi di servizio: la
richiesta rivolta al contesto (89) e i due risultati di tool delle conferme
(117), che non sono né istruzioni né schemi.

La colonna «turno rispedito» è quella che la prova stampa: conta ogni
messaggio che contiene la domanda d'apertura, quindi 624 due volte per il
profilo, 613 due volte per le memorie, e per intero il messaggio di sistema
del contesto — 4.543 caratteri, che portano la conversazione dentro le
proprie istruzioni. Le due colonne si sovrappongono lì, e il totale non è la
loro somma: 21.821 + 7.017 + 9.148 fa più di 33.649.

Due terzi sono le istruzioni dei tre store — le regole di estrazione condivise
(`CRITERI_ESTRAZIONE`) più quelle di ciascuno — e il 27% gli schemi degli
strumenti. La conversazione viaggia comunque cinque volte, e il costo non è
il turno: è l'impianto fisso delle istruzioni, ripetuto a ogni chiamata.
Spegnere uno store toglie le sue chiamate; un modello che non chiama affatto
lo strumento costa i tentativi del contesto, perché profilo e memorie non
ritentano ma il contesto sì.

**Con i modelli veri.** `tests/e2e_test.py` stampa ora la riga che il client
mostra sotto la risposta, e accanto i token dell'apprendimento divisi fra
ingresso e uscita. Su un turno corto (288 caratteri) con
`deepseek-v4.1-flash:cloud` per conversazione ed estrazione:

```
costo del turno      - finestra 8.2k/256.0k (3%)  risposta 319 tok / 4.6 s  apprendimento 424 tok / 5.7 s  turno 15.0 s
token apprendimento  - 5602 in / 424 out
```

5.602 token di ingresso e 424 di uscita, per un turno di una riga (misurati
prima della mitigazione descritta in fondo alla sezione): il costo
dell'apprendimento sta quasi tutto in ingresso, ed è circa due terzi di
quello che il turno occupa nella finestra (8,2k). Il tempo si somma dopo la
risposta — 5,7 s su 15,0 s — e su un turno lungo cresce con il contenuto,
perché la conversazione viaggia in ogni estrazione.

**Cosa si può fare, in ordine di rapporto fra guadagno e rischio.**

1. **Fermare profilo e memorie dopo la tool call** (fatto il 22 settembre
   2026, vedi sotto), come fa il contesto: cinque chiamate diventano tre e
   spariscono due copie delle istruzioni. Richiede che Ares sovrascriva
   `_build_functions_for_model` nei due store, una superficie privata di
   Agno — lo stesso genere di appiglio che `AresSessionContextStore` usa già
   per il retry, sorvegliato da `tests/learning_cost_test.py`. Non cambia cosa
   si impara: la risposta di conferma è scartata anche oggi.
2. **Accorciare le istruzioni condivise.** `CRITERI_ESTRAZIONE` è ripetuto
   identico nei tre store e viaggia cinque volte per turno: una parte può
   stare in un blocco solo, o essere più breve. Tocca la qualità, quindi va
   misurata con il benchmark invece che decisa a tavolino.
3. **Estrarre meno spesso.** Profilo e memorie solo nei turni che sembrano
   contenere qualcosa di durevole, o ogni N turni. È una politica, non
   un'ottimizzazione: cambia cosa entra in memoria e quando.
4. **Cambiare modello all'estrazione.** `ARES_LEARNING_MODEL` è già separato
   dalla conversazione: un modello locale qui toglie il costo monetario e
   sposta quello in latenza. La misura con il modello locale di serie dice
   che la qualità scende, e il confronto è in questa pagina.

La prima è l'unica che non tocca la memoria; le altre vanno decise con il
benchmark in mano.

### La conferma tolta a profilo e memorie, 22 settembre 2026

`ares/agent/learning.py` costruisce ora i due store con
`AresUserProfileStore` e `AresUserMemoryStore`, che sovrascrivono
`_build_functions_for_model` per impostare `stop_after_tool_call` sulla loro
tool call: la funzione `senza_conferma` è l'unico punto nuovo, il nome dello
strumento è l'unica differenza fra i due. Agno esegue comunque la tool call —
il flag ferma il ciclo del modello *dopo* — quindi l'esito resta in
`response.tool_executions`, che è da dove `was_updated` e la conferma di Ares
leggono ciò che è stato scritto.

La stessa prova offline, sullo stesso turno finto, misura il dopo:

- **tre chiamate** invece di cinque: una per profilo, memorie e contesto;
- **19.970 caratteri** invece di 33.649 (-41%), di cui 13.182 di istruzioni
  (erano 21.821), 5.780 di turno rispedito (erano 7.017) e 5.462 di schemi
  (erano 9.148);
- spegnere uno store toglie esattamente una chiamata: 3 accese, 2 con uno
  spento, 0 con tutti e tre spenti;
- un modello che non chiama lo strumento costa ora **più** di uno che scrive —
  quattro chiamate contro tre, perché paga i tentativi del contesto: prima era
  il contrario, quattro contro cinque, e il testo sopra descriveva un caso che
  non si verificava.

Il numero di chiamate è asserito, non solo stampato: una patch di Agno che
reintroducesse la conferma rende rossa la prova invece di alzare una cifra nel
rapporto. Un'altra prova legge profilo e memorie dopo il turno e verifica che
contengano ciò che il modello ha passato: se il flag un giorno saltasse anche
la scrittura, il costo scenderebbe e la memoria resterebbe vuota.

Il carattere è una misura offline, che non dipende dal modello. Per avere il
numero vero, lo stesso turno — con una richiesta esplicita di ricordare, così
che entrambi gli store scrivano — è stato eseguito con
`deepseek-v4.1-flash:cloud` su un archivio nuovo, prima e dopo:

| | chiamate | token in ingresso | token in uscita |
| --- | --- | --- | --- |
| prima | 5 | 9.533 | 841 |
| dopo | 3 | 5.388 | 611 |

Il 43% di ingresso in meno è la stessa cosa che misura la prova offline: le due
chiamate tolte portavano 1.917 e 2.228 token, le più care delle cinque. Il
profilo e le memorie risultano scritti in entrambe le esecuzioni.

`tests/e2e_test.py`, che usa il turno corto della prova end-to-end invece di
questo, stampa `5274 in / 529 out` e un apprendimento di 2,2 s su un turno di
4,8 s. Quel turno non chiede di ricordare niente, quindi quanti store
scrivevano non è sotto controllo e i suoi token non sono un confronto: il
numero da guardare è la tabella qui sopra.

## Latenza dell'estrazione, 30 settembre 2026

Dopo ogni risposta l'utente aspetta le tre estrazioni. Stessi sei turni su un
archivio nuovo (presentazione, preferenze, un piano in tre passi, due
avanzamenti, un vincolo da ricordare), Agno 3.0.11, media per turno:

| Configurazione | Estrazione | Risposta | Modello in VRAM |
| --- | ---: | ---: | --- |
| locale Qwen3.8-9B Q8_0, contesto 256k | 33,8 s | 22,9 s | 14,4 GB di 19,5 |
| locale Q8_0, 128k | 14,0 s | 12,3 s | tutto (12,4 GB) |
| locale Q8_0, 64k | 12,7 s | 11,3 s | tutto (10,3 GB) |
| locale Q8_0, 32k | 13,2 s | 9,3 s | tutto (9,2 GB) |
| locale Qwen3.8-9B-heretic Q6_K, 32k | 13,1 s | 8,9 s | tutto (7,5 GB) |
| cloud `glm-5.3-flash`, in serie | 15,8 s | 2,7 s | — |
| cloud `glm-5.3-flash`, in parallelo | 11,6 s | 4,9 s | — |
| cloud `deepseek-v4.1-flash` in estrazione, in serie | 5,1 s | 4,2 s | — |
| cloud `deepseek-v4.1-flash` in estrazione, in parallelo | 3,0 s | 5,0 s | — |

Su una Radeon RX 7800 XT da 16 GB. La risposta in cloud varia molto fra un
turno e l'altro (da 1,5 a 16,8 s), quindi la sua colonna non è un confronto.

**In locale conta il contesto.** A 262.144 token il modello di serie non
entra in scheda e un quarto gira sulla CPU: risposta ed estrazione
rallentano entrambe di circa due volte e mezzo. Da 128k in giù sta tutto in
VRAM, e fra 128k e 32k la differenza è rumore: `NUM_CTX` di serie è ora
131.072, `ARES_NUM_CTX` lo cambia dal `.env` e `ares preflight` avvisa
quando il modello caricato non sta in VRAM. Un modello più piccolo a parità
di contesto non guadagna: la Q6_K impiega quanto la Q8_0.

**In cloud contano i token in uscita.** `glm-5.3-flash` ragiona anche con
`think: false`: il ragionamento finisce nel contenuto della risposta, che Ares
scarta. Il contesto di sessione arriva a 4.600 token in uscita, con 13.000
caratteri di ragionamento e 2.900 di argomenti. `deepseek-v4.1-flash`, con
`think: false`, non ragiona ed esce con 250–1.100 token. Quale modello
consigliare per l'estrazione è una questione di qualità, da decidere con
questo benchmark.

**In parallelo solo in cloud.** I tre store sono indipendenti. Con
l'estrazione cloud ora girano insieme (`AresLearningMachine.in_parallelo`) e
il turno aspetta il più lento invece della somma: -27% con glm, -41% con
deepseek. In locale Ollama serve una richiesta alla volta e in parallelo si
perde qualcosa (33,8 → 36,4 s), quindi resta in serie. Con il codice
definitivo, due coppie di esecuzioni una dopo l'altra, in serie e in
parallelo, su un servizio cloud più lento del solito: 47,1 → 27,4 s e
45,7 → 30,1 s, con profilo, memorie e contesto scritti in tutte. Dopo un Ctrl-C un
cancello ferma le scritture dei thread ancora in attesa del modello: il turno
fotografa la memoria subito dopo, e una scrittura tardiva sfuggirebbe a eco e
conferma.

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

### Esempi a contrasto nel prompt, 1 ottobre 2026

Il paragrafo sui ricordi del prompt di conversazione ha ricevuto tre esempi
a contrasto (correzione, proposta, avvio), lontani dai casi del benchmark.
Tutti i casi, 3 ripetizioni, `glm-5.3-flash:cloud` per la conversazione e
`deepseek-v4.1-flash:cloud` per l'estrazione, Agno 3.0.11. Fasi su 57:

| Prompt | Superate | Fallite | Da revisionare | Non conclusive | Errori |
| --- | ---: | ---: | ---: | ---: | ---: |
| `main` dopo #144 | 23 | 1 | 31 | 2 | 0 |
| con gli esempi | 25 | 1 | 28 | 3 | 0 |

La differenza sta dentro il rumore di tre ripetizioni: gli esempi non
peggiorano il recupero, e non si può dire che lo migliorino. L'unico
fallimento del ramo è di formato: alla correzione la sonda risponde
«risposte dettagliate» invece dell'aggettivo, con il dato giusto. Quello di
`main` è una proposta non accettata data per confermata. Le fasi da
revisionare sono quasi tutte risposte giuste con il valore conteso ancora
nello store, come previsto dal protocollo.

Prima di questa misura il benchmark non girava: dal 21 settembre il
processo importava la configurazione prima del worker, che la rifiuta.


### Il 27B locale, 1 ottobre 2026

Primo giro del benchmark su un modello locale diverso dal 9B di serie:
`ares-qwen3.8-27b`, copia con renderer di
`hf.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF:IQ3_S` (26,9B a 3,5 bit per
peso), in entrambi i ruoli, contesto 64k con la KV cache a 8 bit del daemon
(`OLLAMA_KV_CACHE_TYPE=q8_0`): 13,48 GiB in VRAM su 16, tutto in scheda. Il
campionamento è quello di Ares (temperatura 0,7 e 0,2, ragionamento acceso
solo in conversazione, default di Ollama per il resto). Tutti i casi, 3
ripetizioni, Agno 3.0.11, prompt di `main` dopo #148. Fasi su 57:

| Modello | Superate | Fallite | Da revisionare | Non conclusive | Errori |
| --- | ---: | ---: | ---: | ---: | ---: |
| cloud, con gli esempi (sezione precedente) | 25 | 1 | 28 | 3 | 0 |
| 27B IQ3_S, 64k, locale | 18 | 3 | 32 | 3 | 1 |
| 9B di serie, 10 settembre, soli casi `avvio` e `correzione` | 0 su 6 | 2 | — | 4 | 0 |

Rapporto: `artifacts/memory-quality/qwen3.8-27b-iq3s-20261001.json`, con
le fotografie di profilo e memorie per ogni fase. Letto fase per fase:

- **le tre fallite sono di formato**, lo stesso dell'unico fallimento cloud:
  alla domanda sull'aggettivo la sonda risponde «risposte dettagliate» o
  «risposte sintetiche» invece del solo aggettivo, e il valutatore confronta
  alla lettera. Nelle tre fasi lo store ha il dato giusto, e nella
  ripetizione 2 di `correzione` la memoria porta un refuso del modello
  («Prefere»), senza conseguenze sul recupero;
- **le 32 da revisionare sono risposte giuste**: 18 con il valore conteso
  ancora nello store (Milano come possibilità, ORIONE-42 come scartato), 14
  con negazioni o incertezze nel testo della memoria. Lette una per una,
  sono le sfumature corrette - «sta valutando», «non ha deciso», «avvio non
  confermato» - che il controllo lessicale segnala di proposito;
- **le tre non conclusive** (`abbandono_piano`) hanno la risposta giusta,
  VEGA-19, con una citazione che non è sottostringa letterale della memoria
  perché il modello salta una parentesi;
- **l'errore è del parser di Ollama**: una volta su 57 il modello emette
  una tool call con XML malformato e il daemon risponde con un HTTP 500
  (`XML syntax error on line 2: unexpected end element </function>`). Con
  il 9B non era mai successo; va tenuto d'occhio sulle misure successive;
- **nessun contenuto inventato nel profilo**, a differenza del 9B di serie
  del 10 settembre. I campi scritti sono `current_focus` (42 fasi),
  `language` «italiano» (29, dedotto dalla lingua dei dialoghi),
  `communication_style` (12, dai dialoghi sulle preferenze) e
  `tools_and_stack` «file Markdown per gli appunti» (6, detto nel dialogo
  sugli appunti). Niente professioni o tecnologie mai nominate;
- **le fasi dove il 9B perdeva passano**: `avvio/iniziato` e
  `decisione_ribadita` superate in tutte e tre le ripetizioni; `decisione` e
  `avvio_futuro` rispondono con valore nullo, come atteso, e sono da
  revisionare solo per i termini «non confermato» nello store.

Tempi medi per fase: estrazione 23,7 s, sonda 17,9 s; il giro intero dura
40 minuti. In chat sono circa 24 secondi di attesa dopo ogni risposta,
contro 14 del 9B a 128k e 11,6 del cloud in parallelo.

La conclusione dell'8 e del 10 settembre non cambia per il 9B di serie, ma
smette di valere per il locale in generale: con il 27B a 3,5 bit la memoria
di Ares ha, su questo protocollo, la stessa qualità misurata con i modelli
cloud, con un formato meno disciplinato nelle sonde. Il prezzo è il
contesto, 64k invece di 128k, e un'estrazione quasi doppia. Il confronto
regge perché codice, dialoghi e criteri sono gli stessi della misura cloud
del 1 ottobre.


### Latenza del 27B locale, 2 ottobre 2026

Stessi sei turni della misura del 30 settembre, su `ares-qwen3.8-27b` in
entrambi i ruoli a 64k di contesto con la KV cache a 8 bit del daemon,
apprendimento acceso, campionamento di Ares. La misura è fatta con il
nucleo (`Sessioni` e `esegui_turno`, un client che non stampa e rifiuta
ogni azione), ed è quella che `python -m evals.latenza` ripete; i tempi sono
`total_duration` di Ollama per ciascun modello, il turno intero comprende
anche Agno e gli strumenti.

| Turno | Risposta | Estrazione | Turno intero | Finestra | Token in uscita | Appreso |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| presentazione | 42,0 s | 26,6 s | 69,8 s | 8.462 | 321 | 8 righe |
| preferenze | 25,1 s | 31,9 s | 68,0 s | 9.510 | 164 | 5 |
| piano | 225,3 s | 45,4 s | 283,8 s | 13.803 | 4.144 | 5 |
| avanzamento 1 | 218,7 s | 42,1 s | 284,1 s | 18.278 | 3.893 | 3 |
| avanzamento 2 | 165,5 s | 54,5 s | 280,4 s | 21.112 | 2.599 | 2 |
| vincolo | 268,7 s | 62,3 s | 347,5 s | 25.784 | 4.252 | 2 |
| **media** | **157,5 s** | **43,8 s** | **222,3 s** | | | |

Il modello è rimasto tutto in scheda (13,48 GiB) per l'intera misura, 22
minuti. Tre letture:

- **la risposta è di due specie.** I turni di sola conversazione costano
  25-42 secondi; quelli in cui il modello decide di lavorare - dal terzo in
  poi apre il quaderno, scrive il piano in `progetti/backup-studio.md`, lo
  aggiorna, prova `remember_about` - costano da 165 a 269 secondi, con
  2.600-4.250 token in uscita fra ragionamento e strumenti. La velocità di
  generazione che se ne ricava è di 15-20 token al secondo. La media del 30
  settembre sul 9B (12,3 s) non dice quanti strumenti usò, quindi non è
  confrontabile: da questa misura l'eval separa le medie dei turni con e
  senza strumenti;
- **l'estrazione cresce con la finestra**, da 27 a 62 secondi mentre il
  contesto passa da 8,5k a 26k token, perché il contesto di sessione rimanda
  la conversazione dentro le proprie istruzioni. In media 44 secondi, oltre
  il triplo del 9B a 128k (14,0 s);
- **la finestra si riempie come stimato** in
  [agentic-improvements.md](agentic-improvements.md#23-quanto-contesto-sta-in-scheda):
  25,8k su 64k dopo sei turni. A questo ritmo il 27B satura intorno al
  quindicesimo turno, e Ollama tronca oltre `num_ctx` senza avvisare.

Un errore di forma: una chiamata a `remember_about` è arrivata senza
`entity_type` ed è stata rifiutata dalla validazione di Agno; il modello ha
riprovato. È il secondo del 27B in una tool call, dopo l'XML malformato del
benchmark.

Letto insieme ai due eval, il 27B a 3,5 bit ha la qualità del cloud su
strumenti e memoria e nessuna invenzione nel profilo, ma un turno operativo
da quattro o cinque minuti e tre quarti di minuto di attesa dopo ogni
risposta. È il modello per chi accetta di aspettare in cambio della
privacy, non un sostituto del 9B per la chat veloce.

### Astensione, dimenticanza e aggiornamento lungo, 2 ottobre 2026

I tre casi nuovi, tre ripetizioni, regime di Ares, con lo stesso modello
per conversazione ed estrazione
(`artifacts/memory-quality/casi-nuovi-*-20261002.json`):

| Caso, fase | 9B di serie, 128k | 27B IQ3_S, 64k |
| --- | --- | --- |
| `astensione`, mai detto | 3 superate | 3 superate |
| `dimenticanza`, annotata | 3 superate | 3 superate |
| `dimenticanza`, dimenticata | 3 superate | 3 superate |
| `aggiornamento_lungo`, iniziale | 3 superate | 2 superate, 1 errore |
| `aggiornamento_lungo`, corretta dopo altro | 3 da revisionare | 3 da revisionare |
| pass^3 per caso | 1,00 / 1,00 / 0,00 | 1,00 / 1,00 / 0,00 |
| media sui casi | 0,67 | 0,67 |

Letture.

- **Astensione.** Senza alcun indizio nel dialogo nessuno dei due modelli
  inventa il gatto: valore nullo, certezza sconosciuta, e negli store non
  compare niente di felino. Tre su tre per entrambi.
- **Dimenticanza.** Dopo «non ricordare più il numero della tessera» la
  memoria viene cancellata davvero: nella fotografia dopo la fase la lista
  delle memorie è vuota, e la sonda si astiene. Tre su tre per entrambi: la
  dimenticanza su richiesta, che il protocollo non misurava, funziona con
  l'estrazione di serie.
- **Aggiornamento lungo.** Dopo quattro scambi su altro la correzione passa:
  valore «Helix» con certezza confermata in tutte e sei le ripetizioni, con
  una citazione che la sostiene. Il verdetto è «da revisionare» perché lo
  store conserva anche «ha abbandonato Vim», e il termine vecchio è nella
  lista da rivedere: alla lettura è una memoria giusta, la storia della
  preferenza e non una contraddizione, come già per `correzione`. Il
  valutatore resta severo per scelta; pass^3 è zero per questo. Sul 27B
  una sonda della fase iniziale è finita in errore HTTP 500 per una tool
  call con XML malformato, lo stesso guasto del 1 ottobre.
- **Costo.** La fase corretta vale cinque estrazioni: 33-38 s sul 9B,
  145-156 s sul 27B. Le altre fasi 5-11 s di estrazione sul 9B, 19-39 s sul
  27B; le sonde 3-6 s e 6-22 s.

### MiMo-V2.6-Distill-Qwen-9B, 2 ottobre 2026

Lo stesso giro del 27B su un terzo 9B: **`ares-mimo-2.6-9b`**, copia con
renderer di `hf.co/bartowski/MiMo-V2.6-Distill-Qwen-9B-GGUF:Q8_0`, in
entrambi i ruoli, contesto 128k (11 GB in VRAM, tutto in scheda), regime di
Ares, tutti gli undici casi (compresi i tre del 2 ottobre), tre
ripetizioni, Agno 3.0.11, prompt di `main` dopo #155. Fasi su 73:

| Modello | Superate | Fallite | Da revisionare | Non conclusive | Errori |
| --- | --- | ---: | ---: | ---: | ---: |
| 27B IQ3_S, 64k, 1 ottobre, 57 fasi | 18 | 3 | 32 | 3 | 1 |
| MiMo 2.6 9B, 128k, 73 fasi | 15 | 8 | 13 | 29 | 8 |

Rapporto: `artifacts/memory-quality/mimo-2.6-9b-20261002.json`. Il
confronto con il 27B regge a meno dei sedici casi nuovi; quello che conta è
la lettura fase per fase, che qui è netta:

- **l'estrattore è debole.** In undici fasi su 73 `save_session_context`
  non salva al primo tentativo, e in cinque nemmeno al secondo: la fase
  finisce in errore («contesto di sessione non salvato»). Il modello passa
  alla funzione argomenti che non passano la validazione (`progress`) o
  chiude una tool call con XML malformato. Gli altri tre errori sono un
  timeout, una sonda che non completa il turno e la fase interrotta dal
  timeout. Il 27B ha un errore su 57, il 9B di serie nessuno nelle sue
  misure;
- **la sonda non rispetta il formato**: sette fasi non conclusive per JSON
  non leggibile (testo prima dell'oggetto, due oggetti, campi mancanti) e
  diciotto perché l'evidenza citata non è una sottostringa dello store, ma
  una riga riscritta («Communication Style: Risposte dettagliate»). Sono
  risposte quasi sempre giuste che il valutatore, di proposito, non conta;
- **delle otto fallite, cinque sono di formato** (l'aggettivo chiesto solo
  come valore arriva come «Risposte dettagliate», lo stesso fallimento del
  cloud e del 27B) e **tre sono vere**: in `dimenticanza` due volte su tre
  non recupera la tessera appena annotata, e la terza ripetizione di
  `temporanea` risponde «sconosciuto» alla preferenza stabile;
- **il profilo contiene segnaposto e deduzioni**, a differenza del 27B. In
  nove fasi `name` vale «Non specificato» o «Gym member», in sette
  `timezone`, `occupation` e `preferred_name` valgono «Non specificato» o
  «Sconosciuto», e `expertise` porta «Sviluppo software» in dialoghi che
  parlano solo di un editor di testo. Non sono le invenzioni del 9B di
  serie del 10 settembre (professioni e tecnologie mai nominate), ma sono
  campi scritti senza che il dialogo li dica, e un profilo pieno di «Non
  specificato» torna in ogni prompt;
- **le fasi nuove**: `astensione` tre superate come gli altri due;
  `dimenticanza` una superata su sei; `aggiornamento_lungo` nessuna
  superata, con la fase lunga a 67-88 secondi.

Tempi medi per fase: estrazione 11,0 s, sonda 10,2 s; il giro intero dura
28 minuti, contro 40 del 27B. In chat l'eval della latenza misura 11,6 s di risposta e 20,1 s di
estrazione per turno (`artifacts/latenza/mimo-2.6-9b-20261002.json`),
contro 12,3 e 14,0 del 9B di serie a 128k: la risposta è pari, l'estrazione
costa metà in più, perché ripete il tentativo quando non salva.

La conclusione del §2.4 vale alla lettera: un modello che migliora l'eval
degli strumenti ma scrive segnaposto nel profilo e non salva il contesto
in una fase su sette non è un miglioramento per la memoria di Ares. Come
modello di conversazione da solo, con il 9B di serie o il cloud a
estrarre, resta da misurare; come modello unico non è un candidato.

### Radicamento dell'estrazione, 2 ottobre 2026

Primo passo della proposta 3.6 di
[agentic-improvements](agentic-improvements.md): profilo e memorie salvano
solo ciò che ha un appiglio nella conversazione o nello store
(`ares/agent/radicamento.py`). Il rapporto porta ora, per fase, i valori
scartati (`scartati`, schema 4). A parità di tutto il resto, gli scarti di
una fase sono esattamente ciò che il codice di prima avrebbe scritto in più.

**MiMo-V2.6 9B, stesso giro del mattino.** Tutti gli undici casi, tre
ripetizioni, `ares-mimo-2.6-9b` nei due ruoli, 73 fasi. Rapporto:
`artifacts/memory-quality/radicamento-mimo-2.6-9b-20261002.json`.

| Giro | Superate | Fallite | Da revisionare | Non conclusive | Errori |
| --- | ---: | ---: | ---: | ---: | ---: |
| prima, mattino | 15 | 8 | 13 | 29 | 8 |
| con il radicamento | 11 | 18 | 13 | 26 | 5 |

- **Il profilo non ha più segnaposto.** Al mattino `name` valeva «Non
  specificato» o «Gym member» in nove fasi, `preferred_name`, `timezone`
  e `occupation` valevano «Non specificato» o «Sconosciuto» in sette fasi
  ciascuno, ed `expertise` portava «Sviluppo software» in quattro. Ora
  nessuno di questi valori è negli store. Gli scarti sono 37, tutti
  segnaposto tranne due meta-valori («Italiano» come stile di
  comunicazione, «nessun progetto o obiettivo attuale dichiarato» come
  obiettivo). **Nessun contenuto detto nel dialogo è stato scartato.**
- **Le fallite in più non vengono dal radicamento.** In 17 fasi fallite su
  18 il dato atteso è nello store, e nessuna di quelle 17 ha scarti: la
  sonda del MiMo risponde `null`, o riscrive il valore e la citazione. Una
  parte viene dagli errori del mattino, che ora arrivano alla sonda e lì
  falliscono (`correzione/iniziale`: da 2 errori a 3 fallite). La
  diciottesima è `personaggio`, dove il nome del personaggio, Livia
  Vesper, entra nel profilo: il nome è nel testo, e distinguere un gioco
  di ruolo da un fatto è giudizio, non un controllo lessicale.
- **Il costo non cambia**: estrazione media 10,9 s contro 11,0, nessuna
  chiamata in più.

In questo giro il MiMo non ha più scritto «Sviluppo software», quindi le
invenzioni di tecnologie non sono state messe alla prova.
**9B di serie, i sei casi del 10 settembre.** `avvio` e `correzione`, tre
ripetizioni, `ares-qwen3.8-9b` (copia con renderer) nei due ruoli, 18 fasi:
4 superate, 7 fallite, 2 da revisionare, 5 non conclusive, nessun errore,
in 4,4 minuti con 6,4 s di estrazione media. Rapporto:
`artifacts/memory-quality/radicamento-9b-20261002.json`.

- **Nessuno scarto.** Il 9B di oggi non scrive più professioni né
  tecnologie mai nominate: il profilo porta solo `current_focus` e
  `communication_style`, entrambi dal dialogo. Lo stack di nove tecnologie
  del 10 settembre veniva dal modello importato senza renderer e dal prompt
  di allora; con renderer e prompt di `main` non si ripresenta, quindi qui
  il radicamento non ha niente da fermare.
- **Le sette fallite sono della sonda, non dello store**, che in ogni fase
  contiene il dato giusto. Cinque sono di formato: «sintetica» o
  «dettagliata» invece della forma attesa, o `null` con lo stato
  «iniziato» scritto nel profilo. Due sono errori di giudizio veri: dopo
  la sola decisione la sonda risponde «iniziato» o «non_iniziato» come
  confermato.

**Lettura.** Il radicamento fa ciò che promette e niente di più: toglie dal
profilo i valori senza appiglio (segnaposto, nomi e professioni dedotti)
senza scartare contenuti detti, e non costa inferenze. Non migliora il
giudizio dell'estrattore né il recupero, che restano i limiti dei 9B in
questo benchmark; il passo 2 della proposta, l'uscita vincolata, mira alla
forma delle estrazioni.

### Contesto di sessione vincolato dallo schema, 2 ottobre 2026

Secondo passo della proposta 3.6: con un estrattore locale l'estrazione
chiede un oggetto JSON vincolato dallo schema (`format` di Ollama,
temperatura 0, nessuno strumento nella stessa richiesta) invece della tool
call, e lo applica con la stessa funzione. `ARES_ESTRAZIONE_VINCOLATA=0` lo
spegne; con il cloud resta la tool call. Tre giri sul MiMo-V2.6 9B, tutti
i casi, tre ripetizioni, dopo il radicamento: senza vincolo (la sezione
precedente), con il vincolo su profilo e contesto, con il vincolo sul solo
contesto. Rapporti `radicamento-`, `vincolata-` e
`contesto-vincolato-mimo-2.6-9b-20261002.json`.

| MiMo 9B | Superate | Fallite | Da revisionare | Non conclusive | Errori | Contesto al 1° tentativo | Estrazione | `current_focus` scritto |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| tool call | 11 | 18 | 13 | 26 | 5 | 66 su 72 | 10,9 s | 32 fasi |
| profilo e contesto vincolati | 9 | 29 | 2 | 32 | 0 | 72 su 72 | 6,8 s | 9 fasi |
| solo il contesto vincolato | 13 | 16 | 15 | 27 | 1 | 72 su 72 | 6,9 s | 31 fasi |

- **Sul contesto il vincolo toglie il guasto più frequente del MiMo.** Il
  contesto si salva sempre al primo tentativo, nessuna fase finisce in
  errore per «contesto non salvato» (erano tre, più un timeout e una fase
  interrotta), e l'estrazione costa un terzo in meno perché non ripete il
  tentativo. L'unico errore rimasto è della sonda.
- **Sul profilo il vincolo fa astenere il modello.** Con la grammatica il
  MiMo mette null nei campi che con lo strumento scriveva: `current_focus`
  passa da 32 fasi a 9, e ciò che serviva fra una sessione e l'altra resta
  solo nel contesto della sessione. `recupero` passa da 2 superate su 3 a
  0, e le fallite da 18 a 29, quasi tutte con la sonda che risponde null
  mentre il dato è negli store, ma non nel profilo che lei legge. È il costo del vincolo sul giudizio «c'è qualcosa da
  salvare?» descritto in [agentic-improvements](agentic-improvements.md).
  Il profilo resta quindi sulla tool call, ripulito dal radicamento.
- **Con il solo contesto vincolato** i benefici restano e il profilo torna
  com'era: 13 superate e 16 fallite, il miglior giro del MiMo di oggi,
  dentro il rumore di tre ripetizioni ma senza la perdita di prima.

**9B di serie**, `avvio` e `correzione` per tre ripetizioni (`recupero`
aggiunto solo nell'ultimo giro):

| 9B di serie | Superate | Fallite | Da revisionare | Non conclusive | Estrazione | `current_focus` scritto |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| tool call | 4 | 7 | 2 | 5 | 6,4 s | 12 fasi su 18 |
| profilo e contesto vincolati | 5 | 4 | 3 | 6 | 5,2 s | 0 |
| solo il contesto vincolato | 6 | 6 | 1 | 5 | 5,1 s | 12 |

Il 9B salvava già il contesto al primo tentativo: qui il vincolo porta
solo un'estrazione più rapida. Anche il 9B, col profilo vincolato, non
scrive mai `current_focus`; `recupero`, con il solo contesto vincolato, è
superato in tre ripetizioni su tre.

### Memorie superate e provenienza, 2 ottobre 2026

Con la proposta 3.7 una memoria corretta o tolta passa fra le superate di
`AresMemories` invece di sparire, e il benchmark, come il prompt, legge solo
le valide. Verifica di non regressione sui tre casi che correggono o tolgono
una memoria, tre ripetizioni, `glm-5.3-flash:cloud` per la conversazione e
`deepseek-v4.1-flash:cloud` per l'estrazione: 15 fasi superate su 18,
nessuna fallita o in errore. Ogni fase `corretta` e `dimenticata` lascia
esattamente una superata. Le tre da revisionare sono `aggiornamento_lungo`,
come prima della modifica: la memoria valida dice che l'utente ha
abbandonato Vim, e il valutatore segnala di proposito il valore conteso.
Rapporto: `artifacts/memory-quality/provenienza-cloud-20261002.json`.

Il benchmark chiama la macchina di apprendimento direttamente, senza il
turno del nucleo: la provenienza la provano `ambiti` offline e un turno vero
sul cloud («uso Helix», poi «sono passato a Zed»), dove la memoria valida e
quella superata portano sessione, turno, cartella e data, e il prompt
contiene solo la valida, con la data e l'id della conversazione: circa
una quindicina di token in più per memoria.

### Consolidamento delle memorie, 2 ottobre 2026

Due prove dal vivo della provenienza hanno lasciato in archivio la stessa
memoria due volte: una identica, una con un inciso in più («Usa Zed come
editor per scrivere codice» e la stessa frase con «informazione comunicata
direttamente dall'utente»). L'estrattore vede le memorie salvate, ma un
modello piccolo a volte le riscrive. Le risposte sono due, una sul percorso
caldo e una a freddo.

Sul percorso caldo, `add_memory` dell'estrazione rifiuta una memoria
identica a una valida a meno di maiuscole, spazi e punto finale
(`chiave_memoria`): non costa una chiamata e non tocca il giudizio del
modello. Le quasi uguali passano, perché distinguerle richiede un giudizio.

A freddo, `ares memories consolidate` confronta le coppie candidate. Le
misure che ne hanno deciso la forma sono su 13 coppie scritte a mano: 4
doppioni con parole diverse, 3 memorie superate da una più recente, 6
coppie distinte sullo stesso tema.

L'embedding da solo non separa. Con `nomic-embed-text-v2-moe` il coseno dei
doppioni va da 0,57 a 0,91, quello delle superate da 0,69 a 0,74, quello
delle distinte da 0,16 a 0,63 («scrive i commit in italiano» e «scrive la
documentazione in italiano»: 0,63). Il prefisso `search_query:` non
cambia il quadro. La soglia è quindi un filtro largo, 0,5, con al massimo
tre vicine per memoria, e la decisione passa al modello.

Il giudizio è una scelta fra tre parole per coppia, a temperatura 0.
Tre ripetizioni delle 13 coppie, 39 giudizi:

| Modello di apprendimento | JSON vincolato | Una parola libera |
| --- | ---: | ---: |
| Qwen3.8 9B Distill Q8_0, di serie | 39/39 (17 s) | 39/39 (6 s) |
| MiMo-V2.6-Distill 9B | 39/39 (16 s) | 27/39 (16 s) |
| Ornith 1.5 9B | 39/39 (13 s) | 39/39 (16 s) |
| `deepseek-v4.1-flash:cloud` | — | 39/39 (21 s) |

Gli errori di MiMo senza vincolo sono tutti «superata» al posto di
«doppione», che portano allo stesso esito quando la più recente è anche
la più completa: la più vecchia si ritira. In
locale la richiesta è vincolata dallo schema; il cloud, che non applica lo
schema, risponde con una parola. Una risposta illeggibile vale «distinte»,
l'unica che non tocca niente.

Sull'archivio delle prove dal vivo il comando trova il doppione con un
giudizio, lo ritira dopo la conferma e il backup, e una seconda passata
non trova altro. La memoria che resta è la più recente: qui è quella con
l'inciso, e la regola è dichiarata invece di affidare al modello anche la
scelta del testo migliore. La frequenza dell'estrazione non cambia: il
consolidamento è un comando che dai tu, come il backup.
