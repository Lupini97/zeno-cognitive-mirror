# zeno_app.py — punto d'ingresso unico della UI V6 (richiesta l'utente
# 2026-07-14: la dashboard come SECONDO PANNELLO nella stessa finestra
# della chat, non come finestra separata).
#
# App multipagina Streamlit: Chat e Dashboard convivono su :8501 con lo
# switcher in sidebar. chat_ui.py e dashboard.py restano INTATTI e
# avviabili anche da soli (il loro set_page_config è protetto: quando
# girano come pagina lo fa già questo entry point).

import os

import streamlit as st

from interfaccia import LOGO

st.set_page_config(page_title='Zeno', page_icon=LOGO if os.path.exists(LOGO) else 'Z',
                   layout='wide')

pagine = st.navigation([
    st.Page('chat_ui.py', title='Chat', icon=':material/chat:', default=True),
    st.Page('dashboard.py', title='Dashboard', icon=':material/monitoring:'),
])
pagine.run()
