# scripts/zeno_engine/estrazione.py
# Estrazione testo pulito da un file, per la pipeline di indicizzazione Zeno.
#
# Punto UNICO di trasformazione file → testo. Sostituisce SimpleDirectoryReader
# (che senza llama-index-readers-file leggeva i PDF come byte grezzi → 98,7%
# dell'indice era rumore binario; vedi stats/audit_indice.txt).
#
# Motori (tutti LOCALI — rispetta ZERO CLOUD):
#   .pdf        → PyMuPDF (fitz); fallback OCR via ocrmypdf per i PDF-immagine
#   .docx       → python-docx (paragrafi + tabelle)
#   .txt / .md  → lettura diretta UTF-8
#   .html/.htm  → strip dei tag (BeautifulSoup se presente, altrimenti regex)
#
# Gate di leggibilità: la metrica good_ratio (lettere ASCII+spazi+accenti it.)
# scarta ciò che resta illeggibile anche dopo OCR, così non finisce nell'indice.

import os
import re
import shutil

_ACCENTI = set('àèéìòùÀÈÉÌÒÙ')

# Path tesseract: i bundle .app non ereditano il PATH della shell (gotcha noto),
# quindi risolviamo il binario esplicitamente con fallback su Homebrew.
_TESSERACT_CMD = (shutil.which('tesseract')
                  or '/opt/homebrew/bin/tesseract'
                  or '/usr/local/bin/tesseract')


# ── Metrica di leggibilità (stessa di audit_indice.py) ───────────────────────

def good_ratio(t: str) -> float:
    """
    Frazione di caratteri 'testo reale': lettere ASCII + spazi + accenti it.
    Testo italiano/inglese vero ~0.75-0.95; byte binari di PDF ~0.40-0.50.
    """
    s = t[:1500]
    if not s:
        return 0.0
    g = sum(1 for ch in s
            if (ch.isascii() and (ch.isalpha() or ch.isspace())) or ch in _ACCENTI)
    return g / len(s)


# ── Parametri da config ──────────────────────────────────────────────────────

def _params(cfg: dict) -> dict:
    e = (cfg or {}).get('estrazione', {})
    return {
        'ocr_abilitato':   e.get('ocr_abilitato', True),
        'ocr_lingue':      e.get('ocr_lingue', 'ita+eng'),
        'soglia':          e.get('soglia_leggibilita', 0.65),
        'min_chars_pagina': e.get('min_chars_pagina', 50),
    }


# ── PDF ───────────────────────────────────────────────────────────────────────

def _estrai_pdf_fitz(filepath: str) -> tuple[str, int]:
    """Estrae testo con PyMuPDF. Ritorna (testo, n_pagine)."""
    # `import fitz` è il vecchio nome: deprecato da PyMuPDF 1.28 (aggiornato
    # l'11/08/2026) e destinato a sparire. Ora si importa `pymupdf`, tenendo il
    # ripiego su fitz per non rompere eventuali ambienti più vecchi.
    try:
        import pymupdf
    except ImportError:
        import fitz as pymupdf
    doc = pymupdf.open(filepath)
    try:
        parti = [page.get_text('text') for page in doc]
        npag = doc.page_count
    finally:
        doc.close()
    return '\n'.join(parti), npag


def ocr_disponibile() -> bool:
    """True se l'OCR (tesseract + pytesseract) è utilizzabile."""
    try:
        import pytesseract  # noqa: F401
    except Exception:
        return False
    return bool(_TESSERACT_CMD) and os.path.exists(_TESSERACT_CMD)


def _ocr_pdf(filepath: str, lingue: str, dpi: int = 300,
             max_pagine: int = 60) -> str | None:
    """
    OCR di un PDF-immagine: PyMuPDF rende ogni pagina in immagine, tesseract
    (via pytesseract) ne estrae il testo. Nessuna dipendenza da ocrmypdf/
    ghostscript. Ritorna il testo OCR, o None se l'OCR non è disponibile/fallisce.
    """
    if not ocr_disponibile():
        return None
    try:
        try:                                 # vedi nota in _estrai_pdf_fitz
            import pymupdf
        except ImportError:
            import fitz as pymupdf
        import pytesseract
        from PIL import Image
        import io
        pytesseract.pytesseract.tesseract_cmd = _TESSERACT_CMD
        zoom = dpi / 72.0
        mat = pymupdf.Matrix(zoom, zoom)
        doc = pymupdf.open(filepath)
        parti = []
        try:
            for i, page in enumerate(doc):
                if i >= max_pagine:      # tetto di sicurezza su PDF enormi
                    break
                pix = page.get_pixmap(matrix=mat)
                img = Image.open(io.BytesIO(pix.tobytes('png')))
                parti.append(pytesseract.image_to_string(img, lang=lingue))
        finally:
            doc.close()
        return '\n'.join(parti)
    except Exception:
        return None


# ── DOCX ──────────────────────────────────────────────────────────────────────

def _estrai_docx(filepath: str) -> str:
    from docx import Document as _Docx
    d = _Docx(filepath)
    parti = [p.text for p in d.paragraphs if p.text and p.text.strip()]
    for tab in d.tables:
        for row in tab.rows:
            celle = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
            if celle:
                parti.append(' | '.join(celle))
    return '\n'.join(parti)


# ── HTML ──────────────────────────────────────────────────────────────────────

def _estrai_html(filepath: str) -> str:
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        raw = f.read()
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(raw, 'html.parser')
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        return re.sub(r'\n{3,}', '\n\n', soup.get_text('\n')).strip()
    except Exception:
        # fallback regex senza dipendenze
        raw = re.sub(r'(?is)<(script|style).*?>.*?</\1>', ' ', raw)
        raw = re.sub(r'(?s)<[^>]+>', ' ', raw)
        raw = re.sub(r'&nbsp;', ' ', raw)
        return re.sub(r'\s{2,}', ' ', raw).strip()


# ── API pubblica ──────────────────────────────────────────────────────────────

def estrai_documento(filepath: str, cfg: dict | None = None) -> tuple[str, dict, str]:
    """
    Estrae testo pulito da un file.

    Ritorna (testo, meta, esito):
      esito ∈ {'ok', 'illeggibile', 'scartato_pdf_cieco', 'vuoto', 'errore', 'non_supportato'}
      meta  = {'pagine': int, 'ocr': bool, 'good_ratio': float, 'n_chars': int}
    Su esito != 'ok' il chiamante NON deve indicizzare il file.
    """
    p = _params(cfg or {})
    ext = os.path.splitext(filepath)[1].lower()
    meta = {'pagine': 0, 'ocr': False, 'good_ratio': 0.0, 'n_chars': 0}

    try:
        if ext == '.pdf':
            testo, npag = _estrai_pdf_fitz(filepath)
            meta['pagine'] = npag
            chars_pag = len(testo) / max(npag, 1)
            gr = good_ratio(testo)
            # PDF-immagine: pochissimo testo per pagina O testo illeggibile → OCR
            if chars_pag < p['min_chars_pagina'] or gr < p['soglia']:
                if p['ocr_abilitato']:
                    ocr_testo = _ocr_pdf(filepath, p['ocr_lingue'])
                    if ocr_testo and good_ratio(ocr_testo) >= p['soglia']:
                        testo, meta['ocr'] = ocr_testo, True
                    else:
                        meta['good_ratio'] = good_ratio(ocr_testo or testo)
                        meta['n_chars'] = len(ocr_testo or testo)
                        return '', meta, 'scartato_pdf_cieco'
                else:
                    meta['good_ratio'] = gr
                    return '', meta, 'scartato_pdf_cieco'

        elif ext == '.docx':
            testo = _estrai_docx(filepath)
        elif ext in ('.txt', '.md'):
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                testo = f.read()
        elif ext in ('.html', '.htm'):
            testo = _estrai_html(filepath)
        else:
            return '', meta, 'non_supportato'

    except Exception as e:
        meta['errore'] = str(e)
        return '', meta, 'errore'

    testo = testo.strip()
    meta['n_chars'] = len(testo)
    meta['good_ratio'] = good_ratio(testo)

    if not testo:
        return '', meta, 'vuoto'
    if meta['good_ratio'] < p['soglia']:
        return '', meta, 'illeggibile'
    return testo, meta, 'ok'
