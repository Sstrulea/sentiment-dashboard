"""fix/calendar-freshness-per-ccy — per-currency calendar freshness.

Synthetic fixtures for the pure per-indicator-threshold logic; the jb_raw
cross-check is exercised against real retained payloads in a separate,
non-pinned integration test (mirrors the pmi_ingest_guard/jb_raw pattern).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.calendar_freshness_guard import (
    per_currency_indicator_freshness,
    currency_freshness_report,
    check_pending_actuals_in_jb_raw,
)

AS_OF = pd.Timestamp("2026-08-01")

IND_CFG = {
    "defaults": {
        "max_age_by_frequency": {"weekly": 14, "monthly": 45, "quarterly": 110},
        "max_age_days": 120, "default_frequency": "monthly",
    },
    "indicators": {
        "cpi_yoy": {"category": "inflation", "weight": 1.0, "frequency": "monthly"},
        "core_cpi": {"category": "inflation", "weight": 1.0, "frequency": "monthly",
                     "frequency_overrides": {"AUD": "quarterly"}},
        "gdp_qoq": {"category": "growth", "weight": 1.0, "frequency": "quarterly"},
        "cpi_monthly": {"category": "inflation_display", "weight": 0.0, "frequency": "monthly"},
    },
}


def _row(ccy, key, release_dt, actual=1.0):
    return {"currency": ccy, "indicator_key": key, "release_dt": pd.Timestamp(release_dt), "actual": actual}


def test_stale_indicator_flags_the_currency():
    cal = pd.DataFrame([
        _row("USD", "cpi_yoy", "2026-06-10"),   # 52 days before AS_OF, threshold 45 -> stale
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["USD"])
    report = currency_freshness_report(out)
    assert report["any_stale"] is True
    assert "USD" in report["stale_currencies"]
    assert report["by_currency"]["USD"]["stale_indicators"][0]["indicator_key"] == "cpi_yoy"


def test_chf_style_rare_cadence_does_not_false_positive():
    # A currency whose ONE tracked indicator here is quarterly (GDP), last
    # printed well within its own 110-day window -- must NOT be flagged just
    # because it's been "a while" in absolute terms.
    cal = pd.DataFrame([
        _row("CHF", "gdp_qoq", AS_OF - pd.Timedelta(days=60)),   # 60d < 110d threshold
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["CHF"])
    report = currency_freshness_report(out)
    assert report["any_stale"] is False


def test_nzd_style_rare_cadence_multiple_indicators_no_false_positive():
    # Multiple indicators, each individually within its own threshold, even
    # though the MOST RECENT one overall is somewhat old.
    cal = pd.DataFrame([
        _row("NZD", "cpi_yoy", AS_OF - pd.Timedelta(days=40)),    # monthly, 40<45
        _row("NZD", "gdp_qoq", AS_OF - pd.Timedelta(days=100)),   # quarterly, 100<110
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["NZD"])
    report = currency_freshness_report(out)
    assert report["any_stale"] is False


def test_absent_indicator_is_no_data_not_stale():
    cal = pd.DataFrame([
        _row("GBP", "cpi_yoy", AS_OF - pd.Timedelta(days=10)),
        # gdp_qoq: zero rows for GBP at all
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["GBP"])
    gdp_row = out[out["indicator_key"] == "gdp_qoq"].iloc[0]
    assert gdp_row["status"] == "no_data"
    assert pd.isna(gdp_row["age_days"])
    cpi_row = out[out["indicator_key"] == "cpi_yoy"].iloc[0]
    assert cpi_row["status"] == "fresh"
    assert gdp_row["status"] != cpi_row["status"]


def test_display_only_indicator_excluded_from_check():
    # cpi_monthly has weight 0.0 -> must never appear in the per-indicator table.
    cal = pd.DataFrame([
        _row("AUD", "cpi_monthly", "2020-01-01"),   # ancient, would be "stale" if checked
        _row("AUD", "core_cpi", AS_OF - pd.Timedelta(days=30)),   # quarterly override for AUD, fresh
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["AUD"])
    assert "cpi_monthly" not in out["indicator_key"].tolist()


def test_report_is_grouped_not_one_alert_per_indicator():
    cal = pd.DataFrame([
        _row("USD", "cpi_yoy", AS_OF - pd.Timedelta(days=100)),   # stale
        _row("USD", "gdp_qoq", AS_OF - pd.Timedelta(days=200)),   # stale
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["USD"])
    report = currency_freshness_report(out)
    assert report["stale_currencies"] == ["USD"]
    assert len(report["by_currency"]["USD"]["stale_indicators"]) == 2   # one currency entry, both listed inside


def test_all_fresh_is_silent():
    cal = pd.DataFrame([
        _row("EUR", "cpi_yoy", AS_OF - pd.Timedelta(days=5)),
        _row("JPY", "cpi_yoy", AS_OF - pd.Timedelta(days=5)),
    ])
    out = per_currency_indicator_freshness(cal, IND_CFG, AS_OF, currencies=["EUR", "JPY"])
    report = currency_freshness_report(out)
    assert report["any_stale"] is False
    assert report["stale_currencies"] == []


# ---------------------------------------------------------------------------
# jb_raw cross-check (real retained payloads, not pinned to exact values)
# ---------------------------------------------------------------------------

def test_jb_raw_check_distinguishes_available_vs_absent():
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.econ_calendar_ff import parse_jblanked_range
    from src.ff_scoring import build_matcher

    raw_dir = Path(__file__).resolve().parents[1] / "data" / "jb_raw"
    if not raw_dir.exists() or not list(raw_dir.glob("jb_range_*.json")):
        pytest.skip("no retained jb_raw payloads in this checkout")

    stale_rows = pd.DataFrame([
        {"currency": "USD", "indicator_key": "core_cpi"},
        {"currency": "USD", "indicator_key": "nonexistent_indicator_key"},
    ])
    parquet_last_dates = {
        ("USD", "core_cpi"): pd.Timestamp("2026-06-10"),
        ("USD", "nonexistent_indicator_key"): pd.Timestamp("2026-06-10"),
    }
    res = check_pending_actuals_in_jb_raw(raw_dir, parse_jblanked_range, build_matcher,
                                          stale_rows, parquet_last_dates)
    # a made-up indicator_key can never match anything -> always "no_newer_data"
    assert res[("USD", "nonexistent_indicator_key")]["status"] == "no_newer_data"
    assert res[("USD", "core_cpi")]["status"] in {"actual_available_not_ingested", "no_newer_data"}
