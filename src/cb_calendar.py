"""Holiday calendars, effective dates and blackout windows for the Central Banks module (pure - no I/O apart from
loading the YAML).

Calendars come from config/cb_calendars.yaml (2026-2027, every date with its official source). Outside the covered
years a calendar knows only weekends - `Calendar.covers(d)` tells the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
CALENDARS_YAML = ROOT / "config" / "cb_calendars.yaml"
ONE = timedelta(days=1)


class Calendar:
    def __init__(self, cal_id: str, holidays: dict[date, str], years: Iterable[int] = (2026, 2027),
                 weekend: Iterable[int] = (5, 6), name: str = "") -> None:
        self.id = cal_id
        self.name = name
        self.holidays = dict(holidays)
        ys = sorted(years)
        self.first, self.last = date(ys[0], 1, 1), date(ys[-1], 12, 31)
        self.weekend = frozenset(weekend)

    # ---- membership ---------------------------------------------------------------------
    def covers(self, d: date) -> bool:
        return self.first <= d <= self.last

    def holiday(self, d: date) -> Optional[str]:
        return self.holidays.get(d)

    def is_business_day(self, d: date) -> bool:
        return d.weekday() not in self.weekend and d not in self.holidays

    # ---- navigation ---------------------------------------------------------------------
    def next_business_day(self, d: date) -> date:
        """First business day strictly after d."""
        d += ONE
        while not self.is_business_day(d):
            d += ONE
        return d

    def prev_business_day(self, d: date) -> date:
        """Last business day strictly before d."""
        d -= ONE
        while not self.is_business_day(d):
            d -= ONE
        return d

    def on_or_before(self, d: date) -> date:
        return d if self.is_business_day(d) else self.prev_business_day(d)

    def on_or_after(self, d: date) -> date:
        return d if self.is_business_day(d) else self.next_business_day(d)

    def add_business_days(self, d: date, n: int) -> date:
        """n business days after (n > 0) or before (n < 0) d; d itself need not be a business day."""
        step = self.next_business_day if n > 0 else self.prev_business_day
        for _ in range(abs(n)):
            d = step(d)
        return d

    # ---- counting -----------------------------------------------------------------------
    def business_days_between(self, a: date, b: date) -> int:
        """Business days in [a, b)."""
        n, d = 0, a
        while d < b:
            n += self.is_business_day(d)
            d += ONE
        return n

    def lag(self, asof: date, today: date) -> int:
        """Business days between the as-of and the last business day <= today (0A convention: on a Saturday,
        Friday's data = 0 and Thursday's = 1)."""
        last = self.on_or_before(today)
        return self.business_days_between(asof, last) if asof < last else 0

    def missing_days(self, have: set[date], first: date, last: date) -> list[date]:
        """Business days in [first, last] with no row."""
        out, d = [], first
        while d <= last:
            if self.is_business_day(d) and d not in have:
                out.append(d)
            d += ONE
        return out


def weekends_only(cal_id: str = "-") -> Calendar:
    return Calendar(cal_id, {}, name="weekends only")


def load_calendars(path: Path | str | None = None) -> dict[str, Calendar]:
    raw = yaml.safe_load(Path(path or CALENDARS_YAML).read_text())
    years = raw["meta"]["years"]
    return {cid: Calendar(cid, {h["date"]: h["name"] for h in c["holidays"]}, years=years,
                          weekend=[5, 6], name=c["name"])
            for cid, c in raw["calendars"].items()}


# ---------------------------------------------------------------------------
# Effective date
# ---------------------------------------------------------------------------

def effective_date(rule: dict, decision: date, cal: Calendar) -> date:
    """When the decision takes effect: `calendar_days` = decision + N days (the bank's own rule, no rolling);
    `next_business_day` = the first business day after the decision in the bank's calendar (BoJ: Fri 18 Sep 2026 ->
    Thu 24 Sep, 21-23 Sep are Japanese holidays)."""
    kind = rule["kind"]
    if kind == "calendar_days":
        return decision + timedelta(days=int(rule["days"]))
    if kind == "next_business_day":
        return cal.next_business_day(decision)
    raise ValueError(f"unknown effective-date rule {kind!r}")


# ---------------------------------------------------------------------------
# Blackout
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Blackout:
    start: datetime                # timezone-aware, bank-local wall clock
    end: datetime
    precision: str                 # exact | approximate
    verified: bool                 # the bank's rule was confirmed on its official page (config `verified`)
    note: str = ""


def _at(d: date, hhmm: str, tz: ZoneInfo) -> datetime:
    h, m = hhmm.split(":")
    return datetime.combine(d, time(int(h), int(m)), tz)


def compute_blackout(rule: Optional[dict], decision: date, first_day: Optional[date],
                     calendars: dict[str, Calendar]) -> Optional[Blackout]:
    """Quiet-period window of one meeting from the bank's rule (config `blackout_rule`), the meeting's first day and
    the bank's calendar. None when the bank has no rule (RBNZ, SNB: not found)."""
    if not rule:
        return None
    tz = ZoneInfo(rule["tz"])
    first = first_day or decision
    kind = rule["kind"]
    note = rule.get("note", "")
    if kind == "saturday_before_start":                # Fed: the Nth Saturday before the first day
        d, n = first, 0
        while n < rule["saturdays"]:
            d -= ONE
            n += d.weekday() == 5
        start_day = d
    elif kind == "days_before_start":                  # ECB: N calendar days before the first day
        start_day = first - timedelta(days=rule["days"])
    elif kind == "days_before_decision":               # BoE: N calendar days before the decision
        start_day = decision - timedelta(days=rule["days"])
    elif kind == "business_days_before_start":         # BoJ: N business days before day 1 (bank calendar)
        start_day = calendars[rule["calendar"]].add_business_days(first, -rule["days"])
    elif kind == "weekday_before_decision":            # BoC: Tuesday 8 days before in MPR months, else Wednesday 7 days
        spec = rule["mpr"] if decision.month in rule["mpr_months"] else rule["other"]
        start_day = decision - timedelta(days=spec["days_before"])
        if start_day.weekday() != spec["weekday"]:
            note = (note + " " if note else "") + "decision is not on the usual weekday: start = decision - days_before"
    elif kind == "weekday_before_start":               # RBA: the given weekday before the meeting's first day
        d = first - ONE
        while d.weekday() != rule["weekday"]:
            d -= ONE
        start_day = d
    else:
        raise ValueError(f"unknown blackout rule {kind!r}")
    end_day = decision + timedelta(days=rule["end_days_after"])
    return Blackout(_at(start_day, rule["start_time"], tz), _at(end_day, rule["end_time"], tz),
                    rule.get("precision", "exact"), bool(rule.get("verified", False)), note)
