"""Year-on-year rates from an official index (stance phase S0a) - pure, no I/O.

The agencies publish some inflation series only as an index (BEA PCE price indexes on FRED, the Statistics Bureau of
Japan CPI, Stats NZ CPI); the stance inputs need the 12-month (4-quarter) rate. The arithmetic is the one of
src/official_series.py ((cur / base - 1) * 100, Decimal ROUND_HALF_UP, one decimal - the rounding the agencies print);
nothing here reads a file. A rate is computed only within ONE series (one index base): a rebased index is a new series.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..official_series import pct_change, period_shift


def lag_for(period: str) -> int:
    """12 for a monthly period ('2026-09'), 4 for a quarterly one ('2026Q3')."""
    return 4 if "Q" in period else 12


def yoy(index: dict, period: str, places: int = 1) -> Optional[Decimal]:
    """The y/y rate of `period` from {period: index value}; None when either point is missing."""
    return pct_change(index.get(period), index.get(period_shift(period, -lag_for(period))), places)


def yoy_series(index: dict, places: int = 1) -> dict:
    """{period: y/y} for every period whose base is present (sorted)."""
    out = {}
    for p in sorted(index):
        v = yoy(index, p, places)
        if v is not None:
            out[p] = v
    return out
