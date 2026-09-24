"""Audit 5B.1 — the D2-c sentiment fold is symmetric: a pair leg without a
sentiment value (USD) is folded with s = 0, not left unfolded."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.economic_compute import compute_instrument

ROOT = Path(__file__).resolve().parents[1]
INST_CFG = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())


def _card(g, i, l, m=None):
    cats = {"growth": {"score_precise": g, "coverage": 3, "weight": 1.0},
            "inflation": {"score_precise": i, "coverage": 3, "weight": 1.0},
            "labour": {"score_precise": l, "coverage": 2, "weight": 1.0}}
    if m is not None:
        cats["monetary"] = {"score_precise": m, "coverage": 1, "weight": 1.0}
    return {"categories": cats, "breakdown": {}, "index": 0.0, "index_wsum": 4.0}


CARDS = {"USD": _card(1.0, 0.5, 1.0, 2.0), "EUR": _card(-0.5, 0.0, 0.5, 1.0), "GBP": _card(0.2, -1.0, 0.0, 1.0)}
SENT = {"EUR": 1.5, "GBP": -1.0, "DXY": 2.0}


def _pair(base, quote, sentiment=SENT):
    return compute_instrument(base + quote, {"type": "fx", "base": base, "quote": quote},
                              CARDS, INST_CFG, sentiment_cells=sentiment)


def test_pair_score_is_the_symmetric_formula():
    w_s = float(INST_CFG["d2c_sentiment_w_s"])
    scale, pd_ = float(INST_CFG["scale"]), float(INST_CFG["pair_divisor"])
    for base, quote in (("EUR", "USD"), ("USD", "GBP"), ("EUR", "GBP")):
        p = _pair(base, quote)
        s_b, s_q = SENT.get(base, 0.0), SENT.get(quote, 0.0)
        if base == "USD":
            s_b = 0.0
        if quote == "USD":
            s_q = 0.0
        want = (p["fund_score"] + w_s * (s_b - s_q) * scale / pd_) / (1 + w_s)
        assert p["score"] == pytest.approx(want, abs=1e-12)


def test_usd_pair_moves_against_usd_by_the_usd_mean():
    """vs the old unfolded USD leg: Δ = −(w_s/(1+w_s))·(scale/pd)·mean_USD."""
    w_s = float(INST_CFG["d2c_sentiment_w_s"])
    scale, pd_ = float(INST_CFG["scale"]), float(INST_CFG["pair_divisor"])
    p = _pair("EUR", "USD")
    mean_usd = (1.0 + 0.5 + 1.0 + 2.0) / 4
    old = ((-0.5 + 0.0 + 0.5 + 1.0) / 4 + w_s * 1.5) / (1 + w_s) * scale / pd_ - mean_usd * scale / pd_
    assert p["score"] - old == pytest.approx(w_s / (1 + w_s) * scale / pd_ * mean_usd, abs=1e-12)


def test_without_sentiment_nothing_is_folded():
    p = _pair("EUR", "USD", sentiment=None)
    assert p["score"] == p["fund_score"]
