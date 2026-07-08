"""V4 long-horizon extension — trading-day forward returns + non-Neutral filter."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.fundamental_v4_replay import attach_trading_day_returns

BUCKETS = ["Very Bearish", "Bearish", "Bullish", "Very Bullish"]


def _px(closes, start="2024-01-01"):
    dates = pd.bdate_range(start, periods=len(closes))
    return {"TEST": pd.DataFrame({"date": dates, "close": closes})}


def test_rH_hand_computation():
    # closes indexed by trading day; as_of = day 3 → r_H uses the H-th SUBSEQUENT
    # trading day in the series (position-based), not calendar business days.
    closes = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109]
    px = _px(closes)
    as_of = px["TEST"]["date"].iloc[3]
    df = pd.DataFrame([{"symbol": "TEST", "as_of": as_of, "bias": "Bullish"}])
    out = attach_trading_day_returns(df, px, [2, 5])
    assert out["r2"].iloc[0] == (closes[5] / closes[3] - 1)   # pos 3 → 5
    assert out["r5"].iloc[0] == (closes[8] / closes[3] - 1)   # pos 3 → 8


def test_as_of_between_trading_days_uses_last_close():
    # as_of on a weekend → pos is the last trading day on/before it.
    closes = list(range(100, 110))
    px = _px(closes, start="2024-01-01")            # Mon 01-01 .. (bdays)
    fri = px["TEST"]["date"].iloc[4]
    as_of = fri + pd.Timedelta(days=1)              # Saturday
    df = pd.DataFrame([{"symbol": "TEST", "as_of": as_of, "bias": "Bearish"}])
    out = attach_trading_day_returns(df, px, [3])
    assert out["r3"].iloc[0] == (closes[7] / closes[4] - 1)   # pos 4 (Fri) → 7


def test_tail_excluded_not_truncated():
    # as_of near the end with fewer than H trading days ahead → NaN (excluded),
    # NOT clamped to the last available close.
    closes = list(range(100, 110))                  # n = 10
    px = _px(closes)
    near_end = px["TEST"]["date"].iloc[8]           # pos 8
    df = pd.DataFrame([{"symbol": "TEST", "as_of": near_end, "bias": "Bullish"}])
    out = attach_trading_day_returns(df, px, [5])    # tgt 13 >= 10
    assert np.isnan(out["r5"].iloc[0])
    # a within-range horizon still resolves
    out2 = attach_trading_day_returns(df, px, [1])
    assert out2["r1"].iloc[0] == (closes[9] / closes[8] - 1)


def test_non_neutral_filter():
    df = pd.DataFrame({"bias": ["Neutral", "Bullish", "Very Bearish", "Neutral",
                                "Bearish", "Very Bullish"]})
    nn = df[df["bias"].isin(BUCKETS)]
    assert set(nn["bias"]) == set(BUCKETS)
    assert "Neutral" not in set(nn["bias"])
    assert len(nn) == 4


def test_symbol_without_prices_is_nan():
    px = _px(list(range(100, 110)))
    df = pd.DataFrame([{"symbol": "NOPX", "as_of": px["TEST"]["date"].iloc[2], "bias": "Bullish"}])
    out = attach_trading_day_returns(df, px, [3])
    assert np.isnan(out["r3"].iloc[0])
