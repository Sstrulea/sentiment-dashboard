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
        "balance_sheet": {"sign": -1, "weight": 1.0},
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


# ---------------------------------------------------------------------------
# FIX 1b — stale sub-components excluded from the rates mean, kept for display
# ---------------------------------------------------------------------------

class _StaleRY:
    score = 2
    series = "REAL_10Y_TSY"
    stale = True


def test_stale_realyield_excluded_from_rates_but_visible():
    # Stale real yield + fresh 2y(+2): real_yield_10y must NOT enter the mean
    # (rates = rate_exp_2y alone = -2), but stays visible (raw +2, stale flag).
    res = compute_crossasset_scores(_cats(monetary=2), realyield_score=_StaleRY(), config=CONFIG)
    sp = res["SP500"]
    ry = _sub(sp, "real_yield_10y")
    assert ry["present"] is False and ry["stale"] is True
    assert ry["raw"] == 2                       # still shown
    rt = _factor(sp, "rates")
    assert rt["value"] == pytest.approx(-2.0)   # rate_exp_2y only (not blended with stale RY)


def test_stale_monetary_cell_excluded_but_visible():
    # A stale monetary cell (build_payload emits coverage 0 + stale) → rate_exp_2y
    # excluded from the mean but raw kept; rates = real_yield alone.
    cats = {"USD": {"monetary": {"score_cell": 2, "coverage": 0, "stale": True}}}
    res = compute_crossasset_scores(cats, realyield_score=2, config=CONFIG)
    sp = res["SP500"]
    re2 = _sub(sp, "rate_exp_2y")
    assert re2["present"] is False and re2["stale"] is True and re2["raw"] == 2
    assert _factor(sp, "rates")["value"] == pytest.approx(-2.0)   # real_yield only


def test_no_data_monetary_cell_is_absent_not_stale():
    # coverage 0 WITHOUT stale = genuine no-data → absent (not a stale display row).
    cats = {"USD": {"monetary": {"score_cell": 0, "coverage": 0}}}
    res = compute_crossasset_scores(cats, realyield_score=None, config=CONFIG)
    re2 = _sub(res["SP500"], "rate_exp_2y")
    assert re2["present"] is False and re2["raw"] is None and re2["stale"] is False
    assert _factor(res["SP500"], "rates")["present"] is False   # nothing in rates


# ---------------------------------------------------------------------------
# Step 7 — balance_sheet (Fed net liquidity) sub-component
# ---------------------------------------------------------------------------

class _Liq:
    """LiquidityScore-like. score: raw (NL falling = +tightening); stale flag."""
    def __init__(self, score, stale=False):
        self.score = score
        self.stale = stale


def test_balance_sheet_present_in_rates():
    res = compute_crossasset_scores(_cats(), realyield_score=None,
                                    config=CONFIG, liquidity_score=_Liq(2))
    bs = _sub(res["SP500"], "balance_sheet")
    assert bs["present"] is True and bs["raw"] == 2 and bs["sign"] == -1
    assert bs["contribution"] == pytest.approx(-2.0)   # sign -1 × 1 × 2


def test_nl_rising_is_bullish_for_indices_and_gold():
    # NL rising = easing → liquidity raw NEGATIVE (-2). sign -1 ⇒ +2 contribution
    # ⇒ MORE bullish for both SP500 and GOLD.
    rising = compute_crossasset_scores(_cats(), None, CONFIG, liquidity_score=_Liq(-2))
    falling = compute_crossasset_scores(_cats(), None, CONFIG, liquidity_score=_Liq(2))
    for sym in ("SP500", "GOLD"):
        assert rising[sym]["score_precise"] > 0      # easing → bullish
        assert falling[sym]["score_precise"] < 0     # tightening → bearish
        assert rising[sym]["score_precise"] > falling[sym]["score_precise"]


def test_three_aligned_rate_subs_bounded_at_2_not_3():
    # 2y +2, real +2, NL falling +2 → all signed -2 → rates mean = -2 (NOT -3).
    res = compute_crossasset_scores(_cats(monetary=2), realyield_score=2,
                                    config=CONFIG, liquidity_score=_Liq(2))
    rt = _factor(res["SP500"], "rates")
    assert rt["value"] == pytest.approx(-2.0)
    assert {c["name"] for c in rt["components"] if c["present"]} == {
        "rate_exp_2y", "real_yield_10y", "balance_sheet"}


def test_rate_subs_divergent_cancel():
    # 2y +2 (signed -2), real -2 (signed +2), NL flat 0 (signed 0) → mean 0.
    res = compute_crossasset_scores(_cats(monetary=2), realyield_score=-2,
                                    config=CONFIG, liquidity_score=_Liq(0))
    assert _factor(res["SP500"], "rates")["value"] == pytest.approx(0.0)


def test_stale_balance_sheet_excluded_but_visible():
    # Stale NL + fresh 2y(+2): balance_sheet excluded from mean (rates = -2 from
    # 2y alone) but still shown with raw + stale flag.
    res = compute_crossasset_scores(_cats(monetary=2), realyield_score=None,
                                    config=CONFIG, liquidity_score=_Liq(2, stale=True))
    bs = _sub(res["SP500"], "balance_sheet")
    assert bs["present"] is False and bs["stale"] is True and bs["raw"] == 2
    assert _factor(res["SP500"], "rates")["value"] == pytest.approx(-2.0)  # 2y only


def test_missing_balance_sheet_backward_compatible():
    # No liquidity_score (default None) → balance_sheet absent; rates = mean of
    # the two present subs, unchanged from pre-Step-7 behavior.
    res = compute_crossasset_scores(_cats(monetary=2), realyield_score=2, config=CONFIG)
    bs = _sub(res["SP500"], "balance_sheet")
    assert bs["present"] is False and bs["raw"] is None
    assert _factor(res["SP500"], "rates")["value"] == pytest.approx(-2.0)  # mean(-2,-2)


# ---------------------------------------------------------------------------
# Accepted degradation 2026-08-16 — balance_sheet weight 0 (docs/accepted-
# degradations.md). Weight 0 keeps the cell visible/inspectable (raw, sign,
# contribution=0, `excluded` flag) but must be a mathematical no-op on every
# composite — identical to the sub-component being absent from config.
# ---------------------------------------------------------------------------

def _rates_block_weight0():
    return {"weight": 1.0, "components": {
        "rate_exp_2y": {"sign": -1, "weight": 1.0},
        "real_yield_10y": {"sign": -1, "weight": 1.0},
        "balance_sheet": {"sign": -1, "weight": 0.0},
    }}


def _rates_block_no_balance_sheet():
    return {"weight": 1.0, "components": {
        "rate_exp_2y": {"sign": -1, "weight": 1.0},
        "real_yield_10y": {"sign": -1, "weight": 1.0},
    }}


def _cfg_with_rates(rates_block):
    return {
        "scale": 5,
        "bias_thresholds": {"mild": 2.0, "very": 4.4},
        "instruments": {
            sym: {"type": t, "home_ccy": "USD", "factors": {
                "growth": {"sign": g, "weight": w},
                "inflation": {"sign": -1, "weight": 1.0},
                "labour": {"sign": -1, "weight": 1.0},
                "rates": rates_block(),
            }}
            for sym, t, g, w in [("SP500", "index", 1, 1.0), ("GOLD", "metal", -1, 0.5)]
        },
    }


def test_weight_zero_sets_excluded_flag_and_zero_contribution():
    cfg = _cfg_with_rates(_rates_block_weight0)
    res = compute_crossasset_scores(_cats(monetary=2), realyield_score=2,
                                    config=cfg, liquidity_score=_Liq(2))
    bs = _sub(res["SP500"], "balance_sheet")
    assert bs["excluded"] is True
    assert bs["raw"] == 2                        # value still live/inspectable
    assert bs["contribution"] == pytest.approx(0.0)   # sign(-1) * weight(0) * raw(2)
    assert bs["present"] is True                  # not stale, not missing — just excluded

    present_cfg = _cfg_with_rates(_rates_block)
    present_res = compute_crossasset_scores(_cats(monetary=2), realyield_score=2,
                                             config=present_cfg, liquidity_score=_Liq(2))
    assert _sub(present_res["SP500"], "balance_sheet")["excluded"] is False


def test_weight_zero_matches_pillar_absent_byte_identical():
    """Weight 0 (component present, excluded from the mean) must be
    mathematically identical to the component being entirely absent from
    config — every instrument's composite, byte-identical.

    NOTE on a bound that was considered and rejected: "no instrument's
    composite changes by more than the pillar's prior (weight=1.0)
    contribution" is NOT a property this code guarantees. This is a
    two-level weighted mean (sub-components -> rates factor -> outer
    instrument composite); dropping a term renormalizes the denominator at
    BOTH levels, which can move the result by more than that term's own
    contribution. Counterexample: cats={growth:1, inflation:-1, labour:0,
    monetary:2}, realyield_score=2, liquidity_score=-1 -> SP500 composite
    moves by 1.25 against a prior contribution of 1.0. It happened to hold
    for the real 2026-08-16 production data (all 8 cross-asset instruments,
    checked before shipping this change) but that is a fact about today's
    specific scored values, not an invariant to test against arbitrary
    inputs — so it is not asserted here."""
    weight0_cfg = _cfg_with_rates(_rates_block_weight0)
    absent_cfg = _cfg_with_rates(_rates_block_no_balance_sheet)
    cats = _cats(monetary=2)
    weight0 = compute_crossasset_scores(cats, realyield_score=2, config=weight0_cfg,
                                        liquidity_score=_Liq(2))
    absent = compute_crossasset_scores(cats, realyield_score=2, config=absent_cfg,
                                       liquidity_score=_Liq(2))
    for sym in ("SP500", "GOLD"):
        assert weight0[sym]["score_precise"] == absent[sym]["score_precise"]
        assert weight0[sym]["score"] == absent[sym]["score"]
        assert _factor(weight0[sym], "rates")["value"] == _factor(absent[sym], "rates")["value"]




# ---------------------------------------------------------------------------
# SENTIMENT factor (Step 5) — weight-0.5 member of the weighted mean
# ---------------------------------------------------------------------------

def _cfg_with_sentiment():
    """SP500 (US index) + DAX (foreign, NO sentiment factor) + GOLD (metal)."""
    return {
        "scale": 5,
        "bias_thresholds": {"mild": 1.9, "very": 4.3},
        "instruments": {
            "SP500": {"type": "index", "home_ccy": "USD", "factors": {
                "growth": {"sign": 1, "weight": 1.0},
                "inflation": {"sign": -1, "weight": 1.0},
                "labour": {"sign": -1, "weight": 1.0},
                "rates": _rates_block(),
                "sentiment": {"sign": 1, "weight": 0.5},
            }},
            "DAX": {"type": "index", "home_ccy": "EUR", "factors": {
                "growth": {"sign": 1, "weight": 1.0},
                "inflation": {"sign": -1, "weight": 1.0},
                "labour": {"sign": -1, "weight": 1.0},
                "rates": _rates_block(),
            }},
        },
    }


def test_sentiment_enters_mean_with_weight_half():
    cfg = _cfg_with_sentiment()
    cats = {"USD": {"growth": 2, "inflation": 0, "labour": 0, "monetary": 0}}
    # No sentiment: num = 1*2 + 1*0 + 1*0 + rates(0) = 2 over wsum 4 → 0.5 ×5 = 2.5
    base = compute_crossasset_scores(cats, realyield_score=0, config=cfg,
                                     sentiment_by_symbol=None)["SP500"]
    # With sentiment +3 (×0.5): num = 2 + 1.5 = 3.5 over wsum 4.5 → 0.7778 ×5 = 3.889
    withs = compute_crossasset_scores(cats, realyield_score=0, config=cfg,
                                      sentiment_by_symbol={"SP500": 3})["SP500"]
    sf = _factor(withs, "sentiment")
    assert sf["present"] is True
    assert sf["value"] == pytest.approx(3.0)      # sign 1 × cell 3
    assert sf["weight"] == 0.5
    assert sf["contribution"] == pytest.approx(1.5)
    assert base["score_precise"] == pytest.approx(2.5)
    assert withs["score_precise"] == pytest.approx(3.5 / 4.5 * 5)


def test_sentiment_absent_factor_is_bit_identical():
    """An instrument without a sentiment factor is unaffected even if a value is
    supplied for it in the map (DAX has no sentiment factor)."""
    cfg = _cfg_with_sentiment()
    cats = {
        "USD": {"growth": 2, "inflation": 0, "labour": 0, "monetary": 0},
        "EUR": {"growth": 1, "inflation": -1, "labour": 0, "monetary": 0},
    }
    none_map = compute_crossasset_scores(cats, realyield_score=0, config=cfg,
                                         sentiment_by_symbol=None)["DAX"]
    full_map = compute_crossasset_scores(cats, realyield_score=0, config=cfg,
                                         sentiment_by_symbol={"DAX": 4, "SP500": 3})["DAX"]
    assert none_map["score_precise"] == full_map["score_precise"]
    assert all(f["name"] != "sentiment" for f in full_map["factors"])


def test_sentiment_factor_absent_when_no_value():
    """Configured sentiment factor but no value in the map → factor excluded."""
    cfg = _cfg_with_sentiment()
    cats = {"USD": {"growth": 2, "inflation": 0, "labour": 0, "monetary": 0}}
    res = compute_crossasset_scores(cats, realyield_score=0, config=cfg,
                                    sentiment_by_symbol={})["SP500"]
    sf = _factor(res, "sentiment")
    assert sf["present"] is False
    assert sf["contribution"] is None
    # score equals the no-sentiment baseline
    assert res["score_precise"] == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# TREND factor (Step 7) — weight-0.5 member, structural twin of SENTIMENT,
# DIRECT on the instrument's own price series.
# ---------------------------------------------------------------------------

def _cfg_with_trend():
    cfg = _cfg_with_sentiment()
    cfg["instruments"]["SP500"]["factors"]["trend"] = {"sign": 1, "weight": 0.5}
    return cfg


def test_trend_enters_mean_with_weight_half():
    cfg = _cfg_with_trend()
    cats = {"USD": {"growth": 2, "inflation": 0, "labour": 0, "monetary": 0}}
    # No trend: num = 2 over wsum 4 → 0.5 ×5 = 2.5
    base = compute_crossasset_scores(cats, realyield_score=0, config=cfg)["SP500"]
    # With trend +3 (×0.5): num = 2 + 1.5 = 3.5 over wsum 4.5 → 3.889
    witht = compute_crossasset_scores(cats, realyield_score=0, config=cfg,
                                      trend_by_symbol={"SP500": 3})["SP500"]
    tf = _factor(witht, "trend")
    assert tf["present"] is True
    assert tf["value"] == pytest.approx(3.0)       # sign 1 × cell 3
    assert tf["weight"] == 0.5
    assert tf["contribution"] == pytest.approx(1.5)
    assert base["score_precise"] == pytest.approx(2.5)
    assert witht["score_precise"] == pytest.approx(3.5 / 4.5 * 5)
    assert witht["trend"] == 3                       # display cell carried


def test_trend_factor_absent_when_no_value():
    cfg = _cfg_with_trend()
    cats = {"USD": {"growth": 2, "inflation": 0, "labour": 0, "monetary": 0}}
    res = compute_crossasset_scores(cats, realyield_score=0, config=cfg,
                                    trend_by_symbol={})["SP500"]
    tf = _factor(res, "trend")
    assert tf["present"] is False
    assert tf["contribution"] is None
    assert res["score_precise"] == pytest.approx(2.5)  # no-trend baseline
    assert res["trend"] is None


def test_trend_not_configured_is_bit_identical():
    """DAX has no trend factor → score identical even if a value is supplied
    (mirror of the foreign-index no-sentiment case)."""
    cfg = _cfg_with_trend()
    cats = {
        "USD": {"growth": 2, "inflation": 0, "labour": 0, "monetary": 0},
        "EUR": {"growth": 1, "inflation": -1, "labour": 0, "monetary": 0},
    }
    none_map = compute_crossasset_scores(cats, realyield_score=0, config=cfg)["DAX"]
    full_map = compute_crossasset_scores(cats, realyield_score=0, config=cfg,
                                         trend_by_symbol={"DAX": 4, "SP500": 3})["DAX"]
    assert none_map["score_precise"] == full_map["score_precise"]
    assert all(f["name"] != "trend" for f in full_map["factors"])


def test_trend_and_sentiment_independent_members():
    """Both factors present → both enter the mean with weight 0.5 each."""
    cfg = _cfg_with_trend()
    cats = {"USD": {"growth": 2, "inflation": 0, "labour": 0, "monetary": 0}}
    res = compute_crossasset_scores(cats, realyield_score=0, config=cfg,
                                    sentiment_by_symbol={"SP500": 2},
                                    trend_by_symbol={"SP500": 3})["SP500"]
    # num = 2 (growth) + 0 + 0 + 0 (rates) + 0.5·2 (sent) + 0.5·3 (trend) = 4.5
    # wsum = 4 + 0.5 + 0.5 = 5 → 4.5/5 ×5 = 4.5
    assert res["score_precise"] == pytest.approx(4.5)
    assert _factor(res, "sentiment")["present"] is True
    assert _factor(res, "trend")["present"] is True
