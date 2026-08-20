"""FAZA 1A — src/data_integrity.py unit tests.

Fixtures are the EXACT cases confirmed by hand on the real parquet
(measure/history-catalog-audit branch, FAZA 0.6): USD CPI y/y 2023-01-11 vs
2024-01-11, GBP CPI y/y 2023-01-17 vs 2024-01-18 (adjacent, not identical
day), CAD Employment Change 2023-01-05 vs 2024-01-05/06, plus the two
sanity-critical LEVEL-zero cases (EUR ECB corrupted zero that MUST be
flagged, SNB 2025-06 move-to-zero that must NOT be). Inline only — no new
files under data/.
"""
from __future__ import annotations

import pandas as pd

from src.data_integrity import (
    build_quarantine_proposal, detect_ghost_rows, detect_implausible_zeros,
)


def _row(ccy, key, cid, name_raw, dt, actual, forecast, previous):
    return {"currency": ccy, "indicator_key": key, "canonical_id": cid,
            "name_raw": name_raw, "release_dt": pd.Timestamp(dt),
            "actual": actual, "forecast": forecast, "previous": previous}


# ---------------------------------------------------------------------------
# Class 1 — ghost rows
# ---------------------------------------------------------------------------

def test_usd_cpi_ghost_confirmed():
    """The exact contested FAZA 0.5 pair: 2023-01-11 is the ghost (December
    2023's real print, wrong year), 2024-01-11 is real; 2023-01-12 is the
    genuine January-2023 print and must NOT be flagged."""
    rows = [
        _row("USD", "cpi_yoy", "usd_cpi", "CPI y/y", "2023-01-11 13:30:00", 3.4, 3.2, 3.1),
        _row("USD", "cpi_yoy", "usd_cpi", "CPI y/y", "2023-01-12 13:30:00", 6.5, 6.5, 7.1),
        _row("USD", "cpi_yoy", "usd_cpi", "CPI y/y", "2023-02-14 13:30:00", 6.4, 6.2, 6.5),
        _row("USD", "cpi_yoy", "usd_cpi", "CPI y/y", "2024-01-11 13:30:00", 3.4, 3.2, 3.1),
        _row("USD", "cpi_yoy", "usd_cpi", "CPI y/y", "2024-02-13 13:30:00", 3.1, 2.9, 3.4),
    ]
    df = pd.DataFrame(rows)
    ghosts = detect_ghost_rows(df)
    assert len(ghosts) == 1
    assert ghosts.iloc[0]["release_dt"] == pd.Timestamp("2023-01-11 13:30:00")
    assert ghosts.iloc[0]["paired_with_dt"] == pd.Timestamp("2024-01-11 13:30:00")
    # the genuine 2023-01-12 print is never flagged
    assert pd.Timestamp("2023-01-12 13:30:00") not in set(ghosts["release_dt"])


def test_gbp_cpi_ghost_adjacent_day():
    rows = [
        _row("GBP", "cpi_yoy", "gbp_cpi", "CPI y/y", "2023-01-17 07:00:00", 4.0, 3.8, 3.9),
        _row("GBP", "cpi_yoy", "gbp_cpi", "CPI y/y", "2023-01-18 07:00:00", 10.5, 10.5, 10.7),
        _row("GBP", "cpi_yoy", "gbp_cpi", "CPI y/y", "2023-02-15 07:00:00", 10.1, 10.3, 10.5),
        _row("GBP", "cpi_yoy", "gbp_cpi", "CPI y/y", "2024-01-17 07:00:00", 4.0, 3.8, 3.9),
        _row("GBP", "cpi_yoy", "gbp_cpi", "CPI y/y", "2024-02-16 07:00:00", 4.1, 4.0, 4.2),
    ]
    df = pd.DataFrame(rows)
    ghosts = detect_ghost_rows(df)
    assert len(ghosts) == 1
    assert ghosts.iloc[0]["release_dt"] == pd.Timestamp("2023-01-17 07:00:00")


def test_cad_employment_ghost():
    rows = [
        _row("CAD", "employment_change", "cad_employment_change", "Employment Change",
            "2023-01-05 13:30:00", 0.1, 12.2, 24.9),
        _row("CAD", "employment_change", "cad_employment_change", "Employment Change",
            "2023-01-06 13:30:00", 104.0, 5.5, 10.1),
        _row("CAD", "employment_change", "cad_employment_change", "Employment Change",
            "2023-02-10 13:30:00", 150.0, 15.0, 104.0),
        _row("CAD", "employment_change", "cad_employment_change", "Employment Change",
            "2024-01-05 13:30:00", 0.1, 12.2, 24.9),
        _row("CAD", "employment_change", "cad_employment_change", "Employment Change",
            "2024-02-09 13:30:00", 41.4, 21.6, 34.7),
    ]
    df = pd.DataFrame(rows)
    ghosts = detect_ghost_rows(df)
    assert len(ghosts) == 1
    assert ghosts.iloc[0]["release_dt"] == pd.Timestamp("2023-01-05 13:30:00")


def test_stable_series_not_flagged_without_cadence_violation():
    """A rate held constant for a full year (CAD Overnight Rate style) repeats
    its exact triple 365 days later WITHOUT any other nearby print — condition
    (a) alone would false-positive; the intersection must not flag it."""
    rows = [
        _row("CAD", "interest_rate_decision", "cad_boc", "Overnight Rate",
            "2023-01-24 14:45:00", 5.0, 5.0, 5.0),
        _row("CAD", "interest_rate_decision", "cad_boc", "Overnight Rate",
            "2023-03-08 14:45:00", 5.0, 5.0, 5.0),
        _row("CAD", "interest_rate_decision", "cad_boc", "Overnight Rate",
            "2023-04-12 14:45:00", 5.0, 5.0, 5.0),
        _row("CAD", "interest_rate_decision", "cad_boc", "Overnight Rate",
            "2024-01-24 14:45:00", 5.0, 5.0, 5.0),
        _row("CAD", "interest_rate_decision", "cad_boc", "Overnight Rate",
            "2024-03-06 14:45:00", 5.0, 5.0, 5.0),
    ]
    df = pd.DataFrame(rows)
    ghosts = detect_ghost_rows(df)
    assert len(ghosts) == 0


def test_dst_duplicate_alone_not_flagged():
    """Condition (b) alone (a same-day-ish ±1h duplicate, unrelated to the
    wrong-year bug) must not be flagged: no 365-day identical-triple partner
    exists for either row."""
    rows = [
        _row("AUD", "import_prices", "aud_import_prices", "Import Prices q/q",
            "2026-04-30 00:30:00", 0.0, 0.6, 0.8),
        _row("AUD", "import_prices", "aud_import_prices", "Import Prices q/q",
            "2026-04-30 01:30:00", 0.0, 0.6, 0.8),
    ]
    df = pd.DataFrame(rows)
    ghosts = detect_ghost_rows(df)
    assert len(ghosts) == 0


# ---------------------------------------------------------------------------
# Class 2 — implausible zeros on LEVEL series
# ---------------------------------------------------------------------------

def test_eur_ecb_corrupted_zero_flagged():
    rows = [
        _row("EUR", "interest_rate_decision", "eur_ecb", "Main Refinancing Rate",
            "2024-01-25 13:15:00", 4.5, 4.5, 4.5),
        _row("EUR", "interest_rate_decision", "eur_ecb", "Main Refinancing Rate",
            "2024-01-30 13:15:00", 0.0, 0.0, 0.0),
        _row("EUR", "interest_rate_decision", "eur_ecb", "Main Refinancing Rate",
            "2024-03-07 13:15:00", 4.5, 4.5, 4.5),
    ]
    df = pd.DataFrame(rows)
    zeros = detect_implausible_zeros(df)
    assert len(zeros) == 1
    assert zeros.iloc[0]["release_dt"] == pd.Timestamp("2024-01-30 13:15:00")


def test_jpy_consecutive_corrupted_zeros_all_flagged():
    """A RUN of consecutive corrupted zeros (not just an isolated one) must
    all be caught — each looks past its zero neighbors to the nearest REAL
    anchor on each side. Real-history anchor points (BOJ's actual 2023-2025
    hiking path) so the series' typical step is realistically small (~0.1-
    0.25), not degenerate like a 2-point fixture would give."""
    rows = [
        _row("JPY", "interest_rate_decision", "jpy_boj", "BOJ Policy Rate",
            "2024-03-19 03:36:00", 0.10, -0.10, -0.10),
        _row("JPY", "interest_rate_decision", "jpy_boj", "BOJ Policy Rate",
            "2024-07-31 03:57:00", 0.25, 0.10, 0.10),
        _row("JPY", "interest_rate_decision", "jpy_boj", "BOJ Policy Rate",
            "2024-10-31 03:28:00", 0.25, 0.25, 0.25),
        _row("JPY", "interest_rate_decision", "jpy_boj", "BOJ Policy Rate",
            "2024-12-17 22:00:00", 0.0, 0.25, 0.25),
        _row("JPY", "interest_rate_decision", "jpy_boj", "BOJ Policy Rate",
            "2025-01-22 22:00:00", 0.0, 0.50, 0.25),
        _row("JPY", "interest_rate_decision", "jpy_boj", "BOJ Policy Rate",
            "2025-03-17 21:00:00", 0.0, 0.50, 0.50),
        _row("JPY", "interest_rate_decision", "jpy_boj", "BOJ Policy Rate",
            "2025-10-30 03:15:00", 0.50, 0.50, 0.50),
    ]
    df = pd.DataFrame(rows)
    zeros = detect_implausible_zeros(df)
    assert len(zeros) == 3
    flagged_dates = set(zeros["release_dt"])
    assert pd.Timestamp("2024-12-17 22:00:00") in flagged_dates
    assert pd.Timestamp("2025-01-22 22:00:00") in flagged_dates
    assert pd.Timestamp("2025-03-17 21:00:00") in flagged_dates


def test_snb_genuine_zero_lower_bound_not_flagged():
    """SNB cutting to 0.00% in 2025-06 with NO later print yet must NOT be
    flagged — there is no non-zero print after it to compare against, by
    construction, not a special case for this one series."""
    rows = [
        _row("CHF", "interest_rate_decision", "chf_snb", "SNB Policy Rate",
            "2024-09-26 07:30:00", 1.25, 1.25, 1.25),
        _row("CHF", "interest_rate_decision", "chf_snb", "SNB Policy Rate",
            "2024-12-12 07:30:00", 0.50, 0.50, 1.25),
        _row("CHF", "interest_rate_decision", "chf_snb", "SNB Policy Rate",
            "2025-03-20 07:30:00", 0.25, 0.25, 0.50),
        _row("CHF", "interest_rate_decision", "chf_snb", "SNB Policy Rate",
            "2025-06-19 07:30:00", 0.0, 0.0, 0.25),
    ]
    df = pd.DataFrame(rows)
    zeros = detect_implausible_zeros(df)
    assert len(zeros) == 0


def test_variation_series_zero_never_flagged():
    """A 0.0 on a VARIATION series (m/m/q/q/y/y) is legitimate and routine —
    detect_implausible_zeros must never touch it regardless of neighbors,
    since it is restricted to LEVEL_KEYS only."""
    rows = [
        _row("CHF", "cpi_yoy", "chf_cpi", "CPI m/m", "2024-01-06 07:30:00", 0.3, 0.3, 0.3),
        _row("CHF", "cpi_yoy", "chf_cpi", "CPI m/m", "2024-02-06 07:30:00", 0.0, 0.2, 0.2),
        _row("CHF", "cpi_yoy", "chf_cpi", "CPI m/m", "2024-03-06 07:30:00", 0.4, 0.3, 0.0),
    ]
    df = pd.DataFrame(rows)
    zeros = detect_implausible_zeros(df)
    assert len(zeros) == 0


# ---------------------------------------------------------------------------
# build_quarantine_proposal
# ---------------------------------------------------------------------------

def test_build_quarantine_proposal_unions_and_dedupes():
    ghost_df = detect_ghost_rows(pd.DataFrame([
        _row("USD", "cpi_yoy", "usd_cpi", "CPI y/y", "2023-01-11 13:30:00", 3.4, 3.2, 3.1),
        _row("USD", "cpi_yoy", "usd_cpi", "CPI y/y", "2023-01-12 13:30:00", 6.5, 6.5, 7.1),
        _row("USD", "cpi_yoy", "usd_cpi", "CPI y/y", "2024-01-11 13:30:00", 3.4, 3.2, 3.1),
    ]))
    zero_df = detect_implausible_zeros(pd.DataFrame([
        _row("EUR", "interest_rate_decision", "eur_ecb", "Main Refinancing Rate",
            "2024-01-25 13:15:00", 4.5, 4.5, 4.5),
        _row("EUR", "interest_rate_decision", "eur_ecb", "Main Refinancing Rate",
            "2024-01-30 13:15:00", 0.0, 0.0, 0.0),
        _row("EUR", "interest_rate_decision", "eur_ecb", "Main Refinancing Rate",
            "2024-03-07 13:15:00", 4.5, 4.5, 4.5),
    ]))
    proposal = build_quarantine_proposal(ghost_df, zero_df)
    assert len(proposal) == 2
    assert set(proposal["reason"]) == {"ghost_wrong_year", "implausible_zero_level"}
    assert list(proposal.columns) == ["currency", "indicator_key", "name_raw",
                                      "release_dt", "reason", "paired_with_dt",
                                      "confidence", "detail"]


def test_build_quarantine_proposal_empty_inputs():
    empty = pd.DataFrame(columns=["currency", "indicator_key", "name_raw", "release_dt",
                                  "reason", "paired_with_dt", "confidence", "detail"])
    proposal = build_quarantine_proposal(empty, empty)
    assert proposal.empty
