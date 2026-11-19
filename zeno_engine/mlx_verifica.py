# Verifica che gli embeddings MLX siano compatibili con quelli già in zeno.db.
#
#   python -m zeno_engine.mlx_verifica [--campione 50]
#
# Perché serve: gli 8.448 vettori dei chunk sono stati prodotti da bge-m3 via
# Ollama. Il repo MLX ha gli STESSI pesi in fp16, ma un'implementazione diversa
# può differire nel pooling (CLS vs media) o nella normalizzazione — e in quel
# caso i vettori nuovi non sarebbero confrontabili con i vecchi. Il retrieval
# peggiorerebbe SENZA errori: il modo peggiore di sbagliare.
#
# Il test ri-calcola l'embedding di N chunk già indicizzati e misura il coseno
# con il vettore salvato. Soglia di accettazione 0,999: sotto, i vettori vanno
# rifatti tutti (zeno indicizza dopo aver cancellato vec_chunks).

from __future__ import annotations

import argparse
import struct
import sys

from zeno_engine import config as _config
from zeno_engine import db as _db
from zeno_engine import indicizzatore as _indicizzatore

SOGLIA = 0.999


def _da_f32(dati: bytes) -> list[float]:
    return list(struct.unpack(f'{len(dati) // 4}f', dati))


def _coseno(a: list[float], b: list[float]) -> float:
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if not na or not nb:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def esegui(campione: int = 50, verboso: bool = True) -> dict:
    cfg = _config.load()
    con = _db.connect(cfg['db'], vec=True)
    try:
        righe = con.execute(
            'SELECT c.id, s.title, c.section, c.text, v.embedding '
            'FROM vec_chunks v JOIN chunks c ON c.id = v.chunk_id '
            'JOIN sources s ON s.id = c.source_id '
            'ORDER BY c.id LIMIT ?', (campione,)).fetchall()
    finally:
        con.close()

    if not righe:
        return {'esito': 'nessun vettore da confrontare', 'confrontati': 0}

    # L'embedding va calcolato sullo STESSO testo di allora: titolo e sezione
    # entrano nel vettore (vedi indicizzatore.testo_da_embeddare).
    testi = [_indicizzatore.testo_da_embeddare(t, sez, txt)[:6000]
             for _, t, sez, txt, _ in righe]

    from zeno_engine.mlx_motore import percorso
    from zeno_engine.mlx_embed import Embedder
    embedder = Embedder(percorso(cfg['modelli']['embeddings']))
    nuovi = embedder.encode(testi)

    somiglianze = [_coseno(_da_f32(riga[4]), nuovo)
                   for riga, nuovo in zip(righe, nuovi)]
    minimo = min(somiglianze)
    medio = sum(somiglianze) / len(somiglianze)

    if verboso:
        print(f'chunk confrontati: {len(somiglianze)}')
        print(f'coseno medio:  {medio:.6f}')
        print(f'coseno minimo: {minimo:.6f}')
        peggiori = sorted(zip(somiglianze, (r[0] for r in righe)))[:5]
        for valore, chunk_id in peggiori:
            print(f'  chunk {chunk_id}: {valore:.6f}')
        if minimo >= SOGLIA:
            print(f'\nOK — i vettori in zeno.db restano validi (soglia {SOGLIA}).')
        else:
            print(f'\nINCOMPATIBILI — sotto la soglia {SOGLIA}. Vanno rifatti tutti:\n'
                  '  DELETE FROM meta WHERE key = \'embedding_model\';\n'
                  '  poi `zeno indicizza` (ricrea vec_chunks da zero).')

    return {'confrontati': len(somiglianze), 'medio': medio, 'minimo': minimo,
            'compatibile': minimo >= SOGLIA}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Confronta embeddings MLX e vettori salvati')
    p.add_argument('--campione', type=int, default=50)
    args = p.parse_args(argv)
    esito = esegui(args.campione)
    return 0 if esito.get('compatibile') else 1


if __name__ == '__main__':
    sys.exit(main())
