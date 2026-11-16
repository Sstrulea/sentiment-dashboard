"""fix/sources-compliance: the once-a-day gates (workflow helper and the market stage) and the licence notes of the sources kept."""
from __future__ import annotations

from datetime import datetime, timezone

import yaml

from src import cb_collect as cc
from src import daily_gate as G

T = lambda h, m=0, d=16: datetime(2026, 11, d, h, m, tzinfo=timezone.utc)      # noqa: E731


def test_daily_gate_once_a_day_after_the_hour(tmp_path):
    st = tmp_path / "gate.json"
    assert not G.due("fred", "21:00", T(20, 59), st)
    assert G.due("fred", "21:00", T(21, 5), st)
    G.mark("fred", T(21, 6), st)
    assert not G.due("fred", "21:00", T(23, 5), st) and G.due("fred", "21:00", T(21, 5, d=17), st)


def test_market_sources_under_a_personal_licence_are_asked_once_a_day_after_settlement(tmp_path, monkeypatch):
    cfg = {"sources": {"eurex_x": {"adapter": "Nope", "daily_after_utc": "06:30"}}}
    paths = cc.Paths(tmp_path / "cb")
    reps = cc.run(paths, now=lambda: T(5, 0), cfg=cfg)
    assert reps[0].status == "skipped" and "not yet" in reps[0].note                         # before settlement: no request
    reps = cc.run(paths, now=lambda: T(7, 0), cfg=cfg)
    assert reps[0].status == "FAILED"                                                          # after it: the adapter is called (here: unknown -> init fails)
    state = cc.load_state(paths)
    state[cc.DAILY_KEY] = {"eurex_x": "2026-11-16"}
    cc.save_state(paths, state)
    reps = cc.run(paths, now=lambda: T(12, 0), cfg=cfg)
    assert reps[0].status == "skipped" and "already fetched today" in reps[0].note


def test_kept_sources_carry_their_licence_note_and_daily_time():
    src = yaml.safe_load(open("config/cb_sources.yaml"))["sources"]
    for sid in ("asx_ib", "asx_bb", "mx_corra", "jpx_tona", "atlantafed_mpt") + tuple(k for k in src if k.startswith("eurex_ois_")):
        assert src[sid]["daily_after_utc"] and src[sid]["license_note"], sid
    pipe = yaml.safe_load(open("config/pipeline.yaml"))
    assert "electronic retrieval system" in pipe["cboe_pc"]["license_note"] and pipe["ff_min_interval_minutes"] == 60


def test_retail_collection_is_stopped():
    wf = open(".github/workflows/retail.yml").read()
    assert "schedule:" not in wf and "if: false" in wf
    assert "_retail()" not in open("src/main.py").read().split("def _daily")[1].split("def _retail")[0] if "def _daily" in open("src/main.py").read() else True
