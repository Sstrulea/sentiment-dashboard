"""rate_before when the official series lags the decision (SNB: snb:LZ arrives days after the effective date)."""
from __future__ import annotations

from src import cb_datasets as ds
from src.cb_calendar import load_calendars
from src.cb_compute import decisions as dd

from .cb_docs_helpers import D

BANKS = ds.load_banks()
CALS = load_calendars()
CHF = BANKS["CHF"]
LZ = CHF["policy_rate"]["official"]["level"]
MEETING = {"date": D(2026, 9, 24), "first_day": None}           # effective 2026-09-25
PREV = {"date": D(2026, 6, 18), "first_day": None}              # effective 2026-06-19


def view(pts):
    return dd.SeriesView([{"series_id": LZ, "date": d, "value": v} for d, v in pts])


def stmt(after):
    return {"after": after, "lower": None, "upper": None, "doc_id": "CHF:statement:2026-09-24"}


LAGGING = view([(D(2026, 6, 1), 0.0), (D(2026, 6, 19), 0.0), (D(2026, 9, 18), 0.0)])


def test_hold_with_lagging_series_takes_rate_before_from_the_series():
    r = dd.compute_decision("CHF", CHF, MEETING, CALS, LAGGING, [], statement=stmt(0.0), prev_meeting=PREV)
    assert (r["status"], r["rate_source"], r["rate_after"]) == ("statement", "statement:CHF:statement:2026-09-24", 0.0)
    assert r["rate_before"] == 0.0 and r["delta_bp"] == 0
    assert "rate_before = snb:LZ on 2026-09-18 (series not yet at the effective date)" in r["notes"]


def test_cut_with_lagging_series():
    r = dd.compute_decision("CHF", CHF, MEETING, CALS, LAGGING, [], statement=stmt(-0.25), prev_meeting=PREV)
    assert r["status"] == "statement" and r["rate_after"] == -0.25 and r["rate_before"] == 0.0 and r["delta_bp"] == -25


def test_series_stopping_before_the_previous_effective_date_gives_no_rate_before():
    stale = view([(D(2026, 6, 1), 0.25), (D(2026, 6, 10), 0.25)])
    r = dd.compute_decision("CHF", CHF, MEETING, CALS, stale, [], statement=stmt(0.0), prev_meeting=PREV)
    assert r["status"] == "statement" and r["rate_before"] is None and r["delta_bp"] is None
    assert "series not yet at the effective date" not in r["notes"]


def test_no_previous_meeting_no_fallback():
    r = dd.compute_decision("CHF", CHF, MEETING, CALS, LAGGING, [], statement=stmt(0.0))
    assert r["rate_before"] is None


def test_series_reaching_the_effective_date_goes_official_as_before():
    full = view([(D(2026, 6, 19), 0.0), (D(2026, 9, 18), 0.0), (D(2026, 9, 25), 0.0)])
    r = dd.compute_decision("CHF", CHF, MEETING, CALS, full, [], statement=stmt(0.0), prev_meeting=PREV)
    assert (r["status"], r["rate_source"], r["rate_before"], r["delta_bp"]) == ("official", LZ, 0.0, 0)
    assert "series not yet" not in r["notes"]


def test_compute_all_passes_the_previous_meeting():
    rows, _ = dd.compute_all({"CHF": CHF}, {"CHF": [PREV, MEETING]}, CALS, LAGGING, {}, D(2026, 9, 24),
                             statements={("CHF", D(2026, 9, 24)): stmt(0.0)})
    row = next(r for r in rows if r["meeting_date"] == D(2026, 9, 24))
    assert row["rate_before"] == 0.0 and row["delta_bp"] == 0
