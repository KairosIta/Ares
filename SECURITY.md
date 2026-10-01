# Security policy

## Versioni supportate

Le correzioni di sicurezza vengono applicate alla branch `main` e alla linea
di rilascio corrente.

| Versione | Supportata |
| --- | --- |
| 0.11.x | Sì |
| < 0.11 | No |

## Segnalare una vulnerabilità

Non pubblicare dettagli sensibili in una issue. Usa il **private vulnerability
reporting** nella scheda *Security* del repository GitHub. Se l’opzione non è
disponibile, apri soltanto una issue minima chiedendo un canale privato, senza
inserire riproduzioni, dati personali o segreti.

Una buona segnalazione include:

- componente e revisione interessati;
- impatto osservato;
- passaggi minimi per riprodurre il problema su dati sintetici;
- eventuale proposta di correzione;
- conferma che non sono stati consultati o conservati dati altrui.

## Modello di sicurezza

Nell'uso ordinario inferenza e stato restano sul computer locale, e la
telemetria Agno è disabilitata. I dati persistenti vivono in directory
escluse da Git; i backup vengono verificati prima del restore.

**Dipendenze.** `uv.lock` blocca versione e artefatto, con gli hash SHA-256
di ogni file; setup e CI installano con `uv sync --locked`, che li verifica.
Un pacchetto diverso o una dipendenza senza hash vengono rifiutati, quindi
una versione ripubblicata su PyPI non passa inosservata. Il workflow `Audit`
confronta le dipendenze bloccate (gruppo di sviluppo compreso) con gli
advisory noti: quando cambiano `uv.lock`, `pyproject.toml` o il workflow, e
una volta la settimana. Come CodeQL resta fuori dai controlli obbligatori:
ciò che trova si legge quando compare e si corregge con un `uv lock`.

Sono particolarmente rilevanti vulnerabilità che permettono:

- accesso fuori dal workspace configurato;
- aggiramento delle conferme per operazioni sensibili;
- perdita o contaminazione dei namespace fra utenti;
- esfiltrazione inattesa di prompt, memorie o file;
- restore di snapshot corrotti o incompatibili;
- scritture concorrenti non protette dal lock di stato.

## Limiti dichiarati

### Ares non è una sandbox

Un comando shell autorizzato opera con i permessi dell’utente che ha avviato
il processo e può accedere alla rete. Modello, prompt e conferme riducono il
rischio operativo ma non sono un confine di sicurezza.

Il confine della cartella vale per gli strumenti sui file — leggere, scrivere,
modificare, spostare, cancellare — non per i comandi. Un comando parte nella
cartella di lavoro, ma poi può:

- leggere e scrivere qualunque file che l’utente può toccare, compresi quelli
  che gli strumenti sui file escludono (`.env`, `.git`, le credenziali) e lo
  stato di Ares in `~/.ares`, memoria compresa;
- leggere le variabili d’ambiente della shell da cui è partito Ares (il
  `.env` di Ares no: non entra nell’ambiente);
- usare la rete;
- avviare processi che restano attivi dopo il limite di tempo del comando e
  dopo la chiusura di Ares.

Per questo in `manuale` e in `modifiche` ogni comando chiede conferma, e la
conferma mostra il comando per intero. In `auto` quella conferma non c’è: un
testo letto in un file o nell’output di un comando può far eseguire
qualunque cosa, con i tuoi permessi, senza che tu lo veda prima. Usa `auto`
solo su un progetto di cui ti fidi, o dentro un container o una macchina
virtuale.

### Conferme e modalità

Tutto ciò che il modello legge — un file del progetto, l'output di un
comando, lo stesso `ARES.md` — può contenere un'istruzione. Per questo le
modalità decidono cosa chiede conferma:

| Modalità | Senza domanda | Con conferma |
| --- | --- | --- |
| `manuale` *(predefinita)* | lettura | scrivere, modificare, spostare, cancellare, eseguire |
| `modifiche` | lettura, scrittura, modifica | spostare, cancellare, eseguire |
| `auto` | tutto | niente |
| `piano` | solo strumenti di lettura | — |

La conferma mostra per intero cosa sta per succedere. `ARES.md` entra nel
prompt come regole del progetto delimitate, non come ordini; se è un link
fuori dalla cartella vale come assente, e il confine del workspace non si
aggira con un link.

In `ares -p`, e comunque quando stdin non è un terminale, nessuno può
rispondere: `auto` e `modifiche` sono rifiutate, le conferme valgono no e
gli store di apprendimento non vengono scritti, né automaticamente né con
gli strumenti. Cronologia e quaderno privato restano persistenti.

### La memoria si scrive prima della conferma

Profilo e memorie vengono scritti dagli strumenti del modello e
dall'estrazione automatica dopo ogni risposta, e ciò che entra viene
reiniettato in ogni sessione futura: un'istruzione in un file o nell'output
di un comando può lasciare una traccia oltre il turno. Agno 3.0.11 non
offre una conferma su questi due store (`PROPOSE` vale solo per le
intuizioni, `HITL` per nessuno), quindi Ares la costruisce **a valle**: con
`MOSTRA_APPRENDIMENTI` e `CONFERMA_APPRENDIMENTI` accesi, sotto ogni
risposta compare ciò che è cambiato e la CLI chiede se tenerlo. Un `n`
riscrive i due store com'erano prima del turno e verifica il ripristino
rileggendoli. È tutto o niente per turno, e Invio tiene; per correggere una
singola riga si chiede ad Ares di usare gli strumenti di memoria.

### Concorrenza e proprietà delle sessioni

Le chat dello stesso utente serializzano i turni con un lock esclusivo per
utente, dall'istantanea iniziale fino alla conferma e all'eventuale
ripristino. Una seconda chat deve riprovare se un turno è in corso; in pipe
il comando termina con codice 3. Utenti diversi lavorano in parallelo. Una
sessione appartiene all'utente che l'ha creata: un altro utente non può
aprirla, nemmeno con `--session` o `/sessione`. Anche un'interruzione o un
errore fuori dal generatore passa dall'eco e dalla conferma.

Il lock è cooperativo: non protegge da programmi che scrivono direttamente
negli archivi. Un arresto forzato del processo può lasciare scritture non
ancora mostrate o confermate.

### Permessi su disco

Su POSIX stato, cronologia e snapshot nascono privati (0700 sulle directory,
0600 sui file); `setup.sh` applica 0600 anche a `.env`, quando esiste. Su
Windows vale la DACL ereditata. I permessi separano gli utenti della
macchina, ma non proteggono da chi ha già accesso all’account che esegue
Ares: per scenari multiutente servono isolamento e cifratura del sistema
operativo.

### Modelli e cloud

I modelli Ollama sono artefatti esterni: provenienza, licenza e limiti vanno
valutati separatamente. Con un modello cloud in `ARES_MAIN_MODEL` (non è il
valore distribuito) attraversa `ollama.com`, sotto la sua privacy policy,
tutto ciò che il modello conversazionale riceve e produce: domande e
risposte, il system prompt con profilo, memorie ed `ARES.md`, i file che
legge, l'output dei comandi, le conversazioni passate che rilegge. Con
`ARES_LEARNING_MODEL` escono anche il testo dei turni e le memorie già
salvate, a ogni estrazione. Gli embedding non escono mai. Il prompt dice al
modello quale ruolo è in cloud, così non promette una privacy che non c'è.
