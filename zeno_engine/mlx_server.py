# Demone di inferenza MLX — occupa il posto che era di `ollama serve`.
#
# Perché un demone e non MLX in-process: chat_ui e i job batch girano in
# PROCESSI DIVERSI (chat_ui lancia `zeno …` come subprocess). Con MLX importato
# in ognuno, due processi caricherebbero DUE COPIE dello stesso 7-8B: su un M3
# da 16 GB è swap immediato. Il demone tiene una copia sola e fa rispettare la
# regola concordata — mai due motori 7-8B residenti insieme.
#
# L'API è modellata su quella di Ollama (stessi percorsi, stessi campi) così il
# resto del motore epistemico non cambia forma: cambia solo chi risponde.
#
# Avvio:  python -m zeno_engine.mlx_server [--porta 11500]

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from zeno_engine.mlx_motore import ErroreCaricamento, Registro

PORTA_DEFAULT = int(os.environ.get('ZENO_MLX_PORTA', '11500'))


def _log(messaggio: str, con_traccia: bool = False):
    """
    Riga diagnostica su stdout — il launcher la reindirizza a /tmp/zeno_mlx.log
    (vedi zeno_launcher.py). SOLO per i guasti veri: le richieste normali non
    stampano nulla (log_message resta silenziato) per non affogare l'unico
    segnale che conta in mesi di traffico di routine.
    """
    print(f'{datetime.now().isoformat(timespec="seconds")} {messaggio}', flush=True)
    if con_traccia:
        traceback.print_exc()

REGISTRO = Registro()
# Serializza SOLO i job batch fra loro (Archivista, Indicizzatore, Temi,
# Critico, Schede, Briefing): non hanno mai bisogno di girare in parallelo,
# e tenerli in fila evita che due job pesanti si contendano la RAM insieme.
#
# Una richiesta marcata 'priorita' (chat/maieutica interattiva) lo SCAVALCA
# e chiama REGISTRO.chat() direttamente, senza aspettare il turno. Non è un
# azzardo: mlx_lm crea uno stream Metal per-THREAD (`mx.new_thread_local_stream`,
# vedi mlx_lm/generate.py) e una cache KV nuova a ogni chiamata (mai condivisa
# fra chiamate concorrenti) — la libreria è pensata per questo. Prima ogni
# domanda in chat aspettava la fine del chunk di Archivista in corso: misurato
# il 2026-09-07, 5s → 17s → 30s con Archivista in coda sullo stesso lucchetto.
LUCCHETTO = threading.Lock()
_NESSUN_LUCCHETTO = contextlib.nullcontext()


def _lucchetto_per(corpo: dict):
    """Il lucchetto dei batch, o nessun lucchetto se la richiesta è 'priorita'."""
    return _NESSUN_LUCCHETTO if corpo.get('priorita') else LUCCHETTO


def _durata(keep_alive) -> float:
    """Accetta i formati keep_alive di Ollama: 0, 300, '30m', '1h', '5s'."""
    if keep_alive is None:
        return 1800.0
    if isinstance(keep_alive, (int, float)):
        return float(keep_alive)
    testo = str(keep_alive).strip()
    moltiplicatori = {'s': 1, 'm': 60, 'h': 3600}
    if testo and testo[-1] in moltiplicatori:
        try:
            return float(testo[:-1]) * moltiplicatori[testo[-1]]
        except ValueError:
            return 1800.0
    try:
        return float(testo)
    except ValueError:
        return 1800.0


class Gestore(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    # ── utilità di risposta ──────────────────────────────────────────────────

    def _json(self, dati: dict, stato: int = 200):
        corpo = json.dumps(dati).encode('utf-8')
        self.send_response(stato)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _errore(self, messaggio: str, stato: int = 500):
        self._json({'error': messaggio}, stato)

    def _corpo(self) -> dict:
        lunghezza = int(self.headers.get('Content-Length') or 0)
        if not lunghezza:
            return {}
        return json.loads(self.rfile.read(lunghezza) or b'{}')

    def log_message(self, formato, *args):
        pass          # il demone gira sotto il launcher: niente rumore su stderr

    # ── instradamento ────────────────────────────────────────────────────────

    def do_GET(self):
        if self.path.startswith('/api/tags'):
            self._json({'models': [{'name': m['name'], 'model': m['name']}
                                   for m in REGISTRO.residenti()]})
        elif self.path.startswith('/api/ps'):
            self._json({'models': REGISTRO.residenti()})
        else:
            self._errore('percorso sconosciuto', 404)

    def do_POST(self):
        try:
            corpo = self._corpo()
        except json.JSONDecodeError as e:
            return self._errore(f'JSON non valido: {e}', 400)
        try:
            if self.path.startswith('/api/chat'):
                return self._chat(corpo)
            if self.path.startswith('/api/embed'):
                return self._embed(corpo)
            if self.path.startswith('/api/rerank'):
                return self._rerank(corpo)
            if self.path.startswith('/api/generate'):
                return self._generate(corpo)
            return self._errore('percorso sconosciuto', 404)
        except ErroreCaricamento as e:
            # 503 e non 500: il modello non c'è o non entra in RAM. È un guasto
            # di backend, e chi chiama deve poterlo distinguere da una risposta
            # inservibile (vedi modelli.ErroreConnessione).
            _log(f'ErroreCaricamento su {self.path} model={corpo.get("model")}: {e}')
            return self._errore(str(e), 503)
        except Exception as e:                      # noqa: BLE001
            # Qui finisce ogni guasto vero (Metal, OOM, bug): senza questa riga
            # il fusibile dell'Archivista scattava senza lasciare NESSUNA
            # traccia di cosa avesse fatto tacere il demone (2026-09-07).
            _log(f'{type(e).__name__} su {self.path} model={corpo.get("model")}: {e}',
                 con_traccia=True)
            return self._errore(f'{type(e).__name__}: {e}')

    # ── endpoint ─────────────────────────────────────────────────────────────

    def _chat(self, corpo: dict):
        modello = corpo.get('model')
        if not modello:
            return self._errore('campo "model" mancante', 400)
        messaggi = corpo.get('messages') or []
        schema = corpo.get('format') or None
        opzioni = corpo.get('options') or {}
        keep_alive = _durata(corpo.get('keep_alive'))
        lucchetto = _lucchetto_per(corpo)

        if not corpo.get('stream'):
            with lucchetto:
                pezzi = list(REGISTRO.chat(modello, messaggi, schema, opzioni, keep_alive))
            return self._json({
                'model': modello,
                'message': {'role': 'assistant', 'content': ''.join(pezzi)},
                'done': True,
                'done_reason': 'stop',
            })

        # Streaming NDJSON, una riga per token: stesso formato di Ollama, così
        # adapter_v5 continua a leggere pezzo['message']['content'].
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()

        def scrivi(riga: dict):
            dati = (json.dumps(riga) + '\n').encode('utf-8')
            self.wfile.write(f'{len(dati):X}\r\n'.encode() + dati + b'\r\n')
            self.wfile.flush()

        with lucchetto:
            try:
                for testo in REGISTRO.chat(modello, messaggi, schema, opzioni, keep_alive):
                    scrivi({'model': modello,
                            'message': {'role': 'assistant', 'content': testo},
                            'done': False})
            except (BrokenPipeError, ConnectionResetError):
                return          # l'utente ha chiuso la chat a metà risposta
            except Exception as e:                   # noqa: BLE001
                # Gli header 200 sono già partiti: non si può più rispondere
                # con un errore HTTP pulito. Senza questa riga un crollo a
                # metà streaming (Metal, OOM) spariva senza lasciare traccia —
                # il chiamante vedeva solo una risposta troncata.
                _log(f'{type(e).__name__} durante lo streaming, model={modello}: {e}',
                     con_traccia=True)
                raise
        scrivi({'model': modello,
                'message': {'role': 'assistant', 'content': ''},
                'done': True, 'done_reason': 'stop'})
        self.wfile.write(b'0\r\n\r\n')
        self.wfile.flush()

    def _embed(self, corpo: dict):
        modello = corpo.get('model')
        if not modello:
            return self._errore('campo "model" mancante', 400)
        ingresso = corpo.get('input')
        if isinstance(ingresso, str):
            ingresso = [ingresso]
        if not ingresso:
            return self._errore('campo "input" mancante', 400)
        # Il retrieval di una domanda interattiva vettorializza la query PRIMA
        # di generare la risposta: se questo passaggio aspettasse il lucchetto
        # dei job batch, "priorita" non servirebbe a molto sull'attesa totale.
        with _lucchetto_per(corpo):
            vettori = REGISTRO.embed(modello, list(ingresso))
        self._json({'model': modello, 'embeddings': vettori})

    def _rerank(self, corpo: dict):
        """Punteggi di pertinenza domanda/testi (cross-encoder, mlx_rerank)."""
        modello = corpo.get('model')
        if not modello:
            return self._errore('campo "model" mancante', 400)
        testi = corpo.get('documents') or []
        with _lucchetto_per(corpo):
            punti = REGISTRO.rerank(modello, corpo.get('query') or '', list(testi))
        self._json({'model': modello, 'scores': punti})

    def _generate(self, corpo: dict):
        """
        Nel motore epistemico /api/generate serve a UNA cosa sola: scaricare un
        modello dalla RAM (prompt vuoto, keep_alive 0) — vedi modelli.scarica().
        Il resto del contratto è supportato per completezza.
        """
        modello = corpo.get('model')
        if not modello:
            return self._errore('campo "model" mancante', 400)
        prompt = corpo.get('prompt') or ''
        keep_alive = _durata(corpo.get('keep_alive'))
        if not prompt and keep_alive == 0:
            with LUCCHETTO:
                REGISTRO.scarica(modello)
            return self._json({'model': modello, 'response': '', 'done': True})
        with LUCCHETTO:
            pezzi = list(REGISTRO.chat(modello, [{'role': 'user', 'content': prompt}],
                                       corpo.get('format') or None,
                                       corpo.get('options') or {}, keep_alive))
        self._json({'model': modello, 'response': ''.join(pezzi), 'done': True})


def _guardiano():
    """Scarica i modelli con keep_alive scaduto, come faceva Ollama."""
    while True:
        time.sleep(30)
        try:
            with LUCCHETTO:
                REGISTRO.scadi()
        except Exception:                            # noqa: BLE001
            pass


# Secondi senza un token (lettura del prompt compresa) oltre i quali una
# generazione è considerata appesa. Il 17-18/11 un calcolo Metal non è mai
# tornato (`IOSurfaceSharedEvent waitUntilSignaledValue`, CPU a zero): il
# thread era perso, teneva LUCCHETTO e ogni richiesta dopo restava in coda
# fino al timeout del client — una prova è rimasta ferma 12 ore. Il passo più
# lento sano è la lettura di un prompt lungo, nell'ordine delle decine di
# secondi; il timeout del client (modelli.TIMEOUT_SECONDI) è 300.
SOGLIA_STALLO = float(os.environ.get('ZENO_MLX_STALLO', '120'))


def _stallo(registro: Registro, soglia: float) -> float:
    """Secondi di fermo se oltre la soglia, altrimenti 0."""
    fermo = registro.fermo_da()
    return fermo if fermo > soglia else 0.0


def _vigila_stallo(soglia: float = SOGLIA_STALLO):
    """
    Se una generazione resta ferma oltre la soglia, il demone ESCE.

    Un thread chiuso dentro un wait di Metal non si può interrompere da
    Python, e il demone non torna sano da solo: solo un processo nuovo riparte
    pulito. Il client (modelli.ClienteMLX) vede la connessione cadere, riavvia
    il demone con assicura_demone() e ripete la richiesta una volta.
    Non tocca LUCCHETTO: il thread appeso lo tiene, e _guardiano ci resta
    bloccato sopra.
    """
    while True:
        time.sleep(10)
        fermo = _stallo(REGISTRO, soglia)
        if fermo:
            _log(f'Generazione ferma da {fermo:.0f} s (soglia {soglia:.0f} s): '
                 'GPU probabilmente appesa in Metal. Il demone esce per essere riavviato.')
            os._exit(3)      # niente pulizia: con un thread appeso potrebbe non finire


def main(argv=None):
    p = argparse.ArgumentParser(description='Demone di inferenza MLX per ZENO')
    p.add_argument('--porta', type=int, default=PORTA_DEFAULT)
    p.add_argument('--host', default='127.0.0.1')
    args = p.parse_args(argv)

    threading.Thread(target=_guardiano, daemon=True).start()
    if SOGLIA_STALLO > 0:
        threading.Thread(target=_vigila_stallo, daemon=True).start()
    server = ThreadingHTTPServer((args.host, args.porta), Gestore)
    print(f'MLX in ascolto su http://{args.host}:{args.porta}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    sys.exit(main())
