# Scarica i pesi MLX — prende il posto di `ollama pull`.
#
#   python -m zeno_engine.mlx_scarica              # tutti i modelli di config
#   python -m zeno_engine.mlx_scarica <repo> ...   # solo quelli indicati
#
# Perché non `huggingface_hub.snapshot_download`: su questa linea (misurata il
# 2026-09-05 a ~165 KB/s in uscita, meno verso HuggingFace) il download si
# ferma a metà e OGNI nuovo tentativo di snapshot_download riparte da zero,
# perché si crea un file .incomplete diverso. Su un modello da 1 GB significa
# non finire mai. Qui la ripresa è esplicita: header Range sui byte già scritti,
# ritentativi finché il file non è completo. Interrompere e rilanciare è sicuro.
#
# I pesi finiscono in BRAIN/models/mlx/locale/<nome-repo>/, che mlx_motore
# preferisce alla cache HuggingFace: da lì si lavora anche senza rete.

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.error
import urllib.request

from zeno_engine import config as _config
from zeno_engine.mlx_motore import CACHE_LOCALE, cartella_locale

BLOCCO = 1 << 20          # 1 MiB per scrittura: ripresa a grana fine
TIMEOUT = 30              # una connessione muta oltre 30 s è morta, non lenta
TENTATIVI = 200           # con stalli ogni pochi MB servono molti riagganci

# File che non servono al motore e pesano (pesi PyTorch, ONNX, duplicati).
IGNORATI = ('.bin', '.pth', '.onnx', '.h5', '.msgpack', '.ot')


def modelli_di_config() -> list[str]:
    cfg = _config.load()
    ordine = ('embeddings', 'router', 'motore_ask', 'motore_batch')
    return [cfg['modelli'][k] for k in ordine if cfg['modelli'].get(k)]


def _file_del_repo(repo: str) -> list[str]:
    from huggingface_hub import list_repo_files
    return [f for f in list_repo_files(repo)
            if not f.endswith(IGNORATI) and not f.startswith('.')]


def _dimensione_remota(url: str) -> int | None:
    richiesta = urllib.request.Request(url, method='HEAD')
    try:
        with urllib.request.urlopen(richiesta, timeout=TIMEOUT) as r:
            return int(r.headers.get('Content-Length') or 0) or None
    except OSError:
        return None


def scarica_file(url: str, destinazione: str, verboso: bool = True) -> bool:
    """Scarica `url` in `destinazione` riprendendo da dove si era interrotto."""
    os.makedirs(os.path.dirname(destinazione), exist_ok=True)
    totale = _dimensione_remota(url)

    for tentativo in range(TENTATIVI):
        fatti = os.path.getsize(destinazione) if os.path.exists(destinazione) else 0
        if totale and fatti >= totale:
            return True

        richiesta = urllib.request.Request(url)
        if fatti:
            richiesta.add_header('Range', f'bytes={fatti}-')
        try:
            with urllib.request.urlopen(richiesta, timeout=TIMEOUT) as r:
                if fatti and r.status != 206:
                    # Il server ignora Range: si riparte pulito, una volta sola.
                    os.remove(destinazione)
                    continue
                if totale is None:
                    totale = fatti + int(r.headers.get('Content-Length') or 0)
                with open(destinazione, 'ab') as f:
                    while blocco := r.read(BLOCCO):
                        f.write(blocco)
                        fatti += len(blocco)
                        if verboso and totale and fatti % (16 * BLOCCO) == 0:
                            print(f'    {fatti // BLOCCO}/{totale // BLOCCO} MiB',
                                  flush=True)
            if totale is None or fatti >= totale:
                return True
        except (OSError, urllib.error.HTTPError):
            pass                                  # stallo o caduta: si riprende
        if verboso and tentativo and tentativo % 10 == 0:
            print(f'    ripresa #{tentativo} a {fatti // BLOCCO} MiB', flush=True)
        time.sleep(2)

    return bool(totale) and os.path.exists(destinazione) and \
        os.path.getsize(destinazione) >= totale


def scarica(repo: str, verboso: bool = True) -> bool:
    destinazione = cartella_locale(repo)
    completo = True
    for nome in _file_del_repo(repo):
        url = f'https://huggingface.co/{repo}/resolve/main/{nome}'
        percorso = os.path.join(destinazione, nome)
        if verboso:
            print(f'  · {nome}', flush=True)
        if not scarica_file(url, percorso, verboso=verboso):
            print(f'  ✗ {nome}: incompleto')
            completo = False
    return completo


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Scarica i modelli MLX di ZENO')
    p.add_argument('repo', nargs='*', help='repo HuggingFace (default: quelli in config)')
    args = p.parse_args(argv)

    elenco = args.repo or modelli_di_config()
    print(f'Cartella modelli: {CACHE_LOCALE}')
    errori = 0
    for repo in elenco:
        if repo.startswith('/'):
            print(f'· {repo}: percorso locale, niente da scaricare')
            continue
        print(f'· {repo}', flush=True)
        try:
            if scarica(repo):
                print(f'  ok → {cartella_locale(repo)}')
            else:
                errori += 1
        except Exception as e:                       # noqa: BLE001
            print(f'  FALLITO: {type(e).__name__}: {e}')
            errori += 1
    return 1 if errori else 0


if __name__ == '__main__':
    sys.exit(main())
