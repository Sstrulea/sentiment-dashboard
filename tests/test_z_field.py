"""fix/economic-display: every FX and cross-asset instrument carries z = score /
RMS(instrument) — the value its bias is read from — so the page never recomputes
the RMS. Display only."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.crossasset_compute import compute_crossasset_scores_v3
from src.economic_compute import bias_label, build_payload
from src.ff_scoring import build_matcher, to_scoring_frame

ROOT = Path(__file__).resolve().parents[1]
FROZEN_FF = ROOT / "tests" / "fixtures" / "frozen" / "economic_calendar_ff_4ace910.parquet"
IND = yaml.safe_load((ROOT / "data" / "economic_indicators.yaml").read_text())
FX = yaml.safe_load((ROOT / "data" / "economic_instruments.yaml").read_text())
X = yaml.safe_load((ROOT / "data" / "crossasset_instruments.yaml").read_text())
AS_OF = pd.Timestamp("2026-09-23T07:06:11")


def test_fx_z_is_score_over_rms_and_drives_the_bias():
    cal = to_scoring_frame(pd.read_parquet(FROZEN_FF), build_matcher())
    cal = cal[pd.to_datetime(cal["release_dt"]) <= AS_OF]
    p = build_payload(cal, IND, FX, as_of=AS_OF, sentiment_cells={"EUR": 1, "DXY": -1},
                      policy_rates={"USD": 3.875, "EUR": 2.5, "GBP": 3.75, "JPY": 1.25,
                                    "AUD": 4.35, "NZD": 2.75, "CAD": 2.25, "CHF": 0.0})
    for inst in p["instruments"]:
        rms = FX["v3"]["rms"][inst["symbol"]]
        assert inst["z"] == pytest.approx(inst["score"] / rms), inst["symbol"]
        assert inst["bias"] == bias_label(inst["z"], FX["v3"]["thresholds"]), inst["symbol"]


def test_crossasset_z_is_score_over_rms():
    cats = {c: {k: {"score_precise": 0.3, "coverage": 1} for k in ("growth", "inflation", "labour")}
            for c in ("USD", "EUR", "JPY", "GBP")}
    ys = {"USD": {"signal": -0.5}, "EUR": {"signal": 0.2}, "JPY": {"signal": 0.4},
          "GBP": {"signal": -0.1}, "REAL10": {"signal": 0.3}}
    out = compute_crossasset_scores_v3(cats, X, FX["v3"]["sigma_ccy"], ys, metal_cot={"GOLD": 1, "SILVER": -2})
    for sym, r in out.items():
        assert r["z"] == pytest.approx(r["score_precise"] / X["v3"]["rms"][sym]), sym
        assert r["bias_label"] == bias_label(r["z"], X["v3"]["thresholds"]), sym
