"""feat/factor-scales — rule N-c: every factor on the same scale (input / σ,
clip ±3), rates on the continuous signal clip(z/0.87, ±2), cross-asset macro
on score_precise. Without the config keys every score is the v1 one."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.crossasset_compute import (categories_by_currency, compute_crossasset_scores,
                                    rate_signal)
from src.economic_compute import compute_instrument

ROOT = Path(__file__).resolve().parents[1]
FX_CFG = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())
X_CFG = yaml.safe_load((ROOT / "data" / "crossasset_instruments.yaml").read_text())
NC_KEYS = ("factor_scales", "factor_scales_single", "factor_clip", "rate_signal",
           "bias_thresholds_scaled")


def _v1(cfg):
    return {k: v for k, v in cfg.items() if k not in NC_KEYS}


def _card(g, i, l, m=None, z=None, stale=False):
    """A scorecard built from per-indicator integer scores (lists), so the
    category means and the per-indicator contribution rows agree."""
    cats, bd = {}, {}
    for cat, scores in (("growth", g), ("inflation", i), ("labour", l)):
        cats[cat] = {"score_cell": round(sum(scores) / len(scores)),
                     "score_precise": sum(scores) / len(scores), "coverage": len(scores), "weight": 1.0}
        for n, sc in enumerate(scores):
            bd[f"{cat}_{n}"] = {"score": sc, "category": cat}
    if m is not None:
        cats["monetary"] = {"score_cell": m, "score_precise": float(m), "coverage": 0 if stale else 1,
                            "weight": 1.0, "stale": stale}
        bd["rate_expectations"] = {"score": m, "z": z, "category": "monetary", "stale": stale}
    wsum = sum(1.0 for c in cats.values() if c["coverage"] > 0)
    num = sum(c["score_precise"] for c in cats.values() if c["coverage"] > 0)
    return {"categories": cats, "breakdown": bd, "index_num": num, "index_wsum": wsum,
            "index": num / wsum * 5}


CARDS = {"EUR": _card([1, 0], [-2, -1], [1, 0], m=1, z=0.7),      # 0.5, -1.5, 0.5
         "USD": _card([0, -1], [2, 1, 1], [0], m=-2, z=-2.4),          # -0.5, 1.333.., 0
         "CHF": _card([0], [1, 0], [-1, 0])}
SENT = {"EUR": 3, "CHF": -1, "DXY": -2}
PM = {"m": 2, "z": 1.3, "delta": 0.1, "spread": 1.5, "as_of": "2026-10-02", "method": "z"}


def test_rate_signal_is_clipped_and_falls_back_to_the_bucket():
    assert rate_signal(0.435, 1, 0.87, 2) == pytest.approx(0.5)
    assert rate_signal(-5.0, -2, 0.87, 2) == -2.0
    assert rate_signal(None, 1, 0.87, 2) == 1.0
    assert rate_signal(None, None, 0.87, 2) is None


@pytest.mark.parametrize("sym,inst,pm", [
    ("EURUSD", {"type": "fx", "base": "EUR", "quote": "USD"}, PM),
    ("EURCHF", {"type": "fx", "base": "EUR", "quote": "CHF"}, None),
    ("US-DOLLAR", {"type": "single", "currency": "USD", "sign": 1}, None),
])
def test_fx_contributions_add_up_to_the_score(sym, inst, pm):
    p = compute_instrument(sym, inst, CARDS, FX_CFG, sentiment_cells=SENT, pair_monetary=pm)
    assert abs(p["contrib_residual"]) < 1e-12
    assert "factor_inputs" in p


def test_fx_pair_follows_rule_n_c():
    """EURUSD: inputs on the intersection / σ, clipped, then mean + D2-c fold."""
    s, clip = FX_CFG["factor_scales"], float(FX_CFG["factor_clip"])
    p = compute_instrument("EURUSD", {"type": "fx", "base": "EUR", "quote": "USD"}, CARDS, FX_CFG,
                           sentiment_cells=SENT, pair_monetary=PM)
    x = {"growth": 1.0, "inflation": -1.5 - 4 / 3, "labour": 0.5, "monetary": 1.3 / 0.87}
    y = {f: max(-clip, min(clip, v / s[f])) for f, v in x.items()}
    mean = sum(y.values()) / 4
    ys = max(-clip, min(clip, 3 / s["sentiment"]))              # COT(EUR) − USD(0)
    w_s = float(FX_CFG["d2c_sentiment_w_s"])
    assert p["score"] == pytest.approx((mean + w_s * ys) / (1 + w_s) * 5 / 2, abs=1e-12)
    assert p["fund_score"] == pytest.approx(mean * 5 / 2, abs=1e-12)
    assert p["monetary_pair"]["m"] == 2 and p["monetary_pair"]["signal"] == pytest.approx(1.3 / 0.87)
    mon = [r for r in p["contributions"] if r["category"] == "monetary"][0]
    assert mon["raw"] == 2 and mon["value"] == pytest.approx(1.3 / 0.87)
    assert mon["contribution"] == pytest.approx(y["monetary"] / 4 / (1 + w_s) * 5 / 2 * (1 + w_s), abs=1e-12)


def test_a_large_input_is_clipped_at_three_sigma():
    cards = dict(CARDS, EUR=_card([9, 9], [-2, -1], [1, 0], m=1, z=0.7))
    p = compute_instrument("EURUSD", {"type": "fx", "base": "EUR", "quote": "USD"}, cards, FX_CFG)
    assert p["factor_inputs"]["growth"]["value_sigma"] == 3.0
    assert abs(p["contrib_residual"]) < 1e-12


@pytest.mark.parametrize("sym,inst,pm", [
    ("EURUSD", {"type": "fx", "base": "EUR", "quote": "USD"}, PM),
    ("US-DOLLAR", {"type": "single", "currency": "USD", "sign": 1}, None),
])
def test_without_the_keys_fx_is_v1(sym, inst, pm):
    p = compute_instrument(sym, inst, CARDS, _v1(FX_CFG), sentiment_cells=SENT, pair_monetary=pm)
    assert "factor_inputs" not in p and "signal" not in (p["monetary_pair"] or {})


class _RY:
    score, z, stale, series = -1, -0.6, False, "DFII10"


def test_crossasset_contributions_add_up_and_macro_reads_score_precise():
    cats = categories_by_currency({c: {"categories": v["categories"], "breakdown": v["breakdown"]}
                                   for c, v in CARDS.items()}, X_CFG)
    out = compute_crossasset_scores(cats, _RY(), X_CFG, sentiment_by_symbol={"GOLD": 2, "SP500": -1})
    for sym, r in out.items():
        present = [f for f in r["factors"] if f["present"]]
        assert sum(f["contribution"] for f in present) == pytest.approx(r["score_precise"], abs=1e-12)
    gold = {f["name"]: f for f in out["GOLD"]["factors"]}
    assert gold["inflation"]["value"] == pytest.approx(-4 / 3)               # sign −1 × USD 4/3
    assert gold["inflation"]["value_sigma"] == pytest.approx(-4 / 3 / X_CFG["factor_scales"]["metal"]["inflation"])
    # rates: mean of −clip(z/0.87) over 2Y (z −2.4 → −2 capped) and DFII10 (z −0.6)
    assert gold["rates"]["value"] == pytest.approx((2.0 + 0.6 / 0.87) / 2)


def test_without_the_keys_crossasset_is_v1():
    cats = {c: v["categories"] for c, v in CARDS.items()}
    v1 = compute_crossasset_scores(cats, _RY(), _v1(X_CFG), sentiment_by_symbol={"GOLD": 2})
    gold = {f["name"]: f for f in v1["GOLD"]["factors"]}
    assert gold["inflation"]["raw"] == 1 and "value_sigma" not in gold["inflation"]   # the rounded cell
