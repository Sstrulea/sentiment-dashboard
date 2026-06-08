"""Tests for src.economic_compute — pure scoring, no network/parquet.

Synthetic fixtures only. Covers: bucket boundaries (z=1.0 / 0.33), direction
inversion (unemployment, jobless claims), the pct fallback, no-consensus,
category coverage with missing indicators, the FX pair differential
(base-quote)/2, and the bias-threshold mapping.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.economic_compute import (
    bias_label,
    bucket_score,
    build_payload,
    compute_currency_scorecard,
    compute_indicator_score,
)

AS_OF = pd.Timestamp("2024-06-10")
CALENDAR_COLUMNS = ["currency", "indicator_key", "release_dt", "actual", "consensus"]

DEFAULTS = {
    "surprise_window_k": 12,
    "z_buckets": [1.0, 0.33],
    "fallback_min_prints": 6,
    "pct_buckets": [0.10, 0.02],
    "max_age_days": 120,
}


def _make_rows(currency, indicator_key, actuals, consensuses, weeks_back_end=0):
    """Build a calendar slice: one row per (actual, consensus), weekly, ending
    `weeks_back_end` weeks before AS_OF (most recent last)."""
    n = len(actuals)
    rows = []
    for i in range(n):
        weeks_before = (n - 1 - i) + weeks_back_end
        rows.append({
            "currency": currency,
            "indicator_key": indicator_key,
            "release_dt": AS_OF - pd.Timedelta(weeks=weeks_before),
            "actual": actuals[i],
            "consensus": consensuses[i],
        })
    return pd.DataFrame(rows, columns=CALENDAR_COLUMNS)


# ---------------------------------------------------------------------------
# bucket_score boundaries
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("v,expected", [
    (1.0, 2),      # >= hi  (boundary inclusive)
    (1.5, 2),
    (0.99, 1),     # just below hi
    (0.33, 1),     # >= lo  (boundary inclusive)
    (0.32, 0),     # just below lo
    (0.0, 0),
    (-0.32, 0),
    (-0.33, -1),
    (-0.99, -1),
    (-1.0, -2),
    (-2.0, -2),
])
def test_bucket_boundaries_z(v, expected):
    assert bucket_score(v, [1.0, 0.33]) == expected


def test_bucket_pct_thresholds():
    assert bucket_score(0.10, [0.10, 0.02]) == 2
    assert bucket_score(0.05, [0.10, 0.02]) == 1
    assert bucket_score(0.02, [0.10, 0.02]) == 1
    assert bucket_score(0.0199, [0.10, 0.02]) == 0
    assert bucket_score(-0.10, [0.10, 0.02]) == -2


def test_bucket_nan_is_zero():
    assert bucket_score(float("nan"), [1.0, 0.33]) == 0
    assert bucket_score(None, [1.0, 0.33]) == 0


# ---------------------------------------------------------------------------
# compute_indicator_score — z path
# ---------------------------------------------------------------------------

def test_indicator_z_score_matches_hand_computation():
    # 12 prints, consensus 100 each; actuals chosen so diffs vary. The latest
    # surprise / sample-sigma must equal the reported z.
    actuals = [100, 101, 99, 102, 98, 100, 101, 99, 100, 102, 98, 103]
    consensus = [100] * 12
    sub = _make_rows("USD", "cpi_yoy", actuals, consensus)
    ind_cfg = {"direction": 1, "category": "inflation", "weight": 1.0}

    res = compute_indicator_score(sub, ind_cfg, DEFAULTS, AS_OF)
    diffs = np.array(actuals) - np.array(consensus)
    sigma = diffs.std(ddof=1)
    expected_z = (actuals[-1] - consensus[-1]) / sigma

    assert res is not None
    assert res["flag"] is None
    assert res["surprise"] == pytest.approx(3.0)
    assert res["z"] == pytest.approx(expected_z)
    assert res["score"] == bucket_score(expected_z, DEFAULTS["z_buckets"])


def test_indicator_strong_beat_scores_plus_two():
    # 11 flat prints (diff 0) + a big beat → small sigma, huge z → +2.
    actuals = [100] * 11 + [110]
    consensus = [100] * 12
    sub = _make_rows("USD", "gdp_qoq", actuals, consensus)
    ind_cfg = {"direction": 1, "category": "growth", "weight": 1.0}
    res = compute_indicator_score(sub, ind_cfg, DEFAULTS, AS_OF)
    assert res["score"] == 2
    assert res["flag"] is None


# ---------------------------------------------------------------------------
# Direction inversion
# ---------------------------------------------------------------------------

def test_unemployment_inversion_higher_actual_is_negative():
    # Unemployment direction -1: actual ABOVE consensus → bearish (negative).
    actuals = [4.0, 4.1, 3.9, 4.0, 4.1, 3.9, 4.0, 4.1, 3.9, 4.0, 4.0, 4.5]
    consensus = [4.0] * 12
    sub = _make_rows("USD", "unemployment_rate", actuals, consensus)
    ind_cfg = {"direction": -1, "category": "labour", "weight": 1.0}
    res = compute_indicator_score(sub, ind_cfg, DEFAULTS, AS_OF)
    assert res["surprise"] == pytest.approx(0.5)   # raw surprise stays positive
    assert res["z"] < 0                              # but z is inverted
    assert res["score"] < 0


def test_jobless_claims_inversion_fewer_claims_is_positive():
    # Claims direction -1: actual BELOW consensus (fewer claims) → bullish.
    actuals = [220, 225, 215, 220, 225, 215, 220, 225, 215, 220, 220, 180]
    consensus = [220] * 12
    sub = _make_rows("USD", "jobless_claims", actuals, consensus)
    ind_cfg = {"direction": -1, "category": "labour", "weight": 1.0}
    res = compute_indicator_score(sub, ind_cfg, DEFAULTS, AS_OF)
    assert res["surprise"] < 0
    assert res["score"] > 0


# ---------------------------------------------------------------------------
# Fallback + no-consensus
# ---------------------------------------------------------------------------

def test_fallback_when_too_few_prints():
    # Only 3 prints (< fallback_min_prints=6) → pct fallback.
    # surprise/|consensus| = 6/100 = 0.06 → between pct buckets 0.02 and 0.10 → +1.
    sub = _make_rows("EUR", "cpi_yoy", [100, 101, 106], [100, 100, 100])
    ind_cfg = {"direction": 1, "category": "inflation", "weight": 1.0}
    res = compute_indicator_score(sub, ind_cfg, DEFAULTS, AS_OF)
    assert res["flag"] == "fallback"
    assert res["z"] is None
    assert res["score"] == 1


def test_fallback_large_beat_scores_plus_two():
    sub = _make_rows("EUR", "gdp_qoq", [1.0, 1.0, 1.5], [1.0, 1.0, 1.0])
    ind_cfg = {"direction": 1, "category": "growth", "weight": 1.0}
    res = compute_indicator_score(sub, ind_cfg, DEFAULTS, AS_OF)
    # pct = 0.5/1.0 = 0.5 >= 0.10 → +2
    assert res["flag"] == "fallback"
    assert res["score"] == 2


def test_no_consensus_scores_zero():
    sub = _make_rows("USD", "cpi_yoy", [100, 101, 102], [np.nan, np.nan, np.nan])
    ind_cfg = {"direction": 1, "category": "inflation", "weight": 1.0}
    res = compute_indicator_score(sub, ind_cfg, DEFAULTS, AS_OF)
    assert res["flag"] == "no_consensus"
    assert res["score"] == 0


def test_absent_when_actual_too_old():
    # Latest actual is 200 days old > max_age_days(120) → indicator absent.
    sub = _make_rows("USD", "cpi_yoy", [100, 101, 102], [100, 100, 100],
                     weeks_back_end=30)  # ~210 days back
    ind_cfg = {"direction": 1, "category": "inflation", "weight": 1.0}
    res = compute_indicator_score(sub, ind_cfg, DEFAULTS, AS_OF)
    assert res is None


# ---------------------------------------------------------------------------
# Currency scorecard coverage
# ---------------------------------------------------------------------------

def _indicators_cfg():
    return {
        "defaults": DEFAULTS,
        "categories": {
            "growth": {"weight": 1.0, "label": "Growth"},
            "inflation": {"weight": 1.0, "label": "Inflation"},
            "labour": {"weight": 1.0, "label": "Labour Market"},
        },
        "indicators": {
            "cpi_yoy": {"pillar": "inflation", "category": "inflation", "direction": 1, "weight": 1.0},
            "gdp_qoq": {"pillar": "growth", "category": "growth", "direction": 1, "weight": 1.0},
            "unemployment_rate": {"pillar": "labour", "category": "labour", "direction": -1, "weight": 1.0},
            "core_pce": {"pillar": "inflation", "category": "inflation", "direction": 1, "weight": 1.0, "currencies": ["USD"]},
        },
    }


def _instruments_cfg():
    return {
        "scale": 5,
        "pair_divisor": 2,
        "categories_display": ["growth", "inflation", "labour"],
        "bias_thresholds": {"very": 7, "mild": 3},
        "currency_for_country": {"US": "USD", "EU": "EUR"},
        "instruments": {
            "US-DOLLAR": {"type": "single", "currency": "USD", "sign": 1, "display": "US Dollar"},
            "EURUSD": {"type": "fx", "base": "EUR", "quote": "USD", "display": "EUR/USD"},
        },
    }


def test_currency_scorecard_coverage_with_missing_indicators():
    # USD has only gdp_qoq present; inflation + labour absent.
    cal = _make_rows("USD", "gdp_qoq", [100] * 11 + [110], [100] * 12)
    card = compute_currency_scorecard(cal, "USD", _indicators_cfg(), _instruments_cfg(), AS_OF)

    assert card["categories"]["growth"]["coverage"] == 1
    assert card["categories"]["inflation"]["coverage"] == 0
    assert card["categories"]["labour"]["coverage"] == 0
    assert card["coverage"] == 1
    assert card["categories"]["growth"]["score_cell"] == 2
    # Index = mean over present categories only (just growth=+2) * scale(5) = 10.
    assert card["index"] == pytest.approx(10.0)
    assert "gdp_qoq" in card["breakdown"]
    assert "cpi_yoy" not in card["breakdown"]


def test_core_pce_excluded_for_non_usd():
    cal = _make_rows("EUR", "core_pce", [100] * 11 + [110], [100] * 12)
    card = compute_currency_scorecard(cal, "EUR", _indicators_cfg(), _instruments_cfg(), AS_OF)
    # core_pce is USD-only → EUR sees nothing.
    assert card["coverage"] == 0
    assert "core_pce" not in card["breakdown"]


def test_empty_calendar_yields_zero_index():
    empty = pd.DataFrame(columns=CALENDAR_COLUMNS)
    card = compute_currency_scorecard(empty, "USD", _indicators_cfg(), _instruments_cfg(), AS_OF)
    assert card["index"] == 0.0
    assert card["coverage"] == 0


# ---------------------------------------------------------------------------
# Instrument derivation: FX pair = (base - quote)/divisor
# ---------------------------------------------------------------------------

def test_fx_pair_is_base_minus_quote_over_divisor():
    # EUR strong growth beat (+2 growth), USD weak gdp miss (-2 growth).
    cal = pd.concat([
        _make_rows("EUR", "gdp_qoq", [1.0] * 11 + [2.0], [1.0] * 12),
        _make_rows("USD", "gdp_qoq", [1.0] * 11 + [0.0], [1.0] * 12),
    ], ignore_index=True)

    payload = build_payload(cal, _indicators_cfg(), _instruments_cfg(), as_of=AS_OF)
    by_sym = {i["symbol"]: i for i in payload["instruments"]}

    eur_idx = payload["currencies"]["EUR"]["index"]
    usd_idx = payload["currencies"]["USD"]["index"]
    assert eur_idx == pytest.approx(10.0)
    assert usd_idx == pytest.approx(-10.0)

    eurusd = by_sym["EURUSD"]
    assert eurusd["score"] == pytest.approx((eur_idx - usd_idx) / 2)  # (10 - -10)/2 = 10
    assert eurusd["bias"] == "Very Bullish"

    # Single currency = index * sign.
    dxy = by_sym["US-DOLLAR"]
    assert dxy["score"] == pytest.approx(usd_idx)
    assert dxy["bias"] == "Very Bearish"


def test_us_only_indicator_does_not_populate_non_usd_pair():
    # jobless_claims is USD-only; on a EUR/USD pair it appears only on the USD
    # (quote) side. Here only EUR has gdp data → category differential is driven
    # by EUR alone, and labour stays at 0 coverage for EUR.
    cal = _make_rows("EUR", "gdp_qoq", [1.0] * 11 + [2.0], [1.0] * 12)
    payload = build_payload(cal, _indicators_cfg(), _instruments_cfg(), as_of=AS_OF)
    by_sym = {i["symbol"]: i for i in payload["instruments"]}
    eurusd = by_sym["EURUSD"]
    # Labour has no coverage on either leg → cell 0.
    assert eurusd["categories"]["labour"]["score_cell"] == 0
    assert eurusd["categories"]["labour"]["coverage"] == 0
    # Growth populated from EUR leg only.
    assert eurusd["categories"]["growth"]["coverage"] == 1


# ---------------------------------------------------------------------------
# Bias thresholds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("score,expected", [
    (0.0, "Neutral"),
    (2.9, "Neutral"),
    (-2.9, "Neutral"),
    (3.0, "Bullish"),
    (6.9, "Bullish"),
    (-3.0, "Bearish"),
    (-6.9, "Bearish"),
    (7.0, "Very Bullish"),
    (10.0, "Very Bullish"),
    (-7.0, "Very Bearish"),
])
def test_bias_label_thresholds(score, expected):
    assert bias_label(score, {"very": 7, "mild": 3}) == expected
