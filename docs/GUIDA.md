<p align="center">
  <img src="../interfaccia/assets/logo_zeno.png" alt="Logo di Zeno" width="96">
</p>

# Guida a Zeno

Questa guida spiega come si usa Zeno giorno per giorno e che cosa fa quando
non lo guardi. Per installarlo segui il [README](../README.md); per sapere
come si è arrivati alle scelte attuali leggi [METODO.md](METODO.md).

I comandi partono dalla cartella `~/Zeno/scripts`, se hai installato Zeno in
`~/Zeno` come indica il README.

## Indice

1. [Il ciclo di Zeno](#1-il-ciclo-di-zeno)
2. [I documenti](#2-i-documenti)
3. [La chat](#3-la-chat)
4. [La dashboard](#4-la-dashboard)
5. [Lavori in background](#5-lavori-in-background)
6. [Configurazione](#6-configurazione)
7. [Comandi da terminale](#7-comandi-da-terminale)
8. [Dove finiscono i dati](#8-dove-finiscono-i-dati)
9. [Problemi comuni](#9-problemi-comuni)

## 1. Il ciclo di Zeno

1. Metti i documenti in `~/Zeno/documents/`.
2. All'avvio Zeno li legge, li divide in frammenti e ne calcola gli embedding
   (`zeno sync`).
3. Mentre l'app è aperta e non la usi, il modello estrae da ogni frammento le
   asserzioni (frasi brevi con una sola idea), le entità (persone, luoghi,
   concetti, opere) e la tua posizione. Le assegna ai temi e costruisce il
   grafo.
4. In chat fai domande all'archivio (modalità ARCHIVIO) o lasci che Zeno ti
   interroghi (modalità MAIEUTICA).
5. Lacune, contraddizioni e collegamenti da confermare finiscono in
   un'agenda. Nella dashboard li guardi, li confermi o li scarti.
6. Ogni giorno Zeno scrive un briefing con tre domande socratiche e qualche
   argomento vecchio da ripassare.

## 2. I documenti

**Formato.** Zeno legge solo file Markdown (`.md`). Il README spiega come
convertire PDF, Word e altri formati con MarkItDown oppure con
`zeno converti`.

**Cartelle e strati.** La cartella decide lo strato, e lo strato decide
quanto pesa un frammento quando Zeno cerca la risposta:

| Strato | Cartelle | Peso nel recupero |
|---|---|---|
| esplicito | `riflessioni/`, `pubblicazioni/` | 1,5 |
| inferito | sintesi prodotte da Zeno | 1,3 |
| dialogico | `chatgpt/`, `gemini/`, sessioni archiviate | 1,1 |
| acquisito | `libri/`, `articoli_saggistica/`, `formazione/` | 1,0 |

**Data e titolo.** Zeno prende la data dal frontmatter (`date:` o `data:`),
poi dal nome del file (`2025-03-12 Appunti.md`), infine dalla data di
modifica del file. Prende il titolo dal frontmatter (`title:` o `titolo:`),
poi dal primo titolo `#`, infine dal nome del file.

**Schede dei libri.** In `libri/` il campo `conoscenza:` del frontmatter dice
quanto conosci un libro: `letto`, `letto-parziale`, `consultato`,
`da-leggere`. Zeno lo usa nelle risposte, per non attribuirti un giudizio su
un libro che non hai letto.

**Aggiornare.** Aggiungi, modifica o cancella file quando vuoi. A ogni avvio
`zeno sync` legge i file nuovi o cambiati e toglie dall'archivio quelli che
non ci sono più. A Zeno aperto lo stesso comando si lancia da terminale.

**Importazioni da CSV.** Gli export CSV di Goodreads e di Vivino messi in
`~/Zeno/import_staging/` diventano schede Markdown al primo `zeno sync`.

## 3. La chat

La chat si apre su <http://localhost:8501>.

### Modalità ARCHIVIO

Fai una domanda e Zeno risponde solo con ciò che trova nei documenti. Prima
decide se i frammenti recuperati bastano a rispondere.

- Se bastano, scrive la risposta e cita i documenti con numeri fra parentesi
  quadre. Sotto la risposta compare l'elenco delle fonti, con la voce di
  ciascuna (la tua voce, autore letto, dialogo) e la data.
- Se non bastano, scrive `LACUNA:` e dice che cosa manca. La lacuna entra
  nell'agenda della modalità MAIEUTICA e nel pannello «Errori e lacune».

Sotto ogni risposta il pollice su o giù registra il tuo giudizio, con una
correzione facoltativa. Il riepilogo si legge con `zeno valuta voti`.

### Modalità MAIEUTICA

Zeno non risponde: ti fa domande. Parte dall'agenda, cioè dalle lacune
aperte, dai collegamenti da confermare e dalle tensioni fra i temi. Quando
nel dialogo cambi posizione, lo segna con `EVOLUZIONE:`. Alla fine della
sessione ti propone una nota, che puoi correggere e salvare. La nota va in
`documents/riflessioni/` ed entra nell'archivio come testo tuo, con il peso
dello strato esplicito.

### Barra laterale e pannello di controllo

- **Barra laterale**: nome, versione, stato dei lavori in background
  («Processi Zeno»), numero di documenti e frammenti, conteggio dei libri
  letti e da leggere.
- **Errori e lacune**: le lacune aperte. Accanto a ognuna, con «Integrazione
  web» acceso, «Cerca sul web» manda una ricerca al motore di `ddgs` e
  «Importa pagina» scarica l'indirizzo che incolli. In entrambi i casi vedi il
  risultato prima di salvarlo.
- **Impostazioni**: modello, soglie e lingua in uso, più il pulsante per
  rileggere la configurazione.
- **Aggiornamenti**: versioni nuove delle librerie e dei modelli, se il
  controllo è acceso.
- **Aggiungi citazione / recensione / riflessione**: scrive una citazione,
  una recensione, una sinossi o una nota sull'autore nella scheda di un libro,
  oppure una riflessione generale, senza aprire i file.

**Integrazione web.** L'interruttore in alto nella chat apre o chiude la
porta verso internet, per la chat e per la dashboard insieme. È spento di
serie, e resta come lo lasci fra un avvio e l'altro (`stats/ricerca_web.json`).
Spento, i pulsanti «Cerca sul web» e «Importa pagina» sono disattivati e le
funzioni che aprirebbero la connessione si rifiutano di farlo. Non riguarda
il download dei modelli e il controllo delle versioni, che si spengono in
`zeno_config.yaml`, sezione `rete`.

## 4. La dashboard

La dashboard si apre su <http://localhost:8502>, oppure dalla voce
«Dashboard» nella barra laterale della chat.

| Scheda | Che cosa mostra | Che cosa puoi fare |
|---|---|---|
| Statistiche | numeri dell'archivio, temi per connessioni, crescita nel tempo | |
| Grafo 3D | temi, documenti ed entità collegati; i nodi nuovi più chiari | ruotare, ingrandire, cliccare un nodo; colorare per tipo o per comunità |
| Journal | il diario settimanale scritto da Zeno | |
| Evoluzione | come crescono temi, asserzioni e collegamenti | |
| Gestione temi | i temi, le proposte di temi nuovi, i temi prioritari | creare, rinominare, descrivere, ritirare un tema; accettare o rifiutare una proposta |
| Lacune aperte | le lacune con età, fonte e area | segnare come risolta, eliminare, unire i doppioni |
| Entità | persone, luoghi, concetti e opere, con le proposte di alias | confermare o rifiutare gli alias |
| Profilo cognitivo | il profilo usato per il tono delle risposte, se lo hai compilato | |

**Temi.** Un archivio nuovo parte con dieci temi generici. Puoi sostituirli
prima del primo avvio con `temi.iniziali` in `zeno_config.yaml`, oppure
cambiarli dopo dalla scheda «Gestione temi». Ogni settimana Zeno propone temi
nuovi a partire dalle asserzioni che non trovano posto in quelli esistenti.

## 5. Lavori in background

Zeno lavora quando l'app è aperta e non la usi. Ogni 15 minuti controlla
quali lavori sono dovuti. Quelli che usano il modello partono solo dopo 10
minuti senza messaggi in chat, così non rallentano le risposte. Un lavoro che
fallisce non blocca gli altri.

| Lavoro | Ogni | Usa il modello | Che cosa fa |
|---|---|---|---|
| Archivista | a ogni avvio, fino a 150 frammenti | sì | estrae asserzioni ed entità dai frammenti nuovi |
| briefing | 1 giorno | sì | briefing con contraddizioni, lacune, tre domande socratiche e argomenti da ripassare |
| giudice | 1 giorno | sì | ruolo delle asserzioni che vengono da testi narrativi |
| mind | 5 giorni | no | cerca le lacune: temi su cui non hai scritto, temi quasi assenti |
| graph | 5 giorni | no | aggiorna il grafo e le sue comunità |
| snapshot | 6 giorni | no | fotografia dell'archivio: conteggi per area, entità più frequenti |
| entità | 7 giorni | no | fonde gli omonimi e propone gli alias |
| journal | 7 giorni | sì | diario della settimana |
| temi | 7 giorni | sì | sistema le asserzioni senza tema e propone temi nuovi |
| critico | 7 giorni | sì | cerca contraddizioni fra le tue asserzioni |
| schede | 7 giorni | sì | schede tema: che cosa sai, come la pensi, tensioni, lacune |

Lo stato dei lavori si vede nella barra laterale della chat e con:

```bash
../.venv_zeno/bin/python -m zeno_engine manutenzione --stato
```

## 6. Configurazione

Tutto si regola in `~/Zeno/zeno_config.yaml`. Dopo una modifica premi
«Ricarica configurazione» nella chat, oppure riavvia Zeno.

| Sezione | Chiavi principali | A che cosa serve |
|---|---|---|
| `identita` | `nome_utente`, `lingua_risposta` | come Zeno ti chiama, in che lingua risponde |
| `rete` | `controllo_aggiornamenti`, `download_modelli` | spegnere i due usi automatici della rete |
| `percorsi` | `documents`, `db`, ... | spostare una cartella fuori da `~/Zeno` |
| `modelli` | `llm`, `embeddings` | modello delle risposte e degli embedding |
| `motore_epistemico.modelli` | `router`, `motore_batch`, `motore_ask` | modello per ogni ruolo |
| `motore_epistemico.sync` | `archivista_auto`, `archivista_background_max`, `periodici_idle_minuti` | lavori in background |
| `motore_epistemico.briefing` | `frequenza_giorni` | ogni quanti giorni il briefing |
| `temi` | `iniziali`, `descrizioni`, `prioritari` | temi di partenza, descrizioni, temi a cui dai precedenza |
| `ui` | `tema` | aspetto: `schedario`, `biblioteca` o `notturno` |

**Cambiare modello.** Ogni modello in formato MLX su Hugging Face va bene, se
ci sta in memoria. Scrivi il nome del repository in `modelli.llm` e nei tre
ruoli di `motore_epistemico.modelli`: al primo uso Zeno scarica i pesi che
mancano, se `rete.download_modelli` è acceso. Se cambi il modello degli
embedding, Zeno deve ricalcolare tutti i vettori, e su un archivio grande
servono ore. Prima di adottare un modello nuovo misuralo con `zeno valuta`
(vedi [METODO.md](METODO.md#rifare-le-misure-sul-tuo-archivio)).

## 7. Comandi da terminale

Tutti i comandi hanno la forma:

```bash
cd ~/Zeno/scripts && ../.venv_zeno/bin/python -m zeno_engine <comando>
```

| Comando | Che cosa fa |
|---|---|
| `--version` | stampa la versione di Zeno |
| `ask "domanda"` | fa una domanda all'archivio senza aprire la chat |
| `sync` | mette in pari l'archivio con i documenti |
| `converti` | converte PDF, DOCX, HTML e TXT in Markdown |
| `audit` | numeri dell'archivio: fonti, frammenti, asserzioni, temi |
| `archivista` | estrae le asserzioni dai frammenti che non le hanno |
| `indicizza` | embedding, temi e archi di somiglianza |
| `critico` | cerca contraddizioni |
| `brief` | scrive il briefing di oggi |
| `temi` | propone temi nuovi |
| `schede` | scrive le schede tema |
| `manutenzione` | esegue i lavori dovuti; con `--stato` li elenca |
| `export-vault` | esporta il grafo come vault di Obsidian |
| `valuta` | misura recupero e risposte (vedi METODO.md) |

L'elenco completo, con tutte le opzioni:

```bash
../.venv_zeno/bin/python -m zeno_engine --help
```

## 8. Dove finiscono i dati

Tutto resta in `~/Zeno`:

| Percorso | Contenuto |
|---|---|
| `zeno.db` | frammenti, embedding, asserzioni, entità, temi, grafo |
| `documents/` | i tuoi documenti |
| `documents_originali/` | gli originali dei file convertiti da `zeno converti` |
| `memoria_elaborata/` | sessioni della chat, schede tema, journal |
| `briefing/` | un briefing al giorno, in Markdown |
| `stats/` | lacune, snapshot, grafo esportato, valutazioni, registri |
| `obsidian_vault/` | il vault creato da `export-vault` |
| `models/` | i pesi dei modelli |

Per ricostruire l'archivio da zero, chiudi Zeno, sposta `zeno.db` nel Cestino
e riavvia: Zeno rilegge i documenti e rifà gli embedding, poi le asserzioni e
i temi in background. I temi che hai creato o rinominato in dashboard vanno
persi.

## 9. Problemi comuni

**«ambiente Python non trovato».** Manca `.venv_zeno`. Rilancia
l'installazione:

```bash
cd ~/Zeno/scripts && ./installa.sh
```

**«Python di Homebrew non trovato».** Installa Python con
`brew install python@3.14` e rilancia `./installa.sh`.

**Il download dei modelli si è fermato.** Rilancia `./installa.sh`: il
download riprende dal punto in cui si era interrotto.

**La prima risposta è lenta.** All'avvio Zeno carica il modello in memoria:
la prima risposta può richiedere un paio di minuti, le altre in mediana circa
20 secondi.

**Zeno non risponde più.** Se un calcolo sulla GPU si blocca per due minuti,
il demone del modello si chiude e la richiesta successiva lo riavvia. Se resta
fermo, chiudi tutto e riavvia:

```bash
lsof -ti tcp:8501,8502,11500 | xargs kill
```

```bash
~/Zeno/scripts/avvia_zeno.sh
```

**Porte già occupate.** `avvia_zeno.sh` chiude da solo ciò che occupa le
porte 8501 e 8502 prima di avviare chat e dashboard. Se su quelle porte gira
un altro programma tuo, fermalo prima.

**Dove guardare gli errori.** I registri stanno in `/tmp`:

| File | Che cosa registra |
|---|---|
| `/tmp/zeno_mlx.log` | il demone del modello |
| `/tmp/zeno_sync.log` | la messa in pari all'avvio |
| `/tmp/zeno_chat.log` | la chat |
| `/tmp/zeno_dashboard.log` | la dashboard |
