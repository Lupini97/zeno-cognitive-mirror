# scripts/zeno_engine/strati.py
# Attribuzione strato/area di un documento Zeno a partire dal suo file_path.
#
# Punto UNICO di verità per la classificazione in strati. Usato da:
#   • index.py    → scrive `strato`/`area` nei metadati al momento dell'indicizzazione
#   • chat_ui.py  → _detect_strato() può delegare qui (path-first)
#
# I quattro strati (con priorità di retrieval, vedi CLAUDE.md):
#   1. esplicito  — riflessioni e pubblicazioni dell'utente      (priorità massima)
#   2. acquisito  — libri, articoli, formazione
#   3. dialogico  — conversazioni archiviate (ChatGPT, Gemini, sessioni)
#   4. inferito   — schede tema da mind.py, connessioni da graph.py
#
# REGOLA: prima il path check, poi i metadati (gotcha noto: i file `_personale`
# in /libri/ vanno classificati `acquisito`, non `esplicito`).

import os

STRATI_VALIDI = ('esplicito', 'acquisito', 'dialogico', 'inferito')


def strato_da_path(file_path: str, cfg: dict | None = None) -> str:
    """
    Determina lo strato di conoscenza dal percorso del file (path-first).
    Replica la logica storica di chat_ui.py::_detect_strato, resa condivisa.
    """
    fp_l = (file_path or '').lower()
    base = os.path.basename(fp_l)

    # memoria_elaborata → schede tema / sintesi inferite
    memoria_dir = ''
    if cfg:
        memoria_dir = (cfg.get('percorsi', {}).get('memoria', '') or '').lower()

    # Override prioritario: _personale dentro /libri/ = acquisito
    if '_personale' in base and '/libri/' in fp_l:
        return 'acquisito'
    if memoria_dir and memoria_dir in fp_l:
        return 'inferito'
    if any(x in fp_l for x in ('/riflessioni/', '/pubblicazioni/')):
        return 'esplicito'
    if '_personale' in base:
        return 'esplicito'
    if any(x in fp_l for x in ('/chatgpt/', '/gemini/', '/query_archiviate/')):
        return 'dialogico'
    return 'acquisito'


def area_da_path(file_path: str, cfg: dict | None = None) -> str:
    """
    Area tematica = prima cartella sotto documents/ (es. 'libri', 'formazione',
    'articoli_saggistica', 'pubblicazioni', 'riflessioni'). Fuori da documents/
    ritorna la cartella radice nota (vault/memoria) o 'sconosciuta'.
    """
    fp = file_path or ''
    docs = ''
    if cfg:
        docs = cfg.get('percorsi', {}).get('documents', '') or ''
    if docs and fp.startswith(docs):
        rel = fp[len(docs):].lstrip('/')
        return rel.split('/')[0] if '/' in rel else 'radice'
    # fuori da documents/: prova vault / memoria
    if cfg:
        if (cfg.get('percorsi', {}).get('vault', '') or '') in fp:
            return 'vault'
        if (cfg.get('percorsi', {}).get('memoria', '') or '') in fp:
            return 'memoria'
    return 'sconosciuta'


def tipo_da_path(file_path: str) -> str:
    """Tipo grossolano del documento, utile per il retrieval/cataloging."""
    ext = os.path.splitext(file_path or '')[1].lower()
    base = os.path.basename((file_path or '').lower())
    if '/libri/' in (file_path or '').lower() and ext == '.md':
        return 'scheda_libro'
    return {
        '.pdf': 'pdf', '.docx': 'docx', '.txt': 'testo',
        '.md': 'markdown', '.html': 'html', '.htm': 'html',
    }.get(ext, 'altro')
