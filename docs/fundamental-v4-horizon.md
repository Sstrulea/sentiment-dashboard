# Fundamental pillar — long-horizon directional edge (READ-ONLY, not adopted)

Branch `feat/fundamental-v4`. Extends the V4 replay to ask: does the fundamental pillar
have directional edge on **long** horizons, on non-Neutral readings only? One variant only —
**V4.0 (production std+early), fundamental-ONLY score** (growth+inflation+labour; no monetary,
rates, realyield, liquidity, sentiment, or trend). Same 829 as-ofs 2023-05-02 → 2026-07-03.
**Nothing adopted, no parameter changes.**

## Setup
Forward returns from the MT5 OHLC parquet at **H ∈ {15, 21, 30} TRADING days** (position-based:
`r_H = close[pos+H]/close[pos] − 1`, `pos` = last trading day ≤ as_of). Tail as-ofs with fewer
than H trading days ahead are **excluded** (NaN), never truncated to the last close. Filter: bias
∈ {Very Bearish, Bearish, Bullish, Very Bullish}. **9,634 non-Neutral obs**; with a price series
and H days available: 4,292 (H15) / 4,227 (H21) / 4,130 (H30). Buckets with n<100 flagged **thin**.

> **Methodological caveat.** With daily as-ofs and H=30, forward windows overlap 29/30 — the obs
> are massively autocorrelated. **No naive p-values**; the real test is sign consistency across
> disjoint sub-periods (M3). n is an effective-sample overstatement throughout.

## M1 — hit-rate vs per-instrument base rate (excess), per bucket × class × H
`base_bull(sym) = %(r_H>0)` unconditional; benchmark = base_bull (bull buckets) / 1−base_bull (bear).

| H | class | bucket | n | hit% | bench% | excess |
|---|---|---|---|---|---|---|
| 15 | fx | Bearish | 1346 | 53.4 | 43.7 | **+9.8** |
| 15 | fx | Bullish | 2158 | 62.2 | 53.7 | **+8.5** |
| 15 | fx | Very Bullish | 161 | 68.3 | 54.4 | +13.9 |
| 15 | fx | Very Bearish | 71 | 36.6 | 43.7 | −7.1 *thin* |
| 15 | x-asset | Bearish | 103 | 39.8 | 26.5 | **+13.3** |
| 15 | x-asset | Bullish | 391 | 79.0 | 69.7 | **+9.3** |
| 30 | fx | Bearish | 1288 | 48.3 | 41.4 | +6.9 |
| 30 | fx | Bullish | 2075 | 63.5 | 56.8 | +6.6 |
| 30 | x-asset | Bearish | 99 | 30.3 | 21.6 | +8.7 *thin* |
| 30 | x-asset | Bullish | 376 | 77.4 | 75.4 | +2.0 |

**Aggregate excess (all non-Neutral, per class × H):** fx +8.9 / +8.4 / +7.2 · x-asset +10.3 / +7.2 / +2.8.
Both classes positive at every H — after removing per-instrument drift, the pillar's non-Neutral calls
beat their base rate. (x-asset excess decays toward H30; fx holds.)

## M2 — mean forward return (bps) per bucket × class × H, + spread S
S = mean r_H(Bullish∪VBull) − mean r_H(Bearish∪VBear).

| H | class | VBear | Bear | Bull | VBull | **S** | monotone |
|---|---|---|---|---|---|---|---|
| 15 | fx | 36 | −13 | 34 | 64 | **+47** | ✗ |
| 21 | fx | 33 | −13 | 46 | 105 | **+61** | ✗ |
| 30 | fx | 9 | −4 | 58 | 149 | **+68** | ✗ |
| 15 | x-asset | 30 | 590 | 815 | 3310 | **+443** | ✓ |
| 21 | x-asset | 185 | 739 | 980 | 3928 | **+459** | ✓ |
| 30 | x-asset | 351 | 908 | 1102 | 4201 | **+421** | ✓ |

S > 0 for both classes at all H. **fx monotonicity fails only via the thin VBearish bucket** (n=71,
mean +36 > Bearish −13); the core Bearish < Bullish < Very Bullish holds. **x-asset is cleanly
monotone** VBear < Bear < Bull < VBull — but Very Bullish is n=2 (meaningless) and the levels are
inflated by index up-drift (base rates ~70–75%).

## M3 — spread S sign by sub-period (2024/2025/2026), per class × H
| H | class | 2024 | 2025 | 2026 | consistent w/ full-sample (+)? |
|---|---|---|---|---|---|
| 15 | fx | −114 | +56 | +36 | 2/3 |
| 30 | fx | −75 | +99 | −28 | **1/3** |
| 15 | x-asset | n/a | +469 | +782 | 2/2 |
| 30 | x-asset | n/a | +581 | +332 | 2/2 |

**fx spread flips negative in 2024** (and again in 2026 at H30) — the edge is regime-dependent.
**x-asset is positive in every sub-period with data — but 2024 is n/a** (cross-asset non-Neutral
coverage only builds from 2025), so "consistent" rests on **two** sub-periods, not three.

## Verdict (pre-registered: edge iff (i) S>0 @H15 AND H30, (ii) sign consistent ≥2/3 sub-periods, (iii) aggregate excess>0)
| class | (i) S>0@15&30 | (ii) sign-consistent | (iii) excess>0 | verdict |
|---|---|---|---|---|
| **fx** | ✓ | **✗ (H30 1/3; 2024 reversed)** | ✓ | **NULL confirmed** |
| **cross-asset** | ✓ | ✓ (2/2) | ✓ | **EDGE (per criterion)** |

**Interpretation.** Unlike the 5–10d null, at 3–6 weeks the fundamental pillar's non-Neutral calls
carry directional information: aggregate base-rate-adjusted excess is +7–10pp for BOTH classes, and
the bull−bear spread is positive across all H. But the pre-registered robustness gate splits them:

- **FX → NULL.** Positive on the full sample, but the spread **reverses in 2024** and is inconsistent
  at H30 (1/3). Regime-dependent, not a robust standalone edge.
- **Cross-asset → EDGE by the criterion, but PROVISIONAL.** It is monotone and sign-consistent, yet
  the evidence is thin: only **two** sub-periods carry data (2024 n/a), Very Bullish is n=2, base rates
  are high (drift-heavy), and windows overlap 29/30. This clears the pre-registered bar but is **not**
  strong enough to adopt — it warrants a dedicated confirmation study (more history, disjoint windows,
  per-instrument breakdown) before any use.

**Nothing is adopted; production is unchanged.** Deliverable: `data/fundamental_v4_h30.csv`
(9,634 non-Neutral obs with r15/r21/r30). Reproduce: `python -m scripts.v4_horizon`.
