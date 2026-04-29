"""Tests for src.retail_compute — pure transformations, no network."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.retail_compute import (
    compute_3m_extreme,
    compute_6m_extreme,
    compute_contrarian_signal,
    compute_cot_confluence,
    compute_underwater_flag,
    compute_volume_position_divergence,
    extreme_class,
)

DEFAULT_CFG = {
    "contrarian_threshold": 70,
    "vol_position_divergence_pp": 15,
    "capitulation_long_min_crowd_pct": 60,
    "capitulation_long_min_loss_pct": 1.0,
    "capitulation_short_min_crowd_pct": 60,
    "capitulation_short_min_loss_pct": 1.0,
    "cot_confluence_retail_extreme_pct": 70,
    "cot_confluence_cot_extreme_rank": 0.85,
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
# Capitulation pressure (renamed from underwater)
# ---------------------------------------------------------------------------

def test_capitulation_long_pressure_does_NOT_fire_on_small_loss():
    # 0.5% loss is below the 1% threshold, even with crowded longs → no fire.
    out = compute_underwater_flag(
        long_pct=70.0,
        avg_long_price=1.0921,
        avg_short_price=1.0810,
        spot_estimate=1.0865,  # avg_long is only ~0.52% above spot
        signal_config=DEFAULT_CFG,
    )
    assert out["long_pressure"] is False
    # Pip estimate still surfaced for modal text.
    assert out["long_pnl_pips_estimate"] < 0


def test_capitulation_long_pressure_does_NOT_fire_when_not_crowded():
    # ≥1% loss but only 50% long → not crowded → no fire.
    out = compute_underwater_flag(
        long_pct=50.0,
        avg_long_price=1.10,
        avg_short_price=1.05,
        spot_estimate=1.08,  # avg_long ~1.85% above spot
        signal_config=DEFAULT_CFG,
    )
    assert out["long_pressure"] is False


def test_capitulation_long_pressure_fires_when_crowded_AND_deep_loss():
    out = compute_underwater_flag(
        long_pct=72.0,
        avg_long_price=1.10,
        avg_short_price=1.05,
        spot_estimate=1.08,  # avg_long ~1.85% above spot
        signal_config=DEFAULT_CFG,
    )
    assert out["long_pressure"] is True


def test_capitulation_short_pressure_does_NOT_fire_on_small_loss():
    out = compute_underwater_flag(
        long_pct=30.0,
        avg_long_price=1.10,
        avg_short_price=1.0935,
        spot_estimate=1.10,  # avg_short only ~0.59% below spot
        signal_config=DEFAULT_CFG,
    )
    assert out["short_pressure"] is False


def test_capitulation_short_pressure_fires_when_crowded_AND_deep_loss():
    out = compute_underwater_flag(
        long_pct=20.0,        # short_pct = 80% → crowded
        avg_long_price=1.10,
        avg_short_price=1.05,
        spot_estimate=1.08,   # avg_short ~2.78% below spot
        signal_config=DEFAULT_CFG,
    )
    assert out["short_pressure"] is True


def test_capitulation_handles_no_avg_prices():
    out = compute_underwater_flag(
        long_pct=70.0,
        avg_long_price=None,
        avg_short_price=None,
        spot_estimate=None,
        signal_config=DEFAULT_CFG,
    )
    assert out["long_pressure"] is False
    assert out["short_pressure"] is False


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
# COT confluence
# ---------------------------------------------------------------------------

def _cot_df(symbol: str, spec_ext_6m: float, report_date: str = "2026-04-22") -> pd.DataFrame:
    return pd.DataFrame({
        "report_date_as_yyyy_mm_dd": [pd.Timestamp(report_date)],
        "symbol": [symbol],
        "spec_ext_6m": [spec_ext_6m],
    })


def test_cot_confluence_returns_none_when_link_missing():
    out = compute_cot_confluence(
        retail_long_pct=85.0,
        cot_link=None,
        invert_cot=False,
        cot_history_df=_cot_df("EUR", 0.95),
        signal_config=DEFAULT_CFG,
    )
    assert out is None


def test_cot_confluence_returns_none_when_no_cot_data():
    out = compute_cot_confluence(
        retail_long_pct=85.0,
        cot_link="EUR",
        invert_cot=False,
        cot_history_df=None,
        signal_config=DEFAULT_CFG,
    )
    assert out is None


def test_cot_confluence_returns_none_when_only_retail_extreme():
    # Retail crowded long but COT large specs neutral → no confluence.
    out = compute_cot_confluence(
        retail_long_pct=85.0,
        cot_link="EUR",
        invert_cot=False,
        cot_history_df=_cot_df("EUR", 0.50),
        signal_config=DEFAULT_CFG,
    )
    assert out is None


def test_cot_confluence_returns_none_when_only_cot_extreme():
    # COT crowded long but retail balanced → no confluence.
    out = compute_cot_confluence(
        retail_long_pct=50.0,
        cot_link="EUR",
        invert_cot=False,
        cot_history_df=_cot_df("EUR", 0.92),
        signal_config=DEFAULT_CFG,
    )
    assert out is None


def test_cot_confluence_returns_none_when_they_disagree():
    # Retail extreme-long, COT extreme-short → disagreement, no edge.
    out = compute_cot_confluence(
        retail_long_pct=85.0,
        cot_link="EUR",
        invert_cot=False,
        cot_history_df=_cot_df("EUR", 0.05),
        signal_config=DEFAULT_CFG,
    )
    assert out is None


def test_cot_confluence_bearish_when_both_extreme_long():
    out = compute_cot_confluence(
        retail_long_pct=84.0,
        cot_link="EUR",
        invert_cot=False,
        cot_history_df=_cot_df("EUR", 0.92),
        signal_config=DEFAULT_CFG,
    )
    assert out is not None
    assert out["has_confluence"] is True
    assert out["direction"] == "bearish_contrarian"
    assert out["retail_long_pct"] == pytest.approx(84.0)
    assert out["cot_spec_ext_6m"] == pytest.approx(0.92)
    assert out["cot_report_date"] == "2026-04-22"


def test_cot_confluence_bullish_when_both_extreme_short():
    out = compute_cot_confluence(
        retail_long_pct=12.0,         # short_pct = 88
        cot_link="EUR",
        invert_cot=False,
        cot_history_df=_cot_df("EUR", 0.08),
        signal_config=DEFAULT_CFG,
    )
    assert out is not None
    assert out["direction"] == "bullish_contrarian"
    assert out["cot_spec_ext_6m"] == pytest.approx(0.08)


def test_cot_confluence_inverts_when_flagged():
    # USDJPY: retail long USD ≈ short JPY. If JPY COT spec_ext_6m=0.05 (short
    # extreme), from USDJPY POV that flips to 0.95 → matches retail extreme-long.
    out = compute_cot_confluence(
        retail_long_pct=85.0,
        cot_link="JPY",
        invert_cot=True,
        cot_history_df=_cot_df("JPY", 0.05),
        signal_config=DEFAULT_CFG,
    )
    assert out is not None
    assert out["direction"] == "bearish_contrarian"
    assert out["cot_spec_ext_6m"] == pytest.approx(0.95)


def test_cot_confluence_inverts_no_match_when_directions_align_in_underlying():
    # Without invert: retail long + COT long → bearish confluence.
    # With invert applied to same data: retail long + COT 0.08 → disagreement, None.
    out = compute_cot_confluence(
        retail_long_pct=85.0,
        cot_link="JPY",
        invert_cot=True,
        cot_history_df=_cot_df("JPY", 0.92),  # inverts to 0.08 → COT extreme-short
        signal_config=DEFAULT_CFG,
    )
    assert out is None
