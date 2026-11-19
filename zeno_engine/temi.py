# Temi prioritari proposti e cadenza del job settimanale sui temi.
#
#   proponi_prioritari   — classifica i topic per volume + crescita recente
#     (asserzioni da fonti ingerite di recente = "cosa stai alimentando ora").
#     → stats/prioritari_proposti.json
#
# I temi NUOVI non si propongono più da qui. Fino al 28/09/2026 li proponeva
# proponi_temi_nuovi() nominando i cluster di entità (co-menzione): L'utente le
# ha giudicate proposte di cattiva qualità ("Valutazione Vini" costruito su
# Libia e Calvino) e sono state tolte, con la loro conferma/rifiuto. Ora le
# propone revisione_temi.genera(), dal contenuto delle asserzioni pesato per
# documento, e l'utente le accetta o rifiuta dalla dashboard.

import json
import os
from datetime import datetime, timedelta
from zeno_engine.config import BRAIN

STATS_DIR = os.path.join(BRAIN, 'stats')


def proponi_prioritari(con, cfg: dict, cartella: str = STATS_DIR,
                       giorni_recenti: int = 30, top_n: int = 5,
                       verboso: bool = True) -> int:
    """Classifica i topic per volume + crescita recente; suggerisce i top_n."""
    cutoff = (datetime.now() - timedelta(days=giorni_recenti)).strftime('%Y-%m-%d %H:%M:%S')
    volume = dict(con.execute("""
        SELECT t.name, COUNT(*) FROM assertion_topics at JOIN topics t ON t.id=at.topic_id
        WHERE t.parent_id IS NULL GROUP BY t.id"""))
    recente = dict(con.execute("""
        SELECT t.name, COUNT(*) FROM assertion_topics at
        JOIN topics t ON t.id=at.topic_id
        JOIN assertions a ON a.id=at.assertion_id
        JOIN chunks c ON c.id=a.chunk_id JOIN sources s ON s.id=c.source_id
        WHERE t.parent_id IS NULL AND s.ingested_at >= ?
        GROUP BY t.id""", (cutoff,)))
    if not volume:
        return 0
    max_v = max(volume.values()) or 1
    max_r = max(recente.values()) if recente else 1

    prio_attuali = {t.lower() for t in (cfg.get('_globale', {}).get('temi', {}) or {}).get('prioritari', [])}
    classifica = []
    for nome, v in volume.items():
        r = recente.get(nome, 0)
        score = 0.6 * (v / max_v) + 0.4 * (r / max_r)
        classifica.append({'tema': nome, 'asserzioni': v, 'recenti': r,
                           'score': round(score, 3),
                           'gia_prioritario': nome.lower() in prio_attuali})
    classifica.sort(key=lambda x: -x['score'])
    suggeriti = [c['tema'] for c in classifica[:top_n]]
    out = {'suggeriti': suggeriti, 'classifica': classifica,
           'generato': datetime.now().isoformat(timespec='seconds')}
    os.makedirs(cartella, exist_ok=True)
    with open(os.path.join(cartella, 'prioritari_proposti.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    if verboso:
        print(f'  prioritari suggeriti: {", ".join(suggeriti)}')
    return len(suggeriti)


# ── Cadenza: proposte rigenerate settimanalmente da `zeno sync` ─────────────
# Risposta alla domanda dell'utente (2026-07-22): i temi si propongono in DUE
# momenti. (1) Quando aggiungi documenti: `zeno sync` estrae e CLASSIFICA le
# asserzioni nuove (col limbo: se non c'è un tema adatto restano non
# classificate). (2) Ogni settimana: `zeno sync` rilancia community + proposte
# temi/prioritari sull'archivio aggiornato, e riconsidera il limbo. Così le
# proposte riflettono ciò che hai aggiunto senza che tu debba lanciare nulla.
from datetime import datetime as _dt

def dovuto(cfg: dict, cartella: str = STATS_DIR) -> bool:
    marker = os.path.join(cartella, 'temi_last_run.txt')
    if not os.path.exists(marker):
        return True
    try:
        ultimo = _dt.fromisoformat(open(marker).read().strip())
    except Exception:
        return True
    return (_dt.now() - ultimo).days >= 7


def segna_eseguito(cartella: str = STATS_DIR):
    os.makedirs(cartella, exist_ok=True)
    with open(os.path.join(cartella, 'temi_last_run.txt'), 'w') as f:
        f.write(_dt.now().isoformat())
