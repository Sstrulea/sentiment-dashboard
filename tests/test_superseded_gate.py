"""STRAT 2 — expected-next-release gate + the actual-only invariant.

A cell scores ONLY on a real `actual`; forecast/previous never substitute. An older
print is SUPERSEDED only when a newer release is genuinely MISSED — its scheduled
date is OVERDUE by more than a normal reporting lag yet has no actual. A release
merely due today/recently (actual pending) does NOT flag the current reading.
"""
from __future__ import annotations

import pandas as pd

from src.economic_compute import compute_indicator_score

AS_OF = pd.Timestamp("2026-07-01")
COLS = ["currency", "indicator_key", "release_dt", "actual", "consensus", "period"]
CFG = {"frequency": "monthly", "direction": 1}
DEFAULTS = {
    "surprise_window_k": 12, "z_buckets": [1.0, 0.33], "pct_buckets": [0.10, 0.02],
    "fallback_min_prints": 6, "max_age_days": 120,
    "max_age_by_frequency": {"weekly": 14, "monthly": 45, "quarterly": 110},
}


def _df(records):
    return pd.DataFrame(
        [{"currency": "USD", "indicator_key": "x", "release_dt": pd.Timestamp(r[0]),
          "actual": r[1], "consensus": r[2], "period": r[3]} for r in records],
        columns=COLS,
    )


# recent, in-window monthly history (latest released 06-05, ~26d before AS_OF).
PUBLISHED = [
    ("2026-04-05", 1.0, 0.9, "2026.03.01"),
    ("2026-05-05", 1.1, 1.0, "2026.04.01"),
    ("2026-06-05", 0.8, 0.9, "2026.05.01"),
]
# older history (latest released 05-05, ~57d before AS_OF → beyond the 45d window).
OLD = [
    ("2026-03-05", 1.0, 0.9, "2026.02.01"),
    ("2026-04-05", 1.1, 1.0, "2026.03.01"),
    ("2026-05-05", 0.8, 0.9, "2026.04.01"),
]


def test_superseded_only_when_newer_release_is_clearly_overdue():
    # Next-period (May) release dated 06-05 → 26d overdue (> monthly grace 20) and
    # no actual → the served (April) print is a MISSED latest.
    df = _df(OLD + [("2026-06-05", None, 0.7, "2026.05.01")])
    r = compute_indicator_score(df, CFG, DEFAULTS, AS_OF, allow_stale=True, currency="USD")
    assert r["superseded_missing"] is True and r["stale"] is True
    assert r["actual"] == 0.8  # still the last REAL actual, never the NaN/forecast


def test_not_superseded_when_next_release_just_due():
    # In-window (served 06-05, 26d) with the next release due only ~12d ago →
    # normal reporting lag, actual pending → NOT superseded, stays FRESH.
    df = _df(PUBLISHED + [("2026-06-19", None, 0.7, "2026.06.01")])
    r = compute_indicator_score(df, CFG, DEFAULTS, AS_OF, allow_stale=True, currency="USD")
    assert r["superseded_missing"] is False
    assert r["stale"] is False


def test_monthly_at_40d_without_missed_release_is_fresh():
    # A monthly cell ~40d old (within the 45d window) with NO overdue scheduled row
    # must stay FRESH — not superseded (rule 2 needs a missed release), not stale.
    df = _df([
        ("2026-03-22", 1.0, 0.9, "2026.02.01"),
        ("2026-04-22", 1.1, 1.0, "2026.03.01"),
        ("2026-05-22", 0.8, 0.9, "2026.04.01"),  # 40d before AS_OF
    ])
    r = compute_indicator_score(df, CFG, DEFAULTS, AS_OF, allow_stale=True, currency="USD")
    assert r["superseded_missing"] is False
    assert r["stale"] is False


def test_not_superseded_when_next_release_is_in_the_future():
    df = _df(PUBLISHED + [("2026-07-05", None, 0.7, "2026.06.01")])
    r = compute_indicator_score(df, CFG, DEFAULTS, AS_OF, allow_stale=True, currency="USD")
    assert r["superseded_missing"] is False


def test_no_scheduled_row_is_not_superseded_only_window_stale():
    # No scheduled next row; served far past the window → stale by cadence window,
    # but NOT superseded (there is no missed scheduled release; the old rule-3 blind
    # cadence check no longer excludes).
    df = _df([
        ("2026-02-05", 1.0, 0.9, "2026.01.01"),
        ("2026-03-05", 1.1, 1.0, "2026.02.01"),
        ("2026-04-20", 0.8, 0.9, "2026.03.01"),  # 72d before AS_OF
    ])
    r = compute_indicator_score(df, CFG, DEFAULTS, AS_OF, allow_stale=True, currency="USD")
    assert r["stale"] is True                # window-stale
    assert r["superseded_missing"] is False  # but NOT superseded


# --- actual-only invariant --------------------------------------------------

def test_actual_only_never_substitutes_forecast_or_previous():
    df = _df(PUBLISHED + [("2026-06-28", None, 5.0, "2026.06.01")])
    r = compute_indicator_score(df, CFG, DEFAULTS, AS_OF, allow_stale=True, currency="USD")
    assert r["actual"] == 0.8
    assert r["consensus"] != 5.0


def test_no_actual_anywhere_returns_none():
    df = _df([("2026-05-05", None, 1.0, "2026.04.01"),
              ("2026-06-05", None, 0.9, "2026.05.01")])
    assert compute_indicator_score(df, CFG, DEFAULTS, AS_OF, allow_stale=True) is None
