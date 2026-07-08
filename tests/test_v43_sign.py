"""V4.3 SIGN scoring variant — sign rule + direction, granularity equality,
fallback sign-only, the ±1/±2 gate at z_buckets[0], byte-identical guard under prod."""
from __future__ import annotations

import copy
import numpy as np
import pandas as pd
import yaml
from pathlib import Path

from src.economic_compute import bucket_score, build_payload, compute_indicator_score

AS_OF = pd.Timestamp("2024-06-10")
COLS = ["currency", "indicator_key", "release_dt", "actual", "consensus"]
ROOT = Path(__file__).resolve().parents[1]


def _rows(key, actuals, cons, ccy="USD"):
    n = len(actuals)
    return pd.DataFrame([{
        "currency": ccy, "indicator_key": key,
        "release_dt": AS_OF - pd.Timedelta(weeks=(n - 1 - i)),
        "actual": actuals[i], "consensus": cons[i],
    } for i in range(n)], columns=COLS)


def _d(variant="sign", **over):
    d = {"surprise_window_k": 12, "z_buckets": [1.54, 0.81], "fallback_min_prints": 6,
         "pct_buckets": [0.10, 0.02], "max_age_days": 120, "scoring_variant": variant}
    d.update(over)
    return d


def _score(sub, cfg, variant="sign"):
    return compute_indicator_score(sub, cfg, _d(variant), AS_OF)


# --- sign rule + direction ------------------------------------------------------

def test_sign_small_beat_scores_pm1_no_deadzone():
    # moderate beat: |z| in (0.81, 1.54)? use a small surprise → prod 0 (dead-zone),
    # sign +1 (any beat scores).
    actuals = [100, 101, 99, 102, 98, 100, 101, 99, 100, 100, 100, 100.5]
    cfg = {"direction": 1, "category": "inflation", "weight": 1.0}
    prod = _score(_rows("cpi_yoy", actuals, [100] * 12), cfg, "prod")
    sign = _score(_rows("cpi_yoy", actuals, [100] * 12), cfg, "sign")
    assert abs(prod["z"]) < 0.81 and prod["score"] == 0        # prod dead-zones it
    assert sign["score"] == 1                                  # sign: any beat → ±1


def test_sign_respects_direction_inversion():
    # unemployment (direction -1): actual ABOVE consensus → negative score.
    actuals = [4.0, 4.1, 3.9, 4.0, 4.1, 3.9, 4.0, 4.1, 3.9, 4.0, 4.0, 4.2]
    r = _score(_rows("unemployment_rate", actuals, [4.0] * 12),
               {"direction": -1, "category": "labour", "weight": 1.0})
    assert r["surprise"] > 0 and r["score"] < 0                # raw beat, inverted sign


def test_sign_gate_at_z_hi():
    # BIG beat (|z| well above 1.54) → ±2; MODERATE (|z| below 1.54) → ±1.
    big = [0.1, -0.1] * 5 + [-0.1, 2.0]
    small = [-1, 1] * 6                                         # latest +1, z≈0.96 < 1.54
    cfg = {"direction": 1, "category": "growth", "weight": 1.0}
    rb = _score(_rows("gdp_qoq", [100 + d for d in big], [100] * 12), cfg)
    rs = _score(_rows("gdp_qoq", [100 + d for d in small], [100] * 12), cfg)
    assert abs(rb["z"]) >= 1.54 and rb["score"] == 2           # gated to ±2
    assert 0 < abs(rs["z"]) < 1.54 and rs["score"] == 1        # below gate → ±1
    # the gate IS z_buckets[0]
    assert rb["score"] == int(np.sign(rb["z"])) * (2 if abs(rb["z"]) >= 1.54 else 1)


def test_sign_equal_at_granularity_is_zero():
    base = [100, 101, 99, 102, 98, 100, 101, 99, 100, 102, 98]
    cfg = {"direction": 1, "category": "inflation", "weight": 1.0}
    assert _score(_rows("cpi_yoy", base + [100.0], [100] * 12), cfg)["score"] == 0      # exact
    assert _score(_rows("cpi_yoy", base + [100 + 1e-10], [100] * 12), cfg)["score"] == 0  # float noise


# --- fallback + no_consensus ----------------------------------------------------

def test_sign_fallback_is_pm1_only():
    # <6 prints, even a huge beat → ±1 (no ±2 gate without z).
    r = _score(_rows("gdp_qoq", [1.0, 1.0, 9.0], [1.0, 1.0, 1.0]),
               {"direction": 1, "category": "growth", "weight": 1.0})
    assert r["flag"] == "fallback" and r["score"] == 1
    # a fallback with equal latest → 0
    r0 = _score(_rows("gdp_qoq", [1.0, 1.0, 1.0], [1.0, 1.0, 1.0]),
                {"direction": 1, "category": "growth", "weight": 1.0})
    assert r0["score"] == 0


def test_sign_no_consensus_zero():
    r = _score(_rows("cpi_yoy", [100, 101, 102], [np.nan, np.nan, np.nan]),
               {"direction": 1, "category": "inflation", "weight": 1.0})
    assert r["flag"] == "no_consensus" and r["score"] == 0


# --- byte-identical guard -------------------------------------------------------

def test_prod_variant_byte_identical():
    cfg = yaml.safe_load((ROOT / "data" / "economic_indicators.yaml").read_text())
    assert cfg["defaults"]["scoring_variant"] == "prod"        # production default
    inst = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())
    # a fixed calendar → payload identical with the flag present vs stripped
    cal = pd.concat([_rows("cpi_yoy", [100, 101, 99, 102, 98, 100, 101, 99, 100, 102, 98, 103],
                           [100] * 12)], ignore_index=True)
    stripped = copy.deepcopy(cfg); stripped["defaults"].pop("scoring_variant")
    p1 = build_payload(cal, cfg, inst, as_of=AS_OF)
    p2 = build_payload(cal, stripped, inst, as_of=AS_OF)
    assert p1["currencies"]["USD"]["categories"] == p2["currencies"]["USD"]["categories"]
