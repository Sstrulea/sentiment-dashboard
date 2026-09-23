"""Audit B1 — /strength as the aggregate of the /economic pairs."""
from __future__ import annotations

import itertools
from pathlib import Path

import pytest
import yaml

from src.economic_compute import compute_instrument
from src.economic_render import strength_from_pairs, _monetary_state

ROOT = Path(__file__).resolve().parents[1]
CCYS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
INST_CFG = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())


def _card(growth, inflation, labour, monetary=None):
    cats = {"growth": {"score_precise": growth, "coverage": 3, "weight": 1.0},
            "inflation": {"score_precise": inflation, "coverage": 3, "weight": 1.0},
            "labour": {"score_precise": labour, "coverage": 2, "weight": 1.0}}
    if monetary is not None:
        cats["monetary"] = {"score_precise": monetary, "coverage": 1, "weight": 1.0}
    return {"categories": cats, "breakdown": {}, "index": 0.0, "index_wsum": 4.0}


def _payload(cards):
    insts = []
    for base, quote in itertools.combinations(CCYS, 2):
        insts.append(compute_instrument(f"{base}{quote}", {"type": "fx", "base": base, "quote": quote},
                                        cards, INST_CFG))
    return {"instruments": insts, "currencies": {c: {} for c in CCYS}}


BASE = {c: _card(0.1 * i, -0.2 * (i % 3), 0.3 * ((i + 1) % 2), monetary=(0.5 if i % 2 else None))
        for i, c in enumerate(CCYS)}


def test_i_score_is_the_exact_mean_of_the_seven_pair_scores():
    p = _payload(BASE)
    scores = strength_from_pairs(p)["scores"]
    for c in CCYS:
        legs = [(i["fund_score"] if i["breakdown"]["base"]["currency"] == c else -i["fund_score"])
                for i in p["instruments"] if c in (i["breakdown"]["base"]["currency"],
                                                   i["breakdown"]["quote"]["currency"])]
        assert len(legs) == 7
        assert scores[c] == pytest.approx(sum(legs) / 7, abs=1e-12)


def test_ii_the_eight_scores_sum_to_zero():
    assert sum(strength_from_pairs(_payload(BASE))["scores"].values()) == pytest.approx(0.0, abs=1e-12)


def test_iii_a_common_shift_leaves_strength_unchanged():
    """The same category shift on every currency (monetary +2 where present,
    growth +2 on all) cancels in every pair."""
    before = strength_from_pairs(_payload(BASE))["scores"]
    shifted = {c: _card(card["categories"]["growth"]["score_precise"] + 2,
                        card["categories"]["inflation"]["score_precise"],
                        card["categories"]["labour"]["score_precise"],
                        (card["categories"]["monetary"]["score_precise"] + 2
                         if "monetary" in card["categories"] else None))
               for c, card in BASE.items()}
    after = strength_from_pairs(_payload(shifted))["scores"]
    assert after == pytest.approx(before, abs=1e-12)


def test_iii_a_shift_on_one_currency_moves_it_the_right_way():
    """+growth on CHF only: CHF rises. Every other currency changes ONLY through
    its own pair with CHF: by exactly -(that pair's change)/7, i.e. down (the 8
    scores still sum to 0). The size differs with the pair's intersection
    (3 or 4 categories, monetary only when both legs have it)."""
    p0 = _payload(BASE)
    before = strength_from_pairs(p0)["scores"]
    cards = dict(BASE)
    g = cards["CHF"]["categories"]
    cards["CHF"] = _card(g["growth"]["score_precise"] + 1.0, g["inflation"]["score_precise"],
                         g["labour"]["score_precise"],
                         g["monetary"]["score_precise"] if "monetary" in g else None)
    p1 = _payload(cards)
    after = strength_from_pairs(p1)["scores"]
    m0, m1 = strength_from_pairs(p0)["matrix"], strength_from_pairs(p1)["matrix"]
    assert after["CHF"] > before["CHF"]
    for c in CCYS:
        if c == "CHF":
            continue
        pair_change = m1[c]["CHF"] - m0[c]["CHF"]
        assert pair_change < 0
        assert after[c] - before[c] == pytest.approx(pair_change / 7, abs=1e-12)
        for o in CCYS:                       # no other pair moved
            if o not in (c, "CHF"):
                assert m1[c][o] == pytest.approx(m0[c][o], abs=1e-12)
    assert sum(after.values()) == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize("cat,rate,state", [
    ({"coverage": 1, "score_cell": 2}, {"score": 2}, ("ok", 2)),
    ({"coverage": 0, "score_cell": 2, "stale": True}, {"score": 2, "stale": True}, ("stale", 2)),
    (None, None, ("missing", None)),
])
def test_monetary_state_from_categories(cat, rate, state):
    card = {"categories": {"monetary": cat} if cat else {},
            "breakdown": {"rate_expectations": rate} if rate else {}}
    m = _monetary_state(card)
    assert (m["state"], m["score"]) == state
