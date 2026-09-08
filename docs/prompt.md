# I prompt di Ares

Il modello conversazionale e il modello che estrae gli apprendimenti
ricevono istruzioni diverse. Cambiare soltanto la voce dell'assistente non
cambia i criteri con cui l'estrattore aggiorna gli archivi.

## Conversazione

`agent/prompts.py` compone ambiente, collaborazione, strumenti e memoria;
`agent/assistant.py` li collega alle capacità abilitate. Agno aggiunge le
guide degli store, le memorie disponibili e le informazioni del turno. Per
ispezionare il risultato completo senza interrogare il modello:

```bash
ares inspect --prompt
ares inspect --prompt --modo piano
```

La scheda distingue il contesto richiesto a Ollama dal limite effettivo del
modello o servizio. Le regole di collaborazione chiedono di consultare le
fonti pertinenti, chiarire le ambiguità sostanziali e verificare l'esito di
un'azione prima di dichiararla completata. L'italiano è la lingua
predefinita; traduzioni e testi richiesti in altre lingue restano possibili.

Le modalità regolano gli strumenti del workspace. `piano` non espone
scritture o comandi sul workspace, ma lascia attive memoria e quaderno.
Gli strumenti sui file rispettano la radice; l'esecuzione di comandi non è
una sandbox. Le conferme operative sono raccolte dall'interfaccia quando
lo strumento sospende il turno, senza una domanda preliminare duplicata.

Con `ares -p` non vengono eseguiti gli aggiornamenti automatici e non vengono
consegnati gli strumenti degli store di apprendimento. Il prompt omette le
relative guide, compresa quella delle intuizioni che Agno altrimenti
reintroduce in inglese. Le memorie già caricate restano consultabili nel
contesto. Cronologia e quaderno sono persistenti: al modello viene chiesto
di scrivere nel quaderno solo su richiesta esplicita, senza usarlo come
alternativa all'apprendimento disattivato. Questa indicazione è una regola
del prompt; non rimuove gli strumenti del quaderno.

Con `SEARCH_PAST_SESSIONS=False` non vengono caricate né suggerite le
conversazioni precedenti della cartella: il prompt non prescrive
`read_past_session` quando lo strumento non è disponibile.

## Estrazione

`CRITERI_ESTRAZIONE` in `agent/learning.py` viene passato a profilo, memorie
e contesto di sessione. Specifica che:

- esempi, ipotesi, citazioni e giochi di ruolo non sono fatti personali;
- una proposta dell'assistente richiede accettazione per diventare una
  decisione dell'utente;
- richieste temporanee e preferenze durevoli vanno distinte;
- qualifiche e incertezza espresse vanno conservate;
- una correzione esplicita sostituisce il fatto superato e conserva gli
  altri fatti validi;
- valutazione, decisione, programma futuro, avvio e completamento sono
  distinti: «ho deciso di realizzarlo» non diventa «lo sto realizzando»;
- un avvio non confermato resta sconosciuto, senza dedurre neppure che il
  lavoro non sia iniziato; il trascorrere del tempo non prova l'esecuzione;
- ribadire una decisione o un obiettivo non annulla un avvio già confermato,
  salvo una rettifica esplicita;
- data di acquisizione e data dell'evento non coincidono necessariamente.

Il campo `current_focus` del profilo descrive progetti e obiettivi
conservandone lo stato dichiarato. La sua descrizione entra nelle istruzioni
di estrazione; il tipo rimane una stringa. Anche il prompt conversazionale
richiede di mantenere queste distinzioni quando usa o aggiorna i ricordi.

Ogni store aggiunge il proprio scopo. Il contesto di sessione distingue
anche azioni tentate, fallite e completate. Entità e intuizioni sono scritte
su scelta del modello conversazionale: le sue guide chiedono di conservare
fonti e condizioni pertinenti, senza spacciare una proposta per una
decisione o un'idea per una procedura verificata.
La guida delle intuizioni richiede titolo, contenuto e contesto in italiano
anche quando la risposta richiesta è in un'altra lingua, conservando nomi
tecnici e identificativi.

## Verifica e limiti

Lo smoke verifica il prompt completo in 19 combinazioni di modalità,
interattività e flag, confrontando i nomi prescritti con gli strumenti
consegnati. Ogni caso ha una conversazione precedente nella cartella e
controlla se il relativo blocco entra o meno nel prompt. Controlla inoltre che `-p` non prometta estrazione automatica e
non reintroduca la guida inglese di Agno. Il caso `auto` non interattivo è
verificato sulla fabbrica dell'agente; la CLI continua a rifiutarlo.

Queste prove controllano la composizione. La qualità semantica richiede
modelli reali e casi ripetuti; le prove con Ollama verificano il ciclo di
apprendimento e il riuso delle intuizioni, senza dimostrare che ogni memoria
sia corretta. Il test delle intuizioni chiede il salvataggio in una
conversazione inglese senza suggerire la lingua dell'archivio e verifica
indicatori italiani nel contenuto salvato; è un controllo mirato, non un
classificatore linguistico generale. Per confrontare revisioni del prompt usare dati sintetici e
lo stesso modello, includendo almeno questi casi:

| Caso | Esito da controllare |
| --- | --- |
| «Potrei trasferirmi a Milano» | Nessuna residenza a Milano presentata come certa |
| Un dialogo inventato con dati personali | Nessun dato del personaggio attribuito all'utente |
| Ares propone backup quotidiani, senza accettazione | Nessuna decisione già presa sui backup |
| Una preferenza stabile viene corretta | La versione precedente non resta attuale |
| «Solo per questa risposta, tre parole» | Nessuna nuova preferenza generale di brevità |
| «Ho deciso di realizzare ORIONE-42» | Decisione conservata; avvio sconosciuto |
| «Comincerò domani» | Programma futuro, senza avvio dato per avvenuto |
| «Ho iniziato», seguito da una decisione ribadita | Avvio confermato ancora recuperabile |

La formulazione del prompt orienta il modello. Autorizzazioni, persistenza
e disponibilità degli strumenti restano responsabilità del codice.
Le nuove regole non riscrivono automaticamente i ricordi esistenti:
correggere un fatto già salvato richiede una fonte o una rettifica che lo
giustifichi. Il [benchmark della memoria](memory-quality.md) conserva il
confronto su archivi sintetici nuovi prima e dopo la modifica.
