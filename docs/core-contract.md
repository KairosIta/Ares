# Nucleo condiviso di Ares: identità, responsabilità e contratto

Data: 13 settembre 2026.

**Stato: proposta di progettazione, non implementata.** Questo documento
approfondisce il primo passo della [roadmap](../ROADMAP.md): rendere Ares
indipendente dall'interfaccia, collegando il contratto ai cinque punti sulla
memoria. Le scelte proposte sono distinte dai fatti verificati nel codice.
Non cambia la configurazione dell'apprendimento né introduce una UI.

**Aggiornamento del 21 settembre 2026.** I due difetti riprodotti al §2 sono
stati corretti: l'identità dell'utente ha un solo punto di normalizzazione
(`ares/state/identita.py`) usato da namespace, profilo/memorie, lock e
manutenzione, e l'ID di sessione non collide più fra cartelle omonime o avvii
nello stesso secondo. L'alfabeto dell'id è esplicito — lettere e cifre ASCII,
punto, trattino e trattino basso — ed è l'insieme che il FileSystem di Agno
lascia intatto: fuori di lì un id verrebbe percent-encodato o anniderebbe il
namespace, quindi è rifiutato all'ingresso. Il debito di migrazione che ne
deriva è dichiarato al §3, "Migrazione dell'identità". Le prove sono in
`tests/cli_test.py`. Il resto del documento — tipi di configurazione passati
ai costruttori, sessioni e turni come operazioni del nucleo, eventi e
autorizzazioni — resta una proposta non implementata.

## 1. Risultato atteso e perimetro

A parità di utente, sessione, contesto, politiche e richieste, CLI, TUI e
desktop devono produrre gli stessi effetti. Possono presentare i dati in
modi diversi, ma non decidere separatamente quando apprendere, quali
operazioni autorizzare, dove scrivere o come proteggere gli archivi.

Il primo risultato è una libreria applicativa Python usata dalla CLI e da
un client di test senza terminale. Trasporto locale, processo desktop,
framework grafico, AgentOS e servizio HTTP non sono prerequisiti di questa
fase. Il contratto deve essere serializzabile, senza implementare ora un
server o garantire riprese distribuite.

Non riscriviamo Agno né duplicheremo tutti i suoi modelli. Il nucleo Ares
possiede le politiche del prodotto; l'adattatore usa le primitive Agno.
Le API pubbliche coprono soltanto le operazioni necessarie ad Ares.

## 2. Evidenze nel codice attuale

| Area | Evidenza | Conseguenza per il contratto |
| --- | --- | --- |
| Configurazione | `config.leggi_percorsi()` e `config.leggi_impostazioni()` costruiscono, al confine del processo, i percorsi e i modelli di questa conversazione; ogni lettore li riceve come parametri. Identità, percorsi e impostazioni sono tre assi, non più nomi di modulo | Una conversazione deve ricevere una configurazione risolta propria, e i modelli applicativi non devono leggere né scrivere un globale |
| Costruzione | `build_assistant` riceve percorsi, impostazioni, utente, sessione, modalità e `interattivo`; il workspace non è un parametro a sé | Il workspace sceglie i percorsi, non il costruttore: `--workspace` è una sostituzione locale, e resta da decidere se il progetto debba essere un campo a sé |
| Turno | `turn_core` separa lo streaming dal terminale, ma espone `RunOutput` e oggetti generici | Conservare l'adattamento esistente e completare i dati pubblici |
| Memoria | `cli/chat.py` coordina fotografia, differenze, conferma e ripristino | Il client che usa soltanto `turn_core` non eredita queste politiche |
| Lock | Il lock del turno avvolge il flusso nella CLI; quello dello stato dura quanto la chat | Coordinamento e durata devono appartenere al servizio applicativo |
| Sessioni | `cli/cartella.py` genera gli ID; `cli/commands.py` ricostruisce l'agente per cambiare sessione/modalità | Creazione e apertura devono diventare operazioni condivise |
| Autorizzazioni | `cli/render.py` chiama `confirm/reject` sui requirement Agno; la cartella consulta `isatty()` | La presenza di un terminale non può determinare le capacità della desktop |
| Manutenzione | Primitive riutilizzabili esistono, ma alcuni flussi uniscono conferma, backup, scrittura e stampa | Estrarre l'operazione completa, mantenendo il rendering nei client |

Riferimenti locali: [config](../ares/config.py),
[costruzione](../ares/agent/assistant.py), [runtime](../ares/agent/runtime.py),
[turno](../ares/agent/turn_core.py), [CLI](../ares/cli/chat.py),
[comandi](../ares/cli/commands.py), [cartella](../ares/cli/cartella.py),
[conferme degli strumenti](../ares/cli/render.py),
[lock](../ares/state/lock.py), [archivi](../ares/state/archivi.py).

### Due problemi riprodotti senza modello o archivi

Sono state eseguite le funzioni estratte dai sorgenti con AST e dipendenze
minime simulate, senza importare Ares né aprire database o lock reali:

1. `nuovo_id_sessione(Path('/progetti/a/api'), datetime(2026, 9, 13, 12))`
   e la stessa chiamata con `/progetti/b/api` restituiscono entrambe
   `api-20260913-120000`. Anche due aperture nella stessa cartella nello
   stesso secondo coincidono. Il nome leggibile non è un ID univoco.
   **Corretto il 2026-09-21:** l'ID conserva il prefisso leggibile e aggiunge
   una coda casuale; `tests/cli_test.py` prova che cartelle omonime e avvii
   nello stesso secondo danno ID distinti.
2. `namespace_utente('Demo') == namespace_utente('demo')`, mentre
   `lock_turno` deriva nomi di lock diversi dai due valori originali.
   Namespace e concorrenza non applicano la stessa equivalenza dell'utente.
   **Corretto il 2026-09-21:** `ares/state/identita.py` è il solo punto di
   normalizzazione; namespace, profilo/memorie (via `build_assistant`), lock,
   manutenzione e ispezione lo usano.

Il secondo punto non dimostra una contaminazione osservata nei dati:
dimostra che due valori equivalenti per alcuni archivi non sono coordinati
dallo stesso lock. Profilo e User Memory ricevevano inoltre il `user_id`
originale: non tutti gli archivi applicavano quella normalizzazione. **Ora
tutti gli archivi ricevono la forma canonica:** profilo e User Memory per
`user_id`, entità e intuizioni per namespace.

Nello schema SQLite di Agno 3.0.9, `session_id` è la chiave primaria della
tabella delle sessioni. L'upsert verifica il proprietario in caso di
conflitto, ma non crea un'identità distinta per progetto. Occorre un ID
univoco nell'archivio, oltre ai controlli di appartenenza.

## 3. Identità: cinque concetti distinti

| Concetto | Significato proposto | Regola |
| --- | --- | --- |
| Utente | La persona a cui appartengono preferenze e ricordi | Un ID canonico usato identicamente da store, lock e sessioni |
| Progetto | Continuità logica del lavoro, indipendente da dove si trova | ID stabile; nome e percorsi sono attributi modificabili |
| Workspace | Una copia concreta dei file su cui operare | Percorso risolto e autorizzato, associato eventualmente a un progetto |
| Sessione | Una conversazione persistente | ID generato dal nucleo, distinto dal titolo, con proprietario e ambito |
| Turno | Un'esecuzione con eventuali pause e riprese | Identità stabile attraverso `continue_run`, distinta da una nuova richiesta |

### Proposta per gli ID

- Generare ID opachi, per esempio UUID, per progetti e sessioni. Conservare
  titoli e alias leggibili separatamente. Gli alias ambigui restituiscono
  un errore o candidati, mai una sessione scelta arbitrariamente.
- Per l'utente, risolvere l'identità all'ingresso una volta sola. Proposta
  minima per gli identificativi testuali attuali: normalizzare e validare
  una volta, poi usare il risultato ovunque. Nome visualizzato separato.
  Prima di implementare, definire alfabeto ammesso, valori vuoti e collisioni;
  evitare una seconda normalizzazione implicita nel FileSystem.
- Non usare `user_id = utente + progetto`: frammenterebbe il profilo
  personale e mescolerebbe proprietà e ambito. I filtri di progetto sono
  una dimensione separata.
- Un ID non è un'autorizzazione: apertura, lettura e ripresa verificano
  proprietario e associazione al progetto nel servizio.

### Migrazione dell'identità (debito dichiarato)

L'ID utente è una chiave **derivata**: `utente_canonico` la ricalcola
dall'input a ogni avvio. Non è un dato scritto una volta, è la funzione che
punta a profilo, sessioni e namespace; cambiarla non aggiorna niente, ri-punta
le letture. Con un archivio pieno ogni modifica della regola diventa una
migrazione di chiave in tre posti — SQLite delle sessioni, profilo/User
Memory, namespace di entità/intuizioni/quaderno — e Ares non registra con
quale regola un archivio è stato scritto: il disallineamento non è un errore,
è un profilo che risulta vuoto.

- L'alfabeto di `ares/state/identita.py` è la **regola 1**. Gli archivi
  scritti prima sono esenti solo perché vuoti: non c'è una migrazione, e non
  è una garanzia permanente.
- La regola 1 non è più una funzione da ricordare: il tipo `Utente` la rende
  un invariante. `Utente.da_grezzo` è l'unica porta e rifiuta una scrittura
  non canonica, quindi dalla porta in poi non esiste una seconda grafia con
  cui cercare — e un lettore nuovo non può dimenticarsi niente, perché una
  stringa grezza non è un `Utente`. `utente_canonico` resta la regola, ma
  chiamata una volta sola al confine del programma.
- La validazione precede ogni lettura. Un id che oggi è invalido
  (`café`, `demo/personale`) non è nominabile dalla CLI: migrarlo è
  un'operazione di basso livello che salta `utente_canonico` e legge le chiavi
  grezze. "Migrare l'identità" non sarà un normale `ares chat`.
- Non è una minuscolizzazione. Un archivio scritto prima della regola aveva
  già entità e quaderno sotto l'id minuscolo e profilo e sessioni sotto quello
  grezzo: se esistono entrambe le grafie, unirle è una collisione da decidere,
  non da fondere in silenzio.
- Un ID **generato** e opaco (sessione, progetto) non ha questo debito: si
  conserva e si legge esatto, e una nuova regola di generazione non rende
  invisibile ciò che esiste. Il debito riguarda solo le chiavi derivate.
- Prima dei dati veri servono due cose: registrare nell'archivio la regola con
  cui è stato scritto, e saper elencare gli id presenti. La migrazione, quando
  servirà, ha la forma di `ares/ops/migrazione.py`: lock esclusivo,
  idempotente, conflitti dichiarati e mai toccati, backup prima, chat ferma
  finché non è conclusa.

### Proposta per il riconoscimento dei progetti

Un registro locale associa progetti a workspace conosciuti. La prima
registrazione può essere automatica e non bloccante per una cartella non
ambigua; il nucleo restituisce la risoluzione effettuata. La cartella
corrente è una comodità dell'adattatore CLI, non una dipendenza del nucleo.

| Caso | Comportamento proposto |
| --- | --- |
| Sottocartella di un workspace noto | Risolve il progetto noto; l'ambito effettivo dei file resta esplicito, senza allargarlo silenziosamente alla root |
| Due progetti registrati annidati | Vince l'associazione più specifica; eventuali conflitti vanno dichiarati |
| Cartella senza Git | È un workspace valido; Git non è richiesto per l'identità |
| Spostamento | Riassociazione esplicita al progetto esistente; nessun riconoscimento dal solo nome |
| Clone o worktree nuovo | Proposta di collegamento possibile; condivisione dei ricordi non decisa dal solo remote Git |
| Stesso progetto, copie o branch diversi | Conoscenza di progetto condivisibile; stato operativo, percorsi ed esiti ancorati anche al workspace/turno |
| Conversazione personale | `project_id` e workspace assenti; ID di sessione comunque nuovo, senza riuso implicito di `principale` |

Non usare come identità l'hash del percorso (cambia con uno spostamento),
il nome della cartella (non univoco) o il remote Git (fork, URL diversi,
copie di lavoro e progetti senza Git). Un file versionato nel repository
può essere valutato in seguito, ma non è necessario per il primo registro.

Il luogo fisico e lo schema del registro vanno definiti nel punto 1 della
memoria. Deve rientrare in backup e restore. Non è prevista una migrazione
dei dati di prova, né una loro cancellazione implicita. Il `project_id` nasce
con una regola canonica fin dall'inizio: è una chiave nuova, e non deve
ereditare il debito di migrazione descritto sopra quando l'archivio sarà
pieno.

## 4. Configurazione e proprietà delle responsabilità

Proponiamo tre gruppi di dati, risolti prima dell'esecuzione:

- **Configurazione applicativa:** percorsi degli archivi e backup,
  connessione Ollama, impostazioni predefinite, configurazione dell'indice.
- **Contesto della conversazione:** identità, workspace opzionale, scelta
  dei modelli, politica operativa e politica di apprendimento.
- **Contesto del turno:** fotografia immutabile delle impostazioni
  effettive, ID e ambiti di lettura/scrittura, revisione della configurazione.

L'ambiente e il `.env` sono sorgenti per costruire questi dati, non oggetti
globali da modificare quando cambia una scheda. Collezioni di opzioni
devono essere copiate o rese immutabili: una dataclass congelata da sola
non impedisce la mutazione di un dizionario interno.

Due pezzi di questa separazione sono fatti. Il primo riguarda i percorsi.
`ares/config.py` non tiene più un `PERCORSI` corrente né le viste che lo
nascondevano (`TMP_DIR`, `DB_FILE`, `BACKUP_DIR`, `WORKSPACE_DIR`...), e
`imposta_percorsi` non esiste: chi legge lo stato riceve un `Percorsi` come
primo parametro, e l'unico punto in cui se ne costruisce uno è il confine del
processo — i comandi della CLI nel proprio corpo, le prove all'import, dopo
`prepara_ambiente`. L'identità è l'altro asse, e viaggia accanto: `Utente` non
è un campo di `Percorsi`.

Il secondo riguarda i modelli. I nomi del tuning — `MAIN_MODEL`,
`LEARNING_MODEL`, `EMBEDDER_MODEL`, `OLLAMA_HOST`, `NUM_CTX`, `KEEP_ALIVE`,
le temperature e i due `think` — restano in `config.py` come sorgente, dove
`.env`, ambiente e predefiniti si incontrano una volta sola all'import, ed è
`leggi_impostazioni()` a fotografarli in un `Impostazioni` congelato alla
porta del processo. Da lì in poi `build_chat_model`, `build_learning_model`,
`build_knowledge`, `build_learning_machine`, `build_assistant`,
`istruzioni_sull_ambiente`, `descrizione`, `esamina` e la riga delle metriche
ricevono l'oggetto: due conversazioni con modelli diversi sono due oggetti, non
due mutazioni a distanza. Le due `options` di Ollama sono proprietà derivate
perché non sono indipendenti dalla coppia di modelli — il contesto
dell'estrazione si stringe solo quando i due modelli sono diversi, altrimenti
Ollama riavvierebbe il runner a ogni passaggio perdendo la cache del prompt.
L'avviso sul cloud è un metodo del tipo, così preflight e banner non possono
leggere una configurazione diversa da quella che stanno per avviare.

La modalità non entra in `Impostazioni`: è già un parametro a ogni confine, e
l'unico difetto reale era il default che fotografava `config.MODO_PREDEFINITO`
all'import. `build_assistant` e le tre funzioni dei prompt lo risolvono adesso
al momento della chiamata, come faceva già `build_workspace`; il comando
`ares` tiene il proprio default dichiarato in `--help`.

Un'eccezione è deliberata e resta: `ares/backup/snapshots.py` legge ancora i
nomi di modulo per scrivere il manifesto dello snapshot e per il controllo di
compatibilità con l'embedder. Quel manifesto registra com'era configurato
*questo processo*, e la verifica dell'embedder è una garanzia esistente: non è
una lettura di comodo da sostituire, ed è l'unico punto rimasto.

Resta fuori la politica operativa e di apprendimento — i flag `LEARN_*`,
`MOSTRA_*`, `CONFERMA_APPRENDIMENTI`, le variabili del workspace e della
cronologia — che è il gruppo successivo.

Il cambio modello o modalità si applica ai turni successivi; durante un
turno attivo restituisce un conflitto, salvo futura operazione dedicata.
Una sessione non viene riassegnata a un altro progetto perché è aperta da
un'altra cartella. Un trasferimento di ambito è un'operazione distinta da
studiare; nel primo contratto si apre una sessione appropriata.

| Rimane nel client | Appartiene ai servizi Ares | Rimane nell'adattatore/infrastruttura |
| --- | --- | --- |
| Argomenti CLI, slash command, scorciatoie | Creazione/apertura sessioni e risoluzione progetto | Costruzione e uso di Agent e LearningMachine |
| Input, Markdown, pannelli, notifiche | Politiche operative e di apprendimento | Traduzione di eventi e requirement Agno |
| Selezione visuale di una cartella | Validazione del workspace e decisioni richieste | SQLite, LanceDB, FileSystem e lock di piattaforma |
| Raccolta di una risposta | Verifica e applicazione dell'autorizzazione | Esecuzione e ripresa effettiva dei tool |
| Formattazione di errori e metriche | Esiti, variazioni memoria e manutenzione coordinata | Misure e dati del provider |

L'esistenza di un terminale non equivale alla possibilità di rispondere.
Distinguere politica operativa, apprendimento e capacità del chiamante di
gestire richieste. I default di `ares -p` restano esplicitamente configurati
dall'adattatore e validati dal servizio; aggiungere una UI non deve rendere
più permissivo un avvio non presidiato.

## 5. Superficie pubblica minima proposta

Nomi indicativi, da tradurre in firme Python prima dell'implementazione.
I risultati sono dati Ares; gli oggetti Agno restano interni.

| Operazione | Input essenziale | Risultato |
| --- | --- | --- |
| Risolvi workspace | Utente, percorso esplicito, eventuale progetto | Contesto risolto o richiesta/errore strutturato |
| Crea sessione | Utente, progetto/workspace opzionali, titolo, impostazioni | Sessione identificata e persistita anche prima del primo messaggio |
| Apri sessione | Utente e ID | Sessione verificata e contesto salvato |
| Elenca/leggi sessioni | Utente, ambito, paginazione | Dati e cursore, senza righe già formattate |
| Avvia turno | Sessione, messaggio, ID richiesta del chiamante | Handle del turno e accesso a eventi/stato |
| Rispondi a richiesta | Turno, richiesta, decisione, eventuale motivo | Accettazione o conflitto; ripresa gestita dal nucleo |
| Interrompi turno | Sessione e turno | Richiesta di arresto; esito terminale successivo |
| Leggi stato del turno | Sessione e turno | Stato corrente, richieste pendenti, esiti disponibili |
| Cambia impostazioni | Sessione, revisione attesa, variazioni | Nuova revisione o conflitto |
| Ispeziona memoria/prompt | Utente, sessione/ambito esplicito | Dati consultabili, non stdout |

Modifica delle memorie, versioni e manutenzione completa saranno precisate
nei punti successivi. Non anticipare un'API CRUD generica per tutti gli
store: oggi hanno capacità e granularità differenti.

L'ID della richiesta del chiamante impedisce che un doppio invio accidentale
avvii due turni. La stessa chiave con un contenuto diverso è un conflitto.
La prima implementazione deve dichiarare la durata della deduplicazione;
non promettiamo esecuzione esattamente una volta dopo un crash o verso
comandi esterni. Persistenza e riconciliazione sono verifiche distinte.

### Eventi

Ogni evento pubblico porta versione del contratto, ID turno/sessione,
numero di sequenza e payload tipizzato. Tipi iniziali: testo, strumento
avviato/concluso/fallito, richiesta di autorizzazione, apprendimento,
variazioni della memoria ed esito terminale.

Gli aggiornamenti del testo possono essere transitori; stato finale,
richieste pendenti ed esiti devono essere consultabili senza rileggere la
stampa della CLI. Sequenze ordinate non implicano un registro durevole di
ogni token: replay dopo riavvio resta fuori dalla prima implementazione.

Non emettere `memory_saved` solo perché è finito il post-hook: oggi Agno può
registrare un warning e proseguire dopo un errore di store. Distinguere
esito verificato, nessuna variazione ed esito non verificato/errore.

### Autorizzazioni

Una richiesta identifica turno, strumento, argomenti e workspace effettivo.
Il nucleo conserva il legame con il requirement Agno. Il client invia solo
la decisione: non costruisce o modifica il comando da autorizzare.

Una seconda risposta uguale non riesegue l'azione; una risposta opposta o
successiva all'annullamento è un conflitto. Se ciò che deve essere
autorizzato cambia, la vecchia autorizzazione non vale per la nuova azione.
Per le modifiche ai file studiare anche la validità dell'anteprima se il
file cambia durante l'attesa. Rifiuto di un tool e arresto del turno sono
operazioni diverse: dopo un rifiuto il modello può ancora rispondere.

## 6. Esecuzione e risorse: minimo sostenibile

Stati applicativi proposti:

`in esecuzione → in attesa di autorizzazione → in esecuzione → finalizzazione → concluso`

Errore e interruzione possono partire dai vari stati; la finalizzazione
chiude risorse e rende gli esiti disponibili. Fine del testo non equivale
a fine del turno: l'apprendimento può essere ancora in corso.

- Tenere inizialmente la serializzazione per utente. Più conversazioni
  aperte non richiedono subito inferenze concorrenti. Un secondo avvio
  restituisce `occupato`; una coda automatica è un lavoro separato.
- Conservare nel servizio la prenotazione del turno anche durante la
  pausa, evitando che un'altra interfaccia aggiri il lock. Il renderer non
  deve determinarne la durata né la riuscita.
- Eseguire il lavoro in un contesto controllato dal runtime. Per il primo
  client CLI può restare sincrono; per un worker la richiesta di stop deve
  raggiungere il processo che esegue Agno. Non serve duplicare subito
  orchestrazioni sync e async o scegliere adesso thread contro processi.
- Chiedere l'arresto non significa che una shell o una scrittura siano già
  state interrotte o annullate. Pubblicare l'esito soltanto quando noto,
  segnalando gli effetti già avvenuti. Verificare separatamente arresto
  durante inferenza, tool, pausa ed estrazione della memoria.
- Il runtime possiede connessioni e handle; un contesto applicativo di
  chiusura li rilascia. La manutenzione entra in una fase esclusiva:
  nessun nuovo turno, attività pendenti risolte o operazione rifiutata,
  risorse interessate chiuse e riaperte dopo il restore.
- Non basta accorciare il lock attuale: per sostituire archivi devono
  essere chiuse anche connessioni SQLite e handle LanceDB. È un requisito
  da provare, incluso Windows, prima di consentire restore a client aperto.

Il comportamento alla disconnessione del client e la ripresa dopo crash
richiedono una decisione specifica. Il primo contratto non considera una
disconnessione come approvazione e non promette continuazione durevole.

## 7. Agno: capacità verificate e limiti

Il riferimento implementativo è la versione 3.0.9 del lock e del sorgente
installato. La documentazione online può includere capacità successive:
non si assume che ogni opzione documentata sia già adottabile.

- Riutilizzare `Agent.run`, `continue_run`, sessioni e requirement per il
  motore. Ares deve aggiungere il contratto di prodotto, non riscrivere
  l'esecuzione. [Running Agents](https://docs.agno.com/agents/running-agents)
- Agno espone `cancel_run/acancel_run`. Nel codice installato il manager
  predefinito è in memoria al processo: un futuro trasporto deve recapitare
  lo stop al runtime corretto. La disponibilità del metodo non prova
  l'arresto immediato di ogni tool o del post-hook.
- Sessioni e metadati possono rappresentare i riferimenti Ares, ma i
  metadati non impongono da soli un filtro di progetto. Lo schema SQL
  distingue sessioni e run. [Session Storage](https://docs.agno.com/database/session-storage)
- Profilo e User Memory sono per utente; entità e intuizioni hanno ambiti
  configurabili. Il modello di identità deve rispettare questa differenza.
  [Learning Stores](https://docs.agno.com/learning/stores/intro)

File Agno esaminati sotto `site-packages/agno`: `agent/agent.py`,
`db/sqlite/schemas.py`, `db/sqlite/sqlite.py`, `run/cancel.py`,
`learn/stores/user_profile.py`, `learn/stores/user_memory.py` e
`learn/machine.py`. Non sono necessarie patch nel framework per definire il
contratto; gli agganci effettivi andranno coperti dalle prove di integrazione.

## 8. Piano di attuazione e prove di accettazione

### Primo incremento proposto

Definire tipi di configurazione e identità, passarli esplicitamente ai
costruttori e rendere creazione/apertura delle sessioni indipendenti dalla
CLI. Introdurre un'operazione applicativa di turno riutilizzando
`turn_core` ed `echo`. Farla usare alla CLI e a un client di test.

In questo incremento conservare il comportamento di apprendimento attuale
come politica esplicita: spostarlo non equivale ad approvare una modifica
silenziosa dei default. L'apprendimento automatico senza domanda resta la
direzione concordata, da realizzare con il punto 3 e con controllo degli
esiti e correzioni appropriate.

L'identità di progetto va definita insieme al punto 1 della memoria.
Introdurre un campo `project_id` non permette di dichiarare già risolto
l'isolamento: il punto 2 deve applicarlo a ogni lettura e scrittura.

### Sequenza successiva

1. Completare eventi e autorizzazioni senza oggetti Agno nel contratto.
2. Integrare politiche e servizi della memoria secondo i cinque punti.
3. Completare arresto, gestione delle risorse e manutenzione condivisa.
4. Verificare la CLI sul nucleo completo; solo dopo affrontare la desktop.

| Prova da implementare | Risultato richiesto |
| --- | --- |
| Due workspace nello stesso processo, chiamate alternate | Prompt, strumenti, conferme e archivi usano il contesto corretto |
| Cartelle omonime, creazione nello stesso istante | Sessioni con ID distinti e titoli leggibili |
| Varianti equivalenti dell'utente | Stessa identità in namespace, lock e sessioni, oppure rifiuto esplicito all'ingresso |
| Apertura di sessione con proprietario o progetto errato | Errore prima di costruire un agente operativo |
| Sessione senza repository | Creazione e ripresa autonome, senza strumenti workspace |
| CLI e client senza terminale | Stesse politiche e scritture, nessuna dipendenza da `isatty()` nel servizio |
| Doppio invio e doppia approvazione | Nessun secondo turno/tool accidentale entro il perimetro dichiarato |
| Rifiuto o risposta tardiva | Nessuna azione autorizzata da una richiesta ormai invalida |
| Arresto nelle quattro fasi | Stato coerente, lock rilasciato quando sicuro, effetti parziali dichiarati |
| Errore di apprendimento dopo risposta riuscita | Esito della risposta distinto da quello delle memorie |
| Manutenzione con client aperto | Coordinamento esplicito, nessuna sostituzione con handle ancora attivi |
| Import del nucleo | Nessun import di `ares.cli` o dipendenza diretta dal rendering/input del terminale nei servizi; eventuali log del framework gestiti nell'adattatore |

Le prove deterministiche usano archivi temporanei e un adattatore simulato;
le prove Agno verificano poi il collegamento reale. Le prove con modello
misurano la qualità semantica separatamente. Lo studio attuale comprende
lettura dei sorgenti e le due verifiche mirate sopra, non un'esecuzione della
suite né una dimostrazione end-to-end di questa architettura proposta.

## 9. Decisioni da chiudere prima del primo codice

| Decisione | Proposta iniziale | Alternativa o limite |
| --- | --- | --- |
| Confine pubblico | Servizi Python e dati Ares serializzabili | HTTP/AgentOS restano valutabili dopo; non necessari ora |
| Identità sessione | ID opaco e titolo/alias separato | I comandi con nomi espliciti richiedono una risoluzione compatibile e non ambigua |
| Identità utente | **Chiusa il 2026-09-21:** normalizzazione unica, alfabeto regola 1 e validazione all'ingresso; il debito di migrazione delle chiavi derivate è dichiarato al §3 | Il nome visualizzato resta separato dall'ID |
| Identità progetto | Registro locale e collegamento esplicito fra copie | Schema persistente da progettare nel punto 1, con copertura backup |
| Concorrenza iniziale | Un turno per utente, errore occupato | Parallelismo e code richiedono ulteriori garanzie sugli store condivisi |
| Evoluzione del refactoring | Incrementi piccoli con CLI funzionante | Nessuna riscrittura complessiva né UI come banco di prova del nucleo |

Queste decisioni rendono concreto il primo intervento senza anticipare
l'intera implementazione della memoria o la tecnologia dell'interfaccia.
