# Reranker bge-reranker-v2-m3 nativo MLX (28/09/2026). Opzionale, spento di
# serie: si accende con motore_epistemico.retrieval.reranker in zeno_config.yaml.
#
# È un cross-encoder: legge domanda e frammento INSIEME e dà la probabilità che
# il frammento risponda. Stesso XLM-RoBERTa large di bge-m3 (mlx_embed.ModelloBGE)
# più la testa di classificazione sul token CLS: dense 1024→1024, tanh,
# out_proj 1024→1. Validato su un esempio del README ufficiale (logit 5,27
# contro ~5,3). I pesi in models/mlx/locale/BAAI__bge-reranker-v2-m3 sono già
# convertiti in fp16 (1,1 GB).
#
# Misura sulle 200 domande sintetiche (zeno valuta recupero): con 20 candidati
# a 512 token la fonte giusta è prima nell'84,5% dei casi contro il 48,5% del
# recupero senza reranker, fra le prime 5 nel 94% contro l'87,5%; 4,1 s a
# domanda (mediana), 1,1 GB in RAM.

from __future__ import annotations

import glob
import json
import os

import mlx.core as mx
import mlx.nn as nn

from zeno_engine.mlx_embed import ModelloBGE, _mappa_chiave

MAX_TOKEN = 512


class _Testa(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.dense = nn.Linear(dim, dim)
        self.out_proj = nn.Linear(dim, 1)

    def __call__(self, cls: mx.array) -> mx.array:
        return self.out_proj(mx.tanh(self.dense(cls)))


class _Modello(nn.Module):
    def __init__(self, cfg: dict):
        super().__init__()
        self.encoder = ModelloBGE(cfg)
        self.testa = _Testa(cfg['hidden_size'])

    def __call__(self, ids: mx.array, maschera: mx.array) -> mx.array:
        return self.testa(self.encoder(ids, maschera)[:, 0, :])[:, 0]


class Reranker:
    """Modello + tokenizer residenti. Va istanziato una volta sola."""

    def __init__(self, percorso: str, max_token: int = MAX_TOKEN):
        with open(os.path.join(percorso, 'config.json'), encoding='utf-8') as f:
            cfg = json.load(f)
        self.modello = _Modello(cfg)
        pesi = []
        for file in sorted(glob.glob(os.path.join(percorso, '*.safetensors'))):
            for k, v in mx.load(file).items():
                v = v.astype(mx.float16)
                if k.startswith('classifier.'):
                    pesi.append(('testa.' + k.removeprefix('classifier.'), v))
                else:
                    nuova = _mappa_chiave(k.removeprefix('roberta.'))
                    if nuova:
                        pesi.append(('encoder.' + nuova, v))
        self.modello.load_weights(pesi)
        self.modello.eval()
        mx.eval(self.modello.parameters())
        from transformers import AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(percorso)
        self.pad_id = cfg.get('pad_token_id', 1)
        self.max_token = max_token

    def punteggi(self, domanda: str, testi: list[str], batch: int = 8) -> list[float]:
        """Probabilità di pertinenza (sigmoide del logit), una per testo."""
        fuori: list[float] = []
        for inizio in range(0, len(testi), batch):
            gruppo = testi[inizio:inizio + batch]
            cod = self.tokenizer([domanda] * len(gruppo), gruppo,
                                 truncation='only_second',
                                 max_length=self.max_token)['input_ids']
            lungo = max(len(c) for c in cod)
            ids = mx.array([c + [self.pad_id] * (lungo - len(c)) for c in cod])
            maschera = mx.array([[1] * len(c) + [0] * (lungo - len(c)) for c in cod])
            p = mx.sigmoid(self.modello(ids, maschera).astype(mx.float32))
            mx.eval(p)
            fuori.extend(p.tolist())
            mx.clear_cache()
        return fuori
