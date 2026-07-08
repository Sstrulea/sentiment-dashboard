# Fundamental pillar V4 — pre-registered experiment (READ-ONLY, not adopted)

Branch `feat/fundamental-v4`. Tests three surprise-scoring variants set purely via config
flags — **V4.0** std+early (= production), **V4.1** mad+early, **V4.2** mad+late — against
pre-registered criteria C-A..C-D. **Nothing is adopted; production defaults (std+early)
remain byte-identical.** Invariants held: z_buckets [1.54, 0.81], window 12, signs, fallback,
pct_buckets, no_consensus→0, max_age, dedup, bias_thresholds. Pillar stays pure surprise.

## Formulas
- **σ (V4.1, `sigma_method: mad`)** on the same 12 raw (pre-direction) surprises `s`:
  `MAD = median(|s − median(s)|)`, `σ_mad = 1.4826·MAD`, `σ_final = max(σ_mad, sigma_floor)`.
  `sigma_floor = 0.1` for level/rate/index indicators (unemployment, PMI×2, cpi_yoy, core_cpi,
  core_pce, ppi_yoy, gdp_qoq, wage_growth, retail_sales); `0.0` otherwise. `σ_final ∈ {0, NaN}`
  → existing pct fallback (rule unchanged). `z = direction·surprise/σ_final`.
- **Contribution (V4.2, `quantize: late`)** per indicator: `|z|<0.2 → 0`; else
  `sign(z)·min(2, |z|·2/1.54)`. Anchors z=0.81→1.052, z=1.54→2.0, |z|≥3→2.0 (clamp).
  Category = weighted mean of continuous contributions (`score_precise`); displayed cell + bias
  unchanged. Under `early`, contribution == bucketed score → **byte-identical to production**.

## Replay setup
`src/fundamental_v4_replay.py` reuses the `calibration_analysis` as-of pattern: **829 business-day
as-ofs 2023-05-02 → 2026-07-03**, step 1, deterministic slice on `release_dt`, on the **FF calendar
(zero-placeholder quarantine applied)**. Each variant is the same code via flags. FX pairs+singles
via `build_payload` (fundamental + monetary/rate); cross-asset via `compute_crossasset_scores`
(+ realyield + liquidity). **Scope:** sentiment and trend are render-layer and variant-invariant,
so excluded — the variant effect lives entirely in the fundamental pillar. Forward returns from
the MT5 OHLC parquet: `r_H = close(as_of+H bd)/close(as_of) − 1`, H ∈ {5, 10}. 92,019 obs.

**Sign convention (verified before metrics):** score>0 on an FX pair = base strength = pair up.
EURUSD sentinel: EURUSD close rose 1.05→1.14 over the window (EUR strengthened), r5 computed matches
manual to 5 dp. `corr(sign score, sign r5) = −0.01` — convention correct; the composite simply has
**no short-horizon directional edge** (near-zero correlation), the central finding below.

## C-A (PRIMARY) — directional hit-rate, non-Neutral, per variant × H × class × period
| class | H | period | V4.0 | V4.1 | V4.2 | Δ(2−0) | n(V0) |
|---|---|---|---|---|---|---|---|
| fx | 5 | all | **51.3** | 50.7 | 50.5 | **−0.8** | 4174 |
| fx | 5 | 2024 | 41.4 | 45.9 | 46.2 | +4.9 | 145 |
| fx | 5 | 2025 | 52.6 | 52.1 | 51.1 | −1.5 | 2601 |
| fx | 5 | 2026 | 49.9 | 48.2 | 49.3 | −0.6 | 1428 |
| fx | 10 | all | **51.6** | 51.1 | 51.5 | **−0.1** | 4174 |
| cross-asset | 5 | all | **40.4** | 38.8 | 43.2 | **+2.9** | 793 |
| cross-asset | 5 | 2024 | 16.1 | 8.1 | 13.4 | −2.7 | 87 |
| cross-asset | 5 | 2025 | 51.8 | 46.1 | 51.2 | −0.6 | 409 |
| cross-asset | 5 | 2026 | 39.0 | 39.4 | 43.5 | +4.5 | 241 |
| cross-asset | 10 | all | **45.4** | 41.7 | 43.4 | **−2.0** | 793 |
| cross-asset | 10 | 2025 | 54.5 | 45.0 | 49.5 | −5.0 | 409 |
| cross-asset | 10 | 2026 | 51.9 | 49.2 | 47.8 | −4.0 | 241 |

FX ≈ 51% (coin-flip) in every variant; cross-asset < 50% (anti-predictive) at both horizons.

**Selectivity guard** (% non-Neutral, |score|>0): fx V4.0 37.1 / V4.2 43.1 (+6.0pp); cross-asset
25.0 / 30.6 (+5.6pp) — both **< 10pp**, so no equalization mandated. Equalized anyway (threshold =
V4.0 |score| p-cut, H=5): fx V4.0 51.2 / V4.2 50.8; cross-asset V4.0 40.4 / V4.2 43.2 — **no gain**.

## C-B — bias flips per instrument (median/yr, consecutive as-ofs)
V4.0 34 · V4.1 30 (−11%) · **V4.2 39 (+14%)** — within the +20% cap. **PASS.**

## C-C — |z| distribution std vs mad (DESCRIPTIVE — thresholds NOT re-derived, per pre-registration)
| pctile | std | mad |
|---|---|---|
| p50 | 0.639 | 0.674 |
| p60 | 0.806 | 0.839 |
| p75 | 1.134 | 1.078 |
| p87.5 | 1.535 | 1.562 |
| p95 | 2.000 | 2.295 |

mad recenters slightly higher at the median and fattens the upper tail (p95 2.30 vs 2.00). n: std 2238, mad 2137.

## C-D — coverage (non-fallback, non-stale, today)
V4.0 (std) **64** → V4.1 (mad) **62** / 81 series. mad **reduces coverage by 2** (series with a
zero-MAD window fall to pct fallback). **FAIL** ("coverage must not drop").

## Zero-rate (reported, not a target) — % Neutral bias
fx: V4.0 62.9 → V4.2 56.9 · cross-asset: V4.0 75.0 → V4.2 69.4. Late-quantize makes the book more
directional, but (per C-A) the extra directional calls do not convert to hit-rate.

## Today grid V4.0 → V4.2 (live, 2026-07-08): 7 category cells, 4 bias flips
GBP inflation 0→−1 · NZD inflation 0→+1 · NZD labour +1→0 · CAD growth +1→0 · CAD labour +2→+1 ·
CHF inflation −2→−1 · CHF labour −2→−1. Flips: AUDUSD Neu→Bear, GBPCHF Very Bull→Bull, AUDNZD
Neu→Bear, FTSE100 Neu→Bull.

**Sentinels (as-of 2026-07-08, engine — gated+deduped live data):** JOLTS z +0.744→+0.774,
ADP −0.479→−0.794, Services PMI +0.529→+0.568 (std→mad); USD labour category **0.00 (V4.0/V4.1) →
+0.086 (V4.2)** — late-quantize lifts a bucketed-0 category to a small non-zero mean, as designed.
(Numerics differ from the pre-registration's illustrative targets because the live path applies the
zero-quarantine gate + flash/final dedup + latest-selection; the mechanism/direction match.)

## Verdicts
| criterion | result |
|---|---|
| **C-A (primary)** | **NOT MET → V4.2 rejected.** No consistent ≥+1.5pp aggregate gain: fx Δ −0.8pp (H5) / −0.1pp (H10); cross-asset +2.9pp (H5) but **−2.0pp (H10, deteriorates >2pp)**; Δ **sign inconsistent across sub-periods** (both classes flip 2024↔2025↔2026). Equalized-selectivity: no gain. |
| **C-B** | MET — V4.2 flips +14% (≤ +20%). |
| **C-C** | Reported descriptively; thresholds NOT re-derived. |
| **C-D** | NOT MET — mad coverage 64→62 (drops 2). |

**Conclusion: REJECT V4.1 and V4.2. Keep production V4.0 (std+early).** The fundamental surprise
pillar has no exploitable 5–10d directional edge (FX ≈ coin-flip, sign-corr ≈ 0; cross-asset
anti-predictive). Robust-σ (mad) and late quantization reshape the score distribution and make the
book more directional, but cannot manufacture signal that is not in the data — and mad marginally
reduces coverage. Guard: `test_defaults_byte_identical_to_std_early` locks production unchanged.
Data: `data/fundamental_v4_backfill.csv` (92,019 obs).
