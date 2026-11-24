"""feat/macro-composite — OECD edition timing and euro-area splice, components on synthetic
cases, the volatility rule, excess returns vs the 15-currency basket, leakage, determinism,
and the validator of George's calls (incl. rejection after the window start)."""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.tree import composite as C

ROOT = Path(__file__).resolve().parents[1]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# components
# ---------------------------------------------------------------------------

def test_excess_employment_monthly_and_quarterly():
    idx = pd.date_range("2000-01-31", periods=100, freq="ME")
    emp = pd.Series(100 * 1.01 ** (np.arange(100) / 12), index=idx)        # steady 1% a year
    assert C.excess_employment(emp, "M") == pytest.approx(0.0, abs=1e-9)
    emp2 = emp.copy()
    emp2.iloc[-3:] *= 1.02                                                   # last 3 months 2% higher
    assert C.excess_employment(emp2, "M") > 0.5
    q = pd.Series(100 * 1.01 ** (np.arange(30) / 4), index=pd.date_range("2000-03-31", periods=30, freq="QE"))
    assert C.excess_employment(q, "Q") == pytest.approx(0.0, abs=1e-9)
    assert C.excess_employment(q.iloc[:20], "Q") is None                     # < 20 quarters of growth history


def test_unemployment_trend_uses_the_previous_60_months():
    idx = pd.date_range("2000-01-31", periods=70, freq="ME")
    u = pd.Series(5.0, index=idx)
    u.iloc[-3:] = 6.0
    assert C.unemployment_trend(u, "M") == pytest.approx(-1.0)              # rising unemployment -> negative
    assert C.unemployment_trend(u.iloc[-62:], "M") is None
    q = pd.Series([5.0] * 20 + [4.0], index=pd.date_range("2000-03-31", periods=21, freq="QE"))
    assert C.unemployment_trend(q, "Q") == pytest.approx(1.0)


def test_external_balance_is_the_12_month_change_of_the_rolling_ratio():
    idx = pd.date_range("2000-01-31", periods=24, freq="ME")
    x = pd.Series(100.0, index=idx)
    m = pd.Series(100.0, index=idx)
    x.iloc[12:] = 120.0
    eb = C.external_balance(x, m, "M")
    assert eb == pytest.approx((1440 - 1200) / (1440 + 1200) - 0.0)


def test_confidence_and_terms_of_trade_respect_t_minus_1():
    idx = pd.date_range("2010-01-31", periods=30, freq="ME")
    b = pd.Series(np.arange(30, dtype=float), index=idx)
    t = pd.Timestamp("2012-03-31")                                            # uses months <= 2012-02
    assert C.confidence(b, t) == pytest.approx(6.0)
    b2 = b.copy()
    b2[b2.index >= "2012-03-31"] = 1e6                                       # month t itself never seen
    assert C.confidence(b2, t) == C.confidence(b, t)
    p = pd.Series(100.0, index=idx)
    p.loc["2012-02-29"] = 110.0
    assert C.terms_of_trade(p, t) == pytest.approx(np.log(1.1))
    assert C.terms_of_trade(p, pd.Timestamp("2014-12-31")) is None            # stale > 12 months


def test_composite_row_clip_labour_vol_rule_and_minimum():
    cc = C.CCYS15
    rng = np.random.default_rng(0)
    raw = {k: pd.Series(rng.normal(size=15), index=cc) for k in
           ("excess_emp", "unemp_trend", "confidence", "external", "tot", "carry")}
    raw["tot"]["USD"] = 1e6                                                 # extreme -> clipped at 3
    z, comp, n = C.composite_row(raw, high_vol=False)
    assert z["tot"].max() <= 3.0
    assert z.loc["EUR", "labour"] == pytest.approx(np.mean([z.loc["EUR", "excess_emp"], z.loc["EUR", "unemp_trend"]]))
    z2, comp2, n2 = C.composite_row(raw, high_vol=True)
    assert n2.max() == 4 and n.max() == 5                                     # carry out in high vol
    assert comp2["EUR"] == pytest.approx(z.loc["EUR", ["labour", "confidence", "external", "tot"]].mean())
    raw2 = {k: v.copy() for k, v in raw.items()}
    for k in ("confidence", "external", "tot"):
        raw2[k]["JPY"] = np.nan
    _, comp3, n3 = C.composite_row(raw2, high_vol=True)
    assert n3["JPY"] == 1 and np.isnan(comp3["JPY"])                          # < 3 components -> not ranked


# ---------------------------------------------------------------------------
# editions
# ---------------------------------------------------------------------------

def test_euro_area_uses_the_widest_code_per_edition_and_measure():
    b = _load("build_oecd_editions", "scripts/research/build_oecd_editions.py")
    d = pd.DataFrame({"REF_AREA": ["EA11", "EA18", "EA11", "EA19", "EA20", "EA19"],
                      "EDITION": ["200501", "201501", "201501", "201901", "202301", "202301"],
                      "MEASURE": ["UNEMP"] * 6, "FREQ": ["M"] * 6,
                      "period": pd.to_datetime(["2004-12-31"] * 6), "value": [1, 2, 3, 4, 5, 6]})
    ea = b.euro_area(d)
    assert dict(zip(ea["EDITION"], ea["REF_AREA"])) == {"200501": "EA11", "201501": "EA18", "201901": "EA19",
                                                       "202301": "EA20"}


def test_edition_cap_picks_the_latest_edition_not_after_t():
    class I(C.P5Inputs):
        def __init__(self):
            self.oecd = pd.DataFrame({"ccy": ["USD"] * 3, "edition": ["202401", "202402", "202403"],
                                      "excess_emp": [1.0, 2.0, 3.0], "unemp_trend": [0.0] * 3, "external": [0.0] * 3})
    ev = I().edition_values("202402")
    assert ev.loc["USD", "edition"] == "202402" and ev.loc["USD", "excess_emp"] == 2.0
    assert np.isnan(ev.loc["EUR", "excess_emp"])


# ---------------------------------------------------------------------------
# returns, vol rule, leakage, determinism (real cached inputs)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def inp():
    return C.P5Inputs()


def test_excess_returns_vs_the_basket_of_15_sum_to_zero(inp):
    m = _load("phase5", "scripts/measure/phase5.py")
    panel = pd.read_parquet(ROOT / "data" / "research" / "composite_panel.parquet")
    panel = panel[panel["month"].between("2015-01", "2015-06")]
    r = m.monthly_returns(inp, panel, 1)
    row = r.dropna(how="all").iloc[0].dropna()
    assert len(row) == 15 and abs(row.sum()) < 1e-6                         # each minus the mean of the other 14
    t0, t1 = panel["asof"].unique()[:2]
    mv = (inp.fx.loc[t1] - inp.fx.loc[t0]) * 1e4
    rates = pd.Series({c: inp.rate_at(c, pd.Timestamp(t0)) for c in C.CCYS15})
    ex = mv + (rates - rates["USD"]) / 12 * 100
    assert row["JPY"] == pytest.approx(ex["JPY"] - ex.drop("JPY").mean())


def test_vol_flags_use_the_previous_60_months(inp):
    v = inp.fx_vol_monthly()
    f = inp.high_vol_flags()
    per = pd.Period("2008-10", "M")
    i = list(v.index).index(per)
    prev = v.iloc[i - 60:i]
    assert f[per] == bool((prev <= v[per]).mean() * 100 >= 80)
    assert f[per]                                                              # Oct 2008 is high vol


def test_signals_do_not_see_after_asof(inp):
    t = pd.Timestamp("2015-06-30")
    a = C.signals_at(inp, t, "201506", pd.Timestamp("2015-05-31"), False)
    b = C.P5Inputs()
    b.oecd = b.oecd.copy()
    b.oecd.loc[b.oecd["edition"] > "201506", ["excess_emp", "unemp_trend", "external"]] = 99.0
    b.bci = {k: s.where(s.index <= "2015-05-31", 1e6) for k, s in b.bci.items()}
    b.ctot = {k: s.where(s.index <= "2015-05-31", 1e6) for k, s in b.ctot.items()}
    b.fx = b.fx.copy()
    b.fx.loc[b.fx.index > t] = 9.0
    b.policy = {k: s.where(s.index <= t, 99.0) for k, s in b.policy.items()}
    s2 = C.signals_at(b, t, "201506", pd.Timestamp("2015-05-31"), False)
    pd.testing.assert_series_equal(a["composite"], s2["composite"])
    pd.testing.assert_frame_equal(a["z"], s2["z"])


def test_signals_are_deterministic(inp):
    t = pd.Timestamp("2020-03-31")
    a = C.signals_at(inp, t, "202003", pd.Timestamp("2020-02-29"), True)
    b = C.signals_at(C.P5Inputs(), t, "202003", pd.Timestamp("2020-02-29"), True)
    pd.testing.assert_frame_equal(a["z"], b["z"])


# ---------------------------------------------------------------------------
# George's calls validator
# ---------------------------------------------------------------------------

def _git(cwd, *args, date=None):
    env = None
    if date:
        import os
        env = {**os.environ, "GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


def test_validator_accepts_only_rows_committed_before_the_window(tmp_path):
    v = _load("validate_calls", "scripts/registry/validate_calls.py")
    repo = tmp_path
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    f = repo / "data" / "registry" / "apeluri_george.csv"
    f.parent.mkdir(parents=True)
    f.write_text("data,banca,directie,probabilitate,motiv,nota\n2026-11-06,Fed,hawkish,65,inflatie,x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "a", date="2026-11-07T10:00:00+00:00")       # Saturday: window = Monday close
    f.write_text(f.read_text() + "2026-11-09,ECB,dovish,70,crestere,\n")
    _git(repo, "commit", "-qam", "b", date="2026-11-10T20:00:00+00:00")       # after the ECB close of 11-10
    f.write_text(f.read_text() + "2026-11-09,BoE,fara_opinie,60,risc,\n2026-11-20,BoJ,hawkish,70,munca,\n")
    res = v.validate(f, "bank", root=repo)
    st = [(r["banca"], r["status"]) for r in res]
    assert st == [("Fed", "accepted"), ("ECB", "rejected"), ("BoE", "rejected"), ("BoJ", "rejected")]
    assert res[0]["window_start_day"] == "2026-11-09" and res[0]["window_end_day"] == "2026-12-07"
    assert "after the window start" in res[1]["reason"]
    assert "fara_opinie requires probabilitate 50" in res[2]["reason"]
    assert "not committed" in res[3]["reason"]


def test_validator_currency_rows(tmp_path):
    v = _load("validate_calls", "scripts/registry/validate_calls.py")
    assert v.check_fx({"data": "2026-11-06", "tari": "USD GBP", "slabe": "JPY;CHF"}) is None
    assert v.check_fx({"data": "2026-11-06", "tari": "USD GBP", "slabe": "USD CHF"})
    assert v.check_fx({"data": "2026-11-06", "tari": "USD", "slabe": "JPY CHF"})
    assert v.fx_start(pd.Timestamp("2026-11-06").date()) == pd.Timestamp("2026-11-09 17:00", tz="UTC")
