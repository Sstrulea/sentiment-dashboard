# Proposal — promote a CAD core-inflation measure into the scored `core_cpi` slot

Status: proposal, measured on real data (live parquet + `data/archive/`
re-parse, `as_of=2026-07-30`). **Not implemented — no scoring change made.**
Adoption is George's decision.

## The problem is worse than "wrong unit"

Today's CAD `core_cpi` is fed by alias `"Core CPI m/m": "Core CPI y/y"` (an
`xf` — FF publishes a monthly % change under a canonical name that implies
y/y). That mislabeling was already known. What this pass found, checking
`consensus` across the full history rather than assuming it was populated:

**`forecast` is `0.0` on all 54/54 rows, for the entire 2023-01 → 2026-07
history, no exceptions.** JBlanked never carries a real forecast for this raw
event. `to_scoring_frame`'s zero-placeholder quarantine (correctly) nulls
`consensus==0.0` to NaN for `core_cpi` (not in `can_be_zero`), so
`compute_indicator_score` has had **zero (actual, consensus) pairs, ever** —
the indicator has sat on `flag: no_consensus`, `score: 0` every single day
since this pipeline existed. It has never once produced a z-score or a
pct-fallback score. This isn't a mislabeled series that's a little noisy —
it's been **inert**, contributing a permanent, silent 0 to CAD's inflation
category, indistinguishable from "no surprise" when it's actually "no data
ever."

The three native BoC measures found in the archive (`Common CPI y/y`,
`Median CPI y/y`, `Trimmed CPI y/y`) all carry real, varying forecasts.
Promoting any of them would not just fix the unit — it would activate a slot
that has never been live.

## Which of the three

The Bank of Canada has tracked all three as its official preferred core-
inflation measures since 2016; **CPI-median and CPI-trim are the two most
frequently cited in BoC communications and Monetary Policy Reports** —
CPI-common is tracked but referenced less often in forward guidance. Data
availability doesn't discriminate between them (all three have comparable
depth: n=43/43/41, full 2023-2026 archive coverage, `consensus` populated on
every archive row for all three — see §3). The measured behavior below
supports the same ranking data availability doesn't settle:

| candidate | n_pairs (trailing 12) | mean surprise | sigma | today's cell score if promoted |
|---|---|---|---|---|
| Common CPI y/y | 12 | −0.025 | 0.106 | **+2** (Very Bullish surprise) |
| Median CPI y/y | 12 | −0.050 | 0.124 | 0 (surprise = 0 exactly) |
| Trimmed CPI y/y | 12 | −0.017 | 0.134 | 0 (surprise = 0 exactly) |

Common's today-score of +2 is a single data point, not a volatility
verdict, but it's the more reactive of the three in this window (lowest
sigma, i.e. tighter historical surprise distribution — a small surprise
buckets more easily to an extreme score). Combined with it being the
least-cited of the three in BoC's own communications, **Median CPI y/y is
the recommended candidate**: cited alongside Trimmed as BoC's headline core
gauge, and its trailing sigma (0.124) sits between the other two — not the
most reactive, not the least.

## Category/pair impact (measured, real archive data, `as_of=2026-07-30`)

CAD `inflation` category, coverage unaffected either way (still 3 — `core_cpi`
already counted before, whether inert or not):

| scenario | core_cpi score | inflation score_precise |
|---|---|---|
| Today (Core CPI m/m, permanently no_consensus) | 0 | −0.667 |
| If Common CPI y/y | **+2** | **0.0** |
| If Median CPI y/y | 0 | −0.667 (unchanged) |
| If Trimmed CPI y/y | 0 | −0.667 (unchanged) |

All 7 CAD-leg pairs, today vs. each candidate:

| pair | today | if Common | if Median | if Trimmed |
|---|---|---|---|---|
| USDCAD | −0.278 Neutral | −0.833 Neutral | −0.278 Neutral (unchanged) | −0.278 Neutral (unchanged) |
| EURCAD | 0.0 Neutral | −0.556 Neutral | 0.0 Neutral (unchanged) | 0.0 Neutral (unchanged) |
| GBPCAD | −0.278 Neutral | −0.833 Neutral | −0.278 Neutral (unchanged) | −0.278 Neutral (unchanged) |
| **AUDCAD** | −0.833 Neutral | **−1.389 Bearish** | −0.833 Neutral (unchanged) | −0.833 Neutral (unchanged) |
| NZDCAD | 0.972 Neutral | 0.417 Neutral | 0.972 Neutral (unchanged) | 0.972 Neutral (unchanged) |
| CADJPY | −1.111 Neutral | −0.556 Neutral | −1.111 Neutral (unchanged) | −1.111 Neutral (unchanged) |
| CADCHF | −0.556 Neutral | 0.0 Neutral | −0.556 Neutral (unchanged) | −0.556 Neutral (unchanged) |

**Only Common CPI y/y flips a bias today (AUDCAD: Neutral → Bearish)** — a
direct consequence of its today-score being +2 instead of the current
permanent 0. Median and Trimmed, both scoring 0 today (by coincidence of the
current print, not by design), leave every pair bit-identical to production —
which is itself informative: swapping to either would be uneventful today,
but would NOT be uneventful on every future release the way the current
inert slot is guaranteed to be.

## Cost: backfill is required, not optional

Promoting without backfill means `core_cpi` starts at `n=0` under the new
alias — the trailing-K sigma window has nothing to compute from until 12
fresh prints accumulate (12 months, given monthly cadence), during which the
indicator would sit on `fallback` (pct-based) or `no_consensus` depending on
how many pairs exist, degrading rather than improving current behavior in
the short run. All three candidates already have their full history
sitting in `data/archive/` (verified above, n=41-43, `d9e656d`), so a
backfill merge (writing those rows into `data/economic_calendar_ff.parquet`
under `core_cpi`'s `indicator_key`, same mechanism as any other historical
merge) is what makes promotion viable same-day instead of a 12-month wait.
**This backfill is part of what adoption would require — not run here.**

## Pre-registered acceptance criteria (V4/V5a style)

Recorded BEFORE any promotion is implemented, so the adoption decision isn't
made by looking at results first:

1. **Primary criterion — activation, not direction**: after promotion +
   backfill, `core_cpi` for CAD must show a non-`no_consensus` flag (z or
   fallback) on at least 90% of the trailing 12 prints. (Today: 0%, by
   definition of the inert-slot finding above.) This is the criterion that
   actually matters — the unit-correctness and bias-flip numbers above are
   secondary color, not the reason to promote.
2. **No other CAD indicator's coverage or score changes** as a side effect of
   the backfill merge (isolation check — same shape as the display-only
   verification already run for this branch's Faza A/B, re-run after any
   backfill).
3. **Bias-flip count is bounded**: the promotion should not flip more than 2
   of CAD's 7 pair biases on the backfill day itself (guards against the
   backfill's very first live print being an outlier that happens to coincide
   with go-live — if more than 2 flip, hold and re-check the backfilled
   history for a data quality issue before accepting).
4. **Sigma stability**: the trailing-12 sigma computed from the backfilled
   history should be within 2x of the sigma measured in this proposal (0.106 /
   0.124 / 0.134 depending on candidate) — a large deviation would mean the
   backfill merge altered dedup/period handling in a way not anticipated here.

## Risks

- **The three don't always release together** — checked precisely (calendar
  day, not exact timestamp, since same-day intra-hour duplication is a known
  separate artifact): all 3 land on the same day on 40 of 42 calendar days in
  the archive; `Trimmed CPI y/y` is absent on 2 days (2025-06-24, 2025-12-15)
  where `Common`/`Median` are present. Not investigated further here (2/42 —
  a genuine occasional gap, not a systemic one) — worth a one-line note if
  Trimmed is the eventual pick, since its recency window would need to
  tolerate an occasional skipped release the other two don't have.
- **`forecast` quality for the candidates is JBlanked-sourced, not BoC-
  sourced** — the three candidates' consensus values are still a
  third-party's survey estimate, same caveat as every other FF/JBlanked
  indicator in this pipeline, not a new risk introduced by this promotion.
- **Backfill mechanics are untested for a slot-swap** (replacing an existing
  indicator_key's history, not adding a new one) — the display-only wiring
  in this branch only ever ADDED new indicator_keys; swapping what feeds an
  EXISTING scored key's baseline is a different, higher-blast-radius
  operation and deserves its own dry run before being applied to production
  `data/economic_calendar_ff.parquet`.

## Not done here

No alias/matcher change points `core_cpi` at any candidate. No backfill was
written to any parquet. All numbers above come from a read-only combination
of the live parquet and a `parse_jblanked_range` re-read of
`data/archive/ff_calendar_range.json` (archive untouched, per instruction).
