# Registro UNICO dei job periodici di Zeno.
#
# Perché esiste (richiesta dell'utente, 2026-07-28): i job di manutenzione erano
# sparsi — ognuno con il proprio `dovuto()`, il proprio file marker e la propria
# chiamata dentro sync.esegui — e chi non era stato aggiunto a quella lista
# semplicemente NON GIRAVA PIÙ. È successo davvero al Critico: fermo dall'11
# luglio, quando il corpus aveva ~3.300 asserzioni contro le 30.524 di oggi.
# Il risultato era che "6 sole contraddizioni, tutte vecchie" SEMBRAVA un bug
# del Critico, mentre era solo un aggiornamento mai eseguito.
#
# Regola: un job periodico si dichiara QUI e da nessun'altra parte. Chi lo
# aggiunge ottiene gratis esecuzione automatica a ogni `zeno sync` (cioè a ogni
# avvio dell'app) e visibilità in `zeno manutenzione --stato`.
#
# Lo stato "ultima esecuzione" vive in `meta` (chiave `job_ultimo:<nome>`), sul
# DB e non su file sparsi. I moduli storici che hanno già il proprio marker
# mantengono il loro (`dovuto`/`segna` propri): non si riscrive ciò che
# funziona, si unifica l'orchestrazione e la visibilità.

import glob
import os
import time
from datetime import datetime

from zeno_engine import processi
from zeno_engine.config import BRAIN

CHIAVE = 'job_ultimo:'


# ── stato generico (per i job che non hanno un marker proprio) ───────────────

def ultimo_generico(con, nome: str) -> datetime | None:
    r = con.execute('SELECT value FROM meta WHERE key = ?',
                    (CHIAVE + nome,)).fetchone()
    if not r:
        return None
    try:
        return datetime.fromisoformat(r[0])
    except Exception:
        return None


def segna_generico(con, nome: str) -> None:
    con.execute('INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)',
                (CHIAVE + nome, datetime.now().isoformat(timespec='seconds')))
    con.commit()


def _dovuto_generico(con, nome: str, giorni: float) -> bool:
    u = ultimo_generico(con, nome)
    return u is None or (datetime.now() - u).total_seconds() >= giorni * 86400


def _ultimo_snapshot() -> datetime | None:
    """snapshot non ha un marker: la sua traccia sono i file
    stats/snapshot_AAAA-MM-GG.json (stessa fonte usata dal suo `dovuto`)."""
    import glob
    import os
    f = sorted(glob.glob(os.path.join(BRAIN, 'stats/snapshot_*.json')))
    if not f:
        return None
    try:
        return datetime.strptime(
            os.path.basename(f[-1])[len('snapshot_'):-len('.json')], '%Y-%m-%d')
    except ValueError:
        return None


def _ultimo_da_marker(nome_file: str) -> datetime | None:
    """Ultima esecuzione dei moduli storici, che usano un file in stats/."""
    import os
    path = os.path.join(os.path.join(BRAIN, 'stats'), nome_file)
    if not os.path.exists(path):
        return None
    try:
        return datetime.fromisoformat(open(path).read().strip())
    except Exception:
        # snapshot non scrive un timestamp: si usa la mtime del file
        try:
            return datetime.fromtimestamp(os.path.getmtime(path))
        except Exception:
            return None


# ── implementazione dei job ─────────────────────────────────────────────────

def _job_snapshot(con, cfg):
    from zeno_engine import snapshot
    return f'scritto {snapshot.genera(con, cfg)}'


def _job_journal(con, cfg):
    from zeno_engine import journal
    p = journal.genera(con, cfg)
    journal.segna_eseguito()
    return f'scritto {p}'


def _job_mind(con, cfg):
    from zeno_engine import mind
    n = mind.aggiorna_lacune_aperte(con)
    mind.segna_eseguito()
    return f'{n} nuove lacune'


def _job_graph(con, cfg):
    from zeno_engine import graph
    p = graph.genera(con)
    graph.segna_eseguito()
    return f'scritto {p}'


def _job_briefing(con, cfg):
    from zeno_engine import briefing
    p = briefing.genera(con, cfg)
    briefing.segna_eseguito()
    return f'scritto {p}'


def _job_temi(con, cfg):
    from zeno_engine import indicizzatore, revisione_temi, temi
    indicizzatore.rileva_community(con)
    # TETTO sul limbo: `zeno sync` gira all'avvio dell'app e riconsiderarlo
    # tutto in un colpo lo bloccherebbe per decine di minuti (2.522 asserzioni
    # = oltre mezz'ora col 3B, misurato il 2026-07-28). Si smaltisce a scaglioni.
    tetto = (cfg.get('periodici') or {}).get('limbo_per_run', 400)
    n_limbo = indicizzatore.assegna_topics(con, cfg, verboso=False, limite=tetto)
    resta = con.execute(
        "SELECT COUNT(*) FROM assertions WHERE topic_id IS NULL "
        "AND COALESCE(ruolo, '') <> 'trama'").fetchone()[0]
    n = revisione_temi.genera(con, cfg)
    temi.proponi_prioritari(con, cfg, verboso=False)
    temi.segna_eseguito()
    return (f'community + {n} proposte + {n_limbo} dal limbo classificate '
            f'({resta} restano)')


def _job_entita(con, cfg):
    """Fonde gli omonimi (automatico: match esatto) e rigenera le proposte di
    alias veri (che restano da confermare a mano)."""
    from zeno_engine import indicizzatore
    r = indicizzatore.fondi_omonimi(con)
    n = indicizzatore.proponi_alias_entita(con)
    segna_generico(con, 'entita')
    return (f"{r['entita_fuse']} omonimi fusi in {r['gruppi']} gruppi, "
            f'{n} proposte di alias')


def _job_critico(con, cfg):
    """Cerca contraddizioni fra asserzioni. NON era in nessuno scheduler: è il
    motivo per cui è rimasto fermo dall'11 luglio."""
    from zeno_engine import critico
    max_coppie = (cfg.get('periodici') or {}).get('critico_max_coppie', 60)
    r = critico.esegui(con, cfg, max_coppie=max_coppie, verboso=False)
    segna_generico(con, 'critico')
    return f"{r['coppie']} coppie valutate, {r['contraddizioni']} contraddizioni"


def _job_schede(con, cfg):
    from zeno_engine import schede
    n = schede.genera_tutte(con, cfg, verboso=False)
    segna_generico(con, 'schede')
    return f'{n} schede-tema rigenerate'


def _job_giudice(con, cfg):
    from zeno_engine import giudice_narrativa
    conta = giudice_narrativa.giudica(con, cfg)
    con.commit()
    segna_generico(con, 'giudice')
    return ', '.join(f'{k} {v}' for k, v in conta.items())


def _giudice_dovuto(con, cfg) -> bool:
    """Dovuto se ci sono asserzioni narrative ancora senza ruolo (e i pesi)."""
    from zeno_engine import giudice_narrativa as gn
    import os
    if not os.path.exists(gn.FILE_PESI):
        return False
    return con.execute(
        'SELECT 1 FROM assertions a JOIN chunks c ON c.id = a.chunk_id '
        'JOIN sources s ON s.id = c.source_id WHERE a.ruolo IS NULL '
        f"AND s.genere IN ({','.join('?' * len(gn.GENERI_NARRATIVI))}) LIMIT 1",
        gn.GENERI_NARRATIVI).fetchone() is not None


# ── REGISTRO ────────────────────────────────────────────────────────────────
# motore=True → il job si salta (silenziosamente, riprovato al sync successivo)
# se il demone MLX non risponde: mai bloccare l'avvio dell'app.

REGISTRO = [
    {'nome': 'snapshot', 'desc': 'Snapshot cognitivo', 'motore': False,
     'giorni': lambda cfg: (cfg.get('_globale') or {}).get('snapshot', {})
                            .get('frequenza_giorni', 6),
     'marker': None, 'ultimo_fn': lambda con: _ultimo_snapshot(),
     'esegui': _job_snapshot,
     'dovuto': lambda con, cfg: __import__(
         'zeno_engine.snapshot', fromlist=['x']).dovuto(cfg)},

    {'nome': 'mind', 'desc': 'Scansione lacune aperte', 'motore': False,
     'giorni': lambda cfg: (cfg.get('_globale') or {}).get('mind', {})
                            .get('frequenza_giorni', 5),
     'marker': 'mind_last_run.txt', 'esegui': _job_mind,
     'dovuto': lambda con, cfg: __import__(
         'zeno_engine.mind', fromlist=['x']).dovuto(cfg)},

    {'nome': 'graph', 'desc': 'Grafo cognitivo (export)', 'motore': False,
     'giorni': lambda cfg: (cfg.get('_globale') or {}).get('graph', {})
                            .get('frequenza_giorni', 5),
     'marker': 'graph_last_run.txt', 'esegui': _job_graph,
     'dovuto': lambda con, cfg: __import__(
         'zeno_engine.graph', fromlist=['x']).dovuto(cfg)},

    {'nome': 'entita', 'desc': 'Fusione omonimi + proposte alias', 'giorni': 7,
     'motore': False, 'marker': None, 'esegui': _job_entita,
     'dovuto': lambda con, cfg: _dovuto_generico(con, 'entita', 7)},

    {'nome': 'briefing', 'desc': 'Briefing epistemico del giorno',
     'giorni': lambda cfg: (cfg.get('_globale') or {}).get('motore_epistemico', {})
                            .get('briefing', {}).get('frequenza_giorni', 1),
     'motore': True, 'marker': 'briefing_last_run.txt', 'esegui': _job_briefing,
     'dovuto': lambda con, cfg: __import__(
         'zeno_engine.briefing', fromlist=['x']).dovuto(cfg)},

    {'nome': 'journal', 'desc': 'Journal settimanale', 'giorni': 7, 'motore': True,
     'marker': 'journal_last_run.txt', 'esegui': _job_journal,
     'dovuto': lambda con, cfg: __import__(
         'zeno_engine.journal', fromlist=['x']).dovuto(cfg)},

    # Ruolo delle asserzioni narrative nuove (trama/tema/citazione/catalogo):
    # gira quando l'Archivista ne ha estratte di nuove, non a calendario.
    # Prima di 'temi': la trama va riconosciuta prima che il router le dia un tema.
    {'nome': 'giudice', 'desc': 'Ruolo delle asserzioni narrative', 'giorni': 1,
     'motore': True, 'marker': None, 'esegui': _job_giudice,
     'dovuto': _giudice_dovuto},

    {'nome': 'temi', 'desc': 'Limbo nei temi + proposte di temi nuovi', 'giorni': 7,
     'motore': True, 'marker': 'temi_last_run.txt', 'esegui': _job_temi,
     'dovuto': lambda con, cfg: __import__(
         'zeno_engine.temi', fromlist=['x']).dovuto(cfg)},

    {'nome': 'critico', 'desc': 'Ricerca contraddizioni', 'giorni': 7, 'motore': True,
     'marker': None, 'esegui': _job_critico,
     'dovuto': lambda con, cfg: _dovuto_generico(con, 'critico', 7)},

    {'nome': 'schede', 'desc': 'Schede-tema V6', 'giorni': 7, 'motore': True,
     'marker': None, 'esegui': _job_schede,
     'dovuto': lambda con, cfg: _dovuto_generico(con, 'schede', 7)},
]


def _giorni(job, cfg: dict) -> float:
    """Cadenza REALE del job. Per i moduli storici va letta dalla stessa voce di
    config che usa il loro `dovuto()`: scriverla a mano qui la fa divergere e
    il flag STANTIO diventa un falso allarme — l'esatto contrario dello scopo
    di questo registro (verificato il 2026-07-29: snapshot risultava 1 giorno
    mentre il suo default è 6, mind e graph 1 invece di 5)."""
    g = job['giorni']
    if callable(g):
        try:
            return float(g(cfg))
        except Exception:
            return 7.0
    return float(g)


def _ultimo(con, job) -> datetime | None:
    if job.get('ultimo_fn'):
        return job['ultimo_fn'](con)
    if job.get('marker'):
        return _ultimo_da_marker(job['marker'])
    return ultimo_generico(con, job['nome'])


def stato(con, cfg: dict) -> list[dict]:
    """Fotografia di TUTTI i job: quando hanno girato l'ultima volta e se sono
    dovuti. È la risposta a "sembra un bug ma è solo un aggiornamento mancante":
    prima di indagare un comportamento strano, si guarda qui."""
    fuori = []
    for job in REGISTRO:
        u = _ultimo(con, job)
        giorni = _giorni(job, cfg)
        giorni_fa = (datetime.now() - u).total_seconds() / 86400 if u else None
        try:
            dovuto = bool(job['dovuto'](con, cfg))
        except Exception:
            dovuto = True
        fuori.append({
            'nome': job['nome'], 'desc': job['desc'], 'giorni': giorni,
            'motore': job['motore'], 'ultimo': u, 'giorni_fa': giorni_fa,
            'dovuto': dovuto,
            # "in ritardo forte": non gira da più del doppio della sua cadenza
            'stantio': giorni_fa is not None and giorni_fa > giorni * 2,
        })
    return fuori


def dovuti(con, cfg: dict) -> list[str]:
    """Nomi dei job scaduti in questo istante. Estratto da esegui_tutti perché
    serve anche a CHI DECIDE di lanciare la manutenzione (il timer ad app
    aperta), che deve poter chiedere "c'è qualcosa da fare?" senza farlo."""
    scaduti = []
    for job in REGISTRO:
        try:
            if job['dovuto'](con, cfg):
                scaduti.append(job['nome'])
        except Exception:
            scaduti.append(job['nome'])    # nel dubbio, dovuto
    return scaduti


def esegui_tutti(con, cfg: dict, verboso: bool = True,
                 forza: str | None = None, salta_motore: bool = False) -> dict:
    """Esegue i job dovuti (o SOLO `forza`, ignorandone la cadenza).
    Ogni job è isolato: se uno fallisce, gli altri proseguono — un job rotto
    non deve impedire l'avvio dell'app né bloccare gli altri aggiornamenti.

    `salta_motore`: esegue solo i job che NON usano il modello. Serve quando
    l'Archivista sta macinando in background e occupa il motore: aggiungergli
    `schede` o `temi` li mette in coda dietro di lui (con due modelli diversi
    per i ruoli, com'era fino a novembre 2026, voleva dire anche due modelli in
    RAM su 16 GB). I job locali (snapshot, mind, graph, entita) intanto girano
    lo stesso."""
    from zeno_engine import modelli
    from zeno_engine.sync import _backend_raggiungibile
    # LUCCHETTO. Da quando la manutenzione può partire da tre punti diversi
    # (avvio dell'app, watcher a quiete raggiunta, timer ad app aperta), due
    # passate possono sovrapporsi: `schede` e `temi` girerebbero in doppio sullo
    # stesso motore, che su 16 GB significa thrashing e nessuna delle due che
    # finisce. Il lucchetto verifica anche il PID, così una passata uccisa a
    # metà non blocca per sempre le successive. `--forza` lo scavalca: è un
    # gesto esplicito dell'utente, non un automatismo.
    if not forza and processi.in_corso('periodici'):
        if verboso:
            print('  job periodici: una passata è già in corso, salto.')
        return {}
    if not forza:
        processi.segna_avvio('periodici', 'manutenzione in corso')
    motore_ok = None                       # sondato una sola volta, se serve
    esiti = {}
    for job in REGISTRO:
        if forza:
            if job['nome'] != forza:
                continue
        elif not job['dovuto'](con, cfg):
            continue
        if job['motore']:
            if salta_motore:
                esiti[job['nome']] = 'rimandato (batch LLM in corso)'
                if verboso:
                    print(f"  {job['nome']}: rimandato, un batch LLM sta girando")
                continue
            if motore_ok is None:
                # Se il demone è spento lo si avvia, invece di rimandare: con
                # Zeno aperto senza zeno_launcher.py (alias `zeno`, terminale)
                # nessuno lo accendeva e questi job non giravano mai più.
                motore_ok = _backend_raggiungibile() or modelli.assicura_demone()
            if not motore_ok:
                esiti[job['nome']] = 'rimandato (motore MLX spento)'
                if verboso:
                    print(f"  {job['nome']}: rimandato, motore MLX spento")
                continue
        # Stato visibile nel pannello "Processi Zeno". Prima lo scrivevano solo
        # Archivista e Indicizzatore: forzare `schede` dal pannello lanciava un
        # processo VERO che macinava per 40 minuti mentre la sidebar restava
        # identica — "il comando non parte", mentre stava girando eccome.
        # Sta qui, nel registro, e non nei singoli job: chi ne aggiunge uno
        # ottiene la visibilità gratis, come già ottiene cadenza ed esecuzione.
        processi.segna_avvio(job['nome'], job.get('desc', ''))
        t0 = time.time()
        try:
            msg = job['esegui'](con, cfg)
            esiti[job['nome']] = msg
            processi.segna_fine(job['nome'], f'{msg} · {time.time() - t0:.0f}s')
            if verboso:
                print(f"  {job['nome']}: {msg}")
        except Exception as e:
            esiti[job['nome']] = f'ERRORE: {e}'
            processi.segna_fine(job['nome'], f'ERRORE: {e}', errore=True)
            if verboso:
                print(f"  {job['nome']}: ERRORE {e}")
    if not forza:
        # Contare anche i rimandati come "eseguiti" nascondeva proprio il
        # guasto: il 15/09 risultavano "9 job eseguiti" e 5 non erano partiti.
        rimandati = sum(1 for m in esiti.values() if str(m).startswith('rimandato'))
        riepilogo = (f'{len(esiti) - rimandati} job eseguiti'
                     + (f', {rimandati} rimandati' if rimandati else ''))
        processi.segna_fine('periodici', riepilogo if esiti else 'niente da fare')
    return esiti
