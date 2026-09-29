# Audit mirato per il refactor del core applicativo

Data: 2026-09-28.
**Stato: analisi del codice attuale.** Serve a guidare la prima estrazione
dal client CLI verso un nucleo applicativo.

## 1. Ciclo di vita della sessione

### Come nasce
- La chat nuova genera l'id con `cli/cartella.nuovo_id_sessione` quando c'è
  una cartella di lavoro; senza cartella resta il nome `"principale"`.
- `cli/chat._sessione_da_aprire` decide se aprire una conversazione nuova,
  riprendere l'ultima, o far scegliere una sessione dall'utente.
- La sessione entra in archivio solo dopo il primo turno salvato.

### Come si riprende
- `cli/chat._sessione_da_aprire` usa `state.stores.sessioni_della_cartella`.
- `state.stores.con_run` ricarica la sessione con i run.
- `cli/chat` mostra il banner e la prima domanda, ma non appartiene alla
  logica di ripresa.

### Come cambia sessione
- `cli/commands._comando_sessione` gestisce:
  - sessione corrente,
  - creazione di una nuova sessione,
  - cambio di sessione,
  - verifica del proprietario,
  - ricostruzione dell'agente.
- `state.stores.sessione_di_altri` protegge da sessioni di un altro utente.
- `agent.assistant.build_assistant` è il punto reale dove l'agente viene
  ricostruito.

### Come cambia modalità
- `cli/commands._comando_modo` ricostruisce l'agente sulla stessa sessione.
- La modalità è fissata alla costruzione dello spazio di lavoro e al prompt.
- Il cambio di modalità non tocca la sessione, ma ricostruisce l'agente.

### Cosa dobbiamo tenere in mente
- Il ciclo di vita della sessione è speso fra `cli/chat.py`,
  `cli/commands.py`, `cli/cartella.py`, `state/stores.py` e
  `agent/assistant.py`.
- Il primo obiettivo del refactor è un servizio unico che decida:
  - nuova sessione,
  - apertura,
  - ripresa,
  - cambio di sessione,
  - cambio di modalità.

## 2. Lock e concorrenza

### Lock attuali
- `state/lock.lock_turno` è un lock esclusivo per utente, che copre
  l'intero turno, dall'istantanea degli apprendimenti fino al ripristino.
- `state/lock.lock_stato` è un lock condiviso o esclusivo sullo stato.
- La chat tiene il lock condiviso dello stato; backup e restore chiedono
  quello esclusivo.
- Il lock del turno è per utente e non per processo, con nome file derivato
  dall'id canonico dell'utente.

### Evidenza nel codice
- `cli/chat.esegui_turno` acquisisce `lock_turno`.
- `cli/chat._esegui_turno_protetto` fotografa lo stato, esegue il turno,
  mostra le differenze e chiede conferma degli apprendimenti.
- `cli/chat._conferma_apprendimenti` ripristina profilo e memorie se l'utente
  rifiuta.
- `backup/snapshots.py` e `backup/restore.py` usano `lock_stato`.

### Cosa dobbiamo tenere in mente
- Il lock del turno è già ben pensato, ma il suo uso resta nella CLI.
- La durata del lock è legata alla sequenza:
  fotografia → turno → differenza → conferma → ripristino.
- Il nucleo deve diventare il proprietario di questa durata, non la CLI.

## 3. Confine con `state/`

### Cosa resta in `state/`
- `state/identita.py` è il punto unico di normalizzazione dell'utente.
- `state/stores.py` è il punto unico di lettura di entità, intuizioni e
  sessioni.
- `state/archivi.py` apre i database e costruisce il deposito dei payload.
- `state/lock.py` e `state/platform_files.py` sono primitive di concorrenza
  e filesystem.
- `state/git.py` legge il ramo dai file, senza eseguire git.

### Cosa non va ancora tolta da qui
- `state/` non deve diventare un servizio di orchestrazione.
- Le letture devono restare in sola lettura, senza stampare e senza decidere
  la politica.
- La logica di apertura e cambio sessione deve salire in un modulo core.

## 4. Copertura di test

### Cosa è già coperto
- Sessioni ordinate per `updated_at`.
- Filtraggio per nome e per cartella.
- Marcatura della sessione corrente.
- Nota sull'assenza della sessione in archivio.
- Controllo del proprietario della sessione.
- Lock condiviso/esclusivo dello stato.
- Lock del turno per utente.
- Backup fermato quando la chat tiene il lock.

### Cosa manca
- Test sul servizio di sessione come unità indipendente.
- Test del ciclo di vita della sessione senza terminale.
- Test sul cambio di modalità senza passare dalla CLI.
- Test sull'apertura di una sessione di altro utente tramite il servizio.

## 5. Impatto su `assistant.py` e `commands.py`

### `agent/assistant.py`
- È la facciata di composizione dell'assistente.
- `build_assistant` è il punto dove nasce l'agente.
- Deve restare un modulo di composizione, ma non più un punto di coordinamento
  con la CLI.

### `cli/commands.py`
- Contiene la logica di `/sessione`, `/sessioni` e `/modo`.
- Deve diventare solo un client del servizio di sessione.
- Le regole di apertura, ripresa e cambio modalità devono salire nel nucleo.

### `cli/chat.py`
- Attualmente coordina:
  - sessione,
  - lock,
  - apprendimento,
  - conferma,
  - backup.
- È il punto principale da alleggerire.

## Direzione concreta
Creare un modulo `ares/core/` con un servizio di sessione che incapsuli:
- generazione dell'id,
- verifica del proprietario,
- apertura e ripresa,
- cambio di sessione e modalità,
- ricostruzione dell'agente,
- coordinamento del lock del turno.

La CLI resta responsabile solo di:
- input,
- output,
- conferme,
- presentazione dell'elenco delle sessioni,
- gestione dell'interfaccia interattiva.
