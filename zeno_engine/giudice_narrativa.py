# Giudice locale delle asserzioni narrative: replica, in locale, il giudizio
# fatto una tantum il 28/09/2026 sulle 5.327 asserzioni di romanzi e poesia
# (stats/giudizi_narrativa/giudizi_claude_20260928.json): trama, tema,
# citazione o scheda da catalogo.
#
# Non è un LLM: una rete piccola (un solo strato nascosto) sugli embedding
# bge-m3 che Zeno calcola già (revisione_temi), più genere della fonte e tipo
# di asserzione. Misurato in validazione incrociata a 5 blocchi: 82% di
# accordo col giudizio di riferimento (trama 89/90 precisione/richiamo, tema 80/84, catalogo
# 78/71, citazione 32/21 — pochi esempi). Qwen3-4B non ci si avvicinava sul
# compito simile del filtro. Giudica in millisecondi.
#
# "catalogo" sposta l'asserzione tra le scartate: lo si fa solo sopra
# SOGLIA_CATALOGO, altrimenti diventa "tema" (tenerla costa meno che perderla).

import json
import os

import numpy as np

from zeno_engine import config, generi

CLASSI = ('trama', 'tema', 'citazione', 'catalogo')
GENERI_NARRATIVI = (generi.NARRATIVA_PROPRIA, generi.NARRATIVA_LETTA,
                    generi.POESIA_PROPRIA, generi.POESIA_LETTA)
KIND = ('claim', 'decision', 'definition', 'fact', 'hypothesis', 'intention',
        'position', 'question', '')
FILE_GIUDIZI = os.path.join(config.BRAIN, 'stats', 'giudizi_narrativa',
                            'giudizi_claude_20260928.json')
FILE_PESI = os.path.join(config.BRAIN, 'models', 'giudice_narrativa.npz')
SOGLIA_CATALOGO = 0.85


def _caratteristiche(vett: np.ndarray, genere: str, kind: str | None) -> np.ndarray:
    return np.concatenate([vett.astype(np.float32),
                           np.array([genere == g for g in GENERI_NARRATIVI], np.float32),
                           np.array([(kind or '') == k for k in KIND], np.float32)])


def _addestra(X: np.ndarray, y: np.ndarray, nascosti: int = 512, epoche: int = 40,
              lr: float = 1e-3, l2: float = 1e-3, seme: int = 0) -> dict:
    """MLP a uno strato nascosto, Adam, mini-batch da 64. Solo numpy."""
    rng = np.random.default_rng(seme)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    X = (X - mu) / sd
    P = [rng.normal(0, np.sqrt(2 / X.shape[1]), (X.shape[1], nascosti)).astype(np.float32),
         np.zeros(nascosti, np.float32),
         rng.normal(0, np.sqrt(2 / nascosti), (nascosti, len(CLASSI))).astype(np.float32),
         np.zeros(len(CLASSI), np.float32)]
    m = [np.zeros_like(p) for p in P]
    v = [np.zeros_like(p) for p in P]
    t = 0
    for _ in range(epoche):
        for blocco in np.array_split(rng.permutation(len(y)), max(1, len(y) // 64)):
            W1, b1, W2, b2 = P
            xb, Y = X[blocco], np.eye(len(CLASSI))[y[blocco]]
            H = np.maximum(0, xb @ W1 + b1)
            Z = H @ W2 + b2
            Z -= Z.max(1, keepdims=True)
            Pr = np.exp(Z)
            Pr /= Pr.sum(1, keepdims=True)
            G = (Pr - Y) / len(blocco)
            GH = (G @ W2.T) * (H > 0)
            grad = [xb.T @ GH + l2 * W1, GH.sum(0), H.T @ G + l2 * W2, G.sum(0)]
            t += 1
            for k in range(4):
                m[k] = 0.9 * m[k] + 0.1 * grad[k]
                v[k] = 0.999 * v[k] + 0.001 * grad[k] ** 2
                P[k] -= lr * (m[k] / (1 - 0.9 ** t)) / (np.sqrt(v[k] / (1 - 0.999 ** t)) + 1e-8)
    return {'mu': mu, 'sd': sd, 'W1': P[0], 'b1': P[1], 'W2': P[2], 'b2': P[3]}


def probabilita(pesi: dict, X: np.ndarray) -> np.ndarray:
    Xn = (X - pesi['mu']) / pesi['sd']
    Z = np.maximum(0, Xn @ pesi['W1'] + pesi['b1']) @ pesi['W2'] + pesi['b2']
    Z -= Z.max(1, keepdims=True)
    P = np.exp(Z)
    return P / P.sum(1, keepdims=True)


def _dati(con, cfg: dict) -> tuple[np.ndarray, np.ndarray]:
    from zeno_engine import modelli, revisione_temi
    with open(FILE_GIUDIZI, encoding='utf-8') as f:
        giudizi = json.load(f)
    ids, mat = revisione_temi.embedding(con, cfg, verboso=False)
    pos = {int(i): k for k, i in enumerate(ids)}
    # Le "catalogo" sono ormai tra le scartate, fuori da `assertions` e quindi
    # dalla cache degli embedding: i loro vettori si ricalcolano dal testo
    # salvato nel file dei giudizi (senza, il modello impara solo tre classi —
    # successo il 28/09/2026 al primo addestramento).
    mancanti = [g for g in giudizi if g['id'] not in pos]
    extra: dict[int, np.ndarray] = {}
    if mancanti:
        if not modelli.assicura_demone():
            raise RuntimeError('demone MLX non raggiungibile: servono gli embedding')
        nome = cfg['modelli']['embeddings']
        modelli.prepara_solo(cfg, nome)
        for k in range(0, len(mancanti), 128):
            blocco = mancanti[k:k + 128]
            vett = modelli.client().embed(model=nome, input=[g['testo'] for g in blocco])['embeddings']
            for g, v in zip(blocco, vett):
                v = np.asarray(v, dtype=np.float32)
                extra[g['id']] = v / (np.linalg.norm(v) + 1e-9)
    X, y = [], []
    for g in giudizi:
        vett = mat[pos[g['id']]] if g['id'] in pos else extra[g['id']]
        X.append(_caratteristiche(vett, g['genere'], g['kind']))
        y.append(CLASSI.index(g['giudizio']))
    return np.stack(X), np.array(y)


def addestra(con, cfg: dict, valuta: bool = True) -> dict:
    """Addestra sui giudizi di riferimento e salva i pesi in models/. Con `valuta`,
    stima prima l'accordo in validazione incrociata a 5 blocchi."""
    X, y = _dati(con, cfg)
    esito = {'esempi': int(len(y))}
    if valuta:
        perm = np.random.default_rng(0).permutation(len(y))
        pred = np.zeros(len(y), int)
        for blocco in np.array_split(perm, 5):
            resto = np.setdiff1d(perm, blocco)
            pred[blocco] = probabilita(_addestra(X[resto], y[resto]), X[blocco]).argmax(1)
        esito['accordo_cv'] = round(float((pred == y).mean()), 3)
    pesi = _addestra(X, y)
    os.makedirs(os.path.dirname(FILE_PESI), exist_ok=True)
    np.savez(FILE_PESI, **pesi)
    return esito


def giudica(con, cfg: dict, limite: int | None = None) -> dict[str, int]:
    """Giudica le asserzioni narrative ancora senza ruolo e scrive ruolo/voce;
    le schede da catalogo sicure vanno tra le scartate. Non fa commit."""
    from zeno_engine import filtro, revisione_temi
    if not os.path.exists(FILE_PESI):
        raise RuntimeError(f'Pesi mancanti ({FILE_PESI}): lanciare prima `zeno giudice --addestra`')
    pesi = dict(np.load(FILE_PESI))
    righe = con.execute(
        'SELECT a.id, a.kind, s.genere, s.path FROM assertions a JOIN chunks c ON c.id = a.chunk_id '
        'JOIN sources s ON s.id = c.source_id WHERE a.ruolo IS NULL '
        f"AND s.genere IN ({','.join('?' * len(GENERI_NARRATIVI))})", GENERI_NARRATIVI).fetchall()
    if limite:
        righe = righe[:limite]
    conta = {c: 0 for c in CLASSI}
    if not righe:
        return conta
    ids, mat = revisione_temi.embedding(con, cfg, verboso=False)
    pos = {int(i): k for k, i in enumerate(ids)}
    righe = [r for r in righe if r[0] in pos]
    X = np.stack([_caratteristiche(mat[pos[r[0]]], r[2], r[1]) for r in righe])
    P = probabilita(pesi, X)
    catalogo = []
    for (aid, _, genere, percorso), p in zip(righe, P):
        classe = CLASSI[int(p.argmax())]
        if classe == 'catalogo' and p[CLASSI.index('catalogo')] < SOGLIA_CATALOGO:
            classe = 'tema'
        conta[classe] += 1
        if classe == 'catalogo':
            catalogo.append((aid, 'catalogo'))
            continue
        con.execute('UPDATE assertions SET ruolo = ? WHERE id = ?', (classe, aid))
        if genere == generi.NARRATIVA_PROPRIA and generi.voce_narratore(percorso):
            con.execute("UPDATE assertions SET voce = 'narratore' WHERE id = ?", (aid,))
    filtro.scarta(con, catalogo, fonte='giudice_locale')
    return conta
