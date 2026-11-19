# Motore generativo MLX — sostituisce le chiamate `ollama.chat`.
#
# Espone la stessa semantica su cui è costruito tutto il motore epistemico:
#   - messaggi in formato chat (system/user), template applicato dal tokenizer;
#   - `format=<JSON schema>` che VINCOLA la generazione (non un suggerimento nel
#     prompt): senza vincolo un 3B sbaglia il JSON abbastanza spesso da far
#     saltare json.loads() nei ~8 punti che ci contano sopra. Il vincolo è
#     imposto token per token con llguidance, che ha un kernel Metal nativo;
#   - streaming token per token per la chat.
#
# Differenza voluta rispetto a Ollama: `num_ctx` NON tronca più il prompt.
# Ollama tagliava in silenzio la coda del prompt (cioè la domanda) quando
# superava num_ctx — bug documentato in adapter_v5.py. Qui il prompt passa
# intero e num_ctx serve solo a emettere un avviso.

from __future__ import annotations

import copy
import gc
import hashlib
import os
import threading
import time

import mlx.core as mx

from zeno_engine.config import BRAIN

# I pesi stanno accanto agli ex-blob di Ollama, sullo stesso disco di BRAIN:
# sono decine di GB, non vanno nella home.
CACHE_MODELLI = os.path.join(BRAIN, 'models', 'mlx')
# Copie scaricate da mlx_scarica, una cartella per repo. Hanno la PRECEDENZA
# sulla cache HuggingFace: così il motore lavora anche senza rete e senza
# dipendere dal formato interno della cache.
CACHE_LOCALE = os.path.join(CACHE_MODELLI, 'locale')


def configura_cache():
    """
    Punta HuggingFace alla cache di ZENO. Va chiamata PRIMA di importare
    huggingface_hub, quindi tutti i moduli che scaricano pesi la invocano
    all'avvio. Xet è disattivato: sul campo (2026-09-05) restava appeso a
    0 byte per minuti prima di partire, il download HTTP classico no.
    """
    os.environ.setdefault('HF_HOME', CACHE_MODELLI)
    os.environ.setdefault('HF_HUB_DISABLE_XET', '1')


configura_cache()


def cartella_locale(repo: str) -> str:
    """Dove mlx_scarica mette (o metterà) i file di `repo`."""
    return os.path.join(CACHE_LOCALE, repo.replace('/', '__'))


def percorso(nome: str) -> str:
    """
    Risolve un nome di modello in una cartella leggibile da mlx_lm:
    percorso assoluto → sé stesso; copia locale scaricata → quella;
    altrimenti si ripiega sulla cache HuggingFace (che scarica se serve).
    """
    if nome.startswith('/'):
        return nome
    locale = cartella_locale(nome)
    if os.path.isdir(locale) and os.path.exists(os.path.join(locale, 'config.json')):
        return locale
    from zeno_engine.config import rete
    if not rete('download_modelli'):
        raise FileNotFoundError(
            f'{nome}: pesi assenti in {locale} e download spento '
            f'(rete.download_modelli in zeno_config.yaml).')
    from huggingface_hub import snapshot_download
    return snapshot_download(nome)

# Tetto di sicurezza quando il chiamante non passa num_predict: senza un
# limite un modello può "correre" senza fermarsi (visto sul campo il
# 2026-07-21, richieste mai concluse dopo 9+ minuti).
MAX_TOKEN_DEFAULT = 2048


class ErroreCaricamento(RuntimeError):
    """
    I pesi non si caricano (mancanti, corrotti, RAM insufficiente).

    Ha una classe propria perché il demone deve rispondere 503 e non 500: per
    chi chiama è un guasto di BACKEND, non una risposta inservibile. L'Archivista
    conta i guasti di backend nel fusibile, mentre i fallimenti di contenuto
    consumano i tentativi del chunk — con un modello che non si carica, ogni
    chunk brucerebbe i suoi tentativi e verrebbe saltato per sempre.
    """


class _Caricato:
    __slots__ = ('modello', 'tokenizer', 'll_tokenizer', 'ultimo_uso', 'scadenza',
                 'prefisso', 'lucchetto_prefisso')

    def __init__(self, modello, tokenizer):
        self.modello = modello
        self.tokenizer = tokenizer
        self.ll_tokenizer = None      # costruito solo se serve il JSON vincolato
        self.ultimo_uso = time.time()
        self.scadenza = 0.0
        # Cache del prompt condiviso fra due chiamate (decisione, poi
        # risposta): (impronta del testo, numero di token, cache). Una sola
        # per modello: serve alla coppia di chiamate di una stessa domanda.
        self.prefisso = None
        self.lucchetto_prefisso = threading.Lock()


class Registro:
    """
    Tiene i modelli residenti in RAM e li scarica su richiesta.

    Replica la disciplina che su Ollama era affidata a keep_alive: sull'M3 da
    16 GB due motori 7-8B insieme mandano la macchina in thrashing, quindi chi
    chiama decide esplicitamente cosa resta dentro (vedi modelli.prepara_motore).
    """

    def __init__(self):
        self._modelli: dict[str, _Caricato] = {}
        self._embedder = None
        self._embedder_nome = ''
        self._reranker = None
        self._reranker_nome = ''
        # Protegge SOLO il caricamento (dict + pesi da disco): la generazione
        # vera gira senza lucchetto, vedi la nota su LUCCHETTO in mlx_server.py.
        self._lucchetto_caricamento = threading.Lock()
        # Generazioni in corso: chiave → istante (monotonic) in cui si è
        # cominciato ad aspettare il modello, None mentre il pezzo prodotto è
        # in mano al chiamante. Lo legge la sentinella dello stallo del
        # demone (mlx_server._vigila_stallo), vedi fermo_da().
        self._attese: dict[object, float | None] = {}

    def fermo_da(self) -> float:
        """
        Da quanti secondi la generazione più ferma aspetta il modello senza
        ricevere un token; 0 se nessuna sta aspettando.

        Conta solo l'attesa del MODELLO, non quella del chiamante: un client
        lento a leggere lo streaming non è uno stallo del demone.
        """
        ora = time.monotonic()
        inizi = [t for t in tuple(self._attese.values()) if t is not None]
        return max((ora - t for t in inizi), default=0.0)

    # ── ciclo di vita ────────────────────────────────────────────────────────

    def carica(self, nome: str) -> _Caricato:
        if nome not in self._modelli:
            with self._lucchetto_caricamento:
                if nome not in self._modelli:         # un'altra chiamata potrebbe
                    from mlx_lm.utils import load     # averlo già caricato nel frattempo
                    try:
                        modello, tokenizer = load(percorso(nome))
                    except Exception as e:             # noqa: BLE001
                        raise ErroreCaricamento(f'{nome}: {type(e).__name__}: {e}') from e
                    self._modelli[nome] = _Caricato(modello, tokenizer)
        voce = self._modelli[nome]
        voce.ultimo_uso = time.time()
        return voce

    def scarica(self, nome: str) -> bool:
        """Libera la RAM occupata da `nome`. Best effort, non solleva."""
        if nome not in self._modelli:
            if nome == self._embedder_nome and self._embedder is not None:
                self._embedder = None
                self._embedder_nome = ''
                gc.collect()
                mx.clear_cache()
                return True
            if nome == self._reranker_nome and self._reranker is not None:
                self._reranker = None
                self._reranker_nome = ''
                gc.collect()
                mx.clear_cache()
                return True
            return False
        del self._modelli[nome]
        gc.collect()
        mx.clear_cache()
        return True

    def residenti(self) -> list[dict]:
        voci = [{'name': n, 'ultimo_uso': v.ultimo_uso, 'scadenza': v.scadenza}
                for n, v in self._modelli.items()]
        if self._embedder is not None:
            voci.append({'name': self._embedder_nome, 'ultimo_uso': 0, 'scadenza': 0})
        if self._reranker is not None:
            voci.append({'name': self._reranker_nome, 'ultimo_uso': 0, 'scadenza': 0})
        return voci

    def scadi(self) -> list[str]:
        """Scarica i modelli il cui keep_alive è passato. Chiamata dal demone."""
        ora = time.time()
        scaduti = [n for n, v in self._modelli.items() if 0 < v.scadenza <= ora]
        for n in scaduti:
            self.scarica(n)
        return scaduti

    # ── generazione ──────────────────────────────────────────────────────────

    def chat(self, nome: str, messaggi: list[dict], schema=None,
             opzioni: dict | None = None, keep_alive: float = 1800.0):
        """Generatore di frammenti di testo. Il chiamante decide se accumularli."""
        opzioni = opzioni or {}
        voce = self.carica(nome)
        voce.scadenza = time.time() + keep_alive if keep_alive else 0.0

        # Qwen3 e gli altri ibridi ragionano ad alta voce se non glielo si
        # vieta: su `ask` sono centinaia di token di latenza in più, e in batch
        # il blocco <think> finirebbe davanti al JSON. Acceso solo su richiesta
        # esplicita (options.pensiero); i template che non conoscono il flag
        # lo ignorano senza rompersi.
        pensiero = bool(opzioni.get('pensiero', False))
        prompt = voce.tokenizer.apply_chat_template(
            messaggi, add_generation_prompt=True, tokenize=False,
            enable_thinking=pensiero)

        from mlx_lm.generate import stream_generate
        from mlx_lm.sample_utils import make_sampler

        campionatore = make_sampler(
            temp=float(opzioni.get('temperature', 0.0)),
            top_p=float(opzioni.get('top_p', 0.0)),
        )
        max_token = int(opzioni.get('num_predict') or MAX_TOKEN_DEFAULT)
        processori = []
        if schema:
            processori.append(_processore_schema(voce, schema))

        # options.cache_prefisso: testo con cui inizia il messaggio e che
        # un'altra chiamata ripete uguale. Il suo calcolo si fa una volta e si
        # riusa. Copia, non riuso diretto: Qwen3.5 ha strati ad attenzione
        # lineare, la cui cache non si può accorciare dopo una generazione.
        # Da qui in avanti ogni attesa del modello è sorvegliata (fermo_da):
        # il 17-18/11 un thread è rimasto per ore dentro un wait di Metal.
        chiave = object()
        self._attese[chiave] = time.monotonic()
        try:
            cache, ingresso = None, prompt
            prefisso = opzioni.get('cache_prefisso')
            if prefisso and prompt.find(prefisso) >= 0 \
                    and voce.lucchetto_prefisso.acquire(blocking=False):
                try:
                    taglio = prompt.find(prefisso) + len(prefisso)
                    cache = _cache_prefisso(voce, prompt[:taglio])
                    ingresso = voce.tokenizer.encode(prompt[taglio:], add_special_tokens=False)
                finally:
                    voce.lucchetto_prefisso.release()

            risposte = stream_generate(
                voce.modello, voce.tokenizer, ingresso,
                max_tokens=max_token, sampler=campionatore,
                logits_processors=processori or None,
                **({'prompt_cache': cache} if cache is not None else {}))
            flusso = self._sorvegliato(chiave, risposte)
            for testo in (flusso if pensiero else _senza_pensiero(flusso)):
                if testo:
                    self._attese[chiave] = None       # in mano al chiamante
                    yield testo
        finally:
            self._attese.pop(chiave, None)
        voce.ultimo_uso = time.time()
        voce.scadenza = time.time() + keep_alive if keep_alive else 0.0

    def _sorvegliato(self, chiave, risposte):
        """Testi di stream_generate, segnando quando si aspetta il modello.
        L'attesa comprende la lettura del prompt, che precede il primo token."""
        iteratore = iter(risposte)
        while True:
            self._attese[chiave] = time.monotonic()
            try:
                p = next(iteratore)
            except StopIteration:
                return
            if p.text:
                yield p.text

    # ── embeddings ───────────────────────────────────────────────────────────

    def embed(self, nome: str, testi: list[str]) -> list[list[float]]:
        if self._embedder is None or self._embedder_nome != nome:
            from zeno_engine.mlx_embed import Embedder
            try:
                self._embedder = Embedder(percorso(nome))
            except Exception as e:                   # noqa: BLE001
                raise ErroreCaricamento(f'{nome}: {type(e).__name__}: {e}') from e
            self._embedder_nome = nome
        return self._embedder.encode(testi)

    # ── reranker (opzionale, retrieval.reranker in config) ───────────────────

    def rerank(self, nome: str, domanda: str, testi: list[str]) -> list[float]:
        if self._reranker is None or self._reranker_nome != nome:
            from zeno_engine.mlx_rerank import Reranker
            try:
                self._reranker = Reranker(percorso(nome))
            except Exception as e:                   # noqa: BLE001
                raise ErroreCaricamento(f'{nome}: {type(e).__name__}: {e}') from e
            self._reranker_nome = nome
        return self._reranker.punteggi(domanda, testi)


def _copia_cache(cache: list) -> list:
    """Copia indipendente di una cache mlx_lm. Gli strati aggiornano gli
    array assegnandoli (anche per fette, che in MLX riscrivono l'oggetto):
    basta che la copia abbia oggetti array suoi, i dati restano condivisi
    finché nessuno li cambia."""
    def dup(v):
        if isinstance(v, mx.array):
            return mx.array(v)
        if isinstance(v, list):
            return [dup(x) for x in v]
        return v
    copie = []
    for c in cache:
        n = copy.copy(c)
        for k, v in vars(c).items():
            setattr(n, k, dup(v))
        copie.append(n)
    return copie


def _cache_prefisso(voce: _Caricato, testo: str) -> list:
    """Cache del modello dopo aver letto `testo`, calcolata una volta per
    testo uguale. Restituisce una copia da consumare."""
    from mlx_lm.models.cache import make_prompt_cache
    impronta = hashlib.sha1(testo.encode('utf-8')).hexdigest()
    if voce.prefisso is None or voce.prefisso[0] != impronta:
        voce.prefisso = None
        token = voce.tokenizer.encode(testo, add_special_tokens=False)
        cache = make_prompt_cache(voce.modello)
        for i in range(0, len(token), 2048):
            voce.modello(mx.array(token[i:i + 2048])[None], cache=cache)
            mx.eval([c.state for c in cache])
        voce.prefisso = (impronta, len(token), cache)
    return _copia_cache(voce.prefisso[2])


def _senza_pensiero(flusso):
    """
    Sopprime i blocchi <think>…</think> dei modelli ibridi (Qwen3).

    Serve anche con `enable_thinking=False`: il flag agisce sul template della
    chat, ma un modello addestrato a ragionare può aprire il blocco lo stesso.
    Il filtro lavora sul flusso, non sul testo finito, perché i tag arrivano
    spezzati fra un token e l'altro — `</th` in un pezzo e `ink>` nel successivo.
    """
    APERTURA, CHIUSURA = '<think>', '</think>'
    dentro = False
    coda = ''
    for pezzo in flusso:
        coda += pezzo
        while coda:
            if dentro:
                i = coda.find(CHIUSURA)
                if i == -1:
                    # Dentro il ragionamento si butta via tutto, tranne quel
                    # tanto che potrebbe essere l'inizio del tag di chiusura.
                    coda = coda[-(len(CHIUSURA) - 1):]
                    break
                coda = coda[i + len(CHIUSURA):]
                dentro = False
                continue
            i = coda.find(APERTURA)
            if i == -1:
                # Si trattiene la coda che potrebbe essere un tag a metà.
                sicuri = len(coda) - (len(APERTURA) - 1)
                if sicuri > 0:
                    testo, coda = coda[:sicuri], coda[sicuri:]
                    yield testo
                break
            if i:
                yield coda[:i]
            coda = coda[i + len(APERTURA):]
            dentro = True
    if coda and not dentro:
        yield coda


def _processore_schema(voce: _Caricato, schema):
    """
    Vincola la generazione a uno schema JSON, token per token.

    llguidance calcola a ogni passo la maschera dei token ammessi dalla
    grammatica e la applica ai logits con un kernel Metal: i token che
    romperebbero il JSON ricevono -inf e non possono essere campionati.
    """
    import llguidance
    from llguidance.mlx import apply_token_bitmask
    from llguidance.numpy import allocate_token_bitmask, fill_next_token_bitmask

    if voce.ll_tokenizer is None:
        from llguidance.hf import from_tokenizer
        # ~1 s di costruzione: si fa una volta per modello, non per chiamata.
        interno = getattr(voce.tokenizer, '_tokenizer', voce.tokenizer)
        voce.ll_tokenizer = from_tokenizer(interno)

    # Spazi fra i token JSON permessi ma LIMITATI a 8 caratteri.
    # Con la spaziatura libera (default) un modello può degenerare in un ciclo
    # di soli a-capo: Qwen3-8B ne ha prodotti 15.051 di fila il 2026-09-06,
    # bruciando l'intero budget di token e lasciando il JSON monco, senza che
    # la grammatica potesse fermarlo — gli spazi erano tutti leciti.
    # Vietarli del tutto (whitespace_flexible=False) chiude il ciclo ma
    # peggiora la resa: misurato lo stesso giorno, il modello imbocca la strada
    # più corta e risponde `{"assertions":[]}` (0 asserzioni su 5 chunk, e il
    # router cala da 11/25 a 8/25). Il tetto tiene insieme le due cose.
    if isinstance(schema, dict) and 'x-regex' in schema:
        # Testo libero con una forma imposta, non JSON: la chat con decisione
        # (adapter_v5) vincola solo la prima riga a SI/NO e lascia libero il
        # resto. Una sola lettura del prompt per decisione e risposta.
        grammatica = llguidance.LLMatcher.grammar_from_regex(schema['x-regex'])
    else:
        grammatica = llguidance.LLMatcher.grammar_from_json_schema(
            schema, overrides={'whitespace_pattern': r'[\x20\x0A\x0D\x09]{0,8}'})
    errore = llguidance.LLMatcher.validate_grammar(grammatica, voce.ll_tokenizer)
    if errore:
        raise ValueError(f'schema JSON non compilabile: {errore}')
    matcher = llguidance.LLMatcher(voce.ll_tokenizer, grammatica)
    maschera = allocate_token_bitmask(1, voce.ll_tokenizer.vocab_size)
    consumati = {'fino_a': None}

    def processore(token, logits):
        # Alla prima chiamata `token` contiene la coda del prompt, che NON va
        # data in pasto alla grammatica: si registra solo il punto di partenza.
        if consumati['fino_a'] is None:
            consumati['fino_a'] = token.size
        elif token.size > consumati['fino_a']:
            for t in token[consumati['fino_a']:].tolist():
                matcher.consume_token(int(t))
            consumati['fino_a'] = token.size
        fill_next_token_bitmask(matcher, maschera, 0)
        return apply_token_bitmask(logits, maschera)

    return processore
