"""Fed SEP parser (real cut of the 16 Sep 2026, 10 Dec 2025 and 18 Mar 2026 pages), the projections dataset, and the
manual RBNZ file (schema + --status warnings)."""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import yaml

from src import cb_collect as cc
from src import cb_datasets as ds
from src.cb_sources.fed_sep import median_from_dots, parse_sep, sep_dates

FIX = Path(__file__).parent / "fixtures" / "cb"
ROOT = Path(__file__).resolve().parents[1]
D = date


def page(ymd: str) -> str:
    return (FIX / f"fed_sep_{ymd}_cut.htm").read_text()


def rows_of(ymd: str):
    return parse_sep(page(ymd), D(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])))


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_sep_2026_09_16_medians_from_the_dots():
    rows = rows_of("20260916")
    med = {r["horizon"]: r for r in rows if r["kind"] == "median_from_dots"}
    assert [med[h]["value"] for h in ("2026", "2027", "2028", "2029", "longer_run")] == [4.125, 4.125, 3.875, 3.625, 3.25]
    assert [med[h]["count"] for h in ("2026", "2027", "2028", "2029", "longer_run")] == [18, 18, 17, 17, 18]     # participants
    assert all(r["variable"] == "federal_funds_rate" and r["level"] is None and r["bank"] == "USD" for r in med.values())


def test_sep_2026_09_16_every_dot_per_level_and_year():
    dots: dict = {}
    for r in rows_of("20260916"):
        if r["kind"] == "dot":
            dots.setdefault(r["horizon"], {})[r["level"]] = r["count"]
    assert dots["2026"] == {4.375: 4, 4.125: 12, 3.875: 2}
    assert dots["2027"] == {4.375: 8, 4.125: 6, 3.625: 3, 3.125: 1}
    assert dots["2028"] == {4.125: 4, 3.875: 5, 3.625: 3, 3.375: 1, 3.125: 4}
    assert dots["2029"] == {3.875: 3, 3.625: 7, 3.375: 2, 3.125: 4, 2.875: 1}
    assert dots["longer_run"] == {3.875: 2, 3.75: 1, 3.625: 2, 3.5: 2, 3.375: 1, 3.25: 2, 3.125: 1, 3.0: 6, 2.875: 1}
    assert sum(1 for r in rows_of("20260916") if r["kind"] == "dot") == 26                     # dot rows
    assert {h: sum(v.values()) for h, v in dots.items()} == {"2026": 18, "2027": 18, "2028": 17, "2029": 17, "longer_run": 18}


def test_sep_2026_09_16_published_medians_of_table_1():
    pub = {}
    for r in rows_of("20260916"):
        if r["kind"] == "median_published":
            pub.setdefault(r["variable"], {})[r["horizon"]] = r["value"]
    assert pub["federal_funds_rate"] == {"2026": 4.1, "2027": 4.1, "2028": 3.9, "2029": 3.6, "longer_run": 3.2}    # rounded by the Fed
    assert pub["change_in_real_gdp"] == {"2026": 2.3, "2027": 2.4, "2028": 2.2, "2029": 2.1, "longer_run": 2.0}
    assert pub["unemployment_rate"] == {"2026": 4.1, "2027": 4.1, "2028": 4.1, "2029": 4.1, "longer_run": 4.2}
    assert pub["pce_inflation"] == {"2026": 3.7, "2027": 2.3, "2028": 2.1, "2029": 2.0, "longer_run": 2.0}
    assert pub["core_pce_inflation"] == {"2026": 3.4, "2027": 2.5, "2028": 2.2, "2029": 2.0}          # no longer-run column for core
    assert sum(1 for r in rows_of("20260916") if r["kind"] == "median_published") == 24
    assert len(rows_of("20260916")) == 55


def test_the_horizons_follow_each_pages_own_header():
    dec = {r["horizon"] for r in rows_of("20251210") if r["kind"] == "median_from_dots"}
    mar = {r["horizon"] for r in rows_of("20260318") if r["kind"] == "median_from_dots"}
    assert dec == {"2025", "2026", "2027", "2028", "longer_run"} and mar == {"2026", "2027", "2028", "longer_run"}
    med = {r["horizon"]: r["value"] for r in rows_of("20251210") if r["kind"] == "median_from_dots"}
    assert med == {"2025": 3.625, "2026": 3.375, "2027": 3.125, "2028": 3.125, "longer_run": 3.0}
    assert {r["count"] for r in rows_of("20251210") if r["kind"] == "median_from_dots"} == {19}
    pub = {r["horizon"]: r["value"] for r in rows_of("20260318") if r["kind"] == "median_published" and r["variable"] == "federal_funds_rate"}
    assert pub == {"2026": 3.4, "2027": 3.1, "2028": 3.1, "longer_run": 3.1}                       # the range block is not read as a median


def test_rows_carry_source_and_meeting_date():
    r = rows_of("20260916")[0]
    assert r["meeting_date"] == D(2026, 9, 16) and r["unit"] == "percent"
    assert r["source_url"] == "https://www.federalreserve.gov/monetarypolicy/fomcprojtabl20260916.htm"
    assert rows_of("20260916") == rows_of("20260916")                                             # deterministic


def test_median_from_dots_odd_even_and_empty():
    assert median_from_dots({4.125: 12, 4.375: 4, 3.875: 2}) == 4.125                              # 18 dots: mean of the 9th and 10th
    assert median_from_dots({3.0: 1, 3.25: 1}) == 3.125
    assert median_from_dots({3.0: 1, 3.25: 1, 3.5: 1}) == 3.25
    assert median_from_dots({}) is None


def test_a_page_without_the_tables_is_a_parse_failure():
    with pytest.raises(ValueError, match="SEP tables not found"):
        parse_sep("<html><body>maintenance</body></html>", D(2026, 9, 16))


def test_sep_dates_from_the_calendar_page_links():
    html = (FIX / "fomccalendars_sep_links_cut.htm").read_text()
    assert sep_dates(html, D(2026, 9, 20)) == [D(2025, 3, 19), D(2025, 6, 18), D(2025, 9, 17), D(2026, 3, 18), D(2026, 6, 17), D(2026, 9, 16)]
    assert sep_dates(html, D(2026, 6, 30))[-1] == D(2026, 6, 17)                                    # not yet released: not listed


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class FakeSep:
    last_status = last_note = ""

    def __init__(self, dates, fail=()):
        self._dates, self.fail, self.fetched = dates, set(fail), []

    def dates(self, today):
        return self._dates

    def page(self, d):
        self.fetched.append(d)
        if d in self.fail:
            self.last_status, self.last_note = "UNREACHABLE", "HTTP 503"
            return None
        return page(d.strftime("%Y%m%d")) if d.strftime("%Y%m%d") in ("20260916", "20251210", "20260318") else "<html>no tables</html>"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_projections_file_fetches_only_what_is_missing_and_is_idempotent(tmp_path):
    paths = cc.Paths(tmp_path / "cb")
    src = FakeSep([D(2025, 12, 10), D(2026, 3, 18), D(2026, 9, 16)])
    rep = ds.run_projections(paths, D(2026, 9, 20), source=src)
    assert rep.fetched == [D(2025, 12, 10), D(2026, 3, 18), D(2026, 9, 16)] and rep.written and not rep.failed
    assert rep.total == 64 + 51 + 55
    first = sha(paths.projections)
    src2 = FakeSep([D(2025, 12, 10), D(2026, 3, 18), D(2026, 9, 16)])
    rep = ds.run_projections(paths, D(2026, 9, 21), source=src2)
    assert src2.fetched == [] and not rep.written and sha(paths.projections) == first            # nothing new: no request, same bytes
    t = pq.read_table(paths.projections)
    assert t.schema.names == ["bank", "meeting_date", "variable", "horizon", "kind", "level", "count", "value", "unit", "source_url"]
    keys = [(r["bank"], r["meeting_date"], r["variable"], r["horizon"], r["kind"], r["level"]) for r in t.to_pylist()]
    assert len(keys) == len(set(keys))


def test_a_new_sep_is_appended_without_touching_the_old_rows(tmp_path):
    paths = cc.Paths(tmp_path / "cb")
    ds.run_projections(paths, D(2026, 9, 20), source=FakeSep([D(2025, 12, 10), D(2026, 3, 18)]))
    old = {(r["meeting_date"], r["variable"], r["horizon"], r["kind"], r["level"]): r for r in ds.load_projections(paths)}
    src = FakeSep([D(2025, 12, 10), D(2026, 3, 18), D(2026, 9, 16)])
    rep = ds.run_projections(paths, D(2026, 9, 20), source=src)
    assert src.fetched == [D(2026, 9, 16)] and rep.fetched == [D(2026, 9, 16)]
    new = {(r["meeting_date"], r["variable"], r["horizon"], r["kind"], r["level"]): r for r in ds.load_projections(paths)}
    assert all(new[k] == v for k, v in old.items()) and len(new) == len(old) + 55


def test_only_the_last_four_seps_are_kept_in_view_and_failures_are_reported(tmp_path):
    paths = cc.Paths(tmp_path / "cb")
    dates = [D(2025, 3, 19), D(2025, 6, 18), D(2025, 9, 17), D(2025, 12, 10), D(2026, 3, 18), D(2026, 9, 16)]
    src = FakeSep(dates, fail={D(2026, 3, 18)})
    rep = ds.run_projections(paths, D(2026, 9, 20), source=src)
    assert rep.sep_dates == dates[-4:] and D(2025, 3, 19) not in src.fetched
    assert "2026-03-18" in rep.failed and "503" in rep.failed["2026-03-18"]
    assert D(2025, 9, 17) in [d for d in src.fetched] and "2025-09-17" in rep.failed          # a page with no tables: PARSE-FAIL, not a crash
    assert {r["meeting_date"] for r in ds.load_projections(paths)} == {D(2025, 12, 10), D(2026, 9, 16)}


def test_calendar_page_failure_is_reported_not_raised(tmp_path):
    class Down(FakeSep):
        def dates(self, today):
            self.last_status, self.last_note = "UNREACHABLE", "HTTP 503"
            return None
    rep = ds.run_projections(cc.Paths(tmp_path / "cb"), D(2026, 9, 20), source=Down([]))
    assert "calendar" in rep.failed and not rep.written
    text, _ = ds.projections_report(rep)
    assert "calendar page failed" in text


# ---------------------------------------------------------------------------
# data/cb/manual/rbnz.yaml
# ---------------------------------------------------------------------------

def seed():
    return yaml.safe_load((ROOT / "data" / "cb" / "manual" / "rbnz.yaml").read_text())


def nz_meetings():
    return ds.load_meetings(ROOT / "data" / "cb" / "meetings.yaml")["NZD"]


def test_seed_rbnz_file_is_valid_and_needs_only_the_sep_2026_mps():
    doc = seed()
    assert ds.validate_rbnz(doc) == []
    assert [(e["meeting"], e["status"]) for e in doc["mps"]] == [(D(2026, 9, 2), "placeholder")]
    assert [m["date"] for m in doc["published_calendar"]["meetings"]] == [m["date"] for m in nz_meetings()]      # the meetings.yaml source
    assert doc["calendar_2027"]["status"] == "pending" and doc["calendar_2027"]["published_until"] == D(2027, 2, 17)
    mps = {m["date"] for m in nz_meetings() if m["has_projections"]}
    assert {e["meeting"] for e in doc["mps"]} <= mps                                    # every entry is a real MPS of the calendar


def filled(meeting=D(2026, 9, 2)):
    return {"meeting": meeting, "status": "filled", "source": {"url": "https://www.rbnz.govt.nz/mps.pdf", "page": "Table 6.1", "retrieved": D(2026, 9, 21)},
            "ocr_track": [{"period": "2026Q4", "value": 2.75}, {"period": "2027Q1", "value": 2.85}], "bank_bill_90d": [{"period": "2026Q4", "value": 2.9}]}


def test_schema_rejects_the_usual_mistakes():
    def with_mps(*entries):
        d = seed()
        d["mps"] = list(entries)
        return ds.validate_rbnz(d)
    assert with_mps(filled()) == []
    e = filled(); e["source"] = None
    assert any("needs source.url and source.page" in x for x in with_mps(e))                 # nothing typed from memory
    e = filled(); e["ocr_track"] = []
    assert any("filled without an ocr_track" in x for x in with_mps(e))
    e = filled(); e["ocr_track"] = [{"period": "2026Q4", "value": "2.75%"}]
    assert any("period (str) and value (number)" in x for x in with_mps(e))
    e = filled(); e["status"] = "done"
    assert any("status must be placeholder | filled" in x for x in with_mps(e))
    assert any("duplicate meeting" in x for x in with_mps(filled(), filled()))
    e = filled(); e["meeting"] = "2026-09-02"
    assert any("meeting must be a date" in x for x in with_mps(e))
    d = seed(); d["calendar_2027"]["status"] = "entered"
    assert any("entered without meetings" in x for x in ds.validate_rbnz(d))
    d = seed(); del d["mps"]
    assert any("missing key 'mps'" in x for x in ds.validate_rbnz(d))
    d = seed(); d["published_calendar"]["meetings"][0]["has_projections"] = "yes"
    assert any("published_calendar meeting needs date and has_projections" in x for x in ds.validate_rbnz(d))
    d = seed(); d["published_calendar"] = {}
    assert any("published_calendar needs a source and meetings" in x for x in ds.validate_rbnz(d))
    assert ds.validate_rbnz({}) == ["file missing or empty"]


def test_status_warns_only_for_the_latest_mps_and_clears_once_it_is_filled():
    doc = seed()
    w = ds.rbnz_warnings(doc, nz_meetings(), D(2026, 9, 20))
    assert w == ["RBNZ MPS 2026-09-02: no OCR track (placeholder) - fill data/cb/manual/rbnz.yaml"]         # Feb / May are not needed
    doc["mps"][0] = filled(D(2026, 9, 2))
    assert ds.rbnz_warnings(doc, nz_meetings(), D(2026, 9, 20)) == []
    # a NEW MPS (9 Dec) after its date, with no entry at all
    later = ds.rbnz_warnings(doc, nz_meetings(), D(2026, 12, 10))
    assert later == ["RBNZ MPS 2026-12-09: no OCR track (no entry) - fill data/cb/manual/rbnz.yaml"]
    assert ds.rbnz_warnings(doc, nz_meetings(), D(2026, 12, 8)) == []                           # before the MPS: nothing to ask for


def test_status_warns_when_the_2027_calendar_runs_out():
    doc = seed()
    assert not any("calendar" in x for x in ds.rbnz_warnings(doc, nz_meetings(), D(2027, 1, 15)))
    w = ds.rbnz_warnings(doc, nz_meetings(), D(2027, 2, 17))
    assert any("RBNZ calendar after 2027-02-17 not entered yet" in x for x in w)
    doc["calendar_2027"] = {"status": "entered", "meetings": [{"date": D(2027, 5, 26), "has_projections": True}]}
    assert not any("calendar" in x for x in ds.rbnz_warnings(doc, nz_meetings(), D(2027, 3, 1)))


def test_an_invalid_file_is_reported_by_status_not_raised():
    bad = seed()
    bad["mps"][0]["status"] = "??"
    w = ds.rbnz_warnings(bad, nz_meetings(), D(2026, 9, 20))
    assert w and all(x.startswith("RBNZ manual file invalid:") for x in w)


def test_status_shows_the_rbnz_warnings(tmp_path):
    paths = cc.Paths(tmp_path / "cb")
    (paths.manual).mkdir(parents=True)
    (paths.manual / "rbnz.yaml").write_text((ROOT / "data" / "cb" / "manual" / "rbnz.yaml").read_text())
    paths.meetings.write_text((ROOT / "data" / "cb" / "meetings.yaml").read_text())
    text, md = ds.extra_status(paths, D(2026, 9, 20))
    assert "RBNZ manual file" in text and "WARN RBNZ MPS 2026-09-02: no OCR track (placeholder)" in text and "RBNZ MPS 2026-09-02" in md
    assert "2026-02-18" not in text.split("RBNZ manual file")[1].split("\n\n")[0]
