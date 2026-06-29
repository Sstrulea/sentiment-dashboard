"""Tests for src.cot_score — pure scoring functions, no network or fresh I/O."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.cot_score import (
    DATE_COL,
    EXT_3Y_COL,
    EXT_6M_COL,
    NET_COL,
    SYMBOL_COL,
    cot_cell,
    flow_score,
    level_score,
    pair_cot,
    score_currencies,
    score_metals,
)


# ---------------------------------------------------------------------------
# Helpers — deterministic spec_net series with known flow behaviour.
# ---------------------------------------------------------------------------

def _rising() -> pd.Series:
    # Strong upward trend with a non-period-4 wobble so sigma > 0 but small
    # relative to the 4-week change -> z >> +0.5.
    return pd.Series([i * 1000 + (i % 3) * 120 for i in range(24)], dtype=float)


def _falling() -> pd.Series:
    return pd.Series([-(i * 1000 + (i % 3) * 120) for i in range(24)], dtype=float)


def _flat_with_noise() -> pd.Series:
    # Last 5 reports are flat (chg4 == 0) but an earlier perturbation gives
    # sigma > 0 -> z == 0 -> flow 0 on a healthy ("ok") basis.
    vals = [100_000.0] * 24
    vals[8] = 100_500.0
    vals[9] = 99_500.0
    return pd.Series(vals, dtype=float)


# ---------------------------------------------------------------------------
# level_score
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "e6, e3, expected",
    [
        (0.90, 0.90, -3),   # blend 90
        (0.53, 0.53, 0),    # blend 53 -> dead zone
        (0.365, 0.365, 1),  # blend 36.5
        (0.14, 0.14, 3),    # blend 14
    ],
)
def test_level_blends(e6, e3, expected):
    level, meta = level_score(e6, e3)
    assert level == expected
    assert meta["basis"] == "6m_3y"
    assert meta["blend"] == pytest.approx((e6 + e3) / 2 * 100)


def test_level_6m_only_when_3y_missing():
    level, meta = level_score(0.36, None)
    assert level == 1
    assert meta["basis"] == "6m_only"
    assert meta["blend"] == pytest.approx(36.0)

    # NaN is treated the same as None.
    level_nan, meta_nan = level_score(0.36, float("nan"))
    assert level_nan == 1
    assert meta_nan["basis"] == "6m_only"


@pytest.mark.parametrize(
    "blend, expected",
    [
        (85.0, -3),
        (84.999, -2),
        (70.0, -2),
        (55.0, -1),
        (54.999, 0),
        (45.0, 0),
        (44.999, 1),
        (30.0, 1),
        (15.0, 2),
        (14.999, 3),
    ],
)
def test_level_exact_boundaries(blend, expected):
    # Pass equal extremes so blend == value (blend = (x + x)/2 * 100 = x*100).
    x = blend / 100.0
    level, _ = level_score(x, x)
    assert level == expected


# ---------------------------------------------------------------------------
# flow_score
# ---------------------------------------------------------------------------

def test_flow_rising_is_positive():
    flow, meta = flow_score(_rising())
    assert flow == 1
    assert meta["basis"] == "ok"
    assert meta["chg4"] > 0
    assert meta["sigma"] > 0
    assert meta["z"] >= 0.5


def test_flow_falling_is_negative():
    flow, meta = flow_score(_falling())
    assert flow == -1
    assert meta["z"] <= -0.5


def test_flow_flat_is_zero():
    flow, meta = flow_score(_flat_with_noise())
    assert flow == 0
    assert meta["basis"] == "ok"
    assert meta["sigma"] > 0
    assert meta["z"] == pytest.approx(0.0)


def test_flow_insufficient_history():
    flow, meta = flow_score(pd.Series(range(8), dtype=float))
    assert flow == 0
    assert meta["basis"] == "insufficient_history"
    assert np.isnan(meta["z"])


def test_flow_zero_sigma_guard():
    # Perfectly linear -> every 4-week change identical -> sigma == 0.
    flow, meta = flow_score(pd.Series([i * 1000.0 for i in range(24)], dtype=float))
    assert flow == 0
    assert meta["basis"] == "insufficient_history"
    assert meta["sigma"] == 0


# ---------------------------------------------------------------------------
# cot_cell
# ---------------------------------------------------------------------------

def test_cell_crowded_long_plus_flow():
    # Level -3 (blend 90) + flow +1 = -2.
    cell, meta = cot_cell(0.90, 0.90, _rising())
    assert meta["level"] == -3
    assert meta["flow"] == 1
    assert cell == -2


def test_cell_crowded_short_plus_flow_clamped():
    # Level +3 (blend 5) + flow +1 = +4 (at the clamp ceiling).
    cell, meta = cot_cell(0.05, 0.05, _rising())
    assert meta["level"] == 3
    assert meta["flow"] == 1
    assert cell == 4


def test_cell_negative_clamp():
    # Level -3 + flow -1 = -4.
    cell, meta = cot_cell(0.90, 0.90, _falling())
    assert meta["level"] == -3
    assert meta["flow"] == -1
    assert cell == -4


def test_cell_bases_do_not_collide():
    _, meta = cot_cell(0.90, 0.90, _rising())
    assert meta["level_basis"] == "6m_3y"
    assert meta["flow_basis"] == "ok"


# ---------------------------------------------------------------------------
# score_metals
# ---------------------------------------------------------------------------

def _fixture_history() -> pd.DataFrame:
    dates = pd.date_range(end="2026-06-09", periods=24, freq="W-TUE")
    frames = []
    for symbol, e6, e3, net in [
        ("GOLD", 0.69, 0.25, _rising()),
        ("COPPER", 0.92, 0.99, _falling()),
    ]:
        g = pd.DataFrame(
            {
                DATE_COL: dates,
                SYMBOL_COL: symbol,
                NET_COL: net.values,
            }
        )
        # extremes only matter on the latest row; keep them constant here.
        g[EXT_6M_COL] = e6
        g[EXT_3Y_COL] = e3
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def test_score_metals_runs_on_fixture():
    out = score_metals(_fixture_history())
    assert list(out["symbol"]) == ["GOLD", "COPPER"]
    assert set(out.columns) == {
        "symbol", "ext_6m", "ext_3y", "blend", "level",
        "chg4", "sigma", "z", "flow", "cell", "basis",
    }
    gold = out[out["symbol"] == "GOLD"].iloc[0]
    assert gold["level"] == 0          # blend 47 -> dead zone
    assert gold["flow"] == 1           # rising net
    copper = out[out["symbol"] == "COPPER"].iloc[0]
    assert copper["level"] == -3       # blend 95.5 -> crowded long
    assert copper["flow"] == -1        # falling net


def test_score_metals_empty_frame():
    out = score_metals(pd.DataFrame())
    assert out.empty
    assert "cell" in out.columns


# ---------------------------------------------------------------------------
# FX: pair_cot (additive)
# ---------------------------------------------------------------------------

def test_pair_cot_usd_leg_passthrough():
    # EUR/USD: base=cell_EUR, quote USD=0 -> cell_EUR (no doubling).
    assert pair_cot(2, 0) == 2
    # USD/JPY: base USD=0, quote=cell_JPY -> -cell_JPY.
    assert pair_cot(0, 2) == -2


def test_pair_cot_none_counts_as_zero():
    assert pair_cot(None, 3) == -3
    assert pair_cot(3, None) == 3
    assert pair_cot(None, None) == 0


def test_pair_cot_cross_cancels_usd():
    # EUR/CHF: cell_EUR - cell_CHF, USD cancels by construction.
    assert pair_cot(1, 1) == 0
    assert pair_cot(3, -1) == 4


def test_pair_cot_clamps_to_pm4():
    assert pair_cot(3, -3) == 4      # +6 -> +4
    assert pair_cot(-3, 3) == -4     # -6 -> -4


# ---------------------------------------------------------------------------
# FX: score_currencies (additive)
# ---------------------------------------------------------------------------

def _fx_fixture() -> pd.DataFrame:
    dates = pd.date_range(end="2026-06-09", periods=24, freq="W-TUE")
    frames = []
    for symbol, e6, e3, net in [
        ("EUR", 0.17, 0.15, _falling()),
        ("DXY", 0.65, 0.45, _flat_with_noise()),
    ]:
        g = pd.DataFrame({DATE_COL: dates, SYMBOL_COL: symbol, NET_COL: net.values})
        g[EXT_6M_COL] = e6
        g[EXT_3Y_COL] = e3
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def test_score_currencies_runs_on_fixture():
    out = score_currencies(_fx_fixture())
    assert set(out["symbol"]) == {"EUR", "DXY"}
    assert set(out.columns) == {
        "symbol", "ext_6m", "ext_3y", "blend", "level",
        "chg4", "sigma", "z", "flow", "cell", "basis",
    }
    eur = out[out["symbol"] == "EUR"].iloc[0]
    assert eur["level"] == 2          # blend 16 -> +2
    assert eur["flow"] == -1          # falling net


def test_score_currencies_empty_frame():
    out = score_currencies(pd.DataFrame())
    assert out.empty
    assert "cell" in out.columns
