"""The meeting of the as-of day (engine.is_upcoming): it stays in the trajectory until its decision row exists, because the global
as-of is the newest date of ANY source (ASX / JPX carry day T from ~08:40 UTC while Eurex is still at T-1 and MX until the evening).
Plus the bank's own data date for 1w / 3w (`data_asof`) and the `joint` of a meeting no contract isolates. Engine fixture frozen at
2026-10-08 (tests/fixtures/cb_engine_1008)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.cb_compute import analysis as A
from src.cb_compute import engine as E
from src.cb_compute import payload as P
from src.cb_loader import load_context, load_pair_defs

FIX = Path(__file__).parent / "fixtures" / "cb_engine_1008"
D = date
DAY = D(2026, 10, 28)                          # BoC 13:45 UTC, Fed 18:00 UTC, RBNZ 01:00 UTC


@pytest.fixture
def ctx():
    c = load_context(FIX)
    assert not any(r["meeting_date"] == DAY for rows in c.decisions.values() for r in rows)     # no decision row for 28 Oct in the fixture
    return c


def with_usd_decision(ctx):
    row = dict(ctx.decisions["USD"][-1], meeting_date=DAY, effective_date=D(2026, 10, 29), rate_before=3.875, rate_after=4.125,
               lower=4.0, upper=4.25, delta_bp=25.0, status="ff_pending")
    ctx.decisions["USD"] = ctx.decisions["USD"] + [row]
    return ctx


@pytest.mark.parametrize("cur", ["USD", "CAD", "NZD"])
def test_the_meeting_of_the_asof_day_stays_until_its_decision_row_exists(ctx, cur):
    tr = E.trajectory(ctx, cur, DAY)
    assert tr.points[0].meeting == DAY
    assert E.is_upcoming(ctx, cur, DAY, DAY) and E.upcoming_meetings(ctx, cur, DAY)[0].decision == DAY


def test_usd_prices_it_from_the_eurex_snapshot_of_8_october(ctx):
    tr = E.trajectory(ctx, "USD", DAY)
    p = tr.points[0]
    assert p.source == "eurex_ois_usd" and p.source_asof == D(2026, 10, 8) and p.flag == "CURVE"
    assert p.window[0] == D(2026, 10, 29)                                                     # boundary at the effective date
    assert p.extra["r_raw"] == pytest.approx(3.9322, abs=0.0001)                              # the same segment as on 8 Oct


def test_payload_next_rate_and_next_decision_contain_it(ctx):
    built = P.build(ctx, DAY, load_pair_defs())
    usd = built["overview"]["paths"]["USD"]
    assert usd["next"]["decision"] == "2026-10-28" and usd["points"][0]["meeting"] == "2026-10-28"
    assert built["banks"]["USD"]["summary"]["next"]["decision"] == "2026-10-28"
    assert built["banks"]["USD"]["summary"]["rate"]["value"] == 3.875 and built["banks"]["USD"]["summary"]["rate"]["pending"] is False
    nd = built["overview"]["next_decision"]
    assert nd["decision"] == "2026-10-28" and nd["ccy"] == "NZD"                             # 01:00Z, the earliest of the day


def test_with_its_decision_row_the_meeting_is_decided(ctx):
    with_usd_decision(ctx)
    tr = E.trajectory(ctx, "USD", DAY)
    assert DAY not in [p.meeting for p in tr.points] and tr.points[0].meeting == D(2026, 12, 9)
    assert tr.base.rate == 4.125 and tr.base.pending and tr.base.eff == D(2026, 10, 29)
    assert not E.is_upcoming(ctx, "USD", DAY, DAY)
    built = P.build(ctx, DAY, load_pair_defs(), currencies=("USD",))
    assert built["banks"]["USD"]["path"]["next"]["decision"] == "2026-12-09"
    assert built["banks"]["USD"]["summary"]["rate"]["pending"] is True and built["banks"]["USD"]["summary"]["last_decision"]["date"] == "2026-10-28"
    assert E.year_end(ctx, tr, 2026).flag != "DECIDED"                                        # 9 Dec is still to come


def test_a_history_trajectory_on_a_recorded_decision_day_excludes_that_meeting(ctx):
    rba = D(2026, 9, 29)
    assert E.is_decided(ctx, "AUD", rba)
    assert rba not in [p.meeting for p in E.trajectory(ctx, "AUD", rba).points]
    ctx.decisions["AUD"] = [r for r in ctx.decisions["AUD"] if r["meeting_date"] != rba]       # without its row it is still to come
    assert E.trajectory(ctx, "AUD", rba).points[0].meeting == rba


def test_history_is_counted_from_the_banks_own_data_date(ctx):
    rep = A.bank_report(ctx, "USD", D(2026, 10, 9))                                           # global as-of 9 Oct (ASX / JPX), Eurex still 8 Oct
    pj = P.path_json(ctx, rep, D(2026, 10, 9))
    assert (pj["data_asof"], pj["data_source"], pj["data_label"]) == ("2026-10-08", "eurex_ois_usd", "Eurex Clearing")
    assert pj["history"]["1w"]["asof"] == "2026-10-01" and pj["history"]["3w"]["asof"] == "2026-09-17"


def test_joint_points_at_the_estimate_that_carries_the_move(ctx):
    pj = P.build(ctx, D(2026, 10, 8), load_pair_defs(), currencies=("NZD",))["banks"]["NZD"]["path"]
    first = pj["points"][0]
    assert first["meeting"] == "2026-10-28" and first["na"] == E.NOT_COVERED
    nxt = pj["points"][1]
    assert first["joint"] == {"meeting": "2026-12-09", "step_bp": nxt["step_bp"], "cum_bp": nxt["cum_bp"]}
    assert nxt["includes"] == ["2026-10-28"] and all(p["joint"] is None for p in pj["points"][1:])
    assert "every NZD point is lower" in pj["spread_note"]
    assert pj["next"]["sort_utc"] == "2026-10-28T01:00:00Z" and pj["next"]["end_utc"] is None


def test_boj_next_has_the_end_of_its_window(ctx):
    pj = P.build(ctx, D(2026, 10, 8), load_pair_defs(), currencies=("JPY",))["banks"]["JPY"]["path"]
    assert pj["next"]["sort_utc"] == "2026-10-30T02:30:00Z" and pj["next"]["end_utc"] == "2026-10-30T04:30:00Z"


def test_decision_day_js_on_the_real_cb_js():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["node", str(root / "tests" / "cb_js" / "test_decision_day.cjs")], cwd=root, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
