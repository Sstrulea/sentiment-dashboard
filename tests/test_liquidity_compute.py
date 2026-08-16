"""Tests for src.liquidity_compute (band scorer) + the pure NL assembly. No I/O."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.liquidity_compute import compute_liquidity_score
from src.liquidity_fetch import assemble_net_liquidity, _print_report
from src.rate_sources import FredSeriesSource

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


def _res(vals, end=AS_OF):
    """reserves frame on business days ending at `end`."""
    return pd.DataFrame({"date": pd.to_datetime(pd.Series(_bdays(len(vals), end))),
                         "reserves": vals})


# ---------------------------------------------------------------------------
# Pure NL assembly: WALCL − TGA − RRP, weekly ffill, RRP-missing → 0
# ---------------------------------------------------------------------------

def test_assemble_net_liquidity_ffill_and_rrp_zero():
    # WALCL weekly (2 points), TGA daily-ish, RRP only later (missing early → 0).
    walcl = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-09"]),
                          "WALCL": [7000.0, 6900.0]})
    tga = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-06"]),
                        "WTREGEN": [800.0, 820.0]})
    # RRP input is in BILLIONS (FRED units): 0.1 bln → 100 mln after ×1000.
    rrp = pd.DataFrame({"date": pd.to_datetime(["2026-01-07"]), "RRPONTSYD": [0.1]})
    out = assemble_net_liquidity(walcl, tga, rrp)
    out = out.set_index(out["date"].dt.strftime("%Y-%m-%d"))
    # 2026-01-05 (Mon): WALCL ffill 7000, TGA ffill 800, RRP missing → 0 → NL 6200
    assert out.loc["2026-01-05", "net_liquidity"] == pytest.approx(7000 - 800 - 0)
    # 2026-01-06: TGA updates to 820, RRP still 0 → 7000-820 = 6180
    assert out.loc["2026-01-06", "net_liquidity"] == pytest.approx(7000 - 820 - 0)
    # 2026-01-08: WALCL still 7000 (next weekly is 01-09), TGA 820, RRP 0.1bln→100mln → 6080
    assert out.loc["2026-01-08", "net_liquidity"] == pytest.approx(7000 - 820 - 100)
    assert out.loc["2026-01-08", "rrp"] == pytest.approx(100.0)   # 0.1 bln stored as 100 mln
    # 2026-01-09: WALCL updates to 6900 → 6900-820-100 = 5980
    assert out.loc["2026-01-09", "net_liquidity"] == pytest.approx(6900 - 820 - 100)
    assert list(out.columns) == ["date", "net_liquidity", "walcl", "tga", "rrp", "source"]


def test_assemble_rrp_billions_to_millions():
    # Realistic 2022-scale check: WALCL/TGA in $ millions, RRP in $ billions.
    # 2022-06-ish: WALCL≈8_900_000 mln, TGA≈700_000 mln, RRP≈2_200 bln (=$2.2T).
    # Without conversion net would be ≈8_197_800 (RRP as 2_200 mln, ~noise);
    # with ×1000 conversion RRP=2_200_000 mln → net ≈ 6_000_000.
    walcl = pd.DataFrame({"date": pd.to_datetime(["2022-06-01"]), "WALCL": [8_900_000.0]})
    tga = pd.DataFrame({"date": pd.to_datetime(["2022-06-01"]), "WTREGEN": [700_000.0]})
    rrp = pd.DataFrame({"date": pd.to_datetime(["2022-06-01"]), "RRPONTSYD": [2_200.0]})
    out = assemble_net_liquidity(walcl, tga, rrp).iloc[0]
    assert out["rrp"] == pytest.approx(2_200_000.0)               # bln → mln
    assert out["net_liquidity"] == pytest.approx(6_000_000.0)     # not ~8_197_800


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
# reserves leg: carried alongside walcl/tga/rrp/net_liquidity; a failed RRP
# fetch blocks only net_liquidity, not reserves (reserves has no RRP dependency)
# ---------------------------------------------------------------------------

def test_assemble_carries_reserves_column():
    walcl = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-09"]),
                          "WALCL": [7000.0, 6900.0]})
    tga = pd.DataFrame({"date": pd.to_datetime(["2026-01-02"]), "WTREGEN": [800.0]})
    rrp = pd.DataFrame({"date": pd.to_datetime(["2026-01-02"]), "RRPONTSYD": [0.1]})
    reserves = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-09"]),
                             "WRBWFRBL": [3000.0, 2950.0]})
    out = assemble_net_liquidity(walcl, tga, rrp, reserves=reserves)
    assert out is not None
    assert "reserves" in out.columns
    assert list(out.columns) == ["date", "net_liquidity", "walcl", "tga", "rrp", "reserves", "source"]
    out_idx = out.set_index(out["date"].dt.strftime("%Y-%m-%d"))
    assert out_idx.loc["2026-01-05", "reserves"] == pytest.approx(3000.0)   # ffill
    assert out_idx.loc["2026-01-09", "reserves"] == pytest.approx(2950.0)


def test_rrp_fetch_failure_does_not_block_reserves():
    # WALCL/TGA/reserves all resolve fine; RRP fetch FAILED (None) and WALCL span
    # reaches the RRP era, so the degraded-build guard still blocks net_liquidity.
    # It must NOT also blank out reserves — reserves has no RRP dependency.
    walcl = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-09"]),
                          "WALCL": [7000.0, 6900.0]})
    tga = pd.DataFrame({"date": pd.to_datetime(["2026-01-02"]), "WTREGEN": [800.0]})
    reserves = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-09"]),
                             "WRBWFRBL": [3000.0, 2950.0]})
    out = assemble_net_liquidity(walcl, tga, None, reserves=reserves)
    assert out is not None                      # old behaviour: whole build was None
    assert "reserves" in out.columns
    assert out["reserves"].notna().all()
    assert out["net_liquidity"].isna().all()     # NL still correctly blocked


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
# Reserves scoring: value_col routing (reserves preferred, net_liquidity is
# the fallback when reserves is absent/unresolved).
# ---------------------------------------------------------------------------

def test_reserves_scored_not_net_liquidity():
    # Same frame carries BOTH columns with OPPOSITE trends: net_liquidity rising
    # fast (would score -2 if read), reserves falling fast (should score +2).
    # Default routing must score reserves, not net_liquidity.
    dates = pd.to_datetime(pd.Series(_bdays(40)))
    df = pd.DataFrame({
        "date": dates,
        "net_liquidity": _ramp(5000, 0.03),
        "reserves": _ramp(3000, -0.03),
    })
    s = compute_liquidity_score(df, as_of=AS_OF, **BANDS)
    assert s.roc == pytest.approx(-0.03, abs=1e-9)
    assert s.score == 2                          # reserves falling -> tightening -> +2
    # Explicit override still routes to net_liquidity on request.
    s_nl = compute_liquidity_score(df, as_of=AS_OF, value_col="net_liquidity", **BANDS)
    assert s_nl.roc == pytest.approx(0.03, abs=1e-9)
    assert s_nl.score == -2                      # net_liquidity rising -> easing -> -2


def test_reserves_falling_is_tightening_positive():
    s = compute_liquidity_score(_res(_ramp(3000, -0.03)), as_of=AS_OF, **BANDS)
    assert s.roc == pytest.approx(-0.03, abs=1e-9)
    assert s.score == 2


def test_reserves_rising_is_easing_negative():
    s = compute_liquidity_score(_res(_ramp(3000, 0.03)), as_of=AS_OF, **BANDS)
    assert s.roc == pytest.approx(0.03, abs=1e-9)
    assert s.score == -2


def test_reserves_stagnant_scores_zero():
    s = compute_liquidity_score(_res([3000.0] * 40), as_of=AS_OF, **BANDS)
    assert s.method == "band" and s.score == 0 and s.roc == pytest.approx(0.0)


def test_missing_reserves_falls_back_to_net_liquidity():
    # Old-style frame: no 'reserves' column at all (e.g. WRBWFRBL unresolved
    # upstream). The pillar must fall back to net_liquidity, not emit 0.
    s = compute_liquidity_score(_nl(_ramp(5000, -0.03)), as_of=AS_OF, **BANDS)
    assert s.method == "band"
    assert s.roc == pytest.approx(-0.03, abs=1e-9)
    assert s.score == 2


# ---------------------------------------------------------------------------
# Calibrated band constants (lock the Step-6 calibration into the module)
# ---------------------------------------------------------------------------

def test_module_bands_are_calibrated():
    # PHASE 1d (reserves, p68/p40 over 2013-09-23 -> 2026-08-12; see
    # docs/prereg/2026-08-15-liquidity-pillar-wresbal-prereg.md).
    from src.liquidity_compute import BAND_HI, BAND_LO
    assert BAND_HI == pytest.approx(0.0428)
    assert BAND_LO == pytest.approx(0.0221)


def test_default_bands_buckets_at_boundaries():
    # Using the MODULE defaults (no explicit bands): roc just under band_lo → 0,
    # between → ±1, at/above band_hi → ±2. Rising level → negative score.
    sub = compute_liquidity_score(_nl(_ramp(5000, 0.010)), as_of=AS_OF, smooth=1)   # < 0.0221
    mid = compute_liquidity_score(_nl(_ramp(5000, 0.030)), as_of=AS_OF, smooth=1)   # [lo,hi)
    big = compute_liquidity_score(_nl(_ramp(5000, 0.050)), as_of=AS_OF, smooth=1)   # ≥ hi
    assert sub.score == 0
    assert mid.score == -1
    assert big.score == -2


# ---------------------------------------------------------------------------
# _print_report must tolerate a net_liquidity-only parquet (no leg columns),
# else the scheduler's liquidity step crashes with a KeyError / non-zero exit
# whenever the committed 2-col parquet is in play (e.g. RRP down at FRED).
# ---------------------------------------------------------------------------

def test_print_report_tolerates_net_liquidity_only(capsys):
    df = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-05"]),
                       "net_liquidity": [6_000_000.0, 6_010_000.0]})
    _print_report(df)                                  # must NOT raise
    out = capsys.readouterr().out
    assert "leg breakdown unavailable" in out
    assert "WALCL" not in out


def test_print_report_shows_legs_when_present(capsys):
    df = pd.DataFrame({"date": pd.to_datetime(["2026-01-02"]),
                       "net_liquidity": [6_000_000.0], "walcl": [8_900_000.0],
                       "tga": [700_000.0], "rrp": [2_200_000.0], "source": ["fred"]})
    _print_report(df)
    out = capsys.readouterr().out
    assert "WALCL" in out and "TGA" in out and "RRP" in out


# ---------------------------------------------------------------------------
# FredSeriesSource: shared CSV parser + curl second-attempt fallback (no network).
# ---------------------------------------------------------------------------

FRED_CSV = "observation_date,DFII10\n2026-01-02,2.10\n2026-01-03,.\n2026-01-06,2.15\n"


def test_parse_fred_csv_drops_na_marker():
    out = FredSeriesSource("DFII10")._parse_fred_csv(FRED_CSV)
    assert len(out) == 2                               # FRED '.' NA row dropped
    assert list(out["value"]) == [2.10, 2.15]


def test_curl_fallback_used_when_requests_fails(monkeypatch):
    src = FredSeriesSource("DFII10")
    monkeypatch.setattr(src, "_get", lambda *a, **k: None)       # requests path fails
    monkeypatch.setattr(src, "_curl_csv", lambda url: FRED_CSV)  # curl succeeds
    out = src._fetch_series()
    assert out is not None and len(out) == 2
    assert list(out["value"]) == [2.10, 2.15]
