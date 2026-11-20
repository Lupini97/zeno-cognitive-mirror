# Indicizzatore — embeddings, topic e archi di similarità.
#
# Tre compiti batch (nessuno in tempo reale):
#   1. embed_chunks     → vettori dei chunk nella tabella virtuale sqlite-vec
#                         `vec_chunks` (creata qui: la dimensione dipende dal
#                         modello di embedding scelto, registrato in `meta`).
#   2. assegna_topics   → il classificatore locale (classificatore_temi) mette
#                         ogni asserzione nei temi decisi dall'utente, o nel limbo.
#   3. archi_similarita → archi `relates_to` tra chunk semanticamente vicini
#                         (k-NN su sqlite-vec, soglia configurabile).
#
# È QUESTA indicizzazione che rende veloce `zeno ask`: nessuna query dovrà
# mai rileggere il corpus.

import json
import os
import struct
import time
from datetime import datetime


from zeno_engine import db as zdb, modelli, tassonomia
from zeno_engine.config import BRAIN


def _f32(vettore: list[float]) -> bytes:
    return struct.pack(f'{len(vettore)}f', *vettore)


# ── 1. Embeddings ─────────────────────────────────────────────────────────────

def _prepara_vec_chunks(con, modello: str, dim: int):
    """
    Crea (o ricrea, se il modello è cambiato) la tabella vettoriale.

    Il confronto passa da `modelli.risolvi`: alla migrazione su MLX il nome
    registrato era 'bge-m3' e quello nuovo 'mlx-community/bge-m3-mlx-fp16' —
    stessi pesi, stessi vettori, nome diverso. Senza la risoluzione degli alias
    il primo embed_chunks avrebbe fatto DROP di 8.448 vettori e ricominciato
    da capo per un semplice cambio di etichetta.
    """
    registrato = con.execute(
        "SELECT value FROM meta WHERE key = 'embedding_model'").fetchone()
    if registrato and modelli.risolvi(registrato[0]) != modelli.risolvi(modello):
        con.execute('DROP TABLE IF EXISTS vec_chunks')
    con.execute(f'CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0('
                f'chunk_id INTEGER PRIMARY KEY, embedding float[{dim}] distance_metric=cosine)')
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('embedding_model', ?)",
                (modelli.risolvi(modello),))
    con.commit()


def testo_da_embeddare(titolo: str, sezione: str | None, testo: str) -> str:
    """Il contesto (titolo/sezione) entra nel vettore: migliora il retrieval."""
    prefisso = titolo + (f' — {sezione}' if sezione else '')
    return f'{prefisso}\n{testo}'


def embed_chunks(con, cfg: dict, batch: int = 32, verboso: bool = True) -> int:
    modello = cfg['modelli']['embeddings']
    modelli.prepara_solo(cfg, modello)
    sonda = modelli.client().embed(model=modello, input=['sonda'])['embeddings'][0]
    _prepara_vec_chunks(con, modello, len(sonda))

    da_fare = con.execute(
        'SELECT c.id, s.title, c.section, c.text FROM chunks c '
        'JOIN sources s ON s.id = c.source_id '
        'WHERE c.id NOT IN (SELECT chunk_id FROM vec_chunks) ORDER BY c.id').fetchall()
    fatti = 0
    for i in range(0, len(da_fare), batch):
        blocco = da_fare[i:i + batch]
        testi = [testo_da_embeddare(t, sez, txt)[:6000] for _, t, sez, txt in blocco]
        vettori = modelli.client().embed(model=modello, input=testi)['embeddings']
        con.executemany('INSERT INTO vec_chunks(chunk_id, embedding) VALUES (?,?)',
                        [(riga[0], _f32(v)) for riga, v in zip(blocco, vettori)])
        con.commit()
        fatti += len(blocco)
        if verboso and fatti % 320 == 0:
            print(f'  embeddings: {fatti}/{len(da_fare)}')
    return fatti


def embed_query(testo: str, cfg: dict) -> bytes:
    # priorita=True sempre: chi chiama embed_query() è SEMPRE una domanda in
    # attesa di risposta (CLI `zeno ask` o la chat live) — mai un batch, che
    # vettorializza il corpus con embed_chunks(), una funzione diversa.
    v = modelli.client().embed(model=cfg['modelli']['embeddings'], input=[testo],
                     keep_alive='30m', priorita=True)['embeddings'][0]
    return _f32(v)


# ── 2. Topic delle asserzioni ────────────────────────────────────────────────

# Override deterministico path→topic (stesso principio di strati.py in V5:
# prima il path, poi il modello). Senza, il router 3B fa aggancio lessicale
# su un termine (il "metodo classico" di uno spumante → un tema di metodologia)
# perché
# riceve solo il testo nudo dell'asserzione, senza sapere che la fonte è
# una recensione. Verificato sul campo il 2026-07-11.
OVERRIDE_TOPIC_PATH = {'/vivino/': 'Vino e sensorialità'}

# Etichette nude confondono un modello piccolo (vedi sopra): una riga di
# descrizione per tema taglia gli agganci lessicali sbagliati. Le descrizioni
# vengono da zeno_config.yaml (db.descrizioni_temi) e dalla dashboard.

PROMPT_TOPIC = """Classifica l'asserzione in UNO dei temi elencati, usando anche la fonte
da cui viene per capire il contesto.
IMPORTANTE: NON forzare. Se l'asserzione non appartiene CHIARAMENTE a nessuno
dei temi elencati (o è troppo generica/fuori contesto), rispondi "Nessuno":
meglio lasciarla non classificata che metterla nel tema sbagliato — verrà
riconsiderata quando emergeranno nuovi temi. Rispondi solo col JSON.
Temi (nome: descrizione):
{temi}"""


def _assegna_topic(con, aid: int, topic_id: int, fonte: str) -> None:
    """Assegna un tema a un'asserzione nel modello MULTI-TEMA: aggiorna il tema
    primario (assertions.topic_id, solo se non ancora impostato) e AGGIUNGE il
    legame in assertion_topics (senza toglierne altri). Se è un sotto-tema,
    lega anche il tema padre."""
    con.execute('UPDATE assertions SET topic_id=? WHERE id=? AND topic_id IS NULL',
                (topic_id, aid))
    con.execute('INSERT OR IGNORE INTO assertion_topics(assertion_id, topic_id, fonte) '
                'VALUES (?,?,?)', (aid, topic_id, fonte))
    p = con.execute('SELECT parent_id FROM topics WHERE id=?', (topic_id,)).fetchone()
    if p and p[0]:
        con.execute("INSERT OR IGNORE INTO assertion_topics(assertion_id, topic_id, fonte) "
                    "VALUES (?,?,'parent')", (aid, p[0]))


def assegna_topics(con, cfg: dict, verboso: bool = True,
                   limite: int | None = None, da_id: int = 0) -> int:
    """Classifica le asserzioni senza tema (topic_id NULL = nuove o in limbo).

    ATTENZIONE: "senza tema" NON vuol dire "nuove". Dopo l'introduzione del
    limbo (2026-07-22) migliaia di asserzioni VECCHIE hanno legittimamente
    topic_id NULL, quindi una chiamata senza filtri le ripassa TUTTE.

    `da_id` classifica solo le asserzioni create dopo un certo id: è ciò che
    serve all'estrazione incrementale di `zeno sync`, che deve tematizzare
    SOLO quel che ha appena estratto. Senza, il sync all'avvio dell'app
    rimacinava l'intero limbo — misurato il 2026-07-28: 40+ minuti di avvio
    per 6 chunk nuovi, con l'app apparentemente impiccata.

    `limite` serve invece ai job periodici, che smaltiscono il backlog del
    limbo a scaglioni invece che tutto in una volta.

    Dal 28/09/2026 decide il classificatore locale (classificatore_temi), non
    più il router Qwen: 90% di accordo con l'indicizzazione di riferimento contro il 58% del router sulle
    stesse asserzioni, e millisecondi invece di circa 15 secondi l'una. Sotto soglia l'asserzione resta nel
    limbo, come prima con "Nessuno"."""
    from zeno_engine import classificatore_temi
    da_fare = con.execute(
        'SELECT a.id, s.path FROM assertions a '
        'JOIN chunks c ON c.id = a.chunk_id JOIN sources s ON s.id = c.source_id '
        # La trama dei romanzi (ruolo 'trama', vedi giudice_narrativa) non ha
        # tema: i personaggi non sono un argomento (utente, 2026-09-28).
        "WHERE a.topic_id IS NULL AND COALESCE(a.ruolo, '') <> 'trama' AND a.id > ? ORDER BY a.id",
        (da_id,)).fetchall()
    if limite:
        da_fare = da_fare[:limite]
    if not da_fare:
        return 0
    scelte = classificatore_temi.classifica(con, cfg, [a for a, _ in da_fare])
    per_nome = {nome: tid for tid, nome in con.execute('SELECT id, name FROM topics')}
    fatti = 0
    for aid, path in da_fare:
        temi = scelte.get(aid, [])
        forzato = next((per_nome.get(nome) for chiave, nome in OVERRIDE_TOPIC_PATH.items()
                        if chiave in (path or '').lower()), None)
        if forzato and forzato not in temi[:1]:
            temi = [forzato] + [t for t in temi if t != forzato]
        for t in temi:
            _assegna_topic(con, aid, t, 'classificatore')
        fatti += bool(temi)
    con.commit()
    if verboso:
        print(f'  topic: {fatti}/{len(da_fare)} classificate, le altre nel limbo')
    return fatti


# ── 2-bis. Ri-classificazione una-tantum dei temi forzati (limbo) ─────────────
#
# I ~28k asserzioni pre-limbo furono classificate da un router SENZA "Nessuno":
# ognuna fu FORZATA in una madre. `assegna_topics` gira solo su topic_id NULL,
# quindi il limbo non le ha mai riconsiderate (0 in limbo su 30k, verificato
# 2026-07-23). Questo comando le rilegge col router-limbo (enum RISTRETTO alle
# sole madri descritte + "Nessuno") e ADOTTA il nuovo verdetto: nuova madre, o
# "Nessuno" → limbo. Non tocca i sotto-temi da cluster: opera solo sulle
# asserzioni la cui madre è il tema PRIMARIO e sostituisce solo il legame
# 'router'/'override', lasciando intatti 'cluster'/'parent'.

MAX_RITENTI_CHIAMATA = 3      # ritenti inline per chiamata prima di arrendersi
PAUSA_RITENTO = 3            # secondi tra un ritento e l'altro


def _ri_primario(con, aid: int, topic_id: int, fonte: str) -> None:
    """Sostituisce il legame PRIMARIO ('router'/'override') di un'asserzione
    madre con uno nuovo verso `topic_id`, e aggiorna topic_id. Lascia intatti
    eventuali legami 'cluster'/'parent' (sotto-temi)."""
    con.execute("DELETE FROM assertion_topics WHERE assertion_id=? "
                "AND fonte IN ('router','override')", (aid,))
    con.execute('UPDATE assertions SET topic_id=? WHERE id=?', (topic_id, aid))
    con.execute("INSERT OR IGNORE INTO assertion_topics(assertion_id, topic_id, fonte) "
                "VALUES (?,?,?)", (aid, topic_id, fonte))


def _ri_limbo(con, aid: int) -> None:
    """Manda un'asserzione in limbo: topic_id NULL e via il legame primario
    'router'/'override'. Lascia intatti 'cluster'/'parent'."""
    con.execute("DELETE FROM assertion_topics WHERE assertion_id=? "
                "AND fonte IN ('router','override')", (aid,))
    con.execute('UPDATE assertions SET topic_id=NULL WHERE id=?', (aid,))


def riclassifica_madri(con, cfg: dict, modello: str | None = None,
                       limite: int | None = None, verboso: bool = True) -> dict:
    """Rilegge col router-limbo le asserzioni assegnate a un TEMA MADRE e adotta
    il verdetto (madre nuova o limbo). RESUMIBILE via meta('riclassifica_cursore'):
    processa per a.id crescente, salva il cursore ad ogni commit (ogni 50); su
    restart riprende da lì. FUSIBILE: se una chiamata fallisce MAX_RITENTI_CHIAMATA
    volte di fila (demone MLX giù) esce pulito con 'fusibile': True, senza avanzare
    il cursore, così il restart non salta nulla."""
    modello = modello or cfg['modelli']['router']
    modelli.prepara_solo(cfg, modello)

    madri = {nome: tid for tid, nome, parent in
             con.execute('SELECT id, name, parent_id FROM topics') if parent is None}
    schema = {'type': 'object',
              'properties': {'topic': {'type': 'string', 'enum': list(madri) + ['Nessuno']}},
              'required': ['topic']}
    descr = {**zdb.descrizioni_temi(), **tassonomia.descrizioni(con)}
    elenco = '\n'.join(f'- {n}: {descr.get(n, n)}' for n in madri)

    riga = con.execute("SELECT value FROM meta WHERE key='riclassifica_cursore'").fetchone()
    cursore = int(riga[0]) if riga else 0

    da_fare = con.execute(
        'SELECT a.id, a.text, s.title, s.type, s.path FROM assertions a '
        'JOIN chunks c ON c.id=a.chunk_id JOIN sources s ON s.id=c.source_id '
        'JOIN topics t ON t.id=a.topic_id '
        "WHERE t.parent_id IS NULL AND COALESCE(a.ruolo, '') <> 'trama' AND a.id > ? ORDER BY a.id", (cursore,)).fetchall()
    if limite:
        da_fare = da_fare[:limite]

    tot = len(da_fare)
    r = {'fatti': 0, 'stabili': 0, 'spostati': 0, 'limbo': 0, 'override': 0,
         'totale': tot, 'fusibile': False}
    for aid, testo, titolo, tipo, path in da_fare:
        pl = (path or '').lower()
        ov = next((nome for chiave, nome in OVERRIDE_TOPIC_PATH.items() if chiave in pl), None)
        if ov:
            _ri_primario(con, aid, madri[ov], 'override')
            r['override'] += 1
        else:
            topic = None
            for tentativo in range(MAX_RITENTI_CHIAMATA):
                try:
                    resp = modelli.client().chat(
                        model=modello,
                        messages=[{'role': 'system', 'content': PROMPT_TOPIC.format(temi=elenco)},
                                  {'role': 'user',
                                   'content': f'FONTE: {titolo} ({tipo})\nASSERZIONE: {testo}'}],
                        format=schema,
                        options={'temperature': 0.0, 'num_ctx': 2048, 'num_predict': 400})
                    topic = json.loads(resp['message']['content']).get('topic')
                    break
                except Exception:
                    if tentativo < MAX_RITENTI_CHIAMATA - 1:
                        time.sleep(PAUSA_RITENTO)
            if topic is None:
                con.commit()                         # salva fino all'ultimo cursore
                r['fusibile'] = True
                if verboso:
                    print(f'  FUSIBILE: il demone MLX non risponde ad a.id={aid} dopo '
                          f'{MAX_RITENTI_CHIAMATA} tentativi. Stop pulito, cursore={cursore}.')
                return r
            vecchio = con.execute('SELECT topic_id FROM assertions WHERE id=?', (aid,)).fetchone()[0]
            if topic in madri:
                nuovo = madri[topic]
                _ri_primario(con, aid, nuovo, 'router')
                r['stabili' if nuovo == vecchio else 'spostati'] += 1
            elif topic == 'Nessuno':
                _ri_limbo(con, aid)
                r['limbo'] += 1
            else:
                r['stabili'] += 1                    # enum chiuso: ramo morto, per sicurezza
        r['fatti'] += 1
        cursore = aid
        con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('riclassifica_cursore', ?)",
                    (str(aid),))
        if r['fatti'] % 50 == 0:
            con.commit()
            if verboso:
                print(f"  riclassifica: {r['fatti']}/{tot} | stabili {r['stabili']} "
                      f"spostati {r['spostati']} limbo {r['limbo']} override {r['override']}")
    con.commit()
    if not limite:                                   # batch completo: azzera il cursore
        con.execute("DELETE FROM meta WHERE key='riclassifica_cursore'")
        con.commit()
    return r


# ── 3. Archi di similarità tra chunk ──────────────────────────────────────────

def archi_similarita(con, cfg: dict, soglia: float = 0.62, k: int = 4) -> int:
    """
    Per ogni chunk: i k vicini più simili (cosine sim ≥ soglia) di ALTRE fonti
    → archi chunk-relates_to-chunk. Sim tra fonti diverse è ciò che interessa
    al Connettore; i vicini interni allo stesso documento sono ovvi.
    """
    adesso = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    righe = con.execute('SELECT chunk_id, embedding FROM vec_chunks').fetchall()
    nuovi = 0
    for cid, emb in righe:
        vicini = con.execute(
            'SELECT v.chunk_id, v.distance FROM vec_chunks v '
            'WHERE v.embedding MATCH ? AND k = ? AND v.chunk_id != ?',
            (emb, k + 6, cid)).fetchall()
        src_source = con.execute(
            'SELECT source_id FROM chunks WHERE id = ?', (cid,)).fetchone()[0]
        presi = 0
        for vid, dist in vicini:
            sim = 1.0 - dist
            if sim < soglia or presi >= k:
                break
            dst_source = con.execute(
                'SELECT source_id FROM chunks WHERE id = ?', (vid,)).fetchone()
            if not dst_source or dst_source[0] == src_source:
                continue
            a, b = sorted((cid, vid))       # arco non orientato: una sola riga
            cur = con.execute(
                'INSERT OR IGNORE INTO edges(src_type, src_id, dst_type, dst_id, relation, '
                'weight, detected_by, detected_at) VALUES (?,?,?,?,?,?,?,?)',
                ('chunk', a, 'chunk', b, 'relates_to', round(sim, 4),
                 'embedding_sim', adesso))
            nuovi += cur.rowcount
            presi += 1
    con.commit()
    return nuovi


# ── 4. Deduplica entità (alias → forma canonica): PROPOSTE, non applicazione ─
#
# entities.canonical_id esiste nello schema fin dall'inizio ma non era mai
# stato popolato: l'Archivista crea "Caligiuri" e "Mario Caligiuri" come due
# entità distinte, che finiscono su archi 'mentions' separati e sdoppiano i
# conteggi nel grafo. Un'euristica a singolo token (cognome contenuto in un
# nome più lungo) sembrava promettente ma testata sul corpus reale (2026-07-15)
# ha prodotto falsi positivi rilevanti — incluso "INTJ" fuso con "INFJ" (due
# tipi MBTI DIVERSI collassati in una sola entità) e "San"→"Tenuta San Mauro",
# "uomo"→"un uomo": nomi comuni/sigle scambiati per alias. Troppo rischioso
# per una scrittura automatica su dati anche sensibili (profilo cognitivo).
# Stessa filosofia già in uso nel progetto per gli archi dialogici del grafo
# (archi_confermati.json/archi_rifiutati.json): qui si generano solo PROPOSTE
# in stats/entita_alias_proposte.json, che restano candidate — canonical_id
# va scritto solo dopo conferma esplicita dell'utente (via una UI dedicata,
# non ancora costruita). Questa funzione non scrive mai sul DB.
def _token_nome_proprio(t: str) -> bool:
    """True se il token è un'iniziale ('P.') o una parola Capitalizzata.
    Le preposizioni/congiunzioni minuscole (di, del, e, il, la, più...) sono
    quindi False: servono a distinguere un nome proprio ('Samuel P. Huntington')
    da una frase descrittiva ('il marito di Medeia', 'INTJ e INFJ')."""
    import re
    if re.fullmatch(r'[A-ZÀ-Ù]\.?', t):
        return True
    return t[:1].isupper()


def _e_nome_proprio(nome: str) -> bool:
    """Il canonico è un nome proprio plausibile: 2-4 token, tutti 'propri'."""
    tok = nome.split()
    return 2 <= len(tok) <= 4 and all(_token_nome_proprio(t) for t in tok)


def proponi_alias_entita(con, cartella: str = os.path.join(BRAIN, 'stats'),
                         kinds: tuple[str, ...] = ('person', 'org')) -> int:
    """Genera stats/entita_alias_proposte.json: UNA proposta per alias (il
    candidato canonico più citato), ad alta precisione. Filtri anti-falsi-
    positivi (dopo il test sul corpus reale 2026-07-22, 2042 proposte grezze
    troppo rumorose): l'alias dev'essere un token singolo che inizia in
    maiuscola e lungo ≥3 (esclude nomi comuni minuscoli come 'imperatore',
    'custode', sigle/numeri corti); il canonico dev'essere un vero nome proprio
    (_e_nome_proprio: niente frasi con preposizioni). Non scrive MAI canonical_id
    sul DB: solo proposte da confermare a mano."""
    import json
    import os
    path_rifiutate = os.path.join(cartella, 'entita_alias_rifiutati.json')
    rifiutate = set()
    if os.path.exists(path_rifiutate):
        try:
            rifiutate = {(r['alias_id'], r['canonico_id'])
                        for r in json.load(open(path_rifiutate, encoding='utf-8'))}
        except Exception:
            rifiutate = set()

    def _menzioni(eid: int) -> int:
        return con.execute("SELECT COUNT(*) FROM edges WHERE dst_type='entity' "
                           "AND dst_id=? AND relation='mentions'", (eid,)).fetchone()[0]

    proposte = []
    for kind in kinds:
        righe = con.execute(
            'SELECT id, name FROM entities WHERE kind = ? AND canonical_id IS NULL',
            (kind,)).fetchall()
        # solo i nomi lunghi che sembrano nomi propri sono canonici validi
        lunghi = [(i, n) for i, n in righe if _e_nome_proprio(n)]
        for id_corto, nome_corto in righe:
            nc = nome_corto.strip()
            if ' ' in nc or len(nc) < 3 or not nc[:1].isupper():
                continue                     # alias = token proprio singolo
            token = nc.lower()
            candidati = [(il, nl) for il, nl in lunghi
                         if il != id_corto
                         and token in [w.lower() for w in nl.split()]
                         and (id_corto, il) not in rifiutate]
            if not candidati:
                continue
            # una sola proposta per alias: il candidato col più menzioni
            classificati = sorted(((_menzioni(il), il, nl) for il, nl in candidati),
                                  reverse=True)
            m, id_lungo, nome_lungo = classificati[0]
            proposte.append({'alias_id': id_corto, 'alias': nc,
                             'canonico_id': id_lungo, 'canonico': nome_lungo,
                             'kind': kind, 'ambiguo': len(candidati) > 1,
                             'menzioni_canonico': m})
    proposte.sort(key=lambda p: (p['ambiguo'], -p['menzioni_canonico']))
    os.makedirs(cartella, exist_ok=True)
    with open(os.path.join(cartella, 'entita_alias_proposte.json'), 'w', encoding='utf-8') as f:
        json.dump(proposte, f, ensure_ascii=False, indent=2)
    return len(proposte)


def fondi_omonimi(con, verboso: bool = False) -> dict:
    """Fonde le entità con lo STESSO nome ma `kind` diverso, puntando le altre
    alla più citata via entities.canonical_id.

    L'indice unico è (name COLLATE NOCASE, kind): quando l'Archivista assegna
    kind diversi allo stesso nome nascono righe distinte. Misurato il
    2026-07-28: 'Russia' esisteva come concept+place+person+org, 295 menzioni
    spezzate su 4 nodi; in totale 963 nomi spezzati per il 22,2% di TUTTE le
    menzioni. La frammentazione spezza il grafo di co-menzione da cui Louvain
    ricava le community, quindi distorce a cascata anche le proposte di temi.

    Diverso — e molto più sicuro — dell'euristica fuzzy bocciata il 2026-07-15
    (accorpava per token condiviso e fondeva 'INTJ' con 'INFJ'): qui il match è
    ESATTO sul nome, a meno di maiuscole e spazi. Per questo può essere
    automatico, mentre gli alias veri restano proposte da confermare a mano.

    Non cancella nulla: scrive solo canonical_id ed è reversibile rimettendolo
    a NULL. Gli archi non si toccano — grafo e conteggi risolvono al canonico
    con COALESCE(canonical_id, id).
    """
    gruppi = con.execute("""
        SELECT LOWER(TRIM(name)) FROM entities WHERE canonical_id IS NULL
        GROUP BY LOWER(TRIM(name)) HAVING COUNT(*) > 1""").fetchall()
    fusi = 0
    gruppi_fatti = 0
    for (chiave,) in gruppi:
        righe = con.execute(
            'SELECT id FROM entities WHERE LOWER(TRIM(name)) = ? '
            'AND canonical_id IS NULL', (chiave,)).fetchall()
        if len(righe) < 2:
            continue
        # canonico = la riga più citata (deterministico: pareggio sciolto per id)
        conteggi = sorted(
            ((con.execute("SELECT COUNT(*) FROM edges WHERE dst_type='entity' "
                          "AND dst_id=? AND relation='mentions'", (eid,)).fetchone()[0], eid)
             for (eid,) in righe), reverse=True)
        canonico = conteggi[0][1]
        for _, eid in conteggi[1:]:
            con.execute('UPDATE entities SET canonical_id=? WHERE id=?', (canonico, eid))
            fusi += 1
        gruppi_fatti += 1
        if verboso and gruppi_fatti % 200 == 0:
            print(f'  fusi {gruppi_fatti}/{len(gruppi)} gruppi')
    con.commit()
    return {'gruppi': gruppi_fatti, 'entita_fuse': fusi}


def applica_alias(con, alias_id: int, canonico_id: int,
                  cartella: str = os.path.join(BRAIN, 'stats')) -> bool:
    """Conferma un alias: scrive entities.canonical_id sull'entità-alias. È
    reversibile (basta rimettere canonical_id a NULL) e non distrugge archi:
    il grafo e i conteggi risolvono canonical_id a display-time (COALESCE).
    Le nuove menzioni sono già instradate al canonico da archivista._entity_id.
    Rimuove la voce da entita_alias_proposte.json se presente."""
    if alias_id == canonico_id:
        return False
    con.execute('UPDATE entities SET canonical_id = ? WHERE id = ?', (canonico_id, alias_id))
    con.commit()
    _rimuovi_da_proposte(alias_id, canonico_id, cartella)
    return True


def rifiuta_alias(con, alias_id: int, canonico_id: int,
                  cartella: str = os.path.join(BRAIN, 'stats')) -> bool:
    """Rifiuta una proposta: la registra in entita_alias_rifiutati.json (così
    non si ripropone) e la toglie da entita_alias_proposte.json. Non tocca il DB."""
    import json
    import os
    path = os.path.join(cartella, 'entita_alias_rifiutati.json')
    rifiutati = []
    if os.path.exists(path):
        try:
            rifiutati = json.load(open(path, encoding='utf-8'))
        except Exception:
            rifiutati = []
    if not any(r.get('alias_id') == alias_id and r.get('canonico_id') == canonico_id
               for r in rifiutati):
        rifiutati.append({'alias_id': alias_id, 'canonico_id': canonico_id})
        os.makedirs(cartella, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(rifiutati, f, ensure_ascii=False, indent=2)
    _rimuovi_da_proposte(alias_id, canonico_id, cartella)
    return True


def _rimuovi_da_proposte(alias_id: int, canonico_id: int,
                         cartella: str = os.path.join(BRAIN, 'stats')) -> None:
    import json
    import os
    path = os.path.join(cartella, 'entita_alias_proposte.json')
    if not os.path.exists(path):
        return
    try:
        proposte = json.load(open(path, encoding='utf-8'))
    except Exception:
        return
    restanti = [p for p in proposte
                if not (p.get('alias_id') == alias_id and p.get('canonico_id') == canonico_id)]
    if len(restanti) != len(proposte):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(restanti, f, ensure_ascii=False, indent=2)


# ── 5. Community detection (Louvain sul grafo di co-menzione entità) ─────────
#
# La tassonomia dei topic è fissa (10 temi decisi a monte). Louvain scopre
# INVECE i cluster EMERGENTI dal modo in cui le entità co-compaiono nelle
# asserzioni dell'utente: rivela sotto-strutture che i 10 temi non nominano
# (es. "editoria/città", "intelligence e legge 124/2007"). Complemento, non
# sostituto, dei topic. Zero LLM, zero dipendenze nuove (networkx già usato).
# Gira nella pipeline `indicizza` dopo gli archi di similarità (veloce: ~0.3s).
def rileva_community(con, min_size: int = 3, max_community: int = 25) -> dict:
    """Assegna entities.community col cluster Louvain di co-menzione. Solo le
    prime `max_community` per dimensione (>= min_size) ricevono un id 0..N-1;
    le altre restano NULL (frammenti/coda lunga, inutili da colorare). Le
    menzioni sono risolte al canonico (COALESCE(canonical_id, id)); la
    community è scritta sull'entità canonica, gli alias la ereditano a
    display-time via canonical_id."""
    import networkx as nx
    canon = dict(con.execute('SELECT id, COALESCE(canonical_id, id) FROM entities'))
    G = nx.Graph()
    for e1, e2, n in con.execute("""
            SELECT m1.dst_id, m2.dst_id, COUNT(*) FROM edges m1
            JOIN edges m2 ON m2.src_id=m1.src_id AND m2.src_type='assertion'
                 AND m2.relation='mentions' AND m2.dst_id > m1.dst_id
            WHERE m1.src_type='assertion' AND m1.relation='mentions'
            GROUP BY m1.dst_id, m2.dst_id"""):
        c1, c2 = canon.get(e1, e1), canon.get(e2, e2)
        if c1 == c2:
            continue
        if G.has_edge(c1, c2):
            G[c1][c2]['weight'] += n
        else:
            G.add_edge(c1, c2, weight=n)

    con.execute('UPDATE entities SET community = NULL')
    if G.number_of_edges() == 0:
        con.commit()
        return {'community': 0, 'entita_assegnate': 0}

    comms = sorted(nx.community.louvain_communities(G, weight='weight', seed=42),
                   key=len, reverse=True)
    assegnate = 0
    n_comm = 0
    for cid, membri in enumerate(c for c in comms if len(c) >= min_size):
        if cid >= max_community:
            break
        n_comm += 1
        for eid in membri:
            con.execute('UPDATE entities SET community = ? WHERE id = ?', (cid, eid))
            assegnate += 1
    con.commit()
    return {'community': n_comm, 'entita_assegnate': assegnate}
