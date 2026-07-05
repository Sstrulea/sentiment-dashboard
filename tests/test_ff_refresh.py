"""Phase 3 — FF production refresh: merge/dedup, anti-degradation guards, source flag,
FRED cross-check quarantine. No network (fetchers injected)."""
from __future__ import annotations

import pandas as pd
import pytest

import src.ff_refresh as R
from src.econ_calendar_ff import CANON_COLUMNS
from src.ff_fred_crosscheck import _fred_value, crosscheck_us

NOW = pd.Timestamp("2026-07-05")


def _canon_row(cid, ccy, dt, actual, released=True):
    return {"canonical_id": cid, "currency": ccy, "name_raw": "raw", "name_canonical": "CPI y/y",
            "datetime_utc": pd.Timestamp(dt), "actual": actual, "forecast": actual - 0.1,
            "previous": actual - 0.2, "released": released, "source": "ff"}


def _frame(rows):
    return pd.DataFrame(rows, columns=CANON_COLUMNS)


# --- merge/dedup (history-preserving, last-write-wins) ----------------------

def test_merge_appends_new_and_updates_revised():
    existing = _frame([_canon_row("usd_cpi", "USD", "2026-05-10", 3.0),
                       _canon_row("usd_cpi", "USD", "2026-06-10", 3.2)])
    weekly = _frame([_canon_row("usd_cpi", "USD", "2026-06-10", 3.3),   # revised actual
                     _canon_row("usd_cpi", "USD", "2026-07-10", 3.1)])   # new print
    out = R.merge_weekly(existing, weekly)
    assert len(out) == 3                                        # 2 + 1 new, revised collapsed
    jun = out[out["datetime_utc"] == pd.Timestamp("2026-06-10")].iloc[0]
    assert jun["actual"] == pytest.approx(3.3)                  # last-write-wins (revision)


def test_merge_into_empty():
    weekly = _frame([_canon_row("eur_cpi", "EUR", "2026-07-01", 2.8)])
    out = R.merge_weekly(None, weekly)
    assert len(out) == 1 and list(out.columns) == CANON_COLUMNS


# --- anti-degradation guards (keep last-good) -------------------------------

def _good_weekly():
    return _frame([_canon_row(f"c{i}", c, "2026-07-06", 1.0)
                   for i, c in enumerate(["USD", "EUR", "GBP", "JPY", "AUD"])])


def _write_existing(tmp_path):
    p = tmp_path / "ff.parquet"
    _frame([_canon_row("usd_cpi", "USD", "2026-06-10", 3.2)]).to_parquet(p, index=False)
    return p


def test_guard_fetch_failed_keeps_last_good(tmp_path, monkeypatch):
    p = _write_existing(tmp_path)
    monkeypatch.setattr(R, "parse_ff_weekly", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("HTTP 500")))
    rep = R.refresh(now_utc=NOW, cfg={"run_fred_crosscheck": False}, parquet_path=p)
    assert rep["status"] == "fetch_failed"
    assert len(pd.read_parquet(p)) == 1                        # unchanged


def test_guard_empty_payload_quarantines(tmp_path, monkeypatch):
    p = _write_existing(tmp_path)
    monkeypatch.setattr(R, "parse_ff_weekly", lambda *a, **k: _frame([]))
    rep = R.refresh(now_utc=NOW, cfg={"run_fred_crosscheck": False}, parquet_path=p)
    assert rep["status"] == "empty"
    assert len(pd.read_parquet(p)) == 1


def test_guard_thin_payload_quarantines(tmp_path, monkeypatch):
    p = _write_existing(tmp_path)
    thin = _frame([_canon_row("usd_cpi", "USD", "2026-07-06", 1.0),
                   _canon_row("eur_cpi", "EUR", "2026-07-06", 1.0)])   # only 2 ccy < 4
    monkeypatch.setattr(R, "parse_ff_weekly", lambda *a, **k: thin)
    rep = R.refresh(now_utc=NOW, cfg={"ff_min_currencies": 4, "run_fred_crosscheck": False}, parquet_path=p)
    assert rep["status"] == "thin"
    assert len(pd.read_parquet(p)) == 1


def test_good_payload_merges(tmp_path, monkeypatch):
    p = _write_existing(tmp_path)
    monkeypatch.setattr(R, "parse_ff_weekly", lambda *a, **k: _good_weekly())
    rep = R.refresh(now_utc=NOW, cfg={"ff_min_currencies": 4, "run_fred_crosscheck": False}, parquet_path=p)
    assert rep["status"] == "ok"
    assert len(pd.read_parquet(p)) == 6                        # 1 existing + 5 new


# --- source flag (ff vs mt5 rollback) ---------------------------------------

def test_calendar_source_flag_and_rollback(tmp_path):
    cfg_ff = tmp_path / "p.yaml"; cfg_ff.write_text("calendar_source: ff\n")
    cfg_mt5 = tmp_path / "q.yaml"; cfg_mt5.write_text("calendar_source: mt5\n")
    assert R.calendar_source(R.load_pipeline_config(cfg_ff)) == "ff"
    assert R.calendar_source(R.load_pipeline_config(cfg_mt5)) == "mt5"
    assert R.calendar_source({}) == "ff"                       # default


# --- FRED cross-check -------------------------------------------------------

def test_fred_value_transforms():
    idx = pd.DataFrame({"date": pd.date_range("2025-06-01", periods=13, freq="MS"),
                        "value": [100 + i for i in range(13)]})
    # yoy_pct for 2026-06: (112/100 - 1)*100 = 12
    assert _fred_value(idx, "yoy_pct", pd.Timestamp("2026-06-15")) == pytest.approx(12.0)
    # mom_diff for 2026-06: 112 - 111 = 1
    assert _fred_value(idx, "mom_diff", pd.Timestamp("2026-06-15")) == pytest.approx(1.0)


def test_crosscheck_quarantines_only_mismatch():
    ff = _frame([
        {"canonical_id": "usd_cpi", "currency": "USD", "name_raw": "CPI y/y",
         "name_canonical": "CPI y/y", "datetime_utc": pd.Timestamp("2026-06-10"),
         "actual": 4.2, "forecast": 4.2, "previous": 4.0, "released": True, "source": "ff"},
    ])
    # FRED CPI index giving y/y ≈ 4.2 for 2026-06 → within tol, no quarantine
    good = pd.DataFrame({"date": pd.date_range("2025-06-01", periods=13, freq="MS"),
                         "value": [100 * (1.042 ** (i / 12)) for i in range(13)]})
    q = crosscheck_us(ff, fetcher=lambda sid: good)
    assert q.empty
    # FRED giving y/y ≈ 1.0 (gross disagreement) → quarantine
    bad = pd.DataFrame({"date": pd.date_range("2025-06-01", periods=13, freq="MS"),
                        "value": [100 * (1.01 ** (i / 12)) for i in range(13)]})
    q2 = crosscheck_us(ff, fetcher=lambda sid: bad)
    assert len(q2) == 1 and q2.iloc[0]["indicator_key"] == "cpi_yoy"
