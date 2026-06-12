"""Tests for src.realyield_compute — pure real-yield momentum scoring, no I/O.

Mirror of test_rate_compute.py (single series instead of per-currency).
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.realyield_compute import (
    RealYieldScore,
    _bucket_z,
    compute_realyield_score,
    compute_realyield_score_for,
)

AS_OF = date(2024, 6, 28)


def _bdays(n: int, end: date = AS_OF) -> list[date]:
    rng = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    return [d.date() for d in rng]


def _df(dates, ys, series="DFII10") -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.to_datetime(pd.Series(dates)),
        "series": series,
        "yield_pct": ys,
        "source": "test",
    })


# ---------------------------------------------------------------------------
# Direction / magnitude via z-path
# ---------------------------------------------------------------------------

def test_steady_rise_scores_plus_two():
    n = 300
    ys = [1.0 + 0.02 * i for i in range(n)]
    s = compute_realyield_score_for(_bdays(n), ys, "DFII10", AS_OF)
    assert s.method == "z"
    assert s.delta_w == pytest.approx(0.02 * 21, abs=1e-9)
    assert s.score == 2          # rising real yield = + (raw convention)
    assert s.z > 0


def test_steady_fall_scores_minus_two():
    n = 300
    ys = [5.0 - 0.02 * i for i in range(n)]
    s = compute_realyield_score_for(_bdays(n), ys, "DFII10", AS_OF)
    assert s.method == "z"
    assert s.score == -2         # falling real yield = − (raw)
    assert s.delta_w < 0 and s.z < 0


def test_flat_scores_zero():
    n = 300
    s = compute_realyield_score_for(_bdays(n), [2.0] * n, "DFII10", AS_OF)
    assert s.score == 0
    assert s.delta_w == pytest.approx(0.0)
    assert s.method == "fallback"   # zero-variance baseline → fallback, delta 0 → 0


def test_raw_sign_up_is_positive():
    # Raw convention: rising real yield is positive (asset sign applied later).
    n = 120
    ys = [3.0] * (n - 21) + [3.0 + 0.01 * i for i in range(21)]
    s = compute_realyield_score_for(_bdays(n), ys, "DFII10", AS_OF)
    assert s.delta_w > 0
    assert s.score > 0


def test_z_threshold_buckets():
    assert _bucket_z(1.0) == 2
    assert _bucket_z(0.99) == 1
    assert _bucket_z(0.5) == 1
    assert _bucket_z(0.49) == 0
    assert _bucket_z(-1.5) == 2   # magnitude only; sign applied separately


def test_moderate_move_consistent_with_z():
    n = 320
    steps = np.sin(np.arange(n) * 0.7) * 0.02
    ys = list(2.0 + np.cumsum(steps))
    s = compute_realyield_score_for(_bdays(n), ys, "DFII10", AS_OF)
    assert s.method == "z"
    expected = (2 if abs(s.z) >= 1.0 else 1 if abs(s.z) >= 0.5 else 0) * \
               (1 if s.delta_w > 0 else -1 if s.delta_w < 0 else 0)
    assert s.score == expected


# ---------------------------------------------------------------------------
# Fallback bands (short history)
# ---------------------------------------------------------------------------

def test_short_history_uses_fallback():
    n = 40
    ys = [1.0] * (n - 21) + [1.0 + (0.10 / 20) * i for i in range(21)]
    s = compute_realyield_score_for(_bdays(n), ys, "DFII10", AS_OF)
    assert s.method == "fallback"
    assert s.z is None
    assert s.delta_w == pytest.approx(0.10, abs=1e-9)
    assert s.score == 1


def test_fallback_large_move_plus_two():
    n = 50
    ys = [2.0] * (n - 21) + [2.0 + (0.40 / 20) * i for i in range(21)]
    s = compute_realyield_score_for(_bdays(n), ys, "DFII10", AS_OF)
    assert s.method == "fallback"
    assert s.delta_w == pytest.approx(0.40, abs=1e-9)
    assert s.score == 2


def test_fallback_small_move_zero():
    n = 45
    ys = [2.0] * (n - 21) + [2.0 + (0.04 / 20) * i for i in range(21)]
    s = compute_realyield_score_for(_bdays(n), ys, "DFII10", AS_OF)
    assert s.method == "fallback"
    assert s.score == 0           # 0.04pp < 0.08 band


def test_insufficient_history():
    ys = [1.0 + 0.1 * i for i in range(10)]
    s = compute_realyield_score_for(_bdays(10), ys, "DFII10", AS_OF)
    assert s.method == "insufficient"
    assert s.score == 0
    assert s.delta_w is None
    assert s.latest_yield == pytest.approx(ys[-1])


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

def test_stale_flag_when_latest_old():
    n = 100
    end = AS_OF - timedelta(days=30)
    ys = [1.0 + 0.01 * i for i in range(n)]
    s = compute_realyield_score_for(_bdays(n, end=end), ys, "DFII10", AS_OF)
    assert s.stale is True


def test_fresh_not_stale():
    n = 100
    ys = [1.0 + 0.01 * i for i in range(n)]
    s = compute_realyield_score_for(_bdays(n), ys, "DFII10", AS_OF)
    assert s.stale is False


# ---------------------------------------------------------------------------
# Frame-level driver
# ---------------------------------------------------------------------------

def test_compute_from_frame_and_dedup():
    n = 300
    dates = _bdays(n)
    df = _df(dates, [1.0 + 0.02 * i for i in range(n)])
    dup = _df([dates[-1]], [df["yield_pct"].iloc[-1]])   # duplicate (series,date)
    out = compute_realyield_score(pd.concat([df, dup], ignore_index=True), as_of=AS_OF)
    assert isinstance(out, RealYieldScore)
    assert out.score == 2


def test_empty_frame_returns_none():
    assert compute_realyield_score(pd.DataFrame(columns=["date", "yield_pct"])) is None


def test_series_filter_selects_target():
    n = 100
    dates = _bdays(n)
    a = _df(dates, [1.0 + 0.02 * i for i in range(n)], series="DFII10")
    b = _df(dates, [5.0 - 0.02 * i for i in range(n)], series="OTHER")
    out = compute_realyield_score(pd.concat([a, b], ignore_index=True),
                                  series="DFII10", as_of=AS_OF)
    assert out.score == 2          # picks DFII10 (rising), not OTHER (falling)


def test_default_as_of_uses_latest_date():
    n = 100
    dates = _bdays(n, end=date(2024, 1, 15))
    out = compute_realyield_score(_df(dates, [1.0 + 0.01 * i for i in range(n)]))
    assert out.as_of == dates[-1]
    assert out.stale is False
