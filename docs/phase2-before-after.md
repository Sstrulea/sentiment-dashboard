# Phase 2 (+ addendum) — FF backfill + baseline rebuild + before/after (MT5 vs Forex Factory)

Branch `feature/ff-calendar-ingest`. **Isolated & read-only over production**: FF is scored through
the *identical* `economic_compute.build_payload` reused by import (`src/ff_scoring.py`); no production
file is modified, nothing is wired into the live pipeline (Phase 3). `as_of = 2026-07-05`; MonPol
(FRED) held identical for both runs so category deltas are purely calendar-sourced. **377 tests green.**

Addendum applied two review corrections: **(1)** cadence-aware z-history threshold, **(2)** deterministic
flash/final at the alias level (no proximity heuristic in scoring).

Artifacts: `data/economic_calendar_ff.parquet`, `docs/phase2-before-after.csv`,
`docs/phase2-before-after-indicators.csv`, `docs/phase2-below-threshold.csv`,
`docs/phase2-flash-final-revisions.csv`.

---

## TASK 1 — FF historical parquet
`data/economic_calendar_ff.parquet` — **3,053 scored rows, 82 series, 2023-01 → 2026-07-05**, 8
currencies (built from `data/raw/ff_calendar_range.json`). (Rows dropped from 3,238 → 3,053: the ~185
final/revision prints now excluded from scoring — see Correction 2.) Continuity: every monthly series is
continuous within 60 d except a single ~61–63 d gap in ~20 series (one missing month each over 3.5 y).

## TASK 2 — baselines rebuilt EXCLUSIVELY from FF (cadence-aware)

Baselines are the trailing-K surprise sigma computed live by `build_payload` from the FF-only frame.
Provenance is guaranteed by construction (`to_scoring_frame` emits `source='ff'` on every row; asserted
for the `# xf` series). 

**Correction 1 — cadence-aware threshold:** cadence is detected per series from the median inter-print
interval (weekly≤10 d / monthly≤45 d / quarterly≤135 d) and the right threshold applied (weekly/monthly
24, quarterly 8). This **drops the fallback list from 17 → 7** (the AU/NZ quarterly CPI/PPI/wage/retail
series with 13–15 prints are correctly *above* the quarterly-8 threshold). `interest_rate_decision` is
**excluded from the discussion** (weight 0, display-only — scored via the FRED rate engine, not calendar
z-surprise).

Remaining below threshold (→ %-surprise fallback):
```
EUR/AUD/CHF/JPY manufacturing_pmi  n=7  (monthly, <24)   ← Flash-PMI variants: short FF history
EUR/AUD services_pmi               n=7  (monthly, <24)
JPY gdp_qoq                        n=2  (quarterly, <8)
```

## Correction 2 — deterministic flash/final (alias-level)

FF distinguishes flash vs final by distinct `name_raw`. Exactly ONE variant per (currency, canonical)
is mapped in `config/ff_aliases.yaml` = the **scored** print; later estimates are listed under
`excluded_final_variants` (so they don't reappear as unmapped WARNINGs) and surface only as **revision
telemetry** (`flash_final_revisions`, 182 pairs). No proximity heuristic runs in scoring — the FF frame
is one-print-per-period by construction. Full flash/final inventory found in the payload:

| currency | canonical | scored (flash) | excluded (telemetry) |
|---|---|---|---|
| EUR | CPI y/y | CPI Flash Estimate y/y | Final CPI y/y |
| EUR | Core CPI y/y | Core CPI Flash Estimate y/y | Final Core CPI y/y |
| EUR | GDP q/q | Prelim Flash GDP q/q | Flash GDP q/q, Revised GDP q/q |
| EUR | Employment Change q/q | Flash Employment Change q/q | Final Employment Change q/q |
| EUR | Mfg/Svc PMI | Flash …PMI | Final …PMI |
| USD | GDP q/q | Advance GDP q/q | Prelim GDP q/q, Final GDP q/q |
| JPY | GDP q/q | Prelim GDP q/q | Final GDP q/q |
| JPY | Mfg PMI | Flash Manufacturing PMI | Final Manufacturing PMI |
| **GBP** | Mfg/Svc PMI | **Final …PMI** (UK headline, n≈141) | Flash …PMI (sparse n≈6) — *documented reversal* |

## TASK 3 — before/after per currency × category (`score_precise`, delta = FF − MT5)

MonPol delta = 0 everywhere ⇒ the comparison isolates the calendar.

| ccy | growth | inflation | labour | monPol |
|---|---|---|---|---|
| USD | 2.00 → 0.75 (−1.25) | 0.75 → −0.25 (−1.00) | 0.40 → 0.00 (−0.40) | 1.00 → 1.00 (0) |
| EUR | 0.75 → −0.50 (−1.25) | **1.33 → 0.00 (−1.33)** | **−1.00 → 1.50 (+2.50)** | 0 |
| GBP | −0.75 → 0.25 (+1.00) | 0.00 → −1.00 (−1.00) | 1.33 → 1.33 (0) | 0 |
| JPY | 2.00 → 1.33 (−0.67) | **1.50 → −0.50 (−2.00)** | 1.00 → 0.50 (−0.50) | 0 |
| AUD | 0.00 → 0.67 (+0.67) | −1.33 → −1.00 (+0.33) | −0.33 → 0.33 (+0.67) | −2.00 → −2.00 (0) |
| NZD | 0.50 → 1.25 (+0.75) | 0.00 → 1.00 (+1.00) | 0.33 → 0.67 (+0.33) | — |
| CAD | 0.00 → 1.00 (+1.00) | 1.33 → 1.33 (0) | 2.00 → 1.00 (−1.00) | 0 |
| CHF | −0.50 → 0.33 (+0.83) | −1.00 → −1.50 (−0.50) | **0.00 → −2.00 (−2.00)** | 0 |

### Status of the 4 previous |delta| ≥ 2 cells (before → after the addendum)

| cell | before addendum | after addendum | why moved |
|---|---|---|---|
| **EUR inflation** | −2.667 | **−1.333** | flash-only cleans it: FF now uses ONLY the CPI flash (2.8/3.0 = tiny miss → score 0), so FF inflation is **0.00 neutral** (was −1.33 when flash+final were both scored). MT5 stays +1.33 (fake-hawkish). Corruption still removed; FF reading is now *more accurate* (neutral, not overstated dovish). |
| **EUR labour** | +2.00 | **+2.50** | flash-only employment: FF scores the flash Employment Change (0.1/0.0 beat → +1) + unemployment 6.2<6.3 (→ +2) = **+1.5**; MT5 sparse/quarterly miss (−1.0). Delta widens. |
| **JPY inflation** | −2.00 | **−2.00** | unchanged — MT5 CPI consensus low (fake +0.2 beats) vs FF aligned (1.4/1.4 met, 1.4/1.7 miss). |
| **CHF labour** | −2.00 | **−2.00** | unchanged — MT5 has no fresh labour (cov 0); FF has 2026-06-04 unemployment (3.1>3.0, weaker) → −2. Coverage gain. |

Also moved (|delta| < 2, from flash-only): **USD growth** −0.75 → −1.25 (FF GDP now Advance-only) and
**EUR growth** −1.50 → −1.25.

### (a) EUR Inflation — the CPI corruption disappears  ★ headline (print-by-print)
```
date        MT5 (actual/cons)   FF flash (actual/cons)
2026-05-20     3.0 / 1.9  +1.1     3.0 / 3.0   0.0
2026-06-17     3.2 / 2.6  +0.6     (final — telemetry only)
2026-07-01     3.0 / 2.1  +0.9     2.8 / 3.0  −0.2  ← MT5 fakes +0.9 hot beat; FF flash = −0.2 miss → score 0
```
MT5's misaligned `consensus` (2.1 vs 3.0) turns a cooling print into a hawkish surprise → MT5 EUR
inflation **+1.33 hawkish**. FF (flash, aligned) → **0.00 neutral**. Plus the m/m corruption (Task 4).

### (b) US Labour — NFP consensus corruption
```
2026-04-03  MT5 178 / -6      ← consensus −6K for a 178K print is impossible (corrupt)
2026-06-05  MT5 172 / 77   |  FF 172 / 85   (sane)
2026-07-02  (MT5 absent)   |  FF  57 / 114  ← FF has the July NFP; MT5 a month behind
```

## TASK 4 — sentinel reconciliation (FF vs official vs MT5)

Forex Factory re-serves the official calendar (Eurostat HICP / BLS / ONS), so FF = official-aligned and
MT5 diverges — concentrated in `consensus`.

| sentinel | FF actual | official (source) | MT5 actual/cons | note |
|---|---|---|---|---|
| **EUR CPI m/m** (Jun-26) | −0.3/−0.2/0.0 (DE/FR/IT) | ≈ 0 (Eurostat HICP flash) | **3.2** (spike) | MT5 reports y/y as m/m → ~1000× error |
| EUR CPI y/y (Jul-01 flash) | 2.8 / 3.0 | 2.8 (Eurostat flash) | 3.0 / 2.1 | MT5 consensus 2.1 vs 3.0 → fake beat |
| US NFP (last 3) | 172, 57 | 172K, 57K (BLS) | 178/-6, 115/90, 172/77 | MT5 consensus −6/77 corrupt; a month behind |
| US CPI y/y (Jun-10) | 4.2 / 4.2 | 4.2 (BLS CPI) | 4.2 / 4.0 | MT5 Apr cons 2.3 vs actual 3.3 = fake +1.0 |
| GBP CPI y/y (last) | 2.8 / 3.0 | 2.8 (ONS CPI) | 2.8 / 3.8 | MT5 consensus 3.8 → fake −1.0 miss |

*(Official hardcoded with sources; FF = official-calendar re-serve, so the FF column is the reference and
the MT5 column shows the divergence.)*

---

## Verdict (re-review gate for Phase 3)

- **Corruption is real, systematic, in MT5's `consensus`/actual pairing** (EUR/JPY CPI, US/GBP surprises).
  FF removes it: **EUR inflation +1.33 (fake-hawkish) → 0.00 (neutral)**, JPY inflation +1.50 → −0.50,
  NFP/CPI/GBP consensus become sane.
- **FF also improves coverage/freshness** (CHF/EUR labour, fresher NFP/GBP CPI).
- **Both corrections applied**: fallback list 17 → **7** (cadence-aware, interest-rate excluded); flash/final
  deterministic at alias level (182 revision pairs in telemetry, no proximity in scoring).
- MonPol unchanged (FRED), baselines 100% FF, deltas explainable print-by-print, no unexplained regressions.
- **Recommend proceeding to Phase 3 review.** Confirm before switch: the 7 short Flash-PMI/JPY-GDP series
  on fallback, and the GBP-PMI flash→final reversal choice.
