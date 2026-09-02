# Agno in Ares

Ares usa **Agno 3.0.1**, ultima stable verificata il 30 agosto 2026, come framework dell'agente. Agno fornisce il ciclo di
esecuzione, gli store e le primitive agentiche; Ares decide invece politica
local-first, modelli Ollama, interfaccia, confini degli strumenti, schema dei
dati, backup e comportamento dell'apprendimento.

La distinzione evita due errori opposti: duplicare nel progetto cio' che il
framework fa gia' bene, oppure presentare come caratteristica di Ares una
capacita' Agno che qui non e' stata configurata e verificata.

## Cosa usa Ares oggi

| Capacita' Agno | Uso concreto in Ares | Decisione del progetto |
| --- | --- | --- |
| `Agent`, run ed eventi streaming | ciclo `run → pausa → continue_run`, output e metriche | `agent/turn_core.py` traduce gli eventi in un contratto indipendente dalla CLI |
| modello Ollama ed embedder Ollama | estrazione strutturata ed embedding restano locali; la conversazione puo' usare un modello cloud di Ollama inoltrato dal daemon | nessun provider diverso da Ollama, nessuna chiave API nell'ambiente: `assistant_runtime` rifiuta un nome cloud fuori da `MAIN_MODEL` |
| `SqliteDb` | sessioni, run, profilo, memorie, contesto ed entita' | file privati, lock cooperativo e snapshot verificati |
| Learning Machine | profilo, memoria utente, contesto di sessione, entita' e conoscenza appresa | schema italiano, namespace per utente e post-hook sul run completo |
| `Knowledge` + LanceDB | ricerca ibrida nelle intuizioni riutilizzabili | indice incorporato, embedding locale e nessun servizio vettoriale remoto |
| FileSystem | quaderno persistente verbatim separato dalle memorie curate | database distinto e namespace per utente |
| Workspace + HITL | lettura, modifica e comandi in una sola directory | nomi `workspace_*`; operazioni sensibili fermano il run e chiedono conferma |
| cronologia e ricerca fra sessioni | finestra recente nel prompt e strumenti per recuperare il passato | limiti espliciti per non saturare il contesto |
| `ResultStore` | risultati tool oltre 16.000 caratteri salvati lossless entro la quota Agno e sostituiti da un'anteprima | indice in `kairos.db`, payload in `filesystem.db`, entrambi inclusi nei backup; retention legata alla sessione |

La [Learning Machine](https://docs.agno.com/learning/overview) di Agno offre
anche Decision Log, modalita' Propose e curatela degli apprendimenti. Sono
primitive interessanti, ma non diventano automaticamente funzionalita' di
Ares: richiedono prima una politica utente, una rappresentazione nella CLI e
copertura nei backup.

## Cosa porta Agno 3 ad Ares

Agno 3 normalizza ogni run nella tabella `agno_runs`, lasciando alle sessioni
solo i propri metadati. Per Ares significa scritture che non ricopiano tutta
la cronologia a ogni turno e accesso diretto ai run. La 3.0.1 aggiunge inoltre
cache degli schemi degli strumenti e caricamento incrementale della storia:
due miglioramenti pertinenti a un assistente longevo con molti strumenti.
Consulta le [note 3.0.0](https://github.com/agno-agi/agno/releases/tag/v3.0.0)
e le [note 3.0.1](https://github.com/agno-agi/agno/releases/tag/v3.0.1).

La major estende anche l'isolamento per utente e rende stabili gli id dei
toolkit. Ares mantiene i propri namespace espliciti `user/<id>`: per le
intuizioni questo e' un filtro di metadati custom, non il nuovo argomento
`user_id` del vector DB. L'indice LanceDB attuale non richiede quindi la
migrazione delle collezioni per-user descritta da Agno; l'isolamento gia'
esistente continua a essere verificato dalla suite.

SQLite in Agno 3 usa WAL e puo' creare i sidecar `-wal` e `-shm`. Gli snapshot
di Ares non copiano il file aperto alla cieca: usano l'API backup di SQLite
sotto lock esclusivo, ottenendo una copia consistente anche con WAL.

## Adeguamenti adottati

- **Risultati tool grandi:** `Workspace.read_file` puo' leggere file molto
  piu' grandi della finestra utile del modello e `get_chat_history` puo'
  restituire una sessione intera. Oltre 16.000 caratteri Agno conserva il
  contenuto completo e lascia nel messaggio un envelope con anteprima,
  dimensione e id. `read_result` e `search_result` permettono di recuperarlo
  a pagine senza reinserirlo tutto nel prompt. Il limite di Agno e' 8.000.000
  byte per risultato e 200.000.000 per sessione: oltre la quota il fallback
  con testa e coda dichiara che il testo completo non e' stato salvato.
- **Retention coerente:** Ares non assegna un TTL ai singoli risultati,
  perche' lascerebbe riferimenti non risolvibili nelle sessioni conservate.
  `sessions/maintenance.py` seleziona invece intere conversazioni inattive con
  anteprima, protezioni esplicite, lock e backup. La cancellazione Agno porta
  con se' run, indice e payload; Ares elimina anche il relativo contesto della
  Learning Machine e verifica entrambi i database.
- **Storia degli strumenti:** Ares continua a includere cinque turni recenti,
  ma soltanto le ultime dieci tool call storiche. Messaggi e risultati completi
  restano in SQLite: e' un filtro del contesto, non una retention dei dati.
- **Run normalizzati:** Ares usa le API v3 per persistere i run e continua a
  consumare `session.runs`, che Agno ricompone dalla tabella dedicata. Non ci
  sono query dirette verso la vecchia colonna JSON.
- **HITL v3:** la ripresa passa la lista `requirements` del `RunOutput`; le
  operazioni workspace sensibili continuano quindi sullo stesso run dopo la
  conferma.
- **WAL e backup:** entrambi i database vengono aperti una volta durante la
  costruzione per materializzare WAL; gli snapshot SQLite ne preservano il
  journal mode oltre alle pagine.

## Capacita' disponibili ma non abilitate

- **Media offloading:** Ares non accetta ancora immagini, audio o video nella
  CLI; abilitarlo ora creerebbe storage senza un percorso utente che lo usi.
- **CodeMode:** riduce molti schemi tool a un kernel Python programmabile, ma
  i circa venticinque strumenti di Ares non giustificano un nuovo ambiente di
  esecuzione. Il workspace con conferme mantiene confini piu' leggibili.
- **Skills:** il caricamento progressivo di istruzioni e riferimenti locali e'
  promettente per specializzazioni future. L'esecuzione degli script delle
  skill deve pero' essere integrata col modello di conferme e col confine del
  workspace prima di essere esposta.
- **Decision Log:** adatto ad audit e feedback sulle decisioni; per Ares serve
  decidere cosa registrare senza trasformare ogni conversazione in
  telemetria locale rumorosa.
- **Learning `PROPOSE`:** buon candidato per apprendimenti che l'utente vuole
  approvare prima del salvataggio. Richiede un flusso CLI distinto dalle
  conferme degli strumenti distruttivi.
- **Curator:** puo' deduplicare e potare apprendimenti, ma deve passare dallo
  stesso modello di anteprima, backup e applicazione gia' usato per le
  entita'.
- **Session summary e compressione:** richiedono ulteriori inferenze e si
  sovrappongono al contesto di sessione gia' estratto. Offloading e limiti
  deterministici proteggono la finestra senza una chiamata al modello.
- **Cache della sessione:** evita letture ripetute dal database ma puo'
  diventare stantia quando due processi Ares aprono la stessa sessione; il
  lock condiviso permette proprio quella concorrenza, quindi resta spenta.
- **AgentOS, Studio, scheduler, team e workflow:** Agno puo' esporre agenti
  tramite API e interfacce, eseguire code durevoli e coordinare piu' agenti.
  Ares oggi e' una CLI personale su un solo host: abilitarli allargherebbe il
  modello di sicurezza e non e' parte di questo upgrade.
- **Context Providers e integrazioni remote:** Agno offre connettori e
  accesso live a fonti esterne. Ares resta deliberatamente Ollama-only:
  l'unico servizio remoto ammesso e' il cloud di Ollama, raggiunto dal
  daemon locale e solo per il modello conversazionale. Il percorso diretto
  di Agno verso `https://ollama.com` con `api_key` non viene usato.
