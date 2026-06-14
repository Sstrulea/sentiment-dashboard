"""Pure Fed net-liquidity momentum scoring (band-based, NOT z) — no I/O.

Net liquidity = WALCL − TGA − RRP. The score is a FIXED percentage-band rate-of-
change over W trading days — deliberately NOT a z-score: z blows up in flat
regimes (tiny std), which would violate "stagnant ⇒ 0". QT ended Dec 2025 and net
liquidity is roughly flat, so the live reading should be ≈ 0.

Raw convention (uniform with the other two rate sub-components, + = TIGHTENING):
  net liquidity FALLING = tightening = +score ; RISING = easing = −score.
The cross-asset YAML applies sign −1, so net liquidity RISING ⇒ bullish for
indices & metals (and falling ⇒ bearish), same as the 2y / real-yield rule.

    compute_liquidity_score(df, as_of=None, W=21, smooth=5,
                            band_hi=BAND_HI, band_lo=BAND_LO, max_age_bd=10)
      -> LiquidityScore{score(−2..+2), roc, latest, as_of, method, stale}

`df` schema: date, net_liquidity (+ optional cols). Pure/deterministic given
`as_of`; safe to unit-test with synthetic frames.
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

# Empirically calibrated on the full net-liquidity history (2002–2026; 5-day
# smooth, 21-bd ROC; BAND_HI=p68, BAND_LO=p40 of |roc|). Realized bucket mix
# {-2:11.5, -1:11.2, 0:40.0, +1:16.8, +2:20.5} → 0≈40%, |±2|≈32%, matching the
# 2y / real-yield pillars. Bands are FRACTIONAL monthly ROC (0.0201 = 2.01%/mo).
BAND_HI = 0.0201        # |roc| ≥ 2.01%/mo → ±2
BAND_LO = 0.0098        # |roc| ≥ 0.98%/mo → ±1


@dataclass
class LiquidityScore:
    score: int                  # -2..+2 (raw: net liquidity FALLING = + tightening)
    roc: Optional[float]        # W-day rate-of-change, fractional (e.g. -0.03 = -3%)
    latest: Optional[float]     # latest (smoothed) net-liquidity level
    as_of: Optional[date]
    method: str                 # "band" | "insufficient"
    stale: bool


def compute_liquidity_score(
    df: pd.DataFrame,
    as_of: Optional[date] = None,
    W: int = W_DEFAULT,
    smooth: int = SMOOTH_DEFAULT,
    band_hi: float = BAND_HI,
    band_lo: float = BAND_LO,
    max_age_bd: int = MAX_AGE_BD,
) -> Optional[LiquidityScore]:
    """Band-based net-liquidity momentum score. Returns None if the frame has no
    usable rows. Lookahead guard: rows dated after `as_of` are dropped here."""
    if df is None or df.empty or "net_liquidity" not in df.columns:
        return None

    if as_of is not None and "date" in df.columns:
        df = df[pd.to_datetime(df["date"], errors="coerce") <= pd.Timestamp(as_of)]
        if df.empty:
            return None

    dates, vals = clean_series(df, "net_liquidity")
    if not vals:
        return None

    ref = _to_date(as_of) if as_of is not None else dates[-1]
    latest_date = dates[-1]
    stale = _bday_lag(latest_date, ref) > max_age_bd

    # Trailing-mean smoothing (damps single-day TGA spikes).
    s = pd.Series(vals)
    sm = s.rolling(window=max(1, int(smooth)), min_periods=1).mean().tolist()
    latest = float(sm[-1])

    n = len(sm)
    if n < W + 1:
        return LiquidityScore(0, None, latest, ref, "insufficient", bool(stale))

    base = sm[-1 - W]
    roc = (sm[-1] - base) / base if base else 0.0
    tightening = -roc                       # falling NL (roc<0) → tightening>0 → +score
    score = _bucket(roc, band_hi, band_lo) * _sign(tightening)
    return LiquidityScore(int(score), float(roc), latest, ref, "band", bool(stale))
