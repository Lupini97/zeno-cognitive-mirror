# zeno.db — fonte di verità unica del motore epistemico (SQLite + sqlite-vec).
#
# Schema come da brief §4, con tre raffinamenti motivati:
#   1. `sources.strato` e `sources.area` (da strati.py): la priorità di
#      retrieval per strato è il cuore dell'architettura V5 e va preservata.
#   2. `chunks` NON ha la colonna BLOB embedding: i vettori vivono nella
#      tabella virtuale sqlite-vec `vec_chunks` (creata dall'Indicizzatore,
#      perché la dimensione dipende dal modello scelto). Un solo posto per i
#      vettori, niente doppioni.
#   3. `chunks_fts` (FTS5): ricerca keyword nativa SQLite a costo zero, per
#      retrieval ibrido (vettoriale + lessicale) del Router.
#
# Idempotenza: cancellare zeno.db e rilanciare `zeno ingest` ricostruisce tutto.

import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,      -- percorso file originale (mai modificato)
    type TEXT,                      -- scheda_libro | maieutica | riflessione | dialogo | pubblicazione | articolo | trascrizione | formazione | studio | other
    strato TEXT,                    -- esplicito | acquisito | dialogico | inferito (da strati.py)
    area TEXT,                      -- prima cartella sotto documents/
    title TEXT,
    created_at TEXT,                -- data del contenuto (non dell'ingestione), YYYY-MM-DD
    ingested_at TEXT NOT NULL,
    hash TEXT NOT NULL,             -- sha256 del testo estratto: rileva modifiche/duplicati
    conoscenza TEXT                 -- schede libro: letto | consultato | letto-parziale | da-leggere | non-applicabile (dal frontmatter)
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    section TEXT,                   -- percorso dei titoli markdown (es. "Sinossi > Trama")
    text TEXT NOT NULL,
    n_chars INTEGER NOT NULL,
    archivista_at TEXT,             -- timestamp dell'estrazione asserzioni (NULL = da fare)
    archivista_tentativi INTEGER NOT NULL DEFAULT 0,  -- fallimenti "contenuto" accumulati: oltre la soglia il chunk è saltato
    UNIQUE(source_id, position)
);

-- FTS5 STANDALONE, non "external content" (content='chunks'): indicizza
-- titolo+sezione+testo, non il solo chunks.text — vedi diagnosi 2026-09-07.
-- Una domanda come "dammi le citazioni dei libri letti" non trovava MAI i
-- file "Citazione da <libro>" (strato esplicito): il loro corpo grezzo è
-- SOLO la citazione ("> Il fattore determinante..."), la parola "citazione"
-- vive unicamente nel TITOLO del file — e chunks_fts indicizzava solo
-- chunks.text, il titolo non vi compariva mai. Ora la stringa indicizzata è
-- la STESSA usata per l'embedding (indicizzatore.testo_da_embeddare):
-- coerenza fra ricerca vettoriale e lessicale, non solo il fix del bug.
-- "external content" avrebbe richiesto ripetere l'identica composizione
-- anche nel trigger di DELETE (il comando 'delete' di FTS5 in quella
-- modalità pretende lo stesso valore inserito, o l'indice si disallinea) —
-- fragile con CASCADE da sources, quindi standalone: la DELETE è un semplice
-- `DELETE FROM chunks_fts WHERE rowid = ...`, corretto in ogni ordine.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, tokenize='unicode61 remove_diacritics 2'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (
        new.id,
        COALESCE((SELECT s.title FROM sources s WHERE s.id = new.source_id), '')
        || CASE WHEN new.section IS NOT NULL AND new.section != ''
                THEN ' — ' || new.section ELSE '' END
        || char(10) || new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    DELETE FROM chunks_fts WHERE rowid = old.id;
END;

CREATE TABLE IF NOT EXISTS topics (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    parent_id INTEGER REFERENCES topics(id),
    descrizione TEXT                -- scritta dall'utente (tassonomia.py)
);

CREATE TABLE IF NOT EXISTS assertions (
    id INTEGER PRIMARY KEY,
    chunk_id INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
    text TEXT NOT NULL,             -- riformulazione atomica in linguaggio naturale
    kind TEXT,                      -- claim | question | definition | position | fact | hypothesis
    stance TEXT,                    -- pro | contro | neutro | incerto
    topic_id INTEGER REFERENCES topics(id),
    confidence REAL,                -- confidenza dell'estrazione (0-1)
    asserted_at TEXT,               -- data del contenuto originale (evoluzione del pensiero)
    modello TEXT                    -- modello Ollama che ha estratto l'asserzione (tracciabilità qualità)
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT,                      -- person | org | concept | place | work
    canonical_id INTEGER REFERENCES entities(id),  -- alias → forma canonica
    community INTEGER               -- cluster Louvain di co-menzione (NULL = frammento/non assegnata)
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY,
    src_type TEXT NOT NULL, src_id INTEGER NOT NULL,   -- assertion | entity | topic | chunk
    dst_type TEXT NOT NULL, dst_id INTEGER NOT NULL,
    relation TEXT NOT NULL,         -- supports | contradicts | mentions | relates_to | elaborates | questions
    weight REAL,
    detected_by TEXT,               -- embedding_sim | llm_extraction | manual
    detected_at TEXT,
    UNIQUE(src_type, src_id, dst_type, dst_id, relation)
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- Appartenenza MOLTI-A-MOLTI asserzione↔tema: un'asserzione può appartenere a
-- più temi, e un tema nuovo aggiunge un legame senza sciogliere i precedenti
-- (2026-07-22). Fonte di verità per i conteggi/selezioni per tema.
-- assertions.topic_id resta come tema "primario" denormalizzato (router).
CREATE TABLE IF NOT EXISTS assertion_topics (
    assertion_id INTEGER NOT NULL REFERENCES assertions(id) ON DELETE CASCADE,
    topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    fonte TEXT,                     -- router | cluster | parent | override
    PRIMARY KEY (assertion_id, topic_id)
);

-- Trace del retrieval iterativo (interleave.py). Non è solo debug: come Zeno
-- naviga la propria memoria è a sua volta un dato interessante.
-- Il brief prevedeva `session_id`, ma zeno.db non ha un concetto di sessione:
-- si usa `query_id`, un identificativo della singola domanda.
CREATE TABLE IF NOT EXISTS retrieval_trace (
    id INTEGER PRIMARY KEY,
    query_id TEXT NOT NULL,
    iterazione INTEGER NOT NULL,
    domanda TEXT NOT NULL,
    metodo TEXT NOT NULL,           -- initial | entita | embedding
    chunk_ids TEXT NOT NULL,        -- JSON array
    decisione TEXT,                 -- JSON: motivo, fonti, entità seguite
    creato_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_query ON retrieval_trace(query_id);

-- Asserzioni da catalogo tolte da `assertions` (filtro.py), con i legami per
-- poterle ripristinare. Spostate e non marcate: spariscono da tutte le query.
CREATE TABLE IF NOT EXISTS asserzioni_scartate (
    id INTEGER PRIMARY KEY,          -- stesso id che aveva in assertions
    chunk_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    kind TEXT, stance TEXT, topic_id INTEGER, confidence REAL,
    asserted_at TEXT, modello TEXT,
    motivo TEXT NOT NULL,            -- nome della regola, o 'modello', o 'manuale'
    fonte TEXT NOT NULL,             -- regola | regola_estrazione | modello | manuale
    scartata_at TEXT NOT NULL,
    legami_temi TEXT,                -- JSON: [[topic_id, fonte], ...]
    legami_archi TEXT                -- JSON: righe di edges che la toccavano
);
CREATE INDEX IF NOT EXISTS idx_scartate_motivo ON asserzioni_scartate(motivo);

-- Documenti PRIME (Reading Brain), importati a mano dall'utente. Una riga per
-- documento, agganciata alla fonte (la copia in riflessioni/libri/prime_*.md).
-- Le asserzioni non hanno colonne nuove: la sezione di provenienza (P R I M E
-- Cantiere) si legge da chunks.section (vedi prime.py). Contratto: docs/PRIME.md.
CREATE TABLE IF NOT EXISTS letture_prime (
    source_id INTEGER PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    nome_file TEXT UNIQUE NOT NULL, -- identità del documento: le versioni nuove lo tengono
    goodreads_id TEXT,              -- id risolto (NULL: libro senza id nella biblioteca)
    libro_file TEXT,                -- nome del file della scheda in documents/libri
    titolo TEXT, autore TEXT,
    versione INTEGER NOT NULL,
    stato_prime TEXT,               -- P | R | I | M | E | chiusa
    iniziata TEXT, aggiornata TEXT, verifica_il TEXT,
    importata_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_nome ON entities(name COLLATE NOCASE, kind);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_assertions_chunk ON assertions(chunk_id);
CREATE INDEX IF NOT EXISTS idx_assertions_topic ON assertions(topic_id);
CREATE INDEX IF NOT EXISTS idx_at_topic ON assertion_topics(topic_id);
CREATE INDEX IF NOT EXISTS idx_at_assertion ON assertion_topics(assertion_id);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_type, src_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON edges(dst_type, dst_id);
"""

# Tassonomia seed (brief §4, livello 1): serve solo a un db NUOVO. Dal
# 28/09/2026 i temi li decide l'utente (tassonomia.py, dashboard): reinserirli
# per nome a ogni connect resuscitava quelli rinominati o ritirati ("Islam ed
# Europa", diventato "Islam", ricompariva vuoto e senza descrizione).
TOPICS_SEED = [
    'Intelligence e sicurezza',
    'Geopolitica',
    'AI ed etica',
    'Criminalità organizzata',
    'Islam',
    'Complessità ed epistemologia',
    'Metodo e OSINT',
    'Scrittura creativa',
    'Vino e sensorialità',
    'Vita professionale/militare',
]


def connect(db_path: str, vec: bool = False) -> sqlite3.Connection:
    """
    Connessione a zeno.db con schema garantito.
    vec=True carica l'estensione sqlite-vec (serve solo a Indicizzatore/Router).
    Journal DELETE (non WAL): l'SSD è ExFAT, meglio evitare i file -shm/-wal.
    """
    con = sqlite3.connect(db_path)
    con.execute('PRAGMA foreign_keys = ON')
    # Batch notturno e sync all'avvio possono scrivere insieme: transazioni
    # brevi entrambi, ma senza busy_timeout il secondo scrittore fallirebbe
    # subito con "database is locked".
    con.execute('PRAGMA busy_timeout = 30000')
    if vec:
        import sqlite_vec
        con.enable_load_extension(True)
        sqlite_vec.load(con)
        con.enable_load_extension(False)
    con.executescript(SCHEMA)
    _migra(con)
    if not con.execute('SELECT 1 FROM topics LIMIT 1').fetchone():
        con.executemany('INSERT INTO topics(name, parent_id) VALUES (?, NULL)',
                        [(t,) for t in TOPICS_SEED])
    con.commit()
    return con


def _migra(con: sqlite3.Connection):
    """Migrazioni additive per db creati con schemi precedenti (mai distruttive)."""
    colonne = {r[1] for r in con.execute('PRAGMA table_info(chunks)')}
    if 'archivista_at' not in colonne:
        con.execute('ALTER TABLE chunks ADD COLUMN archivista_at TEXT')
    if 'archivista_tentativi' not in colonne:
        con.execute('ALTER TABLE chunks ADD COLUMN archivista_tentativi INTEGER NOT NULL DEFAULT 0')
    colonne_sources = {r[1] for r in con.execute('PRAGMA table_info(sources)')}
    if 'conoscenza' not in colonne_sources:
        # Livello di conoscenza delle schede libro. Viveva SOLO nel frontmatter,
        # che l'ingestione scarta: il prompt ARCHIVIO ordinava al modello di
        # dichiararlo "dal campo conoscenza nel frontmatter" e di ignorare i
        # libri `non-applicabile`, cioè gli chiedeva un dato che non poteva
        # vedere. Risultato misurato il 2026-08-24: 474 schede su 702 sono libri
        # MAI letti (263 da-leggere + 211 non-applicabile), 253 con la sinossi
        # Wikipedia indicizzata allo stesso peso dei libri letti — ed è così che
        # "Addio, Miss Marple" è finito in una risposta come se fosse stato letto.
        con.execute('ALTER TABLE sources ADD COLUMN conoscenza TEXT')
    colonne_entities = {r[1] for r in con.execute('PRAGMA table_info(entities)')}
    if 'community' not in colonne_entities:
        con.execute('ALTER TABLE entities ADD COLUMN community INTEGER')
    colonne_edges = {r[1] for r in con.execute('PRAGMA table_info(edges)')}
    if 'note' not in colonne_edges:
        # Spiegazione del Critico ("dove sta la tensione"): senza, il briefing
        # può solo mostrare le due asserzioni nude.
        con.execute('ALTER TABLE edges ADD COLUMN note TEXT')
    colonne_assertions = {r[1] for r in con.execute('PRAGMA table_info(assertions)')}
    if 'modello' not in colonne_assertions:
        con.execute('ALTER TABLE assertions ADD COLUMN modello TEXT')
        # Backfill: fino al 2026-07-15 l'unico motore batch era llama3.1:8b
        # (prima del batch_rapido 3B sull'acquisito) — righe esistenti note.
        con.execute("UPDATE assertions SET modello = 'llama3.1:8b' WHERE modello IS NULL")
    # Genere della fonte e ruolo/voce dell'asserzione (28/09/2026, generi.py).
    # Nei romanzi — dell'utente e letti — la trama pesa poco e non va nei temi;
    # nei romanzi dell'utente la voce del narratore NON è una posizione sua.
    if 'genere' not in colonne_sources:
        con.execute('ALTER TABLE sources ADD COLUMN genere TEXT')
    for tabella in ('assertions', 'asserzioni_scartate'):
        colonne = {r[1] for r in con.execute(f'PRAGMA table_info({tabella})')}
        if 'ruolo' not in colonne:      # trama | tema | citazione (NULL = non giudicata)
            con.execute(f'ALTER TABLE {tabella} ADD COLUMN ruolo TEXT')
        if 'voce' not in colonne:       # narratore (NULL = voce dell'autore della fonte)
            con.execute(f'ALTER TABLE {tabella} ADD COLUMN voce TEXT')
    # Descrizione di ogni tema (28/09/2026, tassonomia.py): la scrive l'utente
    # dalla dashboard; la leggono il classificatore, le schede tema e il grafo.
    if 'descrizione' not in {r[1] for r in con.execute('PRAGMA table_info(topics)')}:
        con.execute('ALTER TABLE topics ADD COLUMN descrizione TEXT')
    # Backfill della tabella molti-a-molti assertion_topics dal topic_id
    # (una tantum, se vuota): 1 legame 'router' per asserzione classificata.
    # In più, per le asserzioni finite in un SOTTO-tema (popola dai cluster ha
    # spostato topic_id da madre a sotto), ripristina il legame col tema MADRE:
    # con il modello multi-tema un'asserzione del sotto-tema appartiene anche al
    # suo tema padre.
    esiste_at = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='assertion_topics'").fetchone()
    if esiste_at and con.execute('SELECT COUNT(*) FROM assertion_topics').fetchone()[0] == 0:
        con.execute("""INSERT OR IGNORE INTO assertion_topics(assertion_id, topic_id, fonte)
            SELECT id, topic_id, 'router' FROM assertions WHERE topic_id IS NOT NULL""")
        con.execute("""INSERT OR IGNORE INTO assertion_topics(assertion_id, topic_id, fonte)
            SELECT a.id, t.parent_id, 'parent' FROM assertions a
            JOIN topics t ON t.id = a.topic_id WHERE t.parent_id IS NOT NULL""")
        con.commit()
