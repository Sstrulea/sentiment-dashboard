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
| **E** *(adăugat 2026-07-28, §13)* | Absența STRUCTURALĂ (NZD/CHF, `rate_scores.get(ccy) is None`) primește aceeași celulă ca staleness-ul deja existent: `{score_cell:0, score_precise:0.0, coverage:0, stale:True}` + flag vizual — reutilizează calea EXISTENTĂ din `compute_currency_scorecard`/`rate_compute.py`, nu una nouă. | nicio afirmație nemăsurată — vezi verificarea decisivă din §13.2: calea existentă EXCLUDE, nu include-cu-0 |

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

---

## 13. Candidatul E + verificarea decisivă + D2-c extins la TREND (2026-07-28)

### 13.1 Candidatul E (adăugat la §3.1)

Absența STRUCTURALĂ (NZD, CHF — `rate_scores.get(ccy) is None`, deci `categories_out` nu primește deloc cheia `"monetary"`) ar primi, sub E, exact aceeași formă de celulă pe care STALENESS o produce deja azi pentru AUD/JPY: `{score_cell:0, score_precise:0.0, coverage:0, stale:True}`, plus un flag vizual (inexistent azi pentru acest caz — azi categoria lipsește complet din UI, nu apare deloc, vezi raportul 2 §M). Mecanismul de excludere nu e nou — e calea deja folosită de `rate_compute.py`/`compute_currency_scorecard` pentru staleness, reutilizată pentru absență.

### 13.2 Verificarea decisivă — EXCLUDERE, confirmat numeric pe AUD

**Cod** (`src/economic_compute.py`, `compute_currency_scorecard`):

```python
categories_out["monetary"] = {
    "score_cell": _clamp_cell(float(rscore)),
    "score_precise": float(rscore),
    "coverage": 0 if is_stale else 1,
    "stale": is_stale,
}
if not is_stale:                              # <- gate-ul decisiv
    total_coverage += 1
    monetary_weight = float(...)
    cat_scores_for_index.append((float(rscore), monetary_weight))
```

Când `is_stale=True`, blocul `if not is_stale:` NU rulează — celula `monetary` e scrisă în `categories_out` (pentru afișare), dar NU e adăugată niciodată în `cat_scores_for_index`, deci nu contribuie NICI la numărător (`index_num`), NICI la numitor (`index_wsum`). E excludere structurală, nu includere-cu-0.

**Confirmare numerică, AUD, azi (2026-07-28):**

```
AUD categories: growth=-0.6667 (cov3), inflation=-1.0 (cov2), labour=+0.6667 (cov3), monetary=0.0 (cov0, stale=True)
AUD index_wsum (din payload) = 3.0        <- NU 4.0
AUD index_num  (din payload) = -1.0

varianta EXCLUSĂ           : mean(-0.6667, -1.0, +0.6667) × 5 = -1.666666666666667
varianta INCLUSĂ-CU-0      : mean(-0.6667, -1.0, +0.6667, 0.0) × 5 = -1.25
AUD index ACTUAL (payload) : -1.666666666666667   <- match EXACT cu varianta EXCLUSĂ (diferență < 1e-9)
                                                       NU cu varianta inclusă-cu-0
```

**Verdict: EXCLUSĂ din numitor, confirmat atât din cod cât și numeric pe AUD, fără ambiguitate.**

**Consecință directă asupra lui E**: pentru că mecanismul EXISTENT pentru staleness deja exclude (nu include-cu-0), candidatul E — care doar reutilizează acest mecanism pentru cazul de absență structurală — **NU cade sub CN-1**. E nu e echivalentul candidatului B (care impune numitor fix 4 cu valoare 0 inclusă — o afirmație nemăsurată, "nu a existat surpriză"). E face o afirmație goală: "nu există date, celula nu contribuie, dar e vizibilă". **E rămâne candidat valid alături de C, D, NULL.**

### 13.3 D2-c extins la TREND — SEMNAT

Colapsare analogă cu §10.1 (SENTIMENT), acum pentru fold-ul TREND (`_fold_trend`, `macro_weight`). În loc de `macro_weight = (leg_eff_wsum(base) + leg_eff_wsum(quote)) / 2` (variază 3.5–4.5 pe pereche, cf. §12.1), se folosește un **numitor constant, calibrat**:

```
macro_weight_target = 4.371794871794871
eff_trend_weight = trend_weight / (macro_weight_target + trend_weight) = 0.5 / 4.871794871794871 = 10.2632%
```

calibrat exact pe media grupului de referință (4,4) de azi (0.5/4.75 și 0.5/5.0, mediate = 0.10263157894736842) — aceeași logică de calibrare ca `w_s=0.125` de la SENTIMENT (§10.1): grupul de referință (acoperire completă) își păstrează ponderea efectivă actuală medie; toate celelalte grupuri ((3,4), (4,3), (3,3)) converg la ACEEAȘI 10.2632%, indiferent de câte categorii au legs-urile.

**Notă de consistență**: la fel ca la SENTIMENT, D2-c NU impută nimic în FUND — schimbă doar cum se combină TREND cu media (deja corect calculată, peste categoriile prezente) a legs-urilor. D1 (tratamentul absenței) rămâne complet separat și neafectat de această decizie.

### 13.4 PA-2 — rămâne NESEMNAT, dar observație

Condiționat explicit de rezultatul §13.2 (acum rezolvat: EXCLUDERE, E supraviețuiește). Nu am semnat PA-2 în numele tău. Observație factuală, nu decizie: cu E confirmat viabil și ieftin (reutilizează cod existent, zero risc de imputare), scenariul pe care PA-2 îl acoperea explicit ("dacă D1=C e SINGURUL candidat care trece, accept scoaterea a 16/28 perechi") devine mai puțin probabil să fie singurul rezultat posibil — dar asta nu elimină nevoia semnăturii tale dacă, după măsurătorile §5, C tot iese singurul supraviețuitor pentru vreun motiv neanticipat aici.

**§5 nu a rulat. Nimic ingerat, nimic scris în `data/`.**

---

## 14. §5 — rezultate (2026-07-28) — PA-2 semnat, măsurători rulate

PA-2 semnat de George. Rulat M-1…M-7 pentru D1 ∈ {C, D, E, NULL}, toate combinate cu D2-c pe AMBELE fold-uri (`w_s=0.125` sentiment, `macro_weight_target=4.371794871794871` trend). Fereastra: 2025-07-25 → 2026-07-21, 53 puncte, 28 perechi FX. **Nicio adopție — doar rezultate.**

Scripturi: `scripts/diag/prereg_m_candidates.py` (motorul de scoring per candidat), `scripts/diag/section5_measurements.py` (bateria M-1…M-7). CSV: `diag-M1-M2-M3-summary.csv`, `diag-M5-candidates-long.csv`, `diag-M6-forward-returns-long.csv`, `diag-M6-M7-gate-deltas.csv`.

### 14.0 Notă preliminară: E ≡ NULL, numeric, peste tot

Confirmat: E și NULL produc rezultate **identice bit-cu-bit** la fiecare metrică (M-1…M-6). Motivul e structural, nu o coincidență: ambele calculează media FUND peste categoriile PROPRII prezente ale fiecărei valute (mecanismul deja verificat exclus, nu inclus-cu-0, în §13.2) — diferența dintre E și NULL era definită doar la nivel de UI (flag vizual pentru absență structurală vs. absență tăcută), ceva ce niciuna din metricile M-1…M-7 nu poate vedea. Rapoartez ambele separat mai jos pentru trasabilitate, dar sunt un singur rezultat numeric.

### 14.1 M-1 — acoperire

| candidat | % perechi scorabile | perechi/zi (medie) |
|---|---:|---:|
| NULL | 100.0% | 28.00/28 |
| E | 100.0% | 28.00/28 |
| D | 100.0% | 28.00/28 |
| **C** | **52.6%** | **14.72/28** |

Sub C, **47.4% din pereche-zile nu primesc niciun scor**. Verificat: perechile care conțin NZD sau CHF sunt **excluse 100% din timp, permanent** — nu doar "uneori", pentru că NZD/CHF nu ajung NICIODATĂ la 4 categorii (absență structurală, nu fluctuație de staleness ca AUD/JPY). Sub C, board-ul FX pierde definitiv 16/28 perechi.

### 14.2 M-2 — flip-uri de bias vs producția curentă (neschimbată)

| candidat | rată flip | n flip / n total |
|---|---:|---:|
| NULL / E | 3.2% | 47/1484 |
| D | 9.6% | 142/1484 |
| C | 0.4%* | 3/780 |

*rata de flip a lui C e calculată DOAR pe cele 780 pereche-zile pe care C încă le scorează — nu e comparabilă direct cu 3.2%/9.6% (eșantion mai mic, sistematic mai ușor: perechile cu NZD/CHF, cele mai afectate de D1, sunt complet absente din acest calcul pentru C).

D2-c singur (fără nicio schimbare de D1, cazul NULL/E) produce deja 3.2% flip-uri față de producția curentă — efectul decuplării ponderii, izolat de orice decizie D1.

### 14.3 M-3 — σ, percentile, split de bias

| candidat | σ mediu | p55 | p90 | p95 | max | %Neutral | %Very |
|---|---:|---:|---:|---:|---:|---:|---:|
| NULL / E | 1.343 | 1.12 | 2.44 | 2.80 | 4.41 | 60.8% | 3.8% |
| D | 1.328 | 1.11 | 2.41 | 2.77 | 4.53 | 61.7% | 3.4% |
| C | 1.273 | 1.14 | 2.41 | 2.75 | 4.16 | 60.0% | 3.1% |

Nicio schimbare dramatică față de producția curentă (σ≈1.37, raportul 1 §C) — D2-c e o corecție structurală, nu o reponderare agresivă ca V1 (raportul 1 §H, care dubla σ). C are cel mai mic σ și cea mai mică coadă Very, dar pe un eșantion de doar 6-15 perechi/zi (cele cu 4 categorii complete), nu 28.

### 14.4 M-4 — criteriul PRIMAR: varianța ponderii efective de pilon

| pilon | valoare unică (toate valutele/perechile) | varianță |
|---|---:|---:|
| SENTIMENT | 11.11111...% | **7.7×10⁻³⁴ ≈ 0** |
| TREND | 10.26316...% | **0.0 exact** |

**Criteriul primar (§6.1) e satisfăcut — dar IDENTIC și TRIVIAL pentru toate cele 4 candidate D1.** M-4 depinde exclusiv de D2-c (constantele `w_s`/`macro_weight_target`), nu de tratamentul categoriei absente. **M-4 nu discriminează între C/D/E/NULL** — validează doar că D2-c e cablat corect (confirmă analitic ce am semnat în §10.1/§13.3), nu ajută la alegerea între candidații D1.

### 14.5 M-6 — poarta de nedeteriorare (hit-rate H=5/H=10, corectat de base-rate, pe clase)

Clase: `affected_NZD_CHF` (perechi care conțin NZD sau CHF, ținta directă a lui D1) vs `unaffected` (nici un leg NZD/CHF) vs `ALL`. Hit-rate corectat = rată brută de acuratețe direcțională − baseline naiv (direcția majoritară a randamentului forward, pe ACELAȘI eșantion evaluat de acel candidat). Prag: delta ≥ −2.00pp.

| candidat | clasă | orizont | actual (corectat) | candidat (corectat) | Δ (pp) | rezultat |
|---|---|---:|---:|---:|---:|---|
| NULL/E | affected | H5 | 1.05% | 3.37% | **+2.32** | PASS |
| NULL/E | unaffected | H5 | −2.52% | −2.55% | −0.02 | PASS |
| NULL/E | ALL | H5 | −0.83% | 0.17% | +1.00 | PASS |
| NULL/E | affected | H10 | 4.41% | 5.49% | +1.08 | PASS |
| NULL/E | unaffected | H10 | −1.30% | −1.31% | −0.01 | PASS |
| NULL/E | ALL | H10 | 1.38% | 1.79% | +0.41 | PASS |
| D | affected | H5 | 1.05% | 2.77% | +1.72 | PASS |
| D | unaffected | H5 | −2.52% | −2.22% | +0.30 | PASS |
| D | ALL | H5 | −0.83% | 0.00% | +0.83 | PASS |
| D | affected | H10 | 4.41% | 4.56% | +0.15 | PASS |
| D | unaffected | H10 | −1.30% | −1.31% | −0.01 | PASS |
| D | ALL | H10 | 1.38% | 1.28% | −0.10 | PASS |
| **C** | **affected** | **H5** | 1.05% | **n=0** | — | **NEEVALUABIL** |
| C | unaffected | H5 | −2.52% | −1.92% | +0.60 | PASS |
| C | ALL | H5 | −0.83% | −1.92% | −1.09 | PASS |
| **C** | **affected** | **H10** | 4.41% | **n=0** | — | **NEEVALUABIL** |
| C | unaffected | H10 | −1.30% | −0.99% | +0.31 | PASS |
| **C** | **ALL** | **H10** | 1.38% | −0.99% | **−2.37** | **FAIL** |

**Rezultat: NULL, E, D trec poarta pe toate cele 6 combinații clasă×orizont. C nu.** C are DOUĂ probleme distincte, ambele structurale (nu statistice):
1. **Clasa `affected_NZD_CHF` e complet neevaluabilă (n=0) la ambele orizonturi** — nu doar că C "scade" performanța pe perechile cu NZD/CHF, e că NU MAI EXISTĂ nicio poziție de evaluat acolo. Un gate de nedeteriorare nu poate certifica "nu degradează" ceva ce nu mai există.
2. **`ALL`/H10 pică gate-ul cu −2.37pp** — dar ăsta e un efect de COMPOZIȚIE, nu de degradare reală: segmentul `unaffected` singur, sub C, e cu +0.31pp (nedegradat). Căderea de pe `ALL` vine din faptul că `ALL` sub producția curentă include perechile `affected` (care aveau cel mai mare corectat, +4.41pp la H10), iar `ALL` sub C nu le mai include deloc — se compară populații diferite, nu aceeași populație înainte/după.

### 14.6 M-7 — placebo (permutare temporală, seed=20260728)

Aceeași baterie, cu randamentele forward permutate în interiorul fiecărui (pereche, orizont), rupând alinierea temporală reală. Deltele placebo (candidat − actual, sub permutare):

| candidat | clasă | orizont | Δ real (pp) | Δ placebo (pp) |
|---|---|---:|---:|---:|
| NULL/E | affected | H5 | +2.32 | +0.17 |
| NULL/E | affected | H10 | +1.08 | +0.78 |
| D | affected | H5 | +1.72 | −1.26 |
| D | affected | H10 | +0.15 | +0.41 |
| (toate) | unaffected | ambele | ≤ 0.60 | ≤ 0.32 |

**Deltele reale sunt de aceeași magnitudine (ordin de mărime) ca deltele placebo** — pe `unaffected`, diferența reală vs placebo e practic indistinctă de zgomot în ambele cazuri. Singura excepție notabilă e NULL/E pe `affected`/H5 (+2.32pp real vs +0.17pp placebo — o diferență reală mai mare decât zgomotul placebo acolo), dar conform §6.3, **nu folosesc asta ca argument pentru selecție** — doar o raportez. Per §6.4: nimic aici nu declanșează gate-ul de placebo ("dacă placebo produce aceeași îmbunătățire ca datele reale, e artefact") într-un mod care ar invalida rezultatul de NEdeteriorare de la M-6 — gate-ul M-6 a fost deja trecut pe baza pragului absolut (−2pp), nu pe baza mărimii îmbunătățirii.

### 14.7 Rezumat mecanic, fără adopție

| candidat | M-4 (primar) | M-6 (poartă) | verdict mecanic |
|---|---|---|---|
| NULL | trece (trivial) | trece (6/6) | **supraviețuiește ambele criterii** |
| E | trece (trivial) | trece (6/6) | **supraviețuiește ambele criterii** (identic numeric cu NULL) |
| D | trece (trivial) | trece (6/6) | **supraviețuiește ambele criterii** |
| C | trece (trivial) | **eșuează/neevaluabil** (2/6 neevaluabile, 1/6 eșuat, 3/6 trec) | **nu trece curat poarta de nedeteriorare** |

**Nicio adopție. Niciun PR. Aceasta e o raportare a rezultatelor pre-înregistrate, nu o recomandare** — §6.3 exclude explicit selecția pe baza îmbunătățirii hit-rate, iar decizia finală (care candidat, dacă vreunul) rămâne a ta.

---

## 15. DECIZIE FINALĂ — §M închis (2026-07-28) — **⚠️ SUPRASEDATĂ, vezi §17**

**⚠️ Decizia D1=E de mai jos a fost redeschisă și invalidată în §17 (2026-07-28): E violează CN-1** (mean-over-present e algebric identic cu o imputare, exact ce CN-1 interzice). Secțiunea rămâne ca înregistrare istorică a raționamentului la momentul respectiv — **nu mai e decizia curentă**.

**Nimic implementat. Niciun PR deschis.** Această secțiune documentează decizia, nu o execută.

| decizie | rezultat |
|---|---|
| **D1** | **E** — unificare absență structurală cu calea existentă de staleness + flag vizual distinct |
| C | **respins** — pierde 47.4% din pereche-zile (16/28 perechi, permanent) fără niciun câștig măsurat față de E (§14.5: E trece poarta de nedeteriorare curat, 6/6; C nu, 2/6 neevaluabile + 1/6 eșuat) |
| D | **respins** — aruncă date măsurate (monetary USD pe perechile cu NZD/CHF), cere cod nou (logica de intersecție per-pereche), fără avantaj empiric față de E (§14.5: ambele trec poarta 6/6; D nu oferă nimic ce E nu oferă deja, la un cost de complexitate și pierdere de informație mai mare) |
| **D2-c** | **confirmat, pe ambele fold-uri** (sentiment `w_s=0.125`, trend `macro_weight_target=4.371794871794871`) — validat M-4, variație ≈0 pe ambele, §14.4 |
| PA-3 | **nu se activează** — cel puțin un candidat (de fapt trei: NULL/E/D) a trecut poarta de nedeteriorare, deci clauza „dacă niciun candidat nu trece" nu se declanșează |

### 15.1 Notă pentru docstring-ul viitor (când se implementează — NU acum)

> **E ≡ NULL, numeric.** Cele două candidate produc rezultate identice bit-cu-bit la orice metrică de scoring (§14.0) — diferența e exclusiv de UI (flag vizual). **E NU repară non-comparabilitatea indicilor valutari** (D-a/D-b din §1.1 rămân neatinse: NZD/CHF tot au un index calculat din 3 categorii, nu 4, cu variație sistematic mai mare) — **E doar o face VIZIBILĂ** în loc de silențioasă (azi: categoria lipsește complet din UI pentru NZD/CHF, fără niciun indiciu; sub E: aceeași excludere din index, dar cu celulă + flag afișate). **Comparabilitatea (D-a/D-b) e adresată separat, de D2-c** — care nu schimbă CE intră în medie (asta rămâne treaba lui D1), ci cum se cuplează SENTIMENT/TREND cu acoperirea de categorii (D-c). Cele două decizii (D1=E, D2=D2-c) rezolvă probleme DIFERITE și nu trebuie confundate ca o singură reparație.

### 15.2 Specificația flag-ului vizual (pentru implementare viitoare — NU acum)

Flag-ul trebuie să distingă **trei** stări, nu două (rezultat direct din §M/§11/§12/§13 — a nu se reduce la o singură categorie "lipsă"):

| stare | exemplu azi | cauză | mesaj |
|---|---|---|---|
| **live** | CAD, EUR, GBP, USD | date curente (`stale=False`) | (fără flag — comportament normal) |
| **stale, recuperabilă** | AUD (13 zile lag, sursă `rba`); **CHF, dacă s-ar ingera** (362 zile lag, sursă `snb` — confirmat reachable în §11/§12.3, deci NU e "indisponibil permanent", doar neingerată/veche) | sursa există și răspunde, dar seria nu s-a mai actualizat recent | „serie învechită — se poate reîmprospăta" |
| **indisponibil permanent** | **NZD** — RBNZ (`rbnz`) blocat la nivel de edge/WAF, confirmat **de două ori** (§11: UA browser standard → HTTP 403; §12.2: UA `macro-data-analysis/1.0` + IPv4-pinned, exact config-ul care a rezolvat FRED → HTTP 403 identic, „Website unavailable"), plus fallback Stooq bot-walled (§11) | sursa primară și fallback-ul sunt ambele blocate, reconfirmat, nu un fluke tranzitoriu | „sursă indisponibilă — nu reîncercăm automat" |

**CHF nu se pune în aceeași categorie cu NZD.** CHF are o sursă (SNB) care a răspuns cu succes chiar în această investigație (7534 puncte, până la 2025-07-31) — starea corectă pentru CHF, sub E, ar fi "stale, recuperabilă" (identică structural cu AUD), NU "indisponibil permanent" ca NZD. Diferența dintre cele două valute (una are un fetch reușit dar vechi, cealaltă are un blocaj confirmat de rețea de două ori) trebuie păstrată vizibil în UI, nu colapsată într-un singur "no data".

---

## §M ÎNCHIS.

**Următorul pas (nu în acest document): §N** — cele două calcule bilaterale divergente (`_build_indicator_cells` vs `_category_cells`, raportul 2 §N — bug pur, fără decizie normativă de luat). Apoi implementarea (D1=E + D2-c + flag-urile din §15.2). Apoi **§Q1, ultimul** — re-derivarea `mild`/`very` la p55/p90, pe distribuția rezultată după ce §N + §M + implementarea sunt așezate (motivul e neschimbat, §8: calibrarea pe o distribuție care urmează să se schimbe e lecția din corupția consensului MT5).

**Nimic implementat aici. Niciun PR.**

---

## §16 — Re-verificare externă NZD/CHF (2026-07-28, read-only, nimic ingerat)

Cerută separat, după §M ÎNCHIS: (1) încercare directă de descărcare a XLSX-ului RBNZ B2 pornind de la pagina de statistici (nu doar URL-urile hardcodate), (2) verificare dacă seria SNB CHF interogată în §11/§12.3 e corectă sau o arhivă/frecvență greșită.

### 16.1 — RBNZ: trei căi noi încercate, toate blocate, dar cu context nou important

- **Pagina reorganizată**: căutarea a găsit URL-ul curent al seriei (`https://www.rbnz.govt.nz/statistics/series/exchange-and-interest-rates/wholesale-interest-rates`) — diferit de vechea structură `/statistics/series/b/b2/` din `src/rate_sources.py`. Testat cu `curl` (UA Chrome complet, fără WebFetch): **HTTP 403**, identic pe pagina generală `/statistics` — blocaj la nivel de domeniu/edge, nu de UA sau de cale specifică.
- **Oglindă data.govt.nz** (`catalogue.data.govt.nz/dataset/wholesale-interest-rates`): pagina HTML, pagina de resursă, ȘI endpoint-ul JSON al API-ului CKAN (`/api/3/action/resource_show`, `/api/3/action/package_show`) — toate returnează **HTTP 200 dar conținutul e o pagină de provocare Imperva** ("Pardon Our Interruption", necesită JS), nu date. Testat cu `curl`, aceeași problemă indiferent de `Accept` header sau path.
- **Descoperire importantă, din snippet-uri de căutare** (nu din fetch direct, pagina RBNZ însăși fiind blocată): fișierul **"Wholesale Interest Rates – B2 Daily (2018-current)" a fost DISCONTINUAT** — RBNZ a trecut la o sursă nouă de date de la **NZFMA** (New Zealand Financial Markets Association): seriile sunt acum **rate de închidere (closing, end-of-day)**, nu rate de la jumătatea zilei, publicate cu **o zi lag**, efectiv din **luni, 25 august 2025**. Benchmark-urile 1, 2, 5 și 10 ani pe obligațiuni guvernamentale există explicit ca serii ("indicative closing-rates at 5.10pm, published by NZFMA") — deci **benchmark-ul de 2 ani EXISTĂ conceptual**, dar fișierul XLSX vechi hardcodat în cod probabil nu mai e actualizat de atunci (metodologie schimbată, posibil și cale schimbată).
- **NZFBF** (`nzfbf.co.nz` — New Zealand Financial Benchmark Facility, administratorul de benchmark menționat), un domeniu NOU, NEBLOCAT (testat cu WebFetch, funcționează): are pagini `/benchmarks/closing-rates/` și `/benchmarks/closing-rates/nzgs` (New Zealand Government Bonds) — dar sunt pagini **informative/metodologice** (link către un PDF de metodologie, `NZGS-Closing-Rates---Methodology-January-2026.pdf`), **fără tabel de date sau link de download vizibil** pe paginile verificate. Nu am explorat exhaustiv tot site-ul (ar putea exista un portal de date separat, autentificat sau nu, pe care nu l-am găsit).

**Verdict RBNZ**: rămâne genuin blocat pe toate căile testate (3 domenii, 5 URL-uri distincte, 2 unelte diferite). Noutatea reală nu e o cale de acces, ci confirmarea DIN SURSE EXTERNE (Scoop News, alertele RBNZ) că fișierul vechi a fost discontinuat printr-o schimbare de metodologie/sursă (NZFMA) din 25 august 2025 — asta explică probabil de ce RBNZ-ul hardcodat în cod nu ar mai funcționa oricum, dincolo de blocajul de edge.

### 16.2 — SNB CHF: NU e eroare de arhivă/frecvență — e un cub complet ÎNGHEȚAT, confirmat din metadata proprie

Re-interogat direct `https://data.snb.ch/api/cube/rendoblid/data/csv/en` (același endpoint ca în `SnbSource`), de data asta inspectând ÎNTREG fișierul (5.46 MB, 146,828 rânduri), nu doar seria "2J" extrasă.

**Metadata din chiar antetul fișierului**:
```
"CubeId";"rendoblid"
"PublishingDate";"2025-09-01 14:29"
```

**Toate cele 22 de dimensiuni D0 din cub** (1J…30J, 10J1, E, K, P, GK, IKH, AAA, AA, A) **se opresc la exact aceeași dată: 2025-07-31** — nu doar "2J". Deci nu e o eroare de selecție a maturității/cubului din partea noastră: verificat via `https://data.snb.ch/api/cube/rendoblid/dimensions/en` că `D0=2J` chiar înseamnă "2 years" sub "CHF Swiss Confederation bond issues" (categoria corectă, guvernamentală, nu corporate/cantonal). **Cubul ÎNTREG a fost publicat ultima dată pe 2025-09-01 și conține date doar până la 2025-07-31** — un îngheț la nivel de sursă (SNB), nu o problemă de frecvență (nu e "anual" — cubul avea cadență zilnică până s-a oprit) și nu o eroare de interogare de partea noastră.

Nu am găsit (în timpul alocat) un cub succesor cu alt nume pe portalul SNB — `rendoblim` (varianta lunară) există ca frate, dar e frecvență mai joasă, nu un înlocuitor mai proaspăt. Pagina portalului SNB (`data.snb.ch/en/topics/ziredev/cube/rendoblid`) e un SPA — nu am putut extrage din ea vreo notă explicită de discontinuare (doar un mesaj de compatibilitate browser la fetch necompilat JS).

**Verdict CHF**: seria interogată în §11/§12.3 era CEA CORECTĂ (cub + dimensiune corecte); staleness-ul de 362 zile nu vine dintr-o greșeală de query, ci dintr-un îngheț confirmat la sursă (metadata proprie a fișierului). Caracterizarea din §15.2 ("stale, recuperabilă") rămâne corectă ca stare AZI, dar "recuperabilă" ar necesita ca SNB să reia publicarea cubului `rendoblid` — nu ține de o cerere diferită de-a noastră.

**Nimic ingerat. Nimic scris în `data/`. M-am oprit aici.**

---

## §M REDESCHIS (2026-07-28)

**§O confirmat închis** (NZD/CHF permanent indisponibile — §16). **§M redeschis**: decizia D1=E din §15 s-a bazat pe o premisă greșită. Documentat mai jos. **Nu decid între candidații rămași — raportez și mă opresc**, conform instrucțiunii.

### 17.1 Eroarea — E (și NULL) violează CN-1

**Afirmația**: mean-over-present e algebric IDENTIC cu o imputare — anume, imputarea categoriei absente cu MEDIA propriilor categorii prezente ale acelei valute, urmată de o medie pe numitor fix (N=4).

**Demonstrație** (identitate matematică generală, nu doar coincidență numerică): fie `x_1,...,x_n` categoriile prezente (n din N=4 posibile, ponderi egale 1.0). Mean-over-present = `(x_1+...+x_n)/n`. Dacă se impută fiecare din cele `N-n` categorii absente cu valoarea `m = (x_1+...+x_n)/n` (media celor prezente) și apoi se calculează media pe numitorul FIX N, rezultatul e:

```
[ (x_1+...+x_n) + (N-n)·m ] / N  =  [ n·m + (N-n)·m ] / N  =  N·m / N  =  m
```

**Exact aceeași valoare.** Nu o aproximare — o identitate algebrică: a media valorile prezente ȘI a le împărți la n E MATEMATIC ECHIVALENT cu a impută absentele cu media prezentelor și a împărți la N. Cele două formulări descriu ACELAȘI număr.

**Confirmare numerică, NZD, azi** (`docs/prereg-...md` §13.2 folosise deja acest exemplu, dar pentru altă întrebare):

```
categorii prezente: growth=0.0, inflation=0.5, labour=0.6667  (n=3)
mean-over-present           = (0+0.5+0.6667)/3           = 0.388889  →  index = 1.944444
imputare monetary=media celor 3, apoi /4  = (0+0.5+0.6667+0.388889)/4 = 0.388889  →  index = 1.944444
identic la 1e-12
```

**Consecință**: E și NULL (numeric identice, §14.0) **fac o afirmație nemăsurată** — anume "monetary pentru NZD s-ar comporta ca media propriilor sale growth/inflation/labour" — exact genul de afirmație pe care CN-1 a fost adoptată să o interzică. Diferența față de candidatul A (imputare cu media cross-secțională GLOBALĂ) e doar CE anume se impută (media proprie a valutei, nu media tuturor valutelor) — dar mecanismul (o valoare nemăsurată calculată și lăsată să influențeze indexul) e identic în esență.

**Eroarea din §13.2/§15**: verificarea de-atunci a confirmat corect că, LA NIVEL DE COD, categoria stale/absentă nu e adăugată explicit în `cat_scores_for_index` (nicio linie de cod nu scrie o valoare imputată undeva) — asta a fost interpretat drept "excludere, deci fără afirmație nemăsurată". Eroarea: **mecanismul de cod (excludere) și rezultatul aritmetic (echivalent cu imputare) sunt lucruri diferite**. CN-1 vorbește despre rezultat ("nicio valoare nemăsurată nu poate intra în index"), nu despre mecanismul de implementare. Excluderea la nivel de cod nu previne echivalența aritmetică la nivel de rezultat.

### 17.2 Candidații rămași sub CN-1 strict

| candidat | supraviețuiește CN-1 strict? | motiv |
|---|---|---|
| A | NU (deja eliminat, §10) | imputare explicită cu media cross-secțională |
| B | NU (deja eliminat, §10) | imputare explicită cu 0 |
| **E / NULL** | **NU (nou, §17.1)** | mean-over-present ≡ imputare cu media proprie a valutei — aceeași familie de eroare ca A/B, doar cu altă valoare imputată |
| **C** | **DA** | când scorează o pereche, ambele legs au N=4 categorii complete — n=N întotdeauna, deci nu există nicio diferență între "mediat peste prezente" și "mediat peste toate" (nimic lipsă de imputat). Nicio afirmație nemăsurată. |
| **D** | **DA** | mediază peste INTERSECȚIA categoriilor prezente pe AMBELE legs — nu impută nimic; ARUNCĂ date reale (ex. monetary USD pe o pereche cu NZD), dar nu inventează niciuna. Nicio afirmație nemăsurată. |

**Sub CN-1 strict, supraviețuiesc DOAR C și D.**

### 17.3 Ce înseamnă asta pentru măsurătorile deja făcute (§5, §14) — fără decizie

Nu redecid — doar reconectez faptele deja măsurate la noul set de candidați valizi:

- **D** a trecut poarta de nedeteriorare (M-6) curat, 6/6 combinații clasă×orizont (§14.5).
- **C** nu a trecut curat: 2/6 neevaluabile (clasa `affected_NZD_CHF`, n=0 la ambele orizonturi — perechile cu NZD/CHF sunt excluse permanent, nu doar degradate) + 1/6 eșuat (`ALL`/H10, −2.37pp, efect de compoziție, nu degradare reală pe segmentul `unaffected` luat singur, care e +0.31pp).
- **PA-2** (semnat) și **PA-3** rămân cum au fost — PA-3 tot nu se activează, pentru că **D** (nu mai NULL/E) trece poarta, deci clauza "dacă niciun candidat nu trece" tot nu se declanșează.
- **D2-c** (§10.1, §13.3, ambele fold-uri) rămâne neafectată de această redeschidere — e o decizie separată (D-c, cuplaj pondere-acoperire), independentă de D1 (D-a/D-b, tratamentul absenței). Nu se redeschide.
- Flag-ul vizual din §15.2 (trei stări) rămâne valid ca SPECIFICAȚIE de UI indiferent de care D1 se alege — dar sub C, categoria NZD/CHF nu mai apare NICIODATĂ ca "celulă cu flag" pe o pereche scorată (perechea în sine dispare); sub D, apare ca înainte (celula există, dar unele contribuții sunt aruncate).

**Nu decid între C și D. Raportez faptele de mai sus și mă opresc**, conform instrucțiunii primite.
