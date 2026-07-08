"""V4 experiment — engine flags (sigma_method mad, sigma_floor, late-quantize).
Byte-identical guard under defaults, MAD/floor, dead-zone, anchors, category mean."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.economic_compute import (
    _late_quantize,
    bucket_score,
    build_payload,
    compute_currency_scorecard,
    compute_indicator_score,
)

AS_OF = pd.Timestamp("2024-06-10")
COLS = ["currency", "indicator_key", "release_dt", "actual", "consensus"]


def _rows(ccy, key, actuals, cons):
    n = len(actuals)
    return pd.DataFrame([{
        "currency": ccy, "indicator_key": key,
        "release_dt": AS_OF - pd.Timedelta(weeks=(n - 1 - i)),
        "actual": actuals[i], "consensus": cons[i],
    } for i in range(n)], columns=COLS)


def _defaults(**over):
    d = {"surprise_window_k": 12, "z_buckets": [1.54, 0.81], "fallback_min_prints": 6,
         "pct_buckets": [0.10, 0.02], "max_age_days": 120, "sigma_method": "std", "quantize": "early"}
    d.update(over)
    return d


# --- regression guard: defaults (std+early) reproduce production exactly ---------

def test_defaults_byte_identical_to_std_early():
    # under defaults the new fields are inert: score == bucket_score(z, z_buckets),
    # contribution == score, no floor.
    actuals = [100, 101, 99, 102, 98, 100, 101, 99, 100, 102, 98, 103]
    sub = _rows("USD", "cpi_yoy", actuals, [100] * 12)
    cfg = {"direction": 1, "category": "inflation", "weight": 1.0, "sigma_floor": 0.1}
    r = compute_indicator_score(sub, cfg, _defaults(), AS_OF)
    diffs = np.array(actuals) - 100
    z = 3.0 / diffs.std(ddof=1)
    assert r["z"] == pytest.approx(z)
    assert r["score"] == bucket_score(z, [1.54, 0.81])
    assert r["contribution"] == float(r["score"])            # early: contribution == score


# --- V4.1 MAD ------------------------------------------------------------------

def test_mad_hand_computation():
    # diffs = 6×(-1), 6×(+1), latest +1 → median 0, MAD 1, σ=1.4826, z=1/1.4826
    diffs = [-1, 1] * 6
    actuals = [100 + d for d in diffs]
    sub = _rows("USD", "jolts", actuals, [100] * 12)            # jolts: sigma_floor 0
    r = compute_indicator_score(sub, {"direction": 1, "category": "labour", "weight": 1.0},
                                _defaults(sigma_method="mad"), AS_OF)
    med = np.median(diffs)
    sigma = 1.4826 * np.median(np.abs(np.array(diffs) - med))
    assert sigma == pytest.approx(1.4826)
    assert r["z"] == pytest.approx(diffs[-1] / sigma)          # +1 / 1.4826


def test_mad_floor_applied():
    # tiny dispersion (MAD 0.01 → σ_mad 0.0148 < floor 0.1) → σ floored to 0.1
    diffs = [-0.01, 0.01] * 6
    sub = _rows("USD", "cpi_yoy", [3.0 + d for d in diffs], [3.0] * 12)  # cpi_yoy: floor 0.1
    r = compute_indicator_score(sub, {"direction": 1, "category": "inflation", "weight": 1.0,
                                      "sigma_floor": 0.1}, _defaults(sigma_method="mad"), AS_OF)
    assert r["z"] == pytest.approx(0.01 / 0.1)                 # floored σ = 0.1


def test_mad_zero_falls_back_to_pct():
    # 11 identical (diff 0) + one beat → MAD 0, no floor → σ 0 → EXISTING pct fallback
    sub = _rows("USD", "jolts", [100] * 11 + [105], [100] * 12)  # jolts floor 0
    r = compute_indicator_score(sub, {"direction": 1, "category": "labour", "weight": 1.0},
                                _defaults(sigma_method="mad"), AS_OF)
    assert r["flag"] == "fallback"
    assert r["z"] is None


# --- V4.2 late-quantize --------------------------------------------------------

def test_late_quantize_anchors():
    assert _late_quantize(0.81) == pytest.approx(1.052, abs=1e-3)
    assert _late_quantize(1.54) == pytest.approx(2.0)
    assert _late_quantize(3.0) == pytest.approx(2.0)           # clamp
    assert _late_quantize(-0.81) == pytest.approx(-1.052, abs=1e-3)   # symmetric
    assert _late_quantize(-1.54) == pytest.approx(-2.0)


def test_late_quantize_dead_zone():
    assert _late_quantize(0.19) == 0.0                         # below dead-zone
    assert _late_quantize(0.20) == pytest.approx(0.26, abs=1e-2)   # 0.20*2/1.54
    assert _late_quantize(-0.19) == 0.0
    assert _late_quantize(float("nan")) == 0.0


def test_late_quantize_contribution_vs_score():
    # dispersed diffs (MAD 1 → σ 1.4826) with a latest surprise of +2 → z≈1.35 in
    # (0.81, 1.54): early bucket = +1 (int), late contribution = continuous ≈1.75.
    diffs = [-1, 1] * 5 + [-1, 2]
    sub = _rows("USD", "gdp_qoq", [100 + d for d in diffs], [100] * 12)
    cfg = {"direction": 1, "category": "growth", "weight": 1.0}
    early = compute_indicator_score(sub, cfg, _defaults(sigma_method="mad", quantize="early"), AS_OF)
    late = compute_indicator_score(sub, cfg, _defaults(sigma_method="mad", quantize="late"), AS_OF)
    assert early["contribution"] == float(early["score"])      # early: int bucket
    assert late["contribution"] == pytest.approx(_late_quantize(late["z"]))
    assert late["score"] == early["score"]                     # displayed cell unchanged


# --- category: continuous mean vs bucketed -------------------------------------

def _ind_cfg():
    return {"defaults": None, "categories": {"labour": {"weight": 1.0, "label": "Labour"}},
            "indicators": {
                "jolts": {"category": "labour", "direction": 1, "weight": 1.0, "currencies": ["USD"]},
                "adp": {"category": "labour", "direction": 1, "weight": 1.0, "currencies": ["USD"]}}}


def test_category_continuous_vs_bucketed():
    # two labour indicators each with z in the dead-band → bucketed 0 each → cat 0;
    # late-quantize gives small non-zero continuous contributions → cat != 0.
    # both series: alternating ±1 diffs (MAD 1 → σ 1.4826), latest +1 → z≈0.674 →
    # bucket 0 (|z|<0.81) but late-quantize ≈0.876 (non-zero).
    diffs = [-1, 1] * 6
    cal = pd.concat([
        _rows("USD", "jolts", [100 + d for d in diffs], [100] * 12),
        _rows("USD", "adp", [100 + d for d in diffs], [100] * 12),
    ], ignore_index=True)
    inst = {"scale": 5, "bias_thresholds": {"very": 3, "mild": 1.3}, "instruments": {}}
    early_cfg = _ind_cfg(); early_cfg["defaults"] = _defaults(sigma_method="mad", quantize="early")
    late_cfg = _ind_cfg(); late_cfg["defaults"] = _defaults(sigma_method="mad", quantize="late")
    early = compute_currency_scorecard(cal, "USD", early_cfg, inst, AS_OF)
    late = compute_currency_scorecard(cal, "USD", late_cfg, inst, AS_OF)
    assert early["categories"]["labour"]["score_precise"] == 0.0    # both bucket to 0
    assert late["categories"]["labour"]["score_precise"] != 0.0     # continuous mean non-zero
    assert late["categories"]["labour"]["coverage"] == 2
