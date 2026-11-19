# scripts/interfaccia/update_checker.py
import requests, subprocess, json, os, shlex, sys
from datetime import datetime, timedelta
_zeno_s = __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__)))
if _zeno_s not in __import__('sys').path: __import__('sys').path.insert(0, _zeno_s)

def _carica_cfg():
    try:
        from zeno_engine.config import carica_config
        return carica_config()
    except Exception:
        return {}

def _cache_path(cfg):
    stats = cfg.get('percorsi', {}).get('stats',
        str(__import__('pathlib').Path(__file__).resolve().parent.parent.parent / 'stats'))
    return os.path.join(stats, 'update_cache.json')

def _frequenza_giorni(cfg):
    return cfg.get('update_checker', {}).get('frequenza_giorni', 7)

def _librerie_monitorate(cfg):
    # Fallback usato solo se zeno_config.yaml non specifica 'librerie'.
    # Stack V6 (venv_zeno): niente più llama-index/qdrant-client.
    default = ['streamlit', 'mlx', 'mlx-lm', 'llguidance', 'pymupdf',
               'pytesseract', 'sqlite-vec', 'networkx', 'plotly', 'pandas']
    return cfg.get('update_checker', {}).get('librerie', default)

def controlla_modelli_mlx(cfg):
    """
    I modelli MLX sono repo HuggingFace fissati per nome: un repo aggiornato a
    monte cambia commit, non nome. Si confronta lo SHA del commit locale con
    quello remoto — se differiscono, i pesi in cache sono vecchi.
    """
    aggiornamenti = []
    try:
        me = cfg.get('motore_epistemico', {}).get('modelli', {})
        repos = {v for v in me.values() if isinstance(v, str) and '/' in v}
        for repo in sorted(repos):
            try:
                r = requests.get(f'https://huggingface.co/api/models/{repo}', timeout=5)
                if r.status_code != 200:
                    continue
                dati = r.json()
                remoto = dati.get('sha')
                if _copia_locale_aggiornata(repo, dati.get('lastModified')):
                    continue
                if remoto and remoto != _sha_locale(repo):
                    aggiornamenti.append({
                        'tipo': 'modello_mlx',
                        'nome': repo,
                        'ultima_versione': remoto[:8],
                        'comando': (f'{shlex.quote(sys.executable)} -m '
                                    f'zeno_engine.mlx_scarica {shlex.quote(repo)}'),
                    })
            except Exception:
                pass
    except Exception:
        pass
    return aggiornamenti


def _copia_locale_aggiornata(repo: str, ultima_modifica: str | None) -> bool:
    """I pesi scaricati da `mlx_scarica` stanno in models/mlx/locale/<repo>/,
    non nella cache HuggingFace: lì `_sha_locale` non trova nulla e ogni
    modello risultava «da aggiornare» per sempre (visto il 2026-11-14: due
    falsi allarmi, file identici a quelli remoti). Qui non c'è un commit da
    confrontare: la copia è aggiornata se è stata scaricata DOPO l'ultima
    modifica del repo. Il percorso replica `mlx_motore.cartella_locale` senza
    importarlo: quel modulo carica MLX, e questo codice gira dentro la chat."""
    try:
        from zeno_engine.config import BRAIN
        pesi = os.path.join(BRAIN, 'models', 'mlx', 'locale',
                            repo.replace('/', '__'), 'model.safetensors')
        if not ultima_modifica or not os.path.exists(pesi):
            return False
        remoto = datetime.fromisoformat(ultima_modifica.replace('Z', '+00:00'))
        return os.path.getmtime(pesi) >= remoto.timestamp()
    except Exception:
        return False


def _sha_locale(repo: str) -> str | None:
    """Commit dei pesi già in cache, senza toccare la rete."""
    try:
        from huggingface_hub import snapshot_download
        percorso = snapshot_download(repo, local_files_only=True)
        return os.path.basename(percorso)
    except Exception:
        return None


def controlla_librerie_python(cfg):
    aggiornamenti = []
    librerie = _librerie_monitorate(cfg)
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'list', '--outdated', '--format=json'],
            capture_output=True, text=True, timeout=30)
        for pkg in json.loads(result.stdout or '[]'):
            if pkg['name'] in librerie:
                aggiornamenti.append({
                    'tipo': 'libreria_python',
                    'nome': pkg['name'],
                    'versione_attuale': pkg['version'],
                    'ultima_versione': pkg['latest_version'],
                    # Il comando DEVE puntare all'interprete che ha fatto il
                    # controllo (sys.executable = .venv_zeno), non a un `pip`
                    # generico. Prima era `pip install --upgrade X
                    # --break-system-packages`: quel flag serve al Python di
                    # sistema gestito da Homebrew, quindi il comando aggiornava
                    # l'interprete sbagliato lasciando il venv com'era — e
                    # intanto toccava l'installazione Homebrew.
                    # shlex.quote: dopo la migrazione su disco interno il path
                    # del venv contiene spazi ("SLM Zeno"), e questo comando
                    # gira con shell=True in chat_ui.py — senza virgolette la
                    # shell lo spezzerebbe sul primo spazio.
                    'comando': (f'{shlex.quote(sys.executable)} -m pip install '
                                f'--upgrade {shlex.quote(pkg["name"])}')
                })
    except: pass
    return aggiornamenti

def controlla_aggiornamenti(forza=False):
    """Controlla aggiornamenti se l'ultimo check supera la frequenza configurata."""
    cfg        = _carica_cfg()
    if (cfg.get('rete') or {}).get('controllo_aggiornamenti', True) is False:
        return []
    cache_path = _cache_path(cfg)
    frequenza  = _frequenza_giorni(cfg)

    if not forza and os.path.exists(cache_path):
        try:
            cache = json.load(open(cache_path))
            ultimo = datetime.fromisoformat(cache.get('timestamp', '2000-01-01'))
            if datetime.now() - ultimo < timedelta(days=frequenza):
                return cache.get('aggiornamenti', [])
        except Exception:
            pass

    aggiornamenti = controlla_modelli_mlx(cfg) + controlla_librerie_python(cfg)
    cache = {'timestamp': datetime.now().isoformat(), 'aggiornamenti': aggiornamenti}
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[UpdateChecker] Errore salvataggio cache: {e}')
    return aggiornamenti

if __name__ == '__main__':
    forza = '--forza' in sys.argv
    risultati = controlla_aggiornamenti(forza=forza)
    if not risultati:
        print('[UpdateChecker] Tutto aggiornato.')
    else:
        print(f'[UpdateChecker] {len(risultati)} aggiornamenti disponibili:')
        for a in risultati:
            print(f'  • {a["nome"]}: {a.get("versione_attuale","?")} → {a.get("ultima_versione","?")}')
            print(f'    Comando: {a["comando"]}')
