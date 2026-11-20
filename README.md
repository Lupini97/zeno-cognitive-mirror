# Zeno

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24393322.svg)](https://doi.org/10.5281/zenodo.24393322)

**English summary.** Zeno is a local research prototype of a personal epistemic
engine, designed as a "cognitive mirror": it organises what a person has written
and read, surfaces connections and contradictions, and asks questions rather
than only giving answers. It runs entirely on the user's own Mac (Apple Silicon,
MLX). It is not a product and has not been validated on real users.

---

Zeno è un prototipo di ricerca di motore epistemico personale, pensato come
«specchio cognitivo». Organizza nel tempo ciò che una persona ha scritto e
letto, ne fa emergere connessioni e contraddizioni e pone domande invece di
limitarsi a fornire risposte.

È un prototipo sviluppato da una sola persona e usato su un solo archivio. Non è
un prodotto e non è stato validato su utenti reali. Zeno non addestra alcun
modello: arricchire l'archivio migliora solo il recupero delle informazioni.

## Che cosa serve

- Un Mac con Apple Silicon (M1 o successivi). Il prototipo è provato su un
  MacBook Air M3 con 16 GB di memoria.
- [Homebrew](https://brew.sh) e il suo Python (3.12 o più recente):
  `brew install python@3.14`. Il Python di sistema di macOS non va bene, perché
  non permette a SQLite di caricare estensioni.
- Circa 6 GB liberi: 4 GB per i modelli, il resto per l'ambiente Python.
- Facoltativo, per i PDF fatti di immagini: `brew install tesseract tesseract-lang`.

Linux e Windows per ora non sono supportati: i modelli girano con MLX, che
richiede Apple Silicon.

## Installazione

```bash
mkdir -p ~/Zeno && cd ~/Zeno
git clone https://github.com/Lupini97/zeno-cognitive-mirror.git scripts
cd scripts
./installa.sh
```

`installa.sh` crea l'ambiente Python, le cartelle e il file
`zeno_config.yaml` accanto al codice, poi scarica i due modelli (circa 4 GB).
Rilanciarlo non sovrascrive configurazione, documenti o archivio.

Apri `~/Zeno/zeno_config.yaml` e scrivi il tuo nome in `identita.nome_utente`.

## Uso

1. Metti i tuoi testi in Markdown (`.md`) dentro `~/Zeno/documents/`:
   - `riflessioni/` e `pubblicazioni/`: ciò che hai scritto tu (peso massimo);
   - `libri/`, `articoli_saggistica/`, `formazione/`: ciò che hai letto o studiato;
   - `chatgpt/` o `gemini/`: conversazioni archiviate.
   PDF, DOCX e HTML si convertono con `python -m zeno_engine converti`.
2. Avvia Zeno: `~/Zeno/scripts/avvia_zeno.sh`.
3. Apri <http://localhost:8501> (chat) e <http://localhost:8502> (dashboard).

Al primo avvio Zeno legge i documenti e calcola gli embedding. L'estrazione
delle asserzioni, i temi e il grafo procedono poi in background, mentre l'app è
aperta e non la stai usando. Su un archivio grande servono ore.

Comandi utili, da `scripts/`:

```bash
../.venv_zeno/bin/python -m zeno_engine ask "domanda"
../.venv_zeno/bin/python -m zeno_engine sync
../.venv_zeno/bin/python -m zeno_engine --help
```

## Come funziona

- **Archivio**: un file SQLite (`zeno.db`), con sqlite-vec per la ricerca
  vettoriale e FTS5 per quella testuale.
- **Modelli**: Qwen3.5-4B per risposte, estrazione e temi, bge-m3 per gli
  embedding, in formato MLX. Un demone locale (127.0.0.1:11500) li tiene in
  memoria, un modello per volta.
- **Quattro strati di conoscenza**, con priorità diverse nel recupero:
  esplicito (ciò che hai scritto), acquisito (ciò che hai letto), dialogico
  (conversazioni), inferito (asserzioni ed entità estratte dal modello).
- **Risposte**: ricerca vettoriale e testuale, pesi per strato, al massimo
  cinque frammenti al modello. Il modello decide prima, con una chiamata a
  parte, se i frammenti bastano; se non bastano la risposta dichiara la lacuna
  invece di colmarla per inferenza.
- **Grafo e domande**: NetworkX con comunità di Louvain; altre componenti
  cercano contraddizioni fra le tue asserzioni e generano domande socratiche.
- **Interfaccia**: Streamlit, chat e dashboard.

Struttura del codice:

```
scripts/
├── chat_ui.py, dashboard.py, zeno_app.py   pagine Streamlit
├── avvia_zeno.sh, installa.sh               avvio e installazione
├── zeno_engine/                             il motore: python -m zeno_engine <comando>
│   ├── config.py                            unico lettore di zeno_config.yaml
│   ├── ingest, archivista, indicizzatore, ask, critico, sync, periodici, ...
│   ├── mlx_server, mlx_motore, mlx_embed    demone e modelli MLX
│   └── valutazione.py, prompt_chat.py       misura della qualità (zeno valuta)
├── interfaccia/                             tema, grafo 3D, finestra, aggiornamenti
└── tests/                                   test con dati inventati
```

## Rete e dati personali

Vedi [PRIVACY.md](PRIVACY.md). In breve: i tuoi testi non lasciano il
computer. La rete serve solo a scaricare i modelli, a controllare le versioni
nuove (si spengono entrambi da `zeno_config.yaml`, sezione `rete`) e alla
ricerca web, che parte solo quando premi «Cerca sul web».

## Limiti

- Le misure di qualità vengono da un solo archivio e da 30 domande: indicano
  una tendenza, non dimostrano nulla in generale.
- I test automatici coprono il guardiano del demone e le lacune, non tutto
  il sistema.
- L'interfaccia e i prompt sono in italiano.

## Test

```bash
cd scripts
../.venv_zeno/bin/python -m unittest discover -s tests -t . -v
```

## Come citare

Michele Modesto (2026). *Zeno: prototipo di ricerca di motore epistemico
personale* (versione 0.2.0). Zenodo. https://doi.org/10.5281/zenodo.24393323

Il DOI di tutte le versioni, che porta sempre all'ultima, è
https://doi.org/10.5281/zenodo.24393322. I metadati sono in [CITATION.cff](CITATION.cff).

## Licenza

[MIT](LICENSE).
