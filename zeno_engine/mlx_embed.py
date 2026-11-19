# Encoder BGE-M3 (XLM-RoBERTa) nativo MLX — sostituisce `ollama.embed`.
#
# Perché scritto a mano invece di usare mlx-embeddings:
#   1. mlx-embeddings è GPL-3: vendorarlo cambierebbe la licenza di ZENO.
#   2. usa mean pooling, mentre BGE-M3 produce il vettore denso dal token CLS.
#      Con il pooling sbagliato i vettori NON sarebbero confrontabili con i
#      ~8.500 chunk già indicizzati in zeno.db (vec_chunks) e il retrieval
#      degraderebbe in silenzio — il modo peggiore di sbagliare.
#
# Contratto: `encode(testi) -> list[list[float]]`, vettori L2-normalizzati a
# 1024 dimensioni, identici (entro la precisione fp16) a quelli di Ollama.

from __future__ import annotations

import glob
import json
import math
import os

import mlx.core as mx
import mlx.nn as nn

# BGE-M3 accetta 8192 token, ma i chunk di ZENO stanno sotto i 1800 caratteri
# (~600 token): il tetto serve solo a impedire che un documento anomalo
# faccia esplodere la memoria dell'attenzione (quadratica nella lunghezza).
MAX_TOKEN = 8192


class _Strato(nn.Module):
    """Un blocco encoder XLM-RoBERTa: attenzione multi-testa + feed-forward."""

    def __init__(self, dim: int, teste: int, dim_intermedia: int, eps: float):
        super().__init__()
        self.teste = teste
        self.dim_testa = dim // teste
        self.scala = 1.0 / math.sqrt(self.dim_testa)
        self.q = nn.Linear(dim, dim)
        self.k = nn.Linear(dim, dim)
        self.v = nn.Linear(dim, dim)
        self.attn_out = nn.Linear(dim, dim)
        self.attn_ln = nn.LayerNorm(dim, eps=eps)
        self.inter = nn.Linear(dim, dim_intermedia)
        self.out = nn.Linear(dim_intermedia, dim)
        self.out_ln = nn.LayerNorm(dim, eps=eps)

    def __call__(self, x: mx.array, maschera: mx.array) -> mx.array:
        b, t, _ = x.shape
        forma = (b, t, self.teste, self.dim_testa)
        q = self.q(x).reshape(forma).transpose(0, 2, 1, 3)
        k = self.k(x).reshape(forma).transpose(0, 2, 1, 3)
        v = self.v(x).reshape(forma).transpose(0, 2, 1, 3)

        punteggi = (q @ k.transpose(0, 1, 3, 2)) * self.scala + maschera
        pesi = mx.softmax(punteggi.astype(mx.float32), axis=-1).astype(punteggi.dtype)
        contesto = (pesi @ v).transpose(0, 2, 1, 3).reshape(b, t, -1)

        x = self.attn_ln(x + self.attn_out(contesto))
        return self.out_ln(x + self.out(nn.gelu(self.inter(x))))


class ModelloBGE(nn.Module):
    """XLM-RoBERTa encoder. Il vettore denso è il token CLS normalizzato."""

    def __init__(self, cfg: dict):
        super().__init__()
        dim = cfg['hidden_size']
        eps = cfg.get('layer_norm_eps', 1e-5)
        self.pad_id = cfg.get('pad_token_id', 1)
        self.emb_word = nn.Embedding(cfg['vocab_size'], dim)
        self.emb_pos = nn.Embedding(cfg['max_position_embeddings'], dim)
        self.emb_type = nn.Embedding(cfg.get('type_vocab_size', 1), dim)
        self.emb_ln = nn.LayerNorm(dim, eps=eps)
        self.strati = [
            _Strato(dim, cfg['num_attention_heads'], cfg['intermediate_size'], eps)
            for _ in range(cfg['num_hidden_layers'])
        ]

    def __call__(self, ids: mx.array, maschera_pad: mx.array) -> mx.array:
        # XLM-RoBERTa numera le posizioni a partire da pad_token_id+1 e salta
        # i token di padding: usare un semplice arange darebbe embedding
        # posizionali sfasati di 2 e vettori diversi da quelli di riferimento.
        posizioni = mx.cumsum(maschera_pad.astype(mx.int32), axis=1) * maschera_pad.astype(mx.int32)
        posizioni = posizioni + self.pad_id

        x = (self.emb_word(ids)
             + self.emb_pos(posizioni)
             + self.emb_type(mx.zeros(ids.shape, dtype=mx.int32)))
        x = self.emb_ln(x)

        # Maschera additiva: -1e9 sui token di padding, così il softmax li azzera.
        additiva = (1.0 - maschera_pad[:, None, None, :].astype(mx.float32)) * -1e9
        additiva = additiva.astype(x.dtype)
        for strato in self.strati:
            x = strato(x, additiva)
        return x


# ── Caricamento pesi ─────────────────────────────────────────────────────────

def _mappa_chiave(k: str) -> str | None:
    """Traduce i nomi HuggingFace nella struttura di questo modulo."""
    k = k.removeprefix('model.')
    if k.startswith('pooler.'):
        return None            # BGE-M3 non usa il pooler denso per il vettore
    sostituzioni = [
        ('embeddings.word_embeddings', 'emb_word'),
        ('embeddings.position_embeddings', 'emb_pos'),
        ('embeddings.token_type_embeddings', 'emb_type'),
        ('embeddings.LayerNorm', 'emb_ln'),
        ('encoder.layer.', 'strati.'),
        ('.attention.self.query.', '.q.'),
        ('.attention.self.key.', '.k.'),
        ('.attention.self.value.', '.v.'),
        ('.attention.output.dense.', '.attn_out.'),
        ('.attention.output.LayerNorm.', '.attn_ln.'),
        ('.intermediate.dense.', '.inter.'),
        ('.output.dense.', '.out.'),
        ('.output.LayerNorm.', '.out_ln.'),
    ]
    for vecchio, nuovo in sostituzioni:
        k = k.replace(vecchio, nuovo)
    return k


class Embedder:
    """Modello + tokenizer residenti. Va istanziato una volta sola."""

    def __init__(self, percorso: str):
        with open(os.path.join(percorso, 'config.json'), encoding='utf-8') as f:
            cfg = json.load(f)
        self.modello = ModelloBGE(cfg)

        pesi = {}
        for file in sorted(glob.glob(os.path.join(percorso, '*.safetensors'))):
            for chiave, valore in mx.load(file).items():
                nuova = _mappa_chiave(chiave)
                if nuova:
                    pesi[nuova] = valore
        self.modello.load_weights(list(pesi.items()))
        self.modello.eval()
        mx.eval(self.modello.parameters())

        from transformers import AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(percorso)
        self.pad_id = cfg.get('pad_token_id', 1)
        self.dim = cfg['hidden_size']

    def encode(self, testi: list[str], batch: int = 8) -> list[list[float]]:
        vettori: list[list[float]] = []
        for inizio in range(0, len(testi), batch):
            gruppo = testi[inizio:inizio + batch]
            codificati = self.tokenizer(gruppo, truncation=True, max_length=MAX_TOKEN)['input_ids']
            lungo = max(len(c) for c in codificati)
            ids = mx.array([c + [self.pad_id] * (lungo - len(c)) for c in codificati])
            maschera = mx.array([[1] * len(c) + [0] * (lungo - len(c)) for c in codificati])

            stati = self.modello(ids, maschera)
            cls = stati[:, 0, :].astype(mx.float32)
            cls = cls / mx.maximum(mx.linalg.norm(cls, axis=-1, keepdims=True), 1e-9)
            mx.eval(cls)
            vettori.extend(cls.tolist())
            # I chunk arrivano a migliaia: senza svuotare la cache Metal la
            # memoria cresce fino a far scattare lo swap sull'M3 da 16 GB.
            mx.clear_cache()
        return vettori
