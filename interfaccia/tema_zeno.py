# tema_zeno.py — aspetto di chat e dashboard (richiesta dell'utente, 28/09/2026).
#
# Prima Zeno aveva l'aspetto di Streamlit di serie: colori e badge fissi,
# scritti per il tema scuro (le metriche della dashboard sparivano in quello
# chiaro), fonti tutte uguali e nessuna traccia della distinzione che conta di
# più in Zeno: chi parla. Qui vivono tre varianti complete, ognuna con tema
# chiaro e scuro:
#
#   biblioteca — carta e inchiostro: serif da lettura, fonti come note a piè
#                di pagina.
#   notturno   — il blu elettrico del logo su fondo inchiostro.
#   schedario  — schedario di biblioteca: DIN condensato, Charter, schede
#                rigate. Scelta dall'utente il 29/09/2026.
#
# La variante si sceglie in zeno_config.yaml (`ui.tema`); il tema nativo di
# Streamlit (colori dei widget, font) va in scripts/.streamlit/config.toml,
# che zeno_launcher.py riscrive a ogni avvio chiamando config_toml().
# Per cambiare a mano (da scripts/):  python -m interfaccia.tema_zeno notturno
#
# Solo font di sistema macOS (Iowan Old Style, New York, Avenir Next, SF Mono):
# nessun font scaricato da internet, coerente con lo zero cloud.

import html
import json
import os
import re
from datetime import datetime

VARIANTE_PREDEFINITA = 'biblioteca'

# ── Chi parla: lo strato della fonte diventa una voce riconoscibile ─────────
ETICHETTE_VOCE = {
    'esplicito': 'La tua voce',
    'dialogico': 'Dialogo',
    'acquisito': 'Autore letto',
    'narratore': 'Narratore',
    'inferito': 'Sintesi di Zeno',
    'nonletto': 'Non letto · sinossi',
    'ignota': 'Fonte',
}
CONOSCENZA_NON_LETTO = ('da-leggere', 'non-applicabile')

MESI = ('gen', 'feb', 'mar', 'apr', 'mag', 'giu',
        'lug', 'ago', 'set', 'ott', 'nov', 'dic')

_SERIF = "'Iowan Old Style', 'Charter', ui-serif, Georgia, serif"
_NEWYORK = "ui-serif, 'New York', 'Iowan Old Style', Georgia, serif"
_SANS = "-apple-system, ui-sans-serif, 'SF Pro Text', system-ui, sans-serif"
_AVENIR = "'Avenir Next', 'Seravek', ui-sans-serif, system-ui, sans-serif"
_MONO = "ui-monospace, 'SF Mono', Menlo, monospace"

# Ogni variante: tema nativo Streamlit (chiaro/scuro) + gettoni CSS + ritocchi.
# I colori delle voci sono scelti per restare distinguibili fra loro e
# leggibili sul fondo (contrasto del testo ≥ 4,5:1).
VARIANTI = {
    'biblioteca': {
        'descrizione': 'Carta e inchiostro: serif da lettura, fonti come note a piè di pagina.',
        'font': _SERIF, 'font_titoli': _SERIF, 'font_meta': _AVENIR,
        'dimensione_base': 17,
        'grafici': ['#3F7CC0', '#B8793A', '#3E9A85', '#8E6CC0', '#8C857A', '#C99A45'],
        'chiaro': {
            'bg': '#F7F3EC', 'superficie': '#EFE8DC', 'testo': '#22201C',
            'tenue': '#6B645A', 'bordo': '#DDD3C3', 'accento': '#1E3A5F',
            'esplicito': '#1E3A5F', 'dialogico': '#2F6B5E', 'acquisito': '#8A5A2B',
            'narratore': '#6B4C8A', 'inferito': '#6B645A', 'nonletto': '#8C857A',
            'lacuna_bg': '#F4E9D3', 'lacuna_bordo': '#B7862F', 'lacuna_testo': '#5C4210',
            'errore': '#9E2B25',
        },
        'scuro': {
            'bg': '#1A1916', 'superficie': '#24221E', 'testo': '#E9E3D7',
            'tenue': '#A39B8E', 'bordo': '#3A362F', 'accento': '#9DB8D9',
            'esplicito': '#9DB8D9', 'dialogico': '#7FBFAE', 'acquisito': '#D4A373',
            'narratore': '#B7A0D6', 'inferito': '#A39B8E', 'nonletto': '#8A8378',
            'lacuna_bg': '#2E2717', 'lacuna_bordo': '#C99A45', 'lacuna_testo': '#EBD3A2',
            'errore': '#E07A6E',
        },
        'css': '''
.z-testata .z-marchio{font-style:italic;letter-spacing:.01em}
.z-fonti{border-top:1px solid var(--z-bordo);margin-top:1.1rem;padding-top:.5rem}
.z-fonti li.z-fonte{border:none;background:none;padding:.25rem 0}
.z-fonte .z-num{background:none;color:var(--z-v);font-weight:600;min-width:1.4rem}
.z-fonti-titolo{font-variant:small-caps;letter-spacing:.08em;text-transform:lowercase;font-size:.95rem}
.z-lacuna{border:1px dashed var(--z-lacuna-bordo);border-left-width:1px}
''',
    },
    'notturno': {
        'descrizione': 'Il blu elettrico del logo su fondo inchiostro; titoli serif, testo sans.',
        'font': _SANS, 'font_titoli': _NEWYORK, 'font_meta': _SANS,
        'dimensione_base': 16,
        'grafici': ['#3B9BE8', '#D39A3A', '#3DB896', '#9A7CF0', '#7F8DA3', '#E0B25C'],
        'chiaro': {
            'bg': '#F3F6FA', 'superficie': '#E6ECF4', 'testo': '#14202F',
            'tenue': '#5A6B80', 'bordo': '#CFD9E6', 'accento': '#1F6FB2',
            'esplicito': '#1F6FB2', 'dialogico': '#1D7F66', 'acquisito': '#9A6512',
            'narratore': '#6A4FC0', 'inferito': '#5A6B80', 'nonletto': '#7D8898',
            'lacuna_bg': '#FFF4DE', 'lacuna_bordo': '#D99A1E', 'lacuna_testo': '#5E4108',
            'errore': '#B3261E',
        },
        'scuro': {
            'bg': '#0B1220', 'superficie': '#121C2E', 'testo': '#DCE5F0',
            'tenue': '#8C9AB0', 'bordo': '#22324D', 'accento': '#4FB3FF',
            'esplicito': '#4FB3FF', 'dialogico': '#5FD1B0', 'acquisito': '#E0B25C',
            'narratore': '#B59CFF', 'inferito': '#8C9AB0', 'nonletto': '#6F7D92',
            'lacuna_bg': '#221B0C', 'lacuna_bordo': '#E0B25C', 'lacuna_testo': '#F1D9A6',
            'errore': '#FF8A80',
        },
        'css': '''
.z-testata .z-marchio{letter-spacing:.02em}
.z-fonti li.z-fonte{border-radius:10px;background:var(--z-superficie);
  box-shadow:inset 0 0 0 1px var(--z-bordo)}
.z-fonte:hover{box-shadow:inset 0 0 0 1px var(--z-v),0 0 18px -6px var(--z-v)}
.z-cit{border-radius:999px}
.z-lacuna{border-left:3px solid var(--z-lacuna-bordo);border-radius:8px}
''',
    },
    # Rifatta il 29/09/2026 su richiesta dell'utente («i caratteri e le celle
    # hanno ancora un aspetto Vanilla IA»): via il sans neutro e i riquadri
    # arrotondati da app qualsiasi. Il riferimento è il vero schedario di
    # biblioteca: etichette dei cassetti in DIN condensato maiuscolo, testo
    # in Charter (serif nato per stampare bene a bassa risoluzione, niente di
    # "digitale"), numeri e date dattiloscritti in monospazio, fonti come
    # schede cartacee con la riga rossa d'intestazione e le righe azzurre,
    # angoli vivi ovunque.
    'schedario': {
        'descrizione': 'Schedario di biblioteca: etichette in DIN condensato, testo in Charter, '
                       'fonti come schede rigate, angoli vivi.',
        'font': "'Charter', 'Iowan Old Style', ui-serif, Georgia, serif",
        'font_titoli': "'DIN Condensed', 'DIN Alternate', 'Avenir Next Condensed', "
                       "'Futura', sans-serif",
        'font_meta': _MONO,
        'dimensione_base': 16,
        'raggio': '0rem',
        'grafici': ['#3F78AE', '#B5652C', '#3F8F63', '#8566B0', '#8A867C', '#C9962F'],
        'chiaro': {
            'bg': '#EEEBE3', 'superficie': '#E3DFD4', 'scheda': '#FFFDF7',
            'testo': '#1D1C19', 'tenue': '#67645B', 'bordo': '#CBC6B8',
            'accento': '#2A5C8A', 'riga': '#DCE5EE', 'riga_rossa': '#D2695A',
            'esplicito': '#2A5C8A', 'dialogico': '#2F7A55', 'acquisito': '#A0521D',
            'narratore': '#74559E', 'inferito': '#67645B', 'nonletto': '#8E8A80',
            'lacuna_bg': '#FBF3DC', 'lacuna_bordo': '#B98416', 'lacuna_testo': '#553B05',
            'errore': '#A8322A',
        },
        'scuro': {
            'bg': '#121314', 'superficie': '#1E1F22', 'scheda': '#1A1B1D',
            'testo': '#E8E6DF', 'tenue': '#9C998F', 'bordo': '#35373B',
            'accento': '#79A9D6', 'riga': '#252E37', 'riga_rossa': '#B45A4C',
            'esplicito': '#79A9D6', 'dialogico': '#6CC495', 'acquisito': '#E0995C',
            'narratore': '#B49BE0', 'inferito': '#9C998F', 'nonletto': '#77746C',
            'lacuna_bg': '#262010', 'lacuna_bordo': '#D2A23E', 'lacuna_testo': '#EFD9A8',
            'errore': '#FF8A80',
        },
        'css': '''
/* Testata: etichetta di cassetto, doppio filetto sotto */
.z-testata{border-bottom:none;position:relative;padding-bottom:.55rem;margin-bottom:1.1rem}
.z-testata::after{content:'';position:absolute;left:0;right:0;bottom:0;height:4px;
  border-top:2px solid var(--z-testo);border-bottom:1px solid var(--z-testo)}
.z-testata .z-marchio{text-transform:uppercase;letter-spacing:.18em;font-size:2.5rem;
  font-weight:700}
.z-testata .z-sottotitolo{text-transform:uppercase;letter-spacing:.16em;font-size:.68rem}
h1,h2,h3,h4{text-transform:uppercase;letter-spacing:.08em;font-weight:700}

/* Messaggi: niente avatar da chat qualsiasi, un'etichetta dattiloscritta */
[data-testid="stChatMessageAvatarUser"],[data-testid="stChatMessageAvatarAssistant"],
[data-testid="stChatMessageAvatarCustom"]{display:none}
[data-testid="stChatMessage"]{gap:0;padding:1rem 0 .9rem;border-radius:0;
  border-top:1px solid var(--z-bordo)}
[data-testid="stChatMessageContent"]::before{display:block;font-family:var(--z-font-meta);
  font-size:.64rem;letter-spacing:.16em;color:var(--z-tenue);margin-bottom:.4rem}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"]::before{content:'RICHIESTA'}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] p{font-weight:600}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"])
  [data-testid="stChatMessageContent"]::before{content:'DALL\\2019 ARCHIVIO'}

/* Citazioni nel testo: numeri di catalogo, angoli vivi */
.z-cit{border-radius:0;font-weight:500;padding:0 .28em}

/* Fonti: schede cartacee con riga rossa d'intestazione e righe azzurre */
.z-fonti-titolo{font-family:var(--z-font-titoli);text-transform:uppercase;
  letter-spacing:.14em;font-size:.95rem;color:var(--z-testo);margin-bottom:.5rem}
.z-fonti ol{grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:.75rem}
.z-fonti li.z-fonte{position:relative;display:block;padding:0 .8rem .6rem 1.05rem;
  background:var(--z-scheda);border:1px solid var(--z-bordo);border-radius:0;
  box-shadow:2px 2px 0 -1px var(--z-scheda),2px 2px 0 0 var(--z-bordo)}
.z-fonte::before{content:'';position:absolute;left:0;top:0;bottom:0;width:4px;
  background:var(--z-v)}
.z-fonte .z-num{position:absolute;top:.5rem;right:.7rem;font-size:.68rem;
  font-weight:500;color:var(--z-tenue);padding:0}
.z-fonte .z-num::before{content:'№\\00a0'}
.z-fonte-titolo{display:block;font-size:.9rem;font-weight:700;line-height:1.3;
  margin:0 -.8rem 0 -1.05rem;padding:.45rem 2.6rem .4rem 1.05rem;
  border-bottom:1px solid var(--z-riga-rossa)}
.z-fonte .z-meta,.z-fonte details{line-height:1.5rem;
  background-image:repeating-linear-gradient(to bottom,transparent 0 calc(1.5rem - 1px),
    var(--z-riga) calc(1.5rem - 1px) 1.5rem)}
.z-fonte .z-meta{margin-top:0;gap:0 .6rem}
.z-voce{text-transform:uppercase;letter-spacing:.1em;font-size:.64rem}
.z-pert{border-radius:0;height:3px}
.z-estratto{border-left:none;padding-left:0;font-size:.84rem;line-height:1.5rem;margin:0}

/* Lacuna: la scheda che manca */
.z-lacuna{border:1px dashed var(--z-lacuna-bordo);border-radius:0;
  background-image:repeating-linear-gradient(135deg,transparent 0 7px,
    color-mix(in srgb,var(--z-lacuna-bordo) 12%,transparent) 7px 8px)}
.z-lacuna .z-etichetta{font-family:var(--z-font-titoli);font-size:.85rem;
  letter-spacing:.14em}
.z-quote{font-family:var(--z-font-testo);border-left:none;padding-left:1rem;
  box-shadow:inset 3px 0 0 var(--z-acquisito),inset 6px 0 0 var(--z-bg),
    inset 7px 0 0 var(--z-acquisito)}

/* Widget di Streamlit: etichette maiuscole, angoli vivi */
.stButton button,[data-testid^="stBaseButton"]{font-family:var(--z-font-titoli);
  text-transform:uppercase;letter-spacing:.1em;border-radius:0}
[data-testid="stTabs"] [role="tab"] p{font-family:var(--z-font-titoli);
  text-transform:uppercase;letter-spacing:.12em}
[data-testid="stMetric"]{background:var(--z-scheda);border:1px solid var(--z-bordo);
  border-top:3px solid var(--z-testo);border-radius:0}
[data-testid="stMetricLabel"] p{font-family:var(--z-font-titoli);text-transform:uppercase;
  letter-spacing:.12em;font-size:.8rem}
[data-testid="stMetricValue"]{font-family:var(--z-font-meta);font-weight:500;
  font-variant-numeric:tabular-nums}
[data-testid="stExpander"] summary{font-family:var(--z-font-titoli);text-transform:uppercase;
  letter-spacing:.08em}
.fonte,.lacuna,.avviso,.lacuna-dash,.errore,.fase-badge{border-radius:0}
''',
    },
}


def variante_attiva(cfg: dict | None = None) -> str:
    nome = ((cfg or {}).get('ui') or {}).get('tema') or os.environ.get('ZENO_TEMA') \
        or VARIANTE_PREDEFINITA
    return nome if nome in VARIANTI else VARIANTE_PREDEFINITA


# ── Tema nativo di Streamlit (config.toml) ─────────────────────────────────
def config_toml(variante: str) -> str:
    """Sezioni [theme] per config.toml: widget, font e colori di base di
    Streamlit, chiaro e scuro (segue l'impostazione di sistema)."""
    v = VARIANTI[variante]

    def blocco(nome: str, base: str, c: dict) -> str:
        return (f'[{nome}]\n'
                f'primaryColor = "{c["accento"]}"\n'
                f'backgroundColor = "{c["bg"]}"\n'
                f'secondaryBackgroundColor = "{c["superficie"]}"\n'
                f'textColor = "{c["testo"]}"\n'
                f'borderColor = "{c["bordo"]}"\n'
                f'linkColor = "{c["accento"]}"\n'
                f'blueColor = "{c["esplicito"]}"\n'
                f'greenColor = "{c["dialogico"]}"\n'
                f'orangeColor = "{c["lacuna_bordo"]}"\n'
                f'redColor = "{c["errore"]}"\n'
                f'violetColor = "{c["narratore"]}"\n'
                f'dataframeHeaderBackgroundColor = "{c["superficie"]}"\n'
                f'dataframeBorderColor = "{c["bordo"]}"\n\n')

    comune = (f'[theme]\n'
              f'font = "{v["font"]}"\n'
              f'headingFont = "{v["font_titoli"]}"\n'
              f'codeFont = "{_MONO}"\n'
              f'baseFontSize = {v["dimensione_base"]}\n'
              f'baseRadius = "{v.get("raggio", "0.4rem")}"\n'
              f'showSidebarBorder = true\n'
              # Nella sezione comune: in [theme.light]/[theme.dark] Streamlit
              # 1.61 la ignora (provato). Toni medi, leggibili su entrambi i fondi.
              f'chartCategoricalColors = {json.dumps(v["grafici"])}\n\n')
    return (f'# Tema di Zeno: variante "{variante}" (generato da tema_zeno.py)\n'
            + comune + blocco('theme.light', 'light', v['chiaro'])
            + blocco('theme.dark', 'dark', v['scuro']))


def scrivi_config(variante: str, percorso: str | None = None) -> str:
    """Riscrive scripts/.streamlit/config.toml: le sezioni server/browser/client
    di sempre (le stesse di zeno_launcher.py) più il tema della variante."""
    scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    percorso = percorso or os.path.join(scripts, '.streamlit', 'config.toml')
    base = ('[server]\nheadless = true\nenableCORS = true\n'
            'enableXsrfProtection = true\n\n'
            '[browser]\ngatherUsageStats = false\n\n'
            '[client]\nshowErrorDetails = false\n\n')
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, 'w', encoding='utf-8') as f:
        f.write(base + config_toml(variante))
    return percorso


# ── CSS ─────────────────────────────────────────────────────────────────────
_CSS_BASE = '''
:root{@@VARS@@}
/* Chrome di Streamlit: via la striscia colorata e il menu hamburger */
[data-testid="stDecoration"]{display:none}
[data-testid="stHeader"]{background:transparent}
[data-testid="stMainBlockContainer"]{max-width:1180px;padding-top:2.2rem}
h1,h2,h3{font-family:var(--z-font-titoli);letter-spacing:-.005em}

/* Testata */
.z-testata{display:flex;align-items:baseline;gap:.9rem;margin:0 0 .6rem 0;
  border-bottom:1px solid var(--z-bordo);padding-bottom:.55rem}
.z-testata .z-marchio{font-family:var(--z-font-titoli);font-size:2.1rem;
  line-height:1;color:var(--z-testo)}
.z-testata .z-sottotitolo{font-family:var(--z-font-meta);font-size:.8rem;
  color:var(--z-tenue);letter-spacing:.04em}

/* Messaggi */
[data-testid="stChatMessage"]{background:transparent;padding:.6rem 0;gap:.8rem}
[data-testid="stChatMessageContent"]{max-width:72ch}
[data-testid="stChatMessageContent"] p{line-height:1.62}
[data-testid="stChatMessageAvatarUser"],[data-testid="stChatMessageAvatarAssistant"]{
  background:var(--z-superficie);color:var(--z-tenue)}

/* Citazioni nel testo: numero colorato per voce */
.z-cits{white-space:nowrap}
.z-cit{display:inline-block;min-width:1.15em;padding:0 .3em;margin-left:.12em;
  font-family:var(--z-font-meta);font-size:.68rem;line-height:1.35;font-weight:600;
  text-align:center;border-radius:4px;text-decoration:none !important;
  color:var(--z-bg) !important;background:var(--z-v);vertical-align:.35em}
.z-cit:hover{filter:brightness(1.12)}

/* Voce di una fonte */
.z-v-esplicito{--z-v:var(--z-esplicito)} .z-v-dialogico{--z-v:var(--z-dialogico)}
.z-v-acquisito{--z-v:var(--z-acquisito)} .z-v-narratore{--z-v:var(--z-narratore)}
.z-v-inferito{--z-v:var(--z-inferito)}   .z-v-nonletto{--z-v:var(--z-nonletto)}
.z-v-ignota{--z-v:var(--z-tenue)}
.z-voce{font-family:var(--z-font-meta);font-size:.72rem;font-weight:600;color:var(--z-v)}

/* Blocco fonti */
.z-fonti{margin-top:.9rem}
.z-fonti-titolo{font-family:var(--z-font-meta);font-size:.78rem;color:var(--z-tenue);
  margin-bottom:.35rem}
.z-fonti ol{list-style:none;margin:0;padding:0;display:grid;gap:.4rem}
/* li.z-fonte dentro .z-fonti: Streamlit dà il suo padding ai <li> del markdown
   e con un selettore più debole vinceva lui (riga rossa che sbordava). */
.z-fonti li.z-fonte{display:flex;gap:.6rem;align-items:flex-start;padding:.5rem .7rem;margin:0}
.z-fonte .z-num{flex:none;font-family:var(--z-font-meta);font-size:.78rem;
  font-weight:600;color:var(--z-v);padding-top:.1rem}
.z-fonte-corpo{min-width:0;flex:1}
.z-fonte-titolo{font-size:.93rem;color:var(--z-testo) !important;
  text-decoration:none !important;font-weight:500}
a.z-fonte-titolo:hover{text-decoration:underline !important}
.z-meta{font-family:var(--z-font-meta);font-size:.72rem;color:var(--z-tenue);
  display:flex;flex-wrap:wrap;gap:.2rem .55rem;align-items:center;margin-top:.1rem}
.z-pert{display:inline-block;width:42px;height:4px;border-radius:2px;
  background:var(--z-bordo);position:relative;overflow:hidden}
.z-pert::after{content:'';position:absolute;inset:0;width:calc(var(--p)*100%);
  background:var(--z-v)}
.z-fonte details{margin-top:.25rem}
.z-fonte summary{font-family:var(--z-font-meta);font-size:.72rem;color:var(--z-tenue);
  cursor:pointer}
.z-estratto{font-size:.85rem;color:var(--z-tenue);line-height:1.5;margin:.3rem 0 0 0;
  border-left:2px solid var(--z-bordo);padding-left:.6rem}

/* Citazione testuale riportata */
.z-quote{font-family:var(--z-font-titoli);font-style:italic;font-size:1.02rem;
  border-left:3px solid var(--z-acquisito);margin:.8rem 0;padding:.2rem 0 .2rem .9rem}
.z-quote cite{display:block;font-style:normal;font-family:var(--z-font-meta);
  font-size:.75rem;color:var(--z-tenue);margin-top:.25rem}

/* Lacuna: un vuoto dell'archivio, non un errore */
.z-lacuna{background:var(--z-lacuna-bg);color:var(--z-lacuna-testo);
  border-left:3px solid var(--z-lacuna-bordo);padding:.6rem .85rem;margin:.7rem 0;
  border-radius:4px;font-size:.93rem;line-height:1.5}
.z-lacuna .z-etichetta{display:block;font-family:var(--z-font-meta);font-size:.7rem;
  font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:var(--z-lacuna-bordo);margin-bottom:.15rem}

/* Classi storiche di chat_ui/dashboard, ora legate al tema */
.fonte{background:var(--z-superficie);color:var(--z-testo) !important;padding:2px 8px;
  border-radius:10px;font-size:12px;margin:2px;display:inline-block;
  text-decoration:none !important;border:1px solid var(--z-bordo)}
a.fonte:hover{border-color:var(--z-accento)}
.lacuna,.avviso,.lacuna-dash{background:var(--z-lacuna-bg);
  border-left:3px solid var(--z-lacuna-bordo);padding:6px 10px;border-radius:4px;
  margin:6px 0;color:var(--z-lacuna-testo) !important;font-size:13px;line-height:1.45}
.errore{background:color-mix(in srgb,var(--z-errore) 10%,var(--z-bg));
  border-left:3px solid var(--z-errore);padding:6px 10px;border-radius:4px;margin:6px 0;
  color:var(--z-testo) !important;font-size:12px;line-height:1.4}
.fase-badge{display:inline-block;padding:2px 10px;border-radius:12px;font-size:11px;
  font-weight:600;letter-spacing:.4px;margin-bottom:6px;font-family:var(--z-font-meta)}
.fase-apertura{background:color-mix(in srgb,var(--z-esplicito) 15%,transparent);color:var(--z-esplicito)}
.fase-approfondimento{background:color-mix(in srgb,var(--z-dialogico) 15%,transparent);color:var(--z-dialogico)}
.fase-consolidamento{background:color-mix(in srgb,var(--z-lacuna-bordo) 15%,transparent);color:var(--z-lacuna-testo)}

/* Dashboard */
[data-testid="stMetric"]{background:var(--z-superficie);border:1px solid var(--z-bordo);
  border-radius:6px;padding:.55rem .8rem}
[data-testid="stMetricLabel"]{font-family:var(--z-font-meta);color:var(--z-tenue)}
[data-testid="stMetricValue"]{font-family:var(--z-font-titoli);font-size:1.6rem}
[data-testid="stTabs"] [role="tab"] p{font-family:var(--z-font-meta)}
'''


def _vars(variante: str) -> str:
    """Gettoni CSS con light-dark(): Streamlit mette `color-scheme: dark` su
    .stApp quando è scuro, quindi il colore giusto si sceglie nel browser, al
    volo, anche se l'utente cambia tema dal menu. (st.context.theme.type
    arriva in ritardo di un giro e dava colori invertiti.)"""
    v = VARIANTI[variante]
    chiaro, scuro = v['chiaro'], v['scuro']
    nomi = {'bg': 'bg', 'superficie': 'superficie', 'testo': 'testo', 'tenue': 'tenue',
            'bordo': 'bordo', 'accento': 'accento', 'esplicito': 'esplicito',
            'dialogico': 'dialogico', 'acquisito': 'acquisito', 'narratore': 'narratore',
            'inferito': 'inferito', 'nonletto': 'nonletto', 'lacuna_bg': 'lacuna-bg',
            'lacuna_bordo': 'lacuna-bordo', 'lacuna_testo': 'lacuna-testo',
            'errore': 'errore'}
    parti = [f'--z-{nomi[k]}:light-dark({chiaro[k]},{scuro[k]})' for k in nomi]
    # Gettoni che solo lo schedario definisce; le altre varianti ripiegano.
    for chiave, css_nome, ripiego in (('scheda', 'scheda', 'superficie'),
                                      ('riga', 'riga', 'bordo'),
                                      ('riga_rossa', 'riga-rossa', 'acquisito')):
        parti.append(f'--z-{css_nome}:light-dark({chiaro.get(chiave, chiaro[ripiego])},'
                     f'{scuro.get(chiave, scuro[ripiego])})')
    parti += [f'--z-font-testo:{v["font"]}', f'--z-font-titoli:{v["font_titoli"]}',
              f'--z-font-meta:{v["font_meta"]}']
    return ';'.join(parti)


def css(variante: str) -> str:
    return ('<style>' + _CSS_BASE.replace('@@VARS@@', _vars(variante))
            + VARIANTI[variante]['css'] + '</style>')


def applica(cfg: dict | None = None) -> str:
    """Inietta il CSS della variante attiva (vale per chiaro e scuro)."""
    import streamlit as st
    variante = variante_attiva(cfg)
    st.markdown(css(variante), unsafe_allow_html=True)
    return variante


def testata(sottotitolo: str) -> str:
    return (f'<div class="z-testata"><span class="z-marchio">Zeno</span>'
            f'<span class="z-sottotitolo">{html.escape(sottotitolo)}</span></div>')


# I grafici plotly non hanno bisogno di nulla: st.plotly_chart usa di serie
# il tema di Streamlit, che prende font e palette (chartCategoricalColors,
# le voci) da config_toml().


# ── Risposte e fonti ───────────────────────────────────────────────────────
def voce(fonte: dict | None) -> str:
    if not fonte:
        return 'ignota'
    if (fonte.get('conoscenza') or '') in CONOSCENZA_NON_LETTO:
        return 'nonletto'
    s = fonte.get('strato') or ''
    return s if s in ETICHETTE_VOCE else 'ignota'


def data_breve(valore) -> str:
    if not valore:
        return ''
    s = str(valore)
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            d = datetime.strptime(s[:19] if 'T' in s or ' ' in s else s[:10], fmt)
            return f'{d.day} {MESI[d.month - 1]} {d.year}'
        except ValueError:
            continue
    return s[:10]


_RE_CIT = re.compile(r'\[(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\]')


def html_risposta(testo: str, fonti: list[dict] | None, chiave: str) -> str:
    """Testo della risposta (markdown del modello) con i riferimenti [n]
    trasformati in numeri colorati per voce, collegati alla fonte, e i
    paragrafi LACUNA: in un riquadro. Il '<' del modello è neutralizzato:
    il testo passa da st.markdown(unsafe_allow_html=True)."""
    fonti = fonti or []
    t = (testo or '').replace('<', '&lt;')

    def cita(m):
        nums = [int(x) for x in re.findall(r'\d+', m.group(1))]
        if not fonti or any(n < 1 or n > len(fonti) for n in nums):
            return m.group(0)                   # un anno fra quadre, non una fonte
        pezzi = []
        for n in nums:
            f = fonti[n - 1]
            titolo = html.escape(f.get('title') or f.get('file') or '', quote=True)
            pezzi.append(f'<a class="z-cit z-v-{voce(f)}" href="#z-{chiave}-{n}" '
                         f'title="{titolo}">{n}</a>')
        return '<sup class="z-cits">' + ''.join(pezzi) + '</sup>'

    t = _RE_CIT.sub(cita, t)

    paragrafi = re.split(r'\n\s*\n', t)
    fuori = []
    for p in paragrafi:
        s = p.strip()
        if s.upper().startswith('LACUNA:'):
            corpo = s[len('LACUNA:'):].strip()
            # Dentro un <div> st.markdown non interpreta più il markdown:
            # grassetti e corsivi del modello si traducono a mano.
            corpo = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', corpo)
            corpo = re.sub(r'(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])', r'<em>\1</em>', corpo)
            fuori.append(f'<div class="z-lacuna"><span class="z-etichetta">Lacuna '
                         f'dell\'archivio</span>{corpo}</div>')
        else:
            fuori.append(p)
    return '\n\n'.join(fuori)


def html_fonti(fonti: list[dict] | None, chiave: str, link=None,
               estratti: bool = True) -> str:
    """Elenco numerato delle fonti: titolo (cliccabile se `link(path)` dà un
    URL), voce, data, pertinenza relativa ed estratto a comparsa."""
    if not fonti:
        return ''
    massimo = max((f.get('score') or 0) for f in fonti) or 1
    righe = []
    for i, f in enumerate(fonti, 1):
        vv = voce(f)
        titolo = html.escape(f.get('title') or f.get('file') or 'senza titolo')
        url = link(f.get('path') or '') if (link and f.get('path')) else ''
        testa = (f'<a class="z-fonte-titolo" href="{html.escape(url, quote=True)}" '
                 f'target="_blank">{titolo}</a>' if url
                 else f'<span class="z-fonte-titolo">{titolo}</span>')
        meta = [f'<span class="z-voce">{ETICHETTE_VOCE[vv]}</span>']
        if data_breve(f.get('data')):
            meta.append(f'<span>{data_breve(f.get("data"))}</span>')
        if f.get('score'):
            p = max(0.08, min(1.0, float(f['score']) / massimo))
            meta.append(f'<span class="z-pert" style="--p:{p:.2f}" '
                        f'title="pertinenza relativa"></span>')
        corpo = f'{testa}<div class="z-meta">{"".join(meta)}</div>'
        if estratti and f.get('estratto'):
            corpo += (f'<details><summary>estratto</summary><p class="z-estratto">'
                      f'{html.escape(f["estratto"])}</p></details>')
        righe.append(f'<li id="z-{chiave}-{i}" class="z-fonte z-v-{vv}">'
                     f'<span class="z-num">{i}</span>'
                     f'<div class="z-fonte-corpo">{corpo}</div></li>')
    return (f'<div class="z-fonti"><div class="z-fonti-titolo">Fonti</div>'
            f'<ol>{"".join(righe)}</ol></div>')


def html_citazione(testo: str, autore: str) -> str:
    return (f'<blockquote class="z-quote">«{html.escape(testo)}»'
            f'<cite>{html.escape(autore)}</cite></blockquote>')


if __name__ == '__main__':
    import sys
    scelta = sys.argv[1] if len(sys.argv) > 1 else VARIANTE_PREDEFINITA
    if scelta not in VARIANTI:
        sys.exit(f'Varianti: {", ".join(VARIANTI)}')
    print('Scritto', scrivi_config(scelta))
    print('Ricorda: ui.tema in zeno_config.yaml =', scelta)
