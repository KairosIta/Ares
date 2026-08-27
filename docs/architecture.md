# Architettura

Ares e' un'applicazione Python local-first. La CLI costruisce un agente Agno
collegato a Ollama, agli store persistenti e a un insieme limitato di
strumenti. Nessun servizio cloud e' necessario durante l'uso ordinario.

## Componenti

- `chat.py` e' il client CLI: comandi, rendering e richieste di conferma;
- `turn_core.py` normalizza gli eventi Agno e coordina `run/continue_run`
  senza dipendere dall'interfaccia;
- `cli_input.py` gestisce editor, completamento e cronologia della REPL;
- `cli_ui.py` rende streaming Markdown, pannelli, tabelle e messaggi;
- `platform_files.py` uniforma lock condivisi/esclusivi fra POSIX e Windows;
- `setup.sh` e `setup.ps1` ricostruiscono lo stesso ambiente bloccato sui due
  sistemi verificati;
- `assistant.py` assembla modello, istruzioni, strumenti e store;
- `learning.py` coordina l'apprendimento sul run completo;
- `stores.py` espone profilo, memorie, contesto, entita' e conoscenza;
- SQLite conserva sessioni e dati strutturati;
- LanceDB conserva la conoscenza vettoriale con embedding serviti da Ollama;
- `backup.py` crea e verifica snapshot locali dello stato;
- `entity_maintenance.py` rileva e fonde entita' duplicate.

## Flusso di un turno

1. Il client consegna il messaggio al core del turno.
2. Il core avvia Agno e pubblica eventi indipendenti dall'interfaccia.
3. Il modello puo' rispondere o richiedere uno strumento.
4. Le operazioni sensibili sospendono il run in attesa di conferma del client.
5. Il core esegue `continue_run` sullo stesso run dopo la decisione.
6. La macchina di apprendimento riceve l'output completo e aggiorna gli store.

L'ultimo passaggio e' separato dall'interfaccia: l'apprendimento usa sempre il
run finale, evitando di perdere il contenuto prodotto dopo una conferma.

## Confini di sicurezza

Gli strumenti per i file sono limitati a una directory di lavoro, ma questo
confine non e' una sandbox di processo. I comandi shell possono accedere alle
risorse dell'host e alla rete, quindi richiedono conferma esplicita. Stato,
workspace, backup e `.env` restano fuori dal controllo versione.

Su POSIX gli snapshot vengono pubblicati con una rinomina di directory. Su
Windows, dove LanceDB può impedire quella rinomina anche dopo la chiusura dei
reader nativi, il manifest viene pubblicato per ultimo come commit marker e
il restore conserva stabile la directory radice con una copia di rollback.

## Configurazione

Le impostazioni versionate sono in `config.py`. Identita' e percorsi locali
possono essere sovrascritti con le variabili mostrate in `.env.example`; il
file `.env` del clone non viene pubblicato.
