# zeno_engine — il motore di Zeno: lettura dei documenti, asserzioni, temi,
# recupero e risposte, sopra un solo file SQLite (zeno.db, con sqlite-vec e
# FTS5). I modelli girano nel demone MLX locale (zeno_engine.mlx_server).
#
# Nessun modulo di questo pacchetto manda testi fuori dal computer: l'unico
# processo esterno è il demone MLX su localhost.

# Numero di versione unico: lo mostrano la schermata iniziale, la chat, la
# dashboard, avvia_zeno.sh e `python -m zeno_engine --version`. Va cambiato
# insieme a version in CITATION.cff.
__version__ = '0.2.1'
