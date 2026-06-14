"""Shared helpers for the momentum / rate sub-scorers (D3 dedupe).

Behavior-preserving extraction of the helpers that were duplicated across
src/rate_compute.py and src/realyield_compute.py. The z-momentum SCORING of those
two modules is unchanged — only these primitives are shared. src/liquidity_compute.py
(band-based, NOT z) also reuses `to_date`, `bday_lag`, `sign`, `bucket`, and
`clean_series`, but keeps its own scoring core.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd


def to_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    ts = pd.Timestamp(v)
    return None if pd.isna(ts) else ts.date()


def bday_lag(latest: date, ref: date) -> int:
    if latest >= ref:
        return 0
    return int(np.busday_count(latest, ref))


def sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def bucket(x: float, hi: float, lo: float) -> int:
    """Magnitude bucket in {0,1,2}: |x| >= hi -> 2, >= lo -> 1, else 0.
    Generalizes the previous _bucket_z (hi=1.0, lo=0.5) and _bucket_fallback
    (hi=0.25, lo=0.08)."""
    a = abs(x)
    if a >= hi:
        return 2
    if a >= lo:
        return 1
    return 0


def clean_series(df: pd.DataFrame, value_col: str) -> tuple[list[date], list[float]]:
    """Ascending (dates, values) with NaNs dropped and one row per date
    (last-write-wins). Identical to the prior per-module cleaners."""
    sub = df.copy()
    sub["date"] = pd.to_datetime(sub["date"], errors="coerce")
    sub[value_col] = pd.to_numeric(sub[value_col], errors="coerce")
    sub = sub.dropna(subset=["date", value_col])
    sub = sub.drop_duplicates(subset=["date"], keep="last").sort_values("date")
    return [d.date() for d in sub["date"]], [float(v) for v in sub[value_col]]
