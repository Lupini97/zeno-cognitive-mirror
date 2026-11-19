# zeno ask — flusso di query interattiva (target: < 20 s totali).
#
#   domanda → Router 3B (classificazione, <5s)
#           → retrieval mirato su sqlite-vec + FTS5 (<1s)
#           → Motore 7-8B con SOLO i top-k chunk (max 5)
#           → risposta con citazione delle fonti (file + data)
#
# La velocità viene dalla riduzione del contesto, non dal modello: nessuna
# query rilegge il corpus. Pesi di strato ereditati dalla filosofia V5:
# il pensiero esplicito dell'utente vale più del materiale acquisito.

import json
import os
import re
import time

from zeno_engine import generi, indicizzatore, modelli
from zeno_engine.config import con_nome, nome_utente

PESI_STRATO = {'esplicito': 1.5, 'inferito': 1.3, 'dialogico': 1.1, 'acquisito': 1.0,
               'narratore': 0.8}

# Genere della fonte (generi.py, deciso con l'utente il 2026-09-28): per la
# conoscenza un saggio pesa più di un romanzo, dove contano temi e belle frasi,
# non i personaggi. Il testo dei romanzi dell'utente è marcato 'narratore' al
# posto di 'esplicito': lì parla il narratore, non lui — prima ne usciva
# "la tua posizione" presa da un personaggio.
PESI_GENERE = {generi.NARRATIVA_LETTA: 0.8, generi.POESIA_LETTA: 0.9}
# Quota di trama nel chunk (assertions.ruolo, giudice_narrativa): un chunk di
# sola trama scende a metà peso. Non vale se il libro è nominato nella domanda.
SCONTO_TRAMA = 0.5

# Un libro POSSEDUTO non è un libro LETTO. Il 2026-08-24, su 702 schede libro,
# 474 erano di libri mai letti (263 `da-leggere`, 211 `non-applicabile`, cioè
# libri per bambini presenti solo a titolo nominativo) e 253 di quelle avevano
# la sinossi Wikipedia indicizzata con lo stesso peso di un libro finito. È
# così che "Addio, Miss Marple" — mai letto, `conoscenza: da-leggere`, sinossi
# completa in scheda — è entrato in una risposta come se fosse sapere di
# L'utente. Il moltiplicatore non li cancella (a una domanda diretta sul titolo
# restano di gran lunga i primi) ma impedisce loro di occupare gli slot in una
# domanda tematica.
PESI_CONOSCENZA = {'da-leggere': 0.45, 'non-applicabile': 0.25}

# Spie di "domanda personale" (ereditate dalla config V5, retrieval.keywords_query_tematica)
KEYWORDS_PERSONALE = (
    'cosa penso', 'come la penso', 'mia visione', 'mio pensiero', 'come ragiono',
    'cosa credo', 'mia opinione', 'il mio pensiero', 'mia idea', 'mia etica',
    'cosa pensa ' + nome_utente().lower(), 'penso io', 'che penso',
    # Aggiunte il 2026-07-29: "la mia posizione sulla guerra in Ucraina" non
    # veniva riconosciuta come personale, quindi il retrieval non privilegiava
    # lo strato esplicito e la risposta attribuiva all'utente le tesi degli
    # articoli che aveva soltanto letto.
    'mia posizione', 'come vedo', 'cosa ne penso', 'secondo me', 'mio parere',
    'mia tesi', 'ho imparato', 'la mia esperienza', 'mi convince', 'mia lettura',
)

PROMPT_ROUTER = """Classifica la domanda dell'utente. Rispondi solo col JSON.
- topic: il tema più pertinente tra: {temi}
- personale: true se la domanda riguarda il pensiero/le posizioni di @UTENTE@
  (es. "cosa penso di...", "la mia visione su..."), false se è una domanda di merito."""

PROMPT_MOTORE = """Sei Zeno, l'archivio cognitivo personale di @UTENTE@. Rispondi in italiano.

REGOLE FERREE:
1. Usa SOLO le informazioni nei frammenti numerati qui sotto. Niente conoscenza esterna.
2. Cita i frammenti con [1], [2]... dopo ogni affermazione che ne deriva.
3. Se i frammenti non bastano a rispondere, dichiara: "LACUNA: l'archivio non
   copre questo aspetto" e di' che cosa manca. Non inventare mai.
4. Distingui il pensiero di @UTENTE@ (frammenti "esplicito"/"dialogico") dal
   materiale studiato (frammenti "acquisito"). Il materiale acquisito è la voce
   degli AUTORI CHE @UTENTE_MAIUSC@ HA LETTO, MAI la sua posizione: non scrivere mai
   "la tua posizione è..." basandoti su un frammento "acquisito". I frammenti
   "narratore" vengono dai romanzi di @UTENTE@: parlano il narratore e i
   personaggi, non lui. Usali per i temi e le frasi, mai come sua posizione.
5. Un frammento marcato "LIBRO NON LETTO" viene da una scheda di un libro che
   @UTENTE@ NON ha letto (solo sinossi/quarta di copertina). Non attribuirgli
   mai una lettura, un giudizio o un ricordo: se lo usi, dichiara che ti basi
   sulla sola sinossi disponibile.
6. Se qui sotto ci sono CITAZIONI RILEVANTI, e sono pertinenti alla domanda,
   riprendile SEMPRE testualmente tra virgolette nella risposta (con l'autore
   indicato), non parafrasarle.
7. Risposta compatta: massimo 250 parole."""


def router_classifica(domanda: str, cfg: dict, temi: list[str]) -> dict:
    schema = {'type': 'object',
              'properties': {'topic': {'type': 'string', 'enum': temi},
                             'personale': {'type': 'boolean'}},
              'required': ['topic', 'personale']}
    try:
        # priorita=True: router_classifica ha un solo chiamante (rispondi(),
        # sempre in primo piano davanti a chi aspetta una risposta).
        r = modelli.client().chat(
            model=cfg['modelli']['router'],
            messages=[{'role': 'system',
                       'content': PROMPT_ROUTER.format(temi=', '.join(temi))},
                      {'role': 'user', 'content': domanda}],
            format=schema, options={'temperature': 0.0, 'num_ctx': 2048, 'num_predict': 400},
            keep_alive='30m', priorita=True)
        return json.loads(r['message']['content'])
    except Exception:
        return {'topic': None, 'personale': False}


def _fts_query(domanda: str) -> str:
    """
    Domanda libera → query FTS5 tollerante (OR di parole significative).

    Prefisso (`termine*`), non match esatto: il tokenizer unicode61 non fa
    stemming, quindi "citazioni" e "citazione" sono due token DIVERSI — una
    domanda al plurale non trovava MAI un titolo al singolare (bug reale,
    2026-09-07: "le ultime citazioni dei libri letti" non trovava nessuno dei
    file "Citazione da <libro>", pur essendo esattamente quello che chiedeva).
    Si taglia l'ultima lettera alle parole di almeno 6 caratteri — grezzo, ma
    porta plurale e singolare sullo stesso stem nella grande maggioranza dei
    casi italiani.
    """
    parole = re.findall(r'\w{4,}', domanda.lower())
    stemmi = (p[:-1] if len(p) >= 6 else p for p in parole[:8])
    return ' OR '.join(f'{p}*' for p in stemmi)


def recupera(con, domanda: str, cfg: dict, personale: bool = False,
             top_k: int | None = None) -> list[dict]:
    """Retrieval ibrido: vettoriale (candidati larghi) + FTS5, ri-pesato per strato."""
    top_k = top_k or cfg['retrieval']['top_k']
    qv = indicizzatore.embed_query(domanda, cfg)
    usa_reranker = bool(cfg['retrieval'].get('reranker'))

    candidati: dict[int, float] = {}
    # Col reranker si pesca più largo (30): è lui a scegliere, e la prova
    # sulle domande sintetiche è stata fatta così.
    for cid, dist in con.execute(
            'SELECT chunk_id, distance FROM vec_chunks WHERE embedding MATCH ? AND k = ?',
            (qv, 30 if usa_reranker else 20)):
        candidati[cid] = 1.0 - dist
    fts = _fts_query(domanda)
    primo_fts = None
    if fts:
        try:
            # ORDER BY rank: senza, FTS5 ritorna le righe in un ordine interno
            # arbitrario (non di rilevanza) — su una OR fra 4 parole comuni può
            # matchare MIGLIAIA di chunk, e LIMIT 10 senza ordinamento prende
            # 10 righe a caso invece delle 10 più pertinenti (bug reale,
            # 2026-09-07: su 1.096 match il chunk giusto restava fuori dal
            # taglio finché non si è aggiunto l'ordinamento per bm25).
            for i, (cid,) in enumerate(con.execute(
                    'SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? '
                    'ORDER BY rank LIMIT 10', (fts,))):
                if i == 0:
                    primo_fts = cid
                if cid in candidati:
                    candidati[cid] += 0.05      # già in vetta per via vettoriale: bonus piccolo
                else:
                    # SOLO lessicale, nessun aggancio vettoriale: 0.05 come
                    # base era un bug, non un valore prudente — più sotto lo
                    # score è base*peso, quindi 0.05×1.8 (esplicito+personale)
                    # fa 0.09, sotto qualsiasi 'acquisito' anche vettorialmente
                    # debole (bug reale, 2026-09-07: "citazioni dei libri
                    # letti" trovava il file "Citazione da <libro>" giusto, ma
                    # con peso di strato non lo faceva MAI risalire). Una
                    # COSTANTE fissa però creava un secondo bug: due match
                    # lessicali di forza diversissima finivano con lo STESSO
                    # score*peso e il pareggio si decideva per ordine di
                    # inserimento nel dict, non per rilevanza — quindi si
                    # scala per posizione nella classifica bm25 (1°=migliore),
                    # nell'ordine di un match vettoriale modesto: competitivo
                    # senza mai battere un hit vettoriale forte.
                    candidati[cid] = 0.34 - i * 0.015
        except Exception:
            pass                                # query FTS malformata: solo vettoriale

    if usa_reranker:
        candidati = riordina(con, domanda, candidati, cfg)
    scelti = senza_doppioni(arricchisci(con, candidati, personale, domanda))
    return garantisci(seleziona(scelti, top_k), scelti, primo_fts)


def _firma(testo: str) -> set[str]:
    return set(re.findall(r'\w{4,}', (testo or '').lower()))


def senza_doppioni(scelti: list[dict], soglia: float = 0.85) -> list[dict]:
    """Toglie i frammenti quasi identici a uno già più in alto in classifica
    (Jaccard sulle parole di 4+ lettere). La stessa recensione vive sia fra le
    riflessioni sia nella scheda del libro: senza, occupava due dei 5 posti
    del contesto (giudizio esterno del 17/11/2026)."""
    out, firme = [], []
    for f in scelti:
        s = _firma(f['text'])
        if s and any(len(s & g) / len(s | g) > soglia for g in firme):
            continue
        out.append(f)
        firme.append(s)
    return out


def garantisci(finali: list[dict], scelti: list[dict], primo_fts: int | None,
               top_k: int = 5) -> list[dict]:
    """Il primo risultato per parole chiave (bm25) entra sempre fra i primi
    top_k. Sul punteggio vettoriale un brano può restare 20°-30° anche quando
    è l'unico a contenere il nome o il termine chiesto, e i due pezzi più
    "vicini" dello stesso file gli rubano il posto (set di prova, d012 e d017).
    Prende il posto del secondo pezzo di un file già presente, altrimenti
    dell'ultimo. Misurato il 17/11/2026: brano giusto fra i 5 da 19 a 21 su
    23 domande reali; set sintetico fonte@5 0,87→0,89, frammento@5 0,555→0,64."""
    if primo_fts is None or not finali or any(f['chunk_id'] == primo_fts for f in finali):
        return finali
    nuovo = next((f for f in scelti if f['chunk_id'] == primo_fts), None)
    if nuovo is None:                           # scartato come doppione
        return finali
    testa = finali[:top_k]
    visti, posto = set(), len(testa) - 1
    for j, f in enumerate(testa):
        if f['path'] in visti:
            posto = j
        visti.add(f['path'])
    return testa[:posto] + testa[posto + 1:] + [nuovo] + finali[top_k:]


def riordina(con, domanda: str, candidati: dict[int, float], cfg: dict) -> dict[int, float]:
    """Cross-encoder (mlx_rerank) sui migliori N candidati: il suo punteggio
    prende il posto di quello grezzo, poi i pesi di strato come sempre.
    Se il reranker non risponde si torna ai candidati di prima: una domanda
    deve avere comunque una risposta."""
    n = int(cfg['retrieval'].get('reranker_candidati', 20))
    scelti = sorted(candidati, key=candidati.get, reverse=True)[:n]
    testi = []
    for cid in scelti:
        riga = con.execute('SELECT c.text, c.section, s.title FROM chunks c '
                           'JOIN sources s ON s.id = c.source_id WHERE c.id = ?',
                           (cid,)).fetchone()
        testi.append(indicizzatore.testo_da_embeddare(riga[2], riga[1], riga[0])
                     if riga else '')
    try:
        punti = modelli.client().rerank(model=cfg['modelli']['reranker'], query=domanda,
                                        documents=testi, priorita=True)['scores']
    except Exception:
        return candidati
    return {cid: float(p) for cid, p in zip(scelti, punti)}


def _titolo_richiesto(domanda_lc: str, titolo: str) -> bool:
    """
    Il titolo della fonte è nominato esplicitamente nella domanda? Serve a
    distinguere un LOOKUP diretto ("parlami del libro X") da una domanda
    tematica che userebbe X solo come prova a supporto — le due intenzioni
    non vanno pesate allo stesso modo (vedi PESI_CONOSCENZA più sotto).

    Richiede almeno 2 parole distintive del titolo (≥5 lettere) dentro la
    domanda, o tutte se il titolo ne ha meno di 2: un titolo di una parola
    sola ("Mitologia") non deve bastare a esentare qualunque domanda che la
    nomini di sfuggita.
    """
    parole_titolo = re.findall(r'[a-zàèéìòù]{5,}', titolo.lower())
    if not parole_titolo:
        return False
    trovate = sum(1 for p in parole_titolo if p in domanda_lc)
    soglia = min(2, len(parole_titolo))
    return trovate >= soglia


def arricchisci(con, candidati: dict[int, float], personale: bool = False,
                domanda: str = '') -> list[dict]:
    """Da {chunk_id: score_grezzo} a frammenti completi, ri-pesati per strato e
    ordinati. Estratto da recupera() per essere riusato dal retrieval iterativo
    (interleave.py), che deve ri-punteggiare a ogni iterazione."""
    domanda_lc = domanda.lower()
    scelti = []
    for cid, base in candidati.items():
        riga = con.execute(
            'SELECT c.text, c.section, s.title, s.path, s.created_at, s.strato, '
            's.conoscenza, s.genere FROM chunks c JOIN sources s ON s.id = c.source_id '
            'WHERE c.id = ?', (cid,)).fetchone()
        if not riga:
            continue
        testo, sezione, titolo, path, created, strato, conoscenza, genere = riga
        if genere == generi.NARRATIVA_PROPRIA and generi.voce_narratore(path or ''):
            strato = 'narratore'
        peso = PESI_STRATO.get(strato, 1.0)
        if personale and strato == 'esplicito':
            peso += 0.3                         # domanda sul pensiero dell'utente
        # La penalità "libro non letto" ha senso per una domanda che userebbe
        # il libro come prova di un'opinione (non l'ha letto → non può averla
        # assorbita). Non ha senso per un LOOKUP diretto ("parlami del libro
        # X"): lì il libro è esattamente ciò che si sta cercando, e la
        # penalità può far perdere il match migliore in classifica a favore
        # di libri non pertinenti (bug reale, 2026-09-08: "Il Prigioniero di
        # Kaag" primo su 20 per punteggio vettoriale — 0,681 — ma spinto fuori
        # dai primi 5 dalla penalità ×0,25 su un libro 'non-applicabile',
        # sostituito da quattro libri del tutto estranei alla domanda).
        # Il marcatore "LIBRO NON LETTO" nel contesto resta INVARIATO: solo il
        # peso di classifica cambia, non l'avviso che arriva al modello.
        if not (domanda_lc and _titolo_richiesto(domanda_lc, titolo)):
            peso *= PESI_CONOSCENZA.get(conoscenza or '', 1.0)
            peso *= PESI_GENERE.get(genere, 1.0)
            if genere in generi.NARRATIVI or genere == generi.POESIA_LETTA:
                tot, trama = con.execute(
                    "SELECT COUNT(*), SUM(ruolo = 'trama') FROM assertions WHERE chunk_id = ?",
                    (cid,)).fetchone()
                if tot:
                    peso *= 1 - SCONTO_TRAMA * (trama or 0) / tot
        scelti.append({'chunk_id': cid, 'score': base * peso, 'text': testo,
                       'section': sezione, 'title': titolo, 'path': path,
                       'created_at': created, 'strato': strato,
                       'conoscenza': conoscenza})
    scelti.sort(key=lambda x: x['score'], reverse=True)
    return scelti


def seleziona(scelti: list[dict], top_k: int, max_per_fonte: int = 2) -> list[dict]:
    """Taglio a k FISSO (comportamento storico).
    Massimo 2 frammenti per fonte: senza, una singola maieutica lunga può
    occupare tutti gli slot (visto sul campo: 5/5 dallo stesso file) e la
    risposta perde prospettiva."""
    finali, per_fonte = [], {}
    for f in scelti:
        if per_fonte.get(f['path'], 0) >= max_per_fonte:
            continue
        finali.append(f)
        per_fonte[f['path']] = per_fonte.get(f['path'], 0) + 1
        if len(finali) >= top_k:
            break
    return finali


def _e_citazione_trascritta(f: dict) -> bool:
    """File dedicato a una citazione trascritta da un libro/fonte: il titolo
    inizia con 'Citazione' o il filename con 'cit_' (convenzione dei file che
    L'utente crea a mano, es. 'Citazione da Atti Umani - Han Kang'). L'INTERO
    passo è la citazione, non uno span tra virgolette."""
    if (f.get('title') or '').strip().lower().startswith('citazione'):
        return True
    return os.path.basename(f.get('path') or '').lower().startswith('cit_')


def _fonte_citabile(f: dict) -> bool:
    """
    Regola dell'utente (2026-07-14): le citazioni si riprendono come in una
    tesi di studio — da libri/articoli/formazione (strato acquisito), dalle
    sue pubblicazioni, E dai file di citazione che ha trascritto da un libro
    (anche se salvati come esplicito — è pur sempre "un libro in cui ho
    riportato una citazione", 2026-07-22). Riflessioni e dialoghi generici
    NON sono fonti di citazione: lì il discorso tra virgolette è un pensiero.
    """
    if f['strato'] in ('acquisito', 'narratore'):   # narratore: le belle frasi dei romanzi
        return True
    if _e_citazione_trascritta(f):
        return True
    path = (f.get('path') or '').lower()
    return f['strato'] == 'esplicito' and '/pubblicazioni' in path


def _passo_citazione(testo: str) -> str:
    """Estrae il passo da un file-citazione: toglie i marcatori blockquote
    '>' e il frontmatter, collassa gli spazi."""
    righe = []
    for r in testo.splitlines():
        r = r.strip()
        if r in ('---',) or r.startswith('#'):
            continue
        righe.append(r.lstrip('>').strip())
    return re.sub(r'\s+', ' ', ' '.join(x for x in righe if x)).strip()


def _attribuzione_citazione(titolo: str) -> str:
    """'Citazione da Atti Umani - Han Kang' → 'Atti Umani - Han Kang'."""
    t = (titolo or '').strip()
    for pref in ('citazione da ', 'citazione: ', 'citazione '):
        if t.lower().startswith(pref):
            return t[len(pref):].strip() or t
    return t


def citazioni_pertinenti(frammenti: list[dict], max_per_frammento: int = 2,
                         max_totali: int = 4) -> list[dict]:
    """
    Citazioni dentro i frammenti già recuperati per la domanda — "pertinenti
    al tema" per costruzione. Due modi:
    - file-citazione trascritta (_e_citazione_trascritta): l'intero passo è la
      citazione (i file 'Citazione da X' sono blockquote senza «», quindi
      estrai_citazioni non li vedrebbe), attribuita all'opera dal titolo;
    - altre fonti citabili (libri acquisiti, pubblicazioni): span tra
      virgolette caporali/curve nel testo grezzo (archivista.estrai_citazioni).
    """
    from zeno_engine import archivista
    citazioni = []
    visti = set()
    for f in frammenti:
        if not _fonte_citabile(f):
            continue
        if _e_citazione_trascritta(f):
            passo = _passo_citazione(f['text'])
            if len(passo) < 25:
                continue
            chiave = passo[:120].lower()
            if chiave in visti:
                continue
            visti.add(chiave)
            citazioni.append({'testo': passo[:700],
                              'autore': _attribuzione_citazione(f['title']),
                              'fonte': f['title'], 'strato': f['strato'],
                              'chunk_id': f['chunk_id']})
            if len(citazioni) >= max_totali:
                return citazioni
            continue
        for c in archivista.estrai_citazioni(f['text'])[:max_per_frammento]:
            chiave = re.sub(r'\s+', ' ', c.lower()).strip()
            if chiave in visti:
                continue
            visti.add(chiave)
            autore = con_nome({'acquisito': f['title'],
                               'narratore': f"@UTENTE@, dal romanzo ({f['title']})"}.get(f['strato'], '@UTENTE@'))
            citazioni.append({'testo': c, 'autore': autore, 'fonte': f['title'],
                              'strato': f['strato'], 'chunk_id': f['chunk_id']})
            if len(citazioni) >= max_totali:
                return citazioni
    return citazioni


def _profilo_cognitivo(cfg: dict) -> str:
    """
    Profilo MBTI calibrato (zeno_config.yaml, sezione V5 profilo_cognitivo)
    applicato STATICAMENTE alla forma delle risposte — principio V5: niente
    ricalibrazione dinamica, il profilo orienta lo stile, mai i contenuti.
    """
    p = (cfg.get('_globale') or {}).get('profilo_cognitivo') or {}
    if not p or not p.get('tipo'):   # senza tipo il profilo non dice nulla
        return ''
    bias = ', '.join(p.get('bias_strutturali', [])[:3])
    return (f"\n\nPROFILO COGNITIVO DI @UTENTE_MAIUSC@ (orienta la FORMA, mai i contenuti): "
            f"{p.get('tipo', '')}, funzione dominante {p.get('funzione_dominante', '')}. "
            f"REGOLA ANTI-LOOP: se il ragionamento supera 3 livelli di astrazione "
            f"consecutivi, riportalo a esempi concreti. Bias da compensare: {bias}.")


def _num_ctx(*blocchi: str) -> int:
    """Finestra di contesto adeguata al prompt effettivo (~3,6 caratteri per
    token in italiano), a scalini e con un tetto. Serve col retrieval iterativo,
    che può portare 8 frammenti invece di 5: con num_ctx fisso a 4096 il prompt
    verrebbe troncato in silenzio da Ollama e la risposta peggiorerebbe proprio
    per via del recupero migliore."""
    caratteri = sum(len(b or '') for b in blocchi) + 1200   # prompt di sistema
    token = caratteri / 3.6 + 400                           # + num_predict
    for scalino in (4096, 6144, 8192):
        if token <= scalino:
            return scalino
    return 8192


def rispondi(con, domanda: str, cfg: dict, verboso: bool = True) -> dict:
    tempi = {}
    t0 = time.time()
    # Il classificatore 3B a query-time è OPT-IN (retrieval.router_llm: true):
    # oggi il topic non guida il retrieval, e su 16 GB tenere il 3B residente
    # accanto a motore+embeddings manda la macchina in thrashing (misurato:
    # 3 modelli residenti → 1 tok/s). L'euristica a keyword copre il caso d'uso;
    # il 3B resta il classificatore dei job batch (Indicizzatore).
    if cfg['retrieval'].get('router_llm', False):
        temi = [n for (n,) in con.execute('SELECT name FROM topics WHERE parent_id IS NULL')]
        rotta = router_classifica(domanda, cfg, temi)
    else:
        dl = domanda.lower()
        rotta = {'topic': None,
                 'personale': any(k in dl for k in KEYWORDS_PERSONALE)}
    tempi['router'] = time.time() - t0

    t1 = time.time()
    personale = rotta.get('personale', False)
    # Retrieval ITERATIVO (interleave.py) se attivo in config: k adattivo +
    # espansione via entità-ponte. Misurato il 2026-07-29 su 8 query reali:
    # +20% frammenti, +23% fonti distinte, nessuna regressione, +0,02 s.
    # Fallback silenzioso al single-shot se qualcosa va storto: una query deve
    # rispondere comunque.
    traccia = []
    if cfg.get('retrieval', {}).get('interleave', False):
        try:
            from zeno_engine import interleave
            frammenti, traccia = interleave.recupera_interleave(
                con, domanda, cfg, personale=personale)
        except Exception:
            frammenti = recupera(con, domanda, cfg, personale=personale)
    else:
        frammenti = recupera(con, domanda, cfg, personale=personale)
    tempi['retrieval'] = time.time() - t1
    if not frammenti:
        return {'risposta': 'LACUNA: nessun contenuto pertinente nell\'archivio.',
                'fonti': [], 'rotta': rotta, 'tempi': tempi}

    contesto = '\n\n'.join(
        f"[{i}] ({f['strato']}, {f['created_at'] or 'data ignota'}"
        f"{', LIBRO NON LETTO: ' + f['conoscenza'] if f.get('conoscenza') in PESI_CONOSCENZA else ''}"
        f") {f['title']}{' — ' + f['section'] if f['section'] else ''}\n{f['text']}"
        for i, f in enumerate(frammenti, 1))

    # LACUNA STRUTTURALE: domanda sul pensiero dell'utente ma nessun frammento
    # del suo pensiero diretto. Senza questo avviso il modello sintetizza il
    # materiale ACQUISITO e lo attribuisce a lui — verificato il 2026-07-29 su
    # "la mia posizione sulla guerra in Ucraina": 5 fonti su 5 erano articoli
    # militari letti, e la risposta cominciava con "La tua posizione sembra
    # essere fortemente supportiva...". È la contaminazione che l'architettura
    # di Zeno esiste per impedire: qui va dichiarata, non colmata.
    avviso = ''
    if personale:
        suoi = [str(i) for i, f in enumerate(frammenti, 1)
                if f['strato'] in ('esplicito', 'dialogico')]
        if not suoi:
            avviso = ('ATTENZIONE: la domanda riguarda il pensiero di @UTENTE@, ma '
                      'NESSUN frammento qui sotto viene dai suoi scritti — sono '
                      'tutti materiale che ha letto. Devi quindi APRIRE la risposta '
                      'con "LACUNA: l\'archivio non contiene una tua posizione '
                      'esplicita su questo", e poi limitarti a riassumere che cosa '
                      'dicono gli autori letti, attribuendolo A LORO. Non dedurre '
                      'né inventare una posizione di @UTENTE@.\n\n')
        elif len(suoi) * 2 < len(frammenti):
            # Il pensiero dell'utente è MINORANZA fra i frammenti: senza dirlo
            # esplicitamente il modello annega quella voce nel materiale letto e
            # presenta come "la tua posizione" le tesi degli autori (verificato
            # il 2026-07-29 con 1 frammento esplicito su 8).
            avviso = (f'ATTENZIONE: di questi frammenti, SOLO i numeri '
                      f'[{"], [".join(suoi)}] contengono il pensiero di @UTENTE@; '
                      f'tutti gli altri sono materiale che ha soltanto letto. '
                      f'La sua posizione va ricavata SOLO da quei frammenti, e '
                      f'va detto chiaramente quanto è sottile la base. Il resto '
                      f'si riporta come "gli autori letti sostengono che...", '
                      f'mai come pensiero suo.\n\n')

    citazioni = citazioni_pertinenti(frammenti)
    blocco_citazioni = ''
    if citazioni:
        blocco_citazioni = 'CITAZIONI RILEVANTI (riprendile testualmente se pertinenti):\n' + \
            '\n'.join(f'- «{c["testo"]}» — {c["autore"]}' for c in citazioni) + '\n\n'

    t2 = time.time()
    from zeno_engine import stato
    # STATO.md come contesto di base (brief §9): fotografia di temi caldi,
    # contraddizioni e propositi, sempre presente e volutamente piccola.
    quadro = stato.per_contesto()
    preambolo = f'QUADRO GENERALE (STATO.md):\n{quadro}\n\n' if quadro else ''
    r = modelli.client().chat(
        model=modelli.prepara_motore(cfg, 'ask'),
        messages=[{'role': 'system',
                   'content': PROMPT_MOTORE + _profilo_cognitivo(cfg)},
                  {'role': 'user',
                   'content': f'{preambolo}{avviso}{blocco_citazioni}'
                              f'FRAMMENTI:\n{contesto}\n\nDOMANDA: {domanda}'}],
        # 4096 di contesto bastano: 5 chunk ≤ 9000 caratteri ≈ 2500 token.
        # keep_alive lungo: router+motore+embeddings coesistono in 16 GB (~9 GB),
        # il ricaricamento a ogni query costava più della generazione stessa.
        # Col retrieval iterativo i frammenti possono arrivare a 8: si alza il
        # contesto quanto basta, altrimenti il prompt verrebbe TRONCATO in
        # silenzio e la risposta peggiorerebbe proprio grazie al recupero
        # migliore. Si resta comunque su una finestra piccola, per principio.
        options={'temperature': 0.3, 'num_ctx': _num_ctx(contesto, preambolo,
                                                          blocco_citazioni, avviso),
                 'num_predict': 400},
        keep_alive='30m', priorita=True)
    tempi['motore'] = time.time() - t2
    tempi['totale'] = time.time() - t0

    fonti = [{'n': i, 'title': f['title'],
              'file': os.path.basename(f['path']), 'path': f['path'],
              'data': f['created_at'], 'strato': f['strato'],
              'score': round(f['score'], 3)}
             for i, f in enumerate(frammenti, 1)]
    return {'risposta': r['message']['content'], 'fonti': fonti,
            'rotta': rotta, 'tempi': tempi, 'citazioni': citazioni}
