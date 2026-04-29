"""Tests for src.retail_compute — pure transformations, no network."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.retail_compute import (
    compute_3m_extreme,
    compute_6m_extreme,
    compute_contrarian_signal,
    compute_cot_divergence,
    compute_underwater_flag,
    compute_volume_position_divergence,
    extreme_class,
)

DEFAULT_CFG = {
    "contrarian_threshold": 70,
    "underwater_threshold_pct": 0.5,
    "vol_position_divergence_pp": 15,
    "cot_divergence_threshold": 0.4,
    "ramp_in": {"min_n_for_extreme": 20},
}


# ---------------------------------------------------------------------------
# extreme_class — match COT _ext_class boundaries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "v,expected",
    [
        (None, "ext-na"),
        (float("nan"), "ext-na"),
        (0.96, "ext-95"),
        (0.95, "ext-95"),
        (0.80, "ext-70"),
        (0.70, "ext-70"),
        (0.50, "ext-neutral"),
        (0.31, "ext-neutral"),
        (0.30, "ext-30"),
        (0.10, "ext-30"),
        (0.05, "ext-05"),
        (0.01, "ext-05"),
    ],
)
def test_extreme_class_buckets(v, expected):
    assert extreme_class(v) == expected


# ---------------------------------------------------------------------------
# compute_3m / 6m extreme
# ---------------------------------------------------------------------------

def test_3m_extreme_returns_none_when_below_min_n():
    s = pd.Series([60.0, 65.0, 70.0])  # n=3 < 20
    val, n = compute_3m_extreme(s, DEFAULT_CFG)
    assert val is None
    assert n == 3


def test_6m_extreme_returns_none_when_below_min_n():
    s = pd.Series([60.0] * 5)
    val, n = compute_6m_extreme(s, DEFAULT_CFG)
    assert val is None
    assert n == 5


def test_3m_extreme_returns_high_for_top_value():
    # 25 values: 0..24, last is the max → extreme should be ~1.0.
    s = pd.Series(np.linspace(0, 24, 25))
    val, n = compute_3m_extreme(s, DEFAULT_CFG)
    assert n == 25
    assert val is not None
    assert val == pytest.approx(1.0, abs=0.05)


def test_3m_extreme_returns_low_for_bottom_value():
    s = pd.Series(list(np.linspace(10, 30, 24)) + [0.0])  # last is the min
    val, n = compute_3m_extreme(s, DEFAULT_CFG)
    assert n == 25
    assert val is not None
    assert val == pytest.approx(0.0, abs=0.05)


def test_3m_extreme_returns_mid_for_median_value():
    vals = list(np.linspace(0, 100, 25))
    vals[-1] = 50.0  # median-ish
    val, n = compute_3m_extreme(pd.Series(vals), DEFAULT_CFG)
    assert val is not None
    assert 0.3 <= val <= 0.7


# ---------------------------------------------------------------------------
# Contrarian signal
# ---------------------------------------------------------------------------

def test_contrarian_signal_at_70_long_is_bearish():
    assert compute_contrarian_signal(70, DEFAULT_CFG) == "bearish_contrarian"


def test_contrarian_signal_just_below_70_is_neutral():
    assert compute_contrarian_signal(69.9, DEFAULT_CFG) == "neutral"


def test_contrarian_signal_at_70_short_is_bullish():
    # short = 100 - 30 = 70 → bullish contrarian
    assert compute_contrarian_signal(30, DEFAULT_CFG) == "bullish_contrarian"


def test_contrarian_signal_neutral_in_band():
    assert compute_contrarian_signal(50, DEFAULT_CFG) == "neutral"


def test_contrarian_signal_handles_none():
    assert compute_contrarian_signal(None, DEFAULT_CFG) == "neutral"


# ---------------------------------------------------------------------------
# Underwater flag
# ---------------------------------------------------------------------------

def test_underwater_longs_flag_when_avg_above_spot():
    out = compute_underwater_flag(
        long_pct=70.0,
        avg_long_price=1.0921,
        avg_short_price=1.0810,
        spot_estimate=1.0865,
        signal_config=DEFAULT_CFG,
    )
    # 1.0921 > 1.0865 * (1 + 0.005) = 1.0919... → True
    assert out["longs_underwater"] is True
    # PnL pips: spot - avg_long → negative
    assert out["long_pnl_pips_estimate"] < 0


def test_underwater_shorts_flag_when_avg_below_spot():
    out = compute_underwater_flag(
        long_pct=30.0,
        avg_long_price=1.10,
        avg_short_price=1.05,
        spot_estimate=1.10,
        signal_config=DEFAULT_CFG,
    )
    assert out["shorts_underwater"] is True
    # short PnL = avg_short - spot < 0
    assert out["short_pnl_pips_estimate"] < 0


def test_underwater_handles_no_avg_prices():
    out = compute_underwater_flag(
        long_pct=50.0,
        avg_long_price=None,
        avg_short_price=None,
        spot_estimate=None,
        signal_config=DEFAULT_CFG,
    )
    assert out["longs_underwater"] is False
    assert out["shorts_underwater"] is False


# ---------------------------------------------------------------------------
# Volume vs position divergence
# ---------------------------------------------------------------------------

def test_vol_pos_divergence_detects_27pp():
    # 72% long by accounts, but only 44% of volume long → ~28pp gap
    out = compute_volume_position_divergence(
        long_pct=72.0,
        long_volume=905.47,
        short_volume=1142.58,
        signal_config=DEFAULT_CFG,
    )
    assert out["has_divergence"] is True
    assert out["divergence_pp"] > 25


def test_vol_pos_divergence_ignores_5pp():
    out = compute_volume_position_divergence(
        long_pct=55.0,
        long_volume=600,
        short_volume=500,
        signal_config=DEFAULT_CFG,
    )
    # vol_long = 600/1100 = 54.5% → gap 0.5pp
    assert out["has_divergence"] is False


def test_vol_pos_divergence_missing_volume():
    out = compute_volume_position_divergence(
        long_pct=55.0,
        long_volume=None,
        short_volume=None,
        signal_config=DEFAULT_CFG,
    )
    assert out["has_divergence"] is False
    assert out["volume_long_pct"] is None


# ---------------------------------------------------------------------------
# COT divergence
# ---------------------------------------------------------------------------

def test_cot_divergence_returns_none_when_link_missing():
    out = compute_cot_divergence(
        retail_long_pct=70.0,
        cot_link=None,
        invert_cot=False,
        cot_history_df=pd.DataFrame(),
        signal_config=DEFAULT_CFG,
    )
    assert out is None


def test_cot_divergence_returns_none_when_no_cot_data():
    out = compute_cot_divergence(
        retail_long_pct=70.0,
        cot_link="EUR",
        invert_cot=False,
        cot_history_df=None,
        signal_config=DEFAULT_CFG,
    )
    assert out is None


def test_cot_divergence_detects_large_gap():
    # Retail 70% long, CFTC large specs only 20% long → gap of 0.50.
    cot = pd.DataFrame({
        "report_date_as_yyyy_mm_dd": [pd.Timestamp("2026-04-22")],
        "symbol": ["EUR"],
        "noncomm_positions_long_all": [20000],
        "noncomm_positions_short_all": [80000],
    })
    out = compute_cot_divergence(
        retail_long_pct=70.0,
        cot_link="EUR",
        invert_cot=False,
        cot_history_df=cot,
        signal_config=DEFAULT_CFG,
    )
    assert out is not None
    assert out["has_divergence"] is True
    assert out["retail_long_normalized"] == pytest.approx(0.70)
    assert out["cot_spec_long_normalized"] == pytest.approx(0.20)
    assert out["divergence_magnitude"] == pytest.approx(0.50)
    assert out["cot_report_date"] == "2026-04-22"


def test_cot_divergence_inverts_when_flagged():
    # USDJPY: retail long USD ≈ short JPY.
    # If COT JPY large specs are 20% long → from USDJPY perspective they are 80% long.
    cot = pd.DataFrame({
        "report_date_as_yyyy_mm_dd": [pd.Timestamp("2026-04-22")],
        "symbol": ["JPY"],
        "noncomm_positions_long_all": [20000],
        "noncomm_positions_short_all": [80000],
    })
    out = compute_cot_divergence(
        retail_long_pct=85.0,
        cot_link="JPY",
        invert_cot=True,
        cot_history_df=cot,
        signal_config=DEFAULT_CFG,
    )
    assert out is not None
    # Inverted: 1 - 0.20 = 0.80; retail = 0.85 → magnitude 0.05 → no divergence
    assert out["cot_spec_long_normalized"] == pytest.approx(0.80)
    assert out["has_divergence"] is False


def test_cot_divergence_ignored_when_below_threshold():
    cot = pd.DataFrame({
        "report_date_as_yyyy_mm_dd": [pd.Timestamp("2026-04-22")],
        "symbol": ["EUR"],
        "noncomm_positions_long_all": [50000],
        "noncomm_positions_short_all": [50000],
    })
    out = compute_cot_divergence(
        retail_long_pct=60.0,
        cot_link="EUR",
        invert_cot=False,
        cot_history_df=cot,
        signal_config=DEFAULT_CFG,
    )
    # retail 0.60 vs cot 0.50 → magnitude 0.10 → no divergence (threshold 0.40)
    assert out["has_divergence"] is False
