# Proposal — continue AUD `retail_sales` under Household Spending m/m

Status: proposal, measured on real data (live parquet + `data/archive/`
re-parse, `as_of=2026-07-30`). **Not implemented — no scoring change made.**
Adoption is George's decision.

## Distributions (from the archive, per Faza B)

| series | n | mean | std | median | min | max |
|---|---|---|---|---|---|---|
| Retail Sales m/m | 33 | 0.176 | 1.247 | 0.3 | −3.9 | 2.0 |
| Household Spending m/m | 9 | 0.467 | 0.900 | 0.3 | −1.1 | 1.6 |

**Medians match exactly (0.3).** Standard deviations differ by ~1.4x
(1.247 vs 0.900) — well within ordinary sampling noise for n=9 vs n=33, not
the kind of gap that signals a unit mismatch. This is NOT the USD Core CPI
y/y trap (native 2.8/2.9 vs the m/m series' 0.2/0.4 — an ~14x, order-of-
magnitude jump on the SAME dates). Both series here are genuinely m/m %
changes, same unit, comparable scale. **Continuation under one canonical is
numerically viable** — the scales don't forbid it.

## Effect on AUD growth (measured, not simulated)

| scenario | growth coverage (N) | growth score_precise | retail_sales cell |
|---|---|---|---|
| Today (production) | 3 | −0.667 | stale=True, excluded, would-be score +2 |
| Display-only (this branch, `household_spending`/`growth_display`) | 3 (unchanged) | −0.667 (unchanged) | n/a — separate slot |
| **Continuation** (Household Spending m/m aliased onto `retail_sales`) | **4** | **−0.25** | fresh (2026-06-25), score **+1** |

Continuation revives the dead cell: `retail_sales` goes from `stale=True`
(364d, excluded from the category average) to a real, current contributor.
The revived score (+1) differs from the naive "if it were still fresh"
number (+2, computed on the pre-rename trailing window alone) because the
concatenated trailing-12 sigma (0.520) is measured across BOTH series
together, not just the old one — this is expected and correct: the sigma is
supposed to reflect the combined history once the two are treated as one
continuous series, not the stale series' sigma frozen at 2025-07.

## Pairs that change bias

All 7 AUD-leg pairs, today vs. continuation:

| pair | today | continuation | flip? |
|---|---|---|---|
| AUDUSD | −0.556 Neutral | −0.208 Neutral | no |
| EURAUD | 0.833 Neutral | 0.486 Neutral | no |
| GBPAUD | 0.556 Neutral | 0.208 Neutral | no |
| AUDJPY | −1.944 Bearish | −1.597 Bearish | no |
| AUDNZD | −1.806 Bearish | −1.458 Bearish | no |
| AUDCAD | −0.833 Neutral | −0.486 Neutral | no |
| **AUDCHF** | −1.389 Bearish | **−1.042 Neutral** | **yes** |

**One flip: AUDCHF, Bearish → Neutral.** Every pair's score moves toward
neutral (less extreme) under continuation — consistent with a stale-excluded
+2 cell being replaced by a fresh but more moderate +1, diluted further by
the wider post-concatenation sigma.

## Recommendation

Continue the series under one canonical (`retail_sales`), not a permanent
separate `household_spending` slot — the distributions support it, and
leaving it as `growth_display` forever means AUD growth stays permanently at
N3 with a structurally dead 4th slot, the same category of problem the CAD
core_cpi proposal describes (inert, not just mislabeled). The display-only
wiring done in this branch (Faza B) is the safe interim state; this
continuation is the natural next step once measured (now) and approved.

## Pre-registered acceptance criteria (V4/V5a style)

1. **Coverage moves 3→4 and stays there** — after continuation + backfill,
   AUD growth coverage must be 4 on every subsequent refresh (not
   intermittently 3, which would indicate the concatenated series has its
   own gaps the old one didn't).
2. **No other AUD indicator's coverage or score changes** as a side effect
   (isolation check, same as every other change on this branch).
3. **Bias-flip count bounded**: no more than 2 of AUD's 7 pair biases should
   flip on the day continuation goes live (measured here: 1 — AUDCHF).
   More than 2 on go-live day would suggest the backfilled history needs a
   second look before accepting.
4. **Sigma stability**: trailing-12 sigma after backfill should be within
   1.5x of the 0.520 measured here — a bigger deviation means the backfill
   merge handled the two series' concatenation differently than assumed in
   this measurement.

## Cost: backfill required, same caveat as the CAD proposal

The concatenated `retail_sales` history above (n=40 after dedup) already
draws on `data/archive/` for the Household Spending m/m leg — a real
backfill merge into `data/economic_calendar_ff.parquet` is needed to make
this the LIVE state; the numbers above are a read-only measurement, not a
committed change.

## Risks

- **This is a slot-swap** (repointing an EXISTING scored indicator_key's
  matcher pattern and merging its history), not an additive display-only
  change — same higher-blast-radius caveat as the CAD proposal.
- **Only 9 Household Spending prints exist** — the concatenated sigma
  (0.520) is influenced by relatively few new-regime points; if ABS revises
  its early Household Spending prints (common for a newly-launched series),
  the trailing sigma could shift more than usual on a routine backfill
  refresh, which criterion 4 above is designed to catch.
- **The renamed series may not be a strict conceptual continuation** —
  "Household Spending" and "Retail Sales" measure related but not identical
  baskets (spending vs. retail trade specifically); this proposal treats
  them as continuable based on measured distributional compatibility, not on
  a claim that ABS considers them methodologically identical.

## Not done here

No alias/matcher change points `retail_sales` at Household Spending m/m. No
backfill was written to any parquet. All numbers above come from a
read-only combination of the live parquet and a `parse_jblanked_range`
re-read of `data/archive/ff_calendar_range.json` (archive untouched, per
instruction).
