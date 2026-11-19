# valutazione.py — misurare le risposte di Zeno (richiesta dell'utente, 28/09/2026).
#
# Prima si misura, poi si decide se servono fine-tuning, DPO, reranker o un
# modello più grande. Tre strumenti, tutti locali, che a terminale stampano
# SOLO numeri (i testi restano nei file sotto stats/valutazione/):
#
#  1. Set di prova dell'utente — domande.yaml: domande con la risposta attesa
#     scritta da lui. `zeno valuta risposte` le pone a Zeno con lo stesso
#     prompt della chat (prompt_chat.py) e per ognuna dice se l'errore nasce
#     dal RECUPERO delle fonti o dalla GENERAZIONE.
#  2. Voti della chat — voti.jsonl: pollice su/giù e correzione facoltativa,
#     con il prompt esatto: la materia prima per un eventuale addestramento
#     sulle preferenze (DPO) quando saranno abbastanza.
#  3. Set sintetico per il solo recupero — sintetico.jsonl: il modello locale
#     scrive una domanda a partire da un frammento preso a caso, e quel
#     frammento è la risposta giusta. Serve a confrontare varianti del
#     recupero (reranker, asserzioni, chunk) senza aspettare le domande di
#     L'utente. Limite noto: domande scritte guardando il frammento gli
#     somigliano più di quelle vere, quindi i numeri assoluti sono ottimisti;
#     valgono i confronti fra varianti.

import json
import math
import os
import random
import re
import time
from datetime import datetime

import yaml

from zeno_engine import ask, indicizzatore, modelli
from zeno_engine import config as _config

MODELLO_DOMANDE = '''# Set di prova di Zeno: lo scrive l'utente e resta solo sul Mac.
#
# Ogni voce è una domanda che faresti davvero a Zeno, con la risposta che ti
# aspetti. Bastano 1-5 frasi: contano i punti chiave, non la forma.
#   tipo: fatto         cosa dice l'archivio su un tema, un libro, un autore
#         pensiero_mio  la tua posizione (deve venire dai tuoi scritti)
#         citazione     una citazione precisa che deve riportare
#         lacuna        l'archivio NON lo sa: la risposta giusta è "LACUNA"
#   fonti_attese: facoltativo, pezzi del nome file o del titolo che devono
#                 comparire fra le fonti; se ci sono si misura anche il recupero.
#
# Quante: almeno 30, meglio 50, di cui una decina di tipo lacuna. Con 30
# domande una differenza fra due modelli sotto i 15 punti percentuali non si
# distingue dal caso. Le voci con la domanda vuota vengono saltate.
domande:
  - id: d001
    tipo: fatto
    domanda: ""
    risposta_attesa: ""
    fonti_attese: []
  - id: d002
    tipo: pensiero_mio
    domanda: ""
    risposta_attesa: ""
    fonti_attese: []
  - id: d003
    tipo: lacuna
    domanda: ""
    risposta_attesa: "LACUNA"
    fonti_attese: []
'''


def cartella(cfg: dict | None = None) -> str:
    cfg = cfg or _config.load()
    stats = ((cfg.get('_globale') or {}).get('percorsi') or {}).get('stats') \
        or os.path.join(_config.BRAIN, 'stats')
    d = os.path.join(stats, 'valutazione')
    os.makedirs(os.path.join(d, 'esiti'), exist_ok=True)
    return d


# ── 1. Voti della chat ──────────────────────────────────────────────────────
def registra_voto(voce: dict, cfg: dict | None = None) -> None:
    """Una riga per voto. Un voto cambiato (o una correzione aggiunta dopo)
    è una riga nuova con lo stesso `id`: vale l'ultima."""
    voce = dict(voce)
    voce.setdefault('ts', datetime.now().isoformat(timespec='seconds'))
    with open(os.path.join(cartella(cfg), 'voti.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps(voce, ensure_ascii=False) + '\n')


def carica_voti(cfg: dict | None = None) -> list[dict]:
    p = os.path.join(cartella(cfg), 'voti.jsonl')
    ultimi: dict[str, dict] = {}
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            for riga in f:
                try:
                    v = json.loads(riga)
                except ValueError:
                    continue
                ultimi[v.get('id') or str(len(ultimi))] = v
    return [v for v in ultimi.values() if v.get('voto') is not None]


def riepilogo_voti(cfg: dict | None = None) -> dict:
    voti = carica_voti(cfg)
    su = sum(1 for v in voti if v['voto'] == 1)
    giu = [v for v in voti if v['voto'] == 0]
    return {'voti': len(voti), 'su': su, 'giu': len(giu),
            'giu_con_correzione': sum(1 for v in giu if (v.get('correzione') or '').strip()),
            'con_prompt': sum(1 for v in voti if v.get('prompt'))}


# ── 2. Set di prova dell'utente ─────────────────────────────────────────────
def prepara_domande(cfg: dict | None = None) -> str:
    p = os.path.join(cartella(cfg), 'domande.yaml')
    if not os.path.exists(p):
        with open(p, 'w', encoding='utf-8') as f:
            f.write(MODELLO_DOMANDE)
    return p


def carica_domande(cfg: dict | None = None) -> list[dict]:
    p = prepara_domande(cfg)
    with open(p, encoding='utf-8') as f:
        dati = yaml.safe_load(f) or {}
    return [d for d in (dati.get('domande') or [])
            if (d.get('domanda') or '').strip()]


TIPI_DOMANDA = ('fatto', 'pensiero_mio', 'citazione', 'lacuna')


def salva_domande(domande: list[dict], cfg: dict | None = None) -> str:
    """Riscrive domande.yaml: l'intestazione con le istruzioni resta, sotto
    le voci compilate (le vuote del modello iniziale si perdono, non servono).
    Scrittura atomica: un file a metà renderebbe illeggibile tutto il set."""
    p = os.path.join(cartella(cfg), 'domande.yaml')
    intestazione = MODELLO_DOMANDE.split('domande:')[0]
    corpo = yaml.safe_dump({'domande': domande}, allow_unicode=True, sort_keys=False,
                           width=100)
    tmp = p + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(intestazione + corpo)
    os.replace(tmp, p)
    return p


def aggiungi_domanda(domanda: str, risposta_attesa: str, tipo: str = 'fatto',
                     fonti_attese: list[str] | None = None,
                     cfg: dict | None = None) -> str:
    """Una voce in più nel set di prova (dalla dashboard o da una correzione
    in chat). Ritorna l'id assegnato, d001, d002..."""
    if tipo not in TIPI_DOMANDA:
        raise ValueError(f'tipo non valido: {tipo}')
    voci = carica_domande(cfg)
    numeri = [int(d['id'][1:]) for d in voci
              if str(d.get('id', '')).startswith('d') and str(d['id'])[1:].isdigit()]
    nuovo = f'd{(max(numeri) + 1) if numeri else 1:03d}'
    voci.append({'id': nuovo, 'tipo': tipo, 'domanda': domanda.strip(),
                 'risposta_attesa': (risposta_attesa or '').strip()
                 or ('LACUNA' if tipo == 'lacuna' else ''),
                 'fonti_attese': [f.strip() for f in (fonti_attese or []) if f.strip()]})
    salva_domande(voci, cfg)
    return nuovo


def modifica_risposta(id_domanda: str, risposta_attesa: str, cfg: dict | None = None) -> None:
    """L'utente ritocca la risposta attesa di una voce già nel set (dashboard)."""
    voci = carica_domande(cfg)
    for d in voci:
        if d.get('id') == id_domanda:
            d['risposta_attesa'] = (risposta_attesa or '').strip() or (
                'LACUNA' if d.get('tipo') == 'lacuna' else '')
            break
    else:
        raise KeyError(id_domanda)
    salva_domande(voci, cfg)


def rimuovi_domanda(id_domanda: str, cfg: dict | None = None) -> None:
    salva_domande([d for d in carica_domande(cfg) if d.get('id') != id_domanda], cfg)


SCHEMA_COPERTURA = {'type': 'object',
                    'properties': {'copertura': {'type': 'integer', 'enum': [0, 1, 2]},
                                   'contraddice': {'type': 'boolean'}},
                    'required': ['copertura', 'contraddice']}
SCHEMA_CONTESTO = {'type': 'object',
                   'properties': {'contesto': {'type': 'string',
                                               'enum': ['si', 'parziale', 'no']}},
                   'required': ['contesto']}
SCHEMA_FEDELTA = {'type': 'object',
                  'properties': {'fedelta': {'type': 'integer', 'enum': [0, 1, 2]}},
                  'required': ['fedelta']}

GIUDICE_COPERTURA = """Confronta la RISPOSTA di un assistente con la RISPOSTA ATTESA scritta dall'utente per la stessa DOMANDA. Giudica solo il contenuto, non lo stile né la lunghezza.
copertura: 2 se la risposta contiene tutti i punti chiave della risposta attesa, 1 se ne contiene una parte, 0 se nessuno.
contraddice: true se la risposta afferma qualcosa in contrasto con la risposta attesa.
Rispondi solo col JSON."""

GIUDICE_CONTESTO = """Ti mostro dei DOCUMENTI recuperati da un archivio e la RISPOSTA ATTESA a una DOMANDA.
I documenti contengono le informazioni necessarie per scrivere la risposta attesa?
contesto: "si" se tutte, "parziale" se solo una parte, "no" se nessuna. Rispondi solo col JSON."""

GIUDICE_FEDELTA = """Ti mostro dei DOCUMENTI e una RISPOSTA scritta usandoli.
fedelta: 2 se ogni affermazione della risposta è sostenuta dai documenti, 1 se qualche affermazione non lo è, 0 se la risposta è in gran parte non sostenuta o inventata.
Una dichiarazione di LACUNA (l'archivio non copre qualcosa) conta come sostenuta. Rispondi solo col JSON."""


def _giudica(modello: str, sistema: str, utente: str, schema: dict) -> dict:
    r = modelli.client().chat(
        model=modello,
        messages=[{'role': 'system', 'content': sistema},
                  {'role': 'user', 'content': utente}],
        format=schema, options={'temperature': 0.0, 'num_predict': 400},
        keep_alive='10m')
    return json.loads(r['message']['content'])


def _norm(t: str) -> str:
    return re.sub(r'\s+', ' ', (t or '').lower()).strip()


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - m), min(1.0, c + m))


def _coseno(a, b) -> float:
    import numpy as np
    a, b = np.asarray(a, dtype='float32'), np.asarray(b, dtype='float32')
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1.0))


def valuta_risposte(cfg: dict | None = None, modello: str | None = None,
                    giudice: str | None = None, etichetta: str = '',
                    prompt: int | None = None) -> dict:
    """Pone il set di prova a Zeno (prompt della chat) e giudica in locale.
    Fase 1: tutte le risposte col modello scelto. Fase 2: il giudice (di
    serie il motore_ask), dopo aver scaricato il modello delle risposte se è
    un altro: un solo modello grande in RAM alla volta."""
    from zeno_engine import prompt_chat
    from zeno_engine.adapter_v5 import EngineZeno
    cfg = cfg or _config.load()
    domande = carica_domande(cfg)
    if not domande:
        return {'domande': 0}
    modello = modello or cfg['modelli']['motore_ask']
    giudice = giudice or cfg['modelli']['motore_ask']
    modelli.assicura_demone()
    g = cfg['_globale']
    prompt = prompt or prompt_chat.VERSIONE_CHAT
    template = prompt_chat.template_archivio(g['identita']['nome_utente'],
                                             prompt_chat.profilo_str(g), prompt)
    motore = EngineZeno(template, cfg, modello=modello, decisione=(prompt == 3),
                        decidi_prima=(prompt == 4))

    esiti = []
    for d in domande:
        t0 = time.time()
        r = motore.query(d['domanda'])
        primo = None
        testo = ''
        for pezzo in r.response_gen:
            if primo is None:
                primo = time.time() - t0
            testo += pezzo
        fonti = [{'numero': n.metadata.get('numero'), 'path': n.metadata.get('file_path'),
                  'title': n.metadata.get('title'), 'strato': n.metadata.get('strato'),
                  'testo': n.testo} for n in r.source_nodes]
        esiti.append({'id': d.get('id'), 'tipo': d.get('tipo', 'fatto'),
                      'domanda': d['domanda'], 'attesa': d.get('risposta_attesa', ''),
                      'fonti_attese': d.get('fonti_attese') or [],
                      'risposta': testo, 'fonti': fonti, 'modello': modello,
                      'decisione': r.decisione,
                      'secondi_decisione': r.secondi_decisione,
                      'secondi': round(time.time() - t0, 2),
                      'primo_token': round(primo or 0, 2)})

    if modello != giudice:
        modelli.scarica(modello)
    for e in esiti:
        documenti = '\n\n'.join(f"[{f['numero']}] {f['title']}\n{f['testo']}" for f in e['fonti'])
        e['lacuna_dichiarata'] = 'LACUNA' in e['risposta'].upper()
        attese = [_norm(x) for x in e['fonti_attese']]
        e['fonti_attese_trovate'] = (
            sum(1 for a in attese if any(a in _norm(f['path']) or a in _norm(f['title'])
                                         for f in e['fonti'])) if attese else None)
        try:
            e.update(_giudica(giudice, GIUDICE_FEDELTA,
                              f"DOCUMENTI:\n{documenti}\n\nRISPOSTA:\n{e['risposta']}",
                              SCHEMA_FEDELTA))
            if e['tipo'] != 'lacuna':
                e.update(_giudica(giudice, GIUDICE_COPERTURA,
                                  f"DOMANDA: {e['domanda']}\n\nRISPOSTA ATTESA:\n{e['attesa']}"
                                  f"\n\nRISPOSTA:\n{e['risposta']}", SCHEMA_COPERTURA))
                e.update(_giudica(giudice, GIUDICE_CONTESTO,
                                  f"DOMANDA: {e['domanda']}\n\nRISPOSTA ATTESA:\n{e['attesa']}"
                                  f"\n\nDOCUMENTI:\n{documenti}", SCHEMA_CONTESTO))
        except Exception as err:                 # un giudizio mancato non ferma il resto
            e['errore_giudice'] = str(err)[:200]
        e['esito'] = _esito(e)

    # Similarità semantica risposta/attesa (bge-m3): misura senza giudice.
    modelli.scarica(giudice)
    coppie = [e for e in esiti if e['tipo'] != 'lacuna' and e['attesa']]
    if coppie:
        vett = modelli.client().embed(
            model=cfg['modelli']['embeddings'],
            input=[x for e in coppie for x in (e['risposta'], e['attesa'])])['embeddings']
        for i, e in enumerate(coppie):
            e['similarita'] = round(_coseno(vett[2 * i], vett[2 * i + 1]), 4)

    nome = f"{datetime.now():%Y%m%d_%H%M}_{etichetta or modello.split('/')[-1]}"
    percorso = os.path.join(cartella(cfg), 'esiti', f'{nome}.jsonl')
    with open(percorso, 'w', encoding='utf-8') as f:
        for e in esiti:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
    sintesi = riassumi(esiti)
    sintesi['file'] = os.path.basename(percorso)
    return sintesi


def _esito(e: dict) -> str:
    """Dove nasce l'errore. Ordine: lacuna → giusta → recupero → generazione."""
    if e['tipo'] == 'lacuna':
        return 'ok' if e['lacuna_dichiarata'] else 'inventata'
    if e.get('copertura') == 2 and not e.get('contraddice'):
        return 'ok'
    if e['fonti_attese'] and not e.get('fonti_attese_trovate'):
        return 'recupero'
    if e.get('contesto') == 'no':
        return 'recupero'
    return 'generazione'


def riassumi(esiti: list[dict]) -> dict:
    n = len(esiti)
    conta = {}
    for e in esiti:
        conta[e['esito']] = conta.get(e['esito'], 0) + 1
    ok = conta.get('ok', 0)
    lo, hi = _wilson(ok, n)
    non_lac = [e for e in esiti if e['tipo'] != 'lacuna']
    lac = [e for e in esiti if e['tipo'] == 'lacuna']

    def media(chiave, gruppo):
        v = [e[chiave] for e in gruppo if isinstance(e.get(chiave), (int, float))]
        return round(sum(v) / len(v), 3) if v else None

    tempi = sorted(e['secondi'] for e in esiti)
    con_attese = [e for e in esiti if e['fonti_attese']]
    return {
        'domande': n, 'esiti': conta,
        'ok_%': round(100 * ok / n, 1) if n else 0,
        'ok_%_intervallo95': [round(100 * lo, 1), round(100 * hi, 1)],
        'copertura_media_0_2': media('copertura', non_lac),
        'fedelta_media_0_2': media('fedelta', esiti),
        'similarita_media': media('similarita', non_lac),
        'lacune_riconosciute': f"{sum(e['lacuna_dichiarata'] for e in lac)}/{len(lac)}",
        'false_lacune': sum(1 for e in non_lac if e['lacuna_dichiarata']),
        'fonti_attese_trovate': (f"{sum(1 for e in con_attese if e['fonti_attese_trovate'])}"
                                 f"/{len(con_attese)}") if con_attese else None,
        'secondi_mediana': tempi[n // 2] if n else None,
        'primo_token_medio': media('primo_token', esiti),
    }


def confronta(file_a: str, file_b: str, cfg: dict | None = None) -> dict:
    """Confronto appaiato di due esiti sulle stesse domande: vittorie,
    sconfitte e pareggi sulla copertura, con test del segno."""
    def leggi(p):
        p = p if os.path.isabs(p) else os.path.join(cartella(cfg), 'esiti', p)
        with open(p, encoding='utf-8') as f:
            return {e['id']: e for e in map(json.loads, f)}
    a, b = leggi(file_a), leggi(file_b)
    vinte = perse = pari = 0
    for k in a.keys() & b.keys():
        pa = 2 if a[k]['esito'] == 'ok' else a[k].get('copertura', 0) or 0
        pb = 2 if b[k]['esito'] == 'ok' else b[k].get('copertura', 0) or 0
        if pa > pb:
            vinte += 1
        elif pb > pa:
            perse += 1
        else:
            pari += 1
    m = vinte + perse
    # p bilaterale del test del segno (binomiale 0,5)
    coda = sum(math.comb(m, i) for i in range(0, min(vinte, perse) + 1)) / (2 ** m) if m else 1.0
    return {'domande_comuni': len(a.keys() & b.keys()), 'A_meglio': vinte,
            'B_meglio': perse, 'pari': pari, 'p_segno': round(min(1.0, 2 * coda), 4)}


# ── 3. Set sintetico per il recupero ───────────────────────────────────────
PROMPT_SINTETICO = """Ti mostro un frammento dell'archivio personale di @UTENTE@. Scrivi UNA domanda in italiano che @UTENTE@ potrebbe fare al suo archivio e a cui questo frammento risponde.
Regole: usa parole tue, non copiare frasi dal frammento; non nominare "il frammento", "il testo" o "il documento"; al massimo 25 parole.
Se il frammento è un indice, una bibliografia, un elenco o non contiene nulla su cui fare una domanda sensata, lascia la domanda vuota. Rispondi solo col JSON."""
SCHEMA_SINTETICO = {'type': 'object', 'properties': {'domanda': {'type': 'string'}},
                    'required': ['domanda']}
QUOTE_STRATO = {'esplicito': 0.4, 'acquisito': 0.4, 'dialogico': 0.2}


def genera_sintetico(con, cfg: dict | None = None, n: int = 200, seme: int = 7) -> dict:
    """Domande sintetiche da frammenti estratti a caso per strato. Riprende da
    dove era arrivato: i frammenti già usati si saltano."""
    cfg = cfg or _config.load()
    p = os.path.join(cartella(cfg), 'sintetico.jsonl')
    fatti = set()
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            fatti = {json.loads(r)['chunk_id'] for r in f if r.strip()}
    rnd = random.Random(seme)
    scelti = []
    for strato, quota in QUOTE_STRATO.items():
        righe = con.execute(
            "SELECT c.id, c.source_id, c.text, s.title FROM chunks c "
            "JOIN sources s ON s.id = c.source_id WHERE s.strato = ? "
            "AND length(c.text) BETWEEN 300 AND 3000 "
            "AND lower(s.path) NOT LIKE '%/vivino/%' ORDER BY c.id", (strato,)).fetchall()
        k = min(len(righe), round(n * quota))
        scelti += [(strato, r) for r in rnd.sample(righe, k)]
    modelli.assicura_demone()
    modello = cfg['modelli']['router']
    modelli.prepara_solo(cfg, modello)
    nuove = vuote = errori = 0
    with open(p, 'a', encoding='utf-8') as f:
        for strato, (cid, sid, testo, titolo) in scelti:
            if cid in fatti:
                continue
            try:
                r = modelli.client().chat(
                    model=modello,
                    messages=[{'role': 'system', 'content': PROMPT_SINTETICO},
                              {'role': 'user', 'content': f'{titolo}\n{testo}'}],
                    format=SCHEMA_SINTETICO,
                    options={'temperature': 0.3, 'num_predict': 90}, keep_alive='10m')
                domanda = (json.loads(r['message']['content']).get('domanda') or '').strip()
            except Exception:
                errori += 1
                continue
            if len(domanda) < 12:
                vuote += 1
                continue
            f.write(json.dumps({'chunk_id': cid, 'source_id': sid, 'strato': strato,
                                'domanda': domanda}, ensure_ascii=False) + '\n')
            nuove += 1
    modelli.scarica(modello)
    return {'campione': len(scelti), 'nuove': nuove, 'scartate_vuote': vuote,
            'errori': errori, 'totale_nel_file': len(fatti) + nuove}


def carica_sintetico(cfg: dict | None = None) -> list[dict]:
    p = os.path.join(cartella(cfg), 'sintetico.jsonl')
    if not os.path.exists(p):
        return []
    with open(p, encoding='utf-8') as f:
        return [json.loads(r) for r in f if r.strip()]


def recupero_attuale(con, domanda: str, cfg: dict, k: int = 10) -> list[dict]:
    """Il recupero della chat (adapter_v5.EngineZeno._recupera), allungato a
    k: i primi 5 coincidono con quelli che la chat passa al modello."""
    from zeno_engine.adapter_v5 import query_enologica
    personale = any(x in domanda.lower() for x in ask.KEYWORDS_PERSONALE)
    fr = ask.recupera(con, domanda, cfg, personale=personale, top_k=k)
    if not query_enologica(domanda):
        fr = [f for f in fr if '/vivino/' not in f['path'].lower()]
    return fr


def valuta_recupero(con, cfg: dict | None = None, varianti: dict | None = None,
                    limite: int | None = None) -> dict:
    """Per ogni variante (nome → funzione(con, domanda, cfg, k) → frammenti
    ordinati): la fonte giusta fra le prime 1/5/10, il frammento giusto fra i
    primi 5, e l'MRR@10 sulla fonte. I vettori delle domande si calcolano una
    volta sola per tutte le varianti."""
    cfg = cfg or _config.load()
    varianti = varianti or {'attuale': recupero_attuale}
    domande = carica_sintetico(cfg)[:limite]
    if not domande:
        return {}
    modelli.assicura_demone()
    modelli.prepara_solo(cfg, cfg['modelli']['embeddings'])
    originale = indicizzatore.embed_query
    memo: dict[str, bytes] = {}

    def embed_memo(testo, c):
        if testo not in memo:
            memo[testo] = originale(testo, c)
        return memo[testo]
    indicizzatore.embed_query = embed_memo
    # Vettori di tutte le domande in blocchi, prima di cronometrare le varianti.
    testi = [d['domanda'] for d in domande]
    for i in range(0, len(testi), 32):
        blocco = testi[i:i + 32]
        vv = modelli.client().embed(model=cfg['modelli']['embeddings'], input=blocco,
                                    keep_alive='10m')['embeddings']
        memo.update({t: indicizzatore._f32(v) for t, v in zip(blocco, vv)})
    path_di = dict(con.execute('SELECT id, path FROM sources'))
    risultati = {}
    try:
        for nome, fn in varianti.items():
            h1 = h5 = h10 = c5 = 0
            rr = 0.0
            per_strato: dict[str, list[int]] = {}
            t0 = time.time()
            for d in domande:
                fr = fn(con, d['domanda'], cfg, 10)
                giusta = path_di.get(d['source_id'])
                ranghi = [i for i, f in enumerate(fr[:10], 1) if f['path'] == giusta]
                r = ranghi[0] if ranghi else None
                h1 += bool(r and r <= 1)
                h5 += bool(r and r <= 5)
                h10 += bool(r)
                rr += 1 / r if r else 0
                c5 += any(f['chunk_id'] == d['chunk_id'] for f in fr[:5])
                per_strato.setdefault(d['strato'], []).append(int(bool(r and r <= 5)))
            n = len(domande)
            risultati[nome] = {
                'fonte@1': round(h1 / n, 3), 'fonte@5': round(h5 / n, 3),
                'fonte@10': round(h10 / n, 3), 'frammento@5': round(c5 / n, 3),
                'mrr@10': round(rr / n, 3),
                'fonte@5_per_strato': {s: round(sum(v) / len(v), 3)
                                       for s, v in sorted(per_strato.items())},
                'ms_per_domanda': round(1000 * (time.time() - t0) / n, 1)}
    finally:
        indicizzatore.embed_query = originale
    risultati['_domande'] = len(domande)
    return risultati
