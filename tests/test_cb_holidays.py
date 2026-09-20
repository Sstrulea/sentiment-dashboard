"""Holiday calendars vs the official pages (real cuts): parsers, the comparison with config/cb_calendars.yaml and the
weekly run. The check only warns: a date missing from the YAML, a verified date that vanished, a year that became
verifiable."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path

import pytest
import yaml

from src import cb_collect as cc
from src import cb_datasets as ds
from src.cb_sources import holidays as H

FIX = Path(__file__).parent / "fixtures" / "cb"
ROOT = Path(__file__).resolve().parents[1]
D = date
CAL = yaml.safe_load((ROOT / "config" / "cb_calendars.yaml").read_text())["calendars"]
TODAY = D(2026, 9, 20)


def txt(name):
    return (FIX / name).read_text()


LISTINGS = {"UK": H.parse_uk(txt("hol_uk_cut.json")), "JP": H.parse_jp((FIX / "hol_jp_cut.csv").read_bytes().decode("cp932")),
            "US": H.parse_us(txt("hol_us_k8_cut.htm")), "EU": H.parse_eu(txt("hol_eu_t2_cut.htm")),
            "CA": H.parse_boc(txt("hol_ca_boc_cut.html")), "AU": H.parse_nsw(txt("hol_au_nsw_cut.htm")), "NZ": H.parse_nz(txt("hol_nz_cut.htm"))}


def rows(cal):
    return deepcopy(CAL[cal]["holidays"])


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

def test_machine_readable_sources_uk_and_jp():
    uk, jp = LISTINGS["UK"], LISTINGS["JP"]
    assert len(uk.dates) == 16 and uk.dates[D(2026, 8, 31)] == "Summer bank holiday" and uk.years == {2026, 2027}
    assert len(jp.dates) == 35 and jp.dates[D(2026, 9, 22)] == "休日" and jp.dates[D(2026, 9, 21)] == "敬老の日"


def test_us_k8_observed_dates_including_the_footnote_ones():
    us = LISTINGS["US"].dates
    assert len(us) == 23 and us[D(2026, 9, 7)] == "Labor Day"
    assert us[D(2026, 7, 3)] == "Independence Day"                            # 4 Jul 2026 is a Saturday -> observed Friday 3 Jul
    assert us[D(2027, 6, 18)] == "Juneteenth National Independence Day"        # 19 Jun 2027 is a Saturday
    assert us[D(2027, 7, 5)] == "Independence Day"                             # 4 Jul 2027 is a Sunday -> Monday
    assert D(2027, 12, 31) in us and D(2027, 12, 24) in us                     # the K.8 footnote dates
    assert D(2028, 11, 10) not in us                                           # outside 2026-2027


def test_eu_target_closing_days_are_computed_from_easter():
    eu = LISTINGS["EU"].dates
    assert eu[D(2026, 4, 3)] == "Good Friday" and eu[D(2026, 4, 6)] == "Easter Monday"
    assert eu[D(2027, 3, 26)] == "Good Friday" and eu[D(2027, 3, 29)] == "Easter Monday" and len(eu) == 12
    assert H.easter(2026) == D(2026, 4, 5) and H.easter(2027) == D(2027, 3, 28)


def test_nz_observed_dates_take_the_year_from_the_weekday():
    nz = LISTINGS["NZ"].dates
    assert len(nz) == 22 and nz[D(2026, 4, 27)] == "Anzac Day" and nz[D(2027, 4, 26)] == "ANZAC Day"
    assert nz[D(2026, 7, 10)] == "Matariki" and nz[D(2027, 6, 25)] == "Matariki"
    assert nz[D(2027, 2, 8)] == "Waitangi Day" and nz[D(2027, 12, 28)] == "Boxing Day"
    assert not any("Auckland" in n for n in nz.values())                       # regional anniversaries are not modelled


def test_nsw_page_gives_the_bank_holiday_and_the_additional_days():
    au = LISTINGS["AU"].dates
    assert au[D(2026, 8, 3)] == "Bank Holiday" and au[D(2027, 8, 2)] == "Bank Holiday"
    assert au[D(2027, 4, 26)] == "Additional Day" and au[D(2026, 4, 27)] == "Additional Day"
    assert au[D(2026, 12, 28)] == "Additional Day" and au[D(2027, 12, 27)] == "Additional Day"


def test_boc_upcoming_closures_are_a_partial_listing():
    ca = LISTINGS["CA"]
    assert ca.partial and ca.years == set()
    assert sorted(ca.dates) == [D(2026, 9, 30), D(2026, 10, 12), D(2026, 11, 11), D(2026, 12, 25), D(2026, 12, 28)]
    assert ca.dates[D(2026, 9, 30)] == "National Day for Truth and Reconciliation"


def test_a_changed_page_layout_is_a_parse_failure():
    with pytest.raises(ValueError, match="closing-day list changed"):
        H.parse_eu("<p>T2 opening hours: T2 is also closed on 1 January</p>")
    with pytest.raises(ValueError, match="parsed to 3"):
        H.parse_nz("<p>New Year's Day 1 January Thursday 1 January Good Friday Varies Friday 3 April Easter Monday Varies Monday 6 April</p>")
    with pytest.raises(ValueError, match="no holiday closures"):
        H.parse_boc("<html>redesigned</html>")
    with pytest.raises(ValueError, match="K.8"):
        H.parse_us("<p>maintenance</p>")


# ---------------------------------------------------------------------------
# Comparison with cb_calendars.yaml
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cal", sorted(LISTINGS))
def test_the_committed_calendars_agree_with_the_official_pages(cal):
    assert H.check_calendar(cal, LISTINGS[cal], rows(cal), TODAY) == []


def test_a_date_on_the_page_but_not_in_the_yaml_is_reported_a_weekend_one_is_not():
    r = [x for x in rows("UK") if x["date"] != D(2026, 12, 25)]                    # Friday
    assert H.check_calendar("UK", LISTINGS["UK"], r, TODAY) == ["HOLIDAYS UK: 2026-12-25 (Christmas Day) is on the official page but not in cb_calendars.yaml"]
    r = [x for x in rows("UK") if x["date"] != D(2027, 12, 27)]                    # a substitute Monday
    assert any("2027-12-27" in w for w in H.check_calendar("UK", LISTINGS["UK"], r, TODAY))
    r = [x for x in rows("EU") if x["date"] != D(2026, 12, 26)]                    # Boxing Day 2026 is a Saturday: irrelevant
    assert H.check_calendar("EU", LISTINGS["EU"], r, TODAY) == []


def test_a_verified_date_that_vanished_from_the_page_is_reported():
    r = rows("UK") + [{"date": D(2026, 6, 15), "name": "Mystery Monday", "source": "x", "verified": True}]
    assert H.check_calendar("UK", LISTINGS["UK"], r, TODAY) == ["HOLIDAYS UK: 2026-06-15 (Mystery Monday) is in cb_calendars.yaml as verified but no longer on the official page"]
    r = rows("UK") + [{"date": D(2026, 6, 15), "name": "Mystery Monday", "source": "x", "verified": False}]
    assert not any("no longer" in w for w in H.check_calendar("UK", LISTINGS["UK"], r, TODAY))     # unverified dates are not held to the page


def test_a_partial_listing_never_reports_removals_but_still_reports_new_dates():
    assert H.check_calendar("CA", LISTINGS["CA"], rows("CA"), TODAY) == []                  # 2026 dates before 30 Sep are not on the page any more
    r = [x for x in rows("CA") if x["date"] != D(2026, 11, 11)]
    assert H.check_calendar("CA", LISTINGS["CA"], r, TODAY) == ["HOLIDAYS CA: 2026-11-11 (Remembrance Day) is on the official page but not in cb_calendars.yaml"]


def test_a_year_that_becomes_verifiable_is_reported():
    r = rows("UK")
    for x in r:
        if x["date"].year == 2027:
            x["verified"] = False
    w = H.check_calendar("UK", LISTINGS["UK"], r, TODAY)
    assert w == ["HOLIDAYS UK: 2027 has 8 unverified date(s) and the official page now covers it - re-run scripts/cb_gen_calendars.py to verify them"]
    assert H.check_calendar("UK", LISTINGS["UK"], rows("UK"), TODAY) == []                  # everything verified: nothing to say


def test_jp_year_end_bank_holidays_can_never_be_verified_by_the_csv():
    jp = rows("JP")
    assert any(not r["verified"] for r in jp)                                             # 31 Dec / 2-3 Jan: statute, not on the CSV
    assert H.check_calendar("JP", LISTINGS["JP"], jp, TODAY) == []                        # so they are out of scope, not a weekly alarm
    assert len(H.scope_rows("JP", jp)) == 35 and len(H.scope_rows("UK", rows("UK"))) == 16


def test_probes_and_the_sic_signature():
    ca = rows("CA")
    assert any(not r["verified"] for r in ca if r["date"].year == 2027)
    w = H.check_probe("CA", 2027, True, ca)
    assert w and "the official 2027 page now exists" in w and "re-run scripts/cb_gen_calendars.py" in w
    assert H.check_probe("CA", 2027, False, ca) is None
    assert H.check_probe("AU", 2027, True, rows("AU")) is None                              # AU 2027 is already verified (NSW page)
    ch = rows("CH")
    sig = {"etag": '"a"', "length": 97613}
    assert H.check_signature("CH", None, sig, ch) is None and H.check_signature("CH", sig, sig, ch) is None
    w = H.check_signature("CH", sig, {"etag": '"b"', "length": 120000}, ch)
    assert "changed (97613 -> 120000 bytes)" in w and "8 date(s) are unverified" in w


# ---------------------------------------------------------------------------
# Weekly run
# ---------------------------------------------------------------------------

class FakeHolidays:
    last_status = last_note = ""

    def __init__(self, listings=LISTINGS, probes=(), sig=None, down=()):
        self.listings, self.probes, self.sig, self.down = listings, set(probes), sig or {"etag": '"a"', "length": 97613}, set(down)

    def listing(self, cal):
        if cal in self.down:
            self.last_status, self.last_note = "UNREACHABLE", "HTTP 503"
            return None
        return self.listings[cal]

    def probe(self, url):
        return url in self.probes

    def signature(self, url):
        return self.sig


class FakePages:
    last_status = last_note = ""

    def meetings(self, bank, url):
        from tests.test_cb_calendar_check import PARSED
        return PARSED[bank]


def stage(tmp_path):
    paths = cc.Paths(tmp_path / "cb")
    paths.dir.mkdir(parents=True)
    paths.meetings.write_text((ROOT / "data" / "cb" / "meetings.yaml").read_text())
    return paths


def test_the_weekly_run_includes_the_holiday_calendars_and_is_quiet_when_all_agrees(tmp_path):
    paths = stage(tmp_path)
    rep = ds.run_calendar_check(paths, TODAY, source=FakePages(), holidays=FakeHolidays())
    assert rep.ran and rep.warnings == [] and rep.failed == {} and rep.signatures == {"CH": {"etag": '"a"', "length": 97613}}
    assert cc.load_state(paths)[ds.CHECK_KEY]["signatures"]["CH"]["length"] == 97613


def test_drift_probes_signature_and_unreachable_pages_show_up_in_status(tmp_path):
    paths = stage(tmp_path)
    ds.run_calendar_check(paths, TODAY, source=FakePages(), holidays=FakeHolidays())
    drifted = dict(LISTINGS)
    drifted["UK"] = H.Listing({2026, 2027}, {**LISTINGS["UK"].dates, D(2026, 11, 2): "Extra bank holiday"})
    rep = ds.run_calendar_check(paths, D(2026, 9, 28), source=FakePages(), force=True,
                                holidays=FakeHolidays(drifted, probes={H.PROBES["CA"][2027]}, sig={"etag": '"b"', "length": 120000}, down={"NZ"}))
    assert any("HOLIDAYS UK: 2026-11-02 (Extra bank holiday) is on the official page" in w for w in rep.warnings)
    assert any("HOLIDAYS CA: the official 2027 page now exists" in w for w in rep.warnings)
    assert any("HOLIDAYS CH: the official file changed (97613 -> 120000 bytes)" in w for w in rep.warnings)
    assert "HOLIDAYS NZ" in rep.failed and "503" in rep.failed["HOLIDAYS NZ"] and not any(w.startswith("HOLIDAYS NZ") for w in rep.warnings)
    text, md = ds.extra_status(paths, D(2026, 9, 29))
    assert "WARN HOLIDAYS UK: 2026-11-02" in text and "UNREACHABLE HOLIDAYS NZ" in text and "HOLIDAYS CH" in md
    assert cc.load_state(paths)[ds.CHECK_KEY]["signatures"]["CH"]["length"] == 120000            # the new baseline for the next week


def test_an_unreachable_sic_file_keeps_the_previous_signature(tmp_path):
    paths = stage(tmp_path)
    ds.run_calendar_check(paths, TODAY, source=FakePages(), holidays=FakeHolidays())

    class NoSic(FakeHolidays):
        def signature(self, url):
            return None
    rep = ds.run_calendar_check(paths, D(2026, 9, 28), source=FakePages(), holidays=NoSic(), force=True)
    assert rep.failed == {"HOLIDAYS CH": "SIC file unreachable"} and rep.signatures == {"CH": {"etag": '"a"', "length": 97613}}
