"""Tests for src.crossasset_compute — pure cross-asset scoring, no I/O.

Covers the top-level {growth, inflation, labour, rates} model where `rates` is a
bounded weighted mean of {rate_exp_2y, real_yield_10y}: aligned sub-components
→ rates ±2 (not ±4); divergent → cancel; graceful missing sub-component.
Plus factor signs, weighted mean, no-data exclusion, and the 5-level bias.
"""
from __future__ import annotations

import pytest

from src.crossasset_compute import (
    compute_crossasset_scores,
    _cell,
    _realyield_raw,
    _realyield_series,
)

# Config mirroring data/crossasset_instruments.yaml (one index + one metal).
def _rates_block():
    return {"weight": 1.0, "components": {
        "rate_exp_2y": {"sign": -1, "weight": 1.0},
        "real_yield_10y": {"sign": -1, "weight": 1.0},
    }}

CONFIG = {
    "scale": 5,
    "bias_thresholds": {"mild": 2.0, "very": 4.4},
    "instruments": {
        "SP500": {"type": "index", "home_ccy": "USD", "factors": {
            "growth": {"sign": 1, "weight": 1.0},
            "inflation": {"sign": -1, "weight": 1.0},
            "labour": {"sign": -1, "weight": 1.0},
            "rates": _rates_block(),
        }},
        "GOLD": {"type": "metal", "home_ccy": "USD", "factors": {
            "growth": {"sign": -1, "weight": 0.5},
            "inflation": {"sign": -1, "weight": 1.0},
            "labour": {"sign": -1, "weight": 1.0},
            "rates": _rates_block(),
        }},
    },
}


def _cats(**usd):
    return {"USD": dict(usd)}


def _factor(res, name):
    return next(f for f in res["factors"] if f["name"] == name)


def _sub(res, name):
    rates = _factor(res, "rates")
    return next(c for c in rates["components"] if c["name"] == name)


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

def test_cell_excludes_no_coverage():
    assert _cell(2) == 2
    assert _cell({"score_cell": 2, "coverage": 3}) == 2
    assert _cell({"score_cell": 0, "coverage": 0}) is None
    assert _cell({"score_cell": 0, "coverage": 4}) == 0
    assert _cell(None) is None


def test_realyield_raw_and_series():
    class RY:
        score = 2
        series = "REAL_10Y_TSY"
    assert _realyield_raw(RY()) == 2
    assert _realyield_series(RY()) == "REAL_10Y_TSY"
    assert _realyield_raw(1) == 1
    assert _realyield_raw(None) is None
    assert _realyield_series(None) == "DFII10"


# ---------------------------------------------------------------------------
# Rates grouping — the core fix
# ---------------------------------------------------------------------------

def test_rates_aligned_does_not_stack():
    # rate_exp_2y +2 and real_yield +2 (both hawkish). Each sub sign -1 →
    # signed -2,-2 → rates value = mean = -2 (NOT -4).
    res = compute_crossasset_scores(_cats(monetary=2), realyield_score=2, config=CONFIG)
    sp = res["SP500"]
    rates = _factor(sp, "rates")
    assert rates["present"] is True
    assert rates["value"] == pytest.approx(-2.0)        # bounded, not -4
    assert rates["contribution"] == pytest.approx(-2.0)  # weight 1.0 × value
    assert _sub(sp, "rate_exp_2y")["contribution"] == pytest.approx(-2.0)
    assert _sub(sp, "real_yield_10y")["contribution"] == pytest.approx(-2.0)


def test_rates_divergent_cancels():
    # rate_exp_2y +2 (signed -2) vs real_yield -2 (signed +2) → rates ≈ 0.
    res = compute_crossasset_scores(_cats(monetary=2), realyield_score=-2, config=CONFIG)
    rates = _factor(res["SP500"], "rates")
    assert rates["value"] == pytest.approx(0.0)


def test_rates_graceful_missing_realyield():
    # real_yield absent → rates = rate_exp_2y alone (signed). monetary +2, sign -1 → -2.
    res = compute_crossasset_scores(_cats(monetary=2), realyield_score=None, config=CONFIG)
    sp = res["SP500"]
    assert _sub(sp, "real_yield_10y")["present"] is False
    assert _sub(sp, "rate_exp_2y")["present"] is True
    assert _factor(sp, "rates")["value"] == pytest.approx(-2.0)
    assert _factor(sp, "rates")["present"] is True


def test_rates_graceful_missing_rate_exp():
    # monetary gap (no 2y) → rates = real_yield alone. real_yield +2, sign -1 → -2.
    res = compute_crossasset_scores(_cats(), realyield_score=2, config=CONFIG)
    sp = res["SP500"]
    assert _sub(sp, "rate_exp_2y")["present"] is False
    assert _sub(sp, "real_yield_10y")["present"] is True
    assert _factor(sp, "rates")["value"] == pytest.approx(-2.0)


def test_rates_absent_when_no_subcomponents():
    res = compute_crossasset_scores(_cats(), realyield_score=None, config=CONFIG)
    assert _factor(res["SP500"], "rates")["present"] is False


def test_rates_does_not_dominate_vs_growth():
    # growth +2 (index sign +1 → +2) ; rates aligned hawkish → -2. Mean over the
    # two present factors = 0 → Neutral. Pre-fix (monetary+real_yield = -4) this
    # would have swamped growth.
    res = compute_crossasset_scores(_cats(growth=2, monetary=2), realyield_score=2, config=CONFIG)
    sp = res["SP500"]
    # present: growth(+2) and rates(-2) → mean 0 → 0 × scale
    assert sp["score_precise"] == pytest.approx(0.0)
    assert sp["bias_label"] == "Neutral"


# ---------------------------------------------------------------------------
# Factor signs / weighted mean / scale
# ---------------------------------------------------------------------------

def test_hot_cpi_index_and_gold_bearish():
    res = compute_crossasset_scores(_cats(inflation=2), realyield_score=None, config=CONFIG)
    assert res["SP500"]["score_precise"] < 0
    assert res["GOLD"]["score_precise"] < 0
    assert _factor(res["SP500"], "inflation")["sign"] == -1


def test_growth_beat_index_bullish_gold_bearish_light():
    res = compute_crossasset_scores(_cats(growth=2), realyield_score=None, config=CONFIG)
    assert res["SP500"]["score_precise"] > 0
    g = _factor(res["GOLD"], "growth")
    assert g["sign"] == -1 and g["weight"] == 0.5
    assert g["contribution"] == pytest.approx(-1.0)   # -1 × 0.5 × 2
    assert res["GOLD"]["score_precise"] < 0


def test_weighted_mean_single_factor_saturates():
    # growth +2 only present (index sign +1): (1×(+2))/1 × 5 = +10.
    res = compute_crossasset_scores(_cats(growth=2), realyield_score=None, config=CONFIG)
    assert res["SP500"]["score_precise"] == pytest.approx(10.0)
    assert res["SP500"]["coverage"] == 1


def test_all_absent_is_neutral_zero():
    res = compute_crossasset_scores({}, realyield_score=None, config=CONFIG)
    for sym in ("SP500", "GOLD"):
        assert res[sym]["score_precise"] == 0.0
        assert res[sym]["bias_label"] == "Neutral"
        assert res[sym]["coverage"] == 0


# ---------------------------------------------------------------------------
# Bias thresholds (mild 2.0 / very 4.4)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("growth,expected", [
    (1, "Very Bullish"),    # +5 ≥ very 4.4
    (-1, "Very Bearish"),   # -5
    (0, "Neutral"),
])
def test_bias_single_growth(growth, expected):
    res = compute_crossasset_scores(_cats(growth=growth), realyield_score=None, config=CONFIG)
    assert res["SP500"]["bias_label"] == expected


def test_bias_mild_band():
    # growth +2 (+2) & inflation 0 (present, 0): mean (2+0)/2 = 1 → ×5 = 5.0 → Very.
    # Use growth +1 & inflation 0 → mean 0.5 → 2.5 → in [2.0,4.4) → Bullish.
    res = compute_crossasset_scores(_cats(growth=1, inflation=0), realyield_score=None, config=CONFIG)
    sp = res["SP500"]
    assert sp["score_precise"] == pytest.approx(2.5)
    assert sp["bias_label"] == "Bullish"
