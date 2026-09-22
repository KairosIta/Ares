"""Configurazione centrale di Ares.

I valori predefiniti sono orientati a un host locale con circa 16 GiB di
VRAM. Modello, contesto, percorsi e identita' possono essere adattati qui o,
quando previsto, tramite variabili d'ambiente.

Importare questo modulo non tocca il disco: legge `.env` e definisce nomi.
La directory dello stato la crea `prepara_archivio(percorsi)`, che chiama chi
l'archivio lo apre davvero. I percorsi sono un oggetto `Percorsi` costruito
da `leggi_percorsi` all'avvio e passato a chi ne ha bisogno: qui non c'e' un
`PERCORSI` corrente, e nemmeno i nomi TMP_DIR, DB_FILE, BACKUP_DIR..., che
erano viste di quell'oggetto e ne nascondevano la provenienza. Chi legge lo
stato lo riceve come parametro; l'unico punto in cui si costruisce e' il
confine del processo.

Lo stesso vale per i modelli: gli undici nomi del tuning restano la sorgente
- `.env`, ambiente e predefiniti si incontrano all'import - e
`leggi_impostazioni` li fotografa in un `Impostazioni` immutabile che i
costruttori ricevono. Le due domande sono separate di proposito: `Percorsi`
dice dove sta lo stato, `Impostazioni` a chi si parla, e l'identita' e' il
terzo asse. Nessuno dei tre e' un nome di modulo che qualcuno riscrive.

Il quarto asse e' la politica: cosa una conversazione impara, quanto
contesto storico vede, come lavora nella cartella e cosa mostra di cio' che
ha imparato. Anche qui i nomi restano la sorgente e `leggi_politica` li
fotografa in una `Politica` fatta di quattro gruppi - `Apprendimento`,
`Cronologia`, `Workspace`, `Mostra` - che i costruttori e i prompt
ricevono. Non e' cosmesi: il prompt deve dire la verita' su cio' che il
modello puo' fare e su cio' che la persona vedra', e un flag riletto da un
modulo a meta' turno poteva cambiare sotto i piedi di un'altra sessione.
"""

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from dotenv import dotenv_values

from ares.state.platform_files import rendi_privato

# La radice del progetto, non quella del package: `.env`, `tmp/` e le
# directory sorelle (backup, workspace) si contano da li'. Questo file sta in
# `ares/`, un livello sotto.
BASE_DIR = Path(__file__).resolve().parent.parent


def leggi_ambiente(percorso_env: Path, ambiente: Mapping[str, str]) -> dict[str, str]:
    """L'ambiente di questo avvio: le righe del `.env` sotto, l'ambiente dato sopra.

    Il `.env` si legge in un dizionario, non in `os.environ`. Fa differenza
    per `run_command`: Agno lancia il sottoprocesso con l'ambiente del
    processo, e un `env` da una shell lo stamperebbe da qualunque cartella di
    lavoro, con l'output che torna nel contesto del modello - che puo' essere
    cloud. Letto qui, il sottoprocesso vede l'ambiente della shell che ha
    lanciato `ares` e nient'altro. Il file sul disco resta leggibile da un
    comando se la cartella di lavoro e' il clone stesso: quello e' l'avviso
    che `cli/cartella.py` da' all'avvio, non un compito di questa funzione.

    La precedenza resta quella di prima: una variabile gia' nell'ambiente
    vince sulla riga del `.env`, cosi' `ARES_TMP=... ares` continua a
    funzionare. Su Windows `os.environ` non distingue le maiuscole, e una
    riga `ares_home=` veniva trovata lo stesso: le chiavi del file si portano
    in maiuscolo li', e solo li', per non cambiare cio' che gia' funzionava.
    """
    righe = {
        chiave.upper() if os.name == "nt" else chiave: valore
        for chiave, valore in dotenv_values(percorso_env).items()
        if valore is not None
    }
    return {**righe, **ambiente}


AMBIENTE: Mapping[str, str] = leggi_ambiente(BASE_DIR / ".env", os.environ)

# ---------------------------------------------------------------------------
# Modelli
# ---------------------------------------------------------------------------

# Modello locale: 9B Q8_0 con supporto per tools, thinking e vision. Gira
# in scheda e non esce mai dalla macchina.
MODELLO_LOCALE = "hf.co/empero-ai/Qwen3.8-9B-Distill-GGUF:Q8_0"

# Modello cloud di Ollama, con tools, thinking e vision. Il nome finisce in
# `:cloud` (o `-cloud`, secondo la famiglia): e' il daemon locale a
# riconoscerlo e a inoltrare la richiesta a https://ollama.com, dopo un
# `ollama signin` fatto una volta sola. Ares continua a parlare con
# localhost, nessuna chiave API entra nell'ambiente o nel `.env`, e il
# footgun di OLLAMA_API_KEY descritto sotto resta chiuso.
#
# Cio' che cambia e' la promessa: prompt e risposte del modello
# conversazionale attraversano un server remoto. Ollama dichiara di
# elaborarli "transiently", di non conservarli oltre il tempo della
# richiesta e di non usarli per addestrare (privacy policy, marzo 2026). E'
# un impegno contrattuale, non una garanzia tecnica, e va scelto sapendolo.
MODELLO_CLOUD = "glm-5.3-flash:cloud"

# Agente principale. Conversazione ed estrazione delle memorie possono usare
# un modello cloud, ciascuna per scelta esplicita nel `.env`; l'embedder no:
# `build_knowledge` rifiuta di costruirlo su un nome cloud, cosi' il
# confine sta nel codice e non in un commento.
#
# Il valore distribuito e' quello locale. Il progetto promette in copertina
# che nessun dato esce dalla macchina se non per un modello scelto apposta, e
# un default deve poter mantenere da solo la promessa di chi lo distribuisce:
# chi clona non manda una conversazione a `ollama.com` senza averlo deciso.
#
# La decisione opposta non richiede pero' di modificare questo file, che
# tornerebbe a divergere a ogni `git pull`. `ARES_MAIN_MODEL` nel `.env` lo
# sovrascrive, come gia' fanno `ARES_TMP` e le altre variabili: qui stanno le
# decisioni versionate, nel `.env` cio' che cambia da una macchina all'altra.
# Quale modello regge questa scheda, e se questa macchina accetta di parlare
# con un servizio remoto, sono esattamente cio' che cambia da una macchina
# all'altra.
#
#     ARES_MAIN_MODEL=glm-5.3-flash:cloud
#
# Il nome non viene interpretato: se non e' scaricato lo dice `ares-preflight`,
# e la chat lo ripete all'avvio insieme all'avviso sul cloud.
MAIN_MODEL = AMBIENTE.get("ARES_MAIN_MODEL") or MODELLO_LOCALE

# Modello per l'estrazione delle memorie. Il valore distribuito e' locale,
# per la stessa ragione di MAIN_MODEL e a maggior ragione: il profilo e le
# memorie sono la parte piu' sensibile di cio' che Ares sa di te, e ogni
# estrazione manda al modello il testo del turno piu' le memorie gia' salvate.
#
# Chi vuole una macchina senza pesi in scheda, con la sola eccezione
# dell'embedder, lo dichiara nel `.env` come per la conversazione:
#
#     ARES_LEARNING_MODEL=glm-5.3-flash:cloud
#
# E' una scelta separata da ARES_MAIN_MODEL, perche' le due variabili
# rispondono a domande diverse: quale modello parla con te, e a chi affidi
# cio' che Ares ricorda di te. Preflight e banner della chat dicono a ogni
# avvio quali dei due ruoli escono dalla macchina.
#
# Con MAIN_MODEL in cloud e LEARNING_MODEL locale la scheda e' libera e il
# 9B resta caldo fra un turno e l'altro. Con MAIN_MODEL locale conviene
# invece MAIN_MODEL anche qui, per evitare lo swap dei pesi fra la risposta
# e l'apprendimento: e' il default distribuito, in cui i due ruoli
# coincidono. Puntando `ARES_MAIN_MODEL` al cloud si separano da soli, e
# `Impostazioni.num_ctx_apprendimento` se ne accorge senza toccare niente.
LEARNING_MODEL = AMBIENTE.get("ARES_LEARNING_MODEL") or MODELLO_LOCALE


def e_modello_cloud(nome: str) -> bool:
    """Vero se il nome designa un modello cloud di Ollama.

    Ollama scrive il tag in due forme, `glm-5.3-flash:cloud` e
    `gpt-oss:120b-cloud`, e le usa entrambe nella libreria. Il confronto e'
    sul solo tag, dopo i due punti, cosi' un modello locale il cui nome di
    repository contenesse "cloud" non verrebbe scambiato per remoto; e un
    nome senza tag e' `:latest` per Ollama, quindi mai cloud.
    """
    _, separatore, tag = nome.rpartition(":")
    return bool(separatore) and (tag == "cloud" or tag.endswith("-cloud"))


# Embedder unico per le collezioni LanceDB. Indici e query devono usare lo
# stesso modello e la stessa dimensionalita'. E' il solo ruolo che resta
# locale sempre, qualunque cosa dica il `.env`: cambiare embedder
# invaliderebbe l'indice gia' scritto, e `build_knowledge` rifiuta un nome
# cloud per questo ruolo.
EMBEDDER_MODEL = "nomic-embed-text-v2-moe"
EMBEDDER_DIMENSIONS = 768

# ---------------------------------------------------------------------------
# Tuning Ollama
# ---------------------------------------------------------------------------

# Server Ollama. Va passato esplicitamente e non lasciato al default: se
# OLLAMA_API_KEY e' presente nell'ambiente e host non e' impostato, Agno
# dirotta le chiamate su https://ollama.com. Un progetto che promette che
# nessun dato esce dalla macchina se non per il modello scelto apposta non
# puo' dipendere da una variabile d'ambiente per mantenere la promessa.
# Anche i modelli cloud passano da qui: e' il daemon a inoltrarli.
OLLAMA_HOST = "http://localhost:11434"

# Ollama usa 4096 token di contesto se non gli dici altro. Un agente con
# tool, memorie iniettate e cronologia lo satura entro pochi turni, e il
# troncamento silenzioso si manifesta come "l'agente ha dimenticato".
#
# Il valore predefinito sfrutta il contesto esteso del modello. Su hardware
# con meno memoria va ridotto insieme alla taglia o alla quantizzazione.
#
# Per un modello cloud il valore viene inoltrato ma non costa VRAM locale:
# il tetto vero lo decide il servizio (glm-5.3-flash dichiara 1M).
NUM_CTX = 262144

# Mantiene i pesi in VRAM tra un turno e l'altro. Senza questo Ollama
# scarica il modello dopo 5 minuti e il turno successivo paga il ricaricamento.
# Per un modello cloud non c'e' nulla da tenere in scheda: innocuo.
KEEP_ALIVE = "30m"

TEMPERATURE = 0.7

# Il modello ragiona prima di rispondere e Ollama glielo accende di default.
# `think` e' un parametro top-level dell'API. Resta attivo per la risposta e
# viene disattivato per le estrazioni strutturate, dove aggiungerebbe latenza.
MAIN_THINK = True
LEARNING_THINK = False

# Contesto dell'estrazione. Ogni store riceve il solo testo utente e
# assistente del turno, senza tool call, piu' le memorie gia' salvate: le
# richieste osservate stanno fra 1k e 2k token. 32k lascia oltre dieci
# volte di margine e costa un ottavo della cache KV di NUM_CTX: sul 9B Q8_0
# sono circa 4,7 GB di VRAM in meno, da 14 a 9,3 GB in scheda. Il margine
# non e' un lusso: Ollama tronca un prompt oltre num_ctx senza errore, e
# un'estrazione troncata scrive memorie parziali in silenzio.
#
# Il risparmio ha senso solo con due modelli diversi. Con lo stesso modello
# locale in entrambi i ruoli un `num_ctx` diverso costringe Ollama a
# riavviare il runner a ogni passaggio fra risposta ed estrazione, e col
# runner se ne va la cache del prompt: il turno dopo rielabora da capo
# tutta la conversazione. In quel caso l'estrazione usa NUM_CTX. La scelta
# sta in `Impostazioni.num_ctx_apprendimento`, perche' dipende da quali due
# modelli sono: un numero solo, deciso qui all'import, non potrebbe seguirli.
NUM_CTX_ESTRAZIONE = 32768

# Estrazione memorie: temperatura bassa, perche' e' un compito di
# trascrizione strutturata e non di conversazione.
TEMPERATURE_ESTRAZIONE = 0.2


# ---------------------------------------------------------------------------
# Impostazioni della conversazione
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Impostazioni:
    """Con quali modelli parla una conversazione, e come li raggiunge.

    E' il secondo asse risolto al confine del processo, accanto a `Percorsi`:
    i percorsi dicono *dove* sta lo stato, le impostazioni dicono *a chi* si
    parla e con quale connessione. Stanno fuori da `Percorsi` per la stessa
    ragione per cui ne sta fuori l'identita': sono domande diverse, e
    tenerle nello stesso oggetto faceva sembrare che cambiare modello
    cambiasse archivio.

    I campi sono i valori risolti, non le variabili d'ambiente: chi vuole
    due conversazioni con modelli diversi ne costruisce due, e nessuna
    rilegge niente a meta' strada.

    Le due `options` di Ollama sono proprieta' e non campi perche' non sono
    indipendenti dai modelli: `num_ctx_apprendimento` dipende da quali due
    modelli sono, e un campo accanto a `apprendimento` potrebbe contraddirlo.
    """

    principale: str
    apprendimento: str
    embedder: str
    embedder_dimensioni: int
    host: str
    keep_alive: str
    num_ctx: int
    temperatura: float
    temperatura_apprendimento: float
    think: bool
    think_apprendimento: bool

    @property
    def num_ctx_apprendimento(self) -> int:
        """Il contesto dell'estrazione: stretto se i due modelli sono diversi.

        Con lo stesso modello in entrambi i ruoli un `num_ctx` diverso
        costringe Ollama a riavviare il runner a ogni passaggio fra risposta
        ed estrazione - e col runner se ne va la cache del prompt, quindi il
        turno dopo rielabora da capo la conversazione. Il risparmio di VRAM
        vale solo quando sono due modelli distinti, ed e' la coppia a
        deciderlo, non un numero scelto a mano.
        """
        return NUM_CTX_ESTRAZIONE if self.apprendimento != self.principale else self.num_ctx

    @property
    def opzioni(self) -> dict[str, Any]:
        return {"num_ctx": self.num_ctx, "temperature": self.temperatura}

    @property
    def opzioni_apprendimento(self) -> dict[str, Any]:
        return {"num_ctx": self.num_ctx_apprendimento, "temperature": self.temperatura_apprendimento}

    def avviso_cloud(self) -> list[str]:
        """Righe che dicono cosa esce dalla macchina; vuoto se niente esce.

        Nominano i ruoli cloud, cio' che mandano fuori e cio' che resta
        locale. Preflight e chat le stampano tali e quali: un avviso che
        riguarda dove finiscono le parole deve dire la stessa cosa ovunque lo
        si legga. E deve dire tutto: con la conversazione in cloud non escono
        solo "prompt e risposte", ma ogni cosa che il modello riceve - i file
        che legge, l'output dei comandi, profilo e memorie iniettati nel
        contesto, le conversazioni passate che rilegge.
        """
        conversazione = e_modello_cloud(self.principale)
        estrazione = e_modello_cloud(self.apprendimento)
        if conversazione and estrazione:
            return [
                "Conversazione ed estrazione delle memorie sono cloud: prompt, risposte, file letti, output dei",
                "comandi, memorie e conversazioni rilette escono dalla macchina verso ollama.com.",
                "Solo l'embedding resta locale.",
            ]
        if conversazione:
            return [
                "Il modello conversazionale e' cloud: prompt, risposte, file letti, output dei comandi, memorie",
                "iniettate e conversazioni rilette escono dalla macchina verso ollama.com.",
                "Estrazione delle memorie ed embedding restano locali.",
            ]
        if estrazione:
            return [
                "Il modello di estrazione e' cloud: il testo dei turni e le memorie gia' salvate",
                "escono dalla macchina verso ollama.com. Conversazione ed embedding restano locali.",
            ]
        return []


def leggi_impostazioni() -> Impostazioni:
    """Le impostazioni di questa conversazione, lette quando servono.

    `leggi_percorsi` rilegge l'ambiente che gli si passa; qui i valori sono
    gia' risolti nei nomi qui sopra, dove `.env`, ambiente e predefiniti si
    incontrano una volta sola all'import. Questa funzione li fotografa in un
    oggetto immutabile al confine del processo - la chat e i suoi comandi nel
    proprio corpo, preflight all'inizio, le prove all'import.

    I nomi restano la sorgente, come `AMBIENTE` lo e' per i percorsi, e
    nessuno li muta: chi costruisce un agente riceve l'oggetto. Il giorno in
    cui una prova o una UI vorranno due conversazioni con modelli diversi
    costruiranno due `Impostazioni`, invece di riscrivere un nome di modulo
    che il resto del processo non vede cambiare.
    """
    return Impostazioni(
        principale=MAIN_MODEL,
        apprendimento=LEARNING_MODEL,
        embedder=EMBEDDER_MODEL,
        embedder_dimensioni=EMBEDDER_DIMENSIONS,
        host=OLLAMA_HOST,
        keep_alive=KEEP_ALIVE,
        num_ctx=NUM_CTX,
        temperatura=TEMPERATURE,
        temperatura_apprendimento=TEMPERATURE_ESTRAZIONE,
        think=MAIN_THINK,
        think_apprendimento=LEARNING_THINK,
    )


# `save_session_context` passa da una tool call con quattro argomenti. Ollama
# puo' restituire JSON troncato senza sollevare un errore: Agno vede una
# risposta valida ma nessuno strumento eseguito. In quel solo caso lo store
# del contesto ripete l'estrazione; zero disabilita il recupero. Un tentativo
# basta a non trasformare un difetto intermittente in latenza moltiplicata.
SESSION_CONTEXT_RETRIES = 1

# ---------------------------------------------------------------------------
# Apprendimento
# ---------------------------------------------------------------------------

# Ogni store in modalita' ALWAYS costa UNA chiamata al modello dopo ogni
# risposta, eseguita in sequenza. Con tre store attivi paghi tre inferenze
# extra per turno. Sul 9B in scheda e col pensiero spento (LEARNING_THINK)
# sono rapide, ma la latenza percepita cresce comunque: disattiva quello
# che non ti serve. Passano tutte da LEARNING_MODEL, locale o cloud che sia.
LEARN_USER_PROFILE = True  # ALWAYS - chi sei, come preferisci le risposte
LEARN_USER_MEMORY = True  # ALWAYS - osservazioni non strutturate su di te
LEARN_SESSION_CONTEXT = True  # ALWAYS - obiettivo, piano, avanzamento
LEARN_ENTITIES = True  # AGENTIC - persone, progetti (nessun costo fisso)
LEARN_KNOWLEDGE = True  # AGENTIC - intuizioni riutilizzabili

# Tetto di scritture per singola estrazione. Il default di Agno e' 10.
MAX_UPDATES_PER_RUN = 5

# Uno strumento per correggere le memorie. Senza questo l'agente puo' solo
# subire cio' che l'estrazione automatica ha scritto: una memoria sbagliata o
# in inglese si toglie solo aprendo SQLite a mano. Il tool passa dalla stessa
# estrazione della modalita' ALWAYS, quindi rispetta le istruzioni in italiano
# dello store, e sa anche cancellare (`enable_delete_memory` e' acceso di
# default; svuotare tutto no, e resta spento).
MEMORY_AGENT_TOOLS = True

# ---------------------------------------------------------------------------
# Cronologia e sessioni passate
# ---------------------------------------------------------------------------

# Le sessioni restano separate. Questi strumenti consentono all'agente di
# consultare esplicitamente sessioni precedenti quando serve.
SEARCH_PAST_SESSIONS = True  # elenca le sessioni passate e ne rilegge una
READ_CHAT_HISTORY = True  # rilegge questa sessione oltre NUM_HISTORY_RUNS

# Quante sessioni elencare e quanti scambi mostrare per ognuna. I default di
# Agno sono 20 e 3, e ogni scambio porta 2 messaggi troncati a 200 caratteri:
# 20 x 3 x 2 x 200 sono 24k caratteri, circa 6k token consumati da una sola
# risposta di tool. Con 6 e 2 si scende sotto 1,5k.
PAST_SESSIONS_LIMIT = 6
PAST_SESSION_RUNS_PREVIEW = 2

# Quante sessioni mostra `/sessioni`. Numero diverso dai due qui sopra perche'
# risponde a un vincolo diverso: quelli difendono la finestra del modello,
# questo l'altezza di un terminale. Cio' che avanza viene contato, non taciuto.
SESSIONI_ELENCO = 20

# Ogni conversazione registra la cartella in cui e' nata, e all'avvio il
# modello riceve le ultime di quella cartella - id, data, prima domanda -
# cosi' "dove eravamo rimasti" funziona anche senza `ares resume`:
# `read_past_session` le rilegge per id. Poche, perche' stanno nel prompt di
# ogni turno; il resto lo trova `search_past_sessions`.
SESSIONI_RECENTI_NEL_PROMPT = 5

# Quante entita' chiedere allo store quando `/entita <testo>` cerca. La
# ricerca del framework e' larga - verifica la query contro *tutti* i valori
# dell'entita', namespace e date comprese - quindi `stores.leggi_entita` ne
# scarta una parte dopo averle ricevute. Il numero e' la finestra da cui
# scartare: chiederne quante se ne vogliono mostrare significherebbe mostrarne
# meno. Alto perche' l'archivio di un utente solo non ci arriva.
ENTITA_FINESTRA_RICERCA = 200

# Quanti turni di questa sessione restano nel contesto senza chiedere nulla.
NUM_HISTORY_RUNS = 5

# I messaggi utente e assistente dei cinque turni restano tutti; degli
# strumenti si reiniettano invece soltanto le dieci chiamate piu' recenti.
# Un turno che legge molti file non deve trascinare ogni risultato nei turni
# successivi. Il filtro riguarda il prompt, non l'archivio: Agno continua a
# conservare la cronologia completa in SQLite.
MAX_TOOL_CALLS_FROM_HISTORY = 10

# Un file del workspace puo' arrivare a 10 MB e get_chat_history puo'
# restituire una sessione intera. Agno 3 sposta i risultati oltre questa
# soglia nel FileSystem, entro le proprie quote, e lascia al modello
# un'anteprima con due strumenti paginati, read_result e search_result. Il
# payload usa filesystem.db e l'indice kairos.db: entrambi sono gia' inclusi
# negli snapshot di Ares. Il superamento quota e' un fallback esplicito con
# testa e coda, non una conservazione lossless.
OFFLOAD_TOOL_RESULTS = True
TOOL_RESULT_THRESHOLD_CHARS = 16_000

# Gli offload seguono la vita della conversazione: nessun TTL puo' lasciare
# in una sessione conservata un result_id ormai illeggibile. La manutenzione
# offline propone invece le sessioni inattive da eliminare per intero; non
# parte mai da sola e usa questo valore soltanto come default della CLI.
SESSION_RETENTION_DAYS = 180

# Il prune per eta' non tocca le conversazioni nominate qui. Una cancellazione
# puntuale resta possibile, con anteprima, backup e conferma, perche' una
# protezione esplicita deve impedire gli automatismi, non rendere il dato
# incancellabile. `principale` era la sessione predefinita prima che le
# conversazioni nascessero per cartella; resta protetta perche' chi la usava
# ci ha dentro mesi di contesto, e `--session principale` la riapre ancora.
SESSIONI_PROTETTE = ("principale",)

# ---------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------

# Come Ares legge l'ora corrente nel proprio prompt. Senza questo Agno fa
# `str(datetime)` e gli consegna `2026-08-21 12:31:35.240856+02:00`: i
# microsecondi non servono a niente e il giorno della settimana, che serve,
# non c'e'. Il fuso resta esplicito perche' le date delle memorie sono in UTC
# e sottrarre due istanti in fusi diversi senza saperlo e' un errore
# silenzioso.
#
# I nomi di giorno e mese escono in inglese: `strftime` segue la locale del
# processo, e cambiarla e' una mutazione globale per una parola. Restano fra
# i frammenti inglesi del prompt, come le istruzioni del FileSystem.
DATETIME_FORMAT = "%A %d %B %Y, %H:%M %Z"

# Ares parla di quando ha saputo le cose solo se le date gli arrivano. In
# archivio ci sono da sempre, e' il rendering di Agno che le scarta: lo
# schema in schemas.py rimette la data accanto a ogni memoria.
#
# Spegnerlo non tocca l'archivio e non lo rende illeggibile: lo schema non
# aggiunge nessun campo, quindi cio' che e' stato scritto con le date si
# rilegge con lo schema di serie e viceversa, date comprese. Si perde solo
# il rendering.
DATE_MEMORIE = True

# ---------------------------------------------------------------------------
# Metriche del turno
# ---------------------------------------------------------------------------

# Quanto e' piena la finestra, quanto e' costato il turno e quanto di quel
# costo era apprendimento. Nessuna inferenza in piu': Ollama restituisce i
# conteggi con ogni risposta e Agno li accumula nel RunOutput, che finora
# `esegui_turno` scartava.
#
# Il troncamento del contesto e il costo degli store di apprendimento non sono
# altrimenti evidenti durante una sessione interattiva.
#
# Spento di default: e' una riga in piu' sotto ogni risposta, e chi non la
# guarda non deve pagarla in disordine. `chat.py --metriche` la accende per
# una sessione sola.
MOSTRA_METRICHE = False

# ---------------------------------------------------------------------------
# Esito degli strumenti
# ---------------------------------------------------------------------------

# Un `[workspace_run_command]` a schermo dice che lo strumento e' partito, non
# come e' finito. Cio' che torna al modello finisce nella sua finestra e non
# nella nostra: se `read_file` non trova il file, l'utente vede il nome dello
# strumento, poi una risposta costruita su un errore che non ha mai letto.
#
# Gratis come le metriche: gli eventi passano gia' dal core del turno e dal
# client CLI. Acceso di default, al contrario delle metriche, perche'
# non e' una misura da tuning ma la differenza fra guardare cosa fa Ares e
# fidarsi del suo racconto.
MOSTRA_ESITO_STRUMENTI = True

# Quanto risultato mostrare. Qui si tronca, mentre la richiesta di conferma
# non tronca mai: sono due cose opposte. Li' si autorizza, e la coda di un
# comando e' precisamente la parte che decide; qui si guarda un esito, e
# `get_chat_history` sa restituire una sessione intera.
ESITO_RIGHE = 3
ESITO_LARGHEZZA = 100

# ---------------------------------------------------------------------------
# Eco di cio' che entra in memoria
# ---------------------------------------------------------------------------

# Il modello scrive nella memoria durevole per due strade: gli strumenti che
# chiama lui (`save_learning`, `remember_about`, `update_user_memory`) e
# l'estrazione automatica dopo la risposta, che aggiorna profilo e memorie
# senza passare da nessuno strumento visibile. Nessuna delle due chiede
# conferma prima di scrivere - quella viene dopo, CONFERMA_APPRENDIMENTI qui
# sotto - e cio' che entra viene reiniettato in ogni sessione futura: un
# file del workspace o l'output di un comando con dentro un'istruzione puo'
# quindi lasciare una traccia che dura oltre il turno. L'eco e' il modo di
# accorgersene subito, invece che alla sessione dopo, quando Ares "ricorda"
# qualcosa che non gli e' mai stato detto.
#
# Con l'eco acceso, sotto la risposta compare cosa e' cambiato in profilo e
# memorie - il testo intero, non un'anteprima - e ogni strumento di memoria
# mostra i propri argomenti oltre all'esito. Tace quando il turno non ha
# scritto niente, cioe' nella maggior parte dei turni. Il contesto di
# sessione resta fuori: cambia a ogni turno per costruzione, e `/contesto`
# lo mostra quando serve. Vedi `agent/echo.py`.
#
# Acceso di default, per la stessa ragione dell'esito degli strumenti: e' la
# differenza fra vedere cosa Ares ha imparato e fidarsi che abbia imparato
# la cosa giusta. Il rimedio, quando una riga non convince, sono gli
# strumenti di memoria (`MEMORY_AGENT_TOOLS`): basta chiederglielo.
MOSTRA_APPRENDIMENTI = True

# Con l'eco acceso, quando un turno ha scritto in profilo o memorie la CLI
# chiede se tenere. Invio tiene; `n` riporta i due store a com'erano prima
# del turno, riscrivendoli con l'istantanea letta allora. E' la conferma che
# Agno non offre su questi store, costruita a valle invece che a monte: la
# scrittura avviene, ma non sopravvive al turno se chi legge dice di no.
#
# "A valle" e' detto sul serio: fra la scrittura e la risposta c'e' una
# finestra, e un processo che muore li' dentro - un `kill`, un crash - lascia
# la scrittura dov'e'. Il lock copre l'attesa fra due chat, non fra due vite
# del processo. La garanzia e' quindi condizionata a un processo che arriva
# alla risposta; cosa chiuderebbe la finestra lo dice `agent/echo.py`.
#
# Tutto o niente, per turno: una memoria giusta e una sbagliata nello stesso
# turno si tengono o si tolgono insieme, e la correzione fine passa dagli
# strumenti di memoria. Non e' una domanda in piu' a ogni messaggio: compare
# solo nei turni che hanno scritto qualcosa, cioe' pochi. Senza l'eco la
# domanda non ha niente da mostrare e non viene fatta.
CONFERMA_APPRENDIMENTI = True

# ---------------------------------------------------------------------------
# Percorsi
# ---------------------------------------------------------------------------

# Dove vive tutto cio' che Ares impara e conserva: `~/.ares`, come `~/.ssh`.
# Prima stava dentro il clone, in `tmp/`, e andava bene finche' `ares` si
# lanciava da li'. Con il comando sul PATH e una cartella di lavoro diversa a
# ogni avvio lo stato deve avere un posto che non dipenda da dove si e'
# scaricato il codice. `ARES_HOME` lo sposta in blocco; `ARES_TMP` e
# `ARES_BACKUP_DIR` spostano le due parti da sole, ed e' cio' che usano le
# prove per girare su un archivio usa-e-getta.
#
# I percorsi sono un oggetto, costruito da `leggi_percorsi` all'avvio e non
# quando questo modulo viene importato. Non esistono piu' i nomi di sempre -
# TMP_DIR, DB_FILE, BACKUP_DIR... - tenuti allineati da `imposta_percorsi`:
# erano un canale invisibile, per cui un modulo leggeva `TMP_DIR` senza che
# dalla sua firma si vedesse da dove venisse, e chi ne scriveva uno solo
# lasciava gli altri indietro. Ora chi legge lo stato lo riceve come
# parametro, e la cartella scelta con `--workspace` e' un `replace` locale
# invece di una mutazione che il resto del processo non vede arrivare.


@dataclass(frozen=True)
class Percorsi:
    """Dove sta lo stato, dove i backup e dove si lavora.

    L'identita' non sta qui: per conto di chi si parla e' un `Utente`, che e'
    un asse suo e non un percorso. Tenerli nello stesso oggetto faceva
    sembrare che scegliere una cartella potesse cambiare utente.
    """

    home: Path
    stato: Path
    backup: Path
    lavoro: Path

    # Il nome del file conserva quello che il progetto aveva prima del
    # rilascio pubblico, mentre le classi sono state rinominate. Non e' una
    # svista: sta nella tupla `DATABASE` di backup/integrity.py e quindi
    # nell'insieme di file che `verifica_snapshot` pretende, cioe' dentro
    # ogni snapshot gia' creato. Cambiarlo e' una migrazione con bump di
    # FORMATO_BACKUP e lettura di entrambi i nomi al restore, non una
    # rinomina - e il nome non arriva mai all'utente.
    @property
    def db_file(self) -> str:
        return str(self.stato / "kairos.db")

    @property
    def fs_db_file(self) -> str:
        return str(self.stato / "filesystem.db")

    @property
    def lancedb_uri(self) -> str:
        return str(self.stato / "lancedb")

    # Lock fratello della directory dello stato, non al suo interno: il
    # restore sostituisce l'intera directory e il file che coordina
    # l'operazione deve restare fermo.
    @property
    def lock_file(self) -> Path:
        return self.stato.with_name(self.stato.name + ".lock")

    # Dentro lo stato per due motivi: le prove sono gia' isolate, e il backup
    # la copia negli snapshot insieme al resto. Un restore pero' non la
    # riavvolge: riporta indietro Ares, non chi gli parla. Contiene tutto cio'
    # che si e' scritto ad Ares; `CronologiaSicura` la crea a 0600 su POSIX.
    @property
    def cronologia_file(self) -> Path:
        return self.stato / "cronologia_chat.txt"


def leggi_percorsi(ambiente: Mapping[str, str] | None = None, cwd: Path | None = None) -> Percorsi:
    """I percorsi di questo avvio, letti dall'ambiente dato o da quello vero.

    `ambiente` e `cwd` esistono per le prove e per chi vuole un secondo
    insieme di percorsi nello stesso processo; senza, si legge `AMBIENTE` -
    l'ambiente vero sopra il `.env` - e la directory corrente. Se quella non esiste piu' -
    cancellata da sotto la shell - si ripiega sulla home, che la verifica dei
    rischi fermera' con un avviso invece di un traceback. Risolta subito: su
    Windows la directory corrente puo' arrivare con i nomi corti
    (`RUNNER~1`), e lo stesso percorso scritto in due modi e' la strada per
    un confronto che fallisce.

    Chi la chiama lo fa al confine del processo: i comandi della CLI nel
    proprio corpo, le prove all'import del modulo dopo `prepara_ambiente`.
    Il risultato viaggia come parametro fino in fondo, quindi due `Percorsi`
    diversi nello stesso processo non si confondono.
    """
    env: Mapping[str, str] = AMBIENTE if ambiente is None else ambiente
    home = Path(env.get("ARES_HOME") or Path.home() / ".ares")
    if cwd is None:
        try:
            cwd = Path(os.getcwd()).resolve()
        except FileNotFoundError:
            cwd = Path.home()
    return Percorsi(
        home=home,
        stato=Path(env.get("ARES_TMP") or home / "stato"),
        backup=Path(env.get("ARES_BACKUP_DIR") or home / "backup"),
        lavoro=cwd,
    )


# Dove stavano prima. Li legge `ops/migrazione.py`, che li sposta una volta
# sola; la chat si ferma finche' ci sono dati li' e niente nel posto nuovo,
# perche' partire con uno stato vuoto accanto a uno pieno li sdoppierebbe.
# Non dipendono dai percorsi correnti - sono il posto di prima, per
# definizione - quindi restano costanti di processo.
VECCHIO_TMP_DIR = BASE_DIR / "tmp"
VECCHIO_BACKUP_DIR = BASE_DIR.parent / "ares-backup"


def prepara_archivio(percorsi: Percorsi) -> Path:
    """Crea la directory dello stato, privata, e restituisce il percorso.

    Sta qui e non nel corpo del modulo perche' importare una configurazione
    non deve produrre effetti: `preflight.py` importa `config` per tre nomi di
    modello e non ha alcun motivo di lasciarsi dietro un archivio, e nemmeno
    `--help`. Chi apre l'archivio la chiama, ed e' idempotente: i tre
    costruttori di `assistant.py`, e il corpo dei comandi che l'archivio lo
    toccano - `backup/snapshots.py` no, legge lo stato e sa dire che non c'e'.
    Nei comandi la chiamata viene **dopo** aver letto gli argomenti, perche'
    `--help` esce prima. Piu' punti dello stretto necessario, di proposito: il
    costo di una chiamata in piu' e' zero, quello di una dimenticata e' un
    archivio leggibile da chiunque.

    I permessi si applicano alla directory e non ai file che contiene, perche'
    la directory e' il confine che regge davvero: senza il diritto di
    attraversarla i modi dei singoli database non si raggiungono, e quei modi
    li decide la umask di chi apre il file, non questo progetto. Qui dentro
    c'e' tutto cio' che e' stato detto ad Ares: la cronologia accanto nasce
    gia' a 0600 e gli snapshot a 0700/0600, questa riga toglie l'asimmetria
    per cui la copia era privata e l'originale no. Su Windows non fa nulla,
    come ovunque nel progetto: li' vale la DACL ereditata.
    """
    percorsi.stato.mkdir(parents=True, exist_ok=True)
    rendi_privato(percorsi.stato)
    # Anche `~/.ares`, quando lo stato ci sta dentro: e' la directory che si
    # attraversa per arrivare a tutto il resto, backup compresi.
    if percorsi.stato.parent == percorsi.home:
        rendi_privato(percorsi.home)
    return percorsi.stato


# Snapshot locali dello stato appreso. Accanto allo stato e non dentro,
# perche' un backup dentro cio' che deve salvare verrebbe copiato
# ricorsivamente e sparirebbe insieme all'originale. Il percorso lo porta
# `Percorsi.backup`, e il lock `Percorsi.lock_file`: non ci sono piu' nomi
# qui che li fotografino all'import.

# Solo il valore suggerito dalla CLI. Nessuno snapshot viene cancellato
# automaticamente: `backup.py prune` mostra sempre i candidati e chiede una
# conferma, a meno di un --yes esplicito.
BACKUP_KEEP = 20

# Dopo quanti giorni la chat ricorda all'avvio che manca un backup. Zero
# spegne il promemoria.
#
# Il backup resta manuale, ed e' una scelta. Farlo da solo all'uscita
# significherebbe una decina di secondi fra il `/esci` e il ritorno alla
# shell - i due SQLite copiati, LanceDB copiato, il sondaggio in un processo
# isolato - pagati a ogni sessione, comprese quelle in cui non e' cambiato
# niente. E un backup che parte da se' e' anche un backup che puo' fallire da
# se', in un momento in cui nessuno sta guardando.
#
# Sette giorni perche' e' l'intervallo oltre il quale la domanda "quanto
# perderei adesso?" comincia ad avere una risposta scomoda, e perche' un
# avviso che compare troppo spesso smette di essere letto.
BACKUP_PROMEMORIA_GIORNI = 7

# ---------------------------------------------------------------------------
# Cronologia della riga di comando
# ---------------------------------------------------------------------------

# Il percorso sta in `Percorsi.cronologia_file`; qui il formato:
# `CronologiaSicura` tiene una voce JSON per messaggio anche multilinea e
# coordina con un lock breve le chat aperte insieme. Il vecchio formato GNU
# Readline viene riletto e migrato alla prima nuova voce.

# Un tetto perche' un file che cresce e basta e' esattamente cio' che questo
# progetto conta altrove. Il backend lo applica atomicamente a ogni nuova
# voce, tenendo la coda piu' recente.
CRONOLOGIA_RIGHE = 2000

# ---------------------------------------------------------------------------
# Spazio di lavoro sul disco
# ---------------------------------------------------------------------------

# La directory di lavoro e' quella da cui si lancia `ares`, come per Claude
# Code o Codex: si apre una cartella, si scrive `ares`, e Ares lavora li'.
# Prima era una directory fissa accanto al progetto, uno spazio di Ares dove
# clonare cose; ora e' il progetto dell'utente, con i suoi file. `--workspace`
# la sceglie esplicitamente, e `cli/cartella.py` la guarda prima di aprirla:
# la radice del disco, la home intera, una directory di sistema o una che
# contiene lo stato di Ares si aprono solo dopo una conferma scritta.
#
# Il confine e' quello che Agno chiama, nel docstring di Workspace, "a
# path-scoping boundary, not a process sandbox": gli strumenti sui file non
# escono da qui, ma `run_command` esegue sulla macchina vera e puo' uscirne,
# leggere l'ambiente, aprire la rete. Cio' che regge il confine e' la
# conferma umana, non il codice: per questo la shell sta fra le azioni da
# confermare e non fra quelle libere.
#
# Il valore e' la directory corrente letta da `leggi_percorsi`; la chat lo
# sostituisce all'avvio con la cartella scelta e autorizzata, con un
# `replace` sul proprio `Percorsi` e non su una globale.
WORKSPACE = True

# Il file che, se c'e' nella cartella di lavoro, entra nelle istruzioni del
# turno: convenzioni del progetto, cosa non toccare, come si lanciano le
# prove. `ares init` ne scrive uno scheletro. Il tetto evita che un file
# enorme occupi da solo la finestra del modello.
WORKSPACE_ISTRUZIONI = "ARES.md"
WORKSPACE_ISTRUZIONI_MAX_BYTE = 32_000

# Il prefisso non e' cosmetico. Il FileSystem privato espone gia' read_file,
# write_file, list_files, move_file e search_content: registrando Workspace
# accanto, Agno scarta cinque strumenti su otto con un WARNING e tiene i
# primi arrivati. Il modello vedrebbe un `read_file` che crede legga il disco
# e che legge invece il quaderno nel database. Rinominare e' l'unico modo per
# tenere le due superfici distinte, ed e' il verso giusto: le istruzioni del
# FileSystem le scrive Agno e nominano i propri strumenti, quelle di qui le
# scriviamo noi.
WORKSPACE_PREFIX = "workspace_"

# Le modalita', come quelle di Claude Code. Ognuna e' una partizione degli
# otto strumenti dello spazio di lavoro in due liste: cio' che e' nella prima
# gira in silenzio, cio' che e' nella seconda mette il turno in pausa e
# aspetta un si', cio' che non e' in nessuna delle due non viene nemmeno
# mostrato al modello. Il paragrafo del prompt che le descrive e' generato da
# qui, quindi una modalita' nuova o cambiata non lascia indietro le istruzioni.
#
#   manuale    leggere, elencare e cercare in silenzio; tutto cio' che lascia
#              una traccia sul disco chiede conferma. La riga di confine e'
#              proprio quella: cio' che il modello legge - un file del
#              progetto, l'output di un comando, lo stesso ARES.md - puo'
#              contenere un'istruzione, e una scrittura che nessuno guarda
#              puo' riscrivere ARES.md, uno script o un Makefile: non
#              distrugge oggi, esegue domani. La conferma mostra il contenuto
#              per intero, come Claude Code mostra la modifica prima di
#              applicarla.
#   modifiche  come "accept edits": scrivere e modificare in silenzio;
#              spostare, cancellare ed eseguire con conferma. Per chi scrive
#              codice e legge il diff dopo.
#   piano      sola lettura: gli strumenti che lasciano traccia non ci sono,
#              e il prompt chiede al modello di proporre, non di fare.
#   auto       nessuna conferma. Solo con `ares --modo auto`, mai qui come
#              valore predefinito e mai con `-p`: una pipe con un testo
#              ostile eseguirebbe comandi senza che nessuno guardi. Per lo
#              stesso motivo `-p` rifiuta anche `modifiche`: scriverebbe.
#
# `ares --modo` sceglie per una sessione, `/modo` cambia a meta' conversazione
# ricostruendo l'agente sulla stessa sessione. WORKSPACE_READ_BEFORE_WRITE
# vale in ogni modalita': e' l'altra rete, contro il modello che riscrive da
# zero un file che si e' immaginato.
Modo = Literal["manuale", "modifiche", "piano", "auto"]
MODALITA: dict[str, tuple[list[str], list[str]]] = {
    "manuale": (["read", "list", "search"], ["write", "edit", "move", "delete", "shell"]),
    "modifiche": (["read", "list", "search", "write", "edit"], ["move", "delete", "shell"]),
    "piano": (["read", "list", "search"], []),
    "auto": (["read", "list", "search", "write", "edit", "move", "delete", "shell"], []),
}
MODO_PREDEFINITO: Modo = "manuale"


def liste_modalita(modo: str) -> tuple[list[str], list[str]]:
    """Le due liste - silenziosi, con conferma - della modalita', o ValueError con i nomi validi."""
    if modo not in MODALITA:
        raise ValueError("modalita' sconosciuta: " + modo + ". Valide: " + ", ".join(MODALITA))
    return MODALITA[modo]


# Gli strumenti che lasciano una traccia sul disco o eseguono qualcosa: per
# definizione, quelli che `manuale` mette sotto conferma. Derivato dalla
# tabella e non scritto una seconda volta, cosi' un nome aggiunto li' entra
# anche qui. E' l'insieme che decide se una modalita' puo' girare senza
# nessuno che guardi: `ares -p` rifiuta quelle che ne mettono anche uno fra
# i silenziosi.
STRUMENTI_CON_TRACCIA = frozenset(MODALITA["manuale"][1])


def modalita_scrive_in_silenzio(modo: str) -> bool:
    """Vero se la modalita' scrive, sposta, cancella o esegue senza conferma."""
    silenziosi, _ = liste_modalita(modo)
    return not STRUMENTI_CON_TRACCIA.isdisjoint(silenziosi)


# Blocca la scrittura su un file esistente finche' l'agente non lo ha letto
# in questa sessione. E' la rete per il caso in cui il modello si immagini il
# contenuto di un file e lo riscriva da zero convinto di modificarlo.
WORKSPACE_READ_BEFORE_WRITE = True


# ---------------------------------------------------------------------------
# Politica della conversazione
# ---------------------------------------------------------------------------
#
# Il quarto asse risolto al confine del processo, dopo percorsi, modelli e
# identita'. I nomi qui sopra restano la sorgente - `.env` non c'entra, sono
# decisioni versionate - e `leggi_politica` li fotografa nei quattro gruppi
# che seguono. Le domande sono separate perche' lo sono le risposte: cosa
# Ares impara di te, quanto contesto storico vede, come si muove nella
# cartella, e cosa ti mostra di cio' che ha imparato.
#
# Perche' un oggetto e non quattro parametri sparsi: il prompt deve dire la
# verita' su cio' che il modello puo' fare - quali store si aggiornano da
# soli, quali strumenti esistono, se una scrittura in memoria verra'
# mostrata e potra' essere rifiutata - e quella verita' e' la stessa che
# governa il comportamento. Detta da un flag riletto a meta' turno poteva
# cambiare fra la costruzione dell'agente e il turno che lo usa.
#
# Restano fuori, e non per dimenticanza: `DATETIME_FORMAT`,
# `TOOL_RESULT_THRESHOLD_CHARS` con `OFFLOAD_TOOL_RESULTS`,
# `ENTITA_FINESTRA_RICERCA` e `CRONOLOGIA_RIGHE` sono configurazione
# dell'applicazione o del deposito dei risultati, non una politica che due
# conversazioni potrebbero volere diversa; `BACKUP_*`, `SESSIONI_PROTETTE` e
# `SESSION_RETENTION_DAYS` sono operazioni di manutenzione; `BASE_DIR`,
# `VECCHIO_*` e `DEFAULT_USER_ID` non sono politiche di nessuno.


@dataclass(frozen=True)
class Apprendimento:
    """Cosa Ares impara, da solo e su richiesta, e quanto costa.

    I nomi dicono la cosa, non il flag che li ha prodotti: `memorie_datate`
    e' la scelta di schema di `DATE_MEMORIE`, e `strumenti_memoria` e' la
    possibilita' di correggere a mano cio' che l'estrazione ha scritto.
    """

    profilo: bool
    memorie: bool
    contesto: bool
    entita: bool
    intuizioni: bool
    memorie_datate: bool
    max_aggiornamenti: int
    tentativi_contesto: int
    strumenti_memoria: bool

    @property
    def automatici(self) -> bool:
        """Vero se almeno uno store si aggiorna dopo ogni risposta, senza strumenti.

        E' il gruppo che paga una chiamata al modello per turno, ed e' anche
        quello che `descrizione` e il prompt nominano quando dicono che Ares
        impara da solo.
        """
        return self.profilo or self.memorie or self.contesto

    @property
    def agentici(self) -> bool:
        """Vero se almeno uno store si aggiorna solo quando il modello lo decide."""
        return self.entita or self.intuizioni


@dataclass(frozen=True)
class Cronologia:
    """Quanto contesto storico entra in vista, e quanto ne resta fuori.

    `sessioni_passate` e `cronologia_chat` accendono gli strumenti con cui il
    modello va a rileggere cio' che non ha in finestra; gli altri quattro
    numeri decidono quanto ne riceve senza chiedere - e due di loro
    difendono la finestra, non l'altezza del terminale: e' `Mostra.sessioni`
    a contare l'elenco di `/sessioni`.
    """

    sessioni_passate: bool
    cronologia_chat: bool
    turni: int
    strumenti_dalla_cronologia: int
    sessioni_ricerca: int
    sessioni_anteprima: int
    sessioni_nel_prompt: int


@dataclass(frozen=True)
class Workspace:
    """Come Ares lavora nella cartella, quando la cartella c'e'.

    La cartella in se' e' `Percorsi.lavoro`: qui sta solo la politica - se
    lo spazio di lavoro esiste, come si chiama il file delle regole, quanto
    ne entra nel prompt, con quale prefisso i suoi strumenti si distinguono
    dal quaderno, e se una scrittura su un file esistente pretende una
    lettura prima.
    """

    attivo: bool
    istruzioni: str
    istruzioni_max_byte: int
    prefisso: str
    leggi_prima_di_scrivere: bool


@dataclass(frozen=True)
class Mostra:
    """Cosa la persona vede del turno, e cosa le viene chiesto.

    `metriche` ed `esito_strumenti` sono pannelli; `apprendimenti` e
    `conferma_apprendimenti` non sono solo quello, perche' il prompt li
    nomina al modello: la riga che dice "la persona vede cosa entra in
    profilo e memorie" e' falsa se l'eco e' spenta, ed e' la stessa riga che
    le dice che un no riporta gli archivi indietro.
    """

    metriche: bool
    esito_strumenti: bool
    esito_righe: int
    esito_larghezza: int
    apprendimenti: bool
    conferma_apprendimenti: bool
    sessioni: int


@dataclass(frozen=True)
class Politica:
    """Cosa una conversazione impara, vede, mostra e come lavora.

    E' il quarto e ultimo asse risolto al confine del processo: `Percorsi`
    dice dove sta lo stato, `Impostazioni` a chi si parla, `Utente` per
    conto di chi, `Politica` cosa e' permesso e cosa si vede. Chi costruisce
    un agente riceve l'oggetto intero e non rilegge un nome di modulo, cosi'
    due conversazioni con politiche diverse sono due oggetti e non due
    mutazioni che il resto del processo non vede.
    """

    apprendimento: Apprendimento
    cronologia: Cronologia
    workspace: Workspace
    mostra: Mostra


def leggi_politica() -> Politica:
    """La politica di questa conversazione, fotografata quando serve.

    Come `leggi_impostazioni`, e per la stessa ragione: i nomi qui sopra
    restano la sorgente e questa funzione li copia in un oggetto immutabile
    al confine del processo - la chat e i suoi comandi nel proprio corpo,
    `inspect` all'inizio, le prove all'import dopo aver regolato i flag.

    Chi costruisce un agente riceve il risultato. Il giorno in cui due
    conversazioni vorranno politiche diverse - una senza apprendimento
    automatico, una sola lettura - costruiranno due `Politica` con
    `dataclasses.replace`, invece di riscrivere un nome che l'altra sta
    usando.
    """
    return Politica(
        apprendimento=Apprendimento(
            profilo=LEARN_USER_PROFILE,
            memorie=LEARN_USER_MEMORY,
            contesto=LEARN_SESSION_CONTEXT,
            entita=LEARN_ENTITIES,
            intuizioni=LEARN_KNOWLEDGE,
            memorie_datate=DATE_MEMORIE,
            max_aggiornamenti=MAX_UPDATES_PER_RUN,
            tentativi_contesto=SESSION_CONTEXT_RETRIES,
            strumenti_memoria=MEMORY_AGENT_TOOLS,
        ),
        cronologia=Cronologia(
            sessioni_passate=SEARCH_PAST_SESSIONS,
            cronologia_chat=READ_CHAT_HISTORY,
            turni=NUM_HISTORY_RUNS,
            strumenti_dalla_cronologia=MAX_TOOL_CALLS_FROM_HISTORY,
            sessioni_ricerca=PAST_SESSIONS_LIMIT,
            sessioni_anteprima=PAST_SESSION_RUNS_PREVIEW,
            sessioni_nel_prompt=SESSIONI_RECENTI_NEL_PROMPT,
        ),
        workspace=Workspace(
            attivo=WORKSPACE,
            istruzioni=WORKSPACE_ISTRUZIONI,
            istruzioni_max_byte=WORKSPACE_ISTRUZIONI_MAX_BYTE,
            prefisso=WORKSPACE_PREFIX,
            leggi_prima_di_scrivere=WORKSPACE_READ_BEFORE_WRITE,
        ),
        mostra=Mostra(
            metriche=MOSTRA_METRICHE,
            esito_strumenti=MOSTRA_ESITO_STRUMENTI,
            esito_righe=ESITO_RIGHE,
            esito_larghezza=ESITO_LARGHEZZA,
            apprendimenti=MOSTRA_APPRENDIMENTI,
            conferma_apprendimenti=CONFERMA_APPRENDIMENTI,
            sessioni=SESSIONI_ELENCO,
        ),
    )


# ---------------------------------------------------------------------------
# Identita'
# ---------------------------------------------------------------------------

# Il valore suggerito dalla CLI quando non si scrive `--user`, e nient'altro:
# non e' un percorso, non viene mai riassegnato e non decide dove si scrive.
# Chi lo usa lo passa a `Utente.da_grezzo`, che lo normalizza o lo rifiuta.
# L'identita' ha smesso di essere un campo di `Percorsi`: scegliere una
# cartella non cambia per conto di chi si parla.
DEFAULT_USER_ID: str = AMBIENTE.get("ARES_USER_ID", "default")


def comando_ares(*parole: str) -> str:
    """Il comando `ares` come lo si scrive da fuori dal venv, con le sue parole.

    `comando_ares("backup", "restore", nome)` da' `ares backup restore <nome>`
    se il setup ha messo `ares` sul PATH, altrimenti `.venv/bin/ares ...` su
    POSIX e `.venv\\Scripts\\ares ...` su Windows. Serve alle righe che
    suggeriscono un rimedio: la chat che ricorda il backup, il restore
    lasciato a meta', la manutenzione che dice da dove tornare.
    """
    nel_venv = r".venv\Scripts\ares" if os.name == "nt" else ".venv/bin/ares"
    eseguibile = "ares" if shutil.which("ares") else nel_venv
    return " ".join((eseguibile, *parole))
