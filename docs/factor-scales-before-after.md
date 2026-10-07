# Scoring v2 — factors on the same scale (rule N-c): before / after

Branch `feat/factor-scales` (from main `c7467fe1`, 2026-10-06). Parts approved: A (cross-asset),
B (FX pairs + US Dollar), C (`gdp_price_index` display-only). Instrumentation:
`scripts/measure/factor_scales.py` (`validate`, `measure`, `report`); per instrument-week numbers in
`docs/factor-scales-before-after.csv` (score_v1, score_v2, labels, v2 inputs).

## Why

The config weights did not show up in the score because the factors entered on different scales:
cross-asset read the rounded category cell, the rate engine's 1.0/0.5 steps put rates at ±2 on ~34%
of days (12.5% for a macro indicator), and the COT (±4) / P-C (±3) cells have a 2-5× larger std than
a macro category.

## Correction 2026-10-07 — US Dollar on the pairs' scale

Under N-c every input is in σ, but pairs score × scale / pair_divisor (2.5) while the US Dollar row
scored × scale (5): the same number of σ gave the dollar twice a pair's score (Very in ~47% of the
last 53 weeks). With `factor_scales_single` the row now scores (scale / pair_divisor) × its
normalised weighted mean; contributions follow (residual < 1e-9); score_v1 unchanged. The FX
thresholds were re-derived after the fix: **0.89 / 1.93** (0.887 / 1.935). The tables below that
involve the FX board are the post-fix numbers.

## Rule N-c

1. Macro = the category's `score_precise` (not `score_cell`) on both boards.
2. Rates = continuous signal `clip(z / 0.87, −2, 2)` for a currency's 2Y, a pair's 2Y spread and
   DFII10 (0.87 = p60 of |z| 2005-2026, cap 2 ≈ p87.5); a series without z keeps its bucket.
3. Every factor input / its σ (historical std on its board), clipped ±3. Weights, signs, D1=D,
   the D2-c fold (w_s = 0.125) and the COT / P-C engines are unchanged.
4. Bias thresholds re-derived: p55/p90 of |score|, last 53 weeks, FX and cross-asset separately.
5. `score_v1` / `bias_v1` on every instrument (v1 formula + v1 thresholds), not displayed, 26 weeks.
6. Without the new config keys every score is the v1 one (C1).
7. A cell shows the value used in the score (one decimal when continuous).

## Step 0 — reconstruction

Weekly, Friday 21:00 UTC, 2023-09-22 .. 2026-10-02 (159 weeks), through the production functions
(`_load_calendar_frame` + policy rate + `scoring_view`, cut at release_dt ≤ t; `compute_rate_scores`
/ `compute_pair_spread_scores`; COT ≤ t, a cell counting only with `spec_extreme_6m`; P/C ≤ t;
`compute_realyield_score`; TREND off).

Validation at the as_of of `public/data/economic.json` (2026-10-06 15:06:51, main config): all 37
FX and cross-asset scores reproduced — max |diff| 2.2e-16 through the production path, 4.4e-16
through the script's own v1 decomposition; over all 159 weeks the decomposition matches production
to 1.1e-15.

## σ per board (ddof=1, every instrument-week)

| board | growth | inflation | labour | rates / monetary | sentiment |
|---|---|---|---|---|---|
| FX pairs — measured without C | 0.775 | 0.818 | 0.932 | 1.026 | 2.182 |
| FX pairs — **with C (config)** | 0.775 | **0.848** | 0.932 | 1.026 | 2.182 |
| US Dollar — without C | 0.279 | 0.410 | 0.298 | 1.024 | 2.190 |
| US Dollar — **with C** | 0.279 | **0.528** | 0.298 | 1.024 | 2.190 |
| indices — without C | 0.377 | 0.466 | 0.466 | 0.937 | 1.809 |
| indices — **with C** | 0.377 | **0.530** | 0.466 | 0.937 | 1.809 |
| metals — without C | 0.279 | 0.409 | 0.298 | 0.976 | 1.858 |
| metals — **with C** | 0.279 | **0.527** | 0.298 | 0.976 | 1.858 |

Without C every σ matches the reference (±0.5%). With C only the inflation σ moves: FX +3.6%
(inside ±5%), US Dollar / metals +29%, indices +14%. Removing `gdp_price_index` takes one print out
of the USD inflation mean (N 6 → 5 today) — a mean of fewer surprises varies more, so the USD
inflation category (which indices and metals read) widens; on pairs it is diluted by the other
currencies. The config carries the with-C σ (the distribution the score now sees).

## Influence (mean over instrument-weeks of |contribution_f| / Σ|contributions|), macro / rates / sentiment, %

| board | v1 (main) | v2 without C | v2 with C | target |
|---|---|---|---|---|
| FX pairs | 63.3 / 11.7 / 25.0 | 73.8 / 13.1 / 13.1 | 73.9 / 13.0 / 13.1 | 74 / 13 / 13 |
| US Dollar | 33.4 / 26.7 / 39.9 | 64.5 / 21.8 / 13.6 | 63.8 / 22.4 / 13.8 | 64 / 22 / 14 |
| indices | 18.8 / 41.0 / 40.3 | 61.4 / 24.8 / 13.8 | 61.4 / 24.9 / 13.7 | 61 / 25 / 14 |
| metals | 11.5 / 43.5 / 44.9 | 60.6 / 25.3 / 14.0 | 60.0 / 25.9 / 14.1 | 61 / 25 / 14 |

(v1 with C: 63.6/11.6/24.8, 35.2/26.2/38.5, 22.6/39.2/38.2, 17.7/41.0/41.3.)

## Thresholds (p55 / p90 of |score|, last 53 weeks 2025-10-03 .. 2026-10-02)

| board | v1 (config) | v2 without C | **v2 with C (config)** |
|---|---|---|---|
| FX (28 pairs + US Dollar) | 1.12 / 2.41 | 0.90 / 2.00 (before the fix) | **0.89 / 1.93** (0.887 / 1.935, after the fix) |
| cross-asset (6 indices + 2 metals) | 1.50 / 3.33 | 1.92 / 3.75 | **1.70 / 3.68** (1.702 / 3.676) |

## Label split on the last 53 weeks — Neutral / Bull-Bear / Very, %

| | v1 | v2 (with C) |
|---|---|---|
| FX table | 61.4 / 32.7 / 5.9 | **55.2 / 34.5 / 10.3** |
| cross-asset table | 49.5 / 42.0 / 8.5 | **55.0 / 35.1 / 9.9** |
| — FX pairs only | 62.3 / 32.3 / 5.4 | 55.5 / 34.4 / 10.1 |
| — US Dollar only (53 obs) | 37.7 / 43.4 / 18.9 | 47.2 / 37.7 / **15.1** (8 of 53 weeks Very; criterion ≤ 15%) |
| — indices only | 51.3 / 40.3 / 8.5 | 57.5 / 35.8 / 6.6 |
| — metals only (106 obs) | 44.3 / 47.2 / 8.5 | 47.2 / 33.0 / 19.8 |

The thresholds are pooled per table, so the 55/35/10 split holds per table. After the scale fix the
US Dollar row is Very in 8 of the last 53 weeks (|score| 2.52, 2.41, 2.18, 2.17, 2.17, 2.05, 2.03,
1.98 vs very 1.93 — the 8th is not a rounding case); the reference was 7 (49 / 38 / 13).

## Stability — weeks between label changes, each formula with its own p55/p90 per board, whole window

| board | v1 | v2 without C | v2 with C | reference |
|---|---|---|---|---|
| FX | 2.77 (with C 2.82) | 2.91 | 2.86 (after the fix) | 2.8 → 2.9 |
| indices | 1.67 (1.69) | 2.39 | 2.41 | 1.7 → 2.4 |
| metals | 2.03 (2.04) | 2.19 | 2.29 | 2.0 → 2.2 |

(Pooling the cross-asset thresholds over indices + metals instead gives metals 2.04 → 1.99; the
per-board definition is the one that reproduces the reference.)

## Labels that change today (as_of 2026-10-06 15:06:51, main vs branch)

After the US Dollar fix and the FX re-derivation (0.89 / 1.93): **9 of 37** — the 8 below plus
AUD/CHF Neutral −0.01 → Bearish −0.89. US Dollar stays Neutral (main +0.82, branch −0.18).

Before the fix (thresholds 0.90 / 1.99), with C: **8 of 37**

| instrument | main | branch |
|---|---|---|
| USD/CHF | Neutral −0.60 | Bearish −1.64 |
| CAD/CHF | Neutral −0.90 | Bearish −1.48 |
| CHF/JPY | Neutral +0.43 | Bullish +1.05 |
| NZD/USD | Neutral +0.63 | Bullish +1.40 |
| NZD/CAD | Neutral +0.93 | Bullish +1.23 |
| EUR/JPY | Neutral +0.97 | Bullish +0.92 |
| EUR/CAD | Bullish +1.32 | Neutral +0.76 |
| Gold | Bearish −3.12 | Neutral −0.63 |

Without C (same code, main indicators, σ/thresholds measured without C): **11 of 37** — exactly the
reference list (Dow / S&P / Nasdaq → Very Bearish −3.79; Gold → Neutral; USD/CHF, AUD/CHF, CAD/CHF →
Bearish; CHF/JPY, NZD/USD, NZD/CAD → Bullish; EUR/CAD → Neutral).

Differences come from part C: the USD inflation category goes 0.167 → −0.200 (the Advance GDP
price index leaves the mean), which flips the sign of the indices' and Gold's inflation factor —
Dow / S&P / Nasdaq end at −2.97 (Bearish, not Very), Gold at −0.63; with the lower cross-asset
thresholds. JPY inflation 0.667 → 0.600 lifts EUR/JPY to +0.92 (Bullish). AUD/CHF lands at −0.89,
just under 0.90 (the FX inflation σ is 0.848 instead of 0.818).

## Informative — hit rate (non-Neutral labels, sign of the forward return) and mean weekly Spearman

Prices: FRED H.10 crosses for FX, DTWEXBGS for the US Dollar, FRED SP500 / DJIA / NASDAQCOM /
NIKKEI225, `data/price_history.parquet` for DAX, FTSE100, Gold, Silver (from late 2024 only).

| | 1 week v1 → v2 | 4 weeks v1 → v2 |
|---|---|---|
| FX (with C) | 51.6% → 51.0% (ρ 0.011 → 0.005) | 56.3% → 54.6% (ρ 0.050 → 0.044) |
| FX (without C) | 51.4% → 51.1% | 56.4% → 54.1% |
| indices (with C) | 39.4% → 43.2% | 35.6% → 44.3% |
| metals (with C) | 53.2% → 47.9% | 59.2% → 55.9% |

FX matches the reference (56.4 → 54.2). Indices and metals do not (ref 43.3 → 48.6 and
61.3 → 62.4): the local DAX / FTSE / metals series start in late 2024, so their samples are short
(metals n ≈ 70) — none of these differences is significant.

## STRENGTH_PCT_K (scripts/measure/strength_pairs_distribution.py, 53 weeks to 2026-10-02)

Constant in `src/economic_render.py`: 22.0. Same method today on v1: p95 |score| 1.605 → K 25.0.
On v2: p95 1.717 → K_raw 23.29 → **23.5** — set in `src/economic_render.py` (2026-10-07). The US
Dollar row does not enter Strength, so the scale fix leaves K unchanged.

## Checks

- C1: branch code with the N-c keys stripped and the main indicators config = main, bit for bit, on
  every score of the economic and history payloads (the only differences: `meta.model` from the
  appended model version, `/strength` pct + `meta.strength_pct_k` from K 23.5, and timestamps).
- Visual (headless Chromium, 2026-10-07): /economic and /strength load with no console errors; the
  cross-asset popup shows value, value in σ and contribution, Σ = score (Gold −0.626 = −0.626); rate
  cells with one decimal; the model note shows on /economic and /strength.
- C6: max |contrib_residual| 4.4e-16 (FX), 0 (cross-asset).
- C7: `score_v1` / `bias_v1` on 37 / 37 instruments, not read by any page script.
