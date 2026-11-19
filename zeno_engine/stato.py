# STATO.md — fotografia sintetica del cervello di Zeno (brief §9, Fase D.12).
#
# Rigenerato a fine di ogni batch; il Router lo carica SEMPRE come contesto
# di base in `ask`. Per questo DEVE restare piccolo (~1.500 caratteri):
# è un costo pagato a ogni query. Nessun LLM: solo SQL, costo zero.

import os
from datetime import datetime
from zeno_engine.config import BRAIN

PERCORSO_STATO = os.path.join(BRAIN, 'STATO.md')
MAX_CHARS_CONTESTO = 1500       # quota di STATO.md iniettata nel contesto di ask


def genera(con, percorso: str = PERCORSO_STATO) -> str:
    oggi = datetime.now().strftime('%Y-%m-%d %H:%M')

    temi = con.execute("""
        SELECT t.name, COUNT(DISTINCT c.source_id) FROM assertion_topics at
        JOIN topics t ON t.id = at.topic_id JOIN assertions a ON a.id = at.assertion_id
        JOIN chunks c ON c.id = a.chunk_id
        GROUP BY t.name ORDER BY 2 DESC LIMIT 5""").fetchall()
    contraddizioni = con.execute("""
        SELECT a1.text, a2.text FROM edges e
        JOIN assertions a1 ON a1.id = e.src_id JOIN assertions a2 ON a2.id = e.dst_id
        WHERE e.relation = 'contradicts' ORDER BY e.weight DESC LIMIT 4""").fetchall()
    domande = con.execute("""
        SELECT text FROM assertions WHERE kind = 'question'
        ORDER BY asserted_at DESC LIMIT 4""").fetchall()
    propositi = con.execute("""
        SELECT text, asserted_at FROM assertions WHERE kind IN ('decision', 'intention')
        ORDER BY asserted_at DESC LIMIT 4""").fetchall()
    ingestioni = con.execute("""
        SELECT title, ingested_at FROM sources ORDER BY ingested_at DESC LIMIT 3""").fetchall()
    # Lacune = assenza del pensiero dell'utente (esplicito/dialogico), non di letture.
    lacune = con.execute("""
        SELECT t.name, COUNT(DISTINCT cd.source_id) AS n FROM topics t
        LEFT JOIN assertion_topics at ON at.topic_id = t.id
        LEFT JOIN assertions a ON a.id = at.assertion_id
            AND a.chunk_id IN (SELECT c.id FROM chunks c
                               JOIN sources s ON s.id = c.source_id
                               WHERE s.strato IN ('esplicito', 'dialogico'))
            AND COALESCE(a.voce, '') <> 'narratore'
        LEFT JOIN chunks cd ON cd.id = a.chunk_id
        WHERE t.parent_id IS NULL GROUP BY t.id HAVING n < 5 ORDER BY n""").fetchall()

    righe = [f'# STATO DI ZENO — {oggi}', '',
             '## Temi caldi']
    righe += [f'- {n} ({c} documenti)' for n, c in temi]
    righe += ['', '## Contraddizioni aperte']
    righe += [f'- «{a[:90]}» VS «{b[:90]}»' for a, b in contraddizioni] or ['- nessuna']
    righe += ['', '## Domande pendenti nel corpus']
    righe += [f'- {d[0][:110]}' for d in domande] or ['- nessuna']
    if propositi:
        righe += ['', '## Propositi dichiarati (da verificare nel tempo)']
        righe += [f'- ({d or "?"}) {t[:100]}' for t, d in propositi]
    righe += ['', '## Lacune tematiche']
    righe += [f'- {n}: {c} documenti tuoi' for n, c in lacune]
    righe += ['', '## Ultime ingestioni']
    righe += [f'- {t[:70]} ({d})' for t, d in ingestioni]
    righe.append('')

    testo = '\n'.join(righe)
    with open(percorso, 'w', encoding='utf-8') as f:
        f.write(testo)
    return percorso


def per_contesto(percorso: str = PERCORSO_STATO) -> str:
    """STATO.md troncato per il contesto di ask ('' se non esiste ancora)."""
    if not os.path.exists(percorso):
        return ''
    with open(percorso, 'r', encoding='utf-8') as f:
        return f.read()[:MAX_CHARS_CONTESTO]
