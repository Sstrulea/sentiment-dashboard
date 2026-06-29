"""Tests for src.trend_score — synthetic series; no network, no real data."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src import trend_score as ts
from src.trend_score import (
    _clamp_round,
    _factor_from_adx,
    _round_half_away,
    _server_today,
    adx_factor,
    drop_forming_bar,
    score_all,
    trend_cell,
    trend_components,
)


def _df(closes, spread=0.5) -> pd.DataFrame:
    """Build a daily OHLC frame from a close array (high/low bracket the close)."""
    closes = np.asarray(closes, dtype=float)
    high = closes + spread
    low = closes - spread
    open_ = np.concatenate([[closes[0]], closes[:-1]])
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"date": dates, "open": open_, "high": high, "low": low, "close": closes})


# --- rounding / clamp helpers ----------------------------------------------

@pytest.mark.parametrize("x,expected", [
    (0.5, 1), (-0.5, -1), (1.5, 2), (-1.5, -2), (2.5, 3),
    (0.4, 0), (-0.4, 0), (0.0, 0), (0.75, 1), (-0.25, 0),
])
def test_round_half_away(x, expected):
    assert _round_half_away(x) == expected


@pytest.mark.parametrize("x,expected", [(5, 3), (-5, -3), (3, 3), (-3, -3), (1.5, 2), (0.0, 0)])
def test_clamp_round_bounds(x, expected):
    assert _clamp_round(x) == expected


# --- ADX factor mapping (incl. NaN guard) ----------------------------------

@pytest.mark.parametrize("adx,factor", [
    (30.0, 1.0), (22.0, 1.0), (21.9, 0.5), (15.0, 0.5), (14.9, 0.25), (5.0, 0.25),
    (float("nan"), 0.25), (None, 0.25),
])
def test_factor_from_adx(adx, factor):
    assert _factor_from_adx(adx) == factor


# --- clear trends: sign convention + strong ADX -> ±3 ----------------------

def test_clear_uptrend_is_plus_three():
    df = _df(100.0 + np.arange(260) * 1.0)  # strictly rising ramp
    short, long, slope, raw = trend_components(df)
    assert (short, long, slope, raw) == (1, 1, 1, 3)
    assert adx_factor(df) == 1.0          # ramp -> very high ADX
    assert trend_cell(df) == 3            # + = bullish


def test_clear_downtrend_is_minus_three():
    df = _df(500.0 - np.arange(260) * 1.0)  # strictly falling ramp
    short, long, slope, raw = trend_components(df)
    assert (short, long, slope, raw) == (-1, -1, -1, -3)
    assert adx_factor(df) == 1.0
    assert trend_cell(df) == -3


# --- whipsaw / range: small raw + weak ADX -> pulled toward 0 ---------------

def test_whipsaw_weak_adx_pulled_to_zero():
    closes = 100.0 + 2.0 * (np.arange(260) % 2)  # 100,102,100,102,...
    df = _df(closes)
    assert adx_factor(df) == 0.25          # choppy -> low ADX
    assert abs(trend_cell(df)) <= 1        # dragged toward neutral


# --- flat slope -------------------------------------------------------------

def test_flat_slope_is_zero():
    # rise for 130 bars, then a 70-bar plateau so both SMA50 windows sit fully
    # inside the constant tail -> slope component 0.
    rising = 100.0 + np.arange(130) * 1.0
    plateau = np.full(70, rising[-1])
    df = _df(np.concatenate([rising, plateau]))
    short, long, slope, raw = trend_components(df)
    assert slope == 0
    assert raw == short + long  # slope contributes nothing


# --- guards -----------------------------------------------------------------

def test_insufficient_data_returns_none():
    df = _df(100.0 + np.arange(150) * 1.0)  # < 200 closes
    assert trend_components(df) is None
    assert trend_cell(df) is None


def test_adx_nan_uses_conservative_factor():
    df = _df(np.full(250, 100.0), spread=0.0)  # flat -> TR=0 -> ADX undefined
    assert np.isnan(ts.compute_adx(df).iloc[-1])
    assert adx_factor(df) == 0.25
    # cell still computed (not None): raw=-2 (short/long -1, slope 0) * 0.25 -> -1
    assert trend_cell(df) == _clamp_round(-2 * 0.25)


# --- score_all integration over a tiny parquet -----------------------------

def test_score_all_maps_and_marks_missing(tmp_path):
    pq = tmp_path / "price_history.parquet"
    yml = tmp_path / "price_symbols.yaml"
    yml.write_text("symbols:\n  EURUSD: EURUSD\n  GOLD: XAUUSD\n")

    up = _df(100.0 + np.arange(250) * 1.0)
    up.insert(0, "symbol", "EURUSD")
    up["source"] = "mt5"
    up = up[["symbol", "date", "open", "high", "low", "close", "source"]]
    up.to_parquet(pq, index=False)

    res = score_all(parquet_path=pq, yaml_path=yml, server_today=pd.Timestamp("2100-01-01"))
    assert set(res) == {"EURUSD", "GOLD"}
    assert res["EURUSD"]["trend_cell"] == 3
    assert res["EURUSD"]["raw"] == 3
    # GOLD has no series -> all-None entry
    assert res["GOLD"]["trend_cell"] is None
    assert all(res["GOLD"][k] is None for k in ("short", "long", "slope", "raw", "adx", "factor"))


# --- Option A: exclude the forming (current server-day) bar -----------------

def test_server_today_applies_offset():
    # utc 23:30 + 3h crosses midnight → next server day
    assert _server_today(pd.Timestamp("2026-06-29 23:30")) == pd.Timestamp("2026-06-30")
    # mid-morning utc + 3h stays the same server day
    assert _server_today(pd.Timestamp("2026-06-29 10:00")) == pd.Timestamp("2026-06-29")


def test_drop_forming_bar_excludes_current_day_only():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-06-26", "2026-06-29", "2026-06-30"]),
        "close": [1.0, 2.0, 3.0],
    })
    out = drop_forming_bar(df, server_today=pd.Timestamp("2026-06-30"))
    assert list(out["date"]) == [pd.Timestamp("2026-06-26"), pd.Timestamp("2026-06-29")]


def test_drop_forming_bar_weekend_safe_no_minus_one_day():
    # Monday server day, no Sunday/Saturday bars: the rule removes the CURRENT day,
    # not "1 day", so the last kept bar is Friday (not "Sunday").
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-06-25", "2026-06-26", "2026-06-29"]),  # Thu, Fri, Mon
        "close": [1.0, 2.0, 3.0],
    })
    out = drop_forming_bar(df, server_today=pd.Timestamp("2026-06-29"))  # Monday
    assert out["date"].max() == pd.Timestamp("2026-06-26")  # Friday, automatically


def test_drop_forming_bar_noop_when_already_stale():
    # Latest bar already closed (before today) → nothing dropped.
    df = pd.DataFrame({"date": pd.to_datetime(["2026-06-24", "2026-06-25"]), "close": [1.0, 2.0]})
    out = drop_forming_bar(df, server_today=pd.Timestamp("2026-06-30"))
    assert len(out) == 2


def test_score_ignores_forming_bar(tmp_path):
    """A current-day bar must NOT affect the score — it's computed on the last
    closed bar. A wild forming bar changes the unfiltered ADX (proving it's
    material), yet the filtered score equals the closed-only score."""
    yml = tmp_path / "price_symbols.yaml"
    yml.write_text("symbols:\n  EURUSD: EURUSD\n")
    server_today = pd.Timestamp("2026-06-30")

    n = 250
    dates = pd.bdate_range(end="2026-06-29", periods=n)  # closed bars, last = Mon 2026-06-29
    closes = 100.0 + np.arange(n) * 1.0                   # clean uptrend

    def _mk(dts, cl):
        cl = np.asarray(cl, float)
        return pd.DataFrame({
            "symbol": "EURUSD", "date": dts,
            "open": cl, "high": cl + 0.5, "low": cl - 0.5, "close": cl, "source": "mt5",
        })

    closed = _mk(dates, closes)
    # Forming bar (2026-06-30): a violent crash that distorts ADX if counted.
    today = _mk(pd.to_datetime(["2026-06-30"]), [closes[-1] - 50.0])
    today.loc[:, "low"] = closes[-1] - 60.0

    pq_all = tmp_path / "all.parquet"
    pd.concat([closed, today], ignore_index=True).to_parquet(pq_all, index=False)
    pq_closed = tmp_path / "closed.parquet"
    closed.to_parquet(pq_closed, index=False)

    filtered = score_all(pq_all, yml, server_today=server_today)["EURUSD"]
    unfiltered = score_all(pq_all, yml, server_today=pd.Timestamp("2100-01-01"))["EURUSD"]
    ref = score_all(pq_closed, yml, server_today=pd.Timestamp("2100-01-01"))["EURUSD"]

    # Filtered == closed-only: the forming bar was ignored.
    assert filtered["trend_cell"] == ref["trend_cell"]
    assert filtered["raw"] == ref["raw"]
    assert filtered["adx"] == pytest.approx(ref["adx"])
    # The forming bar WAS material: it moved the unfiltered ADX.
    assert filtered["adx"] != pytest.approx(unfiltered["adx"])


# --- trend signature (cron render trigger) ----------------------------------

def _mk_eurusd(dates, closes):
    closes = np.asarray(closes, float)
    return pd.DataFrame({
        "symbol": "EURUSD", "date": dates,
        "open": closes, "high": closes + 0.5, "low": closes - 0.5,
        "close": closes, "source": "mt5",
    })


def test_trend_signature_ignores_forming_bar(tmp_path):
    """The signature must NOT change when only the forming (current-day) bar's
    OHLC moves — this is the whole point: hourly intraday ticks don't re-trigger."""
    from src.trend_signature import trend_signature

    yml = tmp_path / "price_symbols.yaml"
    yml.write_text("symbols:\n  EURUSD: EURUSD\n")
    st = pd.Timestamp("2026-06-30")
    dates = pd.bdate_range(end="2026-06-29", periods=250)
    closes = 100.0 + np.arange(250) * 1.0

    closed = _mk_eurusd(dates, closes)
    a = pd.concat([closed, _mk_eurusd(pd.to_datetime(["2026-06-30"]), [closes[-1] + 0.2])])
    b = pd.concat([closed, _mk_eurusd(pd.to_datetime(["2026-06-30"]), [closes[-1] - 8.0])])
    pa = tmp_path / "a.parquet"; a.to_parquet(pa, index=False)
    pb = tmp_path / "b.parquet"; b.to_parquet(pb, index=False)

    assert trend_signature(pa, yml, server_today=st) == trend_signature(pb, yml, server_today=st)


def test_trend_signature_changes_on_closed_trend_change(tmp_path):
    """A genuine change in the CLOSED-bar trend flips the signature (→ re-render)."""
    from src.trend_signature import trend_signature

    yml = tmp_path / "price_symbols.yaml"
    yml.write_text("symbols:\n  EURUSD: EURUSD\n")
    st = pd.Timestamp("2026-06-30")
    dates = pd.bdate_range(end="2026-06-29", periods=250)

    up = tmp_path / "up.parquet"
    _mk_eurusd(dates, 100.0 + np.arange(250) * 1.0).to_parquet(up, index=False)   # +3
    down = tmp_path / "down.parquet"
    _mk_eurusd(dates, 600.0 - np.arange(250) * 1.0).to_parquet(down, index=False)  # -3

    assert trend_signature(up, yml, server_today=st) != trend_signature(down, yml, server_today=st)
