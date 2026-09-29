# Piano di refactor del core applicativo

Data: 2026-09-28.
**Stato: piano iniziale, non ancora implementato.**

## Obiettivo
Creare un nucleo applicativo indipendente dall'interfaccia, a partire dal
ciclo di vita della sessione.

## Primo modulo
- `ares/core/session.py`
  - `SessioneRiferimento`
  - `SessioneAttiva`
  - `Sessioni`
  - `elenco`
  - `nuova`
  - `apri`
  - `riprendi`

## Ordine di lavoro
1. Creare il modulo di sessione con le firme del contratto minimo.
2. Portare dentro il servizio:
   - generazione dell'id di sessione;
   - verifica del proprietario;
   - apertura e ripresa;
   - cambio di sessione;
   - cambio di modalità.
3. Far diventare la CLI un client del servizio.
4. Rimuovere dalla CLI la logica di coordinamento della sessione.
5. Aggiungere prove per il servizio e per un client senza terminale.

## Cosa non toccare nel primo passaggio
- il formato degli archivi;
- la logica di apprendimento;
- la politica di conferma;
- la retention delle sessioni;
- la struttura dei lock.

## Risultato atteso
La CLI diventa solo un client del servizio di sessione, senza decidere
come nasce, come si apre o come si cambia una conversazione. Un secondo
client senza terminale deve poter ottenere gli stessi effetti.
