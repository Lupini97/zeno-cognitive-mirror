# scripts/interfaccia/errors.py
import os, sys, traceback
from pathlib import Path
from datetime import datetime

# ── Dizionario codici errore ─────────────────────────────────────────────────
CODICI = {
    'yaml':    ('ERR-CFG', 'Errore nel file di configurazione YAML'),
    'modello': ('ERR-MOD', 'Modello non disponibile via demone MLX'),
    'fs':      ('ERR-FS',  'File o cartella non trovati'),
    'db':      ('ERR-DB',  'Problema con il database zeno.db'),
    'index':   ('ERR-IDX', 'Errore durante l\'indicizzazione'),
    'system':  ('ERR-SYS', 'Errore di sistema generico'),
    'import':  ('ERR-IMP', 'Errore durante importazione documenti'),
}


# ── Risoluzione percorso BRAIN (autonoma, senza config.py) ──────────────────

def _trova_brain_errors():
    """Cerca BRAIN senza dipendere da config.py (evita import circolari)."""
    if env := os.environ.get('ZENO_BRAIN'):
        p = Path(env)
        if (p / 'zeno_config.yaml').exists():
            return p
    parent = Path(__file__).resolve().parent.parent.parent   # interfaccia → scripts → BRAIN
    if (parent / 'zeno_config.yaml').exists():
        return parent
    if sys.platform == 'darwin':
        try:
            for vol in Path('/Volumes').iterdir():
                if (vol / 'zeno_config.yaml').exists():
                    return vol
        except PermissionError:
            pass
    for c in [Path.home() / 'Zeno', Path.home() / 'zeno']:
        if (c / 'zeno_config.yaml').exists():
            return c
    return None


# ── Diagnostica strutturata ──────────────────────────────────────────────────

def diagnostica(categoria: str, eccezione: Exception, contesto: str = ''):
    """
    Stampa errore strutturato e registra in error_log.txt.
    """
    codice, label = CODICI.get(categoria, ('ERR-???', 'Errore non classificato'))
    tb    = traceback.format_exception(type(eccezione), eccezione, eccezione.__traceback__)
    linea = tb[-2].strip() if len(tb) >= 2 else ''
    msg   = (f'\n╔══ {codice}: {label} ══╗\n'
             f'║ {eccezione}\n'
             f'║ {linea}\n')
    if contesto:
        msg += f'║ Contesto: {contesto}\n'
    msg += f'╚{"═"*40}╝\n'
    print(msg, file=sys.stderr)

    brain = _trova_brain_errors()
    if not brain:
        return codice
    log_path = brain / 'stats' / 'error_log.txt'
    try:
        os.makedirs(log_path.parent, exist_ok=True)
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f'{datetime.now().isoformat()} {codice} {eccezione}\n')
            if contesto:
                f.write(f'  Contesto: {contesto}\n')
            f.write(f'  {linea}\n')
    except Exception:
        pass   # il log è best-effort: non blocca mai l'esecuzione
    return codice


# ── Verifica di sistema ─────────────────────────────────────────────────────

def _pesi_in_cache(repo: str) -> bool:
    """
    I pesi MLX sono già scaricati? Sostituisce il controllo `ollama pull`.

    Deve guardare ESATTAMENTE dove guarda il motore vero (mlx_motore.percorso):
    prima gli alias Ollama→MLX (modelli.risolvi), poi la cartella locale di
    mlx_scarica (models/mlx/locale/<repo>/), poi la cache HuggingFace. Un
    controllo che guarda solo la cache HF darebbe ERR-MOD anche a pesi
    scaricati e perfettamente funzionanti — è successo il 2026-09-06: il
    motore li trovava in locale, questo controllo no, e Zeno restava bloccato
    all'avvio con un falso allarme.
    """
    if not repo:
        return True
    if repo.startswith('/'):
        return os.path.isdir(repo)
    try:
        from zeno_engine import modelli as _modelli
        from zeno_engine.mlx_motore import cartella_locale
        repo = _modelli.risolvi(repo)
        locale = cartella_locale(repo)
        if os.path.isdir(locale) and os.path.exists(os.path.join(locale, 'config.json')):
            return True
        from huggingface_hub import snapshot_download
        snapshot_download(repo, local_files_only=True)
        return True
    except Exception:
        return False


def verifica_sistema() -> list[str]:
    """
    Verifica che tutti i componenti di Zeno siano operativi.
    Legge modelli e percorsi da zeno_config.yaml; usa valori di default se non
    disponibili (utile al primo avvio prima che il config sia scritto).
    """
    import requests
    problemi: list[str] = []
    venv_python = sys.executable

    # Valori di default — sovrascritti da config se disponibile
    llm_model     = 'mlx-community/Meta-Llama-3.1-8B-Instruct-4bit'
    embed_model   = 'mlx-community/bge-m3-mlx-fp16'
    brain_path    : str | None = None

    brain = _trova_brain_errors()
    if brain:
        brain_path = str(brain)
        try:
            import yaml
            cfg = yaml.safe_load((brain / 'zeno_config.yaml').read_text(encoding='utf-8'))
            llm_model   = cfg.get('modelli', {}).get('llm',        llm_model)
            embed_model = cfg.get('modelli', {}).get('embeddings', embed_model)
        except Exception:
            pass
    else:
        problemi.append(
            'ERR-SYS: Cartella BRAIN non trovata. '
            'Imposta ZENO_BRAIN=/percorso o sposta gli script in BRAIN/scripts/.')
        return problemi

    # ── Demone MLX ───────────────────────────────────────────────────────────
    # /api/tags elenca i modelli RESIDENTI, non quelli disponibili: la presenza
    # dei pesi si verifica nella cache locale, che è l'unica cosa che conta per
    # far partire un job (il demone li carica su richiesta).
    try:
        requests.get('http://127.0.0.1:11500/api/ps', timeout=3)
    except Exception:
        problemi.append('ERR-MLX: demone MLX non raggiungibile. Avvialo con: '
                        f'{venv_python} -m zeno_engine.mlx_server')

    for modello in (llm_model, embed_model):
        if not _pesi_in_cache(modello):
            problemi.append(f'ERR-MOD: pesi di {modello} non in cache. Scaricali con: '
                            f'{venv_python} -m zeno_engine.mlx_scarica {modello}')

    # ── Motore V6: il DB è zeno.db (SQLite + sqlite-vec) ─────────────────────
    if brain and not (brain / 'zeno.db').exists():
        problemi.append(
            'ERR-DB: zeno.db non trovato. Esegui: python -m zeno_engine ingest')

    return problemi
