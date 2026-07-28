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
    "z_buckets": [1.54, 0.81],   # recalibrated (C2, clean data): ±2 at |z|>=1.54, ±1 at |z|>=0.81
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
    (1.54, 2),     # >= hi  (boundary inclusive) — recalibrated (clean-data) production threshold
    (2.0, 2),
    (1.53, 1),     # just below hi
    (0.81, 1),     # >= lo  (boundary inclusive)
    (0.80, 0),     # just below lo
    (0.0, 0),
    (-0.80, 0),
    (-0.81, -1),
    (-1.53, -1),
    (-1.54, -2),
    (-2.0, -2),
])
def test_bucket_boundaries_z(v, expected):
    assert bucket_score(v, [1.54, 0.81]) == expected


def test_bucket_pct_thresholds():
    assert bucket_score(0.10, [0.10, 0.02]) == 2
    assert bucket_score(0.05, [0.10, 0.02]) == 1
    assert bucket_score(0.02, [0.10, 0.02]) == 1
    assert bucket_score(0.0199, [0.10, 0.02]) == 0
    assert bucket_score(-0.10, [0.10, 0.02]) == -2


def test_bucket_nan_is_zero():
    assert bucket_score(float("nan"), [1.54, 0.81]) == 0
    assert bucket_score(None, [1.54, 0.81]) == 0


def test_production_z_buckets_are_recalibrated():
    """Guard the adopted C2 recalibration in the production config (window unchanged)."""
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "data" / "economic_indicators.yaml").read_text())
    d = cfg["defaults"]
    assert d["z_buckets"] == [1.54, 0.81]      # C2 (clean data): ±2 at p87.5=1.54, ±1 at p60=0.81
    assert d["surprise_window_k"] == 12         # window NOT changed (C1 not confirmed)
    assert d["pct_buckets"] == [0.10, 0.02]     # fallback thresholds unchanged


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
    # jobless_claims is USD-only; EUR never gets a labour indicator scored, so
    # EUR's own labour category stays at 0 coverage. Growth is populated from
    # the gdp_qoq print given here.
    cal = _make_rows("EUR", "gdp_qoq", [1.0] * 11 + [2.0], [1.0] * 12)
    payload = build_payload(cal, _indicators_cfg(), _instruments_cfg(), as_of=AS_OF)
    eur = payload["currencies"]["EUR"]
    assert eur["categories"]["labour"]["score_cell"] == 0
    assert eur["categories"]["labour"]["coverage"] == 0
    assert eur["categories"]["growth"]["coverage"] == 1


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
    assert usd["categories"]["monetary"] == {
        "score_cell": 2, "score_precise": 2.0, "coverage": 1, "weight": 1.0, "stale": False}
    assert usd["breakdown"]["rate_expectations"]["score"] == 2
    assert usd["breakdown"]["rate_expectations"]["method"] == "z"


def test_stale_monetary_excluded_from_index_but_displayed():
    # FIX 1a: a stale 2y rate is kept for display (cell value + stale, coverage 0)
    # but excluded from the currency index — mirroring stale calendar indicators.
    cal = _make_rows("USD", "gdp_qoq", [100] * 11 + [110], [100] * 12)  # growth +2 only
    fresh = build_payload(cal, _indicators_cfg(), _instruments_cfg(),
                          as_of=AS_OF, rate_scores={"USD": _rate(-2, stale=False)})
    stale = build_payload(cal, _indicators_cfg(), _instruments_cfg(),
                          as_of=AS_OF, rate_scores={"USD": _rate(-2, stale=True)})
    su = stale["currencies"]["USD"]
    # displayed: cell present with value + stale flag, coverage 0
    assert su["categories"]["monetary"] == {
        "score_cell": -2, "score_precise": -2.0, "coverage": 0, "weight": 1.0, "stale": True}
    assert su["breakdown"]["rate_expectations"]["score"] == -2   # still in breakdown
    # excluded from index: stale index == growth-only index (no monetary pull)
    growth_only = build_payload(cal, _indicators_cfg(), _instruments_cfg(), as_of=AS_OF)
    assert su["index"] == pytest.approx(growth_only["currencies"]["USD"]["index"])
    assert su["index"] != pytest.approx(fresh["currencies"]["USD"]["index"])


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


# ---------------------------------------------------------------------------
# Period-based flash/final dedup (replaces fragile proximity for bi-weekly cadence)
# ---------------------------------------------------------------------------

def _rows_p(ccy, key, dates, actuals, cons, periods):
    df = pd.DataFrame({
        "currency": ccy, "indicator_key": key,
        "release_dt": pd.to_datetime(pd.Series(dates)),
        "actual": actuals, "consensus": cons, "period": periods,
    })
    return df


def test_period_dedup_keeps_published_final_not_future_blank():
    # EU-HICP-like: flash + final per period (~14d apart), interleaved across months,
    # plus a future scheduled blank for the newest period. Dedup must keep the
    # PUBLISHED final per period and never the future blank.
    dates = ["2026-03-31", "2026-04-16", "2026-04-30", "2026-05-20",
             "2026-06-02", "2026-06-17"]
    actuals = [1.7, 2.6, 1.9, 3.0, 2.6, None]          # last is future/blank
    cons = [1.8, 1.7, 2.3, 1.9, 2.6, 2.6]
    periods = ["2026.03.01", "2026.03.01", "2026.04.01", "2026.04.01",
               "2026.05.01", "2026.05.01"]
    df = _rows_p("EUR", "cpi_yoy", dates, actuals, cons, periods)
    dd = _dedup_flash_final(df.sort_values("release_dt").reset_index(drop=True), 18)
    # one row per period, each the latest PUBLISHED print
    assert list(dd["period"]) == ["2026.03.01", "2026.04.01", "2026.05.01"]
    assert list(dd["actual"]) == [2.6, 3.0, 2.6]
    # latest published = May 2.6 @ 2026-06-02 (NOT the 2026-06-17 blank)
    pub = dd[dd["actual"].notna()]
    assert pub["release_dt"].max() == pd.Timestamp("2026-06-02")


def test_period_dedup_scores_latest_published_period():
    # End-to-end via compute_indicator_score: the score uses May's 2.6, not the
    # old April flash (1.9) the proximity heuristic used to latch onto.
    dates = ["2026-03-31", "2026-04-16", "2026-04-30", "2026-05-20",
             "2026-06-02", "2026-06-17"]
    actuals = [1.7, 2.6, 1.9, 3.0, 2.6, None]
    cons = [1.8, 1.7, 2.3, 1.9, 2.6, 2.6]
    periods = ["2026.03.01", "2026.03.01", "2026.04.01", "2026.04.01",
               "2026.05.01", "2026.05.01"]
    df = _rows_p("EUR", "cpi_yoy", dates, actuals, cons, periods)
    cfg = {"direction": 1, "category": "inflation", "weight": 1.0, "frequency": "monthly"}
    r = compute_indicator_score(df, cfg, DEFAULTS_FREQ, pd.Timestamp("2026-06-11"),
                                allow_stale=True, currency="EUR")
    assert r["actual"] == 2.6
    assert r["release_dt"] == pd.Timestamp("2026-06-02")
    assert r["stale"] is False


def test_period_dedup_all_blank_group_not_chosen_as_current():
    # A period with only a future/blank print must not become the "current" value.
    dates = ["2026-05-02", "2026-06-02"]
    df = _rows_p("EUR", "cpi_yoy", dates, [2.4, None], [2.3, 2.4],
                 ["2026.04.01", "2026.05.01"])
    cfg = {"direction": 1, "category": "inflation", "weight": 1.0, "frequency": "monthly"}
    r = compute_indicator_score(df, cfg, DEFAULTS_FREQ, pd.Timestamp("2026-06-05"),
                                allow_stale=True, currency="EUR")
    assert r["actual"] == 2.4 and r["release_dt"] == pd.Timestamp("2026-05-02")


def test_dedup_proximity_fallback_when_period_missing():
    # No period column → falls back to proximity, still guarded (no blank/future pick).
    dates = ["2026-04-01", "2026-04-09", "2026-05-01", "2026-05-09"]
    df = pd.DataFrame({
        "currency": "GBP", "indicator_key": "manufacturing_pmi",
        "release_dt": pd.to_datetime(pd.Series(dates)),
        "actual": [50.0, 50.5, 51.0, None], "consensus": [49, 49, 50, 50],
    })
    dd = _dedup_flash_final(df.sort_values("release_dt").reset_index(drop=True), 18)
    # April pair (8d) collapses to final 50.5; May cluster keeps published 51.0 (not blank)
    assert list(dd["actual"]) == [50.5, 51.0]


# ---------------------------------------------------------------------------
# SENTIMENT factor in the FX score (Step 6) — weight-0.5 currency-level factor
# ---------------------------------------------------------------------------

def _instruments_cfg_sent():
    cfg = _instruments_cfg()
    cfg["sentiment_weight"] = 0.5
    return cfg


def _eur_usd_growth_cal():
    # EUR +2 growth beat, USD -2 growth miss → eur_idx +10, usd_idx -10 (cat-only).
    return pd.concat([
        _make_rows("EUR", "gdp_qoq", [1.0] * 11 + [2.0], [1.0] * 12),
        _make_rows("USD", "gdp_qoq", [1.0] * 11 + [0.0], [1.0] * 12),
    ], ignore_index=True)


def test_fx_sentiment_off_is_bit_identical():
    cal = _eur_usd_growth_cal()
    off = build_payload(cal, _indicators_cfg(), _instruments_cfg_sent(), as_of=AS_OF)
    base = build_payload(cal, _indicators_cfg(), _instruments_cfg_sent(),
                         as_of=AS_OF, sentiment_cells=None)
    a = {i["symbol"]: i["score"] for i in off["instruments"]}
    b = {i["symbol"]: i["score"] for i in base["instruments"]}
    assert a == b  # no sentiment_cells → identical to macro-only baseline


def test_fx_usd_pair_leg_is_zero_not_dxy():
    """EUR/USD must be INVARIANT to the DXY cell (USD pair leg = 0), while the
    single US-DOLLAR row IS driven by DXY."""
    cal = _eur_usd_growth_cal()
    p_hi = build_payload(cal, _indicators_cfg(), _instruments_cfg_sent(), as_of=AS_OF,
                         sentiment_cells={"EUR": 4, "DXY": 4})
    p_lo = build_payload(cal, _indicators_cfg(), _instruments_cfg_sent(), as_of=AS_OF,
                         sentiment_cells={"EUR": 4, "DXY": -4})
    eurusd_hi = next(i for i in p_hi["instruments"] if i["symbol"] == "EURUSD")["score"]
    eurusd_lo = next(i for i in p_lo["instruments"] if i["symbol"] == "EURUSD")["score"]
    assert eurusd_hi == eurusd_lo  # DXY irrelevant to a pair → USD leg is 0

    dxy_hi = next(i for i in p_hi["instruments"] if i["symbol"] == "US-DOLLAR")["score"]
    dxy_lo = next(i for i in p_lo["instruments"] if i["symbol"] == "US-DOLLAR")["score"]
    assert dxy_hi > dxy_lo  # single USD row DOES use the DXY cell


def test_fx_sentiment_base_drives_pair_and_dxy_single():
    cal = _eur_usd_growth_cal()
    # Higher EUR sentiment → more bullish EUR/USD.
    p1 = build_payload(cal, _indicators_cfg(), _instruments_cfg_sent(), as_of=AS_OF,
                       sentiment_cells={"EUR": 0, "DXY": 0})
    p2 = build_payload(cal, _indicators_cfg(), _instruments_cfg_sent(), as_of=AS_OF,
                       sentiment_cells={"EUR": 4, "DXY": 0})
    s1 = next(i for i in p1["instruments"] if i["symbol"] == "EURUSD")["score"]
    s2 = next(i for i in p2["instruments"] if i["symbol"] == "EURUSD")["score"]
    assert s2 > s1

    # Single US-DOLLAR uses cell_DXY: negative DXY → more bearish dollar.
    usd_card = p1["currencies"]["USD"]
    single0 = next(i for i in p1["instruments"] if i["symbol"] == "US-DOLLAR")["score"]
    p3 = build_payload(cal, _indicators_cfg(), _instruments_cfg_sent(), as_of=AS_OF,
                       sentiment_cells={"EUR": 0, "DXY": -4})
    single_neg = next(i for i in p3["instruments"] if i["symbol"] == "US-DOLLAR")["score"]
    assert single_neg < single0


def test_fx_sentiment_does_not_touch_index_or_categories():
    """The macro `index` and `categories` (read by cross-asset) are unchanged when
    sentiment is supplied — only instrument scores move."""
    cal = _eur_usd_growth_cal()
    off = build_payload(cal, _indicators_cfg(), _instruments_cfg_sent(), as_of=AS_OF)
    on = build_payload(cal, _indicators_cfg(), _instruments_cfg_sent(), as_of=AS_OF,
                       sentiment_cells={"EUR": 4, "DXY": -2})
    for ccy in ("EUR", "USD"):
        assert off["currencies"][ccy]["index"] == on["currencies"][ccy]["index"]
        assert off["currencies"][ccy]["categories"] == on["currencies"][ccy]["categories"]


# ---------------------------------------------------------------------------
# TREND factor in the FX score — weight-0.5, DIRECT on the pair (NOT base−quote)
# ---------------------------------------------------------------------------

def _instruments_cfg_trend():
    cfg = _instruments_cfg_sent()
    cfg["trend_weight"] = 0.5
    return cfg


def test_fx_trend_off_is_bit_identical():
    cal = _eur_usd_growth_cal()
    off = build_payload(cal, _indicators_cfg(), _instruments_cfg_trend(), as_of=AS_OF)
    none_ = build_payload(cal, _indicators_cfg(), _instruments_cfg_trend(),
                          as_of=AS_OF, trend_cells=None)
    a = {i["symbol"]: i["score"] for i in off["instruments"]}
    b = {i["symbol"]: i["score"] for i in none_["instruments"]}
    assert a == b  # no trend_cells → identical to the no-trend baseline
    assert all(i["trend"] is None for i in off["instruments"])


def test_fx_trend_folds_direct_on_pair_with_weight_half():
    cal = _eur_usd_growth_cal()
    p0 = build_payload(cal, _indicators_cfg(), _instruments_cfg_trend(), as_of=AS_OF,
                       trend_cells=None)
    pT = build_payload(cal, _indicators_cfg(), _instruments_cfg_trend(), as_of=AS_OF,
                       trend_cells={"EURUSD": 3})
    e0 = next(i for i in p0["instruments"] if i["symbol"] == "EURUSD")
    eT = next(i for i in pT["instruments"] if i["symbol"] == "EURUSD")
    assert eT["score"] > e0["score"]          # +3 trend → more bullish
    assert eT["trend"] == 3                    # display cell = the pair's own cell
    # Arithmetic: new = (m·W + 0.5·3)/(W+0.5)·scale, W = mean of the two legs'
    # effective weights (no sentiment supplied here → macro-only index_wsum).
    scale = 5.0
    W = (p0["currencies"]["EUR"]["index_wsum"] + p0["currencies"]["USD"]["index_wsum"]) / 2.0
    m = e0["score"] / scale
    assert eT["score"] == pytest.approx((m * W + 0.5 * 3) / (W + 0.5) * scale)


def test_fx_trend_is_per_pair_not_decomposed():
    """Trend on EURUSD must NOT leak to instruments sharing a leg — trend is
    per-pair only, there is no per-currency trend."""
    cal = _eur_usd_growth_cal()
    p0 = build_payload(cal, _indicators_cfg(), _instruments_cfg_trend(), as_of=AS_OF,
                       trend_cells=None)
    pT = build_payload(cal, _indicators_cfg(), _instruments_cfg_trend(), as_of=AS_OF,
                       trend_cells={"EURUSD": 3})
    # US-DOLLAR shares the USD leg but has its own (absent) trend → unchanged.
    s0 = next(i for i in p0["instruments"] if i["symbol"] == "US-DOLLAR")["score"]
    sT = next(i for i in pT["instruments"] if i["symbol"] == "US-DOLLAR")["score"]
    assert s0 == sT
    assert next(i for i in pT["instruments"] if i["symbol"] == "US-DOLLAR")["trend"] is None


def test_fx_trend_unlisted_pair_is_identical():
    cal = _eur_usd_growth_cal()
    p0 = build_payload(cal, _indicators_cfg(), _instruments_cfg_trend(), as_of=AS_OF,
                       trend_cells=None)
    pT = build_payload(cal, _indicators_cfg(), _instruments_cfg_trend(), as_of=AS_OF,
                       trend_cells={"GBPJPY": 3})  # EURUSD not listed
    e0 = next(i for i in p0["instruments"] if i["symbol"] == "EURUSD")["score"]
    eT = next(i for i in pT["instruments"] if i["symbol"] == "EURUSD")["score"]
    assert e0 == eT


def test_fx_trend_does_not_touch_index_or_categories():
    cal = _eur_usd_growth_cal()
    off = build_payload(cal, _indicators_cfg(), _instruments_cfg_trend(), as_of=AS_OF)
    on = build_payload(cal, _indicators_cfg(), _instruments_cfg_trend(), as_of=AS_OF,
                       trend_cells={"EURUSD": 3})
    for ccy in ("EUR", "USD"):
        assert off["currencies"][ccy]["index"] == on["currencies"][ccy]["index"]
        assert off["currencies"][ccy]["categories"] == on["currencies"][ccy]["categories"]
