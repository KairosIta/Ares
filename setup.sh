#!/usr/bin/env bash
#
# Ricostruzione dell'ambiente
# ===========================
# Crea il virtualenv, installa le dipendenze bloccate in uv.lock, installa
# Ares nel venv, mette `ares` sul PATH, porta in `~/.ares` lo stato di un
# clone precedente e verifica che Ollama sia in piedi con i modelli giusti.
#
#     ./setup.sh
#
# Idempotente: se il venv c'e' gia' lo allinea invece di ricrearlo. Lo stato
# appreso e gli snapshot non vengono toccati, salvo lo spostamento una tantum
# in `~/.ares` di quelli lasciati dentro il clone da una versione precedente.

set -euo pipefail
cd "$(dirname "$0")"

# `.env` puo' contenere identita', percorsi e future impostazioni locali. Non
# e' versionato e, come lo stato appreso, non deve nascere leggibile dagli
# altri utenti della macchina. Su Windows vale invece la DACL ereditata.
if [ -f .env ]; then
    chmod 600 .env
fi

if ! command -v uv > /dev/null; then
    echo "Manca uv. Installalo con:"
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# `sync` porta il venv esattamente com'e' scritto in uv.lock: crea `.venv`
# se manca, sulla versione di Python in `.python-version`, rimuove i residui
# di installazioni manuali e installa Ares in editable, cosi' i comandi
# `ares` e gli alias `ares-backup`... compaiono in `.venv/bin`. Il lock porta gli hash
# degli artefatti e uv li verifica: se un file scaricato non corrisponde
# l'installazione si ferma, invece di riuscire con altro dentro.
#
# `--locked` rifiuta un lock non allineato al pyproject invece di
# riscriverlo in silenzio; `--no-dev` lascia fuori ruff, mypy e coverage, che
# su una macchina che usa soltanto Ares non servono (CONTRIBUTING spiega come
# averli).
echo "Installo le dipendenze bloccate."
uv sync --locked --no-dev

# `sync` allinea cio' che e' installato al lock; `check` verifica anche che i
# requisiti dichiarati dai pacchetti installati siano compatibili fra loro.
# setup.ps1 fa lo stesso controllo: i due percorsi di installazione devono
# rifiutare lo stesso ambiente incoerente.
uv pip check --python .venv/bin/python

# `ares` da qualunque cartella: un link in `~/.local/bin` al comando del venv.
# Un link e non un `uv tool install`, perche' quello risolverebbe le
# dipendenze da capo senza guardare uv.lock: il comando globale deve essere
# esattamente l'ambiente bloccato, e seguire il codice del clone a ogni pull.
# `ARES_BIN_DIR` sceglie un'altra directory.
BIN_DIR="${ARES_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN_DIR"
ln -sfn "$PWD/.venv/bin/ares" "$BIN_DIR/ares"
echo "Comando globale: $BIN_DIR/ares"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        echo "  $BIN_DIR non e' nel PATH. Aggiungi al profilo della shell:"
        echo "      export PATH=\"$BIN_DIR:\$PATH\""
        ;;
esac

# Lo stato di un clone precedente, da `tmp/` e `../ares-backup` a `~/.ares`.
# Non fa niente se e' gia' li' o se non c'e' niente da spostare.
echo
.venv/bin/ares migrate

echo
if ! .venv/bin/ares preflight; then
    echo
    echo "Le dipendenze sono a posto: manca qualcosa sul lato Ollama."
    exit 1
fi
