# Filtro delle asserzioni da catalogo: dati bibliografici, anagrafici, voti,
# stato di lettura, "il documento descrive…". Non sono pensiero, eppure il
# router doveva comunque dar loro un tema — e finivano nel limbo o nel tema
# sbagliato (revisione a mano dell'utente, 25/09/2026: circa 1 asserzione su 10
# del campione era di questo tipo; "il libro è dell'utente Modesto… a che serve
# a Zeno questa asserzione").
#
# Le asserzioni scartate NON si cancellano e NON si marcano: si SPOSTANO in
# `asserzioni_scartate`, con i loro legami ai temi e agli archi. Marcarle
# avrebbe voluto dire correggere ~54 query in 20 file (e dimenticarne una
# basta a far rientrare la spazzatura); spostate, spariscono ovunque in un
# colpo solo. `ripristina()` le rimette esattamente dov'erano.
#
# Le regole valgono SOLO per le fonti di strato `acquisito` (libri, articoli,
# formazione): il pensiero dell'utente (esplicito, dialogico) non si filtra mai
# con un'espressione regolare.

import json
import re
from datetime import datetime

from zeno_engine.config import nome_utente

STRATI_FILTRATI = ('acquisito',)

# Tarate sulla revisione a mano dell'utente (27/09/2026, 150 asserzioni giudicate):
# precisione 42/43 (98%), prendono 42 delle 63 inutili; le altre sono trama,
# generi, dettagli di contorno e restano al giudizio del modello. Sul DB
# (strato acquisito) colpiscono 1.179 asserzioni.
# Tolte dopo la revisione, perché l'utente le ha giudicate UTILI:
#   anagrafica (date di nascita/morte di autori, cattedre): 6 su 8 utili;
#   meta_documento ("il libro descrive/tratta…"): 3 su 4 utili.
# `voto` è volutamente stretta: "il voto" da solo prendeva anche il voto
# politico ("la partecipazione al voto").
REGOLE = {
    'voto': (r"\bmi[ol] voto\b|\bvoto (del|al|di questo) libro\b"
             r"|\bvalutazione (di|del) (" + re.escape(nome_utente().lower()) + r"|goodreads)"
             r"|\bstelle\b.*goodreads"),
    'goodreads': r"goodreads|\bedizione\b.*(disponibil|altra)|\bISBN\b",
    'stato_lettura': (r"(il libro|la lettura) (è stat[oa]|risulta) (complet|lett|terminat|iniziat)"
                      r"|\bda leggere\b|\bin lettura\b"
                      r"|^il libro\b.{0,120}\bè stato (completato|letto)\b"),
    'biblio': (r"\(\d{4}\)\s*,|\btrad\.\s*it\b|\bed\.\s*\w+"
               r"|,\s*(Einaudi|Mondadori|Feltrinelli|Adelphi|Laterza|FrancoAngeli|Il Mulino"
               r"|Bompiani|Rizzoli|Armando|Etas)\b"
               r"|^[A-Z][\w'’-]+ ([A-Z]\.\s?){1,3},"          # "Perkinson H.J., Titolo…"
               r"|,\s*(Milano|Roma|Torino|Bologna|Firenze|Napoli|Bari|Padova|Venezia)[,\s]+(19|20)\d\d\b"),
    # Queste due si appoggiano alla maiuscola del nome proprio: niente IGNORECASE.
    'autore_scrive': r"^[A-Z][\w'’.-]+( [A-Z][\w'’.-]+)* (ha scritto|è l'autore|è autore|ha pubblicato)\b",
    'nazionalita': (r"^[A-Z][\w'’.-]+( [A-Z][\w'’.-]+)* (era|è) (francese|italian[oa]|russ[oa]"
                    r"|inglese|american[oa]|tedesc[oa]|spagnol[oa]|giapponese)\.?$"),
}
_SENSIBILI_MAIUSCOLE = ('autore_scrive', 'nazionalita')
_COMPILATE = {k: re.compile(v, 0 if k in _SENSIBILI_MAIUSCOLE else re.I)
              for k, v in REGOLE.items()}

# La tabella `asserzioni_scartate` è nello SCHEMA di db.py: la crea connect().


def motivo_regola(testo: str) -> str | None:
    """Nome della prima regola che colpisce `testo`, o None."""
    for nome, rx in _COMPILATE.items():
        if rx.search(testo):
            return nome
    return None


def candidati_regole(con) -> list[tuple[int, str]]:
    """(id, regola) delle asserzioni oggi in `assertions` che le regole scartano."""
    righe = con.execute(
        'SELECT a.id, a.text FROM assertions a JOIN chunks c ON c.id = a.chunk_id '
        'JOIN sources s ON s.id = c.source_id '
        f"WHERE s.strato IN ({','.join('?' * len(STRATI_FILTRATI))})",
        STRATI_FILTRATI).fetchall()
    fuori = []
    for aid, testo in righe:
        m = motivo_regola(testo)
        if m:
            fuori.append((aid, m))
    return fuori


def _colonne_archi(con) -> list[str]:
    return [r[1] for r in con.execute('PRAGMA table_info(edges)')]


def scarta(con, voci: list[tuple[int, str]], fonte: str) -> int:
    """Sposta le asserzioni `voci` = [(id, motivo), ...] in asserzioni_scartate,
    con i legami ai temi e gli archi. Non fa commit: decide il chiamante."""
    colonne_archi = _colonne_archi(con)
    adesso = datetime.now().isoformat(timespec='seconds')
    n = 0
    for aid, motivo in voci:
        riga = con.execute(
            'SELECT id, chunk_id, text, kind, stance, topic_id, confidence, asserted_at, modello, '
            'ruolo, voce FROM assertions WHERE id = ?', (aid,)).fetchone()
        if not riga:
            continue
        temi = con.execute('SELECT topic_id, fonte FROM assertion_topics WHERE assertion_id = ?',
                           (aid,)).fetchall()
        archi = con.execute(
            f"SELECT {', '.join(colonne_archi)} FROM edges "
            "WHERE (src_type = 'assertion' AND src_id = ?) OR (dst_type = 'assertion' AND dst_id = ?)",
            (aid, aid)).fetchall()
        con.execute(
            'INSERT OR REPLACE INTO asserzioni_scartate(id, chunk_id, text, kind, stance, topic_id, '
            'confidence, asserted_at, modello, ruolo, voce, motivo, fonte, scartata_at, legami_temi, '
            'legami_archi) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (*riga, motivo, fonte, adesso, json.dumps(temi),
             json.dumps([dict(zip(colonne_archi, a)) for a in archi], ensure_ascii=False)))
        con.execute("DELETE FROM edges WHERE (src_type = 'assertion' AND src_id = ?) "
                    "OR (dst_type = 'assertion' AND dst_id = ?)", (aid, aid))
        con.execute('DELETE FROM assertions WHERE id = ?', (aid,))   # assertion_topics: CASCADE
        n += 1
    return n


def ripristina(con, motivo: str | None = None, fonte: str | None = None,
               ids: list[int] | None = None) -> int:
    """Rimette in `assertions` le scartate che corrispondono ai filtri (nessun
    filtro = tutte), con temi e archi. Non fa commit."""
    q, par = 'SELECT * FROM asserzioni_scartate WHERE 1=1', []
    if motivo:
        q += ' AND motivo = ?'
        par.append(motivo)
    if fonte:
        q += ' AND fonte = ?'
        par.append(fonte)
    if ids:
        q += f" AND id IN ({','.join('?' * len(ids))})"
        par += ids
    nomi = [d[0] for d in con.execute(q + ' LIMIT 0', par).description]
    n = 0
    for valori in con.execute(q, par).fetchall():
        r = dict(zip(nomi, valori))
        if not con.execute('SELECT 1 FROM chunks WHERE id = ?', (r['chunk_id'],)).fetchone():
            continue                        # il chunk non esiste più (fonte reingerita)
        con.execute(
            'INSERT OR IGNORE INTO assertions(id, chunk_id, text, kind, stance, topic_id, '
            'confidence, asserted_at, modello, ruolo, voce) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (r['id'], r['chunk_id'], r['text'], r['kind'], r['stance'], r['topic_id'],
             r['confidence'], r['asserted_at'], r['modello'], r.get('ruolo'), r.get('voce')))
        for topic_id, fonte_t in json.loads(r['legami_temi'] or '[]'):
            con.execute('INSERT OR IGNORE INTO assertion_topics(assertion_id, topic_id, fonte) '
                        'VALUES (?,?,?)', (r['id'], topic_id, fonte_t))
        for arco in json.loads(r['legami_archi'] or '[]'):
            colonne = list(arco)
            con.execute(f"INSERT OR IGNORE INTO edges({', '.join(colonne)}) "
                        f"VALUES ({', '.join('?' * len(colonne))})", [arco[c] for c in colonne])
        con.execute('DELETE FROM asserzioni_scartate WHERE id = ?', (r['id'],))
        n += 1
    return n
