"""Phase 2b, the stage end to end with a recorded client: nothing here reaches the API. Real documents (the frozen phase 2a ones), recorded model outputs."""
from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from src import cb_collect as cc
from src.cb_docs import store as dst
from src.cb_summarize import prompts as PR
from src.cb_summarize import run as R
from src.cb_summarize import source as SRC
from src.cb_summarize import store as SS
from src.cb_summarize.client import APIError, RecordedClient, Response
from src.cb_summarize.config import load as load_cfg

from .cb_docs_helpers import FakeSession
from .cb_sum_helpers import FED_KEY, GOOD_FED, GOOD_TEXTS, MIN_V, NEXT_STMT_V, NOW, STMT_V, TODAY, collected_dir, dumps, fresh_copy, responder_generic, variant
from .test_cb_docs_collect import fetcher

CFG = load_cfg()
FKEY = R.failure_key(FED_KEY, PR.load("statement"), CFG)                 # doc_id | prompt_version | provider | model


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    return collected_dir(tmp_path_factory)


@pytest.fixture()
def paths(base, tmp_path):
    return cc.Paths(fresh_copy(base, tmp_path))


def go(paths, client, *, cfg=CFG, session=None, **kw):
    return R.run_summaries(paths, TODAY, client=client, fetcher=fetcher(session or FakeSession()), env={}, now=NOW, cfg=cfg, **kw)


def fed_only(paths, client, **kw):
    kw.setdefault("only", {FED_KEY})
    return go(paths, client, **kw)


# --- no key: the stage is skipped, not failed --------------------------------------------------------------------------------------------

def test_without_a_key_the_stage_is_skipped_and_touches_nothing(paths):
    rep = R.run_summaries(paths, TODAY, env={}, cfg=CFG, now=NOW)
    assert rep.enabled is False and "OPENAI_API_KEY is not set" in rep.skipped_reason and rep.calls == 0 and rep.new == 0
    assert not paths.summaries.exists()


def test_status_warns_that_the_key_is_missing_and_counts_what_waits(paths, monkeypatch):
    from src import cb_datasets as ds
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    title, heads, rows, notes = ds._summaries_status_section(paths, TODAY)
    assert title == "summaries (phase 2b)" and rows[0][0] == 0 and rows[0][1] > 20 and rows[0][2] == rows[0][1]
    assert any(n.startswith("WARN OPENAI_API_KEY is not set: the summaries stage is skipped") for n in notes)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    assert not any(n.startswith("WARN") for n in ds._summaries_status_section(paths, TODAY)[3])


def test_the_summaries_stage_is_last_and_not_part_of_a_plain_run():
    assert cc.STAGES[-1] == "summaries" and "summaries" not in cc.DEFAULT_STAGES and cc.DEFAULT_STAGES == cc.STAGES[:-1]


# --- the good path and the contract ----------------------------------------------------------------------------------------------------------

def test_a_good_output_is_stored_with_the_contract(paths):
    c = RecordedClient([dumps(GOOD_FED)], tokens=(1200, 340))
    rep = fed_only(paths, c)
    assert (rep.new, rep.calls, rep.failed_validation, rep.stopped) == (1, 1, [], None) and (rep.input_tokens, rep.output_tokens) == (1200, 340)
    assert rep.cost_usd == CFG.cost_usd(1200, 340) > 0
    files = sorted(p.name for p in paths.summaries.iterdir())
    assert files == ["summaries_2026-09.json"]                                                                          # the month of the statement
    rec = SS.load(paths.summaries)[FED_KEY]
    doc = dst.load_documents(paths)[(FED_KEY,)]
    assert rec["model"] == "gpt-5.6-terra" and rec["provider"] == "openai" and rec["prompt_version"] == STMT_V and rec["generated_at"] == "2026-09-21T08:00:00Z"
    assert rec["input_sha256"] == doc["text_sha256"]
    assert [p["text"] for p in rec["summary"]] == GOOD_TEXTS and all(set(p) == {"text", "evidence"} for p in rec["summary"])
    for p in rec["summary"]:                                                                                            # the evidence: paragraphs, the verbatim fragments, where they are
        ev = p["evidence"]
        assert set(ev) == {"paragraphs", "fragments", "coverage"} and ev["coverage"] == 1.0 and 1 <= len(ev["fragments"]) <= 3
        for f in ev["fragments"]:
            assert set(f) == {"text", "paragraph", "start", "end"} and doc["text"][f["start"]:f["end"]] == f["text"]
            assert f["paragraph"] in ev["paragraphs"] and f["text"] in doc["text"].split("\n")[f["paragraph"] - 1]
    assert len(rec["summary"]) == 3 and 1 <= len(rec["quotes"]) <= 5 and rec["coverage"]["paragraphs"] == [1, 2, 3, 4] and rec["coverage"]["truncated"] is False
    assert all(set(q) == {"paragraph", "text", "start", "end"} and doc["text"][q["start"]:q["end"]] == q["text"] for q in rec["quotes"])
    assert rec["numbers"] and all(doc["text"][n["start"]:n["end"]].strip() == n["source_text"] for n in rec["numbers"])
    assert rec["usage"] == {"input_tokens": 1200, "output_tokens": 340, "attempts": 1, "reasoning_tokens": 0}
    assert "text" not in rec and set(rec) >= {"doc_id", "model", "prompt_version", "generated_at", "input_sha256", "summary", "quotes", "changes_vs_previous", "numbers", "coverage"}


def test_the_call_carries_the_prompt_of_the_kind_and_the_numbered_paragraphs_of_the_document(paths):
    c = RecordedClient([dumps(GOOD_FED)])
    fed_only(paths, c)
    system, messages = c.calls[0]
    assert system == PR.load("statement").system and "Never use these words in a summary point" in system
    assert len(messages) == 1 and messages[0]["role"] == "user"
    body = messages[0]["content"]
    assert body.startswith("Document: Monetary policy decision statement\nBank: Federal Reserve\nSource: https://www.federalreserve.gov/")
    assert "[1] The Federal Open Market Committee approved the following statement" in body and "[4] Inflation remains elevated." in body


def test_changes_vs_previous_is_read_off_the_redline_for_statements_only(paths):
    fed_only(paths, RecordedClient([dumps(GOOD_FED)]))
    ch = SS.load(paths.summaries)[FED_KEY]["changes_vs_previous"]
    red = next(r for r in dst.load_redlines(paths) if r["currency"] == "USD" and r["meeting_date"] == date(2026, 9, 16))
    assert ch["vs_meeting"] == "2026-07-29" and ch["vs_doc_id"] == "USD:statement:2026-07-29"
    assert (ch["added_words"], ch["removed_words"]) == (red["added_words"], red["removed_words"])
    assert {"paragraph": 2, "removed": "maintain", "added": "raise"} in ch["changes"]                                     # exactly the words, no reading of them
    assert {"paragraph": 1, "removed": "9", "added": "12"} in ch["changes"] and {"paragraph": 1, "removed": "3", "added": "0"} in ch["changes"]      # the vote, word for word
    dropped = [c for c in ch["changes"] if c["paragraph"] is None]
    assert len(dropped) == 1 and dropped[0]["added"] == "" and dropped[0]["removed"].startswith("Voting against the monetary policy action were")   # a paragraph that is gone
    assert all(set(c) == {"paragraph", "removed", "added"} for c in ch["changes"]) and ch["truncated"] is False
    rec = R.run_summaries(paths, TODAY, client=RecordedClient(responder=responder_generic), fetcher=fetcher(FakeSession()), env={}, now=NOW, cfg=CFG, types={"minutes"})
    assert rec.new >= 1
    assert all(r["changes_vs_previous"] is None for r in SS.load(paths.summaries).values() if r["type"] != "statement")


def test_summary_files_are_deterministic_sorted_and_written_once(paths):
    fed_only(paths, RecordedClient([dumps(GOOD_FED)]))
    p = paths.summaries / "summaries_2026-09.json"
    first = p.read_bytes()
    assert p.read_text().endswith("\n") and json.loads(first)["version"] == 1
    SS.write(paths.summaries, SS.load(paths.summaries))
    assert p.read_bytes() == first                                                                                     # same records -> same bytes


# --- validation failure: one retry with the errors, then nothing ---------------------------------------------------------------------------

def paragraph_swapped(q_text):
    return variant(quotes=[{"paragraph": 2, "text": q_text}])


BAD = {
    "invented quote": (dumps(paragraph_swapped("The Committee decided to lower the target range for the federal funds rate")), "quote 1 is not verbatim in paragraph 2"),
    "invented number": (dumps(variant(summary=[GOOD_FED["summary"][0], "The Committee raised the target range to 4.5 percent.", GOOD_FED["summary"][2]])),
                        "'4.5 percent' of summary point 2 does not appear in the document"),
    "direction word": (dumps(variant(summary=GOOD_FED["summary"][:2] + ["The move is hawkish, the Committee says."])), "uses the word 'hawkish'"),
    "soft word not in the document": (dumps(variant(summary=GOOD_FED["summary"][:2] + ["The Committee signals a further increase in the target range."])),
                                      "the word 'signals' of summary point 3 is not in the document"),
    "too long": (dumps(variant(summary=GOOD_FED["summary"][:2] + ["The Committee " + "states that inflation remains elevated " * 30])), "characters; allowed 20-400"),
    "invalid json": ('Here is the summary: {"summary": [', "the output is not valid JSON"),
    "unsupported claim": (dumps(variant(summary=GOOD_FED["summary"][:2] + [{"text": "The Committee will begin buying government bonds and mortgage securities at a faster pace.",
                                                                            "evidence": {"paragraphs": [2], "fragments": GOOD_FED["summary"][1]["evidence"]["fragments"]}}])),
                          "summary point 3 is not supported by the paragraph(s) it cites [2]"),
    "altered fragment": (dumps(variant(summary=[GOOD_FED["summary"][0], {"text": GOOD_TEXTS[1], "evidence": {"paragraphs": [2], "fragments": [GOOD_FED["summary"][1]["evidence"]["fragments"][0].replace("percent", "per cent")]}},
                                                GOOD_FED["summary"][2]])),
                         "evidence fragment 1 of summary point 2 is not verbatim in the paragraph(s) it cites [2]"),
    "no evidence": (dumps(variant(summary=[{"text": t} for t in GOOD_TEXTS])), "needs 'evidence'"),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_a_bad_first_output_gets_one_retry_with_the_errors_as_feedback_and_a_good_second_is_stored(paths, case):
    bad, expected = BAD[case]
    c = RecordedClient([bad, dumps(GOOD_FED)])
    rep = fed_only(paths, c)
    assert (rep.new, rep.calls, rep.failed_validation) == (1, 2, [])
    second = c.calls[1][1]
    assert [m["role"] for m in second] == ["user", "assistant", "user"] and second[1]["content"] == bad                     # the model sees its own output
    assert expected in second[2]["content"] and second[2]["content"].startswith("Your previous output failed the automatic check")
    assert SS.load(paths.summaries)[FED_KEY]["usage"]["attempts"] == 2


@pytest.mark.parametrize("case", sorted(k for k in BAD if k != "invented quote"))                                                # (an invented quote alone: the summary is published without it - see below)
def test_two_bad_outputs_write_nothing_and_mark_validation_failed(paths, case):
    bad, expected = BAD[case]
    c = RecordedClient([bad, bad])
    rep = fed_only(paths, c)
    assert rep.new == 0 and rep.calls == 2 and [d for d, _ in rep.failed_validation] == [FED_KEY]
    assert SS.load(paths.summaries) == {}                                                                              # no summary, and never a repaired one
    assert not list(paths.summaries.glob("summaries_*.json"))
    f = SS.load_failures(paths.summaries)[FKEY]
    assert f["doc_id"] == FED_KEY and f["input_sha256"] == dst.load_documents(paths)[(FED_KEY,)]["text_sha256"] and expected in " ".join(f["errors"])
    again = fed_only(paths, RecordedClient([]))                                                                         # the same failure is not paid for again
    assert again.calls == 0 and again.unchanged == 1


def test_a_failed_document_does_not_stop_the_others(paths):
    def responder(system, messages):
        return dumps(GOOD_FED) if "Today's policy action will support a timelier return" in messages[0]["content"] else "not json"
    rep = go(paths, RecordedClient(responder=responder), types={"statement"}, banks={"USD", "GBP"})
    assert rep.new == 1 and len(rep.failed_validation) == 7 and rep.calls == 1 + 2 * 7                                  # 3 other FOMC + 4 BoE statements, 2 calls each
    assert list(SS.load(paths.summaries)) == [FED_KEY] and len(SS.load_failures(paths.summaries)) == 7


def alt_prompts(tmp_path, monkeypatch, version=NEXT_STMT_V):
    alt = tmp_path / "prompts_alt"
    shutil.copytree(PR.DIR, alt)
    (alt / "statement.md").write_text((alt / "statement.md").read_text().replace(STMT_V, version))
    monkeypatch.setattr(PR, "DIR", alt)


def test_a_new_prompt_version_summarises_again_and_a_success_clears_the_old_failure(paths, tmp_path, monkeypatch):
    fed_only(paths, RecordedClient(["nope", "nope"]))
    assert list(SS.load_failures(paths.summaries)) == [FKEY]
    alt_prompts(tmp_path, monkeypatch)
    rep = fed_only(paths, RecordedClient([dumps(GOOD_FED)]))
    assert rep.new == 1 and rep.calls == 1
    assert SS.load(paths.summaries)[FED_KEY]["prompt_version"] == NEXT_STMT_V and SS.load_failures(paths.summaries) == {}


# --- idempotence ---------------------------------------------------------------------------------------------------------------------------

def test_the_same_input_makes_no_call_the_second_time(paths):
    fed_only(paths, RecordedClient([dumps(GOOD_FED)]))
    snap = {p.name: p.read_bytes() for p in paths.summaries.iterdir()}
    c = RecordedClient([])                                                                                              # any call would raise: nothing is recorded
    rep = fed_only(paths, c)
    assert c.calls == [] and (rep.new, rep.calls, rep.unchanged) == (0, 0, 1)
    assert {p.name: p.read_bytes() for p in paths.summaries.iterdir()} == snap                                          # not a byte changed


def edited_fed_output() -> dict:
    """GOOD_FED for the statement whose paragraph 4 now says "Inflation remains elevated at this time." (the evidence and the quote follow the text)."""
    out = variant(quotes=[{"paragraph": 4, "text": "Inflation remains elevated at this time."}])
    out["summary"][2]["evidence"]["fragments"][1] = "Inflation remains elevated at this time. Today's policy action will support"
    return out


def test_a_changed_document_text_is_a_new_input_and_is_summarised_again(paths):
    fed_only(paths, RecordedClient([dumps(GOOD_FED)]))
    docs = dst.load_documents(paths)
    d = docs[(FED_KEY,)]
    new_text = d["text"].replace("Inflation remains elevated.", "Inflation remains elevated at this time.")
    d["text"], d["text_sha256"] = new_text, dst.X.sha256(new_text) if hasattr(dst, "X") else __import__("hashlib").sha256(new_text.encode()).hexdigest()
    dst.write_documents(paths, docs)
    edited = dumps(edited_fed_output())
    c = RecordedClient([edited])
    rep = fed_only(paths, c)
    assert (rep.new, rep.calls) == (1, 1)
    assert SS.load(paths.summaries)[FED_KEY]["input_sha256"] == d["text_sha256"]


def test_a_new_prompt_version_costs_one_call_per_document_of_that_kind_only(paths, tmp_path, monkeypatch):
    cfg = dataclasses.replace(CFG, max_documents=50)
    rep1 = go(paths, RecordedClient(responder=responder_generic), cfg=cfg, types={"statement", "minutes"}, banks={"USD", "AUD"})
    assert rep1.new == 14 and rep1.stopped is None                                                                      # 8 statements + 6 minutes (2 FOMC + 4 RBA in the fixtures)
    alt_prompts(tmp_path, monkeypatch)
    c = RecordedClient(responder=responder_generic)
    rep2 = go(paths, c, cfg=cfg, types={"statement", "minutes"}, banks={"USD", "AUD"})
    statements = [r for r in SS.load(paths.summaries).values() if r["type"] == "statement"]
    assert rep2.new == len(statements) == len(c.calls) == 8 and rep2.unchanged == 6                                     # the minutes are still summarised under minutes-v1
    assert {r["prompt_version"] for r in statements} == {NEXT_STMT_V}
    assert {r["prompt_version"] for r in SS.load(paths.summaries).values() if r["type"] == "minutes"} == {MIN_V}


# --- caps ----------------------------------------------------------------------------------------------------------------------------------

def test_the_document_cap_stops_the_run_cleanly_and_the_next_run_goes_on(paths):
    cfg = dataclasses.replace(CFG, max_documents=2)
    c = RecordedClient(responder=responder_generic)
    rep = go(paths, c, cfg=cfg)
    assert (rep.new, rep.calls) == (2, 2) and rep.stopped == "document cap reached (2 per run)" and rep.pending > 0
    rep2 = go(paths, RecordedClient(responder=responder_generic), cfg=cfg)
    assert rep2.new == 2 and rep2.unchanged >= 2 and set(SS.load(paths.summaries)) >= {d for d in list(SS.load(paths.summaries))[:2]}
    assert len(SS.load(paths.summaries)) == 4


def test_the_input_token_budget_counts_the_real_usage_and_stops_before_the_call_that_would_exceed_it(paths):
    system = PR.load("statement").system                                                                                # (the estimate of a call counts the prompt: v5 is longer than v3)
    usd = sorted((d for d in dst.load_documents(paths).values() if d["currency"] == "USD" and d["type"] == "statement"), key=lambda d: d["published_date"], reverse=True)
    est = [(len(system) + sum(len(p) + 8 for p in d["text"].split("\n"))) // CFG.chars_per_token for d in usd]
    budget = 1000 + est[1]                                                                                              # the first call fits, the second fits after 1000 real tokens, the third does not
    assert est[0] <= budget and 2000 + est[2] > budget
    cfg = dataclasses.replace(CFG, max_input_tokens=budget)
    c = RecordedClient(responder=responder_generic, tokens=(1000, 200))
    rep = go(paths, c, cfg=cfg, types={"statement"}, banks={"USD"})
    assert rep.new == 2 and rep.input_tokens == 2000 and rep.stopped.startswith(f"input token budget reached (2000 used, {budget} allowed per run)")
    tiny = dataclasses.replace(CFG, max_input_tokens=200)
    rep0 = go(paths, RecordedClient([]), cfg=tiny, types={"statement"})
    assert rep0.calls == 0 and rep0.new == 0 and rep0.stopped.startswith("input token budget reached")


# --- what is summarised, in which order ------------------------------------------------------------------------------------------------------

def test_candidates_follow_the_priority_the_last_four_meetings_and_the_relevance_filter(paths):
    from src.cb_docs import store as ST
    from src import cb_datasets as ds
    docs = sorted(ST.load_documents(paths).values(), key=lambda d: (d["currency"], d["published_date"], d["doc_id"]))
    meetings = ds.load_meetings(paths.meetings)
    todo = R.candidates(docs, meetings, TODAY, CFG)
    ranks = [CFG.priority[d["type"]] for d in todo]
    assert ranks == sorted(ranks) and set(ranks) == {1, 2, 3, 4}
    assert [d["type"] for d in todo[:28]] == ["statement"] * 28 and len({d["doc_id"] for d in todo}) == len(todo)
    stmts = [d for d in todo if d["type"] == "statement"]
    assert [d["published_date"] for d in stmts[:7]] == sorted((d["published_date"] for d in stmts[:7]), reverse=True)  # newest first within a rank
    speeches = [d for d in todo if d["type"] in ("speech", "testimony")]
    assert speeches and all(d["relevance"] == "monetary" and (TODAY - d["published_date"]).days <= 60 for d in speeches)
    assert not any(d["type"] in ("presser_video",) for d in todo)
    older = {c: sorted(m["date"] for m in meetings[c] if m["date"] <= TODAY)[:-4] for c in meetings}
    assert not any(d["meeting_date"] in older.get(d["currency"], ()) for d in todo if d["meeting_date"])               # only the last four decided meetings


def test_speeches_that_did_not_pass_the_relevance_filter_are_never_summarised(paths):
    c = RecordedClient(responder=responder_generic)
    go(paths, dataclasses.replace(CFG) and c, cfg=dataclasses.replace(CFG, max_documents=60, max_input_tokens=10**7), types={"speech", "testimony"})
    docs = dst.load_documents(paths)
    done = [docs[(k,)] for k in SS.load(paths.summaries)]
    assert done and all(d["relevance"] == "monetary" for d in done)


# --- long documents: downloaded at need, summarised, never committed -------------------------------------------------------------------------

def test_long_documents_are_downloaded_summarised_and_not_committed(paths):
    before = {p.name: p.read_bytes() for p in paths.documents.iterdir()}
    s = FakeSession()
    rep = go(paths, RecordedClient(responder=responder_generic), session=s, types={"minutes"}, banks={"AUD", "USD"})
    assert rep.new >= 4 and not rep.failed_validation
    assert {p.name: p.read_bytes() for p in paths.documents.iterdir()} == before                                        # the document store is untouched: no text committed
    recs = SS.load(paths.summaries)
    docs = dst.load_documents(paths)
    aud = recs["AUD:minutes:2026-08-11"]
    assert aud["source"]["method"] == "html-selector" and aud["input_sha256"] == docs[("AUD:minutes:2026-08-11",)]["text_sha256"]   # the hash phase 2a stored
    assert "text" not in aud and docs[("AUD:minutes:2026-08-11",)]["text"] is None
    assert any("rba-board-minutes/2026/2026-08-11.html" in u for u in s.urls())
    s2 = FakeSession()
    rep2 = go(paths, RecordedClient([]), session=s2, types={"minutes"}, banks={"AUD", "USD"})
    assert rep2.calls == 0 and rep2.new == 0 and not any("minutes" in u for u in s2.urls())                             # a summarised document is not even downloaded again


def test_a_pdf_document_is_read_with_the_phase_2a_extraction(paths):
    rep = go(paths, RecordedClient(responder=responder_generic), types={"summary_of_opinions"}, banks={"JPY"})
    assert rep.new + len(rep.source_errors) == 3
    recs = SS.load(paths.summaries)
    assert recs and all(r["source"]["method"] == "pypdf" and r["format"] == "pdf" for r in recs.values())


def test_a_document_that_cannot_be_downloaded_is_reported_and_tried_again_not_marked(paths):
    from .cb_docs_helpers import Resp
    url = "https://www.rba.gov.au/monetary-policy/rba-board-minutes/2026/2026-08-11.html"
    rep = go(paths, RecordedClient(responder=responder_generic), session=FakeSession(extra={url: Resp(404)}), types={"minutes"}, banks={"AUD"})
    assert [d for d, _ in rep.source_errors] == ["AUD:minutes:2026-08-11"] and "404" in rep.source_errors[0][1] and rep.new == 3    # the other three minutes are done
    assert not SS.load_failures(paths.summaries) and "AUD:minutes:2026-08-11" not in SS.load(paths.summaries)
    again = go(paths, RecordedClient(responder=responder_generic), types={"minutes"}, banks={"AUD"})
    assert again.new == 1 and not again.source_errors                                                                   # the page is back: summarised on the next run


def test_a_document_longer_than_the_limit_is_cut_at_a_paragraph_and_says_so(paths):
    cfg = dataclasses.replace(CFG, max_source_chars=1500)
    c = RecordedClient(responder=responder_generic)
    rep = go(paths, c, cfg=cfg, types={"minutes"}, banks={"AUD"})
    assert rep.new >= 1
    rec = SS.load(paths.summaries)["AUD:minutes:2026-08-11"]
    assert rec["source"]["truncated"] and rec["coverage"]["truncated"] and rec["source"]["chars_sent"] <= 1500 and rec["source"]["chars"] > 1500
    n_sent = sum(1 for line in c.calls[0][1][0]["content"].splitlines() if line.startswith("[") and "] " in line[:8])
    assert n_sent == rec["source"]["paragraphs_sent"]


# --- the API -------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["auth", "bad_request", "overloaded", "rate_limit", "server", "transport"])
def test_an_api_failure_stops_the_run_cleanly_and_writes_nothing(paths, kind):
    c = RecordedClient([APIError(kind, 401 if kind == "auth" else None, "boom")])
    rep = go(paths, c, types={"statement"})
    assert rep.stopped == f"the API stopped the run ({kind}): boom" and rep.new == 0 and rep.calls == 0 and len(c.calls) == 1   # no second document is tried
    assert not paths.summaries.exists() or not list(paths.summaries.glob("*.json"))


def test_a_stage_error_is_reported_never_raised_by_the_collector(paths, monkeypatch, capsys):
    monkeypatch.setattr("src.cb_summarize.run.run_summaries", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("kaput")))
    rc = cc.main(["--data-dir", str(paths.dir), "--stage", "summaries"])
    assert rc == 0 and "summaries: FAILED RuntimeError: kaput" in capsys.readouterr().out


def test_the_run_is_recorded_in_the_state_for_status(paths):
    from src import cb_datasets as ds
    fed_only(paths, RecordedClient([dumps(GOOD_FED)], tokens=(1500, 400)))
    st = cc.load_state(paths)["summaries"]
    assert st["last_run"]["new"] == 1 and st["last_run"]["input_tokens"] == 1500 and st["totals"] == {"calls": 1, "input_tokens": 1500, "output_tokens": 400, "summaries": 1}
    import hashlib
    before = hashlib.sha256(paths.state.read_bytes()).hexdigest()
    R.run_summaries(paths, TODAY, client=RecordedClient([]), fetcher=fetcher(FakeSession()), env={}, now=NOW.replace(hour=9), cfg=CFG, only={FED_KEY})   # a later run, nothing to do
    assert hashlib.sha256(paths.state.read_bytes()).hexdigest() == before                                               # a run that did nothing leaves state.json alone (no commit for a timestamp)
    assert cc.load_state(paths)["summaries"]["totals"]["summaries"] == 1
    title, heads, rows, notes = ds._summaries_status_section(paths, TODAY)
    assert rows[0][0] == 1 and rows[0][4] == 1 and rows[0][5] == 0                                                      # stored 1 - the last ACTIVE run: 1 new (an idle run is not logged)
    assert any("all runs: 1 summaries, 1 calls, 1500 in / 400 out tokens" in n for n in notes)
    fed_only(paths, RecordedClient(["x", "x"]), only={"USD:statement:2026-07-29"})
    assert any(n.startswith(f"validation_failed USD:statement:2026-07-29 ({STMT_V}, openai gpt-5.6-terra)") for n in ds._summaries_status_section(paths, TODAY)[3])


# --- the dry run: what would it cost, without calling anything ------------------------------------------------------------------------------

def test_the_dry_run_measures_what_is_pending_without_calling_the_model_or_writing_anything(paths):
    from .test_cb_docs_collect import snapshot
    before = snapshot(paths.dir)
    est = R.estimate(paths, TODAY, fetcher=fetcher(FakeSession()), cfg=CFG, banks={"USD"}, types={"statement", "minutes"})
    assert [t for _id, t, _c, _k in est.documents].count("statement") == 4 and [t for _id, t, _c, _k in est.documents].count("minutes") == 2
    assert est.output_tokens == 6 * CFG.estimate_output_tokens and est.input_tokens == sum(k for *_x, k in est.documents) > 0
    assert est.cost_usd == CFG.cost_usd(est.input_tokens, est.output_tokens) and est.runs == 1
    assert snapshot(paths.dir) == before                                                                                # not a file was written
    text = R.estimate_report(est, CFG)
    assert "6 documents still to summarise, 1 runs at 12 documents per run" in text and "list prices of gpt-5.6-terra, checked 2026-09-21" in text and "statement" in text and "minutes" in text


def test_the_dry_run_leaves_out_what_is_already_summarised_or_failed(paths):
    fed_only(paths, RecordedClient([dumps(GOOD_FED)]))
    fed_only(paths, RecordedClient(["x", "x"]), only={"USD:statement:2026-07-29"})
    est = R.estimate(paths, TODAY, fetcher=fetcher(FakeSession()), cfg=CFG, banks={"USD"}, types={"statement"})
    assert sorted(i for i, *_ in est.documents) == ["USD:statement:2026-04-29", "USD:statement:2026-06-17"]


def test_the_backlog_is_split_into_capped_runs(paths):
    est = R.estimate(paths, TODAY, fetcher=fetcher(FakeSession()), cfg=dataclasses.replace(CFG, max_documents=5), banks={"USD", "AUD"}, types={"statement", "minutes"})
    assert len(est.documents) == 14 and est.runs == 3


def test_the_dry_run_command_needs_no_key_and_prints_the_estimate(paths, capsys, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("src.cb_summarize.run.estimate", lambda paths_, today, **kw: R.Estimate(documents=[("USD:statement:x", "statement", 900, 800)], input_tokens=800, output_tokens=700, cost_usd=0.0129, runs=1))
    rc = cc.main(["--data-dir", str(paths.dir), "--stage", "summaries", "--summaries-dry-run", "--summaries-bank", "USD"])
    out = capsys.readouterr().out
    assert rc == 0 and "summaries dry run (no call): 1 documents still to summarise" in out and "about $0.01" in out
    assert not paths.summaries.exists()


# --- gaps found by the mutations ---------------------------------------------------------------------------------------------------------

def test_a_document_of_an_older_meeting_than_the_last_four_is_not_a_candidate(paths):
    from src import cb_datasets as ds
    docs = sorted(dst.load_documents(paths).values(), key=lambda d: (d["currency"], d["published_date"], d["doc_id"]))
    meetings = ds.load_meetings(paths.meetings)
    last = sorted(m["date"] for m in meetings["USD"] if m["date"] <= TODAY)
    fifth = last[-5]
    old = dict(next(d for d in docs if d["doc_id"] == FED_KEY), doc_id="USD:statement:old", meeting_date=fifth, published_date=fifth)
    got = {d["doc_id"] for d in R.candidates(docs + [old], meetings, TODAY, CFG)}
    assert "USD:statement:old" not in got and FED_KEY in got
    future = dict(old, doc_id="USD:statement:future", meeting_date=date(2026, 10, 28), published_date=date(2026, 10, 28))
    assert "USD:statement:future" not in {d["doc_id"] for d in R.candidates(docs + [future], meetings, TODAY, CFG)}               # not decided yet


def test_a_failure_of_an_older_prompt_version_is_history_and_is_dropped(paths):
    SS.write_failures(paths.summaries, {"USD:statement:2026-06-17|statement-v0": {"doc_id": "USD:statement:2026-06-17", "prompt_version": "statement-v0", "provider": "openai", "model": "gpt-5.6-terra", "input_sha256": "x",
                                                                              "errors": ["old"], "at": "2026-01-01T00:00:00Z"}})
    rep = fed_only(paths, RecordedClient([dumps(GOOD_FED)]))
    assert rep.new == 1 and SS.load_failures(paths.summaries) == {}


def test_a_success_clears_the_failure_of_the_same_document(paths):
    fed_only(paths, RecordedClient(["nope", "nope"]))
    assert list(SS.load_failures(paths.summaries)) == [FKEY]
    docs = dst.load_documents(paths)
    d = docs[(FED_KEY,)]
    import hashlib
    d["text"] = d["text"].replace("Inflation remains elevated.", "Inflation remains elevated at this time.")
    d["text_sha256"] = hashlib.sha256(d["text"].encode()).hexdigest()
    dst.write_documents(paths, docs)                                                                                    # the statement changed: a new input, the failure no longer applies
    rep = fed_only(paths, RecordedClient([dumps(edited_fed_output())]))
    assert rep.new == 1 and SS.load_failures(paths.summaries) == {} and FED_KEY in SS.load(paths.summaries)


def test_changes_vs_previous_lists_added_changed_and_dropped_paragraphs():
    from src.cb_summarize import changes as CH
    row = {"prev_meeting_date": date(2026, 7, 29), "prev_doc_id": "USD:statement:2026-07-29", "added_words": 12, "removed_words": 3,
           "ops_json": json.dumps([{"p": 0, "prev": 0, "kind": "same", "ops": []},
                                   {"p": 1, "prev": 1, "kind": "changed", "ops": [["=", "The rate is "], ["-", "3.5"], ["+", "3.75"], ["=", " percent; "], ["-", "old"], ["+", "new "], ["+", "words"]]},
                                   {"p": 2, "prev": None, "kind": "added", "ops": [["+", " A brand new paragraph. "]]},
                                   {"p": None, "prev": 2, "kind": "removed", "ops": [["-", "A paragraph that is gone."]]}])}
    ch = CH.from_redline(row)
    assert ch["changes"] == [{"paragraph": 2, "removed": "3.5", "added": "3.75"}, {"paragraph": 2, "removed": "old", "added": "new words"},
                             {"paragraph": 3, "removed": "", "added": "A brand new paragraph."}, {"paragraph": None, "removed": "A paragraph that is gone.", "added": ""}]
    assert (ch["vs_meeting"], ch["vs_doc_id"], ch["added_words"], ch["removed_words"], ch["truncated"]) == ("2026-07-29", "USD:statement:2026-07-29", 12, 3, False)
    many = dict(row, ops_json=json.dumps([{"p": i, "prev": None, "kind": "added", "ops": [["+", f"p{i}"]]} for i in range(CH.MAX_ITEMS + 5)]))
    assert len(CH.from_redline(many)["changes"]) == CH.MAX_ITEMS and CH.from_redline(many)["truncated"] is True


# --- no extractable text: marked once, never retried; a media availability is never a candidate ---------------------------------------------------

AUD_MINUTES = "AUD:minutes:2026-08-11"
AUD_URL = "https://www.rba.gov.au/monetary-policy/rba-board-minutes/2026/2026-08-11.html"


def no_layout(session_extra=None):
    from .cb_docs_helpers import Resp
    return FakeSession(extra={AUD_URL: Resp(200, b"<html><body><div id=\"other\"><p>The layout changed.</p></div></body></html>", {"Content-Type": "text/html"}), **(session_extra or {})})


def test_a_page_without_extractable_text_is_marked_no_text_once_and_not_fetched_again(paths):
    rep = go(paths, RecordedClient(responder=responder_generic), session=no_layout(), types={"minutes"}, banks={"AUD"})
    assert [d for d, _ in rep.no_text] == [AUD_MINUTES] and "no container of the page holds the document text" in rep.no_text[0][1] and rep.source_errors == []
    marks = SS.load_no_text(paths.summaries)
    assert set(marks) == {AUD_MINUTES} and marks[AUD_MINUTES]["url"] == AUD_URL and marks[AUD_MINUTES]["at"] == "2026-09-21T08:00:00Z"
    assert rep.new == 3 and SS.load_failures(paths.summaries) == {} and AUD_MINUTES not in SS.load(paths.summaries)      # the other three are summarised
    before = (paths.summaries / "no_text.json").read_bytes()
    s2 = FakeSession()
    again = go(paths, RecordedClient([]), session=s2, types={"minutes"}, banks={"AUD"})
    assert again.no_text == [] and again.calls == 0 and not any(AUD_URL == u for u in s2.urls())                        # not fetched, not asked
    assert (paths.summaries / "no_text.json").read_bytes() == before


def test_a_page_too_short_to_be_a_document_is_no_text_too(paths):
    from .cb_docs_helpers import Resp
    short = Resp(200, b"<html><body><div id=\"content\"><p>Media availability: the Governor will speak at 11:20.</p></div></body></html>", {"Content-Type": "text/html"})
    rep = go(paths, RecordedClient(responder=responder_generic), session=FakeSession(extra={AUD_URL: short}), types={"minutes"}, banks={"AUD"})
    assert [d for d, _ in rep.no_text] == [AUD_MINUTES] and "no container of the page holds the document text" in rep.no_text[0][1]      # 90 characters are no minutes


def test_a_pdf_with_too_little_text_is_no_text_too(paths):
    rep = go(paths, RecordedClient(responder=responder_generic), types={"minutes"}, banks={"JPY"})                    # the fixture keeps only the cover page of the BoJ minutes
    marks = SS.load_no_text(paths.summaries)
    assert rep.new == 0 and sorted(marks) == ["JPY:minutes:2026-04-28", "JPY:minutes:2026-06-16"]
    assert all("characters extracted" in m["reason"] and m["url"].endswith(".pdf") for m in marks.values())


def test_a_failed_download_is_not_no_text(paths):
    from .cb_docs_helpers import Resp
    rep = go(paths, RecordedClient(responder=responder_generic), session=FakeSession(extra={AUD_URL: Resp(503)}), types={"minutes"}, banks={"AUD"})
    assert rep.no_text == [] and [d for d, _ in rep.source_errors] == [AUD_MINUTES] and not (paths.summaries / "no_text.json").exists()


def test_a_document_whose_link_changed_is_looked_at_again(paths):
    go(paths, RecordedClient(responder=responder_generic), session=no_layout(), types={"minutes"}, banks={"AUD"})
    docs = dst.load_documents(paths)
    docs[(AUD_MINUTES,)]["url"] = AUD_URL + "?v=2"
    dst.write_documents(paths, docs)
    s = FakeSession(extra={AUD_URL + "?v=2": no_layout().extra[AUD_URL]})
    rep = go(paths, RecordedClient([]), session=s, types={"minutes"}, banks={"AUD"})
    assert AUD_URL + "?v=2" in s.urls() and [d for d, _ in rep.no_text] == [AUD_MINUTES]                                # asked again, marked again for the new url
    assert SS.load_no_text(paths.summaries)[AUD_MINUTES]["url"].endswith("?v=2")


def test_the_dry_run_skips_marked_documents_and_says_which_errors_would_be_marked(paths):
    go(paths, RecordedClient(responder=responder_generic), session=no_layout(), types={"minutes"}, banks={"AUD"})
    est = R.estimate(paths, TODAY, fetcher=fetcher(FakeSession()), cfg=CFG, banks={"AUD"}, types={"minutes"})
    assert est.documents == [] and est.source_errors == []                                                             # marked: not even measured
    fresh = R.estimate(paths, TODAY, fetcher=fetcher(no_layout()), cfg=CFG, banks={"USD"}, types={"minutes"})
    assert fresh.source_errors == [] and len(fresh.documents) == 2


def test_the_status_and_the_page_say_no_text(paths, monkeypatch):
    from src import cb_datasets as ds
    from src.cb_compute import payload as P
    from src.cb_loader import load_context
    go(paths, RecordedClient(responder=responder_generic), session=no_layout(), types={"minutes"}, banks={"AUD"})
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    notes = ds._summaries_status_section(paths, TODAY)[3]
    assert any(n.startswith(f"no_text {AUD_MINUTES}: no container of the page holds the document text (marked 2026-09-21, not retried)") for n in notes)
    ctx = load_context(paths.dir)
    slot = P.summary_slot(ctx, next(d for d in ctx.documents if d["doc_id"] == AUD_MINUTES))
    assert slot == {"status": "pending", "label": "summary pending", "doc_id": AUD_MINUTES, "reason": "no extractable text: the page was fetched and holds none (marked once, not retried)"}


def test_an_announcement_is_never_a_candidate(paths):
    from src import cb_datasets as ds
    docs = sorted(dst.load_documents(paths).values(), key=lambda d: (d["currency"], d["published_date"], d["doc_id"]))
    assert any("/media-availability" in d["url"] and d["relevance"] == "non_document" for d in docs)                    # there are some in the real feed fixture
    todo = R.candidates(docs, ds.load_meetings(paths.meetings), TODAY, CFG)
    assert not any("/media-availability" in d["url"] for d in todo)
    forced = [dict(d, relevance="monetary") for d in docs if "/media-availability" in d["url"]]                          # even if its relevance were wrong, the marker is `non_document`
    assert all(d["relevance"] != "non_document" for d in R.candidates(docs + forced, ds.load_meetings(paths.meetings), TODAY, CFG))


def test_the_command_line_can_narrow_the_stage_to_banks_types_and_documents(paths, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr("src.cb_summarize.run.run_summaries", lambda p, t, **kw: seen.update(kw) or R.SummariesReport())
    cc.main(["--data-dir", str(paths.dir), "--stage", "summaries", "--summaries-bank", "USD", "--summaries-type", "statement", "--summaries-doc", FED_KEY,
             "--summaries-doc", "USD:statement:2026-07-29"])
    assert seen["banks"] == {"USD"} and seen["types"] == {"statement"} and seen["only"] == {FED_KEY, "USD:statement:2026-07-29"}
    seen.clear()
    cc.main(["--data-dir", str(paths.dir), "--stage", "summaries"])
    assert seen["banks"] is None and seen["types"] is None and seen["only"] is None                                   # nothing narrowed: everything that is due


# --- named speakers and the grounding of a speech ------------------------------------------------------------------------------------------------

ROSTER = {"people": [{"name": "Christopher J. Waller", "currency": "USD"}, {"name": "Michelle W. Bowman", "currency": "USD"}, {"name": "Kazuo Ueda", "currency": "JPY"},
                     {"name": "Jo Li", "currency": "USD"}]}


def test_the_speakers_of_a_document_are_its_banks_roster_its_speaker_and_the_names_it_gives_after_a_title():
    text = "Chairman Warsh opened. Governor Cook's remarks followed. Then Mr. Smith and Dr. Nguyen spoke, and Bob agreed. Thank you, Mr. Chairman, and Ms. President."
    got = R.speakers_of({"currency": "USD", "speaker": "Philip N. Jefferson"}, text, ROSTER)                          # Jefferson: only as the speaker of the document
    assert got == ("bowman", "christopher", "cook", "jefferson", "michelle", "nguyen", "philip", "smith", "waller", "warsh")     # every name word; not Ueda (another bank), not "Jo Li" (2 letters), not "Bob" (no title)
    assert "chairman" not in got and "president" not in got                                                            # a title after "Mr." is not a name
    assert R.people_of({"currency": "USD", "speaker": "Philip N. Jefferson"}, text, ROSTER) == (("christopher", "waller"), ("michelle", "bowman"), ("philip", "jefferson"), ("warsh",), ("cook",),
                                                                                                ("smith",), ("nguyen",))     # each person as their name words, surname last
    assert R.speakers_of({"currency": "JPY", "speaker": ""}, "nothing here", ROSTER) == ("kazuo", "ueda")
    assert R.speakers_of({"currency": "USD"}, "", None) == ()


SPEECH_PARAS = ["Good morning. I am pleased to be here to talk about the economy and the outlook for monetary policy in the coming year, and I thank the organisers for the invitation.",
                "I expect inflation to move down as the labor market cools, and I see the risks to employment as more important than a month ago.",
                "Inflation is likely to remain above the 2 percent goal for some time, and I support a cautious approach to the next steps in policy."]
SPEECH_OUT = {"summary": [{"text": "Waller talks about the economy and the outlook for monetary policy.", "evidence": {"paragraphs": [1], "fragments": ["talk about the economy and the outlook for monetary policy"]}},
                          {"text": "Waller expects inflation to move down as the labor market cools.", "evidence": {"paragraphs": [2], "fragments": ["I expect inflation to move down as the labor market cools,"]}},
                          {"text": "Governor Waller says inflation is likely to remain above the 2 percent goal for some time.",
                           "evidence": {"paragraphs": [3], "fragments": ["Inflation is likely to remain above the 2 percent goal for some time,"]}}],
              "quotes": [{"paragraph": 2, "text": "I expect inflation to move down as the labor market cools"}], "coverage": [1, 2, 3]}


def speech_run(client, speakers, cfg=CFG, paras=SPEECH_PARAS, currency="USD"):
    from src.cb_summarize import source as SRC
    doc = {"currency": currency, "type": "speech", "url": "https://www.federalreserve.gov/newsevents/speech/waller20260903a.htm", "speaker": "Christopher J. Waller"}
    src = SRC.from_paragraphs(paras, "html-selector", 10**6, min_chars=1)
    return R.summarise(client, cfg, PR.load("speech"), doc, src, speakers=speakers)


def test_a_speech_that_says_i_expect_is_summarised_as_waller_expects_at_the_first_attempt():
    c = RecordedClient([dumps(SPEECH_OUT)])
    ok, usage, errs = speech_run(c, R.speakers_of({"currency": "USD", "speaker": "Christopher J. Waller"}, "\n".join(SPEECH_PARAS), ROSTER))
    assert ok.ok and errs == [] and usage["attempts"] == 1 and len(c.calls) == 1
    assert [p["evidence"]["fragments"][0]["paragraph"] for p in ok.points] == [1, 2, 3]


def test_the_same_output_without_a_known_speaker_fails_twice_and_the_feedback_says_who_may_be_named():
    c = RecordedClient([dumps(SPEECH_OUT), dumps(SPEECH_OUT)])
    ok, usage, errs = speech_run(c, ())
    assert ok is None and usage["attempts"] == 2
    assert any("'expects' of summary point 2 is not attributed to the bank or to a named speaker" in e for e in errs)
    assert "named speaker" in c.calls[1][1][2]["content"]


def test_a_retry_after_an_unsupported_claim_carries_the_missing_words():
    bad = json.loads(dumps(SPEECH_OUT))
    bad["summary"][0]["text"] = "Waller talks about the economy, the outlook for monetary policy and the bond purchases of the central bank."
    c = RecordedClient([dumps(bad), dumps(SPEECH_OUT)])
    ok, usage, errs = speech_run(c, ("waller",))
    assert ok.ok and usage["attempts"] == 2
    fb = c.calls[1][1][2]["content"]
    assert "summary point 1 is not supported by the paragraph(s) it cites [1]" in fb and "'bond'" in fb and "'purchases'" in fb


def with_point(i, **changes):
    out = json.loads(dumps(SPEECH_OUT))
    out["summary"][i] = dict(out["summary"][i], **changes)
    return dumps(out)


@pytest.mark.parametrize("what, output, cfg_over, needle", [
    ("min_support", with_point(0, text="Waller talks about the economy and the outlook for monetary policy in the coming year today."), {"min_support": 1.0}, "100% needed"),
    ("fragment_words", with_point(0, evidence={"paragraphs": [1], "fragments": ["talk about the economy and"]}), {"fragment_words": (6, 40)}, "has 5 words; allowed 6-40"),
    ("evidence_paragraphs", with_point(0, evidence={"paragraphs": [1, 2], "fragments": ["talk about the economy and the outlook"]}), {"evidence_paragraphs": (1, 1)}, "cites 2 paragraphs as evidence; allowed 1-1"),
    ("attribution_words", with_point(0, text="Waller noted the economy and the outlook for monetary policy."), {"attribution_words": ()}, "'noted'"),
    ("fragment_shared", with_point(0, evidence={"paragraphs": [1], "fragments": ["to talk about the economy and"]}), {"fragment_shared": 3}, "has 2 content word(s) in common with the point; at least 3 needed"),
    ("fragments", with_point(0, evidence={"paragraphs": [1], "fragments": ["talk about the economy and the outlook", "the outlook for monetary policy in the coming year"]}), {"fragments": (1, 1)},
     "has 2 evidence fragments; allowed 1-1"),
    ("attribution_pronouns", with_point(1, text="Waller says the labor market cools; he expects inflation to move down.",
                                        evidence={"paragraphs": [2], "fragments": ["I expect inflation to move down as the labor market cools,"]}), {"attribution_pronouns": ()},
     "'expects' of summary point 2 is not attributed"),
])
def test_the_run_applies_the_grounding_rules_of_the_config(what, output, cfg_over, needle):
    ok, usage, errs = speech_run(RecordedClient([output]), ("waller",))                                                # the configured rules: accepted
    assert ok is not None and ok.ok, errs
    strict = dataclasses.replace(CFG, **cfg_over)
    c = RecordedClient([output, output])
    ok, usage, errs = speech_run(c, ("waller",), cfg=strict)
    assert ok is None and any(needle in e for e in errs), (what, errs)


def test_the_run_gives_each_document_the_speakers_of_its_own_bank_and_its_own_speaker(paths, monkeypatch):
    seen = {}
    real = R.summarise

    def spy(client, cfg, prompt, doc, src, speakers=(), trace=None, people=()):
        seen[doc["doc_id"]] = speakers
        return real(client, cfg, prompt, doc, src, speakers=speakers, trace=trace, people=people)
    monkeypatch.setattr(R, "summarise", spy)
    go(paths, RecordedClient(responder=responder_generic), cfg=dataclasses.replace(CFG, max_documents=60, max_input_tokens=10**7), types={"speech"}, banks={"JPY"})
    assert seen and all("ueda" in sp and "masu" in sp for sp in seen.values())                                          # the roster of the bank
    docs = dst.load_documents(paths)
    takata = next(k for k in seen if docs[(k,)]["speaker"] == "Hajime Takata")
    assert "takata" in seen[takata] and not {"waller", "powell", "warsh"} & set(seen[takata])                          # no member of another bank


# --- what the log says: first attempt, retry, failure and why ------------------------------------------------------------------------------------

def test_a_first_attempt_that_passes_is_counted_and_has_nothing_to_explain(paths):
    from src import cb_datasets as ds
    rep = fed_only(paths, RecordedClient([dumps(GOOD_FED)]))
    assert (rep.first_pass, rep.retried, rep.rejected) == (1, [], [])
    line, md = ds.summaries_report(rep)
    assert "  ATTEMPTS 1 passed at the first attempt, 0 after the retry, 0 published partially, 0 failed twice" in line and "RETRIED" not in line and "REJECTED" not in line


def test_a_retry_that_passes_says_what_the_first_attempt_got_wrong(paths):
    from src import cb_datasets as ds
    bad, expected = BAD["unsupported claim"]
    rep = fed_only(paths, RecordedClient([bad, dumps(GOOD_FED)]))
    assert rep.new == 1 and rep.first_pass == 0 and rep.rejected == [] and len(rep.retried) == 1
    doc_id, errors = rep.retried[0]
    assert doc_id == FED_KEY and any(expected in e for e in errors)
    line, md = ds.summaries_report(rep)
    assert "  ATTEMPTS 0 passed at the first attempt, 1 after the retry, 0 published partially, 0 failed twice" in line
    assert f"  RETRIED {FED_KEY}: the first attempt failed - " in line and "summary point 3 is not supported by the paragraph(s) it cites [2]" in line and "REJECTED" not in line


def test_a_failed_document_puts_the_refused_output_in_the_log(paths):
    from src import cb_datasets as ds
    bad, expected = BAD["unsupported claim"]
    other = bad.replace("mortgage securities", "corporate debt")
    rep = fed_only(paths, RecordedClient([bad, other]))
    assert rep.new == 0 and rep.first_pass == 0 and rep.retried == [] and [d for d, _ in rep.failed_validation] == [FED_KEY]
    assert rep.rejected == [(FED_KEY, other)]                                                                           # the LAST output the check refused
    line, md = ds.summaries_report(rep)
    assert "  ATTEMPTS 0 passed at the first attempt, 0 after the retry, 0 published partially, 1 failed twice" in line
    assert f"  FIRST ATTEMPT {FED_KEY} failed - " in line and "summary point 3 is not supported by the paragraph(s) it cites [2]" in line.split("FIRST ATTEMPT")[1].split("\n")[0]
    assert f"  REJECTED OUTPUT {FED_KEY}: " in line and "government bonds and corporate debt" in line
    long = ds.summaries_report(dataclasses.replace(rep, rejected=[(FED_KEY, "word " * 2000)]))[0]
    assert len(max(long.splitlines(), key=len)) < 3100                                                                  # one bounded line per document


NEG_PARAS = ["Good morning. I am not going to talk about the past in this speech, because the audience knows why the present matters for the coming year of monetary policy.",
             "I expect inflation to move down as the labor market cools, and I see the risks to employment as more important than a month ago.",
             "Inflation is likely to remain above the 2 percent goal for some time, and I support a cautious approach to the next steps in policy."]


def test_the_run_checks_the_negation_and_the_banks_own_name_words_it_is_configured_with():
    inverted = with_point(0, text="Waller says he is going to talk about the past in this speech.", evidence={"paragraphs": [1], "fragments": ["I am not going to talk about the past in this speech,"]})
    ok, usage, errs = speech_run(RecordedClient([inverted, inverted]), ("waller",), paras=NEG_PARAS)
    assert ok is None and any("summary point 1 has none but the closest clause of the paragraph(s) it cites has one" in e for e in errs)
    ok, usage, errs = speech_run(RecordedClient([inverted]), ("waller",), cfg=dataclasses.replace(CFG, negations=()), paras=NEG_PARAS)
    assert ok is not None and ok.ok, errs                                                                               # the configured negations decide: none configured, none checked
    bank = with_point(0, text="The Reserve Bank of Australia talks about the economy and the outlook for monetary policy.")
    ok, usage, errs = speech_run(RecordedClient([bank]), ("waller",), currency="AUD")
    assert ok is not None and ok.ok, errs                                                                               # the bank's own name is the attribution, whatever the paragraphs say
    ok, usage, errs = speech_run(RecordedClient([bank, bank]), ("waller",), currency="USD")
    assert ok is None and any("names 'Australia'" in e for e in errs)                                                   # (for another bank it is a name like any other)


def test_the_run_hands_the_officials_and_the_people_of_the_banks_roster_to_the_source_and_the_check(paths, monkeypatch):
    seen_officials, seen_people = {}, {}
    real_load, real_sum = SRC.load, R.summarise

    def load(doc, fetcher, max_chars, officials=(), prose=None):
        seen_officials[doc["doc_id"]] = officials
        return real_load(doc, fetcher, max_chars, officials, prose)

    def spy(client, cfg, prompt, doc, src, speakers=(), trace=None, people=()):
        seen_people[doc["doc_id"]] = people
        return real_sum(client, cfg, prompt, doc, src, speakers=speakers, trace=trace, people=people)
    monkeypatch.setattr(SRC, "load", load)
    monkeypatch.setattr(R, "summarise", spy)
    go(paths, RecordedClient(responder=responder_generic), cfg=dataclasses.replace(CFG, max_documents=60, max_input_tokens=10**7), types={"speech"}, banks={"JPY"})
    assert seen_officials and all("ueda" in o and "himino" in o and "waller" not in o for o in seen_officials.values())                 # the surnames of the bank's members
    assert seen_people and all(("kazuo", "ueda") in p and all(isinstance(g, tuple) and g == tuple(w.lower() for w in g) for g in p) for p in seen_people.values())   # first name, surname last


def test_the_feedback_of_the_retry_repeats_the_hard_limits():
    m = PR.feedback_message(["a", "b"])
    assert m.startswith("Your previous output failed the automatic check for these reasons:\n- a\n- b\n") and "at most 3 paragraphs and giving at most 3 fragments of 5 to 40 words" in m
    assert "3 to 6 points" in m and m.endswith("copied character for character.")


def test_the_first_attempt_of_a_document_that_failed_twice_is_not_the_last_one(paths):
    from src import cb_datasets as ds
    bad, expected = BAD["unsupported claim"]
    rep = fed_only(paths, RecordedClient([bad, "not json at all"]))
    assert rep.new == 0 and [d for d, _ in rep.failed_validation] == [FED_KEY]
    (doc_id, first), = rep.first_errors
    assert doc_id == FED_KEY and any(expected in e for e in first) and not any("valid JSON" in e for e in first)                 # attempt 1: the unsupported claim
    assert "the output is not valid JSON" in " ".join(rep.failed_validation[0][1]) and rep.rejected == [(FED_KEY, "not json at all")]     # attempt 2: its own reason and its own output
    line = ds.summaries_report(rep)[0]
    assert "  FIRST ATTEMPT %s failed - " % FED_KEY in line and "not supported by the paragraph(s) it cites [2]" in line.split("FIRST ATTEMPT")[1].split("\n")[0]
