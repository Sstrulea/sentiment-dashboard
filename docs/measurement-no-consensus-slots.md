# Measurement — no-consensus slots diluting category scores

Status: **measurement only**, branch `measure/no-consensus-slots`. No production
file changed. No parquet written. `data/archive/` read-only (parsed via the
existing `parse_jblanked_range`, never written). All numbers below come from
running the **unmodified** production scoring functions
(`compute_indicator_score`, `compute_instrument` from `src/economic_compute.py`,
and `to_scoring_frame` from `src/ff_scoring.py`) against the real, live
`data/economic_calendar_ff.parquet`, `as_of = 2026-07-30`. The only new code is
in `scripts/measure/` (harness + report scripts, clearly marked as
instrumentation, never imported by anything under `src/`).

Harness fidelity check (done before trusting any variant number): re-running
the production aggregation path through the measurement harness reproduces
`build_payload`'s real output **bit-for-bit** — 0 mismatches across all 8
currencies' categories/index and all 29 instruments (28 FX pairs + the
US-DOLLAR single row). See `scripts/measure/common.py` docstring and the
verification command in Appendix A.

## Pre-registered predictions

Written from reading the spec + `economic_compute.py` before running
`scripts/measure/run_all.py`. Compared against measured results in §7.

1. The 6 flagged series are 0% or ~100% no-consensus at the series level — no
   surprises expected there (this was given as verified).
2. **Variant A and Variant B will NOT be "nearly identical" if — and only
   if — point 1 turns up a currently-healthy series whose *latest* print
   happens to lack consensus.** The spec's own text flags this as the
   deciding condition. Prediction: worth checking specifically for GDP
   series, which are visibly more prone to missing forecasts (flash/advance
   prints) than headline CPI/PPI.
3. AUD growth is expected to hit **N=1** (GDP alone) under both variants,
   exactly as illustrated in the spec — this should also be the single
   biggest source of bias flips, since one atomic score (GDP's) will now
   swing the whole category instead of being averaged down by two 0-scored
   PMI slots.
4. Cross-asset instruments (DJIA/SP500/NASDAQ/DAX/NIKKEI/FTSE100/GOLD/SILVER)
   should show **zero** change under any variant — none of their home
   currencies (USD/EUR/JPY/GBP) is AUD/CAD/NZD.
5. CAD core_cpi promotion to Median CPI y/y should remove CAD from the
   blacklist (Median has consensus in the archive, per the existing
   proposal doc) and should not, by itself, introduce new bias flips beyond
   what Variant A already causes for CAD's legs — the proposal document
   already measured Median's current surprise as exactly 0.

## 1. Row-level inventory, all populated series

Script: `scripts/measure/inventory_no_consensus.py` (run via `to_scoring_frame`,
so quarantine is applied exactly as the live payload sees it).

Of 140 theoretical (currency, indicator) combinations allowed by
`economic_indicators.yaml`, **82** have at least one row with a valid `actual`
in the live FF parquet (the other 58 are indicators the country-matcher never
maps for that currency — e.g. `household_spending`/`cpi_monthly` are AUD-only
in the matcher despite having no `currencies:` whitelist in the config; this
is pre-existing and out of scope). **The spec cites 83; this run finds 82 — a
1-series discrepancy not investigated further (does not change any
structural conclusion below).**

### 1a. The 6 flagged series — confirmed structural (row counts match spec exactly)

| ccy | indicator | raw total prints | valid-actual prints (post zero-quarantine) | with consensus |
|---|---|---|---|---|
| CAD | Core CPI y/y (`core_cpi`) | 54 | 44 | 0 |
| NZD | BusinessNZ Manufacturing Index | 43 | 43 | 0 |
| NZD | BusinessNZ Services Index | 43 | 43 | 0 |
| CAD | S&P Global Manufacturing PMI | 43 | 41 | 2 |
| AUD | S&P Global Manufacturing PMI | 8 | 7 | 0 |
| AUD | S&P Global Services PMI | 8 | 7 | 0 |

**Verification note (spec assumption refined, not overturned):** the spec's
"total" column matches the **raw** print count exactly (54/43/43/43/8/8 —
confirmed against the FF parquet before any quarantine). What it doesn't
state is that some of those raw prints don't even reach "valid actual" —
for CAD `core_cpi` specifically, **10 of the 54 raw prints have
`actual == 0.0`**, and because `core_cpi` is not in `can_be_zero`, the
zero-placeholder quarantine (`src/ff_scoring.to_scoring_frame`) nulls those
`actual` values too, not just `consensus`. Practically inert for the
no-consensus question itself (consensus is 0% either way, out of 44 or 54),
but it means 10 of CAD `core_cpi`'s historical releases are **entirely
invisible** to scoring, not merely unscored — a legitimate m/m-flat-month
reading (this series is fed by "Core CPI m/m", see
`docs/proposal-cad-core-promotion.md`) is being thrown away by the same
mechanism that guards against FF's unreleased-event placeholder. This is a
`can_be_zero`-flag gap, not a no-consensus-slot issue — flagged per instructions
but **not fixed here** (`can_be_zero` edits are explicitly out of scope, and
the CAD Median promotion in §6 makes it moot for `core_cpi` specifically by
retiring the wrong-unit series entirely).

### 1b. Otherwise-healthy series with scattered no-consensus rows (the "transitory" check)

**This directly contradicts a stated assumption.** The spec states: *"Nu
există în date niciun caz tranzitoriu (serie sănătoasă cu o lună fără
forecast)"* — no transitory case exists. Measured: **14 series** have
between 1.1% and 18.8% of their historical prints missing consensus while
being otherwise healthy (>80% coverage):

| ccy | indicator | total | no-consensus rows | pct |
|---|---|---|---|---|
| CAD | GDP q/q | 32 | 6 | 18.8% |
| EUR | GDP q/q | 12 | 2 | 16.7% |
| GBP | GDP q/q (m/m alias) | 37 | 5 | 13.5% |
| CHF | CPI y/y | 32 | 4 | 12.5% |
| GBP | PPI (Output y/y) | 30 | 3 | 10.0% |
| USD | PPI y/y | 38 | 3 | 7.9% |
| CHF | PPI y/y | 38 | 2 | 5.3% |
| CAD | PPI y/y | 39 | 2 | 5.1% |
| CAD | CPI y/y | 40 | 2 | 5.0% |
| USD | Core PCE y/y | 39 | 1 | 2.6% |
| EUR | PPI y/y | 42 | 1 | 2.4% |
| USD | JOLTS | 44 | 1 | 2.3% |
| USD | Wage Growth | 43 | 1 | 2.3% |
| USD | Jobless Claims | 182 | 2 | 1.1% |

GDP series are the visible pattern (13–19% vs ≤12.5% everywhere else) — flash/
advance GDP prints occasionally carry no forecast. Confirms prediction #2's
hunch.

**Of these 14, exactly 2 currently have their *latest* (as-of-2026-07-30)
print land on a no-consensus row**: **GBP GDP q/q** (actual 0.1, no
consensus, released 2026-07-16, not stale) and **USD PPI y/y** (actual -0.3,
no consensus, released 2026-07-15, not stale). This is exactly the
transitory case the spec said doesn't exist, occurring **right now**, and it
is exactly what makes Variant A and Variant B diverge today (§3).

## 2. Baseline (current production), affected currencies

Confirms the spec's own worked example bit-for-bit (CAD inflation:
Core CPI 0.10/no-consensus/0, CPI y/y -0.40/-0.20/-1, PPI -1.40/-0.40/-1,
precise -0.667, N3):

| ccy | category | N (coverage) | score_precise | notes |
|---|---|---|---|---|
| AUD | growth | 3 | -0.667 | Mfg PMI + Services PMI both no-consensus, contribute 0; GDP -2, Retail Sales stale (excluded) |
| AUD | inflation | 2 | -1.000 | 3 rows displayed (cpi_yoy, core_cpi, ppi_yoy); core_cpi is `stale` (already excluded) — the pre-existing stale/absent asymmetry the spec's "mechanism" section describes |
| CAD | growth | 3 | 0.667 | Mfg PMI no-consensus, contributes 0 |
| CAD | inflation | 3 | -0.667 | Core CPI no-consensus, contributes 0 (the spec's worked example) |
| NZD | growth | 4 | 0.000 | Both Mfg/Services indices no-consensus, contribute 0 |
| NZD | inflation | 2 | 0.500 | unaffected (no NZD series in the flagged list) |

Full baseline currency index: AUD 0.00, CAD 1.25, CHF 1.11, EUR 2.50,
GBP 2.08, JPY 2.22, NZD 1.94, USD 2.08 (monetary/rate category included,
identical across all variants below — see §3 harness note).

## 3. Variant A vs Variant B — measured, not assumed

Both variants tested with the SAME harness (`scripts/measure/common.py`),
which re-runs `compute_indicator_score` unmodified and swaps only the
category-inclusion predicate — this is the one line
(`if cat in per_cat and not scored.get("stale")`) either variant would
actually change in production.

- **Variant A** (blacklist): exclude iff `stale` OR `(currency, indicator_key)`
  in the fixed 6-series list.
- **Variant B** (per-row): exclude iff `stale` OR `flag == "no_consensus"`.

**They do NOT give nearly-identical results today.** Variant B additionally
excludes **GBP GDP q/q** and **USD PPI y/y** — the two transitory hits found
in §1b — which Variant A's static blacklist has no way to know about. Net
effect: **8 instruments differ numerically** between A and B (none currently
flip bias relative to each other):

| symbol | Variant A score | Variant B score | diff |
|---|---|---|---|
| US-DOLLAR | 2.0833 | 1.6667 | -0.417 (USD inflation N 3→2 under B only) |
| EURUSD | 0.2083 | 0.4167 | +0.208 |
| GBPUSD | 0.0000 | 0.2083 | +0.208 |
| USDJPY | -1.3889 | -1.6667 | -0.278 |
| USDCHF | -0.8333 | -1.1111 | -0.278 |
| USDCAD | 0.4167 | 0.2083 | -0.208 |
| AUDUSD | -1.8750 | -1.6667 | +0.208 |
| NZDUSD | 1.2500 | 1.5278 | +0.278 |

This is exactly the divergence the spec asked to be quantified ("dacă punctul
1 găsește rânduri răzlețe... trebuie știut cu cât") — now it's quantified:
**up to 0.42 points on the ±10 scale, 0 bias flips caused by the A/B
difference itself.**

## 4. Effect vs today — both variants

### Category level (only currencies with a changed cell shown; all others are
byte-identical to baseline in both variants — verified, not assumed)

| ccy | category | N before | N after (A) | N after (B) | precise before | precise after (A) | precise after (B) |
|---|---|---|---|---|---|---|---|
| AUD | growth | 3 | **1** | **1** | -0.667 | **-2.000** | **-2.000** |
| CAD | growth | 3 | 2 | 2 | 0.667 | 1.000 | 1.000 |
| CAD | inflation | 3 | 2 | 2 | -0.667 | -1.000 | -1.000 |
| NZD | growth | 4 | 2 | 2 | 0.000 | 0.000 | 0.000 |
| GBP | growth | 4 | 4 | **3** | 0.000 | 0.000 | 0.000 |
| USD | inflation | 3 | 3 | **2** | -0.667 | -0.667 | -1.000 |

Every changed cell is explained by exactly the slots removed (criterion 1 —
verified by construction, see the "slot-level explanation" table in Appendix
B): AUD growth loses Mfg+Services PMI (both variants); CAD growth loses Mfg
PMI; CAD inflation loses Core CPI; NZD growth loses Mfg+Services indices; GBP
growth loses GDP q/q (Variant B only); USD inflation loses PPI y/y
(Variant B only). No other cell, in any currency, moves at all.

### Currency index, before → after

| ccy | index before | index after (A) | index after (B) |
|---|---|---|---|
| AUD | 0.00 | **-1.67** | **-1.67** |
| CAD | 1.25 | 1.25 | 1.25 |
| NZD | 1.94 | 1.94 | 1.94 |
| USD | 2.08 | 2.08 | **1.67** |
| GBP | 2.08 | 2.08 | 2.08 |
| CHF, EUR, JPY | unchanged | unchanged | unchanged |

CAD's and NZD's index are **unchanged** despite their category N dropping —
not a bug, and explained arithmetically by the same rule in both cases:
removing a no-consensus entry always removes a **0** from the category's sum
(that's what the `no_consensus` flag scores), so the category mean only
moves if the *remaining* sum is non-zero. NZD growth's remaining 3 entries
already summed to exactly 0 before removal → mean stays 0.0 at N=2. CAD's
two affected categories (growth, inflation) both have non-zero remaining
sums, so each category's own mean *does* move (§4 table: growth
0.667→1.000, inflation -0.667→-1.000) — but by equal-and-opposite amounts
(±0.333), which cancel in CAD's 4-category currency-level mean, leaving
CAD's index unchanged. **GBP's own categories never move at all** (its growth
sum was also exactly 0 before removing GDP q/q, mean stays 0.0 at N=3) — the
GBP-leg pairs that move (GBPUSD, EURGBP-adjacent) do so purely because
**USD**'s inflation mean moves (ppi_yoy's remaining sum ≠ 0, same mechanism
as CAD), which is why the US-DOLLAR single-currency row itself is the one
that changes (2.08→1.67 under Variant B only) while GBP's own index (2.08)
stays put in every variant.

### All 28 FX pairs + US-DOLLAR, before → after (full table in
`docs/measurement-no-consensus-instruments.csv`)

**Bias-flip count — enumerated, not estimated: 6 pairs, identical set for
both variants:**

| pair | before | after (A) | after (B) |
|---|---|---|---|
| AUDUSD | Neutral (-1.04) | **Bearish (-1.88)** | **Bearish (-1.67)** |
| GBPAUD | Neutral (1.04) | **Bullish (1.88)** | **Bullish (1.88)** |
| AUDJPY | Bearish (-1.94) | **Very Bearish (-3.06)** | **Very Bearish (-3.06)** |
| AUDNZD | Bearish (-1.81) | **Very Bearish (-2.92)** | **Very Bearish (-2.92)** |
| AUDCAD | Neutral (-0.63) | **Bearish (-1.46)** | **Bearish (-1.46)** |
| AUDCHF | Bearish (-1.39) | **Very Bearish (-2.50)** | **Very Bearish (-2.50)** |

**Every flip involves AUD.** This is the direct, arithmetic consequence of
AUD growth's N=3→1 collapse (§5) — a single indicator (GDP, score -2) now
sets the whole category instead of being averaged with two 0-scored PMI
slots, and AUD is the currency whose one un-excluded growth print (GDP) is
also its most extreme score. No other currency's collapse (CAD growth 3→2,
CAD inflation 3→2, NZD growth 4→2) is severe enough, or its remaining
mean far enough from 0, to cross a bias threshold. GBP/USD's Variant-B-only
change (N 4→3 / 3→2) doesn't cross a threshold either.

**Cross-asset instruments (DJIA/SP500/NASDAQ/DAX/NIKKEI/FTSE100/GOLD/SILVER):
0 of 8 changed, under either variant** — confirmed by direct computation, not
inferred from config (all 8 home currencies are USD/EUR/JPY/GBP; none of the
3 affected currencies AUD/CAD/NZD ever appears as a cross-asset home
currency). Prediction #4 confirmed exactly.

## 5. The N=1 / N=0 problem

Measured cell-by-cell across all (currency, category) pairs (excluding
`monetary`, untouched by either variant):

| variant | N=1 cells | N=0 cells |
|---|---|---|
| baseline | 2 (CHF inflation, CHF labour — pre-existing, unrelated to this measurement) | 0 |
| Variant A | 3 (+ AUD growth) | 0 |
| Variant B | 3 (+ AUD growth) | 0 |

**AUD growth hits exactly N=1 under both variants, as the spec predicted**:
GDP (consensus present) is the sole survivor; Mfg PMI and Services PMI are
excluded by the rule, Retail Sales was already `stale`-excluded before either
variant touched anything. **No cell reaches N=0 in either variant, today** —
criterion 2 is satisfied as measured, not merely assumed.

**What the code does at N=0** (read from `compute_currency_scorecard` /
the measurement harness, which copies the same formula verbatim): no
division by zero — guarded by `if coverage > 0 and wsum > 0`. At N=0,
`score_precise = 0.0`, `score_cell = 0`, `coverage = 0`, and the category is
**silently dropped from the currency index** (`cat_scores_for_index` only
appends when `coverage > 0`) — i.e. it behaves exactly like an *absent*
category, not a "neutral surprise." A downstream consumer that only reads
`score_cell` cannot tell N=0 apart from a genuine 0-score category; only
`coverage == 0` distinguishes them (and `_present_categories`, used by every
FX pair's D1=D intersection, already keys off `coverage`, so an N=0 category
is correctly excluded from any pair built on it — verified, this is the same
mechanism already used for `stale`).

**The trade-off, stated numerically, not recommended on:** today AUD growth
is diluted (N=3, GDP's -2 pulled toward 0 by two 0-scored PMI slots,
precise=-0.667) but can never hit N=0 (two dead PMI slots are always there to
average against — structurally guaranteed, not by design). After either
variant, AUD growth is honest (N=1, precise=-2.000, the GDP print
undiluted) but now **one stale/missing GDP release away from N=0** — a
single quarterly print's max-age window (110 days) lapsing would zero out
AUD growth's contribution to AUD's index and to all 6 AUD-leg pairs
simultaneously. This is the volatility the spec's framing warned about,
quantified: it is a real, live exposure today under either variant (AUD GDP
was released 2026-06-03, well inside its 110-day window — not imminent, but
structurally possible the way it wasn't before).

## 6. Interaction with the CAD Core CPI promotion proposal

**Median CPI y/y consensus check (archive, `data/archive/ff_calendar_range.json`,
read-only, via the existing `parse_jblanked_range`): confirmed populated.**

| indicator | rows | actual valid | consensus valid |
|---|---|---|---|
| CAD Median CPI y/y | 43 | 43 | 43 (100%) |
| CAD Common CPI y/y | 43 | 43 | 43 (100%) |
| CAD Trimmed CPI y/y | 41 | 41 | 41 (100%) |
| CAD Core CPI y/y (current, wrong-unit) | 53 | 43 | 0 (0%) |

(Archive snapshot is dated ~2026-07-03/05, slightly behind the live parquet's
2026-07-30 cut — hence 53 vs 54 raw core_cpi prints; not a discrepancy, just
an older cut, noted for transparency.) This confirms prediction #5 and the
proposal doc's own finding: promoting to Median CPI y/y resolves the
no-consensus problem for CAD `core_cpi` **and** its wrong-unit problem in one
move, which would retire CAD `core_cpi` from the Variant A/B blacklist
entirely (5 series remain: NZD Mfg/Services, CAD Mfg PMI, AUD Mfg/Services
PMI).

**Combined scenario measured** (5-series exclusion + CAD `core_cpi` rows
replaced, for CAD only, by the archive's Median CPI y/y rows — the same
backfill-merge mechanism the promotion proposal describes, using the
archive's latest available Median print, 2026-06-22, as "current" since no
live backfill exists):

| CAD leg pair | baseline | Variant A (5-series, no promotion) | combined (5-series + promotion) |
|---|---|---|---|
| USDCAD | 0.4167 Neutral | 0.4167 Neutral (unchanged — cancellation, see §4) | 0.2083 Neutral |
| EURCAD | 0.6250 Neutral | 0.6250 Neutral | 0.4167 Neutral |
| GBPCAD | 0.4167 Neutral | 0.4167 Neutral | 0.2083 Neutral |
| CADJPY | -1.1111 Neutral | -1.1111 Neutral | -0.8333 Neutral |
| **AUDCAD** | -0.6250 Neutral | **-1.4583 Bearish** | **-1.6667 Bearish** |
| NZDCAD | 0.9722 Neutral | 0.9722 Neutral | 0.6944 Neutral |
| CADCHF | -0.5556 Neutral | -0.5556 Neutral | -0.2778 Neutral |

**Zero additional bias flips from the promotion beyond what Variant A alone
already causes** (AUDCAD, driven entirely by AUD's own growth-N collapse,
§4) — matches the proposal doc's own pre-registered finding that Median's
current surprise is 0 (it scores 0 under promotion too, only the *N-dilution*
problem for CAD inflation itself disappears since the slot's flag stops
being `no_consensus` — CAD inflation `score_precise` returns to -0.667,
identical to today, because Median's own score is 0, same as core_cpi's
forced no-consensus 0 was). The other CAD pairs move by a small, uniform
amount (CAD's index rises slightly, ~0.08–0.21 points, from the growth-N fix
alone, since inflation's contribution is unchanged) — none cross a bias
threshold.

## 7. Predictions vs measured — scorecard

| # | prediction | outcome |
|---|---|---|
| 1 | 6 series structural, 0%/~100% | **Confirmed**, raw-count-exact |
| 2 | A/B diverge iff a healthy series' *latest* print lacks consensus; GDP suspected | **Confirmed** — GBP GDP q/q + USD PPI y/y, both hit today; 8 instruments differ by up to 0.42 |
| 3 | AUD growth → N=1, biggest source of bias flips | **Confirmed** — AUD growth N=1 in both variants; all 6 flips are AUD-involving pairs |
| 4 | Cross-asset: 0 changes | **Confirmed** — 0 of 8, verified by direct computation |
| 5 | CAD promotion: Median has consensus, no new flips | **Confirmed** — 100% consensus in archive; combined scenario adds 0 flips beyond Variant A |

**One spec assumption is falsified, not merely refined**: *"Nu există în
date niciun caz tranzitoriu"* (§1b) — two exist right now (GBP GDP q/q, USD
PPI y/y), and they are the entire reason Variant A and Variant B are not
interchangeable today. This does not change the direction of any
recommendation-relevant number (no bias flip results from the A/B
difference itself), but it does mean **"A and B give nearly identical
results today" is true only for the 6-series blacklist's own scope — it is
not true of the two variants' full behavior**, which is exactly what
criterion 4 (recurring manual maintenance) turns on: Variant B caught these
two cases with zero configuration; Variant A structurally cannot until a
human notices and edits the blacklist.

## Reproduction

```
.venv/bin/python3 scripts/measure/inventory_no_consensus.py   # §1
.venv/bin/python3 scripts/measure/run_all.py                  # §2-§6
```

`scripts/measure/common.py` is the harness (imports production functions
unmodified). `scripts/measure/inventory_no_consensus.py` and
`scripts/measure/run_all.py` are report scripts. None of the three is
imported by `src/` or `scripts/econ_refresh.sh` — instrumentation only.

## Appendix A — harness fidelity check

```python
# scripts/measure/common.py's compute_currency_scorecard_variant, called with
# rule_baseline (exclude iff stale — i.e. production's actual current rule),
# reproduces build_payload(...) bit-for-bit:
#   0 mismatches across 8 currencies (index, coverage, every category's
#   score_precise + coverage) and 29 instruments (score + bias), as of
#   2026-07-30, both via to_scoring_frame(economic_calendar_ff.parquet).
```

## Appendix B — slot-level explanation (criterion 1)

Rows newly excluded (i.e. `not stale` in baseline, but excluded by the
variant's rule) — every category-level number in §4 traces to exactly this
list, nothing else:

**Variant A excludes** (6 rows, fixed list):
AUD manufacturing_pmi, AUD services_pmi, CAD core_cpi, CAD manufacturing_pmi,
NZD manufacturing_pmi, NZD services_pmi.

**Variant B excludes** (8 rows, same 6 plus 2 discovered by the rule itself):
the same 6, **plus GBP gdp_qoq** (actual 0.1, no consensus) **and USD
ppi_yoy** (actual -0.3, no consensus).
