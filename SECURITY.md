# Security policy

## Versioni supportate

Le correzioni di sicurezza vengono applicate alla branch `main` e alla linea
di rilascio corrente.

| Versione | Supportata |
| --- | --- |
| 0.7.x | Sì |
| < 0.7 | No |

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

Ares mantiene inferenza e stato sul computer locale nell’uso ordinario e
disabilita la telemetria Agno. I dati persistenti vivono in directory escluse
da Git e i backup vengono verificati prima del restore.

Le dipendenze sono bloccate a versione e ad artefatto: `uv.lock` porta gli
hash SHA-256 di ogni file, e setup e CI installano con `uv sync --locked`,
che li verifica. L'installazione rifiuta così sia un
pacchetto che non corrisponde sia una futura dipendenza priva di hash. Un pin
dice quale versione installare, un hash dice quale file: senza, la
ripubblicazione di una versione già esistente su PyPI passerebbe inosservata.

Versione bloccata e artefatto verificato non dicono però se quella versione
*ha un avviso pubblicato*: è una domanda sul mondo, non sul lock. Il workflow
`Audit` la fa a ogni push e pull request e una volta la settimana, esportando
dall'`uv.lock` l'elenco esatto delle dipendenze — gruppo di sviluppo compreso
— e confrontandolo con gli advisory noti. Come CodeQL resta fuori dai
controlli obbligatori: ciò che trova va letto quando compare, e si corregge
con un `uv lock` deciso leggendo l'avviso.

Sono particolarmente rilevanti vulnerabilità che permettono:

- accesso fuori dal workspace configurato;
- aggiramento delle conferme per operazioni sensibili;
- perdita o contaminazione dei namespace fra utenti;
- esfiltrazione inattesa di prompt, memorie o file;
- restore di snapshot corrotti o incompatibili;
- scritture concorrenti non protette dal lock di stato.

## Limiti dichiarati

Ares non è una sandbox. Un comando shell autorizzato opera con i permessi
dell’utente che ha avviato il processo e può accedere alla rete. Il modello,
i prompt e le conferme riducono il rischio operativo ma non costituiscono un
confine di sicurezza.

Tutto ciò che il modello legge — un file del progetto, l'output di un
comando, lo stesso `ARES.md` — può contenere un'istruzione. Per questo ogni
strumento che lascia una traccia sul disco (scrivere, modificare, spostare,
cancellare, eseguire) chiede conferma mostrando per intero cosa sta per
fare, e `ARES.md` entra nel prompt come regole del progetto delimitate, non
come ordini. In `ares -p` non c'è nessuno a rispondere: le conferme valgono
no e il turno non scrive in memoria, né da solo né con gli strumenti.

La memoria durevole si scrive prima della conferma, non dopo. Profilo e
memorie vengono scritti sia dagli strumenti che il modello chiama sia
dall'estrazione automatica dopo ogni risposta, e ciò che entra viene
reiniettato in ogni sessione futura: un file del workspace o l'output di un
comando che contenga un'istruzione può quindi lasciare una traccia che dura
oltre il turno. Agno 3.0.9 non offre una modalità che imponga una conferma
su questi due store — `PROPOSE` vale solo per le intuizioni, `HITL` per
nessuno — quindi la conferma è costruita da Ares **a valle**: con
`MOSTRA_APPRENDIMENTI` e `CONFERMA_APPRENDIMENTI` accesi, sotto ogni risposta
compare per intero ciò che è cambiato in profilo e memorie e la CLI chiede
se tenerlo; un `n` riporta i due store a com'erano prima del turno,
riscrivendoli con l'istantanea letta allora, e verifica di esserci riuscito
rileggendoli. È tutto o niente per turno, e funziona se qualcuno legge:
Invio tiene. La correzione fine di una singola riga passa dagli strumenti
di memoria, chiedendo ad Ares di correggerla o cancellarla.

Su POSIX stato, cronologia e snapshot nascono privati (0700 sulle directory,
0600 sui file); `setup.sh` applica 0600 anche a `.env`, quando esiste. Su
Windows vale la DACL ereditata. Restano comunque leggibili da chiunque abbia
accesso all’account che esegue Ares: i permessi separano gli utenti della
macchina, non proteggono da chi è già dentro l’account. Per scenari multiutente
servono isolamento e cifratura gestiti dal sistema operativo.

I modelli Ollama sono artefatti esterni al repository: provenienza, licenza e
limiti del modello scelto devono essere valutati separatamente. Se
`ARES_MAIN_MODEL` indica un modello cloud di Ollama — che non è il valore
distribuito — attraversa `ollama.com`, sotto la sua privacy policy, tutto
ciò che il modello conversazionale riceve e produce: le domande e le
risposte, il system prompt con profilo, memorie ed `ARES.md`, i file del
workspace che legge, l'output dei comandi autorizzati, le conversazioni
passate che rilegge. Se lo indica `ARES_LEARNING_MODEL` escono anche il
testo dei turni e le memorie già salvate, a ogni estrazione. Gli embedding
non escono mai. Il prompt dice al modello quale ruolo è in cloud, così non
rassicura l'utente sulla privacy quando non può.
