#!/usr/bin/env python3
# zeno_splash.py
"""
Splash screen di avvio per Zeno — E finestra principale.
Mostra logo, barra di progresso e stati del boot; a fine boot la STESSA
finestra viene navigata sulla chat (vedi vai_a_chat(), zeno_window.py) invece
di essere distrutta per aprirne una seconda.

Perché una sola finestra: su macOS pywebview avvia il loop Cocoa con
NSApplication.run() all'interno del primo webview.start() del processo;
quando l'ultima finestra si chiude viene chiamato NSApp.stop_(), ma è una
limitazione nota di Cocoa/PyObjC che stop_() da solo non è garantito
svegliare il loop — serve un evento successivo. Un secondo webview.start()
nello stesso processo (per la finestra "vera", dopo aver chiuso lo splash)
poteva quindi non tornare mai: l'app restava "non risponde" finché il Dock
non la terminava a forza (osservato il 19/08/2026, ~2 minuti dopo l'avvio,
con Ollama/Streamlit rimasti orfani sullo sfondo). Un'unica finestra per
tutto il ciclo di vita, con un solo webview.start(), elimina il problema.

Chiamato da zeno_launcher.py — non eseguire direttamente.

Dipendenze: pywebview (già richiesto dal launcher), nessun tkinter.
"""

import threading
import time
import webview
from pathlib import Path


# ── HTML / CSS della splash ───────────────────────────────────────────────────

_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: #0D1B2A;
    color: #FFFFFF;
    font-family: -apple-system, 'SF Pro Display', 'Segoe UI', sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 100vh;
    user-select: none;
    -webkit-app-region: drag;
  }

  #logo {
    width: 120px;
    height: 120px;
    border-radius: 24px;
    margin-bottom: 14px;
  }

  #title {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 3px;
    color: #FFFFFF;
    margin-bottom: 4px;
  }

  #subtitle {
    font-size: 11px;
    color: #5A8FC0;
    margin-bottom: 28px;
    letter-spacing: 0.3px;
  }

  #separator {
    width: 340px;
    height: 1px;
    background: #1E3A5F;
    margin-bottom: 20px;
  }

  #status {
    font-size: 11px;
    color: #8AB4D4;
    margin-bottom: 12px;
    height: 16px;
    transition: opacity 0.2s;
  }

  #bar-track {
    width: 340px;
    height: 5px;
    background: #1A2E47;
    border-radius: 3px;
    overflow: hidden;
  }

  #bar-fill {
    height: 100%;
    width: 0%;
    background: #2E75B6;
    border-radius: 3px;
    transition: width 0.35s ease;
  }

  #version {
    position: absolute;
    bottom: 14px;
    font-size: 11px;
    color: #5A8FC0;
    letter-spacing: 0.5px;
  }
</style>
</head>
<body>
  <img id="logo" src="__LOGO__" alt="">
  <div id="title">ZENO</div>
  <div id="subtitle">Specchio cognitivo personale</div>
  <div id="separator"></div>
  <div id="status">Inizializzazione…</div>
  <div id="bar-track"><div id="bar-fill"></div></div>
  <div id="version">Versione __VERSIONE__</div>

  <script>
    function setStatus(testo, progresso) {
      document.getElementById('status').textContent   = testo;
      document.getElementById('bar-fill').style.width = progresso + '%';
    }
  </script>
</body>
</html>
"""


def _html() -> str:
    """La pagina della splash con il logo (incorporato in base64: la pagina
    non ha un indirizzo da cui caricare file) e la versione del codice."""
    import base64
    from interfaccia import LOGO
    from zeno_engine import __version__
    try:
        with open(LOGO, 'rb') as f:
            logo = 'data:image/png;base64,' + base64.b64encode(f.read()).decode('ascii')
    except OSError:
        logo = ''
    return _HTML.replace('__LOGO__', logo).replace('__VERSIONE__', __version__)


# ── Classe splash ─────────────────────────────────────────────────────────────

class ZenoSplash:
    """
    Finestra splash basata su PyWebView — nessuna dipendenza da tkinter.
    È la STESSA finestra che poi ospita la chat (vedi vai_a_chat()):
    dimensioni e cornice sono già quelle della finestra principale.

    Uso:
        splash = ZenoSplash(brain_path)
        splash.run(boot_fn)   # blocca main thread; boot_fn gira in thread daemon
        # boot_fn chiama:
        #   splash.set_status(testo, 0-100)
        #   splash.vai_a_chat(on_close=...)   # a fine boot, al posto di close()
    """

    W, H = 1280, 820

    def __init__(self, brain: Path):
        self.brain   = brain
        self._window = None
        self._ready  = threading.Event()   # segnalato quando il DOM è pronto

    # ── API pubblica ──────────────────────────────────────────────────────────

    def set_status(self, testo: str, progresso: int):
        """Thread-safe: aggiorna testo e barra di progresso."""
        self._ready.wait(timeout=10)
        if self._window:
            js = f"setStatus({repr(testo)}, {progresso});"
            try:
                self._window.evaluate_js(js)
            except Exception:
                pass

    def vai_a_chat(self, on_close=None):
        """
        Chiamata a fine boot al posto di close(): NON distrugge la finestra
        (eviterebbe un secondo webview.start(), vedi nota in testa al file),
        la naviga sulla chat tramite zeno_window.collega_finestra().
        """
        if self._window:
            from interfaccia.zeno_window import collega_finestra
            collega_finestra(self._window, on_close=on_close)

    def close(self):
        """Thread-safe: chiude la finestra a forza (errori fatali pre-boot).
        Per la normale fine del boot usare vai_a_chat(), non questo metodo."""
        if self._window:
            try:
                self._window.destroy()
            except Exception:
                pass

    def run(self, boot_fn):
        """
        Avvia boot_fn in un thread daemon, poi entra nel loop PyWebView.
        Unico webview.start() del processo: resta bloccato per tutta la vita
        dell'app (splash + chat + dashboard sono la stessa finestra).
        """
        self._window = webview.create_window(
            title            = 'Zeno',
            html             = _html(),
            width            = self.W,
            height           = self.H,
            min_size         = (960, 600),
            background_color = '#0D1B2A',
            text_select      = True,
            zoomable         = True,
        )

        _avviato = threading.Event()   # guardia: boot_fn parte una volta sola

        def on_loaded():
            self._ready.set()
            # L'evento `loaded` si ripete a ogni navigazione (vai_a_chat()
            # naviga la STESSA finestra sulla chat, poi la tab bar naviga
            # avanti e indietro chat/dashboard): senza guardia, ogni
            # ricaricamento di pagina rilanciava l'intero boot da capo
            # (avvia_servizi + zeno sync), uccidendo e riavviando Streamlit
            # in loop — bug introdotto passando a finestra singola, scoperto
            # il 19/08/2026 durante il collaudo del fix.
            if _avviato.is_set():
                return
            _avviato.set()
            threading.Thread(
                target=boot_fn,
                args=(self,),
                daemon=True
            ).start()

        self._window.events.loaded += on_loaded

        # webview.start() blocca fino alla chiusura della finestra
        webview.start(debug=False)
