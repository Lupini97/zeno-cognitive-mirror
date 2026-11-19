# Revisione dei temi: proposte di temi madre nuovi, figli, unioni, ritiri e
# spostamenti, a partire dal CONTENUTO delle asserzioni (embedding bge-m3).
#
# Perché non basta temi.proponi_temi_nuovi(): quella raggruppa le ENTITÀ
# (co-menzione) e propone solo figli. Il 25/09/2026 ha prodotto "Valutazione
# Vini" costruito su Libia, Gheddafi e Italo Calvino, "Geopolitica
# Internazionale" (2.075 asserzioni) doppione del padre, figli di figli. E il
# limbo — il segnale più forte che manca un tema madre — non veniva mai usato
# per scoprire temi, solo per riclassificare.
#
# Solo LETTURA sul DB. Scrive stats/revisione_temi/proposte.json e
# rapporto.md; nulla si applica senza la conferma dell'utente.

import json
import os
from datetime import datetime

import numpy as np

from zeno_engine import config, modelli

CARTELLA = os.path.join(config.BRAIN, 'stats', 'revisione_temi')

# PESO DEL DOCUMENTO (utente, 28/09/2026): dentro un gruppo (un tema, un
# gruppo candidato) ogni documento vale 1. Se ci entra con k asserzioni,
# ciascuna pesa 1/k; se con una sola, quella pesa quanto un documento intero.
# Il divisore è quante asserzioni di quel documento stanno NEL gruppo, non il
# totale del documento: un'asserzione può stare in più temi. Senza, un
# documento lungo (il manoscritto: 1.551 asserzioni) decideva da solo centri,
# unioni e temi nuovi. Tutte le misure qui sotto — centri, separabilità, quota
# in comune, dimensioni — sono in documenti.

# Soglie iniziali, da tarare sulle prime revisioni dell'utente.
VICINI = 10                 # grafo k-NN per i gruppi
SIM_MIN_ARCO = 0.55         # coseno minimo perché due asserzioni siano "vicine"
GRUPPO_MIN = 15             # asserzioni minime perché Louvain dia un gruppo
FIGLIO_MIN = 30
FONTI_MIN = 10              # documenti distinti perché un gruppo sia un tema
FIGLI_PER_MADRE = 3
# Louvain taglia una stessa zona in gruppi quasi uguali: il 28/09/2026 tre
# figli proposti per il vino, tre per la geopolitica, con centri a 0,86-0,95.
# Dentro una madre si tiene il più ampio e si scartano i gemelli.
SIM_GEMELLI = 0.85
# Unioni: NON la similarità dei centroidi — con bge-m3 i centroidi di temi in
# italiano stanno tutti fra 0,63 e 0,99 (mediana 0,87, misurato il 27/09/2026)
# e i doppioni veri non si distinguono dai vicini. Si usa la SEPARABILITÀ:
# quante asserzioni dei due temi tornano al proprio assegnandole al centroide
# più vicino (media bilanciata; 0,5 = indistinguibili). Mediana misurata 0,88.
SEP_UNIONE = 0.73
QUOTA_COMUNE = 0.5          # o: metà del tema più piccolo sta anche nell'altro
TEMA_MIN = 3                # documenti: sotto, un sotto-tema è candidato al ritiro
MARGINE_SPOSTA = 0.03       # un figlio più vicino a un'altra madre di così
# Un gruppo da pochi documenti non è un tema: è quel libro. Il 27/09/2026 otto
# dei dodici "temi madre" del limbo erano scene del manoscritto dell'utente
# ("Percezione di Alessandra", "Eredità di Pino"). Da qui FONTI_MIN.


# ── embedding (cache incrementale) ──────────────────────────────────────────

def embedding(con, cfg: dict, verboso: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """(ids, matrice normalizzata) di TUTTE le asserzioni in `assertions`.
    Ricalcola solo quelle nuove; le scartate/cancellate escono da sole."""
    os.makedirs(CARTELLA, exist_ok=True)
    f_ids, f_emb = (os.path.join(CARTELLA, n) for n in ('ids.npy', 'embedding.npy'))
    cache = {}
    if os.path.exists(f_ids) and os.path.exists(f_emb):
        cache = dict(zip(np.load(f_ids).tolist(), np.load(f_emb)))
    righe = con.execute('SELECT id, text FROM assertions ORDER BY id').fetchall()
    mancanti = [(i, t) for i, t in righe if i not in cache]
    if mancanti:
        if not modelli.assicura_demone():
            raise RuntimeError('demone MLX non raggiungibile: servono gli embedding')
        nome = cfg['modelli']['embeddings']
        modelli.prepara_solo(cfg, nome)
        for k in range(0, len(mancanti), 128):
            blocco = mancanti[k:k + 128]
            vett = modelli.client().embed(model=nome, input=[t for _, t in blocco])['embeddings']
            for (i, _), v in zip(blocco, vett):
                cache[i] = np.asarray(v, dtype=np.float16)
            if verboso and (k // 128) % 20 == 0:
                print(f'  embedding {k + len(blocco)}/{len(mancanti)}')
    ids = np.array([i for i, _ in righe])
    mat = np.stack([cache[i] for i in ids]).astype(np.float32)
    mat /= np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9
    np.save(f_ids, ids)
    np.save(f_emb, mat.astype(np.float16))
    return ids, mat


# ── gruppi: grafo k-NN + Louvain (networkx, già usato da Zeno) ──────────────

def gruppi(mat: np.ndarray, minimo: int, seme: int = 42) -> list[np.ndarray]:
    """Indici (in `mat`) dei gruppi di almeno `minimo` elementi, dal più grande."""
    import networkx as nx
    n = len(mat)
    if n < minimo:
        return []
    g = nx.Graph()
    g.add_nodes_from(range(n))
    for a in range(0, n, 1024):
        sim = mat[a:a + 1024] @ mat.T
        for r, riga in enumerate(sim):
            i = a + r
            riga[i] = -1
            top = np.argpartition(-riga, VICINI)[:VICINI]
            for j in top:
                if riga[j] >= SIM_MIN_ARCO:
                    g.add_edge(i, int(j), weight=float(riga[j]))
    comunita = nx.community.louvain_communities(g, weight='weight', seed=seme)
    fuori = [np.array(sorted(c)) for c in comunita if len(c) >= minimo]
    return sorted(fuori, key=len, reverse=True)


def _pesi(src: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Peso di ogni asserzione del gruppo `idx`: 1/k, con k le asserzioni del
    suo documento dentro il gruppo (vedi PESO DEL DOCUMENTO)."""
    _, inv, conta = np.unique(src[idx], return_inverse=True, return_counts=True)
    return 1.0 / conta[inv]


def _centro(mat: np.ndarray, src: np.ndarray, idx: np.ndarray) -> np.ndarray:
    c = _pesi(src, idx) @ mat[idx]
    return c / (np.linalg.norm(c) + 1e-9)


def _rappresentative(mat: np.ndarray, src: np.ndarray, idx: np.ndarray, n: int = 6) -> list[int]:
    """Le più vicine al centro, una per documento: gli esempi mostrano la
    varietà del tema, non il suo documento più lungo."""
    ordine = np.argsort(-(mat[idx] @ _centro(mat, src, idx)))
    scelte, visti = [], set()
    for k in ordine:
        if src[idx[k]] not in visti:
            visti.add(src[idx[k]])
            scelte.append(int(idx[k]))
            if len(scelte) == n:
                break
    return scelte


def _descrivi(con, ids_gruppo: list[int]) -> dict:
    """Composizione di un gruppo: strati, tipi di fonte, temi attuali, entità."""
    q = ','.join('?' * len(ids_gruppo))
    strati = dict(con.execute(
        f'SELECT s.strato, COUNT(*) FROM assertions a JOIN chunks c ON c.id=a.chunk_id '
        f'JOIN sources s ON s.id=c.source_id WHERE a.id IN ({q}) GROUP BY 1', ids_gruppo))
    tipi = dict(con.execute(
        f'SELECT s.type, COUNT(*) FROM assertions a JOIN chunks c ON c.id=a.chunk_id '
        f'JOIN sources s ON s.id=c.source_id WHERE a.id IN ({q}) GROUP BY 1 ORDER BY 2 DESC', ids_gruppo))
    temi = dict(con.execute(
        f'SELECT COALESCE(t.name, "(limbo)"), COUNT(*) FROM assertions a '
        f'LEFT JOIN topics t ON t.id=a.topic_id WHERE a.id IN ({q}) GROUP BY 1 ORDER BY 2 DESC', ids_gruppo))
    entita = [n for (n,) in con.execute(
        f"SELECT e.name FROM edges d JOIN entities e ON e.id=d.dst_id "
        f"WHERE d.src_type='assertion' AND d.dst_type='entity' AND d.src_id IN ({q}) "
        f"GROUP BY e.id ORDER BY COUNT(*) DESC LIMIT 8", ids_gruppo)]
    fonti = con.execute(
        f'SELECT s.title, COUNT(*) FROM assertions a JOIN chunks c ON c.id=a.chunk_id '
        f'JOIN sources s ON s.id=c.source_id WHERE a.id IN ({q}) GROUP BY s.id ORDER BY 2 DESC',
        ids_gruppo).fetchall()
    return {'strati': strati, 'tipi_fonte': tipi, 'temi_attuali': temi, 'entita': entita,
            'n_fonti': len(fonti), 'fonte_principale': fonti[0][0] if fonti else None,
            'quota_fonte_principale': round(fonti[0][1] / len(ids_gruppo), 2) if fonti else 0}


PROMPT_NOME = """Sei il catalogatore dell'archivio cognitivo di @UTENTE@.
Ti mostro un gruppo di asserzioni simili fra loro. Proponi il nome del TEMA che
le accomuna: 2-5 parole in italiano, specifico (non "Varie", non "Cultura",
non "Società"), più una descrizione di una riga che dica cosa ci sta dentro e
cosa no. Se il gruppo è solo un insieme di dati sparsi senza un tema, rispondi
con nome "NESSUN TEMA". Rispondi solo col JSON."""
SCHEMA_NOME = {'type': 'object',
               'properties': {'nome': {'type': 'string'}, 'descrizione': {'type': 'string'}},
               'required': ['nome', 'descrizione']}


def nomina(cfg: dict, testi: list[str], contesto: str = '') -> dict:
    modello = cfg['modelli']['router']
    corpo = (contesto + '\n' if contesto else '') + '\n'.join(f'- {t}' for t in testi)
    r = modelli.client().chat(
        model=modello,
        messages=[{'role': 'system', 'content': PROMPT_NOME},
                  {'role': 'user', 'content': corpo}],
        format=SCHEMA_NOME, options={'temperature': 0.0, 'num_ctx': 4096, 'num_predict': 400})
    try:
        return json.loads(r['message']['content'])
    except ValueError:
        return {'nome': '?', 'descrizione': r['message']['content'][:200]}


# ── analisi ─────────────────────────────────────────────────────────────────

def analizza(con, cfg: dict, verboso: bool = True) -> dict:
    ids, mat = embedding(con, cfg, verboso)
    pos = {int(i): k for k, i in enumerate(ids)}
    testo = dict(con.execute('SELECT id, text FROM assertions'))
    fonte_di = dict(con.execute('SELECT a.id, c.source_id FROM assertions a '
                                'JOIN chunks c ON c.id = a.chunk_id'))
    src = np.array([fonte_di[int(i)] for i in ids])

    def n_doc(idx) -> int:
        return len(set(src[np.asarray(sorted(idx), dtype=int)].tolist()))

    temi = {tid: (nome, parent) for tid, nome, parent in
            con.execute('SELECT id, name, parent_id FROM topics')}
    membri: dict[int, list[int]] = {}
    for aid, tid in con.execute('SELECT assertion_id, topic_id FROM assertion_topics'):
        if aid in pos:
            membri.setdefault(tid, []).append(pos[aid])
    centro = {tid: _centro(mat, src, np.array(m)) for tid, m in membri.items() if m}
    madri = [t for t, (_, p) in temi.items() if p is None]

    def proposta(idx: np.ndarray, tipo: str, extra: dict) -> dict:
        aids = [int(ids[k]) for k in idx]
        rapp = [testo[int(ids[k])] for k in _rappresentative(mat, src, idx)]
        d = _descrivi(con, aids)
        c = _centro(mat, src, idx)
        vicino = max(madri, key=lambda t: float(centro[t] @ c)) if madri else None
        return {'tipo': tipo, 'n': len(aids), 'esempi': rapp, **d,
                'madre_piu_vicina': temi[vicino][0] if vicino else None,
                'assertion_ids': aids, **extra}

    out = {'generato': datetime.now().isoformat(timespec='seconds'),
           'asserzioni': len(ids), 'madri_nuove': [], 'figli_nuovi': [],
           'unioni': [], 'ritiri': [], 'spostamenti': []}

    # 1. limbo → temi madre mancanti
    limbo = np.array([pos[a] for (a,) in con.execute(
        "SELECT id FROM assertions WHERE topic_id IS NULL "
        "AND COALESCE(ruolo, '') <> 'trama'") if a in pos], dtype=int)
    if verboso:
        print(f'limbo: {len(limbo)} asserzioni')
    out['fonte_unica'] = []
    for g in gruppi(mat[limbo], GRUPPO_MIN):
        p = proposta(limbo[g], 'madre_nuova', {})
        out['fonte_unica' if p['n_fonti'] < FONTI_MIN else 'madri_nuove'].append(p)
    out['madri_nuove'].sort(key=lambda p: -p['n_fonti'])

    # 2. dentro ogni madre, ciò che nessun figlio raccoglie → figli nuovi
    figli_di: dict[int, list[int]] = {}
    for t, (_, p) in temi.items():
        if p is not None:
            figli_di.setdefault(p, []).append(t)
    for m in madri:
        coperti = {k for f in figli_di.get(m, []) for k in membri.get(f, [])}
        liberi = np.array(sorted(set(membri.get(m, [])) - coperti), dtype=int)
        if verboso:
            print(f'{temi[m][0]}: {len(liberi)} senza figlio')
        candidati = []
        for g in gruppi(mat[liberi], FIGLIO_MIN):
            p = proposta(liberi[g], 'figlio_nuovo', {'madre': temi[m][0]})
            (out['fonte_unica'] if p['n_fonti'] < FONTI_MIN else candidati).append(p)
        candidati.sort(key=lambda p: -p['n_fonti'])
        tenuti, centri_tenuti = [], []
        for p in candidati:
            c = _centro(mat, src, np.array([pos[a] for a in p['assertion_ids']]))
            if all(float(c @ ct) < SIM_GEMELLI for ct in centri_tenuti):
                tenuti.append(p)
                centri_tenuti.append(c)
        out['figli_nuovi'] += tenuti[:FIGLI_PER_MADRE]

    # 3. temi esistenti: unioni, ritiri, spostamenti
    insiemi = {t: set(m) for t, m in membri.items()}

    def separabilita(a: int, b: int) -> float | None:
        sa, sb = np.array(sorted(insiemi[a] - insiemi[b])), np.array(sorted(insiemi[b] - insiemi[a]))
        if len(sa) < 3 or len(sb) < 3:
            return None
        ca, cb = _centro(mat, src, sa), _centro(mat, src, sb)
        wa, wb = _pesi(src, sa), _pesi(src, sb)
        giusti_a = float(wa @ (mat[sa] @ ca > mat[sa] @ cb)) / wa.sum()
        giusti_b = float(wb @ (mat[sb] @ cb > mat[sb] @ ca)) / wb.sum()
        return 0.5 * (giusti_a + giusti_b)

    def in_comune(a: int, b: int) -> float:
        """Quota (in documenti) del tema più piccolo che sta anche nell'altro."""
        piccolo, altro = (a, b) if n_doc(insiemi[a]) <= n_doc(insiemi[b]) else (b, a)
        idx = np.array(sorted(insiemi[piccolo]))
        w = _pesi(src, idx)
        dentro = np.array([k in insiemi[altro] for k in idx])
        return float(w @ dentro) / w.sum()

    figli = [t for t, (_, p) in temi.items() if p is not None and t in centro]
    for i, a in enumerate(figli):
        pa = temi[a][1]
        coppie = [(pa, 'doppione del padre')] if pa in centro else []
        coppie += [(b, 'doppione di un fratello') for b in figli[i + 1:]]
        for b, perche in coppie:
            # i figli stanno anche nel padre per costruzione
            comune = 0.0 if perche == 'doppione del padre' else in_comune(a, b)
            sep = separabilita(a, b)
            if (sep is not None and sep <= SEP_UNIONE) or comune >= QUOTA_COMUNE:
                grande, piccolo = (a, b) if n_doc(insiemi[a]) >= n_doc(insiemi[b]) else (b, a)
                if perche == 'doppione del padre':
                    grande, piccolo = b, a
                out['unioni'].append({'tema': temi[piccolo][0], 'dentro': temi[grande][0],
                                      'separabilita': round(sep, 2) if sep is not None else None,
                                      'in_comune': round(comune, 2), 'n': len(insiemi[piccolo]),
                                      'n_fonti': n_doc(insiemi[piccolo]), 'perche': perche})
        if n_doc(insiemi[a]) < TEMA_MIN:
            out['ritiri'].append({'tema': temi[a][0], 'n': len(insiemi[a]),
                                  'n_fonti': n_doc(insiemi[a])})
        if temi[pa][1] is not None:
            out['spostamenti'].append({'tema': temi[a][0], 'da': temi[pa][0],
                                       'a': temi[temi[pa][1]][0],
                                       'perche': 'figlio di un figlio: massimo due livelli'})
        elif pa in centro:
            migliore = max(madri, key=lambda m: float(centro[a] @ centro[m]) if m in centro else -1)
            guadagno = float(centro[a] @ centro[migliore]) - float(centro[a] @ centro[pa])
            if migliore != pa and guadagno >= MARGINE_SPOSTA:
                out['spostamenti'].append({'tema': temi[a][0], 'da': temi[pa][0],
                                           'a': temi[migliore][0], 'margine': round(guadagno, 3),
                                           'perche': 'più vicino a un\'altra madre'})
    out['unioni'].sort(key=lambda u: (u['separabilita'] is None, u['separabilita'] or 0))
    return out


def nomina_proposte(out: dict, cfg: dict, verboso: bool = True) -> None:
    modelli.prepara_solo(cfg, cfg['modelli']['router'])
    for chiave in ('madri_nuove', 'figli_nuovi'):
        for p in out[chiave]:
            ctx = f"(sotto il tema madre «{p['madre']}»)" if p.get('madre') else ''
            p.update({f'proposta_{k}': v for k, v in nomina(cfg, p['esempi'], ctx).items()})
            if verboso:
                print(f"  {chiave}: {p['proposta_nome']} ({p['n']})")


def scrivi(out: dict) -> str:
    os.makedirs(CARTELLA, exist_ok=True)
    with open(os.path.join(CARTELLA, 'proposte.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    r = [f"# Revisione dei temi — {out['generato'][:10]}", '',
         f"{out['asserzioni']} asserzioni. Solo proposte: nulla è stato applicato.", '']

    def blocco(titolo, lista, madre=False):
        r.extend([f'## {titolo} ({len(lista)})', ''])
        for k, p in enumerate(lista, 1):
            r.append(f"### {k}. {p.get('proposta_nome', '?')} — {p['n_fonti']} documenti, {p['n']} asserzioni"
                     + (f" (sotto «{p['madre']}»)" if madre else ''))
            r.append(f"*{p.get('proposta_descrizione', '')}*")
            r.append(f"Strati: {p['strati']} · fonti: {p['tipi_fonte']}")
            r.append(f"Temi attuali: {p['temi_attuali']} · madre più vicina: {p['madre_piu_vicina']}")
            r.append(f"Entità: {', '.join(p['entita'])}")
            r.append(f"{p['n_fonti']} fonti; la principale ({p['fonte_principale']}) "
                     f"pesa il {p['quota_fonte_principale']:.0%}")
            r.extend(f'- {e}' for e in p['esempi'])
            r.append('')

    blocco('Temi madre mancanti (dal limbo)', out['madri_nuove'])
    blocco('Figli nuovi', out['figli_nuovi'], madre=True)
    r.extend([f"## Gruppi scartati perché da meno di {FONTI_MIN} documenti ({len(out['fonte_unica'])})", ''])
    r.extend(f"- {p['n']} asserzioni, {p['quota_fonte_principale']:.0%} da «{p['fonte_principale']}»"
             f" (limbo)" if p['tipo'] == 'madre_nuova' else
             f"- {p['n']} asserzioni, {p['quota_fonte_principale']:.0%} da «{p['fonte_principale']}»"
             f" (in «{p.get('madre')}»)" for p in out['fonte_unica'])
    r.append('')
    for titolo, chiave in (('Unioni', 'unioni'), ('Ritiri', 'ritiri'), ('Spostamenti', 'spostamenti')):
        r.extend([f'## {titolo} ({len(out[chiave])})', ''])
        r.extend(f'- {json.dumps(p, ensure_ascii=False)}' for p in out[chiave])
        r.append('')
    r.extend([f"## Temi da seminare ({len(out.get('semine', []))})", ''])
    for v in out.get('semine', []):
        r.append(f"- {v['tema']}: " + '; '.join(f"{d['titolo']} ({len(d['assertion_ids'])})"
                                                for d in v['documenti']))
    r.append('')
    percorso = os.path.join(CARTELLA, 'rapporto.md')
    with open(percorso, 'w', encoding='utf-8') as f:
        f.write('\n'.join(r) + '\n')
    return percorso


# ── proposte per l'utente (dashboard) ────────────────────────────────────────
# Dal 28/09/2026 sono le sole proposte di temi nuovi: sostituiscono quelle a
# cluster di entità di temi.py, giudicate inutili dall'utente. Si propongono
# solo temi madre (dal limbo) e figli; unioni, ritiri e spostamenti restano
# nel rapporto come suggerimenti da leggere. Nulla nasce senza il suo sì.

FILE_RIFIUTATE = os.path.join(CARTELLA, 'rifiutate.json')
SOVRAPPOSIZIONE_RIFIUTO = 0.5   # un gruppo che ripete per metà uno rifiutato non si ripropone


def _chiave(p: dict) -> str:
    return f"{p['tipo']}-{min(p['assertion_ids'])}"


def _rifiutate() -> list[set]:
    if not os.path.exists(FILE_RIFIUTATE):
        return []
    with open(FILE_RIFIUTATE, encoding='utf-8') as f:
        return [set(r['assertion_ids']) for r in json.load(f)]


def _gia_rifiutata(p: dict, rifiutate: list[set]) -> bool:
    ids = set(p['assertion_ids'])
    return any(len(ids & r) / len(ids | r) >= SOVRAPPOSIZIONE_RIFIUTO for r in rifiutate)


# SEMINA. Un tema creato dall'utente parte vuoto, e il classificatore impara
# solo dagli esempi. La descrizione da sola non basta per riempirlo: sui temi
# esistenti, le asserzioni più simili alla descrizione sono del tema solo una
# volta su due (misurato il 28/09/2026). Basta però per scegliere i CANDIDATI:
# si propongono i documenti più vicini alla descrizione e l'utente spunta quelli
# giusti. Dal giro dopo il classificatore conosce il tema.
SEMINA_DOCUMENTI = 6
SEMINA_SIM = 0.40           # coseno asserzione-descrizione per entrare col documento


def semine(con, cfg: dict) -> list[dict]:
    """Per i temi descritti ma con meno di TEMA_MIN documenti: i documenti
    più vicini alla descrizione (dentro la madre, se è un figlio)."""
    temi = con.execute(
        "SELECT t.id, t.name, t.parent_id, t.descrizione FROM topics t "
        "LEFT JOIN assertion_topics at ON at.topic_id = t.id "
        "LEFT JOIN assertions a ON a.id = at.assertion_id "
        "LEFT JOIN chunks c ON c.id = a.chunk_id "
        "WHERE COALESCE(t.descrizione, '') <> '' GROUP BY t.id "
        "HAVING COUNT(DISTINCT c.source_id) < ?", (TEMA_MIN,)).fetchall()
    if not temi:
        return []
    ids, mat = embedding(con, cfg, verboso=False)
    pos = {int(i): k for k, i in enumerate(ids)}
    nome_emb = cfg['modelli']['embeddings']
    modelli.prepara_solo(cfg, nome_emb)
    vett = np.asarray(modelli.client().embed(
        model=nome_emb, input=[f'{n}: {d}' for _, n, _, d in temi])['embeddings'], np.float32)
    vett /= np.linalg.norm(vett, axis=1, keepdims=True) + 1e-9
    titolo = dict(con.execute('SELECT id, title FROM sources'))
    testo = dict(con.execute('SELECT id, text FROM assertions'))
    out = []
    for (tid, nome, madre, _), v in zip(temi, vett):
        dentro = {a for (a,) in con.execute(
            'SELECT assertion_id FROM assertion_topics WHERE topic_id = ?', (tid,))}
        universo = (con.execute('SELECT at.assertion_id, c.source_id FROM assertion_topics at '
                                'JOIN assertions a ON a.id = at.assertion_id '
                                'JOIN chunks c ON c.id = a.chunk_id WHERE at.topic_id = ? '
                                "AND COALESCE(a.ruolo, '') <> 'trama'", (madre,)) if madre else
                    con.execute("SELECT a.id, c.source_id FROM assertions a JOIN chunks c "
                                "ON c.id = a.chunk_id WHERE COALESCE(a.ruolo, '') <> 'trama'")).fetchall()
        per_doc: dict[int, list[int]] = {}
        for aid, sid in universo:
            if aid in pos and aid not in dentro:
                per_doc.setdefault(sid, []).append(aid)
        punti = []
        for sid, aids in per_doc.items():
            sim = mat[[pos[a] for a in aids]] @ v
            c = mat[[pos[a] for a in aids]].mean(0)
            punti.append((float(c @ v / (np.linalg.norm(c) + 1e-9)), sid, aids, sim))
        punti.sort(key=lambda x: -x[0])
        documenti = []
        for vic, sid, aids, sim in punti[:SEMINA_DOCUMENTI]:
            ordine = np.argsort(-sim)
            scelte = [aids[k] for k in ordine if sim[k] >= SEMINA_SIM] or [aids[ordine[0]]]
            documenti.append({'source_id': int(sid), 'titolo': titolo.get(sid), 'vicinanza': round(vic, 2),
                              'assertion_ids': scelte, 'esempi': [testo[a] for a in scelte[:2]]})
        if documenti:
            out.append({'chiave': f'semina-{tid}', 'tema': nome, 'tid': tid, 'documenti': documenti})
    return out


def semina(con, chiave: str, source_ids: list[int]) -> int:
    """Lega al tema le asserzioni proposte dei documenti scelti dall'utente.
    Ritorna quante. Non fa commit."""
    out = leggi_proposte()
    voce = next((v for v in out.get('semine', []) if v['chiave'] == chiave), None)
    if voce is None:
        raise ValueError('Proposta non più presente: ricarica la pagina')
    riga = con.execute('SELECT COALESCE(parent_id, id) FROM topics WHERE id = ?', (voce['tid'],)).fetchone()
    if riga is None:
        raise ValueError(f"Il tema «{voce['tema']}» non esiste più")
    n = 0
    for d in voce['documenti']:
        if d['source_id'] not in source_ids:
            continue
        for aid in d['assertion_ids']:
            if not con.execute('SELECT 1 FROM assertions WHERE id = ?', (aid,)).fetchone():
                continue
            for t in dict.fromkeys((voce['tid'], riga[0])):
                con.execute("INSERT OR IGNORE INTO assertion_topics(assertion_id, topic_id, fonte) "
                            "VALUES (?, ?, 'semina')", (aid, t))
            con.execute('UPDATE assertions SET topic_id = ? WHERE id = ? AND topic_id IS NULL',
                        (riga[0], aid))
            n += 1
    out['semine'] = [v for v in out['semine'] if v['chiave'] != chiave]
    with open(os.path.join(CARTELLA, 'proposte.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return n


def genera(con, cfg: dict, verboso: bool = False) -> int:
    """Analisi completa + nomi: rigenera proposte.json e rapporto.md.
    Ritorna quante proposte di temi nuovi aspettano l'utente."""
    out = analizza(con, cfg, verboso)
    out['semine'] = semine(con, cfg)
    rif = _rifiutate()
    for k in ('madri_nuove', 'figli_nuovi'):
        out[k] = [p for p in out[k] if not _gia_rifiutata(p, rif)]
    nomina_proposte(out, cfg, verboso)
    # Via i nomi vuoti e quelli che ripetono un tema esistente ("Vini e
    # sensorialità" dentro "Vino e sensorialità": resto non smistato, non un tema).
    from difflib import SequenceMatcher
    esistenti = [n.lower() for (n,) in con.execute('SELECT name FROM topics')]
    for k in ('madri_nuove', 'figli_nuovi'):
        out[k] = [p for p in out[k]
                  if (p.get('proposta_nome') or '').strip().upper() not in ('', '?', 'NESSUN TEMA')
                  and all(SequenceMatcher(None, p['proposta_nome'].strip().lower(), e).ratio() < 0.8
                          for e in esistenti)]
        for p in out[k]:
            p['chiave'] = _chiave(p)
    scrivi(out)
    return len(out['madri_nuove']) + len(out['figli_nuovi']) + len(out['semine'])


def leggi_proposte() -> dict:
    percorso = os.path.join(CARTELLA, 'proposte.json')
    if not os.path.exists(percorso):
        return {}
    with open(percorso, encoding='utf-8') as f:
        return json.load(f)


def _togli(chiave: str) -> dict | None:
    out = leggi_proposte()
    trovata = None
    for k in ('madri_nuove', 'figli_nuovi'):
        for p in out.get(k, []):
            if p.get('chiave') == chiave:
                trovata = p
        out[k] = [p for p in out.get(k, []) if p.get('chiave') != chiave]
    if trovata:
        with open(os.path.join(CARTELLA, 'proposte.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    return trovata


def accetta(con, chiave: str, nome: str, descrizione: str, madre: str | None) -> int:
    """Crea il tema proposto (nome e descrizione come li ha corretti l'utente)
    e ci lega le asserzioni del gruppo. Ritorna quante. Non fa commit."""
    from zeno_engine import tassonomia
    p = next((p for k in ('madri_nuove', 'figli_nuovi')
              for p in leggi_proposte().get(k, []) if p.get('chiave') == chiave), None)
    if p is None:
        raise ValueError('Proposta non più presente: ricarica la pagina')
    tid = tassonomia.crea(con, nome, descrizione, madre)
    mid = con.execute('SELECT COALESCE(parent_id, id) FROM topics WHERE id = ?',
                      (tid,)).fetchone()[0]
    n = 0
    for aid in p['assertion_ids']:
        if not con.execute('SELECT 1 FROM assertions WHERE id = ?', (aid,)).fetchone():
            continue            # scartata o cancellata dopo l'analisi
        for t in dict.fromkeys((tid, mid)):
            con.execute("INSERT OR IGNORE INTO assertion_topics(assertion_id, topic_id, fonte) "
                        "VALUES (?, ?, 'proposta')", (aid, t))
        con.execute('UPDATE assertions SET topic_id = ? WHERE id = ? AND topic_id IS NULL',
                    (mid, aid))
        n += 1
    _togli(chiave)
    return n


def rifiuta(chiave: str) -> None:
    """Toglie la proposta e se la ricorda: un gruppo che ne ripete per metà
    le asserzioni non verrà riproposto."""
    p = _togli(chiave)
    if p is None:
        return
    voci = []
    if os.path.exists(FILE_RIFIUTATE):
        with open(FILE_RIFIUTATE, encoding='utf-8') as f:
            voci = json.load(f)
    voci.append({'nome': p.get('proposta_nome'), 'data': datetime.now().isoformat(timespec='seconds'),
                 'assertion_ids': p['assertion_ids']})
    with open(FILE_RIFIUTATE, 'w', encoding='utf-8') as f:
        json.dump(voci, f, ensure_ascii=False)
