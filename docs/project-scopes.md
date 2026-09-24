# Identità di progetto e ambiti applicati dal codice

Data: 22 settembre 2026.

**Stato: proposta di progettazione, non implementata.** Questo documento
approfondisce i primi due punti della [roadmap](../ROADMAP.md) — *Identità
stabile e memoria per progetto* e *Ambiti applicati dal codice in scrittura e
recupero* — e ne prepara l'implementazione. Distingue i fatti verificati nel
codice e nel framework dalle scelte proposte: finché una voce non è marcata
come verificata, è una proposta. Non introduce un identificativo di progetto,
non cambia le chiavi degli archivi, non migra dati esistenti. Le prove di
accettazione sono descritte al §8 e non implementate.

**Aggiornamento del 22 settembre 2026.** Le due verifiche preliminari chieste
dal §8 sono state fatte e sono misurate al §3.4: una provenienza di progetto
nelle memorie si scrive con le API pubbliche, sopravvive a una riscrittura e
va riallineata quando il turno tocca la voce; il filtro per namespace delle
intuizioni è applicato dopo il limite, e con due ambiti può restituire zero
intuizioni del progetto. Le due decisioni che dipendevano dalle verifiche —
la forma della provenienza e la strategia di ricerca — sono chiuse al §9. Il
resto del documento resta una proposta non implementata.

## 1. Risultato atteso e perimetro

Ares sa **chi** sta parlando (l'utente) e **quando** (la sessione), ma non
**dove** nel senso che conta: la cartella di lavoro entra nei metadati della
sessione e nel filtro dell'elenco, non negli archivi che il modello legge e
scrive. Oggi profilo, memorie, entità, intuizioni e quaderno sono di
*qualcuno*, non di *qualcosa*: due repository aperti dalla stessa persona
condividono tutto ciò che Ares ha imparato, tranne l'etichetta con cui le
conversazioni si elencano.

Il risultato atteso è che l'appartenenza di un ricordo a un progetto diventi
una proprietà verificabile del sistema, non una convenzione del prompt:

- **la persona** conserva ciò che vale ovunque: lingua, fuso orario, stile di
  comunicazione, strumenti, preferenze;
- **il progetto** conserva ciò che vale lì: convenzioni, vincoli, entità
  ricorrenti, decisioni, procedure, note del quaderno;
- **la sessione** conserva ciò che vale in quella conversazione: obiettivo,
  piano, avanzamento, cronologia;
- **la condivisione** è esplicita: una conoscenza riutilizzabile si può
  consultare da un altro progetto solo con un percorso dichiarato, non come
  effetto collaterale di una ricerca.

Fuori perimetro, in questa fase: la sincronizzazione fra installazioni, la
condivisione fra utenti, l'interfaccia desktop, la ricerca semantica
sull'intero archivio e la revisione degli apprendimenti (punto 3 della
roadmap). Il prerequisito è il contratto del nucleo
([core-contract.md](core-contract.md)): l'ambito è una proprietà dei servizi
condivisi, non della CLI.

## 2. Evidenze nel codice attuale

### 2.1 L'ambito di ogni archivio

Verificato il 22 settembre 2026. La colonna *ambito* è ciò che il codice
applica davvero, non ciò che il nome suggerisce.

| Archivio | Ambito oggi | Da dove viene | Riferimenti |
| --- | --- | --- | --- |
| Profilo utente (`kairos.db`) | utente, in ogni cartella e sessione | `user_id=utente.id` sull'Agent | [assistant.py](../ares/agent/assistant.py), [learning.py](../ares/agent/learning.py) |
| User Memory (`kairos.db`) | utente | `user_id=utente.id` | [learning.py](../ares/agent/learning.py) |
| Contesto di sessione (`kairos.db`) | sessione; la chiave non porta l'utente | `session_id` della conversazione | [learning.py](../ares/agent/learning.py) |
| Entità (`kairos.db`) | utente, sotto `user/<id>/personale` | `namespace_entita(utente)` | [learning.py](../ares/agent/learning.py), [stores.py](../ares/state/stores.py) |
| Intuizioni (`lancedb/learned_knowledge`) | utente, sotto `user/<id>` | `namespace_utente(utente)` | [learning.py](../ares/agent/learning.py), [runtime.py](../ares/agent/runtime.py) |
| Quaderno e file dell'agente (`filesystem.db`) | utente: non per cartella, non per sessione | `namespace=namespace_utente(utente)` | [archivi.py](../ares/state/archivi.py), [prompts.py](../ares/agent/prompts.py) |
| Payload di offload (`filesystem.db`) | utente in scrittura, utente più sessione in lettura | namespace del quaderno e `run_context` | [archivi.py](../ares/state/archivi.py) |
| Sessioni e cronologia chat (`kairos.db`) | utente, con la cartella come etichetta nei metadati | `user_id`; `metadata={CHIAVE_CARTELLA: str(spazio.root)}` | [assistant.py](../ares/agent/assistant.py), [stores.py](../ares/state/stores.py) |
| File del progetto (workspace) | cartella: directory corrente o `--workspace` | `percorsi.lavoro` | [config.py](../ares/config.py), [runtime.py](../ares/agent/runtime.py) |
| Istruzioni `ARES.md` | cartella | `radice_lavoro` più nome configurato | [prompts.py](../ares/agent/prompts.py) |
| Cronologia del REPL | installazione: né utente né cartella | `percorsi.cronologia_file` | [config.py](../ares/config.py), [chat.py](../ares/cli/chat.py) |
| Lock dello stato / del turno | installazione / utente | `percorsi.lock_file`; impronta di `utente.id` | [lock.py](../ares/state/lock.py) |
| Backup e ripristino | installazione: tutti gli utenti, tutte le cartelle | `percorsi.stato` intero | [snapshots.py](../ares/backup/snapshots.py), [restore.py](../ares/backup/restore.py) |

Tre letture di questa tabella:

1. **La cartella è un'etichetta, non un'identità.** Entra in un solo posto dei
   dati — `metadata[CHIAVE_CARTELLA]` — e in un solo filtro: `leggi_sessioni`
   con `cartella=` e `sessioni_della_cartella`, entrambi confronti di stringhe
   sul percorso risolto. Nessuno store di apprendimento la riceve.
2. **Solo due archivi hanno un asse di namespace**: entità e intuizioni. Gli
   altri sono per utente o per sessione e non offrono un posto dove mettere il
   progetto senza cambiare le chiavi.
3. **L'installazione è l'unità di backup**, quindi un registro dei progetti
   che vivesse fuori dagli archivi non verrebbe salvato con essi. Se il
   registro è memoria, deve stare in `percorsi.stato`.

### 2.2 Dove l'ambito si allarga già oggi

Nessuno di questi percorsi è un difetto in sé: sono scelte dichiarate, e
alcune hanno un commento che le spiega. Sono però i punti in cui una richiesta
del modello o dell'utente può raggiungere dati di un altro progetto, e vanno
decisi uno per uno quando l'ambito diventa parte del contratto (§6.3).

| Percorso | Filtro reale | Riferimenti |
| --- | --- | --- |
| `search_past_sessions` (strumento Agno) | `user_id`, meno la sessione corrente: **nessuna cartella** | `agno/agent/_default_tools.py:490-503` |
| `read_past_session` (strumento Agno) | `session_id` più `user_id`: nessuna verifica della cartella di nascita | `agno/agent/_default_tools.py:587-591` |
| `leggi_sessioni(cartella=None)` | solo `user_id`; è il percorso di `/sessioni tutte` e di `ares inspect` senza `--session` | [stores.py](../ares/state/stores.py), [commands.py](../ares/cli/commands.py), [inspect_learning.py](../ares/ops/inspect_learning.py) |
| Quaderno | solo il namespace dell'utente: `read_file`, `list_files`, `search_content` leggono note scritte altrove | [archivi.py](../ares/state/archivi.py), [prompts.py](../ares/agent/prompts.py) |
| `ares inspect --file` | solo il namespace dell'utente | [inspect_learning.py](../ares/ops/inspect_learning.py) |
| Entità e intuizioni | namespace dell'utente, uguale in ogni cartella | [stores.py](../ares/state/stores.py) |
| Profilo e User Memory | solo `user_id`: iniettati in ogni cartella | `agno/learn/stores/user_profile.py`, `agno/learn/stores/user_memory.py` |
| `ares entities audit` e `merge` | namespace dell'utente: operano su tutte le entità dell'utente | [maintenance.py](../ares/entities/maintenance.py), [audit.py](../ares/entities/audit.py) |
| `ares sessions status`, `prune`, `delete` | solo `user_id`: tutte le cartelle | [retention.py](../ares/sessions/retention.py) |

Il testo del prompt dichiara il primo limite: `search_past_sessions` "elenca le
sessioni passate dell'utente, entro il tetto configurato, senza sapere dove
sono nate" ([prompts.py](../ares/agent/prompts.py)). Ares compensa con l'elenco delle
conversazioni nate in questa cartella, ma lo strumento resta user-wide: la
compensazione è un'istruzione, non un filtro.

### 2.3 Riproduzioni offline

Tre misure fatte sui pacchetti installati (Agno 3.0.9, Python 3.12), senza
modelli né rete, su un archivio temporaneo. Servono a separare ciò che Agno
permette da ciò che vieta.

**Prima misura — quali store hanno un namespace.** I campi delle dataclass di
configurazione:

```text
UserProfileConfig        namespace=False
UserMemoryConfig         namespace=False
SessionContextConfig     namespace=False
EntityMemoryConfig       namespace=True
LearnedKnowledgeConfig   namespace=True
```

Cambiare il namespace della Learning Machine, quindi, non rende profilo e User
Memory specifici del progetto: lo dice la firma della configurazione, prima
ancora del comportamento.

**Seconda misura — come si chiamano le righe.** Le chiavi sono deterministiche
e il namespace non entra in quelle di profilo, memorie e contesto:

```text
id user_profile     utente demo, nessun namespace : user_profile_demo
id user_profile     utente demo, progetto alfa    : user_profile_demo
id user_memory      utente demo, progetto alfa    : memories_demo
id entity_memory    personale                     : entity_user/demo/personale_person_ada
id entity_memory    alfa                          : entity_user/demo/alfa_person_ada
```

Il parametro `namespace` è accettato dalla funzione che costruisce l'id ma non
usato per questi tre tipi: due progetti scrivono la stessa riga. Per le entità
invece il namespace è parte dell'identità, ed è l'asse che manca agli altri.

**Terza misura — due scritture in due progetti, una sola riga.** Ripetendo la
scrittura di un profilo con lo stesso utente e contenuti diversi:

```text
righe di profilo per demo dopo due scritture: 1 [{'name': 'secondo progetto'}]
entita' nel namespace personale: 1 - nel namespace alfa: 1
```

Il secondo progetto non aggiunge un profilo: sovrascrive quello del primo. Le
entità invece restano separate, e il filtro per namespace funziona.

### 2.4 Conseguenze

- Un ricordo di progetto **non ha dove stare** finché l'unico asse è l'utente.
  Il caso peggiore non è il rumore: è la sovrascrittura silenziosa della terza
  misura, che non produce un errore.
- Entità e intuizioni possono diventare per progetto **cambiando il namespace**
  che Ares già passa, senza toccare Agno; profilo, memorie e quaderno no.
- Il percorso è un'etichetta, e l'etichetta è già ambigua: due `api/` in due
  posti generano due id di sessione distinti ma la stessa etichetta logica, e
  uno spostamento cambia il percorso senza cambiare il progetto. Serve
  un'identità che il percorso non è.

## 3. Che cosa Agno 3.0.9 permette davvero

### 3.1 Per store

| Store | Namespace nella configurazione | Chiave della riga | Filtro in lettura | Asse progetto |
| --- | --- | --- | --- | --- |
| `UserProfileStore` | no | `user_profile_{user_id}` | `learning_type`, `user_id` | **no** |
| `UserMemoryStore` | no | `memories_{user_id}` | `learning_type`, `user_id` | **no** |
| `SessionContextStore` | no | `session_context_{session_id}` | `learning_type`, `session_id` | solo indiretto, via sessione |
| `EntityMemoryStore` | sì | `entity_{namespace}_{tipo}_{id}`, oppure `entity_user_{impronta utente}_{tipo}_{id}` | `namespace` esatto, più `user_id` solo con namespace `user` | **sì**, namespace libero |
| `LearnedKnowledgeStore` | sì | documento vettoriale | metadato `namespace` esatto; `user_id` solo con namespace `user` | **sì**, namespace libero |
| `DecisionLogStore` | no | riga per decisione | `agent_id`, `session_id` | no |

Riferimenti: `agno/learn/machine.py` (`namespace: Default namespace for
entity_memory and learned_knowledge`), `agno/learn/utils.py:69-83` per le
chiavi, `agno/learn/stores/*` per i filtri, `agno/db/sqlite/sqlite.py` per la
clausola `namespace` esatta.

### 3.2 I limiti accertati

Sono i vincoli che una proposta deve rispettare o dichiarare come debito.

1. **Un profilo per progetto non esiste nell'API attuale.** Lo store legge e
   scrive per `user_id`, la chiave è `user_profile_{user_id}` ed è una chiave
   primaria: una seconda riga per progetto collide. Lo stesso vale per le
   memorie.
2. **Il namespace non è ortogonale all'utente.** Con un namespace libero le
   entità scrivono `user_id` nullo: due utenti che condividono lo stesso
   namespace si vedono a vicenda. L'isolamento per utente oggi regge perché
   Ares compone l'utente *dentro* la stringa (`user/<id>/personale`), e la
   stessa composizione dovrà valere per il progetto.
3. **Il namespace non si cambia a metà turno nel percorso automatico.** Né
   `build_context` né `get_tools` inoltrano un namespace: quello effettivo è
   fissato alla costruzione. Un ambito che cambia da un turno all'altro
   richiede una chiamata esplicita alla macchina di apprendimento.
4. **Il filtro delle intuizioni è letterale e applicato dopo il limite.** Il
   namespace diventa un filtro sui metadati, non una clausola del motore
   vettoriale: i risultati sono filtrati *dopo* i primi `limit`, quindi una
   ricerca ristretta può restituire meno di `limit` elementi e perdere
   corrispondenze fuori dal gruppo iniziale. Due namespace non si interrogano
   con una sola ricerca. L'entità del taglio è misurata al §3.4: non è un
   caso di scuola, e con il namespace del progetto il blocco iniettato può
   restare vuoto. Va detto che oggi non succede nulla di tutto questo, perché
   l'ambito è uno solo per utente e il filtro non toglie niente: il limite si
   manifesta quando gli ambiti diventano due, cioè quando il §5 si realizza.
   La stessa condizione non vale per la colonna dell'owner, che Agno applica
   come prefiltro del motore: il percorso delle intuizioni non la usa.
5. **La colonna `metadata` delle righe di apprendimento esiste ma non è
   usata**: nessuno store la scrive e i lettori non la filtrano. Non è una
   via per il progetto senza modificare Agno.
6. **Le sessioni non si filtrano per metadato.** La cartella è scritta nel
   `session_data`, ma `get_sessions` non offre un filtro sul contenuto: Ares
   filtra in Python. Lo stesso vale per i run, che non hanno una colonna
   metadati.
7. **Il `user_id` assente spegne gli store per utente** (nessuna lettura,
   nessuna scrittura) e fa ricadere entità e intuizioni sul namespace
   predefinito `global`, che è condiviso. L'ambito mancante, quindi, non è
   neutro: ha un default pericoloso in due store su cinque.

### 3.3 Che cosa se ne deduce

Le sole leve disponibili senza modificare Agno sono **la composizione della
stringa del namespace** e **il filtro in codice prima di comporre il prompt o
di passare una richiesta**. La prima copre entità e intuizioni; la seconda
copre tutto ciò che gli store restituiscono in modo più largo del voluto —
profilo, memorie, quaderno, sessioni — ma solo se il dato porta con sé
l'informazione necessaria a filtrarlo. Da qui la proposta del §5: dare al
progetto un namespace composto per entità e intuizioni, e per gli archivi senza
namespace una **provenienza dichiarata dentro il dato**, resa visibile dal
punto in cui Ares già costruisce il testo per il prompt.

### 3.4 Esito delle due verifiche preliminari

Il §8 chiedeva due verifiche prima di scegliere la forma della provenienza
delle memorie e della ricerca delle intuizioni. Sono state fatte il 22
settembre 2026 sui pacchetti installati, con i doppi delle prove: nessun
modello reale, nessuna rete, un archivio temporaneo per ciascuna. Gli esiti
seguenti sono misure, non previsioni. Le misure sono ora la prova `ambiti`
(`tests/scoping_test.py`, nel runner offline), che le tiene ferme: se Agno
cambia una delle due premesse il fallimento arriva lì, e questo paragrafo va
aggiornato con lui.

**Memorie: la provenienza si scrive, sopravvive, e va riallineata.**

```text
1. dopo l'estrazione      : 1 {added_by_agent, content, created_at, id, source, updated_at}
   content                : Le migrazioni si scrivono a mano.
2. dopo la scrittura Ares : {added_by_agent, content, created_at, id, progetto, source, updated_at} progetto = alfa
3. dopo la riscrittura    : {added_by_agent, content, created_at, id, progetto, source, updated_at, updated_by_agent} progetto = alfa
   content                : Le migrazioni si scrivono a mano e si provano.
3b. dopo il riallineamento: beta
4. resa per il prompt     : '(fra parentesi quadre, la data in cui hai saputo la cosa)\n- Le migrazioni si scrivono a mano e si provano. [2026-09-22]'
   cosa vede il modello   : [{'id': 'e626ea51', 'content': 'Le migrazioni si scrivono a mano e si provano.'}]
```

Quattro fatti, con la loro conseguenza:

- Agno scrive già chiavi proprie in ogni voce — `source`, `added_by_agent`,
  e `updated_by_agent` dopo una riscrittura. Una chiave in più di Ares non è
  una forma nuova: è la stessa, e convive con quelle.
- Una scrittura di Ares **dopo il turno** persiste e sopravvive a un
  `update_memory` che cambia il contenuto: la voce riscritta porta ancora
  `progetto = alfa`. Le API sono quelle pubbliche — `get` e `save` — cioè le
  stesse che `ares/agent/echo.py` usa già per `istantanea` e `ripristina`, e
  il punto in cui Ares rilegge e riscrive gli store dopo il turno esiste.
- La sopravvivenza è anche il difetto: il modello **non vede la
  provenienza**, perché il prompt di estrazione riceve `{id, content}` e
  nient'altro. Può quindi riscrivere da un altro progetto una memoria che
  resta etichettata con il primo, e la misura lo mostra — contenuto nuovo,
  `progetto` vecchio. Il rimedio è della stessa natura: riallineare la chiave
  dopo il turno, sulle voci che il turno ha toccato. Misurato: `beta`.
- La resa per il prompt riceve le voci come stanno in archivio, quindi il
  filtro in `get_memories_text` — codice di Ares — è possibile, e le chiavi
  non servono al modello per essere utili.

**Intuizioni: il taglio è reale, e non è proporzionale al limite.**

```text
documenti: 30 in user/demo (uguali alla query), 5 in user/demo/progetti/alfa (vicini)
  filtro namespace, limit= 5 -> in ambito: 0
  filtro namespace, limit=20 -> in ambito: 0
  filtro namespace, limit=30 -> in ambito: 0
  filtro namespace, limit=35 -> in ambito: 5
  filtro namespace, limit=40 -> in ambito: 5
  senza filtro,     limit= 5 -> in ambito: 0
  owner (prefiltro), limit= 5 -> righe dell'utente: 5
  ambito unico di oggi, limit= 5 -> in ambito: 5
```

Finché il gruppo iniziale è tutto fuori ambito, la ricerca nel progetto
restituisce **zero** anche alzando il limite fino a contare tutti i documenti
fuori ambito (30); il progetto riappare solo quando il limite li supera (35).
Il prefiltro dell'owner, nella stessa tabella, restituisce 5 su 5 con limite
5: la differenza non è quanti dati ci sono, è il momento in cui il filtro
agisce. E con il namespace unico di oggi il filtro restituisce 5 su 5: il
problema nasce dalla separazione, non dall'esistente — il che spiega perché
non sia mai stato visto.

Conseguenza per la proposta: due ricerche, una per namespace, non bastano
finché restano appese all'iniezione automatica, che usa `limit=5` e un
namespace fissato alla costruzione. Il blocco delle intuizioni va composto da
Ares — una ricerca per namespace con un limite esplicito e una fusione — cioè
prendendo in mano quella parte del prompt, che il contratto del nucleo già
contempla. L'alternativa è trasformare il filtro dei metadati in prefiltro,
e vuol dire una sottoclasse del vector db o una patch: più efficace, più
costoso da sostenere fra le versioni.

## 4. Identità di progetto: dal percorso a un id

### 4.1 Che cosa non può essere identità

| Candidato | Perché no |
| --- | --- |
| Impronta del percorso | Uno spostamento o un `mv` cambiano l'identità: il progetto perde la memoria senza aver fatto niente |
| Nome della cartella | Due `api/` in due posti collidono; una rinomina azzera la memoria. È già il titolo degli id di sessione, e va bene lì |
| Remote Git | Un fork, un clone senza remote, un secondo remote, un repository senza Git: quattro casi in cui l'identità o manca o è la stessa per progetti diversi |
| Contenuto di `ARES.md` | È configurazione, si modifica spesso, e un progetto può non averlo |
| Utente più percorso | Impedisce di riconoscere lo stesso progetto altrove e di collegare un worktree |

Nessuno di questi valori è verificabile come *lo stesso progetto di ieri*: sono
tutti descrizioni del presente. L'identità deve essere un valore **assegnato
una volta e ricordato**, non ricalcolato.

### 4.2 Il registro dei progetti (proposta)

Un registro locale, dentro `percorsi.stato`, quindi incluso nel backup. Contiene:

- per ogni progetto: un `project_id` **opaco e generato** (non derivato da
  nulla), un'etichetta leggibile scelta al primo riconoscimento, e la data;
- per ogni associazione: il percorso (o i percorsi) che oggi puntano a quel
  progetto, con l'indicazione del tipo — principale, clone, worktree —
  e la data dell'ultimo uso;
- niente contenuti di progetto: il registro è un indice, non una memoria.

La risoluzione avviene in un punto solo, il confine del programma, come per
`Utente.da_grezzo`: da lì in poi l'ambito è un tipo già valido e nessun lettore
si chiede se il percorso è quello giusto. L'ordine è: `--progetto` esplicito,
se c'è; altrimenti il percorso esatto nel registro; altrimenti il percorso
genitore più specifico registrato; altrimenti nessun progetto (ambito
personale) con la proposta di registrarlo al primo apprendimento. Il modello
può proporre un'etichetta o un'associazione, ma il codice la valida e la
scrive: è il principio del §6.2.

### 4.3 I casi da definire

| Caso | Comportamento proposto | Perché |
| --- | --- | --- |
| Progetto in una sottocartella | La risoluzione sceglie l'associazione più specifica; un progetto annidato in un altro è raggiungibile per percorso esatto | Evita che lavorare in `vendor/` di un progetto registri tutto nel progetto esterno |
| Due progetti annidati | Il più specifico vince solo se registrato esplicitamente; altrimenti si chiede | La scelta silenziosa qui sbaglia dati in modo invisibile |
| Cartella senza Git | Progetto come gli altri: l'identità non dipende da Git | Metà delle cartelle di lavoro di una persona non sono repository |
| Spostamento della cartella | L'associazione si aggiorna; il progetto resta lo stesso | È la ragione per cui l'id non è un'impronta del percorso |
| Clone | Nuova associazione allo stesso progetto, proposta e non automatica | Un clone può essere un progetto nuovo: solo la persona lo sa |
| Worktree | Nuova associazione allo stesso progetto | Un worktree è lo stesso lavoro su un altro ramo |
| Copia e branch | Come il clone: proposta, mai deduzione dal remote | Il remote non è identità |
| Conversazione personale (nessuna cartella) | Ambito personale, progetto assente | Non tutto appartiene a un progetto: una preferenza personale resta disponibile ovunque |

### 4.4 Forma dell'id e del namespace

L'id utente ammette solo lettere e cifre ASCII, punto, trattino e trattino
basso, e la barra è esclusa di proposito: è il separatore del namespace, e un
id che la contiene annida un namespace dentro l'altro
([identita.py](../ares/state/identita.py)). Il `project_id` segue la stessa
regola e diventa un segmento, non un percorso:

```text
user/<utente>/personale                      entita' e note personali, valide ovunque
user/<utente>/progetti/<project_id>          entita', note e intuizioni del progetto
```

Il valore `personale` resta riservato, come oggi. La barra separa la
struttura, l'id non la contiene: è la stessa composizione che già isola gli
utenti, quindi non serve alcun cambio di Agno.

## 5. Ripartizione degli archivi (proposta)

| Archivio | Ambito proposto | Come si esprime | Cosa cambia rispetto a oggi |
| --- | --- | --- | --- |
| Profilo | persona | `user_id` (invariato) | Nulla: le preferenze personali restano trasversali, come chiede la verifica attesa della roadmap |
| Memorie | persona, con provenienza dichiarata | contenuto con il progetto quando esiste, riallineato a ogni turno che tocca la voce; filtro in resa | Le memorie di progetto si annotano e si presentano solo nel progetto; quelle personali ovunque |
| Contesto di sessione | sessione | `session_id` (invariato) | Nulla |
| Entità | progetto | `namespace_entita(utente, progetto)` | Oggi `user/<id>/personale`; le entità personali restano nel namespace personale |
| Intuizioni | progetto, personale consultabile | namespace del progetto e del personale, cercati a parte con un limite esplicito | Oggi `user/<id>`; il blocco va composto da Ares, non dall'iniezione automatica |
| Quaderno | progetto | namespace del quaderno composto col progetto | Oggi per utente: due progetti si vedono i file |
| Sessioni e cronologia | sessione, etichetta di progetto nei metadati | `CHIAVE_CARTELLA` più l'id di progetto | La cartella resta l'etichetta leggibile; l'id è il filtro |
| Registro dei progetti | installazione (con l'utente come proprietario) | nuovo archivio in `percorsi.stato` | Non esiste |

Due precisazioni che riguardano il progetto e non l'utente:

- **Il profilo non diventa per progetto.** Non è una rinuncia: ciò che è
  specifico di un progetto è un fatto (entità) o un criterio (intuizione), e
  quei due archivi hanno già l'asse. Un profilo per progetto richiederebbe
  chiavi che Agno non prevede e produrrebbe un secondo profilo da tenere
  allineato.
- **Le memorie hanno bisogno di una provenienza riallineata.** Lo strumento con
  cui il modello aggiunge una memoria porta solo il testo, quindi la
  provenienza non può venire dall'estrazione: la scrive Ares dopo il turno,
  nel punto in cui già rilegge e riscrive gli store, e la riallinea sulle voci
  che il turno ha toccato — perché il modello non vede la chiave e può
  riscrivere da un altro progetto una memoria che la porta ancora. Misurato al
  §3.4: si scrive, sopravvive alla riscrittura, e senza riallineamento resta
  quella di prima. Il filtro in resa è l'altra metà, e vive in
  `AresMemories.get_memories_text`, che è codice di Ares
  ([schemas.py](../ares/agent/schemas.py)).
- **Le intuizioni non si possono dividere e lasciare all'iniezione
  automatica.** Il filtro per namespace agisce dopo il limite (§3.2, §3.4),
  quindi una ricerca nel progetto può restituire zero: il blocco va composto
  da Ares, una ricerca per namespace con un limite esplicito, e l'iniezione
  automatica smette di essere il percorso di quelle righe.

## 6. Ambiti applicati dal codice

### 6.1 Dove l'ambito deve viaggiare

Il punto 2 della roadmap chiede che l'ambito sia trasportato e validato in
quattro momenti. Per ognuno, la proposta è che l'ambito sia un parametro
obbligatorio dell'operazione, e che nessun default silenzioso lo sostituisca:

| Momento | Che cosa riceve l'ambito | Chi lo valida |
| --- | --- | --- |
| Estrazione | utente, sessione e progetto del turno | Il codice, prima di avviare l'estrazione: il modello non sceglie dove si scrive |
| Salvataggio | il progetto risolto all'apertura della sessione | Il codice; il modello può proporre un'etichetta, non un ambito |
| Ricerca | il progetto corrente più, se richiesto, il personale | Il codice, che compone la ricerca e filtra i risultati |
| Composizione del prompt | il progetto corrente, dichiarato nel testo | Il codice, che non inserisce dati di altri ambiti |

### 6.2 Ambito mancante o ambiguo

La roadmap lo dice esplicitamente: il modello può proporre una
classificazione, ma il codice deve validarla. Le tre situazioni:

- **progetto assente** (conversazione personale, cartella non registrata): si
  scrive nell'ambito personale. Non è un errore e non deve diventare un
  rifiuto: una preferenza personale deve restare possibile da qualunque
  cartella.
- **ambito ambiguo** (due progetti annidati, percorso non registrato ma sotto
  un progetto registrato): si chiede una volta, e la risposta si ricorda come
  associazione. Non si sceglie il più probabile.
- **ambito assente dove il default è pericoloso** (entità e intuizioni, il cui
  namespace predefinito è `global`): il costruttore non deve poter nascere
  senza namespace. Oggi Ares lo passa sempre; la proposta è renderlo
  obbligatorio nel tipo, così che l'omissione sia un errore di compilazione e
  non una scrittura condivisa.

### 6.3 I percorsi che allargano l'ambito

Uno per uno, con la decisione proposta. "Dichiarare" significa che il
comportamento resta ma il testo o l'output dicono che l'ambito è più largo;
"chiudere" significa che il percorso non deve più attraversare il progetto
senza una richiesta esplicita.

| Percorso | Trattamento proposto |
| --- | --- |
| `search_past_sessions` | Dichiarare nel prompt e nell'aiuto che elenca le conversazioni di ogni progetto; l'elenco ristretto per cartella resta il percorso normale |
| `read_past_session` | Lasciare: legge per id, e l'id si ottiene da un elenco. Verificare che il prompt non suggerisca di provare id a caso |
| `/sessioni tutte` e `ares inspect` senza `--session` | Dichiarare nell'output che l'elenco attraversa i progetti; aggiungere il filtro per progetto quando il registro esiste |
| Quaderno | Chiudere: il quaderno diventa di progetto, con il personale come ambito separato |
| `ares inspect --file` | Dichiarare il progetto del file, una volta che il quaderno ha un progetto |
| `ares entities audit` e `merge` | Dichiarare l'ambito nell'intestazione; offrire il filtro per progetto |
| `ares sessions status`, `prune`, `delete` | Dichiarare l'ambito; il filtro per progetto si aggiunge quando serve, non prima |
| Backup e ripristino | Lasciare: coprono l'installazione, ed è la ragione per cui il registro ci deve stare dentro |

Il criterio comune: un allargamento **esplicito** — l'utente ha chiesto
`tutte`, oppure ha invocato un comando di manutenzione — resta, purché sia
detto. Un allargamento **implicito** in una risposta normale va chiuso o
dichiarato nel prompt.

## 7. Superficie minima (proposta)

Il documento non progetta l'interfaccia, ma l'ambito ha bisogno di un modo per
essere nominato. Il minimo:

- `--progetto <id o etichetta>` all'avvio della chat, come esiste
  `--workspace`: vince sulla risoluzione automatica;
- `ares progetti` con gli elenchi, l'associazione di una cartella e la
  rimozione; le azioni distruttive passano dalle conferme esistenti;
- `ares inspect --progetto <id>` per guardare un progetto senza aprirlo;
- `/progetto` nella chat per leggere l'ambito corrente e cambiarlo tra un
  turno e l'altro — che richiede una ricostruzione dell'agente, come
  `/sessione`;
- il progetto corrente nell'intestazione dell'avvio e in `/cartella`.

Niente elenchi interattivi nuovi: le scelte si fanno con gli stessi strumenti
di `scegli_sessione`, e il silenzio dell'utente non deve mai significare una
scelta di progetto.

## 8. Prove di accettazione (da implementare)

Le prove sono offline e deterministiche, nella forma delle prove esistenti
([testing.md](testing.md)): nessun modello reale, nessuna rete, un ambiente
temporaneo per prova. Le due verifiche preliminari che il documento chiedeva
sono già state fatte, e sono misurate al §3.4; le prove che ne derivano sono
elencate qui come codice nuovo, perché il comportamento da fissare è quello
della proposta, non quello del framework.

| Prova | Che cosa dimostra | Tipo |
| --- | --- | --- |
| Due progetti, una persona | Una convenzione registrata nel progetto A non compare nelle risposte del progetto B, e viceversa | codice nuovo |
| La preferenza trasversale | Una preferenza personale resta disponibile in A e in B | codice nuovo |
| Spostamento | Spostata la cartella e aggiornata l'associazione, il progetto e i suoi ricordi restano gli stessi | codice nuovo |
| Clone e worktree | Il comportamento dichiarato accade: proposta di associazione, nessuna condivisione automatica dal remote | codice nuovo |
| Senza Git | Una cartella senza repository è un progetto valido, registrabile e riconosciuto | codice nuovo |
| Ambito mancante | Una scrittura senza progetto finisce nel personale e non nel progetto corrente | codice nuovo |
| Ambito ambiguo | Con due progetti annidati non registrati la richiesta è esplicita e nessuna scrittura avviene prima della risposta | codice nuovo |
| Nessun allargamento silenzioso | `search_past_sessions`, `inspect` e il quaderno non restituiscono dati di un altro progetto come se fossero di questo | codice nuovo |
| Il quaderno non si mescola | Un file scritto nel progetto A non è leggibile dal progetto B, e il personale resta leggibile da entrambi | codice nuovo |
| Provenienza sopravvive e si riallinea | Dopo una riscrittura da un altro progetto la provenienza della voce è quella nuova, e il filtro in resa presenta la voce solo lì | codice nuovo |
| Intuizioni di progetto non troncate | Con l'ambito personale che occupa il gruppo iniziale, la ricerca del progetto restituisce le sue intuizioni e non zero | codice nuovo |
| Backup e ripristino | Il registro dei progetti e gli ambiti tornano identici dopo un ciclo di snapshot e ripristino | codice nuovo |

Le due prove che dipendevano dalle verifiche sono le più vicine a un difetto
silenzioso: entrambe passano quando i dati sono pochi, ed è la ragione per cui
vanno scritte con un archivio popolato — l'ambito personale più grande del
progetto, e una voce riscritta da un altro progetto. La loro **premessa nel
framework** non è più una misura di laboratorio: la tiene ferma la prova
`ambiti` di `tests/run.py` (§3.4). Qui resta il comportamento di Ares, che il
codice non ha ancora.

## 9. Decisioni da chiudere prima del codice

| Decisione | Opzioni | Nota |
| --- | --- | --- |
| Forma dell'id di progetto | Opaco generato (proposto) oppure leggibile e unico | Un id leggibile invita a derivarlo da qualcosa: è il rischio che il §4.1 esclude |
| Dove vive il registro | In `percorsi.stato` (proposto), quindi nel backup | Fuori dallo stato non verrebbe salvato né ripristinato |
| Dati esistenti | Nessuna migrazione per profilo e memorie se restano personali; le entità di `user/<id>/personale` restano personali finché non si riassegnano | Il debito va dichiarato, non risolto in silenzio |
| Provenienza delle memorie | **Campo dentro il dato, riallineato dopo ogni turno che tocca la voce** (misurato al §3.4), con il filtro in resa in `get_memories_text` | La sola resa filtrata non basta: senza la chiave non c'è niente da filtrare |
| Intuizioni personali e di progetto | **Blocco composto da Ares: una ricerca per namespace con limite esplicito e fusione** (misurato al §3.4); il prefiltro del vector db resta l'alternativa più efficace e più costosa | L'iniezione automatica usa `limit=5` e un namespace solo: con due ambiti può restituire zero |
| Chi compone il blocco delle intuizioni | Ares, nel prompt, come già fa per le altre sezioni; l'iniezione automatica resta per entità e contesto | Conseguenza della riga sopra, e va decisa insieme |
| Clone e worktree | Associazione proposta oppure automatica per worktree | La persona sa se un clone è lo stesso progetto |
| Allargamenti dichiarati | Quali restano e con quale testo | Elenco al §6.3 |
| Versione del formato | Il manifesto degli snapshot dichiara `namespace_format: user/<id>`; con il progetto cambia, e il numero di formato va incrementato | `ares/backup/snapshots.py` |

## 10. Debito dichiarato

- Le entità esistenti vivono in `user/<id>/personale`: restano personali e
  disponibili ovunque finché non vengono riassegnate a un progetto. Nessuna
  migrazione automatica le sposta, perché l'appartenenza non è deducibile.
- Profilo e memorie già scritti non hanno provenienza. Con la chiave decisa al
  §9, i ricordi esistenti restano senza progetto e valgono ovunque: è il
  comportamento corretto, ed è anche l'unico compatibile senza dedurre
  un'appartenenza che nessuno ha dichiarato.
- Il blocco delle intuizioni passa dall'iniezione automatica a una
  composizione di Ares (§3.4). È un cambiamento di comportamento, non solo di
  forma: il testo del prompt per quelle righe cambia, e la prova del costo
  dell'apprendimento ne terrà conto.
- La ricerca delle intuizioni resta più debole di quella delle entità: il
  prefiltro del motore non è disponibile senza una sottoclasse del vector db,
  e con un limite esplicito il taglio si riduce senza sparire. È un debito
  verso Agno, non verso Ares.
- Il registro dei progetti non esiste: finché non c'è, il comportamento
  attuale è quello descritto al §2, e ogni prova del §8 che lo richiede
  fallisce per assenza, non per difetto.
