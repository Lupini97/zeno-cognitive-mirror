# scripts/chat_ui.py
import streamlit as st, sys, json, os, re, time, uuid, glob
from datetime import datetime, timedelta
from pathlib import Path
_zeno_s = __import__('os').path.dirname(__import__('os').path.abspath(__file__))
if _zeno_s not in __import__('sys').path: __import__('sys').path.insert(0, _zeno_s)
from zeno_engine.config import carica_config
from interfaccia.errors import verifica_sistema
from zeno_engine.profilo import carica_profilo, aggiorna_profilo_da_sessione
from interfaccia.update_checker import controlla_aggiornamenti
# Migrazione V6 (2026-07-14): il motore è zeno.db + zeno_engine, non più
# LlamaIndex/Qdrant. L'adapter replica il contratto del vecchio query engine
# (query → response_gen + source_nodes): la UI sotto NON cambia.
from zeno_engine import prompt_chat
from zeno_engine.adapter_v5 import (
    EngineZeno, PromptTemplate, MotoreLocale,            # attivi
    NodeWithScore, BaseNodePostprocessor, QueryBundle,   # tipi dei postprocessor
)
from typing import List, Optional, ClassVar
from ddgs import DDGS

# Protetto: quando chat_ui gira come pagina di zeno_app.py il page_config
# lo ha già fatto l'entry point; da sola (streamlit run chat_ui.py) resta valido.
try:
    st.set_page_config(page_title='Zeno', page_icon='Z', layout='wide')
except Exception:
    pass
# Colori e caratteri vivono in interfaccia/tema_zeno.py (variante in zeno_config.yaml,
# ui.tema): qui restano solo le regole di impaginazione. Le classi storiche
# .fonte/.lacuna/.avviso/.errore/.fase-* sono ridefinite lì, legate al tema
# chiaro o scuro — prima erano colori fissi pensati per un solo fondo.
st.markdown('''<style>
/* Tre colonne con scroll verticale indipendente */
[data-testid="stHorizontalBlock"] { align-items: flex-start !important; }
[data-testid="column"]:last-child {
    position: sticky;
    top: 60px;
    max-height: calc(100vh - 80px);
    overflow-y: auto;
    align-self: flex-start;
}
[data-testid="column"]:first-child {
    max-height: calc(100vh - 80px);
    overflow-y: auto;
}

/* Chat input fisso al fondo della colonna centrale */
.stChatInput { position: sticky; bottom: 0; z-index: 100;
  background: var(--background-color); padding: 8px 0; }

/* Forza font-size su lacuna/avviso/errore in qualsiasi contesto incluso st.chat_message */
[data-testid="stChatMessageContent"] .lacuna,
[data-testid="stChatMessageContent"] .avviso,
[data-testid="stChatMessageContent"] .errore,
.stChatMessage .lacuna,
.stChatMessage .avviso,
.stChatMessage .errore { font-size: 11px !important; line-height: 1.4 !important; }

/* Pannello di controllo: testo compatto uniforme */
[data-testid="column"]:last-child h3 { font-size: 13px !important; margin: 4px 0 !important; }
[data-testid="column"]:last-child .stAlert { font-size: 11px !important; padding: 6px 10px !important; }
[data-testid="column"]:last-child .stAlert p { font-size: 11px !important; margin: 0 !important; }
[data-testid="column"]:last-child .stSubheader { font-size: 12px !important; }
[data-testid="column"]:last-child p { font-size: 12px !important; }
[data-testid="column"]:last-child .stMarkdown p { font-size: 11px !important; }
[data-testid="column"]:last-child code { font-size: 10px !important; }
</style>''', unsafe_allow_html=True)

cfg = carica_config()
from interfaccia import tema_zeno
from zeno_engine.config import PYTHON, SCRIPTS, con_nome
tema_zeno.applica(cfg)

# ── Chiusura Zeno — va intercettata PRIMA di qualsiasi layout ────────────────
if st.session_state.get('chiudi_zeno_fase') == 'esegui':
    st.title('⏻ Chiusura Zeno')
    with st.status('Operazioni in corso...', expanded=True) as _sc:
        st.write('Scrittura snapshot finale...')
        try:
            import subprocess as _sp
            _sp.run([PYTHON,
                     '-m', 'zeno_engine', 'manutenzione', '--forza', 'snapshot'],
                    cwd=SCRIPTS,
                    capture_output=True, timeout=60)
            st.write('✓ Snapshot completato')
        except Exception as _e:
            st.write(f'⚠ Snapshot: {_e}')

        # ── Auto-salvataggio sessione (se non già salvata e ha contenuto) ──
        st.write('Verifica sessione corrente...')
        try:
            _history_close = st.session_state.get('history', [])
            if (not st.session_state.get('sessione_gia_salvata')
                    and len(_history_close) >= 2):
                _ts_auto = datetime.now().strftime('%Y%m%d_%H%M')
                _path_auto = os.path.join(
                    cfg['percorsi']['memoria'],
                    'query_archiviate', f'sessione_{_ts_auto}.json')
                with open(_path_auto, 'w', encoding='utf-8') as _f_auto:
                    json.dump(_history_close, _f_auto, ensure_ascii=False, indent=2)
                # Aggiorna anche i temi attivi
                try:
                    _scrivi_temi_attivi(cfg, _history_close)
                except Exception:
                    pass
                st.write(f'✓ Sessione auto-salvata ({len(_history_close)} messaggi)')
            else:
                st.write('✓ Sessione già salvata — nessuna duplicazione')
        except Exception as _e:
            st.write(f'⚠ Auto-salvataggio: {_e}')

        st.write('Scrittura contesto sessione...')
        try:
            _ts_c  = datetime.now().strftime('%Y%m%d_%H%M')
            _ctx_p = os.path.join(cfg['percorsi']['stats'], 'sessione_contesto.json')
            _rilevanti = [m for m in st.session_state.get('history', [])
                          if len(m.get('content', '')) > 20]
            with open(_ctx_p, 'w', encoding='utf-8') as _f:
                json.dump({'timestamp': _ts_c, 'scambi': _rilevanti},
                          _f, ensure_ascii=False, indent=2)
            st.write('✓ Contesto sessione scritto')
        except Exception as _e:
            st.write(f'⚠ Contesto: {_e}')

        st.write('Invio segnale di stop ai processi background...')
        try:
            _sent = os.path.join(cfg['percorsi']['stats'], '.zeno_stop')
            open(_sent, 'w').close()
            st.write('✓ Sentinel creato (watcher / mind / graph si fermeranno al prossimo ciclo)')
        except Exception as _e:
            st.write(f'⚠ Sentinel: {_e}')

        st.write('Arresto server...')
        _sc.update(label='✓ Zeno chiuso correttamente.', state='complete')

    st.success('A presto.')
    time.sleep(2)
    import subprocess as _sp2
    _sp2.Popen(['bash', '-c',
        'sleep 1 && '
        'lsof -ti tcp:8501 | xargs kill -9 2>/dev/null; '
        'lsof -ti tcp:8502 | xargs kill -9 2>/dev/null'])
    st.stop()

if st.session_state.get('chiudi_zeno_richiesto'):
    _mai = (st.session_state.get('modalita_corrente') == 'MAIEUTICA'
            and len(st.session_state.get('history', [])) > 0
            and not st.session_state.get('sessione_gia_salvata'))
    if _mai:
        st.title('⏻ Chiudi Zeno')
        st.warning('Sessione MAIEUTICA in corso con scambi non salvati.')
        _c1, _c2, _c3 = st.columns(3)
        if _c1.button('💾 Salva e chiudi'):
            _ts_s = datetime.now().strftime('%Y%m%d_%H%M')
            _pp   = f"{cfg['percorsi']['memoria']}/query_archiviate/sessione_{_ts_s}.json"
            with open(_pp, 'w') as _f:
                json.dump(st.session_state.get('history', []), _f,
                          ensure_ascii=False, indent=2)
            # Marca come salvata per evitare doppio salvataggio nell'esegui
            st.session_state['sessione_gia_salvata'] = True
            st.session_state['chiudi_zeno_fase'] = 'esegui'
            st.rerun()
        if _c2.button('🗑 Chiudi senza salvare'):
            # Marca come "gestita" per evitare auto-save nell'esegui
            st.session_state['sessione_gia_salvata'] = True
            st.session_state['chiudi_zeno_fase'] = 'esegui'
            st.rerun()
        if _c3.button('Annulla'):
            st.session_state.pop('chiudi_zeno_richiesto', None)
            st.rerun()
        st.stop()
    else:
        st.session_state['chiudi_zeno_fase'] = 'esegui'
        st.rerun()

# ── Profilo cognitivo MBTI (V4) — letto dinamicamente da zeno_config.yaml ─────
def _costruisci_profilo_mbti() -> str:
    """Profilo cognitivo dai valori attuali in config (ora in
    zeno_engine/prompt_chat.py, condiviso con la valutazione)."""
    return prompt_chat.profilo_mbti(cfg)

PROFILO_COGNITIVO_MBTI = _costruisci_profilo_mbti()

# ── Verifica sistema ──────────────────────────────────────────────────────────
problemi = verifica_sistema()
if problemi:
    st.error('Zeno non è operativo. Problemi:')
    for p in problemi: st.warning(p)
    st.info('Risolvi i problemi e riavvia con: zeno')
    st.stop()

profilo = carica_profilo()
aggiornamenti_disponibili = controlla_aggiornamenti()

# ── Helper: fonti cliccabili ─────────────────────────────────────────────────

def _fonti_link(source_nodes):
    """Restituisce (lista_nomi, dict nome→path) dai source_nodes, senza duplicati."""
    visti, nomi, paths = set(), [], {}
    for n in source_nodes:
        nome = n.metadata.get('file_name', '?')
        path = n.metadata.get('file_path', '')
        if nome not in visti:
            visti.add(nome)
            nomi.append(nome)
            paths[nome] = path
    return nomi, paths

def _url_fonte(path: str) -> str:
    """URL che apre la fonte: obsidian:// per i .md del vault, altrimenti
    l'interceptor ?apri= (il browser blocca file:// su localhost)."""
    from urllib.parse import quote
    vault = ((cfg.get('percorsi', {}) or {}).get('vault', '') or '').rstrip('/')
    if not path:
        return ''
    if path.endswith('.md') and vault and path.startswith(vault + '/'):
        return f"obsidian://open?path={quote(path, safe='/:()')}"
    return f"http://localhost:8501/?apri={quote(path, safe='/:()@')}"


def _fonti_dettaglio(source_nodes) -> list[dict]:
    """Una voce per DOCUMENTO, nell'ordine dei numeri [n] del prompt
    (adapter_v5): titolo, voce (strato), data, pertinenza, estratto."""
    per_numero: dict[int, dict] = {}
    for n in source_nodes:
        m = n.metadata or {}
        num = m.get('numero') or (len(per_numero) + 1)
        voce = per_numero.get(num)
        if voce is None:
            per_numero[num] = {
                'title': m.get('title') or m.get('file_name', '?'),
                'file': m.get('file_name', '?'), 'path': m.get('file_path', ''),
                'strato': m.get('strato'), 'conoscenza': m.get('conoscenza'),
                'data': m.get('created_at'), 'score': n.score or 0,
                'estratto': (getattr(n, 'testo', '') or '')[:320].strip(),
            }
        elif (n.score or 0) > voce['score']:
            voce['score'] = n.score
    return [per_numero[k] for k in sorted(per_numero)]


def _mostra_risposta(testo: str, fonti_dett: list | None, chiave: str) -> None:
    """Risposta con i riferimenti [n] colorati per voce, lacune in riquadro,
    e l'elenco numerato delle fonti sotto."""
    st.markdown(tema_zeno.html_risposta(testo, fonti_dett, chiave),
                unsafe_allow_html=True)
    if fonti_dett:
        st.markdown(tema_zeno.html_fonti(fonti_dett, chiave, link=_url_fonte),
                    unsafe_allow_html=True)


def _voto_risposta(i_msg: int) -> None:
    """Pollice su/giù sotto una risposta di Zeno; col giù, una correzione
    facoltativa. Tutto in stats/valutazione/voti.jsonl, sul Mac: è il metro
    della qualità (`zeno valuta voti`) e, col prompt esatto accanto, la
    materia prima di un eventuale addestramento sulle preferenze."""
    from zeno_engine import valutazione
    storia = st.session_state.history
    msg = storia[i_msg]
    mid = msg.setdefault('id', uuid.uuid4().hex[:12])
    chiave = f'voto_{mid}'

    def _salva(correzione=None):
        domanda = next((m['content'] for m in reversed(storia[:i_msg])
                        if m.get('role') == 'user'), '')
        try:
            valutazione.registra_voto({
                'id': mid, 'voto': st.session_state.get(chiave),
                'correzione': correzione, 'modalita': msg.get('modalita'),
                'modello': msg.get('modello'), 'domanda': domanda,
                'risposta': msg.get('content', ''),
                'fonti': [{'path': f.get('path'), 'title': f.get('title'),
                           'strato': f.get('strato')} for f in msg.get('fonti_dett') or []],
                'prompt': st.session_state.get('prompt_msg', {}).get(mid)})
        except Exception as e:
            st.toast(f'Voto non salvato: {e}')

    st.feedback('thumbs', key=chiave, on_change=_salva)
    if st.session_state.get(chiave) == 0:
        _corr = st.text_area('Come avrebbe dovuto rispondere? (facoltativo)',
                             key=f'corr_{mid}', height=80)
        if st.button('Salva correzione', key=f'corr_ok_{mid}') and _corr.strip():
            _salva(_corr.strip())
            st.toast('Correzione salvata')


def _render_fonti(nomi, paths=None):
    """Badge fonte uniformi: classe .fonte su ogni elemento, link o no.
    - .md dentro il vault Obsidian → obsidian://open?path=... (lì Obsidian lo trova)
    - tutto il resto, inclusi i .md di documents/ → ?apri=<path> via query param
      interceptor: il server fa `open <path>` = app di sistema (aggira il blocco
      file:// dei browser; obsidian:// falliva sui file fuori dal vault)
    - senza path → span non cliccabile, identico stile visivo
    La classe .fonte è applicata direttamente sull'ancora/span per evitare
    conflitti con lo stile browser dei link nidificati.
    """
    from urllib.parse import quote
    vault = ((cfg.get('percorsi', {}) or {}).get('vault', '') or '').rstrip('/')
    parti = []
    for nome in nomi:
        path = (paths or {}).get(nome, '')
        if path:
            if path.endswith('.md') and vault and path.startswith(vault + '/'):
                url = f"obsidian://open?path={quote(path, safe='/:()')}"
            else:
                url = f"http://localhost:8501/?apri={quote(path, safe='/:()@')}"
            parti.append(f'<a href="{url}" target="_blank" class="fonte">{nome}</a>')
        else:
            parti.append(f'<span class="fonte">{nome}</span>')
    return ' '.join(parti)

# ── Pannello processi background ──────────────────────────────────────────────

@st.cache_data(ttl=2)
def _leggi_processi_attivi(stats_dir: str) -> dict | None:
    """
    Legge stats/processi_attivi.json con cache 2 s (il frammento del pannello
    si ri-esegue ogni 3 s: una cache più lunga lo terrebbe fermo).
    Restituisce None se il file non esiste o è malformato.
    """
    try:
        p = os.path.join(stats_dir, 'processi_attivi.json')
        if not os.path.exists(p):
            return None
        with open(p, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None

VENV_ZENO   = PYTHON
SCRIPTS_DIR = SCRIPTS


def _config_periodici() -> dict:
    """Sezione `motore_epistemico.sync` (default nel codice se manca)."""
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from zeno_engine import config as _cfg_z
        return (_cfg_z.load().get('sync') or {})
    except Exception:
        return {}


def _job_dovuti_ora() -> list[str]:
    """Job scaduti in questo istante, letti dal registro unico."""
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from zeno_engine import config as _cfg_z, db as _db_z, periodici
        _c = _cfg_z.load()
        con = _db_z.connect(_c['db'])
        try:
            return periodici.dovuti(con, _c)
        finally:
            con.close()
    except Exception:
        return []


def _stato_batch() -> tuple[bool, bool]:
    """(manutenzione già in corso, batch LLM in corso).
    L'Archivista NON blocca il timer — può macinare per ore, e bloccarlo
    significherebbe non far girare nulla per tutto quel tempo. Blocca solo i
    job che userebbero il motore MLX: gli altri (snapshot, mind, graph, entita) sono
    locali e girano lo stesso."""
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from zeno_engine import processi
        return processi.in_corso('periodici'), processi.in_corso('archivista')
    except Exception:
        return False, False


def _timer_periodici() -> None:
    """
    I job del registro giravano SOLO a `zeno sync`, cioè all'avvio dell'app e
    quando il watcher rilevava file nuovi. Con Zeno aperto per giorni nessuna
    cadenza scattava: tutte insieme al riavvio successivo (utente, 2026-08-24:
    «sì lo preferisco» — che girino anche ad app aperta).

    Il timer vive qui e non nel watcher di proposito: `avvia_zeno.sh` non lancia
    il watcher (lo fa solo zeno_launcher.py), mentre questo frammento c'è per
    definizione ogni volta che l'app è aperta — che è esattamente la condizione
    richiesta.

    Tre cautele, tutte necessarie:
    - il lavoro NON gira qui dentro: si lancia `zeno manutenzione` staccato,
      altrimenti un job da 40 minuti bloccherebbe la UI Streamlit;
    - non parte se l'utente ha scritto da poco (`periodici_idle_minuti`): il
      batch si prende il motore MLX e le risposte in chat rallentano;
    - non parte se una passata (o l'Archivista) sta già girando — il lucchetto
      vero è in periodici.esegui_tutti, questo è solo il primo filtro.
    """
    conf = _config_periodici()
    if not conf.get('periodici_ad_app_aperta', True):
        return
    idle_min = float(conf.get('periodici_idle_minuti', 10))

    ultima = st.session_state.get('ultima_attivita')
    if ultima and (time.time() - ultima) < idle_min * 60:
        st.caption(f'🕒 Job periodici: in attesa di {idle_min:.0f} min di quiete.')
        return
    gia_in_corso, batch_llm = _stato_batch()
    if gia_in_corso:
        return

    dovuti = _job_dovuti_ora()
    if not dovuti:
        return

    comando = [VENV_ZENO, '-m', 'zeno_engine', 'manutenzione']
    if batch_llm:
        comando.append('--solo-locali')

    import subprocess as _sp_tim
    from zeno_engine import processi as _proc_tim
    try:
        _sp_tim.Popen(
            _proc_tim.con_caffeina(comando),
            cwd=SCRIPTS_DIR,
            stdout=open('/tmp/zeno_periodici.log', 'a'),
            stderr=_sp_tim.STDOUT,
            start_new_session=True)
        st.session_state['ultimo_avvio_periodici'] = datetime.now().strftime('%H:%M')
        st.caption(f"🕒 Avviati in background: {', '.join(dovuti)}"
                   + (' (solo i locali: un batch LLM sta girando)' if batch_llm else ''))
    except Exception as e:
        st.caption(f'🕒 Job periodici non avviati: {e}')


def _render_processi_sidebar(stats_dir: str) -> None:
    """Expander '🔄 Processi Zeno' nella sidebar."""
    processi = _leggi_processi_attivi(stats_dir)
    # Un job IN CORSO fa ri-eseguire solo questo frammento ogni 3 s: senza,
    # Streamlit ridisegna la sidebar unicamente quando l'utente clicca qualcosa,
    # e un batch da 40 minuti sembrava non essere mai partito.
    in_corso = any((d or {}).get('stato') == 'in_corso'
                   for d in (processi or {}).values())
    if in_corso:
        _pannello_processi_live(stats_dir)
    else:
        _pannello_processi(processi, espanso=False)
    _timer_periodici_live()


def _frammento_3s(fn):
    """st.fragment esiste da Streamlit 1.33; se manca, il pannello resta
    statico invece di far esplodere la sidebar."""
    return st.fragment(run_every='3s')(fn) if hasattr(st, 'fragment') else fn


@_frammento_3s
def _pannello_processi_live(stats_dir: str) -> None:
    _pannello_processi(_leggi_processi_attivi(stats_dir), espanso=True)


def _frammento_timer(fn):
    """Battito del timer dei job periodici. Lento per costruzione: le cadenze
    si misurano in giorni, il controllo costa la lettura di nove marcatori, e
    un intervallo corto servirebbe solo a moltiplicare i risvegli."""
    try:
        minuti = float(_config_periodici().get('periodici_check_minuti', 15))
    except Exception:
        minuti = 15.0
    if not hasattr(st, 'fragment'):
        return fn
    return st.fragment(run_every=f'{int(max(minuti, 1) * 60)}s')(fn)


@_frammento_timer
def _timer_periodici_live() -> None:
    _timer_periodici()


def _pannello_processi(processi, espanso: bool) -> None:
    EMOJI = {
        'attivo':     '🟢',
        'in_corso':   '🔵',
        'idle':       '⚪',
        'schedulato': '🟡',
        'completato': '✅',
        'errore':     '🔴',
    }
    LABEL = {
        'watcher':        'Watcher',
        'snapshot':       'Snapshot',
        'mind':           'Mind',
        'graph':          'Graph',
        'mbti':           'MBTI',
        'cross_linker':   'CrossLinker',
        'update_checker': 'UpdateChecker',
        'index':          'Index',
        'archivista':     'Archivista',
        'indicizza':      'Indicizzatore',
    }
    with st.expander('🔄 Processi Zeno', expanded=espanso):
        # Il `return` anticipato quando il file di stato mancava portava via con
        # sé anche il pulsante "Esegui adesso": niente watcher = niente comandi,
        # che è l'esatto momento in cui servono. Ora manca solo l'elenco.
        if not processi:
            st.caption('Nessun processo ha ancora scritto il proprio stato.')
        for nome, dati in (processi or {}).items():
            stato     = dati.get('stato', 'idle')
            emoji     = EMOJI.get(stato, '⚪')
            label     = LABEL.get(nome, nome)
            dettaglio = dati.get('dettaglio', '')
            ts        = (dati.get('ultimo_avvio') or
                         dati.get('ultimo_evento') or
                         dati.get('ultimo', ''))
            durata    = dati.get('durata_secondi')

            riga = f'{emoji} **{label}** — {stato}'
            if ts:
                riga += f'  \n<span style="font-size:10px;color:#888;">{ts}'
                if durata is not None:
                    riga += f' · {durata}s'
                riga += '</span>'
            if dettaglio:
                riga += f'  \n<span style="font-size:10px;color:#2E75B6;">{dettaglio}</span>'

            st.markdown(riga, unsafe_allow_html=True)

        _render_forza_job()


# ── Forzatura manuale dei job periodici ──────────────────────────────────────
# I job del registro (periodici.REGISTRO) girano da sé a ogni `zeno sync`, cioè
# a ogni avvio dell'app, ognuno con la sua cadenza. Questo blocco serve al caso
# opposto: farne ripartire uno SUBITO senza aspettare la scadenza né riavviare
# Zeno — è il pulsante che l'utente aveva nella finestra e che si era perso nella
# migrazione a V6.
#
# Sempre in background (Popen, mai run): `schede` da solo impiega ~40 minuti e
# un'esecuzione sincrona bloccherebbe l'intera UI Streamlit fino alla fine.



def _stato_job() -> list[dict]:
    """Nome, descrizione e scadenza dei job, letti dal registro unico."""
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from zeno_engine import periodici
        return [{'nome': j['nome'], 'desc': j.get('desc', '')}
                for j in periodici.REGISTRO]
    except Exception:
        return []


@st.cache_data(ttl=60)
def _scadenze_job() -> list[dict]:
    """
    Quando ha girato ogni job e quando è di nuovo dovuto. Risponde alla domanda
    che il pannello non sapeva rispondere — "le periodiche partono davvero?" —
    senza aprire un terminale per `zeno manutenzione --stato`.
    """
    try:
        sys.path.insert(0, SCRIPTS_DIR)
        from zeno_engine import config as _cfg_z, db as _db_z, periodici
        _c = _cfg_z.load()
        con = _db_z.connect(_c['db'])
        try:
            righe = periodici.stato(con, _c)
        finally:
            con.close()
        return [{'nome': r['nome'], 'giorni_fa': r['giorni_fa'],
                 'giorni': r['giorni'], 'dovuto': r['dovuto'],
                 'stantio': r['stantio']} for r in righe]
    except Exception:
        return []


def _render_forza_job() -> None:
    job = _stato_job()
    if not job:
        return
    st.markdown('---')

    # Cadenze: i job del registro girano a ogni `zeno sync`, cioè all'AVVIO
    # dell'app e quando il watcher vede cambiare un file — non su un orologio
    # mentre Zeno è aperto. Detto così sembra che "non partano con i tempi
    # previsti": in realtà partono al primo avvio utile dopo la scadenza.
    # Mostrarlo qui è la differenza fra un sospetto e un dato.
    scadenze = _scadenze_job()
    if scadenze:
        with st.popover('🕒 Cadenze', use_container_width=True):
            st.caption('I job girano all\'avvio di Zeno (e quando il watcher '
                       'rileva file nuovi), non a orologio.')
            for r in scadenze:
                if r['giorni_fa'] is None:
                    st.markdown(f"⚪ **{r['nome']}** — mai eseguito")
                    continue
                manca = r['giorni'] - r['giorni_fa']
                if r['stantio']:
                    icona, testo = '🔴', f"in ritardo di {-manca:.0f}g"
                elif r['dovuto']:
                    icona, testo = '🟡', 'dovuto al prossimo avvio'
                else:
                    icona, testo = '🟢', f'fra {manca:.0f}g'
                st.markdown(
                    f"{icona} **{r['nome']}** — {r['giorni_fa']:.0f}g fa "
                    f"(ogni {r['giorni']:.0f}g) · {testo}")

    st.caption('Forza un aggiornamento')

    scelta = st.selectbox(
        'Job', [j['nome'] for j in job],
        format_func=lambda n: next(
            (f"{j['nome']} — {j['desc']}" for j in job if j['nome'] == n), n),
        key='forza_job_scelta', label_visibility='collapsed')

    avviato = st.session_state.get('forza_job_avviato')
    if st.button('▶ Esegui adesso', key='forza_job_btn',
                 help='Parte in background: puoi continuare a usare la chat'):
        import subprocess as _sp_job          # il modulo non è importato in testa
        from zeno_engine import processi as _proc_job
        try:
            _proc = _sp_job.Popen(
                _proc_job.con_caffeina(
                    [VENV_ZENO, '-m', 'zeno_engine', 'manutenzione', '--forza', scelta]),
                cwd=SCRIPTS_DIR,
                    stdout=open(f'/tmp/zeno_job_{scelta}.log', 'w'),
                stderr=_sp_job.STDOUT,
                # Staccato dalla sessione di Streamlit: senza, chiudere l'app
                # a metà job lo porterebbe giù con sé.
                start_new_session=True)
            st.session_state['forza_job_avviato'] = scelta
            st.session_state['forza_job_pid'] = _proc.pid
            # Si conserva l'oggetto Popen, non solo il pid: un figlio finito e
            # mai atteso resta ZOMBIE, e `os.kill(pid, 0)` su uno zombie
            # RIESCE — il pannello avrebbe detto "in esecuzione" per sempre.
            # poll() lo raccoglie e restituisce il codice di uscita.
            st.session_state['forza_job_proc'] = _proc
            st.rerun()
        except Exception as e:
            st.error(f'Avvio fallito: {e}')

    if avviato:
        # Popen non fallisce se il comando muore un istante dopo: la conferma
        # va data sul processo VERO, non sull'intenzione di lanciarlo. Prima il
        # pannello diceva "avviato" e poi non cambiava mai, indistinguibile da
        # un job che non era mai partito.
        pid = st.session_state.get('forza_job_pid')
        proc = st.session_state.get('forza_job_proc')
        if proc is not None:
            vivo = proc.poll() is None
        else:
            # Nessun Popen sotto mano (sessione ripresa dopo un reload): si
            # ripiega sul pid. Qui uno zombie non può esserci: se il processo
            # non è più figlio di questo Streamlit, è già stato raccolto.
            try:
                os.kill(int(pid), 0)
                vivo = True
            except (OSError, TypeError, ValueError):
                vivo = False
        if vivo:
            st.caption(f'🔵 **{avviato}** in esecuzione (pid {pid}) — '
                       f'l\'avanzamento compare qui sopra ogni 3 s. '
                       f'Log: `/tmp/zeno_job_{avviato}.log`')
        else:
            _rc = proc.returncode if proc is not None else None
            _esito = '✅' if _rc in (0, None) else '🔴'
            st.caption(f'{_esito} **{avviato}** terminato'
                       + (f' (exit {_rc})' if _rc else '')
                       + f' — esito qui sopra, dettagli in '
                         f'`/tmp/zeno_job_{avviato}.log`')
            _coda = ''
            try:
                with open(f'/tmp/zeno_job_{avviato}.log', encoding='utf-8') as _fh:
                    _coda = ''.join(_fh.readlines()[-6:]).strip()
            except OSError:
                pass
            if _coda:
                st.code(_coda, language=None)


# ── Apertura file locale via query param (cross-browser) ─────────────────────
# Streamlit non espone endpoint REST, ma si può intercettare un query param
# all'avvio della pagina e chiamare subprocess.Popen(["open", path]).
_qp = st.query_params
if 'apri' in _qp:
    _path_apri = _qp['apri']
    if _path_apri and os.path.exists(_path_apri):
        import subprocess as _subp
        _subp.Popen(['open', _path_apri])
    st.query_params.clear()

# ── Web search ────────────────────────────────────────────────────────────────
def cerca_sul_web(query, max_risultati=3):
    try:
        with DDGS() as ddgs:
            risultati = list(ddgs.text(query, max_results=max_risultati))
        if not risultati:
            return [], 'Nessun risultato trovato.'
        testi = []
        for r in risultati:
            testi.append({
                'titolo': r.get('title', ''),
                'url': r.get('href', ''),
                'estratto': r.get('body', '')[:600]
            })
        return testi, None
    except Exception as e:
        return [], f'Errore ricerca web: {e}'

def estrai_query_da_lacuna(testo_lacuna, query_originale):
    m = re.search(r'LACUNA:\s*([^|\n]+)', testo_lacuna)
    if m:
        return m.group(1).strip()
    return query_originale

def importa_da_url(url):
    try:
        import requests
        from bs4 import BeautifulSoup
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; Zeno/4.0)'}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'header', 'footer',
                         'aside', 'form', 'iframe', 'noscript',
                         'figure', 'figcaption', 'advertisement']):
            tag.decompose()
        for tag in list(soup.find_all(True)):
            if tag.parent is None:
                continue
            cls = ' '.join(tag.get('class', []) or [])
            if any(kw in cls.lower() for kw in
                   ['ad', 'banner', 'cookie', 'popup', 'sidebar',
                    'promo', 'newsletter', 'social', 'share']):
                tag.decompose()
        titolo = soup.title.string.strip() if soup.title else url
        contenitore = soup.find('article') or soup.find('main') or soup.body
        if not contenitore:
            return None, 'Impossibile estrarre contenuto dalla pagina.'
        paragrafi = [p.get_text(' ', strip=True)
                     for p in contenitore.find_all(['p', 'h1', 'h2', 'h3', 'li'])
                     if len(p.get_text(strip=True)) > 40]
        if not paragrafi:
            return None, 'Nessun testo significativo trovato nella pagina.'
        testo = '\n\n'.join(paragrafi[:60])
        return titolo, testo
    except Exception as e:
        return None, f'Errore importazione: {e}'

def rimuovi_lacuna(i):
    lacune = st.session_state.get('lacune_sessione', [])
    if 0 <= i < len(lacune):
        lac = lacune[i]
        # Persisti il rifiuto in lacune_rifiutate.json
        # così mind.py non la riproporrà nei cicli successivi
        testo = lac.get('testo', lac) if isinstance(lac, dict) else str(lac)
        _persisti_lacuna_rifiutata(cfg, testo)
        # Rimuovila anche da lacune_aperte.json se presente
        _risolvi_lacuna_aperta(cfg, testo)
        lacune.pop(i)
    for key in [f'web_risultati_{i}', f'url_risultato_{i}']:
        st.session_state.pop(key, None)


# ── Feature 1: Persistenza lacune su file ────────────────────────────────────

def _normalizza_lacuna(testo):
    # Confronto per contenuto (zeno_engine/lacune.py), non per testo intero.
    from zeno_engine import lacune as _lacune
    return _lacune.chiave(testo) if (testo or '').strip() else ''

def _inferisci_area_da_fonti(fonti):
    if not fonti:
        return 'sconosciuta'
    fn = ' '.join(fonti).lower()
    for kw, area in [('libri', 'libri'), ('articoli', 'articoli_saggistica'),
                     ('riflessioni', 'riflessioni'), ('pubblicazioni', 'pubblicazioni'),
                     ('formazione', 'formazione'), ('sessione_', 'dialogico'),
                     ('tema_', 'inferito'), ('journal_', 'inferito')]:
        if kw in fn:
            return area
    return 'sconosciuta'

def _scrivi_lacuna_su_file(cfg, testo, query_originale, fonte, fonti_risposta=None):
    """
    Persiste una lacuna in stats/lacune_aperte.json.
    Idempotente: non duplica se esiste già lo stesso testo (normalizzato).
    giorni_aperti NON viene scritto: è calcolato al momento della lettura.
    Un errore di IO non blocca la risposta all'utente.
    """
    try:
        lacune_path = os.path.join(cfg['percorsi']['stats'], 'lacune_aperte.json')
        testo_norm  = _normalizza_lacuna(testo)
        from zeno_engine import lacune as _lacune
        if testo_norm in _lacune.chiavi_rifiutate(cfg['percorsi']['stats']):
            return  # eliminata o rifiutata dall'utente: non riproporla
        area        = _inferisci_area_da_fonti(fonti_risposta or [])
        lacune = []
        if os.path.exists(lacune_path):
            try:
                lacune = json.load(open(lacune_path, encoding='utf-8'))
            except Exception:
                lacune = []
        for entry in lacune:
            if _normalizza_lacuna(entry.get('testo', '')) == testo_norm:
                if entry.get('query_originale') != query_originale:
                    entry['query_originale'] = query_originale
                    with open(lacune_path, 'w', encoding='utf-8') as f:
                        json.dump(lacune, f, ensure_ascii=False, indent=2)
                return  # già presente: non duplicare
        lacune.append({
            'testo':                  testo,
            'query_originale':        query_originale,
            'data_prima_rilevazione': datetime.now().isoformat(),
            'fonte':                  fonte,
            'area':                   area,
        })
        with open(lacune_path, 'w', encoding='utf-8') as f:
            json.dump(lacune, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # errore IO non blocca la risposta


# ── Feature 2: Segnale temi attivi ───────────────────────────────────────────

def _scrivi_temi_attivi(cfg, history):
    """
    Scrive stats/sessione_temi_attivi.json con file citati e score medi.
    Merge con finestra mobile 30 giorni (entry più vecchie vengono scartate).
    """
    try:
        temi_path = os.path.join(cfg['percorsi']['stats'], 'sessione_temi_attivi.json')
        ts_now    = datetime.now().isoformat()
        cutoff    = (datetime.now() - timedelta(days=30)).isoformat()

        file_citati = list({f
                            for m in history
                            if m.get('role') == 'assistant'
                            for f in (m.get('fonti') or [])})
        scores_raw      = st.session_state.get('sessione_fonti_scores', {})
        score_medi_sess = {fn: sum(v) / len(v)
                           for fn, v in scores_raw.items() if v}

        existing = {}
        if os.path.exists(temi_path):
            try:
                existing = json.load(open(temi_path, encoding='utf-8'))
            except Exception:
                pass

        sessioni = [s for s in existing.get('sessioni', [])
                    if s.get('timestamp', '') >= cutoff]
        if file_citati:
            sessioni.append({'timestamp': ts_now,
                             'file_citati': file_citati,
                             'score_medi': score_medi_sess})

        tutti_files    = list({f for s in sessioni for f in s.get('file_citati', [])})
        score_medi_agg = {}
        for fn in tutti_files:
            valori = [s['score_medi'][fn] for s in sessioni
                      if fn in s.get('score_medi', {})]
            if valori:
                score_medi_agg[fn] = sum(valori) / len(valori)

        with open(temi_path, 'w', encoding='utf-8') as f:
            json.dump({'timestamp': ts_now,
                       'file_citati': tutti_files,
                       'score_medi': score_medi_agg,
                       'sessioni': sessioni},
                      f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── Lacune da dialogo: agenda da Gemello Statistico ─────────────────────────
def _carica_lacune_da_dialogo(cfg):
    """Legge lacune_da_dialogo.json e restituisce le voci non ancora affrontate."""
    path = os.path.join(cfg['percorsi']['stats'], 'lacune_da_dialogo.json')
    try:
        lista = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
        return [e for e in lista if not e.get('affrontata')]
    except Exception:
        return []

def _marca_lacuna_affrontata(cfg, lac_id):
    """Imposta affrontata=True sulla lacuna con quel id in lacune_da_dialogo.json."""
    if not lac_id:
        return
    path = os.path.join(cfg['percorsi']['stats'], 'lacune_da_dialogo.json')
    try:
        lista = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
        for e in lista:
            if e.get('id') == lac_id:
                e['affrontata'] = True
        with open(path, 'w', encoding='utf-8') as _fw:
            json.dump(lista, _fw, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _carica_lacune_agenda(cfg):
    """
    Unisce le lacune disponibili per la MAIEUTICA da due fonti:
    - lacune_da_dialogo.json: agenda programmata (eredità V5, può essere vuota)
    - lacune_aperte.json:     lacune vive del V6 (mind_scan, chat, tensioni)
    Dedup per testo normalizzato. Ogni voce riporta 'origine': 'dialogo'|'aperta',
    così i bottoni del widget sanno su quale file agire.
    """
    voci, visti = [], set()
    for e in _carica_lacune_da_dialogo(cfg):
        t = _normalizza_lacuna(e.get('testo', ''))
        if t and t not in visti:
            visti.add(t)
            voci.append({**e, 'origine': 'dialogo'})
    path_aperte = os.path.join(cfg['percorsi']['stats'], 'lacune_aperte.json')
    try:
        aperte = json.load(open(path_aperte, encoding='utf-8')) if os.path.exists(path_aperte) else []
    except Exception:
        aperte = []
    for e in aperte:
        t = _normalizza_lacuna(e.get('testo', ''))
        if t and t not in visti:
            visti.add(t)
            voci.append({'testo':      e.get('testo', ''),
                         'tema_fonte': e.get('tema_fonte', '') or e.get('area', ''),
                         'fonte':      e.get('fonte', ''),
                         'origine':    'aperta'})
    return voci


def _risolvi_lacuna_aperta(cfg, testo_lacuna):
    """
    Rimuove la lacuna con quel testo da lacune_aperte.json e la aggiunge
    a lacune_ignorate.json (usato dal tab Grafo cognitivo) in modo che
    scompaia immediatamente anche dal grafo senza dover rigenerare graph.py.
    Il match su lacune_aperte.json è normalizzato (lowercase + strip).
    """
    if not testo_lacuna:
        return
    stats_dir = cfg['percorsi']['stats']

    # 1. Rimuovi da lacune_aperte.json
    path_aperte = os.path.join(stats_dir, 'lacune_aperte.json')
    if os.path.exists(path_aperte):
        try:
            lacune   = json.load(open(path_aperte, encoding='utf-8'))
            target   = _normalizza_lacuna(testo_lacuna)   # anche le copie
            filtrate = [l for l in lacune
                        if _normalizza_lacuna(l.get('testo', '')) != target]
            if len(filtrate) < len(lacune):
                with open(path_aperte, 'w', encoding='utf-8') as _fw:
                    json.dump(filtrate, _fw, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # 2. Aggiungi l'id del grafo a lacune_ignorate.json
    # L'id e' costruito come graph.py: f'lacuna_{slug(testo.lower())}'
    try:
        _t = re.sub(r'[^\w\s-]', '', testo_lacuna.lower())
        _t = re.sub(r'\s+', '-', _t.strip())[:60]
        lac_nid  = f'lacuna_{_t}'
        path_ign = os.path.join(stats_dir, 'lacune_ignorate.json')
        ignorate = []
        if os.path.exists(path_ign):
            try:
                ignorate = json.load(open(path_ign, encoding='utf-8'))
            except Exception:
                ignorate = []
        if lac_nid not in ignorate:
            ignorate.append(lac_nid)
            with open(path_ign, 'w', encoding='utf-8') as _fw:
                json.dump(ignorate, _fw, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _persisti_lacuna_rifiutata(cfg, testo_lacuna):
    """
    Scrive la lacuna rifiutata in stats/lacune_rifiutate.json.
    mind.py legge questo file in scansiona_lacune() e non la ripropone mai più.
    Idempotente: non duplica se il testo è già presente (normalizzato).
    """
    if not testo_lacuna:
        return
    stats_dir = cfg['percorsi']['stats']
    path = os.path.join(stats_dir, 'lacune_rifiutate.json')
    testo_norm = _normalizza_lacuna(testo_lacuna)
    try:
        esistenti = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
        for e in esistenti:
            if _normalizza_lacuna(e.get('testo', '')) == testo_norm:
                return  # già presente
        esistenti.append({
            'testo':        testo_lacuna,
            'data_rifiuto': datetime.now().isoformat(),
            'motivo':       'ignorata_da_utente',
        })
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(esistenti, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _persisti_correzione_dialogo(cfg, prompt_utente, risposta_zeno, tipo_segnale):
    """
    Persiste in stats/correzioni.json i casi in cui l'utente segnala
    esplicitamente un errore di Zeno durante il dialogo.
    Struttura: testo del segnale, contesto (prompt + risposta), tipo e data.
    graph.py e mind.py non leggono ancora questo file, ma è la base per
    future iterazioni che insegnino a Zeno a non ripetere gli stessi errori.
    """
    stats_dir = cfg['percorsi']['stats']
    path = os.path.join(stats_dir, 'correzioni.json')
    try:
        esistenti = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
        esistenti.append({
            'data':           datetime.now().isoformat(),
            'tipo_segnale':   tipo_segnale,
            'prompt_utente':  prompt_utente[:500],
            'risposta_zeno':  risposta_zeno[:500],
        })
        # Mantieni le ultime 200 correzioni
        esistenti = esistenti[-200:]
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(esistenti, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# Pattern di segnale errore — rilevati in _rileva_segnale_errore()
_PATTERN_ERRORE = [
    r'\b(non c[\'e`è] entr[ao])\b',
    r'\b(hai (frainteso|sbagliato|capito male))\b',
    r'\b(non (è|e) (così|corretto|giusto|vero))\b',
    r'\b(stai (sbagliando|confondendo|facendo errori?))\b',
    r'\b(questo non (ha senso|torna|è quello che))\b',
    r'\b(no[,.]? (questo|quello|non è))\b',
    r'\bnon ti seguo\b',
    r'\bstorti sbagliato\b',
]
_RE_ERRORE = re.compile('|'.join(_PATTERN_ERRORE), re.IGNORECASE)

# Pattern connessione emergente — rilevati in _rileva_connessione_emergente()
_PATTERN_CONNESSIONE = [
    r'questo mi ricorda\s+(.{4,80})',
    r'mi fa pensare a\s+(.{4,80})',
    r'è simile a\s+(.{4,80})',
    r'come in\s+(.{4,80})',
    r'come (fa|dice|scrive|sosteneva?)\s+(.{4,80})',
    r'lo stesso (di|che)\s+(.{4,80})',
    r'collega(?:to)? (a|con)\s+(.{4,80})',
]
_RE_CONNESSIONE = re.compile('|'.join(_PATTERN_CONNESSIONE), re.IGNORECASE)


def _rileva_segnale_errore(prompt_utente):
    """
    Controlla se il messaggio dell'utente contiene un segnale esplicito
    di errore nella risposta precedente di Zeno.
    Restituisce (True, tipo) o (False, '').
    """
    if len(prompt_utente) < 5:
        return False, ''
    m = _RE_ERRORE.search(prompt_utente)
    if m:
        testo_match = m.group(0).lower()
        if 'frainteso' in testo_match or 'capito male' in testo_match:
            tipo = 'fraintendimento'
        elif 'non c' in testo_match and 'entr' in testo_match:
            tipo = 'connessione_errata'
        else:
            tipo = 'errore_generico'
        return True, tipo
    return False, ''


def _rileva_connessione_emergente(prompt_utente):
    """
    Rileva se l'utente sta esprimendo una connessione tra concetti
    ("questo mi ricorda X", "è simile a Y", "come in Z").
    Restituisce (True, testo_connessione) o (False, '').
    """
    if len(prompt_utente) < 10:
        return False, ''
    m = _RE_CONNESSIONE.search(prompt_utente)
    if m:
        # Prende il primo gruppo non-None (i pattern usano gruppi opzionali)
        testo = next((g for g in m.groups() if g), '').strip()
        # Rimuove eventuale punteggiatura finale
        testo = re.sub(r'[.,;:!?]+$', '', testo).strip()
        if len(testo) > 3:
            return True, testo
    return False, ''


def _proponi_connessione_emergente(cfg, testo_connessione, prompt_utente):
    """
    Scrive in stats/connessioni_emerse.json una connessione rilevata
    nel dialogo. graph.py potrà leggerla come segnale per archi dialogici.
    Restituisce True se la connessione è stata persistita.
    """
    stats_dir = cfg['percorsi']['stats']
    path = os.path.join(stats_dir, 'connessioni_emerse.json')
    try:
        esistenti = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else []
        # Evita duplicati sulla stessa connessione (normalizzato)
        testo_norm = re.sub(r'\s+', ' ', testo_connessione.lower()).strip()
        for e in esistenti:
            if re.sub(r'\s+', ' ', e.get('testo', '').lower()).strip() == testo_norm:
                return False
        esistenti.append({
            'data':           datetime.now().isoformat(),
            'testo':          testo_connessione,
            'contesto_query': prompt_utente[:300],
            'confermata':     False,  # attende conferma dall'utente
        })
        esistenti = esistenti[-300:]
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(esistenti, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _leggi_scheda_tema_per_chat(tema_fonte, cfg):
    """
    Legge la scheda tema corrispondente a tema_fonte dal filesystem.
    Restituisce le sezioni principali come dizionario.
    Usata per arricchire il punto_partenza in MAIEUTICA prima che la sessione inizi.
    """
    if not tema_fonte:
        return {}
    def _slug(t):
        t = re.sub(r'[^\w\s-]', '', t.lower())
        return re.sub(r'[\s]+', '-', t.strip())[:60]
    temi_dir = os.path.join(cfg['percorsi']['memoria'], 'profilo', 'temi')
    fp = os.path.join(temi_dir, f'tema_{_slug(tema_fonte)}.md')
    if not os.path.exists(fp):
        return {}
    try:
        testo = open(fp, encoding='utf-8').read()
        corpo = testo
        if testo.startswith('---'):
            fine_fm = testo.find('---', 3)
            if fine_fm > 0:
                corpo = testo[fine_fm + 3:].strip()
        sezioni = {}
        for nome, pat in [
            ('cosa_sa',    r'(?:##?\s*|\d+\.\s*)COSA SA[^\n]*\n(.*?)(?=\n##|\n\d+\.|-{3,}|\Z)'),
            ('come_pensa', r'(?:##?\s*|\d+\.\s*)COME LA PENSA[^\n]*\n(.*?)(?=\n##|\n\d+\.|-{3,}|\Z)'),
            ('tensioni',   r'(?:##?\s*|\d+\.\s*)TENSIONI[^\n]*\n(.*?)(?=\n##|\n\d+\.|-{3,}|\Z)'),
            ('evoluzione', r'(?:##?\s*|\d+\.\s*)EVOLUZIONE[^\n]*\n(.*?)(?=\n##|\n\d+\.|-{3,}|\Z)'),
        ]:
            m = re.search(pat, corpo, re.DOTALL | re.IGNORECASE)
            if m:
                sezioni[nome] = m.group(1).strip()
        return sezioni
    except Exception:
        return {}


# ── Scrittura diretta su file .md ─────────────────────────────────────────────

def _normalizza_titolo_hint(raw: str, trigger_pattern: str) -> str:
    """
    Pulisce il titolo estratto da un prompt di comando:
    1. Rimuove il trigger verbale (es. 'aggiungi citazione', 'recensione su')
    2. Rimuove qualsiasi tipo di virgoletta/caporale
    3. Rimuove l'attribuzione autore finale (es. 'di Calvino', 'by Hemingway')
    """
    hint = re.sub(trigger_pattern, '', raw, flags=re.I).strip()
    hint = re.sub(r'["""\'«»]', '', hint)
    hint = re.sub(r'\s+(?:di|by)\s+\w[\w\s]{0,25}$', '', hint, flags=re.I).strip()
    return hint

def _cerca_file_libro(hint: str, libri_path: str) -> list:
    """
    Cerca un file .md nella libreria con strategia a due livelli:
    1. Match sull'hint completo
    2. Fallback: match parola per parola (>3 caratteri), ordine originale
    """
    hint_l    = hint.lower()
    candidati = [f for f in os.listdir(libri_path)
                 if f.endswith('.md') and hint_l in f.lower()]
    if not candidati:
        for parola in [w for w in hint_l.split() if len(w) > 3]:
            # parola INTERA (\b): "atti" non deve agganciare "attività"
            rx = re.compile(r'\b' + re.escape(parola) + r'\b')
            candidati = [f for f in os.listdir(libri_path)
                         if f.endswith('.md') and rx.search(f.lower())]
            if candidati:
                break
    if not candidati:
        # 3. Fallback fuzzy (V6): tollera refusi ("vegetarianna", "zaratustra").
        #    Confronto sul TITOLO (prima di " - Autore"), non sul nome file.
        import difflib
        punteggi = []
        for f in os.listdir(libri_path):
            if not f.endswith('.md') or f.startswith('._'):
                continue
            titolo = f[:-3].split(' - ')[0].lower()
            r = difflib.SequenceMatcher(None, hint_l, titolo).ratio()
            if r >= 0.6:
                punteggi.append((r, f))
        candidati = [f for _, f in sorted(punteggi, reverse=True)[:3]]
    return candidati

_PLACEHOLDER_SINOSSI = [
    '_Da compilare con enrich_books.py_',
    '_Da compilare con enrich_books_web.py_',
    '_⚠ Ricerca web non ha trovato risultati pertinenti.',
]

def scrivi_sinossi_su_libro(titolo_hint, testo_sinossi, libri_path):
    """Aggiorna la sezione Sinossi di una scheda libro."""
    candidati = _cerca_file_libro(titolo_hint, libri_path)
    if not candidati:
        return f'Nessun libro trovato con "{titolo_hint}" nel titolo.'
    filepath = os.path.join(libri_path, candidati[0])
    content  = open(filepath, encoding='utf-8').read()
    ts       = datetime.now().strftime('%Y-%m-%d')
    testo_md = f'_{testo_sinossi}_\n\n*Fonte: integrazione web — {ts}*'
    # Sostituisce qualsiasi placeholder sinossi
    sostituito = False
    for ph in _PLACEHOLDER_SINOSSI:
        if ph in content:
            content = content.replace(ph, testo_md, 1)
            sostituito = True
            break
    if not sostituito:
        if '## Sinossi' in content:
            idx = content.find('## Sinossi') + len('## Sinossi')
            content = content[:idx] + '\n' + testo_md + '\n' + content[idx:]
        else:
            content += f'\n## Sinossi\n{testo_md}\n'
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return f'Sinossi aggiornata in "{candidati[0][:-3]}".'


def scrivi_autore_su_libro(titolo_hint, testo_autore, libri_path):
    """Aggiorna/aggiunge la sezione Autore di una scheda libro."""
    candidati = _cerca_file_libro(titolo_hint, libri_path)
    if not candidati:
        return f'Nessun libro trovato con "{titolo_hint}" nel titolo.'
    filepath = os.path.join(libri_path, candidati[0])
    content  = open(filepath, encoding='utf-8').read()
    ts       = datetime.now().strftime('%Y-%m-%d')
    sezione  = f'## Autore\n_{testo_autore}_\n\n*Fonte: integrazione web — {ts}*\n\n'
    if '## Autore' in content:
        # Sostituisce la sezione esistente
        content = re.sub(
            r'## Autore\n.*?(?=\n## |\Z)',
            sezione,
            content, count=1, flags=re.DOTALL
        )
    elif '## Note personali' in content:
        content = content.replace('## Note personali', sezione + '## Note personali')
    else:
        content += '\n' + sezione
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    return f'Sezione Autore aggiornata in "{candidati[0][:-3]}".'


def scrivi_citazione_su_libro(titolo_hint, testo_citazione, libri_path):
    candidati = _cerca_file_libro(titolo_hint, libri_path)
    if not candidati:
        return f'Nessun libro trovato con "{titolo_hint}" nel titolo.'
    filepath = os.path.join(libri_path, candidati[0])
    content  = open(filepath, encoding='utf-8').read()
    nuova    = f'\n> {testo_citazione}\n'
    if '## Citazioni' in content:
        content = content.replace('> _prima citazione_', '')
        idx = content.find('## Citazioni') + len('## Citazioni')
        content = content[:idx] + nuova + content[idx:]
    else:
        content += f'\n## Citazioni\n{nuova}'
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    # ── Specchio in /riflessioni/libri/ — indicizzata come esplicito ──────────
    # Le citazioni scelte dall'utente sono pensiero esplicito: ha selezionato
    # quella frase tra migliaia. Devono essere recuperabili con peso pieno.
    try:
        ts       = datetime.now().strftime('%Y-%m-%d_%H%M')
        rifl_dir = os.path.join(cfg['percorsi']['documents'], 'riflessioni', 'libri')
        os.makedirs(rifl_dir, exist_ok=True)
        slug     = re.sub(r'[^\w]', '_', titolo_hint.lower())[:40]
        mirror   = os.path.join(rifl_dir, f'cit_{slug}_{ts}.md')
        titolo_libro = candidati[0][:-3]
        with open(mirror, 'w', encoding='utf-8') as f:
            f.write(
                f'---\n'
                f'tipo: citazione_libro\n'
                f'libro: "{titolo_libro}"\n'
                f'data: {ts[:10]}\n'
                f'strato: esplicito\n'
                f'attribuzione: citazione_scelta\n'
                f'---\n\n'
                f'# Citazione da {titolo_libro}\n\n'
                f'> {testo_citazione}\n'
            )
    except Exception:
        pass
    return f'Citazione aggiunta a "{candidati[0][:-3]}".'

def scrivi_riflessione_su_libro(titolo_hint, testo, libri_path):
    """SUPERATA dal 2026-08-11: riflessione e recensione su un libro sono la
    stessa cosa e finiscono entrambe in `## Recensione`. Non delega a un corpo
    morto — inoltra a scrivi_recensione_su_libro, così se un percorso vecchio la
    richiamasse scriverebbe comunque nel posto giusto e con lo strato giusto."""
    return scrivi_recensione_su_libro(titolo_hint, testo, libri_path)


def _scrivi_riflessione_su_libro_storica(titolo_hint, testo, libri_path):
    candidati = _cerca_file_libro(titolo_hint, libri_path)
    if not candidati:
        return f'Nessun libro trovato con "{titolo_hint}".'
    filepath = os.path.join(libri_path, candidati[0])
    content  = open(filepath, encoding='utf-8').read()
    ts       = datetime.now().strftime('%Y-%m-%d')
    nuova    = f'\n**{ts}** — {testo}\n'
    if '## Riflessioni' in content:
        content = content.replace('_Da compilare dopo la lettura. Cosa mi ha lasciato questo libro?_', '')
        idx = content.find('## Riflessioni') + len('## Riflessioni')
        content = content[:idx] + nuova + content[idx:]
    else:
        content += f'\n## Riflessioni\n{nuova}'
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    # ── Specchio in /riflessioni/libri/ — indicizzata come esplicito ──────────
    try:
        ts_full  = datetime.now().strftime('%Y-%m-%d_%H%M')
        rifl_dir = os.path.join(cfg['percorsi']['documents'], 'riflessioni', 'libri')
        os.makedirs(rifl_dir, exist_ok=True)
        slug     = re.sub(r'[^\w]', '_', titolo_hint.lower())[:40]
        mirror   = os.path.join(rifl_dir, f'rifl_{slug}_{ts_full}.md')
        titolo_libro = candidati[0][:-3]
        with open(mirror, 'w', encoding='utf-8') as f:
            f.write(
                f'---\n'
                f'tipo: riflessione_libro\n'
                f'libro: "{titolo_libro}"\n'
                f'data: {ts}\n'
                f'strato: esplicito\n'
                f'attribuzione: tuo_pensiero_diretto\n'
                f'---\n\n'
                f'# Riflessione su {titolo_libro}\n\n'
                f'{testo}\n'
            )
    except Exception:
        pass
    return f'Riflessione aggiunta a "{candidati[0][:-3]}".'

def scrivi_recensione_su_libro(titolo_hint, testo, libri_path):
    candidati = _cerca_file_libro(titolo_hint, libri_path)
    if not candidati:
        return f'Nessun libro trovato con "{titolo_hint}".'
    filepath = os.path.join(libri_path, candidati[0])
    content  = open(filepath, encoding='utf-8').read()
    ts       = datetime.now().strftime('%Y-%m-%d')
    nuova    = f'\n**{ts}** — {testo}\n'
    if '## Recensione' in content:
        for _vuoto in ('_Da compilare dopo la lettura._', '_Nessuna recensione_'):
            content = content.replace(_vuoto, '')
        idx = content.find('## Recensione') + len('## Recensione')
        content = content[:idx] + nuova + content[idx:]
    else:
        content += f'\n## Recensione\n{nuova}'
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

    # ── Specchio in /riflessioni/libri/ — indicizzato come esplicito ──────────
    # La scheda libro è strato `acquisito`: è la voce dell'AUTORE letto. Una
    # recensione scritta lì dentro veniva quindi pesata come materiale acquisito
    # e, peggio, restava fuori dalla maieutica — coppie_candidate confronta solo
    # asserzioni esplicito/dialogico. Le riflessioni avevano già questo specchio,
    # le recensioni no: la stessa cosa scritta dall'utente valeva in due modi
    # diversi a seconda della voce di menù scelta. L'utente, 2026-08-11: «sono le
    # riflessioni con valutazioni tutte e due […] il peso è quello di pensiero
    # mio personale quindi alto».
    try:
        ts_full  = datetime.now().strftime('%Y-%m-%d_%H%M')
        rifl_dir = os.path.join(cfg['percorsi']['documents'], 'riflessioni', 'libri')
        os.makedirs(rifl_dir, exist_ok=True)
        slug     = re.sub(r'[^\w]', '_', titolo_hint.lower())[:40]
        mirror   = os.path.join(rifl_dir, f'rec_{slug}_{ts_full}.md')
        titolo_libro = candidati[0][:-3]
        with open(mirror, 'w', encoding='utf-8') as f:
            f.write(
                f'---\n'
                f'tipo: recensione_libro\n'
                f'libro: "{titolo_libro}"\n'
                f'data: {ts}\n'
                f'strato: esplicito\n'
                f'attribuzione: tuo_pensiero_diretto\n'
                f'---\n\n'
                f'# Recensione di {titolo_libro}\n\n'
                f'{testo}\n'
            )
    except Exception:
        pass
    return f'Recensione aggiunta a "{candidati[0][:-3]}".'

def salva_riflessione_generale(testo):
    ts = datetime.now().strftime('%Y-%m-%d_%H%M')
    riflessioni_dir = cfg['percorsi']['documents'] + '/riflessioni'
    os.makedirs(riflessioni_dir, exist_ok=True)
    filepath = os.path.join(riflessioni_dir, f'riflessione_{ts}.md')
    md = (f'---\ntipo: riflessione\ndata: {ts}\n'
          f'attribuzione: tuo_pensiero_abbozzato\n---\n\n{testo}\n')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(md)
    return f'Riflessione salvata in documents/riflessioni/riflessione_{ts}.md'

def interpreta_comando(prompt, libri_path):
    p = prompt.lower()

    # ── Citazione ──────────────────────────────────────────────────────────
    cit_triggers = ['aggiungi citazione', 'segna questa frase', 'questa frase per',
                    'aggiungi questa citazione', 'cita per', 'segna frase']
    if any(t in p for t in cit_triggers):
        parti = re.split(r'[:—]', prompt, maxsplit=1)
        if len(parti) == 2:
            testo_cit   = parti[1].strip()
            titolo_hint = _normalizza_titolo_hint(
                parti[0],
                r'aggiungi citazione|segna (questa )?frase|questa frase per|'
                r'aggiungi questa citazione|cita per')
            if titolo_hint and testo_cit:
                return 'citazione', scrivi_citazione_su_libro(titolo_hint, testo_cit, libri_path)

    # ── Recensione ─────────────────────────────────────────────────────────
    rec_triggers = ['recensione su', 'aggiungi recensione', 'scrivi recensione',
                    'aggiungi sintesi', 'sintesi su', 'aggiungi valutazione',
                    'valutazione su', 'recensione per', 'mia recensione']
    if any(t in p for t in rec_triggers):
        parti = re.split(r'[:—]', prompt, maxsplit=1)
        if len(parti) == 2:
            testo       = parti[1].strip()
            titolo_hint = _normalizza_titolo_hint(
                parti[0],
                r'aggiungi recensione|scrivi recensione|recensione (su|per)|'
                r'aggiungi sintesi|sintesi su|aggiungi valutazione|valutazione su|mia recensione')
            if titolo_hint and testo:
                return 'recensione_libro', scrivi_recensione_su_libro(titolo_hint, testo, libri_path)

    # ── Riflessione su libro → confluisce in Recensione ─────────────────────
    # Categoria unificata (utente, 2026-08-11): recensione e riflessione su un
    # libro sono la stessa cosa — un suo giudizio motivato — e tenerle separate
    # faceva solo sì che finissero in sezioni diverse e con peso diverso. I
    # trigger restano tutti attivi per non spezzare le frasi che l'utente già usa,
    # ma scrivono tutti nella sezione Recensione, con lo specchio esplicito.
    rifl_libro = ['riflessione su', 'considerazione su', 'riflessione per', 'nota su',
                  'aggiungi riflessione', 'mia riflessione su', 'pensiero su']
    if any(t in p for t in rifl_libro):
        parti = re.split(r'[:—]', prompt, maxsplit=1)
        if len(parti) == 2:
            testo       = parti[1].strip()
            titolo_hint = _normalizza_titolo_hint(
                parti[0],
                r'riflessione (su|per)|considerazione su|nota su|'
                r'aggiungi riflessione|mia riflessione su|pensiero su')
            if titolo_hint and testo:
                return 'recensione_libro', scrivi_recensione_su_libro(titolo_hint, testo, libri_path)

    # ── Riflessione generale ────────────────────────────────────────────────
    rifl_gen = [r'salva (come )?( mia )?riflessione', r'annota (questo )?pensiero',
                r'tieni questo pensiero', r'metti tra (le mie )?riflessioni',
                r'voglio tenere questo', r'annotalo']
    if any(re.search(t, p) for t in rifl_gen):
        parti = re.split(r'[:—]', prompt, maxsplit=1)
        testo = parti[1].strip() if len(parti) == 2 else prompt
        return 'riflessione_generale', salva_riflessione_generale(testo)

    # ── Sinossi su libro ────────────────────────────────────────────────────
    sin_triggers = ['aggiungi sinossi', 'salva sinossi', 'aggiorna sinossi',
                    'scrivi sinossi', 'aggiungi trama']
    if any(t in p for t in sin_triggers):
        parti = re.split(r'[:—]', prompt, maxsplit=1)
        if len(parti) == 2:
            testo       = parti[1].strip()
            titolo_hint = _normalizza_titolo_hint(
                parti[0],
                r'aggiungi sinossi|salva sinossi|aggiorna sinossi|'
                r'scrivi sinossi|aggiungi trama')
            if titolo_hint and testo:
                return 'sinossi_libro', scrivi_sinossi_su_libro(
                    titolo_hint, testo, libri_path)

    # ── Autore su libro ─────────────────────────────────────────────────────
    aut_triggers = ['aggiungi autore', 'aggiorna autore', 'salva autore',
                    'aggiungi biografia', 'aggiorna biografia']
    if any(t in p for t in aut_triggers):
        parti = re.split(r'[:—]', prompt, maxsplit=1)
        if len(parti) == 2:
            testo       = parti[1].strip()
            titolo_hint = _normalizza_titolo_hint(
                parti[0],
                r'aggiungi autore|aggiorna autore|salva autore|'
                r'aggiungi biografia|aggiorna biografia')
            if titolo_hint and testo:
                return 'autore_libro', scrivi_autore_su_libro(
                    titolo_hint, testo, libri_path)

    return None, None


# ════════════════════════════════════════════════════════════════════════════
# ── MAIEUTICA L0-L3 — Funzioni core ────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════

def carica_agenda_maieutica() -> dict:
    """
    L0 — Costruisce l'agenda interna della sessione MAIEUTICA.
    Legge: lacune_aperte.json, grafo_connessioni.json, schede tema.
    Non viene mai mostrata all'utente.
    """
    stats_dir    = cfg['percorsi']['stats']
    memoria_dir  = cfg['percorsi']['memoria']
    lacune_cfg   = cfg.get('lacune', {})
    max_lacune   = lacune_cfg.get('max_lacune_agenda_maieutica', 5)

    agenda = {
        'lacune':          [],
        'archi_dialogici': [],
        'tensioni_temi':   [],
        'punto_partenza':  None,
        'trattati':        []
    }

    # 1. Lacune aperte ordinate per anzianità (le più vecchie prima)
    lacune_path = os.path.join(stats_dir, 'lacune_aperte.json')
    if os.path.exists(lacune_path):
        try:
            lacune_raw = json.load(open(lacune_path, encoding='utf-8'))
            if isinstance(lacune_raw, list):
                lacune_raw.sort(key=lambda x: (
                    0 if x.get('da_dialogo') else 1,
                    x.get('data_prima_rilevazione', '')
                ))
                agenda['lacune'] = lacune_raw[:max_lacune]
        except Exception:
            pass

    # 2. Archi dialogici non confermati
    grafo_path = os.path.join(stats_dir, 'grafo_connessioni.json')
    conf_path  = os.path.join(stats_dir, 'archi_confermati.json')
    rif_path   = os.path.join(stats_dir, 'archi_rifiutati.json')
    if os.path.exists(grafo_path):
        try:
            grafo      = json.load(open(grafo_path, encoding='utf-8'))
            confermati = json.load(open(conf_path)) if os.path.exists(conf_path) else []
            rifiutati  = json.load(open(rif_path))  if os.path.exists(rif_path)  else []
            conf_keys  = {tuple(sorted([a['source'], a['target']])) for a in confermati}
            rif_keys   = {tuple(sorted([a['source'], a['target']])) for a in rifiutati}
            nodi       = grafo.get('nodi', {})
            archi_dialogici = [
                {
                    **a,
                    'source_label': nodi.get(a['source'], {}).get('label', a['source']),
                    'target_label': nodi.get(a['target'], {}).get('label', a['target'])
                }
                for a in grafo.get('archi', [])
                if a.get('tipo') == 'dialogico'
                and not a.get('confermato')
                and tuple(sorted([a['source'], a['target']])) not in conf_keys
                and tuple(sorted([a['source'], a['target']])) not in rif_keys
            ]
            agenda['archi_dialogici'] = archi_dialogici[:4]
        except Exception:
            pass

    # 3. Tensioni irrisolte dalle schede tema
    temi_dir = os.path.join(memoria_dir, 'profilo', 'temi')
    if os.path.exists(temi_dir):
        try:
            for fname in sorted(os.listdir(temi_dir))[:10]:
                if not fname.endswith('.md'):
                    continue
                testo = open(os.path.join(temi_dir, fname), encoding='utf-8').read()
                pattern = r'(?:##\s*(?:Tensioni|Contraddizioni|Domande aperte|Nodi irrisolti)[^\n]*\n)(.*?)(?=\n##|\Z)'
                m = re.search(pattern, testo, re.DOTALL | re.IGNORECASE)
                if m:
                    tensione_raw = m.group(1).strip()
                    if len(tensione_raw) > 20:
                        tema_m = re.search(r'tema:\s*(.+)', testo)
                        tema   = tema_m.group(1).strip() if tema_m else fname.replace('.md', '')
                        agenda['tensioni_temi'].append({
                            'tema':     tema,
                            'tensione': tensione_raw[:300],
                            'file':     fname
                        })
        except Exception:
            pass

    # Sceglie il punto di partenza
    if agenda['lacune']:
        l = agenda['lacune'][0]
        agenda['punto_partenza'] = {
            'tipo':   'lacuna',
            'testo':  l.get('testo', l.get('argomento', '')),
            'motivo': 'lacuna aperta più urgente per anzianità'
        }
    elif agenda['archi_dialogici']:
        a = agenda['archi_dialogici'][0]
        agenda['punto_partenza'] = {
            'tipo':   'arco_dialogico',
            'testo':  f"{a['source_label']} ↔ {a['target_label']}: {a.get('motivo', '')}",
            'motivo': 'connessione inferita in attesa di conferma'
        }
    elif agenda['tensioni_temi']:
        t = agenda['tensioni_temi'][0]
        agenda['punto_partenza'] = {
            'tipo':   'tensione_tema',
            'testo':  f"[{t['tema']}] {t['tensione'][:150]}",
            'motivo': 'tensione irrisolta dal profilo tematico'
        }

    return agenda


def inizializza_sessione_maieutica():
    """
    L1 — Inizializza lo stato interno della sessione MAIEUTICA.
    Chiamata una sola volta all'apertura della sessione.
    """
    if st.session_state.get('maieutica_inizializzata'):
        return

    agenda  = carica_agenda_maieutica()

    st.session_state.update({
        'maieutica_inizializzata':  True,
        'maieutica_sessione_id':    datetime.now().strftime('%Y%m%d_%H%M') + '_' + uuid.uuid4().hex[:6],
        'maieutica_agenda':         agenda,
        'maieutica_fase':           'apertura',
        'maieutica_fasi_attraversate': {'apertura'},
        'maieutica_domande_count':  0,
        'maieutica_inizio_ts':      time.time(),
        'maieutica_evoluzioni':     [],
        'maieutica_tensioni':       [],
        'maieutica_fonti_sessione': [],
        'maieutica_archi_sessione': [],
        'mostra_popup_chiusura':    False,
        'trigger_chiusura_tipo':    None,
        'sessione_matura':          False,
        'nota_generata':            None,
        'sessione_gia_salvata':     False,
    })


def aggiorna_fase_maieutica():
    """
    L1 — Transizione di fase basata sul conteggio scambi e contenuto.
    apertura → approfondimento dopo 2 scambi utente (o EVOLUZIONE: rilevata).
    approfondimento → consolidamento dopo 5 scambi totali o 15 minuti.
    """
    n   = st.session_state.get('maieutica_domande_count', 0)
    ela = (time.time() - st.session_state.get('maieutica_inizio_ts', time.time())) / 60
    fase_attuale      = st.session_state.get('maieutica_fase', 'apertura')
    fasi_attraversate = st.session_state.get('maieutica_fasi_attraversate', {'apertura'})
    mai_cfg           = cfg.get('maieutica', {})

    nuova_fase = fase_attuale
    if fase_attuale == 'apertura':
        if n >= 2 or st.session_state.get('maieutica_evoluzioni'):
            nuova_fase = 'approfondimento'
    elif fase_attuale == 'approfondimento':
        soglia_t = mai_cfg.get('soglia_minuti', 20) * 0.75
        if n >= 5 or ela >= soglia_t:
            nuova_fase = 'consolidamento'

    if nuova_fase != fase_attuale:
        st.session_state['maieutica_fase'] = nuova_fase
        fasi_attraversate.add(nuova_fase)
        st.session_state['maieutica_fasi_attraversate'] = fasi_attraversate


def verifica_maturita() -> bool:
    """
    L1 — La sessione è matura quando ha attraversato tutti e tre le fasi
    e ha prodotto almeno un EVOLUZIONE: o una tensione esplicitata.
    """
    fasi = st.session_state.get('maieutica_fasi_attraversate', set())
    evol = st.session_state.get('maieutica_evoluzioni', [])
    tens = st.session_state.get('maieutica_tensioni', [])
    return (
        {'apertura', 'approfondimento', 'consolidamento'}.issubset(fasi)
        and (len(evol) > 0 or len(tens) > 0)
    )


def verifica_trigger_chiusura(testo_risposta: str, testo_utente: str) -> tuple[bool, str]:
    """
    L2 — Controlla i quattro trigger di chiusura.
    Restituisce (trigger_scattato: bool, tipo: str).
    """
    mai_cfg  = cfg.get('maieutica', {})
    soglia_d = mai_cfg.get('soglia_domande', 7)
    soglia_t = mai_cfg.get('soglia_minuti', 20)
    parole_c = mai_cfg.get('parole_convergenza',
                           ['quindi', 'in sintesi', 'alla fine', 'credo che',
                            'mi rendo conto', 'ho capito'])

    # (c) Comando esplicito
    if testo_utente.strip().lower() in ('/chiudi', '/chiudi sessione'):
        return True, 'comando_esplicito'

    # (a) Soglia domande
    n = st.session_state.get('maieutica_domande_count', 0)
    if n >= soglia_d:
        fase = st.session_state.get('maieutica_fase', 'apertura')
        return True, ('soglia_domande_apertura' if fase == 'apertura' else 'soglia_domande')

    # (b) Soglia tempo
    ela = (time.time() - st.session_state.get('maieutica_inizio_ts', time.time())) / 60
    if ela >= soglia_t:
        return True, 'soglia_tempo'

    # (d) Convergenza linguistica
    testo_lower = testo_utente.lower()
    if any(parola in testo_lower for parola in parole_c) and len(testo_utente) > 80:
        return True, 'convergenza_linguistica'

    return False, ''


def genera_nota_maieutica() -> str:
    """
    L3 — Genera la nota di riflessione in prima persona chiamando l'LLM locale.
    """
    nome        = cfg['identita']['nome_utente']
    history     = st.session_state.get('history', [])
    evoluzioni  = st.session_state.get('maieutica_evoluzioni', [])
    tensioni    = st.session_state.get('maieutica_tensioni', [])
    agenda      = st.session_state.get('maieutica_agenda', {})
    fonti       = list(set(st.session_state.get('maieutica_fonti_sessione', [])))

    scambi = []
    for msg in history:
        if msg['role'] == 'user':
            scambi.append(f"PENSIERO: {msg['content']}")
        elif msg['role'] == 'assistant':
            contenuto = re.sub(r'EVOLUZIONE:[^\n]*', '', msg['content'])
            contenuto = re.sub(r'LACUNA:[^\n]*', '', contenuto).strip()
            scambi.append(f"DOMANDA: {contenuto[:400]}")
    trascrizione = '\n\n'.join(scambi)

    punto = agenda.get('punto_partenza') or {}
    tema_contestuale = punto.get('testo', '') if punto else ''

    tensioni_testo = '\n'.join(f'- {t}' for t in (tensioni + evoluzioni)) if (tensioni or evoluzioni) \
                     else '(nessuna tensione esplicitata — inferire dal contenuto della sessione)'

    fonti_testo = '\n'.join(f'- {f}' for f in fonti) if fonti \
                  else '(nessuna fonte recuperata — usare nomi di documenti plausibili dal contesto)'

    prompt_generazione = f"""Sei Zeno, archivio cognitivo di {nome}.

Il tuo compito è scrivere una riflessione intellettuale in prima persona, come se {nome} l'avesse scritta lui stesso dopo questa sessione di pensiero maieutico. Non è un riassunto della conversazione: è la distillazione in voce coerente di una posizione che stava emergendo.

━━━ PROFILO COGNITIVO DI {nome.upper()} (usalo per calibrare lo stile) ━━━
INTJ con tratti INFJ. Funzione dominante Ni (visione sistemica, pattern a lungo termine).
Stile di pensiero: alta astrazione, pensiero sistemico, tensione tra intuizione e validazione empirica.
Scrittura: densa ma non oscura, orientata a costruire argomenti non a elencare osservazioni.
Rischio da evitare nel documento: astrazione autoreferenziale senza agganci concreti.

━━━ MATERIALE DELLA SESSIONE ━━━
Contesto tematico di partenza: {tema_contestuale if tema_contestuale else '(emergente dalla conversazione)'}

Trascrizione semplificata:
{trascrizione[:3000]}

Tensioni ed evoluzioni esplicitate:
{tensioni_testo}

Fonti recuperate durante la sessione (per la sezione Connessioni):
{fonti_testo}

━━━ ISTRUZIONI PER IL DOCUMENTO ━━━

VOCE E PROSPETTIVA:
- Scrivi in prima persona singolare. Non "@UTENTE@ pensa" ma "Penso", "Mi trovo a chiedermi", "Ho capito che", "Non riesco ancora a decidere se".
- La voce deve essere quella di qualcuno che sta mettendo a fuoco qualcosa che già sapeva in modo confuso. Non di qualcuno che ha trovato la risposta.
- Evita parafrasi della conversazione. Trasforma i pensieri emersi in posizioni elaborate.

STRUTTURA OBBLIGATORIA:

1. TITOLO — Una riga. Concettuale, non descrittivo.
   ✗ "Riflessione sulla complessità" / "Note su X e Y"
   ✓ Qualcosa che cattura la tensione centrale come farebbe il titolo di un saggio.
   Esempi di stile (non di contenuto): "Il paradosso del controllo", "Quando la mappa diventa territorio", "Sul costo dell'astrazione".

2. CORPO — 300-600 parole. Prosa fluida. Nessun bullet point, nessun elenco.
   - Sviluppa la posizione intellettuale emersa, non la sequenza della conversazione.
   - Integra le tensioni senza risolverle forzatamente: dove c'è ambiguità, lasciala emergere come forza del testo, non come limite.
   - Usa almeno un esempio concreto o un ancoraggio empirico (anti-loop Ni-Ti).
   - Ogni paragrafo deve avanzare il ragionamento, non ripeterlo.
   - Non scrivere mai "durante questa riflessione", "questa sessione mi ha portato", "ho realizzato oggi": scrivi come se fosse già pensiero consolidato.

3. TENSIONI APERTE — Sezione finale con titolo "## Tensioni aperte"
   2-4 domande. Specifiche, non generiche.
   ✗ "Come si relazionano X e Y?"
   ✓ "Se accetto che X implica Z, perché continuo a comportarmi come se Y fosse il vincolo principale?"
   Le domande devono essere in prima persona e rispecchiare qualcosa di irrisolto nel corpo.

4. CONNESSIONI — Sezione finale con titolo "## Connessioni"
   2-3 voci. Formato:
   - "Questo tema risuona con [nome file o autore]: [una frase che spiega perché, non cosa]"
   - "Vedi la tensione con [nome file o autore]: [perché è tensione, non semplice rimando]"

VINCOLI ASSOLUTI:
- Nessun bullet point nel corpo
- Nessuna meta-riflessione sulla conversazione
- Nessuna frase che inizia con "Zeno" o che faccia riferimento al sistema
- Non superare 700 parole in totale

Scrivi ora il documento:"""

    llm = MotoreLocale(model=cfg['modelli']['llm'],
                       request_timeout=cfg['modelli'].get('timeout_secondi', 600))
    try:
        risposta = llm.complete(prompt_generazione)
        return str(risposta)
    except Exception as e:
        return f'[Errore generazione nota: {e}]'


def salva_nota_maieutica(testo_nota: str) -> str:
    """
    L3 — Salva la nota editata su file .md con frontmatter.
    Restituisce il path del file salvato.
    """
    sessione_id  = st.session_state.get('maieutica_sessione_id', 'unknown')
    evoluzioni   = st.session_state.get('maieutica_evoluzioni', [])
    tensioni     = st.session_state.get('maieutica_tensioni', [])
    agenda       = st.session_state.get('maieutica_agenda', {})
    punto        = agenda.get('punto_partenza') or {}
    tema         = punto.get('tema_fonte') or punto.get('testo', 'generale')[:60]

    archi_confermati_sess = [
        {'source': a['source'], 'target': a['target']}
        for a in st.session_state.get('maieutica_archi_confermati_impliciti', [])
    ]

    linee           = testo_nota.strip().split('\n')
    titolo_inferito = linee[0].lstrip('# ').strip() if linee else 'riflessione'
    corpo           = '\n'.join(linee[1:]).strip() if len(linee) > 1 else testo_nota

    tensioni_aperte = bool(tensioni) or bool(evoluzioni)
    data_iso        = datetime.now().strftime('%Y-%m-%d')
    ts              = datetime.now().strftime('%Y-%m-%d_%H%M')

    frontmatter = (
        f'---\n'
        f'fonte: maieutica\n'
        f'sessione_id: {sessione_id}\n'
        f'data: {data_iso}\n'
        f'tema: "{tema}"\n'
        f'tensioni_aperte: {str(tensioni_aperte).lower()}\n'
        f'archi_confermati: {json.dumps(archi_confermati_sess, ensure_ascii=False)}\n'
        f'attribuzione: tuo_pensiero_abbozzato\n'
        f'tipo: riflessione\n'
        f'---\n\n'
    )

    riflessioni_dir = cfg['percorsi']['documents'] + '/riflessioni'
    os.makedirs(riflessioni_dir, exist_ok=True)
    slug_t  = re.sub(r'[^\w\s-]', '', titolo_inferito.lower())
    slug_t  = re.sub(r'\s+', '_', slug_t)[:40]
    filepath = os.path.join(riflessioni_dir, f'maieutica_{ts}_{slug_t}.md')

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(frontmatter)
        f.write(f'# {titolo_inferito}\n\n')
        f.write(corpo)

    # Se la sessione ha usato una lacuna da dialogo come punto di partenza:
    # 1. marcala affrontata nell'agenda (lacune_da_dialogo.json)
    # 2. rimuovila dall'archivio (lacune_aperte.json) — il dialogo l'ha elaborata
    _lid = st.session_state.pop('_lac_dialogo_attiva', '')
    if _lid:
        _marca_lacuna_affrontata(cfg, _lid)
        _testo_lac = (st.session_state.get('maieutica_agenda', {})
                      .get('punto_partenza', {}).get('testo', ''))
        _risolvi_lacuna_aperta(cfg, _testo_lac)

    st.session_state['sessione_gia_salvata'] = True
    return filepath


def salva_archi_confermati_da_sessione():
    """
    L0 — Scrive in archi_confermati.json gli archi che hanno ricevuto
    conferma implicita o esplicita durante la sessione MAIEUTICA.
    """
    archi_sess = st.session_state.get('maieutica_archi_confermati_impliciti', [])
    if not archi_sess:
        return

    conf_path = os.path.join(cfg['percorsi']['stats'], 'archi_confermati.json')
    try:
        confermati = json.load(open(conf_path, encoding='utf-8')) if os.path.exists(conf_path) else []
        conf_keys  = {tuple(sorted([a['source'], a['target']])) for a in confermati}
        nuovi      = [a for a in archi_sess
                      if tuple(sorted([a['source'], a['target']])) not in conf_keys]
        if nuovi:
            confermati.extend(nuovi)
            with open(conf_path, 'w', encoding='utf-8') as f:
                json.dump(confermati, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def registra_arco_implicito(testo_risposta_utente: str):
    """
    L0 — Scansiona la risposta dell'utente cercando conferme implicite
    agli archi dialogici nell'agenda.
    """
    agenda = st.session_state.get('maieutica_agenda', {})
    archi  = agenda.get('archi_dialogici', [])
    if not archi:
        return

    archi_confermati_impl = st.session_state.get('maieutica_archi_confermati_impliciti', [])
    testo_lower = testo_risposta_utente.lower()

    negazioni = ['non ', 'no ', 'falso', 'sbagliato', 'non è', 'non credo', 'in realtà no']
    for arco in archi:
        sl = arco['source_label'].lower()
        tl = arco['target_label'].lower()
        if sl in testo_lower and tl in testo_lower:
            idx_s = testo_lower.find(sl)
            idx_t = testo_lower.find(tl)
            finestra = testo_lower[max(0, min(idx_s, idx_t)-30):
                                   max(idx_s, idx_t)+len(max(sl, tl))+30]
            if not any(neg in finestra for neg in negazioni):
                chiave = tuple(sorted([arco['source'], arco['target']]))
                già_presenti = {tuple(sorted([a['source'], a['target']]))
                                for a in archi_confermati_impl}
                if chiave not in già_presenti:
                    archi_confermati_impl.append(arco)
    st.session_state['maieutica_archi_confermati_impliciti'] = archi_confermati_impl

    agenda_trattati = agenda.get('trattati', [])
    for arco in archi:
        sl = arco['source_label'].lower()
        tl = arco['target_label'].lower()
        if sl in testo_lower or tl in testo_lower:
            chiave = f"{arco['source']}_{arco['target']}"
            if chiave not in agenda_trattati:
                agenda_trattati.append(chiave)
    agenda['trattati'] = agenda_trattati
    st.session_state['maieutica_agenda'] = agenda


# ── Sidebar sinistra: gemello statistico ─────────────────────────────────────
with st.sidebar:
    # ── Logo ──────────────────────────────────────────────────────────────
    logo_path = str(Path(__file__).resolve().parent.parent / 'Logo_Zeno.png')
    if os.path.exists(logo_path):
        st.image(logo_path, width=80)
    st.title('Zeno')
    st.caption(con_nome('Archivio personale di @UTENTE@'))
    st.divider()

    _render_processi_sidebar(cfg['percorsi']['stats'])
    st.divider()

    # ── Gemello statistico ────────────────────────────────────────────────
    # V6: gli snapshot sono datati (snapshot_AAAA-MM-GG.json, uno al giorno
    # in cui girano) — non esiste più un file fisso "snapshot.json". Si
    # prende l'ultimo per data, come fanno periodici.py e dashboard.py.
    _snap_glob = sorted(glob.glob(os.path.join(cfg['percorsi']['stats'],
                                                 'snapshot_*.json')))
    if _snap_glob:
        try:
            s = json.load(open(_snap_glob[-1], encoding='utf-8'))
        except Exception:
            s = {}
        _tot_documenti = sum(a.get('totale', 0)
                              for a in s.get('documenti', {}).values())
        st.caption(f"Snapshot: {s.get('data', 'N/D')}")
        st.write(f"Documenti: {_tot_documenti or 'N/D'}")
        st.write(f"Chunk vettoriali: {s.get('vettori_totali', 'N/D')}")
        libri = s.get('libri', {})
        libri_letti     = libri.get('read', 0)
        libri_corso     = libri.get('currently-reading', 0)
        libri_daleggere = libri.get('to-read', 0)
        libri_totale    = libri_letti + libri_corso + libri_daleggere
        if libri_totale == 0:
            libri_vault = cfg['percorsi'].get('libri',
                          os.path.join(cfg['percorsi'].get('vault', ''), 'libri'))
            if os.path.exists(libri_vault):
                libri_letti = len([f for f in os.listdir(libri_vault) if f.endswith('.md')])
        if libri_letti or libri_corso or libri_daleggere or libri.get('rating_medio'):
            st.divider()
            st.caption('Biblioteca')
            st.write(f'Letti: {libri_letti}')
            if libri_corso: st.write(f'In corso: {libri_corso}')
            if libri_daleggere: st.write(f'Da leggere: {libri_daleggere}')
            if libri.get('rating_medio'):
                st.write(f'Rating medio: {libri["rating_medio"]}')
    else:
        # Messaggio ereditato dal V5, quando snapshot.py si lanciava a mano.
        # In V6 è un job periodico del registro (periodici.REGISTRO, cadenza 6
        # giorni) che `zeno sync` esegue a ogni avvio: se le statistiche non ci
        # sono, non c'è niente da lanciare a mano — o non è ancora girato, o è
        # fallito. Il pulsante qui sotto, nel pannello Processi, lo forza.
        st.info('Statistiche non ancora generate. Il job **snapshot** gira da sé '
                'a ogni avvio (cadenza 6 giorni); puoi forzarlo dal pannello '
                '🔄 Processi Zeno qui in alto.')
    st.divider()

    # ── Salva sessione ────────────────────────────────────────────────────
    if st.button('Salva sessione'):
        ts   = datetime.now().strftime('%Y%m%d_%H%M')
        path = f"{cfg['percorsi']['memoria']}/query_archiviate/sessione_{ts}.json"
        with open(path, 'w') as f:
            json.dump(st.session_state.get('history', []), f, ensure_ascii=False, indent=2)
        _scrivi_temi_attivi(cfg, st.session_state.get('history', []))
        st.session_state['sessione_gia_salvata'] = True
        st.success('Sessione salvata')

    st.divider()
    if st.button('⏻ Chiudi Zeno', type='secondary',
                  help='Salva la sessione, aggiorna gli snapshot e arresta i processi background'):
        st.session_state['chiudi_zeno_richiesto'] = True
        st.session_state['chiudi_zeno_fase'] = None
        st.rerun()

        _render_processi_sidebar(cfg['percorsi']['stats'])

# ── Engine con cache ──────────────────────────────────────────────────────────
@st.cache_resource
def load_engine():
    # V6: niente più Qdrant/Docker da inizializzare. Il retrieval apre
    # zeno.db a ogni query (connessione SQLite: costo in millisecondi,
    # zero problemi di thread con Streamlit).
    return None

idx = load_engine()

# ── Bilanciamento pesi per strato e per documento ────────────────────────────
class StratoBalancer(BaseNodePostprocessor):
    """
    Post-processor per il retrieval bilanciato di Zeno.

    Funzionamento:
    - Un documento = un candidato (best chunk per file)
    - Penalità abbondanza: riduce il vantaggio numerico dei libri nel pool
    - Bonus temporalità: documenti recenti ricevono un piccolo boost
    - Soglia minima score: esclude chunk irrilevanti
    - Boost query tematiche: schede tema ricevono ×1.25 su query di pensiero
    - Quota garantita: slot minimi riservati per esplicito e inferito
    - Filtro vino: chunk da /vivino/ esclusi su query non enologiche
    """
    top_n:                  int   = 5
    memoria_dir:            str   = ''
    pesi_strato:            dict  = {}
    quota_garantita:        dict  = {}
    penalita_abbondanza:    float = 0.3
    bonus_temporalita:      float = 0.15
    tau_temporalita_giorni: float = 365.0
    soglia_min_score:       float = 0.25
    keywords_tematica:      list  = []

    PESI_DEFAULT: ClassVar[dict] = {
        'esplicito': 1.4,
        'inferito':  1.3,
        'dialogico': 1.1,
        'acquisito': 1.0,
    }
    QUOTA_DEFAULT: ClassVar[dict] = {
        'esplicito': 1,
        'inferito':  1,
    }
    VINO_KW: ClassVar[frozenset] = frozenset({
        'vino', 'vini', 'wine', 'degust', 'cantina', 'vigna', 'terroir',
        'sommelier', 'uvaggio', 'vitigno', 'annata', 'calice', 'vivino',
        'enologi', 'barrique', 'tannin', 'acidità', 'bouquet', 'viticoltur',
        'enoteca', 'vendemmia', 'mosto', 'fermentaz',
    })

    def _e_query_enologica(self, query_bundle) -> bool:
        if not query_bundle:
            return False
        return any(k in query_bundle.query_str.lower() for k in self.VINO_KW)

    def _e_query_tematica(self, query_bundle) -> bool:
        if not query_bundle or not self.keywords_tematica:
            return False
        q = query_bundle.query_str.lower()
        return any(kw in q for kw in self.keywords_tematica)

    def _detect_strato(self, node) -> str:
        meta  = node.metadata or {}
        fp    = meta.get('file_path', '') or meta.get('file_name', '')
        fp_l  = fp.lower()

        # Override prioritario: _personale in /libri/ = acquisito,
        # anche se indicizzato con strato=esplicito.
        # Deve stare PRIMA del check sul campo strato nei metadati,
        # altrimenti il metadata sovrascrive questa logica.
        if '_personale' in os.path.basename(fp_l) and '/libri/' in fp_l:
            return 'acquisito'

        strato = meta.get('strato', '')
        pesi   = self.pesi_strato or self.PESI_DEFAULT
        if strato in pesi:
            return strato
        if self.memoria_dir and self.memoria_dir.lower() in fp_l:
            return 'inferito'
        if any(x in fp_l for x in ['/riflessioni/', '/pubblicazioni/']):
            return 'esplicito'
        if '_personale' in os.path.basename(fp_l):
            return 'esplicito'
        if any(x in fp_l for x in ['/chatgpt/', '/gemini/', '/query_archiviate/']):
            return 'dialogico'
        return 'acquisito'

    def _bonus_tempo(self, node) -> float:
        if self.bonus_temporalita <= 0:
            return 0.0
        try:
            fp = (node.metadata or {}).get('file_path', '')
            if fp and os.path.exists(fp):
                import math
                giorni_fa = (time.time() - os.path.getmtime(fp)) / 86400
                return self.bonus_temporalita * math.exp(
                    -giorni_fa / max(self.tau_temporalita_giorni, 1.0))
        except Exception:
            pass
        return 0.0

    def _postprocess_nodes(
        self,
        nodes: List[NodeWithScore],
        query_bundle: Optional[QueryBundle] = None,
    ) -> List[NodeWithScore]:
        if not nodes:
            return nodes

        pesi            = self.pesi_strato or self.PESI_DEFAULT
        quota           = self.quota_garantita or self.QUOTA_DEFAULT
        query_enologica = self._e_query_enologica(query_bundle)
        query_tematica  = self._e_query_tematica(query_bundle)

        # ── 1. Best chunk per documento + filtro vino + soglia minima ─────────
        per_doc: dict = {}
        for nws in nodes:
            score = nws.score or 0.0
            if score < self.soglia_min_score:
                continue
            fp = (nws.node.metadata or {}).get('file_path') or \
                 (nws.node.metadata or {}).get('file_name') or \
                 nws.node.node_id
            if not query_enologica and '/vivino/' in fp.lower():
                continue
            if fp not in per_doc or score > per_doc[fp].score:
                per_doc[fp] = nws

        if not per_doc:  # fallback senza soglia
            for nws in nodes:
                fp = (nws.node.metadata or {}).get('file_path') or \
                     (nws.node.metadata or {}).get('file_name') or \
                     nws.node.node_id
                if not query_enologica and '/vivino/' in fp.lower():
                    continue
                score = nws.score or 0.0
                if fp not in per_doc or score > per_doc[fp].score:
                    per_doc[fp] = nws

        # ── 2. Conteggio documenti per strato (penalità abbondanza) ───────────
        conteggi: dict = {}
        for nws in per_doc.values():
            s = self._detect_strato(nws.node)
            conteggi[s] = conteggi.get(s, 0) + 1

        # ── 3. Score pesato ───────────────────────────────────────────────────
        pesati: list = []
        for nws in per_doc.values():
            strato    = self._detect_strato(nws.node)
            mult      = pesi.get(strato, 1.0)
            n_strato  = max(conteggi.get(strato, 1), 1)
            penalita  = n_strato ** self.penalita_abbondanza
            bonus_t   = self._bonus_tempo(nws.node)
            new_score = round(((nws.score or 0.0) * mult / penalita) + bonus_t, 4)
            if query_tematica and strato == 'inferito':
                new_score = round(new_score * 1.25, 4)
            pesati.append(NodeWithScore(node=nws.node, score=new_score))

        pesati.sort(key=lambda x: x.score or 0.0, reverse=True)

        # ── 4. Quota garantita ────────────────────────────────────────────────
        risultato: list  = []
        per_strato: dict = {}
        rimanenti: list  = []

        for nws in pesati:
            strato     = self._detect_strato(nws.node)
            slot_quota = quota.get(strato, 0)
            slot_usati = per_strato.get(strato, 0)
            if slot_usati < slot_quota and nws not in risultato:
                risultato.append(nws)
                per_strato[strato] = slot_usati + 1
            else:
                rimanenti.append(nws)

        for nws in rimanenti:
            if len(risultato) >= self.top_n:
                break
            if nws not in risultato:
                risultato.append(nws)

        risultato.sort(key=lambda x: x.score or 0.0, reverse=True)
        top = risultato[:self.top_n]

        # ── 5. Tronca testo per LLM (copia del nodo, originale intatto) ───────
        import copy as _copy
        for i, nws in enumerate(top):
            if len(nws.node.text) > 1000:
                _n      = _copy.copy(nws.node)
                _n.text = nws.node.text[:1000]
                top[i]  = NodeWithScore(node=_n, score=nws.score)
        return top


def get_engine(modalita):
    ret_cfg     = cfg.get('retrieval', {})
    top_k_raw   = cfg['modelli'].get('similarity_top_k', 5)
    multiplier  = ret_cfg.get('top_k_fetch_multiplier', 8)
    fetch_min   = ret_cfg.get('top_k_fetch_min', 50)
    top_k_fetch = max(top_k_raw * multiplier, fetch_min)
    pesi_strato = ret_cfg.get('pesi_strato', {})

    balancer = StratoBalancer(
        top_n                  = top_k_raw,
        memoria_dir            = cfg['percorsi'].get('memoria', ''),
        pesi_strato            = pesi_strato,
        quota_garantita        = ret_cfg.get('quota_garantita', {'esplicito': 1, 'inferito': 1}),
        penalita_abbondanza    = float(ret_cfg.get('penalita_abbondanza', 0.3)),
        bonus_temporalita      = float(ret_cfg.get('bonus_temporalita', 0.15)),
        tau_temporalita_giorni = float(ret_cfg.get('tau_temporalita_giorni', 365.0)),
        soglia_min_score       = float(ret_cfg.get('soglia_min_score', 0.25)),
        keywords_tematica      = ret_cfg.get('keywords_query_tematica', []),
    )

    # V6: retrieval e pesi per strato vivono in zeno_engine.ask (che replica
    # la filosofia dello StratoBalancer qui sopra, filtro vino incluso).
    # L'engine si costruisce in fondo, quando il template qa è pronto.
    _ = (top_k_fetch, balancer)   # parametri V5 conservati a documentazione

    nome        = cfg['identita']['nome_utente']
    profilo_str = f'\n\n{PROFILO_COGNITIVO_MBTI}' if profilo else ''
    if profilo:
        profilo_str += f'\n\nPROFILO PERSONALE:\n{profilo[:600]}'

    if modalita == 'ARCHIVIO':
        # Testo del prompt in zeno_engine/prompt_chat.py: lo stesso usato
        # da `zeno valuta`, così i numeri misurano la chat vera.
        qa = PromptTemplate(prompt_chat.template_archivio(nome, profilo_str))
    else:  # MAIEUTICA — fase-aware
        fase         = st.session_state.get('maieutica_fase', 'apertura')
        agenda       = st.session_state.get('maieutica_agenda', {})
        punto        = agenda.get('punto_partenza') or {}
        n_domande    = st.session_state.get('maieutica_domande_count', 0)
        archi_agenda = agenda.get('archi_dialogici', [])

        if fase == 'apertura' and punto:
            _scheda_ctx = ''
            if punto.get('scheda_cosa_sa') or punto.get('scheda_tensioni'):
                _parti = []
                if punto.get('tema_fonte'):
                    _parti.append(f'TEMA: {punto["tema_fonte"]}')
                if punto.get('scheda_cosa_sa'):
                    _parti.append(f'COSA SA:\n{punto["scheda_cosa_sa"]}')
                if punto.get('scheda_come_pensa'):
                    _parti.append(f'COME LA PENSA:\n{punto["scheda_come_pensa"]}')
                if punto.get('scheda_tensioni'):
                    _parti.append(f'TENSIONI IRRISOLTE:\n{punto["scheda_tensioni"]}')
                if punto.get('scheda_evoluzione'):
                    _parti.append(f'EVOLUZIONE:\n{punto["scheda_evoluzione"]}')
                _scheda_ctx = '\n\nCONTESTO SCHEDA TEMA (inferito da Zeno):\n' + '\n'.join(_parti)
            contesto_fase = (
                f'PUNTO DI PARTENZA (lacuna programmata da risolvere): '
                f'"{punto.get("testo", "")}".'
                f'{_scheda_ctx}'
                f'\n\nUsando questo contesto, formula UNA SOLA domanda di apertura '
                f'che metta a fuoco la lacuna specifica. '
                f'La domanda deve emergere dalla tensione tra ciò che {nome} già sa '
                f'e ciò che manca — non fare una domanda generica sul tema. '
                f'Esplora il territorio, non convergere ancora.'
            )
        elif fase == 'approfondimento':
            contesto_fase = (
                f'FASE: approfondimento (scambio {n_domande}). '
                f'Hai già esplorato il territorio. Ora scava. '
                f'Cerca le assunzioni non dichiarate, i salti logici, i punti di massima resistenza. '
                f'Se rilevi contraddizioni con l\'archivio, segnalale con '
                f'EVOLUZIONE: [cosa pensavi prima] vs [cosa stai dicendo ora]. '
                f'Fai UNA SOLA domanda che metta pressione sul punto più fragile.'
            )
        elif fase == 'consolidamento':
            archi_str = '; '.join(
                f'{a["source_label"]} ↔ {a["target_label"]}'
                for a in archi_agenda[:2]
            ) if archi_agenda else ''
            contesto_fase = (
                f'FASE: consolidamento (scambio {n_domande}). '
                f'Chiedi a {nome} di sintetizzare in prima persona la posizione emersa. '
                f'Non fare domande espansive — fai domande di verifica e ancoraggio. '
                + (f'Connessioni da consolidare: {archi_str}. ' if archi_str else '')
                + f'Chiedi: cosa pensi ora che non pensavi prima? Come lo diresti con parole tue?'
            )
        else:
            contesto_fase = ''

        qa = PromptTemplate(
            f'Sei Zeno in modalità MAIEUTICA, interlocutore socratico di {nome}. '
            f'Il tuo obiettivo NON è dare risposte complete. '
            f'È fare domande che portino {nome} a scoprire connessioni nuove, '
            f'contraddizioni interne, zone inesplorate del suo pensiero. '
            f'{contesto_fase} '
            f'Usa il contesto dell\'archivio per individuare tensioni reali, non generiche. '
            f'Se rilevi una contraddizione con qualcosa scritto in precedenza, '
            f'segnalala con: EVOLUZIONE: [cosa pensavi prima] vs [cosa pensi ora]. '
            f'Se @UTENTE@ cita o attribuisce contenuti a un autore o opera non presente '
            f'nel tuo archivio, segnalalo con: LACUNA: [autore/opera citata] | '
            f'SUGGERIMENTO: ricerca bibliografica. '
            f'Rispondi SEMPRE in italiano. '
            f'Il tuo archivio locale è sempre la base — non inventare contenuti. '
            f'Termina SEMPRE con UNA SOLA domanda. '
            f'{profilo_str}\n\n'
            f'Contesto dal tuo archivio:\n{{context_str}}\n\n'
            f'Proposta di {nome}: {{query_str}}\n\n'
            f'Risposta con domanda maieutica:'
        )

    # L'adapter carica da sé la sezione motore_epistemico di zeno_config.yaml.
    # ARCHIVIO v4: decide con una chiamata a parte se i documenti rispondono.
    return EngineZeno(str(qa), decidi_prima=(modalita == 'ARCHIVIO'
                                             and prompt_chat.VERSIONE_CHAT == 4))


# ── Layout principale ─────────────────────────────────────────────────────────
if 'pannello_aperto' not in st.session_state:
    st.session_state.pannello_aperto = True

_h1, _h2, _h3 = st.columns([2.5, 1, 0.4])
with _h2:
    web_attivo = st.toggle(
        'Integrazione web', value=False,
        help=(
            'Se attivo, Zeno cerca sul web SOLO quando il tuo archivio '
            'non è sufficiente. Il tuo archivio rimane sempre la fonte primaria. '
            'Tutto ciò che recupera ti viene mostrato prima di essere salvato.'
        )
    )
with _h3:
    icona = '◀' if st.session_state.pannello_aperto else '▶'
    if st.button(icona, help='Mostra/nascondi pannello di controllo'):
        st.session_state.pannello_aperto = not st.session_state.pannello_aperto
        st.rerun()

if st.session_state.pannello_aperto:
    col_chat, col_ctrl = st.columns([3, 1])
else:
    col_chat = st.container()
    col_ctrl = None

if st.session_state.pannello_aperto:
    with col_ctrl:
        st.subheader('Pannello di controllo')
        tab_err, tab_set, tab_upd = st.tabs(['Errori e Lacune', 'Impostazioni', 'Aggiornamenti'])

        with tab_err:
            log_path = str(__import__('pathlib').Path(__file__).resolve().parent.parent / 'stats' / 'errori.log')
            if os.path.exists(log_path):
                errori = open(log_path, encoding='utf-8').read().strip()
                if errori:
                    col_err, col_err_x = st.columns([9, 1])
                    with col_err:
                        st.markdown(f'<div class="errore">{errori[-1500:]}</div>',
                                    unsafe_allow_html=True)
                    with col_err_x:
                        if st.button('✕', key='chiudi_errori', help='Cancella log errori'):
                            open(log_path, 'w').close()
                            st.rerun()
                else:
                    st.success('Nessun errore registrato.')

            lacune_log = st.session_state.get('lacune_sessione', [])
            if lacune_log:
                st.subheader('Lacune rilevate')
                for i, lacuna in enumerate(lacune_log):
                    if isinstance(lacuna, str):
                        lacuna = {'testo': lacuna, 'query': lacuna[:120]}

                    _col_chiudi, _col_dial = st.columns([1, 1])
                    with _col_chiudi:
                        if st.button('✕ Ignora', key=f'chiudi_{i}'):
                            rimuovi_lacuna(i)
                            st.rerun()
                    with _col_dial:
                        if st.button("📌 Affronta con Zeno", key=f"dial_{i}",
                                     help="Aggiunge all'agenda delle sessioni MAIEUTICA"):
                            _lid = re.sub(r"\s+", "_", lacuna["testo"][:60].lower())
                            _lid = re.sub(r"[^\w_]", "", _lid)
                            _lac_path = os.path.join(cfg["percorsi"]["stats"],
                                                     "lacune_da_dialogo.json")
                            try:
                                _ld = json.load(open(_lac_path, encoding="utf-8")) \
                                      if os.path.exists(_lac_path) else []
                            except Exception:
                                _ld = []
                            if not any(e.get("id") == _lid for e in _ld):
                                _ld.append({"id": _lid,
                                            "testo": lacuna["testo"],
                                            "tema_fonte": "",
                                            "data_aggiunta": datetime.now().isoformat(),
                                            "affrontata": False})
                                with open(_lac_path, "w", encoding="utf-8") as _fw:
                                    json.dump(_ld, _fw, ensure_ascii=False, indent=2)
                            st.success("Aggiunta all'agenda MAIEUTICA.")
                    # Testo completo scrollabile
                    st.text_area('Testo lacuna', value=lacuna['testo'],
                                 height=120, disabled=True,
                                 key=f'lac_txt_{i}', label_visibility='collapsed')

                    # Cerca sul web — sempre visibile, indipendente dal toggle
                    btn_key = f'web_lacuna_{i}'
                    res_key = f'web_risultati_{i}'
                    query_mirata = estrai_query_da_lacuna(lacuna['testo'], lacuna['query'])

                    _wb1, _wb2 = st.columns([2, 1])
                    with _wb1:
                        if st.button('🔍 Cerca sul web', key=btn_key):
                            with st.spinner(f'Ricerca: "{query_mirata}"...'):
                                risultati, err = cerca_sul_web(query_mirata)
                            if err:
                                st.error(err)
                            elif risultati:
                                st.session_state[res_key] = risultati
                            else:
                                st.warning('Nessun risultato trovato.')
                    with _wb2:
                        if res_key in st.session_state:
                            if st.button('🔄 Rigenera', key=f'web_regen_{i}'):
                                del st.session_state[res_key]
                                with st.spinner('Nuova ricerca...'):
                                    _nr, _ne = cerca_sul_web(query_mirata)
                                if not _ne and _nr:
                                    st.session_state[res_key] = _nr
                                st.rerun()

                    if res_key in st.session_state:
                        risultati = st.session_state[res_key]
                        st.warning('**Fonti web — non provengono dal tuo archivio.**')
                        selezionati = []
                        for j, rw in enumerate(risultati):
                            checked = st.checkbox(
                                f"[{rw['titolo']}]({rw['url']})",
                                key=f'chk_{i}_{j}'
                            )
                            st.caption(rw['estratto'])
                            if checked:
                                selezionati.append(rw)
                        if selezionati and st.button('Salva selezionati', key=f'salva_{i}'):
                            ts = datetime.now().strftime('%Y%m%d_%H%M')
                            for j, rw in enumerate(selezionati):
                                fpath = (cfg['percorsi']['documents'] +
                                         f"/articoli_saggistica/web_{ts}_{j}.md")
                                with open(fpath, 'w', encoding='utf-8') as f:
                                    f.write(
                                        f"---\ntipo: fonte_web\nurl: {rw['url']}\n"
                                        f"data: {ts}\nattribuzione: sapere_acquisito\n---\n\n"
                                        f"# {rw['titolo']}\n\n{rw['estratto']}\n"
                                    )
                            del st.session_state[res_key]
                            rimuovi_lacuna(i)
                            st.success(f'{len(selezionati)} fonte/i salvata/e. '
                                       'Il Watcher le indicizzerà automaticamente.')
                            st.rerun()

                    url_key     = f'url_input_{i}'
                    url_res_key = f'url_risultato_{i}'
                    st.markdown('**Aggiungi una fonte manualmente:**')
                    url_input = st.text_input('Incolla URL', key=url_key,
                                              placeholder='https://...')
                    if url_input and st.button('📥 Importa pagina', key=f'importa_{i}'):
                        with st.spinner('Scarico e filtro la pagina...'):
                            titolo_url, testo_url = importa_da_url(url_input)
                        if testo_url and titolo_url:
                            st.session_state[url_res_key] = {
                                'titolo': titolo_url,
                                'testo': testo_url,
                                'url': url_input
                            }
                        else:
                            st.error(testo_url)

                    if url_res_key in st.session_state:
                        doc = st.session_state[url_res_key]
                        st.success(f'Pagina estratta: **{doc["titolo"]}** '
                                   f'({len(doc["testo"])} caratteri)')
                        with st.expander('Anteprima testo estratto'):
                            st.text(doc['testo'][:1500] + ('...' if len(doc['testo']) > 1500 else ''))
                        if st.button('💾 Salva in archivio', key=f'salva_url_{i}'):
                            ts   = datetime.now().strftime('%Y%m%d_%H%M')
                            slug_u = re.sub(r'[^\w]', '_', doc['titolo'])[:50]
                            fpath = (cfg['percorsi']['documents'] +
                                     f"/articoli_saggistica/url_{ts}_{slug_u}.md")
                            with open(fpath, 'w', encoding='utf-8') as f:
                                f.write(
                                    f"---\ntipo: fonte_web\nurl: {doc['url']}\n"
                                    f"data: {ts}\nattribuzione: sapere_acquisito\n---\n\n"
                                    f"# {doc['titolo']}\n\n{doc['testo']}\n"
                                )
                            del st.session_state[url_res_key]
                            rimuovi_lacuna(i)
                            st.success('Salvato. Il Watcher lo indicizzerà automaticamente.')
                            st.rerun()

                    st.divider()
            else:
                if not (os.path.exists(log_path) and
                        open(log_path).read().strip()):
                    st.success('Nessuna lacuna rilevata.')

            # ── Connessioni emerse dal dialogo ────────────────────────────────
            _conn_sess = st.session_state.get('_connessioni_emerse_sessione', [])
            if _conn_sess:
                st.subheader('Connessioni emerse')
                st.caption(
                    'Hai espresso queste connessioni durante il dialogo. '
                    'Salvate in connessioni_emerse.json per il grafo cognitivo.'
                )
                conn_path = os.path.join(cfg['percorsi']['stats'], 'connessioni_emerse.json')
                for idx_c, testo_c in enumerate(_conn_sess):
                    col_c1, col_c2 = st.columns([5, 1])
                    with col_c1:
                        st.markdown(f'🔗 _{testo_c}_')
                    with col_c2:
                        if st.button('✕', key=f'rm_conn_{idx_c}',
                                     help='Rimuovi questa connessione'):
                            _conn_sess.pop(idx_c)
                            st.session_state['_connessioni_emerse_sessione'] = _conn_sess
                            if os.path.exists(conn_path):
                                try:
                                    _cl = json.load(open(conn_path, encoding='utf-8'))
                                    _tn = re.sub(r'\s+', ' ', testo_c.lower()).strip()
                                    _cl = [e for e in _cl
                                           if re.sub(r'\s+', ' ',
                                               e.get('testo', '').lower()).strip() != _tn]
                                    with open(conn_path, 'w', encoding='utf-8') as _fw:
                                        json.dump(_cl, _fw, ensure_ascii=False, indent=2)
                                except Exception:
                                    pass
                            st.rerun()
                st.divider()

            # ── Segnali di errore della sessione ─────────────────────────────
            _err_sess = st.session_state.get('_segnali_errore_sessione', [])
            if _err_sess:
                with st.expander(f'⚠ {len(_err_sess)} segnale/i di errore rilevato/i'):
                    st.caption(
                        'Salvati in correzioni.json per calibrare le inferenze future.'
                    )
                    for _se in _err_sess[-3:]:
                        st.markdown(
                            f'<div class="avviso">⚠ {_se["tipo"]} — '
                            f'<em>{_se["prompt"][:120]}</em></div>',
                            unsafe_allow_html=True
                        )

        with tab_set:
            st.caption(f'Modifica {__import__("pathlib").Path(__file__).resolve().parent.parent / "zeno_config.yaml"} per cambiare:')
            st.write(f"Modello LLM: `{cfg['modelli']['llm']}`")
            st.write(f"Soglia lacune: `{cfg['soglie']['lacuna_similarita']}`")
            st.write(f"Lingua: `{cfg['identita']['lingua_risposta']}`")
            mai_cfg = cfg.get('maieutica', {})
            if mai_cfg:
                st.write(f"MAIEUTICA — soglia domande: `{mai_cfg.get('soglia_domande', 7)}`")
                st.write(f"MAIEUTICA — soglia minuti: `{mai_cfg.get('soglia_minuti', 20)}`")
            if st.button('Ricarica configurazione'):
                st.cache_resource.clear()
                st.rerun()

        with tab_upd:
            # Prima qui c'era solo il comando da copiare a mano. Il comando era
            # anche sbagliato (puntava al Python di sistema, non a .venv_zeno):
            # vedi update_checker.controlla_librerie_python. Ora si installa da
            # qui, un pacchetto alla volta e con esito visibile.
            if aggiornamenti_disponibili:
                st.warning(f'{len(aggiornamenti_disponibili)} aggiornamenti disponibili')
                for _i, a in enumerate(aggiornamenti_disponibili):
                    _c1, _c2 = st.columns([3, 1])
                    _c1.write(f"**{a['nome']}**: "
                              f"{a.get('versione_attuale', '')} → {a['ultima_versione']}")
                    if _c2.button('Installa', key=f'upd_{_i}'):
                        import subprocess as _sp_upd
                        with st.spinner(f"Installo {a['nome']}…"):
                            _r = _sp_upd.run(a['comando'], shell=True,
                                             capture_output=True, text=True,
                                             timeout=900)
                        if _r.returncode == 0:
                            st.success(f"{a['nome']} aggiornato. "
                                       'Riavvia Zeno per usarlo davvero: il '
                                       'processo in corso tiene ancora in '
                                       'memoria la versione vecchia.')
                            controlla_aggiornamenti(forza=True)
                        else:
                            st.error(f"Installazione fallita ({_r.returncode}).")
                            st.code((_r.stderr or _r.stdout)[-800:])
                    with _c1.expander('comando', expanded=False):
                        st.code(a['comando'])
            else:
                st.success('Sistema aggiornato.')
            if st.button('Controlla ora'):
                controlla_aggiornamenti(forza=True)
                st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# ── COLONNA CHAT ────────────────────────────────────────────────────────────
# ════════════════════════════════════════════════════════════════════════════
with col_chat:
    st.markdown(tema_zeno.testata(f"archivio cognitivo di {cfg['identita']['nome_utente']}"),
                unsafe_allow_html=True)

    r1, r2 = st.columns([2, 1])
    with r1:
        modalita = st.radio('Modalità', ['ARCHIVIO', 'MAIEUTICA'], horizontal=True)

    # ── L0/L1: inizializzazione sessione MAIEUTICA ────────────────────────
    if modalita == 'MAIEUTICA':
        if st.session_state.get('modalita_corrente') != 'MAIEUTICA':
            st.session_state['maieutica_inizializzata'] = False
        st.session_state['modalita_corrente'] = 'MAIEUTICA'
        inizializza_sessione_maieutica()

        fase_attuale = st.session_state.get('maieutica_fase', 'apertura')
        etichette_fase = {
            'apertura':       ('APERTURA', 'fase-apertura'),
            'approfondimento':('APPROFONDIMENTO', 'fase-approfondimento'),
            'consolidamento': ('CONSOLIDAMENTO', 'fase-consolidamento')
        }
        etichetta, css_class = etichette_fase.get(fase_attuale, ('APERTURA', 'fase-apertura'))
        n_dom    = st.session_state.get('maieutica_domande_count', 0)
        ela_min  = int((time.time() - st.session_state.get('maieutica_inizio_ts', time.time())) / 60)
        mai_cfg  = cfg.get('maieutica', {})
        soglia_d = mai_cfg.get('soglia_domande', 7)
        soglia_t = mai_cfg.get('soglia_minuti', 20)

        col_fase, col_chd = st.columns([3, 1])
        with col_fase:
            st.markdown(
                f'<span class="fase-badge {css_class}">{etichetta}</span> '
                f'<span style="font-size:11px;color:#888;">'
                f'{n_dom}/{soglia_d} domande · {ela_min}/{soglia_t} min'
                f'</span>',
                unsafe_allow_html=True
            )
        with col_chd:
            if st.button('⏹ /chiudi', help='Chiude la sessione e genera la nota',
                         key='btn_chiudi_mai'):
                st.session_state['trigger_chiusura_tipo'] = 'comando_esplicito'
                st.session_state['mostra_popup_chiusura'] = True
                st.rerun()

        agenda     = st.session_state.get('maieutica_agenda', {})
        n_lacune   = len(agenda.get('lacune', []))
        n_archi    = len(agenda.get('archi_dialogici', []))
        n_tensioni = len(agenda.get('tensioni_temi', []))
        if n_lacune + n_archi + n_tensioni > 0:
            st.caption(
                f'Agenda interna: {n_lacune} lacune · {n_archi} archi · {n_tensioni} tensioni'
            )
    else:
        st.session_state['modalita_corrente'] = 'ARCHIVIO'

    # ── L2: Popup di chiusura ─────────────────────────────────────────────
    if st.session_state.get('mostra_popup_chiusura') and modalita == 'MAIEUTICA':
        tipo_trigger = st.session_state.get('trigger_chiusura_tipo', '')
        matura       = verifica_maturita()

        with st.container():
            st.divider()
            if tipo_trigger == 'soglia_domande_apertura':
                st.warning(
                    '⚠️ Il tema si sta rivelando più ampio del previsto. '
                    'Vuoi restringere il fuoco su un aspetto specifico, o continuare l\'esplorazione?'
                )
            elif tipo_trigger == 'soglia_domande':
                st.info(f'✦ Raggiunte {soglia_d} domande. La sessione è {"matura" if matura else "ancora in sviluppo"}.')
            elif tipo_trigger == 'soglia_tempo':
                st.info(f'⏱ Trascorsi {soglia_t} minuti.')
            elif tipo_trigger == 'convergenza_linguistica':
                st.info('✦ Rilevata convergenza nel pensiero.')
            elif tipo_trigger == 'comando_esplicito':
                st.info('Chiusura richiesta.')

            if not matura:
                st.caption(
                    '_La sessione non ha ancora attraversato tutte le fasi o prodotto marcatori EVOLUZIONE. '
                    'Il documento generato potrebbe essere meno ricco._'
                )

            c1, c2, c3 = st.columns(3)
            with c1:
                if st.button('▶ Continua', key='popup_continua'):
                    st.session_state['mostra_popup_chiusura'] = False
                    st.session_state['trigger_chiusura_tipo'] = None
                    st.rerun()
            with c2:
                if st.button('💾 Chiudi e genera nota', key='popup_genera', type='primary'):
                    st.session_state['mostra_popup_chiusura'] = False
                    st.session_state['genera_nota_in_corso']  = True
                    st.rerun()
            with c3:
                if st.button('🗑 Chiudi senza salvare', key='popup_no_salva'):
                    # Se il dialogo ha avuto almeno 2 scambi (L'utente ha risposto),
                    # la lacuna è stata elaborata anche senza nota scritta
                    _h_close = st.session_state.get('history', [])
                    _user_count = sum(1 for m in _h_close if m.get('role') == 'user')
                    if _user_count >= 1:
                        _lid_close = st.session_state.get('_lac_dialogo_attiva', '')
                        if _lid_close:
                            _marca_lacuna_affrontata(cfg, _lid_close)
                        _testo_close = (st.session_state.get('maieutica_agenda', {})
                                        .get('punto_partenza', {}).get('testo', ''))
                        _risolvi_lacuna_aperta(cfg, _testo_close)
                    st.session_state.pop('_lac_dialogo_attiva', None)
                    st.session_state['mostra_popup_chiusura']   = False
                    st.session_state['history']                 = []
                    st.session_state['maieutica_inizializzata'] = False
                    st.rerun()
            st.divider()

    # ── L3: Generazione e anteprima nota ─────────────────────────────────
    if st.session_state.get('genera_nota_in_corso') and modalita == 'MAIEUTICA':
        with st.spinner('Zeno sta scrivendo la nota di riflessione...'):
            nota = genera_nota_maieutica()
            st.session_state['nota_generata']       = nota
            st.session_state['genera_nota_in_corso'] = False
            st.session_state['mostra_nota_preview']  = True
        st.rerun()

    if st.session_state.get('mostra_nota_preview') and modalita == 'MAIEUTICA':
        nota = st.session_state.get('nota_generata', '')
        st.subheader('Nota generata — revisiona prima di salvare')
        nota_editata = st.text_area('Modifica liberamente:', value=nota, height=400,
                                     key='nota_editor')
        c_salva, c_rigenera, c_scarta = st.columns(3)
        with c_salva:
            if st.button('💾 Salva in archivio', key='salva_nota_definitiva', type='primary'):
                path_salvato = salva_nota_maieutica(nota_editata)
                salva_archi_confermati_da_sessione()
                _scrivi_temi_attivi(cfg, st.session_state.get('history', []))
                st.session_state['mostra_nota_preview']    = False
                st.session_state['nota_generata']          = None
                st.session_state['history']                = []
                st.session_state['maieutica_inizializzata'] = False
                st.success(f'Nota salvata. Il Watcher la indicizzerà automaticamente.\n`{path_salvato}`')
                st.rerun()
        with c_rigenera:
            if st.button('↺ Rigenera', key='rigenera_nota'):
                st.session_state['genera_nota_in_corso'] = True
                st.session_state['mostra_nota_preview']  = False
                st.rerun()
        with c_scarta:
            if st.button('🗑 Scarta', key='scarta_nota'):
                st.session_state['mostra_nota_preview'] = False
                st.session_state['nota_generata']       = None
                st.rerun()
        st.stop()

    # ── Widget lacune per la MAIEUTICA (menu a tendina) ────────────────────
    # Unisce l'agenda programmata (lacune_da_dialogo.json, eredità V5) e le
    # lacune vive di lacune_aperte.json: il menu resta visibile finché esiste
    # almeno una lacuna, non solo quando l'agenda V5 aveva voci programmate.
    _lac_agenda = _carica_lacune_agenda(cfg)
    if _lac_agenda:
        opzioni_lac = {l['testo'][:100]: l for l in _lac_agenda}
        _exp_open = (modalita == 'MAIEUTICA' and not st.session_state.get('history'))
        with st.expander(f'💬 {len(_lac_agenda)} lacune in agenda per MAIEUTICA',
                         expanded=_exp_open):
            st.caption(
                'Lacune aperte dell\'archivio e agenda programmata. '
                'In modalità MAIEUTICA seleziona quella da affrontare come punto di partenza.'
            )
            _sel = st.selectbox(
                'Seleziona argomento',
                ['— lascia decidere a Zeno —'] + list(opzioni_lac.keys()),
                key='lac_agenda_sel'
            )
            _lac_sel = opzioni_lac.get(_sel)
            if _lac_sel and modalita == 'MAIEUTICA':
                if st.button('▶ Imposta come punto di partenza', key='lac_agenda_set'):
                    _ag = st.session_state.get('maieutica_agenda', {})
                    # Legge la scheda tema per arricchire il contesto della sessione
                    _scheda = _leggi_scheda_tema_per_chat(_lac_sel.get('tema_fonte', ''), cfg)
                    _ag['punto_partenza'] = {
                        'tipo':              'lacuna',
                        'testo':             _lac_sel.get('testo', ''),
                        'tema_fonte':        _lac_sel.get('tema_fonte', ''),
                        'motivo':            ('lacuna programmata in agenda'
                                              if _lac_sel.get('origine') == 'dialogo'
                                              else con_nome('lacuna aperta scelta da @UTENTE@')),
                        'scheda_cosa_sa':    _scheda.get('cosa_sa', '')[:500],
                        'scheda_come_pensa': _scheda.get('come_pensa', '')[:400],
                        'scheda_tensioni':   _scheda.get('tensioni', '')[:400],
                        'scheda_evoluzione': _scheda.get('evoluzione', '')[:300],
                    }
                    st.session_state['maieutica_agenda']              = _ag
                    # Non-vuoto anche per le lacune senza id (da lacune_aperte.json):
                    # a fine sessione _marca_lacuna_affrontata sarà un no-op ma
                    # _risolvi_lacuna_aperta rimuoverà la voce elaborata.
                    st.session_state['_lac_dialogo_attiva']           = (
                        _lac_sel.get('id', '') or '_lacuna_aperta_')
                    st.session_state['maieutica_avvia_prima_domanda'] = True
                    st.rerun()
            if _lac_sel:
                if st.button("✕ Rimuovi dall'agenda", key='lac_agenda_rm'):
                    if _lac_sel.get('origine') == 'dialogo':
                        _marca_lacuna_affrontata(cfg, _lac_sel.get('id', ''))
                    else:
                        _persisti_lacuna_rifiutata(cfg, _lac_sel.get('testo', ''))
                        _risolvi_lacuna_aperta(cfg, _lac_sel.get('testo', ''))
                    st.rerun()

    # ── Widget connessioni da confermare (V4) ─────────────────────────────
    grafo_path = os.path.join(cfg['percorsi']['stats'], 'grafo_connessioni.json')
    if os.path.exists(grafo_path):
        try:
            grafo = json.load(open(grafo_path))
            archi = grafo.get('archi', [])
            conf_path  = os.path.join(cfg['percorsi']['stats'], 'archi_confermati.json')
            rif_path   = os.path.join(cfg['percorsi']['stats'], 'archi_rifiutati.json')
            confermati = json.load(open(conf_path)) if os.path.exists(conf_path) else []
            rifiutati  = json.load(open(rif_path))  if os.path.exists(rif_path)  else []
            conf_keys  = {tuple(sorted([a['source'], a['target']])) for a in confermati}
            rif_keys   = {tuple(sorted([a['source'], a['target']])) for a in rifiutati}

            da_valutare = [
                a for a in archi
                if a.get('tipo') == 'dialogico'
                and not a.get('confermato')
                and tuple(sorted([a['source'], a['target']])) not in conf_keys
                and tuple(sorted([a['source'], a['target']])) not in rif_keys
            ][:3]

            if da_valutare:
                with st.expander(f'🔗 {len(da_valutare)} connessioni inferite da valutare'):
                    for arco in da_valutare:
                        nodi_grafo = grafo.get('nodi', {})
                        s_label = nodi_grafo.get(arco['source'], {}).get('label', arco['source'])
                        t_label = nodi_grafo.get(arco['target'], {}).get('label', arco['target'])
                        st.markdown(
                            f"**{s_label}** ↔ **{t_label}**  \n"
                            f"_{arco.get('motivo', '')}_"
                        )
                        arc_key = f"{arco['source']}_{arco['target']}"
                        c1, c2 = st.columns(2)
                        with c1:
                            if st.button('Confermo', key=f"conf_{arc_key}"):
                                confermati.append(arco)
                                with open(conf_path, 'w') as f:
                                    json.dump(confermati, f, ensure_ascii=False, indent=2)
                                st.rerun()
                        with c2:
                            if st.button('Non è così', key=f"rif_{arc_key}"):
                                rifiutati.append(arco)
                                with open(rif_path, 'w') as f:
                                    json.dump(rifiutati, f, ensure_ascii=False, indent=2)
                                st.rerun()
        except Exception:
            pass

    st.divider()

    if 'history'         not in st.session_state: st.session_state.history         = []
    if 'lacune_sessione' not in st.session_state: st.session_state.lacune_sessione = []

    if st.session_state.history:
        if st.button('↩ Cancella ultima domanda',
                     help='Rimuove la tua ultima domanda e la risposta di Zeno'):
            h = st.session_state.history
            while h and h[-1]['role'] == 'assistant':
                h.pop()
            while h and h[-1]['role'] == 'user':
                h.pop()
            if modalita == 'MAIEUTICA':
                st.session_state['maieutica_domande_count'] = max(
                    0, st.session_state.get('maieutica_domande_count', 1) - 1)
            log = f"{cfg['percorsi']['memoria']}/query_archiviate/log_corrente.json"
            os.makedirs(os.path.dirname(log), exist_ok=True)   # installazione nuova
            with open(log, 'w') as f:
                json.dump(h, f, ensure_ascii=False, indent=2)
            st.rerun()

    chat_container = st.container(height=520)
    with chat_container:
        for _i_msg, msg in enumerate(st.session_state.history):
            with st.chat_message(msg['role']):
                if msg['role'] == 'assistant' and 'fonti_dett' in msg:
                    _mostra_risposta(msg['content'], msg['fonti_dett'], f'h{_i_msg}')
                    _voto_risposta(_i_msg)
                else:
                    st.write(msg['content'])
                    if msg.get('fonti'):
                        st.markdown(
                            _render_fonti(msg['fonti'], msg.get('fonti_paths')),
                            unsafe_allow_html=True)

    # ── Prima domanda automatica MAIEUTICA ───────────────────────────────────
    # Scatta quando l'utente clicca "Imposta come punto di partenza".
    # Usa .get() per il check e .pop() separato per consumare il flag —
    # evita il problema di short-circuit nella valutazione della condizione.
    if (modalita == 'MAIEUTICA'
            and st.session_state.get('maieutica_avvia_prima_domanda')):
        st.session_state.pop('maieutica_avvia_prima_domanda', None)
        _punto_pp = st.session_state.get('maieutica_agenda', {}).get('punto_partenza', {})
        # Usa tema_fonte come query per il retrieval: recupera chunk rilevanti
        # sul tema completo, non sulla singola frase della lacuna
        _query_rag = _punto_pp.get('tema_fonte') or _punto_pp.get('testo', '')
        engine = get_engine('MAIEUTICA')
        with chat_container:
            with st.chat_message('assistant'):
                with st.spinner('Zeno sta elaborando...'):
                    _r     = engine.query(_query_rag)
                    _fonti, _fonti_paths = _fonti_link(_r.source_nodes)
                    _fonti_dett = _fonti_dettaglio(_r.source_nodes)
                _stream_ph2 = st.empty()
                _gen2 = getattr(_r, 'response_gen', None)
                if _gen2 is not None:
                    _risposta = ''
                    try:
                        for _tok in _gen2:
                            _risposta += _tok
                            _stream_ph2.markdown(_risposta + ' ▌')
                    except Exception:
                        _risposta = _risposta or str(_r)
                    _stream_ph2.empty()
                else:
                    _risposta = str(_r)
                _mostra_risposta(_risposta, _fonti_dett, f'h{len(st.session_state.history)}')
        _mid = uuid.uuid4().hex[:12]
        st.session_state.setdefault('prompt_msg', {})[_mid] = getattr(_r, 'prompt', None)
        st.session_state.history.append(
            {'role': 'assistant', 'content': _risposta, 'fonti': _fonti,
             'fonti_paths': _fonti_paths, 'fonti_dett': _fonti_dett,
             'id': _mid, 'modalita': 'MAIEUTICA', 'modello': getattr(_r, 'modello', None)})
        st.session_state['maieutica_domande_count'] = 1
        aggiorna_fase_maieutica()
        st.rerun()

    # ── Menu comandi rapidi (solo ARCHIVIO) ──────────────────────────────
    if modalita == 'ARCHIVIO':
        with st.expander('✏️ Aggiungi citazione / recensione / riflessione'):
            libri_path_menu = cfg['percorsi']['libri']
            # 'Riflessione su libro' è stata unificata in 'Recensione'
            # (2026-08-11): erano la stessa cosa scritta in due sezioni diverse
            # e con due pesi diversi. 'Riflessione generale' resta: quella non
            # riguarda un libro.
            _op = st.selectbox(
                'Operazione',
                ['Citazione', 'Recensione', 'Sinossi', 'Autore',
                 'Riflessione generale'],
                key='menu_op',
                label_visibility='collapsed'
            )
            if _op != 'Riflessione generale':
                _titolo_menu = st.text_input(
                    'Titolo libro (anche parziale)',
                    key='menu_titolo',
                    placeholder='es. un titolo o un autore'
                )
            _label_testo = {
                'Citazione': 'Citazione da aggiungere',
                'Sinossi':   'Testo sinossi',
                'Autore':    'Testo sezione Autore',
            }.get(_op, 'Testo')
            _testo_menu = st.text_area(
                _label_testo,
                key='menu_testo',
                height=100
            )
            # ── Due tempi: prima si vede il bersaglio, poi si scrive ──────────
            # _cerca_file_libro ripiega su un match parola-per-parola: l'11/08/2026
            # una recensione de "L'arte di correre" (libro non ancora in
            # archivio) è finita in "L'arte di comunicare" di Cicerone, perché
            # "arte" bastava ad agganciarlo — e la scrittura era immediata e
            # silenziosa. Ora il primo pulsante RISOLVE soltanto, mostra su quale
            # file andrebbe a scrivere, e aspetta conferma.
            if st.button('Verifica destinazione', key='menu_verifica'):
                _t = st.session_state.get('menu_testo', '').strip()
                _h = (st.session_state.get('menu_titolo', '').strip()
                      if _op != 'Riflessione generale' else '')
                if not _t:
                    st.warning('Il campo testo è vuoto.')
                elif _op != 'Riflessione generale' and not _h:
                    st.warning('Inserisci almeno una parola del titolo.')
                else:
                    _cand = ([] if _op == 'Riflessione generale'
                             else _cerca_file_libro(_h, libri_path_menu))
                    st.session_state['menu_conferma'] = {
                        'op': _op, 'testo': _t, 'hint': _h,
                        'candidati': _cand,
                    }
                    st.rerun()

            _pending = st.session_state.get('menu_conferma')
            if _pending:
                _op_p, _t_p = _pending['op'], _pending['testo']
                _h_p, _cand = _pending['hint'], _pending['candidati']

                if _op_p == 'Riflessione generale':
                    st.info('Verrà salvata come **riflessione generale** in '
                            '`documents/riflessioni/`, non legata a un libro.')
                elif not _cand:
                    st.error(f'Nessun libro trovato con «{_h_p}». '
                             'Se il libro è nuovo, aggiorna prima Goodreads: '
                             'la scheda non esiste ancora.')
                else:
                    _target = _cand[0][:-3]
                    if len(_cand) > 1:
                        st.warning(f'**{len(_cand)} libri** corrispondono a «{_h_p}». '
                                   f'Verrà scelto il primo:')
                    st.info(f'**{_op_p}** → scheda **{_target}**')
                    if len(_cand) > 1:
                        st.caption('Altri candidati: '
                                   + ', '.join(c[:-3] for c in _cand[1:5]))
                    if _op_p == 'Recensione':
                        st.caption('Verrà scritta anche in `riflessioni/libri/` '
                                   'come strato *esplicito* (pensiero tuo).')

                _c_ok, _c_no = st.columns(2)
                _scrivibile = bool(_cand) or _op_p == 'Riflessione generale'
                if _c_ok.button('✓ Conferma e scrivi', key='menu_conferma_ok',
                                type='primary', disabled=not _scrivibile):
                    if _op_p == 'Citazione':
                        _esito = scrivi_citazione_su_libro(_h_p, _t_p, libri_path_menu)
                    elif _op_p == 'Recensione':
                        _esito = scrivi_recensione_su_libro(_h_p, _t_p, libri_path_menu)
                    elif _op_p == 'Sinossi':
                        _esito = scrivi_sinossi_su_libro(_h_p, _t_p, libri_path_menu)
                    elif _op_p == 'Autore':
                        _esito = scrivi_autore_su_libro(_h_p, _t_p, libri_path_menu)
                    else:
                        _esito = salva_riflessione_generale(_t_p)
                    st.success(_esito)
                    for _k in ('menu_titolo', 'menu_testo', 'menu_conferma'):
                        st.session_state.pop(_k, None)
                    st.rerun()
                if _c_no.button('Annulla', key='menu_conferma_no'):
                    # Testo e titolo restano nei campi: si corregge e si riprova.
                    st.session_state.pop('menu_conferma', None)
                    st.rerun()

    # ── Input utente ──────────────────────────────────────────────────────
    placeholder = '/chiudi per terminare la sessione' if modalita == 'MAIEUTICA' else 'Scrivi qui...'
    if prompt := st.chat_input(placeholder):
        # Marcatore di attività: il timer dei job periodici (sidebar) non lancia
        # nulla se l'utente ha scritto da poco. Un `schede` da 40 minuti che
        # parte a metà conversazione si prende il motore MLX e le risposte rallentano.
        st.session_state['ultima_attivita'] = time.time()
        st.session_state.history.append({'role': 'user', 'content': prompt})
        with chat_container:
            with st.chat_message('user'): st.write(prompt)

        libri_path = cfg['percorsi']['libri']
        tipo_cmd, risultato_cmd = interpreta_comando(prompt, libri_path)

        if tipo_cmd:
            with st.chat_message('assistant'):
                st.success(risultato_cmd)
            st.session_state.history.append(
                {'role': 'assistant', 'content': risultato_cmd, 'fonti': []})

        else:
            engine = get_engine(modalita)
            with chat_container:
                _bolla = st.chat_message('assistant')
                with _bolla:
                    with st.spinner('Zeno sta elaborando...'):
                        r                  = engine.query(prompt)
                        fonti, fonti_paths = _fonti_link(r.source_nodes)
                        fonti_dett         = _fonti_dettaglio(r.source_nodes)
                        scores             = [n.score for n in r.source_nodes if n.score]
                        soglia             = cfg['soglie']['lacuna_similarita']

                        # Feature 2: accumulo score per file (per _scrivi_temi_attivi)
                        for _n in r.source_nodes:
                            if _n.score:
                                _fn = _n.metadata.get('file_name', '?')
                                st.session_state.setdefault('sessione_fonti_scores', {}) \
                                    .setdefault(_fn, []).append(_n.score)

                    # ── Streaming o fallback sincrono ──────────────────────────
                    # Con streaming=True, r.response_gen è un iteratore di token.
                    # Lo mostriamo in tempo reale; poi applichiamo lo stile finale.
                    _stream_ph = st.empty()
                    _gen = getattr(r, 'response_gen', None)
                    if _gen is not None:
                        risposta_str = ''
                        try:
                            for _tok in _gen:
                                risposta_str += _tok
                                _stream_ph.markdown(risposta_str + ' ▌')
                        except Exception:
                            risposta_str = risposta_str or str(r)
                        _stream_ph.empty()   # Pulizia cursore — ri-renderizziamo sotto
                    else:
                        risposta_str = str(r)

                    lacuna_rilevata = ('LACUNA:' in risposta_str or
                                       (scores and max(scores) < soglia))

                    if 'LACUNA:' in risposta_str:
                        testo_lacuna = risposta_str
                        st.session_state.lacune_sessione.append(
                            {'testo': testo_lacuna, 'query': prompt})
                        # Feature 1: persistenza su file
                        fonte_lac = ('sessione_maieutica'
                                     if modalita == 'MAIEUTICA' else 'sessione_archivio')
                        _scrivi_lacuna_su_file(cfg, testo_lacuna, prompt, fonte_lac, fonti)
                    elif scores and max(scores) < soglia:
                        avviso = (f'Attenzione: bassa corrispondenza '
                                  f'(score {max(scores):.2f}). Risposta basata su archivio parziale.')
                        st.session_state.lacune_sessione.append({'testo': avviso, 'query': prompt})
                        fonte_lac = ('sessione_maieutica'
                                     if modalita == 'MAIEUTICA' else 'sessione_archivio')
                        _scrivi_lacuna_su_file(cfg, avviso, prompt, fonte_lac, fonti)
                        st.markdown(f'<div class="avviso">{avviso}</div>', unsafe_allow_html=True)

                    # Prima la risposta intera finiva in un riquadro giallo a
                    # 11 px appena conteneva "LACUNA:": ora si legge come le
                    # altre e solo il paragrafo della lacuna è evidenziato.
                    _mostra_risposta(risposta_str, fonti_dett,
                                     f'h{len(st.session_state.history)}')

            _mid = uuid.uuid4().hex[:12]
            st.session_state.setdefault('prompt_msg', {})[_mid] = getattr(r, 'prompt', None)
            st.session_state.history.append(
                {'role': 'assistant', 'content': risposta_str, 'fonti': fonti,
                 'fonti_paths': fonti_paths, 'fonti_dett': fonti_dett,
                 'id': _mid, 'modalita': modalita, 'modello': getattr(r, 'modello', None)})
            with _bolla:
                _voto_risposta(len(st.session_state.history) - 1)

            # ── Rilevamento segnali feedback (ARCHIVIO e MAIEUTICA) ───────────
            # Errore esplicito: "non c'entra", "hai frainteso", ecc.
            _e_errore, _tipo_errore = _rileva_segnale_errore(prompt)
            if _e_errore:
                # Recupera la risposta precedente di Zeno come contesto
                _storia = st.session_state.history
                _ris_prec = next(
                    (m['content'] for m in reversed(_storia[:-1])
                     if m.get('role') == 'assistant'), '')
                _persisti_correzione_dialogo(cfg, prompt, _ris_prec, _tipo_errore)
                st.session_state.setdefault('_segnali_errore_sessione', []).append(
                    {'prompt': prompt[:200], 'tipo': _tipo_errore})
                # Toast inline: Zeno riconosce il segnale di errore
                with chat_container:
                    st.warning(
                        f'⚠ Segnale "{_tipo_errore}" registrato — '
                        f'la correzione è stata salvata per calibrare le inferenze future.',
                        icon=None
                    )

            # Connessione emergente: "questo mi ricorda X", "è simile a Y"
            _e_conn, _testo_conn = _rileva_connessione_emergente(prompt)
            if _e_conn:
                _nuova = _proponi_connessione_emergente(cfg, _testo_conn, prompt)
                if _nuova:
                    st.session_state.setdefault('_connessioni_emerse_sessione', []).append(
                        _testo_conn)
                    # Mostra un toast inline nella chat: Zeno riconosce la connessione
                    with chat_container:
                        st.info(
                            f'🔗 Connessione annotata: _"{_testo_conn}"_ — '
                            f'salvata nel grafo cognitivo. Puoi rimuoverla dal pannello Errori e Lacune.',
                            icon=None
                        )

            # ── Aggiornamenti stato MAIEUTICA post-risposta ───────────────
            if modalita == 'MAIEUTICA':
                st.session_state['maieutica_domande_count'] = \
                    st.session_state.get('maieutica_domande_count', 0) + 1

                st.session_state['maieutica_fonti_sessione'] = list(set(
                    st.session_state.get('maieutica_fonti_sessione', []) + fonti
                ))

                for m in re.finditer(r'EVOLUZIONE:\s*([^\n]+)', risposta_str):
                    evol = m.group(1).strip()
                    if evol not in st.session_state.get('maieutica_evoluzioni', []):
                        st.session_state.setdefault('maieutica_evoluzioni', []).append(evol)
                        aggiorna_profilo_da_sessione(evol)

                tensioni_keywords = cfg.get('maieutica', {}).get('keywords_tensione', [
                    'non so se', 'ma d\'altra parte', 'eppure', 'però allo stesso tempo',
                    'mi contraddico', 'non sono sicuro', 'forse entrambe', 'in realtà no'
                ])
                prompt_lower = prompt.lower()
                for kw in tensioni_keywords:
                    if kw in prompt_lower and len(prompt) > 60:
                        tensione_breve = prompt[:120]
                        if tensione_breve not in st.session_state.get('maieutica_tensioni', []):
                            st.session_state.setdefault('maieutica_tensioni', []).append(tensione_breve)
                        break

                registra_arco_implicito(prompt)
                aggiorna_fase_maieutica()

                trigger, tipo_trigger = verifica_trigger_chiusura(risposta_str, prompt)
                if trigger:
                    st.session_state['mostra_popup_chiusura'] = True
                    st.session_state['trigger_chiusura_tipo'] = tipo_trigger
                    st.rerun()

            log = f"{cfg['percorsi']['memoria']}/query_archiviate/log_corrente.json"
            os.makedirs(os.path.dirname(log), exist_ok=True)   # installazione nuova
            with open(log, 'w') as f:
                json.dump(st.session_state.history, f, ensure_ascii=False, indent=2)
