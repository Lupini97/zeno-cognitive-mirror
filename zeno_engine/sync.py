# Sync all'avvio — "Zeno si sveglia e si mette in pari" (richiesta l'utente,
# 2026-07-12: mai più il difetto del watcher V5 che vedeva solo i cambiamenti
# a processo acceso e ignorava tutto ciò che era successo a Zeno spento).
#
# Sequenza di `zeno sync` (pensata per essere chiamata all'avvio della UI,
# oltre che a mano):
#   1. IMPORT STAGING — se in import_staging/ ci sono CSV Vivino/Goodreads
#      nuovi o modificati (hash registrato in meta), lancia i convertitori V5
#      (venv V5, scrivono Markdown nel corpus). L'utente non lancia mai script.
#   2. INGEST — riconciliazione totale del corpus: nuovi, modificati, spariti
#      (hash-based: becca anche ciò che è cambiato a Zeno spento).
#   3. EMBEDDINGS — vettorializza i soli chunk nuovi (se il demone MLX è raggiungibile;
#      offline: rimandato al sync successivo, mai bloccante).
#   I chunk nuovi restano con archivista_at NULL → il prossimo batch notturno
#   dell'Archivista li estrae da sé. Nessun passo richiede intervento umano.

import fnmatch
import hashlib
import os
import subprocess

from zeno_engine import processi
from zeno_engine.config import BRAIN, PYTHON, SCRIPTS

# I convertitori di staging (import_vivino.py, goodreads_to_obsidian.py) sono
# script V5, ma il venv V5 non esiste più: è stato rimosso il 2026-07-15 e qui
# era rimasto un percorso morto. Finché in staging non arrivava un CSV NUOVO il
# guasto restava invisibile — l'hash invariato faceva saltare il subprocess —
# poi al primo CSV nuovo `subprocess.run` sollevava FileNotFoundError e
# affondava l'intero sync (nessuno lo cattura qui né in esegui()). Girano
# entrambi sul runtime V6: chiedono solo pandas e config, presenti in .venv_zeno.
VENV_STAGING = PYTHON
STAGING = os.path.join(BRAIN, 'import_staging')

# pattern CSV (case-insensitive) → modulo convertitore (zeno_engine/importatori/,
# lì dal 29/09/2026: prima erano script sciolti in scripts/)
IMPORTATORI = [
    ('*vivino*.csv', 'zeno_engine.importatori.import_vivino'),
    ('*goodreads*.csv', 'zeno_engine.importatori.goodreads_to_obsidian'),
]


def _hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for blocco in iter(lambda: f.read(1 << 20), b''):
            h.update(blocco)
    return h.hexdigest()


def importa_staging(con, verboso: bool = True) -> list[str]:
    """Lancia i convertitori V5 sui CSV nuovi/modificati. Ritorna i CSV processati."""
    processati = []
    try:
        file_staging = sorted(os.listdir(STAGING))
    except OSError:
        return processati
    for pattern, script in IMPORTATORI:
        # fnmatch su nome minuscolo: 'Vivino_lista_vini.csv' deve matchare
        # '*vivino*.csv' (glob era case-sensitive: bug visto al primo sync).
        for nome in file_staging:
            if nome.startswith('._') or not fnmatch.fnmatch(nome.lower(), pattern):
                continue
            csv = os.path.join(STAGING, nome)
            h = _hash_file(csv)
            chiave = f'staging_hash:{nome}'
            gia = con.execute('SELECT value FROM meta WHERE key = ?', (chiave,)).fetchone()
            if gia and gia[0] == h:
                continue                    # già importato, invariato
            if verboso:
                print(f'  staging: {nome} → {script}')
            try:
                esito = subprocess.run([VENV_STAGING, '-m', script],
                                       cwd=SCRIPTS, capture_output=True, text=True,
                                       timeout=600)
            except (OSError, subprocess.SubprocessError) as e:
                # Un convertitore che non parte non deve affondare il sync:
                # il resto della messa in pari (corpus, embeddings, job) vale
                # comunque. Il CSV resta senza hash, quindi riprova al giro dopo.
                if verboso:
                    print(f'  ERRORE avvio {script}: {e}')
                continue
            if esito.returncode == 0:
                con.execute('INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)',
                            (chiave, h))
                con.commit()
                processati.append(nome)
            elif verboso:
                print(f'  ERRORE {script}: {esito.stderr.strip()[-300:]}')
    return processati


def _backend_raggiungibile() -> bool:
    """Il demone MLX risponde? Se è spento, embeddings ed estrazione slittano."""
    try:
        from zeno_engine import modelli
        return modelli.raggiungibile()
    except Exception:
        return False


def esegui(con, cfg: dict, verboso: bool = True,
           salta_motore: bool = False) -> dict:
    """Sync completo all'avvio. Ogni passo è tollerante: mai bloccante.

    `salta_motore`: i job periodici che usano il modello (temi, schede,
    critico…) NON girano in questa passata. Lo chiede il launcher: lo splash
    aspetta la fine del sync, e `temi` + `schede` durano decine di minuti —
    oltre i 900 s concessi venivano uccisi a metà, restavano scaduti e
    ripartivano a ogni avvio (visto il 2026-11-14: fermi da fine settembre,
    finestra bloccata 15 minuti a ogni apertura). Li riprende il timer della
    chat (`_timer_periodici`), staccato e ad app già aperta."""
    from zeno_engine import ingest

    csv_fatti = importa_staging(con, verboso)

    r = ingest.ingest_corpus(con, cfg)
    if verboso:
        print(f"  corpus: nuovi {r['nuovo']} | aggiornati {r['aggiornato']} | "
              f"rimossi {r['rimossi']} | invariati {r['invariato']}")

    if r.get('riallineate') or r.get('doppioni_rimossi'):
        if verboso:
            print(f"  percorsi riallineati: {r['riallineate']} fonti ri-puntate, "
                  f"{r['doppioni_rimossi']} doppioni rimossi")

    # La condizione era `if r['nuovo'] or r['aggiornato'] or r['rimossi']`, cioè
    # "embedda solo se QUESTO sync ha cambiato qualcosa". Basta che una sessione
    # venga chiusa a metà vettorializzazione perché l'arretrato resti lì per
    # sempre: al sync dopo il corpus è invariato e il ramo non parte più.
    # È successo davvero — il 2026-08-17, dopo la migrazione dall'SSD: 67 fonti
    # embeddate su 1582, e per una settimana il retrieval vettoriale ha lavorato
    # solo sulle copie vecchie. Ora il criterio è l'ARRETRATO, che è il dato
    # vero: finché esiste un chunk senza vettore, il sync ci riprova.
    embeddati = 0
    try:
        arretrato = con.execute(
            'SELECT COUNT(*) FROM chunks WHERE id NOT IN '
            '(SELECT chunk_id FROM vec_chunks)').fetchone()[0]
    except Exception:
        # vec_chunks non esiste ancora (archivio nuovo): tutti i chunk sono
        # arretrato, anche ai sync successivi in cui nessun file è cambiato.
        arretrato = con.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
    if arretrato:
        if _backend_raggiungibile():
            from zeno_engine import indicizzatore
            try:
                try:
                    con.execute('DELETE FROM vec_chunks WHERE chunk_id NOT IN '
                                '(SELECT id FROM chunks)')
                    con.commit()
                except Exception:
                    pass   # archivio nuovo: vec_chunks la crea embed_chunks
                if verboso and arretrato > 500:
                    print(f'  {arretrato} chunk da vettorializzare: '
                          f'la prima messa in pari richiede qualche minuto…')
                embeddati = indicizzatore.embed_chunks(con, cfg, verboso=False)
            except Exception as e:
                if verboso:
                    print(f'  embeddings rimandati: {e}')
        elif verboso:
            print('  Demone MLX spento: embeddings rimandati al prossimo sync.')

    da_estrarre = con.execute(
        'SELECT COUNT(*) FROM chunks WHERE archivista_at IS NULL').fetchone()[0]

    # Estrazione incrementale: i pochi chunk nuovi (una citazione, una
    # riflessione, una scheda libro appena aggiunta) ottengono SUBITO le
    # asserzioni + i topic, così entrano nel grafo e nelle analisi, non solo
    # nel retrieval vettoriale (2026-07-22: la "Citazione da Atti Umani" era
    # embeddata ma senza asserzioni → invisibile a grafo/lacune). Se la coda è
    # grande (import massiccio) si lascia al batch manuale, per non allungare
    # l'avvio dell'app di minuti. Soglia configurabile.
    estratti_ora = 0
    soglia_inc = (cfg.get('sync') or {}).get('estrazione_incrementale_max', 12)
    if 0 < da_estrarre <= soglia_inc and _backend_raggiungibile():
        try:
            from zeno_engine import archivista, indicizzatore
            # Confine PRIMA dell'estrazione: tutto ciò che nasce dopo è "nuovo".
            # Senza, assegna_topics prendeva ogni asserzione con topic_id NULL —
            # cioè anche le migliaia in LIMBO, che sono vecchie e legittimamente
            # non classificate: 40+ minuti di avvio dell'app per 6 chunk nuovi
            # (misurato il 2026-07-28). Il backlog del limbo è compito del job
            # periodico `temi`, non dell'avvio.
            confine = con.execute(
                'SELECT COALESCE(MAX(id), 0) FROM assertions').fetchone()[0]
            r_arch = archivista.esegui_batch(con, cfg, limite=soglia_inc,
                                             ruolo='batch', verboso=False)
            estratti_ora = r_arch['chunk']
            if estratti_ora:
                indicizzatore.assegna_topics(con, cfg, verboso=False, da_id=confine)
            if verboso:
                print(f'  estrazione incrementale: {estratti_ora} chunk → asserzioni + topic')
        except Exception as e:
            if verboso:
                print(f'  estrazione incrementale rimandata: {e}')
    elif da_estrarre > soglia_inc:
        # Arretrato grosso: prima qui c'era solo un `print` che diceva all'utente
        # di lanciare il batch a mano — e siccome il launcher cattura l'output
        # del sync, quel messaggio non lo leggeva nessuno. Risultato: 441 chunk
        # fermi, cioè recensioni e riflessioni nuove embeddate ma senza
        # asserzioni, quindi invisibili a grafo, lacune e maieutica.
        # Ora parte da sé, ma STACCATO (start_new_session) e con un tetto per
        # esecuzione: l'avvio dell'app non aspetta, la chat resta usabile e
        # l'avanzamento si vede nel pannello "Processi Zeno" (ETA compresa).
        # Il tetto serve alla RAM: l'Archivista tiene residente llama3.1:8b
        # accanto al 7B della chat e a bge-m3 — tre modelli su 16 GB vanno in
        # thrashing (gotcha noto). Si smaltisce a scaglioni, un avvio alla volta.
        cfg_sync = cfg.get('sync') or {}
        tetto = cfg_sync.get('archivista_background_max', 150)
        if not cfg_sync.get('archivista_auto', True):
            if verboso:
                print(f'  {da_estrarre} chunk da estrarre: avvio automatico '
                      f'disattivato (sync.archivista_auto: false).')
        elif processi.in_corso('archivista'):
            if verboso:
                print(f'  {da_estrarre} chunk da estrarre: un Archivista sta già girando.')
        elif not _backend_raggiungibile():
            if verboso:
                print(f'  {da_estrarre} chunk da estrarre: demone MLX spento, rimandati.')
        else:
            try:
                log = open('/tmp/zeno_archivista.log', 'a')
                subprocess.Popen(
                    processi.con_caffeina(
                        [VENV_STAGING, '-m', 'zeno_engine', 'archivista',
                         '--limite', str(tetto)]),
                    cwd=SCRIPTS, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True)
                if verboso:
                    print(f'  {da_estrarre} chunk da estrarre: Archivista avviato in '
                          f'background (max {tetto} per volta) → /tmp/zeno_archivista.log')
            except Exception as e:
                if verboso:
                    print(f'  Archivista non avviato: {e}')

    # Job periodici: la lista NON sta più qui. Sta tutta in periodici.REGISTRO,
    # unico posto dove un job si dichiara — prima erano sparsi in questa
    # funzione e chi non veniva aggiunto qui semplicemente non girava più
    # (è successo al Critico: fermo dall'11 luglio senza che nulla lo dicesse).
    # Ogni job è isolato: uno che fallisce non ferma gli altri né l'avvio.
    from zeno_engine import periodici as _periodici
    periodici = _periodici.esegui_tutti(con, cfg, verboso=verboso,
                                        salta_motore=salta_motore)

    return {'csv': csv_fatti, 'ingest': r, 'embeddati': embeddati,
            'da_estrarre': da_estrarre, 'periodici': periodici}
