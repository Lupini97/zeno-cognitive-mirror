# Mind scan — sostituisce v5_deprecato/mind.py per la parte "lacune".
#
# V5 generava anche schede-tema in Markdown (memoria_elaborata/profilo/temi/);
# quella parte non è ricostruita qui (il V6 non ha più bisogno di schede
# derivate: le asserzioni in zeno.db SONO la scheda). Questo modulo copre
# solo la scrittura di stats/lacune_aperte.json, che la dashboard legge
# direttamente — stesso schema del V5, per compatibilità con la UI esistente.
#
# Una lacuna V6 = un topic della tassonomia con poco pensiero ESPLICITO/
# DIALOGICO registrato (i libri letti non colmano una lacuna: vedi
# briefing.py, stessa logica). Idempotente: non duplica lacune già aperte
# né quelle che l'utente ha già rifiutato dalla chat.

import json
import os
import re
from datetime import datetime
from zeno_engine import lacune as lacune_mod
from zeno_engine.config import BRAIN

STATS_DIR = os.path.join(BRAIN, 'stats')
# In DOCUMENTI, non asserzioni (peso del documento, l'utente 2026-09-28): un
# solo scritto lungo non toglie la lacuna.
SOGLIA_LACUNA = 5


def _norm(t: str):
    # Confronto per contenuto (zeno_engine/lacune.py), non per testo intero.
    return lacune_mod.chiave(t) if (t or '').strip() else ''


def _lacune_da_topic(con) -> list[dict]:
    righe = con.execute("""
        SELECT t.name, COUNT(DISTINCT cd.source_id) AS n FROM topics t
        LEFT JOIN assertion_topics at ON at.topic_id = t.id
        LEFT JOIN assertions a ON a.id = at.assertion_id
            AND a.chunk_id IN (SELECT c.id FROM chunks c
                               JOIN sources s ON s.id = c.source_id
                               WHERE s.strato IN ('esplicito', 'dialogico'))
            AND COALESCE(a.voce, '') <> 'narratore'
        LEFT JOIN chunks cd ON cd.id = a.chunk_id
        WHERE t.parent_id IS NULL
        GROUP BY t.id HAVING n < ? ORDER BY n ASC""", (SOGLIA_LACUNA,)).fetchall()
    lacune = []
    for nome, n in righe:
        if n == 0:
            testo = (f"Il tema \"{nome}\" non ha ancora nessuna traccia del tuo "
                     f"pensiero diretto (nessuna asserzione esplicita/dialogica).")
        else:
            testo = (f"Il tema \"{nome}\" è quasi assente dal tuo pensiero diretto "
                     f"(solo {n} tuoi documenti lo toccano).")
        lacune.append({'testo': testo, 'query_originale': '', 'area': nome})
    return lacune


def aggiorna_lacune_aperte(con, cartella: str = STATS_DIR) -> int:
    lacune_path = os.path.join(cartella, 'lacune_aperte.json')
    rifiutate_path = os.path.join(cartella, 'lacune_rifiutate.json')

    try:
        lacune = json.load(open(lacune_path, encoding='utf-8')) \
                 if os.path.exists(lacune_path) else []
    except Exception:
        lacune = []

    rifiutate_norms = set()
    if os.path.exists(rifiutate_path):
        try:
            for e in json.load(open(rifiutate_path, encoding='utf-8')):
                t = _norm(e.get('testo', ''))
                if t:
                    rifiutate_norms.add(t)
        except Exception:
            pass

    esistenti = {_norm(l.get('testo', '')) for l in lacune}
    aggiunte = 0
    for nuova in _lacune_da_topic(con):
        tn = _norm(nuova['testo'])
        if tn in esistenti or tn in rifiutate_norms:
            continue
        lacune.append({
            'testo': nuova['testo'],
            'query_originale': nuova['query_originale'],
            'data_prima_rilevazione': datetime.now().isoformat(),
            'fonte': 'mind_scan_v6',
            'area': nuova['area'],
        })
        esistenti.add(tn)
        aggiunte += 1

    os.makedirs(cartella, exist_ok=True)
    with open(lacune_path, 'w', encoding='utf-8') as f:
        json.dump(lacune, f, ensure_ascii=False, indent=2)
    return aggiunte


def dovuto(cfg: dict, cartella: str = STATS_DIR) -> bool:
    globale = cfg.get('_globale') or {}
    freq = globale.get('mind', {}).get('frequenza_giorni', 5)
    marker = os.path.join(cartella, 'mind_last_run.txt')
    if not os.path.exists(marker):
        return True
    try:
        ultimo = datetime.fromisoformat(open(marker).read().strip())
    except Exception:
        return True
    return (datetime.now() - ultimo).days >= freq


def segna_eseguito(cartella: str = STATS_DIR):
    os.makedirs(cartella, exist_ok=True)
    with open(os.path.join(cartella, 'mind_last_run.txt'), 'w') as f:
        f.write(datetime.now().isoformat())
