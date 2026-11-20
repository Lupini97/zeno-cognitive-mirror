# Privacy

Zeno lavora sul tuo computer. Documenti, archivio (`zeno.db`), modelli e
risposte restano nella cartella in cui lo installi.

## Che cosa esce dal computer

| Quando | Verso | Che cosa parte | Come spegnerlo |
|---|---|---|---|
| Installazione e primo avvio | huggingface.co | la richiesta dei pesi dei modelli | `rete.download_modelli: false` |
| Ogni 30 giorni circa | pypi.org, huggingface.co | i nomi delle librerie e dei modelli, per sapere se ci sono versioni nuove | `rete.controllo_aggiornamenti: false` |
| Quando premi «Cerca sul web», con «Integrazione web» acceso | il motore di ricerca di `ddgs` | il testo della ricerca che vedi nel campo | lascia spento «Integrazione web» (è spento di serie) |
| Quando premi «Importa pagina», con «Integrazione web» acceso | il sito indicato | la richiesta di quella pagina | lascia spento «Integrazione web» |

I tuoi testi, le domande alla chat e le risposte non vengono mai inviati
altrove. Il codice non contiene alcuna chiamata a servizi di intelligenza
artificiale esterni.

## Che cosa resta sul computer

- `zeno.db`: frammenti dei tuoi documenti, embedding, asserzioni, grafo.
- `stats/`: registri, valutazioni, lacune, giudizi che dai in dashboard.
- `memoria_elaborata/`: sessioni della chat archiviate e schede tema.

Per cancellare tutto basta cancellare la cartella di installazione.

## Avvertenza

Le interfacce di chat e dashboard ascoltano solo su `localhost`. Streamlit
accetta connessioni solo da `localhost` e dagli indirizzi del computer
(`enableCORS` e `enableXsrfProtection` accesi), così un sito aperto nel
browser non può leggere Zeno.
