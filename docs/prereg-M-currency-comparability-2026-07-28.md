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

**Decizii luate (2026-07-28):**

| | decizie |
|---|---|
| CN-1 (nicio valoare nemăsurată în index) | **adoptată** |
| Set de candidați D1 rămași după CN-1 | **C, D, NULL** (A, B cad) |
| D2 (decuplare numitor) | **D2-c** (corectată — vezi §10.1, NU D2-b literal) |
| PA-1 | semnat |
| PA-2 | **amânat** — vezi §10.2 |
| PA-3 | semnat |
| PA-4 | semnat |
| PA-5 | semnat |

### 10.1 D2-c — formularea corectată (înlocuiește D2-b)

D2-b literal ("numitor constant, independent de numărul de categorii prezente") a fost respinsă: un numitor constant aplicat peste o sumă calculată doar din categoriile prezente e echivalent numeric cu imputarea la zero a categoriilor absente (exact ce CN-1 interzice). Formularea corectă decuplează ponderea EFECTIVĂ a sentimentului de acoperire, păstrând în același timp media doar peste categoriile prezente (D1 neatins de D2):

```
leg = (mean(categorii_prezente) + w_s · s) / (1 + w_s)
```

cu **w_s = 0.125**, ales să păstreze ponderea efectivă actuală a valutelor cu 4 categorii: `w_s/(1+w_s) = 0.125/1.125 = 11.11%` (identică cu status quo pentru CAD/EUR/GBP/USD). Pentru valutele cu 3 categorii (NZD, CHF, și AUD/JPY când stale), ponderea efectivă scade de la 14.29% (actual) la **11.11%** — aceeași ca restul, indiferent de acoperire.

**Deschis, nespecificat de George:** formula de mai sus acoperă doar leg-ul SENTIMENT. Cuplajul analog la nivelul TREND (`macro_weight` din `_fold_trend`, care e media greutăților efective ale ambelor legs și deci variază 3.5–4.5 pe pereche) nu are încă o formulă corectată echivalentă. Nu extind principiul la TREND fără semnătură explicită — rămâne o decizie separată, semnalată aici, nu implementată.

### 10.2 PA-2 — amânat, condiționat de §O extern

Înainte de a rula orice măsurătoare din §5, se rulează o căutare EXTERNĂ (rețea) pentru serii 2y NZD/CHF: RBNZ, SNB, BIS. Dacă apare o sursă utilizabilă, §O(1) din raportul 2 se redeschide, D1 devine parțial irelevant (categoria devine prezentă peste tot), iar PA-2 nu mai trebuie semnat separat. Rezultatul căutării: **§11 (addendum)**, mai jos.

**Fără rezolvarea §11 (sau semnarea explicită a PA-2), măsurătorile §5 nu se rulează.**

---

## 11. Addendum — §O extern (rulat 2026-07-28)

Căutare EXTERNĂ (rețea, read-only — **nimic scris în `data/`**), folosind adaptoarele existente din `src/rate_sources.py` (RBNZ, SNB, Stooq) plus o verificare independentă BIS. Rezultate live, chiar acum:

| sursă | valută | rezultat |
|---|---|---|
| **RBNZ B2 xlsx** (`RbnzSource`) | NZD | **HTTP 403** (bot-wall, confirmat live) |
| **Stooq** (`StooqSource`, fallback declarat) | NZD | **BOT-WALL** (`2nzy.b:bot-wall`, confirmat live) |
| BIS (`stats.bis.org`, catalogul complet de dataflow-uri) | NZD | **Nu există un dataflow de randamente guvernamentale** — catalogul BIS conține `WS_CBPOL` (rate de politică monetară ale băncilor centrale), NU randamente de piață pe 2 ani. Nu e un substitut legitim (instrument diferit) — nefolosit. |
| **SNB rendoblid, cube D0=2J** (`SnbSource`) | CHF | **SUCCES** — 7534 puncte, 1988-01-04 → **2025-07-31**, ultima valoare −0.083% |
| Stooq (fallback) | CHF | BOT-WALL (`2chy.b:bot-wall`) |
| BIS | CHF | la fel ca NZD — niciun dataflow de randamente guvernamentale |

### Interpretare

- **NZD rămâne complet blocat**: sursa primară (RBNZ) e bot-walled cu HTTP 403 chiar acum, fallback-ul (Stooq) e de asemenea bot-walled chiar acum, iar BIS nu publică deloc randamente guvernamentale de 2 ani (doar rate de politică monetară — un instrument diferit, nu l-am substituit). **PA-2 rămâne activ pentru NZD** — nicio sursă nouă nu a apărut.
- **CHF NU mai e "fără sursă"** — sursa SNB directă (deja codată în `src/rate_sources.SnbSource`, niciodată operaționalizată în `rate_fetch.py`/`data/rates.parquet`) răspunde cu succes ACUM și întoarce 7534 puncte istorice pe exact tenorul corect (2J/2-year), până la 2025-07-31. Asta e **362 de zile** vechime față de azi (2026-07-28) — mult peste `MAX_AGE_BD=7` zile lucrătoare, deci ar intra STALE, nu live, dacă ar fi ingerată. Dar e o schimbare calitativă reală: CHF trece din **ABSENT structural** (0 rânduri, niciodată) în **recuperabil-dar-stale** — exact profilul AUD de azi, nu profilul NZD. Confirmă nota din `src/rate_sources/__init__.py` ("CHF → SNB rendoblid D0=2J, often stale").
- **Nu am scris nimic în `data/rates.parquet` sau altundeva.** Fetch-ul a fost strict pentru a răspunde la întrebarea "există o sursă" — ingerarea (dacă se decide) e o acțiune separată, neautorizată aici.

### Consecință asupra deciziilor din §10

- **PA-2 rămâne NESEMNAT, dar cu scop redus**: relevant doar pentru NZD (singura valută încă genuin fără sursă). Pentru CHF, D1 (C/D/NULL) devine opțional — dacă se decide ingerarea seriei SNB găsite, CHF s-ar comporta ca AUD (STALE, exclus din index, dar NU necesită o decizie D1 despre "categorie absentă structural").
- **D1 rămâne relevant integral pentru NZD** (16/28 perechi conțin NZD sau CHF; dintre acestea, cele cu NZD tot au nevoie de o decizie D1 indiferent de rezultatul de mai sus).
- Nu am redeschis §O(1) din raportul 2 ca "rezolvat" — am doar RE-VERIFICAT live cele 3 surse cerute și am găsit un rezultat diferit de ipoteza inițială pentru CHF (era catalogat "0 surse" în raportul 2; corect e "sursă găsită, dar stale/neingerată").

---

## 12. Trei verificări suplimentare (2026-07-28, read-only, nimic ingerat, §5 nu a rulat)

### 12.1 Ponderea efectivă a TREND-ului per pereche, grupată pe (n_cat_base, n_cat_quote)

Analog cu D-c (sentiment), calculat pentru toate cele 28 perechi FX, starea curentă (azi): `eff_trend_weight = trend_weight / (macro_weight + trend_weight)`, unde `macro_weight` e media greutăților efective ale celor două legs (din `_leg_eff_wsum`, include deja sentiment-ul acolo unde e prezent).

| grup (n_cat_base, n_cat_quote) | n perechi | medie eff_trend_weight | varianță |
|---|---:|---:|---:|
| (4,4) | 6 | 10.26% | 0.0000083 |
| (3,4) | 4 | 11.44% | 0.0000138 |
| (4,3) | 12 | 11.22% | 0.0000065 |
| (3,3) | 6 | **12.50%** | 0.0000000 (exact, toate identice) |

**Grupat pe pereche neordonată** — (3,3): 12.50% · (3,4)∪(4,3): 11.27% · (4,4): 10.26%.

**Varianța totală pe cele 28 perechi: 5.995 × 10⁻⁵** (std ≈ 0.77 puncte procentuale). Micuță în absolut, dar **monotonă și sistematică**, nu zgomot: efectul crește exact cu numărul de categorii lipsă (0 lipsă → 10.0–10.26%, 1 lipsă → ~11.1–11.4%, 2 lipsă → 12.5% exact). Confirmă numeric nota deschisă din §10.1: cuplajul D-c există și la TREND, nu doar la SENTIMENT, în aceeași direcție (mai puține categorii FUND → pondere efectivă mai mare pentru factorii non-FUND). Interval relativ: 10.0% → 12.5% = **+25%** relativ (vs +29% găsit la sentiment, 11.11%→14.29%) — ordin de mărime comparabil.

### 12.2 RBNZ, o singură reîncercare cu configurația care a rezolvat FRED

Config: `User-Agent: macro-data-analysis/1.0 (+https://github.com/Sstrulea/macro-data-analysis)` (exact `FRED_UA` din `src/rate_sources.py`) + `urllib3.util.connection.allowed_gai_family` deja pinned la `AF_INET` (activ global, confirmat: `AddressFamily.AF_INET`) + `Referer: https://www.rbnz.govt.nz/statistics`.

**Rezultat: HTTP 403, identic** — pagina întoarsă e `"Website unavailable - Reserve Bank of New Zealand"` (blocaj la nivel de edge/WAF, nu un tarpit dependent de UA ca la FRED). **RBNZ declarat permanent blocat**, conform instrucțiunii — nu se mai reîncearcă acest vector.

### 12.3 SNB CHF — flag și valoare, dacă s-ar ingera azi

**Corecție de premisă**: `max_age_by_frequency` (din `data/economic_indicators.yaml`, weekly:14/monthly:45/quarterly:110) guvernează EXCLUSIV indicatorii de calendar (surprise-based, categoria growth/inflation/labour). Pilonul `monetary`/`rate_expectations` NU trece deloc prin acel mecanism — folosește propriul prag hardcodat din `src/rate_compute.py`: **`MAX_AGE_BD = 7` zile LUCRĂTOARE** (nu calendaristice), verificat direct în `compute_rate_score_for`.

Rulat `compute_rate_score_for` (funcția pură, neschimbată) pe seria SNB chiar fetch-uită (7534 puncte, 1988-01-04 → 2025-07-31), cu `ref = 2026-07-28`:

```
RateScore(currency='CHF', rate_score=0, delta_w=-0.009, latest_yield=-0.083,
          z=-0.0709, method='z', as_of=2026-07-28, stale=True)
```

- **Lag real: 258 zile LUCRĂTOARE** (`np.busday_count`) de la ultima observație (2025-07-31) — de peste 36× pragul de 7 zile. **Flag: STALE**, fără ambiguitate.
- **Celula `monetary` CHF**, dacă s-ar ingera azi: `score_cell = 0`, `score_precise = 0.0`, `coverage = 0`, `stale = True` — exact structura pe care AUD/JPY o au azi (afișată/greyed, dar EXCLUSĂ din `index_wsum`/`index_num`; indexul CHF ar rămâne neschimbat față de azi, calculat tot din 3 categorii).
- Deci: chiar dacă seria SNB ar fi ingerată chiar acum, CHF **NU** ar deveni "PREZENTĂ" pentru monetary — ar deveni **STALE** (ca AUD), nu PREZENTĂ. Trecerea de la ABSENTĂ→STALE tot nu rezolvă D-a/D-b/D-c pentru CHF fără o ingestie de date SNB mai proaspete decât 2025-07-31, pe care nu am găsit-o (cube-ul D0=2J pare să nu fi mai avansat de-atât, per `src/rate_sources` — un fapt separat de rezolvat, nu în scopul acestei verificări).

**Nimic ingerat, nimic scris în `data/`.**

**M-am oprit aici, conform instrucțiunii. Nu am rulat §5.**
