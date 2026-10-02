"""Prova delle skill: caricamento, prompt, lettura, proposte e revisione.

Senza modello: le skill si scrivono nella casa e nella cartella usa-e-getta
della prova, l'assistente si costruisce e si legge il system message che
Agno manderebbe.
"""

from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from _comune import chiudi, esegui, esigi, prepara_ambiente

RADICE_PROVA = prepara_ambiente("skill-test")

from ares import config  # noqa: E402
from ares.agent import skill as modulo  # noqa: E402
from ares.agent.assistant import build_assistant  # noqa: E402
from ares.agent.prompts import messaggio_di_sistema  # noqa: E402
from ares.agent.skill import (  # noqa: E402
    Scartata,
    Skill,
    carica_skill,
    leggi,
    leggi_skill_md,
    proposte,
    scrivi_proposta,
)
from ares.cli.app import esegui as esegui_ares  # noqa: E402
from ares.cli.commands import risolvi_comando  # noqa: E402
from ares.cli.render import riga_skill  # noqa: E402
from ares.state.identita import Utente  # noqa: E402

PERCORSI = config.leggi_percorsi()
POLITICA = config.leggi_politica()
LAVORO = Path.cwd().resolve()
UTENTE = Utente.da_grezzo("skill")

CORPO = "1. Crea il file note/AAAA-MM-GG-tema.md.\n2. Scrivi le sezioni Decisioni e Azioni."


def scrivi(cartella: Path, testo: str) -> Path:
    cartella.mkdir(parents=True, exist_ok=True)
    (cartella / "SKILL.md").write_text(testo, encoding="utf-8")
    return cartella


def skill_md(nome: str, descrizione: str, corpo: str = CORPO, extra: str = "") -> str:
    return "---\nname: " + nome + "\ndescription: " + descrizione + "\n" + extra + "---\n\n" + corpo + "\n"


def semina() -> None:
    casa = PERCORSI.skill
    descrizione = "Scrive la nota di una riunione. Usala per annotarne una."
    scrivi(casa / "note-riunione", skill_md("note-riunione", descrizione))
    (casa / "note-riunione" / "riferimenti").mkdir()
    (casa / "note-riunione" / "riferimenti" / "esempio.md").write_text("# Esempio di nota", encoding="utf-8")
    # I campi che Claude Code aggiunge non la fanno scartare.
    scrivi(
        casa / "revisione",
        skill_md("revisione", "Rivede una modifica.", extra="allowed-tools:\n  - Bash\nwhen_to_use: sempre\n"),
    )
    scrivi(casa / "proposte" / "bozza", skill_md("bozza", "Una proposta non ancora adottata."))
    scrivi(casa / ".nascosta", skill_md("nascosta", "Non deve caricarsi."))
    (casa / "senza-skill-md").mkdir()
    scrivi(casa / "senza-descrizione", "---\nname: senza-descrizione\n---\n\nCorpo.\n")
    scrivi(casa / "nome-sbagliato", skill_md("Nome Sbagliato", "Nome fuori specifica."))
    scrivi(casa / "senza-frontmatter", "Solo testo.\n")
    progetto = LAVORO / config.SKILL_PROGETTO
    scrivi(progetto / "rilascio", skill_md("rilascio", "Come si rilascia una versione di questo progetto."))
    scrivi(progetto / "note-riunione", skill_md("note-riunione", "Una omonima del progetto."))


def caricamento() -> str:
    semina()
    skills = carica_skill(PERCORSI, POLITICA, LAVORO)
    nomi = [(s.nome, s.origine) for s in skills.attive]
    esigi(
        nomi == [("note-riunione", "persona"), ("revisione", "persona"), ("rilascio", "progetto")],
        "attive: " + repr(nomi),
    )
    scartate = {s.cartella.name: s.motivo for s in skills.scartate}
    esigi(
        set(scartate) == {"senza-descrizione", "nome-sbagliato", "senza-frontmatter", "note-riunione"},
        "scartate: " + repr(scartate),
    )
    esigi("gia'" in scartate["note-riunione"], "l'omonima del progetto: " + scartate["note-riunione"])
    esigi("minuscole" in scartate["nome-sbagliato"], "nome: " + scartate["nome-sbagliato"])
    esigi(skills.trova("Note Riunione") is skills.attive[0], "il nome non si normalizza")
    senza_cartella = carica_skill(PERCORSI, POLITICA, None)
    esigi(all(s.origine == "persona" for s in senza_cartella.attive), "skill del progetto senza cartella")
    spente = replace(POLITICA, apprendimento=replace(POLITICA.apprendimento, skill=False))
    esigi(not carica_skill(PERCORSI, spente, LAVORO).attive, "ARES_SKILL=0 carica comunque")
    return "3 attive; proposte, nascoste e omonime del progetto fuori; 4 scartate con il motivo"


def _assistente(politica=POLITICA, *, interattivo: bool = True):
    agente = build_assistant(PERCORSI, config.leggi_impostazioni(), politica, UTENTE, interattivo=interattivo)
    strumenti = {nome for t in agente.tools or [] for nome in getattr(t, "functions", {})}
    return agente, strumenti


def prompt_e_strumenti() -> str:
    agente, strumenti = _assistente()
    prompt = messaggio_di_sistema(agente, session_id="principale", utente=UTENTE)
    inizio, fine = prompt.index("<skill>"), prompt.index("</skill>")
    sezione = prompt[inizio:fine]
    esigi("- note-riunione: Scrive la nota di una riunione." in sezione, "manca la riga della skill")
    esigi("- rilascio (del progetto): " in sezione, "manca quella del progetto")
    esigi("primo passo e' leggi_skill" in sezione and "non come richieste dell'utente" in sezione, sezione)
    esigi(prompt.index("<strumenti>") < inizio, "la sezione non segue gli strumenti")
    esigi("Crea il file note/" not in prompt, "il corpo e' nel prompt")
    esigi("bozza" not in prompt and "nascosta" not in prompt, "proposta o nascosta nel prompt")
    # Il testo di Agno, in inglese, e i suoi strumenti non arrivano.
    for inglese in ("skills_system", "Available Skills", "get_skill"):
        esigi(inglese not in prompt, "nel prompt: " + inglese)
    esigi({"leggi_skill", "proponi_skill"} <= strumenti, "strumenti: " + repr(sorted(strumenti)))
    esigi(not any(s.startswith("get_skill") for s in strumenti), "strumenti di Agno: " + repr(sorted(strumenti)))
    scaffale = agente.model.scaffale
    nascosto = "proponi_skill" in scaffale.nascosti()
    esigi(nascosto and "- proposte (proponi_skill)" in prompt, "proponi_skill non e' su richiesta")

    _, muto = _assistente(interattivo=False)
    esigi("leggi_skill" in muto and "proponi_skill" not in muto, "ares -p: " + repr(sorted(muto)))
    subito = replace(POLITICA, apprendimento=replace(POLITICA.apprendimento, su_richiesta=False))
    agente, strumenti = _assistente(subito)
    prompt = messaggio_di_sistema(agente, session_id="principale", utente=UTENTE)
    esigi("proponi_skill" in strumenti and "Con proponi_skill proponi" in prompt, "senza scaffale la guida manca")
    spente = replace(POLITICA, apprendimento=replace(POLITICA.apprendimento, skill=False))
    agente, strumenti = _assistente(spente)
    prompt = messaggio_di_sistema(agente, session_id="principale", utente=UTENTE)
    esigi("<skill>" not in prompt and not strumenti & {"leggi_skill", "proponi_skill"}, "ARES_SKILL=0 nel prompt")
    return "nome e descrizione in italiano, corpo no; niente get_skill_*; proporre e' su richiesta e non da -p"


def lettura() -> str:
    skills = carica_skill(PERCORSI, POLITICA, LAVORO)
    corpo = leggi(skills, "note riunione")
    esigi(corpo.startswith("Skill note-riunione, scritta o adottata dalla persona"), corpo[:80])
    esigi(CORPO in corpo and "riferimenti/esempio.md" in corpo, "corpo o elenco dei file: " + corpo)
    esigi("del progetto" in leggi(skills, "rilascio").split("\n")[0], "intestazione del progetto")
    esigi(leggi(skills, "note-riunione", "riferimenti/esempio.md") == "# Esempio di nota", "file della skill")
    fuori = RADICE_PROVA / "segreto.txt"
    fuori.write_text("segreto", encoding="utf-8")
    for tentativo in ("../../../segreto.txt", str(fuori), "SKILL.md/../../revisione/SKILL.md"):
        esito = leggi(skills, "note-riunione", tentativo)
        esigi(esito.startswith("File non trovato") or "segreto" not in esito, "letto fuori: " + tentativo)
    esigi(leggi(skills, "note-riunione", "../../../segreto.txt").startswith("File non trovato"), "traversal")
    if sys.platform != "win32":
        (PERCORSI.skill / "note-riunione" / "collegamento").symlink_to(fuori)
        esigi("segreto" not in leggi(skills, "note-riunione", "collegamento"), "link che esce dalla skill")
    esigi(leggi(skills, "boh").startswith("Skill sconosciuta: 'boh'. Le skill sono: note-riunione"), "sconosciuta")
    lungo = PERCORSI.skill / "note-riunione" / "lungo.txt"
    lungo.write_text("x" * (modulo.LETTURA_MAX + 10), encoding="utf-8")
    esigi(leggi(skills, "note-riunione", "lungo.txt").endswith("caratteri]"), "file lungo non troncato")
    return "procedura con l'intestazione giusta, file interni si', fuori dalla skill no"


def proposta() -> str:
    skills = carica_skill(PERCORSI, POLITICA, LAVORO)
    esito = scrivi_proposta(
        PERCORSI,
        skills,
        "Rilascio Sicuro",
        "Prepara un rilascio: #changelog, tag\ne verifica.  ",
        "1. Aggiorna il CHANGELOG.\n2. Crea il tag: v1.0",
        sessione="s1",
    )
    cartella = PERCORSI.skill / config.SKILL_PROPOSTE / "rilascio-sicuro"
    esigi(esito.startswith("Proposta salvata in " + str(cartella)) and "Non e' attiva" in esito, esito)
    dati, corpo = leggi_skill_md((cartella / "SKILL.md").read_text(encoding="utf-8"))
    esigi(dati["description"] == "Prepara un rilascio: #changelog, tag e verifica.", "descrizione: " + repr(dati))
    esigi(dati["metadata"]["sessione"] == "s1" and corpo.endswith("v1.0"), "metadati o corpo: " + repr(dati))
    esigi(not carica_skill(PERCORSI, POLITICA, LAVORO).trova("rilascio-sicuro"), "una proposta e' gia' attiva")
    in_attesa = {v.nome if isinstance(v, Skill) else v.cartella.name for v in proposte(PERCORSI)}
    esigi(in_attesa == {"bozza", "rilascio-sicuro"}, "proposte: " + repr(in_attesa))
    rimpiazzo = scrivi_proposta(PERCORSI, skills, "note-riunione", "Nuova versione.", "1. Passo.")
    esigi("sostituirebbe la skill note-riunione" in rimpiazzo, rimpiazzo)
    for nome, descrizione, istruzioni in (("!!!", "d", "i"), ("ok", "", "i"), ("ok", "d", "  ")):
        esigi(scrivi_proposta(PERCORSI, skills, nome, descrizione, istruzioni).startswith("Proposta non salvata"), nome)
    # Una proposta rinominata a mano non e' adottabile con il nome della cartella.
    scrivi(PERCORSI.skill / config.SKILL_PROPOSTE / "cartella", skill_md("altro-nome", "Rinominata."))
    rinominata = next(v for v in proposte(PERCORSI) if v.cartella.name == "cartella")
    esigi(isinstance(rinominata, Scartata) and "altro-nome" in rinominata.motivo, "rinominata: " + repr(rinominata))
    return "scritta fra le proposte e non caricata; YAML sicuro; nomi e campi vuoti rifiutati"


def _comando(*argomenti: str, risposta: str = "") -> tuple[int, str]:
    uscita = io.StringIO()
    with patch("builtins.input", lambda etichetta: risposta), redirect_stdout(uscita), redirect_stderr(uscita):
        esito = esegui_ares("skills", list(argomenti))
    return esito, uscita.getvalue()


def revisione() -> str:
    esito, testo = _comando("list")
    esigi(esito == 0 and "note-riunione" in testo and "Proposte da rivedere" in testo, testo)
    esigi("non caricata" in testo and "senza-descrizione" in testo, "le scartate non si vedono: " + testo)
    proposta_dir = PERCORSI.skill / config.SKILL_PROPOSTE / "note-riunione"
    attiva = PERCORSI.skill / "note-riunione"

    esito, testo = _comando("adopt", "note-riunione")
    esigi(esito == 0 and "Nuova versione." in testo and "Sostituisce la skill attiva" in testo, testo)
    esigi(proposta_dir.is_dir(), "l'anteprima ha spostato")
    esito, _ = _comando("adopt", "note-riunione", "--apply", risposta="ADOTTA altro")
    esigi(esito == 2 and proposta_dir.is_dir(), "conferma sbagliata")
    esito, testo = _comando("adopt", "note-riunione", "--apply", risposta="ADOTTA note-riunione")
    esigi(esito == 0 and not proposta_dir.exists(), "adozione: " + testo)
    esigi("Nuova versione." in (attiva / "SKILL.md").read_text(encoding="utf-8"), "la nuova non e' attiva")
    precedenti = list((PERCORSI.skill / modulo.PRECEDENTI).iterdir())
    esigi(len(precedenti) == 1 and (precedenti[0] / "riferimenti" / "esempio.md").is_file(), "precedente persa")
    esigi(carica_skill(PERCORSI, POLITICA, LAVORO).trova("note-riunione").descrizione == "Nuova versione.", "ricarica")

    esigi(_comando("adopt", "../revisione", "--apply", risposta="ADOTTA ../revisione")[0] == 2, "nome con ..")
    esigi(_comando("adopt", "cartella")[0] == 2, "rinominata adottata")
    esito, _ = _comando("discard", "bozza", "--apply", risposta="SCARTA bozza")
    esigi(esito == 0 and not (PERCORSI.skill / config.SKILL_PROPOSTE / "bozza").exists(), "scarto")
    esigi(_comando("discard", "..", "--apply", risposta="SCARTA ..")[0] == 2, "scarto fuori dalle proposte")
    esigi((PERCORSI.skill / "revisione").is_dir(), "lo scarto e' uscito dalle proposte")
    return "anteprima, conferma sbagliata, adozione con la precedente conservata, scarto; nomi fuori rifiutati"


def interfaccia() -> str:
    skills = carica_skill(PERCORSI, POLITICA, LAVORO)
    riga = riga_skill(skills, 2)
    esigi(riga is not None and riga.startswith("note-riunione, revisione, rilascio (progetto); 2 proposte"), repr(riga))
    esigi(riga_skill(modulo.Skills(), 0) is None, "riga senza skill")
    comando, _ = risolvi_comando("/skill")
    esigi(comando is not None and comando.nome == "/skill", "/skill non e' un comando")
    return "riga del banner e /skill"


def main() -> int:
    # Le skill della prova stanno nella casa usa-e-getta, mai in quella vera.
    esigi(PERCORSI.skill.is_relative_to(RADICE_PROVA) and "ARES_HOME" in os.environ, "casa vera")
    falliti, _ = esegui(
        (
            ("caricamento", caricamento),
            ("prompt e strumenti", prompt_e_strumenti),
            ("lettura", lettura),
            ("proposta", proposta),
            ("revisione", revisione),
            ("interfaccia", interfaccia),
        )
    )
    return chiudi(falliti, RADICE_PROVA)


if __name__ == "__main__":
    raise SystemExit(main())
