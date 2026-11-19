# Journal V6 — diario cognitivo periodico (settimanale) dal motore epistemico.
#
# Il tab "Journal" della dashboard legge memoria_elaborata/snapshot_cognitivi/
# journal_*.md. Era una feature V5 (mind.py) mai portata: qui la ricostruisco
# dai dati puliti di zeno.db. A differenza di STATO.md (fotografia corrente,
# sovrascritta, iniettata nel contesto di ask) e del BRIEFING (prompt
# giornaliero "cosa lavorare"), il journal è una RETROSPETTIVA datata e
# archiviata: cosa contiene la tua mente questa settimana, cosa si è aggiunto,
# cosa resta aperto. Zero LLM: solo SQL, così si genera sempre.

import os
from datetime import datetime, timedelta
from zeno_engine.config import BRAIN

DIR_JOURNAL = os.path.join(BRAIN, 'memoria_elaborata/snapshot_cognitivi')
STATS_DIR = os.path.join(BRAIN, 'stats')


def _settimana_iso(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    y, w, _ = dt.isocalendar()
    return f'{y}-W{w:02d}'


def genera(con, cfg: dict, cartella: str = DIR_JOURNAL) -> str:
    oggi = datetime.now()
    sett = _settimana_iso(oggi)
    cutoff7 = (oggi - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')

    temi = con.execute("""
        SELECT t.name, COUNT(DISTINCT c.source_id) FROM assertion_topics at
        JOIN topics t ON t.id=at.topic_id JOIN assertions a ON a.id=at.assertion_id
        JOIN chunks c ON c.id=a.chunk_id
        WHERE t.parent_id IS NULL GROUP BY t.id ORDER BY 2 DESC LIMIT 6""").fetchall()
    posizioni = con.execute("""
        SELECT a.text, a.stance FROM assertions a
        JOIN chunks c ON c.id=a.chunk_id JOIN sources s ON s.id=c.source_id
        WHERE s.strato IN ('esplicito','dialogico') AND a.kind='position'
          AND COALESCE(a.voce, '') <> 'narratore'
        ORDER BY a.id DESC LIMIT 6""").fetchall()
    contraddizioni = con.execute("""
        SELECT a1.text, a2.text FROM edges e
        JOIN assertions a1 ON a1.id=e.src_id JOIN assertions a2 ON a2.id=e.dst_id
        WHERE e.relation='contradicts' ORDER BY e.weight DESC LIMIT 4""").fetchall()
    nuove_fonti = con.execute("""
        SELECT title, ingested_at FROM sources
        WHERE ingested_at >= ? ORDER BY ingested_at DESC LIMIT 8""", (cutoff7,)).fetchall()
    n_ass = con.execute('SELECT COUNT(*) FROM assertions').fetchone()[0]
    n_ent = con.execute('SELECT COUNT(*) FROM entities').fetchone()[0]

    # lacune aperte (file curato)
    lacune = []
    import json
    import re as _re
    lp = os.path.join(STATS_DIR, 'lacune_aperte.json')
    if os.path.exists(lp):
        try:
            visti = set()
            for l in json.load(open(lp, encoding='utf-8')):
                # prima riga, troncata: alcune lacune vecchie contengono un
                # intero dump di sessione maieutica → si mostra solo l'incipit
                testo = _re.sub(r'\s+', ' ', (l.get('testo', '') or '').split('\n')[0]).strip()[:160]
                chiave = testo.lower()[:60]
                if testo and chiave not in visti:
                    visti.add(chiave)
                    lacune.append(testo)
            lacune = lacune[:6]
        except Exception:
            lacune = []
    # temi nuovi proposti (revisione_temi), in attesa del sì dell'utente
    try:
        from zeno_engine import revisione_temi
        _prop = revisione_temi.leggi_proposte()
        temi_prop = [p.get('proposta_nome', '') for k in ('madri_nuove', 'figli_nuovi')
                     for p in _prop.get(k, [])][:8]
    except Exception:
        temi_prop = []

    r = [f'---', f'tipo: journal_v6', f'settimana: {sett}',
         f'data: {oggi.strftime("%Y-%m-%d %H:%M")}', f'---', '',
         f'# Journal — settimana {sett}', '',
         f'*Fotografia dell\'archivio: {n_ass} asserzioni, {n_ent} entità.*', '',
         '## Temi caldi']
    r += [f'- **{n}** — {c} documenti' for n, c in temi]
    r += ['', '## Le tue posizioni (ultime registrate)']
    r += [f'- [{s or "?"}] {t}' for t, s in posizioni] or ['- (nessuna posizione recente)']
    r += ['', '## Tensioni aperte']
    r += [f'- «{a[:85]}» **VS** «{b[:85]}»' for a, b in contraddizioni] or ['- Nessuna contraddizione rilevata.']
    r += ['', '## Lacune aperte']
    r += [f'- {l}' for l in lacune] or ['- Nessuna lacuna aperta.']
    if temi_prop:
        r += ['', '## Temi emergenti da vagliare']
        r += [f'- {t}' for t in temi_prop]
    r += ['', '## Aggiunte degli ultimi 7 giorni']
    r += [f'- {t[:70]} ({d[:10]})' for t, d in nuove_fonti] or ['- Nessuna aggiunta recente.']
    r.append('')

    os.makedirs(cartella, exist_ok=True)
    percorso = os.path.join(cartella, f'journal_{sett}.md')
    with open(percorso, 'w', encoding='utf-8') as f:
        f.write('\n'.join(r))
    return percorso


def dovuto(cfg: dict, cartella: str = STATS_DIR) -> bool:
    """Un journal a settimana (di default)."""
    marker = os.path.join(cartella, 'journal_last_run.txt')
    if not os.path.exists(marker):
        return True
    try:
        ultimo = datetime.fromisoformat(open(marker).read().strip())
    except Exception:
        return True
    return _settimana_iso(datetime.now()) != _settimana_iso(ultimo)


def segna_eseguito(cartella: str = STATS_DIR):
    os.makedirs(cartella, exist_ok=True)
    with open(os.path.join(cartella, 'journal_last_run.txt'), 'w') as f:
        f.write(datetime.now().isoformat())
