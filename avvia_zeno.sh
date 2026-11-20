#!/bin/bash
# avvia_zeno.sh — avvio di Zeno dal terminale: chat e dashboard nel browser.
# zeno_launcher.py (la finestra dell'app) fa la stessa sequenza per conto suo.
# Fermare Zeno: lsof -ti tcp:8501,8502,11500 | xargs kill
#
# Sequenza: demone MLX → zeno sync (messa in pari) → chat :8501 → dashboard :8502.

# BRAIN è la cartella che contiene scripts/ (o ZENO_BRAIN, se impostata).
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"
BRAIN="${ZENO_BRAIN:-$(dirname "$SCRIPTS")}"
PY="$BRAIN/.venv_zeno/bin/python"

if [ ! -x "$PY" ]; then
    echo "ERRORE: ambiente Python non trovato ($PY). Lancia prima ./installa.sh" >&2
    exit 1
fi

# 1. Demone MLX (se non già attivo). Qui c'era ancora `ollama serve`, rimasto
#    dal passaggio a MLX: Ollama non è più installato, il demone non partiva e
#    i job periodici col motore venivano rimandati a ogni passata (25/09/2026).
if ! curl -s -o /dev/null http://127.0.0.1:11500/api/ps; then
    (cd "$SCRIPTS" && nohup "$PY" -m zeno_engine.mlx_server >> /tmp/zeno_mlx.log 2>&1 &)
    for i in $(seq 1 60); do
        sleep 1
        curl -s -o /dev/null http://127.0.0.1:11500/api/ps && break
    done
fi

# 2. Messa in pari (nuovi file, CSV staging, embeddings) — mai bloccante
(cd "$SCRIPTS" && "$PY" -m zeno_engine sync --solo-locali > /tmp/zeno_sync.log 2>&1)

# 3. Libera le porte da sessioni precedenti
for porta in 8501 8502; do
    lsof -ti tcp:$porta 2>/dev/null | xargs kill -9 2>/dev/null
done

# 4. UI — zeno_app.py: chat + dashboard nella stessa finestra (pagine);
#    la dashboard resta anche su 8502 per chi la vuole separata.
cd "$SCRIPTS"
nohup "$PY" -m streamlit run zeno_app.py --server.port 8501 \
    --server.headless true --server.address localhost \
    > /tmp/zeno_chat.log 2>&1 &
nohup "$PY" -m streamlit run dashboard.py --server.port 8502 \
    --server.headless true --server.address localhost \
    > /tmp/zeno_dashboard.log 2>&1 &

VERSIONE=$(sed -n "s/^__version__ = '\(.*\)'/\1/p" "$SCRIPTS/zeno_engine/__init__.py")
echo "Zeno $VERSIONE avviato: chat http://localhost:8501 · dashboard http://localhost:8502"
