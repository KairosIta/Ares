"""Configurazione centrale di Ares.

I valori predefiniti sono orientati a un host locale con circa 16 GiB di
VRAM. Modello, contesto, percorsi e identita' possono essere adattati qui o,
quando previsto, tramite variabili d'ambiente.

Importare questo modulo non tocca il disco: legge `.env` e definisce nomi.
La directory dello stato la crea `prepara_archivio()`, che chiama chi
l'archivio lo apre davvero.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from platform_files import rendi_privato

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# ---------------------------------------------------------------------------
# Modelli
# ---------------------------------------------------------------------------

# Agente principale: 9B Q8_0 con supporto per tools, thinking e vision.
MAIN_MODEL = "hf.co/empero-ai/Qwythos-9B-Claude-Mythos-5-1M-GGUF:Q8_0"

# Modello per l'estrazione delle memorie. Tenerlo uguale a MAIN_MODEL evita
# lo swap dei pesi tra la risposta e la fase di apprendimento.
LEARNING_MODEL = MAIN_MODEL

# Embedder unico per le collezioni LanceDB. Indici e query devono usare lo
# stesso modello e la stessa dimensionalita'.
EMBEDDER_MODEL = "nomic-embed-text-v2-moe"
EMBEDDER_DIMENSIONS = 768

# ---------------------------------------------------------------------------
# Tuning Ollama
# ---------------------------------------------------------------------------

# Server Ollama. Va passato esplicitamente e non lasciato al default: se
# OLLAMA_API_KEY e' presente nell'ambiente e host non e' impostato, Agno
# dirotta le chiamate su https://ollama.com. Un progetto che promette che
# nessun dato esce dalla macchina non puo' dipendere da una variabile
# d'ambiente per mantenere la promessa.
OLLAMA_HOST = "http://localhost:11434"

# Ollama usa 4096 token di contesto se non gli dici altro. Un agente con
# tool, memorie iniettate e cronologia lo satura entro pochi turni, e il
# troncamento silenzioso si manifesta come "l'agente ha dimenticato".
#
# Il valore predefinito sfrutta il contesto esteso del modello. Su hardware
# con meno memoria va ridotto insieme alla taglia o alla quantizzazione.
NUM_CTX = 262144

# Mantiene i pesi in VRAM tra un turno e l'altro. Senza questo Ollama
# scarica il modello dopo 5 minuti e il turno successivo paga il ricaricamento.
KEEP_ALIVE = "30m"

TEMPERATURE = 0.7

OLLAMA_OPTIONS = {
    "num_ctx": NUM_CTX,
    "temperature": TEMPERATURE,
}

# Il modello ragiona prima di rispondere e Ollama glielo accende di default.
# `think` e' un parametro top-level dell'API. Resta attivo per la risposta e
# viene disattivato per le estrazioni strutturate, dove aggiungerebbe latenza.
MAIN_THINK = True
LEARNING_THINK = False

# Estrazione memorie: temperatura bassa, perche' e' un compito di
# trascrizione strutturata e non di conversazione.
#
# Il contesto resta identico a quello dell'agente: con lo stesso modello un
# `num_ctx` diverso puo' costringere Ollama a ricaricare l'istanza.
LEARNING_OPTIONS = {
    "num_ctx": NUM_CTX,
    "temperature": 0.2,
}

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
# che non ti serve.
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
# incancellabile. La sessione predefinita e' il solo valore protetto di serie.
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
# Percorsi
# ---------------------------------------------------------------------------

# Dove vive lo stato appreso. ARES_TMP lo sposta altrove, e serve alle prove
# che devono girare su un archivio usa-e-getta. Va letta qui e non nei singoli
# percorsi: e' una decisione sola.
TMP_DIR = Path(os.environ.get("ARES_TMP") or BASE_DIR / "tmp")
# Il nome del file conserva quello che il progetto aveva prima del rilascio
# pubblico, mentre le classi sono state rinominate. Non e' una svista: questo
# nome sta nella tupla `DATABASE` di backup_integrity.py e quindi nell'insieme di file
# che `verifica_snapshot` pretende, cioe' dentro ogni snapshot gia' creato.
# Cambiarlo e' una migrazione con bump di FORMATO_BACKUP e lettura di
# entrambi i nomi al restore, non una rinomina - e il nome non arriva mai
# all'utente.
DB_FILE = str(TMP_DIR / "kairos.db")
FS_DB_FILE = str(TMP_DIR / "filesystem.db")
LANCEDB_URI = str(TMP_DIR / "lancedb")


def prepara_archivio() -> Path:
    """Crea la directory dello stato, privata, e restituisce il percorso.

    Sta qui e non nel corpo del modulo perche' importare una configurazione
    non deve produrre effetti: `preflight.py` importa `config` per tre nomi di
    modello e non ha alcun motivo di lasciarsi dietro un archivio, e nemmeno
    `--help`. Chi apre l'archivio la chiama, ed e' idempotente: i tre
    costruttori di `assistant.py`, e il `main()` dei comandi che l'archivio lo
    toccano - `backup.py` no, legge tmp/ e sa dire che non c'e'. Nei comandi
    la chiamata va **dopo** `parse_args()`, perche' `--help` esce li' in
    mezzo. Piu' punti dello stretto necessario, di proposito: il costo di una
    chiamata in piu' e' zero, quello di una dimenticata e' un archivio
    leggibile da chiunque.

    I permessi si applicano alla directory e non ai file che contiene, perche'
    la directory e' il confine che regge davvero: senza il diritto di
    attraversarla i modi dei singoli database non si raggiungono, e quei modi
    li decide la umask di chi apre il file, non questo progetto. Qui dentro
    c'e' tutto cio' che e' stato detto ad Ares: la cronologia accanto nasce
    gia' a 0600 e gli snapshot a 0700/0600, questa riga toglie l'asimmetria
    per cui la copia era privata e l'originale no. Su Windows non fa nulla,
    come ovunque nel progetto: li' vale la DACL ereditata.
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    rendi_privato(TMP_DIR)
    return TMP_DIR


# Snapshot locali dello stato appreso. Fuori da tmp/, perche' un backup dentro
# cio' che deve salvare verrebbe copiato ricorsivamente e sparirebbe insieme
# all'originale; fuori anche dal workspace, che Ares puo' modificare. La
# variabile d'ambiente rende le prove interamente usa-e-getta.
BACKUP_DIR = Path(os.environ.get("ARES_BACKUP_DIR") or BASE_DIR.parent / "ares-backup")

# Lock fratello di tmp/, non al suo interno: il restore sostituisce l'intera
# directory dello stato e il file che coordina l'operazione deve restare fermo.
STATE_LOCK_FILE = TMP_DIR.with_name(TMP_DIR.name + ".lock")

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

# Sta qui e non accanto alle altre impostazioni della REPL perche' e' un
# percorso, e i percorsi si decidono dopo TMP_DIR.
#
# Dentro tmp/ per due motivi: le prove sono gia' isolate - ognuna sposta
# ARES_TMP prima di importare config, quindi nessuna puo' scrivere nella
# cronologia vera - e `backup.py` la copia negli snapshot insieme al resto.
# Un restore pero' non la riavvolge: riporta indietro Ares, non chi gli parla,
# e quella dello snapshot torna solo se tmp/ e' andata persa davvero.
# Contiene tutto cio' che si e' scritto ad Ares. `CronologiaSicura` lo crea a
# 0600 su POSIX (su Windows conserva la DACL ereditata), tiene una voce JSON
# per messaggio anche multilinea e coordina con un lock breve le chat aperte
# insieme. Il vecchio formato GNU Readline viene riletto e migrato alla prima
# nuova voce.
CRONOLOGIA_FILE = TMP_DIR / "cronologia_chat.txt"

# Un tetto perche' un file che cresce e basta e' esattamente cio' che questo
# progetto conta altrove. Il backend lo applica atomicamente a ogni nuova
# voce, tenendo la coda piu' recente.
CRONOLOGIA_RIGHE = 2000

# ---------------------------------------------------------------------------
# Spazio di lavoro sul disco
# ---------------------------------------------------------------------------

# Una directory sola, fuori dal progetto e fuori da tmp/, dove Ares puo'
# clonare repository e lavorarci. Sta accanto al progetto invece che dentro
# per due motivi: cio' che ci finisce non e' codice di questo repo e non deve
# comparire in `git status`, e un agente che puo' scrivere nella directory in
# cui vive puo' riscrivere se stesso.
#
# Il confine e' quello che Agno chiama, nel docstring di Workspace, "a
# path-scoping boundary, not a process sandbox": gli strumenti sui file non
# escono da qui, ma `run_command` esegue sulla macchina vera e puo' uscirne,
# leggere l'ambiente, aprire la rete. Cio' che regge il confine e' la
# conferma umana, non il codice: per questo la shell sta fra le azioni da
# confermare e non fra quelle libere.
WORKSPACE = True
WORKSPACE_DIR = Path(os.environ.get("ARES_WORKSPACE") or BASE_DIR.parent / "ares-lavoro")

# Il prefisso non e' cosmetico. Il FileSystem privato espone gia' read_file,
# write_file, list_files, move_file e search_content: registrando Workspace
# accanto, Agno scarta cinque strumenti su otto con un WARNING e tiene i
# primi arrivati. Il modello vedrebbe un `read_file` che crede legga il disco
# e che legge invece il quaderno nel database. Rinominare e' l'unico modo per
# tenere le due superfici distinte, ed e' il verso giusto: le istruzioni del
# FileSystem le scrive Agno e nominano i propri strumenti, quelle di qui le
# scriviamo noi.
WORKSPACE_PREFIX = "workspace_"

# Due liste che si escludono: cio' che e' in `allowed` gira in silenzio, cio'
# che e' in `confirm` mette il turno in pausa e aspetta un si', cio' che non
# e' in nessuna delle due non viene nemmeno mostrato al modello.
#
# La riga di confine e' "distruttivo o pericoloso": scrivere un file nuovo
# nella propria directory non lo e', cancellare e spostare lo sono, e la
# shell lo e' sempre perche' e' l'unico strumento che esce dal recinto.
# L'unica scrittura davvero distruttiva - sovrascrivere un file che esiste -
# la copre WORKSPACE_READ_BEFORE_WRITE qui sotto.
#
WORKSPACE_ALLOWED = ["read", "list", "search", "write", "edit"]
WORKSPACE_CONFIRM = ["move", "delete", "shell"]

# Blocca la scrittura su un file esistente finche' l'agente non lo ha letto
# in questa sessione. E' la rete per il caso in cui il modello si immagini il
# contenuto di un file e lo riscriva da zero convinto di modificarlo.
WORKSPACE_READ_BEFORE_WRITE = True

# ---------------------------------------------------------------------------
# Identita'
# ---------------------------------------------------------------------------

DEFAULT_USER_ID = os.environ.get("ARES_USER_ID", "default")
