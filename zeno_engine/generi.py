# Genere di ogni fonte (sources.genere), deciso con l'utente il 28/09/2026.
#
# Il tipo di fonte (scheda_libro, articolo, pubblicazione…) e lo strato
# (esplicito/acquisito/dialogico) non bastavano: un romanzo dell'utente e la sua
# tesi erano entrambi "esplicito", un saggio letto e un manga entrambi
# "scheda_libro". Da qui due guasti:
#   - la voce del narratore dei romanzi dell'utente passava per una sua
#     posizione ("Il mondo ha bisogno della mia conoscenza");
#   - la trama dei romanzi (suoi e letti) pesava come il pensiero di un saggio.
#
# Il genere si ricava dalla cartella (opere dell'utente) e dal campo `area:` del
# frontmatter (libri letti). Le schede senza area utile si classificano una
# volta a mano in stats/generi_libri.json: {titolo_file: genere}.

import json
import os

from zeno_engine import config

NARRATIVA_PROPRIA = 'narrativa_propria'
SAGGISTICA_PROPRIA = 'saggistica_propria'
POESIA_PROPRIA = 'poesia_propria'
LETTERE = 'lettere'
RECENSIONI = 'recensioni'          # recensioni di libri/film, note di degustazione (Vivino)
RIFLESSIONI = 'riflessioni'        # riflessioni, maieutica, dialoghi con AI
NARRATIVA_LETTA = 'narrativa_letta'
POESIA_LETTA = 'poesia_letta'
SAGGISTICA_LETTA = 'saggistica_letta'
DA_CLASSIFICARE = 'da_classificare'

# Generi in cui conta il tema e la frase, non la trama.
NARRATIVI = (NARRATIVA_PROPRIA, NARRATIVA_LETTA)

_CARTELLE_PROPRIE = {           # sottocartella di documents/pubblicazioni/ → genere
    'libri': NARRATIVA_PROPRIA,
    'articoli': SAGGISTICA_PROPRIA,
    'tesi': SAGGISTICA_PROPRIA,
    'militare': SAGGISTICA_PROPRIA,
    'poesie': POESIA_PROPRIA,
    'lettere': LETTERE,
    'recensioni': RECENSIONI,
    'vivino': RECENSIONI,
}
_AREE_LIBRI = {                 # valore di `area:` nelle schede di documents/libri/
    'narrativa': NARRATIVA_LETTA,
    'manga': NARRATIVA_LETTA,
    'letteratura-per-ragazzi': NARRATIVA_LETTA,
    'narrativa-interattiva': NARRATIVA_LETTA,
    'poesia-classici': POESIA_LETTA,
    'saggistica': SAGGISTICA_LETTA,
    'consultazione': SAGGISTICA_LETTA,
}
_AREE_ACQUISITE = {             # prima cartella sotto documents/ → genere
    'articoli_saggistica': SAGGISTICA_LETTA,
    'formazione': SAGGISTICA_LETTA,
    'psicologia_cognitiva': SAGGISTICA_LETTA,
    'riflessioni': RIFLESSIONI,
}
FILE_MANUALE = os.path.join(config.BRAIN, 'stats', 'generi_libri.json')


def _area_frontmatter(percorso: str) -> str:
    try:
        with open(percorso, encoding='utf-8') as f:
            for n, riga in enumerate(f):
                if riga.startswith('area:'):
                    return riga.split(':', 1)[1].strip().lower()
                if n > 40:
                    break
    except OSError:
        pass
    return ''


def _manuale() -> dict[str, str]:
    try:
        with open(FILE_MANUALE, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def genere_di(percorso: str, manuale: dict[str, str] | None = None) -> str:
    """Genere di una fonte dal suo percorso (e dal frontmatter, per i libri)."""
    rel = percorso.split('/documents/', 1)[-1]
    parti = rel.split('/')
    radice = parti[0].lower()
    if radice == 'pubblicazioni' and len(parti) > 2:
        return _CARTELLE_PROPRIE.get(parti[1].lower(), SAGGISTICA_PROPRIA)
    if radice == 'riflessioni' and len(parti) > 2 and parti[1].lower() == 'libri':
        return RECENSIONI                       # specchi delle recensioni (rec_*.md)
    if radice == 'libri':
        titolo = os.path.splitext(parti[-1])[0]
        manuale = _manuale() if manuale is None else manuale
        if titolo in manuale:
            return manuale[titolo]
        area = _area_frontmatter(percorso)
        for voce in (v.strip() for v in area.split(',')):
            if voce in _AREE_LIBRI:
                return _AREE_LIBRI[voce]
        return DA_CLASSIFICARE
    return _AREE_ACQUISITE.get(radice, SAGGISTICA_LETTA)


def assegna(con) -> dict[str, int]:
    """Scrive sources.genere per tutte le fonti. Non fa commit."""
    manuale = _manuale()
    conta: dict[str, int] = {}
    for sid, percorso in con.execute('SELECT id, path FROM sources').fetchall():
        g = genere_di(percorso, manuale)
        con.execute('UPDATE sources SET genere = ? WHERE id = ?', (g, sid))
        conta[g] = conta.get(g, 0) + 1
    return conta


# ── Ruolo e voce delle asserzioni narrative ─────────────────────────────────

def voce_narratore(percorso: str) -> bool:
    """Nei romanzi dell'utente parla il narratore, non lui: vale per il testo del
    romanzo, non per scalette, sinossi, temi e schede dei personaggi (cartella
    Altro/), che sono appunti d'autore."""
    rel = percorso.split('/documents/', 1)[-1]
    parti = rel.split('/')
    if len(parti) < 4 or parti[0].lower() != 'pubblicazioni' or parti[1].lower() != 'libri':
        return False
    nome = os.path.basename(percorso).lower()
    return 'altro' not in (p.lower() for p in parti[2:-1]) and not nome.startswith('scaletta')


def applica_giudizi(con, giudizi: list[dict]) -> dict[str, int]:
    """Scrive assertions.ruolo (tema | citazione | trama) e assertions.voce dai
    giudizi [{id, giudizio}], e sposta tra le scartate quelli 'catalogo'.
    Non fa commit."""
    from zeno_engine import filtro
    conta: dict[str, int] = {}
    catalogo = []
    for g in giudizi:
        if g['giudizio'] == 'catalogo':
            catalogo.append((g['id'], 'catalogo'))
            continue
        con.execute('UPDATE assertions SET ruolo = ? WHERE id = ?', (g['giudizio'], g['id']))
        conta[g['giudizio']] = conta.get(g['giudizio'], 0) + 1
    for aid, percorso in con.execute(
            "SELECT a.id, s.path FROM assertions a JOIN chunks c ON c.id = a.chunk_id "
            "JOIN sources s ON s.id = c.source_id WHERE s.genere = ?", (NARRATIVA_PROPRIA,)).fetchall():
        if voce_narratore(percorso):
            con.execute("UPDATE assertions SET voce = 'narratore' WHERE id = ?", (aid,))
            conta['voce_narratore'] = conta.get('voce_narratore', 0) + 1
    conta['catalogo'] = filtro.scarta(con, catalogo, fonte='giudizio')
    return conta
