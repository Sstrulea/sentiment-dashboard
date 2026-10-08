"""The final Score (2026-10-08): an integer −10..+10 from z and the board's
thresholds, truncated toward zero, always consistent with the bias. Display only."""
from __future__ import annotations

import pandas as pd
import pytest

from src.economic_compute import final_score, final_score_precise

TH = {"mild": 0.70, "very": 1.51}
EPS = 1e-9


@pytest.mark.parametrize("z,expected", [
    (0.0, 0), (0.35, 1), (0.70 - EPS, 2), (0.70, 3),                  # just below / at mild
    (1.51 - EPS, 5), (1.51, 6),                                       # just below / at very
    (1.51 * 1.5, 8), (1.51 * 2, 10), (1.51 * 2 + EPS, 10), (50.0, 10),  # the cap at 10
    (-(0.70 - EPS), -2), (-0.70, -3), (-1.51, -6), (-9.0, -10),       # the sign
])
def test_cut_points_cap_and_sign(z, expected):
    assert final_score(z, TH) == expected


def test_truncates_toward_zero_never_rounds():
    v = final_score_precise(0.6999, TH)
    assert 2.99 < v < 3 and final_score(0.6999, TH) == 2
    assert final_score(-0.6999, TH) == -2
    assert final_score(None, TH) is None and final_score_precise(None, TH) is None


def _range_ok(bias: str, fs: int) -> bool:
    a = abs(fs)
    if bias == "Neutral":
        return a <= 2
    if bias.startswith("Very"):
        return 6 <= a <= 10 and (fs > 0) == bias.endswith("Bullish")
    return 3 <= a <= 5 and (fs > 0) == (bias == "Bullish")


def test_every_bias_matches_its_final_score_range_in_the_payload():
    from src.economic_render import build_economic_payload
    p = build_economic_payload(as_of=pd.Timestamp("2026-10-07T19:06:42"))
    n = 0
    for inst in p["instruments"]:
        assert _range_ok(inst["bias"], inst["final_score"]), (inst["symbol"], inst["bias"], inst["final_score"])
        n += 1
    for inst in p["crossasset"]["instruments"]:
        assert _range_ok(inst["bias_label"], inst["final_score"]), (inst["symbol"], inst["bias_label"], inst["final_score"])
        n += 1
    for ccy, card in p["currencies"].items():
        assert _range_ok(card["bias_label"], card["final_score"]), (ccy, card["bias_label"], card["final_score"])
        assert card["pct"] == pytest.approx(max(0, min(100, 50 + 5 * final_score_precise(
            card["strength_z"], p["meta"]["v3"]["thresholds_strength"]))), abs=0.05)
        n += 1
    assert n == 29 + 8 + 8
