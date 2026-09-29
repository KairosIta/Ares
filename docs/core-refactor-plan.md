# Piano di refactor del core applicativo

Data: 2026-09-28, aggiornato il 2026-09-29.
**Stato: implementati i primi due passaggi (sessione e turno).**

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

## Secondo passaggio: il turno (fatto)

`ares/core/turn.py` esegue un turno completo sotto il lock del turno
dell'utente: fotografia di profilo e memorie, turno, variazioni, conferma
degli apprendimenti e ripristino. Prima la sequenza stava in `cli/chat.py`.

Il client offre il protocollo `ClienteTurno`:

| Metodo | Cosa fa il client |
| --- | --- |
| `flusso()` | apre la presentazione e restituisce chi riceve gli eventi |
| `risolvi_pausa(output)` | conferma o rifiuta gli strumenti in pausa |
| `pausa_irrisolta()`, `interrotto()`, `guasto(errore)` | avvisa |
| `apprendimenti(righe, chiedi=...)` | mostra cosa è entrato in memoria e, se richiesto, chiede se tenerlo |

Il risultato è un `EsitoTurno` (risposta, righe apprese, esito del
ripristino). La CLI implementa il protocollo con `ClienteCli`; la prova
`nucleo` con un client che non stampa.

## Passi successivi

1. **Autorizzazioni degli strumenti senza terminale.** `cli/render.py`
   chiama `confirm/reject` sui requirement Agno e la CLI consulta `isatty()`
   per decidere se le modalità silenziose sono ammesse. Un client desktop
   deve poter dire la sua presenza senza passare da un terminale.
2. **Lock dello stato.** Dura quanto la chat e lo prende ancora
   `cli/chat.py`; va offerto dal servizio insieme all'apertura.
3. **Riferimenti di sessione.** `SessioneRiferimento` ed `elenco` per gli
   elenchi, così la CLI smette di leggere le sessioni di Agno.

## Cosa non si tocca

Il formato degli archivi, la logica di apprendimento, la retention delle
sessioni e la struttura dei lock.
