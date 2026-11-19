# Gestione dell'alternanza dei modelli in RAM (vincolo M3 16 GB).
#
# Regola concordata con l'utente (checkpoint 2026-07-10): i due motori 7-8B
# non stanno mai in memoria insieme — quando lavora uno, l'altro è spento.
# Il costo è il caricamento (~30-60 s dall'SSD USB) al primo uso dopo lo
# scambio; le chiamate successive sono a caldo (keep_alive 30 min).
#
# Il backend è il demone MLX (zeno_engine/mlx_server.py), non più Ollama:
# stessa forma di API, stessa disciplina di RAM, modelli in formato MLX.
# Questo modulo è l'UNICO punto in cui il resto del motore tocca il backend.

import json
import os
import urllib.error
import urllib.request

from zeno_engine.config import con_nome

# Client con timeout: una chiamata appesa (con Ollama, incastrato in
# "Stopping...", visto sul campo il 2026-07-10: batch fermo 81 minuti) deve
# FALLIRE, così scattano retry/fusibile invece di bloccare la notte.
TIMEOUT_SECONDI = 300

HOST = os.environ.get('ZENO_MLX_HOST', '127.0.0.1')
# 11500 e non 114xx: su questa macchina un `ollama serve` occupa già 11434/11435,
# e puntare per sbaglio a Ollama darebbe risposte da un backend che non è più il
# nostro. Sovrascrivibile con ZENO_MLX_PORTA.
PORTA = int(os.environ.get('ZENO_MLX_PORTA', '11500'))

# Nomi Ollama ancora presenti in config o negli appunti → repo MLX equivalente.
# Serve a non rompere una config vecchia: i modelli veri sono quelli MLX.
ALIAS = {
    'llama3.1:8b': 'mlx-community/Meta-Llama-3.1-8B-Instruct-4bit',
    'qwen2.5:7b-instruct-q4_K_M': 'mlx-community/Qwen2.5-7B-Instruct-4bit',
    'qwen2.5:3b-instruct': 'mlx-community/Qwen2.5-3B-Instruct-4bit',
    'bge-m3': 'mlx-community/bge-m3-mlx-fp16',
}


def risolvi(nome: str) -> str:
    return ALIAS.get(nome, nome)


class ErroreMLX(RuntimeError):
    """Il demone ha risposto con un errore, o non risponde affatto."""


class ErroreConnessione(ErroreMLX):
    """
    Il demone non risponde: spento, in stallo, o richiesta scaduta.

    Distinguerlo dall'errore di risposta NON è pedanteria: l'Archivista conta
    solo i guasti di backend per far scattare il fusibile, mentre i fallimenti
    di contenuto consumano i tentativi del chunk e oltre la soglia lo saltano
    per sempre. Confondere i due significa bruciare chunk buoni mentre il
    motore era semplicemente spento.
    """


class ErroreRisposta(ErroreMLX):
    """Il demone ha risposto, ma con un errore applicativo (HTTP 4xx/5xx)."""


class ClienteMLX:
    """
    Shim con la stessa superficie di `ollama.Client` per i soli metodi usati
    dal motore: chat(), embed(), generate(). Le risposte sono dizionari con
    le stesse chiavi, così i ~15 punti che fanno r['message']['content'] o
    r['embeddings'] non cambiano di una riga.
    """

    def __init__(self, timeout: float = TIMEOUT_SECONDI):
        self.timeout = timeout
        self.base = f'http://{HOST}:{PORTA}'

    def _posta(self, percorso: str, carico: dict, flusso: bool = False):
        try:
            return self._posta_una(percorso, carico, flusso)
        except ErroreConnessione:
            # Il demone esce da solo quando una generazione resta appesa in
            # Metal (mlx_server._vigila_stallo). Se non risponde più lo si
            # riavvia e si ripete UNA volta; se risponde, l'errore è un altro
            # (un timeout vero, per esempio) e si propaga com'è.
            if raggiungibile() or not assicura_demone():
                raise
            return self._posta_una(percorso, carico, flusso)

    def _posta_una(self, percorso: str, carico: dict, flusso: bool = False):
        richiesta = urllib.request.Request(
            self.base + percorso,
            data=json.dumps(carico).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST')
        try:
            risposta = urllib.request.urlopen(richiesta, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            dettaglio = e.read().decode('utf-8', 'replace')[:400]
            if e.code in (502, 503, 504):
                # Modello che non si carica o demone in affanno: guasto di
                # backend, non risposta inservibile.
                raise ErroreConnessione(f'{percorso}: HTTP {e.code} — {dettaglio}') from e
            raise ErroreRisposta(f'{percorso}: HTTP {e.code} — {dettaglio}') from e
        except OSError as e:
            raise ErroreConnessione(
                f'{percorso}: demone MLX non raggiungibile ({e}). '
                f'Avvialo con: python -m zeno_engine.mlx_server') from e
        if flusso:
            return _righe(risposta)
        with risposta:
            return json.loads(risposta.read())

    def chat(self, model: str, messages: list, format=None, options=None,
             keep_alive=None, stream: bool = False, priorita: bool = False, **_):
        messages = [dict(m, content=con_nome(m.get('content'))) if isinstance(m, dict) else m
                    for m in messages]
        carico = {'model': risolvi(model), 'messages': messages, 'stream': bool(stream)}
        if format:
            carico['format'] = format
        if options:
            if options.get('cache_prefisso'):
                options = dict(options, cache_prefisso=con_nome(options['cache_prefisso']))
            carico['options'] = options
        if keep_alive is not None:
            carico['keep_alive'] = keep_alive
        if priorita:
            # Scavalca il lucchetto dei job batch nel demone (mlx_server.py):
            # SOLO per le chiamate interattive che aspettano davanti all'utente
            # (ask, maieutica). Archivista/Indicizzatore/Temi/Critico/Schede/
            # Briefing restano a priorità normale — non scriverla mai lì:
            # comprometterebbe il senso stesso della priorità.
            carico['priorita'] = True
        return self._posta('/api/chat', carico, flusso=stream)

    def embed(self, model: str, input, priorita: bool = False, **_):
        carico = {'model': risolvi(model), 'input': input}
        if priorita:
            carico['priorita'] = True
        return self._posta('/api/embed', carico)

    def rerank(self, model: str, query: str, documents: list[str], priorita: bool = False):
        carico = {'model': risolvi(model), 'query': query, 'documents': documents}
        if priorita:
            carico['priorita'] = True
        return self._posta('/api/rerank', carico)

    def generate(self, model: str, prompt: str = '', keep_alive=None, **_):
        carico = {'model': risolvi(model), 'prompt': con_nome(prompt)}
        if keep_alive is not None:
            carico['keep_alive'] = keep_alive
        return self._posta('/api/generate', carico)


def _righe(risposta):
    """NDJSON in ingresso → un dizionario per token, come lo streaming Ollama."""
    with risposta:
        for riga in risposta:
            riga = riga.strip()
            if riga:
                yield json.loads(riga)


def client() -> ClienteMLX:
    return ClienteMLX(timeout=TIMEOUT_SECONDI)


def raggiungibile(timeout: float = 3.0) -> bool:
    """Il demone MLX risponde? Sostituisce il ping a localhost:11434."""
    try:
        with urllib.request.urlopen(f'http://{HOST}:{PORTA}/api/ps', timeout=timeout):
            return True
    except OSError:
        return False


def assicura_demone(attesa_secondi: float = 60.0) -> bool:
    """Avvia il demone MLX se non risponde e aspetta che sia pronto.

    Serve a chi lancia lavoro col modello FUORI da zeno_launcher.py (l'unico
    che avvia il demone): con Zeno aperto da `avvia_zeno.sh` o dal terminale
    i job periodici col motore venivano rimandati in silenzio a ogni passata
    — fermi dal 2026-09-07 al 25/09, visto con `manutenzione --stato`.
    Il demone resta acceso dopo: la chat aperta lo usa, e i modelli escono
    comunque dalla RAM allo scadere del loro keep_alive."""
    if raggiungibile():
        return True
    import subprocess
    import sys
    import time
    scripts = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        subprocess.Popen([sys.executable, '-m', 'zeno_engine.mlx_server'],
                         cwd=scripts, stdout=open('/tmp/zeno_mlx.log', 'a'),
                         stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        return False
    limite = time.time() + attesa_secondi
    while time.time() < limite:
        if raggiungibile(timeout=1.0):
            return True
        time.sleep(1)
    return False


def scarica(nome_modello: str):
    """Scarica il modello dalla RAM (best effort: se non è caricato, non fa nulla)."""
    try:
        client().generate(model=nome_modello, prompt='', keep_alive=0)
    except Exception:
        pass


def prepara_motore(cfg: dict, ruolo: str) -> str:
    """
    Ritorna il modello per `ruolo`, scaricando gli altri motori 7-8B così non
    stanno mai in RAM insieme:
    - 'batch'        → motore_batch (7-8B): pensiero esplicito/dialogico,
      dove conta la qualità dell'estrazione più del tempo.
    - 'batch_rapido' → router 3B (già in uso altrove per topic/classificazione,
      nessun modello nuovo da scaricare): backlog acquisito (libri/formazione),
      dove il volume conta più della finezza — deciso con l'utente il
      2026-07-15 per accelerare senza uscire dal vincolo M3 16GB/zero-cloud.
    - 'ask'          → motore_ask (7-8B): risposte discorsive.
    Il router 3B è leggero e può coesistere con un 7-8B, ma qui lo teniamo
    da solo comunque: batch_rapido gira su migliaia di chunk consecutivi,
    meglio non condividere RAM con un motore che nel frattempo non serve.
    """
    batch, ask_, router = (cfg['modelli']['motore_batch'],
                           cfg['modelli']['motore_ask'],
                           cfg['modelli']['router'])
    if ruolo == 'batch_rapido':
        for m in (batch, ask_):
            if m != router:
                scarica(m)
        return router
    if ruolo == 'batch':
        if ask_ != batch:
            scarica(ask_)
        return batch
    if batch != ask_:
        scarica(batch)
    return ask_


def prepara_solo(cfg: dict, nome_modello: str):
    """
    Scarica dalla RAM ogni altro modello configurato (motore_batch, motore_ask,
    router, embeddings) prima di usare `nome_modello`. Estende ai modelli
    'minori' (router 3B, embeddings) la stessa disciplina di prepara_motore()
    sui motori 7-8B: caricarne più di uno insieme ha causato un blocco reale
    di ~2 ore (2026-07-20, ogni richiesta in timeout a 5 minuti) quando
    `indicizza` ha caricato bge-m3 mentre qwen2.5:3b era ancora residente
    dall'Archivista (keep_alive 30 min) appena finito — con ~1,3 GB di RAM
    libera su un M3 16 GB, due modelli insieme bastano a far collassare il
    backend, qualunque esso sia.
    Va chiamata da ogni funzione che invoca un modello FUORI da prepara_motore
    (oggi: embed_chunks, assegna_topics).
    """
    for chiave in ('motore_batch', 'motore_ask', 'router', 'embeddings', 'reranker'):
        m = cfg['modelli'].get(chiave)
        if m and m != nome_modello:
            scarica(m)
