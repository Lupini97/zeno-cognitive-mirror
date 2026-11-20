# Interruttore della ricerca web («Integrazione web», in alto nella chat).
#
# Spento di serie. Finché resta spento, chat e dashboard non cercano sul web
# e non scaricano pagine: le funzioni che aprono la connessione chiedono a
# questo modulo prima di farlo, e i pulsanti restano disattivati.
#
# Lo stato vive in stats/ricerca_web.json, così vale per la chat e per la
# dashboard (processi diversi) e resta uguale fra un avvio e l'altro.

import json
import os

CHIUSA = ('Ricerca web spenta. Per usarla accendi «Integrazione web» in alto '
          'nella chat.')


def _percorso() -> str | None:
    try:
        from zeno_engine.config import carica_config
        return os.path.join(carica_config()['percorsi']['stats'], 'ricerca_web.json')
    except Exception:
        return None


def attiva() -> bool:
    """True solo se l'utente ha acceso l'interruttore. Nel dubbio, spenta."""
    p = _percorso()
    if not p:
        return False
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f).get('attiva') is True
    except (OSError, ValueError, AttributeError):
        return False


def imposta(valore: bool) -> None:
    p = _percorso()
    if not p:
        return
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump({'attiva': bool(valore)}, f)
