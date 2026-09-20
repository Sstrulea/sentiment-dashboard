"""Rate decisions (phase 1B-1). The oracle is table A1 of the 0A spike: the last four decisions of every bank must be
reproduced exactly from REAL cut data (official series + Forex Factory rows around the 32 meetings). Precedence,
placeholder / NaN rejection, duplicate FF rows, SNB without FF, MRO -> DFR across the 2024-09-18 spread change and
conflicts are tested on synthetic series over the real bank configuration."""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from src import cb_collect as cc
from src import cb_datasets as ds
from src.cb_calendar import load_calendars
from src.cb_compute import decisions as dd
from src.cb_sources.official import parse_ecb

FIX = Path(__file__).parent / "fixtures" / "cb"
ROOT = Path(__file__).resolve().parents[1]
D = date
TODAY = D(2026, 9, 20)
BANKS = ds.load_banks()
CALS = load_calendars()
MEETINGS = ds.load_meetings(ROOT / "data" / "cb" / "meetings.yaml")
UTC = timezone.utc


@pytest.fixture(scope="module")
def real():
    view = dd.SeriesView(pq.read_table(FIX / "decisions_official_cut.parquet").to_pylist())
    ff = ds.ff_by_bank(BANKS, pd.read_parquet(FIX / "decisions_ff_cut.parquet"))
    rows, unresolved = dd.compute_all(BANKS, MEETINGS, CALS, view, ff, TODAY)
    return {(r["currency"], r["meeting_date"]): r for r in rows}, unresolved, view, ff


# ---------------------------------------------------------------------------
# Oracle: the last 4 decisions of every bank (0A, table A1)
# ---------------------------------------------------------------------------

# (currency, meeting) -> (rate_before, rate_after, lower, upper, delta_bp, effective_date, status)
# Fed rates are midpoints; lower / upper is the range after the decision.
ORACLE = {
    ("USD", D(2026, 4, 29)): (3.625, 3.625, 3.50, 3.75, 0, D(2026, 4, 30), "official"),
    ("USD", D(2026, 6, 17)): (3.625, 3.625, 3.50, 3.75, 0, D(2026, 6, 18), "official"),
    ("USD", D(2026, 7, 29)): (3.625, 3.625, 3.50, 3.75, 0, D(2026, 7, 30), "official"),
    ("USD", D(2026, 9, 16)): (3.625, 3.875, 3.75, 4.00, 25, D(2026, 9, 17), "official"),
    ("EUR", D(2026, 4, 30)): (2.00, 2.00, None, None, 0, D(2026, 5, 6), "official"),
    ("EUR", D(2026, 6, 11)): (2.00, 2.25, None, None, 25, D(2026, 6, 17), "official"),
    ("EUR", D(2026, 7, 23)): (2.25, 2.25, None, None, 0, D(2026, 7, 29), "official"),
    ("EUR", D(2026, 9, 10)): (2.25, 2.50, None, None, 25, D(2026, 9, 16), "official"),
    ("GBP", D(2026, 4, 30)): (3.75, 3.75, None, None, 0, D(2026, 4, 30), "official"),
    ("GBP", D(2026, 6, 18)): (3.75, 3.75, None, None, 0, D(2026, 6, 18), "official"),
    ("GBP", D(2026, 7, 30)): (3.75, 3.75, None, None, 0, D(2026, 7, 30), "official"),
    ("GBP", D(2026, 9, 17)): (3.75, 3.75, None, None, 0, D(2026, 9, 17), "official"),
    ("JPY", D(2026, 4, 28)): (0.75, 0.75, None, None, 0, D(2026, 4, 30), "bis"),
    ("JPY", D(2026, 6, 16)): (0.75, 1.00, None, None, 25, D(2026, 6, 17), "bis"),
    ("JPY", D(2026, 7, 31)): (1.00, 1.00, None, None, 0, D(2026, 8, 3), "bis"),
    ("JPY", D(2026, 9, 18)): (1.00, 1.25, None, None, 25, D(2026, 9, 24), "ff_pending"),
    ("CAD", D(2026, 4, 29)): (2.25, 2.25, None, None, 0, D(2026, 4, 30), "official"),
    ("CAD", D(2026, 6, 10)): (2.25, 2.25, None, None, 0, D(2026, 6, 11), "official"),
    ("CAD", D(2026, 7, 15)): (2.25, 2.25, None, None, 0, D(2026, 7, 16), "official"),
    ("CAD", D(2026, 9, 2)): (2.25, 2.25, None, None, 0, D(2026, 9, 3), "official"),
    ("AUD", D(2026, 3, 17)): (3.85, 4.10, None, None, 25, D(2026, 3, 18), "official"),
    ("AUD", D(2026, 5, 5)): (4.10, 4.35, None, None, 25, D(2026, 5, 6), "official"),
    ("AUD", D(2026, 6, 16)): (4.35, 4.35, None, None, 0, D(2026, 6, 17), "official"),
    ("AUD", D(2026, 8, 11)): (4.35, 4.35, None, None, 0, D(2026, 8, 12), "official"),
    ("NZD", D(2026, 4, 8)): (2.25, 2.25, None, None, 0, D(2026, 4, 9), "bis"),
    ("NZD", D(2026, 5, 27)): (2.25, 2.25, None, None, 0, D(2026, 5, 28), "bis"),
    ("NZD", D(2026, 7, 8)): (2.25, 2.50, None, None, 25, D(2026, 7, 9), "bis"),
    ("NZD", D(2026, 9, 2)): (2.50, 2.75, None, None, 25, D(2026, 9, 3), "bis"),
    ("CHF", D(2025, 9, 25)): (0.0, 0.0, None, None, 0, D(2025, 9, 26), "official"),
    ("CHF", D(2025, 12, 11)): (0.0, 0.0, None, None, 0, D(2025, 12, 12), "official"),
    ("CHF", D(2026, 3, 19)): (0.0, 0.0, None, None, 0, D(2026, 3, 20), "official"),
    ("CHF", D(2026, 6, 18)): (0.0, 0.0, None, None, 0, D(2026, 6, 19), "official"),
}


def test_the_oracle_has_the_four_last_decisions_of_all_eight_banks():
    assert len(ORACLE) == 32 and {c for c, _ in ORACLE} == set(BANKS)
    assert all(sum(1 for c, _ in ORACLE if c == b) == 4 for b in BANKS)


@pytest.mark.parametrize("key", sorted(ORACLE))
def test_last_four_decisions_reproduce_table_a1(real, key):
    rows = real[0]
    assert key in rows, f"{key} was not resolved"
    r = rows[key]
    before, after, lo, up, delta, eff, status = ORACLE[key]
    assert (r["rate_before"], r["rate_after"]) == (pytest.approx(before), pytest.approx(after))
    assert r["lower"] == (pytest.approx(lo) if lo is not None else None) and r["upper"] == (pytest.approx(up) if up is not None else None)
    assert r["delta_bp"] == pytest.approx(delta) and r["effective_date"] == eff and r["status"] == status
    assert r["bank"] == BANKS[key[0]]["id"] and r["currency"] == key[0]


def test_exactly_the_oracle_is_produced_and_nothing_is_unresolved(real):
    rows, unresolved, _, _ = real
    assert set(rows) == set(ORACLE) and unresolved == []


def test_surprise_vs_consensus_is_computed_for_every_decision_that_has_a_consensus(real):
    rows = real[0]
    with_c = {k: r for k, r in rows.items() if r["consensus"] is not None}
    assert {k[0] for k in rows if k not in with_c} == {"CHF"}                     # SNB: no numeric FF rows -> n/a
    assert len(with_c) == 28 and all(r["surprise_consensus_bp"] is not None for r in with_c.values())
    assert all(rows[k]["surprise_consensus_bp"] is None and rows[k]["consensus"] is None for k in rows if k[0] == "CHF")
    # every FF forecast of these meetings equalled the outcome: zero surprise (Fed: upper 4.00 forecast -> midpoint 3.875)
    assert all(abs(r["surprise_consensus_bp"]) < 1e-9 for r in with_c.values())
    assert rows[("USD", D(2026, 9, 16))]["consensus"] == pytest.approx(3.875) and rows[("EUR", D(2026, 9, 10))]["consensus"] == pytest.approx(2.50)


def test_boj_zero_placeholder_and_nan_rows_are_rejected_with_a_trace(real):
    rows = real[0]
    n = rows[("JPY", D(2026, 4, 28))]["notes"]
    assert "actual 0.0 with previous 0.75" in n and "rejected" in n                 # the 28 Apr placeholder
    assert "missing (NaN)" in rows[("JPY", D(2026, 7, 31))]["notes"]                # the 31 Jul row has no actual
    r = rows[("JPY", D(2026, 9, 18))]
    assert r["rate_after"] == pytest.approx(1.25) and r["status"] == "ff_pending" and r["rate_source"] == "ff:BOJ Policy Rate"
    assert "0.0 with previous 1" in r["notes"] and "NaN" in r["notes"] and "waiting for the official / BIS series" in r["notes"]
    assert r["decision_time_utc"] is None                                            # BoJ publishes only a window


def test_nzd_takes_effect_on_the_next_nz_business_day_and_bis_is_read_on_that_date(real):
    """RBNZ (site blocked): the rule is derived from BIS - the OCR change of 2 Sep 2026 shows on 3 Sep. BIS is read on
    the effective date exactly like the official series of the other banks (no offset)."""
    r = real[0][("NZD", D(2026, 9, 2))]
    assert r["effective_date"] == D(2026, 9, 3) and r["rate_before"] == pytest.approx(2.50) and r["rate_after"] == pytest.approx(2.75)
    assert r["status"] == "bis" and r["rate_source"] == "bis:NZ" and "after the effective date" not in r["notes"]
    assert real[0][("NZD", D(2026, 7, 8))]["effective_date"] == D(2026, 7, 9)


def test_duplicate_ff_rows_on_the_decision_day_use_the_closest_to_the_decision_time(real):
    for key in (("USD", D(2026, 4, 29)), ("CAD", D(2026, 4, 29))):
        assert "2 FF rows in the window; closest to the decision time used" in real[0][key]["notes"]


def test_decision_time_is_the_banks_wall_clock_in_utc(real):
    rows = real[0]
    assert rows[("USD", D(2026, 9, 16))]["decision_time_utc"] == datetime(2026, 9, 16, 18, 0, tzinfo=UTC)      # 14:00 ET
    assert rows[("EUR", D(2026, 9, 10))]["decision_time_utc"] == datetime(2026, 9, 10, 12, 15, tzinfo=UTC)     # 14:15 CEST
    assert rows[("NZD", D(2026, 9, 2))]["decision_time_utc"] == datetime(2026, 9, 2, 2, 0, tzinfo=UTC)         # 14:00 NZST (UTC+12)


# ---------------------------------------------------------------------------
# Synthetic scenarios on the real bank configuration
# ---------------------------------------------------------------------------

def view(**series):
    """view(**{'boe:IUDBEDR': {D(...): 3.75}}) -> SeriesView"""
    rows = [{"series_id": sid, "date": d, "value": v} for sid, pts in series.items() for d, v in pts.items()]
    return dd.SeriesView(rows)


def step(sid_level: dict, start: date, days: int = 12):
    """Daily observations from `start`: {date: level} where sid_level = {change_date: level} (step function)."""
    out, cur = {}, None
    for i in range(days):
        d = start + timedelta(days=i)
        cur = sid_level.get(d, cur)
        out[d] = cur
    return out


def ff(day: date, hh: int, actual, forecast=None, previous=None, mm: int = 0):
    return dd.FfRow(datetime(day.year, day.month, day.day, hh, mm, tzinfo=UTC), actual, forecast, previous)


GBP_MEETING = {"date": D(2026, 9, 17)}                    # BoE: effective on the decision day
BOE = {"boe:IUDBEDR": step({D(2026, 9, 10): 3.75, D(2026, 9, 17): 4.00}, D(2026, 9, 10))}


def gbp(view_, rows, manual=None, meeting=GBP_MEETING):
    return dd.compute_decision("GBP", BANKS["GBP"], meeting, CALS, view_, rows, manual)


def test_official_beats_ff_and_agreeing_sources_are_not_a_conflict():
    r = gbp(view(**BOE), [ff(D(2026, 9, 17), 11, 4.00, 4.00, 3.75)])
    assert (r["status"], r["rate_source"], r["rate_after"], r["rate_before"], r["delta_bp"]) == ("official", "boe:IUDBEDR", 4.0, 3.75, 25.0)
    assert r["consensus"] == 4.0 and r["surprise_consensus_bp"] == 0.0


def test_conflict_official_wins_and_the_disagreement_is_recorded():
    r = gbp(view(**BOE), [ff(D(2026, 9, 17), 11, 3.75, 3.75, 3.75)])
    assert r["status"] == "conflict" and r["rate_after"] == 4.0 and r["rate_source"] == "boe:IUDBEDR"
    assert "CONFLICT" in r["notes"] and "FF says 3.75" in r["notes"] and r["delta_bp"] == 25.0


def test_bis_beats_ff_and_a_bis_ff_disagreement_is_a_conflict():
    boj = {"bis:JP": step({D(2026, 6, 10): 0.75, D(2026, 6, 17): 1.0}, D(2026, 6, 10), 14)}
    m = {"date": D(2026, 6, 16), "first_day": D(2026, 6, 15)}
    ok = dd.compute_decision("JPY", BANKS["JPY"], m, CALS, view(**boj), [ff(D(2026, 6, 16), 3, 1.0, 1.0, 0.75)])
    assert (ok["status"], ok["rate_source"], ok["rate_after"]) == ("bis", "bis:JP", 1.0)
    bad = dd.compute_decision("JPY", BANKS["JPY"], m, CALS, view(**boj), [ff(D(2026, 6, 16), 3, 0.75, 1.0, 0.75)])
    assert bad["status"] == "conflict" and bad["rate_after"] == 1.0 and bad["rate_source"] == "bis:JP"


def test_ff_alone_is_ff_pending_and_manual_is_the_last_resort():
    r = gbp(view(), [ff(D(2026, 9, 17), 11, 4.00, 4.00, 3.75)])
    assert (r["status"], r["rate_after"], r["rate_before"], r["delta_bp"]) == ("ff_pending", 4.0, 3.75, 25.0)
    both = gbp(view(), [ff(D(2026, 9, 17), 11, 4.00, 4.00, 3.75)], {"rate_after": 3.75, "note": "typed by hand"})
    assert both["status"] == "ff_pending" and both["rate_after"] == 4.0 and "manual entry says 3.75" in both["notes"]
    only = gbp(view(), [], {"rate_after": 4.0, "rate_before": 3.75, "note": "from the BoE press release"})
    assert (only["status"], only["rate_source"], only["rate_after"], only["delta_bp"]) == ("manual", "manual", 4.0, 25.0)
    assert "from the BoE press release" in only["notes"]
    assert gbp(view(), []) is None                                                   # no source at all: no row


def test_official_series_that_has_not_reached_the_effective_date_falls_back_to_ff():
    late = view(**{"boe:IUDBEDR": step({D(2026, 9, 10): 3.75}, D(2026, 9, 10), 3)})       # ends 12 Sep: 17 Sep not covered
    r = gbp(late, [ff(D(2026, 9, 17), 11, 4.00, 4.00, 3.75)])
    assert r["status"] == "ff_pending" and "waiting for the official / BIS series" in r["notes"]


def test_status_transitions_from_ff_pending_to_official_when_the_series_arrives():
    late = view(**{"boe:IUDBEDR": step({D(2026, 9, 10): 3.75}, D(2026, 9, 10), 3)})
    rows = [ff(D(2026, 9, 17), 11, 4.00, 4.00, 3.75)]
    assert gbp(late, rows)["status"] == "ff_pending" and gbp(view(**BOE), rows)["status"] == "official"


def test_zero_placeholder_is_rejected_unless_the_official_level_confirms_zero():
    placeholder = [ff(D(2026, 9, 17), 11, 0.0, 3.75, 3.75)]
    r = gbp(view(), placeholder)
    assert r is None                                                                  # rejected, nothing else available
    r = gbp(view(**BOE), placeholder)
    assert r["status"] == "official" and r["rate_after"] == 4.0 and "rejected (placeholder" in r["notes"]
    zero = {"boe:IUDBEDR": step({D(2026, 9, 10): 0.25, D(2026, 9, 17): 0.0}, D(2026, 9, 10))}
    ok = gbp(view(**zero), [ff(D(2026, 9, 17), 11, 0.0, 0.0, 0.25)])                  # a real cut to zero, confirmed
    assert ok["rate_after"] == 0.0 and ok["status"] == "official" and "rejected" not in ok["notes"] and ok["delta_bp"] == -25.0
    ff_zero = gbp(view(**zero), [ff(D(2026, 9, 17), 11, 0.0, 0.0, 0.25)])
    assert ff_zero["rate_after"] == 0.0                                               # 0.00 is a value, not a gap


def test_nan_actual_is_rejected():
    r = gbp(view(**BOE), [ff(D(2026, 9, 17), 11, None, 4.0, 3.75)])
    assert r["status"] == "official" and "missing (NaN)" in r["notes"] and r["consensus"] == 4.0
    assert gbp(view(), [ff(D(2026, 9, 17), 11, None, 4.0, 3.75)]) is None


def test_two_ff_rows_the_closest_to_the_decision_time_wins():
    # BoE decides at 12:00 London (11:00 UTC): a tentative row 7 hours early says 3.75, the real one at 11:00 says 4.00
    rows = [ff(D(2026, 9, 17), 4, 3.75, 3.75, 3.75), ff(D(2026, 9, 17), 11, 4.00, 4.00, 3.75)]
    r = gbp(view(), rows)
    assert r["rate_after"] == 4.0 and "2 FF rows in the window; closest to the decision time used" in r["notes"]
    r = gbp(view(), list(reversed(rows)))
    assert r["rate_after"] == 4.0                                                     # order-independent


def test_ff_rows_outside_the_official_meeting_window_are_not_matched():
    stray = [ff(D(2026, 9, 25), 11, 9.9, 9.9, 9.9)]
    assert gbp(view(), stray) is None
    assert gbp(view(), [ff(D(2026, 9, 18), 11, 4.0, 4.0, 3.75)])["rate_after"] == 4.0        # decision + 1 day is inside the window


def test_ff_is_matched_on_currency_and_name_not_on_the_name_alone():
    df = pd.DataFrame([
        {"currency": "NZD", "name_raw": "Official Cash Rate", "datetime_utc": pd.Timestamp("2026-09-02 02:00"), "actual": 2.75, "forecast": 2.75, "previous": 2.5},
        {"currency": "AUD", "name_raw": "Official Cash Rate", "datetime_utc": pd.Timestamp("2026-09-02 02:00"), "actual": 9.99, "forecast": 9.99, "previous": 9.9},
        {"currency": "NZD", "name_raw": "Cash Rate", "datetime_utc": pd.Timestamp("2026-09-02 02:00"), "actual": 7.77, "forecast": 7.77, "previous": 7.7}])
    by = ds.ff_by_bank(BANKS, df)
    assert [r.actual for r in by["NZD"]] == [2.75]        # "Official Cash Rate" of another currency and NZD "Cash Rate" are not matched
    assert by["AUD"] == [] and by["CHF"] == []


def test_snb_has_no_ff_and_no_consensus():
    swiss = {"snb:LZ": step({D(2026, 6, 10): 0.0}, D(2026, 6, 10), 14)}
    junk = [ff(D(2026, 6, 18), 7, 0.0, 0.0, 0.25)]                                    # a marker row would not be used either
    r = dd.compute_decision("CHF", BANKS["CHF"], {"date": D(2026, 6, 18)}, CALS, view(**swiss), junk)
    assert r["status"] == "official" and r["rate_after"] == 0.0 and r["consensus"] is None and r["surprise_consensus_bp"] is None
    assert "no numeric FF rows" in r["notes"]
    assert dd.compute_decision("CHF", BANKS["CHF"], {"date": D(2026, 6, 18)}, CALS, view(), junk) is None      # FF is never a fallback for SNB


# ---------------------------------------------------------------------------
# ECB: MRO -> DFR across the 2024-09-18 spread change
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def ecb2024():
    dfr = parse_ecb((FIX / "ecb_dfr_2024_cut.csv").read_text(), D(2000, 1, 1))
    mro = parse_ecb((FIX / "ecb_mro_2024_cut.csv").read_text(), D(2000, 1, 1))
    return view(**{"ecb:DFR": dict(dfr), "ecb:MRO": dict(mro)})


def test_the_spread_is_050_until_2024_09_17_and_015_from_the_18th(ecb2024):
    assert dd.spread_on(ecb2024, D(2024, 9, 17)) == 0.50 and dd.spread_on(ecb2024, D(2024, 9, 18)) == 0.15
    assert dd.spread_on(ecb2024, D(2024, 9, 10)) == 0.50 and dd.spread_on(ecb2024, D(2024, 9, 20)) == 0.15


def test_ff_mro_maps_to_dfr_with_the_spread_of_the_effective_date(ecb2024):
    """12 Sep 2024: FF (real row) says MRO 3.65 (previous 4.25). The cut took effect on 18 Sep, when MRO - DFR was
    already 0.15: DFR after = 3.50, before = 3.75 (spread 0.50 on 17 Sep) - matching the official DFR. Using the spread of
    the meeting day would give 3.15."""
    ff_rows = ds.ff_by_bank(BANKS, pd.read_parquet(FIX / "decisions_ff_cut.parquet"))["EUR"]
    row = next(r for r in ff_rows if r.day == D(2024, 9, 12))
    assert (row.actual, row.forecast, row.previous) == (3.65, 3.65, 4.25)
    meeting = {"date": D(2024, 9, 12), "first_day": D(2024, 9, 11)}
    eff = D(2024, 9, 18)
    cand, consensus, _ = dd.ff_candidate(BANKS["EUR"], ecb2024, [row], meeting, eff, datetime(2024, 9, 12, 12, 15, tzinfo=UTC), None)
    assert cand.after == 3.50 and cand.before == 3.75 and consensus == 3.50
    assert round(3.65 - dd.spread_on(ecb2024, D(2024, 9, 12)), 2) == 3.15                     # the wrong (meeting-day) spread
    assert dd.official_candidate(BANKS["EUR"], ecb2024, eff).after == 3.50                     # the official DFR agrees
    full = dd.compute_decision("EUR", BANKS["EUR"], meeting, CALS, ecb2024, [row])
    assert (full["rate_after"], full["rate_before"], full["delta_bp"], full["status"]) == (3.5, 3.75, -25.0, "official")
    assert full["consensus"] == 3.5 and full["surprise_consensus_bp"] == 0.0 and full["effective_date"] == eff


def test_ecb_ff_only_decision_is_mapped_with_the_series_spread_and_flagged(ecb2024):
    only_mro = view(**{"ecb:MRO": dict(ecb2024._d["ecb:MRO"])})                               # DFR missing -> spread unknown
    row = ff(D(2024, 9, 12), 12, 3.65, 3.65, 4.25, 15)
    m = {"date": D(2024, 9, 12), "first_day": D(2024, 9, 11)}
    assert dd.compute_decision("EUR", BANKS["EUR"], m, CALS, only_mro, [row]) is None          # cannot map: no invented offset


# ---------------------------------------------------------------------------
# Fed: range from the two limits, FF upper limit -> midpoint
# ---------------------------------------------------------------------------

def test_fed_range_and_midpoint_from_the_two_official_limits():
    up = {D(2026, 9, 14) + timedelta(days=i): (4.0 if i >= 3 else 3.75) for i in range(8)}
    lo = {d: v - 0.25 for d, v in up.items()}
    r = dd.compute_decision("USD", BANKS["USD"], {"date": D(2026, 9, 16), "first_day": D(2026, 9, 15)}, CALS,
                            view(**{"fred:DFEDTARU": up, "fred:DFEDTARL": lo}), [ff(D(2026, 9, 16), 18, 3.75, 3.75, 3.75)])
    assert (r["lower"], r["upper"], r["rate_after"], r["rate_before"], r["delta_bp"]) == (3.75, 4.0, 3.875, 3.625, 25.0)
    assert r["status"] == "conflict" and "FF says 3.625" in r["notes"]                          # FF (upper 3.75 -> midpoint 3.625) disagrees


def test_a_non_zero_surprise_is_reported_in_bp():
    r = gbp(view(**BOE), [ff(D(2026, 9, 17), 11, 4.00, 3.75, 3.75)])                          # consensus: hold; outcome: hike
    assert r["consensus"] == 3.75 and r["surprise_consensus_bp"] == 25.0


def test_hold_has_zero_delta_and_a_missing_before_is_taken_from_another_source():
    hold = {"boe:IUDBEDR": step({D(2026, 9, 10): 3.75}, D(2026, 9, 10))}
    assert gbp(view(**hold), [])["delta_bp"] == 0.0
    late_start = {"boe:IUDBEDR": {D(2026, 9, 17): 4.0, D(2026, 9, 18): 4.0}}                   # series starts on the effective day
    r = gbp(view(**late_start), [ff(D(2026, 9, 17), 11, 4.0, 4.0, 3.75)])
    assert r["rate_before"] == 3.75 and "rate_before taken from another source" in r["notes"] and r["delta_bp"] == 25.0


# ---------------------------------------------------------------------------
# Selection and the stored dataset
# ---------------------------------------------------------------------------

def test_select_meetings_takes_the_last_n_past_meetings():
    ms = [{"date": D(2026, 1, 28)}, {"date": D(2026, 3, 18)}, {"date": D(2026, 4, 29)}, {"date": D(2026, 6, 17)},
          {"date": D(2026, 7, 29)}, {"date": D(2026, 9, 16)}, {"date": D(2026, 10, 28)}]
    assert [m["date"] for m in dd.select_meetings(ms, D(2026, 9, 20))] == [D(2026, 4, 29), D(2026, 6, 17), D(2026, 7, 29), D(2026, 9, 16)]
    assert [m["date"] for m in dd.select_meetings(ms, D(2026, 9, 16))][-1] == D(2026, 9, 16)      # the decision day counts
    assert [m["date"] for m in dd.select_meetings(ms, D(2026, 10, 28))][-1] == D(2026, 10, 28)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def stage_paths(tmp_path):
    """A data dir with the real meetings, the official cut as an official_series partition and the FF cut."""
    paths = cc.Paths(tmp_path / "cb")
    paths.dir.mkdir(parents=True)
    (paths.meetings).write_text((ROOT / "data" / "cb" / "meetings.yaml").read_text())
    rows = pq.read_table(FIX / "decisions_official_cut.parquet").to_pylist()
    store = {(r["series_id"], r["date"]): r for r in rows}
    cc.cs.write(cc.OFFICIAL, paths.dir, store)
    return paths


def test_decisions_file_is_written_once_and_is_byte_identical_on_rerun(tmp_path):
    paths = stage_paths(tmp_path)
    rep = ds.run_decisions(paths, TODAY, ff_path=FIX / "decisions_ff_cut.parquet")
    assert rep.computed == 32 and rep.total == 32 and rep.written and rep.unresolved == [] and rep.conflicts == []
    assert rep.by_status == {"official": 24, "bis": 7, "ff_pending": 1}
    first = sha(paths.decisions)
    mt = paths.decisions.stat().st_mtime_ns
    rep = ds.run_decisions(paths, TODAY, ff_path=FIX / "decisions_ff_cut.parquet")
    assert not rep.written and sha(paths.decisions) == first and paths.decisions.stat().st_mtime_ns == mt
    t = pq.read_table(paths.decisions)
    assert t.schema.names == ["bank", "currency", "meeting_date", "decision_time_utc", "rate_before", "rate_after", "lower",
                              "upper", "delta_bp", "effective_date", "consensus", "surprise_consensus_bp", "rate_source", "status", "notes"]
    keys = [(r["currency"], r["meeting_date"]) for r in t.to_pylist()]
    assert len(keys) == len(set(keys)) == 32


def test_older_rows_stay_when_the_window_moves_and_a_new_meeting_is_added(tmp_path):
    paths = stage_paths(tmp_path)
    ds.run_decisions(paths, TODAY, ff_path=FIX / "decisions_ff_cut.parquet")
    before = {(r["currency"], r["meeting_date"]) for r in ds.load_decisions(paths)}
    # a later "today" for the Fed only adds the 28 Oct meeting when its data exists; older rows are never dropped
    ds.run_decisions(paths, D(2026, 9, 21), ff_path=FIX / "decisions_ff_cut.parquet", n=2)
    after = {(r["currency"], r["meeting_date"]) for r in ds.load_decisions(paths)}
    assert before <= after


def test_a_disagreeing_ff_row_shows_up_as_a_conflict_in_the_report_and_in_status(tmp_path):
    paths = stage_paths(tmp_path)
    ff_df = pd.read_parquet(FIX / "decisions_ff_cut.parquet")
    hit = (ff_df.currency == "GBP") & (ff_df.datetime_utc.dt.date == D(2026, 9, 17))
    ff_df.loc[hit, "actual"] = 3.50                                              # FF disagrees with the BoE series
    bad = tmp_path / "ff_bad.parquet"
    ff_df.to_parquet(bad)
    rep = ds.run_decisions(paths, TODAY, ff_path=bad)
    assert rep.by_status["conflict"] == 1 and rep.conflicts[0][:2] == ("GBP", D(2026, 9, 17))
    text, md = ds.decisions_report(rep, TODAY)
    assert "CONFLICT GBP 2026-09-17" in text and "CONFLICT" in md
    row = next(r for r in ds.load_decisions(paths) if r["currency"] == "GBP" and r["meeting_date"] == D(2026, 9, 17))
    assert row["rate_after"] == 3.75 and row["rate_source"] == "boe:IUDBEDR"          # the official value stays
    st, _ = ds.extra_status(paths, TODAY)
    assert "CONFLICT GBP 2026-09-17" in st


def test_policy_diff_against_carry(tmp_path):
    paths = stage_paths(tmp_path)
    ds.run_decisions(paths, TODAY, ff_path=FIX / "decisions_ff_cut.parquet")
    diff = {d["currency"]: d for d in ds.policy_diff(ds.load_decisions(paths))}
    assert set(diff) == set(BANKS) and all(d["diff_bp"] == 0 for d in diff.values()), diff
    yml = tmp_path / "carry.yaml"
    yml.write_text("rates:\n  USD: {rate_pct: 3.625}\n  JPY: {rate_pct: 1.00}\n  CHF: {rate_pct: null}\n")
    d2 = {d["currency"]: d for d in ds.policy_diff(ds.load_decisions(paths), yml)}
    assert d2["USD"]["diff_bp"] == -25.0 and d2["JPY"]["diff_bp"] == -25.0 and d2["CHF"]["diff_bp"] is None
    assert d2["JPY"]["cb_status"] == "ff_pending" and d2["JPY"]["cb_effective"] == D(2026, 9, 24)


def test_committed_decisions_are_consistent():
    rows = ds.cs.read_table_file(ROOT / "data" / "cb" / "decisions.parquet")
    assert rows, "decisions seed missing"
    keys = [(r["currency"], r["meeting_date"]) for r in rows]
    assert len(keys) == len(set(keys)) and {r["currency"] for r in rows} == set(BANKS)
    for r in rows:
        assert r["status"] in dd.STATUSES and r["rate_source"] and r["effective_date"] >= r["meeting_date"]
        if r["rate_before"] is not None:
            assert r["delta_bp"] == pytest.approx((r["rate_after"] - r["rate_before"]) * 100, abs=1e-6)
        if r["consensus"] is not None:
            assert r["surprise_consensus_bp"] == pytest.approx((r["rate_after"] - r["consensus"]) * 100, abs=1e-6)
        else:
            assert r["surprise_consensus_bp"] is None
    assert all((r["consensus"] is None) for r in rows if r["currency"] == "CHF")
