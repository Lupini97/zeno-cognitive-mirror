#!/usr/bin/env python3
# zeno_window.py
"""
Passaggio della finestra di Zeno dallo splash alla chat (PyWebView).
Naviga la finestra GIÀ APERTA da zeno_splash.py verso Streamlit chat (:8501)
e inietta una tab bar persistente che permette di navigare verso la
dashboard (:8502) senza aprire il browser.

La tab bar viene re-iniettata ad ogni caricamento di pagina via evento `loaded`,
così sopravvive alle navigazioni tra :8501 e :8502.

Non crea una finestra propria e non chiama webview.start(): su macOS un
secondo giro del loop Cocoa nello stesso processo (un secondo webview.start())
non torna in modo affidabile quando la prima finestra si chiude — è la causa
del blocco allo splash osservato l'19/08/2026 (l'app restava "non risponde"
finché il Dock non la terminava a forza, con Ollama/Streamlit orfani sullo
sfondo). Un'unica finestra per tutto il ciclo di vita elimina il problema.

Chiamato da zeno_splash.py — non eseguire direttamente.

Dipendenze: pywebview
"""

import threading
import time
from pathlib import Path
import webview   # pip install pywebview


# ── Porte ────────────────────────────────────────────────────────────────────

URL_CHAT      = 'http://localhost:8501'
URL_DASHBOARD = 'http://localhost:8502'


# ── Tab bar — JavaScript iniettato in ogni pagina Streamlit ──────────────────
#
# Approccio: position:fixed overlay, z-index altissimo, re-iniettato a ogni
# page load. Streamlit riceve le navigazioni come normali cambi di URL nella
# stessa finestra → nessun problema di X-Frame-Options o CORS.
#
# Il colore di sfondo di Streamlit in dark mode (#0E1117) è volutamente vicino
# al nostro BG (#0D1B2A) per un'integrazione visiva coerente.

_TAB_JS = """
(function () {
    var BAR_ID = 'zeno-tab-bar';
    if (document.getElementById(BAR_ID)) return;  // già presente

    var isChat = window.location.href.indexOf(':8501') !== -1;

    /* ── Stili ─────────────────────────────────────────────────── */
    var css = [
        '#' + BAR_ID + ' {',
        '  position: fixed; top: 0; left: 0; right: 0; height: 44px;',
        '  background: #1E3A5F;',
        '  border-bottom: 1px solid #2E4F7A;',
        '  display: flex; align-items: center;',
        '  padding: 0 14px; gap: 6px;',
        '  z-index: 2147483647;',
        '  font-family: -apple-system, "Segoe UI", sans-serif;',
        '  -webkit-app-region: drag;',
        '}',

        '#' + BAR_ID + ' .z-logo {',
        '  color: #FFFFFF; font-size: 13px; font-weight: 700;',
        '  letter-spacing: 0.8px; margin-right: 10px;',
        '  -webkit-app-region: no-drag;',
        '}',

        '#' + BAR_ID + ' .z-tab {',
        '  background: transparent; border: none;',
        '  color: rgba(255,255,255,0.55);',
        '  padding: 5px 15px; border-radius: 5px;',
        '  font-size: 12.5px; cursor: pointer;',
        '  transition: background 0.15s, color 0.15s;',
        '  -webkit-app-region: no-drag;',
        '}',

        '#' + BAR_ID + ' .z-tab:hover {',
        '  background: rgba(46,117,182,0.35);',
        '  color: rgba(255,255,255,0.85);',
        '}',

        '#' + BAR_ID + ' .z-tab.z-active {',
        '  background: #2E75B6; color: #FFFFFF;',
        '}',

        '[data-testid="stAppViewContainer"] > div:first-child {',
        '  padding-top: 44px !important;',
        '}',

        '#root > div:first-child {',
        '  margin-top: 44px;',
        '}'
    ].join('\\n');

    var style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);

    /* ── HTML barra ────────────────────────────────────────────── */
    var bar = document.createElement('div');
    bar.id = BAR_ID;
    bar.innerHTML = [
        '<span class="z-logo">&#x1F9E0;&nbsp;&nbsp;ZENO</span>',
        '<button class="z-tab ' + (isChat  ? 'z-active' : '') + '" ',
        '        onclick="window.location.href=\\'http://localhost:8501\\'">',
        '  Chat</button>',
        '<button class="z-tab ' + (!isChat ? 'z-active' : '') + '" ',
        '        onclick="window.location.href=\\'http://localhost:8502\\'">',
        '  Dashboard</button>'
    ].join('');

    if (document.body) {
        document.body.insertBefore(bar, document.body.firstChild);
    } else {
        document.addEventListener('DOMContentLoaded', function () {
            document.body.insertBefore(bar, document.body.firstChild);
        });
    }
})();
"""


# ── Iniezione ritardata (attende che Streamlit carichi il DOM) ────────────────

def _inietta_tab(window: webview.Window, ritardo: float = 0.8):
    """Aspetta ritardo secondi poi inietta la tab bar."""
    time.sleep(ritardo)
    try:
        window.evaluate_js(_TAB_JS)
    except Exception:
        pass


# ── API pubblica ──────────────────────────────────────────────────────────────

def collega_finestra(window: webview.Window, on_close=None):
    """
    Collega la finestra ESISTENTE (creata da zeno_splash.py) al flusso
    principale: registra l'iniezione della tab bar e la chiusura, poi naviga
    verso la chat. NON crea finestre e NON chiama webview.start() — quel
    ciclo gira già, avviato da zeno_splash.run().

    on_close: callable opzionale invocato DOPO la chiusura della finestra.
    """

    # Re-inietta la tab bar ad ogni caricamento di pagina (chat/dashboard)
    def on_loaded():
        threading.Thread(
            target=_inietta_tab,
            args=(window,),
            daemon=True
        ).start()

    window.events.loaded += on_loaded

    # Registra callback di chiusura
    if on_close:
        window.events.closed += on_close

    # Icona Dock personalizzata + visibilità nel Dock (macOS)
    try:
        import AppKit
        from interfaccia import LOGO
        logo = Path(LOGO)
        if logo.exists():
            img = AppKit.NSImage.alloc().initWithContentsOfFile_(str(logo))
            AppKit.NSApp.setApplicationIconImage_(img)
        AppKit.NSApp.setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyRegular
        )
        AppKit.NSApp.activateIgnoringOtherApps_(True)
    except Exception:
        pass

    window.load_url(URL_CHAT)
