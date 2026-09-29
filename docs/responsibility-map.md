# Mappa delle responsabilità di Ares

Data: 2026-09-28.
**Stato: fotografia del codice attuale.** Non è una proposta di rifattorizzazione
completata, ma un punto di partenza per decidere cosa spostare nel nucleo
applicativo.

## Fotografia attuale

| Area | Righe | File | Responsabilità attuale |
| --- | ---: | ---: | --- |
| **CLI** | 3538 | 11 | Interfaccia utente, REPL, comandi, rendering, input, conferme. Qui vive anche parte del coordinamento di lock, sessioni, backup, migrazione e ripristino degli apprendimenti. |
| **Agent** | 1871 | 8 | Core dell’assistente: runtime, ciclo del turno, apprendimento, prompt, schemi. |
| **Backup** | 1308 | 8 | Snapshot, restore, verifica, integrità, rollback. |
| **Entities** | 1260 | 6 | Audit e manutenzione delle entità, fusione e piani. |
| **State** | 792 | 7 | Accesso agli archivi, identità, lock, primitive di piattaforma. |
| **Ops** | 561 | 4 | Preflight, ispezione dello stato, migrazione. |
| **Sessions** | 532 | 4 | Retention e manutenzione delle sessioni. |
| **Config** | 1071 | 1 | Configurazione centrale e percorsi dello stato. |
| **Root files vari** | 33 | 6 | `__main__`, `__init__` e simili. |

Il totale del package `ares/` è **10.966 righe**.

## Lettura rapida

- Lo stato è già ben separato: `state/` è il layer più stabile.
- Il turno è già parzialmente indipendente dall’interfaccia in `agent/turn_core.py`.
- Le aree di manutenzione (`backup/`, `entities/`, `ops/`, `sessions/`) hanno
  responsabilità leggibili.
- La CLI è il punto più critico di mescolanza: non è solo presentazione, ma
  coordina anche politica, stato, apprendimento e manutenzione.

## Mappa per responsabilità

| Responsabilità | Dove sta oggi | Dove dovrebbe stare |
| --- | --- | --- |
| Interfaccia utente | `cli/` | `cli/` |
| Ciclo del turno | `agent/turn_core.py` | nucleo applicativo |
| Politica di conferma | `cli/render.py` + `cli/chat.py` | nucleo applicativo |
| Apprendimento | `agent/learning.py` | nucleo applicativo |
| Fotografia e ripristino memoria | `agent/echo.py` + `cli/chat.py` | nucleo applicativo |
| Lock | `state/lock.py` | nucleo / servizio |
| Sessioni | `cli/commands.py`, `cli/cartella.py`, `state/stores.py` | nucleo applicativo |
| Backup | `backup/` | infrastruttura |
| Entità | `entities/` | manutenzione |
| Retention sessioni | `sessions/` | manutenzione |
| Configurazione | `config.py` | core / ingresso |

## Punti da estrarre prima degli altri

### 1. Gestione della sessione
Il ciclo di vita della sessione è oggi speso fra la CLI e lo stato:
`cli/commands.py` ricostruisce l’agente, `cli/cartella.py` genera gli ID,
`state/stores.py` legge e filtra. Le regole di apertura, ripresa, cambio
sessione e appartenenza alla cartella devono diventare operazioni condivise
usabili dalla CLI e da un client senza terminale.

**Motivo:** è il primo blocco di logica che oggi vive nel client, ma non
appartiene all’interfaccia. Senza questo, la futura UI duplicherebbe la stessa
logica.

### 2. Politica di conferma e ripristino degli apprendimenti
Oggi la CLI fotografa profilo e memorie prima del turno, chiede conferma e
ripristina. Questa è politica del prodotto, non presentazione: deve stare nel
nucleo, accanto al ciclo del turno e alla macchina di apprendimento.

**Motivo:** la futura UI deve avere la stessa semantica di conferma e lo stesso
comportamento in caso di interruzione, senza reimplementare la politica.

### 3. Lock e coordinamento del turno
Il lock dello stato e il lock del turno sono già buone primitive, ma la loro
durata e coordinamento con apprendimento, conferma e manutenzione sono decisi
dalla CLI. La gestione deve diventare parte del nucleo.

**Motivo:** è il punto in cui l’indipendenza dall’interfaccia incontra la
safety reale. È un prerequisito per client multipli senza sorprese di
concorrenza.

## Prossimo passo
Prima di rifattorizzare, definire per ciascuno dei tre punti:
- API pubblica minima;
- cosa resta nel client;
- come si prova con la CLI attuale;
- come si prova con un client senza terminale.

## Bozza di API pubblica minima per la gestione della sessione

Data: 2026-09-28.
**Stato: bozza. L'API implementata, più piccola, è descritta in
[core-refactor-plan.md](core-refactor-plan.md).**

L'obiettivo è un servizio unico per il ciclo di vita della sessione, usabile
dalla CLI e da un client senza terminale, senza cambiare subito il formato
degli archivi.

```python
@dataclass(frozen=True)
class SessioneRiferimento:
    id: str
    titolo: str | None
    cartella: Path | None
    aggiornata: datetime | None


@dataclass
class SessioneAttiva:
    riferimento: SessioneRiferimento
    agente: Agent


class Sessioni:
    def __init__(self, percorsi, impostazioni, politica, utente): ...

    def elenco(self, *, solo_cartella=True, query="") -> list[SessioneRiferimento]: ...

    def nuova(self, nome: str | None = None) -> SessioneAttiva: ...

    def apri(self, nome: str) -> SessioneAttiva: ...

    def riprendi(self, nome: str | None = None, *, scegli: bool = False) -> SessioneAttiva | None: ...
```

### Cosa conterrebbe già oggi

- `Sessioni` incapsula:
  - generazione dell'ID,
  - verifica del proprietario,
  - apertura e ripresa,
  - ricostruzione dell'agente al cambio di sessione o modalità,
  - filtraggio per cartella.

- La CLI smette di chiamare direttamente `build_assistant`, `sessione_di_altri`,
  `sessioni_della_cartella` e `nuovo_id_sessione`.

- Il client resta responsabile solo di:
  - presentare l'elenco,
  - gestire input e conferme,
  - mostrare eventi e stato.

### Cosa non fa ancora

- non introduce un nuovo modello dati;
- non tocca il formato degli archivi;
- non nasconde ancora completamente Agno;
- non risolve ancora il problema dell'ambito di progetto.

## Contratto minimo del servizio di sessione

Data: 2026-09-28.
**Stato: implementato in `ares/core/session.py`, con le differenze elencate
in [core-refactor-plan.md](core-refactor-plan.md).**

### Obiettivo
Un unico punto di accesso al ciclo di vita della sessione, indipendente
dall'interfaccia, che garantisca apertura, ripresa e cambio di contesto con
le stesse regole per CLI e futura UI.

### Cosa deve garantire
- Un id di sessione univoco e stabile, generato dal servizio.
- Il controllo del proprietario prima di aprire una sessione esistente.
- Il filtraggio per cartella come regola del servizio, non del client.
- La ricostruzione dell'agente al cambio di sessione o di modalità.
- Un comportamento coerente fra client interattivo e client senza terminale.

### Cosa deve restituire
- Un riferimento leggibile alla sessione: id, cartella, titolo, aggiornamento.
- Un agente già pronto a servire quella sessione.
- Un modo per distinguere una sessione nuova da una ripresa.

### Cosa non deve fare
- Non deve stampare o chiedere conferme: queste restano al client.
- Non deve decidere la modalità di apprendimento: la riceve come parametro.
- Non deve toccare il formato degli archivi.
- Non deve introdurre un nuovo modello dati di persistenza.
- Non deve nascondere completamente Agno in questa fase.

### API minima
```python
@dataclass(frozen=True)
class SessioneRiferimento:
    id: str
    titolo: str | None
    cartella: Path | None
    aggiornata: datetime | None


@dataclass
class SessioneAttiva:
    riferimento: SessioneRiferimento
    agente: Agent


class Sessioni:
    def __init__(self, percorsi, impostazioni, politica, utente): ...

    def elenco(self, *, solo_cartella=True, query="") -> list[SessioneRiferimento]: ...

    def nuova(self, nome: str | None = None) -> SessioneAttiva: ...

    def apri(self, nome: str) -> SessioneAttiva: ...

    def riprendi(self, nome: str | None = None, *, scegli: bool = False) -> SessioneAttiva | None: ...
```

### Cosa resta al client
- Presentare l'elenco delle sessioni.
- Gestire input, conferme e messaggi di stato.
- Decidere come visualizzare eventi, metriche e apprendimenti.

### Cosa resta al servizio
- Generare l'id.
- Verificare il proprietario.
- Aprire, riprendere e cambiare sessione.
- Ricostruire l'agente quando serve.
- Applicare la regola della cartella.

### Prova minima
- Creare una sessione nuova dalla CLI e da un client senza terminale e
  verificare che l'id sia univoco.
- Riprendere una sessione esistente e verificare che il proprietario sia
  rispettato.
- Cambiare sessione e modalità e verificare che l'agente venga ricostruito
  in modo coerente.
