# Archivista — estrae asserzioni atomiche, entità e stance da ogni chunk.
#
# Agente LLM (il modello locale servito dal demone MLX) pensato per il BATCH,
# mai per il tempo reale. L'output è vincolato da JSON Schema, imposto token
# per token da llguidance: il modello non può produrre JSON malformato; resta
# comunque un retry difensivo. Fino a settembre 2026 il motore era un 7-8B
# su Ollama, con il parametro `format`.
#
# La stance è la posizione di L'UTENTE: viene chiesta solo per i chunk di
# strato esplicito/dialogico; per il materiale acquisito (libri, formazione)
# le asserzioni sono di norma `fact`/`definition` con stance neutra.

import json
import os
import re
from datetime import datetime


# decision/intention (brief §9, pattern da Rowboat): propositi e decisioni di
# L'utente ("approfondirò X", "devo continuare a...") tracciabili nel tempo —
# il Socratico può chiederne conto nei briefing successivi.
KINDS = ['claim', 'question', 'definition', 'position', 'fact', 'hypothesis',
         'decision', 'intention']
STANCES = ['pro', 'contro', 'neutro', 'incerto']
ENTITY_KINDS = ['person', 'org', 'concept', 'place', 'work']

# Oltre questi fallimenti "contenuto" (non-timeout) un chunk viene saltato per
# sempre: pochi chunk-scaletta su cui il 3B non produce JSON valido non devono
# ripresentarsi a ogni run né bloccare la coda (deciso 2026-07-21).
MAX_TENTATIVI_CHUNK = 3

SCHEMA_ESTRAZIONE = {
    'type': 'object',
    'properties': {
        'assertions': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'text': {'type': 'string'},
                    'kind': {'type': 'string', 'enum': KINDS},
                    'stance': {'type': 'string', 'enum': STANCES},
                    'confidence': {'type': 'number'},
                    'entities': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'name': {'type': 'string'},
                                'kind': {'type': 'string', 'enum': ENTITY_KINDS},
                            },
                            'required': ['name', 'kind'],
                        },
                    },
                },
                # Tutti required: con lo structured output i campi facoltativi
                # vengono semplicemente omessi dal modello (visto sul campo:
                # 0 entità su 166 asserzioni finché entities era opzionale).
                'required': ['text', 'kind', 'stance', 'confidence', 'entities'],
            },
        },
    },
    'required': ['assertions'],
}

PROMPT_SISTEMA = """Sei l'Archivista di Zeno, l'archivio cognitivo personale di @UTENTE@.
Il tuo compito: estrarre dal frammento di testo le ASSERZIONI ATOMICHE che contiene.

Regole:
1. Ogni asserzione è UNA frase autonoma e completa in italiano (soggetto-predicato-oggetto),
   comprensibile senza leggere il frammento. RIFORMULA sempre, non copiare: l'asserzione
   deve reggersi da sola, senza connettivi come "tuttavia", "quindi", "nonostante questo",
   e senza pronomi che rimandano al testo.
2. kind: claim (tesi generale) | question (domanda aperta) | definition |
   position (presa di posizione, giudizio o valutazione personale di @UTENTE@) |
   fact (dato oggettivo verificabile) | hypothesis (congettura) |
   decision (decisione presa da @UTENTE@) | intention (proposito, impegno futuro:
   "devo approfondire X", "continuerò a...", "voglio capire Y").
   ATTENZIONE: se il testo è di @UTENTE@, un giudizio o una valutazione ("è un ottimo
   rosato", "penso che", "credo sia sbagliato") è SEMPRE position, mai fact.
   Ma NON tutto è position: dati di provenienza, tipologia, colore, vitigno, trama
   di un libro sono fact catalogici di scarso valore — estraili solo se rari e
   significativi. In una recensione, le position sono SOLO i giudizi valutativi
   e sensoriali di @UTENTE@, non le caratteristiche del prodotto.
3. stance: la posizione di @UTENTE_MAIUSC@ (pro/contro/incerto); "neutro" solo quando il testo
   non esprime una posizione o non è suo. Se @UTENTE@ esita o si contraddice → incerto.
4. entities: persone, organizzazioni, concetti chiave, luoghi, opere citate nel frammento
   (nome proprio o concetto specifico, niente parole generiche).
5. confidence: quanto sei sicuro dell'estrazione (0.0-1.0).
6. Da 0 a 5 asserzioni per frammento. SCARTA il banale e il catalogico: dati
   bibliografici ("X ha scritto molti libri", editore, pagine), indici, agende,
   elenchi di slide, formule di cortesia, ripetizioni e riformulazioni della
   stessa idea. Meglio 2 asserzioni dense che 5 diluite. Se non c'è pensiero
   estraibile, restituisci una lista vuota.
Rispondi SOLO col JSON richiesto."""


def prompt_utente(chunk_text: str, meta: dict) -> str:
    origine = ('testo scritto da @UTENTE@' if meta['strato'] == 'esplicito'
               else 'dialogo di @UTENTE@ con un\'AI (estrai solo il pensiero di @UTENTE@)'
               if meta['strato'] == 'dialogico'
               else 'materiale di studio/lettura (non scritto da @UTENTE@)')
    return (f"FONTE: {meta['title']} ({meta['type']}, {meta['created_at'] or 'data ignota'})\n"
            f"NATURA: {origine}\n"
            f"SEZIONE: {meta['section'] or '—'}\n\n"
            f"FRAMMENTO:\n{chunk_text}")


def estrai_da_chunk(chunk_text: str, meta: dict, modello: str,
                    tentativi: int = 2) -> tuple[list[dict] | None, str]:
    """Chiama il modello. Ritorna (asserzioni, motivo):
       - (list, 'ok')        estrazione riuscita (lista anche vuota = niente di estraibile)
       - (None, 'contenuto') il modello ha risposto ma l'output non era valido/parsabile:
                             è un chunk ostinato, NON una colpa di Ollama
       - (None, 'server')    timeout/connessione: il demone MLX non risponde (stallo o giù)
    La distinzione è ciò che permette al fusibile di scattare SOLO su un vero
    down del demone MLX (serie di 'server'), mai su qualche chunk difficile
    (2026-07-21: 4 chunk-scaletta bloccavano l'intera pipeline)."""
    from zeno_engine import modelli
    ultimo_motivo = 'contenuto'
    for _ in range(tentativi):
        try:
            r = modelli.client().chat(
                model=modello,
                messages=[{'role': 'system', 'content': PROMPT_SISTEMA},
                          {'role': 'user', 'content': prompt_utente(chunk_text, meta)}],
                format=SCHEMA_ESTRAZIONE,
                # num_predict: tetto ai token generati. Senza, un chunk
                # difficile può far "correre" il modello senza fermarsi
                # (visto sul campo il 2026-07-21: richieste attive, CPU >0%,
                # mai concluse dopo 9+ minuti) — 5 asserzioni JSON stanno
                # ben sotto 1000 token, un valore più alto non serve mai.
                options={'temperature': 0.1, 'num_ctx': 4096, 'num_predict': 1024},
            )
            dati = json.loads(r['message']['content'])
            valide = []
            for a in dati.get('assertions', []):
                testo = (a.get('text') or '').strip()
                if len(testo) < 15 or a.get('kind') not in KINDS:
                    continue
                a['text'] = testo
                a['stance'] = a.get('stance') if a.get('stance') in STANCES else 'neutro'
                a['confidence'] = min(max(float(a.get('confidence', 0.5)), 0.0), 1.0)
                a['entities'] = [e for e in (a.get('entities') or [])
                                 if e.get('name', '').strip() and e.get('kind') in ENTITY_KINDS]
                valide.append(a)
            return valide, 'ok'
        except modelli.ErroreConnessione:
            ultimo_motivo = 'server'        # demone MLX spento o in stallo
            continue
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            ultimo_motivo = 'contenuto'     # ha risposto, ma output inservibile
            continue
        except Exception as e:
            nome = type(e).__name__.lower()
            mod = getattr(type(e), '__module__', '')
            ultimo_motivo = ('server' if ('timeout' in nome or 'connect' in nome
                                          or 'httpx' in mod or 'httpcore' in mod)
                             else 'contenuto')
            continue
    return None, ultimo_motivo


# ── Citazioni (euristica testuale, zero LLM) ────────────────────────────────
#
# zeno.db non ha nessuna tabella/tag che distingua una citazione (verificato
# il 2026-07-14: 'assertions.kind' non prevede 'quote', e l'Archivista
# RIFORMULA sempre il testo — le virgolette originali si perdono quasi
# sempre nell'estrazione, vedi PROMPT_SISTEMA regola 1). L'unico posto dove
# sopravvive il discorso riportato testuale è il chunk grezzo (chunks.text).
# Euristica: le virgolette caporali «…» (convenzione tipografica italiana
# per la citazione) e le doppie curve "…" (usate da molti editor/OCR al
# posto delle caporali). Le virgolette dritte ASCII " sono ESCLUSE apposta:
# compaiono anche nel frontmatter YAML (title: "...") e in markup vario,
# troppo rumore. Una soglia minima di caratteri scarta i titoli citati fra
# virgolette (es. «Il messaggero russo») tenendo solo frasi vere e proprie.
RE_CITAZIONE = re.compile(r'[«“]([^«»“”]{25,400})[»”]')


def estrai_citazioni(testo: str, min_chars: int = 25, min_parole: int = 4) -> list[str]:
    """Citazioni testuali (spans tra virgolette) dentro un chunk grezzo.
    `min_parole` scarta i falsi positivi da glossario/termini alternativi
    tipo «esercitarsi/allenarsi/addestrarsi» (una sola "parola" con slash,
    niente spazi): una citazione vera è una frase, non un'etichetta.
    Non distingue da sé l'autore: chi chiama questa funzione lo deduce dallo
    strato/fonte del chunk (esplicito/dialogico → l'utente, acquisito →
    l'autore del testo)."""
    if not testo:
        return []
    citazioni = []
    for m in RE_CITAZIONE.finditer(testo):
        c = ' '.join(m.group(1).split())
        if len(c) >= min_chars and len(c.split()) >= min_parole:
            citazioni.append(c)
    return citazioni


# ── Persistenza ───────────────────────────────────────────────────────────────

def _entity_id(con, name: str, kind: str) -> int:
    """Crea o recupera l'entità. Se è un alias già deduplicato (vedi
    indicizzatore.deduplica_entita, che popola entities.canonical_id), risolve
    subito alla forma canonica: le nuove menzioni di "Caligiuri" finiscono
    così sugli stessi archi di "Mario Caligiuri", senza aspettare un secondo
    passaggio di dedup."""
    name = ' '.join(name.split())
    riga = con.execute(
        'SELECT id, canonical_id FROM entities WHERE name = ? COLLATE NOCASE AND kind = ?',
        (name, kind)).fetchone()
    if riga:
        eid, canonico = riga
        return canonico if canonico else eid
    return con.execute('INSERT INTO entities(name, kind) VALUES (?,?)', (name, kind)).lastrowid


def salva_estrazione(con, chunk_id: int, asserted_at: str | None, asserzioni: list[dict],
                     modello: str | None = None, strato: str | None = None) -> int:
    """Salva le asserzioni del chunk. Ritorna quante ne ha scartate il filtro
    (filtro.py): quelle da catalogo di una fonte `acquisito` finiscono subito in
    asserzioni_scartate — salvate e poi spostate, così restano ripristinabili
    con i loro archi, come quelle filtrate a posteriori."""
    from zeno_engine import filtro
    try:
        from zeno_engine import prime    # assente nella versione pubblica
    except ImportError:
        prime = None
    adesso = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    da_scartare = []
    # Cantiere di un documento PRIME: ipotesi dell'utente, non ancora decise.
    # Qui e non nel prompt, perché l'estrazione può partire da più punti
    # (batch, sync, `prime elabora`) e il tipo deve uscirne uguale ovunque.
    riga_chunk = con.execute(
        'SELECT c.section, s.type FROM chunks c JOIN sources s ON s.id = c.source_id '
        'WHERE c.id = ?', (chunk_id,)).fetchone()
    if (prime and riga_chunk and riga_chunk[1] == prime.TIPO
            and prime.provenienza(riga_chunk[0]) == 'Cantiere'):
        for a in asserzioni:
            a['kind'] = 'hypothesis'
    for a in asserzioni:
        cur = con.execute(
            'INSERT INTO assertions(chunk_id, text, kind, stance, confidence, asserted_at, modello) '
            'VALUES (?,?,?,?,?,?,?)',
            (chunk_id, a['text'], a['kind'], a['stance'], a['confidence'], asserted_at, modello))
        assertion_id = cur.lastrowid
        for e in a['entities']:
            eid = _entity_id(con, e['name'], e['kind'])
            con.execute(
                'INSERT OR IGNORE INTO edges(src_type, src_id, dst_type, dst_id, relation, '
                'weight, detected_by, detected_at) VALUES (?,?,?,?,?,?,?,?)',
                ('assertion', assertion_id, 'entity', eid, 'mentions',
                 1.0, 'llm_extraction', adesso))
        if strato in filtro.STRATI_FILTRATI:
            motivo = filtro.motivo_regola(a['text'])
            if motivo:
                da_scartare.append((assertion_id, motivo))
    if da_scartare:
        filtro.scarta(con, da_scartare, fonte='regola_estrazione')
    con.execute('UPDATE chunks SET archivista_at = ? WHERE id = ?', (adesso, chunk_id))
    return len(da_scartare)


# ── Batch ─────────────────────────────────────────────────────────────────────

def chunk_da_processare(con, source_ids: list[int] | None = None,
                        limite: int | None = None,
                        strati: list[str] | None = None) -> list[tuple]:
    q = ('SELECT c.id, c.text, c.section, s.title, s.type, s.strato, s.created_at '
         'FROM chunks c JOIN sources s ON s.id = c.source_id '
         'WHERE c.archivista_at IS NULL '
         'AND COALESCE(c.archivista_tentativi, 0) < ?')
    par: list = [MAX_TENTATIVI_CHUNK]
    if source_ids:
        q += f" AND s.id IN ({','.join('?' * len(source_ids))})"
        par += source_ids
    if strati:
        q += f" AND s.strato IN ({','.join('?' * len(strati))})"
        par += strati
    q += ' ORDER BY s.strato = \'esplicito\' DESC, s.id, c.position'
    if limite:
        q += ' LIMIT ?'
        par.append(limite)
    return con.execute(q, par).fetchall()


def campiona_documenti(con, n: int = 10) -> list[int]:
    """N documenti diversificati per il test qualità: prima il pensiero dell'utente."""
    ids: list[int] = []
    quote = [('maieutica', 3), ('riflessione', 1), ('pubblicazione', 2),
             ('dialogo', 1), ('articolo', 1), ('scheda_libro', 2)]
    for tipo, quota in quote:
        righe = con.execute(
            'SELECT s.id FROM sources s JOIN chunks c ON c.source_id = s.id '
            'WHERE s.type = ? GROUP BY s.id HAVING SUM(c.n_chars) > 600 '
            'ORDER BY RANDOM() LIMIT ?', (tipo, quota)).fetchall()
        ids += [r[0] for r in righe]
    return ids[:n]


def esegui_batch(con, cfg: dict, source_ids: list[int] | None = None,
                 limite: int | None = None, strati: list[str] | None = None,
                 verboso: bool = True, ruolo: str = 'batch') -> dict:
    """
    `ruolo='batch'` (default) usa il motore 7-8B: qualità massima, per il
    pensiero esplicito/dialogico. `ruolo='batch_rapido'` usa il router 3B
    (deciso con l'utente il 2026-07-15 per il backlog acquisito, restando
    in locale): più veloce, adeguato al materiale catalogico/di studio.
    """
    from zeno_engine import modelli, processi
    modello = modelli.prepara_motore(cfg, ruolo)
    da_fare = chunk_da_processare(con, source_ids, limite, strati)
    totale = len(da_fare)
    # 'secondi' conta SOLO il tempo dei chunk riusciti: usato per la media
    # s/chunk e l'ETA. Un chunk fallito con doppio timeout (5 min x 2
    # tentativi, vedi TIMEOUT_SECONDI) andrebbe altrimenti a gonfiare la media
    # e l'ETA in modo grottesco (visto sul campo il 2026-07-15: un solo chunk
    # bloccato ha fatto salire l'ETA a "33 giorni" su un batch normale).
    esito = {'chunk': 0, 'asserzioni': 0, 'falliti': 0, 'saltati': 0,
             'secondi': 0.0, 'secondi_falliti': 0.0, 'fusibile': False}
    falliti_server_di_fila = 0    # SOLO i timeout/connessione contano per il fusibile
    import time
    for cid, testo, sezione, titolo, tipo, strato, created in da_fare:
        meta = {'title': titolo, 'type': tipo, 'strato': strato,
                'created_at': created, 'section': sezione}
        t0 = time.time()
        asserzioni, motivo = estrai_da_chunk(testo, meta, modello)
        dt = time.time() - t0
        if asserzioni is None:
            esito['secondi_falliti'] += dt
            esito['falliti'] += 1
            if motivo == 'server':
                # Timeout/connessione: possibile down del demone MLX. Solo
                # QUESTI accendono il fusibile.
                falliti_server_di_fila += 1
                if falliti_server_di_fila >= 5:
                    print('  FUSIBILE: 5 timeout consecutivi, il demone MLX non '
                          'risponde (vedi /tmp/zeno_mlx.log, poi rilancia lo STESSO '
                          'comando: i chunk già fatti non si ripetono).')
                    esito['fusibile'] = True
                    processi.aggiorna('archivista', {
                        'stato': 'errore', 'ultimo_evento': datetime.now().isoformat(timespec='seconds'),
                        'dettaglio': f'fusibile (demone MLX non risponde) a {esito["chunk"]}/{totale} chunk ({modello})'})
                    break
            else:
                # 'contenuto': il demone ha risposto (è vivo → azzera il fusibile),
                # ma questo chunk è ostinato. Segna un tentativo: oltre la soglia
                # verrà saltato per sempre, senza bloccare la coda.
                falliti_server_di_fila = 0
                con.execute('UPDATE chunks SET archivista_tentativi = '
                            'COALESCE(archivista_tentativi, 0) + 1 WHERE id = ?', (cid,))
                con.commit()
                esito['saltati'] += 1
                if verboso:
                    print(f'  [skip] {titolo[:45]:45s} chunk ostinato (contenuto non estraibile)')
            continue
        falliti_server_di_fila = 0
        scartate = salva_estrazione(con, cid, created, asserzioni, modello=modello,
                                    strato=strato)
        con.commit()
        esito['secondi'] += dt
        esito['chunk'] += 1
        esito['asserzioni'] += len(asserzioni) - scartate
        esito['scartate'] = esito.get('scartate', 0) + scartate
        if verboso:
            print(f'  [{esito["chunk"]}/{len(da_fare)}] {titolo[:45]:45s} '
                  f'{len(asserzioni)} asserzioni  {dt:5.1f}s')
        media = esito['secondi'] / esito['chunk']
        eta = processi.formatta_eta(media * (totale - esito['chunk']))
        pct = round(100 * esito['chunk'] / totale) if totale else 100
        processi.aggiorna('archivista', {
            'stato': 'in_corso', 'pid': os.getpid(),
            'ultimo_evento': datetime.now().isoformat(timespec='seconds'),
            'dettaglio': (f'{esito["chunk"]}/{totale} chunk ({pct}%) · {modello} · '
                         f'{titolo[:35]} · ETA ~{eta}')})
    if not esito['fusibile']:
        processi.aggiorna('archivista', {
            'stato': 'completato',
            'ultimo_evento': datetime.now().isoformat(timespec='seconds'),
            'dettaglio': f'{esito["chunk"]}/{totale} chunk fatti, {esito["falliti"]} falliti ({modello})'})
    return esito
