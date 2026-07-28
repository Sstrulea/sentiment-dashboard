# Specificație pre-înregistrată — §M: comparabilitatea indicilor valutari

**Status:** DRAFT pentru review. De commituit pe `diag/scoring-audit` **înainte** de orice măsurătoare sau linie de cod.
**Data redactării:** 2026-07-28
**Referințe:** `docs/diagnostic-scoring-2026-07-27.md` (§A–K), `docs/diagnostic-scoring-2-2026-07-28.md` (§L–Q)

---

## 1. Problema

Indexul valutar se calculează ca medie peste categoriile **prezente**, confirmat empiric în §M:

```
index(NZD) = mean(0, +0.50, +0.667) × 5 = 1.944     # 3 categorii
index(USD) = mean(0, −0.667, +0.333, +2.00) × 5 = 2.083   # 4 categorii
```

Scorul perechii face `(index_base − index_quote) / pair_divisor`, operație care presupune că cei doi indici sunt **același estimator**. Nu sunt.

### 1.1 Trei defecte separabile

**D-a. Non-comparabilitatea diferenței.** Media peste 3 categorii e un estimator nedeplasat al mediei celor 3, nu al mediei celor 4. Omisiunea e nedeplasată doar dacă `monetary` lipsește aleator și are aceeași distribuție ca celelalte. Niciuna nu e adevărată: absența e structurală (NZD/CHF, dintotdeauna) iar §O a arătat că `monetary` atinge ±2 acolo unde growth/inflation/labour se aglomerează în jurul lui 0.

**D-b. Asimetrie de varianță.** O medie peste 3 termeni are varianță mai mare decât una peste 4. Leg-ul mai zgomotos domină cozile diferenței. Perechile cu NZD/CHF au distribuții de scor sistematic mai late — ceea ce interacționează direct cu pragurile p55/p90 calibrate pe distribuția pooled.

**D-c. Cuplaj acoperire ↔ pondere de pilon.** Din §L, numitorul la nivel de leg e `n_categorii + sentiment_weight`:

| valută | numitor | pondere efectivă sentiment |
|---|---|---|
| 3 categorii (NZD, CHF) | 3.5 | **14.29%** |
| 4 categorii (restul) | 4.5 | **11.11%** |

Sentimentul cântărește **cu ~29% mai mult** pe valutele cu date lipsă. Nimeni nu a proiectat asta. Afectează 16/28 perechi.

**D-c este separabil de D-a și D-b** și se tratează ca decizie distinctă (§4).

---

## 2. Ce NU e în scop

Declarate explicit, ca să nu se strecoare în timpul implementării:

- `z_buckets`, `surprise_window_k`, orice prag de indicator
- Compoziția categoriilor, adăugarea de indicatori
- Re-derivarea `mild`/`very` (§Q1) — **vine după**, nu în paralel
- V1 (reponderare piloni) — suspendată până la normalizarea scalelor
- V3 (rank cross-secțional) — respinsă
- Ponderarea categoriilor după conținut informațional istoric — decizie diferită, cu suprafață de fitting, nu se atinge aici

---

## 3. Decizia D1 — tratamentul categoriei absente

### 3.1 Candidați

| | mecanism | afirmația pe care o face |
|---|---|---|
| **A** | Imputare la media cross-secțională a categoriei, la același as-of. Numitor fix 4. | „Valuta lipsă se comportă ca media globală" |
| **B** | Imputare cu 0. Numitor fix 4. | „Nu a existat surpriză" |
| **C** | Excludere: perechile cu leg incomplet nu primesc scor, primesc `insufficient_coverage` | nicio afirmație nemăsurată |
| **D** | Differencing pe intersecția seturilor de categorii prezente în **ambele** legs | nicio afirmație nemăsurată |
| **NULL** | Se păstrează mean-over-present. Se documentează degradarea în docstring + flag vizual | nicio afirmație nemăsurată, dar comparabilitatea rămâne ruptă |

### 3.2 Constrângere normativă — se decide ÎNAINTE de date

Datele nu pot spune dacă imputarea e acceptabilă. E o alegere despre ce afirmație ești dispus să faci. Se decide întâi, și **elimină** candidați înainte de orice măsurătoare.

> **CN-1.** Nicio valoare nemăsurată nu poate intra în index.

Dacă adopți CN-1: **A și B cad** înainte de măsurători. Rămân C, D, NULL.

Argumentul pentru CN-1: **B contrazice direct concluzia din §N** — conversia tăcută unknown → neutral e chiar defectul identificat acolo. A adopta B în index în timp ce repari aceeași conversie în celulele bilaterale e incoerent. A e mai slab decât B ca afirmație, dar importă un factor global într-o valută unde e nemăsurat.

Argumentul contra CN-1: C și D pierd informație măsurată (D aruncă `monetary` USD, deși e reală). CN-1 privilegiază onestitatea față de completitudine.

**Decizie George: CN-1 adoptată / respinsă → ____________**

### 3.3 Compromisul fiecărui candidat rămas

- **C** — maxim conservator. Azi ar scoate din board **16/28 perechi**, inclusiv NZD/USD. Consistent cu „honest capability boundaries".
- **D** — păstrează toate perechile, numitori egali *în interiorul* perechii, zero imputare. Cost: aruncă `monetary` USD pe perechile cu NZD/CHF, iar scorurile nu mai sunt comparabile *între* perechi (perechi diferite folosesc seturi diferite de categorii). Comparabilitatea între perechi era deja compromisă, iar V3 a fost respinsă — deci costul e mai mic decât pare, dar afectează calibrarea pooled p55/p90.
- **NULL** — zero risc de implementare, comparabilitatea rămâne ruptă, dar devine **vizibilă**. E calea „accepted degradation documented" pe care ai folosit-o deja pentru foreign 2y.

---

## 4. Decizia D2 — decuplarea numitorului de pilon

Separabilă de D1. Se poate adopta independent.

| | mecanism |
|---|---|
| **D2-a** | Se păstrează numitorul `n_categorii + w_sent` (comportament actual) |
| **D2-b** | Numitor constant, independent de numărul de categorii prezente |

D2-b nu impută nimic, nu inventează date, nu face nicio afirmație nemăsurată — doar decuplează ponderea de pilon de acoperire. **Nu are cost normativ.** Are cost de distribuție: schimbă toate scorurile.

Notă: dacă D1 = C sau D2-b, defectul D-c dispare parțial ca efect secundar. Măsoară-l separat oricum.

---

## 5. Măsurători pre-înregistrate

Se rulează pe `diag/scoring-audit`, read-only, pe fereastra 2025-07-25 → 2026-07-21 (53 puncte săptămânale), pentru **fiecare candidat supraviețuitor**:

| # | metrică |
|---|---|
| M-1 | Nr. perechi scorabile (acoperire) |
| M-2 | Flip-uri de bias vs starea curentă — tabel complet |
| M-3 | σ cross-secțional, p55, p90, p95, max ale `\|score\|` |
| M-4 | **Pondere efectivă a sentimentului și trendului, per valută.** Metrica-țintă: varianța între valute |
| M-5 | Amprenta de imputare (doar A/B, dacă CN-1 e respinsă): nr. currency-days imputate + distribuția valorilor |
| M-6 | Hit-rate direcțional H=5 și H=10, per candidat vs curent, cu corecție de base-rate per instrument |
| M-7 | Placebo: scoruri permutate temporal, aceeași baterie |

---

## 6. Criterii de acceptare

### 6.1 Primar — structural, NU de performanță

> **Varianța ponderii efective de pilon între valute (M-4) → 0.**

Scopul modificării e restaurarea comparabilității. Aceasta e ținta.

### 6.2 Poartă de nedeteriorare

> Hit-rate direcțional (M-6) **nu scade cu >2pp** la H=5 și H=10, pe nicio clasă de instrumente.

Același prag ca în V5a.

### 6.3 Ce NU e criteriu

> **Îmbunătățirea hit-rate-ului nu e criteriu de selecție.**

Selecția pe hit-rate între 3-5 variante, pe 21 de luni de date de trend, e fitting. Hit-rate-ul e poartă, nu obiectiv. Dacă un candidat îmbunătățește hit-rate-ul, e efect secundar, se raportează, nu se folosește pentru a alege.

### 6.4 Poartă de placebo

> Dacă M-7 (scoruri permutate) produce aceeași îmbunătățire ca datele reale, rezultatul e artefact. Aceeași procedură care a retrogradat edge-ul cross-asset în V5a.

---

## 7. Pre-angajamente — semnate înainte de rulare

- [ ] **PA-1.** Adopt candidatul selectat **chiar dacă NZD/USD iese mai rău.** Semnul corecției e necunoscut.
- [ ] **PA-2.** Dacă D1 = C se dovedește singurul candidat care trece, accept scoaterea a **16/28 perechi din board, inclusiv NZD/USD**, în care am poziție deschisă.
- [ ] **PA-3.** Dacă niciun candidat nu trece poarta de nedeteriorare, adopt **NULL**: păstrez mean-over-present, documentez degradarea în docstring, adaug flag vizual. Nu slăbesc criteriile ca să treacă ceva.
- [ ] **PA-4.** Nu ating pragurile `mild`/`very` în acest ciclu. §Q1 vine după, pe distribuția rezultată.
- [ ] **PA-5.** Nu adaug excepții per-valută sau per-pereche. Regula aleasă se aplică uniform.

---

## 8. Ordine de execuție

§M nu e primul. Ordinea, din analiza precedentă:

1. **§N** — cele două calcule bilaterale divergente (bug pur, fără decizie)
2. **§M** — acest document
3. **§O** — sursă NZ/CH 2y, sau acceptare formală cu flag
4. Flag-uri de proveniență în UI (single-leg, categorie absentă, stale)
5. **§Q1** — re-derivare `very` la p90, **ultimul**

Motivul pentru 5-ultimul: pașii 1-4 schimbă distribuția scorurilor. Calibrarea pe o distribuție care urmează să se schimbe e fix lecția din corupția consensului MT5 — *„Calibration results from corrupted baselines are meaningless."*

---

## 9. Ce ar invalida acest document

- Dacă §N descoperă că cele două calcule bilaterale divergente au cauză comună cu mean-over-present, D1 se redeschide cu candidați diferiți
- Dacă apare o sursă pentru NZ/CH 2y (§O), problema devine în mare parte irelevantă — categoria devine prezentă peste tot și rămâne doar D-c de tratat
- Dacă §L a interpretat greșit numitorul de leg, D2 e prost formulată și se rescrie

---

## 10. Semnătură

**Decizii de luat înainte de orice cod:**

| | |
|---|---|
| CN-1 (nicio valoare nemăsurată în index) | adoptată / respinsă |
| Set de candidați D1 rămași după CN-1 | ____________ |
| D2 (decuplare numitor) | D2-a / D2-b |
| PA-1 … PA-5 | semnate / nesemnate |

**Fără aceste decizii completate, măsurătorile nu se rulează.**
