"""Audit 2026-09-23, 2.1/2.3 — provenance backfill (src/ff_provenance.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.econ_calendar_ff import CANON_COLUMNS
from src.ff_provenance import apply_backfill, build_backfill


def _pq(rows):
    return pd.DataFrame([{"canonical_id": c, "currency": ccy, "name_raw": n, "name_canonical": n,
                          "datetime_utc": pd.Timestamp(dt), "actual": a, "forecast": f,
                          "previous": 1.0, "released": True, "source": "ff"}
                         for c, ccy, n, dt, a, f in rows])


def test_ff_blank_ff_and_jb_origins_and_override():
    pq = _pq([("chf_cpi", "CHF", "CPI m/m", "2026-09-03 06:30", 0.4, 0.0),
              ("nzd_businessnz_services_index", "NZD", "BusinessNZ Services Index",
               "2026-09-13 22:30", 52.1, 0.0),
              ("aud_import_prices", "AUD", "Import Prices q/q", "2026-07-30 01:30", 0.0, 0.0),
              ("usd_cpi", "USD", "CPI y/y", "2026-05-12 12:30", 3.0, 2.9)])
    weekly = [("2026-09-01", [
        {"title": "CPI m/m", "country": "CHF", "date": "2026-09-03T08:30:00+02:00", "forecast": "0.0%", "previous": "0.1%"},
        {"title": "BusinessNZ Services Index", "country": "NZD", "date": "2026-09-14T10:30:00+12:00",
         "forecast": "", "previous": "50.0"}])]
    ov = [{"canonical_id": "aud_import_prices", "date": "2026-07-30", "forecast_origin": "ff",
           "forecast": 0.0, "evidence": "FF page"}]
    b = build_backfill(pq, weekly, [], ov).set_index("canonical_id")
    assert b.loc["chf_cpi", "forecast_origin"] == "ff" and b.loc["chf_cpi", "forecast"] == 0.0
    assert b.loc["nzd_businessnz_services_index", "forecast_origin"] == "ff_blank"
    assert np.isnan(b.loc["nzd_businessnz_services_index", "forecast"])
    assert b.loc["aud_import_prices", "forecast_origin"] == "ff"      # documented override
    assert b.loc["usd_cpi", "forecast_origin"] == "jb"                # before the FF archive


def test_jb_status_is_per_event_not_inherited_from_a_dst_sibling():
    """Archive DST duplicate: the 0.0 copy says Data Not Loaded, the real copy Good."""
    pq = _pq([("jpy_prelim_industrial_production", "JPY", "Prelim Industrial Production m/m",
               "2026-02-26 23:50", 1.8, 0.5),
              ("jpy_prelim_industrial_production", "JPY", "Prelim Industrial Production m/m",
               "2026-02-26 22:50", 0.0, 0.5)])
    jb = [("2026-03-01", [
        {"Name": "Prelim Industrial Production m/m", "Currency": "JPY", "Date": "2026.02.27 01:50:00",
         "Actual": 1.8, "Forecast": 0.5, "Previous": 0.1, "Quality": "Good Data", "Strength": "Strong Data"},
        {"Name": "Prelim Industrial Production m/m", "Currency": "JPY", "Date": "2026.02.27 00:50:00",
         "Actual": 0.0, "Forecast": 0.5, "Previous": 0.1, "Quality": "Data Not Loaded",
         "Strength": "Data Not Loaded"}])]
    b = build_backfill(pq, [], jb).sort_values("datetime_utc")
    assert b["jb_status"].tolist() == ["Data Not Loaded", "Good Data"]


def test_apply_backfill_is_idempotent():
    pq = _pq([("chf_cpi", "CHF", "CPI m/m", "2026-09-03 06:30", 0.4, 0.0)])
    b = pd.DataFrame([{"canonical_id": "chf_cpi", "datetime_utc": "2026-09-03 06:30",
                       "forecast_origin": "ff", "forecast": 0.0, "jb_status": "Bad Data"}])
    once = apply_backfill(pq, b)
    twice = apply_backfill(once, b.assign(forecast_origin="jb", forecast=9.9))
    assert list(once.columns) == CANON_COLUMNS
    pd.testing.assert_frame_equal(once, twice)
