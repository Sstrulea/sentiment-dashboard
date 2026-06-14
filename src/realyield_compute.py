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
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from .momentum_common import bday_lag as _bday_lag, bucket as _bucket, clean_series
from .momentum_common import sign as _sign, to_date as _to_date

# Defaults — identical thresholds/cadence to the rate pillar (src.rate_compute).
W_DEFAULT = 21          # trading-day offset (~1 month), in ROWS not calendar days
BASELINE_N = 252        # rolling window of W-changes for the volatility baseline
MIN_FOR_Z = 60          # need this many W-changes for a stable std → else fallback
MAX_AGE_BD = 7          # latest obs older than this many business days → stale

# Source preference for the US 10y real yield. DFII10 (FRED) first; the Treasury
# real-yield-curve series is the keyless backup. The momentum is computed on ONE
# series only (never mixed) to avoid methodology-difference artifacts.
PREFERRED_SERIES = ["DFII10", "REAL_10Y_TSY"]

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


def _bucket_z(z: float) -> int:
    return _bucket(z, Z_HI, Z_LO)


def _bucket_fallback(delta: float) -> int:
    return _bucket(delta, FB_HI, FB_LO)


def _clean_series(df: pd.DataFrame) -> tuple[list[date], list[float]]:
    return clean_series(df, "yield_pct")


def compute_realyield_score_for(
    dates: list[date],
    ys: list[float],
    series: str,
    ref: date,
    W: int = W_DEFAULT,
    baseline_n: int = BASELINE_N,
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
        base = changes[-baseline_n:]
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


def select_series(
    df: pd.DataFrame,
    preference: Optional[list[str]] = None,
    as_of: Optional[date] = None,
    max_age_bd: int = MAX_AGE_BD,
) -> Optional[str]:
    """Pick ONE series to score from a multi-series frame.

    Returns the first series in `preference` that is present AND fresh (latest
    obs within `max_age_bd` business days). If none is fresh, returns the present
    preferred series with the most recent latest date (so a score still emerges,
    flagged stale). None if the frame has no usable series.
    """
    if df is None or df.empty or "series" not in df.columns:
        return None
    preference = preference or PREFERRED_SERIES
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["date"])
    if d.empty:
        return None
    ref = _to_date(as_of) if as_of is not None else _to_date(d["date"].max())

    present = {s: g["date"].max() for s, g in d.groupby("series")}
    # honour preference order; series not in the preference list go last
    ordered = [s for s in preference if s in present] + \
              [s for s in present if s not in (preference or [])]
    fresh = [s for s in ordered if _bday_lag(_to_date(present[s]), ref) <= max_age_bd]
    if fresh:
        return fresh[0]
    # none fresh → the present series with the most recent data
    return max(present, key=lambda s: present[s]) if present else None


def compute_realyield_score(
    df: pd.DataFrame,
    series: Optional[str] = None,
    as_of: Optional[date] = None,
    W: int = W_DEFAULT,
    baseline_n: int = BASELINE_N,
    min_for_z: int = MIN_FOR_Z,
    max_age_bd: int = MAX_AGE_BD,
    preference: Optional[list[str]] = None,
) -> Optional[RealYieldScore]:
    """Momentum score for one real-yield series.

    If `series` is given, filters to it. If `series` is None and the frame has a
    `series` column, auto-selects the preferred AVAILABLE series (DFII10 if fresh,
    else REAL_10Y_TSY) via `select_series` — never mixing series. Returns None if
    the frame has no usable rows.

    Lookahead guard: when `as_of` is given, rows dated after it are dropped here
    (structural — independent of caller slicing).
    """
    if df is None or df.empty:
        return None

    if as_of is not None and "date" in df.columns:
        df = df[pd.to_datetime(df["date"], errors="coerce") <= pd.Timestamp(as_of)]
        if df.empty:
            return None

    chosen = series
    if "series" in df.columns:
        if chosen is None:
            chosen = select_series(df, preference=preference, as_of=as_of, max_age_bd=max_age_bd)
            if chosen is None:
                return None
        df = df[df["series"] == chosen]
        if df.empty:
            return None
    if chosen is None:
        chosen = "DFII10"  # no series column → label with the canonical name

    ref = _to_date(as_of) if as_of is not None else None
    if ref is None:
        ref = _to_date(pd.to_datetime(df["date"], errors="coerce").max())

    dates, ys = _clean_series(df)
    if not ys:
        return None
    return compute_realyield_score_for(
        dates, ys, chosen, ref, W=W, baseline_n=baseline_n,
        min_for_z=min_for_z, max_age_bd=max_age_bd,
    )
