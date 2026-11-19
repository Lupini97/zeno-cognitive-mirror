# Classificatore locale dei temi: dal 28/09/2026 sostituisce il router Qwen in
# indicizzatore.assegna_topics. Replica, in locale, la prima indicizzazione
# di riferimento (lo strumento una tantum etichette_temi.py è stato tolto il
# 29/09/2026: resta nella storia git) e segue i temi come l'utente li cambia
# dalla dashboard: si riaddestra da solo (circa 30 secondi) quando cambiano.
#
# Non è un LLM: una rete piccola (uno strato nascosto, un'uscita per tema) sugli
# embedding bge-m3 dell'asserzione e del CENTRO DEL SUO DOCUMENTO. Il centro
# conta: la prima indicizzazione ha letto documento per documento, e una frase isolata ("il
# comandante decide in fretta") non dice di che tema è. Misurato in validazione
# incrociata a 5 blocchi divisi per documento (28/09/2026, 27.810 asserzioni
# da 1.371 documenti), accordo con l'indicizzazione di riferimento sul tema madre:
#   87% delle asserzioni (90% pesando ogni documento uguale);
#   sopra SOGLIA ne classifica il 91%, con il 91% di accordo; il resto al limbo;
#   figli: precisione 79%, richiamo 50% (nel dubbio resta il solo tema madre).
# Senza il centro del documento: 63%. k-NN sulle asserzioni etichettate: 64%.
# Il vecchio router Qwen3-4B, su 60 asserzioni a caso: 35 giuste (58%) a circa
# 15 secondi l'una; il classificatore sulle stesse 60: 54 (90%).
#
# Un tema appena creato non ha esempi, quindi la rete non lo conosce: prima lo
# si semina (revisione_temi propone i documenti più vicini alla descrizione,
# L'utente sceglie), poi al riaddestramento successivo entra nel classificatore.

import os
from datetime import datetime

import numpy as np

from zeno_engine import config

FILE_PESI = os.path.join(config.BRAIN, 'models', 'classificatore_temi.npz')
SOGLIA = 0.5          # probabilità minima di un tema madre; sotto, limbo
SOGLIA_FIGLIO = 0.5
ESEMPI_MIN = 5        # asserzioni perché un tema entri nel classificatore


def _embedding(con, cfg: dict):
    from zeno_engine import revisione_temi
    ids, mat = revisione_temi.embedding(con, cfg, verboso=False)
    return {int(i): k for k, i in enumerate(ids)}, mat


def _centri(con, pos: dict, mat: np.ndarray) -> dict[int, np.ndarray]:
    """Centro (normalizzato) delle asserzioni non di trama di ogni documento."""
    per_doc: dict[int, list[int]] = {}
    for aid, sid in con.execute("SELECT a.id, c.source_id FROM assertions a "
                                "JOIN chunks c ON c.id = a.chunk_id "
                                "WHERE COALESCE(a.ruolo, '') <> 'trama'"):
        if aid in pos:
            per_doc.setdefault(sid, []).append(pos[aid])
    out = {}
    for sid, righe in per_doc.items():
        c = mat[righe].mean(0)
        out[sid] = c / (np.linalg.norm(c) + 1e-9)
    return out


def _caratteristiche(con, aids: list[int], pos: dict, mat: np.ndarray,
                     centri: dict) -> np.ndarray:
    fonte = dict(con.execute(
        f"SELECT a.id, c.source_id FROM assertions a JOIN chunks c ON c.id = a.chunk_id "
        f"WHERE a.id IN ({','.join('?' * len(aids))})", aids))
    righe = [pos[a] for a in aids]
    return np.hstack([mat[righe], np.stack([centri[fonte[a]] for a in aids])])


def _temi_addestrabili(con) -> list[int]:
    return [t for (t,) in con.execute(
        "SELECT at.topic_id FROM assertion_topics at JOIN assertions a ON a.id = at.assertion_id "
        "WHERE COALESCE(a.ruolo, '') <> 'trama' GROUP BY at.topic_id "
        "HAVING COUNT(*) >= ? ORDER BY at.topic_id", (ESEMPI_MIN,))]


def _rete(X: np.ndarray, Y: np.ndarray, nascosti: int = 512, epoche: int = 30,
          lr: float = 1e-3, l2: float = 1e-4, seme: int = 0) -> dict:
    """MLP a uno strato nascosto, uscite sigmoidi (un'asserzione può stare in
    più temi), Adam, mini-batch da 64. Solo numpy."""
    rng = np.random.default_rng(seme)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    X = (X - mu) / sd
    P = [rng.normal(0, np.sqrt(2 / X.shape[1]), (X.shape[1], nascosti)).astype(np.float32),
         np.zeros(nascosti, np.float32),
         rng.normal(0, np.sqrt(2 / nascosti), (nascosti, Y.shape[1])).astype(np.float32),
         np.zeros(Y.shape[1], np.float32)]
    m = [np.zeros_like(p) for p in P]
    v = [np.zeros_like(p) for p in P]
    t = 0
    for _ in range(epoche):
        for b in np.array_split(rng.permutation(len(Y)), max(1, len(Y) // 64)):
            W1, b1, W2, b2 = P
            H = np.maximum(0, X[b] @ W1 + b1)
            dZ = (1 / (1 + np.exp(-np.clip(H @ W2 + b2, -30, 30))) - Y[b]) / len(b)
            dH = (dZ @ W2.T) * (H > 0)
            g = [X[b].T @ dH + l2 * W1, dH.sum(0), H.T @ dZ + l2 * W2, dZ.sum(0)]
            t += 1
            for k in range(4):
                m[k] = 0.9 * m[k] + 0.1 * g[k]
                v[k] = 0.999 * v[k] + 0.001 * g[k] ** 2
                P[k] -= lr * (m[k] / (1 - 0.9 ** t)) / (np.sqrt(v[k] / (1 - 0.999 ** t)) + 1e-8)
    return {'W1': P[0], 'b1': P[1], 'W2': P[2], 'b2': P[3], 'mu': mu, 'sd': sd}


def _probabilita(pesi, X: np.ndarray) -> np.ndarray:
    H = np.maximum(0, ((X - pesi['mu']) / pesi['sd']) @ pesi['W1'] + pesi['b1'])
    return 1 / (1 + np.exp(-np.clip(H @ pesi['W2'] + pesi['b2'], -30, 30)))


def addestra(con, cfg: dict, verboso: bool = False) -> dict:
    """Addestra sui legami attuali (tutti: indicizzazione di riferimento, proposte accettate, semina,
    classificatore) e salva i pesi. Ritorna {'asserzioni', 'temi'}."""
    pos, mat = _embedding(con, cfg)
    temi = _temi_addestrabili(con)
    col = {t: k for k, t in enumerate(temi)}
    legami: dict[int, list[int]] = {}
    for aid, tid in con.execute(
            "SELECT at.assertion_id, at.topic_id FROM assertion_topics at "
            "JOIN assertions a ON a.id = at.assertion_id WHERE COALESCE(a.ruolo, '') <> 'trama'"):
        if aid in pos and tid in col:
            legami.setdefault(aid, []).append(col[tid])
    aids = sorted(legami)
    if not aids or not temi:
        # Archivio nuovo: nessun tema con asserzioni da cui imparare. Niente
        # pesi, e classifica() lascia tutto nel limbo finché non ci sono.
        return {'asserzioni': 0, 'temi': len(temi)}
    X = _caratteristiche(con, aids, pos, mat, _centri(con, pos, mat))
    Y = np.zeros((len(aids), len(temi)), np.float32)
    for r, a in enumerate(aids):
        Y[r, legami[a]] = 1
    if verboso:
        print(f'  classificatore temi: {len(aids)} asserzioni, {len(temi)} temi')
    pesi = _rete(X, Y)
    os.makedirs(os.path.dirname(FILE_PESI), exist_ok=True)
    np.savez(FILE_PESI, temi=np.array(temi), addestrato=datetime.now().isoformat(timespec='seconds'),
             **pesi)
    return {'asserzioni': len(aids), 'temi': len(temi)}


def _pesi(con, cfg: dict):
    """Pesi aggiornati: riaddestra se mancano o se i temi sono cambiati
    (creati, ritirati, seminati) dall'ultimo addestramento."""
    if os.path.exists(FILE_PESI):
        pesi = dict(np.load(FILE_PESI))
        if pesi['temi'].tolist() == _temi_addestrabili(con):
            return pesi
    if not addestra(con, cfg)['asserzioni']:
        return None
    return dict(np.load(FILE_PESI))


def classifica(con, cfg: dict, aids: list[int]) -> dict[int, list[int]]:
    """{assertion_id: [temi]}: il primo è il tema madre principale, poi le
    altre madri e i figli sopra soglia. Lista vuota = limbo."""
    if not aids:
        return {}
    pesi = _pesi(con, cfg)
    if pesi is None:          # nessun esempio per addestrare: tutto nel limbo
        return {}
    pos, mat = _embedding(con, cfg)
    aids = [a for a in aids if a in pos]
    if not aids:
        return {}
    temi = pesi['temi'].tolist()
    madre = dict(con.execute('SELECT id, COALESCE(parent_id, id) FROM topics'))
    Pr = _probabilita(pesi, _caratteristiche(con, aids, pos, mat, _centri(con, pos, mat)))
    out = {}
    for a, p in zip(aids, Pr):
        madri = sorted(((p[k], t) for k, t in enumerate(temi)
                        if madre.get(t) == t and p[k] >= SOGLIA), reverse=True)
        scelte = [t for _, t in madri]
        scelte += [t for k, t in enumerate(temi) if t in madre and madre[t] != t
                   and madre[t] in scelte and p[k] >= SOGLIA_FIGLIO]
        out[a] = scelte
    return out
