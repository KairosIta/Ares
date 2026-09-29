# Piano di refactor del core applicativo

Data: 2026-09-28, aggiornato il 2026-09-29.
**Stato: implementati i primi quattro passaggi (sessione, turno, autorizzazioni, stato in uso).**

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
| `autorizza(richiesta)`, `negata(richiesta)` | vedi il terzo passaggio |
| `pausa_irrisolta()`, `interrotto()`, `guasto(errore)` | avvisa |
| `apprendimenti(righe, chiedi=...)` | mostra cosa è entrato in memoria e, se richiesto, chiede se tenerlo |

Il risultato è un `EsitoTurno` (risposta, righe apprese, esito del
ripristino). La CLI implementa il protocollo con `ClienteCli`; la prova
`nucleo` con un client che non stampa.

## Terzo passaggio: le autorizzazioni (fatto)

`ares/core/autorizzazioni.py`. Il client dichiara la sua **presenza**
(`presidiato`: qualcuno legge le richieste e risponde) a `Sessioni` e al
turno; le regole che ne seguono sono del nucleo:

| Regola | Prima | Ora |
| --- | --- | --- |
| senza presenza nessuna modalità scrive in silenzio | `_guardie_di_avvio`, solo all'avvio | `verifica_modo`, all'avvio, in `Sessioni.apri` e in `cambia_modo` |
| `auto` solo all'apertura | `/modo`, per nome | `verifica_modo(..., in_corso=True)` → `ModoNonAmmesso` |
| senza presenza una conferma vale no | il fallback di `CliInput` che risponde vuoto | `risolvi_pausa` rifiuta senza chiedere e avvisa con `negata` |
| `confirm()` / `reject()` sui requirement | `chiedi_conferme` in `cli/render.py` | `risolvi_pausa`; il client risponde con una `Decisione` |
| niente domanda sugli apprendimenti senza presenza | risposta vuota del fallback | `chiedi` è falso |

Il client riceve una `Richiesta` (strumento, argomenti, cartella) e non
vede più gli oggetti Agno. La CLI mostra la richiesta con `righe_richiesta`
e chiede con `chiedi_autorizzazione`; decide la presenza da `isatty()` su
stdin, e `-p` non è mai presidiato.

Chiude un buco: da una pipe `ares --modo modifiche` era rifiutato, ma
`/modo modifiche` nella stessa pipe passava.

Restano alla CLI le conferme scritte di `cli/cartella.py` e
`cli/conferma.py` (cartella rischiosa, restore, prune, fusione): sono
domande di manutenzione, non del turno.

## Quarto passaggio: lo stato in uso (fatto)

`ares/core/stato.py`. Un client apre lo stato con `stato_in_uso(percorsi)`
e lo tiene per tutta la sua vita:

| Condizione | Prima | Ora |
| --- | --- | --- |
| lock condiviso, contro backup, restore e migrazione | `avvia` in `cli/chat.py`, `ares inspect` | `stato_in_uso` → `StatoOccupato` |
| stato ancora nel posto delle versioni vecchie | `_guardie_di_avvio`, con `ops/migrazione.py` | `stato_in_uso` → `StatoDaMigrare`, controllato sotto il lock |
| directory dello stato privata | `cli/chat.py`, dopo la cartella | `Sessioni`, alla costruzione |

Il rilevamento delle parti da spostare (`parti`, `conflitti`) sta in
`state/vecchio_posto.py`: `ops/migrazione.py` importa la CLI e il nucleo non
può dipendere da lei. `ares inspect` apre lo stato come la chat, e con una
migrazione in sospeso lo dice invece di mostrare un archivio vuoto.

La chat rifiuta prima gli argomenti incoerenti (modalità, `--scegli` con
`-p`), senza lock; poi apre lo stato, chiede la cartella e costruisce
`Sessioni`. Aprire lo stato non scrive al suo interno: un avvio rifiutato
non lascia niente dietro di sé.

## Passi successivi

1. **Riferimenti di sessione.** `SessioneRiferimento` ed `elenco` per gli
   elenchi, così la CLI smette di leggere le sessioni di Agno.

## Cosa non si tocca

Il formato degli archivi, la logica di apprendimento, la retention delle
sessioni e la struttura dei lock.
