# Architettura

Ares e' un'applicazione Python local-first. La CLI costruisce un agente Agno
collegato a Ollama, agli store persistenti e a un insieme limitato di
strumenti. Nessun servizio cloud e' necessario durante l'uso ordinario.

## Componenti

- `chat.py` coordina sessioni, comandi, conferme e continuazione dei run;
- `cli_input.py` gestisce editor, completamento e cronologia della REPL;
- `cli_ui.py` rende streaming Markdown, pannelli, tabelle e messaggi;
- `platform_files.py` uniforma lock condivisi/esclusivi fra POSIX e Windows;
- `assistant.py` assembla modello, istruzioni, strumenti e store;
- `learning.py` coordina l'apprendimento sul run completo;
- `stores.py` espone profilo, memorie, contesto, entita' e conoscenza;
- SQLite conserva sessioni e dati strutturati;
- LanceDB conserva la conoscenza vettoriale con embedding serviti da Ollama;
- `backup.py` crea e verifica snapshot locali dello stato;
- `entity_maintenance.py` rileva e fonde entita' duplicate.

## Flusso di un turno

1. La CLI invia il messaggio all'agente.
2. Il modello puo' rispondere o richiedere uno strumento.
3. Le operazioni sensibili sospendono il run in attesa di conferma.
4. `continue_run` completa lo stesso run dopo la decisione dell'utente.
5. La macchina di apprendimento riceve l'output completo e aggiorna gli store.

L'ultimo passaggio e' separato dall'interfaccia: l'apprendimento usa sempre il
run finale, evitando di perdere il contenuto prodotto dopo una conferma.

## Confini di sicurezza

Gli strumenti per i file sono limitati a una directory di lavoro, ma questo
confine non e' una sandbox di processo. I comandi shell possono accedere alle
risorse dell'host e alla rete, quindi richiedono conferma esplicita. Stato,
workspace, backup e `.env` restano fuori dal controllo versione.

## Configurazione

Le impostazioni versionate sono in `config.py`. Identita' e percorsi locali
possono essere sovrascritti con le variabili mostrate in `.env.example`; il
file `.env` del clone non viene pubblicato.
