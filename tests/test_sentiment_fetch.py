"""Tests for src.sentiment_fetch — hits the real CBOE endpoints (network required)."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from src.sentiment_fetch import (
    check_pc_backfill_availability,
    fetch_pc_daily,
    fetch_vix_history,
)


def _is_sorted_ascending(s: pd.Series) -> bool:
    return s.is_monotonic_increasing


@pytest.mark.parametrize("symbol", ["VIX", "VIX3M"])
def test_fetch_vix_history_schema(symbol):
    df = fetch_vix_history(symbol)
    assert not df.empty
    assert list(df.columns) == ["date", "close"]
    assert pd.api.types.is_datetime64_any_dtype(df["date"])
    assert pd.api.types.is_float_dtype(df["close"])
    assert df["close"].notna().all()
    assert _is_sorted_ascending(df["date"])


def test_fetch_pc_daily_recent():
    # Walk back a few business days; skip weekends/holidays by retry.
    today = dt.date.today()
    row = None
    last_tried = None
    for offset in (3, 4, 5, 6, 7, 8):
        candidate = today - dt.timedelta(days=offset)
        if candidate.weekday() >= 5:  # Saturday/Sunday
            continue
        last_tried = candidate
        row = fetch_pc_daily(candidate)
        if row is not None:
            break
    assert row is not None, f"No P/C data returned for any recent date (last tried {last_tried})"
    assert row["date"] == last_tried
    for key in ("total", "equity", "index", "spx_spxw", "vix"):
        assert key in row


def test_fetch_pc_daily_future_returns_none():
    future = dt.date.today() + dt.timedelta(days=7)
    assert fetch_pc_daily(future) is None


def test_fetch_pc_daily_invalid_date_raises():
    with pytest.raises(ValueError):
        fetch_pc_daily("2024-01-15")  # type: ignore[arg-type]


def test_check_pc_backfill_availability_structure():
    report = check_pc_backfill_availability()
    assert isinstance(report, dict)
    assert set(report.keys()) == {"supports_backfill", "results", "recommendation"}
    assert isinstance(report["supports_backfill"], bool)
    assert isinstance(report["results"], list)
    assert len(report["results"]) == 10
    assert isinstance(report["recommendation"], str)
    for r in report["results"]:
        assert set(r.keys()) >= {"date", "status", "sample"}
