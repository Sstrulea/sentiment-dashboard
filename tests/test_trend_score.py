"""Tests for src.trend_score v2 (regime + momentum; ADX display-only). Synthetic
series; no network, no real data."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import trend_score as ts
from src.trend_score import (
    REGIME_MAP,
    SLOPE_THRESH_ATR,
    _clamp,
    _server_today,
    atr,
    drop_forming_bar,
    momentum_score,
    regime_score,
    score_all,
    trend_cell,
)


def _df(closes, spread=0.5) -> pd.DataFrame:
    """Daily OHLC frame from a close array (high/low bracket the close by `spread`)."""
    closes = np.asarray(closes, dtype=float)
    high = closes + spread
    low = closes - spread
    open_ = np.concatenate([[closes[0]], closes[:-1]])
    dates = pd.date_range("2023-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low, "close": closes})


# --- Layer 1: REGIME truth table (all 4 states) -----------------------------

def test_regime_map_is_the_approved_table():
    assert REGIME_MAP == {3: 2, 2: 1, 1: -1, 0: -2}


def test_regime_three_bull_points_is_plus_two():
    assert regime_score(pd.Series(100.0 + np.arange(300) * 1.0)) == (3, 2)


def test_regime_zero_bull_points_is_minus_two():
    assert regime_score(pd.Series(400.0 - np.arange(300) * 1.0)) == (0, -2)


def test_regime_pullback_two_bull_points_is_plus_one():
    # classic uptrend pullback: below SMA50, above SMA200, SMA50>SMA200 → 2 → +1
    rise = 100.0 + np.arange(280) * 1.0
    dip = rise[-1] - np.arange(1, 16) * 2.0
    assert regime_score(pd.Series(np.concatenate([rise, dip]))) == (2, 1)


def test_regime_one_bull_point_is_minus_one():
    # uptrend then a sharp crash below both MAs (SMA50>SMA200 still lags) → 1 → −1
    rise = 100.0 + np.arange(280) * 1.0
    crash = rise[-1] - np.arange(1, 26) * 8.0
    assert regime_score(pd.Series(np.concatenate([rise, crash]))) == (1, -1)


# --- Layer 2: MOMENTUM thresholds (above / below / exactly at the band) ------

def _ramp(slope=1.0, n=250):
    return pd.Series(100.0 + np.arange(n) * slope)   # SMA50 slope over 20 bars = 20*slope


@pytest.mark.parametrize("atr_last,expected", [
    (20.0, 1),                                # slope_atr = 0.05, clearly above → +1
    (1.0 / (SLOPE_THRESH_ATR + 0.001), 1),    # slope_atr just above +thresh → +1
    (1.0 / SLOPE_THRESH_ATR, 0),              # EXACTLY at +thresh → 0 (strict >)
    (100.0, 0),                               # slope_atr = 0.01 < thresh → 0
])
def test_momentum_thresholds_rising(atr_last, expected):
    # ramp slope 1/bar → SMA50[0]-SMA50[20] = 20 → slope_atr = 1/atr_last
    _, mom = momentum_score(_ramp(1.0), atr_last)
    assert mom == expected


def test_momentum_symmetric_falling():
    _, mom = momentum_score(_ramp(-1.0), 1.0 / (SLOPE_THRESH_ATR + 0.001))
    assert mom == -1


def test_momentum_atr_undefined_is_zero():
    assert momentum_score(_ramp(1.0), float("nan"))[1] == 0
    assert momentum_score(_ramp(1.0), 0.0)[1] == 0


# --- ATR normalization: same relative slope at different price levels → same --

def test_atr_normalization_scale_invariant():
    c1 = 100.0 + np.arange(250) * 0.5
    k = 50.0
    df1, df2 = _df(c1, spread=0.5), _df(c1 * k, spread=0.5 * k)   # everything ×k
    m1 = momentum_score(pd.Series(c1), float(atr(df1).iloc[-1]))
    m2 = momentum_score(pd.Series(c1 * k), float(atr(df2).iloc[-1]))
    assert m1[1] == m2[1]                          # same momentum score
    assert m1[0] == pytest.approx(m2[0], rel=1e-6)  # same slope/atr


# --- clamp + full cell ------------------------------------------------------

@pytest.mark.parametrize("x,expected", [(5, 3), (-5, -3), (3, 3), (-3, -3), (2, 2), (0, 0)])
def test_clamp_bounds(x, expected):
    assert _clamp(x) == expected


def test_clear_uptrend_cell_is_plus_three():
    # regime +2 + momentum +1 = +3 (no ADX factor damping any more)
    assert trend_cell(_df(100.0 + np.arange(300) * 1.0)) == 3


def test_clear_downtrend_cell_is_minus_three():
    assert trend_cell(_df(400.0 - np.arange(300) * 1.0)) == -3


def test_adx_does_not_affect_the_cell():
    # a fresh breakout has low ADX; v2 must NOT damp it (v1's defect). Rising ramp
    # → +3 regardless of ADX. (Also asserts ADX is computed but unused in the cell.)
    df = _df(100.0 + np.arange(300) * 1.0)
    assert trend_cell(df) == 3


# --- guards -----------------------------------------------------------------

def test_insufficient_data_returns_none():
    df = _df(100.0 + np.arange(150) * 1.0)   # < MIN_BARS (200)
    assert regime_score(pd.Series(df["close"])) is None
    assert trend_cell(df) is None


# --- score_all integration --------------------------------------------------

def test_score_all_maps_and_marks_missing(tmp_path):
    pq = tmp_path / "price_history.parquet"
    yml = tmp_path / "price_symbols.yaml"
    yml.write_text("symbols:\n  EURUSD: EURUSD\n  GOLD: XAUUSD\n")

    up = _df(100.0 + np.arange(250) * 1.0)
    up.insert(0, "symbol", "EURUSD")
    up["source"] = "mt5"
    up = up[["symbol", "date", "open", "high", "low", "close", "source"]]
    up.to_parquet(pq, index=False)

    res = score_all(parquet_path=pq, yaml_path=yml, server_today=pd.Timestamp("2100-01-01"))
    assert set(res) == {"EURUSD", "GOLD"}
    e = res["EURUSD"]
    assert e["trend_cell"] == 3
    assert e["regime"] == 2 and e["momentum"] == 1 and e["bull_points"] == 3
    assert e["adx"] is not None                 # ADX present (display-only)
    # GOLD has no series -> all-None entry
    assert res["GOLD"]["trend_cell"] is None
    assert all(res["GOLD"][k] is None for k in ("bull_points", "regime", "slope_atr", "momentum", "adx"))


# --- Option A: exclude the forming (current server-day) bar ------------------

def test_server_today_applies_offset():
    assert _server_today(pd.Timestamp("2026-06-29 23:30")) == pd.Timestamp("2026-06-30")
    assert _server_today(pd.Timestamp("2026-06-29 10:00")) == pd.Timestamp("2026-06-29")


def test_drop_forming_bar_excludes_current_day_only():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-06-26", "2026-06-29", "2026-06-30"]),
        "close": [1.0, 2.0, 3.0],
    })
    out = drop_forming_bar(df, server_today=pd.Timestamp("2026-06-30"))
    assert list(out["date"]) == [pd.Timestamp("2026-06-26"), pd.Timestamp("2026-06-29")]


def test_drop_forming_bar_weekend_safe_no_minus_one_day():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-06-25", "2026-06-26", "2026-06-29"]),
        "close": [1.0, 2.0, 3.0],
    })
    out = drop_forming_bar(df, server_today=pd.Timestamp("2026-06-29"))
    assert out["date"].max() == pd.Timestamp("2026-06-26")


def test_score_ignores_forming_bar(tmp_path):
    """A current-day bar must NOT affect the score — computed on the last closed bar."""
    yml = tmp_path / "price_symbols.yaml"
    yml.write_text("symbols:\n  EURUSD: EURUSD\n")
    server_today = pd.Timestamp("2026-06-30")
    n = 250
    dates = pd.bdate_range(end="2026-06-29", periods=n)
    closes = 100.0 + np.arange(n) * 1.0

    def _mk(dts, cl):
        cl = np.asarray(cl, float)
        return pd.DataFrame({
            "symbol": "EURUSD", "date": dts,
            "open": cl, "high": cl + 0.5, "low": cl - 0.5, "close": cl, "source": "mt5",
        })

    closed = _mk(dates, closes)
    today = _mk(pd.to_datetime(["2026-06-30"]), [closes[-1] - 50.0])
    today.loc[:, "low"] = closes[-1] - 60.0

    pq_all = tmp_path / "all.parquet"
    pd.concat([closed, today], ignore_index=True).to_parquet(pq_all, index=False)
    pq_closed = tmp_path / "closed.parquet"
    closed.to_parquet(pq_closed, index=False)

    filtered = score_all(pq_all, yml, server_today=server_today)["EURUSD"]
    unfiltered = score_all(pq_all, yml, server_today=pd.Timestamp("2100-01-01"))["EURUSD"]
    ref = score_all(pq_closed, yml, server_today=pd.Timestamp("2100-01-01"))["EURUSD"]

    assert filtered["trend_cell"] == ref["trend_cell"]
    assert filtered["regime"] == ref["regime"] and filtered["momentum"] == ref["momentum"]
    assert filtered["adx"] == pytest.approx(ref["adx"])
    assert filtered["adx"] != pytest.approx(unfiltered["adx"])   # forming bar WAS material


# --- trend signature (cron render trigger) ----------------------------------

def _mk_eurusd(dates, closes):
    closes = np.asarray(closes, float)
    return pd.DataFrame({
        "symbol": "EURUSD", "date": dates,
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "source": "mt5",
    })


def test_trend_signature_ignores_forming_bar(tmp_path):
    from src.trend_signature import trend_signature
    yml = tmp_path / "price_symbols.yaml"
    yml.write_text("symbols:\n  EURUSD: EURUSD\n")
    st = pd.Timestamp("2026-06-30")
    dates = pd.bdate_range(end="2026-06-29", periods=250)
    closes = 100.0 + np.arange(250) * 1.0
    closed = _mk_eurusd(dates, closes)
    a = pd.concat([closed, _mk_eurusd(pd.to_datetime(["2026-06-30"]), [closes[-1] + 0.2])])
    b = pd.concat([closed, _mk_eurusd(pd.to_datetime(["2026-06-30"]), [closes[-1] - 8.0])])
    pa = tmp_path / "a.parquet"; a.to_parquet(pa, index=False)
    pb = tmp_path / "b.parquet"; b.to_parquet(pb, index=False)
    assert trend_signature(pa, yml, server_today=st) == trend_signature(pb, yml, server_today=st)


def test_trend_signature_changes_on_closed_trend_change(tmp_path):
    from src.trend_signature import trend_signature
    yml = tmp_path / "price_symbols.yaml"
    yml.write_text("symbols:\n  EURUSD: EURUSD\n")
    st = pd.Timestamp("2026-06-30")
    dates = pd.bdate_range(end="2026-06-29", periods=250)
    up = tmp_path / "up.parquet"
    _mk_eurusd(dates, 100.0 + np.arange(250) * 1.0).to_parquet(up, index=False)
    down = tmp_path / "down.parquet"
    _mk_eurusd(dates, 600.0 - np.arange(250) * 1.0).to_parquet(down, index=False)
    assert trend_signature(up, yml, server_today=st) != trend_signature(down, yml, server_today=st)
