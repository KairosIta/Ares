#!/usr/bin/env bash
#
# Ricostruzione dell'ambiente
# ===========================
# Crea il virtualenv, installa le dipendenze bloccate in requirements.txt e
# verifica che Ollama sia in piedi con i modelli giusti.
#
#     ./setup.sh
#
# Idempotente: se il venv c'e' gia' lo allinea invece di ricrearlo. Non
# tocca `tmp/`, dove vive tutto lo stato appreso, ne' gli snapshot locali.

set -euo pipefail
cd "$(dirname "$0")"

VERSIONE_PYTHON=3.12

if ! command -v uv > /dev/null; then
    echo "Manca uv. Installalo con:"
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

if [ -d .venv ]; then
    echo "Virtualenv gia' presente, lo allineo."
else
    echo "Creo il virtualenv su Python ${VERSIONE_PYTHON}."
    uv venv --python "${VERSIONE_PYTHON}" .venv
fi

# sync e non install: il venv finisce esattamente com'e' scritto in
# requirements.txt, senza i residui di installazioni manuali precedenti.
# E' la differenza tra un ambiente riproducibile e uno che funziona qui.
# Il lock porta gli hash degli artefatti, quindi qui uv non verifica solo che
# la versione sia quella giusta ma che il file scaricato sia quello: se non
# corrisponde l'installazione si ferma, invece di riuscire con altro dentro.
echo "Installo le dipendenze bloccate."
uv pip sync --python .venv/bin/python requirements.txt

# uv pip sync considera `agno==2.9.0` gia' soddisfatto quando agno e'
# installato in editable dal clone locale, perche' la versione coincide: il
# venv continuerebbe a girare su ../agno mentre requirements.txt dice PyPI.
# Due ambienti che si somigliano e divergono senza dirlo.
if ! .venv/bin/python - <<'PY'
import pathlib
import sys

import agno

venv = pathlib.Path(".venv").resolve()
sys.exit(0 if pathlib.Path(agno.__file__).is_relative_to(venv) else 1)
PY
then
    echo
    echo "agno veniva da fuori dal venv (installazione editable): lo reinstallo."
    uv pip install --python .venv/bin/python --reinstall-package agno -r requirements.txt
fi

echo
if ! .venv/bin/python preflight.py; then
    echo
    echo "Le dipendenze sono a posto: manca qualcosa sul lato Ollama."
    exit 1
fi
