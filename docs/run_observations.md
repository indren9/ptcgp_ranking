# Diario Run

Diario operativo per monitorare le run Pocket / Pokemon TCG nei prossimi
giorni.

Obiettivo: capire se ranking, diagnostiche, wildcard e tempi di esecuzione
restano stabili nell'uso quotidiano. Aggiungere una voce per ogni run.

Scope monitoraggio:
- Pocket: solo ultimo set attivo, `B3b - Everyday Wonders`.
- Pokemon TCG: set corrente, `PBL - Pitch Black`, quando viene runnato.
- `CRI - Chaos Rising` resta nel diario come baseline storica chiusa.
- I set Pocket storici non vengono monitorati nel diario perche' non cambiano
  piu'.

## Pocket Trend Watch

Set osservato: `B3b - Everyday Wonders`.

Decisione attuale: mantenere il ranking principale conservativo con candidate
pool all'80% della share cumulata e filtro NaN iterativo. Le wildcard restano
diagnostiche e non entrano automaticamente nel ranking MARS.

Trend iniziale:

```text
Data        Raw deck  Top meta  Core MARS  Ranking coverage min/med  NaN pre max
2026-07-14       448        47         38  86.49% / 95.95%           0.3261
2026-07-15       461        47         39  84.21% / 94.74%           0.3478
2026-07-16       476        47         40  84.62% / 94.87%           0.3913
2026-07-18       n/a        47         40  84.62% / 94.87%           0.3696
2026-07-21       508        46         40  84.62% / 94.87%           0.3333
```

Lettura provvisoria:
- La top meta resta stabile nell'area 46-47 deck.
- Il core cresce lentamente da 38 a 40 deck, senza salti improvvisi.
- La top 5 e' stabile nei nomi, con piccoli scambi di posizione interni.
- La coverage del ranking resta accettabile e stabile.
- Dopo il picco del 2026-07-16, la diagnostica NaN pre-filtro migliora: il
  2026-07-21 il massimo scende a 0.3333 e le righe critiche scendono a 34.
- Pocket resta il caso da monitorare, ma i dati recenti supportano i default
  attuali.

Prossime verifiche:
- Se il core resta circa 38-42 deck e la top 5 resta stabile, i default sono
  probabilmente corretti.
- Se `nan_ratio max` continua a salire oltre circa 0.40 o la coverage minima
  del ranking scende sotto circa 80%, rivedere il filtro NaN o la candidate
  pool.
- Se compaiono wildcard con coverage alta e buon volume contro il core,
  discuterle come appendice, non come promozione automatica.

## Template

```text
Data:
Profilo:
Set:
Core MARS rows:
Top 5:
Wildcard high-confidence:
Tempo run:
Problemi:
Osservazioni:
```

## Entries

### 2026-07-14 - tcg

```text
Data: 2026-07-14
Profilo: tcg
Set: CRI - Chaos Rising
Core MARS rows: 27
Top 5:
  1. Dragapult
  2. Crustle
  3. Dragapult Blaziken
  4. Slowking
  5. Raging Bolt Ogerpon
Wildcard high-confidence: n/a; profilo standard, wildcard non attiva
Tempo run: circa 4.0 minuti (10:47:53 -> 10:51:54)
Problemi: nessuno rilevato
Osservazioni:
  - Decklist raw: 133 deck.
  - Top meta / fetch matchup: 27 deck.
  - Matchup raw rows: 2840.
  - Matchup score rows: 702.
  - Coverage ranking: min 100%, median 100%, max 100%.
  - Diagnostica NaN pre-filtro: 27 deck, nan_ratio max 0.0, critical rows 0.
  - Report Excel generato correttamente.
  - Run pulita e coerente con il profilo tcg standard.
```

### 2026-07-14 - pocket

```text
Data: 2026-07-14
Profilo: pocket
Set: B3b - Everyday Wonders
Core MARS rows: 38
Top 5:
  1. Suicune ex Baxcalibur
  2. Miraidon ex Magnezone
  3. Indeedee ex Giratina ex
  4. Mega Blaziken ex Greninja
  5. Mega Altaria ex Espeon
Wildcard high-confidence: n/a; profilo standard, wildcard non attiva
Tempo run: circa 8.1 minuti (10:56:11 -> 11:04:17)
Problemi: nessuna anomalia bloccante rilevata
Osservazioni:
  - Decklist raw: 448 deck.
  - Top meta / fetch matchup: 47 deck.
  - Matchup raw rows: 4829.
  - Matchup score rows: 1332.
  - Coverage ranking: min 86.49%, median 95.95%, max 100%.
  - Diagnostica NaN pre-filtro: 47 deck, coverage median 89.13%,
    nan_ratio max 0.3261, critical rows 38.
  - I buchi di coverage sono presenti prima del filtro, ma il filtro NaN ha
    ridotto il core da 47 a 38 deck.
  - Report Excel generato correttamente.
```

### 2026-07-15 - tcg

```text
Data: 2026-07-15
Profilo: tcg
Set: CRI - Chaos Rising
Core MARS rows: 27
Top 5:
  1. Dragapult
  2. Crustle
  3. Dragapult Blaziken
  4. Slowking
  5. Raging Bolt Ogerpon
Wildcard high-confidence: n/a; profilo standard, wildcard non attiva
Tempo run: circa 3.5 minuti (11:05:53 -> 11:09:22)
Problemi: nessuno rilevato
Osservazioni:
  - Decklist raw: 134 deck.
  - Top meta / fetch matchup: 27 deck.
  - Matchup raw rows: 2849.
  - Matchup score rows: 702.
  - Coverage ranking: min 100%, median 100%, max 100%.
  - Diagnostica NaN pre-filtro: 27 deck, nan_ratio max 0.0, critical rows 0.
  - Report Excel generato correttamente.
  - Nessuna anomalia rispetto al 2026-07-14: core, top 5 e coverage stabili.
```

### 2026-07-15 - pocket

```text
Data: 2026-07-15
Profilo: pocket
Set: B3b - Everyday Wonders
Core MARS rows: 39
Top 5:
  1. Suicune ex Baxcalibur
  2. Miraidon ex Magnezone
  3. Mega Blaziken ex Greninja
  4. Indeedee ex Giratina ex
  5. Mega Altaria ex Espeon
Wildcard high-confidence: n/a; profilo standard, wildcard non attiva
Tempo run: circa 6.7 minuti (10:55:46 -> 11:02:26)
Problemi: nessuna anomalia bloccante rilevata
Osservazioni:
  - Decklist raw: 461 deck.
  - Top meta / fetch matchup: 47 deck.
  - Matchup raw rows: 4955.
  - Matchup score rows: 1396.
  - Coverage ranking: min 84.21%, median 94.74%, max 100%.
  - Diagnostica NaN pre-filtro: 47 deck, coverage median 89.13%,
    nan_ratio max 0.3478, critical rows 37.
  - Il core cresce da 38 a 39 deck rispetto al 2026-07-14.
  - Top 5 stabile nei nomi; Mega Blaziken ex Greninja e Indeedee ex Giratina
    ex si scambiano posizione.
  - Coverage minima leggermente piu' bassa rispetto al 2026-07-14, ma coerente
    con la dinamica Pocket e non bloccante.
  - Report Excel generato correttamente.
```

### 2026-07-16 - pocket

```text
Data: 2026-07-16
Profilo: pocket
Set: B3b - Everyday Wonders
Core MARS rows: 40
Top 5:
  1. Suicune ex Baxcalibur
  2. Miraidon ex Magnezone
  3. Mega Blaziken ex Greninja
  4. Mega Altaria ex Espeon
  5. Indeedee ex Giratina ex
Wildcard high-confidence: n/a; profilo standard, wildcard non attiva
Tempo run: circa 9.0 minuti (17:05:20 -> 17:14:20)
Problemi: nessuna anomalia bloccante rilevata
Osservazioni:
  - Decklist raw: 476 deck.
  - Top meta / fetch matchup: 47 deck.
  - Matchup raw rows: 5074.
  - Matchup score rows: 1466.
  - Coverage ranking: min 84.62%, median 94.87%, max 100%.
  - Diagnostica NaN pre-filtro: 47 deck, coverage median 86.96%,
    nan_ratio max 0.3913, critical rows 37.
  - Il core cresce da 39 a 40 deck rispetto al 2026-07-15.
  - Top 5 stabile nei nomi; Mega Altaria ex Espeon supera Indeedee ex
    Giratina ex.
  - La coverage pre-filtro peggiora leggermente rispetto al 2026-07-15:
    deck piu' critico Arceus ex Crobat, coverage 60.87%, nan_ratio 0.3913.
  - Report Excel generato correttamente.
```

### 2026-07-16 - tcg

```text
Data: 2026-07-16
Profilo: tcg
Set: CRI - Chaos Rising
Core MARS rows: 27
Top 5:
  1. Dragapult
  2. Crustle
  3. Dragapult Blaziken
  4. Slowking
  5. Raging Bolt Ogerpon
Wildcard high-confidence: n/a; profilo standard, wildcard non attiva
Tempo run: circa 1.4 minuti (17:19:31 -> 17:20:54)
Problemi: nessuna anomalia bloccante su CRI standard
Osservazioni:
  - Decklist raw: 134 deck.
  - Top meta / fetch matchup: 27 deck.
  - Matchup raw rows: 2852.
  - Matchup score rows: 702.
  - Coverage ranking: min 100%, median 100%, max 100%.
  - Diagnostica NaN pre-filtro: 27 deck, nan_ratio max 0.0, critical rows 0.
  - Top 5 e core stabili rispetto al 2026-07-15.
  - Nella root TCG risultano 195 directory con ranking latest aggiornato oggi,
    distribuite su vari formati/set. CRI standard e' corretto, ma questo sembra
    indicare una run batch o un profilo notebook che ha toccato molte
    combinazioni oltre allo scope del diario.
  - Report Excel generato correttamente.
```

### 2026-07-17 - tcg

```text
Data: 2026-07-17
Profilo: tcg
Set: CRI - Chaos Rising
Core MARS rows: 27
Top 5:
  1. Dragapult
  2. Crustle
  3. Dragapult Blaziken
  4. Slowking
  5. Raging Bolt Ogerpon
Wildcard high-confidence: n/a; profilo standard, wildcard non attiva
Tempo run: circa 0.2 minuti nella finestra output timestampata
Problemi: nessuno rilevato
Osservazioni:
  - Decklist raw latest al 2026-07-17: 134 deck.
  - Top meta latest al 2026-07-17: 27 deck.
  - Matchup raw latest al 2026-07-17: 2860 rows.
  - Matchup score rows: 702.
  - Coverage ranking: min 100%, median 100%, max 100%.
  - Diagnostica NaN pre-filtro: 27 deck, nan_ratio max 0.0, critical rows 0.
  - Top 5 e core identici al 2026-07-16.
  - TCG standard resta pienamente stabile.
```

### 2026-07-18 - pocket morning

```text
Data: 2026-07-18
Profilo: pocket
Set: B3b - Everyday Wonders
Core MARS rows: 40
Top 5:
  1. Suicune ex Baxcalibur
  2. Miraidon ex Magnezone
  3. Mega Blaziken ex Greninja
  4. Mega Altaria ex Espeon
  5. Indeedee ex Giratina ex
Wildcard high-confidence: n/a; profilo standard, wildcard non attiva
Tempo run: circa 0.1 minuti nella finestra output timestampata; decklist raw
  storico non disponibile perche' salvato solo come latest
Problemi: nessuna anomalia bloccante rilevata
Osservazioni:
  - Top meta / fetch matchup: 47 deck, dedotto dalla diagnostica NaN.
  - Matchup score rows: 1466.
  - Coverage ranking: min 84.62%, median 94.87%, max 100%.
  - Diagnostica NaN pre-filtro: 47 deck, coverage median 86.96%,
    nan_ratio max 0.3696, critical rows 37.
  - Peggior deck pre-filtro: Arceus ex Crobat, coverage 63.04%,
    nan_ratio 0.3696.
  - Run coerente con il 2026-07-16, con NaN max leggermente migliore.
```

### 2026-07-18 - pocket evening

```text
Data: 2026-07-18
Profilo: pocket
Set: B3b - Everyday Wonders
Core MARS rows: 40
Top 5:
  1. Suicune ex Baxcalibur
  2. Miraidon ex Magnezone
  3. Mega Blaziken ex Greninja
  4. Mega Altaria ex Espeon
  5. Indeedee ex Giratina ex
Wildcard high-confidence: n/a; profilo standard, wildcard non attiva
Tempo run: circa 0.0 minuti nella finestra output timestampata; probabile run
  interamente da cache
Problemi: nessuna anomalia bloccante rilevata
Osservazioni:
  - Risultati identici alla run mattutina del 2026-07-18.
  - Matchup score rows: 1466.
  - Coverage ranking: min 84.62%, median 94.87%, max 100%.
  - Diagnostica NaN pre-filtro: 47 deck, coverage median 86.96%,
    nan_ratio max 0.3696, critical rows 37.
  - Conferma di stabilita' intra-day.
```

### 2026-07-21 - pocket

```text
Data: 2026-07-21
Profilo: pocket
Set: B3b - Everyday Wonders
Core MARS rows: 40
Top 5:
  1. Miraidon ex Magnezone
  2. Suicune ex Baxcalibur
  3. Mega Altaria ex Espeon
  4. Mega Blaziken ex Greninja
  5. Indeedee ex Giratina ex
Wildcard high-confidence: n/a; profilo standard, wildcard non attiva
Tempo run: circa 4.7 minuti (12:01:02 -> 12:05:42)
Problemi: nessuna anomalia bloccante rilevata
Osservazioni:
  - Decklist raw: 508 deck.
  - Top meta / fetch matchup: 46 deck.
  - Matchup raw rows: 5269.
  - Matchup score rows: 1486.
  - Coverage ranking: min 84.62%, median 94.87%, max 100%.
  - Diagnostica NaN pre-filtro: 46 deck, coverage median 91.11%,
    nan_ratio max 0.3333, critical rows 34.
  - Il raw cresce molto rispetto al 2026-07-16, ma il core resta stabile a 40.
  - Il top meta passa da 47 a 46 deck senza peggiorare la coverage del ranking.
  - La diagnostica NaN migliora: max 0.3333 contro 0.3913 del 2026-07-16,
    critical rows 34 contro 37.
  - Top 5 stabile nei nomi; Miraidon ex Magnezone supera Suicune ex
    Baxcalibur.
  - Questa run supporta il default attuale: candidate pool 80% + filtro NaN
    iterativo.
```

### 2026-07-21 - tcg

```text
Data: 2026-07-21
Profilo: tcg
Set: PBL - Pitch Black
Core MARS rows: 22
Top 5:
  1. Dragapult Blaziken
  2. Alakazam Dudunsparce
  3. Festival Lead
  4. Dragapult
  5. Grimmsnarl Froslass
Wildcard high-confidence: n/a; profilo standard, wildcard non attiva
Tempo run: circa 2.3 minuti (12:08:05 -> 12:10:22)
Problemi: nessuna anomalia bloccante rilevata
Osservazioni:
  - Nuovo set TCG corrente: CRI - Chaos Rising non viene piu' monitorato come
    set attivo.
  - Decklist raw: 96 deck.
  - Top meta / fetch matchup: 22 deck.
  - Matchup raw rows: 1186.
  - Matchup score rows: 454.
  - Coverage ranking: min 95.24%, median 100%, max 100%.
  - Diagnostica NaN pre-filtro: 22 deck, coverage median 100%,
    nan_ratio max 0.0476, critical rows 8.
  - Peggiori deck pre-filtro tutti a nan_ratio 0.0476: Grimmsnarl Froslass,
    Rocket's Honchkrow, Tera Box, Basic Box, Beedrill.
  - Rispetto a CRI il core e' piu' piccolo, ma la qualita' dati resta alta.
```
