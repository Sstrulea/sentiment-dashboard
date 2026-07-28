# Diagnostic scoring — rezumat final și prioritizare

**Read-only, pe toată durata investigației.** Branch `diag/scoring-audit`. Nimic din `src/`, YAML sau config nu a fost modificat vreodată. Acest document ÎNCHIDE diagnosticul — consolidează §A–Q, §N (căile bilaterale) și a treia cale, cu o prioritate rescrisă. **Nu implementează nimic. Niciun PR.**

Documente sursă (toate pe acest branch):
- `docs/diagnostic-scoring-2026-07-27.md` — §A–K (dispersie, corelație, calibrare, cross-asset, zero-density, forensic, rate-quality, contrafactuale V1/V2/V3, half-life)
- `docs/diagnostic-scoring-2-2026-07-28.md` — §L–Q (reconciliere aritmetică, categorii absente, celule bilaterale, contrafactual monetary, asimetrie de vechime, serii restante)
- `docs/prereg-M-currency-comparability-2026-07-28.md` — specificația pre-înregistrată §M (decizii CN-1/D1/D2, §5 M-1…M-7, decizia finală)
- `docs/diagnostic-N-2026-07-28.md` — cele două căi bilaterale divergente (cale 1/cale 2)
- `docs/diagnostic-thirdpath-2026-07-28.md` — a treia cale (accumulatoarele reale) vs cale 1

---

## Ce s-a găsit, pe scurt

1. **Compresia scorurilor** (§A) nu vine (dominant) din diferențierea base−quote (contracție 0.71×, exact ce prezice corelația aproape-zero dintre valute, §B) — vine din medierea categoriilor FUND într-un index per monedă (contracție 0.50×, cea mai mare din tot lanțul).
2. **Calibrarea a driftat**: pragul `very` e peste p90 empiric — Very realizat 3.8% vs ținta 10% (§C), și **istoricul real** (nu un replay) arată un SALT, nu o eroziune graduală, chiar la commit-ul de zero-placeholder-quarantine (2026-07-07): Very% cade de la un regim de 10-46% la **exact 0.0% timp de 16 zile consecutive** (§Q1).
3. **Categoria `monetary` lipsește structural pentru NZD/CHF** (0 rânduri, dintotdeauna, nu doar stale) — confirmat numeric: indexul monedei EXCLUDE categoriile absente/stale din medie, nu le include cu 0 (§M, verificat pe AUD: `index_wsum=3.0`).
4. **Sursele de date externe**: NZD (RBNZ) e blocat definitiv, reconfirmat de două ori (UA standard și configurația care a rezolvat FRED) — HTTP 403 identic. CHF (SNB) ARE o sursă care răspunde (7534 puncte, până la 2025-07-31) — nu e "fără sursă" ca NZD, e "veche, recuperabilă" (prereg §11/§12).
5. **Celulele bilaterale afișate** (tabelul dens ȘI câmpul `inst["categories"]`) sunt AMÂNDOUĂ greșite față de ce produce efectiv scorul: cale 1 (dense table) omite `pair_divisor` față de cale 2 (`_category_cells`, exact ×2 sistematic, §N) — dar cale 2 nu mai e randată de nicăieri din UI din 2026-06-08 (cod mort, orfan de la un refactor incomplet). Și mai important: **NICIUNA din cele două căi de afișare nu se apropie de contribuția reală a unui indicator la scor** — divergența cale-afișată vs contribuția reală e 55.9% din celule, dependentă de caz (formula exactă: `index_wsum·coverage_c·pair_divisor/scale`), pentru că tabelul dens nu diluează deloc un indicator după câți "frați" are în categoria lui (§thirdpath).
6. **Reponderarea D2-c** (decuplarea SENTIMENT/TREND de acoperirea de categorii) a fost măsurată pre-înregistrat (§5, M-1…M-7): candidatul **E** (unificare absență-cu-staleness + flag vizual) trece curat poarta de nedeteriorare; **C** (exclude perechile incomplete) nu — pierde 16/28 perechi permanent, inclusiv întreaga clasă NZD/CHF, fără niciun câștig măsurat. **Decizie finală semnată: D1=E, D2-c confirmat pe ambele fold-uri** (prereg §15).

---

## Prioritatea rescrisă

### 1. Tabelul dens = contribuții reale (formula validată)

**De ce primul**: e singura problemă care afectează DIRECT ce vede utilizatorul, la orice zi, pe orice pereche — nu o problemă de date lipsă (NZD/CHF) sau de calibrare a pragurilor, ci o discrepanță între ce se AFIȘEAZĂ și ce PRODUCE scorul, chiar și pentru monede complet acoperite (USD, EUR, GBP). Afectează 55.9% din celule, cu o rată de nepotrivire de semn (sumă afișată vs scor final) de 20.6% pe fereastra de 53 săptămâni (§thirdpath).

**Formula validată** (descompunere liniară EXACTĂ — suma reconstruiește `macro_score` la `1e-16`, nu o aproximare):

```
contribuție(indicator k, categoria c, moneda ccy) =
    ± score_k · [ weight_c / (index_wsum_ccy · coverage_c) ] · scale / pair_divisor
```

unde semnul e `+` pentru leg-ul bază, `−` pentru leg-ul cotație; `index_wsum_ccy` = numărul de categorii prezente/ne-stale ale monedei azi (3 sau 4); `coverage_c` = numărul de indicatori ne-stale din acea categorie.

#### Variantă (a) — DOAR contribuții reale, sumabile la `score_precise`

Fiecare celulă arată contribuția exactă (compact, rotunjit la întreg — convenția deja folosită de `score_cell` peste tot în sistem — cu valoarea precisă disponibilă la hover/tooltip, ca la categoriile actuale).

**Mockup NZDUSD** (azi, 2026-07-28):

| indicator | contribuție precisă | compact (afișat) |
|---|---:|---:|
| cpi_yoy | +0.833 | **+1** |
| core_cpi | 0.000 | 0 |
| employment_change | +0.104 | 0 |
| unemployment_rate | +0.174 | 0 |
| wage_growth | +0.278 | 0 |
| jobless_claims | −0.208 | 0 |
| rate_expectations | −1.250 | **−1** |
| *(restul: manufacturing_pmi, services_pmi, gdp_qoq, retail_sales, core_pce, ppi_yoy, adp, jolts — toate 0.000)* | 0.000 | 0 |
| **Σ precis** | **−0.069** | *(= macro_score FUND-only, verificat exact)* |

**Mockup USDJPY**:

| indicator | contribuție precisă | compact (afișat) |
|---|---:|---:|
| gdp_qoq | −0.556 | **−1** |
| retail_sales | −0.556 | **−1** |
| cpi_yoy | −0.417 | 0 |
| core_cpi | 0.000 | 0 |
| employment_change | −0.104 | 0 |
| unemployment_rate | +0.104 | 0 |
| jobless_claims | +0.208 | 0 |
| rate_expectations | +1.250 | **+1** |
| *(restul: 0.000)* | 0.000 | 0 |
| **Σ precis** | **−0.069** | *(= macro_score FUND-only, verificat exact)* |

**Câte celule devin 0 la rotunjire, varianta (a)**:

| | NZDUSD | USDJPY | combinat |
|---|---:|---:|---:|
| celule totale (15 coloane) | 15 | 15 | 30 |
| rotunjite la 0 | **13 (86.7%)** | **12 (80.0%)** | **25 (83.3%)** |
| dintre acestea, nenule azi sub cale-1 actuală (deci "dispar" vizual) | 4/6 | 5/8 | **9/14 (64.3%)** |

**Costul variantei (a)**: dispersia unui indicator individual e atât de diluată de `index_wsum·coverage_c` încât marea majoritate a celulelor rotunjite arată 0 — un tabel care azi arată predominant valori întregi ±1/±2 ar deveni, sub (a), predominant zerouri, chiar dacă fiecare 0 e acum "corect" (contribuția reală chiar e mică). Riscul: utilizatorul pierde exact tipul de informație pe care nota din modal o promite azi ("Rounded cells can hide divergence") — doar că acum ascunderea ar veni din precizia adevărului, nu dintr-o eroare de calcul.

#### Variantă (b) — contribuții reale + scorul brut de indicator, marcate distinct

Fiecare celulă arată AMBELE valori, vizual distincte (ex. contribuția ca valoare principală + culoare, scorul brut ca etichetă secundară/tooltip) — nimic nu se pierde la rotunjire, pentru că scorul brut (întotdeauna întreg, −2..+2) rămâne vizibil separat.

**Mockup NZDUSD** (bază=NZD, cotație=USD; raw = scorul brut per indicator pe fiecare leg, `—` = indicator absent pe acel leg):

| indicator | contribuție (compact) | raw NZD (bază) | raw USD (cotație) |
|---|---:|---:|---:|
| cpi_yoy | +1 *(precis +0.833)* | **+1** | **−2** |
| core_cpi | 0 | — | **−1** *(stale)* |
| employment_change | 0 *(precis +0.104)* | 0 | **−1** |
| unemployment_rate | 0 *(precis +0.174)* | **+1** | **+1** |
| wage_growth | 0 *(precis +0.278)* | **+1** | 0 |
| jobless_claims | 0 *(precis −0.208)* | — | **+2** |
| rate_expectations | **−1** *(precis −1.250)* | — *(categorie absentă)* | **+2** |

**Mockup USDJPY** (bază=USD, cotație=JPY):

| indicator | contribuție (compact) | raw USD (bază) | raw JPY (cotație) |
|---|---:|---:|---:|
| gdp_qoq | **−1** *(precis −0.556)* | 0 | **+2** |
| retail_sales | **−1** *(precis −0.556)* | 0 | **+2** |
| cpi_yoy | 0 *(precis −0.417)* | **−2** | 0 |
| core_cpi | 0 | **−1** *(stale)* | 0 |
| employment_change | 0 *(precis −0.104)* | **−1** | — |
| unemployment_rate | 0 *(precis +0.104)* | **+1** | 0 |
| jobless_claims | 0 *(precis +0.208)* | **+2** | — |
| rate_expectations | **+1** *(precis +1.250)* | **+2** | 0 *(stale)* |

(Notă: `unemployment_rate` la NZDUSD arată exact cazul pe care nota din modal îl semnalează deja azi — ambele legs +1, diferența brută 0, dar sub varianta (a)/(b) contribuția reală +0.174 tot nu e zero, doar mică; nu e cazul de "±2 pe legs opuse" din exemplul modalului, dar ilustrează același fenomen de compact-0-ascunde-informație.)

**Costul variantei (b)**: mai multă informație pe ecran (două numere per celulă în loc de unul), risc de aglomerare vizuală; nu rezolvă complet confuzia pe care nota din modal o menționează deja azi ("un 0 poate ascunde o divergență reală de ±2 pe legs opuse") — dar acum utilizatorul poate vedea EXPLICIT că un 0-compact vine dintr-un raw ±1/±2 diluat, nu dintr-o lipsă reală de semnal.

**Nicio recomandare între (a) și (b)** — ambele sunt raportate simetric, cu costurile lor.

### 2. Ștergerea codului mort `_category_cells`

`src/economic_compute.py::_category_cells` (linia 466) produce `inst["categories"]`, confirmat **niciodată citit** de `static/economic-chart.js` din commit-ul `b6872df` (2026-06-08 17:54) — 7 săptămâni de calcul orfan pe fiecare refresh. Al doilea pe listă (nu primul) pentru că e independent de decizia de la punctul 1 — dacă tabelul dens se schimbă (varianta a sau b), codul mort rămâne mort oricum și trebuie eliminat separat, nu ca parte a acelei schimbări.

### 3. §M: D1 = E, D2-c confirmat

Deja decis și semnat (prereg §15): unificarea absenței structurale (NZD/CHF) cu calea de staleness deja existentă + flag vizual distinct; D2-c pe ambele fold-uri (`w_s=0.125` sentiment, `macro_weight_target=4.371794871794871` trend). C și D respinse — fără câștig empiric măsurat față de E (§5, M-1…M-7).

### 4. §O: flag cu trei stări

`live` / `stale, recuperabilă` (AUD azi; CHF, dacă s-ar ingera — sursă SNB confirmată reachable) / `indisponibil permanent` (NZD — RBNZ blocat, reconfirmat de două ori). Specificat în prereg §15.2.

### 5. §Q1: re-derivarea `very` la p90 — ULTIMUL

Neschimbat din motivul original (prereg §8): pașii 1-4 schimbă distribuția scorurilor afișate (mai ales pasul 1 — o schimbare de la scoruri brute la contribuții diluate ar rescrie complet distribuția `|score|` a TABELULUI, chiar dacă `score_precise` al PERECHII nu se schimbă). Calibrarea pe o distribuție care urmează să se schimbe e exact lecția din corupția consensului MT5 ("Calibration results from corrupted baselines are meaningless").

---

## Ce NU s-a făcut aici

- Nu s-a implementat nicio variantă (a)/(b).
- Nu s-a șters `_category_cells`.
- Nu s-a atins niciun prag (`mild`/`very`) sau vreo pondere din YAML.
- Nu s-a deschis niciun PR.

**Diagnostic închis. Aștept decizia asupra priorității și a variantei de afișare de la punctul 1.**
