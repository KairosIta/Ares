# Roadmap

Aggiornata il 13 settembre 2026 dopo la prima analisi della memoria e del
prompt di Ares.

Ares deve poter lavorare in più cartelle e repository mantenendo
comprensibili le proprie azioni e controllabili gli apprendimenti.
Le priorità sono isolamento del contesto, qualità della memoria e controllo
dell'utente su ciò che cambia nel tempo.

## Premesse e metodo

- L'uso finora è stato sperimentale: non ci sono dati personali da salvare
  o migrare. Migrare gli archivi di prova non è un requisito di questo
  lavoro; eventuali cambi incompatibili andranno comunque dichiarati.
- La configurazione scelta attualmente usa modelli cloud per l'affidabilità
  ottenibile con l'hardware disponibile. Ares deve continuare a supportare
  un uso interamente locale con modelli e hardware adeguati, senza rendere
  obbligatorio il cloud. L'embedding resta locale.
- I cinque punti sotto sono direzioni di lavoro concordate, non soluzioni
  tecniche già validate. Prima di scrivere codice per ciascun punto,
  approfondire comportamento attuale, API e limiti della versione Agno
  adottata, alternative, impatto sulle interfacce e criteri di verifica.
- Ogni approfondimento deve produrre una decisione esplicita sul perimetro
  e sui risultati attesi. Riutilizzare le primitive Agno dove adatte;
  implementare in Ares le politiche che il framework non offre.
- La UI sarà l'interfaccia principale per conversare e lavorare ogni giorno
  con Ares, in forma di applicazione desktop. Il riferimento d'uso è
  l'esperienza dell'app Codex su Linux; tecnologia e architettura restano
  da approfondire. Il lavoro sulla UI viene dopo l'indipendenza del nucleo
  dall'interfaccia e i cinque approfondimenti sulla memoria.

## Base disponibile

Ares usa Agno 3.0.9, SQLite e LanceDB, un quaderno persistente,
apprendimento sul turno completato anche dopo `continue_run`, backup e
manutenzione di entità e sessioni. La CLI comprende REPL Rich/Prompt
Toolkit, ripresa delle conversazioni, esportazione Markdown, uso in pipe e
quattro modalità: `manuale`, `modifiche`, `piano`, `auto`. I risultati degli
strumenti troppo grandi vengono conservati fuori dal prompt e riletti a
pagine; la loro retention segue le conversazioni. Installazione
riproducibile e CI su Ubuntu e Windows completano la base esistente.

Le sessioni registrano la cartella, ma profilo, memorie, entità, intuizioni
e quaderno sono condivisi fra i progetti dello stesso utente. La conferma
attuale ripristina profilo e memorie dopo la scrittura, per l'intero turno;
non copre gli altri archivi, e fra la scrittura e la risposta resta una
finestra in cui un processo che muore lascia la scrittura dov'è. I lock
coordinano i turni dello stesso utente e le operazioni di manutenzione. Dal
21 settembre 2026 l'identità dell'utente ha una sola forma canonica —
namespace, profilo/memorie e lock concordano — e l'ID di sessione non
collide fra cartelle omonime o avvii nello stesso secondo.

Il prompt effettivo combina istruzioni nel codice, `ARES.md`, dati appresi
e contesto di esecuzione. È ispezionabile con `ares inspect --prompt`, ma
non esiste un ciclo di revisione e adozione di nuove procedure apprese.
L'apprendimento riguarda dati e contesto, non i pesi del modello.

Riferimenti: [architettura](docs/architecture.md), [integrazione Agno](docs/agno.md),
[prompt](docs/prompt.md), [qualità della memoria](docs/memory-quality.md).

## Ordine di lavoro: nucleo condiviso, memoria, infine UI

Primo studio disponibile: [identità, responsabilità e contratto del nucleo](docs/core-contract.md).
Contiene evidenze sul codice, proposte e prove di accettazione; non è
un'implementazione né una decisione definitiva su tutte le alternative.

La priorità è rendere Ares indipendente dall'interfaccia. La CLI sarà il
primo client dei servizi condivisi e continuerà a permettere di usare e
verificare Ares durante il lavoro. La progettazione e l'implementazione
della desktop vengono alla fine.

Prima di implementare, definire i confini e i contratti del nucleo:

- Configurazione dell'applicazione separata dal contesto di ogni
  conversazione, senza cambiare workspace e politiche tramite globali
  condivise fra sessioni.
- Servizi per apertura e gestione di progetti e sessioni, esecuzione dei
  turni, apprendimento e manutenzione. La CLI raccoglie input e presenta
  risultati; le regole di comportamento appartengono ai servizi.
- Eventi, risultati, errori e richieste di autorizzazione strutturati:
  l'adattatore Agno gestisce gli oggetti del framework senza imporli ai client.
- Ciclo di vita del turno: avvio, pausa, ripresa, interruzione e conclusione;
  coordinamento dei lock e della manutenzione anche con un client aperto.
- Politica di apprendimento distinta dalle autorizzazioni su file e comandi
  e dalla presenza di un terminale. Direzione da approfondire: apprendimento
  automatico con registro e correzioni puntuali, senza conferma obbligatoria
  a ogni variazione; revisione preventiva come opzione esplicita.

Questa separazione si collega ai cinque punti sotto: identità e contesti
ai punti 1–2; servizi di memoria e relative politiche al punto 3; eventi e
storia delle modifiche al punto 4; selezione del contesto e procedure
apprese al punto 5. Definire prima i contratti, poi implementare per passi
concreti insieme ai rispettivi approfondimenti, evitando di costruire
un'infrastruttura generica prima di conoscerne le necessità.

**Verifica attesa:** la CLI e un client di test senza terminale usano gli
stessi servizi e ottengono gli stessi effetti a parità di richieste e
politiche. Provare due workspace nello stesso processo, approvazioni
duplicate o tardive, interruzioni, apprendimento e manutenzione. Preservare
le garanzie esistenti; dichiarare e verificare ogni cambiamento di politica.

## Memoria: cinque approfondimenti prima dell'implementazione

### 1. Identità stabile e memoria per progetto

**Obiettivo:** distinguere ciò che riguarda la persona da ciò che vale solo
per un progetto o una sessione.

Da approfondire:

- Identità di progetto distinta dal percorso, considerando sottocartelle,
  spostamenti, cloni, worktree e cartelle senza Git.
- Ripartizione fra profilo personale, memoria del progetto e contesto della
  sessione, includendo entità, intuizioni, quaderno e cronologia.
- Condivisione delle conoscenze riutilizzabili e consultazione esplicita di
  altri progetti, conservando le preferenze personali trasversali.

**Verifica attesa:** due repository con convenzioni opposte mantengono i
propri fatti; una preferenza personale pertinente resta disponibile in
entrambi. Spostamenti e worktree hanno un comportamento definito e provato.

Approfondimento disponibile: [identità di progetto e ambiti applicati dal
codice](docs/project-scopes.md), con le evidenze nel codice e in Agno, le
proposte e le prove di accettazione. Non è un'implementazione.

### 2. Ambiti applicati dal codice in scrittura e recupero

**Obiettivo:** rendere l'isolamento una proprietà verificabile del sistema.

Da approfondire:

- Trasporto e validazione dell'identità utente, progetto e sessione in
  estrazione, salvataggio, ricerca e composizione del prompt.
- Namespace e filtri supportati da ogni store Agno: cambiare il namespace
  della Learning Machine non rende automaticamente profilo e User Memory
  specifici del progetto.
- Ricerca delle sessioni, accessi al quaderno e percorsi alternativi che
  potrebbero recuperare dati fuori dall'ambito corrente.
- Comportamento con ambito mancante o ambiguo: il modello può proporre una
  classificazione, ma il codice deve validarla.

**Verifica attesa:** prove di scrittura e lettura fra ambiti diversi,
incluse richieste ambigue e strumenti, dimostrano che nessun percorso
automatico allarga implicitamente il contesto consentito.

Approfondimento disponibile: [identità di progetto e ambiti applicati dal
codice](docs/project-scopes.md), §3 e §6 sul trasporto dell'ambito, i limiti
di ogni store Agno e i percorsi che allargano il contesto.

### 3. Gestione unificata e revisione degli apprendimenti

**Obiettivo:** gestire tutti gli archivi con operazioni coerenti,
utilizzabili dalla CLI e dalla futura UI.

Da approfondire:

- Inventario completo, filtri per ambito e tipo, modifica e cancellazione
  per identificativo. Una ricerca semantica non equivale a un inventario.
- Revisione per singola voce, comprese modifiche e rimozioni, e gestione
  delle proposte in attesa senza usarle come ricordi confermati, quando è
  scelta la revisione preventiva. Il controllo puntuale deve essere
  disponibile anche dopo il salvataggio automatico, senza obbligare
  l'utente ad approvare ogni ricordo.
- Superamento dei limiti della conferma attuale: scrittura anticipata,
  approvazione dell'intero turno e copertura limitata a profilo e memorie.
- Modalità senza apprendimento distinta da una sessione effimera:
  chiarire la persistenza di cronologia, quaderno e risultati.
- Semantica di «dimentica»: non usare più un dato, rimuoverlo dalla memoria
  attiva o cancellarlo anche dalle altre copie, con una politica per i backup.
- Concorrenza, interruzioni, errori e ripristino verificabile.

**Verifica attesa:** accettare una voce e rifiutarne un'altra produce lo
stato previsto; una proposta rifiutata o pendente non riappare come fatto
confermato nelle sessioni successive.

### 4. Provenienza, correzioni e storia

**Obiettivo:** spiegare da dove viene un ricordo e come è cambiato.

Da approfondire:

- Riferimenti a progetto, sessione e turno sorgente; distinzione fra parole
  dell'utente, proposte dell'assistente e informazioni da fonti esterne.
- Data dell'evento distinta da acquisizione e aggiornamento del ricordo.
- Stato attuale, superato o da verificare, collegamento fra correzione e
  voce precedente e storia delle modifiche.
- Provenienza consultabile senza reinserire tutta la cronologia nel prompt;
  comportamento dei riferimenti quando la fonte viene eliminata.

**Verifica attesa:** «perché pensi questa cosa?» conduce a una fonte
consultabile o ne dichiara l'assenza; una correzione aggiorna il dato attivo
senza presentare il precedente come ancora valido.

### 5. Crescita della memoria ed evoluzione controllata del prompt

**Obiettivo:** migliorare con l'esperienza mantenendo memoria pertinente e
istruzioni comprensibili, misurabili e reversibili.

Da approfondire:

- Budget del contesto e selezione dei ricordi per pertinenza, ambito e
  validità; revisione di duplicati e informazioni obsolete senza eliminare
  fatti ancora utili solo perché vecchi.
- Limiti del Curator Agno: nella versione esaminata opera sul campo
  `memories` di un profilo personalizzato, non sullo User Memory Store
  usato da Ares. Non è una pulizia pronta da abilitare.
- Separazione fra regole fondamentali versionate, preferenze personali,
  convenzioni di progetto e procedure apprese da esiti verificati.
- Ciclo proposta, evidenze, revisione, valutazione, adozione e annullamento
  delle nuove istruzioni, evitando riscritture autonome indiscriminate del
  prompt e generalizzazioni da una singola eccezione.
- Benchmark con più progetti, nomi uguali in contesti diversi, cambi di
  cartella, correzioni, rifiuti e accumulo nel tempo. Misurare separatamente
  qualità, costo, latenza e occupazione del contesto.

**Verifica attesa:** confronti prima/dopo sullo stesso protocollo mostrano
il beneficio delle modifiche senza perdere isolamento e controllo. I
risultati valgono per i modelli e le configurazioni effettivamente provati,
sia cloud sia locali.

## Fase finale: UI desktop

L'interfaccia principale sarà un'app desktop per conversare e lavorare
quotidianamente con Ares. Il riferimento condiviso è l'esperienza dell'app
Codex su Linux, con conversazione centrale e accesso a progetti e attività.
Questo riferimento non implica replicarne tutte le funzionalità.

La UI deve permettere di vedere e gestire attività, conversazioni,
strumenti, conferme e apprendimenti, rendendo visibili progetto corrente,
ambito dei ricordi e cambiamenti nel tempo. Linux è l'ambiente d'uso
attuale; il perimetro multipiattaforma della UI va ancora definito.

Dopo il lavoro sul nucleo condiviso e sulla memoria, discutere flussi
prioritari, struttura dell'interfaccia,
rapporto con la CLI, rappresentazione degli eventi e delle approvazioni,
ispezione di memoria e prompt e configurazione dei modelli. Definire un
primo perimetro concreto basato sui servizi condivisi già verificati.
Mantenere il funzionamento interamente locale come possibilità. Tecnologia,
architettura e modalità di distribuzione non sono ancora state scelte.

## Altri miglioramenti da rivalutare

Restano dalla roadmap precedente, subordinati alle priorità sopra:

- Sonda del benchmark robusta alla prosa attorno al JSON, senza classificare
  come successo una risposta che contraddice il JSON nel testo circostante.
- Profili di configurazione per modelli, hardware e finestre di contesto.
- Benchmark ripetibili di latenza, VRAM e affidabilità degli store.
- Copertura automatica più ampia del percorso asincrono.
- Valutazione delle prove in-process preservando l'isolamento dagli archivi
  reali garantito oggi dai processi separati.
- Documentazione inglese completa.
- Valutazione esplicita di macOS e distribuzioni Linux fuori dalla matrice CI.

## Non obiettivi attuali

- Dipendenza obbligatoria da servizi cloud.
- Sincronizzazione remota automatica delle memorie.
- Esecuzione shell presentata come sandbox sicura.
- Supporto garantito per qualunque modello o configurazione hardware.
- Addestramento automatico dei pesi del modello o riscrittura incontrollata
  delle regole fondamentali di Ares.
