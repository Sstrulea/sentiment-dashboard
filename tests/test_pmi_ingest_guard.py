"""FAZA 4 — country/local-time ingest guard (docs/proposal-pmi-ingest-guard.md,
src/pmi_ingest_guard.py). Synthetic fixtures only; the jb_raw regression
check (nothing normal gets quarantined today) lives in
test_pmi_ingest_guard_jb_raw_regression below.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.pmi_ingest_guard import country_hour_guard, DEFAULT_MIN_PRINTS


def _row(ccy, cid, dt, actual=50.0, released=True):
    return {"currency": ccy, "canonical_id": cid, "name_raw": cid, "name_canonical": cid,
           "datetime_utc": pd.Timestamp(dt), "actual": actual, "forecast": actual - 0.1,
           "previous": actual - 0.2, "released": released, "source": "ff"}


def _monthly_series(ccy, cid, n, hour_str, start="2024-01-01"):
    """n monthly released rows, all at the SAME UTC clock time (no DST games —
    used to build a clean trailing baseline before injecting a deviation)."""
    dates = pd.date_range(start, periods=n, freq="MS")
    return [_row(ccy, cid, f"{d.date()} {hour_str}") for d in dates]


def test_foreign_hour_is_quarantined():
    # GBP series at a steady 08:30 UTC (~London morning) for DEFAULT_MIN_PRINTS+2
    # releases, then one row at 14:45 UTC (a foreign-country hour) — must flag.
    rows = _monthly_series("GBP", "gbp_test_pmi", DEFAULT_MIN_PRINTS + 2, "08:30:00")
    next_month = pd.Timestamp(rows[-1]["datetime_utc"]) + pd.DateOffset(months=1)
    foreign_dt = next_month.replace(hour=14, minute=45)
    rows.append(_row("GBP", "gbp_test_pmi", foreign_dt))
    df = pd.DataFrame(rows)
    out = country_hour_guard(df, min_prints=DEFAULT_MIN_PRINTS)
    flagged = out[out["canonical_id"] == "gbp_test_pmi"]
    assert len(flagged) == 1
    assert flagged.iloc[0]["reason"] == "country_mismatch"
    assert flagged.iloc[0]["deviation_hours"] > 2.0


def test_correct_hour_passes():
    # A clean, steady-hour series never flags any of its own rows.
    rows = _monthly_series("CHF", "chf_test_pmi", DEFAULT_MIN_PRINTS + 6, "08:30:00")
    df = pd.DataFrame(rows)
    out = country_hour_guard(df, min_prints=DEFAULT_MIN_PRINTS)
    assert out[out["canonical_id"] == "chf_test_pmi"].empty


def test_dst_shift_is_not_a_false_positive():
    # CHF's own real pattern: 07:30 UTC in summer (CEST, local 09:30), 08:30 UTC
    # in winter (CET, local 09:30) — same LOCAL hour both seasons. A trailing
    # window straddling the DST boundary must not flag the shifted UTC row.
    dates_winter = pd.date_range("2024-01-01", periods=6, freq="MS")   # Jan-Jun: winter/spring
    dates_summer = pd.date_range("2024-07-01", periods=6, freq="MS")   # Jul-Dec: summer/autumn
    rows = [_row("CHF", "chf_dst_pmi", f"{d.date()} 08:30:00") for d in dates_winter]
    rows += [_row("CHF", "chf_dst_pmi", f"{d.date()} 07:30:00") for d in dates_summer]
    df = pd.DataFrame(rows)
    out = country_hour_guard(df, min_prints=DEFAULT_MIN_PRINTS)
    assert out[out["canonical_id"] == "chf_dst_pmi"].empty


def test_interest_rate_decision_excluded():
    # No fixed announcement hour -> excluded regardless of how erratic the times are.
    rows = [
        _row("USD", "usd_fed_interest_rate_decision", "2024-01-31 19:00:00"),
        _row("USD", "usd_fed_interest_rate_decision", "2024-03-20 18:00:00"),
        _row("USD", "usd_fed_interest_rate_decision", "2024-05-01 18:00:00"),
        _row("USD", "usd_fed_interest_rate_decision", "2024-06-12 18:00:00"),
        _row("USD", "usd_fed_interest_rate_decision", "2024-07-31 18:00:00"),
        _row("USD", "usd_fed_interest_rate_decision", "2024-09-18 18:00:00"),
        _row("USD", "usd_fed_interest_rate_decision", "2024-11-07 19:00:00"),
        _row("USD", "usd_fed_interest_rate_decision", "2024-12-18 19:00:00"),
        _row("USD", "usd_fed_interest_rate_decision", "2025-01-29 03:00:00"),  # wildly different hour
    ]
    df = pd.DataFrame(rows)
    out = country_hour_guard(df, min_prints=DEFAULT_MIN_PRINTS)
    assert out.empty


def test_thin_series_below_min_prints_is_skipped():
    rows = _monthly_series("JPY", "jpy_thin_pmi", 3, "00:30:00")
    rows.append(_row("JPY", "jpy_thin_pmi", "2024-04-01 12:00:00"))  # would deviate, but n too small
    df = pd.DataFrame(rows)
    out = country_hour_guard(df, min_prints=DEFAULT_MIN_PRINTS)
    assert out[out["canonical_id"] == "jpy_thin_pmi"].empty


def test_unreleased_rows_never_checked():
    rows = _monthly_series("EUR", "eur_test_pmi", DEFAULT_MIN_PRINTS + 2, "09:00:00")
    rows.append(_row("EUR", "eur_test_pmi", "2025-01-01 20:00:00", actual=float("nan"), released=False))
    df = pd.DataFrame(rows)
    out = country_hour_guard(df, min_prints=DEFAULT_MIN_PRINTS)
    assert out[out["canonical_id"] == "eur_test_pmi"].empty


def test_pmi_ingest_guard_jb_raw_regression():
    """Run the guard on the real, currently-retained data/jb_raw/ payloads
    (parsed through the production parser) and assert it quarantines nothing
    — today's normal ingest must pass clean."""
    import json
    from pathlib import Path
    from src.econ_calendar_ff import parse_jblanked_range

    raw_dir = Path(__file__).resolve().parents[1] / "data" / "jb_raw"
    payloads = sorted(raw_dir.glob("jb_range_*.json"))
    assert payloads, "no retained jb_raw payloads found — nothing to regression-test"

    for path in payloads:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            data = data.get("data") or list(data.values())[0]
        cal = parse_jblanked_range(data)
        if cal.empty:
            continue
        out = country_hour_guard(cal, min_prints=DEFAULT_MIN_PRINTS)
        assert out.empty, f"{path.name} unexpectedly quarantined rows: {out.to_dict('records')}"
