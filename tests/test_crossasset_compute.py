"""Tests for src.crossasset_compute — pure cross-asset scoring, no I/O.

Synthetic category scores + real-yield score. Covers: factor signs (hot CPI →
index AND gold bearish; growth beat → index bullish, gold slightly bearish;
rising real yield → both bearish), weighted mean, graceful real_yield absence,
no-data category exclusion, and the 5-level bias mapping.
"""
from __future__ import annotations

import pytest

from src.crossasset_compute import (
    compute_crossasset_scores,
    compute_instrument_score,
    _cell,
    _realyield_raw,
)

# Minimal config mirroring data/crossasset_instruments.yaml (one index + one metal).
CONFIG = {
    "scale": 5,
    "bias_thresholds": {"mild": 1.3, "very": 3.0},
    "instruments": {
        "SP500": {
            "type": "index", "home_ccy": "USD",
            "factors": {
                "growth": {"sign": 1, "weight": 1.0},
                "inflation": {"sign": -1, "weight": 1.0},
                "labour": {"sign": -1, "weight": 1.0},
                "monetary": {"sign": -1, "weight": 1.0},
                "real_yield": {"sign": -1, "weight": 1.0},
            },
        },
        "GOLD": {
            "type": "metal", "home_ccy": "USD",
            "factors": {
                "growth": {"sign": -1, "weight": 0.5},
                "inflation": {"sign": -1, "weight": 1.0},
                "labour": {"sign": -1, "weight": 1.0},
                "monetary": {"sign": -1, "weight": 1.0},
                "real_yield": {"sign": -1, "weight": 1.0},
            },
        },
    },
}


def _cats(**usd):
    """USD-only category map (raw score_cells)."""
    return {"USD": dict(usd)}


def _factor(res, name):
    return next(f for f in res["factors"] if f["name"] == name)


# ---------------------------------------------------------------------------
# Coercion helpers
# ---------------------------------------------------------------------------

def test_cell_accepts_raw_and_dict_and_excludes_no_coverage():
    assert _cell(2) == 2
    assert _cell(-1) == -1
    assert _cell(None) is None
    assert _cell({"score_cell": 2, "coverage": 3}) == 2
    assert _cell({"score_cell": 0, "coverage": 0}) is None   # no-data → absent
    assert _cell({"score_cell": 0, "coverage": 4}) == 0      # real Neutral → present


def test_realyield_raw_accepts_score_obj_number_none():
    class RY:
        score = 2
    assert _realyield_raw(RY()) == 2
    assert _realyield_raw(1) == 1
    assert _realyield_raw(None) is None


# ---------------------------------------------------------------------------
# Factor signs
# ---------------------------------------------------------------------------

def test_hot_cpi_makes_index_and_gold_bearish():
    # inflation hot (+2), nothing else present, no real yield.
    res = compute_crossasset_scores(_cats(inflation=2), None, CONFIG)
    sp, gold = res["SP500"], res["GOLD"]
    assert sp["score_precise"] < 0 and "Bear" in sp["bias_label"]
    assert gold["score_precise"] < 0 and "Bear" in gold["bias_label"]
    assert _factor(sp, "inflation")["sign"] == -1
    assert _factor(sp, "real_yield")["present"] is False     # graceful


def test_growth_beat_index_bullish_gold_slightly_bearish():
    # growth beat (+2) only.
    res = compute_crossasset_scores(_cats(growth=2), None, CONFIG)
    sp, gold = res["SP500"], res["GOLD"]
    assert sp["score_precise"] > 0 and "Bull" in sp["bias_label"]   # index bullish
    g = _factor(gold, "growth")
    assert gold["score_precise"] < 0                                # gold bearish
    assert g["sign"] == -1 and g["weight"] == 0.5                   # ...but light weight
    assert g["contribution"] == pytest.approx(-1.0)                 # -1 * 0.5 * 2


def test_rising_real_yield_makes_both_bearish():
    # no category data, only a rising real-yield score (+2).
    res = compute_crossasset_scores({}, 2, CONFIG)
    for sym in ("SP500", "GOLD"):
        r = res[sym]
        ry = _factor(r, "real_yield")
        assert ry["present"] is True and ry["sign"] == -1
        assert ry["contribution"] == pytest.approx(-2.0)
        assert r["score_precise"] < 0                                # both bearish


# ---------------------------------------------------------------------------
# Weighted mean (over present factors only)
# ---------------------------------------------------------------------------

def test_weighted_mean_over_present_factors():
    # SP500: growth +2 (sign +1) and inflation +1 (sign -1), others absent.
    # num = (+1*1*2) + (-1*1*1) = 1 ; wsum = 2 ; mean 0.5 ; *scale 5 = 2.5
    res = compute_crossasset_scores(_cats(growth=2, inflation=1), None, CONFIG)
    sp = res["SP500"]
    assert sp["score_precise"] == pytest.approx(2.5)
    assert sp["score"] == 2          # rounded display
    assert sp["bias_label"] == "Bullish"
    assert sp["coverage"] == 2       # only 2 factors present


def test_cancellation_to_neutral():
    # growth +1 (sign +1) vs real_yield +1 (sign -1) → cancel → 0 → Neutral.
    res = compute_crossasset_scores(_cats(growth=1), 1, CONFIG)
    sp = res["SP500"]
    assert sp["score_precise"] == pytest.approx(0.0)
    assert sp["bias_label"] == "Neutral"


def test_metal_growth_half_weight_dampens():
    # GOLD growth +2 with inflation 0 present: num = (-1*0.5*2)+(-1*1*0) = -1 ;
    # wsum = 1.5 ; mean = -0.6667 ; *5 = -3.33 → Very Bearish.
    res = compute_crossasset_scores(_cats(growth=2, inflation=0), None, CONFIG)
    gold = res["GOLD"]
    assert gold["score_precise"] == pytest.approx(-10.0 / 3.0, abs=1e-6)


# ---------------------------------------------------------------------------
# Graceful real_yield absence (FRED down) + no-data category exclusion
# ---------------------------------------------------------------------------

def test_realyield_missing_excluded_others_still_score():
    # real yield None → real_yield factor excluded everywhere; growth still scores.
    res = compute_crossasset_scores(_cats(growth=1), None, CONFIG)
    sp = res["SP500"]
    assert _factor(sp, "real_yield")["present"] is False
    assert _factor(sp, "real_yield")["contribution"] is None
    # growth +1 only present: (+1*1*1)/1 * 5 = +5
    assert sp["score_precise"] == pytest.approx(5.0)


def test_no_data_category_dict_excluded():
    # build_payload-style category dicts: coverage 0 → excluded; coverage>0 → used.
    cats = {"USD": {
        "growth": {"score_cell": 2, "coverage": 4},
        "inflation": {"score_cell": 0, "coverage": 0},   # no data → excluded
        "labour": {"score_cell": 0, "coverage": 0},
        "monetary": {"score_cell": 0, "coverage": 0},
    }}
    sp = compute_crossasset_scores(cats, None, CONFIG)["SP500"]
    assert _factor(sp, "growth")["present"] is True
    assert _factor(sp, "inflation")["present"] is False
    # only growth present (+2, sign +1): (+1*1*2)/1 * 5 = +10
    assert sp["score_precise"] == pytest.approx(10.0)
    assert sp["coverage"] == 1


def test_all_factors_absent_is_neutral_zero():
    res = compute_crossasset_scores({}, None, CONFIG)
    for sym in ("SP500", "GOLD"):
        assert res[sym]["score_precise"] == 0.0
        assert res[sym]["bias_label"] == "Neutral"
        assert res[sym]["coverage"] == 0


# ---------------------------------------------------------------------------
# Bias thresholds (5 levels, reusing FX mild=1.3 / very=3.0)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("growth,expected", [
    (0, "Neutral"),       # 0 → Neutral
    (1, "Very Bullish"),  # +5 → Very Bullish
    (-1, "Very Bearish"), # -5 → Very Bearish
])
def test_bias_single_growth_factor(growth, expected):
    sp = compute_crossasset_scores(_cats(growth=growth), None, CONFIG)["SP500"]
    assert sp["bias_label"] == expected


def test_bias_mild_band_bullish():
    # land in [1.3, 3.0) → Bullish (score_precise 2.5 from the weighted-mean case)
    sp = compute_crossasset_scores(_cats(growth=2, inflation=1), None, CONFIG)["SP500"]
    assert 1.3 <= abs(sp["score_precise"]) < 3.0
    assert sp["bias_label"] == "Bullish"
