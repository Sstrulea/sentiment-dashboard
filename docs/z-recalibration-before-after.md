# z-threshold recalibration — before/after (production adoption)

Adopts criterion **C2** from `docs/fundamental-calibration.md`: `defaults.z_buckets`
**[1.0, 0.33] → [1.71, 0.81]** in `data/economic_indicators.yaml`. Recalibrated from the
empirical |z| distribution — 1.71 = p87.5 (±2 now catches ~12% of prints, was 31.7% at 1.0),
0.81 = p60. **Window unchanged** (12; C1 not confirmed). **Signs unchanged.** `pct_buckets`
(fallback) unchanged. `as_of 2026-07-06`, FF calendar.

## Sentinel sanity ✓
| sentinel | z | old score | new score |
|---|---|---|---|
| EUR (DE) unemployment_rate | +1.111 | **+2** | **+1** | (±2→±1 at \|z\|=1.11; sign + = unemployment fell) |
| EUR cpi_yoy | −0.31 | 0 | 0 | unchanged (\|z\|<0.81) |

## Grid impact (full grids: `docs/z-recal-{categories,instruments}.csv`)

**All changes reduce magnitude** (stricter thresholds → marginal reads pulled toward neutral);
**no sign flips** anywhere.

### Category cells changed: 6 / 30
| currency · category | before → after |
|---|---|
| USD growth | +1 → 0 |
| EUR labour | +2 → +1 |
| GBP inflation | −1 → 0 |
| NZD inflation | +1 → 0 |
| CAD labour | +1 → 0 |
| CHF inflation | −2 → −1 |

### Instrument bias flips: 4 (all toward less extreme)
| instrument | before → after |
|---|---|
| GBPNZD | Bearish → Neutral |
| NZDCAD | Bullish → Neutral |
| NZDCHF | Very Bullish → Bullish |
| SILVER | Bearish → Neutral |

### Per-indicator bucket changes: 25 (breakdown)
All are magnitude reductions (±2→±1 or ±1→0), e.g. USD core_cpi −2→−1, USD retail_sales +2→+1,
GBP cpi_yoy −2→−1, GBP wage_growth +2→+1, AUD core_cpi +2→+1, AUD gdp_qoq −2→−1, CAD cpi_yoy
+2→+1, CAD employment_change +2→+1, EUR unemployment +2→+1, CHF cpi_yoy −1→0. Full list in the
CSV. (Fallback/short-history series — flash-PMIs, JPY gdp_qoq — are unchanged: the recalibration
touches z-scored series only.)

## Tests
Updated the threshold boundary test to [1.71, 0.81]; updated the `DEFAULTS`/`test_superseded_gate`
fixtures to the recalibrated buckets (their z-path score assertions use large surprises that clear
both ±2 thresholds, so they stay valid); added `test_production_z_buckets_are_recalibrated`
guarding the adopted value (+ window/sign unchanged).
