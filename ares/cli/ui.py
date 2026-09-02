"""Presentazione Rich della chat, separata dal motore conversazionale.

Questo modulo non conosce Agno, gli store o la configurazione dell'agente:
riceve testo gia' deciso da ``chat.py`` e lo rende. Tenere il confine qui
permette di cambiare tema e componenti senza toccare ``continue_run`` o il
percorso di apprendimento.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from threading import Event, RLock, Thread
from time import monotonic

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
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
    """Testo letterale: parentesi quadre e path non diventano markup Rich."""
    return Text(str(valore), style=style or "")


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
        if self.console.is_terminal:
            self.console.print(Markdown(contenuto, code_theme="monokai", hyperlinks=False))
        else:
            # Una pipe conserva il sorgente Markdown, utile per log e file.
            # Si aggiunge soltanto il newline che la CLI usa per separare il
            # prompt successivo quando il modello non ne ha gia' prodotto uno.
            self.console.print(
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
                (nome, "ares.tool"),
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
                (str(messaggio or "senza messaggio"), "ares.text"),
            )
        )

    def cancelled(self) -> None:
        self.above(
            Group(
                _testo("Turno interrotto: la risposta e' parziale.", "ares.warning"),
                _testo("Resta in archivio come annullato, ma non e' stato appreso.", "ares.muted"),
            )
        )


class CliRenderer:
    """Componenti visuali piccoli, riusabili e sicuri per la REPL."""

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

    def line(self, valore: object = "", *, style: str | None = None) -> None:
        self.console.print(_testo(valore, style))

    def blank(self) -> None:
        self.console.print()

    def lines(self, righe: Iterable[object], *, style: str | None = None) -> None:
        for riga in righe:
            self.line(riga, style=style)

    def heading(self, titolo: str) -> None:
        self.console.rule(_testo(titolo, "ares.title"), style="ares.border", align="left")

    def speaker(self, nome: str, *, style: str) -> None:
        self.console.print(_testo(nome, style))

    def banner(self, *, modello: str, sessione: str, utente: str) -> None:
        dati = Table.grid(padding=(0, 2))
        dati.add_column(style="ares.muted", no_wrap=True)
        dati.add_column(style="ares.text")
        dati.add_row(_testo("modello", "ares.muted"), _testo(modello, "ares.text"))
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

    def stream(self) -> RichRunStream:
        return RichRunStream(self)


UI = CliRenderer()
