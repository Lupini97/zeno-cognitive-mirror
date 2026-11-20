# Zeno

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24393322.svg)](https://doi.org/10.5281/zenodo.24393322)
[![Licenza: MIT](https://img.shields.io/badge/licenza-MIT-blue.svg)](LICENSE)
![Piattaforma: macOS su Apple Silicon](https://img.shields.io/badge/macOS-Apple%20Silicon-lightgrey.svg)

**English summary.** Zeno is a research prototype of a personal epistemic
engine, designed as a "cognitive mirror". It reads what one person has written
and read, extracts atomic claims, links them in a graph and points out gaps and
contradictions. In its maieutic mode it asks Socratic questions instead of
answering. Everything runs on the user's own Mac (Apple Silicon): one local
language model (Qwen3.5-4B, 4-bit, via MLX), bge-m3 embeddings, and SQLite with
sqlite-vec and FTS5. No text leaves the computer. On a 30-question test set
built from the author's archive, the current configuration answered 29
questions correctly and recognised all 7 questions the archive could not
answer, with a median of 18.9 s per answer on a MacBook Air M3. It is a
single-user prototype, not a product, and it has not been tested on other
users. The interface and prompts are in Italian. Installation steps are below;
[docs/METODO.md](docs/METODO.md) (in Italian) describes how the design and the
models were chosen.

---

Zeno è un prototipo di ricerca: un motore epistemico personale, pensato come
«specchio cognitivo». Legge ciò che hai scritto e ciò che hai letto, ne estrae
le asserzioni, le collega in un grafo e ti mostra le lacune e le
contraddizioni. Puoi interrogarlo come un archivio, e lui risponde citando i
tuoi documenti. Oppure puoi scegliere la modalità maieutica, in cui Zeno non
risponde e ti fa domande.

Gira tutto sul tuo Mac. I tuoi testi, le domande e le risposte restano sul
computer.

![La chat di Zeno su un archivio di prova](docs/img/chat.png)

*La chat in modalità ARCHIVIO, su un archivio di prova con testi inventati.*

## Indice

- [Per chi è](#per-chi-è)
- [Che cosa serve](#che-cosa-serve)
- [Installazione](#installazione)
- [Primo uso](#primo-uso)
- [Come funziona](#come-funziona)
- [Risultati e limiti](#risultati-e-limiti)
- [Rete e dati personali](#rete-e-dati-personali)
- [Comandi da terminale](#comandi-da-terminale)
- [Come citare](#come-citare)

## Per chi è

Zeno serve a chi tiene da anni appunti, riflessioni, recensioni e
conversazioni e vuole vedere come il proprio pensiero si collega e cambia nel
tempo. Ha un solo utente e un solo archivio. Non addestra alcun modello:
quando aggiungi documenti migliora il recupero delle informazioni, mentre i
pesi del modello restano quelli originali.

L'ha scritto una persona sola, per la tesi di Master di II livello in
Intelligence (a.a. 2025/2026) e per il proprio archivio. Il percorso, le scelte
e le misure sono in [docs/METODO.md](docs/METODO.md).

## Che cosa serve

- Un Mac con Apple Silicon (M1 o successivi). Zeno è provato su un MacBook Air
  M3 con 16 GB di memoria. Il modello occupa circa 3 GB mentre lavora.
- Circa 6 GB liberi sul disco: 4 GB per i modelli, il resto per l'ambiente
  Python.
- [Homebrew](https://brew.sh) e il suo Python, versione 3.12 o più recente. Il
  Python di sistema di macOS non va bene, perché il suo SQLite non carica
  estensioni.
- La connessione a internet, solo per l'installazione.

Linux e Windows non sono supportati: i modelli girano con MLX, che esiste solo
per Apple Silicon.

## Installazione

Apri il Terminale e incolla un blocco alla volta.

**1. Homebrew e Python.** Se hai già Homebrew, salta il primo comando.

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

```bash
brew install python@3.14
```

Facoltativo, serve solo per leggere i PDF fatti di immagini:

```bash
brew install tesseract tesseract-lang
```

**2. Scarica Zeno.** Zeno vive in una cartella sua, qui `~/Zeno`. Il codice va
nella sottocartella `scripts`.

```bash
mkdir -p ~/Zeno && cd ~/Zeno
git clone https://github.com/Lupini97/zeno-cognitive-mirror.git scripts
```

**3. Installa.** Lo script controlla il Mac, crea l'ambiente Python, le
cartelle e il file di configurazione, poi scarica i due modelli (circa 4 GB,
una volta sola).

```bash
cd ~/Zeno/scripts && ./installa.sh
```

Puoi rilanciarlo quando vuoi: non sovrascrive configurazione, documenti o
archivio.

**4. Scrivi il tuo nome.** Zeno ti chiama con il nome che trova in
`identita.nome_utente`. Apri la configurazione:

```bash
open -e ~/Zeno/zeno_config.yaml
```

e cambia `nome_utente: Utente` con il tuo nome. Salva e chiudi.

Zeno ordina le asserzioni in temi. Parte con dieci temi generici (lavoro,
studio, scrittura, società, scienza, filosofia, storia, arte, relazioni,
salute). Dalla scheda «Gestione temi» della dashboard li rinomini, ne crei e ne
ritiri, e Zeno ne propone di nuovi quando legge i tuoi documenti. Se preferisci
partire dai tuoi, scrivili in `temi.iniziali` prima del primo avvio: il file
contiene un esempio.

Alla fine `~/Zeno` contiene:

```
~/Zeno/
├── scripts/            il codice
├── .venv_zeno/         l'ambiente Python
├── zeno_config.yaml    la tua configurazione
├── documents/          i tuoi documenti
├── models/             i pesi dei modelli
├── stats/              registri, valutazioni, lacune
└── zeno.db             l'archivio, creato al primo avvio
```

## Primo uso

**1. Metti i documenti.** Zeno legge file Markdown (`.md`) dentro
`~/Zeno/documents/`. La cartella in cui metti un file decide lo strato di
conoscenza e quindi quanto pesa nelle risposte:

| Cartella | Che cosa ci va | Strato |
|---|---|---|
| `riflessioni/`, `pubblicazioni/` | ciò che hai scritto tu | esplicito (peso massimo) |
| `libri/`, `articoli_saggistica/`, `formazione/` | ciò che hai letto o studiato | acquisito |
| `chatgpt/`, `gemini/` (creale tu) | conversazioni archiviate | dialogico |

Se hai PDF, DOCX, HTML o TXT, mettili nelle stesse cartelle e convertili.
Zeno scrive il `.md` accanto a ogni file e sposta l'originale in
`~/Zeno/documents_originali/`.

```bash
cd ~/Zeno/scripts && ../.venv_zeno/bin/python -m zeno_engine converti
```

**2. Avvia Zeno.**

```bash
~/Zeno/scripts/avvia_zeno.sh
```

Poi apri nel browser la chat, <http://localhost:8501>, e la dashboard,
<http://localhost:8502>.

Al primo avvio Zeno legge i documenti e calcola gli embedding. Poi, mentre
l'app è aperta e non la usi, estrae le asserzioni, assegna i temi e costruisce
il grafo. Su qualche decina di documenti bastano pochi minuti; su un archivio
di migliaia di file servono ore, distribuite su più avvii. La prima risposta
dopo l'avvio è più lenta, perché Zeno carica il modello in memoria.

**3. Usa la chat.** In alto scegli la modalità:

- **ARCHIVIO**: fai una domanda e Zeno risponde citando i documenti con
  numeri fra parentesi quadre, che rimandano all'elenco delle fonti. Se i
  documenti non bastano, scrive `LACUNA:` e dice che cosa manca. Ogni lacuna
  finisce nell'agenda della modalità maieutica. Sotto ogni risposta puoi dare
  un pollice su o giù.
- **MAIEUTICA**: Zeno non risponde, ti fa domande. Parte dall'agenda: le
  lacune aperte, i collegamenti da confermare, le tensioni fra i temi. Quando
  nel dialogo cambi posizione, lo segna con `EVOLUZIONE:`. A fine sessione ti
  propone una nota, che puoi correggere e salvare.

Il pulsante «Cerca sul web» è l'unico che usa internet, e parte solo quando lo
premi.

**4. Guarda la dashboard.** Le schede mostrano statistiche dell'archivio, il
grafo in 3D, il diario settimanale, l'evoluzione nel tempo, i temi (da
confermare o rifiutare), le lacune aperte (da eliminare o unire), le entità e
il profilo cognitivo, che resta vuoto se non lo compili.

**5. Ferma Zeno.** Chiudere il browser non basta: chat, dashboard e modello
restano attivi in background. Per fermarli:

```bash
lsof -ti tcp:8501,8502,11500 | xargs kill
```

## Come funziona

```mermaid
flowchart LR
    D["documents/<br/>file Markdown"] --> I["Lettura e frammenti<br/>embedding bge-m3"]
    I --> DB[("zeno.db<br/>SQLite + sqlite-vec + FTS5")]
    DB --> A["In background:<br/>asserzioni, temi, grafo,<br/>contraddizioni"]
    A --> DB
    Q["Domanda in chat"] --> R["Ricerca vettoriale + testuale<br/>pesi per strato<br/>max 5 frammenti"]
    DB --> R
    R --> S{"I frammenti<br/>bastano?"}
    S -- sì --> W["Risposta con citazioni [n]"]
    S -- no --> L["LACUNA dichiarata"]
    L --> M["Agenda della<br/>modalità maieutica"]
    A --> M
```

**Archivio.** Un solo file SQLite, `zeno.db`, contiene frammenti, embedding,
asserzioni, entità, temi e archi del grafo. sqlite-vec fa la ricerca
vettoriale, FTS5 quella per parole. Se cancelli `zeno.db`, l'avvio successivo
rilegge i documenti e ricalcola gli embedding; asserzioni e temi si rifanno in
background.

**Modelli.** Un solo modello linguistico, `mlx-community/Qwen3.5-4B-4bit`,
scrive le risposte, estrae le asserzioni e assegna i temi.
`mlx-community/bge-m3-mlx-fp16` calcola gli embedding. Girano con MLX dentro un
demone locale (`127.0.0.1:11500`), che tiene in memoria una copia sola del
modello per tutti i processi di Zeno. Un vincolo di grammatica (llguidance)
obbliga il modello a produrre JSON valido nelle estrazioni. Puoi cambiare
modello in `zeno_config.yaml`, con un altro in formato MLX.

**Quattro strati di conoscenza.** Nel recupero ogni frammento riceve un peso
secondo lo strato: esplicito 1,5 (ciò che hai scritto), inferito 1,3 (sintesi
di Zeno), dialogico 1,1 (conversazioni), acquisito 1,0 (ciò che hai letto). La
tua voce viene prima di quella dei libri.

**Una risposta.** Zeno combina la ricerca vettoriale e quella per parole,
applica i pesi, scarta i frammenti quasi identici e ne passa al modello al
massimo cinque, non più di due per fonte. Il primo risultato della ricerca per
parole ha sempre un posto. Poi il modello lavora in due chiamate. La prima
decide soltanto se i frammenti bastano. Se bastano, la seconda scrive la
risposta citando i documenti; se non bastano, Zeno dichiara la lacuna invece di
colmarla con ciò che il modello sa per conto suo.

**In background.** Quando l'app è aperta e non la usi da dieci minuti, Zeno
estrae le asserzioni dai frammenti nuovi, propone temi, aggiorna il grafo
(NetworkX, comunità di Louvain), cerca contraddizioni fra le tue asserzioni e
prepara ogni giorno un briefing con tre domande socratiche e qualche argomento
vecchio da ripassare.

**Interfaccia.** Streamlit, con chat e dashboard. Il grafo 3D usa una libreria
JavaScript inclusa nel repository: l'interfaccia non carica nulla da internet.

Struttura del codice:

```
scripts/
├── chat_ui.py, dashboard.py, zeno_app.py   pagine Streamlit
├── avvia_zeno.sh, installa.sh               avvio e installazione
├── zeno_launcher.py                         finestra dell'app (pywebview)
├── zeno_engine/                             il motore: python -m zeno_engine <comando>
│   ├── config.py                            unico lettore di zeno_config.yaml
│   ├── ingest, archivista, indicizzatore    lettura, asserzioni, embedding e temi
│   ├── ask, adapter_v5, prompt_chat         recupero e risposta
│   ├── critico, briefing, graph, lacune     contraddizioni, briefing, grafo, lacune
│   ├── sync, periodici                      messa in pari e lavori in background
│   ├── mlx_server, mlx_motore, mlx_embed    demone e modelli MLX
│   └── valutazione                          misura della qualità (zeno valuta)
├── interfaccia/                             tema, grafo 3D, finestra, aggiornamenti
├── docs/                                    metodo e immagini
└── tests/                                   test con dati inventati
```

## Risultati e limiti

L'autore ha misurato le risposte su 30 domande reali poste al proprio
archivio, ognuna con la risposta attesa scritta da lui. Sette domande
riguardano cose che l'archivio non contiene: lì la risposta giusta è la
lacuna. Un modello locale (Qwen3-4B-Instruct-2507) ha fatto da giudice, con
punteggi da 0 a 2.

| Misura | Configurazione attuale |
|---|---|
| Risposte corrette | 29 su 30 |
| Copertura della risposta attesa (0-2) | 1,91 |
| Fedeltà ai documenti (0-2) | 1,93 |
| Lacune riconosciute | 7 su 7 |
| Lacune segnalate a torto | 2 |
| Tempo per risposta (mediana) | 18,9 s |
| Prima parola a schermo | circa 13 s |
| Brano giusto fra i frammenti passati al modello | 21 su 23 |

Tempi misurati su un MacBook Air M3 da 16 GB, a modello già caricato. Prima
del lavoro sulle istruzioni e sul recupero la stessa misura dava 52 secondi di
mediana. Una lettura esterna una tantum (Claude, 17 novembre 2026, sulle
configurazioni di prova) ha dato fedeltà più basse del giudice locale. Numeri,
metodo e storia sono in [docs/METODO.md](docs/METODO.md).

Limiti da tenere presenti:

- Le misure vengono da un solo archivio, da 30 domande e dalla persona che ha
  scritto il programma. Indicano una tendenza e non dimostrano nulla in
  generale.
- Il giudice è un modello piccolo, della stessa famiglia di quelli giudicati.
  Tende a premiare le risposte lunghe, quindi la fedeltà può essere
  sovrastimata.
- I test automatici coprono il guardiano del demone e la gestione delle
  lacune, non tutto il sistema.
- Interfaccia e istruzioni al modello sono in italiano.
- Zeno ha un solo utente. Un uso con più persone, per esempio in una scuola,
  è una proposta della tesi e non è stato costruito.

## Rete e dati personali

Dettagli in [PRIVACY.md](PRIVACY.md). In breve: i tuoi testi non lasciano il
computer e il codice non chiama alcun servizio di intelligenza artificiale
esterno. La rete serve a scaricare i modelli, a controllare ogni 30 giorni se
ci sono versioni nuove e alla ricerca web quando premi «Cerca sul web». I
primi due si spengono da `zeno_config.yaml`, sezione `rete`. Chat e dashboard
ascoltano solo su `localhost`.

## Comandi da terminale

Tutti i comandi partono dalla cartella `scripts`:

```bash
cd ~/Zeno/scripts
```

Fai una domanda senza aprire la chat:

```bash
../.venv_zeno/bin/python -m zeno_engine ask "che cosa ho scritto sulla memoria?"
```

Metti in pari l'archivio con i documenti (file nuovi, modificati o tolti):

```bash
../.venv_zeno/bin/python -m zeno_engine sync
```

Guarda quanti documenti, frammenti e asserzioni ci sono:

```bash
../.venv_zeno/bin/python -m zeno_engine audit
```

Elenca tutti i comandi:

```bash
../.venv_zeno/bin/python -m zeno_engine --help
```

Per misurare Zeno sul tuo archivio, con le tue domande, vedi
[docs/METODO.md](docs/METODO.md#rifare-le-misure-sul-tuo-archivio).

Test automatici, su dati inventati:

```bash
cd ~/Zeno/scripts && ../.venv_zeno/bin/python -m unittest discover -s tests -t . -v
```

## Come citare

Michele Modesto (2026). *Zeno: prototipo di ricerca di motore epistemico
personale* (versione 0.2.1). Zenodo. https://doi.org/10.5281/zenodo.24398096

Il DOI di tutte le versioni, che porta sempre all'ultima, è
https://doi.org/10.5281/zenodo.24393322. La versione 0.2.0 ha il DOI
https://doi.org/10.5281/zenodo.24393323. I metadati sono in [CITATION.cff](CITATION.cff): GitHub li mostra nel pulsante «Cite this
repository».

## Licenza

[MIT](LICENSE).
