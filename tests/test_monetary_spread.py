"""Audit 5B.2 — pair monetary on the 2y spread."""
from __future__ import annotations

import itertools
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.economic_compute import compute_instrument
from src.rate_compute import _end_changes, compute_pair_spread_scores, spread_series

ROOT = Path(__file__).resolve().parents[1]
INST_CFG = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())


def _rates(seed=3, n=400):
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2025-01-01", periods=n)
    rows = []
    for c, lvl in (("USD", 4.0), ("EUR", 2.0), ("JPY", 1.0)):
        y = lvl + np.cumsum(rng.normal(0, 0.03, n))
        keep = rng.random(n) > (0.1 if c == "JPY" else 0.0)       # JPY misses some days
        rows += [{"currency": c, "date": d, "tenor": "2y", "yield_pct": v, "source": "x"}
                 for d, v, k in zip(days, y, keep) if k]
    return pd.DataFrame(rows)


def test_spread_is_the_inner_join_without_fill():
    r = _rates()
    d, v = spread_series(r, "EUR", "JPY")
    assert len(d) == (r.currency == "JPY").sum()
    d2, v2 = spread_series(r, "JPY", "EUR")
    assert d == d2 and v == [-x for x in v2]


def test_averaged_ends():
    ys = list(np.arange(40, dtype=float))
    ch = _end_changes(ys, 21, 5)
    assert ch[0] == pytest.approx(21.0) and len(ch) == 40 - 21 - 5 + 1


def test_m_is_exactly_antisymmetric():
    r = _rates()
    pairs = [(b + q, b, q) for b, q in itertools.permutations(["USD", "EUR", "JPY"], 2)]
    for d in pd.bdate_range("2025-06-02", periods=30, freq="5B"):
        s = compute_pair_spread_scores(r, pairs, as_of=d.date())
        for b, q in itertools.combinations(["USD", "EUR", "JPY"], 2):
            assert s[b + q]["m"] == -s[q + b]["m"] and s[b + q]["z"] == -s[q + b]["z"]


def _card(g, m=None):
    cats = {"growth": {"score_precise": g, "coverage": 2, "weight": 1.0},
            "inflation": {"score_precise": 0.5, "coverage": 2, "weight": 1.0}}
    if m is not None:
        cats["monetary"] = {"score_precise": m, "coverage": 1, "weight": 1.0}
    return {"categories": cats, "breakdown": {"rate_expectations": {"score": m or 0, "category": "monetary"}},
            "index": 0.0, "index_wsum": 3.0}


CARDS = {"EUR": _card(1.0, 2.0), "USD": _card(-1.0, 2.0), "CHF": _card(0.5)}


def test_m_replaces_the_legs_difference_in_the_pair():
    pm = {"m": -2, "z": -1.3, "delta": -0.1, "spread": -1.8, "as_of": "2026-09-23", "method": "z"}
    p0 = compute_instrument("EURUSD", {"type": "fx", "base": "EUR", "quote": "USD"}, CARDS, INST_CFG)
    p1 = compute_instrument("EURUSD", {"type": "fx", "base": "EUR", "quote": "USD"}, CARDS, INST_CFG,
                            pair_monetary=pm)
    scale, pd_ = float(INST_CFG["scale"]), float(INST_CFG["pair_divisor"])
    # wsum 3 (growth, inflation, monetary): Δfund = (m − (2 − 2)) · scale / (3 · pd)
    assert p1["fund_score"] - p0["fund_score"] == pytest.approx(-2 * scale / (3 * pd_), abs=1e-12)
    mon = [r for r in p1["contributions"] if r["category"] == "monetary"][0]
    assert mon["raw"] == -2 and mon["pair_spread"] is True
    assert mon["contribution"] == pytest.approx(-2 * scale / (3 * pd_), abs=1e-12)
    other0 = [r for r in p0["contributions"] if r["category"] != "monetary"]
    other1 = [r for r in p1["contributions"] if r["category"] != "monetary"]
    assert other0 == other1                                    # bit-identical
    assert p1["monetary_pair"]["used"] is True


def test_no_fresh_2y_on_a_leg_means_no_spread():
    pm = {"m": 2, "z": 1.5, "delta": 0.1, "spread": 1.0, "as_of": "2026-09-23", "method": "z"}
    a = compute_instrument("EURCHF", {"type": "fx", "base": "EUR", "quote": "CHF"}, CARDS, INST_CFG)
    b = compute_instrument("EURCHF", {"type": "fx", "base": "EUR", "quote": "CHF"}, CARDS, INST_CFG,
                           pair_monetary=pm)
    assert b.pop("monetary_pair") is None and a.pop("monetary_pair") is None
    assert a == b
