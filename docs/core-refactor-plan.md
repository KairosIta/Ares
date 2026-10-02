# Piano di refactor del core applicativo

Data: 2026-09-28, aggiornato il 2026-09-30.
**Stato: implementati i sei passaggi (sessione, turno, autorizzazioni, stato in uso, riferimenti di sessione, lock della manutenzione).**

## Obiettivo
Un nucleo applicativo indipendente dall'interfaccia, a partire dal ciclo di
vita della sessione. L'analisi di partenza è in
[core-refactor-audit.md](core-refactor-audit.md).

## Primo passaggio: la sessione (fatto)

`ares/core/session.py`, con la CLI come client:

| Decisione | Prima | Ora |
| --- | --- | --- |
| id di una conversazione nuova | `cli/cartella.nuovo_id_sessione` | `Sessioni.id_nuovo` (`core/id_sessione.py`) |
| sessioni della cartella per `resume` | `cli/chat` + `state/stores` | `Sessioni.elenco` (quinto passaggio) |
| proprietario prima di aprire | `cli/chat`, `cli/commands` | `Sessioni.apri` → `SessioneDiAltri` |
| costruzione dell'agente | `cli/chat`, `/sessione`, `/modo` | `Sessioni.apri`, `nuova`, `cambia_modo` |

La prova `nucleo` (`tests/core_test.py`) usa il servizio come un client
senza terminale.

Differenze dalla bozza di API in [responsibility-map.md](responsibility-map.md):

- **Servizio senza stato proprio.** La sessione corrente resta al client, che
  riceve una `SessioneAttiva` (id, modalità, agente) a ogni apertura. La CLI
  costruisce il servizio dalla configurazione della conversazione.
- **Niente `riprendi`.** La scelta fra «ultima» e «scelta dall'elenco» è una
  domanda all'utente, quindi resta alla CLI, che la risolve con `elenco`
  (quinto passaggio).

## Secondo passaggio: il turno (fatto)

`ares/core/turn.py` esegue un turno completo sotto il lock del turno
dell'utente: fotografia di profilo e memorie, turno, variazioni, conferma
degli apprendimenti e ripristino. Prima la sequenza stava in `cli/chat.py`.

Il client offre il protocollo `ClienteTurno`:

| Metodo | Cosa fa il client |
| --- | --- |
| `flusso()` | apre la presentazione e restituisce chi riceve gli eventi |
| `autorizza(richiesta)`, `negata(richiesta)`, `concessa(richiesta)` | vedi il terzo passaggio |
| `pausa_irrisolta()`, `rifiuti_esauriti(quanti)`, `interrotto()`, `guasto(errore)` | avvisa |
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
| le regole della persona sui comandi | — | l'`Arbitro` le legge (`core/regole.py`): `consenti` conferma e avvisa con `concessa`, `nega` rifiuta e avvisa con `negata`, con la regola in `Richiesta.regola` |
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

## Quinto passaggio: i riferimenti di sessione (fatto)

`ares/state/sessioni.py` legge le conversazioni e le restituisce come
`SessioneRiferimento` (id, cartella, ultima modifica, scambi, prima domanda) e
`Conversazione` (gli scambi, per `/esporta`): gli oggetti di sessione di Agno
non escono da lì. Sta in `state/` perché lo usa anche il prompt, e `agent/`
non dipende dal nucleo.

| Lettura | Prima | Ora |
| --- | --- | --- |
| `/sessioni`, TAB su `/sessione` | `leggi_sessioni` sull'agente, con i run di tutte le sessioni | `Sessioni.elenco(ambito="qui" o "tutte")` |
| `ares resume`, `--scegli` | `della_cartella` + `con_scambi`, oggetti Agno | `Sessioni.elenco(ambito="nate_qui")` |
| `/esporta` | `stato.agent.db.get_session` | `Sessioni.conversazione` |
| conversazioni nel prompt | `sessioni_della_cartella` + `con_run` | `elenca(ambito="nate_qui", escludi=...)` |
| `ares inspect` senza `--session` | `leggi_sessioni`, con tutti i run, per un id | `elenca(ambito="tutte", limite=0).nomi` |

Un elenco filtra, conta e taglia sulle sessioni senza run; poi rilegge con i
run solo le voci tagliate, con `get_session`, che unisce anche i run rimasti
nella vecchia colonna di Agno. Su un archivio di prova con messaggi da 2 KB,
`/sessioni` costava 0,11 s con 60 sessioni da 30 scambi e 0,40 s con 200, e
cresceva con la storia; ora legge solo le 20 mostrate, in circa 0,03 s.

Gli ambiti restano due regole diverse, come prima: `qui` (quelle di qui e
quelle senza cartella, per `/sessioni`) e `nate_qui` (per `resume` e il
prompt, che non devono riprendere una sessione vecchia da una cartella
qualunque). La resa in testo è del client: `cli/conversazioni.py` per il
terminale e il Markdown, `agent/prompts.py` per il modello. Il prompt e
l'esportazione sono identici byte per byte a prima.

## Sesto passaggio: il lock della manutenzione (fatto)

`core/stato.py` ha `stato_esclusivo`, accanto a `stato_in_uso`: lock
esclusivo e lo stesso controllo del posto, dentro il lock.

| Operazione | Prima | Ora |
| --- | --- | --- |
| `backup create`, `prune`, `restore` | `lock_stato` in `backup/` | `stato_esclusivo` |
| `sessions`, `entities` con `--apply` | `lock_stato` in `esegui_protetto` | `stato_esclusivo` |
| `sessions status`, `entities audit`, anteprime | `lock_stato` condiviso in `esegui_protetto` | `stato_in_uso` |
| `ares migrate` | due `lock_stato` esclusivi | invariato: è l'operazione che risolve il posto vecchio |

Prima la manutenzione non guardava il posto vecchio. Con lo stato in `tmp/`
del clone e uno snapshot già in `~/.ares`, la chat si fermava ma `backup
restore` installava lo stato nel posto nuovo: l'archivio restava sdoppiato,
un conflitto che `ares migrate` non risolve. `backup create`, `sessions` ed
`entities` dicevano «nessuno stato» o «nessun archivio». Ora si fermano con
l'avviso di `ares migrate` ed escono con 1, come la chat. `sessions`, senza
archivio, controlla il posto senza lock (`verifica_posto`), per non creare
niente dove Ares non c'è ancora.

Restano come prima `acquisisci_lock=False`, con cui snapshot e restore si
chiamano dentro un lock già preso, e `backup list` e `verify`, che non
prendono lock.

## Cosa non si tocca

Il formato degli archivi, la logica di apprendimento, la retention delle
sessioni e la struttura dei lock.
