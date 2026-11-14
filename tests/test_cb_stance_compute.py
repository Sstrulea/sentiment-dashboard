"""Stance S1 (+ S1.1 corrections, cb_stance v3) - golden 8/8 (today + 12 months) on the frozen S0a inputs of 2026-11-14, edge cases, purity, append-only, payload,
display-only guard. The golden expectations are computed HERE with explicit arithmetic - never by calling stance.py."""
from __future__ import annotations

import json
import statistics
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src import cb_collect as cc
from src import cb_stance_history as SH
from src.cb_compute import stance as ST
from src.cb_compute.payload import stance_json

ROOT = Path(__file__).resolve().parents[1]
FIX = json.loads((ROOT / "tests" / "fixtures" / "cb_stance" / "s1_inputs_2026-11-14.json").read_text())
P = FIX["params"]
DELTA, EPS, TOL = P["situations"]["delta_pp"], P["situations"]["epsilon_pp"], P["boundary_tol_pp"]


def _val(d):
    return None if d is None else ST.Val(**d)


def inputs(cur: str) -> ST.BankInputs:
    b = dict(FIX["banks"][cur])
    for k in ("rate", "pi_under", "pi_head", "y_today", "u", "u_star", "pi_12m", "y_12m", "u_12m", "last_projection"):
        b[k] = _val(b.get(k))
    b["neutral"] = None if b.get("neutral") is None else ST.Neutral(**b["neutral"])
    b["band"] = tuple(b["band"]) if b.get("band") else None
    b["flags"] = set(b["flags"])
    return ST.BankInputs(**b)


# ------------------------------------------------------- explicit arithmetic --

def exp_gap(pi, star, band):
    if pi is None:
        return None
    if star is not None:
        return pi - star
    lo, hi = band
    return (pi - hi if pi > hi else 0.0) + (pi - lo if pi < lo else 0.0)


def exp_situation(x, g):
    if abs(x) <= EPS:
        return "S0" if abs(g) <= DELTA else "S2"
    d = 1 if x > 0 else -1
    return "S1" if d * g > DELTA else "S3"


def expected(cur: str) -> dict:
    b = FIX["banks"][cur]
    star, band = b["target_star"], b["band"]
    pi_u, pi_h = b["pi_under"]["v"], b["pi_head"]["v"]
    if b["slack_type"] == "output_gap":
        y = b["y_today"]["v"]
        y12 = b["y_12m"]["v"] if b.get("y_12m") else y
    elif b["slack_type"] == "unemployment_gap":
        y = -2.0 * (b["u"]["v"] - b["u_star"]["v"])
        y12 = -2.0 * (b["u_12m"]["v"] - b["u_star"]["v"]) if b.get("u_12m") else y
    else:
        y = y12 = None
    t, t12 = (y / 3 if y is not None else 0.0), (y12 / 3 if y12 is not None else 0.0)
    x = exp_gap(pi_u, star, band) + t
    xh = exp_gap(pi_h, star, band) + t
    pi12 = b["pi_12m"]["v"] if b.get("pi_12m") else None
    x12 = None if pi12 is None else exp_gap(pi12, star, band) + t12
    out = {"x": x, "x_headline": xh, "y": y, "x_12m": x12}
    n = b.get("neutral")
    if n is None:
        out.update(S=None, G=None, situation=None, situation_12m=None)
        return out
    S = b["rate"]["v"] - n["mid"]
    G = 1.5 * x - S
    out.update(S=S, S_lo=b["rate"]["v"] - n["hi"], S_hi=b["rate"]["v"] - n["lo"], G=G, situation=exp_situation(x, G),
               pivot=None if star is None else star + S / 1.5 - t)
    if x12 is not None:
        out.update(G_12m=1.5 * x12 - S, situation_12m=exp_situation(x12, 1.5 * x12 - S))
    return out


@pytest.mark.parametrize("cur", ST.BANKS)
def test_golden_today_and_12_months(cur):
    r = ST.compute(inputs(cur), P)
    e = expected(cur)
    for k, v in e.items():
        if v is None or isinstance(v, str):
            assert r[k] == v, (cur, k, r[k], v)
        else:
            assert r[k] == pytest.approx(v, abs=1e-12), (cur, k, r[k], v)


def test_golden_literal_values():
    """A few values written out by hand from the official inputs of 2026-11-14."""
    usd = ST.compute(inputs("USD"), P)
    assert usd["x"] == pytest.approx((3.1 - 2.0) + (-2.0 * (4.1 - 4.2)) / 3)                 # core PCE 3.1, UNRATE 4.1, u* 4.2
    assert usd["S"] == pytest.approx(3.875 - 3.2) and usd["situation"] == "S1"             # midpoint 3.75-4.00; SEP Table 1 longer-run median 3.2
    assert (usd["N"]["lo"], usd["N"]["hi"]) == (2.9, 3.9)                                   # Table 1 longer-run Range (S1.1 A3)
    assert usd["N"]["check"] == {"sep": "2026-09-16", "published": 3.2, "from_dots": 3.25, "rounded": 3.2, "dots": 18, "ok": True}
    assert usd["situation_12m"] == "S3" and "boundary" not in usd["flags_12m"]             # pre-registered: S3 without boundary
    gbp = ST.compute(inputs("GBP"), P)
    assert gbp["N"]["mid"] == pytest.approx(statistics.median([(2.75 + 3.0) / 2, 3.0]))     # Taylor 29 Sep 2026, Ramsden 5 Feb 2026
    assert (gbp["N"]["lo"], gbp["N"]["hi"]) == (2.75, 3.0)                                  # every declared bound (S1.1 A6)
    assert -0.55 <= gbp["G"] <= -0.30 and (gbp["situation"], gbp["direction"]) == ("S2", -1)
    nzd = ST.compute(inputs("NZD"), P)
    assert nzd["y"] == -1.4 and nzd["x"] == pytest.approx(0.4333, abs=5e-5)                 # MPS Sep 2026 output gap, March-year 2027 (S1.1 A5)
    assert inputs("NZD").y_today.asof == "M2027" and inputs("CAD").pi_12m.asof == "2027Q4"
    jpy = ST.compute(inputs("JPY"), P)
    assert (jpy["N"]["lo"], jpy["N"]["hi"]) == (pytest.approx(-0.9 + 2.0), pytest.approx(0.5 + 2.0))                 # real + target
    eur = ST.compute(inputs("EUR"), P)
    assert (eur["N"]["lo"], eur["N"]["hi"], eur["N"]["nominal_from"]) == (1.75, 2.25, "nominal")                    # nominal as published
    cad = ST.compute(inputs("CAD"), P)
    assert cad["pi_under"] == pytest.approx((1.8 + 1.9) / 2)                                # mean of CPI-trim and CPI-median, unrounded
    aud = ST.compute(inputs("AUD"), P)
    assert aud["N"]["mid"] == pytest.approx(statistics.median([4.193964989513948, 3.659528095190119, 4.416598826587419,
                                                               3.32158218543885, 3.169446731565178, 3.02024403284537]))


# --------------------------------------------------------------- edge cases ---

def test_chf_band_in_below_above():
    chf = inputs("CHF")
    assert ST.gap(1.0, chf) == 0.0 and ST.gap(-0.4, chf) == pytest.approx(-0.4) and ST.gap(2.7, chf) == pytest.approx(0.7)


def test_need_only_without_neutral():
    for cur in ("NZD", "CHF"):
        r = ST.compute(inputs(cur), P)
        assert r["situation"] is None and r["G"] is None and "need_only" in r["flags"] and r["x"] is not None and r["x_12m"] is not None


def test_boundary_and_neutral_sensitive():
    eur = ST.compute(inputs("EUR"), P)                                                     # x = 0.5 = epsilon exactly
    assert "boundary" in eur["flags"] and "neutral_sensitive" in eur["flags"]
    c = ST.classify(0.52, 1.0, DELTA, EPS)
    assert c["situation"] == "S1" and c["margin"] == pytest.approx(0.02)
    usd = ST.compute(inputs("USD"), P)
    assert "boundary" not in usd["flags"] and "neutral_sensitive" not in usd["flags"]


def test_neutral_stale_is_a_flag_not_na():
    eur = ST.compute(inputs("EUR"), P)
    assert eur["N"]["stale"] is True and "stale" in eur["flags"] and eur["S"] is not None and eur["situation"] is not None


def test_no_slack_and_slack_held():
    assert "no_slack" in ST.compute(inputs("EUR"), P)["flags"]
    jpy = ST.compute(inputs("JPY"), P)
    assert "slack_held" in jpy["flags"] and jpy["y_12m"] == jpy["y"]


@pytest.mark.parametrize("cur", ["CAD", "GBP", "NZD"])
def test_headline_basis(cur):
    r = ST.compute(inputs(cur), P)
    assert r["pi_12m_basis"] == "headline" and "headline_basis" in r["flags"]


@pytest.mark.parametrize("points, d, want", [
    ([{"horizon": "FY2026", "transform": "fiscal_year"}, {"horizon": "FY2027", "transform": "fiscal_year"}], date(2027, 11, 14), "FY2027"),
    ([{"horizon": "FY2026", "transform": "fiscal_year"}, {"horizon": "FY2027", "transform": "fiscal_year"}], date(2027, 3, 14), "FY2026"),
    ([{"horizon": "2026", "transform": "annual_average"}, {"horizon": "2027", "transform": "annual_average"}], date(2027, 11, 14), "2027"),
    ([{"horizon": "2027Q4", "transform": "yoy_quarter"}, {"horizon": "2027", "transform": "annual_average"}], date(2027, 11, 14), "2027Q4"),
    ([{"horizon": "M2027", "transform": "year_average_march"}, {"horizon": "M2028", "transform": "year_average_march"}], date(2027, 11, 14), "M2028"),
    ([{"horizon": "2026Q4", "transform": "quarter_level"}], date(2027, 11, 14), None),
])
def test_period_containing_as_of_plus_12_months(points, d, want):
    got = ST.pick_containing(points, d)
    assert (got or {}).get("horizon") == want


def test_single_period_rule_today_and_12_months():
    """cb_stance v3 period_rule: the shortest published period containing the date; for today, if none contains the as-of, the last
    period that has ended."""
    pts = [{"horizon": "2026Q2", "transform": "quarter_level", "value": -1.5}, {"horizon": "M2027", "transform": "year_average_march", "value": -1.4},
           {"horizon": "M2028", "transform": "year_average_march", "value": -0.9}]
    assert ST.pick_period(pts, date(2026, 11, 14), today=True)["horizon"] == "M2027"      # NZD: contains the as-of (v2 read 2026Q2)
    q = [{"horizon": "2026Q1", "transform": "quarter_level"}, {"horizon": "2026Q2", "transform": "quarter_level"}, {"horizon": "2025", "transform": "annual_average"}]
    assert ST.pick_period(q, date(2026, 11, 14), today=True)["horizon"] == "2026Q2"        # none contains it: the last ended
    assert ST.pick_period(q, date(2026, 11, 14)) is None                                   # 12 months: never the fallback
    tie = [{"horizon": "2026", "transform": "annual_average"}, {"horizon": "2026Q4", "transform": "quarter_level"}]
    assert ST.pick_period(tie, date(2027, 2, 1), today=True)["horizon"] == "2026Q4"        # same end: the shorter
    assert ST.pick_period(tie, date(2026, 11, 14), today=True)["horizon"] == "2026Q4"      # containing: the shorter
    assert ST.point_value({"low": -1.25, "high": -0.25}) == (-0.75, -1.25, -0.25)


def _est(member, published, point=None, low=None, high=None, basis="nominal"):
    return {"label": member, "member": member, "basis": basis, "point": point, "low": low, "high": high, "published": published}


def test_gbp_members_in_office_latest_statement_last_12_months():
    entry = {"status": "filled", "flag": "MEMBERS_VIEW", "estimates": [
        _est("A", date(2025, 7, 1), point=2.5),                                   # older than 12 months: out
        _est("B", date(2026, 2, 1), point=3.0), _est("B", date(2026, 9, 1), low=2.75, high=3.0),       # B: the latest only
        _est("C", date(2026, 6, 1), point=3.25),                                  # not in office: out
        _est("D", date(2026, 10, 1), point=0.75, basis="real")]}
    n, _ = ST.neutral_from_estimates("GBP", entry, 2.0, date(2026, 11, 14), {"A", "B", "D"}, 15)
    assert sorted(v for _, v in n.used) == [2.75, 2.875] and n.mid == pytest.approx(2.8125)
    assert (n.lo, n.hi) == (2.75, 3.0)                                       # S1.1 A6: min-max of every declared bound (B 2.75-3.0, D 0.75+2)


def test_real_to_nominal_vs_nominal_published():
    real = {"status": "filled", "flag": "STAFF_ESTIMATE", "estimates": [{"label": "r", "basis": "real", "low": -0.9, "high": 0.5, "published": date(2026, 3, 27)}]}
    n, _ = ST.neutral_from_estimates("JPY", real, 2.0, date(2026, 11, 14), set(), 15)
    assert (n.lo, n.hi, n.nominal_from) == (pytest.approx(1.1), pytest.approx(2.5), "real+target")
    both = {"status": "filled", "flag": "STAFF_ESTIMATE", "estimates": [
        {"label": "real", "role": "primary", "basis": "real", "low": -0.5, "high": 0.5, "published": date(2025, 2, 13)},
        {"label": "nom", "role": "primary_nominal", "basis": "nominal", "low": 1.75, "high": 2.25, "published": date(2025, 2, 13)}]}
    n, _ = ST.neutral_from_estimates("EUR", both, 2.0, date(2026, 11, 14), set(), 15)
    assert (n.lo, n.hi, n.nominal_from, n.stale) == (1.75, 2.25, "nominal", True)


def test_boj_lens_is_flagged_estimate():
    jpy = ST.compute(inputs("JPY"), P)
    assert jpy["lens"]["kind"] == "market" and jpy["lens"]["cutoff_flag"] == "ESTIMATE" and "estimate" in jpy["flags"]


class _Pt:
    def __init__(self, eff, rate, flag="CURVE"):
        self.eff, self.rate, self.flag, self.reason, self.meeting, self.cum_bp = eff, rate, flag, "", eff, 0.0


class _Tr:
    def __init__(self, pts, base=0.0):
        self.points, self.na_reason = pts, ""
        self.base = type("B", (), {"rate": base})()


def test_snb_constant_rate_lens(monkeypatch):
    tr = _Tr([_Pt(date(2026, 12, 11), 0.25), _Pt(date(2027, 3, 19), 0.5)])
    fc = {"series": [{"role": "rate_assumption", "values": [{"horizon": "all", "value": 0.0}]}]}
    out = ST.lens_inputs(None, "CHF", "constant_rate", fc, tr, date(2026, 11, 14), date(2027, 11, 14))
    assert out["delta_bp"] == pytest.approx(50.0) and out["assumed_rate"] == 0.0


def test_market_lens_without_history_at_cutoff_is_na():
    eur = ST.compute(inputs("EUR"), P)
    assert eur["lens"]["delta_bp"] is None and "no market history at the cutoff" in eur["lens"]["na"]


def test_rate_at_uses_the_last_meeting_effective_before_the_date():
    tr = _Tr([_Pt(date(2026, 12, 10), 4.0), _Pt(date(2027, 6, 1), 4.25), _Pt(date(2028, 1, 1), 4.5)], base=3.75)
    assert ST.rate_at(tr, date(2027, 11, 14))[0] == 4.25 and ST.rate_at(tr, date(2026, 11, 20))[0] == 3.75


def _hist(*pairs):
    return [(date.fromisoformat(d), v) for d, v in pairs]


def test_cycle_stages_from_the_full_history():
    """S1.1 A1: moves = changes in the history; date = the decision day when decisions.parquet has it, else the effective date (marked);
    moves after the last history observation from decisions.parquet; holds between moves do not break a stage."""
    D = date
    hist = _hist(("2024-01-01", 5.0), ("2024-06-12", 4.75), ("2024-09-18", 4.5), ("2025-09-18", 4.25), ("2025-10-30", 4.0), ("2026-01-05", 4.0),
                 ("2026-06-17", 4.25), ("2026-11-05", 4.25))
    decs = [{"meeting_date": D(2025, 9, 17), "effective_date": D(2025, 9, 18), "delta_bp": -25}, {"meeting_date": D(2025, 10, 29), "effective_date": D(2025, 10, 30), "delta_bp": -25},
            {"meeting_date": D(2026, 3, 18), "effective_date": D(2026, 3, 19), "delta_bp": 0}, {"meeting_date": D(2026, 6, 16), "effective_date": D(2026, 6, 17), "delta_bp": 25},
            {"meeting_date": D(2026, 9, 16), "effective_date": D(2026, 9, 17), "delta_bp": 0}, {"meeting_date": D(2026, 11, 10), "effective_date": D(2026, 11, 11), "delta_bp": 25}]
    c = ST.cycle_of(hist, decs, D(2026, 11, 14), "hist:x")
    assert (c["direction"], c["count"], c["first_move"], c["last_move"]) == ("hike", 2, "2026-06-16", "2026-11-10")
    assert [m["date_source"] for m in c["moves"]] == ["decision", "decision_recent"]          # 11-11 is after the last history day 11-05
    assert c["previous"] == {"direction": "cut", "count": 4, "first_move": "2024-06-12", "last_move": "2025-10-29"}
    assert c["holds_since"] == 0 and c["holds_from"] is None and c["history"] == {"series": "hist:x", "from": "2024-01-01", "to": "2026-11-05"}
    c = ST.cycle_of(hist, decs, D(2026, 10, 1))                                                # as-of: nothing after it is used
    assert (c["count"], c["holds_since"], c["last_move"]) == (1, 1, "2026-06-16")
    prev = ST.cycle_of(hist, decs, D(2026, 10, 1))["previous"]
    assert prev["count"] == 4 and ST.cycle_of(hist, decs, D(2024, 12, 1))["moves"][0]["date_source"] == "history_effective"


def test_cycle_holds_counted_from_the_first_decision_and_no_history():
    D = date
    hist = _hist(("2025-01-01", 1.0), ("2025-06-20", 0.0), ("2026-11-10", 0.0))
    decs = [{"meeting_date": D(2025, 9, 25), "effective_date": D(2025, 9, 26), "delta_bp": 0}, {"meeting_date": D(2025, 12, 11), "effective_date": D(2025, 12, 12), "delta_bp": 0}]
    c = ST.cycle_of(hist, decs, D(2026, 11, 14))
    assert (c["direction"], c["count"], c["holds_since"], c["holds_from"]) == ("cut", 1, 2, "2025-09-25")       # decisions start after the last move
    assert c["previous"] is None and "no earlier stage" in c["previous_na"]
    c = ST.cycle_of([], [{"meeting_date": D(2026, 1, 1), "effective_date": D(2026, 1, 2), "delta_bp": 25}], D(2026, 11, 14))
    assert (c["direction"], c["count"], c["moves"][0]["date_source"]) == ("hike", 1, "decision_recent")


def test_cycle_golden_from_the_seed_inputs():
    """The pre-registered cycles (S1.1 expected values) as read from the history on 2026-11-14."""
    want = {"USD": ("hike", 1, ("cut", 6)), "EUR": ("hike", 2, ("cut", 8)), "GBP": ("hike", 1, ("cut", 6)), "JPY": ("hike", 6, None),
            "CAD": ("hike", 1, ("cut", 9)), "AUD": ("hike", 5, ("cut", 3)), "NZD": ("hike", 2, ("cut", 9)), "CHF": ("cut", 6, ("hike", 5))}
    for cur, (d, n, prev) in want.items():
        c = FIX["banks"][cur]["cycle"]
        assert (c["direction"], c["count"]) == (d, n), cur
        assert (None if c["previous"] is None else (c["previous"]["direction"], c["previous"]["count"])) == prev, cur
    assert FIX["banks"]["JPY"]["cycle"]["first_move"].startswith("2024-03")
    chf = FIX["banks"]["CHF"]["cycle"]
    assert chf["last_move"].startswith("2025-06") and chf["holds_since"] == 5


def test_flags_for_the_12_month_classification():
    """S1.1 A2: boundary / neutral_sensitive computed apart for the 12-month situation (explicit arithmetic)."""
    for cur in ST.BANKS:
        r = ST.compute(inputs(cur), P)
        if r["G_12m"] is None:
            assert r["flags_12m"] == [], cur
            continue
        b = FIX["banks"][cur]
        i, n = b["rate"]["v"], b["neutral"]
        x12 = r["x_12m"]
        s_lo, s_hi = exp_situation(x12, 1.5 * x12 - (i - n["lo"])), exp_situation(x12, 1.5 * x12 - (i - n["hi"]))
        assert ("neutral_sensitive" in r["flags_12m"]) == (s_lo != s_hi or r["situation_12m_at_N_lo"] != r["situation_12m_at_N_hi"]), cur
        assert ("boundary" in r["flags_12m"]) == (r["margin_12m"] < TOL), cur
    usd = ST.compute(inputs("USD"), P)
    assert "neutral_sensitive" in usd["flags_12m"] and "neutral_sensitive" not in usd["flags"]       # 12m S3 at mid, S1 at N low


def test_horizon_gap_label_at_every_bank():
    """S1.1 A7: consistent / inconsistent wherever the bank has a projection (JPY +0.3 -> inconsistent)."""
    for cur in ST.BANKS:
        r = ST.compute(inputs(cur), P)
        assert r["lens"].get("consistent") in (True, False), cur
        assert r["lens"]["consistent"] == (abs(r["lens"]["horizon_gap"]) <= P["horizon_consistency_pp"] + 1e-12), cur
    jpy = ST.compute(inputs("JPY"), P)
    assert jpy["lens"]["horizon_gap"] == pytest.approx(0.3) and jpy["lens"]["consistent"] is False


def test_own_path_lens_is_the_bank_page_comparison():
    """S1.1 A4: USD = the page GAP vs dots for the year of as-of + 12 months; NZD = OCR track vs market (ESTIMATE), not 'GAP n/a'."""
    usd, nzd = FIX["banks"]["USD"]["lens"], FIX["banks"]["NZD"]["lens"]
    assert (usd["kind"], usd["basis"], usd["year"], usd["gap_bp"], usd["flag"]) == ("own_path", "gap", 2027, 57.7, "CURVE")
    assert (nzd["kind"], nzd["basis"], nzd["year"], nzd["gap_bp"], nzd["flag"]) == ("own_path", "ocr_track", 2027, 55.7, "ESTIMATE")
    assert "estimate" in inputs("NZD").flags


def test_sep_neutral_is_the_published_median_and_range_and_the_dots_only_check():
    D = date(2026, 9, 16)

    def row(kind, value=None, level=None, count=None, variable="federal_funds_rate", horizon="longer_run"):
        return {"meeting_date": D, "variable": variable, "horizon": horizon, "kind": kind, "value": value, "level": level, "count": count}
    dots = [row("dot", level=lv, count=c) for lv, c in ((2.875, 3), (3.0, 4), (3.125, 2), (3.375, 4), (3.5, 3), (3.875, 2))]
    proj = [row("median_published", 3.2), row("range_low", 2.9), row("range_high", 3.9)] + dots
    n, _ = ST.sep_neutral(proj, date(2026, 11, 14))
    assert (n.mid, n.lo, n.hi, n.flag) == (3.2, 2.9, 3.9, "SEP_LONGER_RUN")
    assert n.check["from_dots"] == 3.25 and n.check["rounded"] == 3.2 and n.check["ok"] is True       # 3.25 -> 3.2: half to even
    bad = [row("median_published", 3.4), row("range_low", 2.9), row("range_high", 3.9)] + dots
    assert ST.sep_neutral(bad, date(2026, 11, 14))[0].check["ok"] is False
    assert ST.sep_neutral([row("median_published", 3.2)] + dots, date(2026, 11, 14))[0] is None      # no Range stored -> n/a
    assert [ST.fed_round(v) for v in (3.25, 3.35, 3.125, 3.0625, 2.875)] == [3.2, 3.4, 3.1, 3.1, 2.9]


def test_add_months_end_of_month():
    assert ST.add_months(date(2026, 11, 14), 12) == date(2027, 11, 14) and ST.add_months(date(2028, 2, 29), 12) == date(2029, 2, 28)


# ------------------------------------------------------------------ payload ---

def test_payload_convention_and_show_calculation():
    for cur in ST.BANKS:
        j = stance_json(ST.compute(inputs(cur), P))
        for k in ("situation", "situation_12m", "stance", "need", "pressure", "pivot", "lens"):
            m = j[k]
            assert set(m) >= {"v", "flag", "stale", "na"} and ((m["v"] is None) == (m["na"] is not None)), (cur, k)
        assert {"gap_under", "y", "x", "S", "G", "inputs"} <= set(j["calc"])
        json.dumps(j, default=str)
    nzd = stance_json(ST.compute(inputs("NZD"), P))
    assert nzd["situation"]["v"] is None and "need only" in nzd["situation"]["na"]


# --------------------------------------------------------- history (append) ---

def test_history_append_only_refuses_a_rewrite(tmp_path):
    paths = cc.Paths(tmp_path)
    res = {c: ST.compute(inputs(c), P) for c in ST.BANKS}
    now = datetime(2026, 11, 14, 7, 30, tzinfo=timezone.utc)
    r1 = SH.append(paths, res, date(2026, 11, 14), now)
    assert sorted(r1.written) == sorted(ST.BANKS) and r1.partitions == ["2026"]
    f = tmp_path / "stance_history" / "stance_history_2026.parquet"
    before = f.read_bytes()
    changed = {c: dict(r, x=99.0) for c, r in res.items()}
    r2 = SH.append(paths, changed, date(2026, 11, 14), datetime(2026, 11, 14, 9, tzinfo=timezone.utc))
    assert r2.written == [] and sorted(r2.refused) == sorted(ST.BANKS) and f.read_bytes() == before
    rows = SH.load(paths)
    usd = rows[("USD", date(2026, 11, 14))]
    assert usd["x"] == res["USD"]["x"] and usd["mandate_id"] == "2025-08-22" and usd["stance_version"] == 3 and usd["code_version"] == ST.STANCE_CODE_VERSION
    assert json.loads(usd["inputs_json"])["pi_under"]["source"].startswith("fred:PCEPILFE")
    r3 = SH.append(paths, res, date(2026, 11, 15), now)
    assert sorted(r3.written) == sorted(ST.BANKS)


def test_seed_snapshot_exists():
    rows = SH.load(cc.Paths())
    assert {b for b, d in rows} == set(ST.BANKS) and len({d for _, d in rows}) >= 1


# -------------------------------------------------------- purity and guards ---

def test_stance_modules_are_pure():
    for f in ("stance", "stance_report"):
        text = (ROOT / "src" / "cb_compute" / f"{f}.py").read_text()
        for bad in ("open(", "read_text", "requests", "urllib", "pq.read", "pd.read", "yaml.", "import os", "subprocess", "write_text", "to_parquet"):
            assert bad not in text, (f, bad)


def test_economic_modules_do_not_import_the_stance():
    mods = ("src.cb_compute.stance", "src.cb_compute.stance_report", "src.cb_stance_history")
    code = ("import sys, src.economic_compute, src.economic_render, src.ff_scoring, src.history_compute;"
            f"bad=[m for m in {mods!r} if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_payload_is_not_written_to_public():
    for f in ("src/cb_render.py",):
        assert "stance_json" not in (ROOT / f).read_text()


def test_status_alerts_when_the_dots_do_not_reproduce_the_published_median(monkeypatch):
    """S1.1 A3: --status carries an ALERT line when the dots median (rounded half-even) differs from Table 1."""
    from src import cb_datasets as ds
    real = ds.load_projections(cc.Paths())
    sec = ds.stance_status(cc.Paths(), date(2026, 11, 14))
    assert not any("ALERT SEP" in n for n in sec[3])                                      # 3.25 -> 3.2 = published 3.2
    sep = max(r["meeting_date"] for r in real)
    bad = [dict(r, value=3.4) if (r["meeting_date"] == sep and r["kind"] == "median_published" and r["variable"] == "federal_funds_rate"
                                  and r["horizon"] == "longer_run") else r for r in real]
    monkeypatch.setattr(ds, "load_projections", lambda paths: bad)
    sec = ds.stance_status(cc.Paths(), date(2026, 11, 14))
    assert any(n.startswith(f"ALERT SEP {sep}") and "Table 1 publishes 3.4" in n for n in sec[3])
