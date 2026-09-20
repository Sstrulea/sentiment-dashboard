"""Effective-date rules vs the change dates of the official / BIS series since 2025-09 (real cuts): BIS is dated by the
effective date, every bank's rule reproduces every change, and a wrong rule is caught."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from src import cb_datasets as ds
from src.cb_calendar import load_calendars
from src.cb_compute.effective_check import check_rule, compare_change_dates, local_days, series_changes
from src.cb_sources.official import parse_bis

FIX = Path(__file__).parent / "fixtures" / "cb"
D = date
SINCE = D(2025, 9, 1)
BANKS = ds.load_banks()
CALS = load_calendars()
AREA = {"USD": "US", "EUR": "XM", "GBP": "GB", "JPY": "JP", "CAD": "CA", "AUD": "AU", "NZD": "NZ", "CHF": "CH"}
OFFICIAL = {"USD": "fred:DFEDTARU", "EUR": "ecb:DFR", "GBP": "boe:IUDBEDR", "CAD": "boc:V39079", "AUD": "rba:FIRMMCRTD", "CHF": "snb:LZ"}
EXTRA_DECISION_DAYS = {"JPY": [D(2025, 12, 19)]}       # BoJ page state_2025 ("Dec. 19, 2025"); FF has no BoJ row that day


@pytest.fixture(scope="module")
def data():
    bis = parse_bis((FIX / "effective_bis_cut.csv").read_text(), D(2000, 1, 1))
    rows = pq.read_table(FIX / "effective_official_cut.parquet").to_pylist()
    ff = pd.read_parquet(FIX / "effective_ff_cut.parquet")
    return bis, rows, ff


def official_points(rows, sid):
    return [(r["date"], r["value"]) for r in rows if r["series_id"] == sid]


def decision_days(ff, cur):
    sub = ff[ff["currency"] == cur]
    days = local_days([t.to_pydatetime() for t in sub["datetime_utc"]], BANKS[cur]["tz"])
    return sorted(set(days) | set(EXTRA_DECISION_DAYS.get(cur, [])))


def points_for(cur, bis, rows):
    return official_points(rows, OFFICIAL[cur]) if cur in OFFICIAL else bis[AREA[cur]]


# ---------------------------------------------------------------------------

def test_series_changes_skips_the_first_observation_and_flat_stretches():
    pts = [(D(2026, 1, 1), 1.0), (D(2026, 1, 2), 1.0), (D(2026, 1, 5), 1.25), (D(2026, 1, 6), 1.25), (D(2026, 1, 9), 1.0)]
    ch = series_changes(pts, D(2026, 1, 1))
    assert [(c.date, c.before, c.after) for c in ch] == [(D(2026, 1, 5), 1.0, 1.25), (D(2026, 1, 9), 1.25, 1.0)]
    assert series_changes(pts, D(2026, 1, 6)) == ch[1:]
    assert series_changes([], D(2026, 1, 1)) == [] and series_changes(pts[:1], D(2026, 1, 1)) == []


def test_local_days_are_the_banks_wall_clock_days():
    nz = [datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)]                      # 02:00 NZST on the 2nd
    assert local_days(nz, "Pacific/Auckland") == [D(2026, 9, 2)]
    boj_placeholder = [datetime(2026, 9, 17, 21, 0)]                              # naive UTC 21:00 = 06:00 JST on the 18th
    assert local_days(boj_placeholder, "Asia/Tokyo") == [D(2026, 9, 18)]


def test_bis_is_dated_by_the_effective_date_at_the_banks_with_an_official_series(data):
    bis, rows, _ = data
    seen = 0
    for cur in ("USD", "EUR", "GBP", "CAD", "AUD"):
        off = series_changes(official_points(rows, OFFICIAL[cur]), SINCE)
        last = max(d for d, _ in bis[AREA[cur]])
        for c, other, gap in compare_change_dates(off, series_changes(bis[AREA[cur]], SINCE)):
            if c.date <= last:                                                     # BIS publishes with a lag of a few days
                assert gap == 0, (cur, c.date, other)
                seen += 1
    assert seen == 10                                                              # Fed 3, ECB 1, BoE 1, BoC 2, RBA 3
    assert "CH" not in bis                                                         # SNB: no change since 2025-09, so the cut keeps no row


def test_bis_lag_is_visible_for_the_latest_changes(data):
    bis, rows, _ = data
    assert max(d for d, _ in bis["US"]) < D(2026, 9, 17) and max(d for d, _ in bis["XM"]) < D(2026, 9, 16)
    fed = series_changes(official_points(rows, "fred:DFEDTARU"), SINCE)
    assert fed[-1].date == D(2026, 9, 17) and series_changes(bis["US"], SINCE)[-1].date == D(2025, 12, 11)


@pytest.mark.parametrize("cur, n", [("USD", 4), ("EUR", 2), ("GBP", 1), ("JPY", 2), ("CAD", 2), ("AUD", 3), ("NZD", 4), ("CHF", 0)])
def test_every_banks_rule_reproduces_every_change_since_2025_09(data, cur, n):
    bis, rows, ff = data
    changes = series_changes(points_for(cur, bis, rows), SINCE)
    checks = check_rule(BANKS[cur]["effective_rule"], CALS[BANKS[cur]["calendar_id"]], changes, decision_days(ff, cur))
    assert len(checks) == n and all(c.ok for c in checks), [(c.change.date, c.decision, c.predicted) for c in checks if not c.ok]


def test_the_matched_decision_days_are_the_known_meetings(data):
    bis, rows, ff = data
    got = {}
    for cur in ("USD", "EUR", "JPY", "NZD", "AUD", "CAD", "GBP"):
        checks = check_rule(BANKS[cur]["effective_rule"], CALS[BANKS[cur]["calendar_id"]], series_changes(points_for(cur, bis, rows), SINCE), decision_days(ff, cur))
        got[cur] = [(c.decision, c.change.date) for c in checks]
    assert got["USD"] == [(D(2025, 9, 17), D(2025, 9, 18)), (D(2025, 10, 29), D(2025, 10, 30)), (D(2025, 12, 10), D(2025, 12, 11)), (D(2026, 9, 16), D(2026, 9, 17))]
    assert got["EUR"] == [(D(2026, 6, 11), D(2026, 6, 17)), (D(2026, 9, 10), D(2026, 9, 16))]              # Thursday -> Wednesday
    assert got["JPY"] == [(D(2025, 12, 19), D(2025, 12, 22)), (D(2026, 6, 16), D(2026, 6, 17))]            # Friday -> Monday; Tuesday -> Wednesday
    assert got["NZD"] == [(D(2025, 10, 8), D(2025, 10, 9)), (D(2025, 11, 26), D(2025, 11, 27)), (D(2026, 7, 8), D(2026, 7, 9)), (D(2026, 9, 2), D(2026, 9, 3))]
    assert got["GBP"] == [(D(2025, 12, 18), D(2025, 12, 18))]


@pytest.mark.parametrize("cur, wrong", [
    ("NZD", {"kind": "calendar_days", "days": 0}),              # the old NZD assumption: same day
    ("EUR", {"kind": "calendar_days", "days": 5}),
    ("USD", {"kind": "calendar_days", "days": 0}),
    ("JPY", {"kind": "calendar_days", "days": 1}),              # Fri 19 Dec 2025 + 1 = Saturday, but the change is on Monday the 22nd
    ("AUD", {"kind": "calendar_days", "days": 2}),
])
def test_a_wrong_rule_is_caught(data, cur, wrong):
    bis, rows, ff = data
    checks = check_rule(wrong, CALS[BANKS[cur]["calendar_id"]], series_changes(points_for(cur, bis, rows), SINCE), decision_days(ff, cur))
    assert checks and not all(c.ok for c in checks)


def test_a_change_without_a_decision_day_in_reach_does_not_match():
    ch = series_changes([(D(2026, 1, 1), 1.0), (D(2026, 3, 1), 1.25)], D(2026, 1, 1))
    (c,) = check_rule({"kind": "calendar_days", "days": 1}, CALS["US"], ch, [D(2026, 1, 20)])
    assert not c.ok and c.decision is None
    (c,) = check_rule({"kind": "calendar_days", "days": 1}, CALS["US"], ch, [D(2026, 2, 28)])
    assert c.ok and c.decision == D(2026, 2, 28)
