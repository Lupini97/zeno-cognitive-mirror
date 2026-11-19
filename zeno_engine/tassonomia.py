# Tassonomia dei temi: la decide l'utente (28/09/2026). Zeno propone
# (revisione_temi), lui crea, rinomina, sposta, descrive e ritira dalla scheda
# "Gestione temi" della dashboard (funzioni in fondo al file). Ogni tema ha una
# descrizione (topics.descrizione): la legge il classificatore per decidere
# cosa ci sta dentro, e compare nelle schede tema del vault e nel grafo.
#
# Una ristrutturazione intera si può anche scrivere in un file Markdown e
# applicare con `leggi` + `applica` (così è nata la tassonomia del 28/09/2026).
# Formato:
#   ## Tema madre (era: nome vecchio)     ← "(era: …)" solo per rinominare
#   descrizione del tema madre
#   - Figlio (era: nome vecchio): descrizione del figlio
#
# Un tema che sparisce dal file viene ritirato: i suoi legami passano al tema
# madre (se era un figlio) o si perdono (se era un tema madre: le sue
# asserzioni tornano nel limbo, da riclassificare).

import re

_ERA = re.compile(r'\s*\(era:\s*([^)]+)\)\s*')


def _nome_era(testo: str) -> tuple[str, str | None]:
    m = _ERA.search(testo)
    if not m:
        return testo.strip(), None
    return _ERA.sub(' ', testo).strip(), m.group(1).strip()


def leggi(percorso: str) -> list[dict]:
    """[{nome, era, madre, descrizione}] nell'ordine del file (madri prima dei figli)."""
    voci, madre = [], None
    with open(percorso, encoding='utf-8') as f:
        righe = [r.rstrip() for r in f]
    for r in righe:
        if r.startswith('## '):
            nome, era = _nome_era(r[3:])
            madre = {'nome': nome, 'era': era, 'madre': None, 'descrizione': ''}
            voci.append(madre)
        elif madre is None:
            continue                                # intestazione e istruzioni
        elif r.startswith('- '):
            senza_era, era = _nome_era(r[2:])      # "(era: …)" contiene anch'esso ':'
            nome, _, descr = senza_era.partition(':')
            nome = nome.strip()
            descr = descr.strip()
            voci.append({'nome': nome, 'era': era, 'madre': madre['nome'],
                         'descrizione': descr[:1].upper() + descr[1:]})
        elif r.strip() and not madre['descrizione']:
            madre['descrizione'] = r.strip()
    nomi = [v['nome'].lower() for v in voci]
    doppi = {n for n in nomi if nomi.count(n) > 1}
    if doppi:
        raise ValueError(f'Temi con lo stesso nome nel file: {", ".join(sorted(doppi))}')
    return voci


def piano(con, voci: list[dict]) -> dict:
    """Cosa cambierebbe applicando `voci`: nulla viene scritto."""
    esistenti = {nome.lower(): (tid, nome, parent) for tid, nome, parent in
                 con.execute('SELECT id, name, parent_id FROM topics')}
    per_id = {v[0]: v for v in esistenti.values()}
    # prima le rinomine esplicite, poi i nomi uguali non già presi da una rinomina
    id_di: dict[str, int | None] = {}
    presi = set()
    for v in voci:
        # "(era: …)" vale solo finché il nome nuovo non esiste: riapplicare lo
        # stesso file non deve rifare la rinomina
        if v['era'] and v['era'].lower() in esistenti and v['nome'].lower() not in esistenti:
            id_di[v['nome']] = esistenti[v['era'].lower()][0]
            presi.add(id_di[v['nome']])
    for v in voci:
        if v['nome'] not in id_di:
            e = esistenti.get(v['nome'].lower())
            id_di[v['nome']] = e[0] if e and e[0] not in presi else None
            if id_di[v['nome']]:
                presi.add(id_di[v['nome']])
    conta = dict(con.execute('SELECT topic_id, COUNT(*) FROM assertion_topics GROUP BY 1'))
    p = {'nuovi': [], 'rinominati': [], 'spostati': [], 'ritirati': [], 'id_di': id_di}
    for v in voci:
        tid = id_di[v['nome']]
        if tid is None:
            p['nuovi'].append((v['nome'], v['madre']))
            continue
        _, vecchio, parent = per_id[tid]
        if vecchio != v['nome']:
            p['rinominati'].append((vecchio, v['nome']))
        madre_prima = per_id[parent][1] if parent else None
        madre_dopo = v['madre']
        if (madre_prima or '').lower() != (madre_dopo or '').lower() and \
                not (madre_prima and madre_dopo and id_di.get(madre_dopo) == parent):
            p['spostati'].append((v['nome'], madre_prima, madre_dopo))
    for tid, (_, nome, parent) in per_id.items():
        if tid not in presi:
            p['ritirati'].append((nome, per_id[parent][1] if parent else None, conta.get(tid, 0)))
    return p


def applica(con, voci: list[dict]) -> dict:
    """Allinea `topics` al file. Non fa commit."""
    p = piano(con, voci)
    id_di = p['id_di']
    madre_di = {v['nome']: v['madre'] for v in voci}
    # 1. ritiri: i legami passano al tema madre di oggi (se è un figlio)
    presi = set(filter(None, id_di.values()))
    for tid, parent in con.execute('SELECT id, parent_id FROM topics').fetchall():
        if tid in presi:
            continue
        if parent:
            con.execute("INSERT OR IGNORE INTO assertion_topics(assertion_id, topic_id, fonte) "
                        "SELECT assertion_id, ?, fonte FROM assertion_topics WHERE topic_id = ?",
                        (parent, tid))
        con.execute('UPDATE assertions SET topic_id = ? WHERE topic_id = ?', (parent, tid))
        con.execute('UPDATE topics SET parent_id = NULL WHERE parent_id = ?', (tid,))
        con.execute('DELETE FROM assertion_topics WHERE topic_id = ?', (tid,))
        con.execute('DELETE FROM topics WHERE id = ?', (tid,))
    # 2. nomi: in due passi, perché uno scambio di nomi violerebbe UNIQUE
    for tid in presi:
        con.execute('UPDATE topics SET name = ? WHERE id = ?', (f'__tmp_{tid}', tid))
    for v in voci:
        if id_di[v['nome']]:
            con.execute('UPDATE topics SET name = ?, descrizione = ? WHERE id = ?',
                        (v['nome'], v['descrizione'], id_di[v['nome']]))
        else:
            id_di[v['nome']] = con.execute(
                'INSERT INTO topics(name, descrizione) VALUES (?, ?)',
                (v['nome'], v['descrizione'])).lastrowid
    # 3. genitori
    for v in voci:
        madre = madre_di[v['nome']]
        con.execute('UPDATE topics SET parent_id = ? WHERE id = ?',
                    (id_di[madre] if madre else None, id_di[v['nome']]))
    return {k: v for k, v in p.items() if k != 'id_di'}


def descrizioni(con) -> dict[str, str]:
    return {n: d for n, d in con.execute(
        "SELECT name, descrizione FROM topics WHERE COALESCE(descrizione, '') <> ''")}


# ── modifiche singole, dalla dashboard ──────────────────────────────────────

def _id(con, nome: str) -> int:
    r = con.execute('SELECT id FROM topics WHERE name = ? COLLATE NOCASE', (nome,)).fetchone()
    if not r:
        raise ValueError(f'Tema inesistente: {nome}')
    return r[0]


def _controlla_madre(con, madre: str | None, tid: int | None = None) -> int | None:
    if not madre:
        return None
    mid = _id(con, madre)
    if con.execute('SELECT parent_id FROM topics WHERE id = ?', (mid,)).fetchone()[0]:
        raise ValueError(f'«{madre}» è già un figlio: solo due livelli')
    if tid is not None and (mid == tid or con.execute(
            'SELECT 1 FROM topics WHERE parent_id = ?', (tid,)).fetchone()):
        raise ValueError('Un tema con figli non può diventare figlio')
    return mid


def crea(con, nome: str, descrizione: str, madre: str | None = None) -> int:
    nome = nome.strip()
    if not nome or not descrizione.strip():
        raise ValueError('Servono nome e descrizione')
    if con.execute('SELECT 1 FROM topics WHERE name = ? COLLATE NOCASE', (nome,)).fetchone():
        raise ValueError(f'Esiste già un tema «{nome}»')
    return con.execute('INSERT INTO topics(name, parent_id, descrizione) VALUES (?, ?, ?)',
                       (nome, _controlla_madre(con, madre), descrizione.strip())).lastrowid


def modifica(con, nome: str, nuovo_nome: str | None = None, descrizione: str | None = None,
             madre: str | None = '') -> None:
    """Rinomina, ridescrive o sposta (madre=None: diventa tema madre; '' = invariata)."""
    tid = _id(con, nome)
    if nuovo_nome and nuovo_nome.strip().lower() != nome.lower():
        if con.execute('SELECT 1 FROM topics WHERE name = ? COLLATE NOCASE',
                       (nuovo_nome.strip(),)).fetchone():
            raise ValueError(f'Esiste già un tema «{nuovo_nome}»')
    if nuovo_nome:
        con.execute('UPDATE topics SET name = ? WHERE id = ?', (nuovo_nome.strip(), tid))
    if descrizione is not None:
        con.execute('UPDATE topics SET descrizione = ? WHERE id = ?', (descrizione.strip(), tid))
    if madre != '':
        con.execute('UPDATE topics SET parent_id = ? WHERE id = ?',
                    (_controlla_madre(con, madre, tid), tid))


def ritira(con, nome: str) -> int:
    """Ritira un tema: le asserzioni di un figlio passano al tema madre, quelle
    di un tema madre (senza figli) tornano nel limbo. Ritorna i legami tolti."""
    tid = _id(con, nome)
    if con.execute('SELECT 1 FROM topics WHERE parent_id = ?', (tid,)).fetchone():
        raise ValueError('Prima ritira o sposta i suoi figli')
    parent = con.execute('SELECT parent_id FROM topics WHERE id = ?', (tid,)).fetchone()[0]
    if parent:
        con.execute("INSERT OR IGNORE INTO assertion_topics(assertion_id, topic_id, fonte) "
                    "SELECT assertion_id, ?, fonte FROM assertion_topics WHERE topic_id = ?",
                    (parent, tid))
    con.execute('UPDATE assertions SET topic_id = ? WHERE topic_id = ?', (parent, tid))
    n = con.execute('DELETE FROM assertion_topics WHERE topic_id = ?', (tid,)).rowcount
    con.execute('DELETE FROM topics WHERE id = ?', (tid,))
    return n
