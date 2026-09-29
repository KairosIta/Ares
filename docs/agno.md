# Agno in Ares

Ares usa **Agno 3.0.11** (verificata il 25 settembre 2026) come framework
dell'agente. Agno fornisce il ciclo di esecuzione, gli store e le primitive
agentiche; Ares decide politica local-first, modelli Ollama, interfaccia,
confini degli strumenti, schema dei dati, backup e comportamento
dell'apprendimento.

Questa pagina serve a non duplicare ciò che il framework fa già, e a non
presentare come funzionalità di Ares una capacità Agno che qui non è
configurata né verificata.

## Cosa usa Ares oggi

| Capacità Agno | Uso concreto in Ares | Decisione del progetto |
| --- | --- | --- |
| `Agent`, run ed eventi streaming | ciclo `run → pausa → continue_run`, output e metriche | `agent/turn_core.py` traduce gli eventi in un contratto indipendente dalla CLI |
| modello Ollama ed embedder Ollama | l'embedding resta locale; conversazione ed estrazione strutturata possono usare, ciascuna per scelta nel `.env`, un modello cloud di Ollama inoltrato dal daemon | nessun provider diverso da Ollama, nessuna chiave API nell'ambiente: `build_knowledge` rifiuta un nome cloud per `EMBEDDER_MODEL` |
| `SqliteDb` | sessioni, run, profilo, memorie, contesto ed entità | file privati, lock cooperativo e snapshot verificati |
| Learning Machine | profilo, memoria utente, contesto di sessione, entità e conoscenza appresa | schema italiano, namespace per utente e post-hook sul run completo |
| `Knowledge` + LanceDB | ricerca ibrida nelle intuizioni riutilizzabili | indice incorporato, embedding locale e nessun servizio vettoriale remoto |
| FileSystem | quaderno persistente verbatim separato dalle memorie curate | database distinto e namespace per utente |
| Workspace + HITL | lettura, modifica e comandi in una sola directory | nomi `workspace_*`; operazioni sensibili fermano il run e chiedono conferma |
| cronologia e ricerca fra sessioni | finestra recente nel prompt e strumenti per recuperare il passato | limiti espliciti per non saturare il contesto |
| `ResultStore` | risultati tool oltre 16.000 caratteri salvati lossless entro la quota Agno e sostituiti da un'anteprima | indice in `kairos.db`, payload in `filesystem.db`, entrambi inclusi nei backup; retention legata alla sessione |

La [Learning Machine](https://docs.agno.com/learning/overview) di Agno offre
anche Decision Log, modalità Propose e curatela degli apprendimenti. Qui
non sono abilitate: ognuna richiede prima una politica utente, una
rappresentazione nella CLI e la copertura nei backup (vedi
[sotto](#capacità-disponibili-ma-non-abilitate)).

**Profilo e memorie non si possono far confermare dal framework.** Delle
quattro modalità di apprendimento (`ALWAYS`, `AGENTIC`, `PROPOSE`, `HITL`),
`PROPOSE` vale solo per `LearnedKnowledgeStore` — `UserProfileStore` e
`UserMemoryStore` la rifiutano — e `HITL` è "reserved for future use;
unsupported by every store". Per questo Ares costruisce la conferma a valle
(vedi "Confini di sicurezza" in [architecture.md](architecture.md)).
`tests/agno_contract_test.py` diventa rosso se Agno cambia questo
comportamento.

## Cosa porta Agno 3 ad Ares

Agno 3 normalizza ogni run nella tabella `agno_runs`, lasciando alle sessioni
solo i propri metadati: le scritture non ricopiano la cronologia a ogni
turno e i run sono accessibili direttamente. La 3.0.1 aggiunge la cache
degli schemi degli strumenti e il caricamento incrementale della storia.
Dettagli nelle [note 3.0.0](https://github.com/agno-agi/agno/releases/tag/v3.0.0)
e [3.0.1](https://github.com/agno-agi/agno/releases/tag/v3.0.1).

### Aggiornare Agno

Il vincolo in `pyproject.toml` è `>=3.0.2,<3.1`: le patch, fino alla
[3.0.11](https://github.com/agno-agi/agno/releases/tag/v3.0.11), entrano dal
solo `uv.lock`. Prima di entrare nel lock ogni patch viene provata sulle
superfici che Ares usa: le firme di `LearningMachine.process` e di
`SessionContextStore`, che Ares sovrascrive, e il ciclo REPL completo.

La versione è citata a mano in più documenti. `tests/agno_contract_test.py`
li confronta con l'installato (l'elenco è `FILE_CHE_DICHIARANO`) e nomina il
file da allineare. `CHANGELOG.md` e `docs/memory-quality.md` sono esclusi,
perché citano le versioni di allora.

La major estende anche l'isolamento per utente e rende stabili gli id dei
toolkit. Ares mantiene i propri namespace espliciti `user/<id>`: per le
intuizioni questo è un filtro di metadati custom, non il nuovo argomento
`user_id` del vector DB. L'indice LanceDB attuale non richiede quindi la
migrazione delle collezioni per-user descritta da Agno; l'isolamento già
esistente continua a essere verificato dalla suite.

SQLite in Agno 3 usa WAL e può creare i sidecar `-wal` e `-shm`. Gli snapshot
di Ares non copiano il file aperto alla cieca: usano l'API backup di SQLite
sotto lock esclusivo, ottenendo una copia consistente anche con WAL.

## Adeguamenti adottati

- **Risultati tool grandi:** `Workspace.read_file` può leggere file molto
  più grandi della finestra utile del modello e `get_chat_history` può
  restituire una sessione intera. Oltre 16.000 caratteri Agno conserva il
  contenuto completo e lascia nel messaggio un envelope con anteprima,
  dimensione e id. `read_result` e `search_result` permettono di recuperarlo
  a pagine senza reinserirlo tutto nel prompt. Il limite di Agno è 8.000.000
  byte per risultato e 200.000.000 per sessione: oltre la quota il fallback
  con testa e coda dichiara che il testo completo non è stato salvato.
- **Retention coerente:** Ares non assegna un TTL ai singoli risultati,
  perché lascerebbe riferimenti non risolvibili nelle sessioni conservate.
  `sessions/maintenance.py` seleziona invece intere conversazioni inattive con
  anteprima, protezioni esplicite, lock e backup. La cancellazione Agno porta
  con sé run, indice e payload; Ares elimina anche il relativo contesto della
  Learning Machine e verifica entrambi i database.
- **Storia degli strumenti:** Ares continua a includere cinque turni recenti,
  ma soltanto le ultime dieci tool call storiche. Messaggi e risultati completi
  restano in SQLite: è un filtro del contesto, non una retention dei dati.
- **Run normalizzati:** Ares usa le API v3 per persistere i run e continua a
  consumare `session.runs`, che Agno ricompone dalla tabella dedicata. Non ci
  sono query dirette verso la vecchia colonna JSON.
- **Nessuna chiamata di chiusura dopo l'estrazione:** Agno richiamerebbe il
  modello dopo la tool call solo per sentirgli dire che ha finito, mentre
  l'esito si legge già da `response.tool_executions`. Profilo e memorie
  impostano `stop_after_tool_call` sovrascrivendo
  `_build_functions_for_model` in `agent/learning.py` (superficie privata,
  come il retry del contesto), come già fa il contesto di sessione. I numeri
  sono in [memory-quality.md](memory-quality.md);
  `tests/learning_cost_test.py` verifica le tre chiamate per turno e che la
  scrittura arrivi negli store.
- **HITL v3:** la ripresa passa la lista `requirements` del `RunOutput`; le
  operazioni workspace sensibili continuano quindi sullo stesso run dopo la
  conferma.
- **WAL e backup:** entrambi i database vengono aperti una volta durante la
  costruzione per materializzare WAL; gli snapshot SQLite ne preservano il
  journal mode oltre alle pagine.

## Capacità disponibili ma non abilitate

- **Media offloading:** Ares non accetta ancora immagini, audio o video nella
  CLI; abilitarlo ora creerebbe storage senza un percorso utente che lo usi.
- **CodeMode:** riduce molti schemi tool a un kernel Python programmabile, ma
  i circa venticinque strumenti di Ares non giustificano un nuovo ambiente di
  esecuzione. Il workspace con conferme mantiene confini più leggibili.
- **Skills:** il caricamento progressivo di istruzioni e riferimenti locali è
  promettente per specializzazioni future. L'esecuzione degli script delle
  skill deve però essere integrata col modello di conferme e col confine del
  workspace prima di essere esposta.
- **Decision Log:** adatto ad audit e feedback sulle decisioni; per Ares serve
  decidere cosa registrare senza trasformare ogni conversazione in
  telemetria locale rumorosa.
- **Learning `PROPOSE`:** vale solo per `learned_knowledge`, che in Ares è
  già `AGENTIC` (il modello sceglie esplicitamente di salvare). Lì non
  aggiunge una pausa imposta, solo istruzioni che chiedono al modello di
  proporre prima di salvare: un'approvazione che dipende dalla sua
  obbedienza. La conferma sulla memoria durevole è costruita in Ares
  (`agent/echo.py`, `CONFERMA_APPRENDIMENTI`).
- **Curator:** può deduplicare e potare apprendimenti, ma deve passare dallo
  stesso modello di anteprima, backup e applicazione già usato per le
  entità.
- **Session summary e compressione:** richiedono ulteriori inferenze e si
  sovrappongono al contesto di sessione già estratto. Offloading e limiti
  deterministici proteggono la finestra senza una chiamata al modello.
- **Cache della sessione:** evita letture ripetute dal database ma può
  diventare stantia quando due processi Ares aprono la stessa sessione; il
  lock condiviso permette proprio quella concorrenza, quindi resta spenta.
- **AgentOS, Studio, scheduler, team e workflow:** Agno può esporre agenti
  tramite API e interfacce, eseguire code durevoli e coordinare più agenti.
  Ares oggi è una CLI personale su un solo host: abilitarli allargherebbe il
  modello di sicurezza.
- **Context Providers e integrazioni remote:** Agno offre connettori e
  accesso live a fonti esterne. Ares resta deliberatamente Ollama-only:
  l'unico servizio remoto ammesso è il cloud di Ollama, raggiunto dal
  daemon locale, per il modello conversazionale e, su scelta separata nel
  `.env`, per quello che estrae le memorie; mai per l'embedding. Il percorso
  diretto di Agno verso `https://ollama.com` con `api_key` non viene usato.
