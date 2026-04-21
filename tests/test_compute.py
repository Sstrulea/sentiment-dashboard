"""Tests for src.compute — built on a synthetic 156-week frame."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.compute import compute_metrics


def _make_frame(n_weeks: int = 156) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range(end="2026-04-20", periods=n_weeks, freq="W-TUE")
    df = pd.DataFrame(
        {
            "report_date_as_yyyy_mm_dd": dates,
            "cftc_contract_market_code": ["TEST01"] * n_weeks,
            "market_and_exchange_names": ["Test Market"] * n_weeks,
            "noncomm_positions_long_all": rng.integers(50_000, 150_000, n_weeks),
            "noncomm_positions_short_all": rng.integers(30_000, 120_000, n_weeks),
            "comm_positions_long_all": rng.integers(80_000, 200_000, n_weeks),
            "comm_positions_short_all": rng.integers(60_000, 180_000, n_weeks),
            "open_interest_all": rng.integers(300_000, 500_000, n_weeks),
        }
    )
    return df


def test_exposures_sum_to_one():
    df = _make_frame()
    out = compute_metrics(df)
    assert np.allclose(out["spec_exp_long"] + out["spec_exp_short"], 1.0)
    assert np.allclose(out["comm_exp_long"] + out["comm_exp_short"], 1.0)


def test_net_equals_long_minus_short():
    df = _make_frame()
    out = compute_metrics(df)
    assert (out["spec_net"] == out["spec_long"] - out["spec_short"]).all()
    assert (out["comm_net"] == out["comm_long"] - out["comm_short"]).all()


def test_percentile_rank_max_is_one():
    # Force an ascending spec_net: max is the last value → rank = 1.0.
    df = _make_frame()
    df = df.sort_values("report_date_as_yyyy_mm_dd").reset_index(drop=True)
    df["noncomm_positions_long_all"] = np.arange(1, len(df) + 1) * 1000
    df["noncomm_positions_short_all"] = 0
    out = compute_metrics(df)
    out = out.sort_values("report_date_as_yyyy_mm_dd").reset_index(drop=True)

    assert out["spec_extreme_6m"].iloc[-1] == pytest.approx(1.0)
    assert out["spec_extreme_3y"].iloc[-1] == pytest.approx(1.0)


def test_percentile_rank_min_is_low():
    df = _make_frame()
    df = df.sort_values("report_date_as_yyyy_mm_dd").reset_index(drop=True)
    # Descending spec_net → final value is the smallest → rank = 1/n.
    df["noncomm_positions_long_all"] = np.arange(len(df), 0, -1) * 1000
    df["noncomm_positions_short_all"] = 0
    out = compute_metrics(df)
    out = out.sort_values("report_date_as_yyyy_mm_dd").reset_index(drop=True)
    assert out["spec_extreme_3y"].iloc[-1] == pytest.approx(1 / 156)


def test_warmup_window_returns_nan():
    df = _make_frame(n_weeks=40)  # less than 156
    out = compute_metrics(df)
    out = out.sort_values("report_date_as_yyyy_mm_dd").reset_index(drop=True)
    # 3-year window never fills, so all 3y ranks are NaN.
    assert out["spec_extreme_3y"].isna().all()
    # 6-month window fills after 26 rows.
    assert out["spec_extreme_6m"].iloc[:25].isna().all()
    assert out["spec_extreme_6m"].iloc[-1] == pytest.approx(
        pd.Series(out["spec_net"].tail(26).values).rank(method="average", pct=True).iloc[-1]
    )
