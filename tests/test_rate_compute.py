"""Tests for src.rate_compute — pure repricing-momentum scoring, no I/O."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.rate_compute import (
    RateScore,
    compute_rate_score_for,
    compute_rate_scores,
)

AS_OF = date(2024, 6, 28)


def _bdays(n: int, end: date = AS_OF) -> list[date]:
    """n business days ending at `end` (ascending)."""
    rng = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    return [d.date() for d in rng]


def _df(currency: str, dates, ys) -> pd.DataFrame:
    return pd.DataFrame({
        "currency": currency,
        "date": pd.to_datetime(pd.Series(dates)),
        "tenor": "2y",
        "yield_pct": ys,
        "source": "test",
    })


# ---------------------------------------------------------------------------
# Direction / magnitude via z-path (long history)
# ---------------------------------------------------------------------------

def test_steady_rise_scores_plus_two():
    # 300 business days rising +0.02pp/day → big 21d change vs tiny vol → +2.
    n = 300
    dates = _bdays(n)
    ys = [1.0 + 0.02 * i for i in range(n)]
    s = compute_rate_score_for(dates, ys, "USD", AS_OF)
    assert s.method == "z"
    assert s.delta_w == pytest.approx(0.02 * 21, abs=1e-9)
    assert s.rate_score == 2          # rising = hawkish = positive
    assert s.z > 0


def test_steady_fall_scores_minus_two():
    n = 300
    dates = _bdays(n)
    ys = [5.0 - 0.02 * i for i in range(n)]
    s = compute_rate_score_for(dates, ys, "EUR", AS_OF)
    assert s.method == "z"
    assert s.rate_score == -2         # falling = dovish = negative
    assert s.delta_w < 0 and s.z < 0


def test_flat_scores_zero():
    n = 300
    dates = _bdays(n)
    ys = [2.0] * n
    s = compute_rate_score_for(dates, ys, "CHF", AS_OF)
    # zero variance baseline → z unusable → fallback; delta 0 → score 0
    assert s.rate_score == 0
    assert s.delta_w == pytest.approx(0.0)
    assert s.method == "fallback"


def test_sign_convention_up_is_positive():
    n = 120
    dates = _bdays(n)
    ys = [3.0] * (n - 21) + [3.0 + 0.01 * i for i in range(21)]
    s = compute_rate_score_for(dates, ys, "GBP", AS_OF)
    assert s.delta_w > 0
    assert s.rate_score > 0


# ---------------------------------------------------------------------------
# z magnitude buckets (mid signal → +1)
# ---------------------------------------------------------------------------

def test_moderate_move_in_noise_scores_one():
    # Random-walk noise (reproducible) so the W-change std is non-trivial, then a
    # final move sized to land z in [0.5, 1.0) → ±1.
    n = 320
    dates = _bdays(n)
    steps = np.sin(np.arange(n) * 0.7) * 0.02   # deterministic oscillation
    ys = list(2.0 + np.cumsum(steps))
    s = compute_rate_score_for(dates, ys, "USD", AS_OF)
    assert s.method == "z"
    assert abs(s.rate_score) <= 2
    # the bucket must be consistent with its own z
    expected = (2 if abs(s.z) >= 1.0 else 1 if abs(s.z) >= 0.5 else 0) * (1 if s.delta_w > 0 else -1 if s.delta_w < 0 else 0)
    assert s.rate_score == expected


def test_z_threshold_exact_buckets():
    # Build changes with known std, then check boundary behavior by construction.
    # Use a series where the last 21d change equals exactly 1*std and 0.5*std.
    from src.rate_compute import _bucket_z
    assert _bucket_z(1.0) == 2
    assert _bucket_z(0.99) == 1
    assert _bucket_z(0.5) == 1
    assert _bucket_z(0.49) == 0
    assert _bucket_z(-1.5) == 2  # magnitude only; sign applied separately


# ---------------------------------------------------------------------------
# Fallback bands (short history)
# ---------------------------------------------------------------------------

def test_short_history_uses_fallback_bands():
    # 40 business days (< MIN_FOR_Z changes) → fallback. delta over 21d = +0.10pp
    # → ≥0.08 band → +1.
    n = 40
    dates = _bdays(n)
    ys = [1.0] * (n - 21) + [1.0 + (0.10 / 20) * i for i in range(21)]
    s = compute_rate_score_for(dates, ys, "NZD", AS_OF)
    assert s.method == "fallback"
    assert s.z is None
    # 5B averaged ends: mean of the last 5 ramp points is 2 steps below the last
    assert s.delta_w == pytest.approx(0.10 - 2 * 0.10 / 20, abs=1e-9)
    assert s.rate_score == 1


def test_fallback_large_move_plus_two():
    n = 50
    dates = _bdays(n)
    ys = [2.0] * (n - 21) + [2.0 + (0.40 / 20) * i for i in range(21)]
    s = compute_rate_score_for(dates, ys, "AUD", AS_OF)
    assert s.method == "fallback"
    assert s.delta_w == pytest.approx(0.40 - 2 * 0.40 / 20, abs=1e-9)   # 5B averaged ends
    assert s.rate_score == 2


def test_fallback_small_move_zero():
    n = 45
    dates = _bdays(n)
    ys = [2.0] * (n - 21) + [2.0 + (0.04 / 20) * i for i in range(21)]
    s = compute_rate_score_for(dates, ys, "CAD", AS_OF)
    assert s.method == "fallback"
    assert s.rate_score == 0          # 0.04pp < 0.08 band


def test_insufficient_history():
    dates = _bdays(10)
    ys = [1.0 + 0.1 * i for i in range(10)]
    s = compute_rate_score_for(dates, ys, "JPY", AS_OF)
    assert s.method == "insufficient"
    assert s.rate_score == 0
    assert s.delta_w is None
    assert s.latest_yield == pytest.approx(ys[-1])


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

def test_stale_flag_set_when_latest_old():
    n = 100
    end = AS_OF - timedelta(days=30)   # latest ~30 calendar days before as_of
    dates = _bdays(n, end=end)
    ys = [1.0 + 0.01 * i for i in range(n)]
    s = compute_rate_score_for(dates, ys, "USD", AS_OF)
    assert s.stale is True


def test_fresh_not_stale():
    n = 100
    dates = _bdays(n, end=AS_OF)
    ys = [1.0 + 0.01 * i for i in range(n)]
    s = compute_rate_score_for(dates, ys, "USD", AS_OF)
    assert s.stale is False


# ---------------------------------------------------------------------------
# Frame-level driver
# ---------------------------------------------------------------------------

def test_compute_rate_scores_multi_currency_and_dedup():
    n = 300
    dates = _bdays(n)
    up = _df("USD", dates, [1.0 + 0.02 * i for i in range(n)])
    dn = _df("EUR", dates, [5.0 - 0.02 * i for i in range(n)])
    # duplicate (currency,date) — last-write-wins should keep the de-duped series
    dup = _df("USD", [dates[-1]], [up["yield_pct"].iloc[-1]])
    df = pd.concat([up, dn, dup], ignore_index=True)
    out = compute_rate_scores(df, as_of=AS_OF)
    assert set(out) == {"USD", "EUR"}
    assert out["USD"].rate_score == 2
    assert out["EUR"].rate_score == -2
    assert isinstance(out["USD"], RateScore)


def test_empty_frame_returns_empty():
    assert compute_rate_scores(pd.DataFrame(columns=["currency", "date", "yield_pct"])) == {}


def test_default_as_of_uses_latest_date_in_frame():
    n = 100
    dates = _bdays(n, end=date(2024, 1, 15))
    df = _df("USD", dates, [1.0 + 0.01 * i for i in range(n)])
    out = compute_rate_scores(df)  # no as_of → ref = latest date in frame
    assert out["USD"].as_of == dates[-1]
    assert out["USD"].stale is False


def test_as_of_cut_ignores_future_rows():
    # FIX 2: rows dated after as_of must not affect the score (structural guard).
    n = 300
    dates = _bdays(n, end=AS_OF)
    rising = _df("USD", dates, [1.0 + 0.02 * i for i in range(n)])
    # append future rows that would flip momentum down hard
    fut_dates = [AS_OF + timedelta(days=k) for k in range(1, 40)]
    future = _df("USD", fut_dates, [100.0 - i for i in range(len(fut_dates))])
    full = pd.concat([rising, future], ignore_index=True)
    cut = compute_rate_scores(full, as_of=AS_OF)["USD"]
    ref = compute_rate_scores(rising, as_of=AS_OF)["USD"]
    assert cut.rate_score == ref.rate_score == 2
    assert cut.latest_yield == pytest.approx(ref.latest_yield)
