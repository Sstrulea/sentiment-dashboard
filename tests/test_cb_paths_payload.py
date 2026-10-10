"""Stage 3 payload (pure): the 12-month implied path per bank (`path_json`), its 12-month end point, the same meetings 5 / 15
business days earlier, the change of the 12-month level, and the nearest decision of all banks (`next_decision`). On the engine
fixture frozen at 2026-10-08 (tests/fixtures/cb_engine_1008): Eurex history starts that day, MX / ASX on 2026-09-18."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.cb_compute import analysis as A
from src.cb_compute import engine as E
from src.cb_compute import payload as P
from src.cb_loader import load_context, load_pair_defs

FIX = Path(__file__).parent / "fixtures" / "cb_engine_1008"
D = date
ASOF = D(2026, 10, 8)


@pytest.fixture(scope="module")
def ctx():
    return load_context(FIX)


@pytest.fixture(scope="module")
def built(ctx):
    return P.build(ctx, ASOF, load_pair_defs())


def paths(built):
    return built["overview"]["paths"]


def test_points_are_the_meetings_of_the_next_12_months(ctx, built):
    usd = paths(built)["USD"]
    meetings = [p["meeting"] for p in usd["points"]]
    upcoming = [m.decision for m in ctx.meetings["USD"] if m.decision > ASOF]
    assert meetings == [d.isoformat() for d in upcoming if d <= ASOF + timedelta(days=365)]
    assert meetings[-1] == "2027-09-15" and "2027-10-27" not in meetings                    # 27 Oct 2027 is 384 days out
    assert set(usd) >= {"ccy", "asof", "points", "m12", "current", "history", "delta", "next"} and usd["asof"] == "2026-10-08"
    p0 = usd["points"][0]
    assert set(p0) >= {"meeting", "effective", "rate", "cum_bp", "step_bp", "flag", "method", "est", "prob", "na"}
    assert p0["flag"] == "CURVE" and p0["est"] is False and p0["na"] is None


def test_probability_only_for_exact_and_curve(built):
    usd, nzd, cad = (paths(built)[c] for c in ("USD", "NZD", "CAD"))
    p0 = usd["points"][0]
    assert p0["prob"]["dir"] == "hike" and p0["prob"]["n"] == 0 and p0["prob"]["p"] == pytest.approx(p0["step_bp"] / 25, abs=1e-3)
    assert all(p["prob"] is None and p["est"] for p in nzd["points"] if p["rate"] is not None)          # ESTIMATE: no probability
    assert cad["points"][0]["flag"] == "EXACT" and cad["points"][0]["prob"] is not None
    assert all(p["prob"] is None for p in cad["points"] if p["flag"] == "ESTIMATE")
    first = nzd["points"][0]
    assert first["rate"] is None and first["na"] == "not covered by any contract"


def test_m12_is_the_last_meeting_with_a_value_and_moves_are_cum_over_25(built):
    for ccy, pj in paths(built).items():
        valued = [p for p in pj["points"] if p["rate"] is not None]
        m = pj["m12"]
        assert m["meeting"] == valued[-1]["meeting"], ccy
        assert m["cum_bp"] == valued[-1]["cum_bp"] and m["moves"] == round(valued[-1]["cum_bp"] / 25, 2), ccy
        assert m["est"] == (valued[-1]["flag"] == "ESTIMATE"), ccy


def test_current_rate_and_overnight_benchmark(built):
    usd = paths(built)["USD"]["current"]
    assert (usd["rate"], usd["lower"], usd["upper"], usd["range"]) == (3.875, 3.75, 4.0, True)               # the Fed as a range
    assert usd["benchmark"]["id"] == "fred:EFFR" and usd["benchmark"]["name"] == "Effective federal funds rate" and usd["benchmark"]["value"] == 3.88
    eur = paths(built)["EUR"]["current"]
    assert eur["range"] is False and eur["lower"] is None and eur["benchmark"]["id"] == "ecb:ESTR"
    nzd = paths(built)["NZD"]["current"]["benchmark"]
    assert nzd["estimated"] and nzd["id"] is None and "estimated BKBM-OCR spread" in nzd["name"]


def test_history_is_na_with_the_reason_when_the_source_starts_later(built):
    usd = paths(built)["USD"]
    for k in ("1w", "3w"):
        assert usd["history"][k]["na"] == "history starts 2026-10-08" and usd["history"][k]["points"] == []
        assert usd["delta"][k]["v"] is None and usd["delta"][k]["na"] == "history starts 2026-10-08"
    cad = paths(built)["CAD"]
    assert cad["history"]["1w"]["na"] is None and cad["history"]["1w"]["asof"] == "2026-10-01"           # 5 business days, CA calendar
    assert cad["history"]["3w"]["na"] == "history starts 2026-09-18" and cad["history"]["3w"]["asof"] == "2026-09-16"   # 30 Sep is a CA holiday


def test_history_points_and_delta_on_the_same_meeting(ctx, built):
    cad = paths(built)["CAD"]
    prev = D(2026, 10, 1)
    tp = E.trajectory(ctx, "CAD", prev)
    hp = cad["history"]["1w"]["points"]
    assert [p["meeting"] for p in hp] == [p["meeting"] for p in cad["points"]]                            # only the current meetings
    for p in hp:
        q = tp.point_for(D.fromisoformat(p["meeting"]))
        assert p["rate"] == (None if q.rate is None else round(q.rate, 3))
    m = cad["m12"]["meeting"]
    now = E.trajectory(ctx, "CAD", ASOF).point_for(D.fromisoformat(m))
    then = tp.point_for(D.fromisoformat(m))
    d = cad["delta"]["1w"]
    assert d["meeting"] == m and d["prev"] == "2026-10-01" and d["v"] == pytest.approx(round((now.level - then.level) * 100, 2))


def test_next_decision_is_the_nearest_of_all_banks(ctx, built):
    nd = built["overview"]["next_decision"]
    assert (nd["ccy"], nd["decision"], nd["sort_utc"]) == ("NZD", "2026-10-28", "2026-10-28T01:00:00Z")    # 14:00 NZDT = 01:00Z, before the Fed / BoC
    assert nd["point"]["meeting"] == "2026-10-28" and nd["point"]["na"] == "not covered by any contract"
    assert nd["time"]["utc"] == "2026-10-28T01:00:00Z" and nd["href"] == "/central-banks/nzd.html"


def test_next_decision_boj_time_tbd_uses_the_start_of_its_window(ctx, built):
    sub = {c: paths(built)[c] for c in ("JPY", "AUD", "GBP")}                                             # 30 Oct BoJ before 3 Nov RBA, 5 Nov BoE
    nd = P.next_decision_json(ctx, sub, ASOF)
    assert nd["ccy"] == "JPY" and nd["time"]["tbd"] is True and nd["time"]["utc"] is None
    assert nd["sort_utc"] == "2026-10-30T02:30:00Z"                                                         # 11:30 JST, the start of the window
    assert nd["point"]["prob"] is not None and nd["point"]["meeting"] == "2026-10-30"
    assert P.next_decision_json(ctx, {}, ASOF) is None


def test_bank_json_carries_the_same_block_and_the_payload_is_deterministic(ctx, built):
    for ccy in built["banks"]:
        assert built["banks"][ccy]["path"] == built["overview"]["paths"][ccy]
    again = P.build(load_context(FIX), ASOF, load_pair_defs())
    for key in ("overview", "banks", "pairs"):
        assert json.dumps(again[key], sort_keys=True, allow_nan=False) == json.dumps(built[key], sort_keys=True, allow_nan=False)


def test_a_bank_without_a_market_path_has_reasons_everywhere(ctx):
    rep = A.bank_report(ctx, "CHF", ASOF)
    rep.trajectory.na_reason = "no market path for this currency"
    rep.trajectory.points = []
    pj = P.path_json(ctx, rep, ASOF)
    assert pj["points"] == [] and pj["m12"]["na"] == "no market path for this currency"
    assert pj["history"]["1w"]["na"] == pj["delta"]["3w"]["na"] == "no market path for this currency"


def test_rate_paths_js_on_the_real_cb_js():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["node", str(root / "tests" / "cb_js" / "test_rate_paths.cjs")], cwd=root, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_public_copies_are_not_behind_static():
    root = Path(__file__).resolve().parents[1]
    for f in ("cb.js", "style.css"):
        assert (root / "public" / f).read_bytes() == (root / "static" / f).read_bytes(), f


def test_path_carries_the_bank_name_without_the_decision_body(ctx, built):
    """v2 (RP style): the Upcoming meetings table shows the full name ("Bank of Canada"), from central_banks.yaml without the body."""
    names = {c: pj["name"] for c, pj in paths(built).items()}
    assert names == {"USD": "Federal Reserve", "EUR": "European Central Bank", "GBP": "Bank of England", "JPY": "Bank of Japan",
                     "CAD": "Bank of Canada", "AUD": "Reserve Bank of Australia", "NZD": "Reserve Bank of New Zealand", "CHF": "Swiss National Bank"}
    for c in names:
        assert built["banks"][c]["path"]["name"] == names[c]                                    # the bank JSON carries the same block
    again = P.build(ctx, ASOF, load_pair_defs())
    assert json.dumps(again["overview"]["paths"], sort_keys=True) == json.dumps(paths(built), sort_keys=True)   # deterministic


def test_rp_style_js_on_the_real_cb_js():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["node", str(root / "tests" / "cb_js" / "test_rp_style.cjs")], cwd=root, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_chart_legend_js_on_the_real_cb_js():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["node", str(root / "tests" / "cb_js" / "test_chart_legend.cjs")], cwd=root, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_bank_page_v3_js_on_the_real_cb_js():
    import subprocess
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(["node", str(root / "tests" / "cb_js" / "test_bank_page_v3.cjs")], cwd=root, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_rbnz_ocr_track_vs_market_at_year_end(ctx, built):
    """Bank page v3: RBNZ Q4 of the published OCR track vs the policy-equivalent rate in force after the last meeting of the year
    (the Fed rule, `year_end`); only years with both values; the market side is an ESTIMATE (BKBM-OCR spread)."""
    vm = built["banks"]["NZD"]["chart"]["bank"]["vs_market"]
    assert vm["note"] == P.OCR_VS_MARKET_NOTE
    rows = {r["year"]: r for r in vm["years"]}
    assert set(rows) == {2026, 2027}                                                   # Q4 of the track and a year-end level exist for both
    for y, r in rows.items():
        ye = built["banks"]["NZD"]["summary"]["end"][str(y)]
        q4 = next(q["value"] for q in built["banks"]["NZD"]["chart"]["bank"]["quarters"] if q["period"] == f"{y}Q4")
        assert r["bank"] == q4 and r["market"] == ye["rate"] and r["meeting"] == ye["meeting"]
        assert r["gap_bp"] == pytest.approx((ye["rate"] - q4) * 100, abs=0.05)
        assert r["flag"] == "ESTIMATE" and r["est"] is True
    assert rows[2026] == {"year": 2026, "bank": 2.8, "market": 3.187, "gap_bp": 38.7, "flag": "ESTIMATE", "est": True, "meeting": "2026-12-09"}
    assert rows[2027]["bank"] == 3.2 and rows[2027]["gap_bp"] == 83.7
    assert "vs_market" not in built["banks"]["USD"]["chart"]["bank"] and "vs_market" not in built["banks"]["EUR"]["chart"]["bank"]
    again = P.build(ctx, ASOF, load_pair_defs())
    assert again["banks"]["NZD"]["chart"]["bank"] == built["banks"]["NZD"]["chart"]["bank"]                      # deterministic


def test_ocr_vs_market_keeps_only_years_with_both_values(ctx, built):
    import copy
    rep = copy.deepcopy(A.bank_report(ctx, "NZD", ASOF))
    rep.bank_path.points = [p for p in rep.bank_path.points if p[0] != "2027Q4"]                 # no 2027 Q4 on the track
    rep.year_ends[2026].rate = None                                                              # no market level at end-2026
    assert P.ocr_vs_market_json(rep)["years"] == []
