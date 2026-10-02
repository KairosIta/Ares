# Security policy

## Versioni supportate

Le correzioni di sicurezza vengono applicate alla branch `main` e alla linea
di rilascio corrente.

| Versione | Supportata |
| --- | --- |
| 0.12.x | Sì |
| < 0.12 | No |

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

### Senza sandbox, di serie

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
qualunque cosa, con i tuoi permessi, senza che tu lo veda prima. Senza
sandbox, usa `auto` solo su un progetto di cui ti fidi, o dentro un
container o una macchina virtuale.

### La sandbox opzionale, su Linux

Con `ARES_SANDBOX=bwrap` nel `.env` ogni comando parte dentro
[bubblewrap](https://github.com/containers/bubblewrap):

- il filesystem è in sola lettura, tranne la cartella di lavoro e una `/tmp`
  privata che sparisce con il comando;
- lo stato di Ares (`~/.ares` o `ARES_HOME`, con stato e backup), il `.env`
  del clone, la directory di runtime della sessione (agente SSH, D-Bus) e un
  elenco di credenziali note della home (`.ssh`, `.gnupg`, `.aws`, `.azure`,
  `.kube`, `.docker`, `.config/gh`, `.config/gcloud`, `.netrc`,
  `.git-credentials`, `.pypirc`, `.npmrc`) sono coperti: il comando non li
  vede;
- la rete non c’è, salvo `ARES_SANDBOX_RETE=1`;
- i processi lasciati in background muoiono quando il comando finisce o
  scade.

Se la sandbox è chiesta ma non si può applicare — un sistema diverso da
Linux, `bwrap` assente, namespace utente negati — la chat non parte e lo
dice in una riga, e `ares preflight` dà l’ambiente come non pronto: Ares non
ripiega in silenzio sui comandi senza sandbox. Su Ubuntu 24.04 i namespace
utente richiedono un profilo AppArmor per `bwrap`.

I suoi limiti:

- dentro la cartella di lavoro un comando fa ciò che vuole: cancella,
  riscrive, legge `.env` e `.git` del progetto. La sandbox protegge il resto
  della macchina, non il progetto, che resta affidato alle conferme e a git;
- legge tutto ciò che sul disco è leggibile e non è nell’elenco: un segreto
  in un percorso diverso resta visibile;
- con `ARES_SANDBOX_RETE=1` la rete è intera, senza filtro per dominio: un
  comando può mandare fuori ciò che legge;
- conferme e modalità non cambiano: `auto` diventa più difendibile, non
  innocuo;
- su Windows e macOS l’opzione non esiste.

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
aggira con un link. Anche ciò che gli strumenti leggono — un file, l'output
di un comando, una ricerca, un risultato riletto, una conversazione passata
— arriva al modello delimitato, con la fonte dichiarata e l'avviso che sono
dati; le righe che imitano il delimitatore vengono citate. È una difesa sul
contenuto, non un filtro: il confine resta la conferma.

Le regole di autorizzazione (`.ares/permessi.toml` nella cartella,
`permessi.toml` in `~/.ares`) tacciono la conferma di un comando per
prefisso o lo negano sempre, anche in `auto`. Le legge il nucleo, non il
modello; `nega` vince; un comando che non si sa spezzare chiede. Sono un
prefisso sulle parole: `git -c core.pager=x status` non è coperto da
`git status`, e un alias o uno script nella cartella con il nome di un
comando consentito passano. Il file del progetto arriva con il clone, come
`ARES.md`: il banner dice quante regole ha letto e da quali file.

Le skill (`.ares/skills` nella cartella, `~/.ares/skills` per la persona)
sono procedure che il modello legge e segue. Ares non ne esegue gli script
e ignora `allowed-tools`: un comando suggerito da una skill passa da
`run_command` con le conferme della modalità. Quelle del progetto arrivano
con il clone, come `ARES.md`: nome e descrizione entrano nel prompt come
regole della cartella, senza parentesi angolari che aprano o chiudano una
sezione, al più 20 skill e 4000 caratteri di descrizioni; la procedura
arriva con la stessa avvertenza fra due righe che la delimitano, e gli altri
file della skill come dati. A parità di nome vince la skill della persona.
Una skill del progetto che, risolti i link, esce dalla cartella di lavoro
non si carica. Il banner le elenca tutte, e `ares skills list` dice perché
una non è caricata. La lettura resta nella cartella della skill: un percorso
o un link che ne esce non si apre. Una skill proposta da Ares va in
`~/.ares/skills/proposte/`, che non si carica: entra solo con
`ares skills adopt --apply` e la conferma scritta, e solo se dopo
l'anteprima non è cambiata. Da `ares -p` non si propone.

In `ares -p`, e comunque quando stdin non è un terminale, nessuno può
rispondere: `auto` e `modifiche` sono rifiutate, le conferme valgono no, le
regole non concedono niente e gli store di apprendimento non vengono
scritti, né automaticamente né con gli strumenti. Cronologia e quaderno
privato restano persistenti.

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
