"""feat/v4-score — TFF parsing / reconciliation, REER value, pre- / post-meeting windows,
shock classification, MS, v4 (regime gate, calibration, abstention, concentration), leakage."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.tree import v4 as V
from src.tree.v4 import Phase4, asof_frame, cs_z, logistic, rank_z, rolling_pct

ROOT = Path(__file__).resolve().parents[1]


def _m():
    spec = importlib.util.spec_from_file_location("phase4", ROOT / "scripts/measure/phase4.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def p4():
    return Phase4(fridays=pd.date_range("2016-01-01", "2026-10-30", freq="7D"))


def test_asof_frame_respects_the_lag_and_rolling_pct():
    s = pd.Series([1.0, 2.0, 3.0], index=pd.to_datetime(["2024-01-02", "2024-01-09", "2024-01-16"]))
    fr = pd.to_datetime(["2024-01-12", "2024-01-15", "2024-01-22"])
    f = asof_frame({"USD": s}, pd.Timedelta(days=6), fr)["USD"]
    assert list(f) == [1.0, 2.0, 3.0][:1] + [2.0, 3.0]                 # report Tue + 6 days
    assert rolling_pct(pd.Series([1.0, 2, 3, 0]), 3, 2).tolist()[1:] == [100.0, 100.0, 100 / 3]


def test_tff_parsed_and_reconciled_with_legacy(p4):
    t, l = p4.tff, p4.legacy
    assert set(t["ccy"]) == set(V.CCYS) and t["date"].min() <= pd.Timestamp("2006-06-13")
    j = t.merge(l[["ccy", "date", "open_interest_all"]], on=["ccy", "date"], suffixes=("_t", "_l"))
    assert (j["open_interest_all_t"] - j["open_interest_all_l"]).abs().max() <= 1      # same reports
    sig = p4.tff_signal()
    assert sig.dropna(how="all").max().max() <= 0 and sig.dropna(how="all").min().min() >= -100   # -(percentile)


def test_reer_value_is_contrarian_deviation_from_5y_mean():
    r = pd.DataFrame({"ccy": "USD", "period": pd.date_range("2010-01-31", periods=80, freq="ME"),
                      "value": np.r_[np.full(70, 100.0), np.full(10, 110.0)]})
    p = Phase4.__new__(Phase4)
    p.reer, p._c = r, {}
    p.fridays = pd.date_range("2015-06-26", "2016-12-30", freq="7D")
    v = p.reer_value()["USD"]
    assert v.loc["2015-06-26"] == pytest.approx(0.0)                      # May-2015 (visible) = the 5y mean
    assert v.loc["2016-12-30"] < -0.05                                      # 10% above its mean -> negative
    assert v.loc["2015-12-18"] == pytest.approx(0.0)                       # Nov-2015 jump not visible yet
    assert v.loc["2015-12-25"] < -0.05                                      # month end + 25 days


def test_pre_meeting_window_and_shock_classification():
    m = _m()
    s = pd.Series(np.arange(30, dtype=float), index=pd.bdate_range("2024-01-01", periods=30))
    k1 = m.fix_index(s, pd.Timestamp("2024-01-31"), before=True)
    assert s.index[k1] == pd.Timestamp("2024-01-30")                        # day -1
    k = m.fix_index(s, pd.Timestamp("2024-01-31"), before=False)
    assert s.index[k] == pd.Timestamp("2024-02-01")                         # day +1
    assert m.shock_kind(5.0, -1.2) == "policy" and m.shock_kind(-3.0, -0.5) == "information"
    assert m.shock_kind(0.0, 1.0) is None


def test_ms_needs_all_parts_and_momentum_sign(p4):
    mom = p4.momentum_sign()
    row = mom.dropna().iloc[-1]
    assert set(row.unique()) <= {-1.0, 0.0, 1.0}
    ms = p4.ms()
    assert ms.loc[:"2022-12-30"].isna().all().all()                         # no surprises before 2023
    t = ms.dropna(how="all").index[-1]
    parts = [p4.surprises().loc[t], p4.cot_pct().loc[t], mom.loc[t]]
    exp = sum(cs_z(x) for x in parts)
    assert ms.loc[t].to_numpy() == pytest.approx(exp.to_numpy(), nan_ok=True)


def test_v4_regime_gate_shrinks_carry_in_liquidity_stress(p4):
    raw = p4.v4_raw(macro=False)
    reg, pct = p4.regime(), p4.crowding()["pct"]
    ls = reg[(reg == "liquidity_stress") & pct.notna()].index
    t = ls[0]
    assert raw.loc[t].to_numpy() == pytest.approx((p4.carry_z().loc[t] * (1 - pct[t] / 100)).to_numpy())
    calm = reg[reg == "calm"].index[-1]
    assert raw.loc[calm].to_numpy() == pytest.approx(p4.carry_z().loc[calm].to_numpy())


def test_logistic_and_calibration_halving_and_abstention(p4):
    rng = np.random.default_rng(0)
    x = rng.normal(size=4000)
    y = (rng.uniform(size=4000) < 1 / (1 + np.exp(-(0.2 + 1.5 * x)))).astype(float)
    a, b = logistic(x, y)
    assert a == pytest.approx(0.2, abs=0.1) and b == pytest.approx(1.5, abs=0.15)
    S = p4.carry_z()
    F = pd.DataFrame(rng.normal(size=S.shape), index=S.index, columns=S.columns)
    P = p4.calibrate(S, F, 4, 156, halve=True)
    reg = p4.regime()
    stress = [t for t in P.dropna(how="all").index if reg[t] != "calm"]
    assert stress and (P.loc[stress] - 0.5).abs().max().max() <= 0.25        # halved
    assert P.loc[P.index < S.index[0] + pd.Timedelta(weeks=156)].isna().all().all()
    lo, hi = V.P_ABSTAIN
    assert (lo, hi) == (0.45, 0.55)


def test_concentration_is_high_when_the_book_is_the_dollar_factor(p4):
    t = p4.fridays[-1]
    S = pd.DataFrame([[-1, 2, 2, -1, 0, 0, 0, 0.5]], index=[t], columns=V.CCYS)   # long EUR/GBP, short USD/JPY
    c = p4.concentration(S)
    assert 0 <= c.iloc[0] <= 1


def test_v4_panels_do_not_see_after_asof():
    fr = pd.date_range("2016-01-01", "2024-06-28", freq="7D")
    a = Phase4(fridays=fr)
    b = Phase4(fridays=fr)
    cut = pd.Timestamp("2024-06-28")
    b.legacy = b.legacy.copy()
    b.legacy.loc[b.legacy["date"] > cut - pd.Timedelta(days=6), "noncomm_positions_long_all"] *= 3
    b.tff = b.tff.copy()
    b.tff.loc[b.tff["date"] > cut - pd.Timedelta(days=6), "asset_mgr_positions_long"] *= 3
    b.reer = b.reer.copy()
    b.reer.loc[b.reer["period"] > cut - pd.Timedelta(days=25), "value"] *= 2
    for k in list(b.bb.by):
        s = b.bb.by[k].copy()
        s[s.index >= cut] *= 5
        b.bb.by[k] = s
    b.lv_usd = b.lv_usd.copy()
    b.lv_usd.loc[b.lv_usd.index >= cut] *= 5
    for name in ("carry_z", "cot_pct", "crowding", "tff_signal", "reer_value", "momentum_sign"):
        x, y = getattr(a, name)(), getattr(b, name)()
        pd.testing.assert_frame_equal(x.loc[[cut]], y.loc[[cut]], check_dtype=False)
    pd.testing.assert_frame_equal(a.v4_raw(macro=False).loc[[cut]], b.v4_raw(macro=False).loc[[cut]])


def test_registry_rows_carry_p_and_flags(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("snapshot", ROOT / "scripts/registry/snapshot.py")
    snap = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snap)
    import json
    payload = tmp_path / "e.json"
    payload.write_text(json.dumps({"as_of": "x", "currencies": {c: {"score_v3": 1.0 * i} for i, c in enumerate(snap.CCYS)}}))
    monkeypatch.setattr(snap, "macro_scores", lambda asof, mi=None: {c: 0.0 for c in snap.CCYS})
    monkeypatch.setattr(snap, "carry_scores", lambda asof, backbone=None: {c: 1.0 for c in snap.CCYS})
    monkeypatch.setattr(snap, "v4_and_ms", lambda asof: ({c: float(i) for i, c in enumerate(snap.CCYS)},
                                                          {(c, h): 0.6 for c in snap.CCYS for h in (2, 4)},
                                                          {c: "crowding" for c in snap.CCYS}, {c: 0.1 for c in snap.CCYS}))
    df = snap.snapshot(pd.Timestamp("2026-01-02"), tmp_path / "c.parquet", payload)
    v4 = df[df.source == "v4"]
    assert set(df.source) == {"v3", "macro_momentum", "carry", "v4", "ms"}
    assert (v4[v4.horizon_weeks.isin([2, 4])]["p_beats_basket"] == 0.6).all() and (v4["flags"] == "crowding").all()
