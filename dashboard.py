# scripts/dashboard.py
import streamlit as st
import json, os, sys, re, sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import subprocess, sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict
_zeno_s = __import__('os').path.dirname(__import__('os').path.abspath(__file__))
if _zeno_s not in __import__('sys').path: __import__('sys').path.insert(0, _zeno_s)
from zeno_engine.config import carica_config
from zeno_engine.config import BRAIN

# Protetto: come pagina di zeno_app.py il page_config lo fa l'entry point;
# da sola (streamlit run dashboard.py, porta 8502) resta valido.
try:
    st.set_page_config(page_title='Zeno Dashboard', layout='wide', page_icon='Z')
except Exception:
    pass
# Colori e caratteri da tema_zeno.py (anche .lacuna-dash): i colori fissi di
# prima erano pensati per il fondo scuro e nel tema chiaro le metriche
# sparivano, testo quasi bianco su bianco.
st.markdown('''<style>
h3 { font-size: 15px !important; }
[data-testid="stDataFrame"] { font-size: 12px; }
</style>''', unsafe_allow_html=True)

@st.cache_resource
def _carica_logo():
    """Legge il logo una volta sola per processo."""
    p = Path(__file__).resolve().parent / 'Logo_Zeno.png'
    if p.exists():
        return p.read_bytes()
    return None

@st.cache_resource
def _carica_cfg():
    """Carica zeno_config.yaml una volta sola per processo."""
    return carica_config()

@st.cache_data(ttl=120)
def _conta_sessioni(qa_dir: str) -> int:
    """Conta i file .json in query_archiviate, aggiorna ogni 2 minuti."""
    if not os.path.exists(qa_dir):
        return 0
    return len([f for f in os.listdir(qa_dir)
                if f.endswith('.json') and not f.startswith('.')])

# ── Header con logo ────────────────────────────────────────────────────────
_logo_bytes = _carica_logo()
_col_logo, _col_titolo = st.columns([1, 8])
with _col_logo:
    if _logo_bytes:
        st.image(_logo_bytes, width=72)
cfg = _carica_cfg()
from interfaccia import tema_zeno
tema_zeno.applica(cfg)
with _col_titolo:
    st.markdown(tema_zeno.testata('gemello statistico'), unsafe_allow_html=True)

# ── Banner calibrazione ────────────────────────────────────────────────────
_stato_cal = cfg.get('profilo_cognitivo', {}).get('stato_calibrazione', 'in_calibrazione')
_mbti_presente = os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'strumenti', 'mbti.py'))
if _stato_cal == 'in_calibrazione' and _mbti_presente:
    _soglia_gg   = cfg.get('mbti', {}).get('soglia_giorni_calibrazione', 30)
    _soglia_sess = cfg.get('mbti', {}).get('soglia_sessioni_calibrazione', 15)
    _qa_dir      = os.path.join(cfg['percorsi']['memoria'], 'query_archiviate')
    _n_sess      = _conta_sessioni(_qa_dir)
    st.info(
        f'🔄 **Profilo cognitivo in calibrazione.** '
        f'Il tipo psicologico verrà confermato automaticamente dopo {_soglia_gg} giorni '
        f'di utilizzo e {_soglia_sess} sessioni archiviate '
        f'(attuali: {_n_sess}/{_soglia_sess}). '
        f'Il tab **Profilo cognitivo** mostra i valori correnti — '
        f'usa il pulsante "Calibra ora" per aggiornare manualmente.'
    )

# Intercetta segnale di chiusura inviato da chat_ui.py via iframe
_qp = st.query_params
if _qp.get('zeno_stop') == '1':
    import streamlit.components.v1 as _comp
    st.markdown('<style>body{background:#0E1117;}</style>', unsafe_allow_html=True)
    _comp.html("""
        <style>
            body { margin:0; background:#0E1117; display:flex;
                   align-items:center; justify-content:center;
                   height:100vh; font-family:Arial,sans-serif; }
            .msg { color:#D6E4F0; text-align:center; }
            .msg h2 { font-size:24px; margin-bottom:8px; }
            .msg p  { font-size:14px; color:#A8C8E8; }
        </style>
        <div class="msg">
            <h2>Z E N O — chiuso</h2>
            <p>Dashboard arrestata.</p>
        </div>
        <script>setTimeout(function(){ window.close(); }, 1200);</script>
    """, height=300)
    st.stop()

stats_dir = cfg['percorsi']['stats']

# ── Web search per lacune (grafo) ─────────────────────────────────────────────
def cerca_sul_web(query, max_risultati=3):
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            risultati = list(ddgs.text(query, max_results=max_risultati))
        if not risultati:
            return [], 'Nessun risultato trovato.'
        return [{'titolo': r.get('title', ''), 'url': r.get('href', ''),
                 'estratto': r.get('body', '')[:600]} for r in risultati], None
    except Exception as e:
        return [], f'Errore ricerca web: {e}'


def importa_da_url_dash(url):
    """Scarica e filtra il testo di una pagina web."""
    try:
        import requests
        from bs4 import BeautifulSoup
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; Zeno/4.0)'}
        resp = requests.get(url, headers=headers, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer',
                         'aside', 'form', 'iframe', 'noscript']):
            tag.decompose()
        titolo = soup.title.string.strip() if soup.title else url
        contenitore = soup.find('article') or soup.find('main') or soup.body
        if not contenitore:
            return None, 'Impossibile estrarre contenuto dalla pagina.'
        paragrafi = [p.get_text(' ', strip=True)
                     for p in contenitore.find_all(['p', 'h1', 'h2', 'h3', 'li'])
                     if len(p.get_text(strip=True)) > 40]
        if not paragrafi:
            return None, 'Nessun testo significativo trovato.'
        return titolo, '\n\n'.join(paragrafi[:60])
    except Exception as e:
        return None, f'Errore importazione: {e}'


def _leggi_contesto_scheda(tema_fonte, cfg):
    """
    Legge il testo della scheda tema che ha generato la lacuna.
    Restituisce (sezione_lacune, estratto_scheda) — entrambi possono essere None.
    """
    if not tema_fonte:
        return None, None
    def _slug(t):
        t = re.sub(r'[^\w\s-]', '', t.lower())
        return re.sub(r'[\s]+', '-', t.strip())[:60]
    temi_dir = os.path.join(cfg['percorsi']['memoria'], 'profilo', 'temi_v6')
    fp = os.path.join(temi_dir, f'tema_{_slug(tema_fonte)}.md')
    if not os.path.exists(fp):
        return None, None
    try:
        testo = open(fp, encoding='utf-8').read()
        m = re.search(
            r'(?:##?\s*|\d+\.\s*)LACUNE?[^\n]*\n(.*?)(?=\n##|\n\d+\.|\Z)',
            testo, re.DOTALL | re.IGNORECASE
        )
        sezione_lacune = m.group(1).strip() if m else None
        corpo = testo
        if testo.startswith('---'):
            fine_fm = testo.find('---', 3)
            if fine_fm > 0:
                corpo = testo[fine_fm + 3:].strip()
        return sezione_lacune, corpo
    except Exception:
        return None, None


def _segna_da_dialogo(cfg, lac_id, testo, tema_fonte):
    """Aggiunge la lacuna a lacune_da_dialogo.json. Idempotente."""
    path = os.path.join(cfg['percorsi']['stats'], 'lacune_da_dialogo.json')
    try:
        lista = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
    except Exception:
        lista = []
    if any(e.get('id') == lac_id for e in lista):
        return
    lista.append({
        'id':           lac_id,
        'testo':        testo,
        'tema_fonte':   tema_fonte,
        'data_aggiunta': datetime.now().isoformat(),
        'affrontata':   False,
    })
    with open(path, 'w', encoding='utf-8') as _fw:
        json.dump(lista, _fw, ensure_ascii=False, indent=2)


def _carica_ids_da_dialogo(cfg):
    """Restituisce il set di lac_id già segnati come 'da risolvere via dialogo'."""
    path = os.path.join(cfg['percorsi']['stats'], 'lacune_da_dialogo.json')
    try:
        lista = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
        return {e['id'] for e in lista if not e.get('affrontata')}
    except Exception:
        return set()


# ── Cache loader ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def carica_snaps():
    snaps = []
    for f in sorted(os.listdir(stats_dir)):
        if f.startswith('snapshot_') and f.endswith('.json'):
            try: snaps.append(json.load(open(os.path.join(stats_dir, f))))
            except: pass
    return snaps

@st.cache_data(ttl=300)
def carica_history_grafo():
    path = os.path.join(stats_dir, 'grafo_history.json')
    if not os.path.exists(path): return []
    try: return json.load(open(path))
    except: return []

# ── Grafo 3D (zeno.db) — fonte unica anche per le sezioni statistiche ──────
# Sostituisce carica_grafo()/grafo_connessioni.json (V5/V6 transitorio,
# eliminato il 2026-07-14): un solo grafo (il 3D), stessi dati per tutta
# la dashboard, cosi i numeri restano coerenti fra tab.
DB_V6 = os.path.join(BRAIN, 'zeno.db')

@st.cache_data(ttl=120)
def carica_dati_grafo_zeno(giorni_nuovi: int = 7):
    """Nodi/archi del grafo 3D letti da zeno.db (grafo3d.dati_grafo)."""
    if not os.path.exists(DB_V6):
        return {'nodes': [], 'links': []}
    try:
        from interfaccia import grafo3d
        return grafo3d.dati_grafo(DB_V6, giorni_nuovi)
    except Exception:
        return {'nodes': [], 'links': []}

@st.cache_data(ttl=120)
def carica_nomi_temi_zeno():
    """Nomi dei temi di primo livello, direttamente da zeno.db (topics)."""
    if not os.path.exists(DB_V6):
        return []
    try:
        con = sqlite3.connect(f'file:{DB_V6}?mode=ro', uri=True)
        nomi = [r[0] for r in con.execute(
            "SELECT name FROM topics WHERE parent_id IS NULL ORDER BY name")]
        con.close()
        return nomi
    except Exception:
        return []

@st.cache_data(ttl=60)
def carica_contraddizioni_zeno():
    """Contraddizioni rilevate dal Critico (edges.relation='contradicts')
    non ancora valutate — sostituisce gli archi 'dialogico' letti in
    precedenza da grafo_connessioni.json."""
    if not os.path.exists(DB_V6):
        return []
    try:
        con = sqlite3.connect(f'file:{DB_V6}?mode=ro', uri=True)
        righe = con.execute("""
            SELECT e.id, a1.id, a1.text, a2.id, a2.text, e.weight, e.note
            FROM edges e
            JOIN assertions a1 ON a1.id = e.src_id
            JOIN assertions a2 ON a2.id = e.dst_id
            WHERE e.relation = 'contradicts'
            ORDER BY e.weight DESC LIMIT 20""").fetchall()
        con.close()
        return [{'edge_id': r[0], 'a1_id': r[1], 'a1_text': r[2],
                 'a2_id': r[3], 'a2_text': r[4], 'peso': r[5] or 0.0,
                 'nota': r[6] or ''} for r in righe]
    except Exception:
        return []

@st.cache_data(ttl=30)
def carica_proposte_alias():
    """Proposte di alias entità (stats/entita_alias_proposte.json), generate
    da `zeno alias`. Non ambigue prima, per numero di menzioni decrescente."""
    path = os.path.join(stats_dir, 'entita_alias_proposte.json')
    if not os.path.exists(path):
        return []
    try:
        props = json.load(open(path, encoding='utf-8'))
    except Exception:
        return []
    props.sort(key=lambda p: (p.get('ambiguo', False), -p.get('menzioni_canonico', 0)))
    return props

def _conferma_alias(alias_id, canonico_id):
    """Scrive entities.canonical_id (reversibile, non distrugge archi)."""
    from zeno_engine import indicizzatore
    con = sqlite3.connect(DB_V6)          # RW: applica_alias fa solo UPDATE+commit
    try:
        indicizzatore.applica_alias(con, alias_id, canonico_id, cartella=stats_dir)
    finally:
        con.close()

def _rifiuta_alias(alias_id, canonico_id):
    from zeno_engine import indicizzatore
    con = sqlite3.connect(DB_V6)
    try:
        indicizzatore.rifiuta_alias(con, alias_id, canonico_id, cartella=stats_dir)
    finally:
        con.close()

@st.cache_data(ttl=30)
def carica_temi_proposti():
    """Proposte di temi nuovi di revisione_temi (madri dal limbo, figli)."""
    from zeno_engine import revisione_temi
    try:
        return revisione_temi.leggi_proposte()
    except Exception:
        return {}

@st.cache_data(ttl=30)
def carica_prioritari_proposti():
    path = os.path.join(stats_dir, 'prioritari_proposti.json')
    try:
        return json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {}
    except Exception:
        return {}

def _decidi_proposta(chiave, nome=None, descrizione=None, madre=None):
    """Accetta (con nome) o rifiuta (senza) una proposta. Ritorna (errore, n)."""
    from zeno_engine import db as _zdb, revisione_temi
    if nome is None:
        revisione_temi.rifiuta(chiave)
        carica_temi_proposti.clear()
        return None, 0
    con = _zdb.connect(DB_V6)
    try:
        n = revisione_temi.accetta(con, chiave, nome, descrizione or '', madre)
        con.commit()
        return None, n
    except ValueError as e:
        con.rollback()
        return str(e), 0
    finally:
        con.close()
        carica_temi_proposti.clear()
        carica_albero_temi.clear()
        carica_nomi_temi_zeno.clear()

def _decidi_semina(chiave, source_ids):
    """Lega al tema nuovo le asserzioni dei documenti scelti. Ritorna (errore, n)."""
    from zeno_engine import db as _zdb, revisione_temi
    con = _zdb.connect(DB_V6)
    try:
        n = revisione_temi.semina(con, chiave, source_ids)
        con.commit()
        return None, n
    except ValueError as e:
        con.rollback()
        return str(e), 0
    finally:
        con.close()
        carica_temi_proposti.clear()
        carica_albero_temi.clear()

@st.cache_data(ttl=30)
def carica_albero_temi():
    """[(id, nome, parent_id, descrizione, n_documenti, n_asserzioni)] da zeno.db."""
    if not os.path.exists(DB_V6):
        return []
    try:
        con = sqlite3.connect(f'file:{DB_V6}?mode=ro', uri=True)
        righe = con.execute("""
            SELECT t.id, t.name, t.parent_id, COALESCE(t.descrizione, ''),
                   COUNT(DISTINCT c.source_id), COUNT(DISTINCT at.assertion_id)
            FROM topics t
            LEFT JOIN assertion_topics at ON at.topic_id = t.id
            LEFT JOIN assertions a ON a.id = at.assertion_id
            LEFT JOIN chunks c ON c.id = a.chunk_id
            GROUP BY t.id ORDER BY t.name""").fetchall()
        con.close()
        return righe
    except Exception:
        return []

def _modifica_temi(azione, *args, **kw):
    """Crea / modifica / ritira un tema (zeno_engine.tassonomia). Ritorna l'errore o None."""
    from zeno_engine import db as _zdb, tassonomia
    con = _zdb.connect(DB_V6)
    try:
        getattr(tassonomia, azione)(con, *args, **kw)
        con.commit()
        return None
    except ValueError as e:
        con.rollback()
        return str(e)
    finally:
        con.close()
        carica_albero_temi.clear()
        carica_nomi_temi_zeno.clear()

@st.cache_data(ttl=120)
def carica_punteggi_mbti():
    """Legge profilo_cognitivo.punteggi da zeno_config.yaml tramite la config già caricata."""
    return cfg.get('profilo_cognitivo', {}).get('punteggi', {})

@st.cache_data(ttl=120)
def carica_storia_mbti_dash():
    """
    Parser di stats/mbti.log — restituisce lista di {timestamp, variazioni}.
    Usa il parser già definito in profile.py quando disponibile,
    con fallback inline per robustezza.
    """
    try:
        from zeno_engine.profilo import carica_storia_mbti  # path già in sys.path (riga 10)
        return carica_storia_mbti()
    except Exception:
        pass
    log_path = os.path.join(stats_dir, 'mbti.log')
    if not os.path.exists(log_path):
        return []
    storia = []
    pat_ts  = re.compile(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')
    pat_var = re.compile(r'(\w+):([\d.]+)→([\d.]+)\(([+-][\d.]+)\)')
    for riga in open(log_path, encoding='utf-8'):
        riga = riga.strip()
        if not riga: continue
        m_ts = pat_ts.match(riga)
        if not m_ts: continue
        variazioni = {m.group(1): {'da': float(m.group(2)), 'a': float(m.group(3)),
                                   'delta': float(m.group(4))}
                      for m in pat_var.finditer(riga)}
        storia.append({'timestamp': m_ts.group(1), 'variazioni': variazioni})
    return storia

@st.cache_data(ttl=60)
def carica_lacune_aperte():
    """
    Legge stats/lacune_aperte.json e calcola giorni_aperti al volo.
    giorni_aperti NON è un campo del JSON: viene calcolato qui.
    Restituisce la lista ordinata per anzianità decrescente.
    """
    path = os.path.join(stats_dir, 'lacune_aperte.json')
    if not os.path.exists(path):
        return []
    try:
        lacune = json.load(open(path, encoding='utf-8'))
        for l in lacune:
            try:
                l['giorni_aperti'] = (
                    datetime.now() -
                    datetime.fromisoformat(l['data_prima_rilevazione'])
                ).days
            except Exception:
                l['giorni_aperti'] = 0
        return sorted(lacune, key=lambda x: -x.get('giorni_aperti', 0))
    except Exception:
        return []


# ── Helper: conta file ────────────────────────────────────────────────────────

def conta_file_dir(directory, ext_filter=None):
    if not os.path.exists(directory): return 0
    # esclude i file AppleDouble ._* che ExFAT genera per ogni file
    files = [f for f in os.listdir(directory) if not f.startswith('.')]
    if ext_filter:
        files = [f for f in files if any(f.endswith(e) for e in ext_filter)]
    return len(files)


# ── Helper: gestione temi ─────────────────────────────────────────────────────

def leggi_frontmatter_dash(filepath):
    try:
        content = open(filepath, encoding='utf-8').read()
        if not content.startswith('---'): return {}
        fine = content.find('---', 3)
        if fine < 0: return {}
        campi = {}
        for riga in content[3:fine].splitlines():
            if ':' in riga:
                k, _, v = riga.partition(':')
                campi[k.strip()] = v.strip().strip('"\'')
        return campi
    except: return {}

def _slug_tema(label):
    import re as _re
    t = _re.sub(r'[^\w\s-]', '', label.lower())
    return _re.sub(r'[\s]+', '-', t.strip())[:60]

def _aggiorna_prioritari(cfg, label, add):
    """Aggiunge o rimuove label da temi.prioritari in zeno_config.yaml."""
    import yaml
    config_path = str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'zeno_config.yaml')
    try:
        data = yaml.safe_load(open(config_path, encoding='utf-8').read())
        if 'temi' not in data:
            data['temi'] = {}
        prio = data['temi'].get('prioritari', [])
        prio_lower = [p.lower() for p in prio]
        if add and label.lower() not in prio_lower:
            prio.append(label)
        elif not add:
            prio = [p for p in prio if p.lower() != label.lower()]
        data['temi']['prioritari'] = prio
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False,
                      sort_keys=False)
    except Exception as e:
        st.error(f'Errore aggiornamento config: {e}')

def _rinomina_tema(t, nuovo_nome, cfg):
    import re as _re
    stats_d    = cfg['percorsi']['stats']
    temi_dir   = os.path.join(cfg['percorsi']['memoria'], 'profilo', 'temi')
    nuovo_slug = _slug_tema(nuovo_nome)
    nuovo_fp   = os.path.join(temi_dir, f'tema_{nuovo_slug}.md')
    try:
        content = open(t['filepath'], encoding='utf-8').read()
        content = _re.sub(r'^tema:.*$',     f'tema: "{nuovo_nome}"',    content, flags=_re.MULTILINE)
        content = _re.sub(r'^# Tema:.*$',   f'# Tema: {nuovo_nome}',   content, flags=_re.MULTILINE)
        os.rename(t['filepath'], nuovo_fp)
        open(nuovo_fp, 'w', encoding='utf-8').write(content)
    except Exception as e:
        st.error(f'Errore rinomina file: {e}'); return
    # Il grafo 3D legge zeno.db in diretta a ogni apertura del tab: non c'è
    # più un grafo_connessioni.json da tenere allineato manualmente qui.
    _aggiorna_prioritari(cfg, t['label'], False)
    if t['prioritario']:
        _aggiorna_prioritari(cfg, nuovo_nome, True)
    st.success(f'Rinominato: "{t["label"]}" → "{nuovo_nome}"')

def _elimina_tema(t, cfg):
    stats_d = cfg['percorsi']['stats']
    try:
        os.remove(t['filepath'])
    except Exception as e:
        st.error(f'Errore eliminazione: {e}'); return
    _aggiorna_prioritari(cfg, t['label'], False)
    log_path = os.path.join(stats_d, 'temi_operazioni.log')
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(f'{datetime.now().isoformat()} | ELIMINA | {t["label"]} | {t["filepath"]}\n')
    st.success(f'"{t["label"]}" eliminato')


# ── Caricamento dati ──────────────────────────────────────────────────────────

snaps   = carica_snaps()
history = carica_history_grafo()

# V6 (2026-07-14): l'assenza di snapshot NON deve più bloccare l'intera
# dashboard (bug del giorno 0: MBTI/grafo 3D/lacune erano irraggiungibili
# solo perché mancava una statistica). `zeno sync` genera uno snapshot
# automaticamente quando serve (snapshot.frequenza_giorni); qui usiamo un
# dict vuoto come fallback innocuo — ogni tab gestisce già i campi mancanti.
last = snaps[-1] if snaps else {}
if not snaps:
    st.caption('📊 Nessuno snapshot ancora generato in questa sessione V6 — '
              'la tab Statistiche sarà vuota finché non ne arriva uno '
              '(automatico al prossimo avvio di Zeno, o `zeno_engine snapshot`).')

# La scheda Set di prova è stata tolta il 2026-11-19 (il set resta in
# stats/valutazione/ e si usa da `zeno valuta`). Letture PRIME compare solo se
# c'è il modulo: la versione pubblica non lo include.
_prime_presente = os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              'interfaccia', 'prime_ui.py'))
_nomi_schede = ['Statistiche', 'Grafo 3D', 'Journal', 'Evoluzione', 'Gestione temi',
                'Lacune aperte', 'Entità', 'Profilo cognitivo']
_schede = st.tabs(_nomi_schede + (['Letture PRIME'] if _prime_presente else []))
(tab_stat, tab_grafo3d, tab_journal, tab_storia, tab_temi, tab_lacune, tab_entita,
 tab_profilo) = _schede[:8]
tab_prime = _schede[8] if _prime_presente else None

# ═══════════════════════════════════════════════════════════════════════
# TAB: GRAFO 3D — vista UNICA del grafo (V6, legge zeno.db in diretta).
# Sostituisce sia il vecchio "Grafo 3D" sia il tab 2D "Grafo cognitivo"
# (rimosso il 2026-07-14: due grafi con dati non allineati confondevano
# più che aiutare). Nodi = temi e documenti/.md dell'archivio, mai
# asserzioni grezze; legenda e pannello dettagli sono nel canvas stesso.
# ═══════════════════════════════════════════════════════════════════════
with tab_grafo3d:
    if not os.path.exists(DB_V6):
        st.info('zeno.db non ancora creato: lancia `zeno ingest`.')
    else:
        try:
            from interfaccia import grafo3d as _g3d
            _giorni_nuovi = st.slider(
                '✨ Evidenzia le novità: nodi entrati nell\'archivio negli ultimi N giorni',
                min_value=1, max_value=30, value=7, key='g3d_giorni_nuovi',
                help='Sposta la barra per scegliere quanto "recente" conta come nuovo. '
                     'I nodi entrati entro questa finestra di giorni appaiono più '
                     'chiari, più grandi e con ✨, così vedi a colpo d\'occhio cosa '
                     'hai aggiunto di recente. (Data usata: quando il documento è '
                     'stato ingerito; temi ed entità ereditano la data della prima fonte.)')
            _html = _g3d.html_grafo(DB_V6, giorni_nuovi=_giorni_nuovi)
            if _html is None:
                st.warning('Libreria 3d-force-graph non trovata in scripts/assets/.')
            else:
                st.components.v1.html(_html, height=660, scrolling=False)
                # riepilogo testuale delle community (stesse del toggle nel grafo)
                try:
                    _comm = _g3d.dati_grafo(DB_V6, giorni_nuovi=_giorni_nuovi).get('communities', [])
                    if _comm:
                        with st.expander(f'🧩 {len(_comm)} community emerse (cluster trasversali ai temi)'):
                            st.caption('Gruppi di entità che compaiono insieme nelle tue '
                                       'asserzioni — scoperti da Louvain, non dai 10 temi fissi. '
                                       'Nel grafo: pulsante "Colora per: Community".')
                            for _c in _comm:
                                st.markdown(f"- **{_c['label']}** "
                                            f"<span style='color:#888;font-size:11px'>"
                                            f"· {_c['nodi']} entità</span>",
                                            unsafe_allow_html=True)
                except Exception:
                    pass
        except Exception as _e:
            st.error(f'Grafo 3D non disponibile: {_e}')

    # ── Contraddizioni da confermare (Critico → edges.contradicts) ─────
    st.divider()
    st.subheader('Contraddizioni da confermare')
    st.caption('Tensioni rilevate dal Critico fra due asserzioni del tuo archivio. '
               'Le tue decisioni restano salvate in `stats/archi_confermati.json` '
               'e `archi_rifiutati.json`.')
    _contraddizioni = carica_contraddizioni_zeno()
    if not _contraddizioni:
        st.success('Nessuna contraddizione rilevata dal Critico, o tutte già valutate.')
    else:
        _conf_path = os.path.join(stats_dir, 'archi_confermati.json')
        _rif_path  = os.path.join(stats_dir, 'archi_rifiutati.json')
        _confermati = json.load(open(_conf_path)) if os.path.exists(_conf_path) else []
        _rifiutati  = json.load(open(_rif_path))  if os.path.exists(_rif_path)  else []
        _conf_keys = {tuple(sorted([str(a.get('source')), str(a.get('target'))])) for a in _confermati}
        _rif_keys  = {tuple(sorted([str(a.get('source')), str(a.get('target'))])) for a in _rifiutati}
        _mostrate = 0
        for c in _contraddizioni:
            key = tuple(sorted([f"a{c['a1_id']}", f"a{c['a2_id']}"]))
            if key in _conf_keys or key in _rif_keys:
                continue
            _mostrate += 1
            with st.container():
                gc1, gc2, gc3 = st.columns([3, 1, 1])
                with gc1:
                    st.write(f"**«{c['a1_text'][:150]}»**  \n↔ **«{c['a2_text'][:150]}»**  \n"
                             f"_{c['nota']}_ (confidenza: {c['peso']:.2f})")
                with gc2:
                    if st.button('Confermo', key=f"g3d_conf_{c['edge_id']}"):
                        _confermati.append({'source': f"a{c['a1_id']}", 'target': f"a{c['a2_id']}",
                                             'motivo': c['nota'], 'peso': c['peso'],
                                             'data_conferma': datetime.now().strftime('%Y-%m-%d')})
                        with open(_conf_path, 'w') as f:
                            json.dump(_confermati, f, ensure_ascii=False, indent=2)
                        st.cache_data.clear(); st.success('Contraddizione confermata.'); st.rerun()
                with gc3:
                    if st.button('Rifiuto', key=f"g3d_rif_{c['edge_id']}"):
                        _rifiutati.append({'source': f"a{c['a1_id']}", 'target': f"a{c['a2_id']}",
                                            'data_rifiuto': datetime.now().strftime('%Y-%m-%d')})
                        with open(_rif_path, 'w') as f:
                            json.dump(_rifiutati, f, ensure_ascii=False, indent=2)
                        st.cache_data.clear(); st.info('Contraddizione rimossa.'); st.rerun()
        if _mostrate == 0:
            st.success('Tutte le contraddizioni rilevate sono già state valutate.')


# ═══════════════════════════════════════════════════════════════════════
# TAB 1: STATISTICHE
# ═══════════════════════════════════════════════════════════════════════
with tab_stat:
    c1, c2, c3, c4, c5 = st.columns(5)
    _vettori_raw = last.get('vettori_totali', 0)
    _vettori_k   = f'{_vettori_raw / 1000:.1f}k' if isinstance(_vettori_raw, (int, float)) else '?'
    c1.metric('Vettori in memoria', _vettori_k,
              help='Chunk totali indicizzati in zeno.db. Espressi in migliaia (k).')
    c2.metric('Sintesi prodotte', last.get('memoria_elaborata', {}).get('sintesi', 0))
    c3.metric('Sessioni archiviate', last.get('memoria_elaborata', {}).get('sessioni', 0))
    c4.metric('Libri letti', last.get('libri', {}).get('read', 0))
    c5.metric('Libri totali', (last.get('libri', {}).get('read', 0) +
                               last.get('libri', {}).get('to-read', 0) +
                               last.get('libri', {}).get('currently-reading', 0)))
    st.divider()

    mem_base    = cfg['percorsi']['memoria']
    temi_dir    = os.path.join(mem_base, 'profilo', 'temi_v6')
    journal_dir = os.path.join(mem_base, 'snapshot_cognitivi')
    sessioni_dir = os.path.join(mem_base, 'query_archiviate')
    n_schede_tema = conta_file_dir(temi_dir, ['.md'])
    n_journal     = conta_file_dir(journal_dir, ['.md'])
    n_sessioni    = conta_file_dir(sessioni_dir, ['.json'])

    if n_schede_tema or n_journal or n_sessioni:
        st.subheader('Memoria elaborata')
        me1, me2, me3 = st.columns(3)
        me1.metric('Schede tema (mind.py)', n_schede_tema)
        me2.metric('Journal settimanali', n_journal)
        me3.metric('Sessioni archiviate', n_sessioni)
        st.divider()

    col1, col2 = st.columns(2)
    with col1:
        if len(snaps) > 1:
            st.subheader('Crescita della memoria')
            df_t = pd.DataFrame([{'data': s['data'],
                                   'vettori (k)': round(s.get('vettori_totali', 0) / 1000, 1)}
                                  for s in snaps])
            fig = px.line(df_t, x='data', y='vettori (k)', markers=True)
            fig.update_traces(line_color='#2E75B6', line_width=3)
            fig.update_layout(margin=dict(t=20, b=20), font=dict(color=None),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                yaxis_title='Vettori (k)')
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info('Servono almeno 2 snapshot per il grafico temporale.')
    with col2:
        st.subheader('Formazione per area')
        _form_dir = os.path.join(cfg['percorsi']['documents'], 'formazione')
        if os.path.exists(_form_dir):
            _form_data = []
            for _sub in sorted(os.listdir(_form_dir)):
                _sub_path = os.path.join(_form_dir, _sub)
                if os.path.isdir(_sub_path):
                    # os.walk conta ricorsivamente tutti i file nelle sotto-sottocartelle
                    _n = sum(
                        len([f for f in _files if not f.startswith('.')])
                        for _, _, _files in os.walk(_sub_path)
                    )
                    if _n > 0:
                        _form_data.append({'sottocartella': _sub, 'documenti': _n})
            # Conta anche i file nella root di formazione (senza sottocartella)
            _root_files = len([f for f in os.listdir(_form_dir)
                                if os.path.isfile(os.path.join(_form_dir, f))
                                and not f.startswith('.')])
            if _root_files > 0:
                _form_data.insert(0, {'sottocartella': '(root)', 'documenti': _root_files})
            if _form_data:
                df_form = pd.DataFrame(_form_data).sort_values('documenti', ascending=True)
                fig2 = px.bar(df_form, x='documenti', y='sottocartella',
                              orientation='h', color='documenti',
                              color_continuous_scale='Blues')
                fig2.update_layout(showlegend=False, coloraxis_showscale=False,
                    margin=dict(t=20, b=20), font=dict(color=None),
                    paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                    yaxis_title='', xaxis_title='Documenti')
                st.plotly_chart(fig2, use_container_width=True)
            else:
                st.info('Nessun documento in documents/formazione/.')
        else:
            st.info('Cartella formazione non trovata.')

    # Temi prioritari / Macro-temi: stessi nodi e archi del tab "Grafo 3D"
    # (zeno.db via grafo3d.dati_grafo, cache condivisa) — numeri sempre
    # coerenti fra le due viste, niente più grafo_connessioni.json.
    _dati_g_stat = carica_dati_grafo_zeno()
    _gradi_stat = {}
    for _l in _dati_g_stat.get('links', []):
        _gradi_stat[_l['source']] = _gradi_stat.get(_l['source'], 0) + 1
        _gradi_stat[_l['target']] = _gradi_stat.get(_l['target'], 0) + 1
    _temi_nodi_stat = [n for n in _dati_g_stat.get('nodes', []) if n.get('group') == 'topic']
    _max_grado_stat = max([_gradi_stat.get(n['id'], 0) for n in _temi_nodi_stat], default=0) or 1

    col3, col4 = st.columns(2)
    with col3:
        st.subheader('Temi per connessioni')
        st.caption('Tutti i 10 temi V6 ordinati per connessioni nel grafo. '
                   'La ⭐ marca quelli segnati prioritari in zeno_config.yaml.')
        _prio_labels_stat = {t.lower() for t in cfg.get('temi', {}).get('prioritari', [])}
        # TUTTI i temi (non solo i prioritari): prima il bug mostrava una barra
        # sola perché i nomi 'prioritari' del config V5 non combaciano coi 10
        # topic V6 (solo 'Geopolitica' matchava). Ora si mostrano tutti, con ⭐.
        temi_prio = [{'tema': ('⭐ ' if n['label'].lower() in _prio_labels_stat else '') + n['label'],
                      'connessioni': max(1, _gradi_stat.get(n['id'], 0))}
                     for n in _temi_nodi_stat]
        if temi_prio:
            df_prio = pd.DataFrame(temi_prio).sort_values('connessioni', ascending=True)
            fig3 = px.bar(df_prio, x='connessioni', y='tema',
                          orientation='h', color='connessioni',
                          color_continuous_scale='Blues')
            fig3.update_layout(showlegend=False, coloraxis_showscale=False,
                margin=dict(t=20, b=20), font=dict(color=None),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                yaxis_title='', xaxis_title='Connessioni nel grafo')
            st.plotly_chart(fig3, use_container_width=True)
        else:
            st.info('Grafo non ancora disponibile: apri il tab "Grafo 3D" o verifica zeno.db.')
    with col4:
        st.subheader('Macro-temi')
        if _temi_nodi_stat:
            temi_nodi = [(n['label'], _gradi_stat.get(n['id'], 0) / _max_grado_stat,
                          _gradi_stat.get(n['id'], 0), n['label'].lower() in _prio_labels_stat)
                         for n in _temi_nodi_stat]
            temi_nodi.sort(key=lambda x: -x[1])
            top = temi_nodi[:15]
            df_mt = pd.DataFrame(top, columns=['tema', 'centralita', 'connessioni', 'prioritario'])
            df_mt['tema_label'] = df_mt.apply(
                lambda r: f"⭐ {r['tema']}" if r['prioritario'] else r['tema'], axis=1)
            fig4 = px.bar(df_mt, x='tema_label', y='connessioni',
                          color='centralita', color_continuous_scale='Blues',
                          hover_data=['centralita'])
            fig4.update_layout(margin=dict(t=20, b=20), font=dict(color=None),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                xaxis_tickangle=-45, yaxis_title='connessioni nel grafo')
            st.plotly_chart(fig4, use_container_width=True)
        else:
            st.info('Grafo non ancora disponibile: apri il tab "Grafo 3D" o verifica zeno.db.')

    libri = last.get('libri', {})
    if libri:
        st.divider()
        st.subheader('Biblioteca')
        lc1, lc2, lc3 = st.columns(3)
        lc1.metric('Letti', libri.get('read', 0))
        lc2.metric('In corso', libri.get('currently-reading', 0))
        lc3.metric('Da leggere', libri.get('to-read', 0))


# ═══════════════════════════════════════════════════════════════════════
# TAB 3: JOURNAL
# ═══════════════════════════════════════════════════════════════════════
with tab_journal:
    snap_dir = os.path.join(cfg['percorsi']['memoria'], 'snapshot_cognitivi')
    if not os.path.exists(snap_dir):
        st.info('Nessun journal ancora generato. Verra creato automaticamente da mind.py.')
    else:
        journals = sorted([f for f in os.listdir(snap_dir) if f.startswith('journal_')], reverse=True)
        if not journals:
            st.info('Nessun journal disponibile.')
        else:
            selezionato = st.selectbox('Seleziona settimana', journals,
                format_func=lambda x: x.replace('journal_', '').replace('.md', ''))
            if selezionato:
                content = open(os.path.join(snap_dir, selezionato), encoding='utf-8').read()
                if content.startswith('---'):
                    fine = content.find('---', 3)
                    if fine > 0: content = content[fine+3:].strip()
                st.markdown(content)


# ═══════════════════════════════════════════════════════════════════════
# TAB 4: EVOLUZIONE
# ═══════════════════════════════════════════════════════════════════════
with tab_storia:
    has_snaps   = len(snaps) > 1
    has_history = len(history) > 1

    if not has_snaps and not has_history:
        st.info('La storia inizia ad accumularsi dopo più run. '
                'Ogni giorno che Zeno gira aggiunge un punto al grafico.')
    else:
        st.subheader('Crescita della struttura cognitiva nel tempo')

        # ── Grafico 1: Documenti (y-sin) + Nodi/Connessioni grafo (y-des) ────
        fig_ev = go.Figure()

        if has_snaps:
            doc_totali = []
            for s in snaps:
                tot = sum(v.get('totale', 0) for v in s.get('documenti', {}).values())
                doc_totali.append({'data': s['data'], 'documenti': tot})
            df_d = pd.DataFrame(doc_totali).drop_duplicates('data').sort_values('data')
            if df_d['documenti'].sum() > 0:
                fig_ev.add_trace(go.Scatter(
                    x=df_d['data'], y=df_d['documenti'],
                    name='Documenti totali', mode='lines+markers',
                    yaxis='y1',
                    line=dict(color='#1A7A4A', width=2)))

        if has_history:
            df_h = pd.DataFrame(history).drop_duplicates('data').sort_values('data')
            fig_ev.add_trace(go.Scatter(
                x=df_h['data'], y=df_h['n_nodi'],
                name='Nodi grafo', mode='lines+markers',
                yaxis='y2',
                line=dict(color='#1E3A5F', width=2)))
            fig_ev.add_trace(go.Scatter(
                x=df_h['data'], y=df_h['n_archi'],
                name='Connessioni', mode='lines+markers',
                yaxis='y2',
                line=dict(color='#85C1E9', width=2)))
            if 'archi_confermati' in df_h.columns:
                fig_ev.add_trace(go.Scatter(
                    x=df_h['data'], y=df_h['archi_confermati'],
                    name='Connessioni confermate', mode='lines+markers',
                    yaxis='y2',
                    line=dict(color='#B7770D', width=2)))

        fig_ev.update_layout(
            height=400, margin=dict(t=20, b=20),
            font=dict(color=None),
            paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
            legend=dict(orientation='h', yanchor='bottom', y=1.02,
                        font=dict(size=11, color=None)),
            yaxis=dict(
                title=dict(text='Documenti totali',
                           font=dict(color='#1A7A4A')),
                tickfont=dict(color='#1A7A4A'),
                gridcolor='rgba(26,122,74,0.2)',
            ),
            yaxis2=dict(
                title=dict(text='Nodi / Connessioni',
                           font=dict(color='#85C1E9')),
                tickfont=dict(color='#85C1E9'),
                overlaying='y',
                side='right',
                gridcolor='rgba(133,193,233,0.1)',
            ),
        )
        st.plotly_chart(fig_ev, use_container_width=True)

        # ── Grafico 2: Memoria elaborata + Lacune (scala omogenea, asse unico) ─
        _has_mem  = has_snaps
        _has_lac  = has_history and 'n_lacune' in pd.DataFrame(history).columns

        if _has_mem or _has_lac:
            st.subheader('Memoria elaborata e lacune nel tempo')
            fig_sec = go.Figure()

            if _has_mem:
                _mem_rows = []
                for s in snaps:
                    _mem = s.get('memoria_elaborata', {})
                    _tot = _mem.get('sintesi', 0) + _mem.get('sessioni', 0)
                    _mem_rows.append({'data': s['data'], 'memoria': _tot})
                df_mem = pd.DataFrame(_mem_rows).drop_duplicates('data').sort_values('data')
                if df_mem['memoria'].sum() > 0:
                    fig_sec.add_trace(go.Scatter(
                        x=df_mem['data'], y=df_mem['memoria'],
                        name='Memoria elaborata', mode='lines+markers',
                        line=dict(color='#E67E22', width=2, dash='dot')))

            if _has_lac:
                # df_h già definito nel blocco has_history sopra; ridefiniamo
                # in modo sicuro per non dipendere dallo scope
                df_h2 = pd.DataFrame(history).drop_duplicates('data').sort_values('data')
                if df_h2['n_lacune'].sum() > 0:
                    fig_sec.add_trace(go.Scatter(
                        x=df_h2['data'], y=df_h2['n_lacune'],
                        name='Lacune', mode='lines+markers',
                        line=dict(color='#7D3C98', width=2, dash='dot')))

            fig_sec.update_layout(
                height=320, margin=dict(t=20, b=20),
                font=dict(color=None),
                paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
                legend=dict(orientation='h', yanchor='bottom', y=1.02,
                            font=dict(size=11, color=None)),
                yaxis=dict(
                    title=dict(text='Conteggio',
                               font=dict(color=None)),
                    tickfont=dict(color=None),
                    gridcolor='rgba(168,200,232,0.15)',
                ),
            )
            st.plotly_chart(fig_sec, use_container_width=True)

        st.divider()

        col_ts1, col_ts2 = st.columns(2)
        with col_ts1:
            if has_snaps:
                st.subheader('Storico snapshot')
                st.caption(
                    'Uno snapshot per giornata — registra lo stato del corpus '
                    'in quel momento. Clicca 🗑 per eliminare righe errate o duplicate.'
                )
                df_snap_tab = pd.DataFrame([
                    {'data': s['data'],
                     'vettori (k)': round(s.get('vettori_totali', 0) / 1000, 1),
                     'documenti': sum(v.get('totale', 0) for v in s.get('documenti', {}).values()),
                     'sessioni': s.get('memoria_elaborata', {}).get('sessioni', 0)}
                    for s in snaps
                ]).drop_duplicates('data').sort_values('data', ascending=False)

                # Intestazioni colonne
                h1, h2, h3, h4, h5 = st.columns([2, 2, 2, 2, 1])
                h1.markdown('**Data**')
                h2.markdown('**Vettori**',
                    help='Chunk totali indicizzati in zeno.db, in migliaia (k)')
                h3.markdown('**Documenti**',
                    help='File totali nel corpus (tutte le aree)')
                h4.markdown('**Sessioni**',
                    help='Sessioni di dialogo archiviate in memoria_elaborata')
                h5.markdown('**Del**')

                for i, row in df_snap_tab.iterrows():
                    c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 1])
                    c1.write(row['data'])
                    c2.write(str(row['vettori (k)']))
                    c3.write(str(row['documenti']))
                    c4.write(str(row['sessioni']))
                    if c5.button('🗑', key=f'del_snap_{row["data"]}',
                                 help=f'Elimina snapshot del {row["data"]}'):
                        snap_file = os.path.join(
                            stats_dir, f'snapshot_{row["data"]}.json')
                        if os.path.exists(snap_file):
                            os.remove(snap_file)
                            st.cache_data.clear()
                            st.success(f'Snapshot {row["data"]} eliminato.')
                            st.rerun()
                        else:
                            st.warning(f'File non trovato: {snap_file}')

        with col_ts2:
            if has_history:
                st.subheader('Storico grafo')
                st.caption(
                    'Una entry per giornata — registra la struttura del grafo '
                    'cognitivo in quel momento. Clicca 🗑 per eliminare righe errate o duplicate.'
                )
                df_h_tab = pd.DataFrame(history).drop_duplicates('data').sort_values('data', ascending=False)
                cols_show = [c for c in ['data', 'n_nodi', 'n_archi', 'n_temi', 'n_lacune']
                             if c in df_h_tab.columns]

                # Intestazioni colonne con helper
                INTESTAZIONI_GRAFO = {
                    'data':    ('Data', None),
                    'n_nodi':  ('Nodi', 'Nodi totali nel grafo: temi, documenti, lacune, emergenti'),
                    'n_archi': ('Archi', 'Connessioni totali: strutturali + semantiche + dialogiche + gerarchiche'),
                    'n_temi':  ('Temi', 'Schede tema generate da mind.py'),
                    'n_lacune':('Lacune', 'Zone di conoscenza mancante inferite dal grafo'),
                }
                header_cols = st.columns(len(cols_show) + 1)
                for j, col_name in enumerate(cols_show):
                    label, help_txt = INTESTAZIONI_GRAFO.get(col_name, (col_name, None))
                    if help_txt:
                        header_cols[j].markdown(f'**{label}**', help=help_txt)
                    else:
                        header_cols[j].markdown(f'**{label}**')
                header_cols[-1].markdown('**Del**')

                for _, row in df_h_tab.iterrows():
                    row_cols = st.columns(len(cols_show) + 1)
                    for j, col_name in enumerate(cols_show):
                        row_cols[j].write(str(row.get(col_name, '')))
                    if row_cols[-1].button('🗑', key=f'del_graph_{row["data"]}',
                                           help=f'Elimina entry grafo del {row["data"]}'):
                        history_path = os.path.join(stats_dir, 'grafo_history.json')
                        if os.path.exists(history_path):
                            try:
                                h = json.load(open(history_path, encoding='utf-8'))
                                h = [e for e in h if e.get('data') != row['data']]
                                json.dump(h, open(history_path, 'w',
                                          encoding='utf-8'), ensure_ascii=False, indent=2)
                                st.cache_data.clear()
                                st.success(f'Entry grafo {row["data"]} eliminata.')
                                st.rerun()
                            except Exception as _e:
                                st.error(f'Errore: {_e}')


# ═══════════════════════════════════════════════════════════════════════
# TAB 5: GESTIONE TEMI
# ═══════════════════════════════════════════════════════════════════════
with tab_temi:
    # ── Albero dei temi, modificabile (28/09/2026): i temi li decide l'utente.
    # Zeno classifica le asserzioni nei temi che trova qui, guidato dalle
    # descrizioni; ciò che non ci rientra resta nel limbo.
    st.subheader('Albero dei temi')
    st.caption('Qui crei, rinomini, sposti, descrivi e ritiri i temi. La descrizione '
               'conta: è ciò che Zeno legge per decidere cosa ci sta dentro, e compare '
               'nella scheda del tema. Scrivi anche cosa NON ci va, se due temi si somigliano. '
               'Numeri in documenti (ogni documento vale 1).')
    _albero = carica_albero_temi()
    _madri = [r for r in _albero if r[2] is None]
    for _mid, _mnome, _, _mdescr, _mdoc, _mass in sorted(_madri, key=lambda r: -r[4]):
        _figli = [r for r in _albero if r[2] == _mid]
        with st.expander(f'**{_mnome}** · {_mdoc} documenti · {len(_figli)} figli'):
            st.markdown(f'*{_mdescr or "— senza descrizione —"}*')
            for _, _fnome, _, _fdescr, _fdoc, _ in sorted(_figli, key=lambda r: -r[4]):
                st.markdown(f"- **{_fnome}** <span style='color:#888;font-size:11px'>"
                            f"· {_fdoc} documenti</span><br><span style='font-size:12px'>"
                            f"{_fdescr or '— senza descrizione —'}</span>", unsafe_allow_html=True)

    if st.session_state.get('msg_temi'):
        st.success(st.session_state.pop('msg_temi'))
    _NUOVO = '➕ Nuovo tema'
    _SENZA_MADRE = '— nessuna: è un tema madre —'
    _nomi = [r[1] for r in _albero]
    _scelto = st.selectbox('Tema da modificare', [_NUOVO] + _nomi, key='tema_scelto')
    _riga = next((r for r in _albero if r[1] == _scelto), None)
    _nome_madre = {r[0]: r[1] for r in _albero}
    with st.form('form_tema', clear_on_submit=False):
        _f_nome = st.text_input('Nome', value='' if _riga is None else _riga[1])
        _f_descr = st.text_area('Descrizione (cosa ci sta dentro e cosa no)',
                                value='' if _riga is None else _riga[3], height=90)
        _opzioni_madre = [_SENZA_MADRE] + [r[1] for r in _madri if _riga is None or r[0] != _riga[0]]
        _madre_ora = _nome_madre.get(_riga[2]) if _riga and _riga[2] else _SENZA_MADRE
        _f_madre = st.selectbox('Tema madre', _opzioni_madre,
                                index=_opzioni_madre.index(_madre_ora) if _madre_ora in _opzioni_madre else 0)
        _salva = st.form_submit_button('Salva')
    if _salva:
        _madre = None if _f_madre == _SENZA_MADRE else _f_madre
        if _riga is None:
            _err = _modifica_temi('crea', _f_nome, _f_descr, _madre)
        else:
            _err = _modifica_temi('modifica', _riga[1], nuovo_nome=_f_nome,
                                  descrizione=_f_descr, madre=_madre)
        if _err:
            st.error(_err)
        else:
            st.session_state['msg_temi'] = (f'Tema «{_f_nome}» salvato. Se è nuovo, al prossimo '
                                            'giro sui temi Zeno ti proporrà qui sotto i documenti '
                                            'da cui partire («Da seminare»).')
            st.rerun()
    if _riga is not None:
        # ritiro in due passi: il primo click chiede conferma
        if st.session_state.get('ritiro_da_confermare') != _riga[1]:
            if st.button(f'Ritira «{_riga[1]}»', key='ritira_tema'):
                st.session_state['ritiro_da_confermare'] = _riga[1]
                st.rerun()
        else:
            st.warning(f'Ritirare «{_riga[1]}»? Le sue asserzioni passano al tema madre '
                       '(o al limbo, se è un tema madre).')
            _r1, _r2 = st.columns([1, 1])
            if _r1.button('Sì, ritira', key='ritira_si'):
                st.session_state.pop('ritiro_da_confermare', None)
                _err = _modifica_temi('ritira', _riga[1])
                if _err:
                    st.error(_err)
                else:
                    st.session_state['msg_temi'] = f'Tema «{_riga[1]}» ritirato.'
                    st.session_state.pop('tema_scelto', None)
                    st.rerun()
            if _r2.button('Annulla', key='ritira_no'):
                st.session_state.pop('ritiro_da_confermare', None)
                st.rerun()

    st.divider()
    # ── V6: proposte automatiche di temi e prioritari (Zeno propone, tu
    # confermi con un click, editabile). Rigenerate da `zeno temi` / indicizza.
    st.subheader('Temi prioritari')
    _prio_now = cfg.get('temi', {}).get('prioritari', [])
    _prio_prop = carica_prioritari_proposti()
    _topic_v6 = carica_nomi_temi_zeno()
    if _topic_v6:
        _sugg = _prio_prop.get('suggeriti', [])
        if _sugg and set(s.lower() for s in _sugg) != set(p.lower() for p in _prio_now):
            st.caption('Zeno suggerisce questi prioritari (volume + cosa stai '
                       'alimentando di recente):')
            st.markdown('  ·  '.join(f'**{s}**' for s in _sugg))
            if st.button('✓ Usa i prioritari suggeriti', key='usa_prio_sugg'):
                for t in list(_prio_now):
                    _aggiorna_prioritari(cfg, t, False)
                for s in _sugg:
                    _aggiorna_prioritari(cfg, s, True)
                st.cache_data.clear()
                st.success('Prioritari aggiornati.'); st.rerun()
        _sel_prio = st.multiselect(
            'Prioritari attuali (modificabili a mano):', options=_topic_v6,
            default=[t for t in _topic_v6 if t.lower() in {p.lower() for p in _prio_now}],
            key='prio_multiselect')
        if st.button('Salva prioritari', key='salva_prio_ms'):
            for t in _topic_v6:
                _aggiorna_prioritari(cfg, t, t in _sel_prio)
            st.cache_data.clear()
            st.success('Prioritari salvati.'); st.rerun()

    st.divider()
    st.subheader('Temi nuovi proposti')
    _prop = carica_temi_proposti()
    _lista = [(k, p) for k in ('madri_nuove', 'figli_nuovi') for p in _prop.get(k, [])]
    if st.session_state.get('msg_proposte'):
        st.success(st.session_state.pop('msg_proposte'))
    if not _lista and not _prop.get('semine'):
        st.caption('Nessuna proposta in sospeso. Zeno le rigenera ogni settimana (job «temi») '
                   'dai gruppi di asserzioni che nessun tema raccoglie bene.')
    else:
        st.caption(f"{len(_lista)} proposte del {_prop.get('generato', '')[:10]}. Ogni gruppo "
                   'raccoglie asserzioni simili da almeno 10 documenti diversi. Correggi nome, '
                   'descrizione e madre prima di crearlo; rifiutato, non torna.')
        _madri_nomi = [r[1] for r in carica_albero_temi() if r[2] is None]
        for _k, _p in _lista:
            _nome_prop = _p.get('proposta_nome', '?')
            _dove = f"sotto «{_p['madre']}»" if _p.get('madre') else 'nuovo tema madre, dal limbo'
            with st.expander(f"**{_nome_prop}** · {_p['n_fonti']} documenti · {_dove}"):
                st.markdown(f"*{_p.get('proposta_descrizione', '')}*")
                st.markdown('\n'.join(f'- {e[:220]}' for e in _p.get('esempi', [])))
                with st.form(f"prop_{_p['chiave']}"):
                    _n = st.text_input('Nome', value=_nome_prop)
                    _d = st.text_area('Descrizione', value=_p.get('proposta_descrizione', ''), height=70)
                    _opz = ['— nessuna: è un tema madre —'] + _madri_nomi
                    _m0 = _p.get('madre') if _p.get('madre') in _madri_nomi else _opz[0]
                    _m = st.selectbox('Tema madre', _opz, index=_opz.index(_m0))
                    _c1, _c2 = st.columns(2)
                    _crea = _c1.form_submit_button('Crea il tema')
                    _rif = _c2.form_submit_button('Rifiuta')
                if _crea:
                    _err, _num = _decidi_proposta(_p['chiave'], _n, _d,
                                                  None if _m == _opz[0] else _m)
                    if _err:
                        st.error(_err)
                    else:
                        st.session_state['msg_proposte'] = f'Tema «{_n}» creato con {_num} asserzioni.'
                        st.rerun()
                if _rif:
                    _decidi_proposta(_p['chiave'])
                    st.session_state['msg_proposte'] = f'Proposta «{_nome_prop}» rifiutata.'
                    st.rerun()
    for _v in _prop.get('semine', []):
        with st.expander(f"🌱 Da seminare: **{_v['tema']}** · {len(_v['documenti'])} documenti candidati"):
            st.caption('Tema nuovo, ancora quasi vuoto: il classificatore lo imparerà dagli esempi. '
                       'Questi sono i documenti più vicini alla sua descrizione: spunta quelli giusti.')
            with st.form(f"semina_{_v['chiave']}"):
                _scelti = []
                for _d in _v['documenti']:
                    if st.checkbox(f"{_d['titolo']} ({len(_d['assertion_ids'])} asserzioni)",
                                   key=f"sem_{_v['chiave']}_{_d['source_id']}"):
                        _scelti.append(_d['source_id'])
                    st.caption(' / '.join(e[:160] for e in _d['esempi']))
                _ok = st.form_submit_button('Aggiungi i documenti spuntati')
            if _ok:
                _err, _num = _decidi_semina(_v['chiave'], _scelti)
                if _err:
                    st.error(_err)
                else:
                    st.session_state['msg_proposte'] = f"«{_v['tema']}»: {_num} asserzioni aggiunte."
                    st.rerun()
    _altri = [(t, v) for t, v in (('Unioni', _prop.get('unioni', [])),
                                  ('Ritiri', _prop.get('ritiri', [])),
                                  ('Spostamenti', _prop.get('spostamenti', []))) if v]
    if _altri:
        with st.expander('Altri suggerimenti sui temi esistenti (da leggere, si applicano a mano sopra)'):
            for _t, _v in _altri:
                st.markdown(f'**{_t}**')
                for _u in _v:
                    if _t == 'Unioni':
                        st.markdown(f"- «{_u['tema']}» dentro «{_u['dentro']}» ({_u['perche']}, "
                                    f"{_u['n_fonti']} documenti)")
                    elif _t == 'Ritiri':
                        st.markdown(f"- «{_u['tema']}»: solo {_u['n_fonti']} documenti")
                    else:
                        st.markdown(f"- «{_u['tema']}» da «{_u['da']}» a «{_u['a']}» ({_u['perche']})")
    st.divider()
    st.markdown('##### Schede tema V6')
    st.caption('Schede generate dai dati puliti di zeno.db: cosa sai, come la '
               'pensi, tensioni, lacune. Rigenerabili con `python -m zeno_engine schede`.')
    _schede_dir = os.path.join(cfg['percorsi']['memoria'], 'profilo', 'temi_v6')
    # esclude i file AppleDouble ._* di ExFAT (binari, danno errore in lettura)
    _md_v6 = lambda d: [f for f in os.listdir(d) if f.endswith('.md') and not f.startswith('.')]
    if not os.path.isdir(_schede_dir) or not _md_v6(_schede_dir):
        st.info('Nessuna scheda V6 ancora generata. Lanciale con '
                '`python -m zeno_engine schede` (dal terminale).')
    else:
        _prio_low = {p.lower() for p in cfg.get('temi', {}).get('prioritari', [])}
        def _label_sch(f):
            return f.replace('tema_', '').replace('.md', '').replace('-', ' ')
        _files_sch = sorted(_md_v6(_schede_dir),
                            key=lambda f: (_label_sch(f).lower() not in _prio_low, _label_sch(f).lower()))
        for _f in _files_sch:
            _fp = os.path.join(_schede_dir, _f)
            _fm = leggi_frontmatter_dash(_fp)
            _nome = _fm.get('tema', _label_sch(_f))
            _star = '⭐ ' if _nome.lower() in _prio_low else ''
            _tot = _fm.get('asserzioni_totali', '?')
            _mio = _fm.get('asserzioni_pensiero_diretto', '?')
            with st.expander(f'{_star}{_nome}  ·  {_tot} asserzioni ({_mio} dal tuo pensiero)'):
                try:
                    _c = open(_fp, encoding='utf-8').read()
                    if _c.startswith('---'):
                        _e = _c.find('---', 3)
                        if _e > 0:
                            _c = _c[_e + 3:].strip()
                    st.markdown(_c)
                except Exception as _e:
                    st.error(f'Errore lettura scheda: {_e}')




# ═══════════════════════════════════════════════════════════════════════
# TAB 6: LACUNE APERTE
# ═══════════════════════════════════════════════════════════════════════
with tab_lacune:
    lacune_path = os.path.join(stats_dir, 'lacune_aperte.json')

    if not os.path.exists(lacune_path):
        st.info(
            'Nessun file `lacune_aperte.json` trovato. '
            'Le lacune vengono create automaticamente dalla chat (modalità ARCHIVIO e MAIEUTICA) '
            'e dal run periodico di `mind.py`.'
        )
    else:
        lacune = carica_lacune_aperte()

        if not lacune:
            st.success('Nessuna lacuna aperta.')
        else:
            # ── Metriche riepilogative ────────────────────────────────────
            n_tot    = len(lacune)
            n_old    = sum(1 for l in lacune if l.get('giorni_aperti', 0) >= 14)
            fonti    = {}
            for l in lacune:
                f = l.get('fonte', 'sconosciuta')
                fonti[f] = fonti.get(f, 0) + 1
            lm1, lm2, lm3 = st.columns(3)
            lm1.metric('Lacune totali', n_tot)
            lm2.metric('Persistenti (≥14gg)', n_old)
            lm3.metric('Fonti distinte', len(fonti))

            # Doppioni: stessa tensione con un'altra data o con le frasi in
            # ordine inverso, stesso tema scritto in due modi (zeno_engine/lacune.py).
            from zeno_engine import lacune as _lacune_z
            _n_doppi = _lacune_z.conta_doppioni(stats_dir)
            if _n_doppi:
                _d1, _d2 = st.columns([3, 1])
                _d1.warning(f'{_n_doppi} lacune sono copie di altre già in elenco.')
                if _d2.button(f'Unisci i doppioni ({_n_doppi})', key='lac_unisci'):
                    _tolte = _lacune_z.unisci_doppioni(stats_dir)
                    st.cache_data.clear()
                    st.success(f'Tolte {_tolte} copie: resta la voce più vecchia di ognuna.')
                    st.rerun()
            st.divider()

            # ── Tabella riepilogativa ─────────────────────────────────────
            st.subheader(f'Lacune aperte ({n_tot})')
            st.caption('Ordinate per anzianità decrescente. '
                       '"✓ Risolta" toglie la lacuna, ma un nuovo controllo può riproporla; '
                       '"🗑 Elimina" la toglie con tutte le sue copie e non torna più '
                       '(per refusi, doppioni o lacune sbagliate).')

            df_lacune = pd.DataFrame([{
                'Testo lacuna':      (l.get('testo', '')[:80] + '…'
                                      if len(l.get('testo', '')) > 80
                                      else l.get('testo', '')),
                'Giorni aperti':     l.get('giorni_aperti', 0),
                'Fonte':             l.get('fonte', '?'),
                'Area':              l.get('area', '?'),
                'Prima rilevazione': l.get('data_prima_rilevazione', '')[:10],
            } for l in lacune])
            st.dataframe(df_lacune, use_container_width=True, hide_index=True)

            # ── Filtro per fonte ──────────────────────────────────────────
            fonti_disponibili = sorted({l.get('fonte', '?') for l in lacune})
            filtro_fonte = st.multiselect(
                'Filtra per fonte', fonti_disponibili, default=fonti_disponibili,
                key='lac_filtro_fonte'
            )
            lacune_filtrate = [l for l in lacune if l.get('fonte', '?') in filtro_fonte]

            st.divider()

            # ── Dettaglio espandibile per ogni lacuna ─────────────────────
            for i, lacuna in enumerate(lacune_filtrate):
                gg   = lacuna.get('giorni_aperti', 0)
                area = lacuna.get('area', '?')
                txt  = lacuna.get('testo', '')
                badge_gg = f'🔴 {gg}gg' if gg >= 14 else f'🟡 {gg}gg'

                with st.expander(
                    f"{badge_gg} · [{area}] {txt[:65]}{'…' if len(txt) > 65 else ''}",
                    expanded=False
                ):
                    st.markdown(f"**Testo completo:**  \n{txt}")
                    q = lacuna.get('query_originale', '')
                    if q:
                        st.markdown(f"**Query originale:** `{q}`")
                    st.caption(
                        f"Fonte: `{lacuna.get('fonte', '?')}` · "
                        f"Aperta il: {lacuna.get('data_prima_rilevazione', '')[:10]}"
                    )

                    # ── Azioni ────────────────────────────────────────────────
                    _a1, _a2, _a3, _a4 = st.columns(4)
                    with _a1:
                        _lac_id_t = re.sub(r'\W', '_', lacuna.get('testo', '')[:60].lower())
                        if st.button('📌 Affronta con Zeno', key=f'lac_dial_{i}',
                                     help='Aggiunge all\'agenda delle sessioni MAIEUTICA'):
                            _segna_da_dialogo(cfg, _lac_id_t,
                                              lacuna.get('testo', ''),
                                              lacuna.get('area', ''))
                            st.success('Aggiunta all\'agenda MAIEUTICA.')
                            st.rerun()
                    with _a2:
                        if st.button('🔍 Cerca sul web', key=f'lac_web_{i}'):
                            with st.spinner('Ricerca...'):
                                _wr, _we = cerca_sul_web(lacuna.get('testo', '')[:80])
                            if _we:
                                st.error(_we)
                            elif _wr:
                                st.session_state[f'lac_wres_{i}'] = _wr
                    with _a3:
                        if st.button('✓ Risolta', key=f'lac_risolvi_{i}'):
                            try:
                                tutte  = json.load(open(lacune_path, encoding='utf-8'))
                                target = lacuna.get('testo', '')
                                tutte  = [l for l in tutte if l.get('testo', '') != target]
                                with open(lacune_path, 'w', encoding='utf-8') as _f:
                                    json.dump(tutte, _f, ensure_ascii=False, indent=2)
                                st.cache_data.clear()
                                st.success('Lacuna rimossa.')
                                st.rerun()
                            except Exception as _e:
                                st.error(f'Errore: {_e}')
                    with _a4:
                        if st.button('🗑 Elimina', key=f'lac_elimina_{i}',
                                     help='Toglie la lacuna e le sue copie; '
                                          'non verrà più riproposta'):
                            try:
                                from zeno_engine import lacune as _lacune_z
                                _n = _lacune_z.elimina(stats_dir, lacuna.get('testo', ''))
                                st.cache_data.clear()
                                st.success(f'Eliminata ({_n} voci).')
                                st.rerun()
                            except Exception as _e:
                                st.error(f'Errore: {_e}')

                    # Risultati web
                    _wres_key = f'lac_wres_{i}'
                    if _wres_key in st.session_state:
                        st.warning('**Fonti web — non provengono dal tuo archivio.**')
                        _sel_w = []
                        for _jj, _rw in enumerate(st.session_state[_wres_key]):
                            if st.checkbox(f"[{_rw['titolo']}]({_rw['url']})",
                                           key=f'lac_wchk_{i}_{_jj}'):
                                _sel_w.append(_rw)
                            st.caption(_rw['estratto'][:300])
                        if _sel_w and st.button('💾 Salva selezionati', key=f'lac_wsalva_{i}'):
                            _ts_w = datetime.now().strftime('%Y%m%d_%H%M')
                            for _jj, _rw in enumerate(_sel_w):
                                _fp_w = (cfg['percorsi']['documents'] +
                                         f"/articoli_saggistica/web_{_ts_w}_{_jj}.md")
                                with open(_fp_w, 'w', encoding='utf-8') as _fw:
                                    _fw.write(
                                        f"---\ntipo: fonte_web\nurl: {_rw['url']}\n"
                                        f"data: {_ts_w}\nattribuzione: sapere_acquisito\n---\n\n"
                                        f"# {_rw['titolo']}\n\n{_rw['estratto']}\n")
                            del st.session_state[_wres_key]
                            st.success(f'{len(_sel_w)} fonte/i salvata/e.')
                            st.rerun()

                    # URL manuale
                    _url_k = f'lac_url_{i}'
                    _url_rk = f'lac_urlres_{i}'
                    _url_v = st.text_input('Incolla URL', key=_url_k, placeholder='https://...')
                    if _url_v and st.button('📥 Importa pagina', key=f'lac_urlbtn_{i}'):
                        with st.spinner('Scarico...'):
                            _tit_u, _tes_u = importa_da_url_dash(_url_v)
                        if _tes_u and _tit_u:
                            st.session_state[_url_rk] = {'titolo': _tit_u, 'testo': _tes_u, 'url': _url_v}
                        else:
                            st.error(_tes_u)
                    if _url_rk in st.session_state:
                        _doc_u = st.session_state[_url_rk]
                        st.success(f"Estratta: **{_doc_u['titolo']}** ({len(_doc_u['testo'])} chars)")
                        if st.button('💾 Salva in archivio', key=f'lac_urlsalva_{i}'):
                            _ts_u = datetime.now().strftime('%Y%m%d_%H%M')
                            _slg_u = re.sub(r'[^\w]', '_', _doc_u['titolo'])[:50]
                            with open(cfg['percorsi']['documents'] + f"/articoli_saggistica/url_{_ts_u}_{_slg_u}.md",
                                      'w', encoding='utf-8') as _fw:
                                _fw.write(f"---\ntipo: fonte_web\nurl: {_doc_u['url']}\n"
                                          f"data: {_ts_u}\nattribuzione: sapere_acquisito\n---\n\n"
                                          f"# {_doc_u['titolo']}\n\n{_doc_u['testo']}\n")
                            del st.session_state[_url_rk]
                            st.success('Salvato.')
                            st.rerun()


# ═══════════════════════════════════════════════════════════════════════
# TAB: ENTITÀ — conferma alias (es. "Kafka" → "Franz Kafka")
# Le proposte le genera `zeno alias` (euristica ad alta precisione); qui
# L'utente conferma/rifiuta. Conferma = scrive entities.canonical_id
# (reversibile); il grafo poi fonde l'alias nel canonico. MAI automatico.
# ═══════════════════════════════════════════════════════════════════════
with tab_entita:
    st.subheader('Alias entità da confermare')
    proposte = carica_proposte_alias()
    if not proposte:
        st.info('Nessuna proposta in sospeso. Rigenerale con `zeno alias` '
                '(dal terminale) dopo nuove ingestioni.')
    else:
        non_amb = [p for p in proposte if not p.get('ambiguo')]
        amb     = [p for p in proposte if p.get('ambiguo')]
        st.caption(
            f'{len(proposte)} proposte · {len(non_amb)} nette, {len(amb)} ambigue. '
            'Confermare fonde l\'alias nel nome completo (reversibile). '
            'Le ambigue (l\'alias combacia con più nomi) vanno guardate con attenzione.')

        # Azione di massa: le non ambigue molto citate sono quasi sempre corrette.
        col_a, col_b = st.columns([3, 2])
        with col_a:
            soglia = st.number_input('Soglia menzioni per la conferma di massa',
                                     min_value=1, max_value=100, value=5, step=1,
                                     key='alias_soglia')
        idonee = [p for p in non_amb if p.get('menzioni_canonico', 0) >= soglia]
        with col_b:
            st.markdown('&nbsp;')
            if st.button(f'✓ Conferma le {len(idonee)} nette con ≥{soglia} menzioni',
                         key='alias_bulk', disabled=not idonee):
                for p in idonee:
                    _conferma_alias(p['alias_id'], p['canonico_id'])
                carica_proposte_alias.clear()
                st.success(f'{len(idonee)} alias confermati.')
                st.rerun()

        st.divider()
        # Elenco rivedibile: non ambigue prima, max 60 per non appesantire.
        MAX_MOSTRA = 60
        for i, p in enumerate(proposte[:MAX_MOSTRA]):
            tag = '⚠️ ambigua' if p.get('ambiguo') else ''
            c1, c2, c3 = st.columns([6, 1, 1])
            with c1:
                st.markdown(
                    f"**{p['alias']}** → {p['canonico']} "
                    f"<span style='color:#888;font-size:11px;'>"
                    f"{p.get('menzioni_canonico', 0)} menz. · {p.get('kind','')} {tag}</span>",
                    unsafe_allow_html=True)
            with c2:
                if st.button('Conferma', key=f"alias_ok_{p['alias_id']}_{p['canonico_id']}"):
                    _conferma_alias(p['alias_id'], p['canonico_id'])
                    carica_proposte_alias.clear()
                    st.rerun()
            with c3:
                if st.button('Rifiuta', key=f"alias_no_{p['alias_id']}_{p['canonico_id']}"):
                    _rifiuta_alias(p['alias_id'], p['canonico_id'])
                    carica_proposte_alias.clear()
                    st.rerun()
        if len(proposte) > MAX_MOSTRA:
            st.caption(f'…e altre {len(proposte) - MAX_MOSTRA}. '
                       'Confermane/rifiutane un blocco e riappariranno le successive.')


# ═══════════════════════════════════════════════════════════════════════
# TAB 7: PROFILO COGNITIVO
# ═══════════════════════════════════════════════════════════════════════
with tab_profilo:
    try:
        from strumenti import mbti_ui as _mbti_ui
    except ImportError:
        _mbti_ui = None
    if _mbti_ui is not None:
        _mbti_ui.mostra(cfg, stats_dir, carica_punteggi_mbti, carica_storia_mbti_dash)
    else:
        st.info('Il profilo cognitivo (strumento MBTI) non è incluso in questa installazione.')



# ═══════════════════════════════════════════════════════════════════════
# TAB: LETTURE PRIME — documenti del metodo PRIME (Reading Brain) importati a
# mano: import, documenti in sospeso, stato di ogni lettura (interfaccia/prime_ui.py,
# logica in zeno_engine/prime.py, contratto in docs/PRIME.md).
# ═══════════════════════════════════════════════════════════════════════
if tab_prime is not None:
    with tab_prime:
        try:
            from interfaccia.prime_ui import mostra_letture_prime as _mostra_prime
            _mostra_prime()
        except Exception as _e:
            st.error(f'Letture PRIME non disponibili: {_e}')
