# Parte teorica

> Risposte ragionate, senza codice. L'ordine segue le domande del brief.

## 1) Metriche per giocatore

Tre metriche che calcolerei, tutte derivabili dagli eventi già presenti nello
schema. Le calcolo a livello partita-giocatore (la grana naturale del dato) e
poi le aggrego a stagione-giocatore con tassi per 90 minuti, così da rendere
confrontabili giocatori con minutaggi diversi.

1. Finalizzazione (xG, gol, gol − xG). Eventi usati: `shot`, con i campi `xg` e
   `outcome="goal"`. Per ogni giocatore sommo l'`xg` dei tiri e conto i gol; la
   differenza gol − xG dice se ha reso più o meno di quanto valessero le
   occasioni. È più stabile del semplice conteggio gol perché tiene conto di
   quanto erano buone le occasioni. La strutturo come colonne in
   `player_match_stats` e la sommo in `player_season_stats`, dove aggiungo anche
   gol/90 e xG/90.

2. Qualità di passaggio (% completamento e volume). Eventi: `pass`, con
   `outcome` in `{complete, incomplete}`. Per giocatore: completati su totali.
   Tengo sempre il volume accanto alla percentuale, perché una percentuale alta
   su cinque passaggi non dice niente, quindi filtro per un minimo di passaggi.
   Con più tempo la estenderei ai passaggi progressivi o nell'ultimo terzo
   usando le coordinate `x, y`.

3. Contributo difensivo (% contrasti vinti e contrasti/90). Eventi: `tackle`
   (`won`/`lost`), con i `foul` come contorno. Misura l'efficacia difensiva
   (tasso di successo) e il volume normalizzato sui minuti. Le due insieme danno
   un quadro più onesto del solo conteggio.

Le ho implementate come bonus in `metrics.py`. A questa scala ricalcolo l'intero
gold ogni volta che cambia il silver (è sotto il secondo); su volumi grandi
ricalcolerei solo i `match_id` toccati e ri-aggregherei i gruppi stagione
interessati.

---

## 2) Data warehouse

### 2.1 AWS (Redshift/Athena) vs BigQuery vs Snowflake

Dato il vostro contesto (dati a livello di GB, batch settimanale, team piccolo,
attenzione ai costi, Parquet su object storage, e la volontà di evitare lock-in
dove possibile), la mia raccomandazione è restare file-first: tenere i Parquet
come fonte di verità e metterci sopra un motore di query, scegliendo lo
strumento che preserva quei file aperti.

| Criterio | Athena (AWS) | BigQuery | Snowflake | Redshift (provisioned) |
|---|---|---|---|---|
| Modello | Serverless, query su file S3 | Serverless, storage e compute separati | Cluster auto-sospendibili | Cluster da gestire (RA3/Serverless mitigano) |
| Costo | Per TB scansionato | Per TB scansionato o a slot | Per secondo di compute | Per ora di cluster |
| Rischio | Costo a sorpresa se le dashboard riscansionano | Idem, mitigato da BI Engine/cache | Costo idle se non auto-sospende | Idle e gestione |
| Lock-in | Basso (dati su S3) | Medio (mitigabile con BigLake + Iceberg) | Medio (external table/Iceberg, multi-cloud) | Alto |
| Tableau | Connettore live, occhio ai costi di scan | Connettore ottimo, con BI Engine | Connettore molto maturo | Connettore maturo |

Se dovessi sceglierne uno solo prenderei BigQuery. È serverless, quindi non c'è
un cluster da gestire per un team piccolo; separa storage e compute; costa per
consumo; ha un ottimo connettore Tableau (con BI Engine ad accelerare le query);
e può leggere i Parquet esterni su GCS via BigLake, così la fonte di verità
resta in file aperti e il lock-in resta contenuto. Snowflake è la seconda scelta
quando contano molto la concorrenza (warehouse multi-cluster) e il multi-cloud.
Redshift provisioned lo eviterei, per la gestione del cluster e il lock-in;
Athena invece è di fatto la risposta "niente warehouse" lato AWS (vedi 2.2).

### 2.2 Quando non serve un warehouse, e quando invece sì

Restare su file più un motore leggero (Parquet su S3/GCS interrogato da
DuckDB, Athena o Trino) conviene quando i dati sono a livello di GB, i carichi
sono batch e poco frequenti, gli utenti sono pochi, e si vogliono costo idle
minimo e zero lock-in. È il vostro caso oggi: più semplice, più economico (si
paga storage e compute solo quando serve) e i dati restano aperti.

Il warehouse diventa necessario quando subentra almeno una di queste cose: molti
utenti BI concorrenti che si aspettano risposte sotto il secondo; bisogno di
governance, permessi e condivisione strutturati; join complessi e ripetuti su
tabelle grandi; molti scrittori concorrenti; SLA di servizio. In pratica, quando
il costo di gestire file e query a mano supera il costo (e la comodità) di un
servizio gestito.

### 2.3 Con molti TB di tracking o più utenti concorrenti

La scelta si sposta verso un motore gestito con storage e compute separati
(BigQuery o Snowflake), ma senza caricarci dentro il tracking grezzo:

- Il tracking/GPS, potenzialmente TB, resta su object storage in Parquet
  partizionato (per competizione, stagione, partita), idealmente in un formato
  tabellare aperto come Iceberg o Delta. È l'antidoto al lock-in: gli stessi
  dati restano interrogabili da più motori.
- Si interroga in modo selettivo (partition pruning, solo le colonne che
  servono); per una dashboard non si fa mai una scansione integrale.
- Si pre-aggrega il livello gold, così Tableau e l'assistente AI leggono tabelle
  piccole invece del tracking grezzo.
- Per la concorrenza si usano warehouse multi-cluster (Snowflake) o un
  serverless che scala (BigQuery), e/o estratti Tableau `.hyper` rigenerati col
  batch settimanale, che tolgono carico al motore.
- Le pipeline pesanti sul tracking possono girare su Spark o un engine dedicato
  che scrive Parquet/Iceberg, tenendo separati i costi di trasformazione.

### 2.4 Esempio concreto per il vostro caso

I dati starebbero su object storage, in un lakehouse a strati:

```
s3://club-data/
  bronze/ (raw)      JSON come arrivano, immutabili, storage class economica
  silver/ (clean)    Parquet tipizzati, partizionati per competizione/stagione
  gold/   (final)    metriche aggregate (player/match, player/season) in Parquet
```

È esattamente la struttura della pipeline pratica (`raw → silver → gold`).

Tableau lo collegherei al livello gold, non al grezzo, con due opzioni a seconda
del volume. Oggi (GB, batch settimanale) userei estratti `.hyper` rigenerati dal
gold a ogni batch: dashboard velocissime e costo di query quasi nullo. Domani
(più dati o più utenti) passerei a una connessione live verso BigQuery o
Snowflake su external table del gold, con BI Engine o warehouse auto-sospeso.

Sui costi e su quando si pagano: lo storage costa pochi centesimi per GB al
mese, sempre, e cresce piano col bronze. Il compute di trasformazione si paga
una volta a settimana col batch incrementale, e solo sulle partite nuove o
cambiate, come fa già la pipeline. Il compute di query si paga solo quando si
interroga, e con gli estratti `.hyper` le dashboard non generano scan, quindi
quasi nulla. Il compute lo si scala solo quando arriva il tracking, lasciando
invariato il resto: niente cluster sempre acceso, niente costo idle, si paga
ciò che serve.

In sintesi: lakehouse aperto (Parquet/Iceberg su object storage), trasformazione
batch incrementale, e sopra un motore serverless o gestito, adottato solo quando
concorrenza e volumi lo richiedono. Buona resa, lock-in minimo, costi
proporzionali all'uso.
