"""Pure Rate-Expectations scoring — no I/O, no network.

Turns a 2-year yield series (per currency) into a repricing-momentum score in
−2..+2: how fast the front end has repriced over ~1 month, normalized by its own
recent volatility. Rising yields = hawkish = positive (bullish for the currency).

    compute_rate_scores(rates_df, as_of=None, W=21, baseline_n=252, ...) -> dict[ccy, RateScore]

`rates_df` schema: currency, date, yield_pct (+ optional tenor/source). Pure and
deterministic given `as_of`; safe to unit-test with synthetic frames.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

# Defaults
W_DEFAULT = 21          # trading-day offset (~1 month), in ROWS not calendar days
BASELINE_N = 252        # rolling window of W-changes for the volatility baseline
MIN_FOR_Z = 60          # need this many W-changes for a stable std → else fallback
MAX_AGE_BD = 7          # latest obs older than this many business days → stale

# z → score thresholds
Z_HI, Z_LO = 1.0, 0.5
# fallback bands on delta_w (percentage points)
FB_HI, FB_LO = 0.25, 0.08


@dataclass
class RateScore:
    currency: str
    rate_score: int           # -2..+2
    delta_w: Optional[float]  # W-day change in yield, percentage points
    latest_yield: Optional[float]
    z: Optional[float]
    method: str               # "z" | "fallback" | "insufficient"
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


def _series_for(rates_df: pd.DataFrame, currency: str) -> tuple[list[date], list[float]]:
    sub = rates_df[rates_df["currency"] == currency]
    if sub.empty:
        return [], []
    sub = sub.copy()
    sub["date"] = pd.to_datetime(sub["date"], errors="coerce")
    sub["yield_pct"] = pd.to_numeric(sub["yield_pct"], errors="coerce")
    sub = sub.dropna(subset=["date", "yield_pct"])
    sub = sub.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    return [d.date() for d in sub["date"]], [float(v) for v in sub["yield_pct"]]


def compute_rate_score_for(
    dates: list[date],
    ys: list[float],
    currency: str,
    ref: date,
    W: int = W_DEFAULT,
    baseline_n: int = BASELINE_N,
    min_for_z: int = MIN_FOR_Z,
    max_age_bd: int = MAX_AGE_BD,
) -> RateScore:
    """Score one already-cleaned ascending (dates, ys) series."""
    n = len(ys)
    latest_yield = ys[-1] if n else None
    latest_date = dates[-1] if n else None
    stale = (latest_date is None) or (_bday_lag(latest_date, ref) > max_age_bd)

    if n < W + 1:
        return RateScore(currency, 0, None, latest_yield, None, "insufficient", ref, stale)

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

    return RateScore(currency, int(score), float(delta_w),
                     None if latest_yield is None else float(latest_yield),
                     None if z is None else float(z), method, ref, bool(stale))


def compute_rate_scores(
    rates_df: pd.DataFrame,
    as_of: Optional[date] = None,
    W: int = W_DEFAULT,
    baseline_n: int = BASELINE_N,
    min_for_z: int = MIN_FOR_Z,
    max_age_bd: int = MAX_AGE_BD,
) -> dict[str, RateScore]:
    """Per-currency repricing-momentum scores. Currencies absent from the frame
    are simply omitted (graceful — no rate row for that currency)."""
    if rates_df is None or rates_df.empty:
        return {}

    ref = _to_date(as_of) if as_of is not None else None
    if ref is None:
        # default reference = most recent date in the frame (deterministic),
        # so freshness is judged against the data, not wall-clock.
        ref = _to_date(pd.to_datetime(rates_df["date"], errors="coerce").max())

    out: dict[str, RateScore] = {}
    for ccy in sorted(rates_df["currency"].dropna().unique()):
        dates, ys = _series_for(rates_df, ccy)
        if not ys:
            continue
        out[ccy] = compute_rate_score_for(
            dates, ys, ccy, ref, W=W, baseline_n=baseline_n,
            min_for_z=min_for_z, max_age_bd=max_age_bd,
        )
    return out
