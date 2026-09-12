"""Presentazione Rich della chat, separata dal motore conversazionale.

Questo modulo non conosce Agno, gli store o la configurazione dell'agente:
riceve testo gia' deciso da ``chat.py`` e lo rende. Tenere il confine qui
permette di cambiare tema e componenti senza toccare ``continue_run`` o il
percorso di apprendimento.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from threading import Event, RLock, Thread
from time import monotonic
from typing import Any

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.measure import Measurement
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

import ares

ACTIVITY_IDLE_SECONDS = 2.0
ACTIVITY_REFRESH_SECONDS = 0.125
PREVIEW_REFRESH_SECONDS = 0.125
ACTIVITY_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
PREVIEW_TAIL_CHARS = 240


ARES_THEME = Theme(
    {
        # I due accenti vengono dall'identita' visiva del repository: rame
        # caldo per Ares, ciano per l'attivita' locale e tecnica.
        "ares.title": "bold #f08068",
        "ares.accent": "#f08068",
        "ares.cyan": "#69ddea",
        # Il corpo segue il foreground del terminale: resta leggibile anche
        # con un tema chiaro. Solo gli accenti del marchio hanno colori fissi.
        "ares.text": "default",
        "ares.muted": "dim",
        "ares.border": "#6d3b32",
        "ares.success": "bold green3",
        "ares.warning": "bold yellow3",
        "ares.error": "bold red3",
        "ares.tool": "bold #69ddea",
    }
)


def _testo(valore: object, style: str | None = None) -> Text:
    """Testo letterale: ne' markup Rich, ne' controlli di terminale.

    Le parentesi quadre restano parentesi. I controlli ANSI vengono tolti,
    perche' non tutto cio' che passa di qui l'ha scritto Ares: il nome di
    uno strumento, i suoi argomenti nel pannello di conferma, l'anteprima
    di un risultato e le righe dell'eco arrivano dal modello o da un file
    del workspace. Rich lascia passare ``ESC`` intatto, e una sequenza in
    un argomento di conferma puo' cancellare la riga che chiede di
    confermarlo. Lo stream ha il proprio filtro perche' i frammenti
    arrivano spezzati; qui il testo e' intero e basta un passaggio.
    """
    return Text(_senza_controlli(str(valore)), style=style or "")


def byte_leggibili(byte: int) -> str:
    """`1.5 MiB` invece di `1572864`: per le tabelle, non per i dati.

    Base 1024 con il suffisso IEC, come nella riga delle metriche della chat:
    due convenzioni nella stessa CLI sono peggio di una qualunque delle due.
    """
    valore = float(byte)
    for unita in ("B", "KiB", "MiB", "GiB"):
        if valore < 1024 or unita == "GiB":
            return (str(int(valore)) if unita == "B" else format(valore, ".1f")) + " " + unita
        valore /= 1024
    return str(byte) + " B"


def _riga(console: Console, testo: Text) -> None:
    """Una riga di testo: a capo per parola sul terminale, intera in una pipe.

    Rich spezza a 80 colonne anche quando nessuno guarda, e una frase
    spezzata in due righe non si trova piu' con grep ne' con `in`. In una
    pipe la riga resta com'e', e a portarla a capo pensa chi la legge.
    """
    console.print(testo, soft_wrap=not console.is_terminal)


def _colonna(colonna: Colonna, indice: int) -> tuple[str, str | None, str]:
    """Nome, stile e allineamento di una colonna, con i default della tabella."""
    parti: tuple[str | None, ...] = (colonna,) if isinstance(colonna, str) else colonna
    nome = str(parti[0])
    stile = parti[1] if len(parti) > 1 else ("ares.cyan" if indice == 0 else "ares.text")
    allineamento = str(parti[2]) if len(parti) > 2 else "left"
    return nome, stile, allineamento


def _senza_controlli(valore: str) -> str:
    """Il testo senza sequenze ANSI ne' caratteri di controllo, in un colpo."""
    filtro = _FiltroControlliTerminale()
    pulito = filtro.feed(valore)
    filtro.finish()
    return pulito


class _FiltroControlliTerminale:
    """Filtra controlli ANSI conservando lo stato fra frammenti di stream.

    Un filtro applicato token per token puo' lasciar passare ``ESC [`` in un
    frammento e ``2J`` nel successivo. Ricomporre l'intera risposta evitava
    quel varco, ma obbligava ``Live`` a ridisegnarla tutta. Questo piccolo
    parser consuma invece CSI, OSC, DCS/SOS/PM/APC e sequenze ESC mentre
    arrivano; un comando non terminato viene scartato alla fine del turno.
    """

    TESTO = "testo"
    ESC = "esc"
    ESC_INTERMEDIO = "esc_intermedio"
    CSI = "csi"
    OSC = "osc"
    STRINGA = "stringa"
    OSC_ESC = "osc_esc"
    STRINGA_ESC = "stringa_esc"

    def __init__(self) -> None:
        self.stato = self.TESTO

    def feed(self, valore: str) -> str:
        uscita: list[str] = []
        for carattere in valore:
            codice = ord(carattere)

            if self.stato == self.TESTO:
                if carattere == "\x1b":
                    self.stato = self.ESC
                elif carattere == "\x9b":
                    self.stato = self.CSI
                elif carattere == "\x9d":
                    self.stato = self.OSC
                elif carattere in "\x90\x98\x9e\x9f":
                    self.stato = self.STRINGA
                elif carattere in "\n\t" or (codice >= 0x20 and not 0x7F <= codice <= 0x9F):
                    uscita.append(carattere)

            elif self.stato == self.ESC:
                if carattere == "[":
                    self.stato = self.CSI
                elif carattere == "]":
                    self.stato = self.OSC
                elif carattere in "P_X^":
                    self.stato = self.STRINGA
                elif 0x20 <= codice <= 0x2F:
                    self.stato = self.ESC_INTERMEDIO
                else:
                    # La sequenza ESC breve termina con questo carattere, che
                    # e' parte del comando e non del testo da mostrare.
                    self.stato = self.TESTO

            elif self.stato == self.ESC_INTERMEDIO:
                if 0x30 <= codice <= 0x7E:
                    self.stato = self.TESTO
                elif carattere == "\x1b":
                    self.stato = self.ESC

            elif self.stato == self.CSI:
                if 0x40 <= codice <= 0x7E:
                    self.stato = self.TESTO
                elif carattere == "\x1b":
                    # ESC cancella una CSI incompleta e ne apre una nuova.
                    self.stato = self.ESC

            elif self.stato == self.OSC:
                if carattere in "\x07\x9c":
                    self.stato = self.TESTO
                elif carattere == "\x1b":
                    self.stato = self.OSC_ESC

            elif self.stato == self.STRINGA:
                if carattere == "\x9c":
                    self.stato = self.TESTO
                elif carattere == "\x1b":
                    self.stato = self.STRINGA_ESC

            elif self.stato == self.OSC_ESC:
                if carattere == "\\":
                    self.stato = self.TESTO
                elif carattere != "\x1b":
                    self.stato = self.OSC

            elif self.stato == self.STRINGA_ESC:
                if carattere == "\\":
                    self.stato = self.TESTO
                elif carattere != "\x1b":
                    self.stato = self.STRINGA

        return "".join(uscita)

    def finish(self) -> None:
        # Un controllo incompleto non ha contenuto utile da recuperare. Lo
        # stato torna pulito per rendere l'istanza riusabile nei test.
        self.stato = self.TESTO


class RichRunStream:
    """Markdown persistente e anteprima TTY confinata a una sola riga.

    I frammenti non entrano nello scrollback mentre sono incompleti: vengono
    sanificati, accumulati e mostrati soltanto in un'anteprima transitoria.
    A un confine semantico (tool, errore o fine flusso) il buffer viene reso
    come Markdown una volta sola. Un resize puo' quindi ridisegnare al massimo
    la riga dell'anteprima, mai una risposta gia' stampata.

    Il Markdown va su `renderer.risposte`, tutto il resto - chi parla, gli
    strumenti, l'indicatore d'attesa - su `renderer.console`. Sono la stessa
    console salvo in `solo_risposte`, dove la seconda e' stderr.
    """

    def __init__(
        self,
        renderer: CliRenderer,
        *,
        clock: Callable[[], float] = monotonic,
        auto_activity: bool = True,
    ) -> None:
        self.renderer = renderer
        self.console = renderer.console
        self._filtro = _FiltroControlliTerminale()
        self._frammenti: list[str] = []
        self._preview_tail = ""
        self._clock = clock
        self._lock = RLock()
        self._activity_enabled = bool(self.console.is_terminal)
        self._auto_activity = auto_activity
        self._activity_done = Event()
        self._activity_thread: Thread | None = None
        self._activity_live: Live | None = None
        self._activity_waiting = False
        self._activity_label: str | None = None
        self._last_visible_at = 0.0
        self._last_preview_at: float | None = None
        self._activity_frame = 0

    def __enter__(self) -> RichRunStream:
        self.renderer.speaker("Ares", style="ares.title")
        if self._activity_enabled and self._auto_activity:
            self._activity_thread = Thread(target=self._activity_loop, daemon=True)
            self._activity_thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._activity_done.set()
        with self._lock:
            self._hide_activity_locked()
            self._activity_label = None
            self._filtro.finish()
            self._flush_content_locked()
        if self._activity_thread is not None:
            self._activity_thread.join(timeout=1.0)

    def _activity_renderable(self, *, waiting: bool) -> Text:
        anteprima = Text(no_wrap=True, overflow="crop", end="")
        if waiting:
            frame = ACTIVITY_FRAMES[self._activity_frame % len(ACTIVITY_FRAMES)]
            self._activity_frame += 1
            anteprima.append(frame, "ares.cyan")
            # L'etichetta dice cosa si sta aspettando: il modello, uno
            # strumento, l'estrazione delle memorie. Solo mentre si aspetta:
            # quando i frammenti scorrono, la coda dell'anteprima basta.
            if self._activity_label:
                anteprima.append(" " + self._activity_label, "ares.muted")
            if self._preview_tail:
                anteprima.append("  ")
        if self._preview_tail:
            anteprima.append("… " + self._preview_tail, "ares.muted")
        return anteprima

    def _hide_activity_locked(self) -> None:
        if self._activity_live is not None:
            self._activity_live.stop()
            self._activity_live = None
        self._activity_waiting = False

    def _show_activity_locked(self, *, waiting: bool) -> None:
        renderable = self._activity_renderable(waiting=waiting)
        if self._activity_live is None:
            self._activity_live = Live(
                renderable,
                console=self.console,
                auto_refresh=False,
                transient=True,
                vertical_overflow="crop",
                redirect_stdout=False,
                redirect_stderr=False,
            )
            self._activity_live.start(refresh=True)
        else:
            self._activity_live.update(renderable, refresh=True)
        self._activity_waiting = waiting
        self._last_preview_at = self._clock()

    def _flush_content_locked(self) -> None:
        if not self._frammenti:
            return
        contenuto = "".join(self._frammenti)
        self._frammenti.clear()
        self._preview_tail = ""
        risposte = self.renderer.risposte
        if risposte.is_terminal:
            risposte.print(Markdown(contenuto, code_theme="monokai", hyperlinks=False))
        else:
            # Una pipe conserva il sorgente Markdown, utile per log e file.
            # Si aggiunge soltanto il newline che la CLI usa per separare il
            # prompt successivo quando il modello non ne ha gia' prodotto uno.
            risposte.print(
                _testo(contenuto),
                end="" if contenuto.endswith("\n") else "\n",
                soft_wrap=True,
            )

    def _activity_loop(self) -> None:
        while not self._activity_done.wait(ACTIVITY_REFRESH_SECONDS):
            self.pulse_activity()

    def pulse_activity(self) -> None:
        """Aggiorna l'indicatore; pubblico solo per prove deterministiche."""
        if not self._activity_enabled:
            return
        with self._lock:
            if self._activity_label is None:
                return
            now = self._clock()
            if now - self._last_visible_at < ACTIVITY_IDLE_SECONDS:
                return
            self._show_activity_locked(waiting=True)

    def activity_started(self, label: str) -> None:
        with self._lock:
            self._hide_activity_locked()
            # Sono etichette decise dall'applicazione, ma il nome di un tool
            # puo' arrivare dal modello: nessuna newline deve spezzare
            # l'invariante di una sola riga del Live.
            self._activity_label = " ".join(str(label).split())
            now = self._clock()
            self._last_visible_at = now
            self._activity_frame = 0

    def activity_stopped(self) -> None:
        with self._lock:
            self._hide_activity_locked()
            self._activity_label = None

    def content(self, frammento: str) -> None:
        with self._lock:
            pulito = self._filtro.feed(frammento)
            if not pulito:
                return
            self._frammenti.append(pulito)
            coda = pulito.replace("\n", " ").replace("\t", " ")
            self._preview_tail = (self._preview_tail + coda)[-PREVIEW_TAIL_CHARS:]
            self._last_visible_at = self._clock()
            now = self._last_visible_at
            if self._activity_enabled and (
                self._activity_live is None
                or self._activity_waiting
                or self._last_preview_at is None
                or now - self._last_preview_at >= PREVIEW_REFRESH_SECONDS
            ):
                self._show_activity_locked(waiting=False)

    def flush(self) -> None:
        """Rende permanente il Markdown ricevuto fino a questo momento."""
        with self._lock:
            self._hide_activity_locked()
            # Un controllo ANSI non terminato non puo' attraversare un
            # confine semantico (tool, pausa o fine di una singola run) e
            # inghiottire il testo della continuazione successiva.
            self._filtro.finish()
            self._flush_content_locked()

    def above(self, renderable: RenderableType) -> None:
        with self._lock:
            self._hide_activity_locked()
            self._last_visible_at = self._clock()
            self._filtro.finish()
            self._flush_content_locked()
            self.console.print(renderable)

    def tool_started(self, nome: str) -> None:
        self.above(
            Text.assemble(
                ("  ◇ ", "ares.cyan"),
                (_senza_controlli(nome), "ares.tool"),
                ("  in esecuzione", "ares.muted"),
            )
        )

    def tool_result(self, righe: Iterable[str], *, errore: bool = False) -> None:
        stile_prima = "ares.error" if errore else "ares.success"
        rese = []
        for indice, riga in enumerate(righe):
            rese.append(_testo(riga, stile_prima if indice == 0 else "ares.muted"))
        if rese:
            self.above(Group(*rese))

    def run_error(self, messaggio: object) -> None:
        self.above(
            Text.assemble(
                ("Errore: ", "ares.error"),
                (_senza_controlli(str(messaggio or "senza messaggio")), "ares.text"),
            )
        )

    def cancelled(self) -> None:
        self.above(
            Group(
                _testo("Turno interrotto: la risposta e' parziale.", "ares.warning"),
                _testo("Resta in archivio come annullato, ma non e' stato appreso.", "ares.muted"),
            )
        )


# Una colonna di `CliRenderer.table`: il nome, lo stile delle celle e
# l'allineamento. Il nome da solo basta per una colonna di testo.
Colonna = str | tuple[str, str | None] | tuple[str, str | None, str]


class CliRenderer:
    """Componenti visuali piccoli, riusabili e sicuri per la REPL e i comandi."""

    def __init__(self, console: Console | None = None) -> None:
        # ``file=None`` fa seguire a Console il sys.stdout corrente: i test
        # che usano redirect_stdout continuano cosi' a catturare l'output.
        if console is None:
            self.console = Console(theme=ARES_THEME, highlight=False)
        else:
            self.console = console
            # Una Console iniettata (test, file o futura esportazione) non
            # conosce il tema costruito da CliRenderer. Applicarlo qui rende
            # i componenti indipendenti da come viene creato l'output.
            self.console.push_theme(ARES_THEME)
        # Gli errori dei comandi di manutenzione vanno su stderr, cosi' uno
        # script che legge stdout - o `--json` - non li trova in mezzo ai
        # dati. `stderr=True` segue il sys.stderr corrente come sopra.
        self.stderr = Console(theme=ARES_THEME, highlight=False, stderr=True)
        # Dove finisce la risposta del modello. E' la stessa console di tutto
        # il resto, salvo dentro `solo_risposte`.
        self.risposte = self.console

    @contextmanager
    def solo_risposte(self) -> Iterator[None]:
        """Su stdout solo la risposta del modello; tutto il resto su stderr.

        E' `ares -p`: chi legge stdout da uno script vuole la risposta, e ci
        trovava in mezzo la riga "Ares", gli strumenti chiamati, le
        anteprime dei loro esiti e le metriche. Per la durata del blocco
        `console` e' stderr e `risposte` resta stdout: ogni componente
        scrive come prima, cambia solo dove arriva. Vale anche con stdout su
        un terminale, perche' la regola non dipende da chi ascolta.
        """
        console, risposte = self.console, self.risposte
        self.risposte = self.console
        self.console = self.stderr
        try:
            yield
        finally:
            self.console, self.risposte = console, risposte

    def line(self, valore: object = "", *, style: str | None = None) -> None:
        _riga(self.console, _testo(valore, style))

    def err(self, valore: object = "", *, style: str | None = "ares.error") -> None:
        """Una riga su stderr; il rosso e' il default perche' quasi sempre e' un errore."""
        _riga(self.stderr, _testo(valore, style))

    def pair(self, chiave: str, valore: object, *, style: str | None = None) -> None:
        """`chiave: valore` su una riga, con la chiave attenuata.

        Resta una riga sola e non una tabella perche' `Sessioni: 2` deve
        potersi cercare con grep e leggersi anche in una pipe.
        """
        _riga(
            self.console,
            Text.assemble(
                (_senza_controlli(chiave) + ": ", "ares.muted"),
                (_senza_controlli(str(valore)), style or "ares.text"),
            ),
        )

    def table(self, colonne: Sequence[Colonna], righe: Iterable[Sequence[object]]) -> None:
        """Una tabella compatta: intestazione attenuata, prima colonna in ciano.

        Su un terminale una cella lunga va a capo dentro la propria colonna.
        In una pipe no: la console si allarga quanto serve alla tabella,
        perche' un nome di snapshot spezzato in tre righe non si puo' ne'
        leggere ne' cercare, e chi legge da una pipe e' quasi sempre un
        `grep` o un occhio che scorre un log.
        """
        tabella = Table(
            box=box.SIMPLE_HEAD,
            show_edge=False,
            pad_edge=False,
            header_style="ares.muted",
            collapse_padding=True,
        )
        for indice, colonna in enumerate(colonne):
            nome, stile, allineamento = _colonna(colonna, indice)
            tabella.add_column(_testo(nome), style=stile, justify=allineamento, overflow="fold")  # type: ignore[arg-type]
        for riga in righe:
            tabella.add_row(*(_testo(cella) for cella in riga))
        if self.console.is_terminal:
            self.console.print(tabella)
            return
        misura = Measurement.get(self.console, self.console.options.update(max_width=4000), tabella)
        larghezza = self.console.width
        self.console.width = max(larghezza, misura.maximum)
        try:
            self.console.print(tabella)
        finally:
            self.console.width = larghezza

    def json(self, dati: Any) -> None:
        """Dati per uno script, senza colori ne' a-capo di Rich.

        Passa dal file della console e non da `print`, cosi' segue la stessa
        redirezione di tutto il resto; `default=str` copre Path e datetime.
        """
        self.console.file.write(json.dumps(dati, ensure_ascii=False, indent=2, default=str) + "\n")
        self.console.file.flush()

    def blank(self) -> None:
        self.console.print()

    def lines(self, righe: Iterable[object], *, style: str | None = None) -> None:
        for riga in righe:
            self.line(riga, style=style)

    def heading(self, titolo: str) -> None:
        self.console.rule(_testo(titolo, "ares.title"), style="ares.border", align="left")

    def speaker(self, nome: str, *, style: str) -> None:
        self.console.print(_testo(nome, style))

    def banner(
        self,
        *,
        modello: str,
        sessione: str,
        utente: str,
        cartella: str | None = None,
        ramo: str | None = None,
        istruzioni: str | None = None,
        modo: str | None = None,
    ) -> None:
        """Il riquadro d'avvio. `cartella`, `ramo`, `istruzioni` e `modo` compaiono solo se ci sono.

        La cartella e' la prima riga dopo il modello perche' e' la cosa che
        cambia da un avvio all'altro, e la sola che, sbagliata, fa danni.
        """
        dati = Table.grid(padding=(0, 2))
        dati.add_column(style="ares.muted", no_wrap=True)
        dati.add_column(style="ares.text")
        dati.add_row(_testo("modello", "ares.muted"), _testo(modello, "ares.text"))
        if cartella is not None:
            # Un percorso non ha spazi: senza `fold` Rich lo troncherebbe
            # con i puntini, e un percorso a meta' non si controlla.
            dove = Text(cartella, style="ares.cyan", overflow="fold")
            if ramo:
                dove.append("  " + ramo, style="ares.muted")
            dati.add_row(_testo("cartella", "ares.muted"), dove)
        if istruzioni:
            dati.add_row(_testo("istruzioni", "ares.muted"), _testo(istruzioni, "ares.text"))
        if modo:
            stile = "ares.warning" if modo == "auto" else "ares.text"
            dati.add_row(_testo("modalita'", "ares.muted"), _testo(modo, stile))
        dati.add_row(_testo("sessione", "ares.muted"), _testo(sessione, "ares.text"))
        dati.add_row(_testo("utente", "ares.muted"), _testo(utente, "ares.text"))
        corpo = Group(
            Text.assemble(("ARES", "ares.title"), ("  " + ares.__version__ + "  local-first AI agent", "ares.muted")),
            Text(""),
            dati,
            Text(""),
            _testo(
                "/ apre i comandi · Alt+Invio va a capo · Ctrl-C interrompe",
                "ares.muted",
            ),
        )
        self.console.print(
            Panel(
                corpo,
                border_style="ares.border",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def help(self, comandi: Sequence[tuple]) -> None:
        tabella = Table(
            box=box.SIMPLE,
            show_header=False,
            padding=(0, 2),
            collapse_padding=True,
        )
        tabella.add_column(style="ares.cyan", no_wrap=True)
        tabella.add_column(style="ares.text")
        for nome, _alias, descrizione, _funzione in comandi:
            tabella.add_row(_testo(nome, "ares.cyan"), _testo(descrizione, "ares.text"))
        self.heading("Comandi")
        self.console.print(tabella)
        self.line(
            "Lo slash apre l'elenco, il TAB completa; basta l'inizio finche' resta unico.",
            style="ares.muted",
        )

    def command_problem(self, righe: Iterable[str]) -> None:
        for indice, riga in enumerate(righe):
            self.line(riga, style="ares.warning" if indice == 0 else "ares.muted")

    def confirmation(self, righe: Sequence[str]) -> None:
        contenuto = Group(
            *[_testo(riga, "ares.warning" if indice == 0 else "ares.text") for indice, riga in enumerate(righe)]
        )
        self.console.print(
            Panel(
                contenuto,
                title=_testo("Autorizzazione richiesta", "ares.warning"),
                title_align="left",
                border_style="yellow3",
                box=box.ROUNDED,
                padding=(0, 1),
            )
        )

    def metrics(self, riga: str) -> None:
        self.line(riga, style="ares.muted")

    def learned(self, righe: Iterable[str]) -> None:
        """Cosa e' entrato in memoria: la sintesi in evidenza, il testo attenuato.

        Fuori dallo stream, perche' arriva quando il turno e' gia' chiuso e
        l'estrazione ha finito di scrivere. Stessa forma dell'esito di uno
        strumento: e' l'esito di un'operazione che nessuno ha chiamato.
        """
        for indice, riga in enumerate(righe):
            self.line(riga, style="ares.success" if indice == 0 else "ares.muted")

    def stream(self) -> RichRunStream:
        return RichRunStream(self)


UI = CliRenderer()


def stampa_store(store: Any, etichetta: str, **filtri: Any) -> None:
    """Stampa uno store, o dice che e' spento invece di sollevare AttributeError.

    Gli store spenti in `config.py` non sono None per errore: la
    LearningMachine non li costruisce affatto, e `lm.user_profile_store`
    restituisce None. Chiamarci `.print()` sopra faceva morire `/profilo` con
    un AttributeError, e `config.py` invita esplicitamente a spegnerli per
    guadagnare latenza. La guardia sta qui e non nei due lettori perche' li'
    sarebbe scritta due volte, ed e' gia' successo con `/entita`. Sta in
    `cli/ui.py` e non in `state/stores.py` perche' stampa: `state/` legge e
    non deve importare l'interfaccia.
    """
    if store is None:
        UI.line(etichetta + ": store spento in config.py", style="ares.muted")
        return
    store.print(**filtri)


# Campi che il framework mette e toglie da solo. Restano fuori dalla ricerca:
# un fatto porta un `id` e due date, e cercarci dentro vuol dire che "2026"
# trova ogni entita' scritta quest'anno.
CONTABILITA = ("id", "created_at", "updated_at")
