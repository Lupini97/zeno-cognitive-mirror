# Critico — job periodico: cerca contraddizioni tra asserzioni.
#
# Strategia in due stadi (mai tutto-contro-tutto):
#   1. CANDIDATI economici: coppie di asserzioni dello stesso topic con
#      stance opposte (pro vs contro), oppure stesso topic + entità in comune,
#      con testi non identici. Solo SQL, zero LLM.
#   2. VERIFICA con il motore 7-8B: per ogni coppia candidata il modello
#      giudica se c'è contraddizione reale (structured output). Le coppie
#      confermate diventano archi `contradicts` con il verdetto nel weight.
#
# Il valore per l'utente non è "hai sbagliato": è vedere dove il proprio
# pensiero è cambiato nel tempo (asserted_at conserva la cronologia).

import json
import os
import re
from datetime import datetime
from zeno_engine import lacune as lacune_mod
from zeno_engine.config import BRAIN


STATS_DIR = os.path.join(BRAIN, 'stats')

PROMPT_CRITICO = """Sei il Critico di Zeno, l'archivio cognitivo di @UTENTE@.
Ti do due asserzioni estratte dai suoi testi, con le date. Giudica:
- contraddizione: true solo se le due asserzioni NON possono essere entrambe
  vere nello stesso senso (non basta una differenza di enfasi o di contesto).
- spiegazione: una frase, in italiano, che nomina la tensione (o perché non c'è).
Rispondi solo col JSON."""

SCHEMA_VERDETTO = {
    'type': 'object',
    'properties': {'contraddizione': {'type': 'boolean'},
                   'confidenza': {'type': 'number'},
                   'spiegazione': {'type': 'string'}},
    'required': ['contraddizione', 'confidenza', 'spiegazione'],
}


# Fonti-CATALOGO: parlano ognuna di un OGGETTO DIVERSO, quindi due giudizi
# discordi non sono una contraddizione del pensiero dell'utente — sono due
# oggetti. Le note di degustazione ne sono il caso puro: "questo vino è ottimo"
# e "il vino non è all'altezza" riguardano due bottiglie. Ma condividono
# l'entità generica "vino", e il secondo criterio di accoppiamento (entità in
# comune) le appaia a raffica.
# Misurato il 2026-07-28: 16 contraddizioni su 16 erano di questo tipo, e in un
# caso la spiegazione del modello diceva testualmente "si riferiscono a vini
# diversi" mentre la marcava come contraddizione con confidenza 0.95.
# Stesso principio dell'override di path in indicizzatore.OVERRIDE_TOPIC_PATH:
# prima il path, poi il modello.
PATH_ESCLUSI = ('/vivino/',)

# Il filtro di path da solo non basta, per due motivi verificati l'11/08/2026.
# (1) Copre 3.159 asserzioni vino su 3.205: le altre 46 stanno fuori da
#     /vivino/ e potevano ancora generare coppie.
# (2) Vale solo in avanti. Le 16 lacune-vino misurate il 28/07 erano già state
#     scritte in lacune_aperte.json, e sincronizza_lacune_da_tensioni aggiunge
#     soltanto: restavano in agenda per sempre, 16 su 31, sommergendo le
#     tensioni vere. L'utente: «quelle sono relative ai singoli vini e non delle
#     tensioni vere e proprie».
# Il criterio giusto è il TEMA, non il percorso: una nota di degustazione è la
# valutazione di un prodotto, non una posizione del pensiero, ovunque sia
# archiviata. Il tema resta pienamente interrogabile in ARCHIVIO — è escluso
# solo dalla maieutica, che lavora sulle contraddizioni.
TOPIC_ESCLUSI = ('Vino e sensorialità',)


def coppie_candidate(con, topic_id: int | None = None, max_coppie: int = 40,
                     da_id: int = 0, per_asserzione: int = 3) -> list[tuple]:
    """Coppie stesso-topic con stance opposte o entità condivise (solo SQL).
    Esclude le fonti-catalogo (PATH_ESCLUSI): vedi nota sopra.

    DIVERSITÀ e AVANZAMENTO (2026-07-28). Prima la query non aveva né ORDER BY
    né alcun tetto per asserzione: SQLite esauriva tutte le coppie degli id più
    bassi, e 60 coppie candidate coprivano appena 3 asserzioni distinte su 378
    disponibili. Per un job settimanale è fatale — ogni run avrebbe rimacinato
    lo stesso angolo dell'archivio all'infinito, senza mai arrivare al resto.
    Due correzioni: `per_asserzione` limita quante coppie può occupare la stessa
    asserzione A, e `da_id` è un cursore che fa ripartire il run successivo da
    dove si era arrivati (esegui() lo salva e lo azzera a giro completato)."""
    filtro_topic = 'AND a1.topic_id = ?' if topic_id else ''
    segnaposti_temi = ','.join('?' * len(TOPIC_ESCLUSI)) or "''"
    # l'ordine segue i segnaposto NELLA QUERY, non l'ordine logico: i temi
    # esclusi compaiono a metà del WHERE (prima di `a1.id > ?`), quindi vengono
    # prima di da_id. Sbagliarlo non dà errore, dà risultati muti.
    par: list = (list(TOPIC_ESCLUSI) + [da_id]
                 + ([topic_id] if topic_id else []) + [per_asserzione, max_coppie])
    esclusi = '\n'.join(
        f"      AND (SELECT LOWER(s.path) FROM chunks c JOIN sources s ON s.id=c.source_id "
        f"WHERE c.id=a{n}.chunk_id) NOT LIKE '%{p}%'"
        for p in PATH_ESCLUSI for n in (1, 2))
    q = f"""
    SELECT id1, t1, d1, id2, t2, d2 FROM (
      SELECT id1, t1, d1, id2, t2, d2,
             ROW_NUMBER() OVER (PARTITION BY id1 ORDER BY id2) AS rn
      FROM (
    SELECT DISTINCT a1.id AS id1, a1.text AS t1, a1.asserted_at AS d1,
                    a2.id AS id2, a2.text AS t2, a2.asserted_at AS d2
    FROM assertions a1
    JOIN assertions a2 ON a2.topic_id = a1.topic_id AND a2.id > a1.id
    WHERE a1.topic_id IS NOT NULL
      AND a1.kind IN ('claim', 'position', 'hypothesis')
      AND a2.kind IN ('claim', 'position', 'hypothesis')
      -- Solo il pensiero dell'UTENTE contro sé stesso: le asserzioni del
      -- materiale acquisito sono la voce degli autori letti (Zarathustra non
      -- può "contraddire l'utente"). Visto sul campo il 2026-07-14.
      AND (SELECT s.strato FROM chunks c JOIN sources s ON s.id = c.source_id
           WHERE c.id = a1.chunk_id) IN ('esplicito', 'dialogico')
      AND (SELECT s.strato FROM chunks c JOIN sources s ON s.id = c.source_id
           WHERE c.id = a2.chunk_id) IN ('esplicito', 'dialogico')
      -- Frammenti senza soggetto ("banale e superficiale") generano falsi
      -- positivi cross-contesto: fuori dal confronto.
      AND LENGTH(a1.text) >= 40 AND LENGTH(a2.text) >= 40
      -- Nei romanzi dell'utente parla il narratore, non lui (2026-09-28): i
      -- personaggi possono "contraddirlo" quanto vogliono.
      AND COALESCE(a1.voce, '') <> 'narratore' AND COALESCE(a2.voce, '') <> 'narratore'
      -- Temi di catalogo (vedi TOPIC_ESCLUSI): fuori dal confronto ovunque
      -- sia archiviata la fonte, non solo sotto i PATH_ESCLUSI.
      AND a1.topic_id NOT IN (SELECT id FROM topics WHERE name IN ({segnaposti_temi}))
{esclusi}
      AND (
            (a1.stance = 'pro' AND a2.stance = 'contro')
         OR (a1.stance = 'contro' AND a2.stance = 'pro')
         OR EXISTS (SELECT 1 FROM edges e1
                    JOIN edges e2 ON e2.dst_id = e1.dst_id
                    WHERE e1.src_type='assertion' AND e1.src_id = a1.id
                      AND e2.src_type='assertion' AND e2.src_id = a2.id
                      AND e1.dst_type='entity' AND e2.dst_type='entity'
                      AND e1.relation='mentions' AND e2.relation='mentions')
          )
      AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.relation='contradicts'
                      AND e.src_type='assertion' AND e.dst_type='assertion'
                      AND e.src_id = a1.id AND e.dst_id = a2.id)
      AND a1.id > ?
      {filtro_topic}
      ) ) WHERE rn <= ? ORDER BY id1, id2 LIMIT ?"""
    return con.execute(q, par).fetchall()


def verifica_coppia(a1: tuple, a2: tuple, modello: str) -> dict | None:
    testo = (f'ASSERZIONE A ({a1[1] or "data ignota"}): {a1[0]}\n'
             f'ASSERZIONE B ({a2[1] or "data ignota"}): {a2[0]}')
    from zeno_engine import modelli
    try:
        r = modelli.client().chat(model=modello,
                        messages=[{'role': 'system', 'content': PROMPT_CRITICO},
                                  {'role': 'user', 'content': testo}],
                        format=SCHEMA_VERDETTO,
                        options={'temperature': 0.1, 'num_ctx': 2048, 'num_predict': 300})
        return json.loads(r['message']['content'])
    except Exception:
        return None


def esegui(con, cfg: dict, topic_id: int | None = None,
           max_coppie: int = 40, verboso: bool = True) -> dict:
    from zeno_engine import modelli
    # Ruolo 'ask', non 'batch': il giudizio di contraddizione è più vicino a un
    # compito di "ask" (valutare una relazione) che di estrazione atomica. La
    # scelta risale a luglio 2026, quando i ruoli avevano modelli diversi e
    # llama3.1:8b (batch) produceva 4 falsi positivi su 5 contro qwen2.5:7b
    # (ask). Oggi i due ruoli usano lo stesso modello.
    modello = modelli.prepara_motore(cfg, 'ask')
    adesso = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    esito = {'coppie': 0, 'contraddizioni': 0}
    # Cursore: il run riparte da dove si era fermato il precedente, così il job
    # settimanale percorre TUTTO l'archivio nel tempo invece di rivalutare
    # sempre le stesse asserzioni (vedi nota in coppie_candidate).
    r_cur = con.execute(
        "SELECT value FROM meta WHERE key='critico_cursore'").fetchone()
    da_id = int(r_cur[0]) if r_cur else 0
    coppie = coppie_candidate(con, topic_id, max_coppie, da_id=da_id)
    if not coppie and da_id:                 # giro completato: si ricomincia
        da_id = 0
        coppie = coppie_candidate(con, topic_id, max_coppie, da_id=0)
        if verboso:
            print('  giro completato: cursore azzerato, si riparte dall\'inizio')
    ultimo_id = da_id
    for id1, t1, d1, id2, t2, d2 in coppie:
        ultimo_id = max(ultimo_id, id1)
        v = verifica_coppia((t1, d1), (t2, d2), modello)
        esito['coppie'] += 1
        if not v:
            continue
        conf = float(v.get('confidenza', 0) or 0)
        if conf > 1.0:                      # qwen risponde in scala 0-100
            conf = conf / 100.0
        v['confidenza'] = min(max(conf, 0.0), 1.0)
        if v['contraddizione'] and v['confidenza'] >= 0.6:
            con.execute(
                'INSERT OR IGNORE INTO edges(src_type, src_id, dst_type, dst_id, relation, '
                'weight, detected_by, detected_at, note) VALUES (?,?,?,?,?,?,?,?,?)',
                ('assertion', id1, 'assertion', id2, 'contradicts',
                 round(float(v['confidenza']), 3), 'llm_extraction', adesso,
                 v.get('spiegazione', '')))
            esito['contraddizioni'] += 1
            if verboso:
                print(f'  ⚡ CONTRADDIZIONE ({v["confidenza"]:.2f}): {v["spiegazione"]}')
                print(f'     A ({d1}): {t1[:90]}')
                print(f'     B ({d2}): {t2[:90]}')
        con.commit()
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('critico_cursore', ?)",
                (str(ultimo_id),))
    con.commit()
    sincronizza_lacune_da_tensioni(con)
    esito['cursore'] = ultimo_id
    return esito


# ── Feature: le tensioni diventano lacune da vagliare in maieutica ──────────
#
# Una contraddizione confermata non è "un errore da correggere": è un punto
# dove il pensiero dell'utente si è mosso nel tempo, o dove convivono due
# posizioni non riconciliate — esattamente il materiale su cui la maieutica
# lavora meglio. coppie_candidate() già restringe il confronto al pensiero
# DIRETTO dell'utente (strato esplicito/dialogico, mai gli autori letti): ogni
# arco 'contradicts' qui sotto è quindi "L'utente contro sé stesso", dialoghi
# inclusi. Stesso schema/dedup di chat_ui.py::_scrivi_lacuna_su_file e
# mind.py::aggiorna_lacune_aperte (testo/query_originale/data_prima_rilevazione/
# fonte/area), per restare compatibile con dashboard e chat_ui senza toccarli.

def _norm_lacuna(t: str):
    # Confronto per contenuto (zeno_engine/lacune.py): la stessa tensione
    # con un'altra data o con le frasi in ordine inverso è un doppione.
    return lacune_mod.chiave(t) if (t or '').strip() else ''


def _strato_assertion(con, assertion_id: int) -> str | None:
    r = con.execute(
        'SELECT s.strato FROM assertions a JOIN chunks c ON c.id = a.chunk_id '
        'JOIN sources s ON s.id = c.source_id WHERE a.id = ?', (assertion_id,)).fetchone()
    return r[0] if r else None


def _lacune_da_tensioni(con) -> list[dict]:
    """Ogni arco contradicts confermato → una proposta di lacuna. fonte
    distingue 'tensione_dialogo' (almeno una delle due asserzioni viene da
    uno scambio dialogico) da 'tensione_esplicito' (entrambe da testi scritti):
    all'utente interessa soprattutto sapere quando il pensiero si è mosso
    IN UN DIALOGO, dove è più probabile che sia stato meno meditato."""
    righe = con.execute("""
        SELECT e.src_id, e.dst_id, a1.text, a1.asserted_at, a2.text, a2.asserted_at,
               e.note, t.name
        FROM edges e
        JOIN assertions a1 ON a1.id = e.src_id
        JOIN assertions a2 ON a2.id = e.dst_id
        LEFT JOIN topics t ON t.id = a1.topic_id
        WHERE e.relation = 'contradicts'""").fetchall()
    lacune = []
    for id1, id2, t1, d1, t2, d2, nota, topic in righe:
        s1 = _strato_assertion(con, id1)
        s2 = _strato_assertion(con, id2)
        fonte = 'tensione_dialogo' if 'dialogico' in (s1, s2) else 'tensione_esplicito'
        testo = (f'Tensione rilevata sul tema "{topic or "senza tema"}": '
                 f'il {d1 or "?"} scrivevi «{t1}», il {d2 or "?"} «{t2}»'
                 + (f' — {nota}' if nota else '')
                 + '. Vale la pena una disamina?')
        lacune.append({'testo': testo, 'query_originale': '',
                       'area': topic or 'Senza tema', 'fonte': fonte})
    return lacune


def sincronizza_lacune_da_tensioni(con, cartella: str = STATS_DIR) -> int:
    """
    Scrive in stats/lacune_aperte.json, come NUOVE LACUNE da vagliare con la
    maieutica, le tensioni/contraddizioni confermate dal Critico (comprese
    quelle emerse nei dialoghi). Idempotente e non distruttivo: rilegge
    sempre TUTTE le edges 'contradicts' esistenti (non solo quelle appena
    trovate in questa corsa), così anche le tensioni di run precedenti non
    ancora proposte finiscono in agenda; deduplica con la stessa normalizzazione
    di chat_ui.py (whitespace collassato + lowercase) e rispetta le lacune
    già rifiutate dall'utente (lacune_rifiutate.json). Un errore di IO non
    interrompe mai `critico.esegui`.
    """
    try:
        lacune_path = os.path.join(cartella, 'lacune_aperte.json')
        rifiutate_path = os.path.join(cartella, 'lacune_rifiutate.json')
        try:
            lacune = json.load(open(lacune_path, encoding='utf-8')) \
                     if os.path.exists(lacune_path) else []
        except Exception:
            lacune = []
        rifiutate_norms = set()
        if os.path.exists(rifiutate_path):
            try:
                for e in json.load(open(rifiutate_path, encoding='utf-8')):
                    t = _norm_lacuna(e.get('testo', ''))
                    if t:
                        rifiutate_norms.add(t)
            except Exception:
                pass

        esistenti = {_norm_lacuna(l.get('testo', '')) for l in lacune}
        aggiunte = 0
        for nuova in _lacune_da_tensioni(con):
            tn = _norm_lacuna(nuova['testo'])
            if tn in esistenti or tn in rifiutate_norms:
                continue
            lacune.append({
                'testo': nuova['testo'],
                'query_originale': nuova['query_originale'],
                'data_prima_rilevazione': datetime.now().isoformat(),
                'fonte': nuova['fonte'],
                'area': nuova['area'],
            })
            esistenti.add(tn)
            aggiunte += 1

        if aggiunte:
            os.makedirs(cartella, exist_ok=True)
            with open(lacune_path, 'w', encoding='utf-8') as f:
                json.dump(lacune, f, ensure_ascii=False, indent=2)
        return aggiunte
    except Exception:
        return 0
