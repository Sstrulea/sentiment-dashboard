"""Pure US-real-yield momentum scoring — no I/O, no network.

Mirror of src/rate_compute.py, but for a single series (the US 10y TIPS real
yield, FRED DFII10). Turns the real-yield series into a momentum score in
−2..+2: how fast it has moved over ~1 month, normalized by its own recent
volatility.

RAW convention here: real yield UP = `+` (rising real yields). The asset-specific
sign (−1 for gold/risk, +1 for USD, etc.) is applied later in the cross-asset
scoring layer (6.3b) — NOT here.

    compute_realyield_score(df, W=21, vol_window=252, ...) -> RealYieldScore

`df` schema: date, yield_pct (+ optional series/source). Pure and deterministic
given `as_of`; safe to unit-test with synthetic frames.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

# Defaults — identical thresholds/cadence to the rate pillar.
W_DEFAULT = 21          # trading-day offset (~1 month), in ROWS not calendar days
VOL_WINDOW = 252        # rolling window of W-changes for the volatility baseline
MIN_FOR_Z = 60          # need this many W-changes for a stable std → else fallback
MAX_AGE_BD = 7          # latest obs older than this many business days → stale

# z → score thresholds (same as rate pillar)
Z_HI, Z_LO = 1.0, 0.5
# fallback bands on delta_w (percentage points)
FB_HI, FB_LO = 0.25, 0.08


@dataclass
class RealYieldScore:
    series: str
    score: int                # -2..+2 (raw: rising real yield = +)
    delta_w: Optional[float]   # W-day change in real yield, percentage points
    latest_yield: Optional[float]
    z: Optional[float]
    method: str                # "z" | "fallback" | "insufficient"
    as_of: Optional[date]
    stale: bool


def _to_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    ts = pd.Timestamp(v)
    return None if pd.isna(ts) else ts.date()


def _bday_lag(latest: date, ref: date) -> int:
    if latest >= ref:
        return 0
    return int(np.busday_count(latest, ref))


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _bucket_z(z: float) -> int:
    a = abs(z)
    if a >= Z_HI:
        return 2
    if a >= Z_LO:
        return 1
    return 0


def _bucket_fallback(delta: float) -> int:
    a = abs(delta)
    if a >= FB_HI:
        return 2
    if a >= FB_LO:
        return 1
    return 0


def _clean_series(df: pd.DataFrame) -> tuple[list[date], list[float]]:
    sub = df.copy()
    sub["date"] = pd.to_datetime(sub["date"], errors="coerce")
    sub["yield_pct"] = pd.to_numeric(sub["yield_pct"], errors="coerce")
    sub = sub.dropna(subset=["date", "yield_pct"])
    sub = sub.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    return [d.date() for d in sub["date"]], [float(v) for v in sub["yield_pct"]]


def compute_realyield_score_for(
    dates: list[date],
    ys: list[float],
    series: str,
    ref: date,
    W: int = W_DEFAULT,
    vol_window: int = VOL_WINDOW,
    min_for_z: int = MIN_FOR_Z,
    max_age_bd: int = MAX_AGE_BD,
) -> RealYieldScore:
    """Score one already-cleaned ascending (dates, ys) real-yield series."""
    n = len(ys)
    latest_yield = ys[-1] if n else None
    latest_date = dates[-1] if n else None
    stale = (latest_date is None) or (_bday_lag(latest_date, ref) > max_age_bd)

    if n < W + 1:
        return RealYieldScore(series, 0, None, latest_yield, None, "insufficient", ref, stale)

    delta_w = ys[-1] - ys[-1 - W]
    changes = [ys[i] - ys[i - W] for i in range(W, n)]

    z: Optional[float] = None
    method = "fallback"
    if len(changes) >= min_for_z:
        base = changes[-vol_window:]
        std = float(np.std(base, ddof=1)) if len(base) >= 2 else float("nan")
        if std and not np.isnan(std) and std > 0:
            z = delta_w / std
            method = "z"

    if method == "z":
        score = _bucket_z(z) * _sign(delta_w)
    else:
        score = _bucket_fallback(delta_w) * _sign(delta_w)

    return RealYieldScore(series, int(score), float(delta_w),
                          None if latest_yield is None else float(latest_yield),
                          None if z is None else float(z), method, ref, bool(stale))


def compute_realyield_score(
    df: pd.DataFrame,
    series: str = "DFII10",
    as_of: Optional[date] = None,
    W: int = W_DEFAULT,
    vol_window: int = VOL_WINDOW,
    min_for_z: int = MIN_FOR_Z,
    max_age_bd: int = MAX_AGE_BD,
) -> Optional[RealYieldScore]:
    """Momentum score for one real-yield series. Filters to `series` if a
    `series` column exists. Returns None if the frame has no usable rows."""
    if df is None or df.empty:
        return None
    if "series" in df.columns:
        df = df[df["series"] == series]
        if df.empty:
            return None

    ref = _to_date(as_of) if as_of is not None else None
    if ref is None:
        ref = _to_date(pd.to_datetime(df["date"], errors="coerce").max())

    dates, ys = _clean_series(df)
    if not ys:
        return None
    return compute_realyield_score_for(
        dates, ys, series, ref, W=W, vol_window=vol_window,
        min_for_z=min_for_z, max_age_bd=max_age_bd,
    )
