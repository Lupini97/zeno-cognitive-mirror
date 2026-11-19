# Briefing — il "rapporto di intelligence su sé stessi".
#
# Genera briefing/BRIEFING_AAAA-MM-GG.md con:
#   1. lo stato del corpus (numeri);
#   2. le CONTRADDIZIONI rilevate dal Critico (con date: l'evoluzione del
#      pensiero nel tempo, non una pagella);
#   3. le LACUNE: temi della tassonomia con poche o nessuna asserzione;
#   4. 3 DOMANDE SOCRATICHE generate dal motore ask (qwen) a partire da
#      tensioni e lacune reali del corpus — mai da conoscenza esterna.
#
# Zeno non risponde al posto dell'utente: interroga. Questo file è il punto
# in cui lo specchio cognitivo si rivolge a lui.

import json
import os
from datetime import datetime
from zeno_engine.config import BRAIN

SOGLIA_LACUNA = 5           # sotto questi documenti un tema è "poco esplorato"
STATS_DIR = os.path.join(BRAIN, 'stats')

PROMPT_SOCRATICO = """Sei il Socratico di Zeno, lo specchio cognitivo di @UTENTE@.
Ti do le contraddizioni emerse dai suoi testi e i temi che non ha quasi mai
esplorato. Genera ESATTAMENTE 3 domande socratiche in italiano, rivolte a @UTENTE@
(dagli del tu), che lo costringano a pensare in proprio. Regole:
- ogni domanda nasce SOLO dal materiale fornito, niente conoscenza esterna;
- niente domande retoriche o generiche: aggancia le sue parole specifiche;
- una domanda deve toccare una lacuna (tema poco esplorato), le altre due le tensioni.
Rispondi solo col JSON."""

SCHEMA_DOMANDE = {
    'type': 'object',
    'properties': {'domande': {'type': 'array',
                               'items': {'type': 'string'},
                               'minItems': 3, 'maxItems': 3}},
    'required': ['domande'],
}


def _contraddizioni(con) -> list[dict]:
    righe = con.execute("""
        SELECT a1.text, a1.asserted_at, a2.text, a2.asserted_at, e.weight, e.note,
               t.name
        FROM edges e
        JOIN assertions a1 ON a1.id = e.src_id
        JOIN assertions a2 ON a2.id = e.dst_id
        LEFT JOIN topics t ON t.id = a1.topic_id
        WHERE e.relation = 'contradicts'
        ORDER BY e.weight DESC""").fetchall()
    return [{'a': r[0], 'data_a': r[1], 'b': r[2], 'data_b': r[3],
             'confidenza': r[4], 'nota': r[5], 'topic': r[6]} for r in righe]


def _lacune(con) -> list[tuple]:
    # La lacuna è dove manca il PENSIERO DELL'UTENTE (esplicito/dialogico),
    # non dove mancano letture: i libri non colmano un vuoto epistemico.
    return con.execute("""
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


def _numeri(con) -> dict:
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    return {
        'fonti': q('SELECT COUNT(*) FROM sources'),
        'chunk': q('SELECT COUNT(*) FROM chunks'),
        'asserzioni': q('SELECT COUNT(*) FROM assertions'),
        'entita': q('SELECT COUNT(*) FROM entities'),
        'archi': q('SELECT COUNT(*) FROM edges'),
        'contraddizioni': q("SELECT COUNT(*) FROM edges WHERE relation='contradicts'"),
    }


def _domande_socratiche(cfg: dict, contraddizioni: list[dict],
                        lacune: list[tuple]) -> list[str]:
    from zeno_engine import modelli
    materiale = 'TENSIONI EMERSE:\n'
    for c in contraddizioni[:5]:
        materiale += (f'- ({c["data_a"]}) "{c["a"]}"  VS  ({c["data_b"]}) "{c["b"]}"\n')
    materiale += '\nTEMI POCO ESPLORATI:\n'
    for nome, n in lacune:
        materiale += f'- {nome} ({n} tuoi documenti)\n'
    try:
        r = modelli.client().chat(
            model=modelli.prepara_motore(cfg, 'ask'),
            messages=[{'role': 'system', 'content': PROMPT_SOCRATICO},
                      {'role': 'user', 'content': materiale}],
            format=SCHEMA_DOMANDE,
            options={'temperature': 0.7, 'num_ctx': 4096}, keep_alive='30m')
        return json.loads(r['message']['content'])['domande'][:3]
    except Exception:
        return []


def genera(con, cfg: dict, cartella: str = os.path.join(BRAIN, 'briefing')) -> str:
    os.makedirs(cartella, exist_ok=True)
    oggi = datetime.now().strftime('%Y-%m-%d')
    numeri = _numeri(con)
    contraddizioni = _contraddizioni(con)
    lacune = _lacune(con)
    domande = _domande_socratiche(cfg, contraddizioni, lacune)
    try:
        # Cose da ricordare: temi/schede vecchi e poco esplorati (zero LLM,
        # non deve mai bloccare il resto del briefing se fallisce).
        from zeno_engine import ripasso
        proposte_ripasso = ripasso.proposte_giornaliere(con, cfg)
    except Exception:
        proposte_ripasso = []

    righe = [f'# BRIEFING ZENO — {oggi}', '',
             '> Rapporto generato dal motore epistemico. Zeno non giudica:',
             '> mostra dove il pensiero si tende, dove tace, e chiede.', '',
             '## Stato del corpus',
             f'- {numeri["fonti"]} fonti · {numeri["chunk"]} frammenti · '
             f'{numeri["asserzioni"]} asserzioni · {numeri["entita"]} entità · '
             f'{numeri["archi"]} legami nel grafo', '',
             '## Tensioni e contraddizioni rilevate', '']
    if not contraddizioni:
        righe.append('Nessuna contraddizione confermata finora.')
    for c in contraddizioni:
        righe += [f'### {c["topic"] or "Senza tema"} (confidenza {c["confidenza"]:.2f})',
                  f'- **{c["data_a"] or "?"}** — «{c["a"]}»',
                  f'- **{c["data_b"] or "?"}** — «{c["b"]}»']
        if c['nota']:
            righe.append(f'- *Dove sta la tensione*: {c["nota"]}')
        righe.append('')
    righe += ['## Lacune — temi quasi assenti dal tuo pensiero registrato', '']
    for nome, n in lacune:
        righe.append(f'- **{nome}**: {n} asserzioni')
    righe += ['', '## Cose da ricordare', '']
    if proposte_ripasso:
        for p in proposte_ripasso:
            righe.append(f'- {p}')
    else:
        righe.append('(nessuna proposta di ripasso oggi)')
    righe += ['', '## Tre domande per te', '']
    for i, d in enumerate(domande, 1):
        righe.append(f'{i}. {d}')
    if not domande:
        righe.append('(generazione domande non riuscita in questa corsa)')
    righe += ['', '---', f'*Generato da zeno_engine il {oggi}. '
              f'Fonte di verità: zeno.db.*', '']

    percorso = os.path.join(cartella, f'BRIEFING_{oggi}.md')
    with open(percorso, 'w', encoding='utf-8') as f:
        f.write('\n'.join(righe))
    return percorso


# ── Periodicità (stesso pattern di snapshot.py/mind.py/graph.py) ────────────
# `zeno sync` gira a ogni avvio dell'app: `dovuto()` decide se è ora di un
# nuovo briefing (e quindi delle proposte di ripasso, che sono "1-3 cose da
# ricordare AD OGNI AVVIO GIORNALIERO", non a ogni singolo avvio nella stessa
# giornata). Default: un briefing al giorno.
def dovuto(cfg: dict, cartella: str = STATS_DIR) -> bool:
    globale = cfg.get('_globale') or {}
    freq = globale.get('motore_epistemico', {}).get('briefing', {}).get('frequenza_giorni', 1)
    marker = os.path.join(cartella, 'briefing_last_run.txt')
    if not os.path.exists(marker):
        return True
    try:
        ultimo = datetime.fromisoformat(open(marker).read().strip())
    except Exception:
        return True
    return (datetime.now() - ultimo).days >= freq


def segna_eseguito(cartella: str = STATS_DIR):
    os.makedirs(cartella, exist_ok=True)
    with open(os.path.join(cartella, 'briefing_last_run.txt'), 'w') as f:
        f.write(datetime.now().isoformat())
