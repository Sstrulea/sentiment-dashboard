"""stance-ui-v6: Position / Need / Why without a verdict.

Exact values run on the FROZEN arguments of stance_page_v6 at as-of 2026-11-20 (tests/fixtures/cb_stance/v6_page_inputs_2026-11-20.json,
written by scripts/cb_freeze_stance_fixture.py; r["v6"] recomputed with stance_v6.evaluate, the block built by the pure
cb_render.build_stance): the golden of the brief (8/8), the market comparison, the votes, the texts, CAD stale, the stale-neutral note, the
payload keys, the JS fixture. The live data (a render at the newest as-of) only carries invariants valid for any data. Plus the reading
rule case by case, the identities with v3, the thresholds = config v6, How to read, the history and the JS (tests/cb_js/test_stance_v6.cjs)."""
from __future__ import annotations

import copy
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest
import yaml

from src.cb_compute import stance_v6 as V
from src.cb_compute.payload import _r2, st_at, st_fmt, st_text, stance_page_v6

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "cb_stance" / "v6_page_inputs_2026-11-20.json"
FIX = json.loads(FIXTURE.read_text())
CFG = yaml.safe_load((ROOT / "config" / "cb_stance.yaml").read_text())
BANKS = ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF")
M = "−"
UI = {"measure_short": "Core", "measure_name": "core"}


def ev(c: str, **r_over) -> dict:
    b = copy.deepcopy(FIX["banks"][c])
    b["r"].update(r_over)
    return V.evaluate(b["r"], b["d"])


def pp(x) -> str:
    return st_fmt(_r2(x), "pp")


def rng(s: dict, a="lo", b="hi") -> str:
    return f"({pp(s[a])} to {pp(s[b])})"


# ---- golden 2026-11-20: Position; Need; Taylor today; Taylor forecast; the reading ----

GOLDEN = [
    ("USD", f"+0.18 Neutral ({M}0.53 to +0.48)", "+0.50 Slightly above target", "+1.08 (+0.78 to +1.78)", f"+0.18 ({M}0.13 to +0.88)",
     "Tightening pressure, sensitive to the neutral range", []),
    ("EUR", f"{M}0.10 Neutral ({M}0.35 to +0.15)", "+0.60 Slightly above target", "+0.25 (0.00 to +0.50)", "+0.40 (+0.15 to +0.65)",
     "Tightening pressure, sensitive to the neutral range", ["no_slack"]),
    ("GBP", f"{M}0.94 Stimulative ({M}1.00 to {M}0.75)", "+2.00 Well above target", f"{M}0.36 ({M}0.55 to {M}0.30)", "+1.34 (+1.15 to +1.40)",
     "Mixed signals", ["headline_basis"]),
    ("JPY", f"{M}1.25 Stimulative ({M}1.95 to {M}0.55)", "+0.70 Slightly above target", "+1.12 (+0.42 to +1.82)", "+1.87 (+1.17 to +2.57)",
     "Tightening pressure, robust to the neutral range", ["slack_held"]),
    ("CAD", f"{M}0.85 Stimulative ({M}1.35 to {M}0.35)", "+0.60 Slightly above target", f"{M}0.28 ({M}0.78 to +0.23)", "+0.78 (+0.28 to +1.28)",
     "Mixed signals", ["slack_held", "headline_basis"]),
    ("AUD", "+0.96 Restrictive (+0.03 to +1.43)", "+0.40 Slightly above target", f"+0.14 ({M}0.33 to +1.07)", f"{M}0.91 ({M}1.38 to +0.02)",
     "Mixed signals", []),
]


@pytest.mark.parametrize("cur,pos,need,today,fc,read,flags", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden_2026_11_20(cur, pos, need, today, fc, read, flags):
    v = ev(cur)
    p, n, t = v["position"], v["need"], v["taylor"]
    assert f"{pp(p['P'])} {p['label']} {rng(p, 'P_lo', 'P_hi')}" == pos
    assert f"{pp(n['N'])} {n['label']}" == need
    assert f"{pp(t['today']['T'])} {rng(t['today'])}" == today
    assert f"{pp(t['forecast']['T'])} {rng(t['forecast'])}" == fc
    assert t["reading"] == read
    assert t["flags"] == flags


def test_golden_total_inflation_basis():
    assert {c for c in BANKS if ev(c)["headline_basis"]} == {"GBP", "CAD", "NZD", "CHF"}


def test_golden_nzd_and_chf_without_neutral():
    nzd, chf = ev("NZD"), ev("CHF")
    for v in (nzd, chf):
        assert v["position"]["P"] is None and v["position"]["na"] == "neutral"        # the payload writes cb_render.neutral_reason
        assert v["taylor"]["reading"] is None and v["taylor"]["na"] == "neutral"
    assert f"{pp(nzd['need']['N'])} {nzd['need']['label']}" == "+0.10 Near target"
    assert f"{pp(chf['need']['N'])} {chf['need']['label']}" == "0.00 Inside target range"


# ---- the market comparison (the v3 lens), in the payload ----

MARKET_LENS = {"USD": "vs dots, end-2027: market 4.421% vs 4.125% = +29.6 bp",
               "GBP": f"vs the 27 Oct curve, at 20 Nov 2027: 4.792% vs 4.906% = {M}11.4 bp",
               "JPY": f"vs the 30 Oct curve, at 20 Nov 2027: 1.854% vs 1.985% = {M}13.1 bp",
               "AUD": f"vs the 28 Oct curve, at 20 Nov 2027: 4.480% vs 4.853% = {M}37.2 bp",
               "NZD": "vs OCR track, 2027: market 3.675% (year-end) vs 3.2% (Q4 average) = +47.5 bp [ESTIMATE]",
               "CHF": "vs an unchanged rate, at 20 Nov 2027: 0.459% vs 0% = +45.9 bp",  # +46.9 before the SNB SARON fixings of 16-20 Nov were added on 23 Nov
               "EUR": "vs bank path: n/a (no market history at the cutoff 2026-08-19 (first data 2026-10-08))",
               "CAD": "no bank rate path to compare"}


def page(c: str, r_over: dict | None = None, **kw) -> dict:
    b = copy.deepcopy(FIX["banks"][c])
    b["r"].update(r_over or {})
    r = dict(b["r"], v6=V.evaluate(b["r"], b["d"]))
    return stance_page_v6(r, UI, None, neutral_na=kw.pop("neutral_na", "x"), **kw)


@pytest.mark.parametrize("cur", BANKS)
def test_market_comparison(cur):
    s = page(cur)
    assert st_text(s, s["why"]["market"]["lines"][-1]) == MARKET_LENS[cur]


# ---- votes and the bank row ----

VOTES = {"USD": "Last decision 28 Oct: vote 10–2 (2 for a hike)", "GBP": "Last decision 5 Nov: vote 8–1 (1 for a hike)",
         "AUD": "Last decision 3 Nov: vote 6–3 (3 for no change)", "JPY": "Last decision 30 Oct: vote not recorded",
         "NZD": "Last decision 28 Oct: decided by consensus", "EUR": "Last decision 29 Oct: vote not published",
         "CAD": "Last decision 28 Oct: vote not published", "CHF": "Last decision 24 Sep: vote not published"}


def _frozen_args() -> dict:
    import importlib.util
    spec = importlib.util.spec_from_file_location("cb_freeze_stance_fixture", ROOT / "scripts" / "cb_freeze_stance_fixture.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load(FIXTURE)


@pytest.fixture(scope="module")
def frozen():
    """(args, overview-like {stance, paths, meta}, {ccy: {"stance": bank block}}) built by the pure cb_render functions from the frozen file."""
    from src import cb_render as R
    args = _frozen_args()
    st = R.build_stance(args)
    ov = {"stance": R.overview_stance(st), "paths": {c: a["path"] for c, a in args["banks"].items()},
          "meta": {"stale_after_bd": args["stale_after_bd"]}}
    return args, ov, {c: {"stance": R.bank_stance(st, c)} for c in BANKS}


@pytest.mark.parametrize("cur", BANKS)
def test_votes_at_the_last_decision(frozen, cur):
    _, _, pages = frozen
    s = pages[cur]["stance"]
    assert st_text(s, s["why"]["bank"]["lines"][-1]) == VOTES[cur]


def test_rendered_golden_texts(frozen):
    _, ov, pages = frozen
    usd, nzd, chf = pages["USD"]["stance"], pages["NZD"]["stance"], pages["CHF"]["stance"]
    assert [st_text(usd, x) for x in usd["position"]["lines"]] == ["real rate 1.38% vs real neutral 1.20% (0.90 to 1.90%)",
                                                                    "with the Fed forecast: core inflation 2.5%, 2027 (Q4/Q4)"]
    assert usd["position"]["title"] == ("Over the neutral range: −0.53 to +0.48. Real rate = 3.875% − 2.5%. "
                                        "Real neutral = neutral 3.2% (2.9 to 3.9%) − target 2%.")
    assert [st_text(usd, x) for x in usd["need"]["lines"]] == [
        "Fed forecast of 16 Sep 2026: core inflation 2.5%, 2027 (Q4/Q4), vs 2%", "conditional on the Fed's own rate path (dots)",
        "now: Core PCE 3.1% (2026-09), output gap 0.2 (from unemployment 4.1% vs u* 4.2%, 2026-10)"]
    t = usd["why"]["taylor"]
    assert t["reading"] == "Tightening pressure, sensitive to the neutral range"
    assert st_text(usd, t["lines"][0]) == "today +1.08 (+0.78 to +1.78): Core PCE 3.1% (2026-09), output gap 0.2"
    assert t["lines"][0]["title"] == "3.2 + 1.5 × (3.1 − 2) + 0.5 × 0.2 − 3.875 = +1.08; neutral 2.9 to 3.9 → +0.78 to +1.78"
    assert st_text(usd, t["lines"][1]) == "forecast +0.18 (−0.13 to +0.88): core inflation 2.5% 2027 (Q4/Q4), output gap 0.2"
    assert st_text(usd, usd["why"]["bank"]["lines"][0]) == "Dots (SEP 16 Sep 2026): 2026 4.125%, 2027 4.125%"
    assert nzd["position"]["na"] == "RBNZ neutral due 9 Dec 2026" and nzd["why"]["taylor"]["lines"][0]["segs"] == ["n/a: RBNZ neutral due 9 Dec 2026"]
    assert st_text(nzd, nzd["why"]["bank"]["lines"][0]) == ("OCR track (Monetary Policy Statement, September 2026): 2026Q3 2.6%, 2026Q4 2.8%, "
                                                            "2027Q1 3%, 2027Q2 3.1%, 2027Q3 3.1%, 2027Q4 3.2%")
    assert "output gap −1.4 (year to Mar 2027)" in st_text(nzd, nzd["need"]["lines"][-1])
    assert chf["position"]["na"] == "SNB publishes no neutral" and chf["why"]["taylor"]["na"] == "SNB publishes no neutral"
    assert st_text(chf, chf["need"]["lines"][0]) == ("SNB forecast of 24 Sep 2026: total inflation 0.8%, 2027 (annual average), target 0–2%; "
                                                     "no core forecast published")
    eur = pages["EUR"]["stance"]
    assert [st_text(eur, x) for x in eur["why"]["taylor"]["lines"]] == [
        "today +0.25 (0.00 to +0.50): Core HICP 2.5% (2026-10)", "forecast +0.40 (+0.15 to +0.65): core inflation 2.6% 2027 (annual average)",
        "no output gap term: the ECB publishes no estimate"]
    assert eur["why"]["taylor"]["lines"][0]["title"] == "2 + 1.5 × (2.5 − 2) − 2.5 = +0.25; neutral 1.75 to 2.25 → 0.00 to +0.50"
    jpy, cad = pages["JPY"]["stance"], pages["CAD"]["stance"]
    assert st_text(jpy, jpy["why"]["taylor"]["lines"][1]).endswith("output gap 0.55 (held)")
    assert st_text(jpy, jpy["why"]["bank"]["lines"][0]) == "Forecast conditioned on the market curve of 30 Oct (estimated cutoff)"
    assert "output gap −0.75 (−1.25 to −0.25, 2026Q3)" in st_text(cad, cad["need"]["lines"][-1])
    assert st_text(cad, cad["why"]["taylor"]["lines"][-1]) == "forecast is total inflation"
    assert st_text(cad, cad["why"]["bank"]["lines"][0]) == "Forecast rate path not published"


# ---- the market row = the "By meeting" table of the same bank ----

@pytest.mark.parametrize("cur", BANKS)
def test_market_row_is_the_by_meeting_table(frozen, cur):
    _, ov, pages = frozen
    s, pj = pages[cur]["stance"], ov["paths"][cur]
    x = s["why"]["market"]["x"]
    w1 = {p["meeting"]: p for p in pj["history"]["1w"]["points"]}
    for j, p in enumerate(pj["points"][:2], 1):
        assert x[f"m{j}_cum"] == p["cum_bp"]                                     # "Δ vs now"
        assert x[f"m{j}_1w"] == (p["rate"] - w1[p["meeting"]]["rate"]) * 100    # "Δ vs 1w": the same expression as pathTableRows
    line = st_text(s, s["why"]["market"]["lines"][1])
    assert line.startswith(f"1w (vs {int(pj['history']['1w']['asof'][8:])} ")


def test_cad_market_data_is_stale(frozen):
    _, ov, pages = frozen
    s = pages["CAD"]["stance"]
    assert st_text(s, s["why"]["market"]["lines"][1]) == "1w (vs 5 Nov): +4.9 / +8.0 bp · data 13 Nov Montréal Exchange [stale]"
    assert {"stale": "older than 2 business days"} in s["why"]["market"]["lines"][1]["segs"]
    assert not any("stale" in g for c in BANKS if c != "CAD" for g in pages[c]["stance"]["why"]["market"]["lines"][1]["segs"] if isinstance(g, dict))


def test_market_point_na_shows_its_reason():
    s = page("USD", path={"points": [{"meeting": "2026-12-09", "cum_bp": None, "na": "no quote"}, {"meeting": "2027-01-27", "cum_bp": 10.0,
                                       "rate": 3.975, "est": True, "flag": "ESTIMATE"}], "history": {"1w": {"asof": "2026-11-13", "points": []}}})
    assert st_text(s, s["why"]["market"]["lines"][0]) == "9 Dec: n/a (no quote) · 27 Jan: ≈ +10.0 bp [ESTIMATE]"
    assert st_text(s, s["why"]["market"]["lines"][1]) == "1w (vs 13 Nov): n/a / n/a bp"


# ---- the reading rule, one case each ----

def test_reading_opposite_signs():
    assert V.reading((0.5, 0.2, 0.8), (-0.3, -0.6, 0.1))[1] == "Mixed signals"


def test_reading_robust_both_directions():
    assert V.reading((1.1, 0.4, 1.8), (1.9, 1.2, 2.6))[1] == "Tightening pressure, robust to the neutral range"
    assert V.reading((-1.1, -1.8, -0.4), (-0.2, -0.9, -0.01))[1] == "Easing pressure, robust to the neutral range"


def test_reading_sensitive_with_an_end_at_zero():
    assert V.reading((0.25, 0.0, 0.5), (0.4, 0.15, 0.65))[1] == "Tightening pressure, sensitive to the neutral range"     # EUR 0.00
    assert V.reading((-0.25, -0.5, 0.0), (-0.4, -0.65, -0.15))[1] == "Easing pressure, sensitive to the neutral range"


def test_reading_one_scenario_na():
    v = ev("USD", G_12m=None, G_12m_lo=None, G_12m_hi=None, pi_12m=None, gap_12m=None)
    assert v["taylor"]["reading"] == "Tightening pressure, robust to the neutral range"                  # today alone: +0.78 .. +1.78
    assert v["taylor"]["missing"] == {"scenario": "forecast", "reason": V.no_forecast_reason("Fed")}
    assert v["position"]["na"] == "the latest Fed forecast does not cover the period containing today + 12 months"
    assert v["need"]["na"] == v["position"]["na"]
    assert V.reading(None, (0.3, -0.1, 0.7))[1] == "Tightening pressure, sensitive to the neutral range"
    s = page("USD", neutral_na="x")
    assert s["need"]["v"] == 0.5


def test_reading_a_zero_uses_b_and_both_zero():
    assert V.reading((0.0, -0.4, 0.4), (0.3, -0.1, 0.7))[1] == "Tightening pressure, sensitive to the neutral range"
    assert V.reading((0.0, -0.4, 0.4), (-0.3, -0.7, 0.1))[1] == "Easing pressure, sensitive to the neutral range"
    assert V.reading((0.0, -0.4, 0.4), (0.0, -0.4, 0.4))[1] == "No pressure at the neutral midpoint"
    assert V.reading((1e-12, -0.4, 0.4), (-1e-12, -0.4, 0.4))[1] == "No pressure at the neutral midpoint"     # float noise is zero


def test_reading_without_neutral():
    v = ev("GBP", N=None, S=None, S_lo=None, S_hi=None, G=None, G_lo=None, G_hi=None, G_12m=None, G_12m_lo=None, G_12m_hi=None)
    assert v["taylor"]["na"] == "neutral" and v["taylor"]["reading"] is None and v["position"]["na"] == "neutral"
    s = page("GBP", dict(N=None, G=None, G_12m=None), neutral_na="BoE: no neutral estimate (pending)")
    assert s["position"]["na"] == "BoE: no neutral estimate (pending)" and s["why"]["taylor"]["lines"][0]["segs"] == ["n/a: BoE: no neutral estimate (pending)"]


# ---- identities with v3 ----

@pytest.mark.parametrize("cur", ("USD", "EUR", "GBP", "JPY", "CAD", "AUD"))
def test_identities(cur):
    r = FIX["banks"][cur]["r"]
    v = ev(cur)
    star = r["target_star"]
    assert v["position"]["P"] == pytest.approx((r["rate"] - r["pi_12m"]) - (r["N"]["mid"] - star), abs=1e-12)
    assert v["position"]["P"] == pytest.approx(r["S"] - r["gap_12m"], abs=1e-12)
    assert v["position"]["P_lo"] == pytest.approx(r["S_lo"] - r["gap_12m"], abs=1e-12)
    assert v["position"]["P_hi"] == pytest.approx(r["S_hi"] - r["gap_12m"], abs=1e-12)
    assert v["need"]["N"] == r["gap_12m"]                                       # exactly the v3 value, not recomputed
    t = v["taylor"]
    assert (t["today"]["T"], t["today"]["lo"], t["today"]["hi"]) == (r["G"], r["G_lo"], r["G_hi"])
    assert (t["forecast"]["T"], t["forecast"]["lo"], t["forecast"]["hi"]) == (r["G_12m"], r["G_12m_lo"], r["G_12m_hi"])


def test_v6_does_not_change_the_v3_values():
    """compute() with v6 = the same v3 / v5 numbers (s1 golden fixture)."""
    from src.cb_compute import stance as ST
    r = ev("USD")
    assert r["need"]["N"] == FIX["banks"]["USD"]["r"]["gap_12m"]
    src = (ROOT / "src" / "cb_compute" / "stance_v6.py").read_text()
    for bad in ("r[\"G\"] =", "r.update(", "r[\"gap_12m\"] ="):
        assert bad not in src
    assert "v6" not in ST.inputs_record.__code__.co_names


# ---- the bank row, one case per path kind ----

def test_bank_row_path_kinds():
    base = page("CHF")
    assert st_text(base, base["why"]["bank"]["lines"][0]) == "Forecast conditioned on an unchanged rate (0%)"
    b = copy.deepcopy(FIX["banks"]["GBP"])
    b["d"]["cutoff_flag"] = "ESTIMATE"
    r = dict(b["r"], v6=V.evaluate(b["r"], b["d"]), inputs={})
    s = stance_page_v6(r, UI, None)
    assert st_text(s, s["why"]["bank"]["lines"][0]) == "Forecast conditioned on the market curve of 27 Oct (estimated cutoff)"
    for c, want in (("USD", "Dots (SEP 16 Sep 2026): 2026 4.125%, 2027 4.125%"), ("CAD", "Forecast rate path not published"),
                    ("EUR", "Forecast conditioned on the market curve of 19 Aug")):
        s = page(c)
        assert st_text(s, s["why"]["bank"]["lines"][0]) == want, c
    s = page("NZD")
    assert st_text(s, s["why"]["bank"]["lines"][0]).endswith("2027Q3 3.1%, 2027Q4 3.2%")


def test_own_path_stops_at_the_forecast_period():
    b = copy.deepcopy(FIX["banks"]["USD"])
    b["r"]["lens"]["path"] = [["2026", 4.125], ["2027", 4.125], ["2028", 3.875]]
    assert V.evaluate(b["r"], b["d"])["bank_path"]["path"] == [("2026", 4.125), ("2027", 4.125)]


def test_vote_kinds():
    from src.cb_compute.payload import _vote_line
    t = lambda v, d="2026-10-28": "".join(_vote_line(v, d)["segs"])           # noqa: E731
    assert t({"kind": "counted", "label": "7–2", "n_against": 2, "against": [{"direction": "raise"}, {"direction": "lower"}]}) == \
        "Last decision 28 Oct: vote 7–2 (1 for a hike, 1 for a cut)"
    assert t({"kind": "counted", "label": "8–1", "n_against": 1, "against": [{"direction": "lower"}]}) == "Last decision 28 Oct: vote 8–1 (1 for a cut)"
    assert t({"kind": "counted", "label": "6–3", "n_against": 3, "against": [{"direction": "hold", "name": ""}]}) == \
        "Last decision 28 Oct: vote 6–3 (3 for no change)"
    assert t({"kind": "unanimous", "label": "12–0", "against": []}) == "Last decision 28 Oct: vote 12–0"
    assert t({"kind": "not_published", "label": "not published"}) == "Last decision 28 Oct: vote not published"
    assert t({"kind": "consensus", "label": "consensus"}) == "Last decision 28 Oct: decided by consensus"
    assert t(None) == "Last decision 28 Oct: vote not recorded"


# ---- notes ----

def test_notes_newer_forecast_stale_neutral_and_overdue_core():
    b = copy.deepcopy(FIX["banks"]["EUR"])
    b["d"]["newer_forecast"] = True
    r = dict(b["r"], v6=V.evaluate(b["r"], b["d"]), inputs={"pi_under": {"v": 2.5, "asof": "2026-10"}})
    s = stance_page_v6(r, UI, None, notes={"position": "from Feb 2025", "need": "update overdue",
                                           "why": "Newer ECB forecast out on 17 Dec 2026, not yet entered."})
    pos = [st_text(s, x) for x in s["position"]["lines"]]
    assert pos[-2:] == ["(from Feb 2025)", "newer forecast not entered"]
    need = [st_text(s, x) for x in s["need"]["lines"]]
    assert need[-1] == "Newer ECB forecast out on 17 Dec 2026, not yet entered." and need[-2].endswith(" update overdue")
    assert {"note": "update overdue", "name": "need"} in s["need"]["lines"][-2]["segs"]
    assert st_text(s, s["why"]["taylor"]["lines"][1]).endswith(" · newer forecast not entered")
    assert "note" not in json.dumps(s["why"]["bank"]) and "Newer" not in json.dumps(s["why"])


def test_today_only_the_eur_neutral_is_stale(frozen):
    _, _, pages = frozen
    got = {c: [l.get("note") for k in ("position", "need") for l in pages[c]["stance"][k]["lines"] if l.get("note")] for c in BANKS}
    assert {c: v for c, v in got.items() if v} == {"EUR": ["position"]}


# ---- payload: only what is displayed ----

def test_payload_has_only_what_is_displayed(frozen):
    _, ov, pages = frozen
    st = ov["stance"]
    assert set(st) == {"how_to_read", "scale_pp", "na", "banks"} and st["scale_pp"] == 2.0
    for c, b in st["banks"].items():
        assert set(b) == {"bank", "asof", "rate_display", "position", "need", "why"}, c
        assert set(b["why"]) == {"taylor", "bank", "market"}
        assert set(b["position"]) <= {"v", "lo", "hi", "label", "na", "lines", "title", "x"}
        assert set(b["need"]) <= {"v", "label", "na", "lines", "title", "x"}
        for k in ("taylor", "bank", "market"):
            assert set(b["why"][k]) <= {"label", "reading", "lines", "na", "x"}
    text = json.dumps(st["banks"])
    for gone in ("conclusion", "Likely on hold", "likely", '"move"', "S0", "pivot", "pressure_12m", "situation", "inputs_json"):
        assert gone not in text, gone
    for c in BANKS:                                     # every number referenced by a line exists and every stored number is displayed
        s = pages[c]["stance"]
        refs = set()
        for sec in (s["position"], s["need"], *s["why"].values()):
            for l in sec.get("lines") or []:
                for g in l["segs"]:
                    if isinstance(g, dict) and "k" in g:
                        assert st_at(s, g["k"]) is not None, (c, g)
                        refs.add(g["k"])
        stored = {f"{p}.x.{k}" for p, sec in (("position", s["position"]), ("need", s["need"])) for k in sec.get("x") or {}}
        stored |= {f"why.{p}.x.{k}" for p, sec in s["why"].items() for k in sec.get("x") or {}}
        assert stored == refs, (c, stored ^ refs)
    assert set(pages["USD"]["stance"]) >= {"mandate", "next_test", "history"}


def test_thresholds_are_the_preregistered_ones():
    import re
    cfg = CFG["v6"]
    assert list(V.SCALE) == cfg["scale_pp"]

    def alt(lab: str) -> tuple:                     # "Slightly above|below target" -> ("Slightly above target", "Slightly below target")
        m = re.match(r"(.*?)(\S+)\|(\S+)(.*)", lab)
        return (lab, lab) if m is None else (m[1] + m[2] + m[4], m[1] + m[3] + m[4])
    for table, labs in ((V.P_LABELS, cfg["p_labels"]), (V.N_LABELS, cfg["n_labels"])):
        assert [alt(x)[0] for x in labs] == list(table[1]) and [alt(x)[1] for x in labs] == list(table[-1])
    rule = " ".join(cfg["reading"])
    for t in (V.READ_MIXED, V.READ_ROBUST.format("Tightening"), V.READ_ROBUST.format("Easing"), V.READ_ZERO,
              "Tightening / Easing pressure, sensitive to the neutral range"):
        assert t in rule, t
    assert V.READ_SENSITIVE.format("Tightening / Easing") == "Tightening / Easing pressure, sensitive to the neutral range"
    assert str(cfg["frozen"]) == "2026-11-23"
    assert CFG["v5"]["scale_pp"] == [0.25, 0.75, 1.5] and CFG["meta"]["current"] == 3     # v5 and the versions untouched


def test_period_label_one_function():
    assert V.period_label("2027", "q4_q4") == "2027 (Q4/Q4)"
    assert V.period_label("2027", "annual_average") == "2027 (annual average)"
    assert V.period_label("2027Q4", "yoy_quarter") == "2027Q4 (y/y)"
    assert V.period_label("FY2027", "fiscal_year") == "FY2027 (Apr 2027 to Mar 2028)"
    assert V.period_label("M2027", "year_average_march") == "year to Mar 2027"
    assert V.period_label("2027Q4", "quarter_level") == "2027Q4"
    assert V.period_label("2026-09", "") == "2026-09"
    for f in ("payload.py", "stance_v6.py"):                                           # no second period formatter for v6
        assert "def period_label" not in (ROOT / "src" / "cb_compute" / f).read_text() or f == "stance_v6.py"


# ---- How to read ----

def test_how_to_read_text_with_thresholds_from_config():
    from src.cb_compute.stance_ui import how_to_read
    t = how_to_read(CFG["v6"]["scale_pp"], 2)
    assert t[0] == ("Position = (policy rate − the bank's inflation forecast) − (neutral rate − target): the real policy rate minus the bank's "
                    "real neutral rate. Need = the bank's inflation forecast − target. The forecast is the bank's projection for the published "
                    "period that contains today + 12 months; the cell shows its measure, its period and the rate path it assumes. Position and "
                    "Need use the same forecast, so they are not two separate signals.")
    assert t[1] == ("Same scale for both: within ±0.25 pp neutral / near target, up to 0.75 slightly, up to 1.5 plain, beyond that very / well. "
                    "Core inflation and the output gap today are shown under Need for comparison.")
    assert t[2] == ("Why gives three readings, not a forecast of the decision. Taylor rule: neutral + 1.5 × (inflation − target) + 0.5 × output "
                    "gap − policy rate, in pp, once with core inflation today and once with the bank's forecast, each across the bank's neutral "
                    "range. Positive: the rule points to a higher rate; negative: to a lower one. Opposite signs: mixed signals. Same sign and "
                    "all four ends on the same side of zero: robust to the neutral range. Otherwise: sensitive to the neutral range.")
    assert t[3] == ("Bank: the rate path the bank publishes (Fed dots, RBNZ OCR track) or the rate path its forecast assumes, and the vote at "
                    "the last decision. Market: the move priced by each of the next two meetings (bp vs the current rate), its change over one "
                    "week, and the market minus the bank's path at the same date. Market data older than 2 business days is marked stale.")
    other = how_to_read([0.3, 0.8, 1.6], 3)
    assert "±0.3 pp" in other[1] and "up to 0.8" in other[1] and "up to 1.6" in other[1] and "older than 3 business days" in other[3]


def test_how_to_read_in_the_frozen_payload(frozen):
    _, ov, _ = frozen
    assert "older than 2 business days" in ov["stance"]["how_to_read"][3] and ov["meta"]["stale_after_bd"] == 2


# ---- history ----

def test_history_derivation_for_old_rows():
    from src.cb_compute.stance_ui import history_points
    d = date(2026, 11, 15)
    old = {"snapshot_date": d, "S": 0.675, "gap_12m": 0.5, "v5_position": 0.675, "v5_need": 1.1}
    assert history_points([old]) == [{"date": "2026-11-15", "position": 0.175, "need": 0.5}]
    no_n = {"snapshot_date": d, "S": None, "gap_12m": 0.1, "v5_position": None, "v5_need": 0.9}
    assert history_points([no_n]) == [{"date": "2026-11-15", "position": None, "need": 0.1}]
    new = {"snapshot_date": date(2026, 11, 24), "S": 0.675, "gap_12m": 0.5, "v6_position": 0.2, "v6_need": 0.6, "v6_reading": "x"}
    assert history_points([new, old])[1] == {"date": "2026-11-24", "position": 0.2, "need": 0.6}


def test_history_row_has_the_v6_columns():
    from datetime import datetime, timezone
    from src import cb_stance_history as SH
    b = copy.deepcopy(FIX["banks"]["USD"])
    r = dict(b["r"], v6=V.evaluate(b["r"], b["d"]), v5={"P": 0.675, "N": 1.1, "conclusion": "c", "reason": "r"}, mandate_id="m",
             code_version="s1.1", stance_version=3, inputs={}, cycle={}, market={}, flags_12m=[], x=None)
    row = SH.row_of(r, date(2026, 11, 20), datetime(2026, 11, 20, tzinfo=timezone.utc))
    assert row["v6_position"] == r["v6"]["position"]["P"] and row["v6_need"] == r["gap_12m"]
    assert row["v6_reading"] == "Tightening pressure, sensitive to the neutral range"
    assert (row["G_12m_lo"], row["G_12m_hi"]) == (r["G_12m_lo"], r["G_12m_hi"])
    assert row["v5_position"] == 0.675 and row["v5_conclusion"] == "c"                 # v5 still written
    assert set(SH.SCHEMA.names) >= {"v6_position", "v6_need", "v6_reading", "G_12m_lo", "G_12m_hi", "v5_position", "v5_reason"}


# ---- JS ----

def test_stance_js():
    r = subprocess.run(["node", str(ROOT / "tests" / "cb_js" / "test_stance_v6.cjs")], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_js_fixture_is_the_frozen_payload(frozen):
    """tests/cb_js/fixtures/stance_v6_2026-11-20.json = the overview stance frozen at 2026-11-20 (regenerate it when the payload changes)."""
    _, ov, _ = frozen
    fx = json.loads((ROOT / "tests" / "cb_js" / "fixtures" / "stance_v6_2026-11-20.json").read_text())
    assert fx["banks"] == ov["stance"]["banks"]


def test_frozen_fixture_is_complete():
    """The frozen file carries every argument of stance_page_v6 for the 8 banks (r without its v6 key) and scale_pp."""
    import inspect
    params = set(inspect.signature(stance_page_v6).parameters)
    assert FIX["asof"] == "2026-11-20" and FIX["scale_pp"] == CFG["v6"]["scale_pp"] and set(FIX["banks"]) == set(BANKS)
    for c, b in FIX["banks"].items():
        assert set(b) == (params - {"r"}) | {"r", "d"}, c
        assert "v6" not in b["r"] and "v5" in b["r"]


# ---- live data (a render at the newest as-of): only invariants valid for any data ----

@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from src import cb_render as R
    pub = tmp_path_factory.mktemp("live")
    R.render(None, pub)
    ov = json.loads((pub / "data" / "cb" / "overview.json").read_text())
    return ov, {c: json.loads((pub / "data" / "cb" / f"{c.lower()}.json").read_text())["stance"] for c in BANKS}


def test_live_stance_has_no_na_and_three_why_rows(live):
    ov, pages = live
    assert ov["stance"].get("na") is None and set(ov["stance"]["banks"]) == set(BANKS)
    for c, s in pages.items():
        assert s.get("na") is None and list(s["why"]) == ["taylor", "bank", "market"], c
        assert [s["why"][k]["label"] for k in ("taylor", "bank", "market")] == ["Taylor rule", "Bank", "Market"]
        assert all(s["why"][k]["lines"] for k in ("taylor", "bank", "market"))


def test_live_market_row_is_the_by_meeting_table(live):
    ov, pages = live
    for c, s in pages.items():
        pj, x = ov["paths"][c], s["why"]["market"].get("x") or {}
        w1 = {p["meeting"]: p for p in (pj.get("history", {}).get("1w") or {}).get("points") or []}
        for j, p in enumerate(pj["points"][:2], 1):
            assert x.get(f"m{j}_cum") == p.get("cum_bp"), c                     # "Δ vs now"
            hp = w1.get(p["meeting"])
            want = (p["rate"] - hp["rate"]) * 100 if hp and hp.get("rate") is not None and p.get("rate") is not None else None
            assert x.get(f"m{j}_1w") == want, c                                 # "Δ vs 1w", the expression of pathTableRows


def test_live_stale_badge_exactly_when_the_overview_row_is_stale(live):
    ov, pages = live
    rows = {b["ccy"]: b for b in ov["banks"]}
    for c, s in pages.items():
        has = any(isinstance(g, dict) and "stale" in g for l in s["why"]["market"]["lines"] for g in l["segs"])
        assert has == bool((rows[c].get("horizon") or {}).get("stale")), c


def test_live_payload_has_only_the_displayed_keys(live):
    ov, pages = live
    assert set(ov["stance"]) == {"how_to_read", "scale_pp", "na", "banks"}
    for c, b in ov["stance"]["banks"].items():
        assert set(b) == {"bank", "asof", "rate_display", "position", "need", "why"}, c
        assert set(b["position"]) <= {"v", "lo", "hi", "label", "na", "lines", "title", "x"}
        assert set(b["need"]) <= {"v", "label", "na", "lines", "title", "x"}
        assert all(set(b["why"][k]) <= {"label", "reading", "lines", "na", "x"} for k in ("taylor", "bank", "market"))
    for c, s in pages.items():
        assert set(s) == {"bank", "asof", "rate_display", "position", "need", "why", "mandate", "next_test", "history", "how_to_read", "scale_pp"}, c


def test_live_how_to_read_takes_the_thresholds_from_config(live):
    from src.cb_compute.stance_ui import how_to_read
    ov, _ = live
    assert ov["stance"]["how_to_read"] == how_to_read(CFG["v6"]["scale_pp"], ov["meta"]["stale_after_bd"])
