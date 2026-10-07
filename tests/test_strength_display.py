"""fix/strength-display: N counts only the indicators that enter Macro (growth +
inflation + labour — not the 2Y entry), and every currency card carries its
categories' values in σ for the drilldown headers. Display only."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.economic_compute import V3_MACRO_CATS, build_payload, compute_currency_scorecard
from src.ff_scoring import build_matcher, to_scoring_frame

ROOT = Path(__file__).resolve().parents[1]
FROZEN_FF = ROOT / "tests" / "fixtures" / "frozen" / "economic_calendar_ff_4ace910.parquet"
IND = yaml.safe_load((ROOT / "data" / "economic_indicators.yaml").read_text())
INST = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())
AS_OF = pd.Timestamp("2026-09-23T07:06:11")


@pytest.fixture(scope="module")
def cal():
    c = to_scoring_frame(pd.read_parquet(FROZEN_FF), build_matcher())
    return c[pd.to_datetime(c["release_dt"]) <= AS_OF]


def test_n_excludes_the_fresh_2y_entry(cal):
    rate = {"score": 1, "stale": False, "z": 0.9, "as_of": "2026-09-22"}
    with_2y = compute_currency_scorecard(cal, "USD", IND, INST, AS_OF, rate_entry=rate)
    without = compute_currency_scorecard(cal, "USD", IND, INST, AS_OF, rate_entry=None)
    macro_n = sum(with_2y["categories"][c]["coverage"] for c in V3_MACRO_CATS)
    assert with_2y["coverage"] == macro_n == without["coverage"]
    assert with_2y["categories"]["monetary"]["coverage"] == 1          # the 2Y is still there, just not N
    assert with_2y["index"] != without["index"]                          # and still in the (v2) index


def test_cards_carry_the_category_values_in_sigma(cal):
    sent = {"EUR": 1, "DXY": -1}
    p = build_payload(cal, IND, INST, as_of=AS_OF, sentiment_cells=sent,
                      policy_rates={"USD": 3.875, "EUR": 2.5, "GBP": 3.75, "JPY": 1.25,
                                    "AUD": 4.35, "NZD": 2.75, "CAD": 2.25, "CHF": 0.0})
    for ccy, card in p["currencies"].items():
        sig = INST["v3"]["sigma_ccy"][ccy]
        ins = card["v3"]["in_sigma"]
        present = [c for c in V3_MACRO_CATS if card["categories"][c]["coverage"] > 0]
        assert sorted(ins) == sorted(present), ccy
        for c in present:
            assert ins[c] == pytest.approx(card["categories"][c]["score_precise"] / sig[c]), (ccy, c)
        # the Macro line = mean of the σ values / σ_macro = the v3 Macro block
        assert card["v3"]["blocks"]["macro"] == pytest.approx(
            sum(ins.values()) / len(ins) / INST["v3"]["sigma_macro"]), ccy
        assert card["coverage"] == sum(card["categories"][c]["coverage"] for c in V3_MACRO_CATS), ccy
