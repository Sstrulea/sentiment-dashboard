# Zero-placeholder quarantine — fix report

Branch `fix/zero-placeholder-quarantine`. Fixes the baseline contamination confirmed in
`docs/baseline_variance_audit.csv`: Forex Factory encodes an unreleased/missing print
(a delayed or cancelled release — e.g. the Oct-2025 US shutdown, and a Feb-2026 outage)
as **actual==0.0** or **consensus==0.0**. The date-based released-gate passed these PAST-dated
0.0 values as real, so they entered the z-baseline as fake huge surprises (e.g. USD
unemployment 0.0 vs 4.3 = −4.30), inflating σ and crushing every real z in that window.

## The gate
- **Per-indicator `can_be_zero` flag** in `data/economic_indicators.yaml` (default **False**).
  TRUE only where 0.0 is a legitimate reading: **employment_change** (net jobs change),
  **retail_sales** (m/m growth), **interest_rate_decision** (a no-change/0 policy rate).
  FALSE (levels/rates/indices — 0.0 impossible): unemployment_rate, manufacturing_pmi,
  services_pmi, cpi_yoy, core_cpi, ppi_yoy, core_pce, gdp_qoq, adp, jolts, jobless_claims,
  wage_growth.
- Applied in `ff_scoring.to_scoring_frame` (the FF→scoring bridge, where indicator_key is
  known): for a `can_be_zero=False` indicator, `actual==0.0` or `consensus==0.0` → **NaN**
  (logged), regardless of date. Runs on EVERY row → cleans both the ongoing weekly ingest
  AND the historical backfill on read. The raw FF parquet keeps the 0.0 (provenance); only
  the scoring frame is quarantined. **131 actual + 260 consensus** placeholders quarantined
  on the current data.

## Sentinels ✓
| sentinel | before (dirty) | after (quarantined) |
|---|---|---|
| USD unemployment_rate | z=−0.08 → **0** | z=+1.067 → **+1** (dead-zone fixed; sign + = unemployment *fell* 4.2<4.3, direction −1) |
| EUR cpi_yoy | z=−0.31 → **0** | z=−2.008 → **−2** (the "dead-zone" was contamination, not calibration) |
| CAD manufacturing_pmi | +53 fake surprise → **+1** | consensus→NaN → **no_consensus, 0** |

> Correction to a prior finding: the EUR-CPI "dead-zone" I concluded was *correct* in
> `docs/fundamental-calibration.md` was in fact contamination (the 2025-10-01 0.0 placeholder,
> surprise −2.20, dominated σ). Clean, it is a −2 signal.

## Grid before/after (gate off → on)
### Category cells changed: 7
| currency · category | before → after |
|---|---|
| EUR inflation | 0 → −1 |
| AUD growth | +1 → 0 |
| NZD growth | +1 → 0 |
| CAD inflation | +1 → 0 |
| CAD labour | 0 → +2 |
| CHF growth | 0 → +1 |
| CHF inflation | −1 → −2 |

### Instrument bias flips: 6 (masked signals restored)
AUDUSD Neutral→Bearish, EURGBP Neutral→Bearish, EURCHF Bullish→Neutral, AUDJPY Neutral→Bearish,
NZDJPY Bullish→Neutral, AUDCHF Bullish→Neutral.

## C2 thresholds re-derived on CLEAN data (reported, NOT changed)
The empirical |z| distribution over **2,238** prints (was 2,556 dirty):

| percentile | clean | dirty (deployed basis) |
|---|---|---|
| p60 (±1) | **0.806** | 0.81 |
| p87.5 (±2) | **1.535** | 1.71 |
| p90 | 1.642 | 1.873 |

- Deployed `z_buckets = [1.71, 0.81]` were derived on the CONTAMINATED distribution (fat tails
  from the fake surprises pushed p87.5 up). On clean data, |z|≥1.71 now catches only **8.7%**
  (target 10-15%); |z|≥0.81 catches **39.9%** (was 63.3% dirty).
- **Candidate on clean data: `[1.54, 0.81]`** — the ±1 threshold is essentially unchanged (0.81),
  but ±2 should come DOWN from 1.71 → ~1.54 to hit ~12%. **Not changed here** — flagged for a
  separate recalibration decision now that the baseline is clean.

## Tests
`test_config_can_be_zero_set`, `test_quarantine_zero_actual_and_consensus`,
`test_can_be_zero_true_keeps_zero`, `test_historical_placeholder_excluded_from_baseline`.
393 green.
