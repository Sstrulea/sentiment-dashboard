"""Weekly check of data/cb/meetings.yaml against the official calendar pages (real cuts of the pages): parsers, the
comparison (warnings only, never a rewrite), weekly gating and the --status section."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest

from src import cb_collect as cc
from src import cb_datasets as ds
from src.cb_sources import calendar as C
from src.cb_sources import holidays as H

FIX = Path(__file__).parent / "fixtures" / "cb"
ROOT = Path(__file__).resolve().parents[1]
D = date
MEETINGS = ds.load_meetings(ROOT / "data" / "cb" / "meetings.yaml")
BANKS = ds.load_banks()


def cut(name: str) -> str:
    return (FIX / f"cal_{name}_cut.htm").read_text()


def dates(ms):
    return [m["date"] for m in ms]


# ---------------------------------------------------------------------------
# Parsers (real page cuts)
# ---------------------------------------------------------------------------

def test_fed_page_gives_first_day_and_the_sep_asterisk():
    ms = C.parse_fed(cut("fed"))
    assert len(ms) == 16 and dates(ms)[:2] == [D(2026, 1, 28), D(2026, 3, 18)]
    by = {m["date"]: m for m in ms}
    assert by[D(2026, 9, 16)]["first_day"] == D(2026, 9, 15) and by[D(2026, 9, 16)]["has_projections"] is True
    assert by[D(2026, 10, 28)]["has_projections"] is False and by[D(2027, 12, 8)]["first_day"] == D(2027, 12, 7)


def test_ecb_page_lists_day_1_and_day_2_of_the_upcoming_meetings_only():
    ms = C.parse_ecb(cut("ecb"))
    assert dates(ms)[:3] == [D(2026, 10, 29), D(2026, 12, 17), D(2027, 2, 4)]
    assert all(m["first_day"] == m["date"] - timedelta(days=1) for m in ms)               # Wednesday / Thursday


def test_boj_tables_give_the_two_days_and_the_outlook_report():
    ms = C.parse_boj(cut("boj"))
    by = {m["date"]: m for m in ms}
    assert len(ms) == 16 and by[D(2026, 9, 18)]["first_day"] == D(2026, 9, 17) and by[D(2026, 9, 18)]["has_projections"] is False
    assert by[D(2026, 10, 30)]["has_projections"] is True and by[D(2026, 4, 28)]["first_day"] == D(2026, 4, 27)


def test_rba_two_day_boards_and_boe_boc_single_days():
    rba = {m["date"]: m for m in C.parse_rba(cut("rba"))}
    assert len(rba) == 16 and rba[D(2026, 9, 29)]["first_day"] == D(2026, 9, 28) and rba[D(2027, 12, 14)]["first_day"] == D(2027, 12, 13)
    boe = {m["date"]: m for m in C.parse_boe(cut("boe"))}
    assert len(boe) == 16 and boe[D(2026, 7, 30)]["has_projections"] is True and boe[D(2026, 9, 17)]["has_projections"] is False
    assert boe[D(2027, 12, 16)]["has_projections"] is False                                # not read from the page footer
    boc = {m["date"]: m for m in C.parse_boc(cut("boc"))}
    assert boc[D(2026, 7, 15)]["has_projections"] is True and boc[D(2026, 9, 2)]["has_projections"] is False
    assert all(m["first_day"] is None for m in list(boe.values()) + list(boc.values()))


def test_snb_archive_and_schedule():
    ms = C.parse_snb(cut("snb_schedule"), cut("snb_archive"))
    got = set(dates(ms))
    assert {D(2025, 9, 25), D(2025, 12, 11), D(2026, 3, 19), D(2026, 9, 24), D(2026, 12, 10)} <= got
    assert C.parse_snb(cut("snb_schedule"), None) and D(2025, 9, 25) not in dates(C.parse_snb(cut("snb_schedule"), None))


PARSED = {"USD": C.parse_fed(cut("fed")), "EUR": C.parse_ecb(cut("ecb")), "GBP": C.parse_boe(cut("boe")), "JPY": C.parse_boj(cut("boj")),
          "CAD": C.parse_boc(cut("boc")), "AUD": C.parse_rba(cut("rba")), "CHF": C.parse_snb(cut("snb_schedule"), cut("snb_archive"))}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bank", sorted(PARSED))
def test_meetings_yaml_agrees_with_the_official_pages(bank):
    """The committed file is what the pages say (the check is the weekly guard that keeps it so)."""
    assert C.check_meetings(bank, PARSED[bank], MEETINGS[bank], D(2026, 9, 20)) == []


def rows_of(bank):
    return deepcopy(MEETINGS[bank])


def test_a_new_meeting_on_the_page_is_reported():
    rows = [r for r in rows_of("JPY") if r["date"] != D(2026, 10, 30)]
    w = C.check_meetings("JPY", PARSED["JPY"], rows, D(2026, 9, 20))
    assert w == ["JPY: 2026-10-30 is on the official page but not in meetings.yaml"]


def test_a_moved_meeting_is_reported_both_ways():
    rows = rows_of("USD")
    for r in rows:
        if r["date"] == D(2026, 10, 28):
            r["date"], r["first_day"] = D(2026, 10, 29), D(2026, 10, 28)
    w = C.check_meetings("USD", PARSED["USD"], rows, D(2026, 9, 20))
    assert any("2026-10-28 is on the official page but not in meetings.yaml" in x for x in w)
    assert any("2026-10-29 is in meetings.yaml but no longer on the official page" in x for x in w)


def test_a_past_meeting_that_dropped_off_the_page_is_not_a_change():
    rows = rows_of("USD")
    w = C.check_meetings("USD", [m for m in PARSED["USD"] if m["date"] >= D(2026, 9, 16)], rows, D(2026, 9, 20))
    assert w == []                                                                        # 2026-01..07 only exist in meetings.yaml now


def test_first_day_and_projection_flags_are_compared_where_the_page_states_them():
    rows = rows_of("USD")
    for r in rows:
        if r["date"] == D(2026, 9, 16):
            r["first_day"], r["has_projections"] = D(2026, 9, 14), False
    w = C.check_meetings("USD", PARSED["USD"], rows, D(2026, 9, 20))
    assert "USD: 2026-09-16 first_day is 2026-09-15 on the page, 2026-09-14 in meetings.yaml" in w
    assert "USD: 2026-09-16 has_projections is True on the page, False in meetings.yaml" in w
    # the RBA page does not state projections (derived from the SMP months): a difference there is not reported
    rba = rows_of("AUD")
    rba[0]["has_projections"] = not rba[0]["has_projections"]
    assert C.check_meetings("AUD", PARSED["AUD"], rba, D(2026, 9, 20)) == []


def test_ff_and_manual_rows_are_not_official_and_not_compared():
    rows = rows_of("EUR")                                                                # past 2026 rows come from FF
    assert any(r["source"] == "ff" for r in rows)
    assert C.check_meetings("EUR", PARSED["EUR"], rows, D(2026, 9, 20)) == []
    rows[-1]["source"] = "ff"                                                             # an upcoming date seen only as FF
    assert "meetings.yaml has it as source=ff" in C.check_meetings("EUR", PARSED["EUR"], rows, D(2026, 9, 20))[0]


def test_an_unparseable_page_is_a_warning_not_an_empty_success():
    w = C.check_meetings("USD", [], rows_of("USD"), D(2026, 9, 20))
    assert w == ["USD: the calendar page parsed to no meeting in scope (layout changed?)"]


# ---------------------------------------------------------------------------
# Weekly run, state, status
# ---------------------------------------------------------------------------

class FakePages:
    last_status = last_note = ""

    def __init__(self, parsed=PARSED, down=()):
        self.parsed, self.down, self.calls = parsed, set(down), []

    def meetings(self, bank, url):
        self.calls.append((bank, url))
        if bank in self.down:
            self.last_status, self.last_note = "UNREACHABLE", "HTTP 403"
            return None
        return self.parsed[bank]


class FakeHolidays:
    """Holiday source over the real page cuts (no network); `probes` = urls that answer 200, `sig` = the SIC file signature."""
    last_status = last_note = ""

    def __init__(self, listings=None, probes=(), sig=None, down=()):
        self.listings = listings if listings is not None else HOLIDAY_LISTINGS
        self.probes, self.sig, self.down, self.calls = set(probes), sig or {"etag": '"a"', "length": 97613}, set(down), []

    def listing(self, cal):
        self.calls.append(cal)
        if cal in self.down:
            self.last_status, self.last_note = "UNREACHABLE", "HTTP 503"
            return None
        return self.listings[cal]

    def probe(self, url):
        return url in self.probes

    def signature(self, url):
        return self.sig


def _hol(name):
    return (FIX / name).read_text()


HOLIDAY_LISTINGS = {
    "UK": H.parse_uk(_hol("hol_uk_cut.json")), "JP": H.parse_jp((FIX / "hol_jp_cut.csv").read_bytes().decode("cp932")),
    "US": H.parse_us(_hol("hol_us_k8_cut.htm")), "EU": H.parse_eu(_hol("hol_eu_t2_cut.htm")), "CA": H.parse_boc(_hol("hol_ca_boc_cut.html")),
    "AU": H.parse_nsw(_hol("hol_au_nsw_cut.htm")), "NZ": H.parse_nz(_hol("hol_nz_cut.htm"))}


def stage(tmp_path):
    paths = cc.Paths(tmp_path / "cb")
    paths.dir.mkdir(parents=True)
    paths.meetings.write_text((ROOT / "data" / "cb" / "meetings.yaml").read_text())
    return paths


def test_the_check_runs_weekly_and_only_warns(tmp_path):
    paths = stage(tmp_path)
    before = paths.meetings.read_text()
    src = FakePages()
    rep = ds.run_calendar_check(paths, D(2026, 9, 20), source=src, holidays=FakeHolidays())
    assert rep.ran and rep.checked == D(2026, 9, 20) and rep.warnings == [] and len(src.calls) == 7
    assert [c[1] for c in src.calls] == [BANKS[b]["calendar_url"] for b in ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF")]
    src2 = FakePages()
    again = ds.run_calendar_check(paths, D(2026, 9, 26), source=src2, holidays=FakeHolidays())                    # 6 days later: not due
    assert not again.ran and src2.calls == [] and again.checked == D(2026, 9, 20)
    due = ds.run_calendar_check(paths, D(2026, 9, 27), source=FakePages(), holidays=FakeHolidays())                # 7 days later
    assert due.ran and due.checked == D(2026, 9, 27)
    forced = ds.run_calendar_check(paths, D(2026, 9, 28), source=FakePages(), holidays=FakeHolidays(), force=True)
    assert forced.ran and forced.checked == D(2026, 9, 28)
    assert paths.meetings.read_text() == before                                          # never rewritten


def test_warnings_and_unreachable_pages_are_kept_in_state_for_status(tmp_path):
    paths = stage(tmp_path)
    drifted = dict(PARSED)
    drifted["JPY"] = [m for m in PARSED["JPY"] if m["date"] != D(2026, 12, 18)] + [{"date": D(2026, 12, 21), "first_day": D(2026, 12, 18), "has_projections": False}]
    rep = ds.run_calendar_check(paths, D(2026, 9, 20), source=FakePages(drifted, down={"AUD"}), holidays=FakeHolidays())
    assert any("JPY: 2026-12-21 is on the official page" in w for w in rep.warnings) and any("2026-12-18 is in meetings.yaml but no longer" in w for w in rep.warnings)
    assert "AUD" in rep.failed and "403" in rep.failed["AUD"] and not any(w.startswith("AUD") for w in rep.warnings)    # unreachable != drift
    st = cc.load_state(paths)[ds.CHECK_KEY]
    assert st["checked"] == "2026-09-20" and st["warnings"] == rep.warnings and "AUD" in st["failed"]
    text, md = ds.extra_status(paths, D(2026, 9, 21))
    assert "meetings calendar check" in text and "WARN JPY: 2026-12-21" in text and "UNREACHABLE AUD" in text and "WARN JPY: 2026-12-21" in md
    text, _ = ds.calendar_check_report(ds.stored_calendar_check(cc.load_state(paths)))
    assert "last run 2026-09-20 (weekly)" in text


def test_status_warns_when_the_weekly_check_itself_is_stale(tmp_path):
    paths = stage(tmp_path)
    ds.run_calendar_check(paths, D(2026, 9, 20), source=FakePages(), holidays=FakeHolidays())
    text, _ = ds.extra_status(paths, D(2026, 10, 5))
    assert "WARN the check is 15 days old (runs weekly)" in text
    assert "days old" not in ds.extra_status(paths, D(2026, 9, 26))[0]


def test_meetings_stay_untouched_and_the_check_state_is_a_separate_section(tmp_path):
    paths = stage(tmp_path)
    ds.run_calendar_check(paths, D(2026, 9, 20), source=FakePages(), holidays=FakeHolidays())
    state = cc.load_state(paths)
    assert set(state) == {ds.CHECK_KEY}                                                   # validators of the collectors are not touched


def test_the_collector_stage_reports_the_check(tmp_path, monkeypatch, capsys):
    paths = stage(tmp_path)
    monkeypatch.setattr(ds, "CalendarPages", lambda: FakePages())
    monkeypatch.setattr(ds.hol, "HolidayPages", lambda: FakeHolidays())
    assert cc.main(["--stage", "calendar", "--force-calendar-check", "--data-dir", str(paths.dir)]) == 0
    out = capsys.readouterr().out
    assert "calendar check: ran" in out and "0 warning(s)" in out
