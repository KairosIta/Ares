"""Le descrizioni in italiano degli strumenti sui file, al posto delle docstring di Agno.

Quelle di Agno sono in inglese, nominano gli strumenti senza il prefisso che
Ares aggiunge (`write_file` invece di `quaderno_write_file`) e citano
parametri che il modello non ha. Qui ogni testo nomina gli strumenti con il
nome che il modello vede: `{w}` e `{q}` sono i prefissi di cartella e
quaderno. I parametri restano quelli di Agno; `descrivi` rifiuta un parametro
che lo schema non ha.
"""

from collections.abc import Mapping
from typing import Any, NamedTuple

from agno.tools.toolkit import Toolkit


class Descrizione(NamedTuple):
    """Il testo dello strumento e quello di ciascun parametro."""

    testo: str
    parametri: Mapping[str, str]


_RIGA_INIZIO = "Prima riga da restituire, da 1."
_RIGA_FINE = "Ultima riga da restituire, compresa."
_CODIFICA = "Codifica del testo, di serie utf-8."

CARTELLA: dict[str, Descrizione] = {
    "read_file": Descrizione(
        "Legge un file della cartella di lavoro. Ogni riga arriva preceduta dal suo numero e da un tab: "
        "il numero non fa parte del testo. Senza start_line ed end_line legge il file intero.",
        {
            "path": "Percorso relativo alla cartella di lavoro.",
            "start_line": _RIGA_INIZIO,
            "end_line": _RIGA_FINE,
            "encoding": _CODIFICA,
        },
    ),
    "list_files": Descrizione(
        "Elenca file e cartelle della cartella di lavoro, con tipo e dimensione. Le cartelle di "
        "dipendenze, cache e segreti (.git, .venv, node_modules, .env...) restano fuori.",
        {
            "directory": "Sottocartella da elencare, di serie la cartella di lavoro.",
            "pattern": "Filtro sui nomi, come *.py.",
            "recursive": "Scende anche nelle sottocartelle, fino a max_depth livelli.",
            "max_depth": "Profondita' massima con recursive, di serie 3.",
        },
    ),
    "search_content": Descrizione(
        "Cerca un testo nei file della cartella di lavoro, senza distinguere maiuscole e minuscole. "
        "Per ogni file trovato da' un estratto intorno alla prima occorrenza. Guarda solo i file di "
        "testo sotto i 500 KB.",
        {
            "query": "Il testo da cercare, cosi' com'e': non un'espressione regolare.",
            "directory": "Sottocartella in cui cercare, di serie tutta la cartella di lavoro.",
            "limit": "Quanti file restituire al massimo, di serie 10.",
        },
    ),
    "write_file": Descrizione(
        "Scrive un file nella cartella di lavoro, creando le cartelle che mancano. Sostituisce il "
        "contenuto intero: per cambiare una parte di un file esistente usa {w}edit_file. Un file "
        "esistente va letto con {w}read_file prima di riscriverlo.",
        {
            "path": "Percorso relativo alla cartella di lavoro.",
            "content": "Il contenuto completo del file.",
            "overwrite": "Con false fallisce se il file esiste gia'; di serie true.",
            "encoding": _CODIFICA,
        },
    ),
    "edit_file": Descrizione(
        "Modifica un file della cartella di lavoro sostituendo un testo con un altro. old_str deve "
        "comparire esattamente una volta, spazi e a capo compresi, e senza i numeri di riga che "
        "mostra {w}read_file; altrimenti la modifica fallisce. Leggi il file prima.",
        {
            "path": "Percorso relativo alla cartella di lavoro.",
            "old_str": "Il testo da sostituire, identico a quello nel file.",
            "new_str": "Il testo nuovo.",
            "replace_all": "Con true sostituisce ogni occorrenza invece di una sola.",
            "encoding": _CODIFICA,
        },
    ),
    "move_file": Descrizione(
        "Sposta o rinomina un file dentro la cartella di lavoro.",
        {
            "src": "Percorso attuale, relativo alla cartella di lavoro.",
            "dst": "Percorso nuovo, relativo alla cartella di lavoro.",
            "overwrite": "Con true sostituisce un file gia' presente in dst; di serie false.",
        },
    ),
    "delete_file": Descrizione(
        "Cancella un file della cartella di lavoro. Non cancella le cartelle.",
        {"path": "Percorso relativo alla cartella di lavoro."},
    ),
    "run_command": Descrizione(
        "",  # da `prompts.descrizione_del_comando`, che dipende da sistema e sandbox
        {
            "args": "Il comando diviso in parole.",
            "tail": "Quante righe di output restituire al massimo, fra testa e coda; di serie 100.",
            "timeout": "Secondi prima di interrompere il comando, di serie 120.",
        },
    ),
}

QUADERNO: dict[str, Descrizione] = {
    "read_file": Descrizione(
        "Legge una nota del quaderno. Ogni riga arriva preceduta dal suo numero: servono a "
        "{q}replace_lines, e non vanno mai copiati nel testo che scrivi.",
        {
            "path": "Percorso della nota, come note/decisioni.md.",
            "start_line": _RIGA_INIZIO,
            "end_line": _RIGA_FINE,
        },
    ),
    "write_file": Descrizione(
        "Crea una nota del quaderno, o ne sostituisce il contenuto intero. Per aggiungere voci a "
        "una nota usa {q}append_file, per correggerne una parte {q}replace_lines.",
        {
            "path": "Percorso della nota; le cartelle si creano da sole.",
            "content": "Il contenuto completo della nota.",
            "overwrite": "Con false fallisce se la nota esiste gia'; di serie true.",
        },
    ),
    "append_file": Descrizione(
        "Aggiunge righe in fondo a una nota del quaderno, creandola se non c'e'. Il testo comincia "
        "sempre su una riga nuova.",
        {
            "path": "Percorso della nota.",
            "content": "Una o piu' righe da aggiungere.",
            "unique": "Con true salta le righe che la nota contiene gia'.",
        },
    ),
    "replace_lines": Descrizione(
        "Sostituisce le righe da start_line a end_line di una nota del quaderno, o le toglie con "
        "content vuoto. I numeri vengono da {q}read_file o {q}search_content; nel testo nuovo non "
        "vanno messi.",
        {
            "path": "Percorso della nota.",
            "start_line": "Prima riga da sostituire, da 1.",
            "end_line": "Ultima riga da sostituire, compresa.",
            "content": "Le righe nuove; vuoto toglie l'intervallo.",
        },
    ),
    "list_files": Descrizione(
        "Elenca le note del quaderno, con dimensione e data dell'ultima modifica in UTC, e quanto spazio resta.",
        {
            "directory": "Cartella del quaderno da elencare, di serie la radice.",
            "pattern": "Filtro sui nomi, come *.md.",
            "recursive": "Scende anche nelle sottocartelle, fino a max_depth livelli.",
            "max_depth": "Profondita' massima con recursive, di serie 3.",
        },
    ),
    "search_content": Descrizione(
        "Cerca un testo nelle note del quaderno, senza distinguere maiuscole e minuscole. Per ogni "
        "nota da' il numero di riga della prima occorrenza, da leggere poi con {q}read_file.",
        {
            "query": "Il testo da cercare, cosi' com'e'.",
            "directory": "Cartella del quaderno in cui cercare, di serie tutto il quaderno.",
            "limit": "Quante note restituire al massimo, di serie 10.",
        },
    ),
    "move_file": Descrizione(
        "Sposta o rinomina una nota del quaderno; per ritirarla spostala in archive/.",
        {
            "src": "Percorso attuale della nota.",
            "dst": "Percorso nuovo; le cartelle si creano da sole.",
            "overwrite": "Con true sostituisce una nota gia' presente in dst; di serie false.",
        },
    ),
}


def descrivi(strumenti: Toolkit, prefisso: str, descrizioni: Mapping[str, Descrizione], **prefissi: str) -> None:
    """Mette le `descrizioni` sugli strumenti di `strumenti` gia' rinominati con `prefisso`.

    Agno ricava lo schema dei parametri dalla docstring a ogni run, salvo che
    lo strumento ne porti uno suo: qui si ricava una volta e si fissa con i
    testi italiani. `prefissi` riempie i segnaposto (`w`, `q`) dei testi. Un
    parametro descritto che lo schema non ha e' un errore: Agno l'ha cambiato.
    """
    for elenco in (strumenti.functions, strumenti.async_functions):
        for nome, descrizione in descrizioni.items():
            funzione = elenco.get(prefisso + nome)
            if funzione is None:
                continue
            if descrizione.testo:
                funzione.description = descrizione.testo.format(**prefissi)
            funzione.parameters = _schema(funzione, descrizione.parametri, **prefissi)


def _schema(funzione: Any, parametri: Mapping[str, str], **prefissi: str) -> dict[str, Any]:
    """Lo schema che Agno ricaverebbe per `funzione`, con le descrizioni dei parametri sostituite."""
    # Copia leggera: `process_entrypoint` riassegna i campi, non li modifica sul posto.
    copia = funzione.model_copy()
    copia.parameters = {"type": "object", "properties": {}, "required": []}
    copia.process_entrypoint()
    schema = copia.parameters
    proprieta = schema.get("properties", {})
    ignoti = set(parametri) - set(proprieta)
    if ignoti:
        raise ValueError(funzione.name + ": parametri descritti ma assenti dallo schema: " + ", ".join(sorted(ignoti)))
    for nome, voce in proprieta.items():
        voce.pop("description", None)
        if nome in parametri:
            voce["description"] = parametri[nome].format(**prefissi)
    return schema
