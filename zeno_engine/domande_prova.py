# domande_prova.py — Zeno propone le domande del set di prova, l'utente risponde
# (richiesta dell'utente, 29/09/2026: «vedi tu di crearle, la maieutica per
# questo esiste, io vi rispondo; se non mi piace la domanda devo poterlo
# segnalare»).
#
# Il modello locale legge frammenti dell'archivio presi a caso e scrive una
# domanda per ciascuno; l'utente, dalla dashboard (scheda "Set di prova"),
# scrive la risposta che si aspetta oppure segnala la domanda. Una domanda con
# risposta entra in domande.yaml, e la fonte da cui è nata diventa la fonte
# attesa: così si misura anche il recupero.
#
# Tipi e da dove nascono:
#   pensiero_mio  un frammento dei suoi scritti (riflessioni, recensioni,
#                 pubblicazioni; mai i romanzi, dove parla il narratore)
#   fatto         un libro o articolo che ha LETTO (saggistica)
#   citazione     un file "Citazione da …" che ha trascritto lui
#   lacuna        (a) un libro della sua biblioteca mai letto: la risposta
#                 giusta è che non c'è un suo giudizio; (b) un tema della sua
#                 tassonomia con pochissimi documenti: domande concrete su cui
#                 l'archivio, verificato col recupero, non ha niente.
#
# Tutto locale. A terminale solo numeri; i testi restano in
# stats/valutazione/proposte.jsonl (una riga per evento, vale l'ultima per id).

import json
import os
import random
from datetime import datetime

from zeno_engine import generi, indicizzatore, modelli, valutazione
from zeno_engine import config as _config

QUOTE = {'pensiero_mio': 0.35, 'fatto': 0.30, 'citazione': 0.10, 'lacuna': 0.25}
MOTIVI_SCARTO = ('poco chiara', 'banale o generica', 'non la farei mai', 'sbagliata',
                 'non ricordo la risposta', 'altro')
# Oltre questa similarità col frammento più vicino, l'archivio qualcosa ce l'ha:
# la domanda non è una vera lacuna. Tarata il 30/09/2026 sulle 200 domande
# sintetiche, che una risposta ce l'hanno: coseno col frammento più vicino
# mediana 0,59, 10° percentile 0,52, sotto 0,48 solo l'1%. A 0,48 tutte le 24
# candidate sui temi poveri erano state scartate; a 0,52 passa circa una
# domanda con risposta su dieci, e la lacuna la conferma comunque l'utente.
SOGLIA_LACUNA = 0.52

SCHEMA = {'type': 'object', 'properties': {'domanda': {'type': 'string'}},
          'required': ['domanda']}
SCHEMA_DUE = {'type': 'object',
              'properties': {'domande': {'type': 'array', 'items': {'type': 'string'},
                                         'minItems': 1, 'maxItems': 2}},
              'required': ['domande']}

REGOLE = ("Regole: usa parole tue e non copiare frasi dal testo; niente \"il frammento\", "
          "\"il testo\", \"il documento\"; al massimo 25 parole; una domanda precisa, che "
          "abbia una risposta, non generica. Rispondi solo col JSON.")

PROMPT = {
    'pensiero_mio': (
        "Ti mostro un brano scritto da @UTENTE@. Scrivi UNA domanda che @UTENTE@ potrebbe "
        "fare al suo archivio per ritrovare la SUA idea o posizione espressa qui, in prima "
        "persona (per esempio «Che cosa penso di…», «Come ho descritto…», «Qual è la mia "
        "posizione su…»). Se il brano non esprime un'idea sua (elenco, appunto tecnico, "
        "dati), lascia la domanda vuota. " + REGOLE),
    'fatto': (
        "Ti mostro un brano di un libro o di un articolo che @UTENTE@ ha letto. Scrivi UNA "
        "domanda che @UTENTE@ farebbe al suo archivio per ritrovare un'idea o un fatto "
        "importante di questo testo; puoi nominare l'autore o l'opera. Se il brano è un "
        "indice, una bibliografia, un colophon o un dettaglio di trama, lascia la domanda "
        "vuota. " + REGOLE),
    'citazione': (
        "Ti mostro una citazione che @UTENTE@ ha trascritto da un libro. Scrivi UNA domanda "
        "con cui @UTENTE@ chiederebbe al suo archivio di ritrovarla, SENZA riportarne le "
        "parole: nomina l'opera o l'autore e il tema (per esempio «Qual è la citazione di "
        "… sul … che ho salvato?»). " + REGOLE),
    'lacuna': (
        "Ti do il nome e la descrizione di un tema. Scrivi DUE domande concrete e "
        "specifiche su questo tema (un fatto, un autore, un evento, un concetto preciso), "
        "come le farebbe una persona curiosa. " + REGOLE),
}


def _file(cfg=None) -> str:
    return os.path.join(valutazione.cartella(cfg), 'proposte.jsonl')


def _scrivi(voce: dict, cfg=None) -> None:
    voce = dict(voce)
    voce.setdefault('ts', datetime.now().isoformat(timespec='seconds'))
    with open(_file(cfg), 'a', encoding='utf-8') as f:
        f.write(json.dumps(voce, ensure_ascii=False) + '\n')


def carica(cfg=None) -> list[dict]:
    """Stato attuale di ogni proposta (vale l'ultima riga per id), in ordine
    di creazione."""
    p = _file(cfg)
    stato: dict[str, dict] = {}
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            for riga in f:
                try:
                    v = json.loads(riga)
                except ValueError:
                    continue
                stato[v['id']] = {**stato.get(v['id'], {}), **v}
    return list(stato.values())


def da_rispondere(cfg=None) -> list[dict]:
    return [p for p in carica(cfg) if p.get('stato') == 'nuova']


def riepilogo(cfg=None) -> dict:
    tutte = carica(cfg)
    conta = {}
    for p in tutte:
        conta[p.get('stato')] = conta.get(p.get('stato'), 0) + 1
    motivi = {}
    for p in tutte:
        if p.get('stato') == 'scartata':
            motivi[p.get('motivo')] = motivi.get(p.get('motivo'), 0) + 1
    return {'proposte': len(tutte), 'per_stato': conta, 'motivi_scarto': motivi}


def rispondi(id_proposta: str, risposta: str, tipo: str | None = None, cfg=None) -> str:
    """La risposta dell'utente: la domanda entra nel set di prova con la fonte
    da cui è nata come fonte attesa. Ritorna l'id nel set (d0xx)."""
    p = next(x for x in carica(cfg) if x['id'] == id_proposta)
    tipo = tipo or p['tipo']
    fonti = [p['fonte_titolo']] if p.get('fonte_titolo') and tipo != 'lacuna' else []
    id_set = valutazione.aggiungi_domanda(p['domanda'], risposta, tipo, fonti, cfg)
    _scrivi({'id': id_proposta, 'stato': 'risposta', 'id_set': id_set, 'tipo_finale': tipo}, cfg)
    return id_set


def scarta(id_proposta: str, motivo: str, nota: str = '', cfg=None) -> None:
    """L'utente non vuole questa domanda: resta il motivo, per capire che cosa
    il generatore sbaglia (e la fonte non viene più riproposta)."""
    _scrivi({'id': id_proposta, 'stato': 'scartata', 'motivo': motivo,
             'nota': (nota or '').strip()}, cfg)


# ── Generazione ────────────────────────────────────────────────────────────
def _chiedi(modello: str, sistema: str, utente: str, schema: dict) -> dict:
    r = modelli.client().chat(
        model=modello,
        messages=[{'role': 'system', 'content': sistema}, {'role': 'user', 'content': utente}],
        format=schema, options={'temperature': 0.4, 'num_predict': 400}, keep_alive='10m')
    return json.loads(r['message']['content'])


def _frammenti(con, tipo: str, esclusi: set[int], k: int, rnd: random.Random) -> list[tuple]:
    """(chunk_id, titolo, testo, path) candidati per il tipo."""
    base = ("SELECT c.id, s.title, c.text, s.path, s.genere FROM chunks c "
            "JOIN sources s ON s.id = c.source_id WHERE length(c.text) BETWEEN 350 AND 3000 "
            "AND lower(s.path) NOT LIKE '%/vivino/%' ")
    if tipo == 'pensiero_mio':
        righe = con.execute(base + "AND s.strato = 'esplicito' AND lower(s.title) NOT LIKE "
                                   "'citazione%'").fetchall()
        righe = [r for r in righe if r[4] not in generi.NARRATIVI
                 and not generi.voce_narratore(r[3] or '')
                 and not os.path.basename(r[3] or '').lower().startswith('cit_')]
    elif tipo == 'fatto':
        righe = con.execute(base + "AND s.strato = 'acquisito' AND (s.conoscenza IS NULL OR "
                                   "s.conoscenza NOT IN ('da-leggere', 'non-applicabile')) "
                                   "AND s.genere = ?", (generi.SAGGISTICA_LETTA,)).fetchall()
    else:   # citazione
        righe = con.execute("SELECT c.id, s.title, c.text, s.path, s.genere FROM chunks c "
                            "JOIN sources s ON s.id = c.source_id WHERE lower(s.title) LIKE "
                            "'citazione%' AND length(c.text) >= 25").fetchall()
    righe = [r for r in righe if r[0] not in esclusi]
    rnd.shuffle(righe)
    # Una sola domanda per fonte: senza, un documento lungo occupa mezzo set.
    scelti, fonti = [], set()
    for r in righe:
        if r[3] in fonti:
            continue
        fonti.add(r[3])
        scelti.append(r[:4])
        if len(scelti) >= k:
            break
    return scelti


def _temi_poveri(con, k: int) -> list[tuple[str, str]]:
    """I temi della tassonomia con meno documenti: terreno da lacune."""
    righe = con.execute(
        "SELECT t.name, COALESCE(t.descrizione, ''), COUNT(DISTINCT c.source_id) AS n "
        "FROM topics t LEFT JOIN assertion_topics at ON at.topic_id = t.id "
        "LEFT JOIN assertions a ON a.id = at.assertion_id "
        "LEFT JOIN chunks c ON c.id = a.chunk_id GROUP BY t.id ORDER BY n ASC, t.name "
        "LIMIT ?", (k,)).fetchall()
    return [(n, d) for n, d, _ in righe]


def _vicinanza_massima(con, domanda: str, cfg: dict) -> float:
    qv = indicizzatore.embed_query(domanda, cfg)
    riga = con.execute('SELECT distance FROM vec_chunks WHERE embedding MATCH ? AND k = 1',
                       (qv,)).fetchone()
    return 1.0 - riga[0] if riga else 0.0


def proponi(con, cfg: dict | None = None, n: int = 40, seme: int | None = None,
            solo: list[str] | None = None) -> dict:
    """Nuove proposte, mai sulle fonti già usate (risposte o scartate).
    `solo`: limita ai tipi indicati (es. ['lacuna'] quando ne mancano)."""
    quote = {t: q for t, q in QUOTE.items() if not solo or t in solo}
    totale_quote = sum(quote.values())
    quote = {t: q / totale_quote for t, q in quote.items()}
    cfg = cfg or _config.load()
    rnd = random.Random(seme if seme is not None else datetime.now().toordinal())
    esistenti = carica(cfg)
    esclusi = {p['chunk_id'] for p in esistenti if p.get('chunk_id')}
    temi_usati = {p.get('tema') for p in esistenti if p.get('tema')}
    libri_usati = {p.get('fonte_titolo') for p in esistenti if p.get('sottotipo') == 'non_letto'}
    numero = len(esistenti)
    modelli.assicura_demone()
    modello = cfg['modelli']['motore_ask']
    modelli.prepara_solo(cfg, modello)
    fatte = {t: 0 for t in QUOTE}
    vuote = errori = 0

    def nuova(voce):
        nonlocal numero
        numero += 1
        _scrivi({'id': f'p{numero:03d}', 'stato': 'nuova', **voce}, cfg)

    for tipo in [t for t in ('pensiero_mio', 'fatto', 'citazione') if t in quote]:
        quanti = max(1, round(n * quote[tipo]))
        for cid, titolo, testo, path in _frammenti(con, tipo, esclusi, quanti * 2, rnd):
            if fatte[tipo] >= quanti:
                break
            try:
                d = (_chiedi(modello, PROMPT[tipo], f'Titolo: {titolo}\n\n{testo}', SCHEMA)
                     .get('domanda') or '').strip()
            except Exception:
                errori += 1
                continue
            if len(d) < 12:
                vuote += 1
                continue
            nuova({'tipo': tipo, 'domanda': d, 'chunk_id': cid, 'fonte_titolo': titolo,
                   'fonte_path': path})
            fatte[tipo] += 1

    # Lacune (a): libri posseduti ma mai letti. Domanda fissa, niente modello:
    # è proprio il caso in cui Zeno sbagliava, attribuendo letture mai fatte.
    quante = max(1, round(n * quote['lacuna'])) if 'lacuna' in quote else 0
    non_letti = [t for (t,) in con.execute(
        "SELECT title FROM sources WHERE conoscenza = 'da-leggere' ORDER BY title")
        if t not in libri_usati]
    rnd.shuffle(non_letti)
    # Metà delle lacune dai libri non letti: sono lacune certe per costruzione.
    for titolo in non_letti[:(quante + 1) // 2]:
        nuova({'tipo': 'lacuna', 'sottotipo': 'non_letto', 'fonte_titolo': titolo,
               'domanda': f'Che giudizio ho dato del libro «{titolo}» dopo averlo letto?'})
        fatte['lacuna'] += 1

    # Lacune (b): temi con pochissimi documenti, verificate col recupero.
    modelli.prepara_solo(cfg, modello)
    candidate = []
    for nome, descr in (_temi_poveri(con, 12) if quante else []):
        if nome in temi_usati:
            continue
        try:
            dd = _chiedi(modello, PROMPT['lacuna'], f'Tema: {nome}\nDescrizione: {descr}',
                         SCHEMA_DUE).get('domande') or []
        except Exception:
            errori += 1
            continue
        candidate += [(nome, d.strip()) for d in dd if len(d.strip()) >= 12]
    modelli.prepara_solo(cfg, cfg['modelli']['embeddings'])
    scartate_vicine = 0
    for nome, d in candidate:
        if fatte['lacuna'] >= quante:
            break
        if _vicinanza_massima(con, d, cfg) >= SOGLIA_LACUNA:
            scartate_vicine += 1        # l'archivio qualcosa ce l'ha: non è una lacuna
            continue
        nuova({'tipo': 'lacuna', 'sottotipo': 'tema_povero', 'tema': nome, 'domanda': d})
        fatte['lacuna'] += 1
    modelli.scarica(cfg['modelli']['embeddings'])
    modelli.scarica(modello)
    return {'nuove': fatte, 'vuote': vuote, 'errori': errori,
            'lacune_scartate_perche_trovate': scartate_vicine,
            'da_rispondere': len(da_rispondere(cfg))}


def testo_fonte(con, chunk_id: int) -> str:
    """Il brano da cui è nata una proposta, da mostrare all'utente se vuole."""
    riga = con.execute('SELECT text FROM chunks WHERE id = ?', (chunk_id,)).fetchone()
    return riga[0] if riga else ''
