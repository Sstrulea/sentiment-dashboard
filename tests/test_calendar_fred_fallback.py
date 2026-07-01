"""Tests for the guarded FRED fallback — fills only when FRED continues the MT5
series within tolerance; refuses (and leaves the gap) otherwise. No network."""
from __future__ import annotations

import pandas as pd
import pytest

from src.calendar_fred_fallback import (
    CALENDAR_COLUMNS,
    Candidate,
    Target,
    apply_fred_fallback,
    evaluate_candidate,
)

Q = [pd.Timestamp("2025-01-01"), pd.Timestamp("2025-04-01"),
     pd.Timestamp("2025-07-01"), pd.Timestamp("2025-10-01")]
GAP = pd.Timestamp("2026-01-01")


def _mt5(vals):
    return {q: v for q, v in zip(Q, vals)}


# --- guard unit ------------------------------------------------------------

def test_evaluate_accepts_within_tolerance():
    mt5 = _mt5([0.5, 0.1, -0.5, 0.1])
    fred = _mt5([0.52, 0.13, -0.46, 0.18])  # all within 0.1pp
    ev = evaluate_candidate(mt5, fred)
    assert ev["accepted"] is True and ev["max_diff"] <= 0.1


def test_evaluate_refuses_on_one_breach():
    mt5 = _mt5([0.5, 0.1, -0.5, 0.1])
    fred = _mt5([0.81, 0.13, -0.46, 0.18])  # Q1 off by 0.31 (sporting-event case)
    ev = evaluate_candidate(mt5, fred)
    assert ev["accepted"] is False and ev["max_diff"] == pytest.approx(0.31, abs=1e-9)


def test_evaluate_refuses_on_thin_overlap():
    mt5 = {Q[0]: 0.5, Q[1]: 0.1}
    fred = {Q[0]: 0.5, Q[1]: 0.1}
    assert evaluate_candidate(mt5, fred)["accepted"] is False  # <4 overlap


# --- engine: fill vs refuse over a tmp parquet -----------------------------

def _parquet(tmp_path, gap_actual=None):
    rows = []
    for q, a in zip(Q, [0.5, 0.1, -0.5, 0.1]):
        rows.append({"release_dt": q + pd.Timedelta(days=60), "country": "Switzerland",
                     "currency": "CHF", "indicator_key": "gdp_qoq", "actual": a,
                     "consensus": a - 0.1, "previous": 0.0, "unit": "1",
                     "event_raw": "GDP q/q", "period": q.strftime("%Y.%m.%d"), "source": "mt5"})
    # scheduled gap row: actual NaN
    rows.append({"release_dt": GAP + pd.Timedelta(days=60), "country": "Switzerland",
                 "currency": "CHF", "indicator_key": "gdp_qoq", "actual": gap_actual,
                 "consensus": 0.3, "previous": 0.1, "unit": "1",
                 "event_raw": "GDP q/q", "period": GAP.strftime("%Y.%m.%d"), "source": "mt5"})
    p = tmp_path / "economic_calendar.parquet"
    pd.DataFrame(rows, columns=CALENDAR_COLUMNS).to_parquet(p, index=False)
    return p


def _fred_frame(values_by_period):
    return pd.DataFrame({"date": [p for p in values_by_period],
                         "value": [values_by_period[p] for p in values_by_period]})


def test_engine_fills_gap_when_series_matches(tmp_path):
    pq = _parquet(tmp_path, gap_actual=None)
    # FRED matches the 4 published quarters within tol AND has the gap quarter.
    fred = _fred_frame({Q[0]: 0.52, Q[1]: 0.13, Q[2]: -0.46, Q[3]: 0.18, GAP: 0.66})
    tgt = [Target("CHF", "gdp_qoq", [Candidate("FAKE", "growth_as_is")])]
    rep = apply_fred_fallback(parquet_path=pq, targets=tgt, fetcher=lambda sid: fred)
    assert rep["filled"] == 1
    out = pd.read_parquet(pq)
    g = out[(out.indicator_key == "gdp_qoq") & (out["period"] == GAP.strftime("%Y.%m.%d"))]
    assert g.iloc[0]["source"] == "fred"
    assert g.iloc[0]["actual"] == pytest.approx(0.66)
    assert g.iloc[0]["consensus"] == pytest.approx(0.3)  # scheduled forecast kept


def test_engine_refuses_when_series_mismatches(tmp_path):
    pq = _parquet(tmp_path, gap_actual=None)
    before = pd.read_parquet(pq)
    # Unadjusted-style series: Q1 off by 0.31 → guard refuses, gap stays NaN.
    fred = _fred_frame({Q[0]: 0.81, Q[1]: 0.13, Q[2]: -0.46, Q[3]: 0.18, GAP: 0.66})
    tgt = [Target("CHF", "gdp_qoq", [Candidate("FAKE", "growth_as_is")])]
    rep = apply_fred_fallback(parquet_path=pq, targets=tgt, fetcher=lambda sid: fred)
    assert rep["filled"] == 0
    after = pd.read_parquet(pq)
    assert after.equals(before)  # parquet untouched (accept-degradation)


def test_engine_never_overrides_present_mt5_actual(tmp_path):
    # If the gap period already has an MT5 actual, FRED must not touch it.
    pq = _parquet(tmp_path, gap_actual=0.9)  # gap now published by MT5
    fred = _fred_frame({Q[0]: 0.52, Q[1]: 0.13, Q[2]: -0.46, Q[3]: 0.18, GAP: 0.66})
    tgt = [Target("CHF", "gdp_qoq", [Candidate("FAKE", "growth_as_is")])]
    rep = apply_fred_fallback(parquet_path=pq, targets=tgt, fetcher=lambda sid: fred)
    assert rep["filled"] == 0
    out = pd.read_parquet(pq)
    g = out[out["period"] == GAP.strftime("%Y.%m.%d")]
    assert g.iloc[0]["actual"] == pytest.approx(0.9) and g.iloc[0]["source"] == "mt5"
