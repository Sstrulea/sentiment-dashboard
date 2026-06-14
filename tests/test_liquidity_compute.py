"""Tests for src.liquidity_compute (band scorer) + the pure NL assembly. No I/O."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.liquidity_compute import compute_liquidity_score
from src.liquidity_fetch import assemble_net_liquidity

AS_OF = date(2026, 6, 1)
W = 21


def _bdays(n, end=AS_OF):
    return [d.date() for d in pd.bdate_range(end=pd.Timestamp(end), periods=n)]


def _nl(vals, end=AS_OF):
    """net_liquidity frame on business days ending at `end`."""
    return pd.DataFrame({"date": pd.to_datetime(pd.Series(_bdays(len(vals), end))),
                         "net_liquidity": vals})


def _ramp(base, roc, n=40):
    """Flat at `base`, then a linear ramp over the last W rows to base*(1+roc),
    so the 21-row ROC (with smooth=1) equals exactly `roc`."""
    head = [base] * (n - W)
    tail = [base * (1 + roc * i / W) for i in range(1, W + 1)]
    return head + tail


# ---------------------------------------------------------------------------
# Pure NL assembly: WALCL − TGA − RRP, weekly ffill, RRP-missing → 0
# ---------------------------------------------------------------------------

def test_assemble_net_liquidity_ffill_and_rrp_zero():
    # WALCL weekly (2 points), TGA daily-ish, RRP only later (missing early → 0).
    walcl = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-09"]),
                          "WALCL": [7000.0, 6900.0]})
    tga = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-06"]),
                        "WTREGEN": [800.0, 820.0]})
    rrp = pd.DataFrame({"date": pd.to_datetime(["2026-01-07"]), "RRPONTSYD": [100.0]})
    out = assemble_net_liquidity(walcl, tga, rrp)
    out = out.set_index(out["date"].dt.strftime("%Y-%m-%d"))
    # 2026-01-05 (Mon): WALCL ffill 7000, TGA ffill 800, RRP missing → 0 → NL 6200
    assert out.loc["2026-01-05", "net_liquidity"] == pytest.approx(7000 - 800 - 0)
    # 2026-01-06: TGA updates to 820, RRP still 0 → 7000-820 = 6180
    assert out.loc["2026-01-06", "net_liquidity"] == pytest.approx(7000 - 820 - 0)
    # 2026-01-08: WALCL still 7000 (next weekly is 01-09), TGA 820, RRP 100 → 6080
    assert out.loc["2026-01-08", "net_liquidity"] == pytest.approx(7000 - 820 - 100)
    # 2026-01-09: WALCL updates to 6900 → 6900-820-100 = 5980
    assert out.loc["2026-01-09", "net_liquidity"] == pytest.approx(6900 - 820 - 100)
    assert list(out.columns) == ["date", "net_liquidity", "walcl", "tga", "rrp", "source"]


def test_assemble_requires_walcl():
    assert assemble_net_liquidity(None, None, None) is None


def test_assemble_rejects_degraded_rrp_in_rrp_era():
    # WALCL/TGA reach 2026 but RRP fetch FAILED (None). Treating RRP=0 over the
    # RRP era would clobber good history → guard returns None (keep parquet).
    walcl = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-09"]),
                          "WALCL": [7000.0, 6900.0]})
    tga = pd.DataFrame({"date": pd.to_datetime(["2026-01-02"]), "WTREGEN": [800.0]})
    assert assemble_net_liquidity(walcl, tga, None) is None
    assert assemble_net_liquidity(walcl, tga, pd.DataFrame()) is None


def test_assemble_allows_missing_rrp_pre_facility():
    # Span entirely before 2013-09 → RRP genuinely did not exist → RRP=0 is correct.
    walcl = pd.DataFrame({"date": pd.to_datetime(["2010-01-04", "2010-01-11"]),
                          "WALCL": [2000.0, 2010.0]})
    tga = pd.DataFrame({"date": pd.to_datetime(["2010-01-04"]), "WTREGEN": [100.0]})
    out = assemble_net_liquidity(walcl, tga, None)
    assert out is not None and len(out)
    assert (out["rrp"] == 0.0).all()
    assert out["net_liquidity"].iloc[0] == pytest.approx(2000.0 - 100.0 - 0.0)


# ---------------------------------------------------------------------------
# Band scorer (raw: NL falling = + tightening; rising = − easing)
# ---------------------------------------------------------------------------

BANDS = dict(band_hi=0.0201, band_lo=0.0098, smooth=1)   # calibrated bands


def test_stagnant_scores_zero():
    s = compute_liquidity_score(_nl([5000.0] * 40), as_of=AS_OF, **BANDS)
    assert s.method == "band" and s.score == 0 and s.roc == pytest.approx(0.0)


def test_rising_fast_is_easing_negative_2():
    s = compute_liquidity_score(_nl(_ramp(5000, 0.03)), as_of=AS_OF, **BANDS)
    assert s.roc == pytest.approx(0.03, abs=1e-9)
    assert s.score == -2            # NL rising fast → easing → −2


def test_rising_mild_is_minus_1():
    s = compute_liquidity_score(_nl(_ramp(5000, 0.015)), as_of=AS_OF, **BANDS)
    assert s.score == -1


def test_falling_is_tightening_positive():
    s = compute_liquidity_score(_nl(_ramp(5000, -0.03)), as_of=AS_OF, **BANDS)
    assert s.roc == pytest.approx(-0.03, abs=1e-9)
    assert s.score == 2             # NL falling → tightening → +2


def test_insufficient_history():
    s = compute_liquidity_score(_nl([5000.0] * 10), as_of=AS_OF, **BANDS)
    assert s.method == "insufficient" and s.score == 0 and s.roc is None


def test_future_rows_ignored():
    base = _nl(_ramp(5000, 0.03), end=AS_OF)
    fut = pd.DataFrame({"date": pd.to_datetime([AS_OF + timedelta(days=k) for k in range(1, 30)]),
                        "net_liquidity": [1.0] * 29})   # would crash the ROC if used
    full = pd.concat([base, fut], ignore_index=True)
    cut = compute_liquidity_score(full, as_of=AS_OF, **BANDS)
    ref = compute_liquidity_score(base, as_of=AS_OF, **BANDS)
    assert cut.score == ref.score == -2
    assert cut.roc == pytest.approx(ref.roc)


def test_smoothing_damps_single_day_spike():
    # Flat 5000 with one 1-day +50% spike at the end: smooth=5 damps it so the
    # ROC stays small (no false ±2), unlike smooth=1.
    vals = [5000.0] * 39 + [7500.0]
    spiky = compute_liquidity_score(_nl(vals), as_of=AS_OF, band_hi=0.0201, band_lo=0.0098, smooth=1)
    smoothed = compute_liquidity_score(_nl(vals), as_of=AS_OF, band_hi=0.0201, band_lo=0.0098, smooth=5)
    assert abs(smoothed.roc) < abs(spiky.roc)


def test_stale_flag_when_old():
    s = compute_liquidity_score(_nl(_ramp(5000, 0.03), end=AS_OF - timedelta(days=30)),
                                as_of=AS_OF, **BANDS)
    assert s.stale is True


# ---------------------------------------------------------------------------
# Calibrated band constants (lock the Step-6 calibration into the module)
# ---------------------------------------------------------------------------

def test_module_bands_are_calibrated():
    from src.liquidity_compute import BAND_HI, BAND_LO
    assert BAND_HI == pytest.approx(0.0201)
    assert BAND_LO == pytest.approx(0.0098)


def test_default_bands_buckets_at_boundaries():
    # Using the MODULE defaults (no explicit bands): roc just under band_lo → 0,
    # between → ±1, at/above band_hi → ±2. NL rising → negative score.
    sub = compute_liquidity_score(_nl(_ramp(5000, 0.007)), as_of=AS_OF, smooth=1)   # < 0.0098
    mid = compute_liquidity_score(_nl(_ramp(5000, 0.015)), as_of=AS_OF, smooth=1)   # [lo,hi)
    big = compute_liquidity_score(_nl(_ramp(5000, 0.03)), as_of=AS_OF, smooth=1)    # ≥ hi
    assert sub.score == 0
    assert mid.score == -1
    assert big.score == -2
