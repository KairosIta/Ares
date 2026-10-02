"""Il nucleo, usato da un client senza terminale
==============================================

`ares.core` decide id, proprietario e ricostruzione dell'agente, le
modalita' ammesse, cosa fare di una richiesta di autorizzazione e quando lo
stato si puo' usare; la CLI e' solo uno dei suoi client. Qui lo si usa come farebbe una UI senza terminale:
nessuna stampa, nessun input, solo chiamate e valori restituiti.

Offline: nessuna chiamata al modello. L'agente vero si costruisce una volta,
per verificare cio' che il servizio gli passa; altrove il costruttore e'
sostituito da uno che registra.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from _comune import chiudi, esegui, esigi, prepara_ambiente

RADICE_PROVA = prepara_ambiente("nucleo-test")

from agno.models.message import Message  # noqa: E402
from agno.models.response import ToolExecution  # noqa: E402
from agno.run.agent import RunOutput  # noqa: E402
from agno.session.agent import AgentSession  # noqa: E402

from ares import config  # noqa: E402
from ares.core import session as nucleo  # noqa: E402
from ares.core import turn as nucleo_turno  # noqa: E402
from ares.core.autorizzazioni import (  # noqa: E402
    MOTIVO_TURNO_CHIUSO,
    Arbitro,
    Decisione,
    ModoNonAmmesso,
    avviso_ultimo_rifiuto,
    risolvi_pausa,
    verifica_modo,
)
from ares.core.id_sessione import nuovo_id_sessione  # noqa: E402
from ares.core.regole import Regola, Regole, analizza, leggi_regole, spezza  # noqa: E402
from ares.core.session import SessioneDiAltri, Sessioni  # noqa: E402
from ares.core.stato import StatoDaMigrare, stato_esclusivo, stato_in_uso, verifica_posto  # noqa: E402
from ares.state.identita import Utente  # noqa: E402
from ares.state.lock import StatoOccupato, lock_stato  # noqa: E402

UTENTE = Utente.da_grezzo("prova-nucleo")
ALTRO = Utente.da_grezzo("prova-nucleo-altro")
# La home e' quella della prova: le regole di autorizzazione personali si
# leggono da `~/.ares`, e qui non devono entrare quelle di questa macchina.
PERCORSI = replace(config.leggi_percorsi(), home=RADICE_PROVA / "home")
IMPOSTAZIONI = config.leggi_impostazioni()
POLITICA = config.leggi_politica()


def _servizio(*, cartella: bool = True, presidiato: bool = False) -> Sessioni:
    politica = POLITICA
    if not cartella:
        politica = replace(POLITICA, workspace=replace(POLITICA.workspace, attivo=False))
    return Sessioni(PERCORSI, IMPOSTAZIONI, politica, UTENTE, presidiato=presidiato)


class Registro:
    """Sostituto di `build_assistant` che ricorda con cosa e' stato chiamato."""

    def __init__(self) -> None:
        self.chiamate: list[dict] = []

    def __call__(self, percorsi, impostazioni, politica, utente, **opzioni):
        self.chiamate.append({"utente": utente, **opzioni})
        return object()


def id_delle_sessioni() -> str:
    """Con la cartella l'id viene da cartella e momento; senza, e' `principale`."""
    momento = datetime(2026, 9, 7, 9, 15, 30)
    esigi(
        nuovo_id_sessione(Path("/x/Mio Progetto_2"), momento, suffisso="abc123")
        == "mio-progetto-2-20260907-091530-abc123",
        "l'id non normalizza il nome della cartella",
    )
    esigi(
        nuovo_id_sessione(Path("/progetti/api"), momento) != nuovo_id_sessione(Path("/progetti/api"), momento),
        "due avvii nello stesso secondo danno lo stesso id",
    )
    con = _servizio().id_nuovo()
    esigi(con.startswith(PERCORSI.lavoro.name.casefold()[:10]), "l'id non nomina la cartella: " + con)
    esigi(_servizio(cartella=False).id_nuovo() == "principale", "senza cartella l'id non e' principale")
    return "id dalla cartella, principale senza"


def apertura_e_modo() -> str:
    """`nuova`, `apri` e `cambia_modo` passano al costruttore sessione, modo e presenza."""
    registro = Registro()
    servizio = _servizio(presidiato=False)
    with patch.object(nucleo, "build_assistant", registro):
        nuova = servizio.nuova()
        esigi(nuova.modo == config.MODO_PREDEFINITO, "la modalita' predefinita non e' quella di config")
        esigi(registro.chiamate[-1]["session_id"] == nuova.id, "l'agente non e' costruito sulla sessione nuova")
        esigi(registro.chiamate[-1]["interattivo"] is False, "un client senza terminale riceve un agente che apprende")

        piano = servizio.cambia_modo(nuova, "piano")
        esigi(piano.id == nuova.id and piano.modo == "piano", "cambiare modalita' cambia sessione")
        esigi(registro.chiamate[-1]["modo"] == "piano", "la modalita' nuova non arriva al costruttore")

        costruiti = len(registro.chiamate)
        try:
            servizio.cambia_modo(nuova, "inesistente")
        except ValueError:
            pass
        else:
            raise AssertionError("una modalita' sconosciuta viene accettata")
        esigi(len(registro.chiamate) == costruiti, "una modalita' sconosciuta costruisce l'agente")
        try:
            servizio.apri("")
        except ValueError:
            pass
        else:
            raise AssertionError("un nome vuoto apre una sessione")
    return "nuova, cambio di modalita', nomi e modalita' non validi"


def _non_ammesso(chiamata, motivo: str) -> None:
    """Esige che `chiamata` sollevi `ModoNonAmmesso` con `motivo`."""
    try:
        chiamata()
    except ModoNonAmmesso as errore:
        esigi(errore.motivo == motivo, "motivo sbagliato: " + errore.motivo + " invece di " + motivo)
    else:
        raise AssertionError("una modalita' non ammessa (" + motivo + ") viene accettata")


def modalita_ammesse() -> str:
    """Senza presenza nessuna modalita' scrive in silenzio; `auto` solo all'apertura.

    La regola si prova sulla tabella intera, poi attraverso il servizio: una
    modalita' rifiutata non costruisce l'agente, ne' all'apertura ne' al
    cambio.
    """
    for modo in config.MODALITA:
        if config.modalita_scrive_in_silenzio(modo):
            _non_ammesso(lambda modo=modo: verifica_modo(modo, presidiato=False), "presenza")
        else:
            verifica_modo(modo, presidiato=False)
        verifica_modo(modo, presidiato=True)
    _non_ammesso(lambda: verifica_modo("auto", presidiato=True, in_corso=True), "avvio")
    verifica_modo("modifiche", presidiato=True, in_corso=True)

    registro = Registro()
    with patch.object(nucleo, "build_assistant", registro):
        senza = _servizio(presidiato=False)
        _non_ammesso(lambda: senza.apri("pipe", modo="modifiche"), "presenza")
        esigi(registro.chiamate == [], "una modalita' non ammessa costruisce l'agente")
        manuale = senza.apri("pipe")
        _non_ammesso(lambda: senza.cambia_modo(manuale, "modifiche"), "presenza")
        esigi(len(registro.chiamate) == 1, "un cambio non ammesso costruisce l'agente")

        con = _servizio(presidiato=True)
        esigi(con.apri("tastiera", modo="auto").modo == "auto", "auto non si apre con presenza")
        # Anche `/sessione` passa da `apri` con la modalita' corrente: auto resta.
        _non_ammesso(lambda: con.cambia_modo(con.apri("tastiera"), "auto"), "avvio")
    return "tabella senza presenza, auto solo all'apertura, niente agente se rifiutata"


class Requisito:
    """Un requirement di Agno in pausa, che ricorda come e' stato risolto."""

    def __init__(self, nome: str, *, da_confermare: bool = True, argomenti: dict | None = None) -> None:
        self.needs_confirmation = da_confermare
        self.tool_execution = ToolExecution(tool_name=nome, tool_args=argomenti or {"path": "note.md"})
        self.esito = "irrisolto"

    def confirm(self) -> None:
        self.esito = "confermato"

    def reject(self, motivo=None) -> None:
        self.esito = "rifiutato: " + str(motivo)


class Pausa:
    def __init__(self, *requisiti: Requisito) -> None:
        self.active_requirements = list(requisiti)


class Autorizzatore:
    """Risponde da copione e registra cio' che il nucleo gli passa."""

    def __init__(self, *decisioni: Decisione, presidiato: bool = True) -> None:
        self.presidiato = presidiato
        self.decisioni = list(decisioni)
        self.chieste: list = []
        self.negate: list = []
        self.concesse: list = []

    def autorizza(self, richiesta):
        self.chieste.append(richiesta)
        return self.decisioni.pop(0)

    def negata(self, richiesta) -> None:
        self.negate.append(richiesta)

    def concessa(self, richiesta) -> None:
        self.concesse.append(richiesta)


def autorizzazioni() -> str:
    """Il nucleo applica le decisioni del client; senza presenza rifiuta senza chiedere."""
    cartella = Requisito(config.WORKSPACE_PREFIX + "delete_file")
    quaderno = Requisito("write_file")
    interno = Requisito("interno", da_confermare=False)
    cliente = Autorizzatore(Decisione(True), Decisione(False, "non quel file"))
    risolti = risolvi_pausa(Pausa(interno, cartella, quaderno), cliente, PERCORSI, POLITICA)
    esigi(risolti == 2 and interno.esito == "irrisolto", "un requirement senza conferma viene contato o toccato")
    esigi(cartella.esito == "confermato", "il si' del client non conferma")
    esigi(quaderno.esito == "rifiutato: non quel file", "il motivo del client non arriva al modello")
    prima, seconda = cliente.chieste
    esigi(
        prima.strumento == cartella.tool_execution.tool_name and prima.argomenti == {"path": "note.md"},
        "la richiesta non descrive lo strumento",
    )
    esigi(prima.radice == PERCORSI.lavoro, "uno strumento della cartella arriva senza la cartella")
    esigi(seconda.radice is None, "uno strumento del quaderno arriva con la cartella")

    nessuno = Autorizzatore(presidiato=False)
    comando = Requisito(config.WORKSPACE_PREFIX + "run_command")
    esigi(risolvi_pausa(Pausa(comando), nessuno, PERCORSI, POLITICA) == 1, "senza presenza la pausa resta irrisolta")
    esigi(nessuno.chieste == [], "senza presenza il nucleo chiede al client")
    esigi(comando.esito == "rifiutato: None", "senza presenza lo strumento non viene rifiutato: " + comando.esito)
    esigi(len(nessuno.negate) == 1, "senza presenza il client non sa del rifiuto")
    esigi(risolvi_pausa(Pausa(), cliente, PERCORSI, POLITICA) == 0, "una pausa senza conferme risulta risolta")
    return "si', no con motivo, cartella e quaderno distinti, rifiuto senza presenza"


def tetto_dei_rifiuti() -> str:
    """L'arbitro chiude il turno dopo N rifiuti di seguito, e una conferma azzera il conto."""
    avviso = avviso_ultimo_rifiuto(3)
    cliente = Autorizzatore(*[Decisione(False, "no") for _ in range(5)])
    arbitro = Arbitro(cliente, PERCORSI, POLITICA, tetto=3)
    primo, secondo, terzo, quarto = (Requisito("x") for _ in range(4))
    esigi(arbitro(Pausa(primo)) == 1 and primo.esito == "rifiutato: no", "il primo rifiuto non e' un rifiuto normale")
    esigi(arbitro(Pausa(secondo)) == 1 and secondo.esito == "rifiutato: no", "il secondo rifiuto porta gia' l'avviso")
    esigi(
        arbitro(Pausa(terzo)) == 1 and terzo.esito == "rifiutato: no " + avviso,
        "il terzo rifiuto non avvisa il modello",
    )
    esigi(not arbitro.esauriti and arbitro.consecutivi == 3, "dopo il terzo rifiuto il turno e' gia' chiuso")
    esigi(arbitro(Pausa(quarto)) == 0 and arbitro.esauriti, "la quarta richiesta non chiude il turno")
    esigi(
        quarto.esito == "rifiutato: " + MOTIVO_TURNO_CHIUSO, "la richiesta oltre il tetto non e' rifiutata col motivo"
    )
    esigi(len(cliente.chieste) == 3, "oltre il tetto il nucleo chiede ancora al client")

    # Una conferma concessa azzera il conto; una pausa mista (si' e no) pure.
    decisioni = [Decisione(False), Decisione(False), Decisione(True), Decisione(False), Decisione(False)]
    arbitro = Arbitro(Autorizzatore(*decisioni), PERCORSI, POLITICA, tetto=3)
    for _ in range(5):
        arbitro(Pausa(Requisito("x")))
    esigi(not arbitro.esauriti and arbitro.consecutivi == 2, "una conferma concessa non azzera il conto")
    misto = Arbitro(Autorizzatore(Decisione(False), Decisione(True)), PERCORSI, POLITICA, tetto=1)
    esigi(
        misto(Pausa(Requisito("x"), Requisito("y"))) == 2 and misto.consecutivi == 0,
        "una pausa mista conta come rifiuto",
    )

    # Senza presenza ogni pausa e' un rifiuto, con l'avviso al posto giusto.
    nessuno = Autorizzatore(presidiato=False)
    arbitro = Arbitro(nessuno, PERCORSI, POLITICA, tetto=1)
    unico = Requisito("x")
    esigi(
        arbitro(Pausa(unico)) == 1 and unico.esito == "rifiutato: " + avviso_ultimo_rifiuto(1),
        "senza presenza l'avviso manca",
    )
    esigi(
        arbitro(Pausa(Requisito("x"))) == 0 and arbitro.esauriti and nessuno.chieste == [],
        "senza presenza il tetto non vale",
    )
    esigi(
        Arbitro(nessuno, PERCORSI, POLITICA).tetto == config.RIFIUTI_CONSECUTIVI,
        "il tetto di serie non viene da config",
    )
    return "avviso al terzo rifiuto, chiusura al quarto, azzeramento con un si', senza presenza"


def regole_di_autorizzazione() -> str:
    """Le regole della persona: tabella di comandi, nega sopra consenti, file assenti o rotti, pipe che non concede."""
    import sys

    from ares.agent.runtime import AresWorkspace, build_workspace

    comando = config.WORKSPACE_PREFIX + "run_command"

    # Spezzare: semplici, composti, con wrapper e percorsi; cio' che non si legge chiede.
    for args, attesi in (
        (["git", "status"], [("git", "status")]),
        (["/usr/bin/git", "status", "--short"], [("/usr/bin/git", "status", "--short")]),
        (
            ["bash", "-lc", "git add . && git push origin main"],
            [("git", "add", "."), ("git", "push", "origin", "main")],
        ),
        (["sh", "-c", "ls -la | wc -l; echo 'a; b'"], [("ls", "-la"), ("wc", "-l"), ("echo", "a; b")]),
        (["env", "FOO=1", "uv", "run", "pytest"], [("uv", "run", "pytest")]),
        (["bash", "-c", "PAGER=cat time git log"], [("git", "log")]),
    ):
        esigi(spezza(args) == attesi, "spezza " + repr(args) + " da' " + repr(spezza(args)))
    for opaco in (
        ["bash", "-lc", "cat x > y"],
        ["bash", "-lc", "echo $(rm -rf .)"],
        ["bash", "-lc", "rm `ls`"],
        ["bash", "-lc", "$CMD status"],
        ["bash", "-lc", "(cd a && rm x)"],
        ["bash", "-lc", "ls 2>&1"],
        ["bash", "-lc", "ls", "extra"],
        ["bash", "-i"],
        ["powershell", "-Command", "Get-Item ."],
        ["env", "-i", "ls"],
        ["bash", "-lc", "echo 'aperto"],
        ["bash", "-lc", "FOO=1"],
        [],
        "ls",
        ["ls", 3],
    ):
        esigi(spezza(opaco) is None, "un comando che non si sa leggere e' spezzato: " + repr(opaco))

    # Le regole: prefisso per parole, la prima per nome; nega vince; una parte scoperta chiede.
    testo = '[comandi]\nconsenti = ["git status", "git log", "ls", "uv run pytest"]\n'
    testo += 'nega = ["rm -rf", "git push", "curl"]\n'
    lette, avvisi = analizza(testo, "prova.toml")
    esigi(len(lette) == 7 and not avvisi, "sette regole senza avvisi attese: " + repr((len(lette), avvisi)))
    regole = Regole(tuple(lette), ("prova.toml",))
    for args, atteso in (
        (["git", "status"], "consenti"),
        (["/usr/bin/git", "status", "--short"], "consenti"),
        (["git", "statusx"], None),
        (["git", "commit", "-m", "x"], None),
        (["bash", "-lc", "git status && git log --oneline"], "consenti"),
        (["bash", "-lc", "git status && git commit -m x"], None),
        (["bash", "-lc", "git status && git push"], "nega"),
        (["bash", "-lc", "ls | curl -X POST http://x -d @-"], "nega"),
        (["rm", "-rf", "/"], "nega"),
        (["rm", "-r", "-f", "x"], None),
        (["bash", "-lc", "git status > out.txt"], None),
        (["env", "uv", "run", "pytest", "-q"], "consenti"),
        (["powershell", "-Command", "git status"], None),
    ):
        decisa = regole.decidi(args)
        effetto = None if decisa is None else decisa.effetto
        esigi(effetto == atteso, repr(args) + ": " + repr(effetto) + ", atteso " + repr(atteso))
    esigi(str(regole.decidi(["git", "push"])) == "nega \u00abgit push\u00bb in prova.toml", "la regola non si descrive")
    esigi(regole.quante("consenti") == 4 and regole.quante("nega") == 3 and regole, "i conti delle regole")

    # Malformato o fuori formato: vale assente, con un avviso che nomina il file.
    for rotto in (
        "[comandi\nconsenti = [",
        '[comandi]\nconsenti = "git status"',
        '[comandi]\nconsenti = ["ls | wc"]',
        "[altro]\nx = 1",
        "comandi = 3",
    ):
        lette, avvisi = analizza(rotto, "rotto.toml")
        esigi(not lette and len(avvisi) == 1 and "rotto.toml" in avvisi[0], repr(rotto) + ": " + repr((lette, avvisi)))
    lette, avvisi = analizza('[comandi]\nconsenti = ["ls"]\nboh = 1\n', "p.toml")
    esigi(len(lette) == 1 and len(avvisi) == 1, "una chiave ignota scarta anche le regole buone")
    esigi(not Regole() and analizza("", "vuoto.toml") == ([], []), "un file vuoto non vale vuoto")

    # I due file sul disco si sommano; quello del progetto deve stare nella cartella.
    percorsi = replace(PERCORSI, home=RADICE_PROVA / "home-regole")
    percorsi.home.mkdir(parents=True, exist_ok=True)
    (percorsi.home / POLITICA.workspace.regole_personali).write_text('[comandi]\nnega = ["git push"]\n')
    progetto = percorsi.lavoro / POLITICA.workspace.regole_progetto
    progetto.parent.mkdir(parents=True, exist_ok=True)
    progetto.write_text('[comandi]\nconsenti = ["git status"]\n')
    try:
        insieme = leggi_regole(percorsi, POLITICA)
        esigi(len(insieme.fonti) == 2 and not insieme.avvisi, "i due file non sono letti entrambi: " + repr(insieme))
        esigi(
            insieme.decidi(["git", "push"]).effetto == "nega"
            and insieme.decidi(["git", "status"]).effetto == "consenti",
            "le regole dei due file non si sommano",
        )
        progetto.write_text("[comandi\n")
        rotte = leggi_regole(percorsi, POLITICA)
        esigi(
            rotte.quante("nega") == 1 and rotte.quante("consenti") == 0 and len(rotte.avvisi) == 1,
            "un file del progetto rotto non vale assente con avviso: " + repr(rotte),
        )
        if os.name != "nt":
            progetto.unlink()
            fuori = RADICE_PROVA / "fuori.toml"
            fuori.write_text('[comandi]\nconsenti = ["rm -rf"]\n')
            progetto.symlink_to(fuori)
            esigi(
                leggi_regole(percorsi, POLITICA).quante("consenti") == 0,
                "un file del progetto che punta fuori dalla cartella viene letto",
            )
        nessuno = leggi_regole(replace(percorsi, lavoro=RADICE_PROVA / "vuota", home=RADICE_PROVA / "vuota"), POLITICA)
        esigi(nessuno == Regole(), "senza file le regole non sono vuote: " + repr(nessuno))
    finally:
        progetto.unlink(missing_ok=True)

    # L'arbitro: consenti conferma senza chiedere, nega rifiuta senza chiedere e col motivo, il resto chiede.
    poche = Regole(
        (Regola("consenti", ("git", "status"), "p.toml"), Regola("nega", ("git", "push"), "p.toml")), ("p.toml",)
    )
    cliente = Autorizzatore(Decisione(False, "no"), Decisione(True), Decisione(False))
    arbitro = Arbitro(cliente, PERCORSI, POLITICA, regole=poche)
    concesso = Requisito(comando, argomenti={"args": ["git", "status"]})
    negato = Requisito(comando, argomenti={"args": ["bash", "-lc", "git add . && git push"]})
    scoperto = Requisito(comando, argomenti={"args": ["make", "test"]})
    file = Requisito(config.WORKSPACE_PREFIX + "delete_file")
    quaderno = Requisito("write_file", argomenti={"args": ["git", "status"]})
    esigi(arbitro(Pausa(concesso, negato, scoperto, file)) == 4, "le quattro richieste non sono risolte")
    esigi(concesso.esito == "confermato", "il comando consentito non e' confermato: " + concesso.esito)
    esigi(
        negato.esito.startswith("rifiutato: Comando negato da una regola") and "p.toml" in negato.esito,
        "il comando negato non porta il motivo con il file: " + negato.esito,
    )
    esigi(scoperto.esito == "rifiutato: no" and file.esito == "confermato", "il resto non passa dal client")
    esigi([r.strumento for r in cliente.chieste] == [comando, config.WORKSPACE_PREFIX + "delete_file"], "chieste")
    esigi(
        len(cliente.concesse) == 1 and cliente.concesse[0].regola.effetto == "consenti",
        "la concessione non arriva al client con la regola",
    )
    esigi(
        len(cliente.negate) == 1 and cliente.negate[0].regola.effetto == "nega" and cliente.negate[0].regola.fonte,
        "il rifiuto per regola non arriva al client con la regola",
    )
    esigi(arbitro.consecutivi == 0, "una pausa con una concessione non azzera il conto")
    esigi(arbitro(Pausa(quaderno)) == 1 and quaderno.esito == "rifiutato: None", "le regole toccano il quaderno")
    esigi(len(cliente.chieste) == 3, "una scrittura nel quaderno con args non passa dal client")

    # I comandi negati di seguito contano come rifiuti: chiudono il turno come i no della persona.
    arbitro = Arbitro(Autorizzatore(), PERCORSI, POLITICA, tetto=2, regole=poche)
    for _ in range(2):
        arbitro(Pausa(Requisito(comando, argomenti={"args": ["git", "push"]})))
    esigi(arbitro.consecutivi == 2, "i rifiuti per regola non contano: " + str(arbitro.consecutivi))
    esigi(arbitro(Pausa(Requisito(comando, argomenti={"args": ["git", "push"]}))) == 0 and arbitro.esauriti, "tetto")

    # Senza presenza consenti non vale (una pipe non concede niente), nega si'.
    nessuno = Autorizzatore(presidiato=False)
    arbitro = Arbitro(nessuno, PERCORSI, POLITICA, regole=poche)
    stato = Requisito(comando, argomenti={"args": ["git", "status"]})
    push = Requisito(comando, argomenti={"args": ["git", "push"]})
    esigi(arbitro(Pausa(stato, push)) == 2 and stato.esito == "rifiutato: None", "senza presenza consenti concede")
    esigi("regola" in push.esito and nessuno.concesse == [], "senza presenza nega non vale, o qualcosa e' concesso")
    esigi([r.regola is None for r in nessuno.negate] == [True, False], "senza presenza le negate non dicono la regola")
    esigi(Arbitro(nessuno, PERCORSI, POLITICA).regole == Regole(), "l'arbitro di serie non legge i file della prova")

    # Il workspace stesso ferma un comando negato prima di eseguirlo: vale anche in auto.
    silenziosi, confermati = config.liste_modalita("auto")
    spazio = AresWorkspace(
        PERCORSI.lavoro, prefisso=config.WORKSPACE_PREFIX, regole=lambda: poche, allowed=silenziosi, confirm=confermati
    )
    fermato = spazio.run_command(["git", "push"])
    esigi(
        fermato.startswith("Errore: comando negato") and "p.toml" in fermato,
        "auto esegue un comando negato: " + fermato,
    )
    esigi(spazio.run_command([sys.executable, "-c", "print('ok')"]) == "ok", "un comando non negato non gira")
    esigi(build_workspace(PERCORSI, POLITICA, "auto").regole is not None, "build_workspace non passa le regole")
    return "spezzatura, prefissi, nega sopra consenti, file sommati o rotti, arbitro con e senza presenza, auto"


def _esclusivo_libero(percorsi) -> bool:
    """Vero se backup e restore potrebbero partire adesso."""
    try:
        with lock_stato(percorsi.lock_file, esclusivo=True):
            return True
    except StatoOccupato:
        return False


def stato_in_uso_dal_client() -> str:
    """Il client tiene lo stato: backup e restore aspettano, e lo stato da migrare non si apre."""
    with stato_in_uso(PERCORSI), stato_in_uso(PERCORSI):
        esigi(not _esclusivo_libero(PERCORSI), "con lo stato in uso un backup potrebbe partire")
    esigi(_esclusivo_libero(PERCORSI), "il lock condiviso resta dopo l'uscita")

    vecchio = RADICE_PROVA / "vecchio-clone" / "tmp"
    vecchio.mkdir(parents=True)
    (vecchio / "kairos.db").write_text("db", encoding="utf-8")
    casa = RADICE_PROVA / "casa-nuova"
    nuovi = replace(PERCORSI, home=casa, stato=casa / "stato", backup=casa / "backup")
    with patch.object(config, "VECCHIO_TMP_DIR", vecchio):
        try:
            with stato_in_uso(nuovi):
                raise AssertionError("lo stato nel posto di prima si apre")
        except StatoDaMigrare as errore:
            esigi([p[0] for p in errore.parti] == ["lo stato"], "parti da spostare sbagliate: " + repr(errore.parti))
    esigi(not nuovi.stato.exists(), "il rifiuto ha creato lo stato nuovo")
    esigi(_esclusivo_libero(nuovi), "il rifiuto lascia il lock condiviso")

    # Aprire lo stato non scrive; il servizio di sessione prepara la directory, privata.
    with stato_in_uso(nuovi):
        esigi(not nuovi.stato.exists(), "aprire lo stato ha creato la directory")
        Sessioni(nuovi, IMPOSTAZIONI, POLITICA, UTENTE, presidiato=True)
        esigi(nuovi.stato.is_dir(), "il servizio di sessione non prepara la directory dello stato")
        if os.name == "posix":
            esigi((nuovi.stato.stat().st_mode & 0o777) == 0o700, "la directory dello stato non e' privata")
    return "lock condiviso finche' serve, migrazione in sospeso rifiutata senza scrivere, directory privata"


def manutenzione_esclusiva() -> str:
    """La manutenzione che scrive tiene lo stato da sola, e lo stato da migrare non la fa partire."""
    with stato_in_uso(PERCORSI):
        try:
            with stato_esclusivo(PERCORSI):
                raise AssertionError("con una chat aperta la manutenzione parte")
        except StatoOccupato:
            pass
    with stato_esclusivo(PERCORSI):
        try:
            with stato_in_uso(PERCORSI):
                raise AssertionError("durante la manutenzione una chat apre lo stato")
        except StatoOccupato:
            pass
    esigi(_esclusivo_libero(PERCORSI), "il lock esclusivo resta dopo l'uscita")

    # Anche i soli backup nel posto di prima fermano: uno snapshot nuovo li sdoppierebbe.
    vecchi = RADICE_PROVA / "vecchio-backup"
    (vecchi / "20260101-manuale").mkdir(parents=True)
    casa = RADICE_PROVA / "casa-manutenzione"
    nuovi = replace(PERCORSI, home=casa, stato=casa / "stato", backup=casa / "backup")
    with patch.object(config, "VECCHIO_BACKUP_DIR", vecchi):
        try:
            verifica_posto(nuovi)
            raise AssertionError("i backup nel posto di prima passano la verifica")
        except StatoDaMigrare as errore:
            esigi([p[0] for p in errore.parti] == ["i backup"], "parti da spostare sbagliate: " + repr(errore.parti))
        esigi(not casa.exists(), "la verifica senza lock ha scritto qualcosa")
        try:
            with stato_esclusivo(nuovi):
                raise AssertionError("la manutenzione parte con i backup nel posto di prima")
        except StatoDaMigrare:
            pass
        esigi(_esclusivo_libero(nuovi), "il rifiuto lascia il lock esclusivo")
    with stato_esclusivo(nuovi):
        pass
    return "esclusiva con chat e manutenzione, backup da migrare rifiutati, verifica senza scrivere"


def sessione_altrui() -> str:
    """La sessione di un altro utente solleva `SessioneDiAltri` senza costruire l'agente."""
    servizio = _servizio()
    servizio.db.upsert_session(AgentSession(session_id="di-altri", user_id=ALTRO.id))
    registro = Registro()
    with patch.object(nucleo, "build_assistant", registro):
        try:
            servizio.apri("di-altri")
        except SessioneDiAltri as errore:
            esigi(errore.nome == "di-altri", "l'errore non nomina la sessione")
        else:
            raise AssertionError("la sessione di un altro utente si apre")
        esigi(registro.chiamate == [], "l'agente nasce anche per una sessione altrui")
        esigi(servizio.apri("mia").id == "mia", "una sessione nuova col nome scelto non si apre")
    return "rifiutata prima di costruire l'agente"


def elenco_delle_sessioni() -> str:
    """Ambito, filtro, taglio e riferimenti, sull'archivio vero; mai sessioni di altri."""
    servizio = _servizio()
    qui = str(PERCORSI.lavoro)

    def messaggio(ruolo: str, testo: str, storia: bool = False) -> Message:
        return Message(role=ruolo, content=testo, from_history=storia)

    for nome, utente, dove, quando, runs in (
        (
            "qui-vecchia",
            UTENTE,
            qui,
            1_000,
            [
                [
                    messaggio("system", "istruzioni"),
                    messaggio("user", " domanda\n uno "),
                    messaggio("assistant", "uno"),
                ],
                [messaggio("user", "domanda uno", storia=True), messaggio("user", "due")],
            ],
        ),
        ("qui-nuova", UTENTE, qui, 2_000, []),
        ("orfana", UTENTE, None, 2_500, []),
        ("altrove", UTENTE, "/altrove", 3_000, []),
        ("qui-di-altri", ALTRO, qui, 4_000, [[messaggio("user", "segreto")]]),
    ):
        servizio.db.upsert_session(
            AgentSession(
                session_id=nome,
                user_id=utente.id,
                # Senza `agent_id` Agno non riaggancia i run alla sessione.
                agent_id="ares",
                metadata={"cartella": dove} if dove else None,
                created_at=quando,
                updated_at=quando,
            )
        )
        for indice, messaggi in enumerate(runs):
            run = RunOutput(
                run_id=nome + "-" + str(indice), session_id=nome, user_id=utente.id, agent_id="ares", messages=messaggi
            )
            servizio.db.upsert_run(run, session_id=nome, user_id=utente.id, run_index=indice)

    def nomi(**argomenti) -> list[str]:
        return list(servizio.elenco(**argomenti).nomi)

    esigi(nomi(ambito="nate_qui") == ["qui-nuova", "qui-vecchia"], "nate qui: " + str(nomi(ambito="nate_qui")))
    esigi(nomi(ambito="qui") == ["orfana", "qui-nuova", "qui-vecchia"], "qui: " + str(nomi(ambito="qui")))
    esigi(
        nomi(ambito="tutte") == ["altrove", "orfana", "qui-nuova", "qui-vecchia"],
        "tutte: " + str(nomi(ambito="tutte")),
    )
    esigi(nomi(ambito="tutte", testo="VECCHIA") == ["qui-vecchia"], "il filtro sul nome non vale")

    tagliato = servizio.elenco(ambito="nate_qui", limite=1)
    esigi(
        tagliato.totale == 2 and [v.id for v in tagliato.voci] == ["qui-nuova"],
        "taglio sbagliato: " + repr(tagliato),
    )
    vecchia = servizio.elenco(ambito="nate_qui", testo="vecchia").voci[0]
    esigi(
        (vecchia.scambi, vecchia.inizio, vecchia.cartella, vecchia.aggiornata) == (2, "domanda uno", qui, 1_000),
        "riferimento sbagliato: " + repr(vecchia),
    )
    esigi(
        len(servizio.elenco(ambito="tutte").voci) == min(4, POLITICA.mostra.sessioni),
        "senza limite non vale quello della politica",
    )

    conversazione = servizio.conversazione("qui-vecchia")
    esigi(conversazione is not None, "la conversazione dell'utente non si legge")
    assert conversazione is not None
    esigi(
        [[(m.ruolo, m.testo) for m in s.messaggi] for s in conversazione.scambi]
        == [[("user", "domanda\n uno"), ("assistant", "uno")], [("user", "due")]],
        "scambi sbagliati: " + repr(conversazione.scambi),
    )
    esigi(servizio.conversazione("qui-di-altri") is None, "si legge la conversazione di un altro utente")
    esigi(servizio.conversazione("inesistente") is None, "una conversazione inesistente non e' None")

    senza = _servizio(cartella=False)
    esigi(senza.elenco(ambito="tutte").totale == 4, "senza cartella `tutte` non elenca")
    for ambito in ("qui", "nate_qui"):
        try:
            senza.elenco(ambito=ambito)
        except ValueError:
            pass
        else:
            raise AssertionError("senza cartella l'ambito " + ambito + " non e' rifiutato")

    # Il prompt di una sessione elenca le altre nate qui, non se stessa.
    agente = _servizio().apri("qui-nuova").agente
    istruzioni = "\n".join(str(i) for i in agente.instructions or [])
    esigi("- qui-vecchia (" in istruzioni, "il prompt non elenca le conversazioni precedenti di qui")
    esigi(
        all("- " + nome + " (" not in istruzioni for nome in ("qui-nuova", "orfana", "altrove", "qui-di-altri")),
        "il prompt elenca la sessione corrente, o una non nata qui o di un altro",
    )
    return "tre ambiti, filtro, taglio dopo il filtro, riferimenti, conversazione e prompt solo dell'utente"


def agente_vero() -> str:
    """L'agente costruito davvero porta sessione e utente, e senza terminale non apprende."""
    attiva = _servizio(presidiato=False).apri("vera")
    esigi(attiva.agente.session_id == "vera", "l'agente non e' sulla sessione aperta")
    esigi(attiva.agente.user_id == UTENTE.id, "l'agente non e' dell'utente del servizio")
    esigi(not attiva.agente.post_hooks, "senza terminale resta il post-hook di apprendimento")
    return "sessione, utente e niente post-hook"


class ClienteSenzaTerminale:
    """Un client di `esegui_turno` che non stampa: registra e risponde da copione."""

    def __init__(self, *, tenere: bool = True, presidiato: bool = True) -> None:
        self.tenere = tenere
        self.presidiato = presidiato
        self.chiamate: list[str] = []
        self.eventi: list[object] = []
        self.righe: list[str] = []

    @contextmanager
    def flusso(self):
        self.chiamate.append("flusso")
        yield self.eventi.append

    def autorizza(self, richiesta) -> Decisione:
        self.chiamate.append("autorizza")
        return Decisione(False)

    def negata(self, richiesta) -> None:
        self.chiamate.append("negata")

    def concessa(self, richiesta) -> None:
        self.chiamate.append("concessa")

    def pausa_irrisolta(self) -> None:
        self.chiamate.append("pausa irrisolta")

    def rifiuti_esauriti(self, quanti: int) -> None:
        self.chiamate.append("rifiuti esauriti " + str(quanti))

    def interrotto(self) -> None:
        self.chiamate.append("interrotto")

    def guasto(self, errore: Exception) -> None:
        self.chiamate.append("guasto: " + str(errore))

    def apprendimenti(self, righe: list[str], *, chiedi: bool) -> bool:
        self.chiamate.append("apprendimenti" + (" chiesti" if chiedi else ""))
        self.righe = righe
        return self.tenere


class AgenteFinto:
    user_id = UTENTE.id


class RispostaFinta:
    is_paused = False


def _turno(cliente, *, prima, dopo, chiedi=True, ciclo=None):
    """Un turno del nucleo con memoria e ciclo del modello simulati."""
    mostra = replace(POLITICA.mostra, apprendimenti=True, conferma_apprendimenti=chiedi)
    politica = replace(POLITICA, mostra=mostra)
    ripristini: list[object] = []

    def ciclo_predefinito(agent, testo, *, on_event, resolve_pause):
        on_event("evento")
        return RispostaFinta()

    letture = iter([prima, dopo])
    with (
        patch.object(nucleo_turno, "run_turn_cycle", ciclo or ciclo_predefinito),
        patch.object(nucleo_turno, "istantanea", lambda agent: "istantanea"),
        patch.object(nucleo_turno, "riduci", lambda stato: next(letture)),
        patch.object(nucleo_turno, "fotografa", lambda agent: next(letture)),
        patch.object(nucleo_turno, "ripristina", lambda agent, stato: ripristini.append(stato) or True),
    ):
        esito = nucleo_turno.esegui_turno(PERCORSI, politica, AgenteFinto(), "ciao", cliente)
    return esito, ripristini


def turno_senza_terminale() -> str:
    """Il turno del nucleo: eventi al client, eco, e un no che ripristina."""
    from ares.agent.echo import Fotografia

    vuota, scritta = Fotografia(), Fotografia(memorie={"m1": "usa Debian 13"})

    cliente = ClienteSenzaTerminale(tenere=False)
    esito, ripristini = _turno(cliente, prima=vuota, dopo=scritta)
    esigi(cliente.eventi == ["evento"], "gli eventi non arrivano al client")
    esigi(cliente.chiamate == ["flusso", "apprendimenti chiesti"], "sequenza sbagliata: " + repr(cliente.chiamate))
    esigi(any("usa Debian 13" in r for r in cliente.righe), "l'eco non mostra la memoria scritta")
    esigi(ripristini == ["istantanea"] and esito.ripristino is True, "un no non ripristina la fotografia di prima")

    cliente = ClienteSenzaTerminale(tenere=True)
    esito, ripristini = _turno(cliente, prima=vuota, dopo=scritta)
    esigi(ripristini == [] and esito.ripristino is None, "un si' ripristina")

    cliente = ClienteSenzaTerminale(tenere=False)
    esito, ripristini = _turno(cliente, prima=vuota, dopo=scritta, chiedi=False)
    esigi(cliente.chiamate[-1] == "apprendimenti" and ripristini == [], "senza conferma si ripristina comunque")

    # Senza presenza nessuno risponde: l'eco si mostra, la domanda no.
    cliente = ClienteSenzaTerminale(tenere=False, presidiato=False)
    esito, ripristini = _turno(cliente, prima=vuota, dopo=scritta)
    esigi(cliente.chiamate[-1] == "apprendimenti" and ripristini == [], "senza presenza si chiede se tenere")

    cliente = ClienteSenzaTerminale()
    esito, _ = _turno(cliente, prima=vuota, dopo=vuota)
    esigi("apprendimenti" not in " ".join(cliente.chiamate) and esito.appreso == (), "eco senza niente di scritto")

    def ciclo_guasto(agent, testo, *, on_event, resolve_pause):
        raise RuntimeError("disco pieno")

    cliente = ClienteSenzaTerminale(tenere=False)
    esito, ripristini = _turno(cliente, prima=vuota, dopo=scritta, ciclo=ciclo_guasto)
    esigi(cliente.chiamate[1] == "guasto: disco pieno", "il guasto non arriva al client: " + repr(cliente.chiamate))
    esigi(ripristini == ["istantanea"], "dopo un guasto cio' che e' stato scritto non passa dalla conferma")

    # Un modello che insiste: il nucleo passa all'arbitro, che dopo i rifiuti
    # di serie smette di riprendere, e il client sa perche' il turno e' finito.
    class InPausa(RispostaFinta):
        is_paused = True

    def ciclo_insistente(agent, testo, *, on_event, resolve_pause):
        pause = 0
        while resolve_pause(Pausa(Requisito("x"))):
            pause += 1
        esigi(pause == config.RIFIUTI_CONSECUTIVI, "l'arbitro del turno non usa il tetto di config: " + str(pause))
        return InPausa()

    cliente = ClienteSenzaTerminale()
    esito, _ = _turno(cliente, prima=vuota, dopo=vuota, ciclo=ciclo_insistente)
    esigi(
        cliente.chiamate == ["flusso"] + ["autorizza"] * config.RIFIUTI_CONSECUTIVI + ["rifiuti esauriti 3"],
        "il turno chiuso dall'arbitro non e' detto al client: " + repr(cliente.chiamate),
    )

    def ciclo_in_pausa(agent, testo, *, on_event, resolve_pause):
        return InPausa()

    cliente = ClienteSenzaTerminale()
    _turno(cliente, prima=vuota, dopo=vuota, ciclo=ciclo_in_pausa)
    esigi(
        cliente.chiamate == ["flusso", "pausa irrisolta"], "una pausa non dell'arbitro e' detta come rifiuti esauriti"
    )
    return "eventi, eco, rifiuto, conferma spenta, senza presenza, niente di scritto, guasto, arbitro"


PROVE = (
    ("id", id_delle_sessioni),
    ("apertura e modo", apertura_e_modo),
    ("modalita' ammesse", modalita_ammesse),
    ("autorizzazioni", autorizzazioni),
    ("tetto dei rifiuti", tetto_dei_rifiuti),
    ("regole", regole_di_autorizzazione),
    ("stato in uso", stato_in_uso_dal_client),
    ("manutenzione", manutenzione_esclusiva),
    ("sessione altrui", sessione_altrui),
    ("elenco", elenco_delle_sessioni),
    ("agente vero", agente_vero),
    ("turno", turno_senza_terminale),
)


def main() -> int:
    falliti, _ = esegui(PROVE)
    return chiudi(falliti, RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
