"""Tests for src.sentiment_compute — pure transformations, no network."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.sentiment_compute import (
    compute_pc_metrics,
    compute_vix_ratio_metrics,
    pc_index_score,
    _classify_regime,
    _signal_for,
)

ROOT = Path(__file__).resolve().parents[1]
THRESHOLDS_YAML = str(ROOT / "data" / "pc_thresholds.yaml")


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "ratio,expected",
    [
        (1.15, "ACUTE_PANIC"),
        (1.05, "BACKWARDATION"),
        (0.95, "NORMAL"),
        (0.85, "COMPLACENCY"),
    ],
)
def test_regime_classification(ratio, expected):
    assert _classify_regime(ratio) == expected


def test_regime_boundary_values():
    # Boundary behavior per spec:
    #   ratio > 1.10 → ACUTE_PANIC
    #   1.00 < ratio <= 1.10 → BACKWARDATION
    #   0.90 <= ratio <= 1.00 → NORMAL
    #   ratio < 0.90 → COMPLACENCY
    assert _classify_regime(1.10) == "BACKWARDATION"
    assert _classify_regime(1.00) == "NORMAL"
    assert _classify_regime(0.90) == "NORMAL"


# ---------------------------------------------------------------------------
# Crossover detection
# ---------------------------------------------------------------------------

def test_crossovers_on_handcrafted_series():
    # Hand-crafted ratio sequence with known crossovers.
    #   day 1: 0.95  (below)
    #   day 2: 1.02  ← crossed above
    #   day 3: 1.05
    #   day 4: 0.98  ← crossed below
    #   day 5: 0.96
    #   day 6: 1.00  ← crossed above (1.00 counts as >=)
    #   day 7: 1.11
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=7, freq="D"),
            "vix_close": [15.0] * 7,
            "vix3m_close": [16.0] * 7,
            "ratio": [0.95, 1.02, 1.05, 0.98, 0.96, 1.00, 1.11],
        }
    )
    out = compute_vix_ratio_metrics(df)
    assert list(out["crossed_above_1"]) == [False, True, False, False, False, True, False]
    assert list(out["crossed_below_1"]) == [False, False, False, True, False, False, False]


def test_days_since_regime_flip_resets_on_change():
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=6, freq="D"),
            "vix_close": [15.0] * 6,
            "vix3m_close": [16.0] * 6,
            # NORMAL, NORMAL, NORMAL, BACKWARDATION, BACKWARDATION, ACUTE_PANIC
            "ratio": [0.95, 0.96, 0.97, 1.05, 1.07, 1.15],
        }
    )
    out = compute_vix_ratio_metrics(df)
    assert list(out["days_since_regime_flip"]) == [0, 1, 2, 0, 1, 0]


# ---------------------------------------------------------------------------
# Percentile rank
# ---------------------------------------------------------------------------

def test_percentile_rank_on_known_distribution():
    # Build a 252-day window of 0.00..2.51 (step 0.01). The last observation is
    # 2.51 — the maximum → percentile should be 100.
    values = np.linspace(0.00, 2.51, 252)
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=252, freq="B"),
            "total": values,
            "equity": values,
            "index": values,
            "spx_spxw": values,
            "vix": values,
        }
    )
    out = compute_pc_metrics(df, THRESHOLDS_YAML)
    pct = out["total"]["df"]["percentile_rank"].iloc[-1]
    assert pct == pytest.approx(100.0, abs=0.5)

    # Mid-point check
    mid_idx = 125
    mid_value = values[mid_idx]
    mid_pct = out["total"]["df"]["percentile_rank"].iloc[mid_idx]
    # mid_value is the max of the prefix so far → percentile ≈ 100 over that prefix
    assert mid_pct > 90


# ---------------------------------------------------------------------------
# Signal interpretation
# ---------------------------------------------------------------------------

def test_signal_contrarian_high_put_is_bearish_extreme():
    # Contrarian: value >= high_put → "bearish_extreme" (contrarian bullish)
    assert _signal_for(1.30, high_put=1.20, high_call=0.70, interpretation="contrarian") == "bearish_extreme"
    assert _signal_for(0.60, high_put=1.20, high_call=0.70, interpretation="contrarian") == "bullish_extreme"
    assert _signal_for(0.95, high_put=1.20, high_call=0.70, interpretation="contrarian") == "neutral"


def test_signal_direct_vix_high_put_is_bullish():
    # VIX: direct — high put volume → bullish (for SPX)
    assert _signal_for(0.90, high_put=0.80, high_call=0.30, interpretation="direct") == "bullish"
    assert _signal_for(0.25, high_put=0.80, high_call=0.30, interpretation="direct") == "bearish"
    assert _signal_for(0.50, high_put=0.80, high_call=0.30, interpretation="direct") == "neutral"


def test_vix_variant_signal_is_direct_not_contrarian():
    """Regression: VIX variant must use 'direct' interpretation, not 'contrarian'."""
    # A single-row frame with a very high VIX P/C — should be "bullish" for the
    # VIX variant, not "bearish_extreme".
    df = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-02")],
            "total": [0.80],
            "equity": [0.50],
            "index": [1.00],
            "spx_spxw": [1.10],
            "vix": [1.50],  # well above static high_put of 0.80 for VIX
        }
    )
    out = compute_pc_metrics(df, THRESHOLDS_YAML)
    assert out["vix"]["current"]["signal"] == "bullish"
    assert out["vix"]["config"]["interpretation"] == "direct"


def test_total_variant_signal_is_contrarian():
    df = pd.DataFrame(
        {
            "date": [pd.Timestamp("2024-01-02")],
            "total": [1.50],  # above 1.20 static high_put
            "equity": [0.50],
            "index": [1.00],
            "spx_spxw": [1.10],
            "vix": [0.50],
        }
    )
    out = compute_pc_metrics(df, THRESHOLDS_YAML)
    assert out["total"]["current"]["signal"] == "bearish_extreme"
    assert out["total"]["config"]["interpretation"] == "contrarian"


# ---------------------------------------------------------------------------
# thresholds_source switchover
# ---------------------------------------------------------------------------

def test_thresholds_source_static_when_warmup():
    # 100 observations < 252-day dynamic_from_day → should be static_default
    df = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=100, freq="B"),
            "total": np.linspace(0.5, 1.5, 100),
            "equity": np.linspace(0.3, 0.9, 100),
            "index": np.linspace(0.8, 1.8, 100),
            "spx_spxw": np.linspace(0.9, 2.0, 100),
            "vix": np.linspace(0.2, 0.9, 100),
        }
    )
    out = compute_pc_metrics(df, THRESHOLDS_YAML)
    for variant in ("total", "equity", "index", "spx_spxw", "vix"):
        assert out[variant]["current"]["thresholds_source"] == "static_default"


def test_thresholds_source_rolling_after_warmup():
    # 300 observations >= 252-day dynamic_from_day → should be rolling_1y
    df = pd.DataFrame(
        {
            "date": pd.date_range("2023-01-01", periods=300, freq="B"),
            "total": np.linspace(0.5, 1.5, 300),
            "equity": np.linspace(0.3, 0.9, 300),
            "index": np.linspace(0.8, 1.8, 300),
            "spx_spxw": np.linspace(0.9, 2.0, 300),
            "vix": np.linspace(0.2, 0.9, 300),
        }
    )
    out = compute_pc_metrics(df, THRESHOLDS_YAML)
    for variant in ("total", "equity", "index", "spx_spxw", "vix"):
        assert out[variant]["current"]["thresholds_source"] == "rolling_1y"


# ---------------------------------------------------------------------------
# pc_index_score — contrarian SENTIMENT for US indices (display-only)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "pct, expected",
    [
        (95.0, 3), (90.0, 3),           # >= 90
        (89.999, 2), (75.0, 2),         # [75, 90)
        (74.999, 1), (60.0, 1),         # [60, 75)
        (59.999, 0), (40.0, 0),         # [40, 60) dead zone
        (39.999, -1), (25.0, -1),       # [25, 40)
        (24.999, -2), (10.0, -2),       # [10, 25)
        (9.999, -3), (0.0, -3),         # < 10
        (86.31, 2),                     # live value sanity
    ],
)
def test_pc_index_score_bands(pct, expected):
    score, meta = pc_index_score(pct)
    assert score == expected
    assert meta["pct"] == pytest.approx(pct)
    assert meta["basis"] == "pct_1y"


def test_pc_index_score_insufficient():
    for bad in (None, float("nan")):
        score, meta = pc_index_score(bad)
        assert score == 0
        assert meta["basis"] == "insufficient_history"
        assert meta["pct"] is None
