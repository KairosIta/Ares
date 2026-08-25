"""Input interattivo della REPL di Ares, separato dal renderer Rich.

Rich disegna risposte e pannelli; Prompt Toolkit possiede il terminale solo
mentre l'utente scrive. Il confine evita che il ridisegno di un prompt possa
interferire con lo streaming Agno e permette un fallback pulito per pipe e
test non interattivi.
"""

from __future__ import annotations

import builtins
import fcntl
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Sequence

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, CompleteEvent, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.history import History, InMemoryHistory
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.output import Output
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.styles import Style


CRONOLOGIA_INTESTAZIONE = "# Ares CLI history v2"


class CronologiaSicura(History):
    """Cronologia multilinea, limitata e coordinata fra piu' processi.

    Una voce e' una stringa JSON su una sola riga fisica: anche un messaggio
    multilinea viene scritto con una sola sostituzione atomica del file. Il
    lock e' separato dal lock dello stato, che la chat mantiene condiviso per
    tutta la propria vita e che qui non potrebbe essere promosso a esclusivo.
    """

    def __init__(self, percorso: Path, limite: int) -> None:
        super().__init__()
        self.percorso = Path(percorso)
        self.limite = max(0, int(limite))
        self.lock_file = self.percorso.with_name("." + self.percorso.name + ".lock")
        self.percorso.parent.mkdir(parents=True, exist_ok=True)
        with self._lock():
            if not self.percorso.exists():
                self._scrivi([])
            else:
                os.chmod(self.percorso, 0o600)

    @contextmanager
    def _lock(self) -> Iterator[None]:
        descrittore = os.open(self.lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        bloccato = False
        try:
            os.fchmod(descrittore, 0o600)
            fcntl.flock(descrittore, fcntl.LOCK_EX)
            bloccato = True
            yield
        finally:
            if bloccato:
                fcntl.flock(descrittore, fcntl.LOCK_UN)
            os.close(descrittore)

    def _leggi(self) -> list[str]:
        if not self.percorso.exists():
            return []
        os.chmod(self.percorso, 0o600)
        with self.percorso.open("r", encoding="utf-8", errors="replace") as file:
            righe = file.read().splitlines()
        if not righe:
            return []

        if righe[0] != CRONOLOGIA_INTESTAZIONE:
            # Formato GNU Readline precedente: una domanda per riga. Viene
            # migrato alla prima scrittura, senza perdere la cronologia gia'
            # presente in un'installazione aggiornata.
            return [riga for riga in righe if riga]

        voci = []
        for riga in righe[1:]:
            try:
                voce = json.loads(riga)
            except (json.JSONDecodeError, TypeError):
                # Una coda troncata non rende illeggibile tutto cio' che la
                # precede. La prossima scrittura atomica la eliminera'.
                continue
            if isinstance(voce, str) and voce:
                voci.append(voce)
        return voci

    def _scrivi(self, voci: Sequence[str]) -> None:
        descrittore, temporaneo = tempfile.mkstemp(
            prefix="." + self.percorso.name + ".",
            dir=self.percorso.parent,
        )
        try:
            os.fchmod(descrittore, 0o600)
            file = os.fdopen(descrittore, "w", encoding="utf-8")
            descrittore = -1
            with file:
                file.write(CRONOLOGIA_INTESTAZIONE + "\n")
                for voce in voci:
                    file.write(json.dumps(voce, ensure_ascii=False) + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporaneo, self.percorso)
            os.chmod(self.percorso, 0o600)
        finally:
            if descrittore >= 0:
                os.close(descrittore)
            try:
                os.unlink(temporaneo)
            except FileNotFoundError:
                pass

    def load_history_strings(self) -> Iterable[str]:
        with self._lock():
            voci = self._leggi()
        if self.limite:
            voci = voci[-self.limite :]
        else:
            voci = []
        return reversed(voci)

    def append_string(self, string: str) -> None:
        if not string:
            return
        if self._loaded_strings and self._loaded_strings[0] == string:
            return
        self._loaded_strings.insert(0, string)
        if self.limite:
            del self._loaded_strings[self.limite :]
        self.store_string(string)

    def store_string(self, string: str) -> None:
        if not string or not self.limite:
            return
        with self._lock():
            voci = self._leggi()
            if not voci or voci[-1] != string:
                voci.append(string)
            self._scrivi(voci[-self.limite :])


class CompletamentoComandi(Completer):
    """Mostra i comandi canonici e la loro descrizione digitando ``/``."""

    def __init__(self, comandi: Sequence[tuple[str, str]]) -> None:
        self.comandi = tuple(comandi)

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        prima = document.text_before_cursor
        if "\n" in prima or not prima.startswith("/") or " " in prima:
            return
        for nome, descrizione in self.comandi:
            if nome.startswith(prima) and nome != prima:
                yield Completion(
                    nome,
                    start_position=-len(prima),
                    display=nome,
                    display_meta=descrizione,
                )


class LexerInputAres(Lexer):
    """Evidenzia soltanto il comando locale, lasciando letterale il testo."""

    def lex_document(self, document: Document) -> Callable[[int], StyleAndTextTuples]:
        def get_line(numero: int) -> StyleAndTextTuples:
            riga = document.lines[numero]
            if numero == 0 and riga.startswith("/"):
                comando, separatore, argomento = riga.partition(" ")
                frammenti: StyleAndTextTuples = [("class:input.command", comando)]
                if separatore:
                    frammenti.append(("class:input.text", separatore + argomento))
                return frammenti
            return [("class:input.text", riga)]

        return get_line


ARES_INPUT_STYLE = Style.from_dict(
    {
        "prompt.user": "bold #69ddea",
        "prompt.marker": "#6d3b32",
        "prompt.ask": "bold ansiyellow",
        "prompt.ask-muted": "ansibrightblack",
        "prompt.continuation": "#6d3b32",
        "input.command": "bold #69ddea",
        "input.text": "",
        "placeholder": "italic ansibrightblack",
        "auto-suggestion": "italic ansibrightblack",
        "completion-menu.completion": "bg:#202020 #e6e6e6",
        "completion-menu.completion.current": "bold bg:#6d3b32 #ffffff",
        "completion-menu.meta.completion": "bg:#202020 #9a9a9a",
        "completion-menu.meta.completion.current": "bg:#6d3b32 #f4d4ce",
        "bottom-toolbar": "noreverse ansibrightblack",
    }
)


def _tasti_chat(completer: CompletamentoComandi) -> KeyBindings:
    tasti = KeyBindings()

    @tasti.add("enter", eager=True)
    def _invia(event) -> None:
        buffer = event.current_buffer
        stato = buffer.complete_state
        if stato is not None and stato.current_completion is not None:
            # TAB seleziona una voce del menu: Invio la applica e spedisce il
            # comando nello stesso gesto, invece di richiedere un secondo
            # Invio che in una chat sembra non aver funzionato.
            buffer.apply_completion(stato.current_completion)
        buffer.validate_and_handle()

    @tasti.add("escape", "enter", eager=True)
    def _a_capo(event) -> None:
        event.current_buffer.insert_text("\n")

    @tasti.add("c-space")
    def _completa(event) -> None:
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.complete_next()
        else:
            buffer.start_completion(select_first=False)

    @tasti.add("c-i", eager=True)
    def _prossimo_completamento(event) -> None:
        buffer = event.app.current_buffer
        candidati = list(
            completer.get_completions(
                buffer.document,
                CompleteEvent(completion_requested=True),
            )
        )
        if len(candidati) == 1:
            buffer.apply_completion(candidati[0])
            return
        if buffer.complete_state:
            buffer.complete_next()
        else:
            buffer.start_completion(select_first=True)

    @tasti.add("s-tab", eager=True)
    def _completamento_precedente(event) -> None:
        buffer = event.app.current_buffer
        if buffer.complete_state:
            buffer.complete_previous()
        else:
            buffer.start_completion(select_first=True)

    return tasti


def _continuazione(
    larghezza: int,
    _numero: int,
    _soft_wrap: bool,
) -> StyleAndTextTuples:
    prefisso = " " * max(0, larghezza - 2) + "· "
    return [("class:prompt.continuation", prefisso)]


class CliInput:
    """Prompt persistente per la chat e prompt effimeri per le conferme."""

    def __init__(
        self,
        *,
        comandi: Sequence[tuple[str, str]],
        cronologia_file: Path,
        cronologia_righe: int,
        interactive: Optional[bool] = None,
        input: Optional[Input] = None,
        output: Optional[Output] = None,
        fallback_input: Callable[[str], str] = builtins.input,
    ) -> None:
        if interactive is None:
            interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
        self.interactive = interactive
        self.fallback_input = fallback_input
        self.history_warning: Optional[str] = None
        try:
            self.history: History = CronologiaSicura(cronologia_file, cronologia_righe)
        except OSError as errore:
            # La cronologia contiene dati utili ma non e' il motore di Ares:
            # un disco in sola lettura o un permesso errato non deve impedire
            # una conversazione. La sessione corrente conserva comunque le
            # frecce su/giu in memoria e rende visibile la degradazione.
            self.history = InMemoryHistory()
            self.history_warning = str(errore)
        self.completer = CompletamentoComandi(comandi)
        self._sessione: Optional[PromptSession[str]] = None
        self._domande: Optional[PromptSession[str]] = None

        if self.interactive:
            comuni = {
                "style": ARES_INPUT_STYLE,
                "input": input,
                "output": output,
                "include_default_pygments_style": False,
                "mouse_support": False,
            }
            self._sessione = PromptSession(
                history=self.history,
                completer=self.completer,
                lexer=LexerInputAres(),
                auto_suggest=AutoSuggestFromHistory(),
                complete_while_typing=True,
                complete_style=CompleteStyle.COLUMN,
                reserve_space_for_menu=min(10, max(3, len(comandi))),
                multiline=True,
                prompt_continuation=_continuazione,
                key_bindings=_tasti_chat(self.completer),
                wrap_lines=True,
                **comuni,
            )
            self._domande = PromptSession(
                history=InMemoryHistory(),
                complete_while_typing=False,
                multiline=False,
                **comuni,
            )

    @staticmethod
    def _messaggio() -> StyleAndTextTuples:
        return [("class:prompt.user", "Tu"), ("class:prompt.marker", " › ")]

    @staticmethod
    def _strumenti() -> StyleAndTextTuples:
        return [
            (
                "class:bottom-toolbar",
                " Invio invia · Alt+Invio va a capo · / comandi · ↑↓ cronologia ",
            )
        ]

    def prompt(self) -> str:
        if self._sessione is None:
            testo = self.fallback_input("Tu › ")
            storico = self.history.get_strings()
            if testo and (not storico or storico[-1] != testo):
                self.history.append_string(testo)
            return testo
        return self._sessione.prompt(
            self._messaggio(),
            placeholder="Scrivi ad Ares oppure / per i comandi",
            bottom_toolbar=self._strumenti,
        )

    def ask(self, etichetta: str, *, muted: bool = False) -> str:
        if self._domande is None:
            return self.fallback_input(etichetta)
        stile = "class:prompt.ask-muted" if muted else "class:prompt.ask"
        return self._domande.prompt([(stile, etichetta)])
