"""Synthetic contexts for the 1B-2 engine tests: a made-up currency `XXX` whose policy path, official benchmark and market
quotes are built from a known rate path, so every implied value has an exact expected answer."""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Optional

from src.cb_calendar import weekends_only
from src.cb_compute.decisions import SeriesView
from src.cb_compute.engine import Context, MarketIndex, Meeting

D = date
CUR = "XXX"
BENCH, POL = "b:ON", "b:POL"


def bdays(start: date, end: date) -> list:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def rate_on(path: list, d: date) -> float:
    """Step function [(from_date, rate)] (ascending) evaluated on `d`."""
    cur = path[0][1]
    for f, r in path:
        if f <= d:
            cur = r
    return cur


def month_avg(path: list, y: int, m: int) -> float:
    n = calendar.monthrange(y, m)[1]
    return sum(rate_on(path, date(y, m, k)) for k in range(1, n + 1)) / n


def window_avg(path: list, start: date, end: date) -> float:
    days = [start + timedelta(days=k) for k in range((end - start).days)]
    return sum(rate_on(path, d) for d in days) / len(days)


def dec(meeting: date, eff: date, before: float, after: float, consensus: Optional[float] = 0.0) -> dict:
    return {"meeting_date": meeting, "effective_date": eff, "rate_before": before, "rate_after": after,
            "delta_bp": round((after - before) * 100, 6), "surprise_consensus_bp": consensus, "currency": CUR}


def official(policy_path: list, spread: float, start: date, end: date, spread_by_day: Optional[dict] = None) -> list:
    """Daily policy series (like the FRED / BoC / RBA / BoE ones) and a daily benchmark = policy in force + spread (+ an
    override per day)."""
    rows = []
    for d in bdays(start, end):
        sp = (spread_by_day or {}).get(d, spread)
        rows.append({"series_id": POL, "date": d, "value": rate_on(policy_path, d)})
        rows.append({"series_id": BENCH, "date": d, "value": rate_on(policy_path, d) + sp})
    return rows


def quote(source: str, inst: str, value: float, asof: date, *, unit: str = "index_points", ref_start: Optional[date] = None,
          ref_end: Optional[date] = None, tenor: Optional[float] = None, contract: str = "") -> dict:
    return {"source": source, "currency": CUR, "instrument": inst, "contract": contract, "field": "settle", "ref_start": ref_start,
            "ref_end": ref_end, "tenor_months": tenor, "value": value, "unit": unit, "asof": asof, "asof_inferred": False}


def futures_month(source: str, inst: str, path: list, benchmark_spread: float, asof: date, months: list) -> list:
    """1M average-rate futures: price = 100 - (average benchmark over the month)."""
    return [quote(source, inst, 100 - (month_avg(path, y, m) + benchmark_spread), asof, ref_start=date(y, m, 1), contract=f"{y}-{m:02d}")
            for y, m in months]


def curve_quotes(source: str, inst: str, asof: date, fn, tenors) -> list:
    return [quote(source, inst, fn(t), asof, unit="percent", tenor=float(t), contract=f"{t}m") for t in tenors]


def make_ctx(*, decisions: list, meetings: list, quotes: list, policy_path: list, spread: float = 0.0, sources: Optional[dict] = None,
             official_start: date = date(2026, 6, 1), official_end: date = date(2026, 12, 31), has_spread: bool = True,
             spread_by_day: Optional[dict] = None, extra_official: Optional[list] = None, stale_after_bd: int = 2) -> Context:
    bank = {"spread": {"benchmark": BENCH, "policy": [POL]}} if has_spread else {}
    return Context(
        banks={CUR: bank},
        sources=sources or {},
        calendars={},
        market=MarketIndex(quotes),
        view=SeriesView((official(policy_path, spread, official_start, official_end, spread_by_day) if has_spread else []) + (extra_official or [])),
        decisions={CUR: sorted(decisions, key=lambda r: r["meeting_date"])},
        meetings={CUR: sorted((Meeting(dc, ef) for dc, ef in meetings), key=lambda m: m.decision)},
        stale_after_bd=stale_after_bd)


def src(name: str, method: str, inst: str = "inst", role: str = "primary", **extra) -> dict:
    return {name: {"currency": CUR, "role": role, "instruments": {inst: {"method": method}}, **extra}}
