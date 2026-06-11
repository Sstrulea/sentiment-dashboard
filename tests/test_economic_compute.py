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


# ---------------------------------------------------------------------------
# Monetary category (4th category, C2 integration) — backward compatible
# ---------------------------------------------------------------------------

def _rate(score, **kw):
    """A RateScore-like dict accepted by build_payload(rate_scores=...)."""
    base = {"rate_score": score, "delta_w": 0.10, "latest_yield": 4.0,
            "z": 1.1, "method": "z", "as_of": "2024-06-10", "stale": False}
    base.update(kw)
    return base


def test_no_rate_scores_is_backward_compatible():
    # Without rate_scores → identical 3-category behavior (no monetary anywhere).
    cal = _make_rows("USD", "gdp_qoq", [100] * 11 + [110], [100] * 12)
    p = build_payload(cal, _indicators_cfg(), _instruments_cfg(), as_of=AS_OF)
    assert "monetary" not in p["currencies"]["USD"]["categories"]
    assert "rate_expectations" not in p["currencies"]["USD"]["breakdown"]


def test_monetary_category_added_with_rate_scores():
    cal = _make_rows("USD", "gdp_qoq", [100] * 11 + [110], [100] * 12)  # growth +2
    rs = {"USD": _rate(2)}
    p = build_payload(cal, _indicators_cfg(), _instruments_cfg(), as_of=AS_OF, rate_scores=rs)
    usd = p["currencies"]["USD"]
    assert usd["categories"]["monetary"] == {"score_cell": 2, "score_precise": 2.0, "coverage": 1}
    assert usd["breakdown"]["rate_expectations"]["score"] == 2
    assert usd["breakdown"]["rate_expectations"]["method"] == "z"


def test_index_averages_over_four_categories():
    # USD growth=+2 only (other surprise cats absent). Without rate: index = +2*scale.
    cal = _make_rows("USD", "gdp_qoq", [100] * 11 + [110], [100] * 12)
    p3 = build_payload(cal, _indicators_cfg(), _instruments_cfg(), as_of=AS_OF)
    assert p3["currencies"]["USD"]["index"] == pytest.approx(10.0)  # +2 * scale(5)
    # With rate=-2: mean(growth +2, monetary -2) = 0 → index 0.
    p4 = build_payload(cal, _indicators_cfg(), _instruments_cfg(),
                       as_of=AS_OF, rate_scores={"USD": _rate(-2)})
    assert p4["currencies"]["USD"]["index"] == pytest.approx(0.0)


def test_monetary_only_currency_index_is_rate_times_scale():
    # No surprise data at all, only a rate score → index = rate * scale.
    empty = pd.DataFrame(columns=CALENDAR_COLUMNS)
    p = build_payload(empty, _indicators_cfg(), _instruments_cfg(),
                      as_of=AS_OF, rate_scores={"USD": _rate(1)})
    usd = p["currencies"]["USD"]
    assert usd["categories"]["monetary"]["coverage"] == 1
    assert usd["index"] == pytest.approx(5.0)  # +1 * scale(5)


def test_graceful_when_currency_lacks_rate_score():
    # USD has a rate score, EUR does not → EUR keeps 3-category behavior, no crash.
    cal = pd.concat([
        _make_rows("USD", "gdp_qoq", [100] * 11 + [110], [100] * 12),
        _make_rows("EUR", "gdp_qoq", [1.0] * 11 + [2.0], [1.0] * 12),
    ], ignore_index=True)
    p = build_payload(cal, _indicators_cfg(), _instruments_cfg(),
                      as_of=AS_OF, rate_scores={"USD": _rate(2)})
    assert "monetary" in p["currencies"]["USD"]["categories"]
    assert "monetary" not in p["currencies"]["EUR"]["categories"]
    # EUR index unchanged = growth +2 * scale
    assert p["currencies"]["EUR"]["index"] == pytest.approx(10.0)


def test_fx_pair_monetary_differential_in_breakdown():
    # EUR rate +2, USD rate -1 → EURUSD monetary differential exists on both legs.
    rs = {"EUR": _rate(2), "USD": _rate(-1)}
    p = build_payload(pd.DataFrame(columns=CALENDAR_COLUMNS),
                      _indicators_cfg(), _instruments_cfg(), as_of=AS_OF, rate_scores=rs)
    by = {i["symbol"]: i for i in p["instruments"]}
    eurusd = by["EURUSD"]
    assert eurusd["breakdown"]["base"]["indicators"]["rate_expectations"]["score"] == 2
    assert eurusd["breakdown"]["quote"]["indicators"]["rate_expectations"]["score"] == -1


# ---------------------------------------------------------------------------
# Fix 1 — per-frequency recency window  |  Fix 2 — flash/final dedup
# ---------------------------------------------------------------------------
from src.economic_compute import effective_frequency, _max_age_for, _dedup_flash_final

DEFAULTS_FREQ = {
    **DEFAULTS,
    "default_frequency": "monthly",
    "max_age_by_frequency": {"weekly": 14, "monthly": 45, "quarterly": 110},
    "dedup_gap_days": {"weekly": 3, "monthly": 18, "quarterly": 45},
}
DEFAULTS_NODEDUP = {**DEFAULTS_FREQ, "dedup_gap_days": {}}


def _rows(ccy, key, dates, actuals, cons):
    return pd.DataFrame({
        "currency": ccy, "indicator_key": key,
        "release_dt": pd.to_datetime(pd.Series(dates)),
        "actual": actuals, "consensus": cons,
    }, columns=CALENDAR_COLUMNS)


def test_max_age_for_per_frequency():
    assert _max_age_for({"frequency": "quarterly"}, DEFAULTS_FREQ, "quarterly") == 110
    assert _max_age_for({"frequency": "monthly"}, DEFAULTS_FREQ, "monthly") == 45
    assert _max_age_for({"frequency": "weekly"}, DEFAULTS_FREQ, "weekly") == 14
    assert _max_age_for({"max_age_days": 99}, DEFAULTS_FREQ, "monthly") == 99  # explicit wins
    assert _max_age_for({}, DEFAULTS_FREQ, None) == 120                        # ultimate fallback


def test_effective_frequency_currency_override():
    cfg = {"frequency": "monthly", "frequency_overrides": {"AUD": "quarterly", "NZD": "quarterly"}}
    assert effective_frequency(cfg, DEFAULTS_FREQ, "AUD") == "quarterly"
    assert effective_frequency(cfg, DEFAULTS_FREQ, "USD") == "monthly"
    assert effective_frequency({}, DEFAULTS_FREQ, "USD") == "monthly"  # default_frequency


def test_quarterly_print_kept_within_window():
    # latest GDP print 100 days old → within quarterly window (110), not stale.
    dates = [AS_OF - pd.Timedelta(days=d) for d in (370, 280, 190, 100)]
    df = _rows("CAD", "gdp_qoq", dates, [1.0, 1.1, 1.2, 1.5], [1.0, 1.0, 1.0, 1.0])
    cfg = {"direction": 1, "category": "growth", "weight": 1.0, "frequency": "quarterly"}
    r = compute_indicator_score(df, cfg, DEFAULTS_FREQ, AS_OF, allow_stale=True, currency="CAD")
    assert r is not None and r["stale"] is False


def test_monthly_window_drops_old_print_quarterly_keeps_it():
    # Same 100-day-old latest print: monthly window (45) drops it, quarterly (110) keeps it.
    dates = [AS_OF - pd.Timedelta(days=d) for d in (190, 145, 100)]
    df = _rows("USD", "cpi_yoy", dates, [2.0, 2.1, 2.5], [2.0, 2.0, 2.0])
    monthly = {"direction": 1, "category": "inflation", "weight": 1.0, "frequency": "monthly"}
    assert compute_indicator_score(df, monthly, DEFAULTS_FREQ, AS_OF, currency="USD") is None
    r = compute_indicator_score(df, monthly, DEFAULTS_FREQ, AS_OF, allow_stale=True, currency="USD")
    assert r["stale"] is True
    quarterly = {**monthly, "frequency": "quarterly"}
    assert compute_indicator_score(df, quarterly, DEFAULTS_FREQ, AS_OF, currency="USD") is not None


def test_per_currency_quarterly_override_protects_nz_cpi():
    # NZ CPI is quarterly; a 100-day-old print survives for NZD but not USD.
    dates = [AS_OF - pd.Timedelta(days=d) for d in (280, 190, 100)]
    df = _rows("NZD", "cpi_yoy", dates, [2.0, 2.1, 2.5], [2.0, 2.0, 2.0])
    cfg = {"direction": 1, "category": "inflation", "weight": 1.0,
           "frequency": "monthly", "frequency_overrides": {"NZD": "quarterly"}}
    assert compute_indicator_score(df, cfg, DEFAULTS_FREQ, AS_OF, currency="NZD") is not None
    assert compute_indicator_score(df, cfg, DEFAULTS_FREQ, AS_OF, currency="USD") is None


# --- Fix 2: flash/final dedup ---

def _flash_final_df():
    # gaps: flash→final = 8d, final→next flash = 22d (S&P PMI-like). finals are the
    # later print of each cluster. surprises: flashes ±5 (noisy), finals +1 (calm).
    offs = [71, 63, 41, 33, 11, 3]                 # days before AS_OF (descending age)
    dates = [AS_OF - pd.Timedelta(days=d) for d in offs]
    actuals = [55, 51, 45, 51, 55, 51]             # flash 55/45/55, final 51 each
    cons = [50, 50, 50, 50, 50, 50]
    return _rows("GBP", "services_pmi", dates, actuals, cons)


def test_dedup_keeps_one_final_per_period():
    df = _flash_final_df().sort_values("release_dt").reset_index(drop=True)
    dd = _dedup_flash_final(df, 18)
    assert len(dd) == 3                            # 6 prints → 3 periods
    assert list(dd["actual"]) == [51, 51, 51]      # finals kept (flashes 55/45/55 dropped)


def test_weekly_series_not_collapsed():
    dates = [AS_OF - pd.Timedelta(days=7 * i) for i in range(6)][::-1]
    df = _rows("USD", "jobless_claims", dates, [220, 221, 219, 222, 218, 215],
               [220] * 6).sort_values("release_dt").reset_index(drop=True)
    assert len(_dedup_flash_final(df, 3)) == 6      # 7d apart > 3d gap → nothing collapses


def test_dedup_changes_sigma_path():
    df = _flash_final_df()
    cfg = {"direction": 1, "category": "growth", "weight": 1.0, "frequency": "monthly"}
    # With dedup: 3 finals (< fallback_min_prints=6) and zero-variance → fallback path.
    deduped = compute_indicator_score(df, cfg, DEFAULTS_FREQ, AS_OF, currency="GBP")
    # Without dedup: 6 noisy prints → stable sigma → z path. Different method + score.
    raw = compute_indicator_score(df, cfg, DEFAULTS_NODEDUP, AS_OF, currency="GBP")
    assert deduped["flag"] == "fallback"
    assert raw["flag"] is None
    assert deduped["score"] != raw["score"]
