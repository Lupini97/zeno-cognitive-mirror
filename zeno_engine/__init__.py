# scripts/zeno_engine/__init__.py — Motore epistemico di Zeno (V6).
#
# Sostituisce lo stack V5 (LlamaIndex+Qdrant), congelato e intatto in
# scripts/v5_deprecato/. Fonte di verità unica: BRAIN/zeno.db
# (SQLite + sqlite-vec). Vedi BRAIN/AUDIT.md per il razionale.
#
# Runtime: BRAIN/.venv_zeno (Python 3.14 Homebrew) — venv dedicato
# perché il Python 3.11 pyenv del V5 è compilato senza
# enable_load_extension, requisito di sqlite-vec. Il venv V5 non è stato toccato.
#
# ZERO CLOUD: nessun modulo di questo pacchetto apre connessioni di rete;
# l'unico processo esterno è il demone MLX su localhost.

__version__ = '0.1.0'
