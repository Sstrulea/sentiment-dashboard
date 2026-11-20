"""feat/reaction-layer — alignment rules (incl. Asian releases), basket, Huber,
contraction, residuals, rank IC, as-of leakage of the reaction nodes, layer schema,
and the measurement helpers of scripts/measure/reaction_v0.py (synthetic data)."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.tree import schema
from src.tree.backbone import AlignedSeries, CLOSES, H10_FIX, basket, us_control
from src.tree.reaction import (ReactionInputs, contract, currency_specs, huber, snapshot)

ROOT = Path(__file__).resolve().parents[1]
DAYS = pd.bdate_range("2025-01-06", "2025-03-31")


def _aligned(ccy, values=None, days=DAYS):
    v = np.arange(len(days), dtype=float) if values is None else values
    return AlignedSeries(pd.Series(v, index=days), *CLOSES[ccy])


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ccy,release_utc,expected_close", [
    ("JPY", "2025-02-20 23:30", "2025-02-21"),   # Tokyo CPI 08:30 JST -> that Tokyo day's 15:00 close
    ("JPY", "2025-02-21 06:30", "2025-02-24"),   # after the 15:00 JST close (06:00 UTC) -> next trading day
    ("AUD", "2025-02-20 00:30", "2025-02-20"),   # ABS 11:30 AEDT -> same-day 16:30 Sydney close
    ("AUD", "2025-02-20 05:31", "2025-02-21"),   # 16:31 AEDT -> next day
    ("EUR", "2025-02-20 10:00", "2025-02-20"),
    ("USD", "2025-02-20 13:30", "2025-02-20"),
    ("USD", "2025-02-20 21:00", "2025-02-21"),   # after the 15:30 New York close
    ("GBP", "2025-02-21 07:00", "2025-02-21"),
])
def test_first_close_after_release(ccy, release_utc, expected_close):
    s = _aligned(ccy)
    d, date = s.move_after(pd.Timestamp(release_utc))
    assert date == pd.Timestamp(expected_close) and d == 1.0


def test_close_times_follow_local_dst():
    s = _aligned("USD", days=pd.DatetimeIndex(["2025-03-07", "2025-03-10"]))
    assert [pd.Timestamp(x).hour for x in s.instants] == [20, 19]      # EST 15:30 = 20:30Z, EDT = 19:30Z
    a = _aligned("AUD", days=pd.DatetimeIndex(["2025-03-28", "2025-04-07"]))
    assert [pd.Timestamp(x).hour for x in a.instants] == [5, 6]         # AEDT -> AEST


def test_move_is_dropped_across_a_long_gap():
    days = pd.DatetimeIndex(["2025-01-02", "2025-01-03", "2025-01-13"])
    s = AlignedSeries(pd.Series([1.0, 2.0, 5.0], index=days), *CLOSES["EUR"])
    d, date = s.move_after(pd.Timestamp("2025-01-10 12:00"))
    assert np.isnan(d) and date == pd.Timestamp("2025-01-13")


def test_h10_fixing_alignment_for_asian_and_us_releases():
    fx = AlignedSeries(pd.Series(np.arange(len(DAYS), dtype=float), index=DAYS), *H10_FIX)
    assert fx.move_after(pd.Timestamp("2025-02-20 23:30"))[1] == pd.Timestamp("2025-02-21")
    assert fx.move_after(pd.Timestamp("2025-02-20 13:30"))[1] == pd.Timestamp("2025-02-20")
    assert fx.move_after(pd.Timestamp("2025-02-20 18:00"))[1] == pd.Timestamp("2025-02-21")  # after noon NY


def test_us_control_takes_the_nearest_us_close():
    us = _aligned("USD", values=np.arange(len(DAYS)) ** 2.0)
    jp = _aligned("JPY")
    k = jp.first_after(pd.Timestamp("2025-02-20 23:30"))                 # Tokyo close 2025-02-21
    assert us_control(us, jp, k) == us.move_at(us.first_after(pd.Timestamp("2025-02-20 12:00")))[0]  # US 02-20
    eu = _aligned("EUR")
    k = eu.first_after(pd.Timestamp("2025-02-20 10:00"))                 # Berlin close 2025-02-20
    assert us_control(us, eu, k) == us.move_at(us.first_after(pd.Timestamp("2025-02-20 12:00")))[0]  # same day


# ---------------------------------------------------------------------------
# Basket
# ---------------------------------------------------------------------------

def test_basket_is_value_minus_mean_of_the_others_and_sums_to_zero():
    lv = pd.DataFrame({"USD": [0.0], "EUR": [0.1], "JPY": [-0.5], "GBP": [0.2]})
    b = basket(lv)
    assert b.at[0, "EUR"] == pytest.approx(0.1 - (0.0 - 0.5 + 0.2) / 3)
    assert b.at[0, "USD"] == pytest.approx(0.0 - (0.1 - 0.5 + 0.2) / 3)
    assert b.sum(axis=1).iloc[0] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Huber + contraction
# ---------------------------------------------------------------------------

def test_huber_resists_outliers_and_reports_se():
    rng = np.random.default_rng(1)
    X = np.column_stack([np.ones(200), rng.normal(size=200)])
    y = X @ [0.5, 3.0] + rng.normal(0, 1, 200)
    y[:10] += 60
    b, se = huber(X, y)
    ols = np.linalg.lstsq(X, y, rcond=None)[0]
    assert abs(b[1] - 3.0) < 0.3 and abs(b[0] - 0.5) < 0.4 and abs(ols[0] - 0.5) > 2
    assert np.all(se > 0) and np.all(se < 0.2)
    with pytest.raises(ValueError):
        huber(np.column_stack([np.ones(5), np.zeros(5)]), np.ones(5))


def test_contraction_is_polarity_aligned_with_w_n_over_n_plus_12():
    raw = {"payrolls": (3.0, 1.0, 24), "unemployment": (-1.0, 1.0, 12), "thin": (9.0, 5.0, 3)}
    cats = {k: "Labor" for k in raw}
    dirs = {"payrolls": 1.0, "unemployment": -1.0, "thin": 1.0}
    out = contract(raw, cats, dirs)
    a_cat = (3.0 * 24 + 1.0 * 12) / 36                       # aligned: payrolls +3, unemployment +1
    assert out["payrolls"]["w"] == pytest.approx(24 / 36)
    assert out["payrolls"]["beta"] == pytest.approx(24 / 36 * 3.0 + 12 / 36 * a_cat)
    assert out["unemployment"]["beta"] == pytest.approx(-(12 / 24 * 1.0 + 12 / 24 * a_cat))
    assert out["thin"]["beta"] == pytest.approx(a_cat) and out["thin"]["w"] == 0.0   # n < 6: prior only
    assert out["unemployment"]["beta_cat"] == pytest.approx(-a_cat)


# ---------------------------------------------------------------------------
# Synthetic layer: residuals, leakage
# ---------------------------------------------------------------------------

CCY_SET = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]


def _synthetic(seed=0):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2021-01-04", "2025-12-31")
    bb = []
    for c in CCY_SET:
        if c not in ("NZD", "CHF"):
            y = 2 + np.cumsum(rng.normal(0, 0.03, len(days)))
            bb.append(pd.DataFrame({"series": f"Y2_{c}", "date": days, "value": y}))
        if c != "USD":
            bb.append(pd.DataFrame({"series": f"FX_{c}", "date": days,
                                    "value": np.cumsum(rng.normal(0, 0.005, len(days)))}))
        bb.append(pd.DataFrame({"series": f"POL_{c}", "date": days, "value": 2.0}))
    bb = pd.concat(bb, ignore_index=True)
    bb["avail"] = bb["date"] + pd.Timedelta(days=1)
    for k in ("ccy", "kind", "code", "source"):
        bb[k] = "x"
    # events: two indicators per currency, monthly, with a known beta (USD: 4 and -2)
    rows = []
    rel = pd.date_range("2022-01-12 13:30", "2025-12-10 13:30", freq="14D")
    for c in ["USD", "EUR"]:
        for j, (ind, cat, d, beta) in enumerate([("cpi_yoy", "Inflation", 1.0, 4.0),
                                                ("unemployment_rate", "Labor", -1.0, -2.0)]):
            for t in rel + pd.Timedelta(days=7 * j):
                z = float(rng.normal())
                day = (t + pd.Timedelta(hours=10)).normalize()
                rows.append({"event_id": f"{c}:{ind}:{t}", "ccy": c, "indicator_key": ind, "category": cat,
                             "release_dt": t, "direction": d, "released": True, "z_raw": z, "z_signed": z * d,
                             "d2y": beta * z + float(rng.normal(0, 0.5)), "y2_date": day,
                             "us_d2y": 0.0 if c != "USD" else np.nan, "fx_basket": float(rng.normal(0, 30)),
                             "fx_date": day, "avail": day + pd.Timedelta(days=1)})
    ev = pd.DataFrame(rows)
    return bb, ev


@pytest.fixture(scope="module")
def layer():
    return schema.load_layer("reaction")


def test_betas_recover_the_true_reaction_and_residuals_are_out_of_sample(layer):
    bb, ev = _synthetic()
    ri = ReactionInputs(bb, ev)
    b = ri.betas("USD", pd.Timestamp("2025-06-06"))
    raw = {i: v["raw"] for i, v in b["indicators"].items()}
    assert raw["cpi_yoy"] == pytest.approx(4.0, abs=0.4) and raw["unemployment_rate"] == pytest.approx(-2.0, abs=0.4)
    r = ri.day_residuals("USD", pd.Timestamp("2025-06-06"))
    assert r["resid"].tail(20).abs().mean() < 1.5                          # data explained
    d = r.iloc[-1]
    b_before = ri.betas("USD", d["release_dt"].normalize())                # residual uses betas before the day
    e = ev[(ev["ccy"] == "USD") & (ev["y2_date"] == d["y2_date"])].iloc[0]
    fit = b_before["const"] + b_before["indicators"][e["indicator_key"]]["beta"] * e["z_raw"]
    assert d["resid"] == pytest.approx(e["d2y"] - fit)


def test_reaction_nodes_do_not_see_after_asof(layer):
    bb, ev = _synthetic(2)
    lay = {**layer, "currencies": ["USD", "EUR", "NZD"]}
    t = pd.Timestamp("2024-09-06")
    a = snapshot(lay, ReactionInputs(bb, ev), t)
    bb2, ev2 = bb.copy(), ev.copy()
    after = bb2["avail"] > t
    bb2.loc[after, "value"] = bb2.loc[after, "value"] * 3 + 1             # rewrite the future (incl. forward FX)
    late = ev2["avail"] > t
    ev2.loc[late, ["d2y", "z_raw", "z_signed", "fx_basket"]] *= -7
    b = snapshot(lay, ReactionInputs(bb2, ev2), t)
    a.pop("inputs_fingerprint"), b.pop("inputs_fingerprint")
    assert a == b
    assert a["currencies"]["NZD"]["beta_data_rates"]["quality"] == "missing"
    assert "Cloudflare" in a["currencies"]["NZD"]["beta_data_rates"]["meta"]["reason"]
    assert a["currencies"]["USD"]["digest_fx_20d"]["quality"] == "ok"


def test_layer_schema(layer):
    assert layer["layer_id"] == "reaction" and len(currency_specs(layer, "USD")) == len(layer["nodes"])
    bad = copy.deepcopy(layer)
    bad["missing_by_ccy"]["ghost"] = {"NZD": "x"}
    with pytest.raises(schema.TreeSchemaError, match="unknown node"):
        schema.validate_layer(bad)
    bad = copy.deepcopy(layer)
    bad["currencies"] = ["USD", "USD"]
    with pytest.raises(schema.TreeSchemaError, match="duplicates"):
        schema.validate_layer(bad)
    assert schema.is_layer("reaction") and not schema.is_layer("jpy")


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

def _measure():
    spec = importlib.util.spec_from_file_location("reaction_v0", ROOT / "scripts/measure/reaction_v0.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_cross_sectional_rank_ic_and_harness():
    m = _measure()
    fr = pd.date_range("2022-01-07", periods=120, freq="7D")
    rng = np.random.default_rng(4)
    R = pd.DataFrame(rng.normal(0, 50, (120, 6)), index=fr, columns=list("ABCDEF"))
    prereg = {"harness": {"halves": {"first": ["2022-01-07", "2023-06-30"], "second": ["2023-07-01", "end"]}}}
    good = m.cs_metrics(R + rng.normal(0, 1, R.shape), R, 2, fr[0], prereg)
    assert good["IC"] > 0.95 and good["pairs_hit"] > 0.95 and good["accuracy"] > 0.9 and good["ls_bp"] > 0
    assert good["N_ind"] == 60 and 0 < good["coverage"] < 1
    bad = m.cs_metrics(-R, R, 2, fr[0], prereg)
    assert bad["IC"] < -0.95 and bad["ls_bp"] < 0
    assert m.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_forward_values_and_clustered_t():
    m = _measure()
    s = pd.Series(np.arange(30, dtype=float), index=pd.bdate_range("2025-01-01", periods=30))
    f = m.forward(s, pd.DatetimeIndex(["2025-01-03", "2025-02-07"]), 1, 1.0)
    assert f.iloc[0] == 5.0 and np.isnan(f.iloc[1])
    mean, t, n, G = m.clustered_mean_t(np.array([10.0, 10, 12, 8, 30, 30]), np.array([1, 1, 2, 2, 3, 3]), mu0=6)
    assert mean == pytest.approx(100 / 6) and n == 6 and G == 3 and t > 0


def test_first_cut_after_a_pause():
    m = _measure()
    idx = pd.to_datetime(["2023-01-01", "2023-03-01", "2023-06-01", "2024-06-01", "2024-07-01", "2025-03-01"])
    pol = pd.Series([1.0, 1.5, 2.0, 1.75, 1.5, 1.5], index=idx)
    pol = pol.reindex(pd.date_range("2023-01-01", "2025-03-01")).ffill()
    assert m.first_cuts(pol, "2023-01-01") == [pd.Timestamp("2024-06-01")]
