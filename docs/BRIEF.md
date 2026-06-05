# Florentina project

Esercizio tecnico
Parte pratica (pipeline in locale) + parte teorica (metriche & data warehouse) · dati forniti · ~6–7 giorni (part-time)
Contesto
Lavoriamo con dati di partite che arrivano come file JSON: li trasformiamo in dati puliti, in modo incrementale. L'esercizio ha due parti: una pratica (costruisci una piccola pipeline, in locale) e una teorica (ragionamenti, senza codice). I dati (finti) te li diamo noi.
Parte pratica (codice)
Nello zip trovi file JSON di partite: un primo lotto e un secondo lotto (con qualche partita nuova e una partita corretta). Lo schema dei campi è in SCHEMA.md. Obiettivo:
•	Trasforma i JSON delle partite in dati puliti e interrogabili (tipizzati, ordinati).
•	Rendi il tutto incrementale: ri-eseguire deve elaborare solo le partite nuove o cambiate, non tutto da capo.
•	Fai in modo che si possano rispondere ad alcune domande sui dati che ottieni (es. conteggi o filtri).
Requisiti minimi: si esegue con un comando ed è riproducibile (breve README); gestisce sia il lotto iniziale sia quello di update, con incrementale vero; codice organizzato in più moduli/funzioni. Strumenti, struttura e formati li scegli tu — ti chiederemo di spiegarci le scelte.
Parte teorica (a parole, senza codice)
1) Metriche. Senza implementarle, descrivi 2-3 metriche per giocatore che calcoleresti e come le otterresti (quali eventi useresti, a che livello le calcoleresti, come le strutturresti).
2) Data warehouse. Il nostro contesto (le informazioni che ti servono per ragionare):
•	Dati: eventi delle partite in JSON (~40 campionati, 2 stagioni); in futuro possibili dati di tracking/GPS, molto pesanti (potenzialmente terabyte).
•	I dati elaborati li teniamo in file aperti (es. Parquet) su storage a oggetti.
•	Chi li usa: un sito proprietario, dashboard in Tableau, un assistente AI (risposte in linguaggio naturale sui dati), qualche analista. Team interno piccolo.
•	Carichi per lo più batch (aggiornamento settimanale), incrementali.
•	Attenti ai costi (paghiamo per ciò che serve, non a tutti i costi); vogliamo scalare senza riscrivere tutto ed evitare lock-in dove possibile.
Domande (in tutto circa mezza pagina):
1.	Tra AWS (es. Redshift o Athena), Google BigQuery e Snowflake, quale sceglieresti per il nostro caso e perché? Confronta costi, modello (serverless vs cluster), scalabilità, lock-in e integrazione con Tableau.
2.	Quando conviene non usare un data warehouse e restare su file + un motore leggero che li interroga? E quando il warehouse diventa davvero necessario?
3.	Come cambierebbe la tua scelta con molti TB di dati di tracking, o con più utenti concorrenti?
4.	Fai un esempio concreto per il nostro caso: dove metteresti i dati grezzi, intermedi e finali, come collegheresti Tableau, e cosa pagheremmo (e quando).
Non cerchiamo la risposta “giusta”: contano il ragionamento, i compromessi che evidenzi e gli esempi concreti.
Se ti avanza tempo (facoltativo)
Se vuoi, implementa una delle metriche che hai descritto, oppure aggiungi qualche test. Nulla di obbligatorio: meglio poco e fatto bene.
