"""Phase 2a: polite HTTP, the documents collector end to end on the frozen real documents (no network), idempotence, the statement rate in
the decisions (precedence official > statement > BIS > FF > manual, conflicts, SNB on decision day, JPY out of ff_pending)."""
from __future__ import annotations

import hashlib
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from src import cb_collect as cc
from src import cb_datasets as ds
from src.cb_calendar import load_calendars
from src.cb_compute import decisions as dd
from src.cb_docs import collect as C
from src.cb_docs import parse as P
from src.cb_docs import sources as S
from src.cb_docs import store as ST
from src.cb_docs.http import Fetcher

from .cb_docs_helpers import D, FakeSession, JitterSession, Resp, statement_text
from .test_cb_docs_parse import ALL, DAYS, RATES

ROOT = Path(__file__).resolve().parents[1]
ENGINE_FIX = Path(__file__).parent / "fixtures" / "cb_engine"
TODAY = D(2026, 9, 20)
BANKS = ds.load_banks()
CALS = load_calendars()
MEETINGS = ds.load_meetings(ENGINE_FIX / "meetings.yaml")


def fetcher(session=None, **kw) -> Fetcher:
    return Fetcher(session=session or FakeSession(), sleep=lambda s: None, min_interval=0, **kw)


# --- polite HTTP ------------------------------------------------------------------------------------------------------------------

def test_robots_txt_is_respected_and_the_page_is_never_requested():
    s = FakeSession()
    f = fetcher(s)
    r = f.get("https://www.youtube.com/feeds/videos.xml?channel_id=UCAzhpt9DmG6PnHXjmJTvRGQ")
    assert r.error == "robots" and not r.ok
    assert s.urls() == []                                                                          # only robots.txt was fetched
    assert f.get("https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm").ok


def test_a_block_is_reported_never_worked_around():
    ua_seen = []

    class Blocked(FakeSession):
        def get(self, url, headers=None, **kw):
            ua_seen.append((headers or {}).get("User-Agent"))
            return Resp(403, b"cf challenge") if not url.endswith("/robots.txt") else Resp(404)
    f = fetcher(Blocked())
    r = f.get("https://www.rbnz.govt.nz/monetary-policy/monetary-policy-decisions")
    assert r.error == "blocked" and not r.ok and len(set(ua_seen)) == 1 and "CbDocsBot" in ua_seen[0]     # one honest identity, no retries
    assert f.requests_made == 2                                                                        # robots.txt + one GET


def test_robots_unreadable_skips_the_host():
    class Down(FakeSession):
        def get(self, url, headers=None, **kw):
            return Resp(503) if url.endswith("/robots.txt") else Resp(200, b"x")
    r = fetcher(Down()).get("https://example.org/a")
    assert r.error == "robots"


def test_conditional_get_and_validators_advance_only_when_remembered():
    s = FakeSession()
    f = fetcher(s)
    url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"
    r1 = f.get(url)
    assert r1.ok and not r1.not_modified and r1.validators["etag"] and url not in f.validators
    f.remember(r1)                                                                                 # only after the data was stored
    r2 = f.get(url)
    assert r2.not_modified and r2.ok and s.calls[-1][2]["If-None-Match"] == r1.validators["etag"]


def test_rate_limit_waits_per_host_and_the_request_budget_is_capped():
    naps, t = [], [0.0]
    f = Fetcher(session=FakeSession(), sleep=lambda s: (naps.append(s), t.__setitem__(0, t[0] + s)), clock=lambda: t[0], min_interval=2.0, max_requests=6)
    for _ in range(3):
        f.get("https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm")
    assert len(naps) >= 2 and all(n == pytest.approx(2.0) for n in naps[:2])
    r = None
    for _ in range(10):
        r = f.get("https://www.federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm")
    assert f.requests_made == 6 and r.error.startswith("network: per-run request budget")


def test_head_is_robots_aware_and_has_no_body():
    s = FakeSession()
    f = fetcher(s)
    assert f.head("https://www.federalreserve.gov/mediacenter/files/FOMCpresconf20260916.pdf").ok
    assert not f.head("https://www.federalreserve.gov/mediacenter/files/nope.pdf").ok
    assert f.head("https://www.youtube.com/feeds/videos.xml?channel_id=x").error == "robots"


# --- collector end to end ---------------------------------------------------------------------------------------------------------

def data_dir(tmp_path) -> Path:
    d = tmp_path / "cb"
    (d / "manual").mkdir(parents=True)
    for f in ("meetings.yaml", "decisions.parquet"):
        shutil.copy(ENGINE_FIX / f, d / f)
    shutil.copy(ENGINE_FIX / "manual" / "rbnz.yaml", d / "manual" / "rbnz.yaml")
    return d


def snapshot(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def run1(tmp_path_factory):
    d = data_dir(tmp_path_factory.mktemp("docs"))
    paths = cc.Paths(d)
    s = FakeSession()
    rep = C.run_documents(paths, TODAY, fetcher=fetcher(s), roster=C.load_roster(), now=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
    return paths, rep, s


def test_all_28_statements_are_extracted_stored_and_carry_their_rate(run1):
    paths, rep, _ = run1
    docs = ST.load_documents(paths)
    stm = sorted((d for d in docs.values() if d["type"] == "statement"), key=lambda d: (d["currency"], d["published_date"]))
    assert len(stm) == 28 and rep.statement_rates == 28
    for d in stm:
        assert d["rate_after"] == pytest.approx(RATES[d["currency"]][DAYS[d["currency"]].index(d["published_date"].isoformat())])
        assert d["text_sha256"] and d["text"] and d["extraction_method"] in ("html-selector", "pypdf") and d["license_note"] and d["url"].startswith("https://")
        assert d["meeting_date"] == d["published_date"] and d["first_seen_at"] == datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    assert {d["currency"] for d in stm} == {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"}                     # RBNZ: BOTWALL, nothing fetched
    assert not any(d["currency"] == "NZD" for d in stm)


def test_only_statements_and_opening_statements_keep_their_text(run1):
    paths, _, _ = run1
    docs = list(ST.load_documents(paths).values())
    assert ST.FULL_TEXT_TYPES == ("statement", "opening_statement")
    with_text = {d["type"] for d in docs if d["text"] is not None}
    assert with_text == {"statement", "opening_statement"}
    assert all(d["text"] for d in docs if d["type"] in ST.FULL_TEXT_TYPES)                                          # never a hash without its text
    assert {d["currency"] for d in docs if d["type"] == "opening_statement"} == {"CAD", "CHF"}                     # the short introductory statements are committed
    long = [d for d in docs if d["type"] not in ST.FULL_TEXT_TYPES]
    assert {"minutes", "deliberations", "presser_transcript", "speech"} <= {d["type"] for d in long}
    assert all(d["text"] is None for d in long)
    assert any(d["type"] == "minutes" and d["text_sha256"] for d in long)                                          # hashed, not committed


def test_doc_row_drops_the_text_of_long_documents_whatever_the_caller_passes():
    kw = dict(doc_id="x", currency="USD", bank="Federal Reserve", title="t", url="https://x", published_date=D(2026, 9, 1), first_seen=datetime(2026, 9, 2, tzinfo=timezone.utc),
              text="long text", text_sha256="ab")
    assert ST.doc_row(type="minutes", **kw)["text"] is None and ST.doc_row(type="speech", **kw)["text"] is None
    assert ST.doc_row(type="statement", **kw)["text"] == "long text" and ST.doc_row(type="opening_statement", **kw)["text"] == "long text"
    assert ST.doc_row(type="minutes", **kw)["text_sha256"] == "ab"                                                  # the hash stays
    with pytest.raises(ValueError):
        ST.doc_row(type="tweet", **kw)


def test_merge_table_is_order_independent_and_a_new_row_replaces_the_old_one():
    a = [{"k": "b", "v": 1}, {"k": None, "v": 2}, {"k": "a", "v": 3}]
    merged = ST.merge_table(a, [{"k": "b", "v": 9}], ("k",))
    assert [r["k"] for r in merged] == [None, "a", "b"] and merged[2]["v"] == 9                                     # None first, then sorted: same rows -> same bytes
    assert ST.merge_table(list(reversed(a)), [{"k": "b", "v": 9}], ("k",)) == merged


def test_votes_rows_for_every_bank_including_not_published_and_consensus(run1):
    paths, rep, _ = run1
    v = {(r["currency"], r["meeting_date"].isoformat()): r for r in ST.load_votes(paths)}
    assert len(v) == 32
    assert [(v[("USD", d)]["n_for"], v[("USD", d)]["n_against"]) for d in DAYS["USD"]] == [(8, 4), (12, 0), (9, 3), (12, 0)]
    assert [(v[("GBP", d)]["n_for"], v[("GBP", d)]["n_against"]) for d in DAYS["GBP"]] == [(8, 1), (7, 2), (6, 3), (6, 3)]
    assert [(v[("JPY", d)]["n_for"], v[("JPY", d)]["n_against"]) for d in DAYS["JPY"]] == [(6, 3), (7, 1), (8, 1), (7, 2)]
    assert [(v[("AUD", d)]["kind"], v[("AUD", d)]["n_for"], v[("AUD", d)]["n_against"]) for d in DAYS["AUD"]] == [("counted", 5, 4), ("counted", 8, 1), ("unanimous", None, 0), ("unanimous", None, 0)]
    assert {v[(c, d)]["kind"] for c in ("EUR", "CAD", "CHF") for d in DAYS[c]} == {"not_published"}
    assert {r["kind"] for r in ST.load_votes(paths) if r["currency"] == "NZD"} == {"consensus"}
    assert v[("GBP", "2026-09-17")]["source"] == "summary+xlsx" and v[("AUD", "2026-06-16")]["source"] == "minutes"


def test_redlines_between_consecutive_statements_of_the_same_bank(run1):
    paths, rep, _ = run1
    rl = {(r["currency"], r["meeting_date"].isoformat()): r for r in ST.load_redlines(paths)}
    assert len(rl) == 21 == rep.redlines                                                                            # 7 banks x 3 (the oldest has no predecessor)
    r = rl[("USD", "2026-09-16")]
    assert r["prev_meeting_date"] == D(2026, 7, 29) and r["prev_doc_id"] == "USD:statement:2026-07-29" and r["added_words"] > 0
    import json
    prev = statement_text("USD", "2026-07-29").split("\n")
    assert P.apply_redline(prev, {"paras": json.loads(r["ops_json"])}) == statement_text("USD", "2026-09-16").split("\n")


def test_minutes_deliberations_opening_statements_and_transcript_links(run1):
    paths, _, s = run1
    docs = ST.load_documents(paths)
    ty = lambda ccy, t: sorted(d["meeting_date"].isoformat() for d in docs.values() if d["currency"] == ccy and d["type"] == t)      # noqa: E731
    assert ty("AUD", "minutes") == DAYS["AUD"] and ty("USD", "minutes") == DAYS["USD"][:2]                         # fixtures hold two of them; the 16 Sep minutes are not due until 7 Oct
    assert ty("CAD", "opening_statement") == DAYS["CAD"] and ty("CHF", "opening_statement") == DAYS["CHF"]
    assert ty("USD", "presser_transcript") == DAYS["USD"] and ty("AUD", "presser_transcript") == DAYS["AUD"]
    assert ty("EUR", "presser_transcript") == ["2026-06-11", "2026-09-10"]                                          # the hash URLs the bank published
    assert ty("JPY", "summary_of_opinions") == DAYS["JPY"][:3] and ty("JPY", "minutes") == DAYS["JPY"][:2]         # PDFs named after the decision date; July's minutes are not due yet
    assert all(docs[(f"JPY:minutes:{d}",)]["extraction_method"] == "pypdf" and docs[(f"JPY:minutes:{d}",)]["text"] is None for d in DAYS["JPY"][:2])
    assert ty("GBP", "minutes") == DAYS["GBP"]                                                                      # same page as the summary, published together
    for day in DAYS["GBP"]:
        m, st = docs[(f"GBP:minutes:{day}",)], docs[(f"GBP:statement:{day}",)]
        assert m["url"] == st["url"] and m["text"] is None and m["text_sha256"] and m["text_sha256"] != st["text_sha256"]   # the part from "Minutes of the ..." on, hashed
        assert "Minutes of the Monetary Policy Committee" not in st["text"]                                          # ... and cut out of the statement
    assert not any("youtube.com/feeds" in u for u in s.urls())


def test_failures_are_reported_not_invented(run1):
    _, rep, _ = run1
    failed = dict((w, why) for w, why in rep.failed)
    assert not any("YouTube" in w for w, _ in rep.failed)                                                          # a known limit is a note, not a failure repeated every run
    assert any("YouTube feeds: disallowed" in n for n in rep.notes) and any("RBNZ" in n for n in rep.notes)


def test_speeches_are_typed_weighted_and_filtered_but_kept(run1):
    paths, _, _ = run1
    sp = [d for d in ST.load_documents(paths).values() if d["type"] in ("speech", "testimony")]
    assert len(sp) > 20 and {d["relevance"] for d in sp} == {"monetary", "other", "non_document"}
    non = [d for d in sp if d["relevance"] == "non_document"]
    assert non and all(d["currency"] == "CAD" and any(k in d["url"] for k in ("/media-availability", "press-conference", "webcast")) for d in non)   # BoC announcements + recordings
    assert not any("/media-availability" in u or "webcast" in u for u in run1[2].urls())                            # ... whose pages are never even requested
    assert not [d for d in sp if "/media-availability" in d["url"] and d["relevance"] != "non_document"]
    assert any(d["type"] == "testimony" for d in sp)
    import json
    fed = [d for d in sp if d["currency"] == "USD" and d["speaker"]]
    assert fed and all(json.loads(d["meta_json"])["weight"] in (1, 2, 3) for d in fed)
    warsh = next((d for d in fed if d["speaker"].endswith("Warsh")), None)
    assert warsh is None or json.loads(warsh["meta_json"])["weight"] == 1                                            # the Chair carries the most weight
    boj = {d["published_date"].isoformat(): d for d in sp if d["currency"] == "JPY"}
    assert set(boj) == {"2026-08-27", "2026-09-02", "2026-09-10"}
    assert [boj[k]["speaker"] for k in sorted(boj)] == ["Ryozo Himino", "Hajime Takata", "Kazuyuki Masu"]              # the BoJ list has none: taken from the page title
    assert boj["2026-08-27"]["role"] == "Deputy Governor" and all("&nbsp;" not in d["title"] and "\xa0" not in d["title"] for d in boj.values())


def test_a_second_run_is_idempotent_and_conditional(run1, tmp_path):
    paths, rep1, _ = run1
    before = snapshot(paths.dir)
    s2 = FakeSession()
    rep2 = C.run_documents(paths, TODAY, fetcher=fetcher(s2), roster=C.load_roster(), now=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    assert rep2.new == 0 and rep2.updated == 0 and rep2.statement_rates <= 28
    assert snapshot(paths.dir) == {k: v for k, v in snapshot(paths.dir).items()}
    changed = {k for k in before if before[k] != snapshot(paths.dir).get(k)}
    assert changed <= {"state.json"}                                                                               # no document, vote or redline was rewritten
    assert not any("federalreserve.gov/newsevents/pressreleases/monetary20260729a.htm" in u for u in s2.urls())     # an old statement is never re-requested


def test_a_changed_page_layout_is_a_failure_not_an_empty_document(tmp_path):
    d = data_dir(tmp_path)
    s = FakeSession(extra={"https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm": Resp(200, b"<html><body><p>new layout</p></body></html>", {"Content-Type": "text/html"})})
    rep = C.run_documents(cc.Paths(d), TODAY, fetcher=fetcher(s), roster={}, banks=("USD",))
    assert any("USD statement 2026-09-16" in w and "no text extracted" in why for w, why in rep.failed)
    assert ST.load_documents(cc.Paths(d)).get(("USD:statement:2026-09-16",)) is None


def test_a_rate_that_cannot_be_parsed_or_is_implausible_is_not_taken(tmp_path):
    d = data_dir(tmp_path)
    bad = statement_text("EUR", "2026-09-10").replace("2.50%", "9.50%")                                             # a raise of 700 bp
    page = ("<html><body><div class='section'>" + "".join(f"<p>{p}</p>" for p in bad.split("\n")) + "</div></body></html>").encode()
    from src.cb_probe import ECB_SEEDS
    s = FakeSession(extra={ECB_SEEDS[D(2026, 9, 10)]: Resp(200, page, {"Content-Type": "text/html"})})
    rep = C.run_documents(cc.Paths(d), TODAY, fetcher=fetcher(s), roster={}, banks=("EUR",))
    doc = ST.load_documents(cc.Paths(d))[("EUR:statement:2026-09-10",)]
    assert doc["rate_after"] is None and doc["text"]                                                                # the text is kept, the rate is not
    assert any("EUR statement 2026-09-10 rate" in w and "rejected" in why for w, why in rep.failed)


def test_the_manual_file_adds_documents_the_collector_cannot_fetch(tmp_path):
    d = data_dir(tmp_path)
    (d / "manual" / "documents.yaml").write_text(
        "documents:\n  - {currency: NZD, type: statement, title: RBNZ statement, url: 'https://www.rbnz.govt.nz/x', published: 2026-09-02, meeting: 2026-09-02, text: 'The Committee agreed the OCR', note: typed}\n"
        "  - {currency: USD, type: presser_video, title: FOMC press conference, url: 'https://www.youtube.com/watch?v=abc', published: 2026-09-16, meeting: 2026-09-16}\n"
        "  - {currency: USD, type: bogus, title: t, url: u, published: 2026-09-16}\n")
    rep = C.run_documents(cc.Paths(d), TODAY, fetcher=fetcher(), roster={}, banks=())
    docs = ST.load_documents(cc.Paths(d)).values()
    nz = next(x for x in docs if x["currency"] == "NZD")
    assert nz["format"] == "manual" and nz["text"] == "The Committee agreed the OCR" and nz["text_sha256"] and nz["extraction_method"] == "manual"
    assert any(x["type"] == "presser_video" and x["currency"] == "USD" for x in docs)
    assert any("manual documents" in w for w, _ in rep.failed)                                                       # the unknown type is skipped with a reason


def test_publication_expectations_warn_only_past_the_usual_lag(run1):
    paths, _, _ = run1
    w = C.expectations(paths, TODAY)
    assert not any(x.startswith("USD minutes") and "2026-09-16" in x for x in w)                                    # due 7 Oct
    assert any(x.startswith("EUR account") for x in w)                                                               # the ECB accounts are not collected: reported
    assert not w or all("overdue" in x for x in w)
    late = C.expectations(paths, D(2026, 10, 20))
    assert any(x.startswith("USD minutes of the 2026-09-16") for x in late)                                          # 7 Oct + 2 days grace has passed
    stored = {(d["currency"], d["type"], d["meeting_date"]) for d in ST.load_documents(paths).values()
              if d["meeting_date"] and (d["currency"], d["type"]) in S.EXPECTED_LAG}
    assert stored                                                                                                    # e.g. the USD / AUD minutes of earlier meetings
    due_by_now = {(c, t, m) for (c, t, m) in stored if m + timedelta(days=S.EXPECTED_LAG[(c, t)] + S.GRACE_DAYS) < D(2026, 10, 20)}
    assert due_by_now                                                                                                # documents that were due and are stored
    assert not any(f"{c} {t.replace('_', ' ')} of the {m} meeting" in x for (c, t, m) in due_by_now for x in late)   # a stored document is never reported as overdue


# --- the statement rate in the decisions -------------------------------------------------------------------------------------------

STATEMENTS = {(c, D.fromisoformat(d)): {"after": RATES[c][i], "lower": None, "upper": None, "doc_id": f"{c}:statement:{d}"}
              for c, days in DAYS.items() for i, d in enumerate(days)}
STATEMENTS[("USD", D(2026, 9, 16))].update(lower=3.75, upper=4.0)
for i, d in enumerate(DAYS["USD"][:3]):
    STATEMENTS[("USD", D.fromisoformat(d))].update(lower=3.5, upper=3.75)


def real_inputs():
    view = dd.SeriesView(pq.read_table(Path(__file__).parent / "fixtures" / "cb" / "decisions_official_cut.parquet").to_pylist())
    ff = ds.ff_by_bank(BANKS, pd.read_parquet(Path(__file__).parent / "fixtures" / "cb" / "decisions_ff_cut.parquet"))
    return view, ff


def test_real_statements_agree_with_the_official_series_everywhere_and_free_jpy_from_ff_pending():
    view, ff = real_inputs()
    base_rows, _ = dd.compute_all(BANKS, MEETINGS, CALS, view, ff, TODAY)
    rows, unresolved = dd.compute_all(BANKS, MEETINGS, CALS, view, ff, TODAY, statements=STATEMENTS)
    base = {(r["currency"], r["meeting_date"]): r for r in base_rows}
    new = {(r["currency"], r["meeting_date"]): r for r in rows}
    assert not unresolved and not [k for k, r in new.items() if r["status"] == "conflict"]                          # the seven banks' own texts agree with the series
    for k, r in new.items():
        assert r["rate_after"] == pytest.approx(base[k]["rate_after"]), k
        if k[0] in ("USD", "EUR", "GBP", "CAD", "AUD", "CHF", "NZD"):
            assert r["status"] == base[k]["status"], k                                                              # an official series still outranks the statement
    jpy = [new[("JPY", D.fromisoformat(d))] for d in DAYS["JPY"]]
    assert [r["status"] for r in jpy] == ["statement"] * 4 and jpy[-1]["rate_source"] == "statement:JPY:statement:2026-09-18"
    assert base[("JPY", D(2026, 9, 18))]["status"] == "ff_pending" and jpy[-1]["rate_after"] == 1.25


def cfg_view(rate_series: dict | None = None):
    return dd.SeriesView([{"series_id": k, "date": d, "value": v} for k, pts in (rate_series or {}).items() for d, v in pts])


def test_snb_gets_its_rate_on_the_decision_day_from_the_statement():
    """No official series has reached the meeting yet (SNB publishes with a lag): the row exists on the day, from the statement."""
    meeting = {"date": D(2026, 6, 18), "first_day": None}
    empty = dd.SeriesView([])
    assert dd.compute_decision("CHF", BANKS["CHF"], meeting, CALS, empty, []) is None
    row = dd.compute_decision("CHF", BANKS["CHF"], meeting, CALS, empty, [], statement={"after": 0.0, "lower": None, "upper": None, "doc_id": "CHF:statement:2026-06-18"})
    assert row["status"] == "statement" and row["rate_after"] == 0.0 and row["effective_date"] == D(2026, 6, 19) and row["rate_source"] == "statement:CHF:statement:2026-06-18"


def test_precedence_official_then_statement_then_bis_then_ff_then_manual():
    gbp, meeting = BANKS["GBP"], {"date": D(2026, 9, 17), "first_day": None}
    sid = gbp["policy_rate"]["official"]["level"]
    official = cfg_view({sid: [(D(2026, 9, 1), 3.75), (D(2026, 9, 17), 3.75)]})
    stmt = {"after": 3.75, "lower": None, "upper": None, "doc_id": "GBP:statement:2026-09-17"}
    assert dd.compute_decision("GBP", gbp, meeting, CALS, official, [], statement=stmt)["status"] == "official"      # the series wins when it has the level
    none = dd.SeriesView([])
    manual = {"rate_after": 3.5, "note": "typed"}
    r = dd.compute_decision("GBP", gbp, meeting, CALS, none, [], manual=manual, statement=stmt)
    assert (r["status"], r["rate_after"]) == ("statement", 3.75)                                                     # statement beats manual
    r = dd.compute_decision("GBP", gbp, meeting, CALS, none, [], manual=manual)
    assert (r["status"], r["rate_after"]) == ("manual", 3.5)


def test_conflicts_keep_the_higher_precedence_value_and_say_who_disagreed():
    gbp, meeting = BANKS["GBP"], {"date": D(2026, 9, 17), "first_day": None}
    sid = gbp["policy_rate"]["official"]["level"]
    official = cfg_view({sid: [(D(2026, 9, 1), 4.0), (D(2026, 9, 17), 4.0)]})
    stmt = {"after": 3.75, "lower": None, "upper": None, "doc_id": "GBP:statement:2026-09-17"}
    r = dd.compute_decision("GBP", gbp, meeting, CALS, official, [], statement=stmt)
    assert r["status"] == "conflict" and r["rate_after"] == 4.0 and "statement:GBP:statement:2026-09-17 says 3.75" in r["notes"] and "wins" in r["notes"]  # the official value stays
    jpy = BANKS["JPY"]
    bis = cfg_view({jpy["policy_rate"]["official"]["bis"]: [(D(2026, 9, 1), 1.0), (D(2026, 9, 24), 1.0)]})
    r = dd.compute_decision("JPY", jpy, {"date": D(2026, 9, 18), "first_day": None}, CALS, bis, [], statement={"after": 1.25, "lower": None, "upper": None, "doc_id": "JPY:statement:2026-09-18"})
    assert r["status"] == "conflict" and r["rate_after"] == 1.25 and "bis:JP says 1" in r["notes"]                      # statement outranks BIS


def test_documents_stage_hands_the_statement_rates_to_the_decisions(run1, tmp_path):
    paths, _, _ = run1
    rates = ds.statement_rates(paths)
    assert len(rates) == 28 and rates[("USD", D(2026, 9, 16))] == {"after": 3.875, "lower": 3.75, "upper": 4.0, "doc_id": "USD:statement:2026-09-16"}
    assert rates[("JPY", D(2026, 9, 18))]["after"] == 1.25


def test_a_surname_is_looked_up_only_among_the_members_of_the_speakers_own_bank():
    run = C._Run.__new__(C._Run)
    run.today = D(2026, 9, 20)
    run.roster = {"people": [{"currency": "CHF", "name": "Antoine Martin", "role": "Vice Chairman", "chair": False, "voter": {"2026": True}},
                             {"currency": "USD", "name": "Jane Martin", "role": "Governor", "chair": False, "voter": {"2026": False}}]}
    assert run.role_of("Martin", "CHF") == ("Antoine Martin", "Vice Chairman", True, False)
    assert run.role_of("Martin", "USD") == ("Jane Martin", "Governor", False, False)
    assert run.role_of("Martin", "GBP") == ("Martin", "", False, False)                                             # unknown at this bank: not borrowed from another


# --- press-conference video from the banks' own pages ------------------------------------------------------------------------------

def videos(paths) -> dict:
    return {(d["currency"], d["meeting_date"].isoformat()): d for d in ST.load_documents(paths).values() if d["type"] == "presser_video"}


def test_press_conference_videos_come_from_the_banks_own_pages_never_the_youtube_feed(run1):
    import json
    paths, _, s = run1
    v = videos(paths)
    assert {c: sorted(d for (cc, d) in v if cc == c) for c in ("USD", "CAD", "AUD", "GBP", "EUR", "JPY", "CHF", "NZD")} == {
        "USD": DAYS["USD"], "CAD": DAYS["CAD"], "AUD": DAYS["AUD"], "GBP": ["2026-04-30", "2026-07-30"],                # the MPR meetings only
        "EUR": ["2026-09-10"], "JPY": [], "CHF": [], "NZD": []}                                                       # the ECB page shows the last conference only
    fed = v[("USD", "2026-09-16")]
    assert fed["url"] == "https://www.federalreserve.gov/monetarypolicy/fomcpresconf20260916.htm" and json.loads(fed["meta_json"])["player"] == "Brightcove"
    assert v[("CAD", "2026-09-02")]["url"] == "https://www.bankofcanada.ca/multimedia/press-conference-policy-rate-announcement-september-2026/"
    assert v[("AUD", "2026-08-11")]["url"] == "https://youtu.be/-VdeRdWUgDc" and json.loads(v[("AUD", "2026-08-11")]["meta_json"])["duration"] == "47:30"
    assert v[("AUD", "2026-03-17")]["url"].startswith("https://www.youtube.com/watch?v=")                                # the RBA uses both forms
    assert v[("GBP", "2026-07-30")]["url"] == "https://www.youtube.com/watch?v=G5m9FOeBD1Q"
    assert v[("EUR", "2026-09-10")]["url"] == "https://www.youtube.com/watch?v=rCBHa4xjqvI"
    assert all(d["format"] == "video" and d["text"] is None for d in v.values())
    assert not any("youtube.com" in u for u in s.urls())                                                            # the video pages are the banks': YouTube itself is never requested


def test_a_meeting_without_a_video_is_remembered_and_not_asked_again(run1, tmp_path):
    from src import cb_collect as cc2
    paths, _, _ = run1
    st = cc2.load_state(paths)
    assert st[C.VIDEO_KEY]["GBP:2026-06-18"] == "none" and st[C.VIDEO_KEY]["GBP:2026-09-17"] == "none" and st[C.VIDEO_KEY]["USD:2026-09-16"] == "video"
    s2 = FakeSession()
    C.run_documents(paths, TODAY, fetcher=fetcher(s2), roster=C.load_roster(), now=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
    asked = " ".join(s2.urls())
    assert "monetary-policy-report/2026/june-2026" not in asked                                                     # decided 18 Jun: past the retry window
    assert "monetary-policy-report/2026/september-2026" in asked                                                    # decided 17 Sep: still inside it (the video may come later)
    assert not any("fomcpresconf" in u for u in s2.urls())                                                          # a stored video is never asked for again


def test_a_video_is_taken_on_a_later_run_when_the_page_gets_it_inside_the_retry_window(tmp_path):
    d = data_dir(tmp_path)
    paths = cc.Paths(d)
    bare = Resp(200, b"<html><body><p>no player yet</p></body></html>", {"Content-Type": "text/html"})
    page = "https://www.federalreserve.gov/monetarypolicy/fomcpresconf20260916.htm"
    two_days_after = D(2026, 9, 18)                                                                                 # the 16 Sep decision is inside the 3-day window
    C.run_documents(paths, two_days_after, fetcher=fetcher(FakeSession(extra={page: bare})), roster=C.load_roster(), now=datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc))
    assert ("USD", "2026-09-16") not in videos(paths)
    C.run_documents(paths, two_days_after, fetcher=fetcher(FakeSession()), roster=C.load_roster(), now=datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc))
    assert ("USD", "2026-09-16") in videos(paths)                                                                   # the player appeared: taken
    C.run_documents(paths, D(2026, 9, 25), fetcher=fetcher(s3 := FakeSession(extra={page: bare})), roster=C.load_roster(), now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc))
    assert ("USD", "2026-09-16") in videos(paths) and not any("fomcpresconf" in u for u in s3.urls())              # once stored, never asked again


def test_the_ecb_landing_video_is_kept_only_for_a_meeting_we_track(tmp_path):
    d = data_dir(tmp_path)
    paths = cc.Paths(d)
    import pathlib

    from .cb_docs_helpers import manifest
    m = manifest()
    entry = m[C.S.ECB_PRESS_LANDING]
    page = (pathlib.Path(__file__).parent / "fixtures" / "cb_docs" / entry["file"]).read_bytes().replace(b"ecb.is260910~", b"ecb.is260101~")   # an extraordinary date
    extra = {C.S.ECB_PRESS_LANDING: Resp(200, page, {"Content-Type": "text/html"})}
    C.run_documents(paths, TODAY, fetcher=fetcher(FakeSession(extra=extra)), roster=C.load_roster(), now=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
    assert not any(k[0] == "EUR" for k in videos(paths))


# --- validators: state.json changes only with the content ------------------------------------------------------------------------------------

def test_validators_advance_only_when_the_stored_content_changes():
    f = Fetcher(session=FakeSession(), validators={}, sleep=lambda s: None, min_interval=0)
    from src.cb_docs.http import Fetched
    r = lambda etag, lm="Mon": Fetched("https://x.example/p", 200, b"x", {}, validators={"etag": etag, "last_modified": lm})      # noqa: E731
    f.remember(r('"a"'), content_sha="AAA")
    assert f.validators["https://x.example/p"] == {"etag": '"a"', "last_modified": "Mon", "sha256": "AAA"}
    f.remember(r('"b"', "Tue"), content_sha="AAA")                                                                      # same content, new validators: nothing moves
    assert f.validators["https://x.example/p"] == {"etag": '"a"', "last_modified": "Mon", "sha256": "AAA"}
    f.remember(r('"c"', "Wed"), content_sha="BBB")                                                                      # the content changed: they advance
    assert f.validators["https://x.example/p"] == {"etag": '"c"', "last_modified": "Wed", "sha256": "BBB"}
    f.remember(r('"d"'))                                                                                                # no hash given: the caller did not vouch for the content
    assert f.validators["https://x.example/p"]["etag"] == '"d"'


def test_the_ecb_etag_is_ignored_it_is_neither_stored_nor_sent_but_last_modified_still_is():
    url = "https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.mp260910~314e508016.en.html"
    s = FakeSession(extra={url: Resp(200, b"<html>x</html>", {"ETag": '"myra-1"', "Last-Modified": "Thu, 10 Sep 2026 12:15:00 GMT", "Content-Type": "text/html"})})
    stored = {url: {"etag": '"myra-old"', "last_modified": "Thu, 10 Sep 2026 12:15:00 GMT", "sha256": "h"}}
    f = Fetcher(session=s, validators=stored, sleep=lambda x: None, min_interval=0)
    assert "etag" not in stored[url]                                                                                    # a legacy ECB ETag is dropped when the state is loaded
    stored[url]["etag"] = '"myra-put-back"'                                                                             # even if one gets into the dict some other way, it is not sent
    r = f.get(url)
    sent = next(h for m, u, h in s.calls if u == url)
    assert "If-None-Match" not in sent and sent["If-Modified-Since"] == "Thu, 10 Sep 2026 12:15:00 GMT"
    assert r.validators == {"last_modified": "Thu, 10 Sep 2026 12:15:00 GMT"}                                            # the response's ETag is not kept
    other = "https://www.federalreserve.gov/x.htm"
    s2 = FakeSession(extra={other: Resp(200, b"<html>y</html>", {"ETag": '"e"', "Content-Type": "text/html"})})
    assert Fetcher(session=s2, sleep=lambda x: None, min_interval=0).get(other).validators == {"etag": '"e"'}              # every other host keeps its ETag


def test_a_jittering_etag_alone_never_changes_state_json(tmp_path):
    d = data_dir(tmp_path)
    paths = cc.Paths(d)
    run = lambda session, hour: C.run_documents(paths, TODAY, fetcher=fetcher(session, validators=cc.load_state(paths).setdefault(C.STATE_KEY, {})),   # noqa: E731
                                                 roster=C.load_roster(), now=datetime(2026, 9, 20, hour, 0, tzinfo=timezone.utc))
    run(JitterSession(), 12)
    run(JitterSession(), 13)                                                                                            # settles (one-off additions of the content hash)
    settled = snapshot(d)
    rep = run(JitterSession(), 14)                                                                                      # every ETag is different again, the bytes are not
    assert rep.new == 0 and rep.updated == 0
    assert snapshot(d) == settled                                                                                       # not one file changed: nothing to commit
    v = cc.load_state(paths)[C.STATE_KEY]
    assert v and all("sha256" in x for x in v.values() if "etag" in x or "last_modified" in x)
    assert not any("etag" in x for u, x in v.items() if "ecb.europa.eu" in u)                                            # and no ECB ETag anywhere


def test_new_content_does_advance_the_validators_and_state_json(tmp_path):
    d = data_dir(tmp_path)
    paths = cc.Paths(d)
    url = "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm"
    run = lambda session: C.run_documents(paths, D(2026, 9, 18), fetcher=fetcher(session, validators=cc.load_state(paths).setdefault(C.STATE_KEY, {})),   # noqa: E731
                                          roster=C.load_roster(), now=datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc))
    run(JitterSession())
    before = cc.load_state(paths)[C.STATE_KEY][url]
    page = (Path(__file__).parent / "fixtures" / "cb_docs" / "statements" / "USD_2026-09-16.html").read_text().replace("Inflation remains elevated.", "Inflation remains elevated at this time.")
    run(JitterSession(extra={url: Resp(200, page.encode(), {"Content-Type": "text/html", "ETag": '"changed"', "Last-Modified": "Tue, 15 Sep 2026 20:00:00 GMT"})}))
    after = cc.load_state(paths)[C.STATE_KEY][url]
    assert after["sha256"] != before["sha256"] and after["etag"] == '"changed"' and after["last_modified"] == "Tue, 15 Sep 2026 20:00:00 GMT"


def test_an_announcement_without_a_description_is_still_never_requested(tmp_path):
    import re as _re
    feed_url = "https://www.bankofcanada.ca/content_type/speeches/feed/"
    xml = (Path(__file__).parent / "fixtures" / "cb_docs" / "feeds" / "boc_speeches.xml").read_text()
    bare = _re.sub(r"<description>.*?</description>|<content:encoded>.*?</content:encoded>", "", xml, flags=_re.S)          # nothing to read the relevance from in the feed
    s = FakeSession(extra={feed_url: Resp(200, bare.encode(), {"Content-Type": "application/xml"})})
    d = data_dir(tmp_path)
    C.run_documents(cc.Paths(d), D(2026, 9, 22), fetcher=fetcher(s), roster=C.load_roster(), now=datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc))   # 22 Sep: the 21 Sep announcement is inside the window
    assert any("/speech-halifax-partnership" in u for u in s.urls())                                                   # the speech of that day IS read (nothing else told its relevance)
    assert not any("/media-availability" in u for u in s.urls())                                                       # an announcement is never fetched to find out what it is
    got = [x for x in ST.load_documents(cc.Paths(d)).values() if x["currency"] == "CAD" and x["type"] == "speech"]
    assert any(x["relevance"] == "non_document" for x in got) and any("/media-availability" not in x["url"] for x in got)


# --- BoC webcast pages listed with the speeches: the meeting's video, or nothing ---------------------------------------------------------------

def boc_speech_rows(paths):
    return {d["url"]: d for d in ST.load_documents(paths).values() if d["currency"] == "CAD" and d["type"] == "speech"}


def test_a_press_conference_webcast_is_attached_to_its_meeting_and_a_speech_webcast_with_no_meeting_is_a_non_document(run1):
    import json
    paths, _, _ = run1
    rows = boc_speech_rows(paths)
    sep = rows["https://www.bankofcanada.ca/multimedia/press-conference-policy-rate-announcement-september-2026/"]
    jul = rows["https://www.bankofcanada.ca/multimedia/press-conference-monetary-policy-report-july-2026/"]
    for row, day in ((sep, "2026-09-02"), (jul, "2026-07-15")):
        meta = json.loads(row["meta_json"])
        assert row["relevance"] == "non_document" and meta["attached_to"] == f"CAD:presser_video:{day}" and meta["media"] == "press_conference"
    unveil = next(d for u, d in rows.items() if "unveiling-canadas-new-20-bank-note-speech-webcasts" in u)                # 3 Sep: a day from the 2 Sep decision, but not a press conference
    assert unveil["relevance"] == "non_document" and "attached_to" not in json.loads(unveil["meta_json"]) and json.loads(unveil["meta_json"])["media"] == "webcast"
    videos_of = [d for d in ST.load_documents(paths).values() if d["currency"] == "CAD" and d["type"] == "presser_video"]
    assert sorted(d["meeting_date"].isoformat() for d in videos_of) == DAYS["CAD"] and len(videos_of) == 4              # one video per meeting: the feed did not add a second
    sep_video = next(d for d in videos_of if d["meeting_date"].isoformat() == "2026-09-02")
    assert sep_video["url"] == sep["url"] and json.loads(sep_video["meta_json"])["source"] == "official page"          # the release page's link came first; the feed did not overwrite it
    assert "Speech: Halifax Partnership" in {d["title"] for d in rows.values() if d["relevance"] != "non_document"}      # a real speech is still a speech


def test_a_press_conference_webcast_becomes_the_video_when_the_release_page_gave_none(tmp_path):
    import json
    import re as _re
    extra = {}
    for iso in DAYS["CAD"]:
        y, m, _ = iso.split("-")
        url = f"https://www.bankofcanada.ca/{y}/{m}/fad-press-release-{iso}/"
        page = (Path(__file__).parent / "fixtures" / "cb_docs" / "statements" / f"CAD_{iso}.html").read_text()
        extra[url] = Resp(200, _re.sub(r"<a [^>]*multimedia/press-conference[^>]*>.*?</a>", "", page, flags=_re.S).encode(), {"Content-Type": "text/html"})   # no video link on the page
    d = data_dir(tmp_path)
    C.run_documents(cc.Paths(d), TODAY, fetcher=fetcher(FakeSession(extra=extra)), roster=C.load_roster(), now=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
    videos_of = {x["meeting_date"].isoformat(): x for x in ST.load_documents(cc.Paths(d)).values() if x["currency"] == "CAD" and x["type"] == "presser_video"}
    assert sorted(videos_of) == ["2026-07-15", "2026-09-02"]                                                            # the two conferences the feed carries
    assert json.loads(videos_of["2026-09-02"]["meta_json"])["source"] == "bank feed" and videos_of["2026-09-02"]["format"] == "video"


def test_a_press_conference_webcast_with_no_meeting_within_a_day_is_only_a_non_document(tmp_path):
    import json
    import re as _re
    feed_url = "https://www.bankofcanada.ca/content_type/speeches/feed/"
    xml = (Path(__file__).parent / "fixtures" / "cb_docs" / "feeds" / "boc_speeches.xml").read_text()
    moved = xml.replace("2026-09-02T10:30:19+00:00", "2026-09-05T10:30:19+00:00")                                          # 3 days after the decision: no meeting matches
    assert moved != xml
    d = data_dir(tmp_path)
    C.run_documents(cc.Paths(d), TODAY, fetcher=fetcher(FakeSession(extra={feed_url: Resp(200, moved.encode(), {"Content-Type": "application/xml"})})), roster=C.load_roster(),
                    now=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
    rows = boc_speech_rows(cc.Paths(d))
    row = rows["https://www.bankofcanada.ca/multimedia/press-conference-policy-rate-announcement-september-2026/"]
    meta = json.loads(row["meta_json"])
    assert row["relevance"] == "non_document" and "attached_to" not in meta and meta["media"] == "press_conference"


def test_the_payload_and_the_summaries_never_see_the_recordings(run1):
    from src.cb_summarize import run as SR
    from src.cb_summarize.config import load as load_cfg
    from src import cb_datasets as ds
    paths, _, _ = run1
    docs = sorted(ST.load_documents(paths).values(), key=lambda d: (d["currency"], d["published_date"], d["doc_id"]))
    todo = SR.candidates(docs, ds.load_meetings(paths.meetings), TODAY, load_cfg())
    assert not any(d["currency"] == "CAD" and d["type"] == "speech" and any(k in d["url"] for k in ("/media-availability", "press-conference", "webcast")) for d in todo)
    assert not any(d["type"] == "presser_video" for d in todo)                                                          # a video is a link, never a text to summarise


def test_webcast_kind_only_knows_the_boc_multimedia_pages():
    k = P.webcast_kind
    assert k("CAD", "Monetary Policy Decision Press Conference", "https://www.bankofcanada.ca/multimedia/press-conference-policy-rate-announcement-september-2026/") == "press_conference"
    assert k("CAD", "Press Conference: Monetary Policy Report - July 2026", "https://www.bankofcanada.ca/multimedia/x/") == "press_conference"
    assert k("CAD", "Unveiling - Speech webcasts", "https://www.bankofcanada.ca/multimedia/unveiling-speech-webcasts/") == "webcast"
    assert k("CAD", "Speech: Halifax Partnership", "https://www.bankofcanada.ca/multimedia/speech-halifax-partnership-2026-09-21/") is None      # a speech page is a speech
    assert k("CAD", "Media Availability: Halifax", "https://www.bankofcanada.ca/multimedia/media-availability-halifax-partnership/") is None       # that one is is_non_document's
    assert k("CAD", "Press conference", "https://www.bankofcanada.ca/2026/09/press-conference/") is None                                        # not under /multimedia/
    assert k("USD", "FOMC press conference", "https://www.federalreserve.gov/multimedia/press-conference") is None                              # a BoC rule only
