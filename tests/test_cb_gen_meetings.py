"""scripts/cb_gen_meetings.py: the generator rebuilds data/cb/meetings.yaml byte for byte from the real page cuts
(offline) - so the committed file is exactly what the official pages say."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import yaml

from src import cb_datasets as ds
from src.cb_compute.effective_check import local_days
from src.cb_sources import calendar as C
from src.cb_sources import meetings as M

FIX = Path(__file__).parent / "fixtures" / "cb"
ROOT = Path(__file__).resolve().parents[1]
D = date
BANKS = ds.load_banks()
EVIDENCE = D(2026, 9, 20)
BOE_EVIDENCE = D(2026, 10, 9)                     # the BoE page re-read: 2027 confirmed (cal_boe_cut_20261009.htm)
BY_BANK = {"GBP": BOE_EVIDENCE}
PAST_EVIDENCE = D(2026, 11, 14)                   # S1.1: the official archives of the 2025 meetings, read on this day


def cut(name, suffix=""):
    return (FIX / f"cal_{name}_cut{suffix}.htm").read_text()


@pytest.fixture(scope="module")
def parsed():
    return {"USD": C.parse_fed(cut("fed")), "EUR": C.parse_ecb(cut("ecb")), "GBP": C.parse_boe(cut("boe", "_20261009")),
            "JPY": C.parse_boj(cut("boj")), "CAD": C.parse_boc(cut("boc")), "AUD": C.parse_rba(cut("rba")),
            "CHF": C.parse_snb(cut("snb_schedule"), cut("snb_archive"))}


@pytest.fixture(scope="module")
def past():
    """The meetings already held (2025-09..12) from the official archives (cuts read 2026-11-14)."""
    boe = [C._m(C.parse_boe_summary(line.split("\t")[1])) for line in (FIX / "cal_boe_summaries_2025_cut_20261114.txt").read_text().splitlines()]
    return {"USD": C.parse_fed((FIX / "cal_fed_2025_cut_20261114.htm").read_text()),
            "CAD": C.parse_boc_history((FIX / "cal_boc_history_cut_20261114.htm").read_text()),
            "JPY": C.parse_boj_decisions((FIX / "cal_boj_decisions_2025_cut_20261114.htm").read_text()), "GBP": boe}


@pytest.fixture(scope="module")
def ecb_days():
    ff = pd.read_parquet(FIX / "meetings_ff_ecb_cut.parquet")
    ff = ff[ff["actual"].notna()]
    return local_days([t.to_pydatetime() for t in ff["datetime_utc"]], BANKS["EUR"]["tz"])


@pytest.fixture(scope="module")
def rbnz():
    return ds.load_rbnz(ROOT / "data" / "cb" / "manual" / "rbnz.yaml")["published_calendar"]["meetings"]


def test_the_generator_reproduces_the_committed_file_byte_for_byte(parsed, ecb_days, rbnz, past):
    text = M.build(parsed, BANKS, EVIDENCE, ecb_days, rbnz, BY_BANK, past, PAST_EVIDENCE)
    assert text == (ROOT / "data" / "cb" / "meetings.yaml").read_text()


def test_ecb_meetings_already_held_come_only_from_ff_and_only_from_2025_09(parsed, ecb_days, rbnz):
    assert D(2025, 9, 11) in ecb_days and D(2026, 9, 10) in ecb_days
    rows = M.build_rows("EUR", parsed["EUR"], BANKS, EVIDENCE, ecb_days)
    ff = [r for r in rows if r[4] == "ff"]
    assert [r[0] for r in ff] == [D(2025, 9, 11), D(2025, 10, 30), D(2025, 12, 18),
                                  D(2026, 2, 5), D(2026, 3, 19), D(2026, 4, 30), D(2026, 6, 11), D(2026, 7, 23), D(2026, 9, 10)]
    assert all(r[5] is False and (r[0] - r[1]).days == 1 for r in ff)
    assert all(r[4] == "official" and r[5] == EVIDENCE for r in rows if r[0] >= D(2026, 10, 29))
    assert len({r[0] for r in rows}) == len(rows) == 19


def test_past_meetings_come_from_the_official_archives_and_fill_only_before_the_page(past):
    assert [m["date"] for m in past["CAD"] if m["date"] < D(2026, 1, 1)][:3] == [D(2025, 12, 10), D(2025, 10, 29), D(2025, 9, 17)]
    assert [m["date"] for m in past["JPY"]][-3:] == [D(2025, 9, 19), D(2025, 10, 30), D(2025, 12, 19)]
    assert all(m["first_day"] is None for m in past["JPY"])                           # the BoJ index has no first day
    assert [m["date"] for m in past["GBP"]] == [D(2025, 9, 18), D(2025, 11, 6), D(2025, 12, 18), D(2025, 8, 7)]
    rows = M.build_rows("CAD", [{"date": D(2026, 1, 28), "has_projections": True}], BANKS, EVIDENCE, past=past["CAD"], past_evidence=PAST_EVIDENCE)
    assert [(r[0], r[4], r[5]) for r in rows] == [(D(2025, 9, 17), "official_archive", PAST_EVIDENCE), (D(2025, 10, 29), "official_archive", PAST_EVIDENCE),
                                                  (D(2025, 12, 10), "official_archive", PAST_EVIDENCE), (D(2026, 1, 28), "official", EVIDENCE)]
    usd = {r[0]: r for r in M.build_rows("USD", [], BANKS, EVIDENCE, past=past["USD"], past_evidence=PAST_EVIDENCE)}
    assert set(usd) == {D(2025, 9, 17), D(2025, 10, 29), D(2025, 12, 10)} and usd[D(2025, 9, 17)][1:3] == (D(2025, 9, 16), True)


def test_boe_2027_was_provisional_on_the_old_page(parsed):
    rows = {r[0]: r for r in M.build_rows("GBP", C.parse_boe(cut("boe")), BANKS, EVIDENCE)}
    assert all(r[5] is False for d, r in rows.items() if d.year == 2027) and all(r[5] == EVIDENCE for d, r in rows.items() if d.year == 2026)
    assert rows[D(2027, 12, 16)][2:4] == (False, False)                                # no MPR, hence no press conference
    assert rows[D(2027, 11, 4)][2:4] == (True, True) and rows[D(2026, 9, 17)][2:4] == (False, False)


def test_boe_year_comes_from_the_heading_and_2027_is_now_confirmed(parsed):
    """The rows carry no year: 'Thursday 4 February' under '2027 confirmed dates' is 2027-02-04 (the old parser looked for
    '2027 provisional dates', found nothing and read every row as 2026)."""
    new = parsed["GBP"]
    assert [m["date"] for m in new] == [m["date"] for m in C.parse_boe(cut("boe"))]       # the same 16 dates as on the old page
    assert [m["date"] for m in new if m["date"].year == 2027] == [D(2027, 2, 4), D(2027, 3, 18), D(2027, 4, 29), D(2027, 6, 17), D(2027, 7, 29),
                                                                  D(2027, 9, 16), D(2027, 11, 4), D(2027, 12, 16)]
    assert not any(m.get("provisional") for m in new)
    rows = {r[0]: r for r in M.build_rows("GBP", new, BANKS, BOE_EVIDENCE)}
    assert all(r[5] == BOE_EVIDENCE for r in rows.values()) and rows[D(2027, 12, 16)][2:4] == (False, False)
    page = "<p>2028 provisional dates Thursday 3 February x 2027 confirmed dates Thursday 4 February y</p>"
    assert [(m["date"], m.get("provisional")) for m in C.parse_boe(page)] == [(D(2028, 2, 3), True), (D(2027, 2, 4), False)]


def test_flags_come_from_the_page_where_it_states_them_else_from_the_config_months(parsed):
    usd = {r[0]: r for r in M.build_rows("USD", parsed["USD"], BANKS, EVIDENCE)}
    assert usd[D(2026, 9, 16)][1:4] == (D(2026, 9, 15), True, True) and usd[D(2026, 10, 28)][2] is False
    rba = {r[0]: r for r in M.build_rows("AUD", parsed["AUD"], BANKS, EVIDENCE)}
    assert rba[D(2026, 8, 11)][2] is True and rba[D(2026, 9, 29)][2] is False           # SMP months Feb/May/Aug/Nov (config)
    assert rba[D(2026, 9, 29)][1] == D(2026, 9, 28)
    chf = M.build_rows("CHF", parsed["CHF"], BANKS, EVIDENCE)
    assert chf[0][0] == D(2025, 9, 25) and len(chf) == 10 and all(r[2] and r[3] for r in chf)
    assert D(2025, 6, 19) not in {r[0] for r in chf}                                    # before the last 4 SNB decisions


def test_one_day_meetings_carry_no_first_day(parsed, rbnz):
    for bank in ("GBP", "CAD", "CHF"):
        assert all(r[1] is None for r in M.build_rows(bank, parsed[bank], BANKS, EVIDENCE))
    assert all(r[1] is None for r in M.build_rows("NZD", [], BANKS, EVIDENCE, manual=rbnz))


def test_rbnz_rows_are_the_manual_calendar_and_never_verified(rbnz):
    rows = M.build_rows("NZD", [], BANKS, EVIDENCE, manual=rbnz)
    assert [(r[0], r[2], r[4], r[5]) for r in rows][:3] == [(D(2026, 2, 18), True, "manual", False), (D(2026, 4, 8), False, "manual", False), (D(2026, 5, 27), True, "manual", False)]
    assert len(rows) == 15 and rows[-1][0] == D(2027, 12, 8)                           # the release runs to 2028-02-09: rows to the end of the range
    assert [(r[0], r[2]) for r in rows if r[0].year == 2027][:3] == [(D(2027, 2, 10), False), (D(2027, 3, 17), True), (D(2027, 5, 5), False)]


def test_a_new_evidence_date_changes_only_the_verified_dates_and_the_meta_line(parsed, ecb_days, rbnz):
    a = M.build(parsed, BANKS, EVIDENCE, ecb_days, rbnz, BY_BANK)
    b = M.build(parsed, BANKS, D(2026, 10, 1), ecb_days, rbnz, BY_BANK)
    diff = [(x, y) for x, y in zip(a.splitlines(), b.splitlines()) if x != y]
    assert diff and all(("verified: 2026-09-20" in x and "verified: 2026-10-01" in y) or x.startswith("meta:") for x, y in diff)
    assert len(a.splitlines()) == len(b.splitlines())
