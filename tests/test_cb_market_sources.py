"""Central Banks market adapters (phase 1A): deterministic parse from REAL cut fixtures
(tests/fixtures/cb/, captured 2026-09-18/19), reference windows per exchange convention, cutoff / as-of
inference, conditional GET and the never-raise contract. No network."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from requests.structures import CaseInsensitiveDict

from src.cb_sources import ADAPTERS
from src.cb_sources.base import (FetchResult, add_months_date, first_wednesday_after_9th, imm_window, month_bounds,
                                 third_wednesday)
from src.cb_sources.market import (AsxBb, AsxIb, AtlantaMpt, BocTbills, BoeOis, EcbAaaForward, JpxTona, MxCorra,
                                   RbaBankBills, TreasuryBills, tenor_label)

FIX = Path(__file__).parent / "fixtures" / "cb"
NOW = datetime(2026, 9, 19, 16, 35, tzinfo=timezone.utc)          # a Saturday


def mk(cls, now: datetime = NOW):
    return cls(now=lambda: now)


class Resp:
    """Minimal requests.Response stand-in."""
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code = status
        self.content = content if isinstance(content, bytes) else content.encode()
        self.headers = CaseInsensitiveDict(headers or {})

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("src.rate_sources.time.sleep", lambda s: None)


def serve(monkeypatch, routes):
    """Route requests.get by URL substring; records (url, headers) of every call."""
    calls = []

    def fake_get(url, headers=None, timeout=None, **kw):
        calls.append((url, dict(headers or {})))
        for needle, resp in routes.items():
            if needle in url:
                return resp(headers or {}) if callable(resp) else resp
        raise AssertionError(f"unrouted url {url}")
    monkeypatch.setattr("src.rate_sources.requests.get", fake_get)
    return calls


# ---------------------------------------------------------------------------
# Convention helpers
# ---------------------------------------------------------------------------

def test_third_wednesday_and_imm_window():
    assert third_wednesday(2026, 12) == date(2026, 12, 16)
    assert third_wednesday(2027, 3) == date(2027, 3, 17)
    assert imm_window(2026, 12) == (date(2026, 12, 16), date(2027, 3, 17))
    assert imm_window(2026, 9) == (date(2026, 9, 16), date(2026, 12, 16))


def test_first_wednesday_after_the_9th_includes_the_10th():
    assert first_wednesday_after_9th(2026, 12) == date(2026, 12, 16)
    assert first_wednesday_after_9th(2027, 3) == date(2027, 3, 10)
    assert first_wednesday_after_9th(2026, 6) == date(2026, 6, 10)      # 10 June 2026 is itself a Wednesday


def test_month_helpers():
    assert month_bounds(2026, 12) == (date(2026, 12, 1), date(2027, 1, 1))
    assert add_months_date(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert tenor_label(1.5) == "1.5M" and tenor_label(12.0) == "12M"


# ---------------------------------------------------------------------------
# Official-history sources
# ---------------------------------------------------------------------------

def test_mpt_parse_is_deterministic_and_windows_are_explicit():
    blob = (FIX / "mpt_data_cut.xlsx").read_bytes()
    a = mk(AtlantaMpt).parse(blob, date(2026, 9, 1))
    assert a == mk(AtlantaMpt).parse(blob, date(2026, 9, 1))
    # only "Rate: mean" (mode / Prob rows filtered); 2029-09-19 and 2029-12-19 starts are beyond 36 months
    assert [(q.asof, q.contract) for q in a] == [
        (date(2026, 9, 16), "202612"), (date(2026, 9, 16), "202703"),
        (date(2026, 9, 17), "202612"), (date(2026, 9, 17), "202703")]
    q = a[2]
    assert (q.value, q.unit, q.field, q.tenor_months) == (431.0, "bp", "mean", 3.0)
    assert (q.ref_start, q.ref_end) == (date(2026, 12, 16), date(2027, 3, 17))
    assert (a[3].ref_start, a[3].ref_end, a[3].value) == (date(2027, 3, 17), date(2027, 6, 16), 454.9)
    assert a[0].value == 430.02 and not q.asof_inferred and q.source == "atlantafed_mpt" and q.currency == "USD"


def test_mpt_stale_dimension_and_since_filter():
    """The publisher's sheet carries <dimension ref="A1"/> (fixture reproduces it): read-only mode must not stop
    after the first cell. `since` keeps older as-ofs out."""
    blob = (FIX / "mpt_data_cut.xlsx").read_bytes()
    assert b'<dimension ref="A1"/>' in __import__("zipfile").ZipFile(__import__("io").BytesIO(blob)).read("xl/worksheets/sheet1.xml")
    assert len(mk(AtlantaMpt).parse(blob, date(2026, 8, 1))) == 6
    assert mk(AtlantaMpt).parse(blob, date(2026, 9, 18)) == []


def test_boe_ois_parse_normalises_months_and_caps_horizon():
    b = mk(BoeOis)
    cur = b.parse((FIX / "boe_latest_cut.zip").read_bytes(), r"OIS daily data current month", date(2026, 9, 1))
    assert sorted(cur) == [date(2026, 9, 16), date(2026, 9, 17)]
    pts = dict(cur[date(2026, 9, 17)])
    assert list(pts)[:3] == [1.0, 2.0, 3.0] and max(pts) == 36.0 and len(pts) == 36    # 1.0000000400000015 -> 1.0; 37..40 dropped
    assert pts[12.0] == pytest.approx(4.793250610342354)
    qs = b.quotes(cur)
    assert len(qs) == 72 and {q.unit for q in qs} == {"percent"} and {q.field for q in qs} == {"fwd"}
    q = next(q for q in qs if q.asof == date(2026, 9, 16) and q.contract == "12M")
    assert (q.tenor_months, q.ref_start, q.ref_end, q.value) == (12.0, None, None, pytest.approx(4.741277543427588))


def test_boe_ois_picks_the_right_member_of_each_zip():
    b = mk(BoeOis)
    his = b.parse((FIX / "boe_hist_cut.zip").read_bytes(), r"2025 to present", date(2026, 8, 1))
    assert list(his) == [date(2026, 8, 28)]                # the "2016 to 2024" decoy has no data rows
    assert dict(his[date(2026, 8, 28)])[1.0] == pytest.approx(3.738407670411019)
    with pytest.raises(ValueError, match="no workbook matching"):
        b.parse((FIX / "boe_hist_cut.zip").read_bytes(), r"nonexistent", date(2026, 8, 1))


def test_boe_ois_fetch_stitches_history_when_since_precedes_current_month(monkeypatch):
    calls = serve(monkeypatch, {
        "latest-yield-curve-data.zip": Resp(200, (FIX / "boe_latest_cut.zip").read_bytes(),
                                            {"Last-Modified": "Fri, 18 Sep 2026 12:58:26 GMT"}),
        "oisddata.zip": Resp(200, (FIX / "boe_hist_cut.zip").read_bytes(), {"Last-Modified": "Thu, 03 Sep 2026 14:52:11 GMT"})})
    res = mk(BoeOis).fetch(since=date(2026, 8, 1))
    assert sorted({q.asof for q in res.quotes}) == [date(2026, 8, 28), date(2026, 9, 16), date(2026, 9, 17)]
    assert res.state == {"latest": {"etag": None, "last_modified": "Fri, 18 Sep 2026 12:58:26 GMT"},
                         "history": {"etag": None, "last_modified": "Thu, 03 Sep 2026 14:52:11 GMT"}}
    calls.clear()
    res = mk(BoeOis).fetch(since=date(2026, 9, 9))          # since inside the current month: history not needed
    assert len(calls) == 1 and "latest-yield-curve-data" in calls[0][0]
    assert sorted({q.asof for q in res.quotes}) == [date(2026, 9, 16), date(2026, 9, 17)]


def test_treasury_parse_bill_tenors():
    t = mk(TreasuryBills)
    qs = t.parse((FIX / "ust_par_curve_cut.csv").read_text(), date(2026, 9, 1))
    assert len(qs) == 27 and {q.asof for q in qs} == {date(2026, 9, 18), date(2026, 9, 17), date(2026, 9, 16)}
    got = {q.contract: q.value for q in qs if q.asof == date(2026, 9, 18)}
    assert got == {"1M": 3.97, "1.5M": 3.98, "2M": 4.1, "3M": 4.14, "4M": 4.24, "6M": 4.24, "12M": 4.44, "24M": 4.76, "36M": 4.83}
    assert all(q.unit == "percent" and q.field == "yield" and q.ref_start is None for q in qs)
    assert t.parse((FIX / "ust_par_curve_cut.csv").read_text(), date(2026, 9, 18)) != []
    assert {q.asof for q in t.parse((FIX / "ust_par_curve_cut.csv").read_text(), date(2026, 9, 18))} == {date(2026, 9, 18)}


def test_treasury_missing_cell_is_skipped_but_zero_is_a_value():
    text = 'Date,"1 Mo","3 Mo","6 Mo"\n09/18/2026,0.00,,4.24\n'
    qs = mk(TreasuryBills).parse(text, date(2026, 9, 1))
    assert {q.contract: q.value for q in qs} == {"1M": 0.0, "6M": 4.24}        # blank skipped, 0.00 kept as a value
    assert next(q for q in qs if q.contract == "1M").value == 0.0


def test_boc_tbills_parse():
    qs = mk(BocTbills).parse(json.loads((FIX / "boc_tbills_cut.json").read_text()), date(2026, 9, 1))
    assert len(qs) == 8
    d17 = {q.contract: q.value for q in qs if q.asof == date(2026, 9, 17)}
    assert d17 == {"1M": 2.28, "2M": 2.31, "3M": 2.33, "6M": 2.58}
    assert [q.tenor_months for q in qs if q.asof == date(2026, 9, 16)] == [1.0, 2.0, 3.0, 6.0]
    assert all(q.currency == "CAD" and q.unit == "percent" for q in qs)


def test_boc_tbills_ignores_missing_series_values():
    payload = {"observations": [{"d": "2026-09-18", "TB.CDN.30D.MID": {"v": "2.2"}, "TB.CDN.90D.MID": {}}]}
    assert [(q.contract, q.value) for q in mk(BocTbills).parse(payload, date(2026, 9, 1))] == [("1M", 2.2)]


def test_ecb_aaa_forward_parse_and_key_list():
    e = mk(EcbAaaForward)
    qs = e.parse((FIX / "ecb_if_cut.csv").read_text(), date(2026, 9, 1))
    assert len(qs) == 8                        # IF_4Y (48M) beyond horizon, SR_3M is not an IF_ series
    assert [(q.asof, q.contract) for q in qs if q.asof == date(2026, 9, 17)] == [
        (date(2026, 9, 17), "3M"), (date(2026, 9, 17), "12M"), (date(2026, 9, 17), "27M"), (date(2026, 9, 17), "36M")]
    assert next(q for q in qs if q.asof == date(2026, 9, 16) and q.contract == "12M").value == pytest.approx(3.3276078926)
    keys = e.keys()
    assert len(keys) == 34 and keys[0] == "IF_3M" and keys[8] == "IF_11M" and keys[9] == "IF_1Y" and keys[-1] == "IF_3Y"
    assert [e.tenor_months(k) for k in ("IF_3M", "IF_1Y", "IF_2Y3M", "IF_30Y", "SR_3M", "IF_")] == [3, 12, 27, 360, None, None]


def test_rba_bank_bills_parse_skips_the_still_empty_latest_row():
    qs = mk(RbaBankBills).parse((FIX / "rba_f1_cut.csv").read_text(), date(2026, 9, 1))
    # the real 18-Sep-2026 row is short (only the total-return index is filled) -> no bank-bill rows for it
    assert sorted({q.asof for q in qs}) == [date(2026, 9, 15), date(2026, 9, 16), date(2026, 9, 17)]
    assert {q.contract: q.value for q in qs if q.asof == date(2026, 9, 17)} == {"1M": 4.41, "3M": 4.68, "6M": 5.07}
    assert RbaBankBills.ua and "Mozilla" not in RbaBankBills.ua          # a Chrome UA earns a 403 from rba.gov.au
    assert mk(RbaBankBills).parse((FIX / "rba_f1_cut.csv").read_text(), date(2026, 9, 17))[0].asof == date(2026, 9, 17)


def test_rba_f1_layout_change_is_a_parse_failure_not_an_exception(monkeypatch):
    serve(monkeypatch, {"f1-data.csv": Resp(200, "Title,X\nSeries ID,ABC\n17-Sep-2026,1\n")})
    s = mk(RbaBankBills)
    assert s.fetch(since=date(2026, 9, 1)) is None
    assert s.last_status == "PARSE-FAIL" and "F1 ids missing" in s.last_note


# ---------------------------------------------------------------------------
# Snapshot sources
# ---------------------------------------------------------------------------

def test_jpx_finds_the_latest_csv_link():
    assert mk(JpxTona).find_csv((FIX / "jpx_page_cut.html").read_text()) == (
        "/english/markets/derivatives/settlement-price/tvdivq00000014l6-att/rb_e20260918.csv", date(2026, 9, 18))
    assert mk(JpxTona).find_csv("<html>JS only</html>") is None


def test_jpx_windows_use_the_start_month_e2_case():
    """Errata E2: the contract month is the START month of the reference period; the code in the issue name
    (FUT_TOA3M_270316) is the last trading day and must NOT drive the window."""
    text = (FIX / "jpx_rb_e20260918_cut.csv").read_bytes().decode("cp932")
    qs, raw = mk(JpxTona).parse(text, date(2026, 9, 18))
    by = {q.contract: q for q in qs}
    assert (by["202612"].ref_start, by["202612"].ref_end, by["202612"].value) == (date(2026, 12, 16), date(2027, 3, 17), 98.525)
    assert (by["202609"].ref_start, by["202609"].ref_end, by["202609"].value) == (date(2026, 9, 16), date(2026, 12, 16), 98.775)
    assert (by["202906"].ref_start, by["202906"].ref_end) == (date(2029, 6, 20), date(2029, 9, 19))
    assert len(qs) == 12 and "202909" not in by and "202912" not in by      # start beyond asof + 36M
    assert all(q.unit == "index_points" and q.field == "settle" and q.tenor_months == 3.0 and q.asof == date(2026, 9, 18) for q in qs)
    # raw keeps only the relevant rows (no Nikkei 225 / header lines)
    assert raw.count("\n") == 11 and all("FUT_TOA3M_" in ln for ln in raw.splitlines())
    assert qs == mk(JpxTona).parse(text, date(2026, 9, 18))[0]


def test_jpx_fetch_uses_conditional_get_on_the_csv(monkeypatch):
    csv_bytes = (FIX / "jpx_rb_e20260918_cut.csv").read_bytes()
    calls = serve(monkeypatch, {
        "settlement-price/index.html": Resp(200, (FIX / "jpx_page_cut.html").read_bytes()),
        "rb_e20260918.csv": lambda h: Resp(304) if h.get("If-Modified-Since") == "Fri, 18 Sep 2026 07:44:43 GMT"
        else Resp(200, csv_bytes, {"Last-Modified": "Fri, 18 Sep 2026 07:44:43 GMT"})})
    first = mk(JpxTona).fetch()
    assert first.status == "ok" and first.raw["asof"] == date(2026, 9, 18) and first.state["last_modified"].endswith("07:44:43 GMT")
    again = mk(JpxTona).fetch(state=first.state)
    assert again.status == "not_modified" and again.quotes == [] and again.state == first.state
    assert calls[-1][1]["If-Modified-Since"] == "Fri, 18 Sep 2026 07:44:43 GMT"


def test_mx_parse_windows_dedup_and_inferred_flag():
    m = mk(MxCorra)
    qs, raw = m.parse((FIX / "mx_expectations_cut.html").read_text(), date(2026, 9, 18))
    assert len(qs) == 8                                       # the repeated CRAU26 row of the page is not doubled
    cra = {q.contract: q for q in qs if q.instrument == "corra_3m_futures"}
    coa = {q.contract: q for q in qs if q.instrument == "corra_1m_futures"}
    assert (cra["202612"].ref_start, cra["202612"].ref_end, cra["202612"].value) == (date(2026, 12, 16), date(2027, 3, 17), 97.225)
    assert cra["202609"].tenor_months == 3.0 and cra["202609"].value == 97.615
    assert (coa["202610"].ref_start, coa["202610"].ref_end, coa["202610"].tenor_months) == (date(2026, 10, 1), date(2026, 11, 1), 1.0)
    assert coa["202609"].value == 97.7125
    assert all(q.asof_inferred and q.unit == "index_points" and q.currency == "CAD" for q in qs)
    assert raw.splitlines()[0] == "September 2026,CRAU26,97.615" and len(raw.splitlines()) == 8


def test_mx_label_and_code_mismatch_is_rejected():
    html = (FIX / "mx_expectations_cut.html").read_text().replace("(CRAZ26)", "(CRAH26)")
    with pytest.raises(ValueError, match="mismatch"):
        mk(MxCorra).parse(html, date(2026, 9, 18))


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc), None),                       # Fri inside 06:00-22:00 UTC -> skip
    (datetime(2026, 10, 9, 12, 41, tzinfo=timezone.utc), None),                      # Fri 12:41Z: the Friday session (after the 12:30Z data) - skip
    (datetime(2026, 10, 9, 8, 41, tzinfo=timezone.utc), None),                       # Fri 08:41Z: the session opened at 06:00Z - skip
    (datetime(2026, 9, 21, 5, 59, tzinfo=timezone.utc), date(2026, 9, 18)),          # Mon before the open -> last Friday
    (datetime(2026, 9, 18, 22, 0, tzinfo=timezone.utc), date(2026, 9, 18)),          # Fri after the window -> today
    (datetime(2026, 9, 19, 16, 35, tzinfo=timezone.utc), date(2026, 9, 18)),         # Sat: no session, window n/a
    (datetime(2026, 9, 20, 15, 0, tzinfo=timezone.utc), date(2026, 9, 18)),          # Sun
    (datetime(2026, 9, 21, 6, 0, tzinfo=timezone.utc), None),                        # Mon 06:00Z = 02:00 ET: the Monday session is open
    (datetime(2026, 9, 22, 5, 0, tzinfo=timezone.utc), date(2026, 9, 21)),           # Tue morning -> Monday
])
def test_mx_asof_is_inferred_from_fetch_time(now, expected):
    assert mk(MxCorra, now).infer_asof() == expected


def test_mx_intraday_fetch_is_skipped_without_touching_the_network(monkeypatch):
    serve(monkeypatch, {})                                    # any request would raise AssertionError
    res = mk(MxCorra, datetime(2026, 9, 17, 15, 0, tzinfo=timezone.utc)).fetch()
    assert res.status == "skipped" and res.quotes == [] and "intraday" in res.note


def test_mx_missing_cra_table_is_a_parse_failure(monkeypatch):
    serve(monkeypatch, {"canadian-interest-rate-expectations": Resp(200, "<html>redesigned</html>")})
    s = mk(MxCorra)
    assert s.fetch() is None and s.last_status == "PARSE-FAIL"


def test_asx_ib_window_is_the_calendar_month():
    qs, raw = mk(AsxIb).parse(json.loads((FIX / "asx_ib_cut.json").read_text()))
    assert [(q.contract, q.value, q.ref_start, q.ref_end) for q in qs[:2]] == [
        ("202609", 95.645, date(2026, 9, 1), date(2026, 10, 1)), ("202610", 95.435, date(2026, 10, 1), date(2026, 11, 1))]
    assert qs[-1].contract == "202701" and qs[-1].ref_end == date(2027, 2, 1)
    assert all(q.asof == date(2026, 9, 18) and q.tenor_months == 1.0 and q.currency == "AUD" for q in qs)
    assert raw.splitlines()[0] == "IBU2026,2026-09-28,95.645,2026-09-18"


def test_asx_bb_uses_the_settlement_month_and_computed_expiry_not_the_api_date():
    qs, raw = mk(AsxBb).parse(json.loads((FIX / "asx_bb_cut.json").read_text()))
    by = {q.contract: q for q in qs}
    # BBZ2026: API dateExpiry 2026-12-14 (Mon); first Wednesday after the 9th is 16 Dec
    assert (by["202612"].ref_start, by["202612"].ref_end, by["202612"].value) == (date(2026, 12, 16), date(2027, 3, 17), 96.55)
    # BBH2027: API 2027-03-08 (Mon) vs Wednesday 10 March
    assert by["202703"].ref_start == date(2027, 3, 10) and by["202703"].ref_end == date(2027, 6, 9)
    assert all(q.tenor_months == 3.0 and q.currency == "NZD" and q.unit == "index_points" for q in qs)
    assert "BBZ2026,2026-12-14,96.55,2026-09-18" in raw


def test_asx_items_without_a_settlement_and_foreign_symbols_are_skipped():
    d = json.loads((FIX / "asx_ib_cut.json").read_text())
    d["data"]["items"][0]["pricePreviousSettlement"] = None
    d["data"]["items"][1]["symbol"] = "XYZ2026"
    qs, _ = mk(AsxIb).parse(d)
    assert [q.contract for q in qs] == ["202611", "202612", "202701"]


def test_asx_horizon_drops_contracts_beyond_36_months():
    d = json.loads((FIX / "asx_ib_cut.json").read_text())
    d["data"]["items"][0].update(symbol="IBZ2029")           # Dec 2029 > 2026-09-18 + 36M
    qs, _ = mk(AsxIb).parse(d)
    assert "202912" not in [q.contract for q in qs] and len(qs) == 4


# ---------------------------------------------------------------------------
# Cutoff, conditional GET, never-raise
# ---------------------------------------------------------------------------

def test_is_final_only_after_the_eod_cutoff_on_the_current_exchange_day():
    early = mk(TreasuryBills, datetime(2026, 9, 18, 15, 0, tzinfo=timezone.utc))     # 11:00 New York
    late = mk(TreasuryBills, datetime(2026, 9, 18, 20, 30, tzinfo=timezone.utc))
    d = date(2026, 9, 18)
    assert not early.is_final(d) and late.is_final(d)
    assert early.is_final(d - timedelta(days=1)) and not late.is_final(d + timedelta(days=1))


def test_cutoff_uses_the_exchange_calendar_date_not_utc():
    tokyo = mk(JpxTona, datetime(2026, 9, 18, 16, 0, tzinfo=timezone.utc))            # already 19 Sep 01:00 in Tokyo
    assert tokyo.is_final(date(2026, 9, 18)) and not tokyo.is_final(date(2026, 9, 20))


def test_conditional_get_sends_validators_and_reports_304(monkeypatch):
    calls = serve(monkeypatch, {"mpt_histdata.xlsx": Resp(304)})
    res = mk(AtlantaMpt).fetch(since=date(2026, 9, 1), state={"etag": "abc", "last_modified": "Fri, 18 Sep 2026 14:24:44 GMT"})
    assert res.status == "not_modified" and res.quotes == [] and res.state["etag"] == "abc"
    assert calls[0][1]["If-None-Match"] == "abc" and calls[0][1]["If-Modified-Since"].startswith("Fri, 18 Sep")
    assert len(calls) == 1                                    # 304 is not retried


def test_first_download_is_unconditional_and_captures_validators(monkeypatch):
    calls = serve(monkeypatch, {"mpt_histdata.xlsx": Resp(200, (FIX / "mpt_data_cut.xlsx").read_bytes(),
                                                          {"ETag": "d0f17fe4", "Last-Modified": "Fri, 18 Sep 2026 14:24:44 GMT"})})
    res = mk(AtlantaMpt).fetch(since=date(2026, 9, 1))
    assert "If-None-Match" not in calls[0][1] and "If-Modified-Since" not in calls[0][1]
    assert res.status == "ok" and len(res.quotes) == 4
    assert res.state == {"etag": "d0f17fe4", "last_modified": "Fri, 18 Sep 2026 14:24:44 GMT"}


def test_http_failure_returns_none_with_status(monkeypatch):
    serve(monkeypatch, {"valet/observations": Resp(503)})
    s = mk(BocTbills)
    assert s.fetch() is None and s.last_status == "UNREACHABLE" and "503" in s.last_note


def test_botwall_html_instead_of_csv_is_reported(monkeypatch):
    serve(monkeypatch, {"f1-data.csv": Resp(200, "<html><body>Just a moment... requires JavaScript</body></html>")})
    s = mk(RbaBankBills)
    assert s.fetch() is None and s.last_status == "BOT-WALL"


def test_network_exception_is_contained(monkeypatch):
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("dns")
    monkeypatch.setattr("src.rate_sources.requests.get", boom)
    s = mk(EcbAaaForward)
    assert s.fetch() is None and s.last_status == "UNREACHABLE" and "ConnectionError" in s.last_note


def test_parser_exception_never_propagates(monkeypatch):
    serve(monkeypatch, {"canadian-interest-rate-expectations": Resp(200, "x")})
    monkeypatch.setattr(MxCorra, "parse", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    s = mk(MxCorra)
    assert s.fetch() is None and s.last_status == "PARSE-FAIL" and "boom" in s.last_note


def test_fetch_result_quotes_come_back_in_a_deterministic_order(monkeypatch):
    serve(monkeypatch, {"daily-treasury-rates": Resp(200, (FIX / "ust_par_curve_cut.csv").read_bytes())})
    res = mk(TreasuryBills).fetch(since=date(2026, 9, 1))
    keys = [(q.asof, q.tenor_months) for q in res.quotes]
    assert keys == sorted(keys) and res.status == "ok"


def test_every_adapter_is_registered_and_supports_only_its_currency():
    assert set(ADAPTERS) == {"atlantafed_mpt", "boe_ois", "jpx_tona", "mx_corra", "asx_ib", "asx_bb", "ust_bills",
                             "boc_tbills", "ecb_aaa_fwd", "rba_bank_bills", "eurex_ois_usd", "eurex_ois_usd_sofr", "eurex_ois_eur",
                             "eurex_ois_gbp", "eurex_ois_jpy", "eurex_ois_chf"}
    for sid, cls in ADAPTERS.items():
        s = cls(now=lambda: NOW, sid=sid)
        assert s.id == sid and s.supports(s.currency) and not s.supports("XXX")
        assert re.fullmatch(r"[A-Z]{3}", s.currency)


# ---------------------------------------------------------------------------
# Eurex Clearing OTC settlement curves (one shared file, six entries)
# ---------------------------------------------------------------------------

EUREX_NOW = datetime(2026, 10, 9, 11, 0, tzinfo=timezone.utc)
EUREX_CSV = FIX / "eurex_settlement_20261008_cut.csv"      # scripts/cb_cut_eurex_fixture.py from settlement-prices_20261008.csv
EUREX_PAGE = FIX / "eurex_settlement_page_cut.htm"          # the download block of the settlement-prices page, 2026-10-09
SIX = {"USD.FEDFUNDS.1D", "USD.SOFR.1D", "EUR.ESTR.1D", "GBP.SONIA.1D", "JPY.TONAR.1D", "CHF.SARON.1D"}


class StreamResp(Resp):
    def iter_lines(self, chunk_size=512):
        yield from self.content.splitlines()

    def close(self):
        pass


def eurex(sid="eurex_ois_usd", now=EUREX_NOW):
    from src.cb_sources.market import EurexOis
    return EurexOis(now=lambda: now, sid=sid)


@pytest.fixture
def eurex_cache():
    from src.cb_sources.market import EurexOis
    EurexOis.clear_cache()
    yield EurexOis
    EurexOis.clear_cache()


def test_eurex_page_link_and_file_date():
    href, day = eurex().find_csv(EUREX_PAGE.read_text())
    assert href == "/resource/blob/5618502/d5a768aed8873e65d125533f04ef3f2c/data/settlement-prices_20261008.csv" and day == date(2026, 10, 8)
    assert eurex().find_csv("<html><a href='/x/settlement-prices.csv'>no date</a></html>") is None


def test_eurex_parse_keeps_only_the_s_rows_of_the_six_curves_at_the_pillars():
    s = eurex()
    with open(EUREX_CSV, newline="") as fh:
        f = s.parse(fh, SIX)
    assert f.asof == date(2026, 10, 8) and set(f.curves) == SIX                          # decoys (EURIBOR 3M, DKK DESTR) dropped
    piv = s.pillars(f.asof)
    assert len(piv) == 39 and piv[7] == "1W" and piv[21] == "3W" and piv[31] == "1M" and piv[(date(2029, 10, 8) - f.asof).days] == "36M"
    for cid, rows in f.curves.items():
        assert sorted(rows) == sorted(piv), cid                                          # off-pillar offsets 1, 2, 8, 15, 400 and the Z rows dropped
        assert all(mat == f.asof + timedelta(days=off) for off, (mat, _) in rows.items()), cid
    assert f.curves["USD.FEDFUNDS.1D"][7][1] == 0.99924600368928                          # exactly as published (S = discount factor, not the Z row)
    assert piv[(date(2027, 3, 8) - f.asof).days] == "5M"                                  # add_months_date, calendar days, no adjustment


def test_eurex_quotes_rows_per_curve():
    s = eurex("eurex_ois_gbp")
    with open(EUREX_CSV, newline="") as fh:
        qs = s.quotes(s.parse(fh, SIX))
    assert len(qs) == 39 and {q.instrument for q in qs} == {"gbp_sonia_ois_df"} and {q.currency for q in qs} == {"GBP"}
    assert {(q.field, q.unit, q.asof, q.ref_start, q.source) for q in qs} == {("df", "discount_factor", date(2026, 10, 8), date(2026, 10, 8), "eurex_ois_gbp")}
    by = {q.contract: q for q in qs}
    assert by["1W"].ref_end == date(2026, 10, 15) and by["1W"].tenor_months == pytest.approx(7 / 30.4375)
    assert by["12M"].ref_end == date(2027, 10, 8) and by["12M"].tenor_months == 12.0 and by["36M"].ref_end == date(2029, 10, 8)
    assert all(0.8 < q.value < 1.0 for q in qs)


def test_eurex_one_download_serves_every_entry(monkeypatch, eurex_cache):
    calls = serve(monkeypatch, {"eurex-otc-clear/settlement-prices": Resp(200, EUREX_PAGE.read_bytes()),
                                "settlement-prices_20261008.csv": StreamResp(200, EUREX_CSV.read_bytes())})
    got = {}
    for sid in ("eurex_ois_usd", "eurex_ois_usd_sofr", "eurex_ois_eur", "eurex_ois_gbp", "eurex_ois_jpy", "eurex_ois_chf"):
        res = eurex(sid).fetch()
        got[sid] = (res.status, len(res.quotes), {q.instrument for q in res.quotes}, res.state)
    assert sum(".csv" in u for u, _ in calls) == 1                                        # the 65 MB file once per process
    assert got["eurex_ois_usd"] == ("ok", 39, {"usd_fedfunds_ois_df"}, {"asof": "2026-10-08"})
    assert got["eurex_ois_chf"][2] == {"chf_saron_ois_df"} and got["eurex_ois_jpy"][2] == {"jpy_tonar_ois_df"}
    assert all(v[0] == "ok" and v[1] == 39 for v in got.values())


def test_eurex_not_modified_when_the_page_file_is_not_newer(monkeypatch, eurex_cache):
    calls = serve(monkeypatch, {"eurex-otc-clear/settlement-prices": Resp(200, EUREX_PAGE.read_bytes())})   # a csv request would be unrouted
    s = eurex()
    s.stored_asof = date(2026, 10, 8)
    res = s.fetch()
    assert res.status == "not_modified" and not res.quotes and "2026-10-08 <= stored as-of 2026-10-08" in res.note
    res = eurex().fetch(state={"asof": "2026-10-09"})                                     # the validator alone works too
    assert res.status == "not_modified"
    assert not any(".csv" in u for u, _ in calls)


def test_eurex_http_failure_is_none_and_cached_for_the_other_entries(monkeypatch, eurex_cache):
    calls = serve(monkeypatch, {"eurex-otc-clear/settlement-prices": Resp(200, EUREX_PAGE.read_bytes()),
                                "settlement-prices_20261008.csv": StreamResp(503)})
    a = eurex()
    assert a.fetch() is None and (a.last_status, a.last_note) == ("UNREACHABLE", "HTTP 503")
    b = eurex("eurex_ois_eur")
    assert b.fetch() is None and b.last_status == "UNREACHABLE"
    assert sum(".csv" in u for u, _ in calls) == 3                                       # 1 + 2 retries for the first entry, none for the second
    serve(monkeypatch, {"eurex-otc-clear/settlement-prices": Resp(500)})
    c = eurex("eurex_ois_gbp")
    assert c.fetch() is None and c.last_status == "UNREACHABLE"


def test_eurex_page_without_link_or_a_changed_header_is_a_parse_failure(monkeypatch, eurex_cache):
    serve(monkeypatch, {"eurex-otc-clear/settlement-prices": Resp(200, "<html>redesigned</html>")})
    s = eurex()
    assert s.fetch() is None and s.last_status == "PARSE-FAIL" and "link" in s.last_note
    serve(monkeypatch, {"eurex-otc-clear/settlement-prices": Resp(200, EUREX_PAGE.read_bytes()),
                        "settlement-prices_20261008.csv": StreamResp(200, b"Date,Curve,Value\n2026-10-08,USD.FEDFUNDS.1D,1\n")})
    s = eurex()
    assert s.fetch() is None and s.last_status == "PARSE-FAIL" and "header" in s.last_note


def test_eurex_curve_missing_from_the_file_is_a_parse_failure(monkeypatch, eurex_cache):
    body = "\n".join(ln for ln in EUREX_CSV.read_text().splitlines() if "CHF.SARON" not in ln).encode()
    serve(monkeypatch, {"eurex-otc-clear/settlement-prices": Resp(200, EUREX_PAGE.read_bytes()),
                        "settlement-prices_20261008.csv": StreamResp(200, body)})
    assert eurex("eurex_ois_usd").fetch().status == "ok"
    s = eurex("eurex_ois_chf")
    assert s.fetch() is None and s.last_status == "PARSE-FAIL" and "CHF.SARON.1D" in s.last_note


def test_mpt_html_instead_of_the_workbook_is_a_parse_failure(monkeypatch):
    serve(monkeypatch, {"mpt_histdata.xlsx": Resp(200, "<!DOCTYPE html><html><body>Page not found</body></html>")})
    s = mk(AtlantaMpt)
    assert s.fetch() is None and s.last_status == "PARSE-FAIL" and "not an xlsx" in s.last_note
    assert "/research-and-data/data/market-probability-tracker/" in s.cfg["url"]            # the location since the move (2026-10)
