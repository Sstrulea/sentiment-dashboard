# Consensus widening on `to_scoring_frame` — measurement + mechanism

Context: the Manual Actuals Panel covers missing/ambiguous `actual`. A
separate gap — `actual` valid but `consensus` (FF `forecast`) quarantined
to NaN — meant an indicator contributed no surprise, and hence no score,
regardless of how good the actual was (live example: NZD Manufacturing PMI
59.70 and Services PMI 50.60, both `no_consensus`, both score 0 — NZD
growth showed N2 instead of N4). `src/ff_scoring.py`'s `to_scoring_frame`
now recovers a subset of these via the same m/m|q/q-suffix + `flagged_bad`
widening already used on the `actual` side — see that function's docstring
for the exact mechanics. This doc is the measurement behind the decision
and the change, and the reference for anyone debugging a score movement
this causes later.

## 1) Scope of the original gap (measured before this change)

Scoring frame, `actual` valid + `consensus` NaN: 194 rows full history, 7
within the panel's 45-day window. All 7 traced to `forecast==0.0`
quarantined by `to_scoring_frame`, never to a raw NaN forecast — i.e. FF
always sent a value, the pipeline discarded it.

Of those 7: 5 are PMI-type indices (AUD/CAD/NZD manufacturing/services PMI)
with no m/m|q/q suffix in `name_raw` — a 0.0 forecast for a ~50-baseline
index is not plausible, so quarantine there is correct; the real gap is
that FF never provides a consensus for these specific feeds at all, which
this widening cannot and does not touch (no suffix, no recovery route).
The other 2 (GBP `gdp_qoq` from "GDP m/m", USD `ppi_yoy` from "PPI m/m")
carry an m/m suffix — 0.0 is a plausible flat-growth forecast, and this is
exactly the ambiguity already solved on the `actual` side.

Widening a config-only can_be_zero route was explicitly rejected for
consensus: `can_be_zero` says whether an indicator's actual LEVEL/NET
CHANGE can legitimately be zero — it says nothing about whether a
forecast for an m/m|q/q print was legitimately zero. Only the
suffix+flagged_bad route applies.

## 2) Recovery rate (full history, m/m|q/q-suffixed consensus==0.0 rows)

67 candidate rows (consensus==0.0, m/m or q/q suffix, matched indicator).

| | rows |
|---|---|
| Total candidates | 67 |
| `flagged_bad` key present | 64 |
| `flagged_bad` key missing → blocked by default | 3 |
| **Recovered** (passes the guard) | **19** |
| **Still quarantined** | **48** |

19/67 is more conservative than a first glance at the 67 names suggests —
`flagged_bad` is a general JBlanked data-quality flag, not specific to
consensus plausibility, so it blocks many rows for reasons unrelated to
whether 0.0 was a sane forecast. This is intentional: the guard is
identical to the one already governing `actual`, and a restrictive false
negative (real 0.0 forecast stays quarantined) is preferred over a
permissive false positive (implausible 0.0 recovered).

Recovered, by (currency, indicator_key): USD `industrial_production_mm`
(4), CAD `cpi_yoy` (2), CHF `ppi_yoy` (2), one each — AUD
`capital_expenditure`, AUD `import_prices`, CAD `gdp_qoq`, CHF `cpi_yoy`,
GBP `gdp_qoq`, GBP `industrial_production_mm`, GBP `ppi_yoy`, JPY
`core_machinery_orders_mm`, USD `gdp_price_index`, USD `import_prices`,
USD `personal_spending_mm`.

## 3) Impact on today's live scores (simulated before implementing)

Ran `compute_currency_scorecard` before/after restoring the 19 recovered
consensuses, for all 6 affected currencies (AUD, CAD, CHF, GBP, JPY, USD).

**5 of 6 currencies: no change at all** — index, category N, category
cell all identical. The recovered rows are old (mostly 2023–2024); each
indicator already has a more recent print with valid consensus as its
"latest," and the trailing-K (12-pair) sigma window doesn't reach back far
enough to include the recovered row.

**USD: the only currency that moved, and indirectly:**
```
index: -2.161 -> -1.952   (Δ+0.208)
growth: N 8->8 (unchanged)  cell 0->0 (unchanged)  precise -0.125->0.000
```
None of the 5 recovered USD rows is itself the "latest" print for its
indicator. But recovering `personal_spending_mm @ 2026-02-08` (old,
already superseded) adds one more pair to the trailing-12 sigma window
used for that SAME series' actual latest print, `personal_spending_mm @
2026-07-30` — untouched directly:
```
personal_spending_mm (2026-07-30, latest — not itself recovered):
  z: -1.004 -> -0.567     score: -1 -> 0
```
The category cell doesn't move (the shift is absorbed by averaging with
the other 7 growth indicators), but the individual indicator card would
show a different score. **This is a correction, not a regression**:
today's sigma for `personal_spending_mm` is computed on an artificially
incomplete distribution — a real historical (actual, consensus) pair that
was always there got quarantined and silently excluded from the surprise
baseline. Recovering it completes the distribution the sigma should have
been computed on all along. See `to_scoring_frame`'s docstring for the
general version of this note.

## 4) Verification: no implausible 0.0 recovered

Structural, not just empirical: the candidate pool was filtered to
`extract_period_suffix(name_raw) in ("m/m", "q/q")` before anything else —
a PMI/index/y/y-named row can never enter it. Confirmed by inspection: all
67 `name_raw` values are genuine m/m or q/q transforms (CPI m/m, GDP m/m,
PPI m/m, Industrial Production m/m, Import Prices m/m|q/q, Capital
Expenditure q/q, Core Machinery Orders m/m, GDP Price Index q/q, Personal
Spending/Income m/m, Unit Labor Costs q/q, Wage Growth m/m) — none are
PMI/index/y/y. The 5 PMI rows from §1 and the other 159 non-suffixed
quarantined rows are untouched by this change; their gap is a missing FF
data feed, not something a widening rule can fix.
