# Pre-registration: Trend pillar — lookback horizon re-specification

**Date:** 2026-08-16
**Status:** PRE-REGISTERED. Diagnostic not yet run. No code touched.
**Supersedes:** the colinearity diagnosis (retracted 2026-08-16, see §1).

---

## 1. Retraction of the prior diagnosis

The previous claim was that the regime layer (SMA50/SMA200 `bull_points`) and the momentum layer (SMA50 slope / ATR) are **colinear by construction** and fail simultaneously.

**This was falsified by its own pre-registered thresholds.**

| Metric | Pre-registered threshold | Result |
|---|---|---|
| median LAG_FRACTION, all days | > 0.50 confirms | **0.3468** |
| median LAG_FRACTION, post-trough | > 0.50 confirms | **0.4627** |
| corr(regime, momentum), median | — | **0.4134** |
| corr(regime, momentum), min | — | **-0.0859** (USDJPY) |

Two instruments show *negative* correlation. Series that are mathematically dependent cannot do that. The observed correlation is ordinary co-movement between two trend indicators computed on the same price series.

**Root cause of the error:** the diagnosis was formed by inspecting GOLD, whose corr = 0.8317 — the single highest of 36 instruments. Generalising from the most extreme member of the sample.

The retraction is unconditional. No part of the colinearity claim is carried forward.

---

## 2. New claim under test — horizon mismatch, not defect

The trader's holding period is **5-15 business days, hard maximum 3 weeks.**

The trend pillar's slow leg is SMA200 — a 200-day lookback informing a 15-day decision. Ratio ~13:1. SMA200 is close to constant over the entire life of a position.

**Claim:** the pillar is not broken. It is correctly measuring a horizon the trader does not trade. This is a specification mismatch, resolvable by re-parameterisation rather than redesign.

**Supporting observation (GOLD, 2026-08-14):** SMA50 = 4146.22, SMA200 = 4503.33. The pillar reads -2 while the trailing 20-bd return is +8.99%. Both are true statements about different horizons.

---

## 3. Selection rule — fixed before any numbers

The lookback is selected by **one criterion only**:

> **PERSISTENCE.** The shortest candidate whose median signal persistence is **>= 15 business days**, where persistence is the median length of contiguous runs of constant `sign(trend_cell)`, computed per instrument and then taken as the median across all 36 instruments.

**Rationale, independent of any output:** a signal that flips sign more often than the position is held cannot serve as confluence for that position. A 15-bd floor is derived from the stated holding period, not from any observed return, score, or instrument.

### Candidate grid — fixed, no post-hoc additions

| # | fast MA | slow MA |
|---|---|---|
| C1 | 10 | 30 |
| C2 | 10 | 50 |
| C3 | 20 | 50 |
| C4 | 20 | 100 |
| C5 | 50 | 200 (current — control) |

The momentum layer's slope window scales with the fast MA in each candidate. ATR normalisation window is unchanged.

### Guard rail

Cross-sectional dispersion of `trend_cell` across the 36 instruments (median over the last 250 bd) **must remain >= 1.5**. Current value: **2.107** — the highest of any pillar on the board. Trend does most of the board's discrimination between instruments; a re-parameterisation that flattens it destroys more than it fixes.

If the shortest persistence-satisfying candidate breaches the dispersion guard rail, move to the next-shortest candidate that satisfies both.

### Explicitly not a selection criterion

`LAG_FRACTION` is reported for information only and **may not be used to choose between candidates.** It was the metric of a diagnosis that has been retracted; reusing it to pick a winner would be selecting on a measure already shown not to mean what was claimed.

Likewise, **no candidate may be chosen by inspecting its effect on GOLD or on any other named instrument.**

---

## 4. Secondary hypothesis — momentum saturation (NOT yet validated)

Observed in the GOLD 90-bar table: the momentum layer read **-1 continuously from 2026-04-27 to 2026-08-14** — roughly 80 bars — unchanged through both a -13% decline and a +9% recovery. All movement in `trend_cell` came from the regime layer.

If momentum is pinned at an extreme for long stretches, it contributes no information and the pillar is effectively regime-only. No lookback change fixes that.

**This hypothesis was generated from the data in D1-D5. Those same data cannot validate it.** It is tested here on its own pre-registered terms:

> **SATURATION CONFIRMED** if median across instruments of "% of days momentum sits at an extreme value" **> 60%** AND median run-length at an extreme **> 20 bd**.
> **SATURATION REJECTED** if either falls below those levels.

If confirmed, the momentum layer needs its own specification and that is a **separate** document. It is not addressed in this one.

---

## 5. Adverse pre-commitments

1. **If the persistence rule selects C5 (50/200, the current setting), v2 stands unchanged.** The conclusion is then that the pillar is correct and the expectation of it was wrong. No re-parameterisation, no consolation change.
2. **Faster lookbacks whipsaw more.** Instruments currently showing long clean readings — the JPY crosses at +2, NZDCAD at +3 — will flip sign more often. This is accepted in advance as the cost of matching the horizon.
3. **GOLD may stay at -2.** The selection rule does not reference it. If the persistence-selected candidate still prints -2 on GOLD, that result is accepted and the pillar ships that way.
4. **|Cells| will move board-wide**, including on instruments currently agreeing with the trader's directional view.
5. **If no candidate satisfies persistence >= 15 bd while holding dispersion >= 1.5, v2 stands** and the honest answer is that no re-parameterisation solves this — which would point to a new sensor and a new specification, not to relaxing these thresholds.

Thresholds in §3 and §4 are not to be adjusted after results are seen. Observing the numbers and then moving a threshold invalidates the exercise.

---

## 6. Diagnostic prompt (READ-ONLY)

READ-ONLY DIAGNOSTIC. No code changes, no PR, no modifications to src/.
Throwaway script under scripts/diag/. Numbers only — no interpretation, no
recommendation, no proposed v3. Stop after printing.

CONTEXT
The v2 trend pillar computes trend_cell = clamp(regime + momentum) where:
  regime   = SMA_fast/SMA_slow bull_points mapping   (currently 50/200)
  momentum = SMA_fast slope normalised by ATR
Re-implement the existing scoring logic parametrically over (fast, slow) so
each candidate below is scored by the SAME code path as production. Do not
change production defaults. The momentum slope window scales with the fast MA;
the ATR window is unchanged.

CANDIDATE GRID (fixed — do not add, drop, or substitute candidates)
  C1 = (10, 30)
  C2 = (10, 50)
  C3 = (20, 50)
  C4 = (20, 100)
  C5 = (50, 200)   current production, control

All 36 instruments, full available history.

T1 — PERSISTENCE (the selection criterion)
For each candidate, per instrument: the median length in business days of
contiguous runs of constant sign(trend_cell). Treat 0 as its own state and
report zero-runs separately from +runs and -runs.
Report per-instrument medians and the median across the 36 instruments.
Also report p25 and p75 across instruments.

T2 — DISPERSION GUARD RAIL
For each candidate: cross-sectional stdev of trend_cell across the 36
instruments, computed per day, median over the last 250 bd. Use the same
methodology as the earlier D5 trend figure (2.1070) so the numbers are
directly comparable. Report min / p25 / median / p75 / max of the daily series.

T3 — MOMENTUM SATURATION
For each candidate, per instrument:
  a) % of days the momentum layer sits at its extreme value (max or min)
  b) median length in bd of contiguous runs at an extreme
  c) correlation between the momentum layer value and the trailing 20-bd
     price return
Report per-instrument and the median across instruments for each candidate.

T4 — LAG_FRACTION (INFORMATIONAL ONLY)
For each candidate, the median LAG_FRACTION across instruments, all days.
Label this table clearly as informational — it is explicitly excluded from
candidate selection.

T5 — FLIP COUNT
For each candidate, per instrument: the number of sign changes in trend_cell
per 250 bd. Report the median across instruments.

OUTPUT
Markdown tables, one per test. Numbers only. Do not identify a winning
candidate. Do not comment on GOLD or any other individual instrument.

---

## 7. What happens after

Results return → evaluated against §3 and §4 by the reviewer, **not by the agent that produced them** → **STOP GATE** → explicit sign-off → only then a v3 specification document, committed before any code.

If a candidate is selected: feature branch + PR, test written and demonstrated failing on `main` first, full 36-instrument before/after score diff in the PR body, reviewed against the adverse pre-commitments in §5.
