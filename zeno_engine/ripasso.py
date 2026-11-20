# Ripasso — le "cose da ricordare" del briefing giornaliero.
#
# Non tutto l'archivio pesa uguale. Temi di pensiero e libri hanno priorità
# alta; il contenuto "da archivio" (vino, manga/fumetti, letteratura per
# ragazzi...) va pesato basso o escluso — stessa nozione già decisa da
# L'utente in V5 per il cross-linking (zeno_config.yaml: cross_linker.
# aree_escluse / aree_corpus_escluse). La riusiamo qui invece di duplicarla:
# è la stessa idea di "contenuto di consultazione, non di pensiero".
#
# Una scheda è candidata al ripasso se: (a) l'archivio ne sa poco (poche o
# zero asserzioni — non è mai stata metabolizzata dall'Archivista), (b) non
# è già stata proposta di recente (cooldown, stats/ripasso_proposte.json) e
# (c) non è stata citata di recente in chat (stats/sessione_temi_attivi.json,
# scritto da chat_ui.py). Solo SQL + euristica su file: zero LLM, costo
# trascurabile — chiamabile a ogni avvio senza pensarci.

import json
import os
import re
from datetime import datetime, timedelta
from zeno_engine.config import BRAIN

STATS_DIR = os.path.join(BRAIN, 'stats')
PROPOSTE_FILE = 'ripasso_proposte.json'

# Tipi che contano come "temi di pensiero e libri" nel senso della richiesta
# dell'utente: una scheda_libro, un articolo studiato, un materiale di
# formazione. Dialoghi/maieutiche/riflessioni sono già il pensiero ESPLICITO
# stesso (non c'è nulla da "ricordare": sono la fonte), quindi restano fuori.
TIPI_RIPASSO = ('scheda_libro', 'articolo', 'formazione')

# Peso per area — usato SOLO se motore_epistemico.ripasso.pesi_area non
# definisce un override in zeno_config.yaml. 'pubblicazioni' è pesata bassa
# di default perché in questo corpus è quasi tutta Vivino (vedi anche
# l'esclusione per parola chiave qui sotto, che la intercetta comunque per
# path); un domani con pubblicazioni non enologiche l'override in config
# permette di correggere senza toccare il codice.
PESI_AREA_DEFAULT = {
    'libri': 1.0,
    'articoli_saggistica': 1.0,
    'formazione': 0.9,
    'riflessioni': 0.8,
    'psicologia_cognitiva': 0.8,
    'pubblicazioni': 0.4,
}

# Fallback se zeno_config.yaml non ha proprio la sezione cross_linker (non
# dovrebbe succedere: è nel template V5), o se manca solo aree_temi extra.
KEYWORD_ARCHIVIO_FALLBACK = ('manga', 'fumetti', 'vivino',
                             'letteratura-per-ragazzi', 'narrativa-interattiva')


def _norm(t: str) -> str:
    return re.sub(r'\s+', ' ', (t or '').lower()).strip()


def _sezione_ripasso(cfg: dict) -> dict:
    globale = cfg.get('_globale') or {}
    return (globale.get('motore_epistemico') or {}).get('ripasso') or {}


def _keyword_archivio(cfg: dict) -> list[str]:
    """Riusa cross_linker.aree_escluse + aree_corpus_escluse (zeno_config.yaml):
    è la lista con cui l'utente ha già classificato 'vino' e 'manga' come
    contenuto di consultazione in V5, non da riscoprire in un ripasso."""
    globale = cfg.get('_globale') or {}
    cl = globale.get('cross_linker', {}) or {}
    parole = list(cl.get('aree_escluse') or []) + list(cl.get('aree_corpus_escluse') or [])
    parole += list(_sezione_ripasso(cfg).get('keyword_archivio_extra') or [])
    return [p.lower() for p in parole] or list(KEYWORD_ARCHIVIO_FALLBACK)


def _pesi_area(cfg: dict) -> dict:
    override = _sezione_ripasso(cfg).get('pesi_area')
    return {**PESI_AREA_DEFAULT, **override} if override else dict(PESI_AREA_DEFAULT)


def _pesi_temi(cfg: dict) -> dict:
    # Le recensioni importate da Vivino finiscono nel tema 'Vino e
    # sensorialità' (indicizzatore.OVERRIDE_TOPIC_PATH), se esiste: una scheda
    # di quel tema pesa poco nel ripasso, coerente con l'esclusione per path.
    default = {'Vino e sensorialità': 0.1}
    override = _sezione_ripasso(cfg).get('pesi_temi')
    return {**default, **override} if override else default


def _file_recenti() -> set:
    """Titoli/nomi file citati nelle sessioni chat recenti (finestra mobile
    già gestita da chat_ui.py in sessione_temi_attivi.json): non riproporre
    ciò che l'utente ha appena discusso."""
    path = os.path.join(STATS_DIR, 'sessione_temi_attivi.json')
    if not os.path.exists(path):
        return set()
    try:
        dati = json.load(open(path, encoding='utf-8'))
    except Exception:
        return set()
    return set(dati.get('file_citati') or [])


def _proposte_recenti(cartella: str, cooldown_giorni: int) -> set:
    """source_id già proposti negli ultimi `cooldown_giorni`: senza questo,
    la stessa scheda povera verrebbe riproposta ogni singolo giorno finché
    L'utente non la affronta o l'Archivista non la arricchisce."""
    path = os.path.join(cartella, PROPOSTE_FILE)
    if not os.path.exists(path):
        return set()
    try:
        proposte = json.load(open(path, encoding='utf-8'))
    except Exception:
        return set()
    soglia = datetime.now() - timedelta(days=cooldown_giorni)
    esclusi = set()
    for p in proposte:
        try:
            data = datetime.fromisoformat(p.get('data', ''))
        except Exception:
            continue
        if data >= soglia:
            esclusi.add(p.get('source_id'))
    return esclusi


def _registra_proposte(source_ids: list, cartella: str = STATS_DIR):
    if not source_ids:
        return
    os.makedirs(cartella, exist_ok=True)
    path = os.path.join(cartella, PROPOSTE_FILE)
    try:
        proposte = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
    except Exception:
        proposte = []
    adesso = datetime.now().isoformat()
    proposte += [{'source_id': sid, 'data': adesso} for sid in source_ids]
    # Le ultime 200 bastano abbondantemente a coprire il cooldown: il file
    # non deve crescere all'infinito.
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(proposte[-200:], f, ensure_ascii=False, indent=2)


def candidati(con, cfg: dict, cartella: str = STATS_DIR) -> list[dict]:
    """
    Schede vecchie e poco esplorate, pesate per area/tema e filtrate dal
    cluster 'archivio' (vino, manga...) e dal cooldown delle proposte già
    fatte. Ordina per peso decrescente, poi per quante asserzioni ha
    (meno = più urgente), poi per anzianità (più vecchia = prima).
    """
    conf = _sezione_ripasso(cfg)
    soglia_asserzioni = conf.get('soglia_asserzioni_povere', 2)
    cooldown = conf.get('cooldown_giorni', 14)
    max_proposte = conf.get('max_proposte', 3)

    keyword_archivio = _keyword_archivio(cfg)
    pesi_area = _pesi_area(cfg)
    pesi_temi = _pesi_temi(cfg)
    recenti = _file_recenti()
    esclusi_cooldown = _proposte_recenti(cartella, cooldown)

    placeholders = ','.join('?' * len(TIPI_RIPASSO))
    righe = con.execute(f"""
        SELECT s.id, s.path, s.title, s.area, s.type, s.created_at,
               COUNT(a.id) AS n_asserzioni,
               GROUP_CONCAT(DISTINCT t.name) AS temi
        FROM sources s
        JOIN chunks c ON c.source_id = s.id
        LEFT JOIN assertions a ON a.chunk_id = c.id
        LEFT JOIN topics t ON t.id = a.topic_id
        WHERE s.type IN ({placeholders})
        GROUP BY s.id
        HAVING n_asserzioni <= ?
        ORDER BY n_asserzioni ASC, s.created_at ASC
    """, (*TIPI_RIPASSO, soglia_asserzioni)).fetchall()

    scelti = []
    for sid, path, title, area, tipo, created, n_ass, temi in righe:
        if sid in esclusi_cooldown:
            continue
        basename = os.path.basename(path or '')
        if basename in recenti or (title or '') in recenti:
            continue
        path_l = (path or '').lower()
        if any(kw in path_l for kw in keyword_archivio):
            continue                                    # cluster "archivio": escluso
        peso = pesi_area.get(area, 0.5)
        for tema in (temi or '').split(','):
            tema = tema.strip()
            if tema:
                peso *= pesi_temi.get(tema, 1.0)
        if peso <= 0:
            continue
        scelti.append({'source_id': sid, 'title': title, 'area': area, 'type': tipo,
                       'created_at': created, 'n_asserzioni': n_ass, 'peso': round(peso, 3)})

    scelti.sort(key=lambda x: (-x['peso'], x['n_asserzioni'], x['created_at'] or ''))
    return scelti[:max_proposte]


def _formatta_proposta(c: dict) -> str:
    eta = f' (nel tuo archivio dal {c["created_at"]})' if c['created_at'] else ''
    if c['n_asserzioni'] == 0:
        stato = 'non ne hai ancora nessuna traccia diretta'
    else:
        stato = f'ne hai solo {c["n_asserzioni"]} asserzioni registrate'
    return f'Ti ricordi **{c["title"]}**{eta}? {stato}: facciamo una disamina?'


def proposte_giornaliere(con, cfg: dict, cartella: str = STATS_DIR) -> list[str]:
    """Le 1-3 proposte di ripasso pronte per il briefing, in italiano.
    Registra ciò che propone (cooldown) per non ripetersi nei prossimi giorni."""
    scelti = candidati(con, cfg, cartella)
    if scelti:
        _registra_proposte([c['source_id'] for c in scelti], cartella)
    return [_formatta_proposta(c) for c in scelti]
