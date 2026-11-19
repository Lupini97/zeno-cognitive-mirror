# Export Obsidian — il grafo di Zeno come vault di note con wikilink (brief §9).
#
# OUTPUT DERIVATO: la fonte di verità resta zeno.db; queste note si possono
# cancellare e rigenerare in ogni momento. Scrive SOLO nella sottocartella
# dedicata `obsidian_vault/Zeno/` (mai nelle note personali dell'utente), che
# viene svuotata e riscritta a ogni export. La visualizzazione grafica la fa
# Obsidian gratis con i backlink.
#
# Struttura:
#   Zeno/INDICE.md            — punto d'ingresso
#   Zeno/Temi/<topic>.md      — asserzioni per tema, con [[entità]] linkate
#   Zeno/Entità/<nome>.md     — una nota per entità citata ≥ MIN_MENZIONI volte
#   Zeno/Contraddizioni.md    — le tensioni rilevate dal Critico

import os
import re
import time
from datetime import datetime
from zeno_engine.config import BRAIN

VAULT_ZENO = os.path.join(BRAIN, 'obsidian_vault/Zeno')
MIN_MENZIONI = 2        # entità citate una volta sola: rumore, niente nota


def _nome_file(nome: str) -> str:
    """Nome nota valido per Obsidian/ExFAT (niente / \\ : * ? " < > | #)."""
    pulito = re.sub(r'[/\\:*?"<>|#^\[\]]', ' ', nome).strip()
    return re.sub(r'\s+', ' ', pulito)[:120] or 'senza nome'


def _wikilink(nome: str, cartella: str) -> str:
    return f'[[{cartella}/{_nome_file(nome)}|{nome}]]'


import unicodedata


def _rimuovi_exfat(percorso: str, directory: bool = False):
    """
    Rimozione a prova di ExFAT+nomi accentati: readdir restituisce NFD ma la
    entry su disco è NFC (o viceversa) → ENOENT sul nome letto, per file E
    directory ("Entità/"). Si ritenta con la normalizzazione alternativa.
    Gli AppleDouble ._* possono sparire da soli col file padre: ENOENT su
    di loro non è un errore.
    """
    rimuovi = os.rmdir if directory else os.unlink
    try:
        rimuovi(percorso)
        return
    except FileNotFoundError:
        pass
    d, nome = os.path.split(percorso)
    alt = unicodedata.normalize(
        'NFD' if unicodedata.is_normalized('NFC', nome) else 'NFC', nome)
    try:
        rimuovi(os.path.join(d, alt))
    except FileNotFoundError:
        if not nome.startswith('._'):
            raise


def _svuota(cartella: str, tentativi: int = 3):
    """Svuotamento della cartella derivata (rigenerabile), robusto su ExFAT."""
    for i in range(tentativi):
        try:
            for radice, cartelle, file in os.walk(cartella, topdown=False):
                for f in file:
                    _rimuovi_exfat(os.path.join(radice, f))
                for d in cartelle:
                    _rimuovi_exfat(os.path.join(radice, d), directory=True)
            os.rmdir(cartella)
            return
        except OSError:
            if i == tentativi - 1:
                raise
            time.sleep(0.5)


def esporta(con, cartella: str = VAULT_ZENO) -> dict:
    # Rigenerazione pulita: la cartella Zeno/ è interamente nostra.
    if os.path.isdir(cartella):
        _svuota(cartella)
    for sub in ('Temi', 'Entità'):
        os.makedirs(os.path.join(cartella, sub), exist_ok=True)
    oggi = datetime.now().strftime('%Y-%m-%d %H:%M')
    n_note = 0

    # Entità → asserzioni che le citano (solo entità con abbastanza menzioni)
    entita = con.execute("""
        SELECT en.id, en.name, en.kind, COUNT(*) AS menzioni FROM entities en
        JOIN edges e ON e.dst_type='entity' AND e.dst_id = en.id AND e.relation='mentions'
        GROUP BY en.id HAVING menzioni >= ? ORDER BY menzioni DESC""",
        (MIN_MENZIONI,)).fetchall()
    nomi_entita = {eid: nome for eid, nome, _, _ in entita}

    for eid, nome, kind, menzioni in entita:
        asserzioni = con.execute("""
            SELECT a.text, a.kind, a.asserted_at, s.title FROM edges e
            JOIN assertions a ON a.id = e.src_id
            JOIN chunks c ON c.id = a.chunk_id JOIN sources s ON s.id = c.source_id
            WHERE e.relation='mentions' AND e.dst_type='entity' AND e.dst_id = ?
            ORDER BY a.asserted_at""", (eid,)).fetchall()
        righe = [f'# {nome}', '', f'*{kind} · {menzioni} menzioni*', '']
        for testo, akind, data, fonte in asserzioni:
            righe.append(f'- ({data or "?"}) [{akind}] {testo}  — *{fonte[:60]}*')
        righe.append('')
        with open(os.path.join(cartella, 'Entità', _nome_file(nome) + '.md'),
                  'w', encoding='utf-8') as f:
            f.write('\n'.join(righe))
        n_note += 1

    # Temi (madri e figli) → descrizione, albero, asserzioni con wikilink alle
    # entità. La descrizione la scrive l'utente (tassonomia.py, 28/09/2026): è
    # la prima cosa che si legge aprendo il tema dal grafo.
    tutti = con.execute("""
        SELECT t.id, t.name, t.parent_id, COALESCE(t.descrizione, ''),
               COUNT(DISTINCT at.assertion_id), COUNT(DISTINCT c.source_id) FROM topics t
        LEFT JOIN assertion_topics at ON at.topic_id = t.id
        LEFT JOIN assertions a ON a.id = at.assertion_id
        LEFT JOIN chunks c ON c.id = a.chunk_id
        GROUP BY t.id ORDER BY 6 DESC""").fetchall()
    nome_di = {r[0]: r[1] for r in tutti}
    topics = [(tid, nome, n_ass) for tid, nome, parent, _, n_ass, _ in tutti if parent is None]
    for tid, nome, parent, descr, n_ass, n_doc in tutti:
        righe = [f'# {nome}', '']
        if descr:
            righe += [f'> {descr}', '']
        if parent:
            righe.append(f'Tema madre: {_wikilink(nome_di[parent], "Temi")}')
        figli = [n for t, n, p, *_ in tutti if p == tid]
        if figli:
            righe.append('Figli: ' + ', '.join(_wikilink(n, 'Temi') for n in figli))
        righe += ['', f'*{n_doc} documenti, {n_ass} asserzioni*', '']
        for aid, testo, akind, stance, data in con.execute("""
                SELECT a.id, a.text, a.kind, a.stance, a.asserted_at FROM assertions a
                JOIN assertion_topics at ON at.assertion_id = a.id
                WHERE at.topic_id = ? ORDER BY a.asserted_at""", (tid,)):
            citate = [nomi_entita[r[0]] for r in con.execute(
                """SELECT dst_id FROM edges WHERE relation='mentions'
                   AND src_type='assertion' AND src_id = ?""", (aid,))
                if r[0] in nomi_entita]
            link = ('  → ' + ', '.join(_wikilink(n, 'Entità') for n in citate)
                    if citate else '')
            righe.append(f'- ({data or "?"}) [{akind}/{stance}] {testo}{link}')
        righe.append('')
        with open(os.path.join(cartella, 'Temi', _nome_file(nome) + '.md'),
                  'w', encoding='utf-8') as f:
            f.write('\n'.join(righe))
        n_note += 1

    # Contraddizioni
    righe = ['# Contraddizioni rilevate', '']
    for a, da, b, db_, w, nota in con.execute("""
            SELECT a1.text, a1.asserted_at, a2.text, a2.asserted_at, e.weight, e.note
            FROM edges e
            JOIN assertions a1 ON a1.id = e.src_id
            JOIN assertions a2 ON a2.id = e.dst_id
            WHERE e.relation='contradicts' ORDER BY e.weight DESC"""):
        righe += [f'## Confidenza {w:.2f}',
                  f'- **{da or "?"}** — «{a}»', f'- **{db_ or "?"}** — «{b}»']
        if nota:
            righe.append(f'- *{nota}*')
        righe.append('')
    with open(os.path.join(cartella, 'Contraddizioni.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(righe))

    # Indice
    righe = [f'# Zeno — Grafo del pensiero', '', f'*Esportato il {oggi} da zeno.db '
             f'(fonte di verità; questo vault è derivato e rigenerabile).*', '',
             '## Temi']
    righe += [f'- {_wikilink(nome, "Temi")} ({n_ass})' for _, nome, n_ass in topics]
    righe += ['', f'## Entità più citate']
    righe += [f'- {_wikilink(nome, "Entità")} ({menzioni})'
              for _, nome, _, menzioni in entita[:30]]
    righe += ['', '- [[Zeno/Contraddizioni|Contraddizioni]]', '']
    with open(os.path.join(cartella, 'INDICE.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(righe))

    return {'note': n_note + 2, 'entita': len(entita), 'temi': len(topics)}
