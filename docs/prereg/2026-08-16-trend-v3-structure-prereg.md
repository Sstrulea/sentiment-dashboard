# Pre-registration: Trend pillar v3 — market structure sensor

**Date:** 2026-08-16
**Status:** PRE-REGISTERED. No diagnostic run. No code touched.
**Follows from:** 2026-08-16-trend-pillar-horizon-prereg.md §5.5 — no lookback
re-parameterisation satisfied the persistence rule, which pointed to a new
sensor and a new specification.

## 1. Why moving averages cannot solve this

The trader's method is structural: a trend changes when the sequence of lower
highs and lower lows breaks. A pullback that does not take out the last higher
low is not a trend change.

A moving average has no concept of a swing point. It cannot distinguish "price
fell but held the higher low" from "price fell and broke the higher low."
Under SMA50/SMA200 both read as deterioration. This is the reported failure and
it is architectural, not a calibration error.

Three prior hypotheses about this pillar were tested and all three failed
(colinearity, horizon mismatch, momentum saturation). This specification is
different in kind: it is not a hypothesis about why v2 is wrong, it is a
transcription of a stated method that v2 does not implement.

## 2. Ground truth supplied by the trader (labelled example, NOT a fitting target)

GOLD, as of 2026-08-14:
- lower-high / lower-low structure broke on BOTH weekly and daily
- the break occurred in the week of 2026-08-03
- expected cell: at least +2
- actual v2 cell: -2

Weekly takes precedence: if weekly is clearly bearish, no long is taken
regardless of daily.

This is ONE labelled example. It may be used to check that a rule is not
absurd. It MUST NOT be used to select between candidate parameters. Selecting
a swing-detection parameter because it makes GOLD read +2 is instrument
tuning and is prohibited.

## 3. Architecture

Layer W — weekly structure gate.
  Detect swing highs/lows on weekly bars. State is BULLISH (higher high and
  higher low intact), BEARISH (lower high and lower low intact), or NEUTRAL
  (transitioning / no confirmed structure).

Layer D — daily structure.
  Same detection on daily bars. Supplies magnitude.

Cell mapping (fixed here, before any numbers):
  W bullish + D bullish, break of structure within last 10 bd  -> +3
  W bullish + D bullish                                        -> +2
  W bullish + D neutral or in pullback                         -> +1
  W neutral                                                    -> sign of D, magnitude capped at 1
  W bearish + D neutral or in pullback                         -> -1
  W bearish + D bearish                                        -> -2
  W bearish + D bearish, break of structure within last 10 bd  -> -3
  W and D in direct conflict                                   -> capped toward 0

Pullback DEPTH is deliberately out of scope. The pillar reports direction.
Entry timing is the trader's job.

## 4. Parameter selection rule — fixed before any numbers

The only free parameter is swing detection. Candidates, fixed, no additions:

  S1  fractal N=2  (5-bar)
  S2  fractal N=3  (7-bar)
  S3  fractal N=5  (11-bar)
  S4  ZigZag 3% threshold
  S5  ZigZag 5% threshold

Weekly layer uses N=1 or N=2 fractal given the smaller bar count; the weekly
parameter is tied to the daily choice and is not independently tuned.

SELECTION RULE:
  Choose the MOST SENSITIVE candidate whose FALSE BREAK RATE stays below 20%,
  where a false break is a confirmed break of structure that is reversed by an
  opposite confirmed break within 10 business days, measured across all 36
  instruments over full history.

Rationale independent of output: the trader wants to catch the turn early,
bounded by a noise budget. A signal where more than one in five structure
breaks immediately reverses is not actionable for entries. The 20% figure is
declared here, before any measurement, and is not to be adjusted afterwards.

Guard rail: cross-sectional dispersion of the cell across 36 instruments
(median over last 250 bd) must remain >= 1.5. Current v2 value 2.107.

## 5. Adverse pre-commitments

1. If the selected parameter does NOT produce >= +2 on GOLD at 2026-08-14, it
   ships anyway and the trader is told plainly. The selection rule does not
   reference GOLD.
2. If no candidate holds false break rate below 20%, v2 stands and the honest
   answer is that structure detection is too noisy at these thresholds — not
   that the threshold should be raised.
3. Cells move board-wide. Instruments currently reading +2 or +3 on clean MA
   trends may drop, because an intact MA trend with no recent structure break
   is a +2 at most under this mapping, not a +3.
4. This sensor will be LATE relative to price on V-shaped reversals with no
   intermediate swing, by construction. That cost is accepted.
5. Three prior diagnoses on this pillar were retracted. If this one fails its
   own gate, it is retracted too, and the conclusion is that the reviewer has
   been wrong four times and should stop proposing changes to this pillar.

## 6. What happens after

Diagnostic runs -> results evaluated against §4 by the trader, NOT by the agent
that produced them -> STOP GATE -> explicit sign-off -> only then a v3
implementation spec, committed before any code.
