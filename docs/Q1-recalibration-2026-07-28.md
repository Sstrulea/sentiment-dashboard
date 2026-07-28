# §Q1 — `mild`/`very` recalibration on the post-D distribution (2026-07-28)

Status: measured and derived, PR open for review, **not merged**. Scope:
`bias_thresholds.mild` / `bias_thresholds.very` in `data/economic_instruments.yaml`
(FX board) and `data/crossasset_instruments.yaml` (cross-asset board). Nothing
else touched — no `z_buckets`, no indicator thresholds, no REGIME/MOMENTUM, no
COT, no weights, no per-instrument/per-class overrides.

All measurements below were run with `scripts/diag/engine.py` (read-only,
nothing ingested, nothing written to `data/`) replaying the post-D1=D+D2-c
code (`feature/m-d1-d-d2c`) across a 53-week window, 2025-07-25 → 2026-07-21,
via two temporary git worktrees (`main` = current production, `postd` = the
merged-but-unmerged post-D state). Comparison CSVs from this run live under
`/tmp/q1-diag/out/` (outside the repo, not committed).

## Why now, not sooner (unchanged from the original reasoning)

`mild`/`very` were last calibrated on the MT5-consensus-contaminated baseline,
before the zero-placeholder quarantine. `z_buckets` were re-derived on clean
data (1.71→1.54); the FX/cross-asset bias thresholds were not — same cause,
one step up the chain. Calibrating on a distribution that's about to change
is the exact MT5 lesson ("calibration results from corrupted baselines are
meaningless"). Steps 1–4 (dead-code removal, contribution breakdown, D1=D,
D2-c) changed the score distribution; this is the first stable distribution
since the pre-quarantine calibration.

## GUARDRAIL (read before ever re-deriving these again)

**This re-derivation is justified by the STRUCTURAL JUMP at the
zero-placeholder-quarantine commit, not by the gap between the actual and
target split.** The gap (measured below) is real, but a gap alone is not
sufficient justification — it's a symptom that can also show up from ordinary
drift. What makes this pass legitimate is that the underlying distribution
changed **discontinuously**: `%Very` on `economic.json`'s git history fell
from a 10–46% range to exactly 0.0%, uninterrupted for 16 days, at the exact
commit that quarantined the MT5 consensus zero-placeholders (report 2, §Q1) —
a step function, not a gradual decline.

**Any future re-derivation of `mild`/`very` requires the same kind of
evidence: a demonstrated structural jump in the score distribution tied to an
identifiable code/data change** (a new scoring decision, a data-quality fix,
a config change that alters what feeds the score). A gradual decline in
%Very or %Neutral over time does **not** qualify on its own — that could
just as easily be regime (market conditions), not a broken calibration. If
someone proposes re-deriving these without pointing at a jump, the answer is
to ask why the distribution changed, not to re-run the percentile calculation.

---

## Q1-IMPL-1 — measured distribution (already reported and approved before proceeding)

53-week window, 2025-07-25 → 2026-07-21.

**FX board `|score_precise|`** (n=1537 = 29 instruments × 53 weeks):

| p50 | p55 | p75 | p90 | p95 | max |
|---|---|---|---|---|---|
| 0.99 | 1.12 | 1.72 | 2.41 | 2.78 | 4.53 |

**Cross-asset `|score|`** (n=424 = 8 instruments × 53 weeks):

| p50 | p55 | p75 | p90 | p95 | max |
|---|---|---|---|---|---|
| 1.48 | 1.50 | 2.41 | 3.33 | 3.67 | 5.74 |

**Split under CURRENT thresholds** (production, pre-recalibration):

| board | mild | very | Neutral | Directional | Very |
|---|---|---|---|---|---|
| FX | 1.3 | 3.0 | 61.42% | 35.07% | 3.51% |
| Cross-asset | 1.9 | 4.3 | 65.09% | 32.78% | 2.12% |

Both thresholds sit above their board's empirical p90 (FX: 3.0 vs 2.41; CA:
4.3 vs 3.33) — the primary evidence that a recalibration is warranted, on top
of the structural-jump evidence above.

## Q1-IMPL-2 — re-derivation

Same method as the original D2 calibration: `mild` = p55, `very` = p90 of
`|score|` on the post-D distribution, same 55/10 targets, FX and cross-asset
derived independently.

| board | mild: old → new | very: old → new |
|---|---|---|
| FX | 1.3 → **1.12** (Δ −0.18) | 3.0 → **2.41** (Δ −0.59) |
| Cross-asset | 1.9 → **1.50** (Δ −0.40) | 4.3 → **3.33** (Δ −0.97) |

Both moves are downward on both boards — consistent with "restoring a
specification that never accounted for the post-quarantine distribution",
not a deliberate loosening for its own sake.

## Q1-IMPL-3 — verification before applying

### 1. Split under NEW thresholds, whole window

| board | Neutral | Directional | Very |
|---|---|---|---|
| FX | **55.04%** | 34.87% | **10.08%** |
| Cross-asset | **51.42%** | 38.21% | **10.38%** |

FX lands almost exactly on target (55.04/10.08 vs 55/10). Cross-asset's
`Very` also lands on target (10.38%), but `Neutral` is 3.6pp under target
(51.42% vs 55%) — see the stability note in (a) below for the likely cause
(a real cluster at p50≈p55 redistributes some mass into `Directional` at the
boundary).

### 2. Full flip table vs `main` (cumulative: D1=D + new thresholds together — what actually changes if both PRs ship)

| board | rows | flips | % |
|---|---|---|---|
| FX | 1537 | 261 | 17.0% |
| Cross-asset | 424 | 93 | 21.9% |

FX transition breakdown (all are intensity changes, in the expected
direction of the threshold move):

| from → to | count |
|---|---|
| Bullish → Very Bullish | 74 |
| Neutral → Bullish | 74 |
| Neutral → Bearish | 36 |
| Bullish → Neutral | 26 |
| Bearish → Very Bearish | 25 |
| Bearish → Neutral | 21 |
| Very Bullish → Bullish | 3 |
| Very Bearish → Bearish | 2 |

Cross-asset: Neutral→Bullish 45, Bullish→Very Bullish 29, Neutral→Bearish 13,
Bearish→Very Bearish 6.

**Direction reversals (Bull↔Bear, not just intensity): 0 on both boards.**
Nothing crosses from bullish-flavored to bearish-flavored (or vice versa) as
a result of this change — every flip is a re-partition of the same sign into
a different band.

Isolated threshold-only effect (post-D scores held fixed, old vs new
thresholds only): FX 199/1537 (12.9%), Cross-asset 93/424 (21.9% — identical
to the vs-`main` count, confirming D1=D has **zero** effect on cross-asset
scores, as expected: cross-asset reads per-currency `categories` cards, which
D1=D never touches).

### 3. Current board (latest window point, 2026-07-21) under new thresholds

**2/29 FX pairs change** vs current production:

| symbol | score (main) | bias (main, today) | score (post-D) | bias (new thresholds) |
|---|---|---|---|---|
| AUDJPY | −1.264 | Neutral | −1.466 | Bearish |
| GBPCAD | +2.542 | Bullish | +2.578 | Very Bullish |

### 4. Weeks (of 53) with ≥1 "Very" on the FX board

| state | weeks with ≥1 Very |
|---|---|
| `main` (current production) | 28/53 (52.8%) |
| post-D, OLD thresholds (isolates D1=D alone) | 28/53 (52.8%) — unchanged, confirms D1=D alone doesn't move this |
| post-D, NEW thresholds (proposed) | **48/53 (90.6%)** |

This jump (53%→91% of weeks) is a much bigger visible swing than the
instrument-level %Very (3.5%→10.1%) suggests — with ~29 pairs/week, even a
~10% per-instrument Very-rate makes "zero Very this week" rare
(≈0.9^29 ≈ 5%, matching the observed 1 − 48/53 ≈ 9.4%). Flagged here as an
expected, arithmetic consequence of the redistribution, not a red flag on its
own — the underlying per-instrument rate (10.08%) is exactly on target.

### (a) Cross-asset stability — ±0.05 perturbation flip rate

Requested because `p50=1.48` and `p55=1.50` are 0.02 apart with ~5% of the
mass between them — `mild=1.50` lands on a real cluster, not open space.

| board | unstable under OLD thresholds | unstable under NEW thresholds |
|---|---|---|
| FX | 59/1537 (3.84%) | 72/1537 (4.68%) |
| Cross-asset | 7/424 (1.65%) | **40/424 (9.43%)** |

"Unstable" = bias label changes under a ±0.05 nudge to the score. FX's
increase is modest (3.84%→4.68%). **Cross-asset's is not**: instability more
than quintuples (1.65%→9.43%, +7.8pp absolute) — a direct, measured
consequence of `mild=1.50` sitting on the p50/p55 cluster. Reported per
instruction, **not adjusted** — the new cross-asset `mild` is still exactly
p55 as derived; nudging it to dodge the cluster would be adjusting the
result, not applying the method. Recorded as a known property of this
threshold in the YAML comment; worth a note if a future pass considers a
different tie-breaking rule for near-degenerate clusters, but that is a
method-level question for later, not something resolved by adjusting this
value now.

### (b) FX subgroup split by `categories_used` (diagnostic only, no per-class thresholds introduced)

| categories_used | n | Neutral | Directional | Very |
|---|---|---|---|---|
| 3 | 704 | 56.25% | 34.23% | 9.52% |
| 4 | 780 | 54.23% | 35.51% | 10.26% |

Only 3 and 4 appear across the entire 53-week window (no pair ever drops to
≤2 shared categories in this data). Both subgroups land close to 55/10 — no
subgroup is far off. **No action taken and none proposed** — this is a
diagnostic confirmation that the single pooled threshold isn't hiding a
badly-served subgroup, not a trigger for per-class thresholds (which remain
explicitly out of scope, interdiction 3).

## Verdicte mecanice

- **Pragurile actuale stau peste p90 empiric post-D**: **CONFIRMED**. FX
  3.0 vs 2.41 (+24%); cross-asset 4.3 vs 3.33 (+29%).
- **Re-derivarea restaurează split-ul la ≈55/10**: **CONFIRMED** for FX
  (55.04/10.08). **Largely confirmed** for cross-asset (51.42/10.38) — `Very`
  lands on target, `Neutral` 3.6pp under, attributable to the measured
  p50/p55 clustering in (a), not a derivation error.
- **Nicio clasă de instrumente nu ajunge la 0% direcțional**: **CONFIRMED**.
  Every subgroup checked (FX categories_used=3/4, both boards overall) keeps
  a substantial directional share (≥34% on every FX subgroup, ≥32% on
  cross-asset).

## Not independently re-verified in this pass

- The original zero-placeholder-quarantine jump itself (%Very 10–46%→0.0%,
  16 days, at the quarantine commit) is cited from report 2 / diag branch
  findings, not re-derived from `economic.json`'s git history in this pass —
  it was already established and signed off in an earlier phase of this
  engagement; re-litigating it wasn't requested here.
- This measurement replays `feature/m-d1-d-d2c` (post-D1=D+D2-c), which is
  itself an open, unmerged PR (#4). If that PR's implementation changes
  before merge, these percentiles would need re-measuring — they are not
  independent of that PR's exact content.

## References

- `docs/prereg-M-currency-comparability-2026-07-28.md` §8 (original Q1
  deferral reasoning), on `diag/scoring-audit`.
- PR #4 (`feature/m-d1-d-d2c`) — the post-D scoring this measurement replays.
- PR #5 (`docs/close-section-o`) — unrelated, reviewed separately.
