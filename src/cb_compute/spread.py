"""Overnight benchmark - policy spread (phase 1B-2, pure).

Median of (benchmark - policy in force) over the last 20 business days of the benchmark's calendar, excluding +-2
business days around the effective dates of rate changes and the last business day of every month (quarter-ends are
month-ends). Output: value, window, n. The implied policy rate of an instrument built on the benchmark is the implied
benchmark rate minus this spread.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from ..cb_calendar import Calendar

WINDOW_BD = 20
EXCLUDE_BD = 2


@dataclass
class Spread:
    value: Optional[float]                 # percentage points (benchmark - policy); None when it cannot be computed
    start: Optional[date] = None           # first / last business day of the 20-day window
    end: Optional[date] = None
    n: int = 0                             # days that entered the median
    excluded: list = field(default_factory=list)      # [(date, reason)]
    benchmark: str = ""
    policy: str = ""
    reason: str = ""                       # why value is None

    @property
    def bp(self) -> Optional[float]:
        return None if self.value is None else self.value * 100


def policy_level(view, sids: list, on: date) -> Optional[float]:
    """Policy rate in force on `on`: one series, or the midpoint of the two limits of a target range."""
    vals = [view.level(s, on) for s in sids]
    return None if any(v is None for v in vals) else sum(vals) / len(vals)


def last_business_day_of_month(cal: Calendar, d: date) -> bool:
    return cal.next_business_day(d).month != d.month


def compute_spread(view, cal: Calendar, benchmark: Optional[str], policy: Optional[list], as_of: date,
                   change_effs: list, window_bd: int = WINDOW_BD, exclude_bd: int = EXCLUDE_BD) -> Spread:
    if not benchmark or not policy:
        return Spread(None, reason="no spread pair for this bank")
    sp = Spread(None, benchmark=benchmark, policy="+".join(policy))
    ends = [view.last_date(benchmark)] + [view.last_date(s) for s in policy]
    if any(e is None for e in ends):
        sp.reason = "series missing"
        return sp
    end = cal.on_or_before(min(min(ends), as_of))
    days, d = [], end
    for _ in range(window_bd):
        days.append(d)
        d = cal.prev_business_day(d)
    days.sort()
    sp.start, sp.end = days[0], days[-1]
    skip: dict = {}
    for e in change_effs:
        centre = cal.on_or_after(e)
        for k in range(-exclude_bd, exclude_bd + 1):
            skip[cal.add_business_days(centre, k)] = f"within {exclude_bd} business days of the effective date {e}"
    obs = {}
    for d in days:
        b = view.level(benchmark, d)
        p = policy_level(view, policy, d)
        if b is None or p is None or view.first_date(benchmark) > d:
            sp.excluded.append((d, "no observation"))
            continue
        if d in skip:
            sp.excluded.append((d, skip[d]))
        elif last_business_day_of_month(cal, d):
            sp.excluded.append((d, "last business day of the month"))
        else:
            obs[d] = b - p
    sp.n = len(obs)
    if not obs:
        sp.reason = "no observation left after the exclusions"
        return sp
    sp.value = statistics.median(obs.values())
    return sp
