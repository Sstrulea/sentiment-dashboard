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


# --- field-aware merge (A2) --------------------------------------------------

def test_merge_field_aware_actual_survives_actualless_redelivery():
    """A2: the daily JBlanked pull wrote the actual in the evening; the next-day
    hourly faireconomy tick re-delivers the SAME event without an actual (the
    weekly feed is structurally actual-less). The actual must survive the
    re-merge; the schedule fields (forecast/previous) still update."""
    evening = _canon_row("usd_cpi", "USD", "2026-07-14 12:30", 3.7)
    redelivery = _canon_row("usd_cpi", "USD", "2026-07-14 12:30", float("nan"))
    redelivery["forecast"], redelivery["previous"] = 3.9, 3.5
    out = R.merge_weekly(_frame([evening]), _frame([redelivery]))
    assert len(out) == 1
    row = out.iloc[0]
    assert row["actual"] == pytest.approx(3.7)      # non-null actual never nulled out
    assert row["forecast"] == pytest.approx(3.9)    # other fields keep last-write-wins
    assert row["previous"] == pytest.approx(3.5)


def test_merge_field_aware_incoming_actual_still_wins():
    # a JB actual lands on a pre-existing actual-less schedule row…
    sched = _canon_row("usd_cpi", "USD", "2026-07-14 12:30", float("nan"))
    jb = _canon_row("usd_cpi", "USD", "2026-07-14 12:30", 3.8)
    out = R.merge_weekly(_frame([sched]), _frame([jb]))
    assert out.iloc[0]["actual"] == pytest.approx(3.8)
    # …and a genuine revision (non-null over non-null) still takes the new value
    out2 = R.merge_weekly(_frame([jb]), _frame([_canon_row("usd_cpi", "USD", "2026-07-14 12:30", 3.9)]))
    assert out2.iloc[0]["actual"] == pytest.approx(3.9)


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
    rep = R.refresh(now_utc=NOW, cfg={"run_fred_crosscheck": False, "archive_ff_weekly": False}, parquet_path=p)
    assert rep["status"] == "fetch_failed"
    assert len(pd.read_parquet(p)) == 1                        # unchanged


def test_guard_empty_payload_quarantines(tmp_path, monkeypatch):
    p = _write_existing(tmp_path)
    monkeypatch.setattr(R, "parse_ff_weekly", lambda *a, **k: _frame([]))
    rep = R.refresh(now_utc=NOW, cfg={"run_fred_crosscheck": False, "archive_ff_weekly": False}, parquet_path=p)
    assert rep["status"] == "empty"
    assert len(pd.read_parquet(p)) == 1


def test_guard_thin_payload_quarantines(tmp_path, monkeypatch):
    p = _write_existing(tmp_path)
    thin = _frame([_canon_row("usd_cpi", "USD", "2026-07-06", 1.0),
                   _canon_row("eur_cpi", "EUR", "2026-07-06", 1.0)])   # only 2 ccy < 4
    monkeypatch.setattr(R, "parse_ff_weekly", lambda *a, **k: thin)
    rep = R.refresh(now_utc=NOW, cfg={"ff_min_currencies": 4, "run_fred_crosscheck": False, "archive_ff_weekly": False}, parquet_path=p)
    assert rep["status"] == "thin"
    assert len(pd.read_parquet(p)) == 1


def test_good_payload_merges(tmp_path, monkeypatch):
    p = _write_existing(tmp_path)
    monkeypatch.setattr(R, "parse_ff_weekly", lambda *a, **k: _good_weekly())
    rep = R.refresh(now_utc=NOW, cfg={"ff_min_currencies": 4, "run_fred_crosscheck": False, "archive_ff_weekly": False}, parquet_path=p)
    assert rep["status"] == "ok"
    assert len(pd.read_parquet(p)) == 6                        # 1 existing + 5 new


# --- HOTFIX 2026-07-29: mass-NaN forecast guard (Patch A degrades cells, not raises) ---

def test_guard_mass_row_failures_quarantines_and_keeps_last_good(tmp_path, monkeypatch):
    # Patch A2 isolates per-row parse failures inside _canonicalize (a single bad
    # cell no longer aborts the payload), but a feed-wide format change producing
    # MORE row failures than mapped rows must still fall back to last-good.
    p = _write_existing(tmp_path)
    weekly = _good_weekly()                             # 5 mapped rows
    monkeypatch.setattr(R, "parse_ff_weekly", lambda *a, **k: weekly)
    import src.econ_calendar_ff as E
    monkeypatch.setattr(E, "ff_row_failures", lambda: {"USD/x": 10})   # 10 > 5 mapped
    rep = R.refresh(now_utc=NOW, cfg={"ff_min_currencies": 4, "run_fred_crosscheck": False, "archive_ff_weekly": False}, parquet_path=p)
    assert rep["status"] == "degraded"
    assert len(pd.read_parquet(p)) == 1                        # unchanged


def test_guard_few_row_failures_does_not_quarantine(tmp_path, monkeypatch):
    p = _write_existing(tmp_path)
    weekly = _good_weekly()                             # 5 mapped rows
    monkeypatch.setattr(R, "parse_ff_weekly", lambda *a, **k: weekly)
    import src.econ_calendar_ff as E
    monkeypatch.setattr(E, "ff_row_failures", lambda: {"USD/x": 1})    # 1 < 5 mapped
    rep = R.refresh(now_utc=NOW, cfg={"ff_min_currencies": 4, "run_fred_crosscheck": False, "archive_ff_weekly": False}, parquet_path=p)
    assert rep["status"] == "ok"
    assert len(pd.read_parquet(p)) == 6


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


# --- FAZA 2 (fix/alert-noise-and-ff-archive): raw weekly archive hook -------

def test_archive_hook_fires_when_enabled(tmp_path, monkeypatch):
    """Wiring check only (no network): archive_ff_weekly=True must call
    fetch_and_archive_weekly exactly once, with the configured URL."""
    p = _write_existing(tmp_path)
    monkeypatch.setattr(R, "parse_ff_weekly", lambda *a, **k: _good_weekly())
    calls = []
    import src.ff_raw_archive as A
    monkeypatch.setattr(A, "fetch_and_archive_weekly",
                        lambda url, **k: calls.append(url) or {"status": "saved", "path": "x", "rotated_out": []})
    rep = R.refresh(now_utc=NOW, cfg={"ff_min_currencies": 4, "run_fred_crosscheck": False,
                                      "archive_ff_weekly": True}, parquet_path=p)
    assert rep["status"] == "ok"
    assert len(calls) == 1


def test_archive_hook_failure_never_breaks_refresh(tmp_path, monkeypatch):
    """The archive call raising must not affect refresh()'s own report or
    the merge — mirrors the FRED cross-check block's own fail-open shape."""
    p = _write_existing(tmp_path)
    monkeypatch.setattr(R, "parse_ff_weekly", lambda *a, **k: _good_weekly())
    import src.ff_raw_archive as A
    monkeypatch.setattr(A, "fetch_and_archive_weekly",
                        lambda url, **k: (_ for _ in ()).throw(RuntimeError("network down")))
    rep = R.refresh(now_utc=NOW, cfg={"ff_min_currencies": 4, "run_fred_crosscheck": False,
                                      "archive_ff_weekly": True}, parquet_path=p)
    assert rep["status"] == "ok"
    assert len(pd.read_parquet(p)) == 6
