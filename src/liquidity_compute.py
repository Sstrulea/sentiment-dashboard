"""Pure liquidity-pillar momentum scoring (band-based, NOT z) — no I/O.

Scores bank reserves (WRBWFRBL) by default — the balance-sheet identity
WALCL − TGA − RRP == Reserves + Currency in circulation + Other liabilities
means the old net_liquidity input carried ~$2.9T of non-reserve ballast that
diluted the signal and imparted a currency-growth drift bias (see
docs/prereg/2026-08-15-liquidity-pillar-wresbal-prereg.md). net_liquidity is
still computed and stored (data/net_liquidity.parquet) as the rollback path,
and is used as an automatic fallback when reserves is unresolved. The score is
a FIXED percentage-band rate-of-change over W trading days — deliberately NOT
a z-score: z blows up in flat regimes (tiny std), which would violate
"stagnant ⇒ 0".

Raw convention (uniform with the other two rate sub-components, + = TIGHTENING):
  reserves FALLING = tightening = +score ; RISING = easing = −score.
The cross-asset YAML applies sign −1, so reserves RISING ⇒ bullish for
indices & metals (and falling ⇒ bearish), same as the 2y / real-yield rule.

    compute_liquidity_score(df, as_of=None, W=21, smooth=5,
                            band_hi=BAND_HI, band_lo=BAND_LO, max_age_bd=10,
                            value_col="reserves")
      -> LiquidityScore{score(−2..+2), roc, latest, as_of, method, stale, series}

`df` schema: date, reserves and/or net_liquidity (+ optional cols). `value_col`
selects which column to score; if it is absent or entirely NaN, falls back to
"net_liquidity" rather than emitting a hard 0. `series` on the returned score
records which column actually got scored ("reserves" | "net_liquidity").
Pure/deterministic given `as_of`; safe to unit-test with synthetic frames.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from .momentum_common import bday_lag as _bday_lag, bucket as _bucket
from .momentum_common import clean_series, sign as _sign, to_date as _to_date

# Defaults
W_DEFAULT = 21          # trading-day ROC horizon (~1 month), in ROWS
SMOOTH_DEFAULT = 5      # trailing-mean window to damp single-day TGA spikes
MAX_AGE_BD = 10         # weekly cadence (WALCL) → looser staleness than the daily series

# Empirically calibrated on bank reserves (WRBWFRBL, the S1-chosen series —
# stdev(roc_WRBWFRBL)=0.063356 vs stdev(roc_WRESBAL)=0.062990, override
# threshold 1.25x not met) over 2013-09-23 (RRP-facility start) -> 2026-08-12
# (n=3363 business days; 5-day smooth, 21-bd ROC; BAND_HI=p68, BAND_LO=p40 of
# |roc|, fixed in advance per the pre-reg — not hand-picked). Derived
# 2026-08-15 via scripts/diag/reserves_gate.py, PHASE 1 of
# docs/prereg/2026-08-15-liquidity-pillar-wresbal-prereg.md. Realized bucket
# mix {-2:16.15, -1:13.95, 0:39.99, +1:14.06, +2:15.85} vs the retired
# net-liquidity mix {-2:11.5, -1:11.2, 0:40.0, +1:16.8, +2:20.5} — both land
# near 0≈40%, |±2|≈32-35%. Bands are FRACTIONAL monthly ROC (0.0428 = 4.28%/mo).
BAND_HI = 0.0428        # |roc| ≥ 4.28%/mo → ±2
BAND_LO = 0.0221        # |roc| ≥ 2.21%/mo → ±1


@dataclass
class LiquidityScore:
    score: int                  # -2..+2 (raw: level FALLING = + tightening)
    roc: Optional[float]        # W-day rate-of-change, fractional (e.g. -0.03 = -3%)
    latest: Optional[float]     # latest (smoothed) level of whichever series was scored
    as_of: Optional[date]
    method: str                 # "band" | "insufficient"
    stale: bool
    series: str = "net_liquidity"   # which column was actually scored: "reserves" | "net_liquidity"


def compute_liquidity_score(
    df: pd.DataFrame,
    as_of: Optional[date] = None,
    W: int = W_DEFAULT,
    smooth: int = SMOOTH_DEFAULT,
    band_hi: float = BAND_HI,
    band_lo: float = BAND_LO,
    max_age_bd: int = MAX_AGE_BD,
    value_col: str = "reserves",
) -> Optional[LiquidityScore]:
    """Band-based liquidity momentum score. Returns None if the frame has no
    usable rows. Lookahead guard: rows dated after `as_of` are dropped here.

    `value_col` is preferred when present with at least one non-NaN value;
    otherwise falls back to "net_liquidity" (e.g. WRBWFRBL unresolved upstream)
    rather than emitting a false 0."""
    if df is None or df.empty:
        return None

    col = value_col
    if col not in df.columns or df[col].dropna().empty:
        if "net_liquidity" in df.columns and not df["net_liquidity"].dropna().empty:
            col = "net_liquidity"
        else:
            return None

    if as_of is not None and "date" in df.columns:
        df = df[pd.to_datetime(df["date"], errors="coerce") <= pd.Timestamp(as_of)]
        if df.empty:
            return None

    dates, vals = clean_series(df, col)
    if not vals:
        return None

    ref = _to_date(as_of) if as_of is not None else dates[-1]
    latest_date = dates[-1]
    stale = _bday_lag(latest_date, ref) > max_age_bd

    # Trailing-mean smoothing (damps single-day spikes, e.g. TGA settlement).
    s = pd.Series(vals)
    sm = s.rolling(window=max(1, int(smooth)), min_periods=1).mean().tolist()
    latest = float(sm[-1])

    n = len(sm)
    if n < W + 1:
        return LiquidityScore(0, None, latest, ref, "insufficient", bool(stale), series=col)

    base = sm[-1 - W]
    roc = (sm[-1] - base) / base if base else 0.0
    tightening = -roc                       # falling level (roc<0) → tightening>0 → +score
    score = _bucket(roc, band_hi, band_lo) * _sign(tightening)
    return LiquidityScore(int(score), float(roc), latest, ref, "band", bool(stale), series=col)
