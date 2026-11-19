# profile.py
"""
Gestione del profilo cognitivo narrativo di Zeno.

Cosa fa questo file:
  - Funzioni runtime chiamate da chat_ui.py e mbti.py durante l'uso normale.
  - Modalità --estendi: aggiunge il profilo esteso (10 domande approfondite)
    al profilo base già creato dall'installer.

Cosa NON fa più:
  - Onboarding base (7 domande): gestito da zeno_installer.py al primo avvio.
    Se vuoi rifare il profilo base, esegui di nuovo zeno_installer.py.

Uso:
    python profile.py --estendi        # aggiunge profilo esteso interattivo
    python profile.py --estendi-gui    # stessa cosa con interfaccia grafica
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ── sys.path dinamico ─────────────────────────────────────────────────────────
_scripts = Path(__file__).resolve().parent.parent   # zeno_engine → scripts
if str(_scripts) not in sys.path:
    sys.path.insert(0, str(_scripts))


# ── Percorso profilo ──────────────────────────────────────────────────────────

def _profilo_path() -> str:
    """
    Risolve il percorso del file .md del profilo cognitivo.
    Usa il nome utente da zeno_config.yaml; fallback su 'utente'.
    """
    try:
        from zeno_engine.config import carica_config
        cfg  = carica_config()
        nome = cfg.get('identita', {}).get('nome_utente', 'utente').lower()
        return os.path.join(cfg['percorsi']['memoria'], 'profilo', f'profilo_{nome}.md')
    except Exception:
        # Fallback dinamico — usa il parent di scripts/
        brain = _scripts.parent
        return str(brain / 'memoria_elaborata' / 'profilo' / 'profilo_utente.md')


# ── Domande profilo esteso ────────────────────────────────────────────────────
# Le DOMANDE_ONBOARDING (profilo base) sono in zeno_installer.py.
# Qui risiedono solo le DOMANDE_ESTESE usate da --estendi.

DOMANDE_ESTESE = [
    ('posizioni_ferme',
     'Su quali temi hai una posizione consolidata che difficilmente cambieresti?'),
    ('temi_in_ricerca',
     'Su quali temi sei ancora in esplorazione — non hai ancora una posizione definita?'),
    ('autori_superati',
     'Ci sono autori o correnti di pensiero che hai studiato ma consapevolmente superato o rifiutato? Perché?'),
    ('autori_interiorizzati',
     'Quali autori o idee hai così assimilato da non citarli più esplicitamente?'),
    ('lacune_percepite',
     'Dove senti le tue lacune più grandi — aree che sai di non conoscere abbastanza?'),
    ('connessioni_personali',
     'Quali connessioni tra i tuoi campi di interesse percepisci tu ma raramente vedi riconosciute dagli altri?'),
    ('tipo_psicologico',
     'Hai fatto analisi del tuo tipo psicologico (MBTI, enneagramma, Big Five)? Cosa è emerso?'),
    ('meta_cognizione',
     "Come descrivi il tuo rapporto con l'errore e con il cambiare idea?"),
    ('domande_irrisolte',
     'Quali sono le domande a cui non hai ancora trovato risposta e che senti come centrali?'),
    ('zeno_aspettative',
     'Cosa ti aspetti da Zeno che un libro, un corso o un interlocutore umano non ti hanno mai dato?'),
]


# ══ Funzioni runtime (chiamate da chat_ui.py e mbti.py) ══════════════════════

def carica_profilo() -> str:
    """Legge e restituisce il contenuto del profilo .md. Stringa vuota se assente."""
    p = _profilo_path()
    if not os.path.exists(p):
        return ''
    return open(p, encoding='utf-8').read()


def aggiorna_profilo_da_sessione(insight: str) -> None:
    """
    Appende un aggiornamento testuale libero al profilo.
    Chiamata da chat_ui.py al termine di sessioni significative.
    """
    p = _profilo_path()
    if not os.path.exists(p):
        return
    ts = datetime.now().strftime('%Y-%m-%d')
    aggiunta = f'\n### Aggiornamento dinamico {ts}\n{insight}\n'
    with open(p, 'a', encoding='utf-8') as f:
        f.write(aggiunta)


def aggiorna_profilo_da_mbti(variazioni: dict, soglia_delta: float = 0.15) -> bool:
    """
    Chiamata da mbti.py dopo ogni run con variazioni significative.
    Appende una sezione strutturata con le funzioni modificate, ma solo se
    almeno una variazione supera soglia_delta (default 0.15).

    variazioni: {funzione: {'da': float, 'a': float, 'delta': float}}
    Restituisce True se ha scritto qualcosa.
    """
    p = _profilo_path()
    if not os.path.exists(p):
        return False

    rilevanti = {f: v for f, v in variazioni.items()
                 if abs(v.get('delta', 0)) >= soglia_delta}
    if not rilevanti:
        return False

    ts = datetime.now().strftime('%Y-%m-%d')
    righe = []
    for funzione, v in sorted(rilevanti.items(), key=lambda x: -abs(x[1]['delta'])):
        direzione = '↑' if v['delta'] > 0 else '↓'
        righe.append(
            f"- **{funzione}**: {v['da']} → {v['a']} "
            f"({direzione}{abs(v['delta']):.3f}) "
            f"— rilevato da mbti.py (proxy lessicale)"
        )

    sezione = (
        f'\n### Calibrazione cognitiva automatica {ts}\n'
        f'*Origine: mbti.py — variazioni ≥ {soglia_delta}*\n\n'
        + '\n'.join(righe) + '\n'
    )
    with open(p, 'a', encoding='utf-8') as f:
        f.write(sezione)
    return True


def carica_storia_mbti() -> list:
    """
    Legge stats/mbti.log e restituisce la storia delle calibrazioni
    come lista di dict ordinata cronologicamente.
    """
    try:
        from zeno_engine.config import carica_config
        cfg      = carica_config()
        log_path = os.path.join(cfg['percorsi']['stats'], 'mbti.log')
    except Exception:
        # Fallback dinamico
        log_path = str(_scripts.parent / 'stats' / 'mbti.log')

    if not os.path.exists(log_path):
        return []

    storia = []
    pat_ts  = re.compile(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]')
    pat_var = re.compile(r'(\w+):([\d.]+)→([\d.]+)\(([+-][\d.]+)\)')

    for riga in open(log_path, encoding='utf-8'):
        riga = riga.strip()
        if not riga:
            continue
        m_ts = pat_ts.match(riga)
        if not m_ts:
            continue
        variazioni = {}
        for m in pat_var.finditer(riga):
            variazioni[m.group(1)] = {
                'da':    float(m.group(2)),
                'a':     float(m.group(3)),
                'delta': float(m.group(4)),
            }
        storia.append({'timestamp': m_ts.group(1), 'variazioni': variazioni})

    return storia


# ══ Funzione --estendi ════════════════════════════════════════════════════════

def estendi_profilo(risposte_estese: dict) -> None:
    """
    Appende il profilo esteso al file .md del profilo base.
    Chiamata dall'interfaccia CLI o GUI di --estendi.
    """
    p = _profilo_path()
    if not os.path.exists(p):
        raise FileNotFoundError(
            f'Profilo base non trovato: {p}\n'
            'Esegui prima zeno_installer.py per creare il profilo base.')

    ts = datetime.now().strftime('%Y-%m-%d')
    aggiunta = f'\n---\n\n## Profilo esteso\n\n*Aggiunto: {ts}*\n\n'
    for chiave, _ in DOMANDE_ESTESE:
        label  = chiave.replace('_', ' ').title()
        valore = risposte_estese.get(chiave, '_non compilato_')
        aggiunta += f'### {label}\n{valore}\n\n'

    with open(p, 'a', encoding='utf-8') as f:
        f.write(aggiunta)


# ══ Interfaccia --estendi GUI (Tkinter) ════════════════════════════════════════

def _estendi_gui() -> None:
    """Wizard Tkinter per --estendi. Stessa palette dell'installer."""
    import tkinter as tk
    from tkinter import ttk, messagebox

    BG, BG2, BG3   = '#0D1B2A', '#122436', '#1A3050'
    BORDER, ACCENT = '#1E3A5F', '#2E75B6'
    TEXT, TEXT_DIM = '#FFFFFF', '#7A9CBD'
    SUCCESS        = '#2ECC71'
    FACE = 'SF Pro Display' if sys.platform == 'darwin' else 'Segoe UI'

    profilo_path = _profilo_path()
    if not os.path.exists(profilo_path):
        root = tk.Tk(); root.withdraw()
        messagebox.showerror(
            'Profilo non trovato',
            f'Profilo base non trovato:\n{profilo_path}\n\n'
            'Esegui prima zeno_installer.py.')
        return

    contenuto = open(profilo_path, encoding='utf-8').read()
    if '## Profilo esteso' in contenuto:
        root = tk.Tk(); root.withdraw()
        if not messagebox.askyesno(
                'Profilo esteso già presente',
                'Il profilo esteso è già presente.\n'
                'Vuoi aggiungere un ulteriore aggiornamento?'):
            return
        root.destroy()

    risposte: dict = {}
    idx = [0]

    root = tk.Tk()
    root.title('Zeno — Profilo esteso')
    root.configure(bg=BG)
    root.resizable(False, False)
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f'700x480+{(sw-700)//2}+{(sh-480)//2}')

    # Header
    tk.Frame(root, bg=ACCENT, height=3).pack(fill='x')
    hdr = tk.Frame(root, bg=BG, pady=12)
    hdr.pack(fill='x', padx=24)
    tk.Label(hdr, text='🧠  ZENO — Profilo esteso',
             font=(FACE, 15, 'bold'), bg=BG, fg=TEXT).pack(anchor='w')
    tk.Label(hdr, text='Domande approfondite sulla tua storia intellettuale.',
             font=(FACE, 10), bg=BG, fg=TEXT_DIM).pack(anchor='w')

    # Barra
    bar = ttk.Progressbar(root, length=652, maximum=len(DOMANDE_ESTESE),
                          mode='determinate')
    bar.pack(padx=24, pady=(0, 6))

    # Body
    body = tk.Frame(root, bg=BG)
    body.pack(fill='both', expand=True, padx=24)

    hdr_lbl = tk.Label(body, text='', font=(FACE, 10), bg=BG, fg=TEXT_DIM)
    hdr_lbl.pack(anchor='w')

    dom_lbl = tk.Label(body, text='', font=(FACE, 12), bg=BG, fg=TEXT,
                       wraplength=640, justify='left')
    dom_lbl.pack(anchor='w', pady=(8, 4))

    txt = tk.Text(body, height=6, width=72, font=(FACE, 11),
                  bg=BG3, fg=TEXT, insertbackground=TEXT, relief='flat',
                  highlightthickness=1, highlightbackground=BORDER,
                  highlightcolor=ACCENT, wrap='word')
    txt.pack(anchor='w')

    msg = tk.Label(root, text='', font=(FACE, 10), bg=BG, fg=TEXT_DIM)
    msg.pack(pady=(4, 0))

    # Footer
    foot = tk.Frame(root, bg='#0A1520', pady=10)
    foot.pack(fill='x', side='bottom')
    inner_foot = tk.Frame(foot, bg='#0A1520')
    inner_foot.pack(padx=24)

    def aggiorna(q: int):
        _, domanda = DOMANDE_ESTESE[q]
        hdr_lbl.configure(text=f'Domanda {q+1} di {len(DOMANDE_ESTESE)}  (opzionale — puoi lasciare vuoto)')
        dom_lbl.configure(text=domanda)
        txt.delete('1.0', 'end')
        chiave = DOMANDE_ESTESE[q][0]
        if chiave in risposte:
            txt.insert('1.0', risposte[chiave])
        bar.configure(value=q)

    def avanti():
        chiave = DOMANDE_ESTESE[idx[0]][0]
        risposte[chiave] = txt.get('1.0', 'end').strip()
        idx[0] += 1
        if idx[0] < len(DOMANDE_ESTESE):
            aggiorna(idx[0])
            if idx[0] == len(DOMANDE_ESTESE) - 1:
                btn_avanti.configure(text='Salva →')
        else:
            # Salvataggio
            try:
                estendi_profilo(risposte)
                msg.configure(text='✓ Profilo esteso salvato.', fg=SUCCESS)
                btn_avanti.configure(state='disabled')
                root.after(1800, root.destroy)
            except Exception as e:
                msg.configure(text=f'✗ Errore: {e}', fg='#E74C3C')

    def indietro():
        chiave = DOMANDE_ESTESE[idx[0]][0]
        risposte[chiave] = txt.get('1.0', 'end').strip()
        if idx[0] > 0:
            idx[0] -= 1
            aggiorna(idx[0])
            btn_avanti.configure(text='Avanti →')

    btn_indietro = tk.Button(inner_foot, text='← Indietro', command=indietro,
                             font=(FACE, 11), bg=BG3, fg=TEXT,
                             activebackground='#22405E', activeforeground=TEXT,
                             relief='flat', bd=0, padx=16, pady=8,
                             cursor='hand2', width=12)
    btn_indietro.pack(side='left', padx=(0, 8))

    btn_avanti = tk.Button(inner_foot, text='Avanti →', command=avanti,
                           font=(FACE, 11), bg=ACCENT, fg=TEXT,
                           activebackground='#3A8FD4', activeforeground=TEXT,
                           relief='flat', bd=0, padx=16, pady=8,
                           cursor='hand2', width=12)
    btn_avanti.pack(side='left')

    aggiorna(0)
    root.mainloop()


# ══ Interfaccia --estendi CLI ═════════════════════════════════════════════════

def _estendi_cli() -> None:
    """Modalità terminale per --estendi (fallback se Tkinter non disponibile)."""
    profilo_path = _profilo_path()
    print('\n' + '=' * 60)
    print('ZENO — Estensione profilo cognitivo')
    print('=' * 60 + '\n')

    if not os.path.exists(profilo_path):
        print(f'✗ Profilo base non trovato: {profilo_path}')
        print('  Esegui prima zeno_installer.py.')
        sys.exit(1)

    contenuto = open(profilo_path, encoding='utf-8').read()
    if '## Profilo esteso' in contenuto:
        print('Il profilo esteso è già presente.')
        r = input('Vuoi aggiungere un ulteriore aggiornamento? (s/n): ').strip().lower()
        if r != 's':
            print('Nessuna modifica.')
            sys.exit(0)

    print('Rispondi liberamente. Puoi lasciare vuoto qualsiasi campo.\n')
    risposte = {}
    for chiave, domanda in DOMANDE_ESTESE:
        print(f'>> {domanda}')
        risposte[chiave] = input('   Risposta: ').strip()
        print()

    estendi_profilo(risposte)
    print(f'\n✓ Profilo esteso salvato: {profilo_path}')


# ══ Entry point ═══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    if '--estendi' not in sys.argv and '--estendi-gui' not in sys.argv:
        print('\nprofile.py — Gestione profilo cognitivo Zeno')
        print()
        print('  Il profilo base viene creato dall\'installer (zeno_installer.py).')
        print('  Per aggiungere il profilo esteso (10 domande approfondite):')
        print()
        print('    python profile.py --estendi        # interfaccia grafica')
        print('    python profile.py --estendi-gui    # interfaccia grafica (esplicito)')
        print()
        sys.exit(0)

    if '--estendi-gui' in sys.argv or '--estendi' in sys.argv:
        try:
            import tkinter
            _estendi_gui()
        except ImportError:
            print('[!] Tkinter non disponibile — uso interfaccia CLI')
            _estendi_cli()
