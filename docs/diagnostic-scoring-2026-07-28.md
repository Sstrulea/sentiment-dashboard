# Diagnostic scoring audit — 2026-07-28

**Read-only.** Nimic din `src/`, `data/*.yaml` sau `config/*.yaml` a fost modificat. Toate contrafactualele (H, I) rulează pe copii în memorie ale configurației, niciodată scrise pe disc. Branch: `diag/scoring-audit`. Scripturile care produc acest raport sunt în `scripts/diag/`.

## Metodologie și fereastră reală

- **Sursă calendar**: `config/pipeline.yaml` are `calendar_source: ff`, deci sursa live e `data/economic_calendar_ff.parquet` (bridged prin `src/ff_scoring.to_scoring_frame`, minus `ff_quarantine.parquet`) — **nu** `data/economic_calendar.parquet` (MT5) pe care `src/calibration_analysis.py` îl citește hardcodat. Am verificat: parquet-ul MT5 e **înghețat din 2026-07-03** (ultimul `actual` non-null), în timp ce feed-ul FF e curent până la 2026-07-24. Am replicat mecanica de slicing a `calibration_analysis.py` (`release_dt <= as_of` → `build_payload` pur) dar peste sursa live FF, ca să nu reproduc silentios date înghețate ca "as-of curent". Această divergență e semnalată explicit — nu e o alegere neutră.
- **Fereastra reală**: ultimele ~12 luni mărginite de cea mai scurtă serie disponibilă (COT settle la 2026-07-21). Replay determinist: **2025-07-25 → 2026-07-21**, eșantionat săptămânal (`W-FRI`, 53 puncte) — cadență aleasă să coincidă cu cadența nativă COT și să țină costul replay-ului (calendar + rate + COT + TREND pe 29 instrumente FX + 8 cross-asset, per zi) tractabil. **Nu s-a extrapolat** dincolo de fereastra asta; secțiunile care au nevoie de serii proprii mai lungi (K) folosesc totuși fereastra de 12 luni pentru consistență, cu gap-uri explicite acolo unde puterea statistică e insuficientă.
- **Ziua curentă** (F, G): 2026-07-28, snapshot separat (nu ultimul punct din fereastră, care e 2026-07-21 din cauza lag-ului COT).
- Motorul de replay (`scripts/diag/engine.py`) reconstruiește payload-ul complet (FX + cross-asset) apelând **exact** `src.economic_compute.build_payload`, `src.crossasset_compute.compute_crossasset_scores` și funcțiile private (`_augmented_index`, `_leg_eff_wsum`, `_fold_trend`) — verificat: reconstrucția manuală din secțiunea F reproduce `instrument["score"]` la 1e-6.

---

## A. Audit de dispersie — unde moare varianța

σ = deviație standard cross-secțională (peste instrumente/valute, pe zi, mediată pe cele 53 zile din fereastră).

| etapă | descriere | σ (raw) | σ (normalizat*) | n mediu | raport raw vs prec. | raport normalizat vs prec. |
|---|---|---:|---:|---:|---:|---:|
| A1 | scor indicator (−2..+2) | 0.926 | 0.926 | 65.1 | — | — |
| A2 | categorie `score_precise` | 0.740 | 0.740 | 29.9 | 0.800 | **0.800** |
| A3 | index valutar (×scale=5) | 1.836 | 0.367 | 8.0 | 2.481 | **0.496** |
| A4 | scor pereche FX (diff. pură, fără fold) | 1.303 | 0.261 | 28.0 | 0.710 | **0.710** |
| A5 | scor final (TREND+SENTIMENT fold) | 1.374 | 0.275 | 28.0 | 1.055 | **1.055** |

*normalizat = A3/A4/A5 împărțite la `scale=5` (multiplicatorul de afișare al index-ului valutar), ca să fie comparabile în aceeași unitate cu A1/A2. Raportul brut A2→A3 (2.48×, "expansiune") e un artefact al `×5`, nu varianță creată — de-scalat, A2→A3 e de fapt **cea mai mare contracție din lanț** (0.496×), nu A3→A4 cum ar sugera comparația brută. `pair_divisor=2` (A3→A4) e o contracție structurală reală și NU e scoasă din normalizare.

**Cea mai mare contracție (normalizată): A2→A3** (mediere pe categorii → index valutar per monedă), raport 0.496 — varianța la nivel de categorie e înjumătățită când cele ~3–4 categorii (growth/inflation/labour/monetary) ale unei monede sunt mediate într-un singur index. A3→A4 (diferențierea pereche) contractă doar la 0.710×, aproape exact ce prezice o subtracție a două variabile necorelate (§B). A4→A5 (fold TREND+SENTIMENT) nu contractă deloc — expandează ușor (1.055×).

### A1 — zerouri pe cauză (toate celulele aplicabile currency×indicator, fereastră întreagă)

| cauză | % din celule aplicabile |
|---|---:|
| dead_zone (`\|z\|<0.81` sau echivalent pct) | 38.2% |
| scored_nonzero | 32.5% |
| no_data (indicator neaplicabil/absent) | 18.0% |
| no_consensus | 6.7% |
| stale (exclus din agregare) | 4.6% |

Pe categorie (%, din celulele acelei categorii): `docs/diag-A-zero-cause-by-category.csv`. Observație: **labour** are cea mai mare rată dead_zone (44.8%) și zero no_consensus; **growth** are cea mai mare rată no_data (20.4%, din PMI-uri fără istoric suficient timpuriu în fereastră) și singura categorie cu no_consensus semnificativ (13.6%, din PMI-uri fără forecast pe unele valute).

CSV: `diag-A-dispersion-by-day.csv`, `diag-A-dispersion-summary.csv`, `diag-A-cells-raw.csv`, `diag-A-zero-cause-by-category.csv`.

---

## B. Corelația între legs

Matrice de corelație (time-series, pe fereastră) a celor 8 indici valutari — `diag-B-currency-correlation-matrix.csv`. **ρ mediu (off-diagonal) = 0.019** — practic necorelate. Cea mai puternică pereche: EUR↔GBP (0.54); cea mai negativă: AUD↔NZD (−0.65).

Predicție teoretică `Var(B−Q) = 2σ²(1−ρ) / pair_divisor²` vs A3→A4 empiric:

| metodă | σ_a4 prezis | raport vs actual (σ_a4=1.303) |
|---|---:|---:|
| M1: σ_a3 cross-secțional (secțiunea A) × ρ time-series | 1.286 | **98.7%** |
| M2: σ per-monedă (time-series, pooled) × ρ time-series | 1.368 | **105.0%** |
| ACTUAL (măsurat) | 1.303 | 100% |

**Predicția teoretică se potrivește cu contracția empirică A3→A4 la ±5%.** Differencing-ul contractă exact cât prezice mecanica subtracției a două variabile aproape necorelate — nu mai mult. Nu e o contracție anormală sau disproporționată; e contracția "de manual" a lui ρ≈0.

CSV: `diag-B-currency-index-timeseries.csv`, `diag-B-currency-correlation-matrix.csv`, `diag-B-theory-vs-empirical.csv`.

---

## C. Drift de calibrare (FX)

|score| percentile (toate cele 28 perechi × 53 zile, n=1484):

| p50 | p55 | p75 | p90 | p95 | max |
|---:|---:|---:|---:|---:|---:|
| 1.028 | 1.167 | 1.795 | 2.500 | 2.829 | 4.600 |

Praguri curente (`data/economic_instruments.yaml`): **mild=1.3, very=3.0**.

- `mild` (1.3) e puțin peste p55 empiric (1.167) → ușor mai mult Neutral decât ținta.
- `very` (3.0) e **peste p90 empiric (2.500)** → pragul "Very" e calibrat prea strict, nu la p90 cum indică comentariul din YAML.

Split realizat (n=1484) vs ținte de calibrare (p55→~55% Neutral, p90→~10% Very):

| bucket | realizat | țintă |
|---|---:|---:|
| Neutral | **59.4%** | ~55% |
| direcțional (Bear+Bull) | 36.8% | ~35% |
| Very (VeryBear+VeryBull) | **3.8%** | ~10% |

**Divergență confirmată, dominant pe coada "Very"**: realizat 3.8% vs ținta 10% — o subreprezentare de peste 2.5×. Neutral e doar ușor peste țintă (59.4% vs 55%).

Zile/lună cu ≥1 "Very" pe board-ul FX (`diag-C-monthly-bias-split.csv`):

| lună | %zile cu Very | %direcțional | %Very |
|---|---:|---:|---:|
| 2025-07 | 0% | 0.0% | 0.0% |
| 2025-08 | 0% | 18.6% | 0.0% |
| 2025-09 | 25% | 48.2% | 1.8% |
| 2025-10 | 80% | 47.9% | 2.8% |
| 2025-11 | 100% | 37.5% | 9.0% |
| 2025-12 | 25% | 33.1% | 1.8% |
| 2026-01 | 0% | 21.4% | 0.0% |
| 2026-02 | 75% | 43.7% | 13.4% |
| 2026-03 | 100% | 43.8% | 9.8% |
| 2026-04 | 50% | 37.5% | 2.7% |
| 2026-05 | 40% | 37.8% | 2.9% |
| 2026-06 | 75% | 44.7% | 3.6% |
| 2026-07 | 25% | 41.9% | 1.8% |

**Nu există un moment unic de "apariție" a divergenței** — seria lunară oscilează puternic (0% → 100% zile-cu-Very de la o lună la alta, cu doar ~112 obs/lună), iar subreprezentarea "Very" e persistentă pe toată fereastra, nu un fenomen recent. Media pe fereastră (3.8% vs 10%) e cea mai stabilă cifră; lectura lunară e predominant zgomot de eșantion mic.

CSV: `diag-C-fx-scores-long.csv`, `diag-C-monthly-bias-split.csv`.

---

## D. Control cross-asset (fără differencing base−quote)

| metrică | FX final (A5) | Cross-asset |
|---|---:|---:|
| σ mediu cross-secțional (raw, scale=5) | 1.374 | **1.196** |
| \|score\| p50/p75/p90/p95/max | 1.03/1.80/2.50/2.83/4.60 | 1.48/2.41/3.33/3.67/5.74 |
| prag mild/very | 1.3/3.0 | 1.9/4.3 |
| Neutral% | 59.4% | **65.1%** |
| direcțional% | 36.8% | 32.8% |
| Very% | 3.8% | **2.1%** |

Cross-asset-ul (8 instrumente, fără nicio subtracție base−quote — doar medie ponderată pe growth/inflation/labour/rates+sentiment+trend) are σ **mai mic** decât FX-ul final (1.196 vs 1.374) și un Neutral% **mai mare** (65.1% vs 59.4%). Dacă differencing-ul ar fi cauza dominantă a compresiei, grupul de control (fără differencing) ar trebui să arate dispersie vizibil mai mare decât FX — **arată mai mică**. Asta e un argument direct **împotriva** ipotezei "differencing = cauza principală": un board care nu diferențiază deloc ajunge la fel de comprimat (sau mai comprimat) prin propriul mecanism de medie ponderată peste factori slab corelați. Cross-asset-ul are de asemenea aceeași direcție de drift de calibrare ca FX (Very realizat 2.1% vs ~8% țintă din comentariul YAML).

CSV: `diag-D-crossasset-scores-long.csv`, `diag-D-crossasset-sigma-by-day.csv`.

---

## E. Densitatea de zerouri pe coloană

Coloană × %zero-sau-gol × cauză dominantă (mediat pe fereastră, n=1537 celule/coloană = 53 zile × 29 instrumente):

| coloană | %zero/gol | cauză dominantă | %no_data | %stale | %no_consensus | %dead_zone | %cancellation |
|---|---:|---|---:|---:|---:|---:|---:|
| manufacturing_pmi | 68.5% | no_consensus | 9.8 | 2.5 | 48.9 | 19.7 | 1.8 |
| services_pmi | 74.0% | no_consensus | 22.4 | 1.0 | 34.4 | 22.8 | 0.1 |
| gdp_qoq | 32.3% | dead_zone | 0.0 | 12.0 | 12.4 | 17.3 | 2.0 |
| retail_sales | 29.9% | dead_zone | 0.0 | 25.4 | 0.0 | 26.1 | 2.7 |
| cpi_yoy | 31.9% | dead_zone | 0.0 | 5.5 | 4.9 | 18.5 | 8.3 |
| core_cpi | 45.5% | dead_zone | 3.4 | 14.9 | 21.3 | 24.3 | 1.8 |
| core_pce | 97.4% | no_data | 72.4 | 5.2 | 2.6 | 17.2 | 0.0 |
| ppi_yoy | 50.5% | dead_zone | 0.0 | 19.1 | 10.6 | 30.8 | 1.4 |
| employment_change | 54.1% | dead_zone | 3.4 | 1.6 | 0.0 | 48.2 | 2.4 |
| unemployment_rate | 32.4% | dead_zone | 0.0 | 7.5 | 0.0 | 20.0 | 9.2 |
| wage_growth | 54.9% | dead_zone | 10.3 | 3.6 | 0.0 | 41.9 | 0.7 |
| adp | 99.5% | no_data | 72.4 | 1.0 | 0.0 | 26.0 | 0.0 |
| jolts | 90.1% | no_data | 72.4 | 1.6 | 0.0 | 16.1 | 0.0 |
| jobless_claims | 84.4% | no_data | 72.4 | 3.1 | 0.0 | 12.0 | 0.0 |
| rate_expectations | 45.5% | dead_zone | 3.4 | 1.4 | 0.0 | 32.5 | 8.5 |

`core_pce`/`adp`/`jolts`/`jobless_claims` sunt indicatori doar-USD: cei 72.4% no_data = exact cele 21/29 instrumente fără leg USD (21 cross-uri non-USD din 29). `manufacturing_pmi`/`services_pmi` au cea mai mare rată no_consensus — multe PMI-uri din calendar (mai ales pe valute non-US) vin fără forecast în feed-ul FF.

Tabelul pentru snapshot-ul curent (2026-07-28) e similar ca formă — `diag-E-zero-density-today-summary.csv`.

CSV: `diag-E-zero-density-{today,window}-{raw,summary}.csv`.

---

## F. Forensic pe instrument (2026-07-28)

Pipeline reconstruit exact (validat: `assert abs(score - reconstrucție) < 1e-6` a trecut pentru toate cele 4 perechi):
`A: diff. categorii (index brut, fără sentiment)` → `B: + fold SENTIMENT (pre-diferențiere)` → `C: + fold TREND (scor final)`.

### NZDUSD

**NZD** (index=1.944, coverage=9): growth 0.0 (cov4), inflation 0.5 (cov2), labour 0.667 (cov3) — **fără categorie `monetary`** (NZD absent din `rates.parquet`, vezi §G).
Indicatori NZD: cpi_yoy actual1.50/cons1.40/surprise0.10/z0.971/score+1; ppi_yoy 0.80/0.50/0.30/z0.374/score0; gdp_qoq 0.80/0.80/0/z0/score0; manufacturing_pmi 59.70/—/no_consensus/score0; services_pmi 50.60/—/no_consensus/score0; retail_sales 0.90/0.50/0.40/z0.420/score0; employment_change 0.20/0.30/−0.10/z−0.300/score0; unemployment_rate 5.30/5.40/−0.10/z0.812/score+1; wage_growth 0.50/0.40/0.10/z0.859/score+1.

**USD** (index=2.083, coverage=14): growth 0.0 (cov4), inflation −0.667 (cov3), labour 0.333 (cov6), monetary +2.0 (cov1, live).
Indicatori USD notabili: cpi_yoy 3.50/3.80/−0.30/z−2.021/score**−2**; core_cpi 0.20/0.30/−0.10/z−1.261/score−1 (**stale**); jobless_claims 187/211/−24/z2.520/score**+2**; employment_change 57/114/−57/z−0.826/score−1; rate_expectations z1.382/score+2.

Sentiment: NZD leg = **+3** (COT), USD leg = None (USD e leg-ul pereche → exclus).

| etapă | valoare |
|---|---:|
| A: diff. index brut (fără sentiment) | **−0.069** |
| B: + SENTIMENT (pre-diff.) | **+0.863** |
| trend_value (pereche NZDUSD) | **−3** (weight 0.5) |
| C: scor final | **−1.003** |

**Label final: Neutral.** **Semnul se inversează la fold-ul TREND** (B→C: +0.863 → −1.003) — TREND (−3, momentum bearish puternic pe preț) domină un semnal fundamental+sentiment deja slab pozitiv.

### GBPUSD / AUDUSD / EURUSD (rezumat)

| pereche | A (fără sentiment) | B (+sentiment) | trend_value | C (final) | label | etapă flip |
|---|---:|---:|---:|---:|---|---|
| GBPUSD | +0.156 | +0.579 | −3 | **−1.061** | Neutral | B→C |
| AUDUSD | −1.875 | −1.399 | 0 | **−1.234** | Neutral | niciuna |
| EURUSD | +0.208 | +0.625 | −3 | **−1.020** | Neutral | B→C |

**3 din 4 perechi (NZDUSD, GBPUSD, EURUSD) își inversează semnul exact la fold-ul TREND**, nu la differencing sau la SENTIMENT — un `trend_value=−3` cu weight 0.5 răstoarnă un semnal FUND+SENTIMENT mic (toate sub 1.0 în valoare absolută). AUDUSD nu se inversează (rămâne negativ pe tot lanțul), dar magnitudinea scade monoton (−1.875 → −1.399 → −1.234).

Detaliu indicator complet pentru toate cele 4 perechi: `diag-F-forensic-indicators.csv`; rezumat: `diag-F-forensic-summary.csv`.

---

## G. Calitatea datelor rate_expectations (2026-07-28)

| valută | status | method | as_of | stale | latest_yield | delta_w | sursă (ultima) | zile de la ultima obs. |
|---|---|---|---|---|---:|---:|---|---:|
| AUD | **present (STALE — exclus din index)** | z | 2026-07-28 | **True** | 4.500 | 0.028 | rba | **13** |
| CAD | present (live, în index) | z | 2026-07-28 | False | 2.900 | 0.170 | boc_valet | 4 |
| **CHF** | **ABSENT (0 rânduri în rates.parquet)** | — | — | — | — | — | — | — |
| EUR | present (live, în index) | z | 2026-07-28 | False | 2.772 | 0.296 | ecb | 4 |
| GBP | present (live, în index) | z | 2026-07-28 | False | 4.641 | 0.413 | boe | 5 |
| JPY | present (STALE — exclus din index) | z | 2026-07-28 | True | 1.382 | −0.018 | mof_jgb | 28 |
| **NZD** | **ABSENT (0 rânduri în rates.parquet)** | — | — | — | — | — | — | — |
| USD | present (live, în index) | z | 2026-07-28 | False | 4.330 | 0.220 | fred | 4 |

**Verificare specifică NZD/AUD/CHF** — cele trei nu sunt afectate identic, cum sugerează nota de proiect ("degradate egal, acceptate ca atare"):

- **NZD**: `data/rates.parquet` are **0 rânduri** pentru NZD. Categoria `monetary` nu există deloc în scorecard-ul NZD (nu apare nici măcar ca celulă `stale`/0 — vezi §F, cardul NZD are doar 3 categorii). RBNZ B2 (403) și fallback-ul Stooq nu au produs niciodată o serie persistată.
- **CHF**: identic cu NZD — **0 rânduri**, categorie monetary absentă din scorecard. SNB stale + fallback Stooq nu au produs date persistate.
- **AUD**: **diferit** — are 3233 rânduri (sursă `rba`), deci a funcționat la un moment dat, dar seria s-a **oprit la 2026-07-15** (13 zile lucrătoare până la 2026-07-28, peste pragul `MAX_AGE_BD=7`) → categoria e prezentă și afișată, dar **exclusă din index** ca stale. AUD are deci un semnal (vechi) vizibil ca atare; NZD/CHF nu au niciun semnal, vizibil sau nu.

Concluzie: eșecul NZD/CHF e mai sever decât "stale" — e **absență structurală, silențioasă** (niciun flag `stale` nu se aprinde pentru o categorie care nu a existat niciodată). AUD e genuin stale, cu semnal vechi păstrat pentru display.

CSV: `diag-G-rate-expectations-quality.csv`.

---

## H. Contrafactual V1 — reponderare (FUND ×0.5, SENTIMENT 0.5 neschimbat, TREND ×1.0)

Realizat ca scratch: `data/economic_indicators.yaml["categories"][cat]["weight"] *= 0.5` (growth/inflation/labour) + `monetary: weight 0.5` explicit adăugat (implicit era 1.0), `instruments_cfg["trend_weight"] = 1.0` (de la 0.5) — toate în copii `copy.deepcopy()` în memorie, YAML-urile de pe disc **neatinse**. Rulat pe toată fereastra (53 zile × 28 perechi = 1484 obs).

| metrică | înainte | după |
|---|---:|---:|
| σ mediu cross-secțional | 1.374 | **2.753** |
| \|score\| p50/p75/p90/p95 | 1.03/1.80/2.50/2.83 | **2.57/3.69/4.64/5.08** |
| Neutral% | 59.4% | **25.5%** |
| direcțional% (Bear+Bull) | 36.8% | 34.7% |
| Very% (VeryBear+VeryBull) | 3.8% | **39.8%** |

**Rată de flip bias: 69.0%** (1024/1484 pereche-zile își schimbă eticheta). Matrice flip (rânduri=înainte, coloane=după) — `diag-H-counterfactual-v1-flip-matrix.csv`; extras (celule mari): Neutral→Bullish 236, Neutral→Bearish 161, Neutral→VeryBullish 126, Bullish→VeryBullish 230, Neutral→VeryBearish 61.

Tabel complet "azi" (2026-07-21, ultimul punct din fereastră) cu toate cele 28 perechi: `diag-H-counterfactual-v1-today.csv`. Exemple: EURUSD Neutral(+0.11) → **Very Bearish(−3.06)**; USDCHF Neutral(+0.71) → **Very Bullish(+4.06)**; GBPCHF Bullish(+2.61) → **Very Bullish(+5.42)**.

**Simplă dublare a ponderii TREND + înjumătățire FUND aproape dublează σ și mută ~34 puncte procentuale din Neutral spre direcțional/Very** — confirmă cantitativ (nu doar calitativ) că actualul echilibru de ponderi (FUND efectiv ×1.0/categorie vs TREND 0.5) e principalul motiv pentru care Neutral domină board-ul, consistent cu concentrarea găsită la §A (contracția A2→A3, medierea categoriilor FUND).

CSV: `diag-H-counterfactual-v1-{long,today,flip-matrix}.csv`.

---

## I. Contrafactual V2 — separare piloni (CONFLICT / BALANCED / SILENT)

Pentru fiecare pereche FX cu label curent **Neutral**, descompunere aditivă exactă (`FUND + SENTIMENT + TREND = scor final`, aceleași unități ca scorul afișat):
`FUND = etapa A (diff. index brut)`, `SENTIMENT = etapa B − etapa A`, `TREND = etapa C − etapa B`.

Regulă (dată de sarcină): **CONFLICT** = ≥2 piloni cu semn opus și `|valoare| ≥ 2` fiecare; **SILENT** = ≥50% din celulele indicator aplicabile ale perechii sunt `no_data`; altfel **BALANCED**.

| | azi (2026-07-28, n=18 Neutral) | fereastră întreagă (n=881 pereche-zile Neutral) |
|---|---:|---:|
| CONFLICT | **0 (0%)** | **0 (0%)** |
| BALANCED | 18 (100%) | 867 (98.4%) |
| SILENT | 0 | 14 (1.6%) |

**Zero CONFLICT, în ambele eșantioane.** Chiar și în cazurile din §F unde TREND inversează semnul (NZDUSD, GBPUSD, EURUSD), magnitudinile pilonilor implicați rămân sub pragul de 2 (ex. NZDUSD: FUND=−0.069, SENTIMENT=+0.93, TREND=−1.87 — TREND singur nu atinge 2, deci nu calificăm drept CONFLICT deși semnul se schimbă). Concluzie mecanică: scalarul unic nu ascunde, în marea majoritate a cazurilor, o ceartă reală între piloni mari și opuși — ascunde faptul că **toți pilonii sunt individual mici** (BALANCED). Cele 1.6% SILENT sunt cazuri cu acoperire de date foarte redusă (crosses non-USD fără PMI/labour suficiente).

CSV: `diag-I-pillar-classification-{today,window}.csv`.

---

## J. Contrafactual V3 — rank cross-secțional (2026-07-28, ultima zi din fereastră, 21 iulie 2026)

Percentilă (`rank(pct=True)`) a fiecărei perechi FX după `score_precise`, în interiorul zilei (28 perechi). Mapare pe rang: p≥80→Very Bullish(rank), 60–80→Bullish(rank), 40–60→Neutral(rank), 20–40→Bearish(rank), ≤20→Very Bearish(rank).

**13 din 28 perechi (46.4%)** ar trece din Neutral (etichetă absolută) în direcțional (etichetă pe rang):

| pereche | scor | percentilă | etichetă rang |
|---|---:|---:|---|
| CADCHF | 1.296 | 75.0 | Bullish (rank) |
| GBPAUD | 1.202 | 71.4 | Bullish (rank) |
| NZDJPY | 1.146 | 67.9 | Bullish (rank) |
| USDCAD | 1.020 | 64.3 | Bullish (rank) |
| USDJPY | 0.898 | 60.7 | Bullish (rank) |
| EURCAD | 0.000 | 39.3 | Bearish (rank) |
| AUDCAD | −0.688 | 35.7 | Bearish (rank) |
| AUDJPY | −0.833 | 32.1 | Bearish (rank) |
| NZDUSD | −1.003 | 28.6 | Bearish (rank) |
| EURUSD | −1.020 | 25.0 | Bearish (rank) |
| CHFJPY | −1.042 | 21.4 | Bearish (rank) |
| GBPUSD | −1.061 | 17.9 | **Very Bearish (rank)** |
| AUDUSD | −1.234 | 14.3 | **Very Bearish (rank)** |

Aproape jumătate din board-ul "Neutral" al zilei e, relativ la restul board-ului aceleiași zile, în treimea superioară sau inferioară a distribuției — pragurile absolute (mild/very) tratează aceste perechi identic cu perechi mult mai aproape de zero (ex. EURAUD la percentila 50.0, GBPNZD la 57.1).

CSV: `diag-J-rank-mapping-today.csv`.

---

## K. Clasificare de orizont — half-life (oarbă la output, zero contact cu randamente forward)

Half-life = `ln(0.5) / ln(ρ₁) × gap_median_zile`, unde ρ₁ e autocorelația de lag-1 calculată pe seria **proprie** a componentei, la cadența ei nativă de observație (nu resamplat/ffill la zilnic — evită inflația artificială a lui ρ₁ dintr-o serie ținută constantă). **Zero contact cu prețuri/randamente** — nicio metodă din secțiunea asta nu a văzut vreun return.

Rezumat pe familie de componente (mediană peste seriile individuale), sortat crescător:

| familie | n serii | n rezolvate | half-life median (zile) | min | max |
|---|---:|---:|---:|---:|---:|
| FUND[monetary] | 8 | 6 | **8.8** | 6.7 | 13.6 |
| TREND[regime] | 37 | 35 | **9.6** | 2.4 | 37.8 |
| FUND[inflation] | 8 | 8 | **16.2** | 9.3 | 74.2 |
| FUND[growth] | 8 | 8 | **17.9** | 11.5 | 49.0 |
| FUND[labour] | 8 | 8 | **19.3** | 5.1 | 43.6 |
| TREND[momentum] | 37 | 35 | **23.5** | 11.7 | 82.8 |
| net_liquidity[level] | 1 | 1 | **26.5** | — | — |
| COT[percentile_6m] | 8 | 8 | **29.8** | 5.2 | 100.9 |
| real_yield_10y[level] | 1 | 1 | **30.9** | — | — |
| rate_exp_2y[level] | 8 | 4 | **32.8** | 24.3 | 85.0 |
| COT[net] | 8 | 8 | **81.2** | 32.1 | 224.0 |

Observații mecanice (nu recomandări):
- **FUND[monetary]** și **TREND[regime]** au cel mai scurt half-life (~9 zile) — componentele cele mai "rapide" din tot ansamblul.
- **COT[net]** e de departe cea mai persistentă serie (median 81 zile, până la 224 pentru unele valute) — poziționarea netă speculativă se mișcă foarte lent față de orice altă componentă.
- Cele 37 instrumente TREND (regime+momentum) au un interval foarte larg (2.4–82.8 zile) — nu e un singur "orizont TREND", variază mult pe instrument.

CSV complet (toate seriile individuale, sortat): `diag-K-halflife-sorted.csv`; brut cu toate notele: `diag-K-halflife-raw.csv`; rezumat pe familie: `diag-K-halflife-family-summary.csv`.

---

## Gaps

- **Sursă calendar**: `src/calibration_analysis.py` e hardcodat pe MT5 (`data/economic_calendar.parquet`), care e înghețat din 2026-07-03. Acest audit a folosit sursa live FF (`calendar_source: ff`) în loc — o deviere deliberată de la mecanica literală a `calibration_analysis.py`, semnalată la începutul raportului, nu o corectare tăcută a scriptului însuși (nu l-am modificat).
- **Cadență săptămânală pentru replay-ul complet** (A, C, D, E, H, I, J): 53 puncte `W-FRI` pe 12 luni, nu zilnic — cost computațional (COT+TREND+FUND pe 29 instrumente FX + 8 cross-asset per zi) făcea replay-ul zilnic pe 12 luni impracticabil în acest audit. Dispersia zi-cu-zi în interiorul unei săptămâni **nu e măsurată**; e posibil ca σ la cadență zilnică să difere (probabil mai mare, din zgomotul TREND zilnic pe preț).
- **NZD/CHF rate_expectations**: zero rânduri istorice, nu doar curente — nu există nicio fereastră din trecut în care să pot măsura cum s-ar fi comportat categoria `monetary` pentru aceste două valute. Exclus din FUND[monetary] la secțiunea K (n=6/8, nu 8/8).
- **rate_exp_2y half-life pentru AUD și JPY**: ρ₁ ≥ 0.995 în fereastra de 12 luni → half-life nerezolvabil (persistență aproape de unitate în fereastra disponibilă; ar necesita o fereastră mai lungă, pe care nu am extrapolat-o).
- **Indicatori cu cadență trimestrială** (GDP la unele valute, CPI/PPI AU/NZ, retail sales NZ): în 12 luni au doar ~4 tipărituri reale; orice măsurătoare individuală pe acești indicatori izolat ar fi subputernică — apar doar agregat, în categoria lor (secțiunea A/E), nu descompuși individual în K.
- **Cross-asset ca grup de control (§D)**: 8 instrumente vs 28 pentru FX — o parte din σ mai mic la cross-asset ar putea fi parțial efect de eșantion mic (n mai mic → σ eșantion cu variație mai mare), nu doar structural. Nu am corectat pentru asta.
- **A4 (secțiunea A/F/I) exclude deliberat SENTIMENT** din "diferențierea pură", deși producția reală face fold-ul SENTIMENT **înainte** de differencing (`_augmented_index`). Am ales această separare ca să izolez exact efectul differencing-ului cerut de §A/§B; §F/§I arată separat și varianta cu SENTIMENT inclus (etapa B). Nu e o eroare a motorului — e o alegere metodologică explicită, documentată la fiecare secțiune unde apare.
- **FTSE100/DXY** (secțiunea K): FTSE100 are varianță zero în subfereastra de 12 luni pentru regime/momentum (regim constant); DXY nu are serie de preț deloc (design — mulți brokeri nu au DXY). Ambele apar ca "insuficient"/"nedefinit" în `diag-K-halflife-raw.csv`, nu au fost completate artificial.
- **Ziua "curentă" pentru F/G/I(today)/J**: 2026-07-28, dar ultimul punct din fereastra de 12 luni e 2026-07-21 (lag COT ~1 săptămână) — cele două nu coincid; am folosit explicit un snapshot separat pentru "azi" în loc să extind fereastra sau să aproximez.

---

## Verdicte mecanice

1. **Differencing-ul base−quote e cauza principală a compresiei — INFIRMAT.**
   A3→A4 (differencing) contractă la 0.710×, care se potrivește la ±5% cu predicția teoretică pentru legs aproape necorelate (ρ mediu=0.019 → predicție 0.987–1.050× din actual, §B). Cea mai mare contracție normalizată din tot lanțul e cu o etapă mai devreme, A2→A3 (medierea categoriilor FUND într-un index per monedă, 0.496×, §A). Grupul de control cross-asset (fără nicio differențiere) ajunge la σ final (1.196) **mai mic** decât FX-ul diferențiat (1.374, §D). Differencing-ul contractă exact cât ar trebui mecanic — nu e "cauza", e un pas care se comportă conform așteptărilor pe legs necorelate.

2. **Calibrarea a driftat de la ținta p55/p90 — CONFIRMAT.**
   FX: |score| la p90 empiric = 2.500, dar pragul `very` e setat la 3.0 (peste p90) → realizat 3.8% Very vs ținta 10% (§C). Cross-asset arată aceeași direcție (2.1% Very realizat vs ~8% țintă din comentariul YAML, §D). Divergența e persistentă pe toată fereastra de 12 luni (nu un eveniment recent, §C) și dominant pe coada "Very", nu pe Neutral (care e doar ușor peste țintă, 59.4% vs 55%).

3. **Celula `monetary` pentru NZD e afectată de degradarea datelor — CONFIRMAT, cu o nuanță importantă.**
   NZD și CHF au **zero rânduri** în `data/rates.parquet` — categoria `monetary` nu există deloc în scorecard-ul lor (nu apare nici ca celulă stale, e complet absentă din `categories`, §F/§G). AUD, în schimb, e prezent dar **stale** (13 zile lag, sursă RBA, exclus din index dar afișat). Nota de proiect ("AUD/NZD/CHF degradate egal") **subestimează** severitatea pentru NZD/CHF: nu e o problemă de prospețime (stale), e absență totală, silențioasă — niciun flag nu semnalează utilizatorului că acea categorie lipsește structural.

4. **Majoritatea "Neutral"-urilor sunt CONFLICT, nu BALANCED sau SILENT — INFIRMAT, decisiv.**
   0% din perechile Neutral (azi și pe toată fereastra, 881 pereche-zile) se califică CONFLICT (≥2 piloni cu semn opus și |valoare|≥2). 98.4% sunt BALANCED (toți pilonii mici, nu se anulează valori mari), 1.6% SILENT. Chiar și cazurile din §F unde TREND inversează semnul scorului final o face cu magnitudini sub pragul de conflict (ex. NZDUSD: TREND=−1.87, nu ≥2). Scalarul unic nu ascunde, în marea majoritate a cazurilor, o ceartă reală între semnale mari și opuse — ascunde faptul că fiecare pilon, individual, e mic.

---

*Raport generat de scripturile din `scripts/diag/` (`engine.py`, `run_replay.py`, `section_{a..k}.py`). Nicio recomandare de adopție inclusă — doar măsurători și interpretări mecanice, conform interdicției din prompt.*
