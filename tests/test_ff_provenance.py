"""Audit 2026-09-23, 2.1/Z5 — provenance migration from files on disk (src/ff_provenance.py)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from src.econ_calendar_ff import CANON_COLUMNS
from src.ff_provenance import backfill, ensure_provenance


def _pq(rows):
    return pd.DataFrame([{"canonical_id": c, "currency": ccy, "name_raw": n, "name_canonical": n,
                          "datetime_utc": pd.Timestamp(dt), "actual": a, "forecast": f,
                          "previous": 1.0, "released": True, "source": "ff"}
                         for c, ccy, n, dt, a, f in rows])


WEEKLY = [("2026-09-01", [
    {"title": "CPI m/m", "country": "CHF", "date": "2026-09-03T08:30:00+02:00", "forecast": "0.0%", "previous": "0.1%"},
    {"title": "BusinessNZ Services Index", "country": "NZD", "date": "2026-09-14T10:30:00+12:00",
     "forecast": "", "previous": "50.0"}])]


def test_ff_blank_ff_unknown_origins_and_override():
    pq = _pq([("chf_cpi", "CHF", "CPI m/m", "2026-09-03 06:30", 0.4, 0.0),
              ("nzd_businessnz_services_index", "NZD", "BusinessNZ Services Index",
               "2026-09-13 22:30", 52.1, 0.0),
              ("aud_import_prices", "AUD", "Import Prices q/q", "2026-07-30 01:30", 0.0, 0.0),
              ("usd_cpi", "USD", "CPI y/y", "2026-05-12 12:30", 3.0, 2.9)])
    ov = [{"canonical_id": "aud_import_prices", "date": "2026-07-30", "forecast_origin": "ff",
           "forecast": 0.0, "evidence": "FF page"}]
    b = backfill(pq, WEEKLY, [], ov).set_index("canonical_id")
    assert (b.loc["chf_cpi", "forecast_origin"], b.loc["chf_cpi", "forecast"]) == ("ff", 0.0)
    assert b.loc["nzd_businessnz_services_index", "forecast_origin"] == "ff_blank"
    assert np.isnan(b.loc["nzd_businessnz_services_index", "forecast"])
    assert b.loc["aud_import_prices", "forecast_origin"] == "ff"      # documented override
    assert b.loc["usd_cpi", "forecast_origin"] == "unknown"           # no evidence on disk
    assert b.loc["usd_cpi", "forecast"] == 2.9                        # value untouched


def test_jb_status_is_per_event_not_inherited_from_a_dst_sibling():
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
    b = backfill(pq, [], jb).sort_values("datetime_utc")
    assert b["jb_status"].tolist() == ["Data Not Loaded", "Good Data"]


def test_ensure_provenance_reads_disk_only_and_is_idempotent(tmp_path):
    ff_raw, jb_raw = tmp_path / "ff_raw", tmp_path / "jb_raw"
    ff_raw.mkdir(); jb_raw.mkdir()
    (ff_raw / "ff_weekly_2026-09-01.json").write_text(json.dumps(WEEKLY[0][1]))
    p = tmp_path / "ff.parquet"
    _pq([("chf_cpi", "CHF", "CPI m/m", "2026-09-03 06:30", 0.4, 0.0)]).to_parquet(p)
    assert ensure_provenance(p, ff_raw, jb_raw, tmp_path / "none.json")
    once = pd.read_parquet(p)
    assert list(once.columns) == CANON_COLUMNS and once["forecast_origin"].tolist() == ["ff"]
    (ff_raw / "ff_weekly_2026-09-01.json").unlink()        # snapshot left retention
    assert not ensure_provenance(p, ff_raw, jb_raw, tmp_path / "none.json")
    pd.testing.assert_frame_equal(pd.read_parquet(p), once)
