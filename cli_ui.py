"""Presentazione Rich della chat, separata dal motore conversazionale.

Questo modulo non conosce Agno, gli store o la configurazione dell'agente:
riceve testo gia' deciso da ``chat.py`` e lo rende. Tenere il confine qui
permette di cambiare tema e componenti senza toccare ``continue_run`` o il
percorso di apprendimento.
"""

from __future__ import annotations

import re
from time import monotonic
from typing import Callable, Iterable, Optional, Sequence

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme


LIVE_REFRESH_PER_SECOND = 8
LIVE_REFRESH_INTERVAL = 1 / LIVE_REFRESH_PER_SECOND

# Il testo del modello puo' contenere controlli terminali, anche divisi fra
# piu' eventi di streaming. Prima si eliminano le stringhe OSC/DCS complete,
# poi CSI, sequenze ESC brevi e ogni controllo C0/C1 rimasto. Newline e TAB
# sono gli unici controlli utili nel Markdown e vengono preservati.
_STRINGHE_TERMINALE = re.compile(
    r"(?:\x1b\]|\x9d).*?(?:\x07|\x1b\\|\x9c)"
    r"|(?:\x1b[P_X^]|[\x90\x98\x9e\x9f]).*?(?:\x1b\\|\x9c)",
    re.DOTALL,
)
_CSI_TERMINALE = re.compile(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]")
_ESC_TERMINALE = re.compile(r"\x1b(?:[ -/]*[0-~])?")
_CONTROLLI_TERMINALE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


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


def _testo(valore: object, style: Optional[str] = None) -> Text:
    """Testo letterale: parentesi quadre e path non diventano markup Rich."""
    return Text(str(valore), style=style)


def _senza_controlli_terminale(valore: str) -> str:
    """Rimuove comandi terminali senza alterare il Markdown ordinario."""
    valore = _STRINGHE_TERMINALE.sub("", valore)
    valore = _CSI_TERMINALE.sub("", valore)
    valore = _ESC_TERMINALE.sub("", valore)
    return _CONTROLLI_TERMINALE.sub("", valore)


class RichRunStream:
    """Una risposta Agno resa dal vivo, con una via semplice per pipe e test.

    Su un terminale vero ``Live`` ridisegna il Markdown accumulato; quando
    stdout e' rediretto si scrivono invece i frammenti letterali. In questo
    modo log, test e pipe non ricevono sequenze di controllo o copie ripetute
    della stessa risposta.
    """

    def __init__(
        self,
        renderer: "CliRenderer",
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self.renderer = renderer
        self.console = renderer.console
        self._frammenti: list[str] = []
        self._clock = clock
        self._ultimo_render: Optional[float] = None
        self.live: Optional[Live] = None
        self._plain_line_open = False

    def __enter__(self) -> "RichRunStream":
        self.renderer.speaker("Ares", style="ares.title")
        if self.console.is_terminal:
            attesa = Spinner("dots", _testo("Ares sta pensando...", "ares.muted"))
            self.live = Live(
                attesa,
                console=self.console,
                refresh_per_second=LIVE_REFRESH_PER_SECOND,
                vertical_overflow="ellipsis",
            )
            self.live.start(refresh=True)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.live is not None:
            finale: RenderableType = self._markdown() if self._frammenti else Text("")
            self.live.update(finale, refresh=True)
            self.live.stop()
        else:
            self.console.print()

    def _markdown(self) -> Markdown:
        # Il Markdown arriva dal modello e non dal markup Rich: stringhe come
        # ``[red]`` restano contenuto, mentre i blocchi di codice ricevono
        # l'evidenziazione di Pygments gia' installato con Rich. Il buffer si
        # ricompone prima della pulizia, cosi' una sequenza ESC divisa fra due
        # frammenti non puo' attraversare il confine e raggiungere il terminale.
        contenuto = _senza_controlli_terminale("".join(self._frammenti))
        return Markdown(contenuto, code_theme="monokai", hyperlinks=False)

    def content(self, frammento: str) -> None:
        self._frammenti.append(frammento)
        if self.live is not None:
            adesso = self._clock()
            if (
                self._ultimo_render is None
                or adesso - self._ultimo_render >= LIVE_REFRESH_INTERVAL
            ):
                self.live.update(self._markdown(), refresh=False)
                # Si misura dalla fine del parsing: se una risposta enorme
                # richiede gia' piu' di un intervallo, il frammento successivo
                # non deve innescare immediatamente un altro giro completo.
                self._ultimo_render = self._clock()
        else:
            # Text evita di interpretare come markup una risposta destinata a
            # una pipe. ``soft_wrap`` conserva lo stesso flusso del print
            # precedente e non inserisce tagli nel testo catturato.
            self.console.print(_testo(frammento), end="", soft_wrap=True)
            if frammento:
                self._plain_line_open = not frammento.endswith("\n")

    def above(self, renderable: RenderableType) -> None:
        if self.live is not None:
            self.live.console.print(renderable)
        else:
            if self._plain_line_open:
                self.console.print()
                self._plain_line_open = False
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

    def __init__(self, console: Optional[Console] = None) -> None:
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

    def line(self, valore: object = "", *, style: Optional[str] = None) -> None:
        self.console.print(_testo(valore, style))

    def blank(self) -> None:
        self.console.print()

    def lines(self, righe: Iterable[object], *, style: Optional[str] = None) -> None:
        for riga in righe:
            self.line(riga, style=style)

    def heading(self, titolo: str) -> None:
        self.console.rule(_testo(titolo, "ares.title"), style="ares.border", align="left")

    def speaker(self, nome: str, *, style: str) -> None:
        self.console.print(_testo(nome, style))

    def prompt(self) -> str:
        invito = Text.assemble(("Tu", "bold #69ddea"), (" › ", "ares.muted"))
        return self.console.input(invito)

    def ask(self, etichetta: str, *, muted: bool = False) -> str:
        stile = "ares.muted" if muted else "ares.warning"
        return self.console.input(_testo(etichetta, stile))

    def banner(self, *, modello: str, sessione: str, utente: str) -> None:
        dati = Table.grid(padding=(0, 2))
        dati.add_column(style="ares.muted", no_wrap=True)
        dati.add_column(style="ares.text")
        dati.add_row(_testo("modello", "ares.muted"), _testo(modello, "ares.text"))
        dati.add_row(_testo("sessione", "ares.muted"), _testo(sessione, "ares.text"))
        dati.add_row(_testo("utente", "ares.muted"), _testo(utente, "ares.text"))
        corpo = Group(
            Text.assemble(("ARES", "ares.title"), ("  local-first AI agent", "ares.muted")),
            Text(""),
            dati,
            Text(""),
            _testo("/ apre i comandi · Ctrl-C interrompe il turno", "ares.muted"),
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
            *[
                _testo(riga, "ares.warning" if indice == 0 else "ares.text")
                for indice, riga in enumerate(righe)
            ]
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
