"""Tests for src.carry_compute — pure carry scoring, no I/O."""
from __future__ import annotations

from datetime import date, timedelta

from src.carry_compute import CarryRow, compute_carry, leg_configured

AS_OF = date(2026, 8, 27)


def _leg(rate_pct=None, verified=None, effective=None, source_name="", source_url=""):
    return {
        "rate_pct": rate_pct,
        "effective": effective,
        "verified": verified,
        "source_name": source_name,
        "source_url": source_url,
    }


def _rates(**legs) -> dict:
    return {"meta": {"stale_after_days": 45}, "rates": legs}


# ---------------------------------------------------------------------------
# Sign
# ---------------------------------------------------------------------------

def test_base_higher_than_quote_is_positive_carry():
    rates = _rates(
        USD=_leg(4.25, verified=AS_OF),
        JPY=_leg(0.25, verified=AS_OF),
    )
    rows = compute_carry(rates, ["USDJPY"], AS_OF)
    assert len(rows) == 1
    row = rows[0]
    assert row.available is True
    assert row.carry_pct == 4.0


def test_base_lower_than_quote_is_negative_carry():
    rates = _rates(
        EUR=_leg(2.00, verified=AS_OF),
        USD=_leg(4.25, verified=AS_OF),
    )
    rows = compute_carry(rates, ["EURUSD"], AS_OF)
    row = rows[0]
    assert row.available is True
    assert row.carry_pct == -2.25


# ---------------------------------------------------------------------------
# Missing leg
# ---------------------------------------------------------------------------

def test_missing_rate_pct_makes_row_unavailable():
    rates = _rates(
        EUR=_leg(None, verified=AS_OF),
        USD=_leg(4.25, verified=AS_OF),
    )
    rows = compute_carry(rates, ["EURUSD"], AS_OF)
    row = rows[0]
    assert row.available is False
    assert row.carry_pct is None
    # the present leg's raw rate still surfaces for display.
    assert row.quote_rate == 4.25
    assert row.base_rate is None


def test_missing_verified_makes_row_unavailable():
    rates = _rates(
        EUR=_leg(2.00, verified=None),
        USD=_leg(4.25, verified=AS_OF),
    )
    rows = compute_carry(rates, ["EURUSD"], AS_OF)
    row = rows[0]
    assert row.available is False
    assert row.carry_pct is None


# ---------------------------------------------------------------------------
# Zero is a real rate, not a missing one — guards against an `if rate_pct:`
# truthiness check anywhere in this module (0.0 is falsy in Python and would
# wrongly route a real zero-rate leg down the "missing" branch, which is
# exactly what CHF's policy rate does in practice).
# ---------------------------------------------------------------------------

def test_zero_rate_pct_is_available_not_missing():
    rates = _rates(
        USD=_leg(4.25, verified=AS_OF),
        CHF=_leg(0.00, verified=AS_OF),
    )
    rows = compute_carry(rates, ["USDCHF"], AS_OF)
    row = rows[0]
    assert row.available is True
    assert row.quote_rate == 0.0
    assert row.carry_pct == 4.25


def test_zero_rate_pct_leg_configured_is_true():
    assert leg_configured(_leg(0.00, verified=AS_OF)) is True


def test_unavailable_row_excluded_from_sort_order():
    rates = _rates(
        USD=_leg(4.25, verified=AS_OF),
        EUR=_leg(2.00, verified=AS_OF),
        GBP=_leg(None, verified=None),
        JPY=_leg(0.25, verified=AS_OF),
    )
    # GBPUSD has no GBP rate -> unavailable, must not land between the two
    # available/sorted rows even though its "sort key" (None) is undefined.
    rows = compute_carry(rates, ["EURUSD", "GBPUSD", "USDJPY"], AS_OF)
    symbols = [r.symbol for r in rows]
    assert symbols == ["USDJPY", "EURUSD", "GBPUSD"]
    assert rows[-1].available is False


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

def test_verified_older_than_threshold_is_stale_but_still_available():
    old = AS_OF - timedelta(days=46)  # > 45-day stale_after_days
    rates = _rates(
        USD=_leg(4.25, verified=old),
        JPY=_leg(0.25, verified=AS_OF),
    )
    rows = compute_carry(rates, ["USDJPY"], AS_OF)
    row = rows[0]
    assert row.available is True
    assert row.carry_pct is not None
    assert row.stale is True


def test_verified_within_threshold_is_not_stale():
    recent = AS_OF - timedelta(days=10)
    rates = _rates(
        USD=_leg(4.25, verified=recent),
        JPY=_leg(0.25, verified=AS_OF),
    )
    rows = compute_carry(rates, ["USDJPY"], AS_OF)
    assert rows[0].stale is False


# ---------------------------------------------------------------------------
# Sort order (full ranking, multiple available rows)
# ---------------------------------------------------------------------------

def test_sort_order_descending_with_unavailable_rows_last():
    rates = _rates(
        USD=_leg(4.25, verified=AS_OF),
        EUR=_leg(2.00, verified=AS_OF),
        JPY=_leg(0.25, verified=AS_OF),
        AUD=_leg(3.85, verified=AS_OF),
        GBP=_leg(None, verified=None),
    )
    pairs = ["EURUSD", "USDJPY", "AUDUSD", "GBPUSD", "EURJPY"]
    rows = compute_carry(rates, pairs, AS_OF)

    available = [r for r in rows if r.available]
    carries = [r.carry_pct for r in available]
    assert carries == sorted(carries, reverse=True)

    # Expected values: EURJPY=1.75, USDJPY=4.00, AUDUSD=-0.40, EURUSD=-2.25
    assert [r.symbol for r in available] == ["USDJPY", "EURJPY", "AUDUSD", "EURUSD"]

    # GBPUSD (missing GBP leg) is unavailable and pinned at the end.
    assert rows[-1].symbol == "GBPUSD"
    assert rows[-1].available is False


# ---------------------------------------------------------------------------
# leg_configured helper
# ---------------------------------------------------------------------------

def test_leg_configured_requires_both_fields():
    assert leg_configured(_leg(4.25, verified=AS_OF)) is True
    assert leg_configured(_leg(None, verified=AS_OF)) is False
    assert leg_configured(_leg(4.25, verified=None)) is False
    assert leg_configured(None) is False
    assert leg_configured({}) is False


def test_empty_yaml_all_null_yields_all_unavailable_in_input_order():
    rates = _rates(
        USD=_leg(), EUR=_leg(), GBP=_leg(), JPY=_leg(),
        AUD=_leg(), NZD=_leg(), CAD=_leg(), CHF=_leg(),
    )
    pairs = ["EURUSD", "GBPUSD", "USDJPY"]
    rows = compute_carry(rates, pairs, AS_OF)
    assert [r.symbol for r in rows] == pairs
    assert all(r.available is False and r.carry_pct is None for r in rows)
