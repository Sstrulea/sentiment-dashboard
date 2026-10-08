"""Metals (2026-10-07): score = (0.67 × Rates + 0.33 × Macro) × 2.5 with the v3 blocks
unchanged; COT left the metal score; a missing block → the other alone; metals have
their own thresholds (thresholds_metal); the index scores and labels do not move."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.crossasset_compute import V3_METAL_WEIGHTS, compute_crossasset_scores_v3, v3_macro_raw
from src.economic_compute import bias_label

ROOT = Path(__file__).resolve().parents[1]
X = yaml.safe_load((ROOT / "data" / "crossasset_instruments.yaml").read_text())
FX = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())
K = X["v3"]
SIG = FX["v3"]["sigma_ccy"]
CATS = {"USD": {"growth": {"score_precise": 0.4, "coverage": 3}, "inflation": {"score_precise": -0.3, "coverage": 2},
                "labour": {"score_precise": 0.2, "coverage": 4}}}
for c in ("EUR", "JPY", "GBP"):
    CATS[c] = CATS["USD"]
YS = {"USD": {"signal": -0.8}, "EUR": {"signal": 0.1}, "JPY": {"signal": 0.2}, "GBP": {"signal": 0.0}, "REAL10": {"signal": 0.5}}


def _metal(sym, ys=YS, cats=CATS, cot=None):
    return compute_crossasset_scores_v3(cats, X, SIG, ys, metal_cot=cot)[sym]


def test_metal_score_is_67_rates_33_macro_times_2_5():
    assert V3_METAL_WEIGHTS == {"rates": 0.67, "macro": 0.33}
    for sym in ("GOLD", "SILVER"):
        m = v3_macro_raw(X["instruments"][sym], CATS["USD"], SIG["USD"]) / K["sigma_macro"][sym]
        r = -0.8 / K["sigma_rates"][sym]                         # metals: US 2Y only
        g = _metal(sym)
        assert g["score_precise"] == pytest.approx((0.67 * r + 0.33 * m) * 2.5)
        assert sum(f["contribution"] for f in g["factors"] if f["present"]) == pytest.approx(g["score_precise"])
        assert [f["name"] for f in g["factors"]] == ["growth", "inflation", "labour", "rates"]   # no COT row


def test_cot_no_longer_moves_the_metal_score():
    assert _metal("GOLD", cot={"GOLD": 4})["score_precise"] == _metal("GOLD", cot={"GOLD": -4})["score_precise"]


def test_a_missing_block_leaves_the_other_alone():
    no_rates = _metal("GOLD", ys=dict(YS, USD={"signal": None}))
    m = v3_macro_raw(X["instruments"]["GOLD"], CATS["USD"], SIG["USD"]) / K["sigma_macro"]["GOLD"]
    assert no_rates["score_precise"] == pytest.approx(m * 2.5)
    empty = {c: {} for c in CATS}
    no_macro = _metal("GOLD", cats=empty)
    assert no_macro["score_precise"] == pytest.approx(-0.8 / K["sigma_rates"]["GOLD"] * 2.5)


def test_metals_have_their_own_thresholds_and_indices_keep_theirs():
    out = compute_crossasset_scores_v3(CATS, X, SIG, YS)
    for sym, r in out.items():
        th = K["thresholds_metal"] if r["type"] == "metal" else K["thresholds"]
        assert r["bias_label"] == bias_label(r["score_precise"] / K["rms"][sym], th), sym
    assert K["thresholds"] == {"mild": 0.65, "very": 1.59}
