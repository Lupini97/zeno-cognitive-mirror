# Adapter V5→V6 — la UI dell'utente resta identica, cambia solo il motore.
#
# chat_ui.py parlava con LlamaIndex/Qdrant attraverso un contratto minimo:
#   engine = get_engine(modalita); r = engine.query(prompt)
#   r.response_gen   → iteratore di token (streaming)
#   r.source_nodes   → [n] con n.metadata{'file_name','file_path'} e n.score
# Questo modulo replica ESATTAMENTE quel contratto sopra zeno.db + MLX:
# nessun cambiamento a struttura, layout o logica della UI.
#
# Include il filtro vino ereditato dallo StratoBalancer V5: i chunk vivino
# sono esclusi dal retrieval sulle query non enologiche (cluster isolato).

import itertools
import os
import re
import time

from zeno_engine import ask as _ask
from zeno_engine import config as _config
from zeno_engine import db as _db
from zeno_engine import modelli as _modelli
from zeno_engine import prompt_chat as _prompt_chat

# Ereditata da StratoBalancer (chat_ui V5): decide se una query è enologica.
VINO_KW = frozenset({
    'vino', 'vini', 'wine', 'degust', 'cantina', 'vigna', 'terroir',
    'sommelier', 'uvaggio', 'vitigno', 'annata', 'calice', 'vivino',
    'enologi', 'barrique', 'tannin', 'acidità', 'bouquet', 'viticoltur',
    'enoteca', 'vendemmia', 'mosto', 'fermentaz',
})


def query_enologica(testo: str) -> bool:
    t = (testo or '').lower()
    return any(k in t for k in VINO_KW)


class NodoCompat:
    """Mimica NodeWithScore quel tanto che serve a _fonti_link/_render_fonti."""

    def __init__(self, frammento: dict, numero: int = 0):
        self.metadata = {
            'file_name': os.path.basename(frammento['path']),
            'file_path': frammento['path'],
            'strato': frammento['strato'],
            # Per l'elenco fonti della chat (tema_zeno.html_fonti): il numero
            # è quello con cui il documento compare nel prompt, [1], [2]...
            'numero': numero,
            'title': frammento.get('title'),
            'created_at': frammento.get('created_at'),
            'conoscenza': frammento.get('conoscenza'),
            'chunk_id': frammento.get('chunk_id'),
        }
        self.score = round(float(frammento['score']), 4)
        self.testo = frammento['text']


class RispostaCompat:
    def __init__(self, generatore, source_nodes):
        self.response_gen = generatore
        self.source_nodes = source_nodes
        self._testo = ''
        self.decisione = None      # True/False solo con decisione o decidi_prima
        self.secondi_decisione = None

    def __str__(self):
        return self._testo


# Prima riga SI o NO; col NO solo la riga di LACUNA, poi la generazione finisce.
REGEX_DECISIONE = r'SI\n[\s\S]*|NO\nLACUNA: [^\n]{1,400}'

# Decisione separata (EngineZeno(decidi_prima=True)): chiamata breve, prima
# della risposta. SI chiude subito la generazione; NO porta con sé la LACUNA,
# che diventa la risposta senza una seconda chiamata.
REGEX_DECIDI = r'SI|NO\nLACUNA: [^\n]{1,400}'
# Segue i documenti e la domanda (prompt_chat.PREFISSO_V4), che la risposta
# ripete uguali: il motore li legge una volta sola.
ISTRUZIONE_DECIDI = (
    'Prima di rispondere, decidi se i documenti qui sopra contengono le '
    'informazioni necessarie per rispondere nel merito alla domanda. '
    'Scrivi SI se almeno un documento risponde nel merito. Scrivi NO se '
    "l'argomento richiesto non è trattato o se ne parla solo di sfuggita: "
    'in quel caso vai a capo e scrivi "LACUNA:" con una sola frase breve in '
    'italiano su che cosa manca nei documenti.')


def _in_loop(righe: list[str]) -> bool:
    """Il 3.5 a volte si avvita: la stessa riga di seguito, o una fila di
    soli numeri di citazione ([1], [2]...). Visto in 5 risposte su 30."""
    if len(righe) >= 3 and righe[-1] == righe[-2] == righe[-3]:
        return True
    return len(righe) >= 4 and all(re.fullmatch(r'\[[\d,\s]+\]', x) for x in righe[-4:])


class EngineZeno:
    """Sostituto del query engine LlamaIndex: retrieval sqlite-vec + MLX."""

    def __init__(self, prompt_template: str, cfg: dict | None = None,
                 modello: str | None = None, decisione: bool = False,
                 decidi_prima: bool = False):
        self.template = str(prompt_template)
        self.cfg = cfg or _config.load()
        # Solo per `zeno valuta` (confronto fra modelli): la chat usa sempre
        # il motore_ask della config.
        self.modello = modello
        # Prima riga vincolata a SI/NO ("i documenti rispondono?"), poi la
        # risposta, nella stessa generazione: il prompt si legge una volta sola.
        # Va col template ARCHIVIO v3, che spiega al modello la prima riga.
        self.decisione = decisione
        # Decisione con una chiamata a parte, prima di scrivere (template v4).
        self.decidi_prima = decidi_prima

    def _recupera(self, domanda: str) -> list[dict]:
        con = _db.connect(self.cfg['db'], vec=True)
        try:
            dl = domanda.lower()
            personale = any(k in dl for k in _ask.KEYWORDS_PERSONALE)
            frammenti = _ask.recupera(con, domanda, self.cfg, personale=personale)
            if not query_enologica(domanda):
                frammenti = [f for f in frammenti if '/vivino/' not in f['path'].lower()]
            return frammenti
        finally:
            con.close()

    def query(self, domanda: str) -> RispostaCompat:
        frammenti = self._recupera(domanda)
        # Citazioni testuali (dell'utente o degli autori letti) dentro i
        # frammenti recuperati: chat_ui.py non va toccato, quindi non possiamo
        # aggiungere la regola nel suo template ARCHIVIO/MAIEUTICA; le
        # aggreghiamo qui, dentro il blocco di contesto di ciascun frammento,
        # così il modello le vede comunque e le sue stesse regole ("cita
        # sempre dall'archivio prima di ogni altra fonte") le raccolgono.
        citazioni = _ask.citazioni_pertinenti(frammenti)
        # Un numero per DOCUMENTO, non per frammento: due pezzi dello stesso
        # file sono la stessa fonte, e la risposta la cita come [n].
        numeri: dict[str, int] = {}
        for f in frammenti:
            numeri.setdefault(f['path'], len(numeri) + 1)

        def _blocco(f: dict) -> str:
            # `conoscenza` in testa al blocco: la regola 6 del template ARCHIVIO
            # ordina di dichiarare il livello di conoscenza di ogni libro "dal
            # campo conoscenza nel frontmatter", ma il frontmatter viene scartato
            # in ingestione — il modello riceveva un ordine su un dato che non
            # poteva vedere, e lo colmava inventando. Ora il dato c'è davvero.
            marca = ''
            if f.get('conoscenza'):
                marca = f" | conoscenza: {f['conoscenza']}"
                if f['conoscenza'] in _ask.PESI_CONOSCENZA:
                    marca += ' (LIBRO NON LETTO: solo sinossi)'
            testo = (f"[{numeri[f['path']]}] "
                     f"[FILE: {os.path.basename(f['path'])} | titolo: {f['title']} | "
                     f"strato: {f['strato']}{marca} | "
                     f"data: {f['created_at'] or '?'}]\n{f['text']}")
            cit_f = [c for c in citazioni if c['chunk_id'] == f['chunk_id']]
            if cit_f:
                testo += '\nCITAZIONI DA RIPRENDERE TESTUALMENTE SE PERTINENTI: ' + \
                    ' | '.join(f'«{c["testo"]}» ({c["autore"]})' for c in cit_f)
            return testo

        contesto = '\n\n'.join(_blocco(f) for f in frammenti) \
            or '(nessun documento pertinente in archivio)'

        # Gli avvisi in testa al contesto, non solo la marca dentro ogni blocco.
        # `zeno ask` da riga di comando aveva già questa protezione (ask.rispondi);
        # la chat — cioè l'unico posto da cui l'utente usa davvero Zeno — no, e
        # una regola sepolta al punto 6 di un elenco di nove non regge contro un
        # modello da 7B: con i marcatori nei soli blocchi, "Parker Pyne indaga"
        # (mai letto) veniva comunque dato per letto. Un avviso esplicito prima
        # dei documenti è la stessa informazione messa dove il modello la vede.
        avvisi = []
        non_letti = [f['title'] for f in frammenti
                     if (f.get('conoscenza') or '') in _ask.PESI_CONOSCENZA]
        if non_letti:
            elenco = ', '.join(f'«{t}»' for t in dict.fromkeys(non_letti))
            avvisi.append(
                f'ATTENZIONE — LIBRI NON LETTI fra i documenti qui sotto: {elenco}. '
                f'Di questi esiste solo la scheda con la sinossi: NON dire che sono '
                f'stati letti, non attribuire loro un giudizio e non dedurne un '
                f'parere. Se li nomini, di\' che ti basi sulla sola sinossi.')
        personale = any(k in domanda.lower() for k in _ask.KEYWORDS_PERSONALE)
        if personale:
            suoi = [f for f in frammenti if f['strato'] in ('esplicito', 'dialogico')]
            if not suoi:
                avvisi.append(
                    'ATTENZIONE — la domanda riguarda il pensiero di @UTENTE@, ma '
                    'NESSUN documento qui sotto viene dai suoi scritti: sono tutti '
                    'materiale che ha letto. Apri la risposta con "LACUNA: '
                    'l\'archivio non contiene una tua posizione esplicita su '
                    'questo" e riporta ciò che dicono gli autori attribuendolo A '
                    'LORO. Non dedurre né inventare una posizione di @UTENTE@.')
            elif len(suoi) * 2 < len(frammenti):
                avvisi.append(
                    'ATTENZIONE — solo una minoranza dei documenti qui sotto '
                    '(strato esplicito o dialogico) è pensiero di @UTENTE@: la sua '
                    'posizione va ricavata SOLO da quelli, dicendo quanto è '
                    'sottile la base. Il resto si riporta come "gli autori letti '
                    'sostengono che…", mai come pensiero suo.')
        # La decisione separata guarda i soli documenti: l'avviso «Apri la
        # risposta con LACUNA» delle domande personali la spingerebbe al NO.
        # Nella v4 gli avvisi vanno dopo documenti e domanda ({avvisi_str}),
        # così l'inizio resta uguale a quello della decisione.
        documenti = contesto
        testo_avvisi = '\n\n'.join(avvisi) + '\n\n' if avvisi else ''
        if '{avvisi_str}' in self.template:
            prompt = (self.template
                      .replace('{context_str}', documenti)
                      .replace('{query_str}', domanda)
                      .replace('{avvisi_str}', testo_avvisi))
        else:
            prompt = (self.template
                      .replace('{context_str}', testo_avvisi + contesto)
                      .replace('{query_str}', domanda))
        prefisso = (_prompt_chat.PREFISSO_V4
                    .replace('{context_str}', documenti)
                    .replace('{query_str}', domanda)) if self.decidi_prima else None

        nodi = [NodoCompat(f, numeri[f['path']]) for f in frammenti]
        if self.decidi_prima:
            t0 = time.time()
            modello_dec = self.modello or _modelli.prepara_motore(self.cfg, 'ask')
            uscita = _modelli.client().chat(
                model=modello_dec,
                messages=[{'role': 'user', 'content': prefisso + ISTRUZIONE_DECIDI}],
                format={'x-regex': REGEX_DECIDI},
                options={'temperature': 0.0, 'num_predict': 200,
                         'cache_prefisso': prefisso},
                keep_alive='30m', priorita=True)['message']['content'].strip()
            secondi = round(time.time() - t0, 2)
            if not uscita.startswith('SI'):
                testo = uscita.partition('\n')[2].strip() or 'LACUNA: i documenti non trattano la domanda.'
                risposta = RispostaCompat(iter([testo]), nodi)
                risposta._testo = testo
                risposta.decisione, risposta.secondi_decisione = False, secondi
                risposta.prompt, risposta.modello = prompt, modello_dec
                return risposta

        cliente = _modelli.client()
        modello = self.modello or _modelli.prepara_motore(self.cfg, 'ask')
        # priorita=True: L'utente sta guardando lo streaming in chat, non deve
        # aspettare che finisca un chunk di Archivista prima ancora di iniziare.
        flusso = cliente.chat(
            model=modello,
            messages=[{'role': 'user', 'content': prompt}],
            stream=True,
            # Finestra dimensionata sul prompt reale: il template ARCHIVIO da
            # solo pesa ~2000 caratteri e 5 frammenti possono arrivare a 9000.
            # Con num_ctx fisso a 4096 Ollama troncava in SILENZIO — e a essere
            # tagliata era la coda del prompt, cioè proprio la domanda. Il
            # demone MLX non tronca: num_ctx resta solo come promemoria della
            # dimensione reale del prompt.
            # 900 e non 600: col prompt che chiede completezza un terzo delle
            # risposte veniva tagliato a metà frase (giudizio del 17/11).
            options={'temperature': 0.3,
                     'num_ctx': _ask._num_ctx(prompt),
                     'num_predict': 900,
                     **({'cache_prefisso': prefisso} if prefisso else {})},
            keep_alive='30m', priorita=True,
            **({'format': {'x-regex': REGEX_DECISIONE}} if self.decisione else {}))

        risposta = RispostaCompat(None, nodi)
        # Il prompt esatto resta sulla risposta: serve ai voti della chat
        # (zeno_engine.valutazione) per un eventuale addestramento futuro.
        risposta.prompt = prompt
        risposta.modello = modello
        if self.decidi_prima:
            risposta.decisione, risposta.secondi_decisione = True, secondi

        def token_grezzi():
            for pezzo in flusso:
                token = pezzo.get('message', {}).get('content', '')
                if token:
                    yield token

        def genera():
            sorgente = token_grezzi()
            if self.decisione:
                # La riga SI/NO non si mostra: serve solo a decidere.
                testa = ''
                for token in sorgente:
                    testa += token
                    if '\n' in testa:
                        break
                riga, _, resto = testa.partition('\n')
                risposta.decisione = riga.strip().upper() == 'SI'
                sorgente = itertools.chain([resto] if resto else [], sorgente)
            righe, riga = [], ''
            for token in sorgente:
                risposta._testo += token
                yield token
                riga += token
                if '\n' in riga:
                    *finite, riga = riga.split('\n')
                    righe += [x.strip() for x in finite if x.strip()]
                    if _in_loop(righe):
                        flusso.close()      # chiude la connessione: il demone smette
                        break
        risposta.response_gen = genera()
        return risposta


# ── Shim per i simboli llama_index ancora citati nel codice V5 ───────────────
# Servono SOLO a far importare chat_ui.py senza llama-index installato:
# le classi decorative (StratoBalancer) restano definite ma inerti.

class PromptTemplate(str):
    """Il template V5 era un PromptTemplate; qui basta la stringa."""
    def __new__(cls, testo: str = ''):
        return super().__new__(cls, testo)


class BaseNodePostprocessor:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _Inerte:
    """Contenitore passivo: conserva i kwargs come attributi (node, score…)."""
    def __init__(self, *a, **k):
        for chiave, valore in k.items():
            setattr(self, chiave, valore)


class _Completamento(str):
    """llm.complete() V5 ritornava un oggetto stampabile: basta una str."""


class MotoreLocale:
    """
    Erede dello shim di llama_index.llms.ollama.Ollama: chat_ui lo usa in
    genera_nota_maieutica() con .complete(prompt). Instradato sul motore_ask
    (qwen, già residente dopo una sessione di chat: nessuno scambio in RAM).
    Il bug del 2026-07-14 ("_Inerte object has no attribute complete") nasceva
    da uno shim inerte qui: mai più silenzioso.
    """

    def __init__(self, model: str = '', request_timeout: float = 600, **_):
        self._cfg = _config.load()

    def complete(self, prompt: str) -> _Completamento:
        # priorita=True: usato per genera_nota_maieutica(), che l'utente aspetta
        # a schermo dopo un click, come lo streaming di EngineZeno.query().
        r = _modelli.client().chat(
            model=_modelli.prepara_motore(self._cfg, 'ask'),
            messages=[{'role': 'user', 'content': str(prompt)}],
            options={'temperature': 0.4, 'num_ctx': 8192, 'num_predict': 1200},
            keep_alive='30m', priorita=True)
        return _Completamento(r['message']['content'])


NodeWithScore = _Inerte
QueryBundle = _Inerte
