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
from datetime import date
from typing import Optional

import numpy as np
import pandas as pd

from .momentum_common import bday_lag as _bday_lag, bucket as _bucket, clean_series
from .momentum_common import sign as _sign, to_date as _to_date

# Defaults
W_DEFAULT = 21          # trading-day offset (~1 month), in ROWS not calendar days
END_N = 5               # each end of the change is the mean of this many observations (audit 5B)
BASELINE_N = 252        # rolling window of W-changes for the volatility baseline
MIN_FOR_Z = 60          # need this many W-changes for a stable std → else fallback
MAX_AGE_BD = 7          # latest obs older than this many business days → stale
# Per-source threshold = source cadence + holiday margin (audit 4C). Daily
# sources keep MAX_AGE_BD. RBA F2 is weekly: published on Friday with data to
# Wednesday, so a healthy series is up to ~7 business days old the day before
# the next release; 10 leaves room for a holiday or a late publication.
SOURCE_MAX_AGE_BD = {"rba": 10}


def max_age_for(source: Optional[str]) -> int:
    """Staleness threshold (business days) for the source of a currency's
    latest observation. Used by compute_rate_scores AND freshness.rates."""
    return SOURCE_MAX_AGE_BD.get(str(source), MAX_AGE_BD)

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


def _bucket_z(z: float) -> int:
    return _bucket(z, Z_HI, Z_LO)


def _bucket_fallback(delta: float) -> int:
    return _bucket(delta, FB_HI, FB_LO)


def _series_for(rates_df: pd.DataFrame, currency: str) -> tuple[list[date], list[float]]:
    sub = rates_df[rates_df["currency"] == currency]
    if sub.empty:
        return [], []
    return clean_series(sub, "yield_pct")


def _end_changes(ys: list[float], W: int = W_DEFAULT, k: int = END_N) -> list[float]:
    """[mean(y[i-k+1..i]) − mean(y[i-W-k+1..i-W]) for i ≥ W+k−1] — the averaged-
    ends change series (audit 5B). Exactly antisymmetric under y → −y."""
    a = np.asarray(ys, dtype=float)
    ends = np.lib.stride_tricks.sliding_window_view(a, k).sum(axis=1) / k   # mean(a[j..j+k-1])
    return [float(ends[j] - ends[j - W]) for j in range(W, len(ends))]


def _latest_source(rates_df: pd.DataFrame, currency: str) -> Optional[str]:
    if "source" not in rates_df.columns:
        return None
    sub = rates_df[rates_df["currency"] == currency]
    if sub.empty:
        return None
    d = pd.to_datetime(sub["date"], errors="coerce")
    return None if d.isna().all() else str(sub.loc[d.idxmax(), "source"])


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

    if n < W + END_N:
        return RateScore(currency, 0, None, latest_yield, None, "insufficient", ref, stale)

    # Audit 5B: averaged ends — Δ = mean of the last END_N observations minus the
    # same mean W observations earlier (one noisy print no longer flips a bucket).
    changes = _end_changes(ys, W)
    delta_w = changes[-1]
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
    max_age_bd: Optional[int] = None,
) -> dict[str, RateScore]:
    """Per-currency repricing-momentum scores. Currencies absent from the frame
    are simply omitted (graceful — no rate row for that currency).

    `max_age_bd` None (default) = per-source threshold (max_age_for, judged on
    the source of the latest observation); an int overrides it for all.

    Lookahead guard: when `as_of` is given, rows dated after it are dropped here
    (structural — independent of caller slicing)."""
    if rates_df is None or rates_df.empty:
        return {}

    if as_of is not None and "date" in rates_df.columns:
        rates_df = rates_df[pd.to_datetime(rates_df["date"], errors="coerce") <= pd.Timestamp(as_of)]
        if rates_df.empty:
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
        age = max_age_bd if max_age_bd is not None else max_age_for(_latest_source(rates_df, ccy))
        out[ccy] = compute_rate_score_for(
            dates, ys, ccy, ref, W=W, baseline_n=baseline_n,
            min_for_z=min_for_z, max_age_bd=age,
        )
    return out


# ---------------------------------------------------------------------------
# Pair monetary on the 2y SPREAD (audit 4A measured, 5B adopted)
# ---------------------------------------------------------------------------

def spread_series(rates_df: pd.DataFrame, base: str, quote: str) -> tuple[list[date], list[float]]:
    """(dates, 2y_base − 2y_quote) on the dates BOTH series observe (inner join,
    no fill), ascending. Swapping base/quote negates every value exactly."""
    db, yb = _series_for(rates_df, base)
    dq, yq = _series_for(rates_df, quote)
    q = dict(zip(dq, yq))
    common = [(d, y - q[d]) for d, y in zip(db, yb) if d in q]
    return [d for d, _ in common], [v for _, v in common]


def compute_pair_spread_scores(rates_df: pd.DataFrame, pairs, as_of: Optional[date] = None) -> dict:
    """{symbol: {m, z, delta, spread, as_of, method}} for fx pairs (symbol, base,
    quote) whose legs both have a 2y series. m = sign(z)·bucket(|z|; 1.0/0.5) of
    the averaged-ends change of the spread (same definition as a currency's own
    score, compute_rate_score_for); the spread is dated at the last common
    date. Freshness is NOT judged here: each leg's own score (max_age_for its
    source) decides whether monetary is in the pair's D1=D intersection."""
    if rates_df is None or rates_df.empty:
        return {}
    if as_of is not None and "date" in rates_df.columns:
        rates_df = rates_df[pd.to_datetime(rates_df["date"], errors="coerce") <= pd.Timestamp(as_of)]
    have = set(rates_df["currency"].dropna().unique())
    out = {}
    for sym, base, quote in pairs:
        if base not in have or quote not in have:
            continue
        dates, vals = spread_series(rates_df, base, quote)
        if not vals:
            continue
        rs = compute_rate_score_for(dates, vals, sym, dates[-1], max_age_bd=10**6)
        out[sym] = {"m": int(rs.rate_score), "z": rs.z, "delta": rs.delta_w,
                    "spread": float(vals[-1]), "as_of": dates[-1].isoformat(),
                    "method": rs.method, "base": base, "quote": quote}
    return out
