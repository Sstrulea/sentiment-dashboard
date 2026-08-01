"""FAZA 1 (fix/watchdog-per-instrument) — per-instrument price freshness.
Synthetic fixtures only; the real-data check (today's state flags FTSE100
and nothing else) is exercised manually via scripts/check_freshness.py, not
duplicated here as a brittle pinned-value test.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.price_freshness_guard import (
    instrument_cadence_threshold,
    per_instrument_freshness,
    freshness_report,
)

AS_OF = pd.Timestamp("2026-07-31")


def _daily_business_dates(start: str, periods: int) -> pd.DatetimeIndex:
    """Mon-Fri only — a clean instrument with normal weekend gaps, no holidays."""
    return pd.bdate_range(start, periods=periods)


def _rows(symbol: str, dates) -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": symbol, "date": pd.to_datetime(list(dates)),
        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "source": "mt5",
    })


def test_stale_instrument_detected():
    # Normal business-day history ending 80 days before as_of — no export since.
    dates = _daily_business_dates("2026-01-01", 90)
    df = _rows("FTSE100", dates[dates < AS_OF - pd.Timedelta(days=75)])
    out = per_instrument_freshness(df, ["FTSE100"], AS_OF)
    row = out.iloc[0]
    assert row["status"] == "stale"
    assert row["age_days"] > row["threshold_days"]


def test_fresh_instrument_passes():
    dates = _daily_business_dates("2026-05-01", 60)
    df = _rows("DAX", dates)
    out = per_instrument_freshness(df, ["DAX"], dates[-1] + pd.Timedelta(hours=6))
    assert out.iloc[0]["status"] == "fresh"


def test_weekend_gap_is_not_flagged():
    # as_of falls on a Saturday/Sunday right after Friday's bar — a completely
    # normal 1-2 day gap for a business-day-only instrument, must not flag.
    dates = _daily_business_dates("2026-06-01", 40)  # ends on a weekday
    friday = dates[-1]
    assert friday.dayofweek == 4  # bdate_range always ends on a weekday; confirm Friday
    saturday = friday + pd.Timedelta(days=1)
    df = _rows("EURUSD", dates)
    out = per_instrument_freshness(df, ["EURUSD"], saturday)
    assert out.iloc[0]["status"] == "fresh"


def test_absent_instrument_is_no_data_not_stale():
    df = _rows("DAX", _daily_business_dates("2026-05-01", 10))
    out = per_instrument_freshness(df, ["DAX", "DXY"], AS_OF)
    dxy = out[out["symbol"] == "DXY"].iloc[0]
    assert dxy["status"] == "no_data"
    assert pd.isna(dxy["age_days"])
    assert pd.isna(dxy["threshold_days"]) or dxy["threshold_days"] is None
    # Distinct from a genuinely stale instrument:
    dax = out[out["symbol"] == "DAX"].iloc[0]
    assert dax["status"] == "stale"   # 10 old bars, decades before as_of relative check
    assert dxy["status"] != dax["status"]


def test_no_data_excluded_from_alerts_but_visible_in_report():
    df = _rows("DAX", _daily_business_dates("2026-07-01", 20))
    per_instrument = per_instrument_freshness(df, ["DAX", "DXY"], pd.Timestamp("2026-07-28"))
    report = freshness_report(per_instrument)
    assert report["any_stale"] is False           # DAX fresh, DXY no_data — neither counts as stale
    assert "DXY" in report["no_data"]
    assert report["stale"] == []


def test_all_fresh_report_is_silent():
    dates = _daily_business_dates("2026-06-01", 60)
    df = pd.concat([_rows(s, dates) for s in ["DAX", "EURUSD", "GOLD"]], ignore_index=True)
    per_instrument = per_instrument_freshness(df, ["DAX", "EURUSD", "GOLD"], dates[-1])
    report = freshness_report(per_instrument)
    assert report["any_stale"] is False
    assert report["stale_count"] == 0
    assert report["fresh_count"] == 3


def test_report_groups_multiple_stale_into_one_report_not_per_alert():
    dates = _daily_business_dates("2026-01-01", 90)
    old_cutoff = AS_OF - pd.Timedelta(days=75)
    fresh_dates = pd.bdate_range(end=AS_OF, periods=90)  # ends AT as_of -> DAX stays fresh
    df = pd.concat([
        _rows("FTSE100", dates[dates < old_cutoff]),
        _rows("NIKKEI", dates[dates < old_cutoff]),
        _rows("DAX", fresh_dates),
    ], ignore_index=True)
    per_instrument = per_instrument_freshness(df, ["FTSE100", "NIKKEI", "DAX"], AS_OF)
    report = freshness_report(per_instrument)
    assert report["any_stale"] is True
    assert report["stale_count"] == 2
    assert {r["symbol"] for r in report["stale"]} == {"FTSE100", "NIKKEI"}


def test_threshold_derives_from_instruments_own_gap_history_not_fixed():
    # An instrument whose OWN history has wider gaps (e.g. weekly cadence)
    # gets a proportionally wider threshold than a daily one.
    weekly_dates = pd.date_range("2026-01-01", periods=20, freq="7D")
    daily_dates = _daily_business_dates("2026-01-01", 90)
    weekly_threshold = instrument_cadence_threshold(pd.Series(weekly_dates))
    daily_threshold = instrument_cadence_threshold(pd.Series(daily_dates))
    assert weekly_threshold > daily_threshold


def test_thin_history_falls_back_to_default_threshold():
    thin = pd.Series(pd.to_datetime(["2026-07-01", "2026-07-02"]))
    t = instrument_cadence_threshold(thin)
    from src.price_freshness_guard import DEFAULT_FALLBACK_THRESHOLD_DAYS
    assert t == DEFAULT_FALLBACK_THRESHOLD_DAYS
