# Piano di refactor del core applicativo

Data: 2026-09-28, aggiornato il 2026-09-29.
**Stato: primo passaggio implementato (servizio di sessione).**

## Obiettivo
Un nucleo applicativo indipendente dall'interfaccia, a partire dal ciclo di
vita della sessione. L'analisi di partenza è in
[core-refactor-audit.md](core-refactor-audit.md).

## Primo passaggio: la sessione (fatto)

`ares/core/session.py`, con la CLI come client:

| Decisione | Prima | Ora |
| --- | --- | --- |
| id di una conversazione nuova | `cli/cartella.nuovo_id_sessione` | `Sessioni.id_nuovo` (`core/id_sessione.py`) |
| sessioni della cartella per `resume` | `cli/chat` + `state/stores` | `Sessioni.della_cartella`, `con_scambi` |
| proprietario prima di aprire | `cli/chat`, `cli/commands` | `Sessioni.apri` → `SessioneDiAltri` |
| costruzione dell'agente | `cli/chat`, `/sessione`, `/modo` | `Sessioni.apri`, `nuova`, `cambia_modo` |

La prova `nucleo` (`tests/core_test.py`) usa il servizio come un client
senza terminale.

Differenze dalla bozza di API in [responsibility-map.md](responsibility-map.md):

- **Servizio senza stato proprio.** La sessione corrente resta al client, che
  riceve una `SessioneAttiva` (id, modalità, agente) a ogni apertura. La CLI
  costruisce il servizio dalla configurazione della conversazione.
- **Niente `SessioneRiferimento`, `elenco` e `riprendi`, per ora.** Elenchi e
  rendering (`/sessioni`, `ares resume --scegli`, le istruzioni al modello)
  lavorano ancora sulle sessioni di Agno tramite `state/stores.py`: un
  riferimento proprio va introdotto quando un secondo client ne avrà bisogno,
  insieme a un `elenco` che lo restituisca. La scelta fra «ultima» e «scelta
  dall'elenco» è una domanda all'utente, quindi resta alla CLI.

## Passi successivi

1. **Turno, lock e conferma degli apprendimenti.** Oggi `cli/chat` tiene il
   lock del turno per l'intera sequenza fotografia → turno → differenza →
   conferma → ripristino. Va spostata in un servizio `Turni` che riceva una
   funzione per chiedere la conferma: senza, un client senza terminale non
   ottiene gli stessi effetti della CLI.
2. **Riferimenti di sessione.** `SessioneRiferimento` ed `elenco` per gli
   elenchi, così la CLI smette di leggere le sessioni di Agno.

## Cosa non si tocca

Il formato degli archivi, la logica di apprendimento, la retention delle
sessioni e la struttura dei lock.
