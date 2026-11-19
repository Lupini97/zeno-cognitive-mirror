# Snapshot cognitivo periodico — sostituisce v5_deprecato/snapshot.py.
#
# Stesso schema JSON del V5 (dashboard.py lo consuma senza modifiche:
# vettori_totali, documenti, velocita_tematica, tag_piu_frequenti,
# memoria_elaborata, libri) ma i numeri vengono da zeno.db, non da Qdrant.
#
# Periodicità (brief V5, zeno_config.yaml: snapshot.frequenza_giorni, default
# 6): `dovuto()` dice se è ora di generarne uno nuovo; `zeno sync` lo chiama
# a ogni avvio, quindi lo snapshot torna automatico senza demoni dedicati.

import glob
import json
import os
import re
from datetime import datetime
from zeno_engine.config import BRAIN

STATS_DIR = os.path.join(BRAIN, 'stats')


def _conta_file(directory: str, estensioni: tuple = ('.md',)) -> tuple[int, dict]:
    if not os.path.exists(directory):
        return 0, {}
    totale, per_sub = 0, {}
    for root, _, files in os.walk(directory):
        n = len([f for f in files if os.path.splitext(f)[1] in estensioni
                and not f.startswith('._')])
        if n:
            rel = os.path.relpath(root, directory)
            per_sub[rel] = n
            totale += n
    return totale, per_sub


def _tag_frequenti(con, top_n: int = 30) -> dict:
    """Le entità più menzionate in zeno.db sostituiscono i tag da frontmatter."""
    righe = con.execute("""
        SELECT en.name, COUNT(*) AS n FROM entities en
        JOIN edges e ON e.dst_type='entity' AND e.dst_id = en.id AND e.relation='mentions'
        GROUP BY en.id ORDER BY n DESC LIMIT ?""", (top_n,)).fetchall()
    return {nome: n for nome, n in righe}


def _stato_libri(cfg: dict) -> dict:
    libri_dir = os.path.join(cfg['documents'], 'libri')
    conteggi = {'read': 0, 'to-read': 0, 'currently-reading': 0}
    voti = []
    for path in glob.glob(os.path.join(libri_dir, '*.md')):
        if os.path.basename(path).startswith('._'):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                head = f.read(2000)
        except OSError:
            continue
        m = re.search(r'^stato:\s*(\S+)', head, re.MULTILINE)
        stato = (m.group(1) if m else '').strip()
        if 'completato' in stato or 'letto' == stato:
            conteggi['read'] += 1
        elif 'lettura' in stato:
            conteggi['currently-reading'] += 1
        else:
            conteggi['to-read'] += 1
        mr = re.search(r'^rating:\s*([\d.]+)', head, re.MULTILINE)
        if mr and float(mr.group(1)) > 0:
            voti.append(float(mr.group(1)))
    conteggi['rating_medio'] = round(sum(voti) / len(voti), 2) if voti else 0
    return conteggi


def genera(con, cfg: dict, cartella: str = STATS_DIR) -> str:
    oggi = datetime.now().strftime('%Y-%m-%d')
    aree = ('libri', 'articoli_saggistica', 'formazione', 'riflessioni', 'pubblicazioni')
    documenti = {}
    for area in aree:
        tot, sub = _conta_file(os.path.join(cfg['documents'], area),
                               ('.md', '.pdf', '.docx', '.html', '.htm', '.txt'))
        documenti[area] = {'totale': tot, 'sottocartelle': sub}

    n_riflessioni, _ = _conta_file(os.path.join(cfg['documents'], 'riflessioni'))
    n_sessioni = len(glob.glob(os.path.join(BRAIN, 'memoria_elaborata/query_archiviate/*.json')))
    n_sintesi, _ = _conta_file(os.path.join(BRAIN, 'memoria_elaborata/sintesi'))

    dati = {
        'data': oggi,
        'timestamp': datetime.now().isoformat(),
        'motore': 'V6 (zeno.db)',
        'vettori_totali': con.execute('SELECT COUNT(*) FROM chunks').fetchone()[0],
        'asserzioni_totali': con.execute('SELECT COUNT(*) FROM assertions').fetchone()[0],
        'documenti': documenti,
        'velocita_tematica': {a: 0 for a in aree},
        'tag_piu_frequenti': _tag_frequenti(con),
        'memoria_elaborata': {'sintesi': n_sintesi, 'sessioni': n_sessioni,
                              'riflessioni': n_riflessioni},
        'libri': _stato_libri(cfg),
    }
    os.makedirs(cartella, exist_ok=True)
    percorso = os.path.join(cartella, f'snapshot_{oggi}.json')
    with open(percorso, 'w', encoding='utf-8') as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)
    return percorso


def dovuto(cfg: dict, cartella: str = STATS_DIR) -> bool:
    """True se manca uno snapshot di oggi, o l'ultimo è più vecchio di frequenza_giorni."""
    freq = (cfg.get('_globale') or {}).get('snapshot', {}).get('frequenza_giorni', 6)
    esistenti = sorted(glob.glob(os.path.join(cartella, 'snapshot_*.json')))
    if not esistenti:
        return True
    ultimo = os.path.basename(esistenti[-1])[len('snapshot_'):-len('.json')]
    try:
        giorni = (datetime.now() - datetime.strptime(ultimo, '%Y-%m-%d')).days
    except ValueError:
        return True
    return giorni >= freq
