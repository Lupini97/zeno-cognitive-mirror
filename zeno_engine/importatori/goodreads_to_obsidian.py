# scripts/zeno_engine/importatori/goodreads_to_obsidian.py
import pandas as pd, os, re, sys
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from zeno_engine.config import carica_config

area_map = {
    'saggistica':          'saggistica',
    'romanzi':             'narrativa',
    'manga':               'manga',
    'libro-game':          'narrativa-interattiva',
    'libri-game':          'narrativa-interattiva',
    'racconti':            'narrativa',
    'opere-poesie-altro':  'poesia-classici',
    'poesia':              'poesia-classici',
    'bambino':             'letteratura-per-ragazzi',
    'ragazzi':             'letteratura-per-ragazzi',
    'fumetti':             'fumetti',
    'graphic-novel':       'fumetti',
    'biografie':           'saggistica',
    'storia':              'saggistica',
    'filosofia':           'saggistica',
    'to-read':             '',
    'currently-reading':   '',
    'read':                '',
}

stato_map = {
    'read':              'completato',
    'to-read':           'da-leggere',
    'currently-reading': 'in-corso',
}

# Aree che non vanno elaborate da Zeno (testo, connessioni, indicizzazione)
AREE_NON_ELABORARE = {
    'manga', 'letteratura-per-ragazzi', 'narrativa-interattiva',
    'fumetti', 'non-applicabile',
}


def safe(v):
    return '' if str(v) in ('nan', 'None', '') else str(v)


def yaml_str(v):
    """Valore destinato a un campo YAML fra virgolette doppie. Una virgoletta
    dentro il valore chiude la stringa e rompe l'intero frontmatter: Zeno
    scarta il blocco e la scheda perde OGNI metadato, `conoscenza` compreso
    (visto su "Fondazione "Filippo Burzio"" il 2026-08-24). Il titolo era già
    protetto, autore ed editore no."""
    return safe(v).replace('"', "'")

def sanitize(n):
    return ''.join(c for c in n if c.isalnum() or c in ' -_,').rstrip()[:80]

def determina_conoscenza(shelf, shelves_str):
    """
    Restituisce il livello di conoscenza del libro per il system prompt.
    - bambino / ragazzi / libro-game / manga / fumetti → non-applicabile
    - saggistica → consultato
    - read → letto
    - currently-reading → letto-parziale
    - to-read → da-leggere
    - shelf sconosciuto → da-leggere (fallback sicuro)
    """
    sl = shelves_str.lower()
    if any(k in sl for k in ('bambino', 'ragazzi', 'libro-game', 'libri-game',
                              'manga', 'fumetti', 'graphic-novel')):
        return 'non-applicabile'
    if 'saggistica' in sl:
        return 'consultato'
    return {'read':              'letto',
            'currently-reading': 'letto-parziale',
            'to-read':           'da-leggere'}.get(shelf, 'da-leggere')

def aggiorna_frontmatter(filepath, nuovi_valori):
    """
    Aggiorna i campi del frontmatter YAML senza toccare le sezioni narrative.
    Chiamata solo su file già esistenti.
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        # `\s*` in modalità MULTILINE mangia anche gli a-capo: su un campo vuoto
        # (`data_lettura:` senza valore) il gruppo scivolava alle righe successive
        # e `(.+)$` agganciava la PRIMA riga non vuota — cioè la chiave dopo. Il
        # valore finiva così tre righe più in basso e le chiavi in mezzo sparivano.
        # Danno misurato il 2026-08-24: 496 schede libro su 702 mutilate
        # (data_aggiunta, stato, rating, rating_goodreads) e 11 con il frontmatter
        # YAML non più valido — fra cui "I baffi" e "Shōgun", dove il `conoscenza:
        # letto` scritto dall'utente diventava invisibile a Zeno. `[^\S\n]*` resta
        # sulla riga; `(.*)$` accetta il valore vuoto e lo sostituisce sul posto.
        for chiave, valore in nuovi_valori.items():
            pattern = rf'^({re.escape(chiave)}:[^\S\n]*)(.*)$'
            content = re.sub(pattern, lambda m: m.group(1) + str(valore),
                             content, count=1, flags=re.MULTILINE)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception as e:
        print(f'  [Merge] Errore aggiornamento {filepath}: {e}')
        return False


def main():
    cfg         = carica_config()
    CSV_PATH    = os.path.join(cfg['percorsi']['brain'], 'import_staging',
                               'goodreads_library_export.csv')
    OUTPUT_PATH = cfg['percorsi']['libri']
    os.makedirs(OUTPUT_PATH, exist_ok=True)

    df = pd.read_csv(CSV_PATH)
    for col in ['ISBN', 'ISBN13']:
        df[col] = df[col].astype(str).str.replace('="', '').str.replace('"', '').str.strip()

    nuove = aggiornate = saltate = 0

    for _, r in df.iterrows():
        titolo     = yaml_str(r['Title'])
        autore     = yaml_str(r['Author'])
        rating     = int(r['My Rating']) if pd.notna(r['My Rating']) else 0
        pagine     = int(r['Number of Pages']) if pd.notna(r['Number of Pages']) else 0
        anno       = int(r['Year Published']) if pd.notna(r['Year Published']) else ''
        data_lettura  = safe(r['Date Read'])
        data_aggiunta = safe(r['Date Added'])
        shelf      = safe(r['Exclusive Shelf'])
        shelves    = safe(r['Bookshelves'])
        review     = safe(r['My Review'])
        notes      = safe(r['Private Notes'])
        read_count = int(r['Read Count']) if pd.notna(r['Read Count']) else 0
        editore    = yaml_str(r['Publisher'])
        anno_orig  = int(r['Original Publication Year']) if pd.notna(r.get('Original Publication Year')) else ''

        aree      = [area_map.get(s.strip(), s.strip()) for s in shelves.split(',')]
        area_str  = ', '.join(filter(None, aree)) or 'da-classificare'
        # stato: fallback esplicito a 'da-leggere' se shelf non riconosciuto o vuoto
        stato     = stato_map.get(shelf, 'da-leggere') if shelf else 'da-leggere'
        stelle    = '⭐' * rating if rating > 0 else 'non valutato'
        conoscenza = determina_conoscenza(shelf, shelves)

        # attribuzione: non-applicabile per aree non elaborate
        attribuzione = ('non-applicabile'
                        if conoscenza == 'non-applicabile'
                        else 'sapere_acquisito_libro')

        filename = sanitize(f'{titolo} - {autore}') + '.md'
        filepath = os.path.join(OUTPUT_PATH, filename)

        # ── Merge: file già esiste ────────────────────────────────────────────
        if os.path.exists(filepath):
            aggiorna_frontmatter(filepath, {
                'stato':       stato,
                'rating':      rating,
                'data_lettura': data_lettura,
                'volte_letto': read_count,
                'conoscenza':  conoscenza,
            })
            aggiornate += 1
            continue

        # ── Crea nuova nota ───────────────────────────────────────────────────
        sinossi_placeholder = '_Da compilare con enrich_books_web.py_'
        anno_orig_line = f'\nanno_originale: {anno_orig}' if anno_orig else ''
        md = f"""---
tipo: libro
titolo: "{titolo}"
autore: "{autore}"
editore: "{editore}"
pagine: {pagine}
anno_pubblicazione: {anno}{anno_orig_line}
data_lettura: {data_lettura}
data_aggiunta: {data_aggiunta}
stato: {stato}
rating: {rating}
rating_goodreads: {r.get('Average Rating', '')}
volte_letto: {read_count}
area: {area_str}
conoscenza: {conoscenza}
attribuzione: {attribuzione}
isbn: {r['ISBN']}
goodreads_id: {r['Book Id']}
tags: [libro, {stato}, {area_str}]
---

# {titolo}
**Autore:** {autore}
**Stato:** {stato} {stelle}

## Sinossi
{sinossi_placeholder}

## Note personali
{notes if notes else '_Nessuna nota_'}

## Recensione
{review if review else '_Cosa mi ha lasciato questo libro? Come si connette al mio pensiero?_'}

## Citazioni
_Durante o dopo la lettura, di a Zeno in chat:_
_'Aggiungi questa citazione al libro [{titolo}]: testo della citazione'_

> _prima citazione_

## Connessioni
_Libri correlati, temi in comune, autori che dialogano con questo._
"""
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(md)
        nuove += 1

    print(f'Completato: {nuove} note create, {aggiornate} aggiornate, {saltate} saltate.')
    print(f'Output: {OUTPUT_PATH}')


if __name__ == '__main__':
    main()
