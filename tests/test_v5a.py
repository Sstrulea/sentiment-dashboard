"""V5a composite ablation — variant renormalization, FULL == production byte-identical,
demeaned-S hand-computation, COT point-in-time release lag."""
from __future__ import annotations

import copy
import pytest
import numpy as np
import pandas as pd
import yaml
from pathlib import Path

import scripts.v5a as V
from src.crossasset_compute import compute_instrument_score

ROOT = Path(__file__).resolve().parents[1]


# --- variant construction + renormalization ------------------------------------

def test_full_variant_is_config_identical():
    ind = yaml.safe_load((ROOT / "data" / "economic_indicators.yaml").read_text())
    ca = yaml.safe_load((ROOT / "data" / "crossasset_instruments.yaml").read_text())
    assert V.variant_ind_cfg(ind, 1.0) == ind                       # ×1 → unchanged
    assert V.variant_ca_cfg(ca, V.COMPOSITE_VARIANTS["V5.FULL"]) == ca   # FULL → no deletions


def test_half_halves_surprise_weights_only():
    ind = yaml.safe_load((ROOT / "data" / "economic_indicators.yaml").read_text())
    h = V.variant_ind_cfg(ind, 0.5)
    for c in V.SURPRISE_CATS:
        assert h["categories"][c]["weight"] == 0.5 * ind["categories"][c]["weight"]
    # monetary is not a config category (added via rate_scores) — controlled by the
    # variant `monetary` flag, not the category weights. Indicators untouched.
    assert h["indicators"] == ind["indicators"]


def test_variant_ca_drops_factors():
    ca = yaml.safe_load((ROOT / "data" / "crossasset_instruments.yaml").read_text())
    t = V.variant_ca_cfg(ca, V.COMPOSITE_VARIANTS["V5.T"])
    f = t["instruments"]["SP500"]["factors"]
    assert set(f) == {"trend"}                                      # trend-only
    ts = V.variant_ca_cfg(ca, V.COMPOSITE_VARIANTS["V5.TS"])["instruments"]["SP500"]["factors"]
    assert set(ts) == {"sentiment", "trend"}
    nof = V.variant_ca_cfg(ca, V.COMPOSITE_VARIANTS["V5.NOF"])["instruments"]["SP500"]["factors"]
    assert "growth" not in nof and "rates" in nof and "trend" in nof


def test_weighted_mean_renormalizes_over_present_factors():
    thr = {"mild": 1.9, "very": 4.3}
    # trend-only: score = trend_value × scale (mean of a single factor)
    cfg_t = {"type": "index", "home_ccy": "USD", "factors": {"trend": {"sign": 1, "weight": 0.5}}}
    r = compute_instrument_score("SP500", cfg_t, {"USD": {}}, None, 5.0, thr, trend_value=2)
    assert r["score_precise"] == 2 * 5.0
    # trend+sentiment: (0.5·1 + 0.5·3)/(1.0) × 5 = 10  (renormalized, weights sum≠1)
    cfg_ts = {"type": "index", "home_ccy": "USD",
              "factors": {"sentiment": {"sign": 1, "weight": 0.5}, "trend": {"sign": 1, "weight": 0.5}}}
    r2 = compute_instrument_score("SP500", cfg_ts, {"USD": {}}, None, 5.0, thr, sentiment_value=1, trend_value=3)
    assert r2["score_precise"] == ((0.5 * 1 + 0.5 * 3) / 1.0) * 5.0


# --- demeaned-S hand computation -----------------------------------------------

def test_demeaned_S_hand_computation():
    # two instruments with different drifts; demeaning removes per-instrument mean
    # BEFORE the bull−bear spread (closes the drift/composition artifact).
    n = 6
    rng = np.random.default_rng(1)
    a = {"symbol": ["A"] * (2 * n), "bias": ["Bullish"] * n + ["Bearish"] * n,
         "r5": rng.normal(0, .01, 2 * n), "r30": rng.normal(0, .01, 2 * n),
         "r15": list(rng.normal(0.05, .01, n)) + list(rng.normal(0.02, .01, n))}   # A drifts up
    b = {"symbol": ["B"] * (2 * n), "bias": ["Bullish"] * n + ["Bearish"] * n,
         "r5": rng.normal(0, .01, 2 * n), "r30": rng.normal(0, .01, 2 * n),
         "r15": list(rng.normal(-0.02, .01, n)) + list(rng.normal(-0.05, .01, n))}  # B drifts down
    df = pd.concat([pd.DataFrame(a), pd.DataFrame(b)], ignore_index=True)
    dm = V.demean(df)
    for sym in ["A", "B"]:
        m = df[df.symbol == sym]["r15"].mean()
        assert np.allclose(dm[dm.symbol == sym]["d15"], df[df.symbol == sym]["r15"] - m)
    # demeaned spread = mean(d15|bull) − mean(d15|bear)
    S = V.spread_S(V.nn_of(dm), 15)
    exp = 1e4 * (dm[dm.bias == "Bullish"]["d15"].mean() - dm[dm.bias == "Bearish"]["d15"].mean())
    assert S == pytest.approx(exp)


# --- COT point-in-time release lag ---------------------------------------------

def test_cot_release_lag_publication_date(monkeypatch):
    # report_date + 3d = publication. A report is available at as_of only if
    # report_date <= as_of − 3d. Capture what cot_cells_asof passes to score_currencies.
    seen = {}
    def fake_cur(frame):
        seen["cur_max"] = frame[V.DATE_COL].max() if len(frame) else None
        return pd.DataFrame(columns=["symbol", "cell"])
    monkeypatch.setattr(V, "score_currencies", fake_cur)
    monkeypatch.setattr(V, "score_metals", lambda f: pd.DataFrame(columns=["symbol", "cell"]))
    hist = pd.DataFrame({V.DATE_COL: pd.to_datetime(["2025-06-03", "2025-06-06", "2025-06-08"])})
    # as_of 2025-06-10 → cutoff 06-07 → include 06-03, 06-06; EXCLUDE 06-08 (pub 06-11 > as_of)
    V.cot_cells_asof(hist, hist.iloc[:0], pd.Timestamp("2025-06-10"))
    assert seen["cur_max"] == pd.Timestamp("2025-06-06")
