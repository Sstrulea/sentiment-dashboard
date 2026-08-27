"""Pure carry (overnight interest-rate differential) scoring — no I/O, no network.

    compute_carry(rates: dict, pairs: list[str], as_of: date) -> list[CarryRow]

`rates` is the full parsed contents of data/policy_rates.yaml
(`{"meta": {"stale_after_days": ...}, "rates": {CCY: {rate_pct, effective,
verified, source_name, source_url}}}`). `pairs` is a list of 6-character FX
symbols ("EURUSD", "USDJPY", ...) — base = symbol[:3], quote = symbol[3:6];
this module never hardcodes a pair list itself.

carry_pct = base_rate - quote_rate, the LONG-the-pair perspective (long the
base, short the quote): positive means the long side earns the differential.

A leg missing `rate_pct` or `verified` makes the ROW unavailable
(available=False, carry_pct=None) — but base_rate/quote_rate still surface
whatever raw rate_pct each leg has, so a render layer can display a lone leg
even when the pair as a whole can't be scored. A leg's `verified` older than
meta.stale_after_days marks the row `stale=True` without excluding it from
scoring — it stays visible and sorted, just flagged.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

DEFAULT_STALE_AFTER_DAYS = 45


@dataclass
class CarryRow:
    symbol: str
    base: str
    quote: str
    base_rate: Optional[float]
    quote_rate: Optional[float]
    carry_pct: Optional[float]
    stale: bool
    available: bool


def leg_configured(leg: Optional[dict]) -> bool:
    """A leg counts as configured once both rate_pct and verified are set —
    the same bar compute_carry uses to mark a row's legs available, reused by
    the render layer for the "N/8 configured" badge."""
    if not leg:
        return False
    return leg.get("rate_pct") is not None and leg.get("verified") is not None


def _to_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v)).date()


def _leg_stale(leg: dict, as_of: date, stale_after_days: int) -> bool:
    verified = _to_date(leg.get("verified"))
    if verified is None:
        return False
    return (as_of - verified).days > stale_after_days


def _row_for_pair(symbol: str, rate_table: dict, as_of: date, stale_after_days: int) -> CarryRow:
    base, quote = symbol[:3], symbol[3:6]
    base_leg = rate_table.get(base) or {}
    quote_leg = rate_table.get(quote) or {}

    base_rate = base_leg.get("rate_pct")
    quote_rate = quote_leg.get("rate_pct")
    available = leg_configured(base_leg) and leg_configured(quote_leg)
    carry_pct = (base_rate - quote_rate) if available else None
    stale = _leg_stale(base_leg, as_of, stale_after_days) or _leg_stale(quote_leg, as_of, stale_after_days)

    return CarryRow(
        symbol=symbol, base=base, quote=quote,
        base_rate=None if base_rate is None else float(base_rate),
        quote_rate=None if quote_rate is None else float(quote_rate),
        carry_pct=carry_pct,
        stale=stale,
        available=available,
    )


def compute_carry(rates: dict, pairs: list[str], as_of: date) -> list[CarryRow]:
    """One CarryRow per symbol in `pairs`, sorted carry_pct descending.

    Rows with available=False (a leg missing rate_pct or verified) are
    excluded from the sort and appended at the end, in their original
    `pairs` order.
    """
    rates = rates or {}
    meta = rates.get("meta") or {}
    stale_after_days = int(meta.get("stale_after_days", DEFAULT_STALE_AFTER_DAYS))
    rate_table = rates.get("rates") or {}

    rows = [_row_for_pair(symbol, rate_table, as_of, stale_after_days) for symbol in pairs]

    available_rows = [r for r in rows if r.available]
    unavailable_rows = [r for r in rows if not r.available]
    available_rows.sort(key=lambda r: r.carry_pct, reverse=True)
    return available_rows + unavailable_rows
