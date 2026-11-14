"""Stance S2 - the UI payload: the deterministic templates (every combination of conditioning, situation and n/a), the B.4
formatting, the cycle / market lines, the mandate card, the next test, the history, the schema written by cb_render, and the JS
checks (tests/cb_js/test_stance.cjs)."""
from __future__ import annotations

import itertools
import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src import cb_render as R
from src.cb_compute import stance_ui as U
from src.cb_compute.payload import stance_page

ROOT = Path(__file__).resolve().parents[1]
M = U.MINUS


# ------------------------------------------------------------------------------------------------ B.4 formatting --

def test_pp_and_bp_formatting():
    assert [U.pp(v) for v in (0.675, -0.55, 0.0, -0.004, 1.1666666)] == ["+0.68", M + "0.55", "0.00", "0.00", "+1.17"]
    assert [U.bp(v) for v in (69.6, -3.13, 0.4, -0.5, 52.376, None)] == ["+70", M + "3", "0", M + "1", "+52", None]
    assert U.pct1(0.968) == "96.8%" and U.day("2026-12-17") == "17 Dec"


def test_chips():
    assert [U.chip(s, d) for s, d in (("S0", 0), ("S1", 1), ("S1", -1), ("S2", 1), ("S2", -1), ("S3", 1), ("S3", -1))] == \
        ["S0 balanced", "S1 hike", "S1 cut", "S2 up", "S2 down", "S3 hold", "S3 hold"]
    assert U.chip(None, None) is None


# ------------------------------------------------------------------------------------------------ market sentence --

def _path(prob=None, cum=None, m12=84.85, est=False):
    pt = {"meeting": "2026-12-09", "prob": prob, "cum_bp": cum}
    return {"next": {"decision": "2026-12-09"}, "points": [pt], "m12": {"cum_bp": m12, "est": est, "meeting": "2027-10-27"}}


@pytest.mark.parametrize("prob, word, p", [
    ({"dir": "hike", "n": 0, "p": 0.968}, "a hike", "96.8%"),
    ({"dir": "cut", "n": 0, "p": 0.62}, "a cut", "62.0%"),
    ({"dir": "hike", "n": 0, "p": 0.027}, "no change", "97.3%"),
    ({"dir": "hike", "n": 1, "p": 0.10}, "a hike", "90.0%"),             # +25 certain, 10% of +50: one hike is the likeliest
])
def test_market_sentence(prob, word, p):
    assert U.market_sentence(_path(prob)) == f"The market prices {word} at the next meeting ({p}) and +85 bp over 12 months."


def test_market_sentence_na_is_omitted():
    assert U.market_sentence(_path(None, cum=32.0)) is None                  # an estimate: no probability
    assert U.market_sentence(_path({"dir": "hike", "n": 0, "p": 0.9}, m12=None)) is None
    assert U.market_sentence(None) is None
    assert U.market_line(_path(None, cum=32.0, m12=99.6, est=True)) == "Market: 9 Dec ≈ +32 bp · 12M ≈ +100 bp"
    assert U.market_line(_path({"dir": "hike", "n": 0, "p": 0.968})) == "Market: 9 Dec 96.8% hike · 12M +85 bp"
    assert U.market_line(_path({"dir": "hike", "n": 0, "p": 0.027})) == "Market: 9 Dec 97.3% hold · 12M +85 bp"


# ------------------------------------------------------------------------------------------------ bank sentence --

FC = {"publication": "Monetary Policy Report, November 2026"}


def _r(kind, **lens):
    lp = {"v": 1.8, "source": "Monetary Policy Report, November 2026: CPI inflation"}
    return {"lens": {"kind": kind, **lens}, "inputs": {"last_projection": lp}}


def test_bank_sentence_every_conditioning():
    assert U.bank_sentence(_r("own_path", gap_bp=57.7, year=2027), None) == "The market is 58 bp above the bank's own path for 2027."
    assert U.bank_sentence(_r("own_path", gap_bp=-12.2, year=2027), None) == "The market is 12 bp below the bank's own path for 2027."
    for cons, hg, rel in ((True, -0.2, "consistent with"), (False, 0.3, "above"), (False, -0.4, "below")):
        assert U.bank_sentence(_r("market", delta_bp=-13.4, consistent=cons, horizon_gap=hg), FC) == (
            f"Since the Monetary Policy Report, November 2026 cutoff the market has moved {M}13 bp; the bank sees CPI inflation at 1.8% "
            f"at the horizon ({rel} target).")
    assert U.bank_sentence(_r("constant_rate", delta_bp=52.4), None) == "The market prices +52 bp more than the constant rate assumed in the bank's forecast."
    assert U.bank_sentence(_r("unpublished"), None) == "The bank does not publish a rate path."


@pytest.mark.parametrize("r, fc", [
    (_r("own_path", gap_bp=None, year=2027), None), (_r("market", delta_bp=None, consistent=True, horizon_gap=0.1), FC),
    (_r("market", delta_bp=3.0, consistent=True, horizon_gap=0.1), None), (_r("market", delta_bp=3.0, consistent=None, horizon_gap=None), FC),
    (_r("constant_rate", delta_bp=None), None), (_r("whatever"), None)])
def test_bank_sentence_na_is_omitted(r, fc):
    assert U.bank_sentence(r, fc) is None


# ------------------------------------------------------------------------------------------------ rule sentence --

S_WORDS = {("S0", 0): "roughly in balance", ("S1", 1): "behind the curve, more hikes needed", ("S1", -1): "behind the curve, more cuts needed",
           ("S2", 1): "room to move up toward neutral", ("S2", -1): "room to move down toward neutral", ("S3", 1): "at or past the required level, hold",
           ("S3", -1): "at or past the required level, hold"}


@pytest.mark.parametrize("today, later, basis", [(a, b, c) for a, b in itertools.product(S_WORDS, S_WORDS) for c in ("underlying", "headline")])
def test_rule_sentence_every_combination(today, later, basis):
    r = {"situation": today[0], "direction": today[1], "situation_12m": later[0], "direction_12m": later[1], "pi_12m_basis": basis}
    tail = " (on headline inflation)" if basis == "headline" else ""
    assert U.rule_sentence(r) == f"On today's data: {S_WORDS[today]}. If the rate is held: {S_WORDS[later]} in 12 months{tail}."


def test_rule_sentence_na_and_order():
    assert U.rule_sentence({"situation": None, "situation_12m": None}) is None                  # need only
    assert U.rule_sentence({"situation": "S1", "direction": 1, "situation_12m": None}) is None
    r = {**_r("unpublished"), "situation": "S0", "direction": 0, "situation_12m": "S3", "direction_12m": 1, "pi_12m_basis": "underlying"}
    s = U.summary(r, _path({"dir": "hike", "n": 0, "p": 0.6}), None)
    assert [x.split()[1] for x in s] == ["market", "bank", "today's"]                            # Market -> Bank -> Rule
    assert U.summary({**_r("own_path", gap_bp=None), "situation": None, "situation_12m": None}, None, None) == []


# ------------------------------------------------------------------------------------------------ cycle line --

def _mv(*ds):
    return [{"date": d, "bp": 25.0} for d in ds]


def test_cycle_line():
    c = {"direction": "hike", "count": 2, "moves": _mv("2026-06-11", "2026-09-10"), "previous": {"direction": "cut", "count": 8}, "holds_since": 1}
    assert U.cycle_line(c) == "Cycle: hiking · 2 hikes (Jun, Sep 2026) after 8 cuts · 1 hold since"
    c = {"direction": "hike", "count": 3, "moves": _mv("2025-12-19", "2026-06-16", "2026-09-18"), "previous": None, "holds_since": 0}
    assert U.cycle_line(c) == "Cycle: hiking · 3 hikes (Dec 2025, Jun, Sep 2026)"
    c = {"direction": "cut", "count": 6, "moves": _mv("2024-03-22", "2024-06-21", "2024-09-27", "2024-12-13", "2025-03-21", "2025-06-20"),
         "previous": {"direction": "hike", "count": 5}, "holds_since": 5}
    assert U.cycle_line(c) == "Cycle: cutting · 6 cuts (Mar 2024–Jun 2025) after 5 hikes · 5 holds since"
    c = {"direction": "hike", "count": 5, "moves": _mv("2026-02-03", "2026-03-17", "2026-05-05", "2026-09-29", "2026-11-03"), "previous": None}
    assert U.cycle_line(c) == "Cycle: hiking · 5 hikes (Feb–Nov 2026)"
    assert U.cycle_line({"direction": None}) is None and U.cycle_line(None) is None


# ------------------------------------------------------------------------------------------------ mandate, next test, history --

def test_mandate_card_point_band_and_two_measures():
    v = {"effective_from": date(2025, 8, 22), "effective_to": None, "target": {"kind": "point", "value": 2.0, "measure": "PCE"},
         "underlying": {"primary": "fred:PCEPILFE", "rule_applied": 2}, "horizon": {"text": "over the longer run"}, "mandate": {"type": "dual"},
         "sources": [{"title": "Statement", "url": "https://x"}], "last_verified": {"date": date(2026, 11, 14)}}
    rules = ["(1) a", "(2) b", "(3) c"]
    m = U.mandate_card(v, {"fred:PCEPILFE": "Core PCE"}, rules)
    assert (m["target"], m["underlying"], m["rule_text"], m["horizon"], m["mandate_type"], m["effective_from"], m["last_verified"], m["source_url"]) == \
        ("2%", "Core PCE", "(2) b", "over the longer run", "dual", "2025-08-22", "2026-11-14", "https://x")
    band = dict(v, target={"kind": "band", "low": 1.0, "high": 3.0, "midpoint": {"value": 2.0}}, underlying={"primary": ["a", "b"], "rule_applied": 1})
    m = U.mandate_card(band, {"a": "CPI-trim", "b": "CPI-median"}, rules)
    assert m["target"] == "1–3% (midpoint 2%)" and m["underlying"] == "CPI-trim and CPI-median (mean of the two)"
    assert U.mandate_card(dict(v, target={"kind": "band", "low": 0.0, "high": 2.0}), {}, rules)["target"] == "0–2% (no declared midpoint)"


def test_next_test_from_the_ff_week():
    t = lambda d: datetime(2026, 11, d, 13, 30, tzinfo=timezone.utc)          # noqa: E731
    ev = [{"currency": "USD", "name_raw": "Core PCE Price Index m/m", "datetime_utc": t(10)},
          {"currency": "USD", "name_raw": "Core PCE Price Index m/m", "datetime_utc": t(26)}, {"currency": "EUR", "name_raw": "x", "datetime_utc": t(20)}]
    mp = {"USD": {"ff_titles": ["Core PCE Price Index m/m"]}, "EUR": {"ff_titles": ["Core CPI Flash Estimate y/y"]}}
    assert U.next_test("USD", ev, mp, date(2026, 11, 14))["date"] == "2026-11-26"
    e = U.next_test("EUR", ev, mp, date(2026, 11, 14))
    assert e["date"] is None and "not in the ForexFactory calendar" in e["na"] and e["schedule"] == "ForexFactory"
    assert U.next_test("CHF", ev, mp, date(2026, 11, 14)) is None                          # no mapping: hidden


def test_ff_week_events_reads_the_newest_file_on_or_before_the_asof(tmp_path):
    (tmp_path / "ff_weekly_2026-11-10.json").write_text(json.dumps([{"title": "A", "country": "USD", "date": "2026-11-12T08:30:00-05:00"}]))
    (tmp_path / "ff_weekly_2026-11-16.json").write_text(json.dumps([{"title": "B", "country": "USD", "date": "2026-11-18T08:30:00-05:00"}]))
    ev = R.ff_week_events(date(2026, 11, 14), tmp_path)
    assert [(e["name_raw"], e["datetime_utc"].isoformat()) for e in ev] == [("A", "2026-11-12T13:30:00+00:00")]
    assert R.ff_week_events(date(2026, 11, 1), tmp_path) == []


def test_history_points_in_bp():
    rows = [{"snapshot_date": date(2026, 11, 15), "G": -0.3625, "G_lo": -0.55, "G_hi": -0.3, "situation": "S2", "direction": -1},
            {"snapshot_date": date(2026, 11, 14), "G": None, "G_lo": None, "G_hi": None, "situation": None, "direction": None}]
    h = U.history_points(rows)
    assert [p["date"] for p in h] == ["2026-11-14", "2026-11-15"] and h[1]["G_bp"] == -36.25 and h[0]["G_bp"] is None


def test_how_to_read_exact_text():
    assert U.HOW_TO_READ[0] == ("Need: how far the bank's preferred underlying inflation is from target, plus one third of the bank's own estimate "
                                "of spare capacity (pp).")
    assert U.HOW_TO_READ[2] == "Pressure: 1.5 × need − stance (bp). Positive points up, negative points down."
    assert U.HOW_TO_READ[-1].startswith("This is a mandate benchmark, not a forecast.") and len(U.HOW_TO_READ) == 9


# ------------------------------------------------------------------------------------------------ written payload --

@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    pub = tmp_path_factory.mktemp("pub")
    R.render(None, pub)
    return pub


BANK_KEYS = {"bank", "asof", "flags", "flags_12m", "situation", "situation_12m", "stance", "need", "need_headline", "need_12m", "pressure",
             "pressure_12m", "pivot", "lens", "cycle", "market", "calc", "chip", "chip_12m", "summary", "cycle_line", "market_line", "m12_bp",
             "rate_display", "mandate", "next_test", "history", "notes", "publication"}


def test_written_payload_schema(rendered):
    ov = json.loads((rendered / "data" / "cb" / "overview.json").read_text())["stance"]
    assert ov["na"] is None and ov["how_to_read"] == U.HOW_TO_READ and set(ov["banks"]) == {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"}
    assert ov["params"] == {"delta_pp": 0.25, "epsilon_pp": 0.5, "inflation_response": 1.5, "version": 3}
    for c, b in ov["banks"].items():
        assert "history" not in b and "inputs" not in b["calc"], c
        page = json.loads((rendered / "data" / "cb" / f"{c.lower()}.json").read_text())["stance"]
        assert BANK_KEYS <= set(page), (c, BANK_KEYS - set(page))
        for k in ("situation", "situation_12m", "stance", "need", "need_12m", "pressure", "pivot", "lens"):
            m = page[k]
            assert set(m) >= {"v", "flag", "stale", "na"} and ((m["v"] is None) == (m["na"] is not None)), (c, k)
        assert page["summary"] == [s for s in page["summary"] if s] and page["calc"]["inputs"]
    usd = json.loads((rendered / "data" / "cb" / "usd.json").read_text())["stance"]
    assert usd["rate_display"] == {"rate": 3.875, "lower": 3.75, "upper": 4.0, "range": True}
    chf = json.loads((rendered / "data" / "cb" / "chf.json").read_text())["stance"]
    assert chf["stance"]["na"] == "no neutral estimate: the bank does not publish a neutral-rate estimate" and chf["chip"] is None


def test_render_is_deterministic(rendered, tmp_path):
    R.render(None, tmp_path)
    for f in ("overview.json", "usd.json", "nzd.json"):
        assert (tmp_path / "data" / "cb" / f).read_bytes() == (rendered / "data" / "cb" / f).read_bytes(), f


def test_a_stance_failure_never_blocks_the_render(monkeypatch, tmp_path):
    import src.cb_loader as L
    monkeypatch.setattr(L, "load_stance", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    R.render(None, tmp_path)
    ov = json.loads((tmp_path / "data" / "cb" / "overview.json").read_text())
    assert ov["stance"] == {"na": "stance unavailable: RuntimeError: boom"} and ov["paths"]
    assert json.loads((tmp_path / "data" / "cb" / "usd.json").read_text())["stance"]["na"].startswith("stance unavailable")


def test_stance_page_keeps_the_bank_reason_for_a_missing_neutral():
    r = {"currency": "NZD", "asof": "2026-11-13", "flags": ["need_only"], "mandate_id": "x", "stance_version": 3, "code_version": "s1.1",
         "notes": ["neutral: the MPS shows the suite only as a chart"], "lens": {}, "x": 0.43}
    j = stance_page(r, None, None, None, None, [], None)
    assert j["stance"]["na"] == "no neutral estimate: the MPS shows the suite only as a chart" and j["pressure"]["na"] == j["stance"]["na"]


# ------------------------------------------------------------------------------------------------ JS --

def test_stance_js():
    r = subprocess.run(["node", str(ROOT / "tests" / "cb_js" / "test_stance.cjs")], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
