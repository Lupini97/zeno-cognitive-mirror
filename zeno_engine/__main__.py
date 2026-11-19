# CLI del motore epistemico:
#
#   python -m zeno_engine ingest [--forza] [--root PATH]
#   python -m zeno_engine audit
#   python -m zeno_engine campione [N]
#
# (ask e brief arrivano nelle fasi C/D.)
# Va eseguito con il venv dedicato: BRAIN/.venv_zeno/bin/python

import argparse
import os
import sys

# Il pacchetto vive in scripts/ accanto ai moduli V5 riusati (strati.py):
# garantiamo che scripts/ sia importabile anche lanciando da altrove.
_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from zeno_engine import config, db, ingest  # noqa: E402
from zeno_engine.config import BRAIN


def cmd_ingest(args):
    cfg = config.load()
    con = db.connect(cfg['db'], vec=True)
    print(f"Ingestione da {args.root or cfg['documents']} → {cfg['db']}")
    r = ingest.ingest_corpus(con, cfg, root=args.root, forza=args.forza)
    try:
        # I vettori non sono legati da foreign key: pulizia orfani esplicita.
        con.execute('DELETE FROM vec_chunks WHERE chunk_id NOT IN (SELECT id FROM chunks)')
        con.commit()
    except Exception:
        pass                                # vec_chunks non ancora creata
    n_sources = con.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
    n_chunks = con.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
    print(f"nuovi {r['nuovo']} | aggiornati {r['aggiornato']} | invariati {r['invariato']} "
          f"| vuoti {r['vuoto']} | errori {r['errore']} | fonti rimosse {r['rimossi']}")
    for p in r['errori_dettaglio']:
        print(f'  ERRORE: {p}')
    print(f'Totale in zeno.db: {n_sources} fonti, {n_chunks} chunk')
    con.close()


def cmd_audit(args):
    cfg = config.load()
    if not os.path.exists(cfg['db']):
        print(f"zeno.db non esiste ancora ({cfg['db']}): lancia prima `ingest`.")
        return
    con = db.connect(cfg['db'])
    print(f"== zeno.db: {cfg['db']} ({os.path.getsize(cfg['db']) / 1e6:.1f} MB) ==\n")
    for etichetta, q in [
        ('Fonti', 'SELECT COUNT(*) FROM sources'),
        ('Chunk', 'SELECT COUNT(*) FROM chunks'),
        ('Asserzioni', 'SELECT COUNT(*) FROM assertions'),
        ('Topic', 'SELECT COUNT(*) FROM topics'),
        ('Entità', 'SELECT COUNT(*) FROM entities'),
        ('Archi', 'SELECT COUNT(*) FROM edges'),
    ]:
        print(f'{etichetta:12s} {con.execute(q).fetchone()[0]:>8,}')
    for titolo, colonna in [('strato', 'strato'), ('area', 'area'), ('tipo', 'type')]:
        print(f'\nFonti per {titolo}:')
        for nome, n, nc in con.execute(
                f'SELECT s.{colonna}, COUNT(DISTINCT s.id), COUNT(c.id) FROM sources s '
                f'LEFT JOIN chunks c ON c.source_id = s.id '
                f'GROUP BY s.{colonna} ORDER BY 3 DESC'):
            print(f'  {str(nome):28s} {n:>6,} fonti  {nc:>8,} chunk')
    con.close()


def cmd_campione(args):
    """N chunk casuali da validare a occhio (checkpoint Fase B)."""
    cfg = config.load()
    con = db.connect(cfg['db'])
    righe = con.execute(
        'SELECT s.path, s.title, s.created_at, c.section, c.text FROM chunks c '
        'JOIN sources s ON s.id = c.source_id ORDER BY RANDOM() LIMIT ?', (args.n,)).fetchall()
    for i, (path, title, created, section, text) in enumerate(righe, 1):
        print(f'\n{"=" * 78}\n[{i}] {title}  ({created})\n    {path}\n    Sezione: {section or "—"}\n{"-" * 78}')
        print(text[:600] + ('…' if len(text) > 600 else ''))
    con.close()


def cmd_archivista(args):
    from zeno_engine import archivista
    cfg = config.load()
    con = db.connect(cfg['db'])
    if args.campione:
        ids = archivista.campiona_documenti(con, args.campione)
        titoli = con.execute(
            f"SELECT type, title FROM sources WHERE id IN ({','.join('?' * len(ids))})",
            ids).fetchall()
        print(f'Campione di {len(ids)} documenti ({cfg["modelli"]["motore_batch"]}):')
        for t, ti in titoli:
            print(f'  - [{t}] {ti}')
        r = archivista.esegui_batch(con, cfg, source_ids=ids)
    else:
        strati = args.strati.split(',') if args.strati else None
        r = archivista.esegui_batch(con, cfg, limite=args.limite, strati=strati,
                                     ruolo=args.ruolo)
    media = r['secondi'] / r['chunk'] if r['chunk'] else 0
    print(f"\nChunk processati {r['chunk']} | asserzioni {r['asserzioni']} "
          f"| falliti {r['falliti']} | saltati {r.get('saltati', 0)} | {media:.1f}s/chunk")
    con.close()
    if r.get('fusibile'):
        # Exit non-zero: la pipeline (`&&` in zeno_launcher.py e negli script di
        # ripresa) deve fermarsi qui, non proseguire a `indicizza` col motore
        # ancora incastrato — visto sul campo il 2026-07-20: ~2 ore di timeout
        # a catena perché lo script non si fermava sull'errore.
        raise SystemExit(1)


def cmd_asserzioni(args):
    """Ultime asserzioni estratte, da validare a occhio (checkpoint Fase C)."""
    cfg = config.load()
    con = db.connect(cfg['db'])
    righe = con.execute(
        'SELECT a.text, a.kind, a.stance, a.confidence, s.title, s.created_at FROM assertions a '
        'JOIN chunks c ON c.id = a.chunk_id JOIN sources s ON s.id = c.source_id '
        'ORDER BY a.id DESC LIMIT ?', (args.n,)).fetchall()
    for testo, kind, stance, conf, titolo, created in righe:
        print(f'• [{kind}/{stance} {conf:.1f}] {testo}\n    ↳ {titolo} ({created})')
    con.close()


def cmd_indicizza(args):
    from datetime import datetime
    from zeno_engine import indicizzatore, processi
    cfg = config.load()
    con = db.connect(cfg['db'], vec=True)

    def _stato(dettaglio):
        processi.aggiorna('indicizza', {
            'stato': 'in_corso', 'ultimo_evento': datetime.now().isoformat(timespec='seconds'),
            'dettaglio': dettaglio})

    _stato(f"embeddings con {cfg['modelli']['embeddings']}…")
    print(f"Embeddings con {cfg['modelli']['embeddings']}…")
    n = indicizzatore.embed_chunks(con, cfg)
    print(f'  {n} chunk embeddati')
    if not args.solo_embeddings:
        _stato(f"topic con {cfg['modelli']['router']}…")
        print(f"Topic delle asserzioni con {cfg['modelli']['router']}…")
        print(f'  {indicizzatore.assegna_topics(con, cfg)} asserzioni classificate')
        _stato('archi di similarità…')
        print('Archi di similarità…')
        print(f'  {indicizzatore.archi_similarita(con, cfg)} archi relates_to creati')
        _stato('community (Louvain)…')
        r_comm = indicizzatore.rileva_community(con)
        print(f"  {r_comm['community']} community, {r_comm['entita_assegnate']} entità assegnate")
        _stato('proposte temi…')
        from zeno_engine import temi
        stats = (cfg.get('_globale', {}).get('percorsi', {}) or {}).get('stats', os.path.join(BRAIN, 'stats'))
        try:
            from zeno_engine import revisione_temi
            nt = revisione_temi.genera(con, cfg)
            temi.proponi_prioritari(con, cfg, cartella=stats, verboso=False)
            print(f'  {nt} temi nuovi proposti + prioritari suggeriti')
        except Exception as e:
            print(f'  proposte temi rimandate: {e}')
    processi.aggiorna('indicizza', {
        'stato': 'completato', 'ultimo_evento': datetime.now().isoformat(timespec='seconds'),
        'dettaglio': f'{n} chunk embeddati'})
    con.close()


def cmd_manutenzione(args):
    """Stato ed esecuzione dei job periodici (registro unico: periodici.REGISTRO).
    `--stato` risponde a "è un bug o è solo che non gira da un mese?"."""
    from datetime import datetime
    from zeno_engine import periodici
    cfg = config.load()
    con = db.connect(cfg['db'])
    if args.stato:
        print(f"{'job':12s} {'cadenza':9s} {'ultima esecuzione':21s} stato")
        print('-' * 68)
        for s in periodici.stato(con, cfg):
            quando = (s['ultimo'].strftime('%Y-%m-%d %H:%M') if s['ultimo']
                      else 'MAI')
            eta = f"({s['giorni_fa']:.0f}g fa)" if s['giorni_fa'] is not None else ''
            if s['stantio']:
                stato_txt = '⚠ STANTIO'
            elif s['dovuto']:
                stato_txt = 'da eseguire'
            else:
                stato_txt = 'aggiornato'
            motore = ' [MLX]' if s['motore'] else ''
            print(f"{s['nome']:12s} {str(s['giorni']) + 'g':9s} "
                  f"{quando + ' ' + eta:21s} {stato_txt}{motore}")
        con.close()
        return
    if args.forza:
        nomi = {j['nome'] for j in periodici.REGISTRO}
        if args.forza not in nomi:
            print(f"Job sconosciuto: {args.forza}. Disponibili: {', '.join(sorted(nomi))}")
            con.close()
            raise SystemExit(2)
        print(f'Eseguo (forzato) il job "{args.forza}"…')
        periodici.esegui_tutti(con, cfg, forza=args.forza)
    else:
        print('Eseguo i job dovuti…')
        esiti = periodici.esegui_tutti(con, cfg, salta_motore=args.solo_locali)
        if not esiti:
            print('  nessun job dovuto: tutto aggiornato.')
    con.close()


def cmd_riclassifica(args):
    """Ri-classificazione una-tantum: rilegge col router-limbo le asserzioni
    forzate su un tema madre dal vecchio router e adotta il nuovo verdetto
    (madre nuova o limbo). Resumibile: rilancia lo stesso comando per riprendere."""
    from zeno_engine import indicizzatore
    cfg = config.load()
    con = db.connect(cfg['db'])
    modello = cfg['modelli']['motore_ask'] if args.motore == 'ask' else cfg['modelli']['router']
    limite = f' — limite {args.limite}' if args.limite else ''
    print(f'Ri-classificazione temi madre con {modello} (enum madri + limbo){limite}…')
    r = indicizzatore.riclassifica_madri(con, cfg, modello=modello, limite=args.limite)
    print(f"\nfatti {r['fatti']}/{r['totale']} | stabili {r['stabili']} | spostati {r['spostati']} "
          f"| limbo {r['limbo']} | override {r['override']}")
    con.close()
    if r['fusibile']:
        print('Interrotto dal fusibile (motore MLX giù): riavvia il demone e rilancia lo STESSO '
              'comando — riprende dal cursore salvato.')
        raise SystemExit(1)


def cmd_schede(args):
    """Genera le schede-tema V6 (cosa sa/come la pensa/tensioni/lacune) dai
    dati puliti di zeno.db, in memoria_elaborata/profilo/temi_v6/."""
    from zeno_engine import schede
    cfg = config.load()
    con = db.connect(cfg['db'])
    n = schede.genera_tutte(con, cfg, solo_prioritari=args.prioritari)
    con.close()
    print(f'{n} schede-tema V6 generate in {schede.DIR_SCHEDE}')


def cmd_temi(args):
    """Rigenera le proposte di temi nuovi (revisione_temi) e i prioritari."""
    from zeno_engine import revisione_temi, temi
    cfg = config.load()
    con = db.connect(cfg['db'])
    stats = (cfg.get('_globale', {}).get('percorsi', {}) or {}).get('stats', os.path.join(BRAIN, 'stats'))
    n = revisione_temi.genera(con, cfg, verboso=True)
    temi.proponi_prioritari(con, cfg, cartella=stats)
    con.close()
    print(f'{n} temi nuovi proposti (stats/revisione_temi/rapporto.md) + prioritari suggeriti. '
          'Conferma dal pannello "Gestione temi" della dashboard.')


def cmd_alias(args):
    """Rigenera le proposte di alias entità (stats/entita_alias_proposte.json).
    Le conferme/rifiuti si fanno dal pannello 'Entità' della dashboard."""
    from zeno_engine import indicizzatore
    cfg = config.load()
    con = db.connect(cfg['db'])
    stats = (cfg.get('_globale', {}).get('percorsi', {}) or {}).get('stats', os.path.join(BRAIN, 'stats'))
    n = indicizzatore.proponi_alias_entita(con, cartella=stats)
    con.close()
    print(f'{n} proposte di alias scritte in {stats}/entita_alias_proposte.json')
    print('Conferma/rifiuta dal pannello "Entità" della dashboard.')


def cmd_ask(args):
    from zeno_engine import ask
    cfg = config.load()
    con = db.connect(cfg['db'], vec=True)
    esito = ask.rispondi(con, args.domanda, cfg)
    print(esito['risposta'])
    if esito.get('citazioni'):
        print('\nCITAZIONI RILEVANTI:')
        for c in esito['citazioni']:
            print(f"  «{c['testo']}» — {c['autore']} ({c['fonte']})")
    if esito['fonti']:
        print('\nFONTI:')
        for f in esito['fonti']:
            print(f"  [{f['n']}] {f['title']} — {f['file']} ({f['data']}, {f['strato']})")
    t = esito['tempi']
    print(f"\nrotta: {esito['rotta']} | router {t.get('router', 0):.1f}s"
          f" | retrieval {t.get('retrieval', 0):.1f}s | motore {t.get('motore', 0):.1f}s"
          f" | TOTALE {t.get('totale', 0):.1f}s")
    con.close()


def cmd_critico(args):
    from zeno_engine import critico
    cfg = config.load()
    con = db.connect(cfg['db'])
    r = critico.esegui(con, cfg, topic_id=args.topic, max_coppie=args.max_coppie)
    print(f"\nCoppie valutate {r['coppie']} | contraddizioni confermate {r['contraddizioni']}")
    print('Lacune da tensioni sincronizzate su stats/lacune_aperte.json.')
    con.close()


def cmd_brief(args):
    from zeno_engine import briefing
    cfg = config.load()
    con = db.connect(cfg['db'])
    percorso = briefing.genera(con, cfg)
    print(f'Briefing scritto: {percorso}')
    con.close()


def cmd_sync(args):
    from zeno_engine import sync
    cfg = config.load()
    con = db.connect(cfg['db'], vec=True)
    r = sync.esegui(con, cfg, salta_motore=getattr(args, 'solo_locali', False))
    print(f"CSV importati: {r['csv'] or 'nessuno'} | chunk embeddati: {r['embeddati']} "
          f"| in coda per l'Archivista: {r['da_estrarre']}")
    con.close()


def cmd_converti(args):
    from zeno_engine import converti
    cfg = config.load()
    r = converti.esegui(cfg, limite=args.limite)
    print(f"\nconvertiti {r['ok']} | .md già presente {r['md_esiste']} | "
          f"scartati (illeggibili) {r['scartati']} | errori {r['errore']}")
    for riga in r['dettaglio_scarti']:
        print(f'  ⚠ {riga}')
    print(f'Originali spostati in {converti.ORIGINALI} (li elimini tu quando vuoi).')


def cmd_snapshot(args):
    from zeno_engine import snapshot
    cfg = config.load()
    con = db.connect(cfg['db'])
    print(f'Snapshot scritto: {snapshot.genera(con, cfg)}')
    con.close()


def cmd_stato(args):
    from zeno_engine import stato
    cfg = config.load()
    con = db.connect(cfg['db'])
    print(f'STATO scritto: {stato.genera(con)}')
    con.close()


def cmd_export_vault(args):
    from zeno_engine import vault
    cfg = config.load()
    con = db.connect(cfg['db'])
    r = vault.esporta(con)
    print(f"Vault Obsidian: {r['note']} note ({r['temi']} temi, {r['entita']} entità) "
          f"in {vault.VAULT_ZENO}")
    con.close()


def cmd_filtro(args):
    """Asserzioni da catalogo: senza opzioni mostra cosa le regole scarterebbero
    (niente scritto); --applica le sposta in asserzioni_scartate dopo un backup
    di zeno.db; --ripristina le rimette al loro posto."""
    import random
    import shutil
    from datetime import datetime
    from zeno_engine import filtro
    cfg = config.load()
    percorso_db = args.db or cfg['db']
    con = db.connect(percorso_db)
    if args.ripristina:
        n = filtro.ripristina(con, motivo=args.motivo)
        con.commit()
        print(f'{n} asserzioni rimesse in assertions'
              + (f' (motivo {args.motivo})' if args.motivo else ''))
        con.close()
        return
    gia = con.execute('SELECT motivo, COUNT(*) FROM asserzioni_scartate '
                      'GROUP BY motivo ORDER BY 2 DESC').fetchall()
    if gia:
        print('Già scartate: ' + ', '.join(f'{m} {n}' for m, n in gia))
    voci = filtro.candidati_regole(con)
    if args.motivo:
        voci = [v for v in voci if v[1] == args.motivo]
    tot = con.execute('SELECT COUNT(*) FROM assertions').fetchone()[0]
    print(f'Le regole scartano {len(voci)} asserzioni su {tot} ({len(voci) / max(tot, 1):.1%}):')
    testi = dict(con.execute('SELECT id, text FROM assertions'))
    per_regola: dict[str, list[int]] = {}
    for aid, m in voci:
        per_regola.setdefault(m, []).append(aid)
    for m, ids in sorted(per_regola.items(), key=lambda x: -len(x[1])):
        print(f'  {m:15s} {len(ids):5d}   es.: {testi[random.choice(ids)][:90]}')
    if not args.applica:
        print('\nNiente scritto. Per spostarle: zeno filtro --applica')
        con.close()
        return
    backup = f"{percorso_db}.pre-filtro-{datetime.now():%Y%m%d-%H%M}.bak"
    con.commit()
    shutil.copy2(percorso_db, backup)
    n = filtro.scarta(con, voci, fonte='regola')
    con.commit()
    print(f'\n{n} asserzioni spostate in asserzioni_scartate. Backup: {backup}')
    print('Per annullare: zeno filtro --ripristina [--motivo REGOLA]')
    con.close()


def cmd_generi(args):
    """Scrive sources.genere per tutte le fonti (generi.py). Rilanciabile."""
    from zeno_engine import generi
    cfg = config.load()
    con = db.connect(args.db or cfg['db'])
    conta = generi.assegna(con)
    if args.giudizi:
        import json
        with open(args.giudizi, encoding='utf-8') as f:
            esito = generi.applica_giudizi(con, json.load(f))
        print('Giudizi applicati: ' + ', '.join(f'{k} {v}' for k, v in esito.items()))
    con.commit()
    for g, n in sorted(conta.items(), key=lambda x: -x[1]):
        print(f'  {g:20s} {n:5d} fonti')
    if conta.get(generi.DA_CLASSIFICARE):
        print(f'Da classificare a mano in {generi.FILE_MANUALE}')
    con.close()


def cmd_giudice(args):
    """Giudice locale delle asserzioni narrative (giudice_narrativa.py)."""
    from zeno_engine import giudice_narrativa as gn
    cfg = config.load()
    con = db.connect(args.db or cfg['db'])
    if args.addestra:
        print(f'Addestrato: {gn.addestra(con, cfg)} → {gn.FILE_PESI}')
    else:
        conta = gn.giudica(con, cfg, limite=args.limite)
        con.commit()
        print('Giudicate: ' + ', '.join(f'{k} {v}' for k, v in conta.items()))
    con.close()


def cmd_valuta(args):
    """Misure di qualità: stampa solo numeri (i testi restano in stats/valutazione/)."""
    import json as _json
    from zeno_engine import valutazione as v
    cfg = config.load()
    if args.azione == 'prepara':
        print('Set di prova da compilare:', v.prepara_domande(cfg))
    elif args.azione == 'voti':
        print(_json.dumps(v.riepilogo_voti(cfg), indent=2))
    elif args.azione == 'sintetico':
        con = db.connect(cfg['db'], vec=True)
        print(_json.dumps(v.genera_sintetico(con, cfg, n=args.n or 200), indent=2))
        con.close()
    elif args.azione == 'recupero':
        con = db.connect(cfg['db'], vec=True)
        print(_json.dumps(v.valuta_recupero(con, cfg, limite=args.limite), indent=2))
        con.close()
    elif args.azione == 'risposte':
        print(_json.dumps(v.valuta_risposte(cfg, modello=args.modello, giudice=args.giudice,
                                            etichetta=args.etichetta or '',
                                            prompt=args.prompt), indent=2))
    elif args.azione == 'confronta':
        print(_json.dumps(v.confronta(args.file[0], args.file[1], cfg), indent=2))
    elif args.azione == 'proponi':
        from zeno_engine import domande_prova
        con = db.connect(cfg['db'], vec=True)
        print(_json.dumps(domande_prova.proponi(con, cfg, n=args.n or 40,
                                                 solo=args.solo or None), indent=2))
        con.close()
    elif args.azione == 'proposte':
        from zeno_engine import domande_prova
        print(_json.dumps(domande_prova.riepilogo(cfg), indent=2))


def cmd_prime(args):
    """Letture PRIME (Reading Brain): importa, in sospeso, scelta del libro, elenco."""
    from zeno_engine import prime
    cfg = config.load()
    con = db.connect(cfg['db'], vec=True)
    if args.azione == 'importa':
        if not args.argomenti:
            print('Uso: prime importa <file> [<file> ...]')
            sys.exit(2)
        da_elaborare, rifiutati = [], 0
        for percorso in args.argomenti:
            r = prime.importa_file(con, cfg, percorso)
            print(f"[{r['stato']}] {r['nome']}")
            for m in r['messaggi']:
                print(f'    {m}')
            if r['stato'] in ('importato', 'sostituito'):
                da_elaborare.append(r['source_id'])
            rifiutati += r['stato'] == 'rifiutato'
        if da_elaborare and not args.senza_estrazione:
            print('Estrazione delle asserzioni (modello locale)…')
            prime.elabora(con, cfg, da_elaborare)
        elif da_elaborare:
            print('Asserzioni da estrarre: lo farà `prime elabora` o il prossimo `zeno sync`.')
        con.close()
        sys.exit(1 if rifiutati else 0)
    elif args.azione == 'sospesi':
        lista = prime.sospesi(cfg)
        if not lista:
            print('Nessun documento in sospeso.')
        for s in lista:
            print(f"{s['nome']}  v{s['versione']}  «{s['titolo']}» di {s['autore']}  "
                  f"({'più schede possibili' if s['motivo'] == 'ambigua' else 'nessuna scheda trovata'})")
            for c in s['candidati']:
                print(f"    - {c['file']}  [goodreads_id: {c['goodreads_id'] or '—'}]")
            if s['candidati']:
                print(f"    scegli: prime scegli {s['nome']} <goodreads_id | nome file della scheda>")
    elif args.azione == 'scegli':
        if len(args.argomenti) != 2:
            print('Uso: prime scegli <documento in sospeso> <goodreads_id | nome file della scheda>')
            sys.exit(2)
        r = prime.scegli(con, cfg, args.argomenti[0], args.argomenti[1])
        print(f"[{r['stato']}] {r['nome']}")
        for m in r['messaggi']:
            print(f'    {m}')
        if r['stato'] in ('importato', 'sostituito') and not args.senza_estrazione:
            prime.elabora(con, cfg, [r['source_id']])
    elif args.azione == 'elenco':
        for r in prime.elenco(con, cfg):
            scad = {'scaduta': '  ⚠ SCADUTA', 'entro_7_giorni': '  ⚠ entro 7 giorni'}.get(r['scadenza'], '')
            print(f"{r['titolo']}  [{r['stato_prime']}]  v{r['versione']}  agg. {r['aggiornata']}"
                  f"  verifica_il: {r['verifica_il'] or '—'}{scad}  Cantiere aperto: {len(r['cantiere_aperto'])}")
    elif args.azione == 'elabora':
        r = prime.elabora(con, cfg)
        print(f"Chunk elaborati: {r['chunk']}, asserzioni: {r['asserzioni']}")
    elif args.azione == 'collegamenti':
        from zeno_engine import prime_collegamenti as pc
        dati = pc.aggiorna(con, cfg)
        n = sum(len(d['collegamenti']) for d in dati['documenti'])
        m = dati['misura']
        print(f"Candidati: {n} su {len(dati['documenti'])} documenti · giudizi {m['giudicati']}/{m['giudizi_min']}"
              f" · precisione {m['precisione'] if m['precisione'] is not None else '—'}"
              f" · file per il vault: {'ATTIVO' if dati['abilitato'] else 'in prova (vuoto)'}")
    elif args.azione == 'giudica':
        from zeno_engine import prime_collegamenti as pc
        if len(args.argomenti) != 2:
            print('Uso: prime giudica <chiave> conferma|contraddice|scarta|non_so|annulla')
            sys.exit(2)
        proposte = pc.leggi_proposte(cfg) or {'documenti': []}
        nome = next((d['nome_file'] for d in proposte['documenti']
                     for c in d['collegamenti'] if c['chiave'] == args.argomenti[0]), '')
        try:
            pc.giudica(cfg, args.argomenti[0], nome, args.argomenti[1])
        except ValueError as e:
            print(e)
            sys.exit(2)
        print('Registrato.')
    elif args.azione == 'misura':
        from zeno_engine import prime_collegamenti as pc
        print(_json_prime(pc.misura_campione(con, cfg)))
        print(_json_prime(pc.misura_giudizi(pc.decisioni(cfg))))
    con.close()


def _json_prime(d):
    import json as _json
    return _json.dumps(d, indent=2, ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser(prog='zeno_engine', description='Motore epistemico di Zeno')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('ingest', help='Ingerisce il corpus Markdown in zeno.db')
    p.add_argument('--forza', action='store_true', help='riprocessa anche i file invariati')
    p.add_argument('--root', default=None, help='cartella radice (default: documents/)')
    p.set_defaults(fn=cmd_ingest)

    p = sub.add_parser('audit', help='Numeri e distribuzioni di zeno.db')
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser('campione', help='Chunk casuali da validare')
    p.add_argument('n', nargs='?', type=int, default=5)
    p.set_defaults(fn=cmd_campione)

    p = sub.add_parser('archivista', help='Estrae asserzioni/entità dai chunk (batch LLM)')
    p.add_argument('--campione', type=int, default=None,
                   help='processa solo N documenti diversificati (test qualità)')
    p.add_argument('--limite', type=int, default=None, help='massimo N chunk')
    p.add_argument('--strati', default=None,
                   help='filtro strati, es. "esplicito,dialogico"')
    p.add_argument('--ruolo', default='batch', choices=['batch', 'batch_rapido'],
                   help='batch = motore 7-8B (qualità); batch_rapido = router 3B '
                        '(backlog acquisito, deciso 2026-07-15 per velocità)')
    p.set_defaults(fn=cmd_archivista)

    p = sub.add_parser('asserzioni', help='Ultime asserzioni estratte')
    p.add_argument('n', nargs='?', type=int, default=20)
    p.set_defaults(fn=cmd_asserzioni)

    p = sub.add_parser('alias', help='Rigenera le proposte di alias entità (conferma in dashboard)')
    p.set_defaults(fn=cmd_alias)

    p = sub.add_parser('temi', help='Proposte di temi nuovi e prioritari (conferma in dashboard)')
    p.set_defaults(fn=cmd_temi)

    p = sub.add_parser('schede', help='Genera le schede-tema V6 dai dati di zeno.db')
    p.add_argument('--prioritari', action='store_true', help='solo i temi prioritari')
    p.set_defaults(fn=cmd_schede)

    p = sub.add_parser('indicizza', help='Embeddings + topic + archi di similarità')
    p.add_argument('--solo-embeddings', action='store_true')
    p.set_defaults(fn=cmd_indicizza)

    p = sub.add_parser('manutenzione',
                       help='Stato dei job periodici (--stato) o esecuzione di quelli dovuti')
    p.add_argument('--stato', action='store_true',
                   help='mostra quando ha girato ogni job e quali sono in ritardo')
    p.add_argument('--forza', default=None, metavar='JOB',
                   help='esegue SUBITO un job ignorandone la cadenza (es. critico)')
    p.add_argument('--solo-locali', action='store_true', dest='solo_locali',
                   help='salta i job che usano il motore MLX (usato dal timer ad app '
                        'aperta quando l\'Archivista sta già girando)')
    p.set_defaults(fn=cmd_manutenzione)

    p = sub.add_parser('ri-classifica',
                       help='Ri-classifica col limbo le asserzioni forzate su un tema madre '
                            '(una-tantum, resumibile)')
    p.add_argument('--motore', default='router', choices=['router', 'ask'],
                   help='router = 3B (veloce); ask = 7B (qualità, scelto per il batch una-tantum)')
    p.add_argument('--limite', type=int, default=None, help='processa solo le prime N (test)')
    p.set_defaults(fn=cmd_riclassifica)

    p = sub.add_parser('ask', help='Domanda all\'archivio (Router → retrieval → Motore)')
    p.add_argument('domanda')
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser('critico', help='Cerca contraddizioni tra asserzioni (job periodico)')
    p.add_argument('--topic', type=int, default=None, help='limita a un topic id')
    p.add_argument('--max-coppie', type=int, default=40)
    p.set_defaults(fn=cmd_critico)

    p = sub.add_parser('brief', help='Genera il briefing epistemico del giorno')
    p.set_defaults(fn=cmd_brief)

    p = sub.add_parser('sync', help='Messa in pari all\'avvio: staging CSV + ingest + embeddings')
    p.add_argument('--solo-locali', action='store_true', dest='solo_locali',
                   help='rimanda i job periodici che usano il modello '
                        '(li riprende il timer della chat): per l\'avvio dell\'app')
    p.set_defaults(fn=cmd_sync)

    p = sub.add_parser('converti', help='Converte PDF/DOCX/HTML/TXT del corpus in .md')
    p.add_argument('--limite', type=int, default=None, help='solo i primi N file')
    p.set_defaults(fn=cmd_converti)

    p = sub.add_parser('stato', help='Rigenera STATO.md (fotografia del cervello)')
    p.set_defaults(fn=cmd_stato)

    p = sub.add_parser('snapshot', help='Genera uno snapshot cognitivo (forzato)')
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser('export-vault', help='Esporta il grafo come vault Obsidian')
    p.set_defaults(fn=cmd_export_vault)

    p = sub.add_parser('generi', help='Genere di ogni fonte (narrativa/saggistica, propria/letta…)')
    p.add_argument('--db', default=None, help='altro file .db (prove su una copia)')
    p.add_argument('--giudizi', default=None, help='JSON [{id, giudizio}]: ruolo/voce + scarta catalogo')
    p.set_defaults(fn=cmd_generi)

    p = sub.add_parser('giudice', help='Ruolo delle asserzioni narrative nuove (locale, senza LLM)')
    p.add_argument('--addestra', action='store_true', help='riaddestra sui giudizi di riferimento')
    p.add_argument('--limite', type=int, default=None)
    p.add_argument('--db', default=None)
    p.set_defaults(fn=cmd_giudice)

    p = sub.add_parser('filtro', help='Asserzioni da catalogo: anteprima, --applica, --ripristina')
    p.add_argument('--applica', action='store_true', help='sposta le scartate (backup prima)')
    p.add_argument('--ripristina', action='store_true', help='rimette le scartate in assertions')
    p.add_argument('--motivo', default=None, help='solo questa regola')
    p.add_argument('--db', default=None, help='altro file .db (prove su una copia)')
    p.set_defaults(fn=cmd_filtro)

    p = sub.add_parser('valuta', help='Qualità delle risposte: set di prova, voti, recupero')
    p.add_argument('azione', choices=['prepara', 'voti', 'sintetico', 'recupero',
                                      'risposte', 'confronta', 'proponi', 'proposte'])
    p.add_argument('file', nargs='*', help='per confronta: due file di esiti')
    p.add_argument('--n', type=int, default=None,
                   help='sintetico: quante domande (200); proponi: quante proposte (40)')
    p.add_argument('--limite', type=int, default=None)
    p.add_argument('--modello', default=None, help='risposte: modello al posto di motore_ask')
    p.add_argument('--giudice', default=None, help='risposte: modello giudice (di serie motore_ask)')
    p.add_argument('--etichetta', default=None)
    p.add_argument('--prompt', type=int, default=None, choices=(1, 2, 3, 4),
                   help='risposte: versione del prompt ARCHIVIO (di serie quella della chat; 3 decide SI/NO in prima riga, 4 decide con una chiamata a parte)')
    p.add_argument('--solo', nargs='*', default=None,
                   help='proponi: solo questi tipi (pensiero_mio fatto citazione lacuna)')
    p.set_defaults(fn=cmd_valuta)

    # Letture PRIME: solo se c'è il modulo (la versione pubblica non lo include).
    if os.path.exists(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prime.py')):
        p = sub.add_parser('prime', help='Letture PRIME: importa, sospesi, scegli, elenco, elabora, collegamenti, giudica, misura')
        p.add_argument('azione', choices=['importa', 'sospesi', 'scegli', 'elenco', 'elabora',
                                          'collegamenti', 'giudica', 'misura'])
        p.add_argument('argomenti', nargs='*',
                       help='importa: i file (da qualunque percorso); scegli: documento e libro')
        p.add_argument('--senza-estrazione', action='store_true',
                       help='importa/scegli: salta le asserzioni (le farà `prime elabora` o `zeno sync`)')
        p.set_defaults(fn=cmd_prime)

    args = ap.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
