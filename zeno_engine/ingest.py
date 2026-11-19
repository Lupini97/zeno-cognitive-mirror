# Ingestore — corpus → zeno.db (nessun LLM coinvolto).
#
# Fase B: solo Markdown (decisione checkpoint 2026-07-10). PDF/DOCX/HTML
# arriveranno in un batch successivo riusando scripts/estrazione.py.
#
# I file sorgente sono in SOLA LETTURA: mai modificati, mai spostati.
# Idempotente: upsert per path con hash sha256 del testo; re-ingest completo
# ricostruisce lo stesso stato. `--forza` riprocessa anche i file invariati.

import hashlib
import os
import re
import unicodedata
from datetime import datetime, date

import yaml

from zeno_engine import strati  # path → strato/area (modulo V5, spostato qui il 29/09/2026)

ESCLUDI_NOMI = {'.DS_Store'}
ESCLUDI_PREFISSI = ('._',)          # AppleDouble di ExFAT

_RE_FRONTMATTER = re.compile(r'\A---\s*\n(.*?)\n---\s*\n?', re.DOTALL)
_RE_HEADING = re.compile(r'^(#{1,6})\s+(.*)$')
# Date nei nomi file: "maieutica_2026-04-18_1453_...", "Riflessione Pace 04_04_2026", "04-02_..."
_RE_DATA_ISO = re.compile(r'(\d{4})-(\d{2})-(\d{2})')
_RE_DATA_IT = re.compile(r'(\d{2})[-_](\d{2})[-_](\d{4})')


# ── Frontmatter e metadati ────────────────────────────────────────────────────

def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Separa frontmatter YAML (se presente) dal corpo. Tollerante agli errori."""
    m = _RE_FRONTMATTER.match(raw)
    if not m:
        return {}, raw
    try:
        fm = yaml.safe_load(m.group(1)) or {}
        if not isinstance(fm, dict):
            fm = {}
    except yaml.YAMLError:
        fm = {}
    return fm, raw[m.end():]


def _normalizza_data(val) -> str | None:
    """Qualunque data ragionevole → 'YYYY-MM-DD', altrimenti None."""
    if val is None:
        return None
    if isinstance(val, (datetime, date)):
        return val.strftime('%Y-%m-%d')
    s = str(val).strip()
    if not s:
        return None
    m = _RE_DATA_ISO.search(s.replace('/', '-'))
    if m:
        return f'{m.group(1)}-{m.group(2)}-{m.group(3)}'
    m = _RE_DATA_IT.search(s)
    if m:
        return f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    return None


def data_contenuto(path: str, fm: dict) -> str | None:
    """
    Data del CONTENUTO (non dell'ingestione), in ordine di affidabilità:
    frontmatter → nome file → mtime del file.
    """
    # data_pubblicazione/data_caricamento: chiavi dei metadati backfill_date
    # (sidecar _meta.md V5), che la conversione a .md porta nel frontmatter.
    for chiave in ('data', 'date', 'data_pubblicazione', 'data_lettura',
                   'data_aggiunta', 'created', 'data_caricamento'):
        d = _normalizza_data(fm.get(chiave))
        if d:
            return d
    base = os.path.basename(path)
    d = _normalizza_data(base)
    if d:
        return d
    m = _RE_DATA_IT.search(base)
    if m:
        return f'{m.group(3)}-{m.group(2)}-{m.group(1)}'
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d')
    except OSError:
        return None


def titolo_documento(path: str, fm: dict, corpo: str) -> str:
    """Titolo: frontmatter → primo H1 del corpo → nome file senza estensione."""
    if fm.get('tipo') == 'lettura_prime' and fm.get('titolo'):
        # Non lo stesso titolo della scheda del libro: in retrieval e FTS le due
        # fonti si confonderebbero.
        return f"Lettura PRIME — {str(fm['titolo']).strip()}"
    for chiave in ('titolo', 'title', 'tema'):
        v = fm.get(chiave)
        if v:
            return str(v).strip()
    for riga in corpo.splitlines():
        m = _RE_HEADING.match(riga.strip())
        if m and len(m.group(1)) == 1:
            return m.group(2).strip().strip('*_')
    return os.path.splitext(os.path.basename(path))[0]


CONOSCENZA_VALIDE = {'letto', 'letto-testo-completo', 'letto-parziale',
                     'consultato', 'da-leggere', 'non-applicabile'}


def conoscenza_documento(path: str, fm: dict) -> str | None:
    """
    Livello di conoscenza di una scheda libro (`conoscenza:` nel frontmatter,
    con ripiego su `stato:`). Fino al 2026-08-24 restava fuori dall'indice
    perché l'ingestione scarta il frontmatter: il retrieval non sapeva
    distinguere un libro letto da uno soltanto posseduto, e la sinossi
    Wikipedia di un `da-leggere` pesava quanto una riflessione su un libro
    finito. Qui entra in `sources.conoscenza` e da lì arriva al contesto.
    """
    v = fm.get('conoscenza')
    if v is None:
        v = fm.get('stato')
    v = str(v or '').strip().lower()
    if not v:
        return None
    v = v.replace('_', '-').replace(' ', '-')
    if v == 'completato':
        return 'letto'
    return v if v in CONOSCENZA_VALIDE else None


def tipo_documento(path: str, fm: dict) -> str:
    """Tipo del documento, derivato da posizione e frontmatter (corpus reale,
    non l'enum teorico del brief: qui non esistono ancora plaud_audio/gat_report)."""
    p = path.lower()
    base = os.path.basename(p)
    # Prima di tutto: i PRIME stanno in riflessioni/libri e il controllo su
    # '/libri/' qui sotto li farebbe passare per schede libro.
    if fm.get('tipo') == 'lettura_prime':
        return 'lettura_prime'
    if fm.get('fonte') == 'maieutica' or base.startswith('maieutica_'):
        return 'maieutica'
    if fm.get('tipo') == 'libro' or '/libri/' in p:
        return 'scheda_libro'
    if '/chatgpt/' in p or '/gemini/' in p or '/query_archiviate/' in p:
        return 'dialogo'
    if '/riflessioni/' in p:
        return 'riflessione'
    if '/pubblicazioni/' in p:
        return 'pubblicazione'
    if 'trascrizioni' in p:
        return 'trascrizione'
    if '/articoli_saggistica/' in p:
        return 'articolo'
    if '/formazione/' in p:
        return 'formazione'
    if '/psicologia_cognitiva/' in p:
        return 'studio'
    return 'other'


# ── Chunking semantico ────────────────────────────────────────────────────────

def spezza_blocco(blocco: str, max_chars: int = 1800) -> list[str]:
    """
    Spezza un singolo blocco più lungo di max_chars sui confini di frase
    (. ! ? seguiti da spazio), con ripiego su a-capo singoli e infine su
    finestre fisse. Serve per i testi convertiti da PDF/OCR, che spesso
    arrivano come un unico paragrafo senza righe vuote: senza questo taglio
    il chunk supera il contesto del modello (4096 token) e l'Archivista
    lavora su testo troncato (visto sul campo il 2026-07-14: chunk da 36k
    caratteri, prompt troncati da Ollama e ~5 minuti a chunk).
    """
    if len(blocco) <= max_chars:
        return [blocco]
    frasi = re.split(r'(?<=[.!?…])\s+', blocco)
    if len(frasi) == 1:                      # nessun confine di frase: prova gli a-capo
        frasi = blocco.splitlines()
    pezzi, buf = [], ''
    for f in frasi:
        f = f.strip()
        if not f:
            continue
        while len(f) > max_chars:            # frase-monstre: finestra fissa
            pezzi.append(f[:max_chars])
            f = f[max_chars:]
        if buf and len(buf) + 1 + len(f) > max_chars:
            pezzi.append(buf)
            buf = f
        else:
            buf = f'{buf} {f}'.strip()
    if buf:
        pezzi.append(buf)
    return pezzi or [blocco[:max_chars]]


def chunk_markdown(corpo: str, max_chars: int = 1800, min_chars: int = 200) -> list[dict]:
    """
    Chunking guidato dalla struttura, non a lunghezza fissa:
    1. il documento è diviso in sezioni sui titoli markdown;
    2. dentro una sezione i paragrafi si accumulano fino a max_chars;
    3. le sezioni sotto min_chars confluiscono nel chunk successivo;
    4. un singolo blocco oltre max_chars viene spezzato sui confini di frase
       (spezza_blocco): nessun chunk supera mai ~max_chars.
    Ogni chunk ricorda il percorso dei titoli ('section') per contesto e citazioni.
    """
    percorso: list[tuple[int, str]] = []   # [(livello, titolo), ...]
    chunks: list[dict] = []
    buffer: list[str] = []
    sezione_corrente = ''

    def sezione_label() -> str:
        return ' > '.join(t for _, t in percorso)

    def flush(forza: bool = False):
        nonlocal buffer
        testo = '\n\n'.join(buffer).strip()
        if not testo:
            buffer = []
            return
        if not forza and len(testo) < min_chars:
            return                          # troppo corto: aspetta altro materiale
        chunks.append({'section': sezione_corrente, 'text': testo})
        buffer = []

    for blocco in re.split(r'\n{2,}', corpo):
        blocco = blocco.strip()
        if not blocco:
            continue
        prima_riga = blocco.splitlines()[0].strip()
        m = _RE_HEADING.match(prima_riga)
        if m:
            flush()                          # nuova sezione: chiudi se abbastanza pieno
            livello = len(m.group(1))
            titolo = m.group(2).strip().strip('*_')
            percorso[:] = [(l, t) for l, t in percorso if l < livello]
            percorso.append((livello, titolo))
            sezione_corrente = sezione_label()
            resto = '\n'.join(blocco.splitlines()[1:]).strip()
            if resto:
                buffer.append(resto)
            continue
        for pezzo in spezza_blocco(blocco, max_chars):
            if sum(len(b) for b in buffer) + len(pezzo) > max_chars and buffer:
                flush(forza=True)
                sezione_corrente = sezione_label()
            buffer.append(pezzo)
    flush(forza=True)
    return chunks


# ── Ingestione ────────────────────────────────────────────────────────────────

def trova_markdown(root: str) -> list[str]:
    """Tutti i .md sotto root, esclusi AppleDouble e file di sistema, path NFC."""
    trovati = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for nome in filenames:
            if nome in ESCLUDI_NOMI or nome.startswith(ESCLUDI_PREFISSI):
                continue
            if not nome.lower().endswith('.md'):
                continue
            p = os.path.join(dirpath, nome)
            trovati.append(unicodedata.normalize('NFC', p))
    return sorted(trovati)


def ingest_file(con, path: str, cfg: dict, forza: bool = False) -> str:
    """
    Ingerisce un singolo file. Ritorna l'esito:
    'nuovo' | 'aggiornato' | 'invariato' | 'vuoto' | 'errore'
    """
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            raw = f.read()
    except OSError:
        return 'errore'

    fm, corpo = parse_frontmatter(raw)
    corpo = corpo.strip()
    if not corpo:
        return 'vuoto'

    ck = cfg.get('chunk', {})
    try:
        from zeno_engine import prime    # assente nella versione pubblica
    except ImportError:
        prime = None
    if fm.get('tipo') == 'lettura_prime' and prime:
        # Documento PRIME (prime.py): chunk per sezione, senza i blocchi
        # `> [!claude]`, e hash del solo testo dell'utente.
        pezzi = prime.chunk_prime(corpo, ck.get('max_chars', 1800))
        if not pezzi:
            return 'vuoto'
        h = prime.testo_hash(pezzi)
    else:
        h = hashlib.sha256(corpo.encode('utf-8')).hexdigest()
        pezzi = None
    riga = con.execute('SELECT id, hash FROM sources WHERE path = ?', (path,)).fetchone()
    if riga and riga[1] == h and not forza:
        return 'invariato'

    cfg_globale = cfg.get('_globale') or {}
    if pezzi is None:
        pezzi = chunk_markdown(corpo, ck.get('max_chars', 1800), ck.get('min_chars', 200))
    if not pezzi:
        return 'vuoto'

    adesso = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tipo = tipo_documento(path, fm)
    # Correzione rispetto a strati.py (V5, intatto): i dialoghi ChatGPT/Gemini
    # sotto /riflessioni/ non sono pensiero diretto dell'utente → strato dialogico.
    # (Deciso al checkpoint Fase B del 2026-07-10.)
    strato = 'dialogico' if tipo == 'dialogo' else strati.strato_da_path(path, cfg_globale)
    valori = (
        tipo,
        strato,
        strati.area_da_path(path, cfg_globale),
        titolo_documento(path, fm, corpo),
        data_contenuto(path, fm),
        adesso, h,
        conoscenza_documento(path, fm),
    )
    if riga:
        source_id = riga[0]
        # I chunk vecchi (e le assertions collegate, via CASCADE) decadono col contenuto.
        con.execute('DELETE FROM chunks WHERE source_id = ?', (source_id,))
        con.execute(
            'UPDATE sources SET type=?, strato=?, area=?, title=?, created_at=?, '
            'ingested_at=?, hash=?, conoscenza=? WHERE id=?', valori + (source_id,))
        esito = 'aggiornato'
    else:
        cur = con.execute(
            'INSERT INTO sources(type, strato, area, title, created_at, ingested_at, hash, '
            'conoscenza, path) VALUES (?,?,?,?,?,?,?,?,?)', valori + (path,))
        source_id = cur.lastrowid
        esito = 'nuovo'

    con.executemany(
        'INSERT INTO chunks(source_id, position, section, text, n_chars) VALUES (?,?,?,?,?)',
        [(source_id, i, c['section'], c['text'], len(c['text'])) for i, c in enumerate(pezzi)],
    )
    return esito


def aggiorna_conoscenza(con, solo_mancanti: bool = True) -> int:
    """
    Rilegge il frontmatter delle schede libro per riempire `sources.conoscenza`.
    Serve perché l'ingestione riscrive una fonte solo se il CONTENUTO cambia
    (hash): senza questa passata, la colonna nuova resterebbe NULL su tutto il
    corpus già ingerito e il retrieval continuerebbe a non distinguere un libro
    letto da uno da leggere. Costo: la lettura di ~700 file piccoli.
    """
    sql = "SELECT id, path FROM sources WHERE type = 'scheda_libro'"
    if solo_mancanti:
        sql += ' AND conoscenza IS NULL'
    aggiornate = 0
    for sid, path in con.execute(sql).fetchall():
        try:
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                fm, _ = parse_frontmatter(f.read())
        except OSError:
            continue
        c = conoscenza_documento(path, fm)
        if c:
            con.execute('UPDATE sources SET conoscenza = ? WHERE id = ?', (c, sid))
            aggiornate += 1
    con.commit()
    return aggiornate


def riallinea_percorsi(con, root: str, verboso: bool = False) -> dict:
    """
    Fonti ereditate da una posizione precedente di BRAIN (l'SSD /Volumes/BRAIN
    fino alla migrazione del 2026-08-17): stesso file, stesso contenuto, path
    diverso. `rimuovi_fonti_mancanti` non le vede — filtra su `root + '/%'` —
    e il re-ingest ne crea di NUOVE accanto: zeno.db si era così ritrovato con
    3164 fonti per 1582 file reali.

    Il danno non era il doppione in sé: le copie fantasma erano quelle
    ARRICCHITE (tutte le 30.315 asserzioni e 8119 vettori su 8119 chunk), le
    copie nuove erano quasi nude (67 fonti embeddate su 1582). Il retrieval
    vettoriale rispondeva quindi da un indice congelato al 2026-08-11 e citava
    percorsi morti, mentre tutto ciò che era stato scritto dopo restava
    invisibile.

    Qui la fonte fantasma viene RI-PUNTATA al file reale — conserva chunk,
    embeddings e asserzioni — e il doppione nudo viene rimosso. L'appaiamento è
    per (hash, nome file) e richiede un solo candidato: nel dubbio non tocca
    nulla. Sopravvive la fonte con più asserzioni; a parità, quella con più
    chunk.
    """
    esito = {'ripuntate': 0, 'doppioni_rimossi': 0, 'ambigue': 0}
    fuori = [(sid, path, h) for sid, path, h in
             con.execute('SELECT id, path, hash FROM sources')
             if not path.startswith(root + os.sep) or not os.path.exists(path)]
    if not fuori:
        return esito

    def _peso(sid: int) -> tuple[int, int]:
        n_ass = con.execute(
            'SELECT COUNT(*) FROM assertions a JOIN chunks c ON c.id = a.chunk_id '
            'WHERE c.source_id = ?', (sid,)).fetchone()[0]
        n_chunk = con.execute(
            'SELECT COUNT(*) FROM chunks WHERE source_id = ?', (sid,)).fetchone()[0]
        return n_ass, n_chunk

    for sid, path, h in fuori:
        if os.path.exists(path):
            continue                          # fuori root ma vivo: non è un fantasma
        base = os.path.basename(path)
        gemelli = [(g_id, g_path) for g_id, g_path in con.execute(
            'SELECT id, path FROM sources WHERE hash = ? AND id != ?', (h, sid))
            if os.path.basename(g_path) == base and g_path.startswith(root + os.sep)
            and os.path.exists(g_path)]
        if len(gemelli) != 1:
            esito['ambigue'] += 1
            continue
        g_id, g_path = gemelli[0]
        if _peso(sid) >= _peso(g_id):
            # Il fantasma è la copia ricca: prende il posto (e il path) del gemello.
            con.execute('DELETE FROM sources WHERE id = ?', (g_id,))
            con.execute('UPDATE sources SET path = ? WHERE id = ?', (g_path, sid))
            esito['ripuntate'] += 1
        else:
            con.execute('DELETE FROM sources WHERE id = ?', (sid,))
        esito['doppioni_rimossi'] += 1
        if verboso:
            print(f'  riallineata: {base}')
    con.commit()
    return esito


def rimuovi_fonti_mancanti(con, root: str) -> list[str]:
    """
    Fonti il cui file non esiste più (spostato/eliminato dall'utente) → rimosse,
    con chunk e asserzioni a cascata. Così il retrieval non cita mai file
    spariti; al reinserimento l'ingestione li riprende come nuovi.
    """
    spariti = [(sid, p) for sid, p in
               con.execute('SELECT id, path FROM sources WHERE path LIKE ?', (root + '/%',))
               if not os.path.exists(p)]
    for sid, _ in spariti:
        con.execute('DELETE FROM sources WHERE id = ?', (sid,))
    con.commit()
    return [p for _, p in spariti]


def ingest_corpus(con, cfg: dict, root: str | None = None, forza: bool = False) -> dict:
    """Ingestione batch dell'intero corpus Markdown. Ritorna il riepilogo per esito."""
    root = root or cfg['documents']
    riallineate = riallinea_percorsi(con, root)
    rimossi = rimuovi_fonti_mancanti(con, root)
    riepilogo = {'nuovo': 0, 'aggiornato': 0, 'invariato': 0, 'vuoto': 0, 'errore': 0,
                 'rimossi': len(rimossi), 'riallineate': riallineate['ripuntate'],
                 'doppioni_rimossi': riallineate['doppioni_rimossi']}
    errori = []
    for path in trova_markdown(root):
        esito = ingest_file(con, path, cfg, forza=forza)
        riepilogo[esito] += 1
        if esito == 'errore':
            errori.append(path)
        if (riepilogo['nuovo'] + riepilogo['aggiornato']) % 200 == 0:
            con.commit()
    con.commit()
    riepilogo['conoscenza'] = aggiorna_conoscenza(con)
    riepilogo['errori_dettaglio'] = errori
    return riepilogo
