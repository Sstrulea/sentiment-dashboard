"""Tests for src.trend_score — synthetic series; no network, no real data."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import trend_score as ts
from src.trend_score import (
    _clamp_round,
    _factor_from_adx,
    _round_half_away,
    adx_factor,
    score_all,
    trend_cell,
    trend_components,
)


def _df(closes, spread=0.5) -> pd.DataFrame:
    """Build a daily OHLC frame from a close array (high/low bracket the close)."""
    closes = np.asarray(closes, dtype=float)
    high = closes + spread
    low = closes - spread
    open_ = np.concatenate([[closes[0]], closes[:-1]])
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low, "close": closes})


# --- rounding / clamp helpers ----------------------------------------------

@pytest.mark.parametrize("x,expected", [
    (0.5, 1), (-0.5, -1), (1.5, 2), (-1.5, -2), (2.5, 3),
    (0.4, 0), (-0.4, 0), (0.0, 0), (0.75, 1), (-0.25, 0),
])
def test_round_half_away(x, expected):
    assert _round_half_away(x) == expected


@pytest.mark.parametrize("x,expected", [(5, 3), (-5, -3), (3, 3), (-3, -3), (1.5, 2), (0.0, 0)])
def test_clamp_round_bounds(x, expected):
    assert _clamp_round(x) == expected


# --- ADX factor mapping (incl. NaN guard) ----------------------------------

@pytest.mark.parametrize("adx,factor", [
    (30.0, 1.0), (22.0, 1.0), (21.9, 0.5), (15.0, 0.5), (14.9, 0.25), (5.0, 0.25),
    (float("nan"), 0.25), (None, 0.25),
])
def test_factor_from_adx(adx, factor):
    assert _factor_from_adx(adx) == factor


# --- clear trends: sign convention + strong ADX -> ±3 ----------------------

def test_clear_uptrend_is_plus_three():
    df = _df(100.0 + np.arange(260) * 1.0)  # strictly rising ramp
    short, long, slope, raw = trend_components(df)
    assert (short, long, slope, raw) == (1, 1, 1, 3)
    assert adx_factor(df) == 1.0          # ramp -> very high ADX
    assert trend_cell(df) == 3            # + = bullish


def test_clear_downtrend_is_minus_three():
    df = _df(500.0 - np.arange(260) * 1.0)  # strictly falling ramp
    short, long, slope, raw = trend_components(df)
    assert (short, long, slope, raw) == (-1, -1, -1, -3)
    assert adx_factor(df) == 1.0
    assert trend_cell(df) == -3


# --- whipsaw / range: small raw + weak ADX -> pulled toward 0 ---------------

def test_whipsaw_weak_adx_pulled_to_zero():
    closes = 100.0 + 2.0 * (np.arange(260) % 2)  # 100,102,100,102,...
    df = _df(closes)
    assert adx_factor(df) == 0.25          # choppy -> low ADX
    assert abs(trend_cell(df)) <= 1        # dragged toward neutral


# --- flat slope -------------------------------------------------------------

def test_flat_slope_is_zero():
    # rise for 130 bars, then a 70-bar plateau so both SMA50 windows sit fully
    # inside the constant tail -> slope component 0.
    rising = 100.0 + np.arange(130) * 1.0
    plateau = np.full(70, rising[-1])
    df = _df(np.concatenate([rising, plateau]))
    short, long, slope, raw = trend_components(df)
    assert slope == 0
    assert raw == short + long  # slope contributes nothing


# --- guards -----------------------------------------------------------------

def test_insufficient_data_returns_none():
    df = _df(100.0 + np.arange(150) * 1.0)  # < 200 closes
    assert trend_components(df) is None
    assert trend_cell(df) is None


def test_adx_nan_uses_conservative_factor():
    df = _df(np.full(250, 100.0), spread=0.0)  # flat -> TR=0 -> ADX undefined
    assert np.isnan(ts.compute_adx(df).iloc[-1])
    assert adx_factor(df) == 0.25
    # cell still computed (not None): raw=-2 (short/long -1, slope 0) * 0.25 -> -1
    assert trend_cell(df) == _clamp_round(-2 * 0.25)


# --- score_all integration over a tiny parquet -----------------------------

def test_score_all_maps_and_marks_missing(tmp_path):
    pq = tmp_path / "price_history.parquet"
    yml = tmp_path / "price_symbols.yaml"
    yml.write_text("symbols:\n  EURUSD: EURUSD\n  GOLD: XAUUSD\n")

    up = _df(100.0 + np.arange(250) * 1.0)
    up.insert(0, "symbol", "EURUSD")
    up["source"] = "mt5"
    up = up[["symbol", "date", "open", "high", "low", "close", "source"]]
    up.to_parquet(pq, index=False)

    res = score_all(parquet_path=pq, yaml_path=yml)
    assert set(res) == {"EURUSD", "GOLD"}
    assert res["EURUSD"]["trend_cell"] == 3
    assert res["EURUSD"]["raw"] == 3
    # GOLD has no series -> all-None entry
    assert res["GOLD"]["trend_cell"] is None
    assert all(res["GOLD"][k] is None for k in ("short", "long", "slope", "raw", "adx", "factor"))
