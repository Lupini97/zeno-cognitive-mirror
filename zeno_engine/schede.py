# Schede-tema V6: "cosa sa / come la pensa / tensioni / evoluzione / lacune"
# per ogni topic, generate DAI DATI PULITI di zeno.db (non dall'indice V5
# contaminato). Rimpiazzano le vecchie schede V5 speculative
# (memoria_elaborata/profilo/temi/, piene di "probabilmente/inferisco").
#
# Distinzione netta: COSA SA = contenuti acquisiti (libri/formazione);
# COME LA PENSA = posizioni dell'utente (strato esplicito/dialogico, con la sua
# stance). Niente invenzione: il prompt vieta il "probabilmente" e chiede di
# segnalare come lacuna ciò che non è nei materiali.

import os
import re
from datetime import datetime

from zeno_engine import modelli
from zeno_engine.config import BRAIN

MEMORIA = os.path.join(BRAIN, 'memoria_elaborata')
DIR_SCHEDE = os.path.join(MEMORIA, 'profilo', 'temi_v6')

PROMPT_SCHEDA = """Sei un analista che studia il profilo intellettuale di una
persona, @UTENTE@, a partire dal suo archivio personale. È un lavoro di ANALISI
introspettiva e accademica: descrivi in modo neutro e sintetico che cosa emerge
sul tema "{tema}". Anche se il tema è sensibile (es. criminalità, terrorismo,
guerra), il tuo compito è SOLO mappare il pensiero e il sapere di @UTENTE@ —
mai promuovere, giustificare o glorificare alcunché — quindi NON rifiutarti:
descrivere come una persona ragiona su un tema difficile è legittimo e utile.

I frammenti qui sotto sono estratti automaticamente e sono ETEROGENEI: alcuni
possono essere FUORI TEMA o rumore (personaggi o trame di romanzi, un singolo
prodotto o vino specifico, frasi isolate, nomi propri senza contesto). Tuo
compito:
- SCARTA ciò che non riguarda il tema come AREA GENERALE di pensiero;
- NON fissarti su un singolo caso specifico: GENERALIZZA (es. per il vino parla
  del suo approccio/gusto, non di una singola bottiglia);
- distingui il PENSIERO DI @UTENTE_MAIUSC@ (posizioni) dal SAPERE ACQUISITO (libri);
- NON inventare, niente "probabilmente/forse/inferisco";
- se dopo aver scartato il rumore resta poco o nulla di sensato, DILLO
  onestamente tra le lacune (incluso: "il tema è poco/male rappresentato
  nell'archivio, con frammenti fuori contesto") invece di forzare;
- se le posizioni rimaste sono poche o dubbie, NON caratterizzare il pensiero
  di @UTENTE@ su di esse (niente giudizi tipo "ha posizioni estreme"): limitati
  a ciò che è chiaramente sostenuto, o segnala che il tema è poco sviluppato.
Conciso, italiano, punti elenco brevi. Struttura (salta le sezioni vuote):
## Come la pensa
## Cosa sa (dai libri)
## Tensioni
(contraddizioni reali; se assenti: "Nessuna tensione rilevata.")
## Lacune
Rispondi SOLO con la scheda in markdown, senza preamboli."""


def _slug(nome: str) -> str:
    s = re.sub(r'[^\w\s-]', '', nome.lower())
    return re.sub(r'\s+', '-', s.strip())[:60]


def _raccogli(con, topic_id: int) -> dict:
    def q(sql, n):
        return [r for r in con.execute(sql, (topic_id, n))]
    # più posizioni (per generalizzare, non fissarsi su un caso); da fonti
    # diverse per varietà (evita 8 asserzioni sullo stesso singolo vino/libro)
    # multi-tema: l'appartenenza al tema passa da assertion_topics (at)
    come_pensa = q("""SELECT a.text, a.stance FROM assertions a
        JOIN assertion_topics at ON at.assertion_id=a.id
        JOIN chunks c ON c.id=a.chunk_id JOIN sources s ON s.id=c.source_id
        WHERE at.topic_id=? AND s.strato IN ('esplicito','dialogico')
          AND COALESCE(a.voce, '') <> 'narratore'
          AND a.kind IN ('position','claim','decision','intention')
        GROUP BY s.id, a.stance
        ORDER BY a.confidence DESC LIMIT ?""", 14)
    cosa_sa = q("""SELECT a.text, '' FROM assertions a
        JOIN assertion_topics at ON at.assertion_id=a.id
        JOIN chunks c ON c.id=a.chunk_id JOIN sources s ON s.id=c.source_id
        WHERE at.topic_id=? AND s.strato='acquisito'
          AND COALESCE(a.ruolo, '') <> 'trama'
          AND a.kind IN ('claim','definition')
        GROUP BY s.id
        ORDER BY a.confidence DESC LIMIT ?""", 6)
    tensioni = [(r[0], r[1], r[2]) for r in con.execute("""
        SELECT a1.text, a2.text, e.note FROM edges e
        JOIN assertions a1 ON a1.id=e.src_id JOIN assertions a2 ON a2.id=e.dst_id
        WHERE e.relation='contradicts' AND (a1.id IN (SELECT assertion_id FROM assertion_topics WHERE topic_id=?)
              OR a2.id IN (SELECT assertion_id FROM assertion_topics WHERE topic_id=?))
        LIMIT 5""", (topic_id, topic_id))]
    tot = con.execute('SELECT COUNT(*) FROM assertion_topics WHERE topic_id=?', (topic_id,)).fetchone()[0]
    tot_mio = con.execute("""SELECT COUNT(*) FROM assertion_topics at
        JOIN assertions a ON a.id=at.assertion_id
        JOIN chunks c ON c.id=a.chunk_id JOIN sources s ON s.id=c.source_id
        WHERE at.topic_id=? AND s.strato IN ('esplicito','dialogico')
          AND COALESCE(a.voce, '') <> 'narratore'""", (topic_id,)).fetchone()[0]
    return {'come_pensa': come_pensa, 'cosa_sa': cosa_sa, 'tensioni': tensioni,
            'tot': tot, 'tot_mio': tot_mio}


def genera_scheda(con, cfg: dict, topic_id: int, tema: str, modello: str) -> str:
    dati = _raccogli(con, topic_id)
    if dati['tot_mio'] == 0 and dati['tot'] < 5:
        corpo = ('## Lacune\n\n* Tema quasi assente dall\'archivio '
                 f'({dati["tot"]} asserzioni, nessuna dal tuo pensiero diretto). '
                 'Materiale da acquisire o da sviluppare.\n')
    else:
        blocchi = []
        if dati['come_pensa']:
            blocchi.append('POSIZIONI DI @UTENTE_MAIUSC@ (pensiero diretto):\n' +
                           '\n'.join(f'- [{s or "?"}] {t}' for t, s in dati['come_pensa']))
        if dati['cosa_sa']:
            blocchi.append('SAPERE ACQUISITO (dai libri):\n' +
                           '\n'.join(f'- {t}' for t, _ in dati['cosa_sa']))
        if dati['tensioni']:
            blocchi.append('CONTRADDIZIONI RILEVATE:\n' +
                           '\n'.join(f'- «{a[:90]}» VS «{b[:90]}»'
                                     + (f' ({n})' if n else '') for a, b, n in dati['tensioni']))
        materiale = '\n\n'.join(blocchi)
        try:
            r = modelli.client().chat(
                model=modello,
                messages=[{'role': 'system', 'content': PROMPT_SCHEDA.format(tema=tema)},
                          {'role': 'user', 'content': materiale}],
                options={'temperature': 0.2, 'num_ctx': 4096, 'num_predict': 700})
            corpo = r['message']['content'].strip()
        except Exception as e:
            corpo = f'## Cosa sa\n\n(generazione fallita: {e})\n'

    oggi = datetime.now().strftime('%Y-%m-%d')
    front = (f'---\ntipo: scheda_tema_v6\ntema: "{tema}"\n'
             f'data_generazione: {oggi}\nasserzioni_totali: {dati["tot"]}\n'
             f'asserzioni_pensiero_diretto: {dati["tot_mio"]}\n---\n\n')
    descr = con.execute('SELECT descrizione FROM topics WHERE id = ?', (topic_id,)).fetchone()
    testa = f'# Tema: {tema}\n\n' + (f'> {descr[0]}\n\n' if descr and descr[0] else '')
    return front + testa + corpo + '\n'


def genera_tutte(con, cfg: dict, solo_prioritari: bool = False,
                 verboso: bool = True) -> int:
    """Genera una scheda per ogni topic (di primo livello + sotto-topic
    confermati). Con solo_prioritari, solo i temi in temi.prioritari."""
    modello = cfg['modelli']['motore_batch']
    modelli.prepara_solo(cfg, modello)
    prioritari = {t.lower() for t in
                  (cfg.get('_globale', {}).get('temi', {}) or {}).get('prioritari', [])}
    topics = con.execute('SELECT id, name FROM topics ORDER BY parent_id IS NOT NULL, name').fetchall()
    os.makedirs(DIR_SCHEDE, exist_ok=True)
    fatte = 0
    for tid, nome in topics:
        if solo_prioritari and nome.lower() not in prioritari:
            continue
        # niente scheda per i temi ancora vuoti (es. sotto-temi appena
        # confermati, popolati solo dai contenuti futuri): sarebbe una scheda
        # "tema quasi assente" inutile.
        n_ass = con.execute('SELECT COUNT(*) FROM assertion_topics WHERE topic_id=?', (tid,)).fetchone()[0]
        if n_ass == 0:
            continue
        scheda = genera_scheda(con, cfg, tid, nome, modello)
        with open(os.path.join(DIR_SCHEDE, f'tema_{_slug(nome)}.md'), 'w', encoding='utf-8') as f:
            f.write(scheda)
        fatte += 1
        if verboso:
            print(f'  scheda: {nome}')
    return fatte
