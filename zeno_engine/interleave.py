# Memory Interleave — retrieval ITERATIVO al posto del single-shot.
#
# Origine: brief dell'utente (2026-07-29) ispirato al pattern di Memory Sparse
# Attention (arXiv:2603.23516). Del paper si prende SOLO l'idea concettuale —
# espandere il recupero a più passi invece di un colpo secco — non
# l'architettura, che richiede un backbone riaddestrato e non è portabile su
# Ollama/M3.
#
# TRE SCELTE DI ADATTAMENTO, decise sui vincoli reali di questa macchina e di
# questo schema (2026-07-29):
#
# 1. NESSUN ROUTER LLM nel ciclo. Il brief proponeva un agente 3B che a ogni
#    iterazione decidesse "basta?" — ma il classificatore 3B a query-time in
#    ask.py è OPT-IN e volutamente SPENTO: su 16 GB tenere router + motore +
#    embeddings residenti manda la macchina in thrashing (1 token/s misurato).
#    Un modello in più moltiplicato per 3 iterazioni è esattamente il costo che
#    si voleva evitare. Qui la decisione è EURISTICA, su segnali già disponibili
#    a costo zero: forza del match, diversità delle fonti, presenza di pensiero
#    diretto quando la domanda è personale.
#
# 2. L'ESPANSIONE VA VIA ENTITÀ, non via archi `relates_to`. Quei 10.160 archi
#    chunk↔chunk sono `detected_by='embedding_sim'`: sono vicinanza vettoriale
#    precalcolata, cioè LO STESSO segnale della ricerca embedding. Seguirli non
#    aggiunge informazione. I 43.585 archi `mentions` (asserzione→entità) sono
#    invece un segnale simbolico che gli embedding NON danno: è il ponte che
#    collega un pensiero di marzo a uno di ottobre lessicalmente distante — la
#    motivazione stessa del brief.
#
# 3. LE ENTITÀ SI SCELGONO IN UNA BANDA, non per sola rarità. Il primo disegno
#    ordinava per IDF puro — sbagliato, e l'A/B lo ha dimostrato: l'IDF è
#    MASSIMO per gli hapax, e sul corpus l'86,9% delle entità compare in una
#    sola fonte, quindi l'espansione seguiva sistematicamente entità che non
#    collegano nulla ("Tariq RAMADAN", "voti inconsapevoli", 1 menzione in 1
#    fonte) e produceva ZERO chunk nuovi su 3 query su 4. All'estremo opposto
#    gli hub esplodono: 'Italia' 423 asserzioni, 'Russia' 295. I ponti veri
#    sono il 12,3% che tocca fra 2 e 10 FONTI DISTINTE — è quella la banda, e
#    l'IDF ordina solo al suo interno. Dopo la correzione la stessa query su
#    disinformazione segue 'Bernays' e 'Cambridge Analytica'.
#
# Granularità: il retrieval lavora su CHUNK (vec_chunks), gli archi vivono su
# ASSERZIONI ed ENTITÀ. Il ponte è chunk → asserzioni → entità → altre
# asserzioni → loro chunk. Il brief parlava genericamente di "nodi": qui il
# passaggio è esplicito.

import json
import math
import struct
from datetime import datetime

from zeno_engine import ask, indicizzatore

# ── parametri (sovrascrivibili da cfg['interleave']) ────────────────────────
DELTA_DEFAULT = 0.15          # k adattivo: tiene i candidati entro max_score - delta
# k_min è allineato al top_k storico (5) DI PROPOSITO: con k_min=3 l'A/B del
# 2026-07-29 mostrava regressioni (3 frammenti/2 fonti dove il single-shot ne
# dava 5/3). Il taglio relativo deve poter far CRESCERE il recupero quando
# l'evidenza è diffusa, non farlo scendere sotto il baseline.
K_MIN = 5
K_MAX = 8                     # tetto: il contesto ridotto è un principio di Zeno
QUOTA_HOP = 3                 # slot riservati ai chunk trovati via entità
BUDGET_CARATTERI = 12000      # ~3300 token: sta in num_ctx 4096 col resto del prompt
MAX_ITERAZIONI = 3
SCORE_DEBOLE = 0.55           # sotto questo il match migliore è fiacco → espandi
FONTI_MINIME = 3              # meno di così = risposta senza prospettiva
MAX_ENTITA_PONTE = 6          # quante entità-ponte seguire per iterazione
FONTI_PONTE_MIN = 2           # sotto = hapax, non collega nulla (86,9% delle entità)
FONTI_PONTE_MAX = 10          # sopra = hub, esplode
MAX_CHUNK_NUOVI = 20


def _par(cfg: dict) -> dict:
    c = (cfg.get('interleave') or {})
    return {
        'delta': c.get('delta', DELTA_DEFAULT),
        'k_min': c.get('k_min', max(K_MIN, cfg.get('retrieval', {}).get('top_k', 5))),
        'quota_hop': c.get('quota_hop', QUOTA_HOP),
        'k_max': c.get('k_max', K_MAX),
        'budget': c.get('budget_caratteri', BUDGET_CARATTERI),
        'max_iter': c.get('max_iterazioni', MAX_ITERAZIONI),
        'score_debole': c.get('score_debole', SCORE_DEBOLE),
        'fonti_minime': c.get('fonti_minime', FONTI_MINIME),
        'max_entita': c.get('max_entita_ponte', MAX_ENTITA_PONTE),
        'fonti_ponte_min': c.get('fonti_ponte_min', FONTI_PONTE_MIN),
        'fonti_ponte_max': c.get('fonti_ponte_max', FONTI_PONTE_MAX),
        'max_nuovi': c.get('max_chunk_nuovi', MAX_CHUNK_NUOVI),
    }


# ── k adattivo (brief §3.1) ─────────────────────────────────────────────────

def seleziona_adattivo(scelti: list[dict], p: dict, da_hop: set | None = None,
                       max_per_fonte: int = 2) -> list[dict]:
    """Taglio RELATIVO invece del top_k fisso: si tengono i frammenti entro
    `delta` dal migliore, con k tra k_min e k_max e un budget di caratteri.
    Una domanda con un match nettissimo prende pochi frammenti molto pertinenti;
    una domanda diffusa ne prende di più. Il tetto resta perché in Zeno la
    velocità viene dalla riduzione del contesto, non dal modello.

    QUOTA PER I CHUNK DA HOP (correzione dopo l'A/B del 2026-07-29). I chunk
    trovati seguendo le entità sono per definizione lessicalmente DISTANTI dalla
    domanda: il loro score embedding è più basso, quindi il taglio relativo li
    scartava tutti — la selezione annullava l'espansione, e il ciclo ripeteva
    "solo 2 fonti distinte" senza mai risolvere. Ora hanno slot riservati, esenti
    dalla soglia (ma non dal tetto k_max, dal budget e dal limite per fonte)."""
    if not scelti:
        return []
    da_hop = da_hop or set()
    soglia = scelti[0]['score'] - p['delta']
    finali, per_fonte, caratteri = [], {}, 0
    presi = set()

    def _prova(f) -> bool:
        if per_fonte.get(f['path'], 0) >= max_per_fonte:
            return False
        if len(finali) >= p['k_max']:
            return False
        if len(finali) >= p['k_min'] and caratteri + len(f['text']) > p['budget']:
            return False
        return True

    # 1) per punteggio, col taglio relativo oltre k_min
    for f in scelti:
        if f['chunk_id'] in da_hop:
            continue                        # trattati dopo, con la loro quota
        if len(finali) >= p['k_max']:
            break
        if len(finali) >= p['k_min'] and f['score'] < soglia:
            break
        if not _prova(f):
            continue
        finali.append(f)
        presi.add(f['chunk_id'])
        per_fonte[f['path']] = per_fonte.get(f['path'], 0) + 1
        caratteri += len(f['text'])

    # 2) quota riservata all'espansione via entità
    quota = p['quota_hop']
    for f in scelti:
        if quota <= 0 or len(finali) >= p['k_max']:
            break
        if f['chunk_id'] not in da_hop or f['chunk_id'] in presi:
            continue
        if not _prova(f):
            continue
        finali.append(f)
        presi.add(f['chunk_id'])
        per_fonte[f['path']] = per_fonte.get(f['path'], 0) + 1
        caratteri += len(f['text'])
        quota -= 1

    finali.sort(key=lambda x: x['score'], reverse=True)
    return finali


# ── decisione "basta?" — nessun LLM ─────────────────────────────────────────

def sufficiente(frammenti: list[dict], personale: bool, p: dict) -> tuple[bool, str]:
    """Sostituisce il Router Agent del brief con segnali che abbiamo già.
    Ritorna (basta, motivo) — il motivo finisce nel trace, che è a sua volta un
    dato interessante: come Zeno naviga la propria memoria."""
    if not frammenti:
        return False, 'nessun frammento recuperato'
    if frammenti[0]['score'] < p['score_debole']:
        return False, f"match migliore fiacco ({frammenti[0]['score']:.2f})"
    fonti = len({f['path'] for f in frammenti})
    if fonti < p['fonti_minime']:
        return False, f'solo {fonti} fonti distinte'
    if personale and not any(f['strato'] in ('esplicito', 'dialogico')
                             for f in frammenti):
        return False, 'domanda personale ma nessun frammento di pensiero diretto'
    return True, f'copertura sufficiente ({len(frammenti)} frammenti, {fonti} fonti)'


# ── IDF delle entità ────────────────────────────────────────────────────────

def idf_entita(con) -> dict[int, tuple[float, int]]:
    """Per entità CANONICA: (idf, numero di FONTI distinte che la citano).

    Le fonti distinte sono il dato decisivo, non le menzioni. Misurato sul
    corpus il 2026-07-29: l'86,9% delle entità compare in UNA SOLA fonte —
    seguirle non porta da nessuna parte, perché non collegano due documenti
    diversi. Una prima versione di questo modulo ordinava per solo IDF e quindi
    pescava sistematicamente proprio da quel gruppo (l'IDF è massimo per gli
    hapax): risultato, 3 query su 4 producevano ZERO chunk nuovi. I ponti veri
    sono il 12,3% che tocca fra 2 e 10 fonti; sopra le 10 si entra negli hub
    ('Italia' 423 asserzioni) che esplodono."""
    n_ass = con.execute('SELECT COUNT(*) FROM assertions').fetchone()[0] or 1
    fuori = {}
    for eid, n, n_fonti in con.execute("""
            SELECT COALESCE(al.canonical_id, al.id) AS c,
                   COUNT(*), COUNT(DISTINCT s.id)
            FROM edges e
            JOIN entities al ON al.id = e.dst_id
            JOIN assertions a ON a.id = e.src_id
            JOIN chunks ch ON ch.id = a.chunk_id
            JOIN sources s ON s.id = ch.source_id
            WHERE e.src_type='assertion' AND e.relation='mentions'
            GROUP BY c"""):
        fuori[eid] = (math.log(n_ass / max(n, 1)), n_fonti)
    return fuori


# ── espansione: chunk → asserzioni → entità rare → altre asserzioni → chunk ──

def _cos(a: bytes, b: bytes) -> float:
    n = min(len(a), len(b)) // 4
    va = struct.unpack(f'{n}f', a[:n * 4])
    vb = struct.unpack(f'{n}f', b[:n * 4])
    num = sum(x * y for x, y in zip(va, vb))
    na = math.sqrt(sum(x * x for x in va)) or 1.0
    nb = math.sqrt(sum(y * y for y in vb)) or 1.0
    return num / (na * nb)


def espandi_via_entita(con, chunk_ids: list[int], idf: dict[int, float],
                       esclusi: set[int], p: dict) -> tuple[dict[int, float], list[str]]:
    """Dai chunk correnti trova le entità RARE che citano, poi gli altri chunk
    che le citano. Ritorna ({chunk_id: bonus}, nomi delle entità seguite)."""
    if not chunk_ids:
        return {}, []
    ph = ','.join('?' * len(chunk_ids))
    # entità menzionate dalle asserzioni dei chunk correnti, risolte al canonico
    menzionate = {}
    for eid, nome in con.execute(f"""
            SELECT DISTINCT COALESCE(al.canonical_id, al.id),
                   (SELECT name FROM entities WHERE id=COALESCE(al.canonical_id, al.id))
            FROM assertions a
            JOIN edges e ON e.src_id = a.id AND e.src_type='assertion'
                        AND e.relation='mentions'
            JOIN entities al ON al.id = e.dst_id
            WHERE a.chunk_id IN ({ph})""", chunk_ids):
        menzionate[eid] = nome
    if not menzionate:
        return {}, []
    # SOLO le entità nella banda-ponte (fra `fonti_min` e `fonti_max` fonti
    # distinte): sotto sono hapax che non collegano nulla, sopra sono hub che
    # riportano centinaia di risultati. Dentro la banda si ordina per IDF.
    ponti = [e for e in menzionate
             if p['fonti_ponte_min'] <= idf.get(e, (0.0, 0))[1] <= p['fonti_ponte_max']]
    ordinate = sorted(ponti, key=lambda e: -idf.get(e, (0.0, 0))[0])[:p['max_entita']]
    if not ordinate:
        return {}, []
    seguite = [menzionate[e] for e in ordinate]

    ph2 = ','.join('?' * len(ordinate))
    bonus: dict[int, float] = {}
    for cid, eid in con.execute(f"""
            SELECT DISTINCT a.chunk_id, COALESCE(al.canonical_id, al.id)
            FROM edges e
            JOIN entities al ON al.id = e.dst_id
            JOIN assertions a ON a.id = e.src_id
            WHERE e.src_type='assertion' AND e.relation='mentions'
              AND COALESCE(al.canonical_id, al.id) IN ({ph2})""", ordinate):
        if cid in esclusi:
            continue
        bonus[cid] = bonus.get(cid, 0.0) + idf.get(eid, (0.0, 0))[0]
    # i più legati per rarità condivisa
    migliori = dict(sorted(bonus.items(), key=lambda kv: -kv[1])[:p['max_nuovi']])
    return migliori, seguite


# ── ciclo ───────────────────────────────────────────────────────────────────

def recupera_interleave(con, domanda: str, cfg: dict, personale: bool = False,
                        traccia_su_db: bool = True) -> tuple[list[dict], list[dict]]:
    """Retrieval iterativo. Ritorna (frammenti, traccia)."""
    p = _par(cfg)
    qv = indicizzatore.embed_query(domanda, cfg)
    query_id = datetime.now().strftime('%Y%m%d%H%M%S%f')

    # iterazione 0: come il single-shot (vettoriale largo + bonus lessicale)
    candidati: dict[int, float] = {}
    for cid, dist in con.execute(
            'SELECT chunk_id, distance FROM vec_chunks WHERE embedding MATCH ? AND k = 20',
            (qv,)):
        candidati[cid] = 1.0 - dist
    fts = ask._fts_query(domanda)
    if fts:
        try:
            # ORDER BY rank e base per posizione per i soli-lessicali: stessa
            # nota di ask.recupera().
            for i, (cid,) in enumerate(con.execute(
                    'SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? '
                    'ORDER BY rank LIMIT 10', (fts,))):
                candidati[cid] = (candidati[cid] + 0.05 if cid in candidati
                                 else 0.34 - i * 0.015)
        except Exception:
            pass

    idf = idf_entita(con)
    traccia: list[dict] = []
    frammenti: list[dict] = []
    metodo = 'initial'
    da_hop: set[int] = set()
    motivo_precedente = None

    for it in range(p['max_iter']):
        scelti = ask.arricchisci(con, candidati, personale, domanda)
        frammenti = seleziona_adattivo(scelti, p, da_hop=da_hop)
        basta, motivo = sufficiente(frammenti, personale, p)
        passo = {'iterazione': it, 'metodo': metodo, 'motivo': motivo,
                 'frammenti': len(frammenti),
                 'fonti': len({f['path'] for f in frammenti}),
                 'chunk_ids': [f['chunk_id'] for f in frammenti],
                 'entita_seguite': []}
        # Stop se il motivo di insufficienza si RIPETE: l'espansione precedente
        # non ha spostato nulla, insistere brucia iterazioni per niente (visto
        # nell'A/B del 2026-07-29: 3 giri a ripetere "solo 2 fonti distinte").
        ripetuto = (not basta) and motivo == motivo_precedente
        if basta or ripetuto or it == p['max_iter'] - 1:
            passo['stop'] = True
            if ripetuto:
                passo['motivo'] += ' — invariato dopo espansione, mi fermo'
            traccia.append(passo)
            break
        motivo_precedente = motivo
        nuovi, seguite = espandi_via_entita(
            con, [f['chunk_id'] for f in frammenti], idf, set(candidati), p)
        passo['entita_seguite'] = seguite
        if not nuovi:
            passo['stop'] = True
            passo['motivo'] += ' — espansione senza nuovi chunk'
            traccia.append(passo)
            break
        da_hop.update(nuovi)
        # i nuovi chunk entrano con la LORO similarità reale alla domanda (così
        # restano sulla stessa scala degli altri) più un bonus per il legame
        for cid, b in nuovi.items():
            riga = con.execute('SELECT embedding FROM vec_chunks WHERE chunk_id = ?',
                               (cid,)).fetchone()
            sim = _cos(qv, riga[0]) if riga else 0.0
            candidati[cid] = sim + min(b / 40.0, 0.08)
        traccia.append(passo)
        metodo = 'entita'

    if traccia_su_db:
        try:
            for passo in traccia:
                con.execute("""INSERT INTO retrieval_trace
                    (query_id, iterazione, domanda, metodo, chunk_ids, decisione, creato_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (query_id, passo['iterazione'], domanda, passo['metodo'],
                     json.dumps(passo['chunk_ids']),
                     json.dumps({k: passo[k] for k in
                                 ('motivo', 'fonti', 'frammenti', 'entita_seguite')
                                 if k in passo}, ensure_ascii=False),
                     datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
            con.commit()
        except Exception:
            pass                     # il trace è osservabilità, non deve mai rompere una query
    return frammenti, traccia
