#!/bin/bash
# installa.sh — installa Zeno su un Mac con Apple Silicon.
#
#   ./installa.sh                 ambiente Python, cartelle, config e modelli
#   ./installa.sh --senza-modelli tutto tranne il download dei modelli (~4 GB)
#
# Struttura che crea, accanto a questa cartella (BRAIN = la cartella che
# contiene scripts/, oppure ZENO_BRAIN se impostata):
#
#   BRAIN/
#   ├── scripts/            questo codice
#   ├── .venv_zeno/         ambiente Python
#   ├── zeno_config.yaml    configurazione (copiata dall'esempio, poi è tua)
#   ├── documents/          i tuoi documenti in Markdown
#   ├── models/             i pesi dei modelli
#   └── zeno.db             l'archivio, creato al primo avvio
#
# Rilanciarlo è sicuro: non sovrascrive config, documenti né archivio.

set -e

SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
BRAIN="${ZENO_BRAIN:-$(dirname "$SCRIPTS")}"
VENV="$BRAIN/.venv_zeno"
MODELLI="mlx-community/Qwen3.5-4B-4bit mlx-community/bge-m3-mlx-fp16"

passo() { printf '\n== %s\n' "$1"; }
errore() { printf 'ERRORE: %s\n' "$1" >&2; exit 1; }

echo "Installo Zeno in: $BRAIN"
if [ -n "$ZENO_BRAIN" ] && [ "$ZENO_BRAIN" != "$(dirname "$SCRIPTS")" ]; then
    # ZENO_BRAIN impostata e diversa dalla cartella di questo codice: potrebbe
    # essere un'altra installazione di Zeno. Si procede solo su conferma.
    printf 'ZENO_BRAIN punta a una cartella diversa da quella di questo codice.\n'
    printf 'Scrivi "si" per installare lì: '
    read -r risposta || risposta=""
    [ "$risposta" = "si" ] || errore "Installazione annullata."
fi

passo "Controllo del computer"
[ "$(uname -s)" = "Darwin" ] || errore "Zeno per ora funziona solo su macOS."
[ "$(uname -m)" = "arm64" ] || errore "Serve un Mac con Apple Silicon (M1 o successivi): i modelli girano con MLX."
MEM_GB=$(( $(sysctl -n hw.memsize) / 1073741824 ))
echo "Memoria: ${MEM_GB} GB"
[ "$MEM_GB" -ge 16 ] || echo "Attenzione: Zeno è provato con 16 GB. Con meno memoria può essere lento."

passo "Python"
# Serve un Python il cui sqlite3 carichi estensioni (sqlite-vec): quello di
# sistema di macOS non lo permette. Di serie si cerca quello di Homebrew.
PY_BASE="${ZENO_PYTHON:-}"
if [ -z "$PY_BASE" ]; then
    for p in /opt/homebrew/bin/python3.14 /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3; do
        [ -x "$p" ] && PY_BASE="$p" && break
    done
fi
[ -n "$PY_BASE" ] || errore "Python di Homebrew non trovato. Installa Homebrew (https://brew.sh), poi: brew install python@3.14"
"$PY_BASE" - <<'EOF' || errore "Questo Python non va bene (serve 3.12 o più, con sqlite3 che carica estensioni). Indica un altro Python con ZENO_PYTHON=/percorso/python3"
import sqlite3, sys
assert sys.version_info >= (3, 12)
assert hasattr(sqlite3.connect(':memory:'), 'enable_load_extension')
EOF
echo "Uso $PY_BASE ($("$PY_BASE" --version))"

passo "Ambiente Python in $VENV"
[ -x "$VENV/bin/python" ] || "$PY_BASE" -m venv "$VENV"
"$VENV/bin/python" -m pip install -r "$SCRIPTS/requirements.txt"

passo "Cartelle e configurazione"
mkdir -p "$BRAIN"/documents/{riflessioni,pubblicazioni,libri,articoli_saggistica,formazione} \
         "$BRAIN"/{stats,memoria_elaborata/query_archiviate,obsidian_vault,import_staging,models}
if [ -f "$BRAIN/zeno_config.yaml" ]; then
    echo "zeno_config.yaml c'è già: lo lascio com'è."
else
    cp "$SCRIPTS/zeno_config.template.yaml" "$BRAIN/zeno_config.yaml"
    echo "Creato zeno_config.yaml. Apri il file e scrivi il tuo nome in identita.nome_utente."
fi

if [ "$1" != "--senza-modelli" ]; then
    passo "Modelli (circa 4 GB, una volta sola)"
    (cd "$SCRIPTS" && "$VENV/bin/python" -m zeno_engine.mlx_scarica $MODELLI)
fi

if ! command -v tesseract >/dev/null 2>&1; then
    passo "Facoltativo"
    echo "Per leggere i PDF fatti di immagini serve Tesseract: brew install tesseract tesseract-lang"
fi

passo "Fatto"
cat <<EOF
Metti i tuoi testi in $BRAIN/documents/:
  riflessioni/, pubblicazioni/   ciò che hai scritto tu (peso massimo)
  libri/, articoli_saggistica/, formazione/   ciò che hai letto o studiato
Poi avvia Zeno con:
  $SCRIPTS/avvia_zeno.sh
e apri http://localhost:8501
EOF
