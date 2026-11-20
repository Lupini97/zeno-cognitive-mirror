<p align="center">
  <img src="../interfaccia/assets/logo_zeno.png" alt="Logo di Zeno" width="96">
</p>

# Come si è arrivati a Zeno

Questo documento racconta il percorso di Zeno da giugno a novembre 2026: che
cosa voleva essere, come è cambiata l'architettura, come sono stati scelti i
modelli e come è stata misurata la qualità. I numeri vengono dalle prove
dell'autore sul proprio archivio. Le date sono quelle delle prove.

Zeno è il prototipo descritto nel par. 4.1.1 della tesi di Master di II
livello in Intelligence *L'ipercomplessità del dominio informativo egemone.
Intelligence, IA e approcci innovativi alla sovranità dello Stato* (a.a.
2025/2026), dello stesso autore del codice. I brani tra virgolette e in
blocco vengono da quel paragrafo.

## Indice

1. [L'idea](#1-lidea)
2. [Vincoli di progetto](#2-vincoli-di-progetto)
3. [Cronologia](#3-cronologia)
4. [Scelta dei modelli](#4-scelta-dei-modelli)
5. [Recupero](#5-recupero)
6. [Qualità delle risposte](#6-qualità-delle-risposte)
7. [Verifica esterna del 17 novembre](#7-verifica-esterna-del-17-novembre)
8. [Limiti](#8-limiti)
9. [Prospettive](#9-prospettive)
10. [Rifare le misure sul tuo archivio](#rifare-le-misure-sul-tuo-archivio)
11. [Riferimenti](#riferimenti)

## 1. L'idea

La tesi presenta Zeno come un «gemello digitale cosciente» che fa domande
invece di dare risposte:

> Zeno, il prototipo di gemello digitale cosciente che questo lavoro prova a
> proporre, interroga la persona a cui è associato anziché fornirle risposte
> già pronte, operando dunque in una modalità che si definisce maieutica […].
> L'aggettivo "cosciente" non attribuisce alla macchina una coscienza in senso
> letterale […], ma indica piuttosto la funzione del sistema, ossia aiutare chi
> lo usa a prendere coscienza di come evolve il proprio pensiero.
>
> (tesi, par. 4.1.1)

Il nome richiama *La coscienza di Zeno* di Svevo. «Gemello digitale» indica un
gemello della conoscenza della persona, non un avatar che ne imita il
carattere. Da qui vengono le due modalità della chat. ARCHIVIO risponde con le
fonti e dichiara ciò che manca. MAIEUTICA parte da lacune, collegamenti e
tensioni e pone domande:

> Il programma propone collegamenti che la persona deve confermare o
> respingere e, dove emergono contraddizioni, individuate dal programma o dalla
> persona stessa, le raccoglie in un'agenda; quando la persona sceglie la
> modalità maieutica, parte una conversazione in cui si dialoga con
> l'assistente per risolvere la contraddizione a parole proprie. Non c'è una
> risposta giusta e una sbagliata, perché non è importante individuare la
> qualità del pensiero, ma mantenere allenata la capacità innovativa della
> persona.
>
> (tesi, par. 4.1.1)

La tesi usa anche l'immagine dell'iceberg: la memoria ricorda la punta, e
l'archivio permette «ogni tanto» di ribaltarlo e rileggere ciò a cui la mente
non arriva da sola.

## 2. Vincoli di progetto

Cinque vincoli hanno guidato tutte le scelte successive.

- **Nessun cloud.** Archivio, modelli e risposte stanno sul Mac dell'utente.
  Una strada opzionale verso un'API esterna era stata prevista nella prima
  versione, mai attivata e tolta dal codice il 30 settembre 2026.
- **Un Mac da 16 GB.** Il prototipo gira su un MacBook Air M3 senza ventola.
  Modelli da 14 miliardi di parametri o più restano fuori, e due modelli
  grandi in memoria insieme mandano il sistema in swap.
- **La voce della persona prima.** Nel recupero ciò che l'utente ha scritto
  pesa più di ciò che ha letto.
- **Dichiarare la lacuna.** Se l'archivio non contiene la risposta, Zeno lo
  dice. Una risposta plausibile ma inventata vale meno di una lacuna
  dichiarata, perché la lacuna diventa materia per la modalità maieutica.
- **Nessun addestramento.** Zeno recupera e cita; i pesi del modello non
  cambiano. Un adattamento (LoRA) è stato misurato e scartato su questo
  hardware (vedi [Limiti](#8-limiti)).

## 3. Cronologia

V5 e V6 sono i nomi interni delle due architetture di Zeno, non numeri di
versione. Le versioni pubblicate del software si chiamano 0.2.0, 0.2.1 e così
via: sono quelle della release su GitHub, del DOI su Zenodo e della schermata
iniziale del programma.

### V5: archivio che riassume (fino a luglio 2026)

La prima versione usava LlamaIndex come framework RAG e Qdrant, in un
container Docker, come database vettoriale. Llama 3.1 8B girava su Ollama,
nomic-embed-text v1.5 calcolava embedding a 768 dimensioni, e i frammenti
erano di 512 token. Un watcher del filesystem aggiornava l'indice solo a app
accesa: i file aggiunti a Zeno spento restavano fuori.

### V6: motore epistemico (luglio 2026)

Zeno è passato da un archivio che riassume a un motore che lavora su
asserzioni. La V6 è nata accanto alla V5, senza toccarla, e l'ha sostituita
il 14 luglio, quando chat e dashboard sono migrate sul motore nuovo.

- Un solo file SQLite, `zeno.db`, con sqlite-vec per i vettori e FTS5 per il
  testo, al posto di Qdrant e Docker. Cancellarlo e rilanciare l'ingestione
  ricostruisce tutto.
- L'Archivista estrae da ogni frammento asserzioni atomiche, entità e
  posizione dell'autore, in JSON vincolato da uno schema.
- Il Critico confronta coppie di asserzioni dello stesso tema e conferma le
  contraddizioni. Con Llama 3.1 8B quattro contraddizioni «confermate» su
  cinque erano falsi positivi; con Qwen2.5 7B no, e il ruolo è passato a lui.
- Due modelli: un router da 3 miliardi (Qwen2.5 3B) per classificare, e un
  motore da 7-8 miliardi per estrazione e risposte, con al massimo cinque
  frammenti di contesto. bge-m3 ha preso il posto di nomic-embed-text.
- `zeno sync` mette in pari l'archivio a ogni avvio, anche con i file
  aggiunti a Zeno spento.
- Il corpus è diventato solo Markdown: PDF, DOCX e HTML si convertono una
  volta, con l'originale messo da parte.
- La dashboard ha un grafo 3D, con la libreria JavaScript inclusa nel codice.

Le risposte richiedevano da 35 a 120 secondi. L'autore ha scelto la qualità
sulla velocità e ha accettato quei tempi.

### Passaggio a MLX (settembre 2026)

Il 5 settembre Zeno ha lasciato Ollama per MLX, il framework di Apple per i
propri processori. Quattro scelte tecniche sono servite a non perdere nulla:

- **Un demone, non una libreria in ogni processo.** Chat e lavori in
  background girano in processi diversi. Con MLX importato in ognuno, due
  processi avrebbero caricato due copie dello stesso modello. Il demone
  `mlx_server` ne tiene una sola e risponde su `127.0.0.1:11500`, con
  un'interfaccia modellata su quella di Ollama, così il resto del codice è
  cambiato poco.
- **JSON vincolato con llguidance.** `mlx_lm` non ha un'uscita strutturata, e
  il motore fa `json.loads()` sulla risposta in otto punti. llguidance impone
  la grammatica token per token.
- **Encoder bge-m3 scritto in MLX.** La libreria `mlx-embeddings` ha licenza
  GPL-3 e calcola la media dei token, mentre bge-m3 usa il token iniziale
  (CLS). Con il pooling sbagliato i vettori nuovi non sarebbero stati
  confrontabili con quelli già salvati. Su 50 frammenti il coseno medio fra
  vettori MLX e vettori Ollama è 0,999978 (minimo 0,999794): nessun frammento
  ha dovuto essere ricalcolato.
- **Prompt intero.** Ollama tagliava in silenzio la coda del prompt, cioè la
  domanda, quando superava la finestra di contesto. Con MLX il prompt passa
  intero.

Il collaudo ha trovato un guasto nuovo. Qwen3-8B, obbligato dalla grammatica
a scrivere JSON, ha prodotto due asserzioni corrette e poi 15.051 ritorni a
capo di fila: gli spazi fra i token JSON erano tutti leciti. Vietarli del
tutto peggiorava le estrazioni, perché il modello rispondeva con una lista
vuota. Il tetto di 8 caratteri di spazio fra un token e l'altro chiude quel
guasto per qualunque modello.

### Recupero e misura (settembre-ottobre 2026)

La ricerca per parole non vedeva i titoli dei file, non riconosceva singolari
e plurali e restituiva i risultati senza ordinarli per pertinenza. Sono stati
corretti tutti e tre i difetti (vedi [Recupero](#5-recupero)). Poi è arrivato
`zeno valuta`, per misurare prima di decidere: un set di domande scritte
dall'utente, un set sintetico per il solo recupero e il pollice su o giù in
chat.

### Un modello, istruzioni nuove, recupero garantito (novembre 2026)

Fra il 15 e il 18 novembre l'autore ha costruito il set di prova (30 domande),
ha riscritto quattro volte le istruzioni della modalità ARCHIVIO, ha
introdotto il posto garantito per la ricerca per parole e ha portato
Qwen3.5-4B su tutti i ruoli. Il 18 novembre è arrivato anche un guardiano: se
un calcolo sulla GPU non torna per 120 secondi, il demone si chiude e la
richiesta successiva lo riavvia. Il blocco era capitato davvero e aveva
fermato una prova per 12 ore. Le versioni pubbliche 0.2.0 e 0.2.1 sono del 20
novembre.

## 4. Scelta dei modelli

### 6 settembre: un 4B al posto di tre modelli

Prova su dati reali dell'archivio: 25 asserzioni già classificate per il
router, 5 frammenti per l'estrazione, una domanda con i frammenti davvero
recuperati per la risposta.

| Modello | Router (s a chiamata) | Estrazioni valide | Asserzioni a frammento | Secondi a frammento | Caratteri al secondo in risposta |
|---|---|---|---|---|---|
| Qwen2.5-3B | 1,45 | 4/5 | 3,0 | 29,1 | 40 |
| **Qwen3-4B-Instruct-2507** | 2,30 | **5/5** | **5,0** | 41,5 | **43** |
| Qwen2.5-7B | 4,40 | 3/5 | 2,0 | 67,6 | 24 |
| Llama-3.1-8B | 5,10 | 5/5 | 5,0 | 60,3 | 23 |
| Qwen3-8B | 5,69 | 5/5 | 4,4 | 55,6 | 23 |

Il 4B ha estratto quanto Llama 3.1 8B in un tempo inferiore del 31%, con 2,1
GB di memoria invece di 4,2, e ha dato la risposta più ancorata alle fonti.
Llama ha aperto la risposta negando l'esistenza dei frammenti che aveva
davanti; Qwen2.5-3B ha ricopiato le istruzioni nella risposta. Con un solo
modello per tutti i ruoli è sparito anche lo scambio di modelli in memoria,
che costava da 30 a 60 secondi a ogni cambio. Le risposte sono scese a 26-47
secondi.

Una metrica è stata scartata durante la prova: l'accordo con la
classificazione già presente nel database. Quella classificazione l'aveva
prodotta Qwen2.5-7B, quindi la metrica premiava chi gli somigliava.

Il campione basta a scartare Llama 3.1 8B e Qwen2.5-3B, non a certificare il
4B su ogni tipo di materiale.

### 8 settembre: Qwen3.5-9B scartato

Il checkpoint Qwen3.5-9B in formato MLX è un modello visione-linguaggio;
`mlx_lm` ne carica solo la parte testuale. Stessi dati:

| Misura | Qwen3-4B-Instruct-2507 | Qwen3.5-9B |
|---|---|---|
| Router, secondi a chiamata | **1,56** | 2,78 |
| Asserzioni a frammento | 4,6 | 4,6 |
| Secondi a frammento | **20,5** | 41,7 |
| Secondi a risposta | **16,6** | 30,7 |
| Memoria | **2,1 GB** | 6 GB |

Stessa qualità di estrazione, velocità dimezzata, memoria quasi tripla. I
tempi del 4B sono la metà di quelli del 6 settembre: fra una prova e l'altra
i tempi variano fino al doppio, e contano i rapporti fra modelli misurati
nella stessa prova.

### Novembre: Qwen3.5-4B

Il 15-17 novembre Qwen3.5-4B ha sostituito il 2507 per le risposte, dopo il
lavoro sulle istruzioni descritto nella [sezione 6](#6-qualità-delle-risposte).
Qwen3-8B, rimesso alla prova sulle risposte, ha inventato tre risposte contro
zero del 4B ed è stato scartato. Tre modelli decisionali esterni, provati
sulla sola decisione «i frammenti bastano?», non hanno battuto il Qwen già in
memoria con JSON vincolato (29 decisioni giuste su 30): uno lo pareggiava ma
occupava circa 10 GB.

Il 18 novembre una prova in sola lettura ha confrontato i due 4B anche su
estrazione e temi: 12 frammenti, 4 per strato, e 40 asserzioni da assegnare a
un tema.

| Modello | Estrazioni valide | Asserzioni a frammento | Secondi a frammento | Aderenza al testo (coseno) | Temi d'accordo con l'archivio |
|---|---|---|---|---|---|
| Qwen3-4B-Instruct-2507 | 12/12 | 4,5 | 22,5 | 0,574 | 21/40 |
| Qwen3.5-4B | 12/12 | 4,1 | 21,4 | 0,573 | 22/40 |

Il campione è piccolo e serve a escludere un peggioramento più che a
dimostrare un miglioramento. Da quel giorno un solo modello,
`mlx-community/Qwen3.5-4B-4bit`, fa tutto. Qwen3.5 scrive JSON più lunghi: con
un tetto di 100 token falliva 12 assegnazioni di tema su 40, con 400 nessuna.

## 5. Recupero

### Ricerca per parole (settembre)

Tre difetti della ricerca FTS5, trovati con domande che citavano il titolo di
un documento:

1. L'indice testuale conteneva solo il corpo del frammento. Una parola
   presente solo nel titolo non trovava nulla. Ora l'indice contiene titolo,
   sezione e testo, la stessa stringa usata per l'embedding.
2. Il tokenizer non riduce le parole alla radice: «citazione» e «citazioni»
   erano due parole diverse. Ora la ricerca usa il prefisso e taglia l'ultima
   lettera alle parole di almeno sei caratteri.
3. Senza `ORDER BY rank` una ricerca che trova migliaia di frammenti ne
   prendeva dieci a caso. Ora li ordina per bm25, e il punteggio di un
   frammento trovato solo per parole scala con la sua posizione.

Un limite resta: una domanda di categoria («le ultime citazioni che ho
trascritto») chiede un elenco di documenti di un tipo, e nessun recupero per
somiglianza la risolve con i pesi.

### 200 domande sintetiche (settembre)

Il modello locale ha scritto 200 domande a partire da frammenti presi a caso:
40% esplicito, 40% acquisito, 20% dialogico. Il frammento d'origine è la
risposta attesa. I valori assoluti sono ottimisti, perché la domanda somiglia
al frammento, e servono a confrontare varianti, con un margine di circa
quattro punti percentuali.

| Variante | Fonte al 1° posto | Fonte fra le prime 5 | Frammento fra i primi 5 | MRR@10 |
|---|---|---|---|---|
| Configurazione di settembre (vettori, testo, pesi) | 0,485 | 0,875 | 0,560 | 0,637 |
| Senza pesi di strato, conoscenza e genere | 0,770 | 0,925 | 0,680 | 0,834 |
| Senza il solo peso di strato | 0,680 | 0,910 | 0,590 | 0,775 |
| Peso di strato attenuato (1,15 invece di 1,5) | 0,605 | 0,895 | 0,575 | 0,722 |
| Più asserzioni (massimo del coseno) | 0,280 | 0,680 | 0,435 | 0,434 |
| Più asserzioni (fusione per ranghi) | 0,455 | 0,850 | 0,505 | 0,617 |
| Più reranker bge-reranker-v2-m3 | **0,820** | **0,945** | **0,755** | **0,873** |

- I pesi di strato spingono in basso alcune fonti giuste. È in parte voluto,
  perché la voce dell'utente viene prima, ma su domande neutre abbassano il
  punteggio. I pesi sono rimasti.
- Aggiungere le asserzioni al recupero peggiora in entrambe le fusioni
  provate. Le asserzioni servono a temi, grafo e contraddizioni, non al
  recupero.
- Il reranker dà il guadagno più grande sul recupero, ma costa da 4 a 10
  secondi a domanda e 1,1 GB di memoria. Misurato poi sulle risposte del set
  reale non ha migliorato nulla, ed è stato tolto.

### Posto garantito e niente doppioni (18 novembre)

Sul set reale, in due domande il brano giusto era primo per bm25 ma fra il
10° e il 26° posto per vettori, e due pezzi dello stesso file gli rubavano il
posto. Due correzioni:

- il primo risultato della ricerca per parole entra sempre fra i cinque
  frammenti, al posto del secondo pezzo di un file già presente;
- i frammenti con parole uguali oltre l'85% (indice di Jaccard) vengono
  scartati.

Risultato: il brano giusto arriva al modello in 21 domande su 23 (prima 19),
senza doppioni. Sulle 200 domande sintetiche la fonte giusta fra le prime
cinque passa da 0,87 a 0,89 e il frammento giusto fra i primi cinque da
0,555 a 0,64. Pesi più forti per la ricerca per parole peggioravano (0,82-0,84).

## 6. Qualità delle risposte

### Il metodo

- **Domande.** Trenta domande reali, ognuna con la risposta attesa scritta
  dall'autore. Quattro tipi: `fatto` (che cosa dice l'archivio), `pensiero_mio`
  (la posizione dell'utente, che deve venire dai suoi scritti), `citazione` e
  `lacuna`. Le sette domande `lacuna` riguardano cose che l'archivio non
  contiene: la risposta corretta è dichiararlo.
- **Giudice.** Un modello locale, Qwen3-4B-Instruct-2507, uguale per tutte le
  configurazioni, assegna da 0 a 2 punti per la **copertura** (quanta parte
  della risposta attesa contiene) e per la **fedeltà** (quanto si attiene ai
  documenti senza aggiungere ciò che non c'è).
- **Esito.** Una risposta è corretta se copre per intero la risposta attesa
  senza contraddirla; su una domanda `lacuna`, se dichiara la lacuna.
- **Lacune segnalate a torto.** Conta ogni «LACUNA» nel testo delle risposte
  alle domande non `lacuna`, comprese quelle parziali dentro una risposta
  giusta.
- **Tempi.** A modello caldo, sul MacBook Air M3 da 16 GB.

### I risultati

| Configurazione | Esiti corretti | Copertura | Fedeltà | Lacune riconosciute | Lacune a torto | Secondi (mediana) |
|---|---|---|---|---|---|---|
| Qwen3-4B-2507, istruzioni v1 | 29/30 | 1,96 | 1,70 | 7/7 | 2 | 52 |
| Qwen3-4B-2507, istruzioni v2 | 27/30 | 1,78 | 1,87 | 7/7 | 3 | 29 |
| Qwen3.5-4B, istruzioni v1 | 26/30 | 2,00 | 1,83 | 3/7 | 3 | 38 |
| Qwen3.5-4B, istruzioni v2 | 28/30 | 1,87 | 1,83 | 7/7 | 4 | 24 |
| Qwen3.5-4B, v3 (decisione nella stessa risposta) | 29/30 | 1,91 | 1,77 | 7/7 | 10 | 29 |
| Qwen3.5-4B, v4 (decisione a parte) | 28/30 | 1,83 | 1,93 | 7/7 | 6 | 33 |
| Qwen3.5-4B, v4 corretta, cache del prefisso | 27/30 | 1,74 | 1,93 | 7/7 | 3 | 21,5 |
| **Qwen3.5-4B, v4 e recupero garantito (attuale)** | **29/30** | **1,91** | **1,93** | **7/7** | **2** | **18,9** |

Nella configurazione attuale la prima parola compare dopo circa 13 secondi
(erano 21 con la v4 prima della cache). L'estrazione delle asserzioni richiede
circa 21 secondi a frammento.

### Che cosa è cambiato fra una versione e l'altra

- **v1 → v2.** Con le istruzioni iniziali Qwen3.5-4B riconosceva solo 3
  lacune su 7. La v2 chiede di usare solo i documenti, di citare ogni frase
  con il suo numero e di scrivere LACUNA per ogni parte scoperta. Le lacune
  tornano 7 su 7: il difetto stava nelle istruzioni, non nel modello.
- **v3.** Il modello decide «sì o no» nella prima riga della stessa risposta.
  Decide peggio e semina lacune parziali: 10 segnalate a torto.
- **v4.** La decisione passa a una chiamata separata, con risposta vincolata.
  La fedeltà sale a 1,93. Documenti e domanda vanno prima delle regole, così
  le due chiamate condividono lo stesso inizio: il demone lo legge una volta
  e ne copia la cache. Qwen3.5 ha strati ad attenzione lineare la cui cache
  non si accorcia, quindi la cache si copia invece di riavvolgerla; la seconda
  lettura di un testo lungo scende da 48 a 1,7 secondi, con un'uscita
  identica. La v4 aggiunge regole su pertinenza, voce e citazioni, fissa la
  lunghezza in proporzione alla domanda (massimo 300 parole) e chiude il
  flusso quando il modello gira a vuoto.
- **Recupero garantito.** Le due domande con il brano giusto mai arrivato al
  modello diventano risposte corrette. Resta un errore vero della decisione,
  che dice «no» a una domanda `fatto` con i documenti giusti davanti.

## 7. Verifica esterna del 17 novembre

Il giudice locale è piccolo e della stessa famiglia dei modelli giudicati. Per
controllarlo l'autore ha fatto una verifica una tantum con Claude (Anthropic),
con un consenso esplicito e limitato a quella prova: i testi del set di prova
sono usciti dal Mac solo quella volta. Il codice di Zeno non contiene alcuna
chiamata a servizi esterni, e la verifica è stata fatta fuori dal programma.

Claude ha letto alla cieca 210 risposte (7 configurazioni per 30 domande),
con le etichette mescolate. La configurazione attuale, con il recupero
garantito del 18 novembre, non c'era ancora.

| Istruzioni | Fedeltà, giudice locale | Fedeltà, lettura esterna |
|---|---|---|
| v1 | 1,70 (2507) · 1,83 (3.5) | 1,03-1,07 |
| v2 | 1,87 (2507) · 1,83 (3.5) | 1,67-1,70 |
| v3 | 1,77 | 1,43-1,47 |
| v4 | 1,93 | 1,67 |

Il giudice locale è stato troppo indulgente soprattutto sulle risposte lunghe
della v1. La lettura esterna ha trovato difetti che i punteggi non mostravano:

- risposte tagliate a metà dal tetto di 600 token (17 su 30 con la v1);
- giri a vuoto di Qwen3.5, con righe ripetute;
- lacune dichiarate su cose che la domanda non chiedeva;
- documenti estranei mescolati alla risposta;
- voce sbagliata: testi altrui, come un narratore o le slide di un corso,
  attribuiti all'utente;
- profilo cognitivo che trapela nel testo e domande rivolte all'utente in
  modalità ARCHIVIO;
- lo stesso documento presente due volte nei cinque frammenti.

La v4 adottata corregge tutti questi punti. Un conteggio automatico sulle sue
30 risposte ha trovato 0 risposte senza citazioni, 0 righe ripetute, 0 tracce
del profilo e 1 intestazione superflua. Il tetto della risposta è salito a 900
token.

## 8. Limiti

> I valori vanno letti con cautela. Trenta domande sono poche, e differenze di
> copertura o di fedeltà inferiori a un decimo corrispondono a una o due
> risposte; il giudice è un modello piccolo, della stessa famiglia di quelli
> giudicati e identico a quello [della prima configurazione nella tabella
> della sezione 6], e un giudice di queste
> dimensioni tende a premiare le risposte lunghe, per cui la fedeltà potrebbe
> risultare sovrastimata; il conteggio delle lacune segnalate a torto
> comprende anche le lacune parziali dichiarate dentro una risposta corretta.
> La misura, infine, è stata condotta da chi ha scritto il programma, sul
> proprio archivio.
>
> (tesi, par. 4.1.1; fra parentesi quadre un adattamento: la tesi rimanda
> alla propria tabella)

Altri limiti noti:

- **Un utente.** Zeno ha un archivio e una persona. Istruzioni, cartelle e
  pesi sono pensati per quel caso.
- **Addestramento non praticabile qui.** Un LoRA sul 4B costa circa 41
  secondi a esempio con 8 strati e un picco di 6,9 GB; con contesti di 4.096
  token blocca 14 GB e va in swap. Su questo Mac non è praticabile.
- **Tempi variabili.** Lo stesso modello ha richiesto 41,5 secondi a
  frammento il 6 settembre e 20,5 l'8 settembre. Contano i rapporti nella
  stessa prova.
- **Guardiano parziale.** Il guardiano del demone sorveglia la generazione di
  testo, non embedding né reranker. Il blocco della GPU che lo ha motivato non
  si riproduce a comando, quindi l'uscita del demone con un calcolo appeso è
  provata nei test e non dal vivo.
- **Test.** I test automatici, su dati inventati, coprono il guardiano e la
  gestione delle lacune, non tutto il programma.
- **Domande di categoria.** «Dammi gli ultimi documenti di tipo X» resta
  scoperta (vedi [Recupero](#5-recupero)).

## 9. Prospettive

La tesi propone usi che il prototipo non realizza. Il principale è la scuola:

> L'ipotesi più interessante riguarda la scuola: un server collocato
> nell'istituto, sul quale gira un programma costruito sul modello di Zeno,
> che dopo la lezione pone domande a ciascuno studente sugli argomenti
> trattati, lasciando al docente la guida del percorso. I dati resterebbero
> così all'interno della scuola, senza passare da fornitori esteri o privati
> […].
>
> (tesi, par. 4.1.1)

La stessa tesi elenca ciò che servirebbe e che oggi non esiste:

> L'ipotesi scolastica non è stata né realizzata né sperimentata, e l'attuale
> prototipo gestisce una sola persona; occorrerebbe dunque progettare archivi
> separati per ogni studente, criteri di accesso, istruzioni al modello non
> legate al nome dell'autore e tutele specifiche per i dati dei minori, oltre a
> valutare i costi e la manutenzione a carico degli istituti.
>
> (tesi, par. 4.1.1)

Il nome dell'utente oggi viene dalla configurazione (`identita.nome_utente`).
Hardware più potente permetterebbe modelli più grandi e un adattamento sui
materiali scelti; anche questa resta un'ipotesi.

## Rifare le misure sul tuo archivio

Gli strumenti di misura sono nel codice. A terminale stampano solo numeri; i
testi restano in `~/Zeno/stats/valutazione/`. Tutti i comandi partono da
`~/Zeno/scripts`.

**1. Prepara il set di prova.** Il comando crea `domande.yaml` con le
istruzioni e le voci vuote da compilare: domanda, risposta attesa, tipo, e se
vuoi i nomi dei file che devono comparire fra le fonti.

```bash
cd ~/Zeno/scripts && ../.venv_zeno/bin/python -m zeno_engine valuta prepara
```

```bash
open -e ~/Zeno/stats/valutazione/domande.yaml
```

Servono almeno 30 domande, meglio 50, di cui una decina di tipo `lacuna`.

**2. Misura le risposte.** Zeno pone ogni domanda con le stesse istruzioni
della chat e salva gli esiti in `stats/valutazione/esiti/`. Il giudice di
serie è il modello delle risposte; `--giudice` ne sceglie un altro.

```bash
../.venv_zeno/bin/python -m zeno_engine valuta risposte --etichetta base
```

**3. Confronta due configurazioni.** Rifai la misura con un altro modello,
poi confronta i due file di esiti con il test del segno appaiato.

```bash
../.venv_zeno/bin/python -m zeno_engine valuta risposte --modello mlx-community/Qwen3-8B-4bit --etichetta prova
```

```bash
../.venv_zeno/bin/python -m zeno_engine valuta confronta ~/Zeno/stats/valutazione/esiti/<file_base>.jsonl ~/Zeno/stats/valutazione/esiti/<file_prova>.jsonl
```

**4. Misura il solo recupero.** Il primo comando scrive 200 domande
sintetiche (circa 20 minuti), il secondo misura quante volte la fonte giusta
arriva fra le prime.

```bash
../.venv_zeno/bin/python -m zeno_engine valuta sintetico
```

```bash
../.venv_zeno/bin/python -m zeno_engine valuta recupero
```

**5. Riepiloga i voti della chat** (pollice su o giù):

```bash
../.venv_zeno/bin/python -m zeno_engine valuta voti
```

## Riferimenti

- Tesi: *L'ipercomplessità del dominio informativo egemone. Intelligence, IA e
  approcci innovativi alla sovranità dello Stato*, tesi di Master di II
  livello in Intelligence, a.a. 2025/2026, par. 4.1.1. Autore: lo stesso del
  codice (vedi [CITATION.cff](../CITATION.cff)).
- MLX e mlx-lm: <https://github.com/ml-explore/mlx>,
  <https://github.com/ml-explore/mlx-lm>
- llguidance: <https://github.com/guidance-ai/llguidance>
- sqlite-vec: <https://github.com/asg017/sqlite-vec>
- Qwen3.5-4B in formato MLX: <https://huggingface.co/mlx-community/Qwen3.5-4B-4bit>
- bge-m3 in formato MLX: <https://huggingface.co/mlx-community/bge-m3-mlx-fp16>
- Chen, J. et al. (2024). *BGE M3-Embedding: Multi-Lingual, Multi-Functionality,
  Multi-Granularity Text Embeddings Through Self-Knowledge Distillation*.
  arXiv:2402.03216.
