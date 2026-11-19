# L'unico punto che legge zeno_config.yaml (config centrale, non versionata).
#   carica_config() → il file intero, così com'è: interfaccia, importatori,
#                     strumenti (era scripts/config.py, fuso qui il 29/09/2026);
#   load()          → la vista del motore: sezione `motore_epistemico` con
#                     default sicuri, più il file intero in '_globale'.

import os
import sys
import yaml

# ZENO_BRAIN permette di spostare l'installazione (la vecchia scripts/config.py
# lo onorava già); di serie è la cartella che contiene scripts/.
BRAIN = os.environ.get('ZENO_BRAIN') or os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(BRAIN, 'zeno_config.yaml')
PERCORSI_PREDEFINITI = {
    'brain': BRAIN,
    'documents': os.path.join(BRAIN, 'documents'),
    'db': os.path.join(BRAIN, 'zeno.db'),
    'memoria': os.path.join(BRAIN, 'memoria_elaborata'),
    'vault': os.path.join(BRAIN, 'obsidian_vault'),
    'stats': os.path.join(BRAIN, 'stats'),
    'models': os.path.join(BRAIN, 'models'),
    'staging': os.path.join(BRAIN, 'import_staging'),
    'libri': os.path.join(BRAIN, 'documents', 'libri'),
}
# La cartella del codice (questa scripts/) e il Python con cui lanciare i
# sottoprocessi: il venv di Zeno se c'è, altrimenti l'interprete corrente.
SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_VENV_PY = os.path.join(BRAIN, '.venv_zeno', 'bin', 'python')
PYTHON = _VENV_PY if os.path.exists(_VENV_PY) else sys.executable

DEFAULTS = {
    'db': os.path.join(BRAIN, 'zeno.db'),
    'documents': os.path.join(BRAIN, 'documents'),
    'modelli': {
        # Modelli in formato MLX, serviti dal demone zeno_engine.mlx_server.
        # UN SOLO modello per tutti i ruoli: Qwen3.5-4B (2026-11-18). In chat
        # col prompt ARCHIVIO v4 fa 29/30 sul set di prova; sull'estrazione è
        # pari al Qwen3-4B-2507 che sostituisce (12/12 valide, stessa aderenza
        # al testo), sui temi madre 22/40 d'accordo col DB contro 21/40.
        # Un modello solo: 2,9 GB residenti, nessuno scambio in RAM, un solo
        # download per chi installa.
        # I vecchi nomi Ollama restano accettati: modelli.ALIAS li traduce.
        'router': 'mlx-community/Qwen3.5-4B-4bit',
        'motore_batch': 'mlx-community/Qwen3.5-4B-4bit',
        'motore_ask': 'mlx-community/Qwen3.5-4B-4bit',
        'embeddings': 'mlx-community/bge-m3-mlx-fp16',
        # Cross-encoder per riordinare i candidati (mlx_rerank.py). Usato solo
        # se retrieval.reranker è vero.
        'reranker': 'BAAI/bge-reranker-v2-m3',
    },
    'chunk': {
        'max_chars': 1800,   # ~450 token: 5 chunk restano ben dentro il contesto del 7-8B
        'min_chars': 200,    # sezioni più corte vengono accorpate alla successiva
    },
    'retrieval': {
        'top_k': 5,          # criterio di accettazione: mai più di 5 chunk al motore
        # Reranker (28/09/2026): spento di serie. Sulle domande sintetiche porta
        # la fonte giusta al primo posto nell'84,5% dei casi contro il 48,5%,
        # al costo di ~4 s a domanda e 1,1 GB di RAM. Da accendere dopo la
        # prova sul set dell'utente (zeno valuta risposte, prima e dopo).
        'reranker': False,
        'reranker_candidati': 20,
    },
}


def script_sync_bmb() -> str | None:
    """Script esterno opzionale da lanciare dopo un sync (ponte verso un altro
    vault): `integrazioni.sync_bmb` in zeno_config.yaml. Assente = niente."""
    try:
        p = ((carica_config().get('integrazioni') or {}).get('sync_bmb') or '').strip()
    except Exception:
        return None
    return os.path.expanduser(p) if p else None


# Il nome di chi usa Zeno non sta nel codice: i prompt e i testi scrivono
# @UTENTE@ (o @UTENTE_MAIUSC@ dove serve il maiuscolo) e con_nome() lo
# sostituisce con identita.nome_utente di zeno_config.yaml. modelli.py lo fa
# su ogni messaggio diretto al modello, l'interfaccia sui propri testi.
NOME_PREDEFINITO = 'Utente'
_nome_in_cache = None


def nome_utente() -> str:
    global _nome_in_cache
    if _nome_in_cache is None:
        try:
            nome = ((carica_config().get('identita') or {}).get('nome_utente') or '').strip()
        except Exception:
            nome = ''
        _nome_in_cache = nome or NOME_PREDEFINITO
    return _nome_in_cache


def con_nome(testo):
    if not isinstance(testo, str) or '@UTENTE' not in testo:
        return testo
    nome = nome_utente()
    return testo.replace('@UTENTE_MAIUSC@', nome.upper()).replace('@UTENTE@', nome)


def rete(chiave: str) -> bool:
    """Interruttori della rete in zeno_config.yaml, sezione `rete`:
    `controllo_aggiornamenti` (versioni di librerie e modelli) e
    `download_modelli` (scarica i pesi che mancano). Accesi se assenti."""
    try:
        return bool((carica_config().get('rete') or {}).get(chiave, True))
    except Exception:
        return True


def carica_config() -> dict:
    """zeno_config.yaml intero, senza default: per chi legge percorsi,
    identità, profilo cognitivo e le altre sezioni fuori dal motore."""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(
            f'zeno_config.yaml non trovato in {BRAIN}. Imposta ZENO_BRAIN=/percorso/brain '
            f'oppure tieni scripts/ dentro la cartella di Zeno.')
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    # Le cartelle non indicate in `percorsi` stanno sotto BRAIN, con i nomi
    # di serie: una config nuova può omettere del tutto la sezione.
    percorsi = cfg.setdefault('percorsi', {}) or {}
    cfg['percorsi'] = percorsi
    for chiave, valore in PERCORSI_PREDEFINITI.items():
        percorsi.setdefault(chiave, valore)
    return cfg


def load() -> dict:
    """Config completa: zeno_config.yaml (se esiste) + sezione motore_epistemico con default."""
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
    me = cfg.get('motore_epistemico') or {}
    merged = _deep_merge(DEFAULTS, me)
    merged['_globale'] = cfg   # file intero, per estrazione.py, strati.py e l'interfaccia
    return merged


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
