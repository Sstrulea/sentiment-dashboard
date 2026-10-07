"""JBlanked /calendar/today/ pull (2026-10-07): the free daily request now covers
only /calendar/today/. One attempt per window (first tick after 20:30 UTC), never
retried; every attempt recorded; a payload without the needed fields is saved
and marked format_mismatch, not ingested. NO network: requests.get is patched
or the fetcher injected — a test that reached JBlanked would fail on the guard
below."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

import src.jb_actuals as J
from src.econ_calendar_ff import CANON_COLUMNS
from src.economic_render import _freshness

FIX = Path(__file__).parent / "fixtures" / "jb_range_raw_2026-10-03.json"
NOW = pd.Timestamp("2026-10-08 21:05")          # first tick after the 20:30 window
CFG = {"jb_today_not_before": "2026-10-08T20:30:00"}
NFP = "usd_nonfarm_payrolls"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import requests

    def boom(*a, **k):
        raise AssertionError("a test must never call JBlanked")
    monkeypatch.setattr(requests, "get", boom)


def _today_payload(extra_field=True):
    """The NFP day of the real 2026-10-03 payload, as /today would return it
    (+ the 'Trend' field JB added on 2026-10-06)."""
    events = [e for e in json.loads(FIX.read_text()) if e["Date"].startswith("2026.10.02")]
    if extra_field:
        events = [dict(e, Trend="Bullish") for e in events]
    return events


def _schedule(tmp_path):
    p = tmp_path / "ff.parquet"
    pd.DataFrame([{"canonical_id": NFP, "currency": "USD", "name_raw": "Non-Farm Employment Change",
                   "name_canonical": "Nonfarm Payrolls", "datetime_utc": pd.Timestamp("2026-10-02 12:30"),
                   "actual": float("nan"), "forecast": 89.0, "previous": 133.0, "released": True,
                   "source": "ff", "forecast_origin": "ff", "jb_status": None}],
                 columns=CANON_COLUMNS).to_parquet(p, index=False)
    return p


def _pull(tmp_path, fetcher, now=NOW, cfg=CFG, state=None):
    return J.pull_actuals(now_utc=now, parquet_path=tmp_path / "ff.parquet",
                          state_path=state or tmp_path / "s.json", raw_dir=tmp_path / "raw",
                          fetcher=fetcher, cfg=cfg)


def test_window_is_20_30_utc_and_one_attempt_per_window():
    assert J.attempt_window_start(pd.Timestamp("2026-10-08 21:05")) == pd.Timestamp("2026-10-08 20:30")
    assert J.attempt_window_start(pd.Timestamp("2026-10-09 10:00")) == pd.Timestamp("2026-10-08 20:30")
    tried = {"last_attempt_at": "2026-10-08T21:05:00", "last_status": "fetch_failed"}
    assert not J.should_attempt_today(pd.Timestamp("2026-10-08 22:05"), tried)   # failed: no retry
    assert not J.should_attempt_today(pd.Timestamp("2026-10-09 20:05"), tried)   # same window
    assert J.should_attempt_today(pd.Timestamp("2026-10-09 20:35"), tried)       # next window
    assert J.should_attempt_today(pd.Timestamp("2026-10-08 21:05"),
                                  {"last_attempt_at": "2026-10-07T08:30:29.892866"})


def test_not_before_holds_the_first_call():
    nb = pd.Timestamp("2026-10-08 20:30")
    assert not J.should_attempt_today(pd.Timestamp("2026-10-07 21:05"), {}, nb)
    assert J.should_attempt_today(pd.Timestamp("2026-10-08 21:05"), {}, nb)


def test_ok_ingests_and_ignores_extra_fields(tmp_path):
    _schedule(tmp_path)
    rep = _pull(tmp_path, lambda: json.dumps(_today_payload()))
    assert rep["status"] == "ok"
    out = pd.read_parquet(tmp_path / "ff.parquet")
    assert out.loc[out["canonical_id"] == NFP, "actual"].tolist() == [29.0]    # guard: forecast 89 = FF
    st = J.load_state(tmp_path / "s.json")
    assert st["last_status"] == "ok" and st["endpoint"] == "today"
    assert st["last_success_at"] == st["last_attempt_at"] == NOW.isoformat()
    assert len(list((tmp_path / "raw").glob("jb_today_*.json"))) == 1


def test_401_is_recorded_saved_and_not_retried(tmp_path):
    _schedule(tmp_path)
    body = '{"message":"This endpoint requires credits"}'

    def fail():
        J._last_response_meta = {"http_status": 401, "rate_limit_headers": {}, "body": body}
        raise RuntimeError("401 Client Error")
    rep = _pull(tmp_path, fail)
    assert rep == {"status": "fetch_failed", "http_status": 401}
    st = J.load_state(tmp_path / "s.json")
    assert st["last_status"] == "fetch_failed" and st["last_http_status"] == 401
    assert "requires credits" in st["last_error"] and "last_success_at" not in st
    assert [p.read_text() for p in (tmp_path / "raw").glob("jb_today_*.json")] == [body]
    # every later tick of the window skips without calling
    for h in (1, 2, 12, 23):
        rep = _pull(tmp_path, lambda: pytest.fail("retried"), now=NOW + pd.Timedelta(hours=h))
        assert rep["status"] == "skipped"


def test_format_mismatch_saves_and_does_not_ingest(tmp_path):
    p = _schedule(tmp_path)
    before = pd.read_parquet(p)
    bad = [{k: v for k, v in e.items() if k != "Actual"} for e in _today_payload()]
    rep = _pull(tmp_path, lambda: json.dumps(bad))
    assert rep["status"] == "format_mismatch"
    assert pd.read_parquet(p).equals(before)
    st = J.load_state(tmp_path / "s.json")
    assert st["last_status"] == "format_mismatch" and "Actual" in st["last_error"]
    assert len(list((tmp_path / "raw").glob("jb_today_*.json"))) == 1
    assert _pull(tmp_path, lambda: pytest.fail("retried"), now=NOW + pd.Timedelta(hours=1))["status"] == "skipped"
    rep = _pull(tmp_path, lambda: json.dumps({"events": []}), now=NOW + pd.Timedelta(days=1))
    assert rep["status"] == "format_mismatch"                              # not a list


def test_the_real_path_calls_today_never_range(tmp_path, monkeypatch):
    _schedule(tmp_path)
    seen = []
    monkeypatch.setenv("JBLANKED_API_KEY", "k" * 32)
    monkeypatch.setattr(J, "fetch_range_raw", lambda *a, **k: pytest.fail("range called"))

    def fake_today(*, api_key, url, timeout=30):
        seen.append(url)
        return json.dumps(_today_payload())
    monkeypatch.setattr(J, "fetch_today_raw", fake_today)
    cfg = dict(CFG, jb_today_url=J.DEFAULT_TODAY_URL)
    assert _pull(tmp_path, None, cfg=cfg)["status"] == "ok"
    assert seen == ["https://www.jblanked.com/news/api/forex-factory/calendar/today/"]


def test_main_uses_the_today_path(monkeypatch):
    calls = []
    monkeypatch.setattr(J, "pull_actuals", lambda **k: calls.append("today") or {"status": "skipped"})
    monkeypatch.setattr(J, "pull_actuals_range", lambda **k: pytest.fail("range called"))
    monkeypatch.setattr("sys.argv", ["jb_actuals"])
    assert J.main() == 0 and calls == ["today"]


def test_badge_carries_the_last_attempt(tmp_path, monkeypatch):
    sp = tmp_path / "jb_last_pull.json"
    monkeypatch.setattr(J, "STATE_JSON", sp)
    J.save_state({"last_success_at": "2026-10-05T01:06:15", "last_attempt_at": "2026-10-08T21:05:00",
                  "last_status": "fetch_failed", "last_http_status": 401}, sp)
    f = _freshness(as_of=pd.Timestamp("2026-10-08 22:00"))
    assert f["actuals_pull"]["stale"] is True
    assert f["actuals_pull"]["last_attempt"] == {"at": "2026-10-08T21:05:00", "status": "fetch_failed",
                                                 "http_status": 401}
