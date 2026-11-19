# Stato dei processi batch di lunga durata (Archivista, Indicizzatore...) per
# il pannello "Processi Zeno" già esistente in chat_ui.py, che legge
# stats/processi_attivi.json — stesso file, stesso schema del watcher (vedi
# watcher.py::_aggiorna_stato_processo), così un solo pannello mostra tutto.
#
# Nato il 2026-07-15: prima il batch Archivista scriveva solo nel log file,
# invisibile dall'app ("non vedo l'avanzamento" — L'utente). Scrittura atomica
# (.tmp + os.replace) per non far leggere a chat_ui.py un json a metà.

import json
import os
from datetime import datetime
from zeno_engine.config import BRAIN

STATS_DIR = os.path.join(BRAIN, 'stats')


def aggiorna(nome: str, dati: dict, cartella: str = STATS_DIR) -> None:
    try:
        os.makedirs(cartella, exist_ok=True)
        json_path = os.path.join(cartella, 'processi_attivi.json')
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
    except Exception:
        pass          # un pannello di stato non deve mai far fallire il batch


def leggi(cartella: str = STATS_DIR) -> dict:
    """Stato di tutti i processi. Dizionario vuoto se il file non c'è o è illeggibile."""
    try:
        with open(os.path.join(cartella, 'processi_attivi.json'), encoding='utf-8') as fh:
            dati = json.load(fh)
        return dati if isinstance(dati, dict) else {}
    except Exception:
        return {}


def _pid_vivo(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def in_corso(nome: str, cartella: str = STATS_DIR) -> bool:
    """
    Vero se `nome` risulta in esecuzione E il suo processo è ancora vivo.
    Il controllo sul PID è la parte che conta: uno stato `in_corso` rimasto
    appeso dopo un crash (o dopo la chiusura dell'app a batch in corso)
    bloccherebbe per sempre ogni riavvio automatico del job.
    """
    d = leggi(cartella).get(nome) or {}
    if d.get('stato') != 'in_corso':
        return False
    pid = d.get('pid')
    return _pid_vivo(pid) if pid is not None else True


def segna_avvio(nome: str, dettaglio: str = '', cartella: str = STATS_DIR) -> None:
    """Marca `nome` come in corso, col PID di CHI scrive (serve a in_corso())."""
    aggiorna(nome, {'stato': 'in_corso', 'pid': os.getpid(),
                    'ultimo_evento': datetime.now().isoformat(timespec='seconds'),
                    'dettaglio': dettaglio}, cartella)


def segna_fine(nome: str, dettaglio: str = '', errore: bool = False,
               cartella: str = STATS_DIR) -> None:
    aggiorna(nome, {'stato': 'errore' if errore else 'completato',
                    'ultimo_evento': datetime.now().isoformat(timespec='seconds'),
                    'dettaglio': dettaglio}, cartella)


def formatta_eta(secondi: float) -> str:
    ore = secondi / 3600
    if ore < 1:
        return f'{int(secondi / 60)}min'
    if ore < 24:
        return f'{ore:.1f}h'
    return f'{ore / 24:.1f}g'


def con_caffeina(comando: list[str]) -> list[str]:
    """
    Antepone `caffeinate -i -s` a un comando: impedisce il sonno del Mac per
    tutta la sua durata, coperchio chiuso incluso — `-s` copre il caso a
    corrente, `-i` quello a batteria.

    Nata il 2026-09-07: con coperchio chiuso e Mac a batteria, macOS cicla fra
    sonno e DarkWake ogni 1-2 minuti (log di sistema: `PMRD: clamshell closed`
    a raffica) — un batch Archivista da un'ora ci è passato sopra abbastanza
    volte da perdere la connessione al demone MLX 5 volte di fila e far
    scattare il fusibile, due volte, senza che ci fosse nulla di rotto nel
    codice. Va messa SOLO sui job di lunga durata lanciati in background
    (Archivista, `zeno manutenzione`, i job del pannello): un `caffeinate`
    tenuto attivo per una chat interattiva terrebbe sveglio il Mac anche
    quando l'utente se ne allontana per un attimo, che è l'opposto di quello
    che vuole.
    """
    import shutil
    binario = shutil.which('caffeinate')
    return [binario, '-i', '-s'] + comando if binario else comando
