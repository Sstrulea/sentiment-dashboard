"""Audit 9B — COT: write/render only on a new report or a revision; Generated = fetched_at."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from src import fetch


def _frame(rows):
    return pd.DataFrame([{"cftc_contract_market_code": c, "report_date_as_yyyy_mm_dd": pd.Timestamp(d),
                          "open_interest_all": oi} for c, d, oi in rows])


BASE = [("099741", "2026-09-08", 100.0), ("099741", "2026-09-15", 110.0)]


def test_history_changed():
    a = _frame(BASE)
    assert not fetch.history_changed(a, _frame(list(reversed(BASE))))       # same data, other order
    assert fetch.history_changed(a, _frame(BASE + [("099741", "2026-09-22", 120.0)]))   # new report
    assert fetch.history_changed(a, _frame([BASE[0], ("099741", "2026-09-15", 111.0)]))  # revision
    assert fetch.history_changed(None, a)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    hist, meta = tmp_path / "history.parquet", tmp_path / "cot_meta.json"
    monkeypatch.setattr(fetch, "HISTORY_FILE", hist)
    monkeypatch.setattr(fetch, "META_FILE", meta)
    monkeypatch.setattr(fetch, "load_contracts", lambda: [{"cftc_code": "099741", "symbol": "EUR", "name": "Euro"}])
    _frame(BASE).to_parquet(hist, index=False)
    return hist, meta


def test_no_new_report_writes_nothing(paths, monkeypatch):
    hist, meta = paths
    before = hist.stat().st_mtime_ns
    monkeypatch.setattr(fetch, "fetch_all", lambda codes, since: _frame(BASE[1:]))
    df, changed = fetch.update_history(return_changed=True)
    assert changed is False and len(df) == 2
    assert hist.stat().st_mtime_ns == before and not meta.exists()


def test_new_report_writes_parquet_and_meta(paths, monkeypatch):
    hist, meta = paths
    monkeypatch.setattr(fetch, "fetch_all", lambda codes, since: _frame([("099741", "2026-09-22", 120.0)]))
    df, changed = fetch.update_history(return_changed=True)
    assert changed is True and len(df) == 3
    m = json.loads(meta.read_text())
    assert m["last_report_date"] == "2026-09-22" and m["fetched_at"].endswith("Z")


def test_weekly_exits_0_without_render_when_unchanged(monkeypatch):
    from src import main
    monkeypatch.setattr(fetch, "update_history", lambda return_changed=False: (_frame(BASE), False))
    import src.render as render
    monkeypatch.setattr(render, "render_dashboard", lambda *a, **k: pytest.fail("must not render"))
    assert main._weekly() == 0


def test_generated_is_fetched_at(tmp_path, monkeypatch):
    from src import render
    from src.compute import build_latest_snapshot
    df = pd.read_parquet(fetch.ROOT / "data" / "history.parquet")
    meta = tmp_path / "cot_meta.json"
    meta.write_text(json.dumps({"last_report_date": "2026-09-15", "fetched_at": "2026-09-19T21:21:07Z"}))
    monkeypatch.setattr(fetch, "META_FILE", meta)
    monkeypatch.setattr(render, "PUBLIC", tmp_path / "public")
    monkeypatch.setattr(render, "ARCHIVE", tmp_path / "public" / "archive")
    out = render.render_dashboard(build_latest_snapshot(df), df)
    assert "2026-09-19 21:21 UTC" in out.read_text()
