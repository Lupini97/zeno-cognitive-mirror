# Graph scan — sostituisce v5_deprecato/graph.py per grafo_connessioni.json.
#
# Schema compatibile col V5 (dashboard.py::tab_grafo lo legge senza modifiche:
# meta{n_nodi,n_archi,generato}, nodi{id:{label,tipo,area,peso,size,color}},
# archi[{source,target,tipo,peso,motivo,data,confermato}]) ma i nodi sono
# TOPIC ed ENTITÀ di zeno.db, gli archi sono `contradicts`/`relates_to`/
# `mentions` — non più "schede tema" e "cross_link" V5.
#
# Il grafo 3D (zeno_engine/grafo3d.py, tab dedicata) resta la vista principale
# e più ricca; questo genera solo l'equivalente 2D per compatibilità con la
# tab storica e con archi_confermati.json (giudizi manuali dell'utente).

import json
import os
from datetime import datetime
from zeno_engine.config import BRAIN

STATS_DIR = os.path.join(BRAIN, 'stats')

COLORI_TOPIC = {
    'esplicito_denso': '#1E3A5F', 'esplicito_scarso': '#C0392B',
}


def _nodi_topic(con) -> dict:
    nodi = {}
    for tid, nome in con.execute('SELECT id, name FROM topics WHERE parent_id IS NULL'):
        n = con.execute("""SELECT COUNT(DISTINCT s.id) FROM assertions a
            JOIN chunks c ON c.id=a.chunk_id JOIN sources s ON s.id=c.source_id
            WHERE a.topic_id=? AND s.strato IN ('esplicito','dialogico')
              AND COALESCE(a.voce, '') <> 'narratore'""",
            (tid,)).fetchone()[0]
        nodi[f'tema_{tid}'] = {
            'id': f'tema_{tid}', 'label': nome, 'tipo': 'tema', 'area': 'inferito',
            'data': datetime.now().strftime('%Y-%m-%d'), 'peso': 1.0,
            'filepath': '', 'tags': [],
            'size': max(6, min(30, n)),
            'color': COLORI_TOPIC['esplicito_scarso'] if n < 15 else COLORI_TOPIC['esplicito_denso'],
            'prioritario': n >= 15,
        }
    return nodi


def _nodi_entita(con, min_menzioni: int = 3) -> dict:
    nodi = {}
    righe = con.execute("""
        SELECT en.id, en.name, en.kind, COUNT(*) AS n FROM entities en
        JOIN edges e ON e.dst_type='entity' AND e.dst_id=en.id AND e.relation='mentions'
        GROUP BY en.id HAVING n >= ? ORDER BY n DESC LIMIT 300""", (min_menzioni,)).fetchall()
    for eid, nome, kind, n in righe:
        nodi[f'entita_{eid}'] = {
            'id': f'entita_{eid}', 'label': nome, 'tipo': 'emergente', 'area': kind,
            'data': datetime.now().strftime('%Y-%m-%d'), 'peso': min(2.0, n / 10),
            'filepath': '', 'tags': [kind],
            'size': max(4, min(20, n)), 'color': '#7D3C98', 'prioritario': False,
        }
    return nodi


def _archi(con, nodi_topic_id_map: dict, nodi_entita_id_map: dict) -> list[dict]:
    archi = []
    oggi = datetime.now().strftime('%Y-%m-%d')

    # contradicts tra asserzioni → arco tema-tema (stesso topic, tensione interna)
    for a1, a2, w, nota in con.execute("""
            SELECT a1.topic_id, a2.topic_id, e.weight, e.note FROM edges e
            JOIN assertions a1 ON a1.id=e.src_id JOIN assertions a2 ON a2.id=e.dst_id
            WHERE e.relation='contradicts'"""):
        if a1 and a1 in nodi_topic_id_map:
            archi.append({'source': f'tema_{a1}', 'target': f'tema_{a1}',
                          'tipo': 'dialogico', 'peso': w or 0.8,
                          'motivo': (nota or 'contraddizione rilevata')[:120],
                          'data': oggi, 'confermato': False})

    # mentions asserzione→entità: aggrega in arco tema-entità
    visti = set()
    for topic_id, entity_id, n in con.execute("""
            SELECT a.topic_id, e.dst_id, COUNT(*) FROM edges e
            JOIN assertions a ON a.id = e.src_id
            WHERE e.relation='mentions' AND e.dst_type='entity' AND a.topic_id IS NOT NULL
            GROUP BY a.topic_id, e.dst_id HAVING COUNT(*) >= 2"""):
        src, dst = f'tema_{topic_id}', f'entita_{entity_id}'
        if src not in nodi_topic_id_map or dst not in nodi_entita_id_map:
            continue
        chiave = (src, dst)
        if chiave in visti:
            continue
        visti.add(chiave)
        archi.append({'source': src, 'target': dst, 'tipo': 'semantico',
                      'peso': min(1.0, n / 10), 'motivo': f'{n} menzioni',
                      'data': oggi, 'confermato': False})
    return archi


def genera(con, cartella: str = STATS_DIR) -> str:
    nodi_topic = _nodi_topic(con)
    nodi_entita = _nodi_entita(con)
    nodi = {**nodi_topic, **nodi_entita}
    archi = _archi(con, nodi_topic, nodi_entita)

    n_lacune = con.execute("""SELECT COUNT(*) FROM topics t
        LEFT JOIN assertions a ON a.topic_id=t.id
            AND a.chunk_id IN (SELECT c.id FROM chunks c JOIN sources s ON s.id=c.source_id
                              WHERE s.strato IN ('esplicito','dialogico'))
            AND COALESCE(a.voce, '') <> 'narratore'
        LEFT JOIN chunks cd ON cd.id = a.chunk_id
        WHERE t.parent_id IS NULL GROUP BY t.id HAVING COUNT(DISTINCT cd.source_id) < 5""").fetchall()

    dati = {
        'meta': {'generato': datetime.now().isoformat(), 'n_nodi': len(nodi),
                 'n_archi': len(archi), 'n_lacune': len(n_lacune), 'versione': '6.0'},
        'nodi': nodi, 'archi': archi, 'metriche': {},
        'lacune': [], 'legenda': {'fonte': 'zeno_engine.graph (zeno.db)'},
    }
    os.makedirs(cartella, exist_ok=True)
    percorso = os.path.join(cartella, 'grafo_connessioni.json')
    with open(percorso, 'w', encoding='utf-8') as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)

    # history: append leggero (n_nodi/n_archi nel tempo), come V5
    hist_path = os.path.join(cartella, 'grafo_history.json')
    try:
        storia = json.load(open(hist_path, encoding='utf-8')) if os.path.exists(hist_path) else []
    except Exception:
        storia = []
    storia.append({'data': dati['meta']['generato'][:10],
                   'n_nodi': len(nodi), 'n_archi': len(archi)})
    with open(hist_path, 'w', encoding='utf-8') as f:
        json.dump(storia[-52:], f, ensure_ascii=False, indent=2)
    return percorso


def dovuto(cfg: dict, cartella: str = STATS_DIR) -> bool:
    globale = cfg.get('_globale') or {}
    freq = globale.get('graph', {}).get('frequenza_giorni', 5)
    marker = os.path.join(cartella, 'graph_last_run.txt')
    if not os.path.exists(marker):
        return True
    try:
        ultimo = datetime.fromisoformat(open(marker).read().strip())
    except Exception:
        return True
    return (datetime.now() - ultimo).days >= freq


def segna_eseguito(cartella: str = STATS_DIR):
    os.makedirs(cartella, exist_ok=True)
    with open(os.path.join(cartella, 'graph_last_run.txt'), 'w') as f:
        f.write(datetime.now().isoformat())
