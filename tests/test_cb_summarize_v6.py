"""Phase 2b, prompts v6: the last gates and the way a summary is published - each point and each quote checked on its own (the ones that still fail after the retry are
removed and the rest is published, when at least three valid points remain and, for a decision statement, the decision point is one of them), the names that are always
allowed (the speaker of the document with their title, the bank, its bodies), documents that are not prose (decks of tables and slides), and the measurement options of
the run (reasoning effort, scratch). Real texts: the Fed statement of 16 Sep 2026, the Fed statement of 29 Apr 2026, the ECB decks of Lane and Schnabel."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from src import cb_collect as cc
from src.cb_docs import store as dst
from src.cb_summarize import prompts as PR
from src.cb_summarize import run as R
from src.cb_summarize import source as SRC
from src.cb_summarize import store as SS
from src.cb_summarize import verify as V
from src.cb_summarize.client import RecordedClient

from .cb_docs_helpers import FakeSession, Resp, statement_paras
from .cb_sum_helpers import FED_KEY, GOOD_FED, GOOD_TEXTS, NOW, TODAY, dumps, variant
from .test_cb_docs_collect import fetcher
from .test_cb_summarize_run import CFG, FKEY, base, fed_only, go, paths                                              # noqa: F401  (the fixtures)
from .test_cb_summarize_verify import PARAS, P_ACT, P_RATE, P_VOTE, grounded, point, rules

FIX = Path(__file__).parent / "fixtures" / "cb"
DEC = {"rate_terms": CFG.decision["rate_terms"], "action_terms": CFG.decision["action_terms"]}
P_RESERVES = point("The Committee is continuing its policy of maintaining ample reserves in the banking system.", [2], "The Committee is continuing its policy of maintaining ample reserves in the banking system.")
BAD_NUMBER = point("The Committee decided to raise the target range to 4.5 percent.", [2], P_RATE["evidence"]["fragments"][0])                    # an invented number: a point that fails
BAD_CLAIM = point("The Committee will begin buying government bonds and mortgage securities at a faster pace.", [2], P_RATE["evidence"]["fragments"][0])
GOOD_QUOTES = [{"paragraph": 4, "text": "Inflation remains elevated."}]


def check(*pts, quotes=None, **over):
    return V.verify({"summary": list(pts), "quotes": GOOD_QUOTES if quotes is None else quotes, "coverage": [1, 2, 3, 4]}, PARAS, **rules(**over))


# --- each point and each quote is checked on its own ---------------------------------------------------------------------------------------------------

def test_a_point_that_fails_is_set_aside_with_its_reasons_and_the_others_are_kept():
    r = check(P_VOTE, P_RATE, P_ACT, BAD_NUMBER)
    assert not r.ok and r.fatal == []
    assert [p["text"] for p in r.points] == [P_VOTE["text"], P_RATE["text"], P_ACT["text"]] and r.summary == [p["text"] for p in r.points]
    (d,) = r.dropped_points
    assert d["text"] == BAD_NUMBER["text"] and any("'4.5 percent' of summary point 4 does not appear in the document" in e for e in d["errors"]) and all("summary point 4" in e for e in d["errors"])
    assert set(d["errors"]) <= set(r.errors) and r.publishable(3) and not r.publishable(4)


def test_every_reason_of_a_point_stays_with_the_point_and_the_valid_ones_are_untouched():
    r = check(BAD_CLAIM, P_VOTE, P_RATE, BAD_NUMBER, P_ACT)
    assert [d["text"] for d in r.dropped_points] == [BAD_CLAIM["text"], BAD_NUMBER["text"]]
    assert len(r.dropped_points[0]["errors"]) == 2 and any("is not supported" in e for e in r.dropped_points[0]["errors"]) and any("content word(s) in common" in e for e in r.dropped_points[0]["errors"])
    assert all(p["evidence"]["coverage"] == 1.0 and p["evidence"]["fragments"] for p in r.points) and len(r.points) == 3


def test_a_quote_that_fails_is_set_aside_and_the_rest_are_kept():
    r = check(P_VOTE, P_RATE, P_ACT, quotes=[{"paragraph": 4, "text": "Inflation remains elevated."}, {"paragraph": 2, "text": "The Committee decided to lower the target range for the federal funds rate"}])
    assert not r.ok and len(r.quotes) == 1 and r.quotes[0]["text"] == "Inflation remains elevated." and r.publishable(3)
    (q,) = r.dropped_quotes
    assert q["text"].startswith("The Committee decided to lower") and "quote 2 is not verbatim in paragraph 2" in q["errors"][0]
    none = check(P_VOTE, P_RATE, P_ACT, quotes=[{"paragraph": 2, "text": "The Committee decided to lower the target range for the federal funds rate"}])
    assert none.quotes == [] and none.publishable(3) and none.coverage == [1, 2, 3]                                                     # every quote failed: published without one


def test_a_fully_valid_output_has_nothing_set_aside():
    r = check(*GOOD_FED["summary"], quotes=GOOD_FED["quotes"])
    assert r.ok and r.errors == [] and r.dropped_points == [] and r.dropped_quotes == [] and r.fatal == [] and r.decision is None and r.publishable(3)


def test_what_removal_cannot_cure_is_fatal():
    assert check(P_VOTE, P_RATE).fatal and "'summary' has 2 points; allowed 3-6" in check(P_VOTE, P_RATE).fatal[0]                        # too few points as they came
    seven = check(*([P_VOTE, P_RATE, P_ACT, P_RESERVES] * 2)[:7])
    assert seven.fatal and not seven.publishable(3)
    assert V.verify({"summary": "text", "quotes": [], "coverage": [1]}, PARAS, **rules()).fatal
    assert V.verify({"summary": [P_VOTE, P_RATE, P_ACT], "quotes": "x", "coverage": [1]}, PARAS, **rules()).fatal
    no_quotes = check(P_VOTE, P_RATE, P_ACT, quotes=[])
    assert no_quotes.fatal and "'quotes' has 0 items; allowed 1-5" in no_quotes.fatal[0]                                                # the model gave none
    six_quotes = check(P_VOTE, P_RATE, P_ACT, quotes=[{"paragraph": 4, "text": "Inflation remains elevated."}] * 6)
    assert six_quotes.fatal and not six_quotes.publishable(3)
    assert not check(P_VOTE, P_RATE, P_ACT, total_chars=100).publishable(3)                                                             # the valid points alone are too long


def test_the_total_length_counts_the_valid_points_only():
    long_bad = point("The Committee will begin buying government bonds and mortgage securities " * 6, [2], P_RATE["evidence"]["fragments"][0])
    r = check(P_VOTE, P_RATE, P_ACT, long_bad, total_chars=330)
    assert not r.ok and r.fatal == [] and r.publishable(3)                                                                              # with the long one removed the rest fits
    assert any("characters in all; allowed 330" in e for e in r.errors)                                                                 # ... but the output as it came did not


def test_the_numbers_and_the_coverage_follow_the_points_that_are_kept():
    r = check(BAD_NUMBER, P_VOTE, P_RATE, P_ACT, quotes=GOOD_QUOTES)
    assert not r.ok
    assert [n["point"] for n in r.numbers] == sorted(n["point"] for n in r.numbers) and {n["point"] for n in r.numbers} <= {1, 2, 3}          # renumbered among the points kept
    assert {(n["point"], n["text"]) for n in r.numbers} >= {(1, "12"), (2, "1/4 percentage point")}
    assert r.coverage == [1, 2, 3, 4]                                                                                                   # the paragraphs the kept points and quotes draw on
    only_bad_coverage = V.verify({"summary": [P_VOTE, P_RATE, P_ACT], "quotes": GOOD_QUOTES, "coverage": [9]}, PARAS, **rules())
    assert not only_bad_coverage.ok and only_bad_coverage.coverage == [1, 2, 3, 4] and only_bad_coverage.publishable(3)                # a wrong declared coverage is recomputed
    full = check(*GOOD_FED["summary"], quotes=GOOD_FED["quotes"])
    assert full.ok and full.coverage == [1, 2, 3, 4]


def test_every_error_about_a_point_says_which_point_and_every_error_about_a_quote_which_quote():
    for text, why in ((BAD_NUMBER, "does not appear"), (BAD_CLAIM, "is not supported")):
        r = check(P_VOTE, P_RATE, P_ACT, text)
        assert all(V.point_of(e) == 4 or V.point_of(e) is None for e in r.errors) and any(V.point_of(e) == 4 and why in e for e in r.errors)
    assert V.point_of("summary point 12 has 3 characters") == 12 and V.point_of("'summary' has 2 points; allowed 3-6") is None
    assert V.quote_of("quote 3 is not verbatim") == 3 and V.quote_of("'quotes' has 0 items") is None


# --- a decision statement keeps its decision point --------------------------------------------------------------------------------------------------------

def test_a_decision_statement_needs_a_valid_point_that_states_the_decision_on_the_rate():
    ok = check(P_VOTE, P_RATE, P_ACT, decision=DEC)
    assert ok.ok and ok.decision is True
    none = check(P_VOTE, P_ACT, P_RESERVES, decision=DEC)
    assert not none.ok and none.decision is False and not none.publishable(3)
    assert any("a decision statement summary must state the decision on the policy rate in one point" in e for e in none.errors)
    broken = check(P_VOTE, BAD_NUMBER, P_ACT, P_RESERVES, decision=DEC)                                                                 # the point states it but fails: 3 valid points, none of them the decision
    assert not broken.ok and broken.decision is False and len(broken.points) == 3 and not broken.publishable(3)
    assert not any("must state the decision" in e for e in broken.errors)                                                               # (it was stated: its own reasons say what is wrong)
    assert check(P_VOTE, BAD_NUMBER, P_RATE, P_ACT, decision=DEC).publishable(3)                                                        # the good decision point survives the bad one
    assert check(P_VOTE, P_ACT, P_RESERVES).decision is None                                                                            # not asked for: not judged


def test_the_decision_lexicon_finds_the_decision_of_every_frozen_statement_and_not_the_labour_market():
    rate, act = V.decision_regex(DEC["rate_terms"]), V.forms_of_text(" ".join(DEC["action_terms"]), adverbs=False)
    for ccy, day in (("USD", "2026-09-16"), ("AUD", "2026-08-11"), ("CAD", "2026-06-10"), ("JPY", "2026-09-18"), ("CHF", "2026-06-18"), ("GBP", "2026-09-17"), ("EUR", "2026-09-10")):
        hits = [s for p in statement_paras(ccy, day) for s in V._SENTENCE.split(p) if V.states_decision(s, rate, act)]
        assert hits, (ccy, day)
    for text in ("The unemployment rate has changed little and job gains have kept pace with the workforce.", "Inflation remains elevated and the rate of increase has slowed.",
                 "Economic activity is expanding at a solid pace.", "The interest rate outlook is uncertain."):
        assert not V.states_decision(text, rate, act), text
    assert V.states_decision("The Bank of Canada today held its target for the overnight rate at 2.25%.", rate, act)
    assert V.states_decision("The MPC voted by a majority of 6-3 to maintain Bank Rate at 3.75%.", rate, act)
    assert V.states_decision("The Bank will encourage the uncollateralized overnight call rate to remain at around 1.25 percent.", rate, act)


# --- the publication of a run ---------------------------------------------------------------------------------------------------------------------------

def out(*pts, quotes=None):
    return dumps({"summary": list(pts), "quotes": quotes or GOOD_FED["quotes"], "coverage": [1, 2, 3, 4]})


def test_a_summary_with_a_point_that_still_fails_after_the_retry_is_published_without_it(paths):
    bad = out(*GOOD_FED["summary"], BAD_CLAIM)
    c = RecordedClient([bad, bad])
    rep = fed_only(paths, c)
    assert (rep.new, rep.calls, rep.failed_validation, rep.first_pass, rep.retried) == (1, 2, [], 0, []) and len(rep.partial) == 1
    doc_id, dropped, dropped_quotes = rep.partial[0]
    assert doc_id == FED_KEY and [d["text"] for d in dropped] == [BAD_CLAIM["text"]] and dropped_quotes == []
    rec = SS.load(paths.summaries)[FED_KEY]
    assert [p["text"] for p in rec["summary"]] == GOOD_TEXTS and rec["usage"]["attempts"] == 2                                       # the three valid points
    assert [d["text"] for d in rec["dropped_points"]] == [BAD_CLAIM["text"]] and any("is not supported" in e for e in rec["dropped_points"][0]["errors"]) and rec["dropped_quotes"] == []
    assert SS.load_failures(paths.summaries) == {}                                                                                      # published: no failure marker
    assert c.calls[1][1][2]["content"].count("summary point 4") >= 2                                                                    # the retry was told what was wrong with point 4


def test_the_retry_is_still_the_first_chance_a_point_fixed_at_the_second_attempt_is_not_dropped(paths):
    bad = out(*GOOD_FED["summary"], BAD_CLAIM)
    good = out(*GOOD_FED["summary"], P_RESERVES)
    rep = fed_only(paths, RecordedClient([bad, good]))
    assert rep.new == 1 and rep.partial == [] and len(rep.retried) == 1
    rec = SS.load(paths.summaries)[FED_KEY]
    assert len(rec["summary"]) == 4 and rec["dropped_points"] == []


def test_the_report_and_the_log_say_which_points_were_removed_and_why(paths):
    from src import cb_datasets as ds
    bad = out(*GOOD_FED["summary"], BAD_CLAIM, quotes=GOOD_FED["quotes"] + [{"paragraph": 2, "text": "The Committee decided to lower the target range for the federal funds rate"}])
    rep = fed_only(paths, RecordedClient([bad, bad]))
    line = ds.summaries_report(rep)[0]
    assert "  ATTEMPTS 0 passed at the first attempt, 0 after the retry, 1 published partially, 0 failed twice" in line and "1 failed validation" not in line and "summaries: 1 new" in line
    part = next(x for x in line.splitlines() if x.startswith("  PARTIAL"))
    assert part.startswith(f"  PARTIAL {FED_KEY}: published without 1 point(s) and 1 quote(s) - ") and f"[{BAD_CLAIM['text'][:70]}]" in part
    assert "is not supported by the paragraph(s) it cites [2]" in part and "[quote The Committee decided to lower the target range fo]" in part and "quote 3 is not verbatim in paragraph 2" in part
    doc = next(x for x in line.splitlines() if x.startswith("  DOC"))
    assert doc.startswith(f"  DOC {FED_KEY}: partial; 2 attempt(s); 2000 in / 600 out (0 reasoning); $") and doc.endswith("; 3 points, 1 removed")


def test_three_valid_points_out_of_five_are_enough(paths):
    bad = out(GOOD_FED["summary"][0], GOOD_FED["summary"][1], GOOD_FED["summary"][2], BAD_CLAIM, BAD_NUMBER)
    rep = fed_only(paths, RecordedClient([bad, bad]))
    assert rep.new == 1 and len(rep.partial) == 1 and len(rep.partial[0][1]) == 2 and rep.failed_validation == []


def test_fewer_than_three_valid_points_and_the_summary_stays_pending_with_its_reasons(paths):
    two = out(GOOD_FED["summary"][0], GOOD_FED["summary"][1], BAD_CLAIM, BAD_NUMBER)
    rep = fed_only(paths, RecordedClient([two, two]))
    assert rep.new == 0 and [d for d, _ in rep.failed_validation] == [FED_KEY] and SS.load(paths.summaries) == {}
    assert FKEY in SS.load_failures(paths.summaries) and any("is not supported" in e or "does not appear" in e for e in SS.load_failures(paths.summaries)[FKEY]["errors"])


def test_a_statement_whose_decision_point_fails_stays_pending_even_with_three_valid_points(paths):
    bad = out(P_VOTE, BAD_NUMBER, P_ACT, P_RESERVES)
    rep = fed_only(paths, RecordedClient([bad, bad]))
    assert rep.new == 0 and [d for d, _ in rep.failed_validation] == [FED_KEY] and SS.load(paths.summaries) == {} and rep.partial == []
    assert rep.docs[0]["outcome"] == "pending"


def test_the_decision_point_is_asked_of_a_decision_statement_only(paths):
    src = SRC.from_paragraphs(PARAS, "stored", 10**6, min_chars=1)
    bad = out(P_VOTE, BAD_NUMBER, P_ACT, P_RESERVES)
    statement = {"doc_id": "x", "currency": "USD", "type": "statement", "url": "https://x", "speaker": ""}
    ok, usage, errs = R.summarise(RecordedClient([bad, bad]), CFG, PR.load("statement"), statement, src)
    assert ok is None and any("does not appear in the document" in e for e in errs)
    minutes = dict(statement, type="minutes")
    ok, usage, errs = R.summarise(RecordedClient([bad, bad]), CFG, PR.load("minutes"), minutes, src)
    assert ok is not None and not ok.ok and len(ok.points) == 3 and ok.decision is None                                                 # the minutes need no decision point
    opening = dict(statement, type="opening_statement")
    ok, usage, errs = R.summarise(RecordedClient([bad, bad]), CFG, PR.load("statement"), opening, src)
    assert ok is not None and ok.decision is None                                                                                       # (the config lists the types: statement)


def test_a_first_attempt_that_can_be_published_is_kept_when_the_retry_is_unusable(paths):
    bad = out(*GOOD_FED["summary"], BAD_CLAIM)
    rep = fed_only(paths, RecordedClient([bad, "not json at all"]))
    assert rep.new == 1 and len(rep.partial) == 1 and rep.failed_validation == []
    rec = SS.load(paths.summaries)[FED_KEY]
    assert [p["text"] for p in rec["summary"]] == GOOD_TEXTS and rec["usage"]["attempts"] == 2
    worse = out(GOOD_FED["summary"][0], GOOD_FED["summary"][1], BAD_CLAIM, BAD_NUMBER)
    rep2 = fed_only(paths, RecordedClient([worse, "not json"]), only={"USD:statement:2026-07-29"})
    assert rep2.new == 0 or rep2.considered == 1                                                                                          # (another document: its own answers)


def test_the_better_attempt_is_the_one_published(paths):
    five = out(*GOOD_FED["summary"], P_RESERVES, BAD_CLAIM)                                                                              # 4 valid, 1 bad
    four = out(*GOOD_FED["summary"], BAD_CLAIM)                                                                                          # 3 valid, 1 bad
    rep = fed_only(paths, RecordedClient([five, four]))
    rec = SS.load(paths.summaries)[FED_KEY]
    assert rep.new == 1 and len(rec["summary"]) == 4 and [d["text"] for d in rec["dropped_points"]] == [BAD_CLAIM["text"]]


def test_a_published_summary_carries_what_was_removed_into_the_page_data(paths):
    from src.cb_compute import payload as P
    bad = out(*GOOD_FED["summary"], BAD_CLAIM)
    fed_only(paths, RecordedClient([bad, bad]))
    rec = SS.load(paths.summaries)[FED_KEY]
    j = P.summary_json(rec, "\n".join(PARAS))
    assert [d["text"] for d in j["dropped"]] == [BAD_CLAIM["text"]] and j["dropped"][0]["reason"] == rec["dropped_points"][0]["errors"][0] and len(j["points"]) == 3
    assert P.summary_json(dict(rec, dropped_points=[]))["dropped"] == [] and P.summary_json({k: v for k, v in rec.items() if k != "dropped_points"})["dropped"] == []     # an older record


# --- the names that are always allowed ----------------------------------------------------------------------------------------------------------------------

def test_the_speaker_of_the_document_with_their_title_and_the_bank_and_its_bodies_are_allowed_names():
    paras = ["Modern independent central banks are not independent of democratic government in the important sense that their authority originates in legislation enacted by elected representatives."
             " Parliament defines the objectives, establishes the powers, and retains the authority to amend the framework within which we operate."]
    quotes = [{"paragraph": 1, "text": "Parliament defines the objectives, establishes the powers"}]
    p1 = point("Governor Andrew Bailey says modern independent central banks are not independent of democratic government.", [1],
               "Modern independent central banks are not independent of democratic government in the important sense")
    p2 = point("The Bank of England's Monetary Policy Committee amends the framework that Parliament defines and operates within.", [1], "Parliament defines the objectives, establishes the powers, and retains the authority to amend the framework")
    p3 = point("Bailey says Parliament defines the objectives and establishes the powers of the Bank.", [1], "Parliament defines the objectives, establishes the powers, and retains the authority")
    common = dict(speakers=("andrew", "bailey"), people=(("andrew", "bailey"),), bank_terms=("bank", "england", "governor"), allowed_names=CFG.allowed_names)
    r = V.verify({"summary": [p1, p2, p3], "quotes": quotes, "coverage": [1]}, paras, **rules(**common))
    assert r.ok, r.errors                                                                                                                # Andrew, Bailey, Bank of England, Monetary Policy Committee: none of them in the paragraph
    bare = V.verify({"summary": [p1, p2, p3], "quotes": quotes, "coverage": [1]}, paras, **rules())
    assert not bare.ok and any("names 'Andrew', 'Bailey'" in e or "names 'England'" in e or "names 'Monetary'" in e for e in bare.errors)      # nobody told the check who they are


def test_the_role_of_the_speaker_is_an_allowed_name_and_the_message_says_who_speaks():
    doc = {"currency": "EUR", "role": "Member of the Executive Board", "speaker": "Philip R. Lane"}
    assert {"european", "central", "bank", "member", "executive", "board"} <= set(R.bank_terms(doc)) and "philip" not in R.bank_terms(doc)          # the bank and the title; the name comes from the roster
    assert "governor" in R.bank_terms({"currency": "GBP", "role": "Governor", "speaker": "Andrew Bailey"}) and "kevin" not in R.bank_terms({"currency": "USD", "role": "Chair", "speaker": "Kevin Warsh"})
    m = PR.user_message("speech", "Bank of England", "https://x", ["a"], speaker="Governor Andrew Bailey")
    assert m == "Document: Speech\nBank: Bank of England\nSpeaker: Governor Andrew Bailey\nSource: https://x\n\nParagraphs:\n[1] a\n"
    assert "Speaker:" not in PR.user_message("speech", "Bank of England", "https://x", ["a"])


def test_the_run_tells_the_model_who_speaks_and_not_a_page_code(paths):
    seen = []
    c = RecordedClient(responder=lambda system, messages: seen.append(messages[0]["content"]) or __import__("tests.cb_sum_helpers", fromlist=["x"]).generic_output(messages))
    go(paths, c, cfg=dataclasses.replace(CFG, max_documents=60, max_input_tokens=10**7), types={"speech"}, banks={"JPY", "AUD"})
    assert any(m.startswith("Document: Speech\nBank: Bank of Japan\nSpeaker: ") for m in seen)
    for m in seen:
        line = next((x for x in m.splitlines() if x.startswith("Speaker: ")), None)
        assert line is None or " " in line[len("Speaker: "):].strip()
        assert "Speaker: sp-" not in m and "Speaker: mc-" not in m


def test_the_prompts_of_v6_say_what_the_new_rules_ask():
    for kind in PR.KINDS:
        s = PR.load(kind).system
        assert "Do not create relations between facts that the source does not state" in s and "unless the cited paragraphs say it in those terms" in s and "Paraphrase close to the text" in s, kind
    assert "The FIRST summary point is the decision itself and nothing else" in PR.load("statement").system
    assert 'by title and surname as the "Speaker:" line of the message gives them ("Governor Bailey says ...", "Chair Warsh says ...")' in PR.load("speech").system and "never as \"the speaker\"" in PR.load("speech").system
    assert "The decision on the policy rate" in PR.load("minutes").system
    assert all(PR.load(k).version == f"{k}-v6" for k in PR.KINDS)


# --- documents that are not prose ----------------------------------------------------------------------------------------------------------------------------

DECKS = json.loads((FIX / "non_prose_decks.json").read_text())
PROSE = json.loads((FIX / "prose_controls.json").read_text())


def test_the_ecb_decks_and_a_page_of_headings_are_not_prose_and_every_real_text_is():
    lane = DECKS["EUR:speech:320dbc76f248"]
    assert SRC.not_prose(lane, CFG.non_prose) == "14% of the characters are digits: a deck of tables or slides, not prose"                      # the Lane speech
    assert "13% of the characters are digits" in SRC.not_prose(DECKS["EUR:speech:419f8df75f1f"], CFG.non_prose)                                # the Schnabel deck
    assert SRC.not_prose(DECKS["CAD:speech:aae6ed7f7f1d"], CFG.non_prose).startswith("only 21% of the text is full sentences")              # a page of headings and links
    for k, paras in PROSE.items():
        assert SRC.not_prose(paras, CFG.non_prose) is None, k
    assert SRC.not_prose(PARAS, CFG.non_prose) is None


def test_each_limit_of_the_measure_decides_on_its_own():
    limits = CFG.non_prose
    prose = PROSE["GBP:speech:c2e9c4edbf10"]
    assert SRC.not_prose(prose, limits) is None
    lines = ["A short line."] * 30 + prose
    assert "of the lines are short" in SRC.not_prose(lines, limits)
    numbers = prose + ["1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 " * 30]
    assert "of the characters are digits" in SRC.not_prose(numbers, limits)
    slides = ["Title Only Words No Verb Here"] * 12 + [prose[0]]
    assert "of the text is full sentences" in SRC.not_prose(slides, limits)
    m = SRC.prose_metrics(prose)
    assert 0.9 < m["prose_share"] <= 1 and m["digit_ratio"] < 0.01 and m["short_share"] < 0.2
    assert SRC.prose_metrics([]) == {"prose_share": 0.0, "digit_ratio": 0.0, "short_share": 0.0}
    assert SRC.not_prose(prose, dict(limits, prose_share_min=0.0, digit_ratio_max=1.0, short_share_max=1.0)) is None and SRC.not_prose(prose, dict(limits, prose_share_min=1.01))


def test_a_source_that_is_not_prose_raises_a_non_prose_error_only_when_asked_to_look():
    lane = DECKS["EUR:speech:320dbc76f248"]
    with pytest.raises(SRC.SourceError) as e:
        SRC.from_paragraphs(lane, "html-selector", 10**6, min_chars=1, prose=CFG.non_prose)
    assert e.value.kind == "non_prose" and "digits" in str(e.value)
    assert SRC.from_paragraphs(lane, "html-selector", 10**6, min_chars=1).paragraphs == lane                                                     # not asked: as before
    assert SRC.from_paragraphs(PROSE["GBP:speech:c2e9c4edbf10"], "html-selector", 10**6, min_chars=1, prose=CFG.non_prose).paragraphs


LANE_URL = "https://www.ecb.europa.eu/press/key/date/2026/html/ecb.sp260911~x.en.html"


def lane_doc():
    return {"doc_id": "EUR:speech:320dbc76f248", "currency": "EUR", "type": "speech", "url": LANE_URL, "format": "html", "speaker": "Philip R. Lane", "role": "Member of the Executive Board", "text": None}


def test_the_lane_deck_is_marked_non_prose_once_and_never_summarised(paths, monkeypatch):
    from src import cb_datasets as ds
    docs = dst.load_documents(paths)
    eur = next(d for d in docs.values() if d["currency"] == "EUR" and d["type"] == "speech" and d["relevance"] == "monetary")
    loads = []

    def load(doc, f, max_chars, officials=(), prose=None):
        loads.append(doc["doc_id"])
        return SRC.from_paragraphs(DECKS["EUR:speech:320dbc76f248"], "html-selector", max_chars, min_chars=1, prose=prose)
    monkeypatch.setattr(SRC, "load", load)
    c = RecordedClient([])
    cfg = dataclasses.replace(CFG, max_documents=60, max_input_tokens=10**7)
    rep = go(paths, c, only={eur["doc_id"]}, cfg=cfg)
    assert c.calls == [] and rep.new == 0 and rep.non_prose and rep.non_prose[0][0] == eur["doc_id"] and rep.no_text == []
    mark = SS.load_no_text(paths.summaries)[eur["doc_id"]]
    assert mark["kind"] == "non_prose" and "digits" in mark["reason"] and mark["url"] == eur["url"]
    line, md = ds.summaries_report(rep)
    assert "1 newly marked non_prose" in line and f"  NON_PROSE {eur['doc_id']}: " in line and "(marked once, not summarised)" in line
    assert next(x for x in line.splitlines() if x.startswith("  DOC")).startswith(f"  DOC {eur['doc_id']}: non_prose; 0 attempt(s); 0 in / 0 out (0 reasoning); $0.0000")
    rep2 = go(paths, RecordedClient([]), only={eur["doc_id"]}, cfg=cfg)
    assert rep2.unchanged == 1 and loads == [eur["doc_id"]]                                                                                   # not fetched again


def test_a_non_prose_document_shows_its_link_and_says_why_it_has_no_summary(paths):
    from src.cb_compute import payload as P
    rec = {"url": "https://x/y.htm", "reason": "14% of the characters are digits: a deck of tables or slides, not prose", "kind": "non_prose", "at": "2026-09-21T10:00:00Z"}

    class Ctx:
        summaries, summary_failures = {}, {}
        summary_no_text = {"EUR:speech:z": rec}
    slot = P.summary_slot(Ctx, {"doc_id": "EUR:speech:z", "url": "https://x/y.htm"})
    assert slot == {"status": "pending", "label": "not summarised", "doc_id": "EUR:speech:z",
                    "reason": "not prose: 14% of the characters are digits: a deck of tables or slides, not prose (marked once, not summarised)"}
    Ctx.summary_no_text = {"EUR:speech:z": {k: v for k, v in rec.items() if k != "kind"}}
    assert P.summary_slot(Ctx, {"doc_id": "EUR:speech:z", "url": "https://x/y.htm"})["label"] == "summary pending"                          # an older mark: no text


def test_the_status_lists_the_kind_of_each_mark(paths):
    from src import cb_datasets as ds
    SS.write_no_text(paths.summaries, {"EUR:speech:a": {"url": "u", "reason": "deck", "kind": "non_prose", "at": "2026-09-21T10:00:00Z"}, "CAD:speech:b": {"url": "v", "reason": "none", "at": "2026-09-20T10:00:00Z"}})
    _title, _heads, _rows, notes = ds._summaries_status_section(paths, TODAY)
    assert any(n.startswith("non_prose EUR:speech:a: deck (marked 2026-09-21, not summarised)") for n in notes)
    assert any(n.startswith("no_text CAD:speech:b: none (marked 2026-09-20, not retried)") for n in notes)


# --- the measurement options ---------------------------------------------------------------------------------------------------------------------------------

def test_the_command_line_takes_the_reasoning_effort_and_a_scratch_run_and_documents_by_comma(paths, monkeypatch):
    seen = {}
    monkeypatch.setattr("src.cb_summarize.run.run_summaries", lambda p, t, **kw: seen.update(kw, summaries=str(p.summaries)) or R.SummariesReport())
    cc.main(["--data-dir", str(paths.dir), "--stage", "summaries", "--summaries-doc", f"{FED_KEY},USD:statement:2026-07-29", "--summaries-doc", "USD:speech:a89335cf0468"])
    assert seen["only"] == {FED_KEY, "USD:statement:2026-07-29", "USD:speech:a89335cf0468"} and seen["cfg"].reasoning_effort == "low" and seen["persist"] is True
    assert seen["summaries"] == str(paths.summaries)
    cc.main(["--data-dir", str(paths.dir), "--stage", "summaries", "--summaries-effort", "medium", "--summaries-scratch", "--summaries-doc", FED_KEY])
    assert seen["cfg"].reasoning_effort == "medium" and seen["persist"] is False and seen["state"] == {}
    assert seen["summaries"] != str(paths.summaries) and "cb_scratch_" in seen["summaries"]                                                     # a scratch directory, not data/cb/summaries


def test_a_scratch_run_writes_no_summary_and_leaves_state_json_alone(paths, monkeypatch, tmp_path):
    state_before = (paths.dir / "state.json").read_bytes() if (paths.dir / "state.json").exists() else None
    monkeypatch.setattr("src.cb_summarize.run.make_client", lambda cfg, key: RecordedClient([dumps(GOOD_FED)]))
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setattr("src.cb_summarize.run.Fetcher", lambda *a, **k: fetcher(FakeSession()))
    cc.main(["--data-dir", str(paths.dir), "--stage", "summaries", "--summaries-scratch", "--summaries-doc", FED_KEY])
    assert not paths.summaries.exists() or SS.load(paths.summaries) == {}                                                                       # nothing written under data/cb/summaries
    after = (paths.dir / "state.json").read_bytes() if (paths.dir / "state.json").exists() else None
    assert after == state_before                                                                                                                # ... and no state.json


def test_the_effort_reaches_the_request_of_the_model(paths):
    from src.cb_summarize import client as CL
    cfg = dataclasses.replace(CFG, reasoning_effort="medium")
    body, _h = CL.OpenAIClient(cfg, "k", session=object()).build("system", [{"role": "user", "content": "x"}], {"type": "object"})
    assert body["reasoning"] == {"effort": "medium"}
    body, _h = CL.OpenAIClient(CFG, "k", session=object()).build("system", [{"role": "user", "content": "x"}], {"type": "object"})
    assert body["reasoning"] == {"effort": "low"}


# --- the edges (found by mutating the checks) ------------------------------------------------------------------------------------------------------------------

def test_a_clause_of_the_point_is_compared_only_when_it_shares_two_content_words_or_all_of_them():
    rx = V.negation_regex(CFG.negations)
    cited = ["Growth is not bright across the sectors of the economy."]
    assert V.negation_error("The weather is bright and sunny.", cited, rx, set(), 1) is None                    # one word in common of three: nothing to compare
    assert V.negation_error("Growth", cited, rx, set(), 1) is not None                                          # a clause of one word: that one word is enough
    assert V.negation_error("Growth is bright.", cited, rx, set(), 1) is not None                               # two words in common: compared (the source says "not bright")


def test_a_clause_is_also_cut_at_which_without_a_comma_and_is_trimmed():
    assert V.clauses_of("Rates which fell rose") == ["Rates", "fell rose"]
    assert V.clauses_of("A rose ; B fell") == ["A rose", "B fell"]
    assert V.clauses_of("  Alone  ") == ["Alone"]


def test_a_point_whose_evidence_check_gave_a_reason_without_its_number_is_still_left_out(monkeypatch):
    real = V.check_evidence

    def generic(point, i, *a, **kw):
        return (None, ["something is wrong with this point"]) if i == 2 else real(point, i, *a, **kw)                  # a reason that does not say "summary point N"
    monkeypatch.setattr(V, "check_evidence", generic)
    r = check(P_VOTE, P_RATE, P_ACT, P_RESERVES)
    assert not r.ok and [p["text"] for p in r.points] == [P_VOTE["text"], P_ACT["text"], P_RESERVES["text"]]
    assert [d["text"] for d in r.dropped_points] == [P_RATE["text"]] and r.dropped_points[0]["errors"] == []              # left out, though no reason names it


def test_the_numbers_of_a_point_that_is_left_out_are_not_kept():
    other = point("The Committee decided to raise the target range by 1/4 percentage point to bonds and mortgage securities.", [2], P_RATE["evidence"]["fragments"][0])
    r = check(P_VOTE, P_RATE, P_ACT, other)
    assert not r.ok and [d["text"] for d in r.dropped_points] == [other["text"]]
    assert [n["text"] for n in r.numbers].count("1/4 percentage point") == 1 and all(1 <= n["point"] <= 3 for n in r.numbers)      # only P_RATE's own number


def test_the_summary_shape_error_is_fatal_on_its_own():
    r = V.verify({"summary": "text", "quotes": GOOD_QUOTES, "coverage": [1]}, PARAS, **rules())
    assert r.fatal == ["'summary' must be a list of points, each {'text': '...', 'evidence': {'paragraphs': [...], 'fragments': ['...']}}"]


def test_the_action_of_a_decision_is_a_word_not_an_adverb_that_contains_one():
    rate, act = V.decision_regex(DEC["rate_terms"]), V.forms_of_text(" ".join(DEC["action_terms"]), adverbs=False)
    assert not V.states_decision("The interest rate outlook looks decidedly uncertain.", rate, act)
    assert V.states_decision("The interest rate was decided by the Council.", rate, act)


def test_a_fragment_needs_as_many_words_in_common_as_the_point_has_apart_from_the_names_that_are_always_allowed():
    paras = ["The board raised borrowing costs today after a long meeting of members."]
    rx = V.blocked_regex(CFG.allowed_names)
    p = point("The Monetary Policy Committee raised.", [1], "The board raised borrowing costs today after a long")
    ev, errs = V.check_evidence(p, 1, paras, [0], (1, 3), (1, 3), (5, 40), 0.85, set(), shared=2, allowed_rx=rx)
    assert errs == [] and ev["fragments"]                                                                                # one word of its own ("raised"), and it is in the fragment
    ev, errs = V.check_evidence(p, 1, paras, [0], (1, 3), (1, 3), (5, 40), 0.85, set(), shared=2)
    assert errs                                                                                                          # (without the phrase removed, "monetary policy" would be claims that are not there)


def test_the_names_that_are_always_allowed_reach_the_check_of_a_run():
    paras = ["Members reported that growth is strong across the sectors of the economy and that prices keep rising in the services sector this quarter.",
             "Members discussed the outlook for growth and inflation over the coming year and the risks around it, in some detail.",
             "The rate was left unchanged after members weighed the outlook for growth, inflation and the exchange rate."]
    src = SRC.from_paragraphs(paras, "html-selector", 10**6, min_chars=1)
    pts = [point("The Monetary Policy Committee reported that growth is strong across the sectors of the economy.", [1], "Members reported that growth is strong across the sectors of the economy"),
           point("Members discussed the outlook for growth and inflation over the coming year.", [2], "Members discussed the outlook for growth and inflation over the coming year"),
           point("The rate was left unchanged after members weighed the outlook for growth.", [3], "The rate was left unchanged after members weighed the outlook for growth")]
    o = dumps({"summary": pts, "quotes": [{"paragraph": 2, "text": "the outlook for growth and inflation"}], "coverage": [1, 2, 3]})
    doc = {"doc_id": "x", "currency": "GBP", "type": "minutes", "url": "https://x", "speaker": ""}
    ok, usage, errs = R.summarise(RecordedClient([o]), CFG, PR.load("minutes"), doc, src)
    assert ok is not None and ok.ok, errs                                                                                # the bank's own body needs no paragraph to name it
    ok, usage, errs = R.summarise(RecordedClient([o, o]), dataclasses.replace(CFG, allowed_names=()), PR.load("minutes"), doc, src)
    assert ok is None and any("names 'Monetary', 'Policy'" in e or "names 'Monetary'" in e for e in errs)


def test_the_message_names_the_speaker_of_a_speech_only_and_never_a_page_code():
    src = SRC.from_paragraphs(PARAS, "stored", 10**6, min_chars=1)
    sent = []

    def ask(doc, kind):
        c = RecordedClient([dumps(GOOD_FED)])
        try:
            R.summarise(c, CFG, PR.load(kind), doc, src)
        except Exception:
            pass
        sent.append(c.calls[0][1][0]["content"])
        return sent[-1]
    speech = {"doc_id": "x", "currency": "GBP", "type": "speech", "url": "https://x", "speaker": "Andrew Bailey", "role": "Governor"}
    assert "\nSpeaker: Governor Andrew Bailey\n" in ask(speech, "speech")
    assert "\nSpeaker: " not in ask(dict(speech, speaker="sp-gov", role=""), "speech")                                # a page code is not a name
    assert "\nSpeaker: " not in ask(dict(speech, speaker="mc-gov", role="Governor"), "speech")
    assert "\nSpeaker: " not in ask(dict(speech, type="statement"), "statement")                                       # only a speech or a testimony has a speaker
    assert "\nSpeaker: Chair Kevin Warsh\n" in ask(dict(speech, type="testimony", speaker="Kevin Warsh", role="Chair"), "speech")
    assert "\nSpeaker: Kevin Warsh\n" in ask(dict(speech, speaker="Kevin Warsh", role=""), "speech")


def test_the_better_of_two_attempts_is_published_even_when_it_is_the_second(paths):
    three = out(*GOOD_FED["summary"], BAD_CLAIM)                                                                         # 3 valid, 1 bad
    four = out(*GOOD_FED["summary"], P_RESERVES, BAD_CLAIM)                                                              # 4 valid, 1 bad
    rep = fed_only(paths, RecordedClient([three, four]))
    rec = SS.load(paths.summaries)[FED_KEY]
    assert rep.new == 1 and len(rec["summary"]) == 4


def test_the_minimum_of_valid_points_is_the_configs(paths):
    bad = out(*GOOD_FED["summary"], BAD_CLAIM)
    rep = fed_only(paths, RecordedClient([bad, bad]), cfg=dataclasses.replace(CFG, publish_min_points=4))
    assert rep.new == 0 and [d for d, _ in rep.failed_validation] == [FED_KEY]                                           # 3 valid points, 4 asked


def test_the_outcome_of_a_document_that_passed_at_the_first_attempt_is_full(paths):
    rep = fed_only(paths, RecordedClient([dumps(GOOD_FED)]))
    assert [x["outcome"] for x in rep.docs] == ["full"] and rep.docs[0]["attempts"] == 1


def test_the_outcome_full_after_a_retry(paths):
    bad = out(*GOOD_FED["summary"], BAD_CLAIM)
    good = out(*GOOD_FED["summary"], P_RESERVES)
    rep = fed_only(paths, RecordedClient([bad, good]))
    assert [x["outcome"] for x in rep.docs] == ["full after retry"] and rep.docs[0]["attempts"] == 2 and rep.docs[0]["points"] == 4 and rep.docs[0]["dropped"] == 0


def test_what_is_and_is_not_a_paragraph_of_full_sentences():
    words = lambda n, end=".": " ".join(["abcdefgh"] * n) + end                                                       # noqa: E731
    assert SRC._is_prose(words(12)) and not SRC._is_prose(words(11)) and not SRC._is_prose(words(12, ""))               # 12 words at least, and a sentence that ends
    assert not SRC._is_prose(" ".join(["a1b2c3d4"] * 12) + ".")                                                          # mostly digits: not sentences
    assert not SRC._is_prose(" ".join(["12345678"] * 12) + ".")
    assert not SRC._is_prose(" ".join(["abcdefgh"] + ["12345678"] * 11) + ".")                                            # a word, then numbers: 12 words and a full stop, but not sentences


def test_a_share_of_digits_exactly_at_the_limit_is_still_prose():
    p = " ".join(["abcdefgh"] * 11 + ["abc12345678."])                                                                  # 100 characters without the spaces, 8 of them digits
    m = SRC.prose_metrics([p])
    assert m["digit_ratio"] == 0.08 and m["prose_share"] == 1.0
    assert SRC.not_prose([p], {"prose_share_min": 0.5, "digit_ratio_max": 0.08, "short_share_max": 0.5}) is None
    assert SRC.not_prose([p], {"prose_share_min": 0.5, "digit_ratio_max": 0.0799, "short_share_max": 0.5}) is not None
