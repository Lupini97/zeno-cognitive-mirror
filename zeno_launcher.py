#!/usr/bin/env python3
# zeno_launcher.py
"""
Zeno Launcher — Orchestratore principale.

Avvia Zeno con un click su Zeno.app (o da terminale).
Gestisce l'intero ciclo di vita:
  Demone MLX → zeno sync → Streamlit (chat + dashboard) → finestra.
La chiusura della finestra spegne tutto.

Dipendenze aggiuntive (una tantum):
    pip install pillow pywebview

Uso:
    python zeno_launcher.py

Per impostare un percorso BRAIN non standard:
    ZENO_BRAIN=/percorso/personalizzato python zeno_launcher.py
"""

import os
import sys
import time
import shutil
import platform
import threading
import subprocess
from pathlib import Path


# ══ Risoluzione percorso BRAIN ═════════════════════════════════════════════════

def trova_brain() -> Path | None:
    """
    Cerca il percorso BRAIN con questa priorità:
      1. Variabile d'ambiente ZENO_BRAIN
      2. Directory parent dello script (se scripts/ è sottocartella)
      3. Directory dello script stesso
      4. Volumi esterni macOS (/Volumes/*)
      5. Candidati standard per OS (home/Zeno, lettere unità Windows)
    """
    # 1. Variabile d'ambiente esplicita
    if env := os.environ.get('ZENO_BRAIN'):
        p = Path(env)
        if (p / 'zeno_config.yaml').exists():
            return p

    # 2/3. Relativo allo script
    script_dir = Path(__file__).resolve().parent
    for candidate in [script_dir.parent, script_dir]:
        if (candidate / 'zeno_config.yaml').exists():
            return candidate

    # 4. Volumi esterni (macOS)
    if platform.system() == 'Darwin':
        try:
            for vol in Path('/Volumes').iterdir():
                if (vol / 'zeno_config.yaml').exists():
                    return vol
        except PermissionError:
            pass

    # 5. Candidati standard
    candidates = [Path.home() / 'Zeno', Path.home() / 'zeno']
    if platform.system() == 'Windows':
        for lettera in 'DEFGHIJ':
            candidates.append(Path(f'{lettera}:/Zeno'))
    for c in candidates:
        if (c / 'zeno_config.yaml').exists():
            return c

    return None


# ══ Gestione servizi ══════════════════════════════════════════════════════════

_processi: dict[str, subprocess.Popen] = {}
_brain: Path | None = None


def _python_venv(brain: Path) -> str:
    """Ritorna il percorso python del venv, cross-platform."""
    if platform.system() == 'Windows':
        candidates = [brain / '.venv_zeno' / 'Scripts' / 'python.exe',
                      brain / '.venv' / 'Scripts' / 'python.exe']
    else:
        candidates = [
            # V6: il runtime del motore (sqlite-vec richiede un Python con
            # enable_load_extension; il 3.11 del V5 non ce l'ha).
            brain / '.venv_zeno' / 'bin' / 'python',
            brain / '.venv' / 'bin' / 'python',
            brain / '.venv' / 'bin' / 'python3',
        ]
    for p in candidates:
        if p.exists():
            return str(p)
    return sys.executable




def _attendi_servizio(url: str, timeout: int = 60) -> bool:
    """Attende che un servizio HTTP risponda. Ritorna True se raggiunto."""
    import urllib.request
    import urllib.error
    scadenza = time.time() + timeout
    while time.time() < scadenza:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(1.5)
    return False


# ── Dialog errore fatale ──────────────────────────────────────────────────────

def _mostra_errore_fatale(titolo: str, messaggio: str):
    """
    Mostra un dialog di errore bloccante (tkinter) e termina il processo.
    Usato per dipendenze hard che impediscono l'avvio.
    """
    try:
        import tkinter.messagebox as mb
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        mb.showerror(f'Zeno — {titolo}', messaggio)
        root.destroy()
    except Exception:
        print(f'[Zeno Launcher] ERRORE FATALE: {titolo}\n{messaggio}',
              file=sys.stderr)
    os._exit(1)


# ── Avvio servizi applicativi ─────────────────────────────────────────────────

def avvia_servizi(brain: Path) -> dict[str, str]:
    """
    Avvia nell'ordine: demone MLX → Streamlit chat → Streamlit dashboard.
    Ritorna un dict di errori (chiave=servizio, valore=messaggio).
    Errori non bloccanti: Zeno si avvia anche con servizi degradati.
    """
    global _brain, _processi
    _brain = brain

    python  = _python_venv(brain)
    scripts = brain / 'scripts'
    stats   = brain / 'stats'
    stats.mkdir(parents=True, exist_ok=True)

    # Rimuove sentinel di stop da sessioni precedenti
    (stats / '.zeno_stop').unlink(missing_ok=True)

    # Libera le porte occupate da sessioni precedenti (macOS/Linux)
    if platform.system() != 'Windows':
        for porta in [8501, 8502]:
            try:
                r = subprocess.run(['lsof', '-ti', f'tcp:{porta}'],
                                   capture_output=True, text=True)
                for pid in r.stdout.strip().split():
                    subprocess.run(['kill', '-9', pid], capture_output=True)
            except Exception:
                pass

    errori: dict[str, str] = {}
    env_base = os.environ.copy()

    # ── Blocca apertura automatica browser ───────────────────────────────────
    _st_config = (
        '[server]\n'
        'headless = true\n'
        # CORS e XSRF accesi (2026-11-19): con CORS spento la connessione
        # della chat accetta qualunque origine, quindi un sito aperto nel
        # browser potrebbe leggere Zeno. Acceso, Streamlit accetta solo
        # localhost e gli indirizzi di questo computer.
        'enableCORS = true\n'
        'enableXsrfProtection = true\n\n'
        '[browser]\n'
        'gatherUsageStats = false\n\n'
        '[client]\n'
        'showErrorDetails = false\n'
    )
    # Solo la config del progetto: quella globale (~/.streamlit) vale per
    # tutte le app Streamlit del computer e non va toccata.
    for _st_dir in [scripts / '.streamlit']:
        try:
            _st_dir.mkdir(parents=True, exist_ok=True)
            (_st_dir / 'config.toml').write_text(_st_config, encoding='utf-8')
        except Exception:
            pass
    # Tema di Zeno (tema_zeno.py, variante in zeno_config.yaml → ui.tema) solo
    # nella config del progetto: in ~/.streamlit varrebbe per ogni app Streamlit.
    try:
        sys.path.insert(0, str(scripts))
        from interfaccia import tema_zeno
        from zeno_engine.config import carica_config as _carica_cfg_tema
        tema_zeno.scrivi_config(tema_zeno.variante_attiva(_carica_cfg_tema()),
                                str(scripts / '.streamlit' / 'config.toml'))
    except Exception:
        pass

    env_streamlit = {
        **env_base,
        'STREAMLIT_SERVER_HEADLESS': 'true',
        'STREAMLIT_BROWSER_GATHER_USAGE_STATS': 'false',
        'BROWSER': '/usr/bin/true',
    }

    # ── 1. Demone MLX ─────────────────────────────────────────────────────────
    # Un solo processo tiene i modelli in RAM per tutti (chat + job batch):
    # con MLX importato in ogni processo, due 7-8B insieme farebbero saltare
    # l'M3 da 16 GB.
    try:
        # /tmp/zeno_mlx.log, non DEVNULL: un fusibile dell'Archivista (5
        # timeout/connessioni di fila) prima non lasciava TRACCIA di cosa
        # avesse fatto tacere il demone — usciva solo un generico "il demone
        # non risponde" senza un solo dettaglio da controllare (visto sul
        # campo il 2026-09-07). Stesso schema di /tmp/zeno_chat.log.
        _log_mlx = open('/tmp/zeno_mlx.log', 'w')
        _processi['mlx'] = subprocess.Popen(
            [python, '-m', 'zeno_engine.mlx_server'],
            cwd=str(scripts),
            env=env_base,
            stdout=_log_mlx,
            stderr=_log_mlx,
        )
    except Exception as e:
        errori['mlx'] = str(e)


    # ── 2. Watcher V6 (riattivato 2026-07-14 su richiesta dell'utente) ────────
    # Divisione dei compiti: `zeno sync` all'avvio mette in pari ciò che è
    # cambiato a Zeno SPENTO; il watcher copre Zeno ACCESO — osserva
    # documents/ e import_staging/ e, a quiete raggiunta, rilancia `zeno sync`
    # (idempotente). Si ferma con la sentinella stats/.zeno_stop.
    # La sentinella vive in zeno_engine/sentinella.py (era scripts/watcher.py
    # fino al 29/09/2026): si lancia come modulo, da scripts/.
    watcher = scripts / 'zeno_engine' / 'sentinella.py'
    if watcher.exists():
        try:
            _processi['watcher'] = subprocess.Popen(
                [python, '-m', 'zeno_engine.sentinella'],
                cwd=str(scripts),
                env=env_base,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            errori['watcher'] = str(e)

    _st_flags = [
        '--server.headless', 'true',
        '--server.address', 'localhost',
    ]

    # ── 3. Streamlit app unica (:8501 — chat + dashboard come pagine) ────────
    # zeno_app.py = entry multipagina (richiesta l'utente: dashboard nel
    # pannello della stessa finestra). Fallback su chat_ui.py se manca.
    chat = scripts / 'zeno_app.py'
    if not chat.exists():
        chat = scripts / 'chat_ui.py'
    if chat.exists():
        try:
            _log_chat = open('/tmp/zeno_chat.log', 'w')
            _processi['chat'] = subprocess.Popen(
                [python, '-m', 'streamlit', 'run', str(chat),
                 '--server.port', '8501', *_st_flags],
                cwd=str(scripts),
                env=env_streamlit,
                stdout=_log_chat,
                stderr=_log_chat,
            )
        except Exception as e:
            errori['chat'] = str(e)

    # ── 4. Streamlit dashboard (:8502) ────────────────────────────────────────
    dash = scripts / 'dashboard.py'
    if dash.exists():
        try:
            _log_dash = open('/tmp/zeno_dash.log', 'w')
            _processi['dashboard'] = subprocess.Popen(
                [python, '-m', 'streamlit', 'run', str(dash),
                 '--server.port', '8502', *_st_flags],
                cwd=str(scripts),
                env=env_streamlit,
                stdout=_log_dash,
                stderr=_log_dash,
            )
        except Exception as e:
            errori['dashboard'] = str(e)

    return errori


def termina_servizi():
    """
    Termina tutti i processi applicativi in ordine sicuro e pulisce le porte.
    """
    global _brain, _processi

    # Segnala al watcher di fermarsi tramite sentinel
    if _brain:
        try:
            (_brain / 'stats' / '.zeno_stop').touch()
        except Exception:
            pass

    # Termina nell'ordine inverso di avvio
    for nome in ['dashboard', 'chat', 'watcher', 'mlx']:
        proc = _processi.pop(nome, None)
        if proc is None:
            continue
        try:
            proc.terminate()
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.kill()
        except Exception:
            pass

    # Forza chiusura porte residue (macOS/Linux)
    if platform.system() != 'Windows':
        for porta in [8501, 8502]:
            try:
                r = subprocess.run(['lsof', '-ti', f'tcp:{porta}'],
                                   capture_output=True, text=True)
                for pid in r.stdout.strip().split():
                    subprocess.run(['kill', '-9', pid], capture_output=True)
            except Exception:
                pass
        subprocess.run(['pkill', '-f', 'zeno_engine.mlx_server'], capture_output=True)
    else:
        subprocess.run(['taskkill', '/F', '/IM', 'python.exe'],
                       capture_output=True, shell=False)


# ══ Sequenza di boot (gira in thread, aggiorna splash) ══════════════════════

def _sequenza_boot(splash, brain: Path, on_chiusura_finestra):
    """
    Avvia i servizi e aggiorna la splash screen con il progresso.
    Chiamata in thread daemon da main().

    Ordine e percentuali:
       10%       → motore zeno.db pronto (nessuna dipendenza esterna)
       20%       → avvio demone MLX + Streamlit
       28%       → verifica connessione demone MLX
       34%       → zeno sync (messa in pari: staging, corpus, embeddings)
       48%       → attesa chat (8501)
       72%       → attesa dashboard (8502)
       88%       → caricamento profilo cognitivo
      100%       → Zeno pronto, la finestra passa dallo splash alla chat
                  (stessa finestra: vedi nota in zeno_splash.py sul perché
                  non se ne apre una seconda)
    """

    # Il motore è zeno.db (SQLite + sqlite-vec): nessuna dipendenza esterna
    # da avviare/attendere prima dei servizi applicativi.
    splash.set_status('Motore pronto (zeno.db).', 10)
    time.sleep(0.4)

    # ── Servizi applicativi ───────────────────────────────────────────────────
    splash.set_status('Avvio motore MLX…', 20)
    errori = avvia_servizi(brain)

    if 'mlx' in errori:
        splash.set_status(f'⚠ MLX: {errori["mlx"]}', 26)
        time.sleep(2)
    else:
        splash.set_status('Verifica connessione MLX…', 28)
        _attendi_servizio('http://127.0.0.1:11500/api/ps', timeout=60)

    # ── zeno sync: Zeno si sveglia e si mette in pari (V6) ──────────────────
    # Nuovi/modificati/rimossi a Zeno spento + CSV in staging + embeddings.
    # Best-effort: un sync fallito non blocca l'avvio della UI.
    splash.set_status('Sincronizzazione archivio (zeno sync)…', 34)
    # L'output andava in `capture_output` e veniva buttato via, e ogni errore
    # finiva in `except Exception: pass`. Il sync ha così potuto scadere sui
    # 900 s (la vettorializzazione dell'arretrato, ~26 minuti) a ogni avvio
    # senza lasciare la minima traccia: da fuori sembrava che non partisse mai.
    # Ora scrive in /tmp/zeno_sync.log — stesso file di avvia_zeno.sh — e un
    # timeout o un errore si vedono sullo splash invece di sparire.
    try:
        with open('/tmp/zeno_sync.log', 'w') as _log_sync:
            subprocess.run(
                # --solo-locali: temi/schede (decine di minuti) non devono
                # tenere ferma la finestra; li riprende il timer della chat.
                [_python_venv(brain), '-m', 'zeno_engine', 'sync',
                 '--solo-locali'],
                cwd=str(brain / 'scripts'),
                env=os.environ.copy(),
                stdout=_log_sync, stderr=subprocess.STDOUT, timeout=900,
            )
    except subprocess.TimeoutExpired:
        splash.set_status('⚠ sync oltre i 15 min: interrotto '
                          '(vedi /tmp/zeno_sync.log)', 40)
        time.sleep(2)
    except Exception as e:
        splash.set_status(f'⚠ sync fallito: {str(e)[:60]}', 40)
        time.sleep(2)

    # Ponte con il vault Goodreads di BMB (utente, 2026-09-05): stessa logica
    # di watcher.py -- best-effort, sganciato, legge solo da Zeno e scrive
    # solo dentro BMB. Copre il caso "Zeno era spento": zeno sync ha appena
    # messo in pari il corpus, ora proviamo a passare le novita' a BMB.
    from zeno_engine.config import script_sync_bmb
    _sync_bmb = script_sync_bmb()   # integrazioni.sync_bmb in zeno_config.yaml
    if _sync_bmb and os.path.exists(_sync_bmb):
        try:
            subprocess.Popen(['python3', _sync_bmb],
                             cwd=os.path.dirname(_sync_bmb),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    splash.set_status('Avvio interfaccia chat…', 48)
    ok_chat = _attendi_servizio('http://localhost:8501', timeout=70)
    if not ok_chat:
        splash.set_status('⚠ Chat non risponde — continuo comunque', 60)
        time.sleep(1.5)

    splash.set_status('Avvio dashboard…', 72)
    _attendi_servizio('http://localhost:8502', timeout=35)

    splash.set_status('Carico profilo cognitivo…', 88)
    time.sleep(0.6)

    splash.set_status('Zeno pronto.', 100)
    time.sleep(0.7)
    splash.vai_a_chat(on_close=on_chiusura_finestra)


# ══ Entry point ═══════════════════════════════════════════════════════════════

def main():
    # Verifica dipendenze (pystray rimosso)
    mancanti = []
    for pkg in ['PIL', 'webview']:
        try:
            __import__(pkg)
        except ImportError:
            mancanti.append({'PIL': 'pillow', 'webview': 'pywebview'}[pkg])
    if mancanti:
        print(f'[Zeno Launcher] Dipendenze mancanti: pip install {" ".join(mancanti)}')
        sys.exit(1)

    # Trova BRAIN
    brain = trova_brain()
    if brain is None:
        import tkinter.messagebox as mb
        import tkinter as tk
        tk.Tk().withdraw()
        mb.showerror(
            'Zeno — Percorso non trovato',
            'Impossibile trovare la cartella BRAIN con zeno_config.yaml.\n\n'
            'Imposta la variabile d\'ambiente:\n'
            '  ZENO_BRAIN=/percorso/della/tua/cartella'
        )
        sys.exit(1)

    # Aggiunge scripts/ al path Python
    scripts = brain / 'scripts'
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))

    print(f'[Zeno Launcher] BRAIN: {brain}')
    print('[Zeno Launcher] Avvio diretto.')

    # ── Splash → boot → chat, un'unica finestra per tutto il ciclo di vita ───
    # (vedi zeno_splash.py: un secondo webview.start() per una seconda
    # finestra non torna in modo affidabile su macOS — causa del blocco
    # allo splash osservato il 19/08/2026).
    from interfaccia.zeno_splash import ZenoSplash
    splash = ZenoSplash(brain)

    def on_chiusura_finestra():
        print('[Zeno Launcher] Finestra chiusa — arresto servizi…')
        termina_servizi()

    splash.run(lambda s: _sequenza_boot(s, brain, on_chiusura_finestra))
    # blocca finché la finestra si chiude (unico webview.start() del processo)

    print('[Zeno Launcher] Uscita.')
    if _processi:
        termina_servizi()


if __name__ == '__main__':
    main()
