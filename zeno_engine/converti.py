# zeno converti — corpus a solo-Markdown (deciso dall'utente il 2026-07-13).
#
# Per ogni PDF/DOCX/HTML/TXT in documents/:
#   1. estrae il testo con estrazione.py (PyMuPDF + OCR + gate good_ratio:
#      l'illeggibile NON diventa .md e resta dov'è, loggato);
#   2. se esiste il sidecar `<base>_meta.md` (backfill_date V5), i suoi
#      metadati confluiscono NEL frontmatter del .md convertito;
#   3. scrive `<base>.md` accanto all'originale;
#   4. sposta originale + sidecar in documents_originali/<stessa struttura>
#      (li cancella l'utente quando si fida; nulla è distrutto).
# Poi `zeno sync` ingerisce i nuovi .md e rimuove le fonti dei sidecar spariti.
#
# `da reinserire/` NON è sotto documents/: mai toccata, per definizione.

import os
import re
import unicodedata
from datetime import datetime

import yaml

from zeno_engine import estrazione  # modulo V5, spostato qui il 29/09/2026
from zeno_engine.config import BRAIN

ORIGINALI = os.path.join(BRAIN, 'documents_originali')
ESTENSIONI = ('.pdf', '.docx', '.html', '.htm', '.txt')


def _sidecar_path(path: str) -> str:
    return os.path.splitext(path)[0] + '_meta.md'


def _leggi_sidecar(path: str) -> dict:
    sc = _sidecar_path(path)
    if not os.path.exists(sc):
        return {}
    try:
        with open(sc, 'r', encoding='utf-8') as f:
            raw = f.read()
        m = re.match(r'\A---\s*\n(.*?)\n---', raw, re.DOTALL)
        return yaml.safe_load(m.group(1)) if m else {}
    except Exception:
        return {}


def _sposta_in_originali(path: str, radice_documents: str):
    rel = os.path.relpath(path, radice_documents)
    dest = os.path.join(ORIGINALI, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    os.rename(path, dest)


def converti_file(path: str, cfg_globale: dict, radice_documents: str) -> str:
    """Converte un file. Esiti: 'ok' | 'md_esiste' | 'scartato:<motivo>' | 'errore'."""
    base_md = os.path.splitext(path)[0] + '.md'
    if os.path.exists(base_md):
        return 'md_esiste'          # mai sovrascrivere un .md esistente

    testo, meta, esito = estrazione.estrai_documento(path, cfg_globale)
    if esito != 'ok':
        return f'scartato:{esito}'

    sidecar = _leggi_sidecar(path)
    fm = {
        'titolo': str(sidecar.get('titolo') or
                      os.path.splitext(os.path.basename(path))[0]),
        'file_originale': os.path.basename(path),
        'convertito_da': 'zeno_converti',
        'data_conversione': datetime.now().strftime('%Y-%m-%d'),
    }
    for chiave in ('tipo', 'data_pubblicazione', 'data_caricamento',
                   'data_pubblicazione_stimata'):
        if sidecar.get(chiave) is not None:
            fm[chiave] = sidecar[chiave]
    if not fm.get('data_pubblicazione') and not fm.get('data_caricamento'):
        fm['data_caricamento'] = datetime.fromtimestamp(
            os.path.getmtime(path)).strftime('%Y-%m-%d')
    if meta.get('ocr'):
        fm['ocr'] = True
        fm['good_ratio'] = round(meta.get('good_ratio', 0), 2)

    corpo = f'# {fm["titolo"]}\n\n{testo.strip()}\n'
    with open(base_md, 'w', encoding='utf-8') as f:
        f.write('---\n' + yaml.safe_dump(fm, allow_unicode=True,
                                         sort_keys=False).strip() +
                '\n---\n\n' + corpo)

    _sposta_in_originali(path, radice_documents)
    sc = _sidecar_path(path)
    if os.path.exists(sc):
        _sposta_in_originali(sc, radice_documents)
    return 'ok'


def esegui(cfg: dict, limite: int | None = None, verboso: bool = True) -> dict:
    radice = cfg['documents']
    cfg_globale = cfg.get('_globale') or {}
    da_fare = []
    for dirpath, dirnames, filenames in os.walk(radice):
        dirnames[:] = [d for d in dirnames if not d.startswith('.')]
        for nome in sorted(filenames):
            if nome.startswith('._') or nome == '.DS_Store':
                continue
            if os.path.splitext(nome)[1].lower() in ESTENSIONI:
                da_fare.append(unicodedata.normalize(
                    'NFC', os.path.join(dirpath, nome)))
    if limite:
        da_fare = da_fare[:limite]

    riepilogo: dict = {'ok': 0, 'md_esiste': 0, 'scartati': 0, 'errore': 0,
                       'dettaglio_scarti': []}
    for i, path in enumerate(da_fare, 1):
        try:
            esito = converti_file(path, cfg_globale, radice)
        except Exception as e:
            esito = 'errore'
            riepilogo['dettaglio_scarti'].append(f'{path} → errore: {e}')
        if esito == 'ok':
            riepilogo['ok'] += 1
        elif esito == 'md_esiste':
            riepilogo['md_esiste'] += 1
        elif esito.startswith('scartato'):
            riepilogo['scartati'] += 1
            riepilogo['dettaglio_scarti'].append(f'{path} → {esito}')
        else:
            riepilogo['errore'] += 1
        if verboso and (esito != 'ok' or i % 20 == 0):
            print(f'  [{i}/{len(da_fare)}] {esito}  {os.path.basename(path)[:55]}')
    return riepilogo
