# Lacune aperte (stats/lacune_aperte.json): riconoscimento dei doppioni ed
# eliminazione definitiva. Prima ogni scrittore confrontava il testo intero,
# minuscolo e senza spazi doppi: la stessa tensione tornava con un'altra data
# o con le due frasi in ordine inverso, lo stesso tema con un'altra formula
# (19/11/2026: 11 copie su 26 lacune). Qui il confronto è per contenuto:
#   tensione → la coppia delle due frasi citate «…», in qualunque ordine;
#   tema     → il nome del tema;
#   altro    → il testo senza punteggiatura.

import json
import os
import re
import unicodedata
from datetime import datetime

APERTE = 'lacune_aperte.json'
RIFIUTATE = 'lacune_rifiutate.json'


def _piatto(testo: str) -> str:
    t = unicodedata.normalize('NFKD', (testo or '').lower())
    t = ''.join(c for c in t if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', t)).strip()


def chiave(testo: str) -> tuple:
    testo = testo or ''
    citate = re.findall(r'«([^»]*)»', testo)
    if testo.startswith('Tensione') and len(citate) >= 2:
        return ('tensione', frozenset(_piatto(c) for c in citate[:2]))
    tema = re.match(r'Il tema "([^"]+)"', testo)
    if tema:
        return ('tema', _piatto(tema.group(1)))
    return ('testo', _piatto(testo))


def _leggi(percorso: str) -> list:
    try:
        return json.load(open(percorso, encoding='utf-8')) if os.path.exists(percorso) else []
    except Exception:
        return []


def _scrivi(percorso: str, dati: list) -> None:
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    temporaneo = percorso + '.tmp'
    with open(temporaneo, 'w', encoding='utf-8') as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)
    os.replace(temporaneo, percorso)


def chiavi_rifiutate(cartella: str) -> set:
    return {chiave(e.get('testo', '')) for e in _leggi(os.path.join(cartella, RIFIUTATE))
            if e.get('testo')}


def deduplica(lacune: list) -> list:
    """Una voce per chiave: tiene la più vecchia (data_prima_rilevazione)."""
    tenute = {}
    for voce in sorted(lacune, key=lambda l: l.get('data_prima_rilevazione') or ''):
        tenute.setdefault(chiave(voce.get('testo', '')), voce)
    rimaste = set(map(id, tenute.values()))
    return [l for l in lacune if id(l) in rimaste]


def conta_doppioni(cartella: str) -> int:
    lacune = _leggi(os.path.join(cartella, APERTE))
    return len(lacune) - len(deduplica(lacune))


def unisci_doppioni(cartella: str) -> int:
    percorso = os.path.join(cartella, APERTE)
    lacune = _leggi(percorso)
    pulite = deduplica(lacune)
    if len(pulite) < len(lacune):
        _scrivi(percorso, pulite)
    return len(lacune) - len(pulite)


def elimina(cartella: str, testo: str, motivo: str = 'eliminata_da_dashboard') -> int:
    """Toglie la lacuna e tutte le sue copie, e la segna fra le rifiutate:
    mind, Critico e chat non la riproporranno. Ritorna quante voci ha tolto."""
    k = chiave(testo)
    percorso = os.path.join(cartella, APERTE)
    lacune = _leggi(percorso)
    rimaste = [l for l in lacune if chiave(l.get('testo', '')) != k]
    if len(rimaste) < len(lacune):
        _scrivi(percorso, rimaste)
    percorso_r = os.path.join(cartella, RIFIUTATE)
    rifiutate = _leggi(percorso_r)
    if k not in {chiave(e.get('testo', '')) for e in rifiutate}:
        rifiutate.append({'testo': testo, 'data_rifiuto': datetime.now().isoformat(),
                          'motivo': motivo})
        _scrivi(percorso_r, rifiutate)
    return len(lacune) - len(rimaste)
