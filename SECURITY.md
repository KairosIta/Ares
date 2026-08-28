# Security policy

## Versioni supportate

Le correzioni di sicurezza vengono applicate alla branch `main` e alla linea
di rilascio corrente.

| Versione | Supportata |
| --- | --- |
| 0.2.x | Sì |
| 0.1.x | No |
| < 0.1 | No |

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

Le dipendenze sono bloccate a versione e ad artefatto: `requirements.txt` e
`requirements-dev.txt` portano gli hash SHA-256 di ogni file, e
`uv pip sync` rifiuta un pacchetto che non corrisponda. Un pin dice quale
versione installare, un hash dice quale file: senza, la ripubblicazione di una
versione già esistente su PyPI passerebbe inosservata.

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

Su POSIX stato, cronologia e snapshot nascono privati (0700 sulle directory,
0600 sui file); su Windows vale la DACL ereditata. Restano comunque leggibili
da chiunque abbia accesso all’account che esegue Ares: i permessi separano gli
utenti della macchina, non proteggono da chi è già dentro l’account. Per
scenari multiutente servono isolamento e cifratura gestiti dal sistema
operativo.

I modelli Ollama sono artefatti esterni al repository: provenienza, licenza e
limiti del modello scelto devono essere valutati separatamente.
