# Security policy

## Versioni supportate

Le correzioni di sicurezza vengono applicate alla branch `main` e alla linea
di rilascio corrente.

| Versione | Supportata |
| --- | --- |
| 0.4.x | Sì |
| < 0.4 | No |

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

La memoria durevole sta fuori dal ciclo di conferma. Profilo e memorie
vengono scritti sia dagli strumenti che il modello chiama sia dall'estrazione
automatica dopo ogni risposta, e ciò che entra viene reiniettato in ogni
sessione futura: un file del workspace o l'output di un comando che
contenga un'istruzione può quindi lasciare una traccia che dura oltre il
turno. Agno 3.0.5 non offre una modalità che imponga una conferma su questi
due store — `PROPOSE` vale solo per le intuizioni, `HITL` per nessuno — e
la mitigazione attuale è la **visibilità**: con `MOSTRA_APPRENDIMENTI`
acceso, sotto ogni risposta compare per intero ciò che è cambiato in profilo
e memorie, e gli strumenti di memoria mostrano i propri argomenti. È una
verifica a posteriori, che funziona se qualcuno la legge; il rimedio, quando
una riga non convince, è chiedere ad Ares di correggerla o cancellarla.

Su POSIX stato, cronologia e snapshot nascono privati (0700 sulle directory,
0600 sui file); `setup.sh` applica 0600 anche a `.env`, quando esiste. Su
Windows vale la DACL ereditata. Restano comunque leggibili da chiunque abbia
accesso all’account che esegue Ares: i permessi separano gli utenti della
macchina, non proteggono da chi è già dentro l’account. Per scenari multiutente
servono isolamento e cifratura gestiti dal sistema operativo.

I modelli Ollama sono artefatti esterni al repository: provenienza, licenza e
limiti del modello scelto devono essere valutati separatamente. Se
`ARES_MAIN_MODEL` indica un modello cloud di Ollama — che non è il valore
distribuito — prompt e risposte della conversazione attraversano
`ollama.com` sotto la sua privacy policy; le memorie estratte e gli embedding
non lo fanno mai.
