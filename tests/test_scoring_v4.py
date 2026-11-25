"""Scoring v4 "swing" (2026-11-25): the FX board scores COT only. The weekly COT
cell history, the instrument score (pair, USD leg, US Dollar row), the label
calibration (RMS and thresholds, the v3 method), v4 applied over v3 with v3 kept
as the shadow, the display meta, and the config switch."""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.cot_score import (DATE_COL, EXT_3Y_COL, EXT_6M_COL, NET_COL, SYMBOL_COL,
                           currency_cell_history, score_currencies)
from src.economic_compute import (V3_DISPLAY_SCALE, apply_v3_fx, apply_v4_fx, final_score,
                                  swing_model, v3_label, v4_calibrate, v4_instrument_score,
                                  v4_sigma_cot)

ROOT = Path(__file__).resolve().parents[1]
FX_CFG = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())
SIG = 2.0


# --- the config switch -----------------------------------------------------------

def test_swing_model_switch():
    assert swing_model({}) == "v3"
    assert swing_model({"swing_model": "v4"}) == "v3"                     # no v4 block → v3
    assert swing_model({"swing_model": "v4", "v4": {"sigma_cot": 1.0}}) == "v4"
    assert swing_model({"swing_model": "v3", "v4": {"sigma_cot": 1.0}}) == "v3"
    assert swing_model(FX_CFG) == "v4"                                     # the live config
    assert v4_sigma_cot(FX_CFG) == pytest.approx(FX_CFG["v3"]["sigma_cot"])
    assert v4_sigma_cot({"v4": {}, "v3": {"sigma_cot": 1.5}}) == 1.5


# --- the weekly COT cell history ---------------------------------------------------

def _hist(n=80, symbols=("EUR", "JPY", "DXY"), seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end="2026-11-17", periods=n, freq="W-TUE")
    frames = []
    for k, sym in enumerate(symbols):
        net = np.cumsum(rng.normal(0, 1000, n)) + 1000 * k
        g = pd.DataFrame({DATE_COL: dates, SYMBOL_COL: sym, NET_COL: net})
        g[EXT_6M_COL] = rng.uniform(0, 1, n)
        g[EXT_3Y_COL] = rng.uniform(0, 1, n)
        g.loc[:4, EXT_6M_COL] = np.nan                                     # no 6-month extreme yet
        g.loc[: n // 2, EXT_3Y_COL] = np.nan                               # 6-month basis only
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def test_cell_history_last_row_is_the_live_cell():
    h = _hist()
    ch = currency_cell_history(h)
    live = score_currencies(h).set_index("symbol")["cell"]
    last = ch.sort_values("date").groupby("symbol")["cell"].last()
    for sym in ("EUR", "JPY", "DXY"):
        assert int(last[sym]) == int(live[sym]), sym


def test_cell_history_at_each_date_uses_only_the_reports_up_to_it():
    h = _hist(n=60)
    ch = currency_cell_history(h).set_index(["date", "symbol"])["cell"]
    for cut in (20, 33, 47):
        t = sorted(h[DATE_COL].unique())[cut]
        live = score_currencies(h[h[DATE_COL] <= t]).set_index("symbol")["cell"]
        for sym in ("EUR", "JPY"):
            assert int(ch[(pd.Timestamp(t), sym)]) == int(live[sym]), (cut, sym)


def test_cell_history_skips_reports_without_the_6_month_extreme_and_honours_last_n():
    h = _hist(n=60)
    ch = currency_cell_history(h)
    assert ch.groupby("symbol").size().to_dict() == {"DXY": 55, "EUR": 55, "JPY": 55}
    tail = currency_cell_history(h, last_n=10)
    assert tail.groupby("symbol").size().to_dict() == {"DXY": 10, "EUR": 10, "JPY": 10}
    full = ch.set_index(["date", "symbol"])["cell"]
    for _, r in tail.iterrows():                                           # same cells, Flow on the whole past
        assert int(full[(r["date"], r["symbol"])]) == int(r["cell"])
    assert currency_cell_history(pd.DataFrame()).empty


# --- the instrument score ----------------------------------------------------------

INSTRUMENTS = {"US-DOLLAR": {"type": "single", "currency": "USD", "sign": 1},
               "EURUSD": {"type": "fx", "base": "EUR", "quote": "USD"},
               "USDJPY": {"type": "fx", "base": "USD", "quote": "JPY"},
               "EURJPY": {"type": "fx", "base": "EUR", "quote": "JPY"}}


def test_instrument_score_pair_usd_leg_and_dollar_row():
    cells = {"EUR": 3, "JPY": -2, "DXY": -1}
    assert v4_instrument_score(INSTRUMENTS["EURJPY"], cells, SIG) == pytest.approx(2.5 * 5 / SIG)
    assert v4_instrument_score(INSTRUMENTS["EURUSD"], cells, SIG) == pytest.approx(2.5 * 3 / SIG)
    assert v4_instrument_score(INSTRUMENTS["USDJPY"], cells, SIG) == pytest.approx(2.5 * 2 / SIG)
    assert v4_instrument_score(INSTRUMENTS["US-DOLLAR"], cells, SIG) == pytest.approx(2.5 * -1 / SIG)
    # the score uses the legs as they are: no ±4 cap on the difference
    assert v4_instrument_score(INSTRUMENTS["EURJPY"], {"EUR": 4, "JPY": -4}, SIG) == pytest.approx(2.5 * 8 / SIG)
    # a missing non-USD leg (or DXY, or NaN) → None
    assert v4_instrument_score(INSTRUMENTS["EURJPY"], {"EUR": 3}, SIG) is None
    assert v4_instrument_score(INSTRUMENTS["EURJPY"], {"EUR": 3, "JPY": float("nan")}, SIG) is None
    assert v4_instrument_score(INSTRUMENTS["US-DOLLAR"], {"EUR": 3}, SIG) is None


# --- the calibration -----------------------------------------------------------------

def _cell_hist(n, seed=1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end="2026-11-17", periods=n, freq="W-TUE")
    rows = []
    for sym in ("EUR", "JPY", "DXY"):
        for d, c in zip(dates, rng.integers(-4, 5, n)):
            rows.append({"date": d, "symbol": sym, "cell": int(c)})
    return pd.DataFrame(rows)


def test_calibration_is_the_v3_method_on_the_weekly_scores():
    ch = _cell_hist(200)
    cal = v4_calibrate(ch, INSTRUMENTS, SIG)
    wide = ch.pivot_table(index="date", columns="symbol", values="cell").sort_index().tail(156)
    sc = {sym: [v4_instrument_score(cfg, dict(r), SIG) for _, r in wide.iterrows()] for sym, cfg in INSTRUMENTS.items()}
    for sym, v in sc.items():
        assert cal["rms"][sym] == pytest.approx(math.sqrt(np.mean(np.square(v)))), sym
    z = [abs(x / cal["rms"][sym]) for sym, v in sc.items() for x in v[-53:]]
    assert cal["thresholds"]["mild"] == pytest.approx(np.quantile(z, 0.55))
    assert cal["thresholds"]["very"] == pytest.approx(np.quantile(z, 0.90))
    assert cal["weeks"] == 156 and cal["thr_weeks"] == 53 and cal["n_z"] == 4 * 53
    assert cal["to"] == "2026-11-17"


def test_calibration_needs_enough_history():
    assert v4_calibrate(None, INSTRUMENTS, SIG) is None
    assert v4_calibrate(_cell_hist(20), INSTRUMENTS, SIG) is None            # < 52 weeks: no RMS, no thresholds
    ch = _cell_hist(200)
    short = ch[~((ch["symbol"] == "JPY") & (ch["date"] < ch["date"].max() - pd.Timedelta(weeks=40)))]
    cal = v4_calibrate(short, INSTRUMENTS, SIG)
    assert cal["rms"]["EURJPY"] is None and cal["rms"]["USDJPY"] is None    # JPY: 41 weeks only
    assert cal["rms"]["EURUSD"] is not None and cal["rms"]["US-DOLLAR"] is not None


# --- v4 over v3, the shadow ----------------------------------------------------------

K = {"sigma_macro": 0.5, "sigma_carry": 2.0, "sigma_cot": SIG,
     "sigma_ccy": {c: {"growth": 0.5, "inflation": 0.5, "labour": 1.0} for c in ("USD", "EUR", "JPY")}}
IND = {"indicators": {f"{c}_x": {"weight": 1.0} for c in ("growth", "inflation", "labour")}}


def _card(ccy, g, i, l):
    cats, bd = {}, {}
    for cat, v in (("growth", g), ("inflation", i), ("labour", l)):
        cats[cat] = {"score_precise": float(v), "coverage": 1, "weight": 1.0, "score_cell": round(v)}
        bd[f"{cat}_x"] = {"score": int(v), "category": cat}
    return {"currency": ccy, "categories": cats, "breakdown": bd}


def _payload():
    cards = {"EUR": _card("EUR", 1, -1, 2), "USD": _card("USD", -1, 1, 0), "JPY": _card("JPY", 0, 1, -1)}
    insts = [{"symbol": s, "type": c["type"], "score": 0.1, "bias": "Neutral",
              "breakdown": ({"base": {"currency": c["base"]}, "quote": {"currency": c["quote"]}}
                            if c["type"] == "fx" else {"base": {"currency": c["currency"]}})}
             for s, c in INSTRUMENTS.items()]
    return {"currencies": cards, "instruments": insts}


def _cfg():
    return {"v3": dict(K, rms={s: 2.0 for s in INSTRUMENTS}, thresholds={"mild": 0.7, "very": 1.5}),
            "swing_model": "v4", "v4": {"sigma_cot": SIG}, "instruments": INSTRUMENTS}


CAL = {"rms": {"US-DOLLAR": 1.0, "EURUSD": 2.0, "USDJPY": 3.0, "EURJPY": None},
       "thresholds": {"mild": 0.8, "very": 1.65}, "weeks": 156, "thr_weeks": 53, "n_z": 212,
       "from": "2023-11-28", "to": "2026-11-17"}


def test_v4_replaces_v3_and_keeps_it_as_the_shadow():
    p = _payload()
    cells = {"EUR": 3, "JPY": -2, "DXY": -1}
    apply_v3_fx(p, _cfg(), IND, cells, {"EUR": 2.0, "USD": 4.0, "JPY": 0.0})
    v3 = {i["symbol"]: {f: i.get(f) for f in ("score", "bias", "z", "rms", "final_score", "contributions")}
          for i in p["instruments"]}
    apply_v4_fx(p, _cfg(), cells, CAL)
    by = {i["symbol"]: i for i in p["instruments"]}
    for sym, inst in by.items():
        for f, v in v3[sym].items():                                       # the v3 shadow
            assert inst[f + "_v3"] == v, (sym, f)
        s = v4_instrument_score(INSTRUMENTS[sym], cells, SIG)
        assert inst["score"] == pytest.approx(s), sym
        assert inst["contrib_sum"] == pytest.approx(inst["score"]) and abs(inst["contrib_residual"]) < 1e-12
        assert [c["key"] for c in inst["contributions"]] == ["cot"]
        r = CAL["rms"][sym]
        assert inst["rms"] == r
        assert inst["z"] == (pytest.approx(s / r) if r else None)
        assert inst["bias"] == v3_label(s, r, CAL["thresholds"])
        assert inst["final_score"] == final_score(inst["z"], CAL["thresholds"])
        assert inst["score_v2"] == 0.1                                      # the v2 shadow stays
    assert by["EURJPY"]["bias"] == "Neutral" and by["EURJPY"]["final_score"] is None   # no RMS → Neutral
    assert by["EURUSD"]["z"] == pytest.approx(2.5 * 3 / SIG / 2.0)
    assert by["EURUSD"]["bias"] == "Very Bullish"                            # z 3.75 / 2 = 1.875 ≥ 1.65
    assert by["US-DOLLAR"]["bias"] == "Bearish"                              # z −1.25
    assert by["EURUSD"]["v3"]["carry"] is not None                           # v3's blocks kept (/strength)


def test_missing_cot_leg_scores_zero_without_contributions():
    p = _payload()
    apply_v3_fx(p, _cfg(), IND, {"EUR": 3}, {"EUR": 2.0, "USD": 4.0, "JPY": 0.0})
    apply_v4_fx(p, _cfg(), {"EUR": 3}, CAL)
    by = {i["symbol"]: i for i in p["instruments"]}
    assert by["EURJPY"]["score"] == 0.0 and by["EURJPY"]["contributions"] == []
    assert by["USDJPY"]["score"] == 0.0 and by["USDJPY"]["bias"] == "Neutral"
    assert by["US-DOLLAR"]["score"] == 0.0                                   # no DXY cell
    assert by["EURUSD"]["score"] == pytest.approx(2.5 * 3 / SIG)


# --- the display ---------------------------------------------------------------------

def test_v4_display_shows_only_cot_on_the_fx_board():
    from src.economic_render import V4_FX_HIDDEN, _v4_display
    p = _payload()
    for inst in p["instruments"]:
        inst.update({"categories_used": 2, "categories_total": 3, "categories_excluded": ["labour"],
                     "indicator_cells": {"gdp": {"v": 1}}, "monetary_pair": None})
    meta = {"table_layout": [{"category": "growth", "columns": []}], "scoring": "v3",
            "bias_thresholds": {"mild": 0.7, "very": 1.51}}
    _v4_display(p, meta, _cfg(), CAL)
    assert meta["fx_model"] == "v4" and meta["scoring"] == "v3"           # cross-asset stays v3
    assert meta["table_layout"] == []
    assert meta["bias_thresholds"] == CAL["thresholds"] == meta["v4"]["thresholds"]
    assert meta["v4"]["sigma_cot"] == SIG and meta["v4"]["scale"] == V3_DISPLAY_SCALE
    for inst in p["instruments"]:
        assert not set(V4_FX_HIDDEN) & set(inst), inst["symbol"]


# --- the live payload (needs the repo's parquet data; skipped without pyarrow) ----------

def test_live_payload_is_v4_and_consistent():
    pytest.importorskip("pyarrow")
    from src.economic_render import build_economic_payload
    p = build_economic_payload()
    meta = p["meta"]
    assert meta["fx_model"] == "v4", meta.get("v4_error")
    th = meta["v4"]["thresholds"]
    assert 0 < th["mild"] < th["very"]
    for inst in p["instruments"]:
        assert inst["contrib_sum"] == pytest.approx(inst["score"], abs=1e-9), inst["symbol"]
        assert inst["bias"] == v3_label(inst["score"], inst["rms"], th), inst["symbol"]
        assert "score_v3" in inst and "bias_v3" in inst, inst["symbol"]
        assert "indicator_cells" not in inst and "categories_used" not in inst, inst["symbol"]
    assert meta["table_layout"] == []
