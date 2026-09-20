"""Validation of the effective-date rules against the change dates of the official / BIS series (pure).

A bank's `effective_rule` (config) says when a decision takes effect. The series of the policy rate change on the
effective date, so for every change of a series the rule applied to the decision that produced it must land on that
date. `check_rule` does exactly that; `compare_change_dates` tells whether two series (BIS vs the official one) change
on the same day - i.e. whether BIS is dated by the effective date.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from ..cb_calendar import Calendar, effective_date

TOL = 1e-9


@dataclass(frozen=True)
class Change:
    date: date
    before: float
    after: float


@dataclass(frozen=True)
class Check:
    change: Change
    decision: Optional[date]         # the decision day whose effective date is the change date (None: no match)
    predicted: Optional[date]        # effective date the rule gives for the closest earlier decision day
    ok: bool


def series_changes(points: Iterable[tuple], since: date) -> list:
    """Dates on which a (date, value) series moves; the first observation is not a change."""
    pts = sorted(points)
    return [Change(d1, v0, v1) for (d0, v0), (d1, v1) in zip(pts, pts[1:]) if d1 >= since and abs(v1 - v0) > TOL]


def local_days(times_utc: Iterable[datetime], tz: str) -> list:
    """Calendar days, in the bank's timezone, of a set of UTC timestamps (FF rows -> decision days)."""
    z = ZoneInfo(tz)
    out = set()
    for t in times_utc:
        t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
        out.add(t.astimezone(z).date())
    return sorted(out)


def check_rule(rule: dict, cal: Calendar, changes: list, decision_days: list, max_gap: int = 10) -> list:
    """For each change: is there a decision day (0..max_gap days before) whose effective date is the change date?"""
    out = []
    for ch in changes:
        cands = [d for d in decision_days if 0 <= (ch.date - d).days <= max_gap]
        hit = next((d for d in sorted(cands, reverse=True) if effective_date(rule, d, cal) == ch.date), None)
        nearest = max(cands, default=None)
        out.append(Check(ch, hit, effective_date(rule, nearest, cal) if nearest else None, hit is not None))
    return out


def compare_change_dates(a: list, b: list) -> list:
    """Pairs (change of a, change of b, days between them) matched by the nearest date."""
    out = []
    for ca in a:
        cb = min(b, key=lambda c: abs((c.date - ca.date).days), default=None)
        out.append((ca, cb, None if cb is None else (cb.date - ca.date).days))
    return out
