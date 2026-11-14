"""feat/macro-momentum — regime v1 (three-valued, percentiles, splice), trend/range,
cycle root, as-of lags of the monthly data, macro_momentum construction, episodes and
control of attempt #3, registry snapshot + scoring, and leakage."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.tree.data import Inputs
from src.tree.engine import classify, eval_nodes
from src.tree.macro import MacroInputs, derived
from src.tree.nodes import NodeResult
from src.tree.regimes import load_v1, trend_range

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _r(nid, v=None, missing=False):
    return NodeResult(nid, v, None, "2020-01-03", "2020-01-02", "missing" if missing else "ok", "context",
                      {"reason": "test"} if missing else {})


# ---------------------------------------------------------------------------
# Regime v1
# ---------------------------------------------------------------------------

def test_regime_v1_rules_and_three_valued_logic():
    cls = load_v1()["classifiers"]["risk_regime_v1"]
    base = {"vix_term": _r("vix_term", 0.9), "vix_pct": _r("vix_pct", 50), "baa_w20_pct": _r("baa_w20_pct", 50),
            "eq20_pct": _r("eq20_pct", 50), "d10y20_pct": _r("d10y20_pct", 50)}
    assert classify(cls, base)["regime"] == "calm"
    assert classify(cls, {**base, "vix_term": _r("vix_term", 1.0)})["regime"] == "liquidity_stress"
    pre2007 = {**base, "vix_term": _r("vix_term", missing=True), "vix_pct": _r("vix_pct", 96),
               "baa_w20_pct": _r("baa_w20_pct", 97)}
    assert classify(cls, pre2007)["regime"] == "liquidity_stress"           # VIX / credit branch decides alone
    calm_pre = {**pre2007, "vix_pct": _r("vix_pct", 60)}
    r = classify(cls, calm_pre)
    assert r["regime"] == "calm" and r["undetermined"] == ["liquidity_stress"] and r["degraded"]
    assert classify(cls, {**base, "eq20_pct": _r("eq20_pct", 10), "d10y20_pct": _r("d10y20_pct", 90)})["regime"] == "inflation_shock"
    assert classify(cls, {**base, "eq20_pct": _r("eq20_pct", 5), "d10y20_pct": _r("d10y20_pct", 10)})["regime"] == "growth_scare"
    assert classify(cls, {**base, "eq20_pct": _r("eq20_pct", 11), "d10y20_pct": _r("d10y20_pct", 99)})["regime"] == "calm"


def _daily(name, values, start="2005-01-03"):
    d = pd.bdate_range(start, periods=len(values))
    return pd.DataFrame({"series": name, "date": d, "avail": d + pd.Timedelta(days=1), "value": values})


def test_derived_pctile_splices_the_equity_series_and_ignores_the_future():
    n = 3000
    nas = _daily("NASDAQCOM", np.linspace(100, 200, n))
    spx = _daily("SP500", np.linspace(100, 50, n))                         # falls after the splice
    cfg = load_v1()
    node = [x for x in cfg["nodes"] if x["id"] == "eq20_pct"][0]
    node = {**node, "params": {**node["params"], "splice": {"after": "2012-12-31"}}}
    inp = Inputs(pd.concat([nas, spx]))
    before = eval_nodes([node], inp, pd.Timestamp("2012-06-01"))["eq20_pct"]
    after = eval_nodes([node], inp, pd.Timestamp("2014-06-02"))["eq20_pct"]
    assert before.value > 0 and after.value < 50                           # NASDAQ rising, then SP500 falling
    fut = pd.concat([nas, spx])
    fut.loc[fut["avail"] > "2012-06-01", "value"] *= -1
    assert eval_nodes([node], Inputs(fut), pd.Timestamp("2012-06-01"))["eq20_pct"].value == before.value


def test_trend_range_on_synthetic_baskets():
    d = pd.bdate_range("2010-01-01", "2016-12-31")
    rng = np.random.default_rng(0)
    noise = pd.DataFrame(rng.normal(0, 0.003, (len(d), 4)).cumsum(axis=0), index=d, columns=list("ABCD"))
    trend = noise.copy()
    trend.loc["2015":] = noise.loc["2015":] + np.linspace(0, 0.3, len(noise.loc["2015":]))[:, None] * [1, -1, 1, -1]
    fr = pd.date_range("2010-01-08", "2016-12-30", freq="7D")
    tr = trend_range(trend, fr)
    assert tr["regime"].iloc[:104].isna().all()                             # needs 2 years of history
    late = tr.loc["2015-06":"2016-12", "regime"].value_counts()
    assert late.get("trend", 0) > late.get("range", 0)
    assert tr["vol_pct"].dropna().between(0, 100).all()


# ---------------------------------------------------------------------------
# Monthly data: lags, YoY, macro_momentum, cycle root
# ---------------------------------------------------------------------------

def test_fetch_lags_and_date_aligned_yoy():
    f = _load("fetch_oecd_macro", "scripts/fetch_oecd_macro.py")
    assert f.LAG_DAYS == {"cpi": 28, "cpi_core": 28, "unemp": 42, "emp": 42, "ip": 42, "cli": 42, "bci": 14}
    per = pd.date_range("2023-01-31", "2025-12-31", freq="ME").delete(33)  # a missing month (2025-10)
    df = pd.DataFrame({"area": "USA", "series": "cpi", "freq": "M", "source": "x", "period": per,
                       "value": 100 * 1.02 ** (np.arange(len(per)) / 12)})
    y = f.with_yoy(df).set_index("period")["yoy"]
    assert np.isnan(y["2025-10-31"]) if "2025-10-31" in y.index else True
    assert not np.isnan(y["2024-12-31"]) and y["2025-12-31"] == pytest.approx(
        (df.set_index("period")["value"]["2025-12-31"] / df.set_index("period")["value"]["2024-12-31"] - 1) * 100)


def _macro_frame():
    per = pd.date_range("2000-01-31", "2020-12-31", freq="ME")
    rng = np.random.default_rng(5)
    rows = []
    for s, lag in (("cpi", 28), ("cpi_core", 28), ("unemp", 42), ("emp", 42), ("ip", 42), ("bci", 14)):
        v = 100 + np.cumsum(rng.normal(0, 0.3, len(per)))
        g = pd.DataFrame({"ccy": "USD", "area": "USA", "series": s, "freq": "M", "period": per, "value": v,
                          "source": "x", "avail": per + pd.Timedelta(days=lag)})
        g["yoy"] = (g["value"] / g["value"].shift(12) - 1) * 100
        rows.append(g)
    return pd.concat(rows, ignore_index=True)


def test_monthly_visibility_respects_the_publication_lag():
    mi = MacroInputs(_macro_frame())
    v = mi.visible("USD", "cpi", "2010-03-15")
    assert v["period"].max() == pd.Timestamp("2010-01-31")                 # Feb CPI due 2010-03-28
    assert mi.visible("USD", "bci", "2010-03-15")["period"].max() == pd.Timestamp("2010-02-28")


def test_macro_momentum_construction_and_leakage():
    fr = _macro_frame()
    mi = MacroInputs(fr)
    t = pd.Timestamp("2015-06-12")
    m = mi.momentum("USD", t)
    # z of infl_head by hand: last 60 derived values of the visible frame
    g = mi.visible("USD", "cpi", t)
    d = derived(g, "yoy_d").dropna().iloc[-60:]
    assert m["z"]["infl_head"] == pytest.approx((d.iloc[-1] - d.mean()) / d.std(ddof=1))
    cats = m["categories"]
    assert cats["Inflation"] == pytest.approx(np.mean([m["z"]["infl_head"], m["z"]["infl_core"]]))
    assert m["composite_eq"] == pytest.approx(np.mean([cats["Inflation"], cats["Labor"], cats["Growth"]]))
    assert m["composite_il"] == pytest.approx(np.mean([cats["Inflation"], cats["Labor"]]))
    assert m["z"]["gr_cli"] is None and m["composite_cli"] is not None    # no CLI -> Growth_cli = ip only
    fut = fr.copy()
    fut.loc[fut["avail"] > t, "value"] *= 5
    assert MacroInputs(fut).momentum("USD", t) == m
    stale = fr[~((fr["series"] == "cpi") & (fr["period"] > "2014-06-30"))]
    assert MacroInputs(stale).momentum("USD", t)["z"]["infl_head"] is None  # last visible print > 200 days old


def test_cycle_root_phase_and_impulses():
    idx = pd.date_range("2018-01-01", "2021-12-31")
    pol = pd.Series(1.0, index=idx)
    pol["2019-03-01":] = 1.25
    pol["2020-03-16":] = 0.25
    mi = MacroInputs(_macro_frame(), {"USD": pol})
    assert mi.phase("USD", "2019-06-07") == "tightening"
    assert mi.phase("USD", "2020-03-06") == "hold_top"                     # > 365 d since the hike
    assert mi.phase("USD", "2020-03-20") == "easing"
    assert mi.phase("USD", "2021-06-04") == "hold_bottom"
    assert mi.phase("USD", "2018-06-01") is None                           # no change seen yet
    cy = mi.cycle("USD", "2019-06-07")
    assert cy["phase"] == "tightening" and cy["inflation_source"] == "core"
    assert cy["cell16"] == f"tightening|g_{cy['growth']}|i_{cy['inflation']}"
    assert cy["cell8"] == f"tightening|i_{cy['inflation']}"


# ---------------------------------------------------------------------------
# Attempt #3: episodes and control
# ---------------------------------------------------------------------------

def test_episodes_use_a_four_week_exclusion_and_control_needs_both_conditions():
    p = _load("phase3", "scripts/measure/phase3.py")
    fr = pd.date_range("2024-01-05", periods=10, freq="7D")
    m = pd.DataFrame({"USD": [1, 1, 1, 1, 1, 0, 0, 1, 0, 0], "EUR": [0] * 10}, index=fr).astype(bool)
    picks = p.pick_units(m)
    assert picks[0] == (fr[0], "USD") and picks[1] == (fr[4], "USD")       # weeks 1-3 excluded
    assert all((b[0] - a[0]).days >= 28 for a, b in zip(picks, picks[1:]))
    g = pd.DataFrame({"USD": [0, 1, 0], "EUR": [0, 0, 0]}, index=fr[:3])
    past = pd.DataFrame({"USD": [-50, -50, 10], "EUR": [-50, 5, -60]}, index=fr[:3])
    q = pd.Series([-20, -20, -20], index=fr[:3])
    c = p.control_mask(g, past, q)
    assert c.to_dict(orient="list") == {"USD": [True, False, False], "EUR": [True, False, True]}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_snapshot_and_scoring(tmp_path, monkeypatch):
    snap = _load("snapshot", "scripts/registry/snapshot.py")
    score = _load("score", "scripts/registry/score.py")
    payload = tmp_path / "economic.json"
    payload.write_text(json.dumps({"as_of": "2026-10-30T16:00:00", "currencies": {
        c: {"score_v3": float(i)} for i, c in enumerate(snap.CCYS)}}))
    monkeypatch.setattr(snap, "macro_scores", lambda asof, mi=None: {c: float(-i) for i, c in enumerate(snap.CCYS)})
    monkeypatch.setattr(snap, "carry_scores", lambda asof, backbone=None: {c: 1.0 for c in snap.CCYS})
    calls = tmp_path / "calls.parquet"
    a = pd.Timestamp("2026-01-02")
    df = snap.snapshot(a, calls, payload, with_v4=False)
    df = snap.snapshot(a, calls, payload, with_v4=False)                                  # idempotent per asof
    assert len(df) == 3 * 8 * 4
    v3 = df[(df.source == "v3") & (df.horizon_weeks == 2)].set_index("ccy")
    assert v3.loc["CHF", "rank"] == 1 and v3.loc["USD", "rank"] == 8
    assert (df["due"] == a + pd.to_timedelta(df["horizon_weeks"] * 7, unit="D")).all()
    d = pd.bdate_range("2025-12-01", "2026-02-20")
    lv = pd.DataFrame({c: np.linspace(0, 0.01 * (i - 3.5), len(d)) for i, c in enumerate(snap.CCYS)}, index=d)
    sc = score.score(pd.read_parquet(calls), lv, now="x")
    done = sc.dropna(subset=["realized_bp"])
    assert set(done["horizon_weeks"]) == {2, 4}                            # 8 / 13 weeks not due yet
    s = score.summary(sc)
    assert s.set_index(["source", "h"]).loc[("v3", 2), "mean_IC"] == pytest.approx(1.0)
    assert s.set_index(["source", "h"]).loc[("macro_momentum", 2), "mean_IC"] == pytest.approx(-1.0)


def test_rbnz_manual_import_finds_the_2y_column(tmp_path):
    from openpyxl import Workbook
    imp = _load("import_rbnz_b2", "scripts/import_rbnz_b2.py")
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append(["Series", "Govt bond 1 year", "Govt bond 2 year", "Swap 2 year"])
    ws.append(["Units", "%", "%", "%"])
    for i, d in enumerate(pd.bdate_range("2024-01-01", periods=5)):
        ws.append([d.to_pydatetime(), 4.0, 4.5 + i / 100, 4.2])
    wb.save(tmp_path / "hb2-daily-close.xlsx")
    df, note = imp.load_manual(tmp_path)
    assert len(df) == 5 and df["value"].iloc[-1] == pytest.approx(4.54)
    prov = json.loads((tmp_path / "PROVENANCE.json").read_text())
    assert prov["files"][0]["column"] == "Govt bond 2 year" and len(prov["files"][0]["sha256"]) == 64
