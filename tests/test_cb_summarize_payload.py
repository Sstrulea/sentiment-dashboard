"""Phase 2b in the payloads: the summary slots are filled from the stored summaries, a summary that failed validation is never shown (its slot stays
pending, with the reason for the tooltip), quotes link to the bank's own page with a text fragment, and the page code renders it all."""
from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import pytest

from src import cb_collect as cc
from src.cb_compute import payload as P
from src.cb_loader import load_context, load_pair_defs
from src.cb_summarize import run as R
from src.cb_summarize import store as SS
from src.cb_summarize.client import RecordedClient
from src.cb_summarize.config import load as load_cfg

from .cb_docs_helpers import FakeSession
from .cb_sum_helpers import FED_KEY, FED_PARAS, GOOD_FED, GOOD_TEXTS, NOW, STMT_V, TODAY, collected_dir, dumps, fresh_copy, responder_generic
from .test_cb_docs_collect import ENGINE_FIX, fetcher

ASOF = date(2026, 9, 18)
ROOT = Path(__file__).resolve().parents[1]
CFG = load_cfg()


@pytest.fixture(scope="module")
def summarised(tmp_path_factory):
    """Fed: the recorded good output for the latest statement, a generic valid one for the FOMC minutes; BoE: two bad outputs (validation failed)."""
    base = collected_dir(tmp_path_factory)
    d = fresh_copy(base, tmp_path_factory.mktemp("sum_payload"))
    for sub in ("market_quotes", "official_series"):
        shutil.copytree(ENGINE_FIX / sub, d / sub)
    shutil.copy(ENGINE_FIX / "projections.parquet", d / "projections.parquet")
    paths = cc.Paths(d)

    def responder(system, messages):
        body = messages[0]["content"]
        if "Today's policy action will support a timelier return" in body:                                            # the 16 Sep 2026 statement only
            return dumps(GOOD_FED)
        if "Bank: Bank of England" in body:
            return "not json"
        from .cb_sum_helpers import generic_output
        return generic_output(messages)

    cfg = __import__("dataclasses").replace(CFG, max_documents=60, max_input_tokens=10**7)
    R.run_summaries(paths, TODAY, client=RecordedClient(responder=responder), fetcher=fetcher(FakeSession()), env={}, now=NOW, cfg=cfg, banks={"USD", "GBP"},
                    types={"statement", "minutes"})
    ctx = load_context(d)
    return P.build(ctx, ASOF, load_pair_defs()), paths


def docs(built, ccy):
    return built[0]["banks"][ccy]["documents"]


def test_the_latest_decision_summary_slot_is_filled_and_carries_the_content_of_the_summary(summarised):
    L = docs(summarised, "USD")["latest"]
    assert L["summary"] == {"status": "ready", "label": "summary", "doc_id": FED_KEY, "reason": None}
    s = docs(summarised, "USD")["summaries"][FED_KEY]
    assert s["points"] == GOOD_TEXTS and [q["text"] for q in s["quotes"]] == [q["text"] for q in GOOD_FED["quotes"]]
    assert (s["model"], s["prompt_version"], s["generated"], s["note"]) == ("gpt-5.6-terra", STMT_V, "2026-09-21", "factual summary, no interpretation")
    assert s["changes"]["vs_meeting"] == "2026-07-29" and {"paragraph": 2, "removed": "maintain", "added": "raise"} in s["changes"]["changes"]
    assert s["truncated"] is False and s["paragraphs_covered"] == [1, 2, 3, 4] and s["label"] == "Statement"


def test_every_point_carries_its_evidence_linked_to_the_source(summarised):
    s = docs(summarised, "USD")["summaries"][FED_KEY]
    assert len(s["evidence"]) == len(s["points"]) == 3
    for ev, want in zip(s["evidence"], GOOD_FED["summary"]):
        w = want["evidence"]
        assert ev["paragraphs"] == w["paragraphs"] and [f["text"] for f in ev["fragments"]] == w["fragments"] and ev["coverage"] == 1.0
        assert all(f["paragraph"] in ev["paragraphs"] and f["text"] in FED_PARAS[f["paragraph"] - 1] and set(f) == {"text", "paragraph"} for f in ev["fragments"])
        assert ev["href"].startswith("https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm#:~:text=")
        directives = ev["href"].split("#:~:text=", 1)[1].split("&text=")
        assert len(directives) == len(w["fragments"]) and all(unquote(d).startswith(f.split()[0]) for d, f in zip(directives, w["fragments"]))   # one directive per fragment
        assert ev["texts"] == {str(n): FED_PARAS[n - 1] for n in w["paragraphs"]}                                       # a statement's text is committed: the page can show the paragraph
    assert s["evidence"][2]["paragraphs"] == [3, 4] and set(s["evidence"][2]["texts"]) == {"3", "4"} and len(s["evidence"][2]["fragments"]) == 2


def test_the_paragraph_of_a_document_that_is_not_committed_is_not_in_the_json():
    rec = {"doc_id": "USD:speech:x", "type": "speech", "format": "html", "title": "t", "url": "https://x/y.htm", "changes_vs_previous": None, "model": "m", "prompt_version": "speech-v5",
           "generated_at": "2026-09-21T08:00:00Z", "coverage": {"truncated": False, "paragraphs": [1]}, "quotes": [],
           "summary": [{"text": "Waller expects inflation to fall.", "evidence": {"paragraphs": [2], "coverage": 1.0, "fragments": [
               {"text": "I expect inflation to fall as the labor market cools", "paragraph": 2, "start": 5, "end": 57}, {"text": "and I see the risks to employment as more important", "paragraph": 2, "start": 60, "end": 111}]}}]}
    j = P.summary_json(rec)
    ev = j["evidence"][0]
    assert j["points"] == ["Waller expects inflation to fall."] and ev["texts"] is None and [f["paragraph"] for f in ev["fragments"]] == [2, 2]
    assert ev["href"] == "https://x/y.htm#:~:text=I%20expect%20inflation%20to%20fall%20as%20the%20labor%20market%20cools&text=and%20I%20see%20the%20risks%20to%20employment%20as%20more%20important"
    assert all(set(f) == {"text", "paragraph"} for f in ev["fragments"])                                                # offsets stay in the store
    pdf = P.summary_json(dict(rec, format="pdf", url="https://x/y.pdf"))
    assert pdf["evidence"][0]["href"] == "https://x/y.pdf"                                                              # a PDF has no text fragment
    legacy = P.summary_json(dict(rec, summary=["a point of a summary made before the evidence existed"]))
    assert legacy["points"] == ["a point of a summary made before the evidence existed"] and legacy["evidence"] == [None]
    v4 = P.summary_json(dict(rec, summary=[{"text": "p", "evidence": {"paragraphs": [2], "paragraph": 2, "fragment": "I expect inflation to fall as the labor market cools", "start": 5, "end": 57, "coverage": 1.0}}]))
    assert v4["evidence"][0]["fragments"] == [{"text": "I expect inflation to fall as the labor market cools", "paragraph": 2}]      # a summary of prompt v4 still shows


def test_every_quote_links_to_the_source_with_a_text_fragment(summarised):
    s = docs(summarised, "USD")["summaries"][FED_KEY]
    q = s["quotes"][0]
    assert q["href"].startswith("https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm#:~:text=")
    frag = q["href"].split("#:~:text=", 1)[1]
    assert "-" not in frag and "%2D" in frag and "," not in frag                                                       # the directive's own characters are encoded
    assert unquote(frag) == q["text"]
    assert all(x["href"].startswith(s["url"]) for x in s["quotes"])


def test_the_text_fragment_of_a_long_quote_uses_its_first_and_last_words():
    long = "The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent, in support of the dual mandate and price stability"
    frag = P.text_fragment(long)
    start, end = frag[len("#:~:text="):].split(",")
    assert unquote(start) == "The Committee decided to raise" and unquote(end) == "dual mandate and price stability"
    assert P.text_fragment("Inflation remains elevated.") == "#:~:text=Inflation%20remains%20elevated."
    assert P.text_fragment("a-b, c&d") == "#:~:text=a%2Db%2C%20c%26d"


def test_a_pdf_source_has_no_text_fragment():
    rec = {"doc_id": "x", "type": "minutes", "format": "pdf", "title": "t", "url": "https://x/y.pdf", "summary": [{"text": "a", "evidence": {"paragraphs": [1], "fragments": [{"text": "some quoted words here and there", "paragraph": 1, "start": 0, "end": 22}], "coverage": 1.0}}], "changes_vs_previous": None, "model": "m",
           "prompt_version": "minutes-v1", "generated_at": "2026-09-21T08:00:00Z", "coverage": {"truncated": False, "paragraphs": [1]},
           "quotes": [{"text": "some quoted words here", "paragraph": 1, "start": 0, "end": 22}]}
    assert P.summary_json(rec)["quotes"][0]["href"] == "https://x/y.pdf"


def test_follow_up_documents_and_the_decisions_table_carry_their_slots(summarised):
    d = docs(summarised, "USD")
    by = {i["type"]: i for i in d["latest"]["follow_up"]}
    assert by["minutes"]["available"] is False and "summary" not in by["minutes"]                                     # the 16 Sep minutes are not published yet
    older = {i["type"]: i for i in next(t for t in d["timeline"] if t["meeting"] == "2026-06-17")["follow_up"]}
    assert older["minutes"]["summary"]["status"] == "ready" and older["minutes"]["doc_id"] in d["summaries"]
    assert d["summaries"][older["minutes"]["doc_id"]]["label"] == "Minutes"
    tl = {t["meeting"]: t for t in d["timeline"]}
    assert tl["2026-09-16"]["statement"]["summary"]["status"] == "ready" and tl["2026-07-29"]["statement"]["summary"]["status"] == "ready"
    rows = summarised[0]["banks"]["USD"]["decisions"]
    assert [r["summary"]["status"] for r in rows] == ["ready"] * 4 and rows[0]["summary"]["doc_id"] == FED_KEY


def test_only_the_summaries_the_page_shows_are_in_its_json(summarised):
    d = docs(summarised, "USD")
    referenced = {t["statement"]["doc_id"] for t in d["timeline"] if t["statement"]} | {i["doc_id"] for t in d["timeline"] for i in t["follow_up"] if i.get("summary", {}).get("status") == "ready"}
    assert set(d["summaries"]) == referenced and len(d["summaries"]) == 6                                             # 4 statements + 2 minutes
    assert docs(summarised, "AUD")["summaries"] == {}


def test_a_summary_that_failed_validation_is_never_shown_and_its_slot_says_why(summarised):
    d = docs(summarised, "GBP")
    assert d["summaries"] == {}                                                                                        # not one of the BoE summaries passed
    slot = d["latest"]["summary"]
    assert slot["status"] == "pending" and slot["label"] == "summary pending" and slot["doc_id"] == "GBP:statement:2026-09-17"
    assert slot["reason"].startswith("the generated summary failed the automatic check twice and is not shown (the output is not valid JSON")
    rows = summarised[0]["banks"]["GBP"]["decisions"]
    assert all(r["summary"]["status"] == "pending" and "failed the automatic check" in r["summary"]["reason"] for r in rows)
    assert not [f for f in SS.load(summarised[1].summaries) if f.startswith("GBP:statement")]                          # nothing stored either


def test_a_document_never_attempted_says_not_generated_yet(summarised):
    assert docs(summarised, "AUD")["latest"]["summary"]["reason"] == "not generated yet"
    assert docs(summarised, "NZD")["latest"]["summary"] == {"status": "pending", "label": "summary pending", "doc_id": None, "reason": "the document is not collected"}


def test_speeches_have_a_slot_too(summarised):
    sp = docs(summarised, "USD")["speeches"]
    assert sp and all(s["summary"]["status"] == "pending" and s["summary"]["reason"] == "not generated yet" for s in sp)


def test_the_payload_is_deterministic_with_summaries(summarised):
    ctx = load_context(summarised[1].dir)
    a = json.dumps(P.build(ctx, ASOF, load_pair_defs())["banks"]["USD"]["documents"], sort_keys=True)
    b = json.dumps(P.build(ctx, ASOF, load_pair_defs())["banks"]["USD"]["documents"], sort_keys=True)
    assert a == b and "generated_at" not in a and "2026-09-21" in a


# --- the page code ---------------------------------------------------------------------------------------------------------------------

JS = (ROOT / "static" / "cb.js").read_text()
CSS = (ROOT / "static" / "style.css").read_text()


def test_the_page_renders_summaries_quotes_notes_and_pending_reasons():
    for needle in ("function summaryBlock", "cb-sum-points", "cb-sum-quotes", "target=\"_blank\" rel=\"noopener\"", "s.note", "s.prompt_version", "s.model", "s.generated",
                   "function summaryDetails", "function summarySlot", "slot.reason", "summary pending"):
        assert needle in JS, needle
    assert "summary pending (phase 2b)" not in JS and "phase 2b" not in JS                                             # the placeholder wording is gone
    for cls in (".cb-summary", ".cb-sum-points", ".cb-sum-quotes", ".cb-sum-foot", ".cb-sum-doc"):
        assert cls in CSS, cls
    assert (ROOT / "public" / "cb.js").read_text() == JS and (ROOT / "public" / "style.css").read_text() == CSS       # the served copies are in step


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_a_point_shows_its_evidence_on_hover_and_click_and_links_to_the_source_text():
    import subprocess
    r = subprocess.run(["node", str(ROOT / "tests" / "cb_js" / "test_point_html.cjs")], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and "point evidence ok" in r.stdout, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    for cls in (".cb-sum-point", ".cb-sum-evidence", ".cb-sum-para mark", ".cb-sum-frag"):
        assert cls in CSS, cls
    for needle in ("function pointHtml", "function markFragments", "cb-sum-evidence", 'aria-expanded', "s.evidence && s.evidence[i]"):
        assert needle in JS, needle


def test_nothing_in_the_page_skeleton_is_hardcoded_for_summaries():
    tpl = (ROOT / "templates" / "central_banks.html.j2").read_text()
    assert "summary" not in tpl.lower()
