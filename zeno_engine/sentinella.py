# scripts/zeno_engine/sentinella.py — sentinella live V6
#
# Divisione dei compiti (risposta alla domanda dell'utente del 2026-07-14):
# - materiale aggiunto a Zeno SPENTO   → lo becca `zeno sync` all'avvio
#   (lanciato da zeno_launcher.py: ingest riconciliazione, embeddings,
#   job periodici, briefing);
# - materiale aggiunto a Zeno ACCESO   → lo becca QUESTO processo: osserva
#   documents/ e import_staging/, e dopo un periodo di quiete rilancia
#   `zeno sync`, che è idempotente e fa ripartire tutta la filiera
#   (ingest → embeddings → job periodici).
#
# Il vecchio watcher V5 (804 righe, trigger su index.py/mind.py/graph.py/
# cross_linker.py ormai in v5_deprecato/, più auto-tag e iniezione
# data_caricamento) è stato sostituito da questo file il 2026-07-14:
# un solo trigger, `zeno sync`, un solo punto di verità. Il vecchio file
# resta nella storia git se servisse ripescare qualcosa.

import json
import os
import subprocess
import sys
import time
import unicodedata
from datetime import datetime

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from zeno_engine.config import carica_config, script_sync_bmb

QUIETE_SECONDI = 60          # aspetta che i file smettano di cambiare
CONTROLLO_OGNI = 5           # passo del loop principale
ESTENSIONI = ('.md', '.csv') # .md nel corpus, .csv nello staging Vivino/Goodreads

# Ponte con il vault Goodreads di BMB (utente, 2026-09-05): i libri si
# gestiscono ormai la' (copertine, pagine Autori, Base per filtrare). Zeno
# resta il punto in cui arriva il nuovo export CSV e in cui l'utente detta a
# voce/chat citazioni e recensioni: dopo ogni sync proviamo a passare quel
# materiale nuovo al vault BMB. Best-effort e sganciato dal resto: se lo
# script o la cartella non ci sono, o se fallisce, non blocca ne' tocca nulla
# di Zeno -- legge solo da documents/, scrive solo dentro BMB.
# Il percorso viene da `integrazioni.sync_bmb` in zeno_config.yaml.
SYNC_BMB_SCRIPT = script_sync_bmb()


def _lancia_sync_bmb() -> None:
    if not SYNC_BMB_SCRIPT or not os.path.exists(SYNC_BMB_SCRIPT):
        return
    try:
        subprocess.Popen(['python3', SYNC_BMB_SCRIPT],
                         cwd=os.path.dirname(SYNC_BMB_SCRIPT),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f'[watcher] sync_da_zeno.py (BMB) non lanciato: {e}')



def _aggiorna_stato_processo(cfg: dict, nome: str, dati: dict) -> None:
    """Scrive lo stato in stats/processi_attivi.json (letto dalla sidebar
    della chat). Scrittura atomica: .tmp + os.replace."""
    try:
        stats_dir = cfg['percorsi']['stats']
        os.makedirs(stats_dir, exist_ok=True)
        json_path = os.path.join(stats_dir, 'processi_attivi.json')
        stato = {}
        if os.path.exists(json_path):
            try:
                stato = json.load(open(json_path, encoding='utf-8'))
            except Exception:
                stato = {}
        stato[nome] = dati
        tmp = json_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            json.dump(stato, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, json_path)
    except Exception as e:
        print(f'[watcher] processi_attivi.json non aggiornabile: {e}')


def _rilevante(path: str) -> bool:
    nome = os.path.basename(path)
    if nome.startswith('.') or nome.endswith('.tmp'):
        return False                          # AppleDouble ._*, nascosti, temporanei
    return nome.lower().endswith(ESTENSIONI)


class _Handler(FileSystemEventHandler):
    """Registra soltanto il momento dell'ultimo evento rilevante: il loop
    principale decide quando la quiete è sufficiente per lanciare il sync."""

    def __init__(self):
        self.ultimo_evento: float | None = None
        self.ultimo_file = ''

    def on_any_event(self, event):
        if event.is_directory:
            return
        # Un rename atomico (Finder "Sostituisci", ri-download, rsync) arriva
        # come FileMovedEvent: il nome vero sta in dest_path, mentre src_path
        # e' il temporaneo (scartato da _rilevante). Guardiamo entrambi.
        candidati = [event.src_path, getattr(event, 'dest_path', None)]
        for grezzo in candidati:
            if not grezzo:
                continue
            path = unicodedata.normalize('NFC', grezzo)
            if not _rilevante(path):
                continue
            self.ultimo_evento = time.time()
            self.ultimo_file = os.path.basename(path)
            return


def _lancia_sync(cfg: dict) -> subprocess.Popen:
    brain = cfg['percorsi']['brain']
    python = os.path.join(brain, '.venv_zeno', 'bin', 'python')
    env = os.environ.copy()
    return subprocess.Popen(
        [python, '-m', 'zeno_engine', 'sync'],
        cwd=_zeno_s, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    cfg = carica_config()
    percorsi = cfg['percorsi']
    sentinella = os.path.join(percorsi['stats'], '.zeno_stop')
    if os.path.exists(sentinella):          # residuo di uno spegnimento passato
        try:
            os.remove(sentinella)
        except Exception:
            pass

    handler = _Handler()
    observer = Observer()
    osservate = []
    for chiave in ('documents', 'staging'):
        cartella = percorsi.get(chiave, '')
        if cartella and os.path.isdir(cartella):
            observer.schedule(handler, cartella, recursive=True)
            osservate.append(cartella)
    if not osservate:
        print('[watcher] nessuna cartella da osservare: esco.')
        return
    observer.start()
    print(f'[watcher] attivo su: {", ".join(osservate)}')
    _aggiorna_stato_processo(cfg, 'watcher', {
        'stato': 'attivo', 'ultimo_avvio': datetime.now().isoformat(timespec='seconds'),
        'dettaglio': f'osserva {len(osservate)} cartelle → zeno sync'})

    sync_proc: subprocess.Popen | None = None
    try:
        while True:
            time.sleep(CONTROLLO_OGNI)
            if os.path.exists(sentinella):
                print('[watcher] sentinella .zeno_stop: esco.')
                break
            if sync_proc is not None and sync_proc.poll() is not None:
                _aggiorna_stato_processo(cfg, 'watcher', {
                    'stato': 'attivo',
                    'ultimo_evento': datetime.now().isoformat(timespec='seconds'),
                    'dettaglio': f'sync completato (exit {sync_proc.returncode})'})
                sync_proc = None
                _lancia_sync_bmb()  # porta le novita' anche nel vault BMB
            if handler.ultimo_evento is None:
                continue
            if time.time() - handler.ultimo_evento < QUIETE_SECONDI:
                continue
            if sync_proc is not None:        # un sync sta ancora girando:
                continue                     # l'evento resta in coda per il giro dopo
            ultimo = handler.ultimo_file
            handler.ultimo_evento = None
            print(f'[watcher] quiete raggiunta (ultimo file: {ultimo}) → zeno sync')
            _aggiorna_stato_processo(cfg, 'watcher', {
                'stato': 'in_corso',
                'ultimo_evento': datetime.now().isoformat(timespec='seconds'),
                'dettaglio': f'sync in corso (trigger: {ultimo})'})
            sync_proc = _lancia_sync(cfg)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join(timeout=5)
        if sync_proc is not None and sync_proc.poll() is None:
            try:
                sync_proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                sync_proc.terminate()
        _aggiorna_stato_processo(cfg, 'watcher', {
            'stato': 'completato',
            'ultimo_evento': datetime.now().isoformat(timespec='seconds'),
            'dettaglio': 'watcher fermato'})


if __name__ == '__main__':
    main()
