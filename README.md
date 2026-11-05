# Zeno

**English summary.** Zeno is a local research prototype of a personal epistemic engine, designed as a "cognitive mirror": it organises what a person has written and read, surfaces connections and contradictions, and asks questions rather than only giving answers. It runs entirely on the user's own computer. It is not a product and has not been validated on real users. This page describes the project; the source code is not published yet.

---

Zeno è un prototipo di ricerca di motore epistemico personale, pensato come «specchio cognitivo»: organizza nel tempo ciò che una persona ha scritto e letto, ne fa emergere connessioni e contraddizioni e pone domande invece di limitarsi a fornire risposte.

> **Stato di questa pagina.** Questa pagina descrive il progetto. Il codice non è ancora pubblico: prima va ripulito dai dati personali presenti nel codice e nella cronologia delle versioni. Qui non ci sono dati personali né contenuti dell'archivio dell'autore, che non è destinato alla pubblicazione.

## Che cosa è

- Un prototipo di ricerca, sviluppato da una sola persona.
- Un sistema locale: archivio, indice, modelli e risposte restano sul computer dell'utente.
- Uno strumento che riflette il pensiero di chi lo usa e lo interroga.

## Che cosa non è

- Non è un prodotto e non è pronto per un uso generale.
- Non è stato validato su utenti reali, e la qualità delle risposte non è stata misurata in modo sistematico.
- Non addestra alcun modello: arricchire l'archivio migliora soltanto il recupero delle informazioni.
- Non è una coscienza in senso letterale.

## Come funziona

Il prototipo gira su un MacBook Air M3 con 16 GB di memoria.

- **Archivio:** SQLite, con sqlite-vec per la ricerca vettoriale e FTS5 per quella testuale.
- **Modelli:** bge-m3 per gli embedding, Qwen3-4B per la generazione del testo, eseguiti con MLX. Le versioni precedenti usavano Ollama e Llama 3.1 8B.
- **Quattro strati di conoscenza**, con priorità diverse nel recupero: esplicito (ciò che la persona ha scritto), acquisito (libri, articoli, formazione), dialogico (conversazioni archiviate), inferito (asserzioni ed entità estratte dal modello).
- **Risposte:** ogni domanda combina ricerca vettoriale e testuale, ripesa i risultati per strato e passa al modello al massimo cinque frammenti. Se non bastano, la risposta dichiara la lacuna invece di colmarla per inferenza.
- **Grafo:** costruito con NetworkX, con rilevamento di comunità tramite l'algoritmo di Louvain. Alimenta le elaborazioni periodiche e la consultazione dell'archivio, ed è esportabile in un vault compatibile con Obsidian.
- **Domande:** altre componenti cercano contraddizioni tra asserzioni e generano domande socratiche a partire dall'archivio.

Tre moduli sono implementati ma non attivi nella configurazione descritta: il router basato su modello linguistico, l'espansione iterativa del recupero tramite entità e il riordino dei risultati (reranker).

## Rete

Il funzionamento di base resta sul computer dell'utente, senza servizi cloud per archivio e risposte. La rete serve solo per scaricare i modelli, per il controllo degli aggiornamenti e per il pulsante facoltativo «Cerca sul web», che invia la ricerca a un motore esterno quando l'utente lo preme.

## Limiti

I test automatici esistenti coprono soltanto una parte del sistema, relativa all'importazione delle letture. Le osservazioni sul prototipo derivano dall'uso personale dell'autore e non sono generalizzabili.

## Come citare

Michele Modesto (2026). *Zeno: prototipo di ricerca di motore epistemico personale*. I metadati sono in [`CITATION.cff`](CITATION.cff).

## Licenza

[MIT](LICENSE).
