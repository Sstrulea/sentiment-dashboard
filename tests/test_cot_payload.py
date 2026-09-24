"""Audit 10A — COT data payload, criteria fixed in advance (a)-(d)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.compute import build_latest_snapshot
from src.cot_payload import IN_MODEL, build

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "data" / "history.parquet"


@pytest.fixture(scope="module")
def built():
    return build(pd.read_parquet(HISTORY))


def _by_symbol(week):
    return {i["symbol"]: i for i in week["instruments"]}


def test_a_latest_week_scores_match_economic(built):
    """(a) last week, the 10 in_model instruments: level and flow identical to
    public/data/economic.json (FX legs + US-DOLLAR + cross-asset GOLD/SILVER),
    blend within ±0.1."""
    econ = json.loads((ROOT / "public" / "data" / "economic.json").read_text())
    ref = {}
    for inst in econ["instruments"]:
        c = inst.get("cot") or {}
        if inst["symbol"] == "US-DOLLAR" and c.get("base_detail"):
            ref["DXY"] = c["base_detail"]
        elif c.get("base") and c.get("base_detail"):
            ref[c["base"]] = c["base_detail"]
        if c.get("quote") and c.get("quote_detail"):
            ref[c["quote"]] = c["quote_detail"]
    for inst in econ["crossasset"]["instruments"]:
        if inst["symbol"] in ("GOLD", "SILVER") and inst.get("cot"):
            ref[inst["symbol"]] = inst["cot"]
    week = _by_symbol(built["weeks"][built["index"]["latest"]])
    assert {s for s, i in week.items() if i.get("in_model")} == set(IN_MODEL)
    for sym in IN_MODEL:
        got, want = week[sym]["score"], ref[sym]
        assert (got["level"], got["flow"]) == (want["level"], want["flow"]), sym
        assert got["blend"] == pytest.approx(want["blend"], abs=0.1), sym


def test_b_percentiles_match_the_latest_snapshot(built):
    """(b) p6 and p3 identical (unrounded) to spec_extreme_6m / _3y."""
    snap = build_latest_snapshot(pd.read_parquet(HISTORY))
    week = _by_symbol(built["weeks"][built["index"]["latest"]])
    for r in snap.itertuples():
        spec = week[r.symbol]["spec"]
        for got, want in ((spec["p6"], r.spec_extreme_6m), (spec["p3"], r.spec_extreme_3y)):
            assert (got is None and pd.isna(want)) or got == float(want), r.symbol


def test_c_flips_on_2026_09_15(built):
    week = _by_symbol(built["weeks"]["2026-09-15"])
    flips = {(s, side, i[side]["flip3y"]) for s, i in week.items() if not i.get("missing")
             for side in ("spec", "comm") if i[side]["flip3y"]}
    assert flips == {("NZD", "spec", "high"), ("UST5Y", "spec", "high"), ("UST5Y", "comm", "low")}


def test_d_jpy_on_2026_09_15(built):
    jpy = _by_symbol(built["weeks"]["2026-09-15"])["JPY"]["spec"]
    assert jpy["d1w"] == 107849
    assert jpy["pct_oi"] == pytest.approx(0.207, abs=0.0005)
    assert jpy["chg4"] == 172900
    assert jpy["z4w"] == pytest.approx(3.38, abs=0.005)


def test_missing_row_and_week_selection(built):
    week = _by_symbol(built["weeks"]["2026-09-15"])
    assert week["OATS"] == {**{k: week["OATS"][k] for k in ("symbol", "name", "category",
                            "category_label", "in_model", "exchange", "quote")}, "missing": True,
                            "last_report": "2026-09-01"}
    series = built["series"]
    assert len(series["dates"]) == len(series["instruments"]["JPY"]["spec_net"])
    assert built["index"]["weeks"][0] == built["index"]["latest"]
