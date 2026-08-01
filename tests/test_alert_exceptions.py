"""fix/alert-noise-and-ff-archive — FAZA 1: alert suppression (Option A).

Covers the three requirements called out explicitly: loud expiry, orphan
detection, and the non-negotiable scoping guarantee (a new defect — either
a wholly different series, or a different indicator on the SAME currency
as an active exception — always alerts).
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.alert_exceptions import classify_stale_rows, find_orphaned_exceptions

AS_OF = pd.Timestamp("2026-08-01")

AUD_EXCEPTION = {
    "scope": "calendar", "currency": "AUD", "indicator": "core_cpi",
    "reason": "ABS moved AUD CPI to monthly cadence.",
    "doc": "docs/faza1-watchdog-per-instrument-implementation.md",
    "review_by": "2026-09-01",
}
USD_EXCEPTION = {
    "scope": "calendar", "currency": "USD", "indicator": "core_cpi",
    "reason": "Native Core CPI y/y unmapped.",
    "doc": "docs/calendar-freshness-per-currency.md",
    "review_by": "2026-08-20",
}
EXCEPTIONS = [AUD_EXCEPTION, USD_EXCEPTION]


def test_active_exception_suppresses_the_gate():
    stale = [{"currency": "AUD", "indicator_key": "core_cpi"}]
    out = classify_stale_rows(stale, EXCEPTIONS, AS_OF, scope="calendar")
    assert out[0]["gate"] == "suppressed"
    assert out[0]["exception_status"] == "active"
    assert "review by 2026-09-01" in out[0]["exception_note"]


def test_expiry_is_loud_not_silent():
    # as_of is PAST USD's review_by (2026-08-20)
    stale = [{"currency": "USD", "indicator_key": "core_cpi"}]
    out = classify_stale_rows(stale, EXCEPTIONS, pd.Timestamp("2026-08-21"), scope="calendar")
    row = out[0]
    assert row["gate"] == "alert"                      # alert returns
    assert row["exception_status"] == "expired"
    assert "EXPIRED" in row["exception_note"]
    assert "2026-08-20" in row["exception_note"]        # the expiry date is named
    assert "re-evaluate" in row["exception_note"].lower()


def test_expired_note_is_distinguishable_from_brand_new_defect():
    # A brand-new defect (no exception at all) must carry a DIFFERENT note
    # than an expired one, so the two are never confused in the report.
    stale = [
        {"currency": "USD", "indicator_key": "core_cpi"},   # expired
        {"currency": "GBP", "indicator_key": "ppi_yoy"},    # brand new, no exception
    ]
    out = classify_stale_rows(stale, EXCEPTIONS, pd.Timestamp("2026-08-21"), scope="calendar")
    expired_row = next(r for r in out if r["currency"] == "USD")
    new_row = next(r for r in out if r["currency"] == "GBP")
    assert expired_row["exception_status"] == "expired"
    assert new_row["exception_status"] == "none"
    assert expired_row["exception_note"] != new_row["exception_note"]
    assert new_row["exception_note"] is None


def test_orphaned_exception_is_flagged_removable():
    # AUD core_cpi has an exception but is no longer in the stale set -> orphan.
    stale = [{"currency": "USD", "indicator_key": "core_cpi"}]
    orphans = find_orphaned_exceptions(stale, EXCEPTIONS, scope="calendar")
    assert len(orphans) == 1
    assert orphans[0]["currency"] == "AUD"
    assert orphans[0]["indicator"] == "core_cpi"


def test_no_orphans_when_all_exceptions_still_apply():
    stale = [
        {"currency": "AUD", "indicator_key": "core_cpi"},
        {"currency": "USD", "indicator_key": "core_cpi"},
    ]
    orphans = find_orphaned_exceptions(stale, EXCEPTIONS, scope="calendar")
    assert orphans == []


# ---------------------------------------------------------------------------
# The non-negotiable criterion: a new defect always alerts, regardless of
# any active exception elsewhere.
# ---------------------------------------------------------------------------

def test_new_defect_on_a_completely_different_currency_still_alerts():
    stale = [
        {"currency": "AUD", "indicator_key": "core_cpi"},   # excepted
        {"currency": "GBP", "indicator_key": "ppi_yoy"},    # brand new, different currency entirely
    ]
    out = classify_stale_rows(stale, EXCEPTIONS, AS_OF, scope="calendar")
    aud_row = next(r for r in out if r["currency"] == "AUD")
    gbp_row = next(r for r in out if r["currency"] == "GBP")
    assert aud_row["gate"] == "suppressed"
    assert gbp_row["gate"] == "alert"     # must NOT be masked by AUD's exception


def test_new_defect_on_a_different_indicator_same_currency_still_alerts():
    # AUD core_cpi is excepted; AUD gdp_qoq becoming stale too must NOT be
    # swallowed by the currency-level exception -- exceptions are scoped to
    # (currency, indicator), never to the whole currency.
    stale = [
        {"currency": "AUD", "indicator_key": "core_cpi"},   # excepted
        {"currency": "AUD", "indicator_key": "gdp_qoq"},    # brand new, same currency, different indicator
    ]
    out = classify_stale_rows(stale, EXCEPTIONS, AS_OF, scope="calendar")
    core_row = next(r for r in out if r["indicator_key"] == "core_cpi")
    gdp_row = next(r for r in out if r["indicator_key"] == "gdp_qoq")
    assert core_row["gate"] == "suppressed"
    assert gdp_row["gate"] == "alert"     # must NOT be masked by the sibling exception


def test_empty_exceptions_list_alerts_on_everything():
    stale = [{"currency": "AUD", "indicator_key": "core_cpi"}]
    out = classify_stale_rows(stale, [], AS_OF, scope="calendar")
    assert out[0]["gate"] == "alert"
    assert out[0]["exception_status"] == "none"


def test_scope_mismatch_is_not_matched():
    # An exception recorded for a different scope (e.g. "price") must never
    # apply to a calendar row with the same currency/indicator strings.
    price_scoped = [{"scope": "price", "currency": "AUD", "indicator": "core_cpi",
                     "reason": "x", "doc": "y", "review_by": "2026-09-01"}]
    stale = [{"currency": "AUD", "indicator_key": "core_cpi"}]
    out = classify_stale_rows(stale, price_scoped, AS_OF, scope="calendar")
    assert out[0]["gate"] == "alert"
    assert out[0]["exception_status"] == "none"
