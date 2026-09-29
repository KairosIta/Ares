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

# `.env` contiene impostazioni locali: privato come lo stato appreso.
if [ -f .env ]; then
    chmod 600 .env
fi

if ! command -v uv > /dev/null; then
    echo "Manca uv. Installalo con:"
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Porta il venv esattamente a uv.lock, hash verificati, con Ares in editable.
# `--locked` rifiuta un lock non allineato; `--no-dev` lascia fuori gli
# strumenti di sviluppo (CONTRIBUTING spiega come averli).
echo "Installo le dipendenze bloccate."
uv sync --locked --no-dev

# Requisiti dei pacchetti installati compatibili fra loro, come in setup.ps1.
uv pip check --python .venv/bin/python

# `ares` da qualunque cartella: un link al comando del venv, non un
# `uv tool install`, che ignorerebbe uv.lock. `ARES_BIN_DIR` cambia directory.
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
