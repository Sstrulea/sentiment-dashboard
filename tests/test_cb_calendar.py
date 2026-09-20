"""Holiday calendars, business-day arithmetic, effective dates and blackout windows (pure functions; expected values
computed by hand from the official calendars)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from src.cb_calendar import (Calendar, compute_blackout, effective_date, load_calendars, weekends_only)

ROOT = Path(__file__).resolve().parents[1]
CALS = load_calendars()
BANKS = yaml.safe_load((ROOT / "config" / "central_banks.yaml").read_text())["banks"]
D = date


# ---------------------------------------------------------------------------
# The file
# ---------------------------------------------------------------------------

def test_eight_calendars_2026_2027_with_a_source_on_every_date():
    raw = yaml.safe_load((ROOT / "config" / "cb_calendars.yaml").read_text())
    assert set(raw["calendars"]) == {"US", "UK", "EU", "JP", "CA", "AU", "NZ", "CH"} == set(CALS)
    assert raw["meta"]["years"] == [2026, 2027]
    for cid, c in raw["calendars"].items():
        assert c["name"] and c["note"], cid
        dates = [h["date"] for h in c["holidays"]]
        assert dates == sorted(set(dates)), cid
        assert all(d.year in (2026, 2027) for d in dates), cid
        for h in c["holidays"]:
            assert h["name"] and h["source"].startswith("http") and isinstance(h["verified"], bool), (cid, h)


def test_every_source_and_bank_points_at_a_known_calendar():
    src = yaml.safe_load((ROOT / "config" / "cb_sources.yaml").read_text())["sources"]
    assert {s["calendar_id"] for s in src.values()} <= set(CALS) and all("calendar_id" in s for s in src.values())
    assert {b: c["calendar_id"] for b, c in BANKS.items()} == {
        "USD": "US", "EUR": "EU", "GBP": "UK", "JPY": "JP", "CAD": "CA", "AUD": "AU", "NZD": "NZ", "CHF": "CH"}
    off = yaml.safe_load((ROOT / "config" / "cb_official.yaml").read_text())["series"]
    assert {s["calendar_id"] for s in off.values()} <= set(CALS)


@pytest.mark.parametrize("cal, day, name", [
    ("JP", D(2026, 9, 21), "Respect for the Aged Day"),
    ("JP", D(2026, 9, 22), "Substitute / citizens' holiday"),
    ("JP", D(2026, 9, 23), "Autumnal Equinox Day"),
    ("UK", D(2026, 8, 31), "Summer bank holiday"),
    ("US", D(2026, 9, 7), "Labor Day"),
    ("US", D(2026, 7, 3), "Independence Day (observed; 4 Jul is a Saturday)"),
    ("EU", D(2026, 4, 3), "Good Friday"),
    ("EU", D(2027, 3, 29), "Easter Monday"),
    ("CA", D(2026, 8, 3), "Civic Holiday"),
    ("AU", D(2026, 8, 3), "Bank Holiday"),
    ("AU", D(2027, 4, 26), "Additional Day (Anzac Day)"),
    ("NZ", D(2026, 7, 10), "Matariki"),
    ("CH", D(2026, 5, 14), "Ascension Day"),
])
def test_known_holidays(cal, day, name):
    c = CALS[cal]
    assert c.holiday(day) == name and not c.is_business_day(day) and c.covers(day)


def test_a_day_that_is_a_holiday_elsewhere_is_open_here():
    assert CALS["UK"].is_business_day(D(2026, 9, 7)) and not CALS["US"].is_business_day(D(2026, 9, 7))   # Labor Day
    assert CALS["US"].is_business_day(D(2026, 8, 31)) and not CALS["UK"].is_business_day(D(2026, 8, 31))
    assert CALS["EU"].is_business_day(D(2026, 5, 25)) and CALS["CH"].is_business_day(D(2026, 12, 24))    # SIC: 24 Dec is a value day


def test_coverage_and_weekends_only_fallback():
    jp = CALS["JP"]
    assert jp.covers(D(2027, 12, 31)) and not jp.covers(D(2028, 1, 3)) and not jp.covers(D(2025, 12, 31))
    assert weekends_only().is_business_day(D(2026, 9, 21)) and not weekends_only().is_business_day(D(2026, 9, 19))


# ---------------------------------------------------------------------------
# Business-day arithmetic
# ---------------------------------------------------------------------------

def test_next_and_previous_business_day_skip_weekends_and_holidays():
    jp, us, uk = CALS["JP"], CALS["US"], CALS["UK"]
    assert jp.next_business_day(D(2026, 9, 18)) == D(2026, 9, 24)          # Fri -> (Mon 21, Tue 22, Wed 23 holidays) -> Thu
    assert jp.prev_business_day(D(2026, 9, 24)) == D(2026, 9, 18)
    assert us.next_business_day(D(2026, 9, 4)) == D(2026, 9, 8)            # Fri -> (Mon 7 Labor Day) -> Tue
    assert uk.prev_business_day(D(2026, 9, 1)) == D(2026, 8, 28)           # Tue -> Mon 31 Aug bank holiday -> Fri 28
    assert uk.next_business_day(D(2026, 8, 28)) == D(2026, 9, 1)
    assert us.next_business_day(D(2026, 9, 10)) == D(2026, 9, 11)


def test_on_or_before_and_after():
    us = CALS["US"]
    assert us.on_or_before(D(2026, 9, 19)) == D(2026, 9, 18) and us.on_or_before(D(2026, 9, 18)) == D(2026, 9, 18)
    assert us.on_or_before(D(2026, 9, 7)) == D(2026, 9, 4)                 # Labor Day -> the Friday before
    assert us.on_or_after(D(2026, 9, 5)) == D(2026, 9, 8) and us.on_or_after(D(2026, 9, 8)) == D(2026, 9, 8)


def test_add_business_days_both_directions():
    jp = CALS["JP"]
    assert jp.add_business_days(D(2026, 9, 17), -2) == D(2026, 9, 15)      # Thu -> Wed 16 -> Tue 15
    assert jp.add_business_days(D(2026, 9, 24), -2) == D(2026, 9, 17)      # Thu -> Fri 18 -> Thu 17 (21-23 are holidays)
    assert jp.add_business_days(D(2026, 9, 18), 2) == D(2026, 9, 25)       # 24 (1), 25 (2)
    assert jp.add_business_days(D(2026, 9, 19), 1) == D(2026, 9, 24)       # from a Saturday
    assert jp.add_business_days(D(2026, 9, 18), 0) == D(2026, 9, 18)


def test_business_days_between_is_half_open():
    assert CALS["JP"].business_days_between(D(2026, 9, 18), D(2026, 9, 25)) == 2          # 18 and 24
    assert CALS["UK"].business_days_between(D(2026, 9, 18), D(2026, 9, 25)) == 5          # 18, 21, 22, 23, 24
    assert CALS["UK"].business_days_between(D(2026, 8, 28), D(2026, 9, 1)) == 1           # Fri only (weekend + bank holiday)
    assert CALS["UK"].business_days_between(D(2026, 9, 18), D(2026, 9, 18)) == 0


@pytest.mark.parametrize("cal, asof, today, lag", [
    ("US", D(2026, 9, 18), D(2026, 9, 19), 0),         # Saturday: Friday = 0 ...
    ("US", D(2026, 9, 17), D(2026, 9, 19), 1),         # ... Thursday = 1 (0A convention)
    ("US", D(2026, 9, 4), D(2026, 9, 8), 1),           # Tue after Labor Day: Fri 4 -> Tue 8 = only the Friday (Monday is a holiday)
    ("UK", D(2026, 9, 4), D(2026, 9, 8), 2),           # ... but in the UK Monday 7 is a business day
    ("UK", D(2026, 8, 28), D(2026, 9, 1), 1),          # after the 31 Aug bank holiday
    ("JP", D(2026, 9, 18), D(2026, 9, 21), 0),         # Monday 21 is a JP holiday: last business day <= today is Fri 18
    ("JP", D(2026, 9, 18), D(2026, 9, 24), 1),         # Thu 24: Fri 18 is one business day behind
    ("US", D(2026, 9, 23), D(2026, 9, 22), 0),         # an as-of ahead of today never gives a negative lag
])
def test_lag_uses_the_calendar_of_the_source(cal, asof, today, lag):
    assert CALS[cal].lag(asof, today) == lag


def test_missing_days_exclude_the_calendar_holidays():
    us = CALS["US"]
    have = {D(2026, 9, 1), D(2026, 9, 2), D(2026, 9, 3), D(2026, 9, 4), D(2026, 9, 8), D(2026, 9, 10)}
    # 7 Sep (Labor Day) is not "missing"; 9 Sep is
    assert us.missing_days(have, D(2026, 9, 1), D(2026, 9, 10)) == [D(2026, 9, 9)]
    assert weekends_only().missing_days(have, D(2026, 9, 1), D(2026, 9, 10)) == [D(2026, 9, 7), D(2026, 9, 9)]
    assert Calendar("X", {D(2026, 9, 9): "x"}).missing_days(have, D(2026, 9, 1), D(2026, 9, 10)) == [D(2026, 9, 7)]


# ---------------------------------------------------------------------------
# Effective date (rule from config, calendar of the bank)
# ---------------------------------------------------------------------------

def eff(bank, decision):
    c = BANKS[bank]
    return effective_date(c["effective_rule"], decision, CALS[c["calendar_id"]])


def test_effective_dates_per_bank():
    assert eff("USD", D(2026, 9, 16)) == D(2026, 9, 17)                  # Fed: Wednesday -> Thursday
    assert D(2026, 9, 16).weekday() == 2 and D(2026, 9, 17).weekday() == 3
    assert eff("EUR", D(2026, 9, 10)) == D(2026, 9, 16)                  # ECB: Thursday -> the Wednesday of the next week
    assert D(2026, 9, 10).weekday() == 3 and D(2026, 9, 16).weekday() == 2
    assert eff("JPY", D(2026, 9, 18)) == D(2026, 9, 24)                  # BoJ: 21-23 Sep are Japanese holidays
    assert eff("JPY", D(2026, 6, 16)) == D(2026, 6, 17) and eff("JPY", D(2026, 4, 28)) == D(2026, 4, 30)   # 29 Apr = Showa Day
    assert eff("GBP", D(2026, 9, 17)) == D(2026, 9, 17)                  # BoE: same day
    assert eff("CAD", D(2026, 9, 2)) == D(2026, 9, 3)
    assert eff("AUD", D(2026, 8, 11)) == D(2026, 8, 12)
    assert eff("CHF", D(2026, 6, 18)) == D(2026, 6, 19)
    assert eff("NZD", D(2026, 9, 2)) == D(2026, 9, 2)


def test_effective_rule_kinds_are_validated():
    with pytest.raises(ValueError):
        effective_date({"kind": "whenever"}, D(2026, 9, 18), CALS["JP"])
    assert effective_date({"kind": "next_business_day"}, D(2026, 9, 18), weekends_only()) == D(2026, 9, 21)


# ---------------------------------------------------------------------------
# Blackout - windows computed by hand
# ---------------------------------------------------------------------------

def bo(bank, decision, first=None):
    return compute_blackout(BANKS[bank]["blackout_rule"], decision, first, CALS)


def at(y, m, d, hh, mm, tz):
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(tz))


def test_fed_blackout_second_saturday_before_the_meeting():
    """FOMC 15-16 Sep 2026 (Tue-Wed): Saturdays before Tue 15 Sep = 12 Sep (1st), 5 Sep (2nd). Blackout 00:00 ET Sat
    5 Sep -> 23:59 ET Thu 17 Sep (the day after the meeting ends)."""
    b = bo("USD", D(2026, 9, 16), D(2026, 9, 15))
    assert b.start == at(2026, 9, 5, 0, 0, "America/New_York") and b.end == at(2026, 9, 17, 23, 59, "America/New_York")
    assert D(2026, 9, 5).weekday() == 5 and b.precision == "exact" and b.verified
    # 27-28 Jan 2027 (Wed-Thu): Saturdays before Wed 27 Jan = 23 Jan, 16 Jan -> starts Sat 16 Jan, ends Fri 29 Jan
    b = bo("USD", D(2027, 1, 28), D(2027, 1, 27))
    assert (b.start.date(), b.end.date()) == (D(2027, 1, 16), D(2027, 1, 29))


def test_boj_blackout_two_business_days_before_day_one():
    """MPM 17-18 Sep 2026 (Thu-Fri): 2 business days before Thu 17 = Tue 15. Start 00:00 JST 15 Sep, end 23:59 JST 18 Sep."""
    b = bo("JPY", D(2026, 9, 18), D(2026, 9, 17))
    assert b.start == at(2026, 9, 15, 0, 0, "Asia/Tokyo") and b.end == at(2026, 9, 18, 23, 59, "Asia/Tokyo")
    assert not b.verified                                                       # the rule is marked unverified in the config
    # a meeting that starts Thu 24 Sep 2026: 23, 22, 21 are holidays, so 2 business days before = Thu 17 (Fri 18 is the first)
    b = bo("JPY", D(2026, 9, 25), D(2026, 9, 24))
    assert b.start.date() == D(2026, 9, 17)
    # Mon-Tue meeting 27-28 Apr 2026: 2 business days before Mon 27 = Thu 23 (Fri 24 is the first)
    assert bo("JPY", D(2026, 4, 28), D(2026, 4, 27)).start.date() == D(2026, 4, 23)


def test_ecb_boc_rba_boe_windows():
    b = bo("EUR", D(2026, 9, 10), D(2026, 9, 9))                                # 7 days before Wed 9 Sep = Wed 2 Sep
    assert b.start == at(2026, 9, 2, 0, 0, "Europe/Berlin") and b.end == at(2026, 9, 10, 14, 45, "Europe/Berlin")
    b = bo("CAD", D(2026, 7, 15))                                               # MPR month: Tuesday 8 days before = 7 Jul
    assert b.start.date() == D(2026, 7, 7) and b.start.weekday() == 1 and b.end == at(2026, 7, 15, 10, 30, "America/Toronto")
    b = bo("CAD", D(2026, 9, 2))                                                # other month: Wednesday 7 days before = 26 Aug
    assert b.start.date() == D(2026, 8, 26) and b.start.weekday() == 2
    b = bo("AUD", D(2026, 9, 29), D(2026, 9, 28))                               # Wednesday before Mon 28 Sep = 23 Sep, 14:00
    assert b.start == at(2026, 9, 23, 14, 0, "Australia/Sydney") and b.end == at(2026, 9, 29, 17, 0, "Australia/Sydney")
    b = bo("GBP", D(2026, 9, 17))
    assert b.start.date() == D(2026, 9, 9) and b.precision == "approximate"     # "usually 8-9 days before" -> 8


def test_banks_without_a_rule_have_no_window():
    assert bo("NZD", D(2026, 9, 2)) is None and bo("CHF", D(2026, 9, 24)) is None
    assert compute_blackout(None, D(2026, 9, 2), None, CALS) is None


def test_unknown_blackout_rule_is_rejected():
    with pytest.raises(ValueError):
        compute_blackout({"kind": "vibes", "tz": "UTC"}, D(2026, 9, 2), None, CALS)


def test_blackout_windows_are_ordered_and_finish_on_or_after_the_decision():
    ms = yaml.safe_load((ROOT / "data" / "cb" / "meetings.yaml").read_text())["meetings"]
    for bank, rows in ms.items():
        for r in rows:
            b = bo(bank, r["date"], r.get("first_day"))
            if b is None:
                continue
            assert b.start < b.end and b.end.date() >= r["date"] and b.end.date() <= r["date"] + timedelta(days=1), (bank, r["date"])
