# grafo3d.py — vista 3D UNICA del grafo di Zeno stile Obsidian (richiesta l'utente,
# checkpoint 2026-07-14: via il tab 2D "Grafo cognitivo", un solo grafo 3D chiaro).
#
# Legge nodi e archi da zeno.db e li renderizza con 3d-force-graph
# VENDORIZZATO in scripts/interfaccia/assets/ (nessun CDN: funziona offline, ZERO CLOUD).
# È un componente della dashboard V6: si innesta come tab, non tocca il resto.
#
# Nodi mostrati (MAI asserzioni grezze — feedback esplicito dell'utente):
#   tema      = i 10 argomenti di primo livello (oro/arancio, grandi)
#   documento = fonti .md dell'archivio con almeno un'asserzione tematizzata
#               (le più ricche, fino a MAX_DOCUMENTI), azzurre
#   entità    = persona/org/concept/place/work citate ≥ MIN_MENZIONI volte
#               (colore per kind) — contesto aggiuntivo, non il fulcro
# Archi: tema→documento (dove il documento ha più asserzioni), tema→entità
#        (dove l'entità è più citata), co-menzioni entità-entità (stesse
#        asserzioni, ≥2 volte).
#
# "Nodi nuovi": zeno.db non ha created_at su topics/entities, quindi si usa
# la MIGLIORE PROXY disponibile — sources.ingested_at (data di ingestione del
# documento). Documenti: la propria ingested_at. Entità: la ingested_at più
# vecchia fra le fonti che le menzionano (prima comparsa nell'archivio). Temi:
# non hanno proxy temporale sensata (i 10 semi sono fissi da sempre) — un
# tema è "nuovo" solo se non fa parte del seed originale (TOPICS_SEED).
#
# File .md al click: i temi e le entità hanno una nota derivata nel vault
# Obsidian (obsidian_vault/Zeno/Temi|Entità/<nome>.md, generata da
# `zeno_engine export-vault`); i documenti sono già essi stessi un file .md
# nel corpus (documents/...), NON nel vault — il pannello lo dichiara.

import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
JS_LIB = os.path.join(ASSETS, '3d-force-graph.min.js')

# zeno_engine (per TOPICS_SEED e la convenzione nomi-file del vault) è un
# sottopacchetto di scripts/: garantiamo che sia importabile anche se questo
# modulo viene eseguito/importato da una cwd diversa da dashboard.py.
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
from zeno_engine.db import TOPICS_SEED          # noqa: E402
from zeno_engine.vault import VAULT_ZENO, _nome_file  # noqa: E402
from zeno_engine.config import BRAIN

MIN_MENZIONI = 4
MAX_ENTITA = 350
MAX_DOCUMENTI = 250
GIORNI_NUOVI_DEFAULT = 7

COLORI_KIND = {
    'topic': '#f6b26b',       # tema — oro/arancio, radice del pensiero
    'document': '#4fc3f7',    # documento .md dell'archivio — azzurro
    'person': '#e06666', 'org': '#6fa8dc', 'concept': '#93c47d',
    'place': '#ffd966', 'work': '#c27ba0',
}
ETICHETTE_KIND = {
    'person': 'Persona', 'org': 'Organizzazione', 'concept': 'Concetto',
    'place': 'Luogo', 'work': 'Opera',
}
# Palette categorica per le community Louvain (leggibile su sfondo scuro).
# Fino a 25 community (vedi indicizzatore.rileva_community max_community=25).
PALETTE_COMMUNITY = [
    '#e6194B', '#3cb44b', '#ffe119', '#4363d8', '#f58231', '#911eb4',
    '#46f0f0', '#f032e6', '#bcf60c', '#fabebe', '#008080', '#e6beff',
    '#9A6324', '#fffac8', '#aaffc3', '#808000', '#ffd8b1', '#4dd0e1',
    '#ff8a65', '#ba68c8', '#a1887f', '#90a4ae', '#dce775', '#f06292',
    '#7986cb',
]


def _percorso_relativo(path: str) -> str:
    try:
        return os.path.relpath(path, BRAIN)
    except Exception:
        return path


def _nota_vault(cartella: str, nome: str) -> str | None:
    """Percorso (relativo al vault) della nota derivata, se già esportata
    con `zeno_engine export-vault`. None se non ancora generata."""
    assoluto = os.path.join(VAULT_ZENO, cartella, _nome_file(nome) + '.md')
    if os.path.exists(assoluto):
        return f'Zeno/{cartella}/{_nome_file(nome)}.md'
    return None


def dati_grafo(db_path: str, giorni_nuovi: int = GIORNI_NUOVI_DEFAULT) -> dict:
    con = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
    nodi, archi = [], []
    soglia_nuovo = datetime.now() - timedelta(days=giorni_nuovi)
    seed_temi = set(TOPICS_SEED)

    def _e_nuovo(ts: str | None) -> bool:
        if not ts:
            return False
        try:
            return datetime.fromisoformat(ts.split('.')[0]) >= soglia_nuovo
        except Exception:
            return False

    # Mappa topic_id → tema di PRIMO LIVELLO (radice dell'albero dei temi).
    # Il grafo disegna un nodo solo per i 10 temi radice, ma `assertions.topic_id`
    # può puntare a un SOTTO-tema (2.308 asserzioni al 2026-07-28, i sotto-temi
    # popolati quando la conferma spostava il tema primario). Senza questa
    # risoluzione si generano archi verso nodi inesistenti (`t12`, `t15`…) e in
    # d3-force UN SOLO arco con un estremo mancante solleva "missing: <id>" e
    # INTERROMPE il rendering: il sintomo era un grafo con un unico nodo visibile.
    _parent = dict(con.execute('SELECT id, parent_id FROM topics'))

    def _radice(tid):
        visti = set()
        while tid is not None and _parent.get(tid) is not None and tid not in visti:
            visti.add(tid)
            tid = _parent[tid]
        return tid

    radice_topic = {tid: _radice(tid) for tid in _parent}

    # ── Temi (argomenti di primo livello) ──────────────────────────────
    temi = con.execute("""SELECT t.id, t.name, COUNT(at.assertion_id) FROM topics t
        LEFT JOIN assertion_topics at ON at.topic_id = t.id
        WHERE t.parent_id IS NULL GROUP BY t.id""").fetchall()
    # descrizione scritta dall'utente (tassonomia.py) e figli, nel pannello dettagli
    descr = {tid: d or '' for tid, d in con.execute('SELECT id, descrizione FROM topics')}
    figli_di: dict[int, list[str]] = {}
    for n, p in con.execute('SELECT name, parent_id FROM topics WHERE parent_id IS NOT NULL ORDER BY name'):
        figli_di.setdefault(p, []).append(n)
    for tid, nome, n in temi:
        nuovo = nome not in seed_temi
        file_vault = _nota_vault('Temi', nome)
        nodi.append({
            'id': f't{tid}', 'group': 'topic', 'val': max(6, min(n // 40, 30)),
            'nuovo': nuovo, 'label': nome, 'tipoLabel': 'Tema',
            'file': file_vault, 'nAsserzioni': n,
            'extra': (descr.get(tid) or 'Argomento di primo livello dell\'archivio.')
                     + (f" Figli: {', '.join(figli_di[tid])}." if tid in figli_di else ''),
            'name': f"{'🆕 ' if nuovo else ''}{nome} · Tema ({n} asserzioni)",
        })

    # ── Documenti (fonti .md con almeno un'asserzione tematizzata) ─────
    righe_doc = con.execute("""
        SELECT s.id, s.path, s.title, s.strato, s.area, s.type, s.created_at,
               s.ingested_at, a.topic_id, COUNT(*) AS n
        FROM sources s
        JOIN chunks c ON c.source_id = s.id
        JOIN assertions a ON a.chunk_id = c.id
        GROUP BY s.id, a.topic_id""").fetchall()

    per_doc: dict[int, dict] = {}
    for sid, path, title, strato, area, tipo, creato, ingerito, topic_id, n in righe_doc:
        d = per_doc.setdefault(sid, {
            'path': path, 'title': title, 'strato': strato, 'area': area,
            'tipo': tipo, 'creato': creato, 'ingerito': ingerito,
            'totale': 0, 'per_topic': {},
        })
        d['totale'] += n
        if topic_id is not None:
            r = radice_topic.get(topic_id, topic_id)   # sotto-tema → tema radice
            d['per_topic'][r] = d['per_topic'].get(r, 0) + n

    # Solo documenti con almeno un'asserzione tematizzata (serve l'arco
    # tema→documento); i più ricchi in cima, tagliati a MAX_DOCUMENTI.
    doc_con_tema = [(sid, d) for sid, d in per_doc.items() if d['per_topic']]
    doc_con_tema.sort(key=lambda x: -x[1]['totale'])
    doc_con_tema = doc_con_tema[:MAX_DOCUMENTI]

    ids_documenti = set()
    for sid, d in doc_con_tema:
        ids_documenti.add(sid)
        tema_dominante, n_dominante = max(d['per_topic'].items(), key=lambda kv: kv[1])
        nome_doc = d['title'] or os.path.basename(d['path'])
        nuovo = _e_nuovo(d['ingerito'])
        nodi.append({
            'id': f'd{sid}', 'group': 'document',
            'val': max(2, min(d['totale'] // 3, 16)),
            'nuovo': nuovo, 'label': nome_doc, 'tipoLabel': 'Documento',
            'file': _percorso_relativo(d['path']), 'nAsserzioni': d['totale'],
            'extra': (f"Tipo: {d['tipo'] or '?'} · Strato: {d['strato'] or '?'} · "
                      f"Area: {d['area'] or '?'} · Data contenuto: {d['creato'] or '?'}"),
            'name': f"{'🆕 ' if nuovo else ''}{nome_doc} · Documento ({d['totale']} asserzioni)",
        })
        archi.append({'source': f't{tema_dominante}', 'target': f'd{sid}',
                      'value': n_dominante, 'tipo': 'appartenenza'})

    # ── Entità (persona/org/concept/place/work) ────────────────────────
    # Gli alias confermati (entities.canonical_id) vengono FUSI nel nome
    # canonico: la menzione di "Kafka" conta per "Franz Kafka". Finché nessun
    # alias è confermato (canonical_id NULL) COALESCE è un no-op e il grafo è
    # identico. `canon` risolve un qualsiasi id entità al suo canonico.
    canon = dict(con.execute("SELECT id, COALESCE(canonical_id, id) FROM entities"))
    # community Louvain per entità canonica (colonna scritta da indicizza)
    entity_comm = dict(con.execute(
        "SELECT id, community FROM entities WHERE community IS NOT NULL"))

    entita = con.execute("""
        SELECT c.id, c.name, c.kind, COUNT(*) AS menzioni
        FROM edges e
        JOIN entities al ON al.id = e.dst_id
        JOIN entities c  ON c.id = COALESCE(al.canonical_id, al.id)
        WHERE e.dst_type='entity' AND e.relation='mentions'
        GROUP BY c.id HAVING menzioni >= ? ORDER BY menzioni DESC LIMIT ?""",
        (MIN_MENZIONI, MAX_ENTITA)).fetchall()
    ids_entita = set()

    # prima comparsa: risolta al canonico (min tra alias e canonico)
    prima_comparsa = {}
    for dst_id, ts in con.execute("""
            SELECT e.dst_id, MIN(s.ingested_at) FROM edges e
            JOIN assertions a ON a.id = e.src_id AND e.src_type = 'assertion'
            JOIN chunks c ON c.id = a.chunk_id JOIN sources s ON s.id = c.source_id
            WHERE e.relation = 'mentions' AND e.dst_type = 'entity'
            GROUP BY e.dst_id"""):
        cid = canon.get(dst_id, dst_id)
        if ts and (cid not in prima_comparsa or ts < prima_comparsa[cid]):
            prima_comparsa[cid] = ts

    for eid, nome, kind, menzioni in entita:
        ids_entita.add(eid)
        nuovo = _e_nuovo(prima_comparsa.get(eid))
        tipo_lbl = ETICHETTE_KIND.get(kind, 'Entità')
        file_vault = _nota_vault('Entità', nome)
        comm = entity_comm.get(eid)
        nodi.append({
            'id': f'e{eid}', 'group': kind or 'concept',
            'val': max(2, min(menzioni // 2, 14)),
            'nuovo': nuovo, 'label': nome, 'tipoLabel': tipo_lbl,
            'file': file_vault, 'nAsserzioni': menzioni,
            'community': comm,
            'extra': f'Tipo: {tipo_lbl}.',
            'name': f"{'🆕 ' if nuovo else ''}{nome} · {tipo_lbl} ({menzioni} asserzioni)",
        })

    # tema → entità: il topic dove l'entità (canonico, alias inclusi) è più citata
    for eid, _, _, _ in entita:
        righe = con.execute("""
            SELECT a.topic_id, COUNT(*) FROM edges e
            JOIN entities al ON al.id = e.dst_id
            JOIN assertions a ON a.id = e.src_id
            WHERE e.relation='mentions' AND e.dst_type='entity'
              AND COALESCE(al.canonical_id, al.id)=? AND a.topic_id IS NOT NULL
            GROUP BY a.topic_id""", (eid,)).fetchall()
        if righe:
            # si aggrega PRIMA sulla radice, poi si prende il tema dominante:
            # altrimenti un tema spezzato fra madre e sotto-temi perderebbe
            # il confronto con un tema meno rappresentato ma non ramificato
            per_radice: dict = {}
            for tid, n in righe:
                r = radice_topic.get(tid, tid)
                per_radice[r] = per_radice.get(r, 0) + n
            radice, n_top = max(per_radice.items(), key=lambda kv: kv[1])
            archi.append({'source': f't{radice}', 'target': f'e{eid}',
                          'value': n_top, 'tipo': 'tematica'})

    # co-menzioni entità-entità (stessa asserzione), risolte al canonico e
    # ri-aggregate (due alias della stessa persona non fanno un arco su se stessa)
    comenzioni: dict = {}
    for e1, e2, n in con.execute("""
            SELECT m1.dst_id, m2.dst_id, COUNT(*) FROM edges m1
            JOIN edges m2 ON m2.src_id = m1.src_id AND m2.src_type='assertion'
                 AND m2.relation='mentions' AND m2.dst_id > m1.dst_id
            WHERE m1.src_type='assertion' AND m1.relation='mentions'
            GROUP BY m1.dst_id, m2.dst_id HAVING COUNT(*) >= 2 LIMIT 3000"""):
        c1, c2 = canon.get(e1, e1), canon.get(e2, e2)
        if c1 == c2 or c1 not in ids_entita or c2 not in ids_entita:
            continue
        chiave = (c1, c2) if c1 < c2 else (c2, c1)
        comenzioni[chiave] = comenzioni.get(chiave, 0) + n
    for (c1, c2), n in comenzioni.items():
        archi.append({'source': f'e{c1}', 'target': f'e{c2}', 'value': n,
                      'tipo': 'co-menzione'})

    # ── Riepilogo community: etichetta = le 3 entità più citate a grafo ──
    membri_comm: dict = {}
    for n in nodi:
        c = n.get('community')
        if c is not None:
            membri_comm.setdefault(c, []).append((n['nAsserzioni'], n['label']))
    communities = []
    for c, membri in membri_comm.items():
        membri.sort(reverse=True)
        etichetta = ', '.join(lbl for _, lbl in membri[:3])
        communities.append({'id': c, 'label': etichetta, 'nodi': len(membri)})
    communities.sort(key=lambda x: -x['nodi'])

    # ── Guardia finale: mai archi verso nodi inesistenti ────────────────
    # d3-force (dentro 3d-force-graph) solleva "missing: <id>" al primo arco
    # con un estremo non presente fra i nodi e ABORTISCE tutto il rendering.
    # Un arco perso è invisibile; un grafo che non si disegna no.
    ids_nodi = {n['id'] for n in nodi}
    archi = [a for a in archi
             if a['source'] in ids_nodi and a['target'] in ids_nodi]

    con.close()
    return {'nodes': nodi, 'links': archi, 'communities': communities}


def conta_per_tipo(dati: dict) -> dict:
    """{tipoLabel: n} — usato per la verifica funzionale e riutilizzabile
    da dashboard.py se serve un riepilogo testuale."""
    conteggio: dict[str, int] = {}
    for n in dati['nodes']:
        conteggio[n['tipoLabel']] = conteggio.get(n['tipoLabel'], 0) + 1
    return conteggio


def html_grafo(db_path: str, altezza: int = 640,
                giorni_nuovi: int = GIORNI_NUOVI_DEFAULT) -> str | None:
    """HTML autosufficiente (JS inline, zero CDN). None se la libreria non è
    vendorizzata in scripts/interfaccia/assets/."""
    if not os.path.exists(JS_LIB):
        return None
    with open(JS_LIB, 'r', encoding='utf-8') as f:
        js = f.read()
    import json
    dati = json.dumps(dati_grafo(db_path, giorni_nuovi), ensure_ascii=False)
    colori = json.dumps(COLORI_KIND)
    palette_comm = json.dumps(PALETTE_COMMUNITY)
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body{{margin:0;background:#0e1117;overflow:hidden;
      font-family:-apple-system,Helvetica,Arial,sans-serif}}
/* Le sovrapposizioni stanno in #wrap, MAI dentro #g: 3d-force-graph SVUOTA
   il proprio contenitore all'init e cancellerebbe legenda, pannello dettagli
   e i pulsanti "Colora per" (bug reale: i comandi delle community erano
   invisibili, non solo poco chiari). */
#wrap{{position:relative}}
.overlay{{position:absolute;z-index:10;background:rgba(20,26,38,0.88);
  color:#D6E4F0;border:1px solid #2E75B6;border-radius:8px;padding:10px 12px;
  font-size:12px;line-height:1.5;max-width:270px;box-shadow:0 2px 10px rgba(0,0,0,.4)}}
#legenda{{top:10px;left:10px}}
#legenda b{{color:#A8C8E8;font-size:13px}}
#legenda .riga{{margin-top:4px}}
#legenda .pallino{{display:inline-block;width:10px;height:10px;border-radius:50%;
  margin-right:6px}}
#legenda .lineetta{{display:inline-block;width:16px;height:3px;border-radius:2px;
  margin-right:6px;vertical-align:middle}}
#legenda hr{{border:none;border-top:1px solid #2E75B6;margin:8px 0}}
#pannello{{top:10px;right:10px;min-width:220px;max-width:300px}}
#pannello-chiudi{{position:absolute;top:6px;right:8px;cursor:pointer;
  color:#A8C8E8;background:none;border:none;font-size:15px}}
#pannello-titolo{{margin:0 18px 4px 0;font-size:14px;color:#D6E4F0}}
#pannello-riga{{margin-top:3px}}
#pannello-vuoto{{color:#8AA6C2;font-style:italic}}
.switch{{background:#1b2436;color:#A8C8E8;border:1px solid #2E75B6;border-radius:5px;
  padding:2px 8px;font-size:11px;cursor:pointer;margin-right:4px}}
.switch.attivo{{background:#2E75B6;color:#fff}}
#legenda-comm .riga-c{{margin-top:3px;font-size:11px}}
#legenda-comm .pallino{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}}
</style></head>
<body><div id="wrap">
  <div id="g"></div>
  <div class="overlay" id="legenda">
    <b>Legenda</b>
    <div class="riga" style="color:#A8C8E8;font-size:11px;margin-top:2px">NODI (colore = tipo di elemento)</div>
    <div class="riga"><span class="pallino" style="background:#f6b26b"></span>Tema (uno dei 10 argomenti)
      &nbsp;<span class="pallino" style="background:#4fc3f7"></span>Documento (.md)</div>
    <div class="riga"><span class="pallino" style="background:#e06666"></span>Persona
      &nbsp;<span class="pallino" style="background:#6fa8dc"></span>Organizzazione
      &nbsp;<span class="pallino" style="background:#93c47d"></span>Concetto</div>
    <div class="riga"><span class="pallino" style="background:#ffd966"></span>Luogo
      &nbsp;<span class="pallino" style="background:#c27ba0"></span>Opera</div>
    <div class="riga" style="color:#A8C8E8;font-size:11px;margin-top:5px">ARCHI (colore = tipo di legame)</div>
    <div class="riga"><span class="lineetta" style="background:#5b7fb5"></span>Appartenenza — il documento tratta il tema</div>
    <div class="riga"><span class="lineetta" style="background:#c79a4e"></span>Tematica — l'entità è trattata soprattutto in quel tema</div>
    <div class="riga"><span class="lineetta" style="background:#6aa06a"></span>Co-menzione — due entità citate insieme</div>
    <div class="riga" style="color:#8AA6C2;font-size:11px">Linea più spessa = legame più forte (più asserzioni).</div>
    <div class="riga" style="margin-top:5px">✨ nodo più chiaro/grande = aggiunto di recente (vedi barra sopra il grafo)</div>
    <hr>
    <div style="font-size:11px">Ogni nodo è un tema, un documento o un'entità —
    mai una singola asserzione. Clicca un nodo per i dettagli.
    Trascina per ruotare, rotella per zoom.</div>
    <hr>
    <div class="riga"><b>Colora i nodi per:</b>
      <button id="btn-tipo" class="switch attivo" onclick="setModo('tipo')">Tipo</button>
      <button id="btn-comm" class="switch" onclick="setModo('community')">Community</button>
    </div>
    <div class="riga" style="color:#8AA6C2;font-size:11px">Le community sono i
    cluster che emergono da chi compare insieme nelle tue asserzioni —
    trasversali ai 10 temi fissi (i colori cambiano solo i nodi entità).</div>
  </div>
  <!-- in BASSO A DESTRA: a sinistra si sovrapporrebbe alla legenda principale,
       che è alta e arriva in fondo (verificato dal vivo in browser). -->
  <div class="overlay" id="legenda-comm" style="display:none;bottom:10px;right:10px;max-height:52%;overflow:auto">
    <b>Community emerse</b>
    <div id="legenda-comm-corpo"></div>
  </div>
  <div class="overlay" id="pannello">
    <div id="pannello-vuoto">Clicca un nodo per vedere i dettagli.</div>
    <div id="pannello-pieno" style="display:none">
      <button id="pannello-chiudi" onclick="document.getElementById('pannello-pieno').style.display='none';document.getElementById('pannello-vuoto').style.display='block';">×</button>
      <h3 id="pannello-titolo"></h3>
      <div id="pannello-corpo"></div>
    </div>
  </div>
</div>
<script>{js}</script>
<script>
const dati = {dati};
const colori = {colori};
const paletteComm = {palette_comm};
const COLORI_ARCO = {{'appartenenza':'#5b7fb5','tematica':'#c79a4e','co-menzione':'#6aa06a'}};
let modoColore = 'tipo';

function chiarisci(hex, amt) {{
    const c = hex.replace('#', '');
    const num = parseInt(c, 16);
    let r = (num >> 16) + amt, g = ((num >> 8) & 0xff) + amt, b = (num & 0xff) + amt;
    r = Math.min(255, Math.max(0, r)); g = Math.min(255, Math.max(0, g)); b = Math.min(255, Math.max(0, b));
    return '#' + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}}
function coloreNodo(n) {{
    if (modoColore === 'community') {{
        // solo le entità hanno community; il resto (temi/documenti) è attenuato
        if (n.community === null || n.community === undefined) return '#3a4048';
        return paletteComm[n.community % paletteComm.length];
    }}
    const base = colori[n.group] || '#93c47d';
    return n.nuovo ? chiarisci(base, 90) : base;
}}

function setModo(m) {{
    modoColore = m;
    document.getElementById('btn-tipo').classList.toggle('attivo', m === 'tipo');
    document.getElementById('btn-comm').classList.toggle('attivo', m === 'community');
    document.getElementById('legenda-comm').style.display = (m === 'community') ? 'block' : 'none';
    G.nodeColor(coloreNodo);
}}

// legenda community (costruita dai dati)
(function() {{
    const corpo = document.getElementById('legenda-comm-corpo');
    (dati.communities || []).forEach(c => {{
        const col = paletteComm[c.id % paletteComm.length];
        const riga = document.createElement('div');
        riga.className = 'riga-c';
        riga.innerHTML = '<span class="pallino" style="background:' + col + '"></span>' +
            c.label + ' <span style="color:#8AA6C2">(' + c.nodi + ')</span>';
        corpo.appendChild(riga);
    }});
}})();

const G = ForceGraph3D()(document.getElementById('g'))
  .graphData(dati)
  .backgroundColor('#0e1117')
  .nodeLabel(n => n.name)
  .nodeVal(n => n.nuovo ? n.val * 1.35 : n.val)
  .nodeColor(coloreNodo)
  .linkOpacity(0.32)
  .linkColor(l => COLORI_ARCO[l.tipo] || '#3a4048')
  .linkWidth(l => Math.min(l.value * 0.3, 2.5))
  .nodeOpacity(0.92)
  .onNodeClick(n => {{
      const d = 60;
      G.cameraPosition({{x: n.x*1.4, y: n.y*1.4, z: n.z*1.4 + d}}, n, 800);
      document.getElementById('pannello-titolo').textContent =
          (n.nuovo ? '🆕 ' : '') + n.label;
      const fileRiga = n.file
          ? `<div id="pannello-riga">📄 <code>${{n.file}}</code></div>`
          : `<div id="pannello-riga" style="color:#8AA6C2">📄 nessun file .md derivato ancora esportato</div>`;
      const comm = (dati.communities || []).find(c => c.id === n.community);
      const commRiga = comm
          ? `<div id="pannello-riga"><b>Community:</b> ${{comm.label}}</div>`
          : '';
      document.getElementById('pannello-corpo').innerHTML =
          `<div id="pannello-riga"><b>Tipo:</b> ${{n.tipoLabel}}</div>` +
          `<div id="pannello-riga"><b>Asserzioni collegate:</b> ${{n.nAsserzioni}}</div>` +
          commRiga +
          fileRiga +
          `<div id="pannello-riga" style="margin-top:6px;color:#A8C8E8">${{n.extra || ''}}</div>`;
      document.getElementById('pannello-vuoto').style.display = 'none';
      document.getElementById('pannello-pieno').style.display = 'block';
  }});
// Il canvas nasce 0x0 (la lib misura il contenitore prima del layout) e i
// setter .width/.height incatenati alla costruzione vengono persi. La
// ricetta che funziona — verificata dal vivo in browser il 2026-07-14 —
// è chiamare i setter DOPO l'inizializzazione, a layout avvenuto; doppio
// colpo (0 e 400 ms) per coprire i tempi di init della scena.
function _dimensiona() {{
    G.width(document.body.clientWidth || window.innerWidth).height({altezza});
}}
setTimeout(_dimensiona, 0);
setTimeout(_dimensiona, 400);
window.addEventListener('resize', _dimensiona);
</script></body></html>"""
