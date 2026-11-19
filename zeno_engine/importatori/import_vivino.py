# scripts/zeno_engine/importatori/import_vivino.py
import pandas as pd, os, sys, re, hashlib, glob
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from zeno_engine.config import carica_config

cfg     = carica_config()
OUT     = os.path.join(cfg['percorsi']['documents'], 'pubblicazioni', 'vivino')
STAGING = cfg['percorsi']['staging']
os.makedirs(OUT, exist_ok=True)

def clean(v):
    s = str(v)
    return '' if s in ('nan', 'None', '') else s.strip()

def slug(t):
    t = re.sub(r'[^\w\s-]', '', t.lower())
    return re.sub(r'[\s]+', '-', t.strip())[:70]

def stelle(rating):
    try:
        r = float(rating)
        return '★' * int(r) + ('½' if r % 1 >= 0.5 else '') if r > 0 else 'non valutato'
    except:
        return 'non valutato'

def hash_riga(row):
    chiave = f"{clean(row.get('Your review',''))}|{clean(row.get('Your rating',''))}|{clean(row.get('Personal Note',''))}"
    return hashlib.md5(chiave.encode()).hexdigest()

def wine_id(row):
    url = clean(row.get('Link to wine', ''))
    if url:
        m = re.search(r'/wines/(\d+)', url)
        if m: return m.group(1)
    return slug(f"{clean(row.get('Winery',''))} {clean(row.get('Wine name',''))}")

def genera_md(row, wid, h):
    cantina   = clean(row.get('Winery', ''))
    nome      = clean(row.get('Wine name', ''))
    vintage   = clean(row.get('Vintage', ''))
    regione   = clean(row.get('Region', ''))
    paese     = clean(row.get('Country', ''))
    stile     = clean(row.get('Regional wine style', ''))
    tipo      = clean(row.get('Wine type', ''))
    avg_r     = clean(row.get('Average rating', ''))
    tuo_r     = clean(row.get('Your rating', ''))
    recensione= clean(row.get('Your review', ''))
    nota      = clean(row.get('Personal Note', ''))
    finestra  = clean(row.get('Drinking Window', ''))
    url       = clean(row.get('Link to wine', ''))
    data_scan = clean(row.get('Scan date', ''))[:10]

    md  = f'---\ntipo: recensione_vino\ncantina: "{cantina}"\nvino: "{nome}"\n'
    md += f'vintage: {vintage}\nregione: {regione}\npaese: {paese}\n'
    md += f'tipo_vino: {tipo}\nstile: {stile}\nrating_medio_vivino: {avg_r}\n'
    md += f'tuo_rating: {tuo_r}\ndata_degustazione: {data_scan}\nfinestra_consumo: {finestra}\n'
    md += f'url: {url}\nvivino_id: {wid}\nhash: {h}\n'
    md += f'attribuzione: tuo_pensiero_pubblicato\n'
    md += f'tags: [vino, sommelier, {tipo.lower()}, {paese.lower()}]\n---\n\n'
    md += f'# {nome}'
    if vintage: md += f' - {vintage}'
    md += f'\n**{cantina}** | {regione}, {paese} | {tipo}\n'
    md += f'**Tuo rating:** {tuo_r} {stelle(tuo_r)}   **Media Vivino:** {avg_r}\n\n'
    if finestra:
        md += f'*Finestra di consumo: {finestra}*\n\n'
    md += f'## La tua recensione\n{recensione if recensione else "_Nessuna recensione_"}\n\n'
    if nota:
        md += f'## Nota personale\n{nota}\n\n'
    md += f'## Riflessioni\n_Aggiungi considerazioni aggiuntive_\n\n'
    md += f'## Abbinamenti\n_Piatti, occasioni, stagionalita_\n\n'
    if url:
        md += f'[Scheda Vivino]({url})\n'
    return md

def aggiorna_recensione(filepath, row, nuovo_hash):
    content    = open(filepath, encoding='utf-8').read()
    recensione = clean(row.get('Your review', ''))
    tuo_r      = clean(row.get('Your rating', ''))
    content = re.sub(r'^hash:.*$', f'hash: {nuovo_hash}', content, flags=re.MULTILINE)
    content = re.sub(r'^tuo_rating:.*$', f'tuo_rating: {tuo_r}', content, flags=re.MULTILINE)
    nuova_rec = f'## La tua recensione\n{recensione if recensione else "_Nessuna recensione_"}\n\n'
    content = re.sub(r'## La tua recensione\n.*?\n\n', nuova_rec, content, flags=re.DOTALL)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == '__main__':
    csv_files = list(dict.fromkeys(
        glob.glob(os.path.join(STAGING, '**/*ivino*.csv'), recursive=True) +
        glob.glob(os.path.join(STAGING, '**/*Vivino*.csv'), recursive=True)
    ))
    if not csv_files:
        print('Nessun CSV Vivino trovato in import_staging/')
        sys.exit(1)

    df = pd.read_csv(csv_files[0])
    df_rec = df[
        (df['Your review'].notna() & (df['Your review'].astype(str).str.len() > 5)) |
        (df['Your rating'].notna() & (df['Your rating'] > 0))
    ]
    print(f'Vini con recensione o rating: {len(df_rec)}')

    creati = aggiornati = saltati = 0
    for _, row in df_rec.iterrows():
        wid      = wine_id(row)
        h        = hash_riga(row)
        cantina  = slug(clean(row.get('Winery', 'cantina')))
        nome     = slug(clean(row.get('Wine name', 'vino')))
        vintage  = clean(row.get('Vintage', ''))[:4]
        filename = f"vivino_{cantina}_{nome}_{vintage}_{wid[:8]}.md"
        filepath = os.path.join(OUT, filename)

        if os.path.exists(filepath):
            content = open(filepath, encoding='utf-8').read()
            m = re.search(r'^hash:\s*(\w+)', content, re.MULTILINE)
            if m and m.group(1) == h:
                saltati += 1
                continue
            aggiorna_recensione(filepath, row, h)
            aggiornati += 1
        else:
            md = genera_md(row, wid, h)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(md)
            creati += 1

    print(f'Completato: {creati} creati, {aggiornati} aggiornati, {saltati} saltati.')
