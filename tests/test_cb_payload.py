"""Phase 3a: the payloads behind /central-banks and their render. At --asof 2026-09-18 (data frozen in tests/fixtures/cb_engine) the JSON
carries the same values as the 1B-2 report; every n/a has its reason, every flag is propagated, and the render is deterministic."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from src import cb_render
from src.cb_compute import payload as P
from src.cb_loader import load_context, load_pair_defs

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(__file__).parent / "fixtures" / "cb_engine"
ASOF = date(2026, 9, 18)
ORDER = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]


@pytest.fixture(scope="module")
def built():
    return P.build(load_context(FIX), ASOF, load_pair_defs())


def rows(built):
    return {r["ccy"]: r for r in built["overview"]["banks"]}


def walk_metrics(node, path=""):
    """Every {"v": ..., "na": ...} dict of a payload."""
    if isinstance(node, dict):
        if "v" in node and "na" in node and "stale" in node:
            yield path, node
        for k, v in node.items():
            yield from walk_metrics(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_metrics(v, f"{path}[{i}]")


# --- schema ----------------------------------------------------------------------------------------------------------------

def test_overview_has_the_eight_banks_in_order_with_the_full_row(built):
    ov = built["overview"]
    assert [r["ccy"] for r in ov["banks"]] == ORDER and ov["meta"]["asof"] == "2026-09-18" and ov["meta"]["stale_after_bd"] == 2
    keys = {"ccy", "bank", "href", "rate", "next", "horizon", "end", "gap", "repricing", "reaction", "last_decision", "stale", "na"}
    for r in ov["banks"]:
        assert keys <= set(r) and r["href"] == f"/central-banks/{r['ccy'].lower()}.html"
        assert set(r["end"]) == {"2026", "2027"} and set(r["repricing"]) == {"1w", "1m"} and set(r["reaction"]) >= {"next", "year"}
    assert set(ov["meta"]["flags"]) == {"EXACT", "CURVE", "UPPER_BOUND", "PROXY", "DECIDED"} and ov["meta"]["flags"]["UPPER_BOUND"]["label"] == "UPPER BOUND"
    assert all(f["help"] for f in ov["meta"]["flags"].values()) and len(ov["meta"]["methodology"]) >= 8


def test_payloads_are_strict_json_no_nan(built):
    for name, obj in [("overview", built["overview"]), ("pairs", built["pairs"])] + list(built["banks"].items()):
        json.dumps(obj, allow_nan=False)


def test_every_metric_carries_its_reason_when_n_a_and_none_when_present(built):
    n = 0
    for name, obj in [("overview", built["overview"]), ("pairs", built["pairs"])] + list(built["banks"].items()):
        for path, m in walk_metrics(obj):
            n += 1
            if m["v"] is None:
                assert isinstance(m["na"], str) and m["na"], (name, path)
            else:
                assert m["na"] is None, (name, path)
    assert n > 400


# --- key values = the 1B-2 report ------------------------------------------------------------------------------------------

def test_usd_row_matches_the_report(built):
    r = rows(built)["USD"]
    assert r["rate"]["range"] and (r["rate"]["lower"], r["rate"]["upper"], r["rate"]["value"]) == (3.75, 4.0, 3.875) and not r["rate"]["pending"]
    h = r["horizon"]
    assert (h["kind"], h["flag"], h["cum_bp"], h["n_meetings"], h["upper_bound"], h["interior"]) == ("window", "UPPER_BOUND", 41.5, 3, True, 1)
    assert h["window"] == ["2026-12-16", "2027-03-17"]
    assert (r["end"]["2026"]["v"], r["end"]["2026"]["flag"]) == (41.5, "UPPER_BOUND") and r["end"]["2027"]["v"] == pytest.approx(74.3, abs=0.05)
    assert r["gap"]["kind"] == "dots" and {y: m["v"] for y, m in r["gap"]["years"].items()} == {"2026": 16.5, "2027": 49.3, "2028": 60.0}
    assert r["gap"]["headline"]["v"] == 16.5 and r["gap"]["sep"] == "2026-09-16"
    rp = r["repricing"]
    assert rp["1w"]["years"]["2026"]["v"] == pytest.approx(7.5, abs=0.05) and rp["1w"]["years"]["2026"]["cum_bp"] == pytest.approx(-17.5, abs=0.05) and rp["1w"]["base_change_bp"] == 25.0
    assert rp["1m"]["years"]["2027"]["v"] == pytest.approx(59.4, abs=0.05) and rp["1w"]["step"]["v"] is None and "UPPER_BOUND" in rp["1w"]["step"]["na"]
    assert r["last_decision"]["delta_bp"] == 25.0 and r["last_decision"]["date"] == "2026-09-16" and r["reaction"]["next"]["v"] == 4.5


def test_step_banks_carry_the_probabilities(built):
    rs = rows(built)
    gbp, aud, cad = rs["GBP"]["horizon"], rs["AUD"]["horizon"], rs["CAD"]["horizon"]
    assert (gbp["kind"], gbp["flag"], gbp["direction"]) == ("step", "CURVE", "hike") and gbp["step_bp"] == pytest.approx(20.4, abs=0.05)
    assert {p["moves"]: p["p"] for p in gbp["probabilities"]} == pytest.approx({0: 0.186, 1: 0.814}, abs=0.001)
    assert aud["step_bp"] == pytest.approx(21.5, abs=0.05) and aud["flag"] == "EXACT" and {p["moves"]: p["p"] for p in aud["probabilities"]} == pytest.approx({0: 0.14, 1: 0.86}, abs=0.001)
    assert cad["step_bp"] == pytest.approx(14.0, abs=0.05) and cad["flag"] == "EXACT" and {p["moves"]: p["p"] for p in cad["probabilities"]} == pytest.approx({0: 0.44, 1: 0.56}, abs=0.001)
    assert (rs["CAD"]["end"]["2026"]["flag"], rs["CAD"]["end"]["2027"]["flag"]) == ("EXACT", "UPPER_BOUND")
    assert rs["GBP"]["repricing"]["1w"]["step"]["v"] == pytest.approx(2.0, abs=0.05) and rs["GBP"]["repricing"]["1w"]["step"]["meeting"] == "2026-11-05"


def test_eur_proxy_shows_levels_and_the_reason_not_bp(built):
    r = rows(built)["EUR"]
    assert r["horizon"]["kind"] == "na" and r["horizon"]["na"].startswith("proxy without short end") and r["horizon"]["level_kind"] == "sovereign_proxy"
    assert r["horizon"]["level"] == 2.762 and r["horizon"]["flag"] == "PROXY"
    for y, lvl in (("2026", 2.872), ("2027", 3.339)):
        e = r["end"][y]
        assert e["v"] is None and e["na"].startswith("proxy without short end") and e["level"] == lvl and e["level_kind"] == "sovereign_proxy" and e["flag"] == "PROXY"
    assert r["repricing"]["1w"]["years"]["2026"]["v"] == pytest.approx(-0.7, abs=0.05) and r["repricing"]["1w"]["years"]["2026"]["flag"] == "PROXY"         # deltas need no basis
    assert r["reaction"]["next"]["v"] == pytest.approx(9.7, abs=0.05) and r["reaction"]["next"]["flag"] == "PROXY"


def test_jpy_pending_decision_and_variable_time(built):
    r = rows(built)["JPY"]
    assert r["rate"]["pending"] and r["rate"]["value"] == 1.25 and r["rate"]["from"] == "2026-09-24" and r["rate"]["in_force"] == 1.0
    assert r["rate"]["decision_status"] == "ff_pending"                                                                       # not confirmed by an official series yet
    t = r["next"]["time"]
    assert t["tbd"] and t["utc"] is None and t["window_local"] == ["11:30", "13:30"] and t["abbr"] == "JST" and r["next"]["decision"] == "2026-10-30"
    assert r["next"]["blackout"]["verified"] is False and r["next"]["blackout"]["precision"] == "exact"


def test_fixed_decision_time_and_blackout_are_utc_instants(built):
    n = rows(built)["USD"]["next"]
    assert n["decision"] == "2026-10-28" and n["effective"] == "2026-10-29" and n["time"]["utc"] == "2026-10-28T18:00:00Z" and n["time"]["abbr"] == "EDT"
    assert n["blackout"]["start_utc"] == "2026-10-17T04:00:00Z" and n["blackout"]["end_utc"] == "2026-10-30T03:59:00Z"
    assert n["has_presser"] and n["conference"]["local"] == "14:30" and n["has_projections"] is False


def test_nzd_and_chf(built):
    nzd, chf = rows(built)["NZD"], rows(built)["CHF"]
    assert nzd["horizon"]["kind"] == "na" and "BKBM" in nzd["horizon"]["na"] and nzd["horizon"]["level"] == 3.45 and nzd["horizon"]["level_kind"] == "bkbm"
    assert nzd["end"]["2026"]["level"] == 3.45 and nzd["end"]["2027"]["level"] == 3.8 and nzd["gap"]["kind"] == "n/a" and "90-day" in nzd["gap"]["na"]
    assert chf["horizon"] == {"kind": "na", "na": "no market path for this currency", "flag": None, "stale": False} and chf["na"] == "no market path for this currency"
    assert chf["end"]["2026"]["v"] is None and chf["end"]["2026"]["na"] == "no market path for this currency"


def test_stale_flag_is_carried_from_the_engine(built):
    assert not any(r["stale"] for r in built["overview"]["banks"])
    later = P.build(load_context(FIX), date(2026, 9, 24), load_pair_defs(), currencies=("GBP",))
    row = later["overview"]["banks"][0]
    assert row["stale"] and row["horizon"]["stale"] and row["end"]["2026"]["stale"]                                            # BoE OIS snapshot is 5 business days old


# --- bank page ---------------------------------------------------------------------------------------------------------------

def test_bank_page_sections(built):
    d = built["banks"]["USD"]
    assert d["summary"]["ccy"] == "USD" and d["meta"]["asof"] == "2026-09-18" and d["bank"]["short"] == "Fed"
    assert [x["date"] for x in d["decisions"]] == ["2026-09-16", "2026-07-29", "2026-06-17", "2026-04-29"]                       # last 4, most recent first
    first = d["decisions"][0]
    assert first["delta_bp"] == 25.0 and first["rate_after"] == 3.875 and (first["lower"], first["upper"]) == (3.75, 4.0) and first["effective"] == "2026-09-17"
    assert first["slots"] == {"votes": None, "statement": None, "conference": None}                                            # phase 2
    assert first["vs_market"]["v"] is None and "only EXACT / CURVE / PROXY" in first["vs_market"]["na"]
    assert first["reaction"]["next"]["v"] == 4.5 and first["reaction"]["next"]["target"] == "2026-10-28"
    assert d["calendar"][0]["decision"] == "2026-10-28" and d["calendar"][0]["blackout"]["verified"] and len(d["calendar"]) == 10
    assert {s["id"] for s in d["sources"]} == {"atlantafed_mpt", "ust_bills"} and all(s["license"] for s in d["sources"])
    assert d["spread"]["bp"] == 2.0 and d["spread"]["n"] == 16 and set(d["horizons"]) == {"2026", "2027"}
    assert d["horizons"]["2026"]["gap"]["v"] == 16.5 and d["horizons"]["2026"]["repricing"]["1w"]["v"] == pytest.approx(7.5, abs=0.05)
    assert d["crosschecks"] and all(c["na"] is None and c["diff_bp"] is not None for c in d["crosschecks"])


def test_chart_series(built):
    c = built["banks"]["USD"]["chart"]
    assert c["history"][0]["opening"] and c["history"][-1] == {"date": "2026-09-17", "rate": 3.875, "lower": 3.75, "upper": 4.0, "decided": "2026-09-16"}
    assert [p["method"] for p in c["market"][:2]] == ["WINDOW", "WINDOW"] and c["market"][0]["window"] == ["2026-12-16", "2027-03-17"] and c["market"][0]["upper_bound"]
    dots = c["bank"]
    assert dots["kind"] == "dots" and [y["year"] for y in dots["years"]] == [2026, 2027, 2028] and dots["years"][0]["median"] == 4.125
    assert dots["years"][0]["dots"] == [{"level": 4.375, "count": 4}, {"level": 4.125, "count": 12}, {"level": 3.875, "count": 2}]
    assert c["meetings"][0] == {"decision": "2026-10-28", "effective": "2026-10-29"}
    nzd = built["banks"]["NZD"]["chart"]["bank"]
    assert nzd["kind"] == "ocr_track" and len(nzd["quarters"]) == 13 and nzd["quarters"][0] == {"period": "2026Q3", "value": 2.6} and nzd["finalised"] == "2026-08-26"
    gbp = built["banks"]["GBP"]["chart"]
    assert gbp["bank"]["kind"] == "n/a" and [p["method"] for p in gbp["market"]][:2] == ["CURVE", "CURVE"] and gbp["market"][0]["rate"] == 3.954
    eur = built["banks"]["EUR"]["chart"]["market"][0]
    assert eur["rate"] is None and eur["level"] == 2.762 and eur["level_kind"] == "sovereign_proxy" and eur["na"].startswith("proxy without short end")


def test_unverified_markers_are_labelled(built):
    assert {u["label"] for u in built["banks"]["NZD"]["unverified"]} == {"effective date derived", "decision time unverified"}
    assert [u["label"] for u in built["banks"]["JPY"]["unverified"]] == ["blackout approximate"]
    assert built["banks"]["USD"]["unverified"] == [] and all(u["text"] for b in built["banks"].values() for u in b["unverified"])


# --- pairs ---------------------------------------------------------------------------------------------------------------------

def test_pairs_payload(built):
    ps = {p["pair"]: p for p in built["pairs"]["pairs"]}
    assert len(ps) == 28 and ps["EURUSD"]["href"] == "/central-banks/pair/eurusd.html" and ps["EURUSD"]["display"] == "EUR/USD"
    eurusd = ps["EURUSD"]
    assert eurusd["current"]["v"] == -137.5 and eurusd["implied"]["2026"]["diff_bp"]["v"] is None
    assert eurusd["implied"]["2026"]["cum_bp"]["na"].startswith("EUR: proxy without short end")
    assert eurusd["repricing"]["1w"]["2026"]["v"] == -8.1 and eurusd["repricing"]["1w"]["2026"]["flag"] == "PROXY" and eurusd["flag"] == "PROXY"
    assud = ps["AUDUSD"]
    assert assud["current"]["v"] == 47.5 and assud["implied"]["2026"]["cum_bp"]["v"] == -3.1 and assud["implied"]["2026"]["diff_bp"]["flag"] == "UPPER_BOUND"
    assert assud["repricing"]["1w"]["2026"]["v"] is None and "history starts 2026-09-18" in assud["repricing"]["1w"]["2026"]["na"] and assud["flag"] == "UPPER_BOUND"
    chf = ps["USDCHF"]
    assert chf["current"]["v"] == 387.5 and chf["flag"] is None and chf["implied"]["2027"]["diff_bp"]["na"] == "CHF: no market path for this currency"
    assert ps["AUDCAD"]["flag"] == "UPPER_BOUND" and ps["AUDCAD"]["implied"]["2026"]["diff_bp"]["flag"] == "EXACT"                 # per metric: 2026 EXACT, 2027 upper bound
    assert ps["AUDCAD"]["implied"]["2027"]["diff_bp"]["flag"] == "UPPER_BOUND"
    assert built["pairs"]["banks"]["USD"] == {"short": "Fed", "href": "/central-banks/usd.html"}


def test_pair_flag_is_the_weakest_of_the_legs(built):
    order = ["EXACT", "CURVE", "UPPER_BOUND", "PROXY"]
    ps = {p["pair"]: p for p in built["pairs"]["pairs"]}
    for p in ps.values():
        flags = [m["flag"] for y in p["implied"].values() for m in (y["diff_bp"],) if m["flag"]] + \
                [m["flag"] for w in p["repricing"].values() for m in w.values() if m["flag"]]
        assert p["flag"] == (max(flags, key=order.index) if flags else None), p["pair"]


# --- render ----------------------------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def site(tmp_path_factory):
    pub = tmp_path_factory.mktemp("pub")
    res = cb_render.render(FIX, pub, ASOF)
    return pub, res


def test_render_writes_the_37_pages_and_10_json_files(site):
    pub, res = site
    assert res["asof"] == ASOF and len(res["files"]) == 47 and res["unchanged"] == 0
    assert (pub / "central-banks.html").exists() and len(list((pub / "central-banks").glob("*.html"))) == 8 and len(list((pub / "central-banks" / "pair").glob("*.html"))) == 28
    assert sorted(p.name for p in (pub / "data" / "cb").glob("*.json")) == sorted(["overview.json", "pairs.json"] + [f"{c.lower()}.json" for c in ORDER])
    assert set(p.relative_to(pub).parts[0] for p in pub.rglob("*") if p.is_file()) == {"central-banks.html", "central-banks", "data"}      # nothing else is written


def test_render_is_deterministic_and_only_rewrites_changes(site, tmp_path):
    pub, _ = site
    again = cb_render.render(FIX, pub, ASOF)
    assert again["files"] == [] and again["unchanged"] == 47
    other = tmp_path / "other"
    cb_render.render(FIX, other, ASOF)
    for f in sorted(p for p in pub.rglob("*") if p.is_file()):
        assert f.read_bytes() == (other / f.relative_to(pub)).read_bytes(), f


def test_html_is_a_shell_nothing_hardcoded(site):
    pub, _ = site
    html = (pub / "central-banks" / "usd.html").read_text()
    assert 'data-page="bank"' in html and '"ccy": "USD"' in html and '/data/cb/{ccy}.json' in html and 'id="cbRoot"' in html
    assert '<a href="/central-banks">Central Banks</a>' in html and 'class="active"' in html and "/cb.js" in html and "/chart.umd.min.js" in html
    for forbidden in ("3.875", "4.290", "Federal Reserve", "+41.5"):
        assert forbidden not in html
    over = (pub / "central-banks.html").read_text()
    assert 'data-page="overview"' in over and "/chart.umd.min.js" not in over
    pair = (pub / "central-banks" / "pair" / "eurusd.html").read_text()
    assert 'data-page="pair"' in pair and '"pair": "EURUSD"' in pair and "<title>EUR/USD | Central Banks | Dashboard</title>" in pair


def test_render_cli(tmp_path, capsys):
    assert cb_render.main(["--data-dir", str(FIX), "--public-dir", str(tmp_path / "p"), "--asof", "2026-09-18"]) == 0
    assert "47 written" in capsys.readouterr().out


def test_payload_module_is_pure():
    text = (ROOT / "src" / "cb_compute" / "payload.py").read_text()
    for bad in ("datetime.now", "date.today", "time.time", "open(", "read_text", "write_text", "requests", "yaml."):
        assert bad not in text, bad


# --- assets ---------------------------------------------------------------------------------------------------------------------

def test_static_assets_are_mirrored_in_public_and_the_js_parses():
    for name in ("cb.js", "style.css", "economic-chart.js"):
        assert (ROOT / "static" / name).read_bytes() == (ROOT / "public" / name).read_bytes(), name
    node = shutil.which("node")
    if node:
        for name in ("cb.js", "economic-chart.js"):
            r = subprocess.run([node, "--check", str(ROOT / "static" / name)], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr


def test_navbar_and_economic_cross_link():
    nav = (ROOT / "templates" / "_navbar.html.j2").read_text()
    assert nav.index('href="/carry"') < nav.index('href="/central-banks"') < nav.index('href="/strength"')
    js = (ROOT / "static" / "economic-chart.js").read_text()
    assert '"/central-banks/pair/"' in js and '"/central-banks/"' in js and 'key !== "rate_expectations"' in js and "ev.stopPropagation()" in js
