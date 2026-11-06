"""feat/macro-composite stage G — registry v1: Brier, George's bank / currency calls, realized
outcomes (H.10 basket of 8, ECB basket of 15 with carry), the summary, staleness, and a snapshot."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.registry import core, inputs
from src.tree.composite import CCYS15

ROOT = Path(__file__).resolve().parents[1]


def test_brier():
    assert core.brier([0.5, 0.5], [1, 0]) == 0.25
    assert core.brier([0.9, 0.2], [1, 0]) == pytest.approx((0.01 + 0.04) / 2)


def test_george_bank_calls_outcome_baselines_and_pending():
    days = pd.bdate_range("2026-09-01", "2026-12-31")
    y_us = pd.Series(np.linspace(3.0, 4.0, len(days)), index=days)            # rising 2Y
    calls = [{"data": "2026-10-02", "banca": "Fed", "directie": "hawkish", "probabilitate": "70", "motiv": "inflatie",
              "window_start_day": "2026-10-05", "window_end_day": "2026-11-02", "nota": ""},
             {"data": "2026-10-02", "banca": "Fed", "directie": "dovish", "probabilitate": "60", "motiv": "munca",
              "window_start_day": "2026-10-05", "window_end_day": "2026-11-02", "nota": ""},
             {"data": "2026-10-02", "banca": "RBNZ", "directie": "hawkish", "probabilitate": "55", "motiv": "risc",
              "window_start_day": "2026-10-05", "window_end_day": "2026-11-02", "nota": ""},
             {"data": "2026-12-01", "banca": "Fed", "directie": "hawkish", "probabilitate": "80", "motiv": "risc",
              "window_start_day": "2026-12-02", "window_end_day": "2026-12-30", "nota": ""}]
    r = core.score_george_banks(calls, {"USD": y_us}, pd.Timestamp("2026-11-20"))
    assert r["n_scored"] == 2 and r["n_pending"] == 2
    assert r["brier_george"] == pytest.approx(((0.7 - 1) ** 2 + (0.4 - 1) ** 2) / 2)
    assert r["brier_0.5"] == 0.25
    assert r["brier_momentum_2y_4w"] == pytest.approx((0.55 - 1) ** 2)          # rising before the window
    assert {x["pending"] for x in r["rows"] if x.get("outcome") is None} == {"no 2Y for NZD", "window not finished"}
    assert r["by_directie"]["dovish"]["n"] == 1 and set(r["by_motiv"]) == {"inflatie", "munca"}


def _levels(days, slopes):
    return pd.DataFrame({c: s * np.arange(len(days)) for c, s in slopes.items()}, index=days)


def test_realized_8_and_15_start_after_friday_and_use_baskets():
    d = pd.bdate_range("2026-01-01", "2026-04-30")
    h10 = _levels(d, {c: (i - 3.5) * 1e-4 for i, c in enumerate(core.CCY8)})
    ecb = _levels(d, {c: (i - 7) * 1e-4 for i, c in enumerate(CCYS15)})
    t = pd.Timestamp("2026-01-09")                                               # a Friday
    r = core.realized([t], h10, ecb, lambda c, t: 1.0, horizons=[1])
    r8 = r[r["universe"] == "8"].set_index("ccy")["ret_bp"]
    assert abs(r8.sum()) < 1e-6 and r8["CHF"] > r8["USD"]
    # monday -> monday: 5 business days of slope; basket demeaning: c - mean of the others
    assert r8["CHF"] == pytest.approx(5 * (3.5e-4 - (-0.5e-4 * 0 + (sum((i - 3.5) * 1e-4 for i in range(7))) / 7)) * 1e4)
    r15 = r[r["universe"] == "15"].set_index("ccy")["ret_bp"]
    assert len(r15) == 15 and abs(r15.sum()) < 1e-6                             # equal rates -> carry cancels


def test_summarise_ic_ls_pairs():
    t = pd.Timestamp("2026-01-09")
    snaps = pd.DataFrame({"asof": t, "source": "x", "universe": "8", "ccy": core.CCY8, "score": np.arange(8.0)})
    real = pd.DataFrame({"asof": t, "universe": "8", "ccy": core.CCY8, "h": 2, "ret_bp": np.arange(8.0) * 10})
    s = core.summarise(snaps, real)[0]
    assert s["mean_IC"] == pytest.approx(1.0) and s["pairs_hit"] == 1.0
    assert s["ls_bp"] == pytest.approx(65.0 - 5.0)


def test_staleness_marks_old_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(inputs, "DIR", tmp_path)
    pd.DataFrame({"date": pd.to_datetime(["2026-01-01"]), "ccy": "USD", "log_usd_value": 0.0}).to_parquet(tmp_path / "h10_fx.parquet")
    st = inputs.staleness(pd.Timestamp("2026-02-01"))
    assert st["h10_fx.parquet"]["stale"] and st["p5_ecb_fx.parquet"]["stale"]           # missing -> stale


def test_refresh_keeps_the_previous_copy_on_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(inputs, "DIR", tmp_path)
    monkeypatch.setattr(inputs, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(inputs, "SEED_FILES", {})
    pd.DataFrame({"a": [1]}).to_parquet(tmp_path / "x.parquet")

    def boom():
        raise RuntimeError("down")
    rep = inputs.refresh("weekly", jobs={"x.parquet": boom, "y.parquet": lambda: pd.DataFrame({"b": [2]})})
    assert rep["x.parquet"].startswith("FAILED") and rep["y.parquet"].startswith("ok")
    assert pd.read_parquet(tmp_path / "x.parquet")["a"].tolist() == [1]
    assert "y.parquet" in json.loads((tmp_path / "state.json").read_text())


def test_snapshot_writes_every_source(tmp_path):
    payload = ROOT / "public" / "data" / "economic.json"
    out = tmp_path / "snap.parquet"
    df = core.snapshot(pd.Timestamp("2026-10-30"), payload=payload, out=out)
    src = set(df["source"])
    assert {"v3", "candidate_live", "composite", "labour", "carry_vol", "ms", "tff_am_contrarian",
            "macro_momentum", "v4"} <= src
    assert set(df[df["source"] == "composite"]["universe"]) == {"8", "15"}
    again = core.snapshot(pd.Timestamp("2026-10-30"), payload=payload, out=out)       # idempotent per asof
    assert len(again) == len(df)
