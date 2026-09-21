"""Phase 2b: the automatic check that stands between a model output and the store. Offline, on the real Fed statement of 16 Sep 2026 and small
constructed sources for what a real statement does not contain (curly quotes, accents, separators)."""
from __future__ import annotations

from fractions import Fraction

import pytest

from src.cb_summarize import verify as V
from src.cb_summarize.config import load as load_cfg

from .cb_docs_helpers import statement_paras
from .cb_sum_helpers import GOOD_FED, GOOD_TEXTS, variant, wrap_points

CFG = load_cfg()
PARAS = statement_paras("USD", "2026-09-16")


def rules(**over):
    """The verifier's rules as the config sets them; a test overrides what it is about."""
    r = dict(points=CFG.summary_points, point_chars=CFG.point_chars, quotes=CFG.quotes, quote_chars=CFG.quote_chars, total_chars=CFG.summary_total_chars,
             blocked=CFG.blocked_words, attributed=CFG.attributed_words, subjects=CFG.attribution_subjects, evidence_paragraphs=CFG.evidence_paragraphs,
             fragments=CFG.fragments, fragment_words=CFG.fragment_words, fragment_shared=CFG.fragment_shared, min_support=CFG.min_support,
             attribution_words=CFG.attribution_words, pronouns=CFG.attribution_pronouns, negations=CFG.negations, speaker_verbs=CFG.speaker_verbs)
    r.update(over)
    return r


def check(obj, paras=None, speakers=()):
    """Verify an output; a summary given as plain strings gets automatic evidence (grounding has its own tests below)."""
    paras = paras or PARAS
    obj = dict(obj)
    if isinstance(obj.get("summary"), list):
        obj["summary"] = wrap_points(obj["summary"], paras)
    return V.verify(obj, paras, speakers=speakers, **rules())


def with_summary(*points):
    return variant(summary=list(points))


# --- the good output -------------------------------------------------------------------------------------------------------------------

def test_a_good_output_passes_and_its_offsets_slice_back_to_the_source():
    r = check(GOOD_FED)
    assert r.ok and r.errors == [] and len(r.summary) == 3 and r.coverage == [1, 2, 3, 4]
    src = V.source_text(PARAS)
    for q in r.quotes:
        assert src[q["start"]:q["end"]] == q["text"] and PARAS[q["paragraph"] - 1].find(q["text"]) >= 0
    assert r.quotes[0]["paragraph"] == 2 and r.quotes[0]["start"] == len(PARAS[0]) + 1                                # the start of paragraph 2 in the joined text
    assert {(n["point"], n["text"]) for n in r.numbers} >= {(1, "12"), (2, "1/4 percentage point"), (2, "3-3/4"), (2, "4 percent")}
    for n in r.numbers:
        assert src[n["start"]:n["end"]].strip() == n["source_text"]
    assert V.verify_stored({"quotes": r.quotes, "numbers": r.numbers}, PARAS) == []


# --- quotes: verbatim, in the paragraph named -----------------------------------------------------------------------------------------

def test_an_invented_quote_is_refused():
    bad = variant(quotes=[{"paragraph": 2, "text": "The Committee decided to lower the target range for the federal funds rate"}])
    r = check(bad)
    assert not r.ok and any("quote 1 is not verbatim in paragraph 2" in e and "not in the document verbatim" in e for e in r.errors)


def test_a_quote_in_another_paragraph_than_the_one_named_is_refused_and_says_where_it_is():
    r = check(variant(quotes=[{"paragraph": 3, "text": "Inflation remains elevated."}]))
    assert not r.ok and "that text is in paragraph 4" in r.errors[0]


@pytest.mark.parametrize("q, why", [
    ({"paragraph": 9, "text": "Inflation remains elevated."}, "paragraphs 1-4"),
    ({"paragraph": 0, "text": "Inflation remains elevated."}, "paragraphs 1-4"),
    ({"paragraph": "4", "text": "Inflation remains elevated."}, "integer 'paragraph'"),
    ({"paragraph": True, "text": "Inflation remains elevated."}, "integer 'paragraph'"),
    ({"paragraph": 4, "text": 5}, "string 'text'"),
    ({"paragraph": 4, "text": "Short"}, "allowed 15-500"),
    ({"paragraph": 3, "text": "Economic activity is expanding at a solid pace.\nWhile"}, "more than one paragraph"),
    ({"text": "Inflation remains elevated."}, "integer 'paragraph'"),
    ("Inflation remains elevated.", "must be an object"),
])
def test_a_malformed_quote_is_refused(q, why):
    r = check(variant(quotes=[q]))
    assert not r.ok and any(why in e for e in r.errors), r.errors


def test_quotes_are_verbatim_exactly_curly_quotes_accents_and_case():
    src = ["The Governing Council’s view is that “inflation” has returned to target.", "Le président Vujčić a déclaré que les taux resteront inchangés jusqu’en 2027."]
    lim = rules(points=(1, 6), quote_chars=(15, 500))
    point = "The document states its view that inflation has returned to target."
    ok = lambda text, para: V.verify({"summary": wrap_points([point], src), "quotes": [{"paragraph": para, "text": text}], "coverage": [1]}, src, **lim)   # noqa: E731
    assert ok("The Governing Council’s view is that “inflation” has returned to target.", 1).ok
    assert ok("Vujčić a déclaré que les taux resteront inchangés jusqu’en 2027", 2).ok                                  # accents and the curly apostrophe are matched as they are
    assert not ok("The Governing Council's view is that “inflation” has returned", 1).ok                                # straight apostrophe: not the document's character
    assert not ok("The Governing Council’s view is that \"inflation\" has returned", 1).ok                             # straight double quotes
    assert not ok("the governing council’s view is that “inflation” has returned", 1).ok                               # case
    assert not ok("Vujcic a déclaré que les taux resteront inchangés jusqu’en 2027", 2).ok                              # accent dropped
    assert not ok("Vujčić a déclaré que les taux resteront inchangés jusqu’en 2027", 2).ok                       # the same letter, decomposed: not the same characters


def test_offsets_are_absolute_in_the_joined_text():
    src = ["First paragraph of the document, long enough.", "Second paragraph with the quoted words inside it."]
    lim = rules(points=(1, 6), quote_chars=(10, 500))
    r = V.verify({"summary": wrap_points(["The document has a second paragraph with quoted words."], src), "quotes": [{"paragraph": 2, "text": "quoted words"}], "coverage": [2]}, src, **lim)
    assert r.ok and r.quotes[0]["start"] == len(src[0]) + 1 + src[1].index("quoted words") and V.source_text(src)[r.quotes[0]["start"]:r.quotes[0]["end"]] == "quoted words"


def test_a_stored_summary_with_a_wrong_offset_is_detected():
    r = check(GOOD_FED)
    q = dict(r.quotes[0], start=r.quotes[0]["start"] + 1, end=r.quotes[0]["end"] + 1)
    n = dict(r.numbers[0], start=r.numbers[0]["start"] + 3, end=r.numbers[0]["end"] + 3)
    errs = V.verify_stored({"quotes": [q], "numbers": [n]}, PARAS)
    assert len(errs) == 2 and "quote 1" in errs[0] and "number 1" in errs[1]


# --- numbers -----------------------------------------------------------------------------------------------------------------------------

def vals(text):
    return [(n.value, n.unit) for n in V.numbers_of(text)]


def test_number_normalisation_separators_percent_basis_points_fractions_signs():
    assert vals("3.75% and 3.75 percent and 3.75 per cent") == [(Fraction(15, 4), "pct")] * 3
    assert vals("25 basis points, 25bp, 25 bp, 25 bps, 25 basis point") == [(Fraction(25), "bp")] * 5
    assert vals("1,234 and 1 234 and 1 234 and 1 234") == [(Fraction(1234), None)] * 4
    assert vals("1/4 percentage point, 0.25 percentage points, ¼ percentage point") == [(Fraction(1, 4), "pp")] * 3
    assert vals("3-3/4 and 3 3/4 and 3¾ and 3.75") == [(Fraction(15, 4), None)] * 4
    assert vals("2¼% and 2.25%") == [(Fraction(9, 4), "pct")] * 2
    assert vals("-0.5, −0.5 and –0.5") == [(Fraction(-1, 2), None)] * 3                                                # hyphen, minus sign, en dash
    assert vals("in 2026 the rate of 4.10") == [(Fraction(2026), None), (Fraction(41, 10), None)]
    assert vals("FOMCpresconf20260916 and Q3 and 007") == []                                                           # glued to letters / an identifier: not quantities
    assert vals("a 12 – 0 vote") == [(Fraction(12), None), (Fraction(0), None)]                                        # a vote count is two numbers, not a negative


def test_a_number_matches_by_value_and_compatible_unit_only():
    src = V.numbers_of("The rate is 25 basis points, 3.75 percent, 1/4 percentage point and 2026.")
    f = lambda t: V.find_number(V.numbers_of(t)[0], src)                                                               # noqa: E731
    assert f("25bp") and f("25 basis points") and f("25")                                                             # unit spelled differently / absent
    assert f("3.75%") and f("3.75") and f("3¾%")
    assert f("0.25 percentage point") and f("¼ percentage point")                                                      # the same value, not converted to basis points
    assert f("0.25 basis points") is None and f("26 basis points") is None and f("3.76%") is None
    assert f("25 percent") is None                                                                                     # 25 is there, but as basis points
    assert f("2026") and f("2027") is None


def test_an_invented_number_is_refused_with_the_point_it_is_in():
    r = check(with_summary(GOOD_TEXTS[0], "The Committee decided to raise the target range to 4.5 percent.", GOOD_TEXTS[2]))
    assert not r.ok and any("'4.5 percent' of summary point 2 does not appear" in e for e in r.errors)


def test_a_converted_number_is_refused():
    r = check(with_summary(GOOD_TEXTS[0], "The Committee raised the target range by 25 basis points to 3-3/4 to 4 percent.", GOOD_TEXTS[2]))
    assert not r.ok and any("'25 basis points'" in e and "never convert" in e for e in r.errors)                      # the document says 1/4 percentage point


def test_the_same_value_in_another_form_is_accepted():
    r = check(with_summary(GOOD_TEXTS[0], "The Committee raised the target range by 0.25 percentage point to 3.75 to 4 percent.", GOOD_TEXTS[2]))
    assert r.ok, r.errors


# --- direction / evaluation vocabulary: refused always; likely / expects / signals / suggests: only the bank's own word, attributed --------------------

@pytest.mark.parametrize("word", list(CFG.blocked_words))
def test_every_always_blocked_word_is_refused_in_a_summary_point(word):
    r = check(with_summary(GOOD_TEXTS[0], GOOD_TEXTS[1], f"The statement was described as {word.upper()} in its tone."))
    assert not r.ok and any("summary point 3" in e and "no direction, forecast or evaluation" in e for e in r.errors)


@pytest.mark.parametrize("word", ["hawkish", "dovish", "bullish", "bearish", "paves the way"])
def test_the_always_blocked_words_stay_refused_even_when_the_document_uses_them_and_the_point_attributes_them(word):
    paras = [f"Some members described the stance as {word} in their discussion of the outlook for the year ahead."]
    r = check({"summary": ["The minutes record the views of members on the stance.", f"The minutes say some members described the stance as {word}.", "The discussion covered the outlook."],
               "quotes": [{"paragraph": 1, "text": f"Some members described the stance as {word}"}], "coverage": [1]}, paras)
    assert not r.ok and any(f"uses the word '{word}'" in e for e in r.errors)


def real_paragraphs(ccy: str, day: str) -> list:
    return statement_paras(ccy, day)


def with_real(paras, *points, quote_of):
    """A valid output around `points` for a real document, quoting the paragraph that holds `quote_of`."""
    i = next(k for k, p in enumerate(paras) if quote_of in p)
    return check({"summary": list(points), "quotes": [{"paragraph": i + 1, "text": quote_of}], "coverage": [i + 1]}, paras)


def test_the_banks_own_word_is_allowed_when_the_point_attributes_it_real_statements():
    snb = real_paragraphs("CHF", "2026-06-18")                                                                         # "the SNB currently expects growth of around 1%"
    r = with_real(snb, "The SNB is leaving the SNB policy rate unchanged at 0%.", "The SNB says it currently expects growth of around 1% for 2026 as a whole.",
                  "The SNB describes global economic growth as likely to be more moderate in the short term.", quote_of="the SNB currently expects growth of around 1%")
    assert r.ok, r.errors
    aud = real_paragraphs("AUD", "2026-08-11")                                                                         # "inflation is likely to remain high for some time"
    r = with_real(aud, "At its meeting today, the Board decided to leave the cash rate target unchanged at 4.35 per cent.", "The Board states that inflation is likely to remain high for some time.",
                  "Today's policy decision was unanimous.", quote_of="inflation is likely to remain high for some time")
    assert r.ok, r.errors
    cad = real_paragraphs("CAD", "2026-06-10")                                                                         # "Recent data suggests that growth will resume"
    r = with_real(cad, "The Bank says recent data suggests that growth will resume in the second quarter.", "The Bank of Canada today held its target for the overnight rate at 2.25%.",
                  "The next scheduled date for announcing the overnight rate target is July 15, 2026.", quote_of="Recent data suggests that growth will resume in the second quarter")
    assert r.ok, r.errors
    boj = real_paragraphs("JPY", "2026-09-18")
    r = with_real(boj, "The statement says Japan's economy is expected to continue growing moderately.", "The Bank will encourage the uncollateralized overnight call rate to remain at around 1.25 percent.",
                  "The Policy Board decided by a 7-2 majority vote to set the guideline.", quote_of="Japan's economy is expected to continue growing moderately")
    assert r.ok, r.errors


def test_the_word_case_does_not_matter_and_neither_does_the_inflection():
    aud = real_paragraphs("AUD", "2026-08-11")
    assert with_real(aud, "The Board decided to leave the cash rate target unchanged at 4.35 per cent.", "The Board says inflation is LIKELY to remain high for some time.",
                     "Today's policy decision was unanimous.", quote_of="inflation is likely to remain high for some time").ok
    cad = real_paragraphs("CAD", "2026-06-10")                                                                         # the document says "suggests"
    for form in ("suggests", "suggested", "suggesting", "suggest"):
        r = with_real(cad, "The Bank held its target for the overnight rate at 2.25%.", f"The Bank {form} that growth will resume in the second quarter.",
                      "The next scheduled date for announcing the overnight rate target is July 15, 2026.", quote_of="Recent data suggests that growth will resume in the second quarter")
        assert r.ok, (form, r.errors)
    snb = real_paragraphs("CHF", "2026-06-18")                                                                         # "As expected", "expected improvement", "currently expects"
    for form in ("expects", "expected", "expecting", "expect"):
        r = with_real(snb, "The SNB is leaving the SNB policy rate unchanged at 0%.", f"The SNB {form} growth of around 1% for 2026 as a whole.",
                      "Unemployment has risen somewhat since the last monetary policy assessment.", quote_of="the SNB currently expects growth of around 1%")
        assert r.ok, (form, r.errors)
    own = with_real(snb, "The SNB is leaving the SNB policy rate unchanged at 0%.", "Wage growth of around 1% for 2026 as a whole is expected.",
                    "Unemployment has risen somewhat since the last monetary policy assessment.", quote_of="the SNB currently expects growth of around 1%")
    assert not own.ok and any("'expected' of summary point 2 is not attributed to the bank or to a named speaker" in e for e in own.errors)   # "expected" is on the list too
    other = with_real(snb, "The SNB is leaving the SNB policy rate unchanged at 0%.", "The SNB signals growth of around 1% for 2026 as a whole.",
                      "Unemployment has risen somewhat since the last monetary policy assessment.", quote_of="the SNB currently expects growth of around 1%")
    assert not other.ok and any("the word 'signals' of summary point 2 is not in the document" in e for e in other.errors)   # a DIFFERENT word is still refused


def test_a_word_family_is_not_a_prefix_likely_is_not_like():
    assert not (V.forms("likely", adverbs=False) & V.forms("like", adverbs=False))                                    # the vocabulary check does not strip "-ly"
    assert V.forms("expects") & V.forms("expected") & V.forms("expecting") & V.forms("expect")
    assert V.forms("signalling") & V.forms("signalled") & V.forms("signals")
    assert V.forms("suggested") & V.forms("suggests")
    assert not (V.forms("expect") & V.forms("expert")) and not (V.forms("signal") & V.forms("sign"))
    doc = ["The Board decided to leave the cash rate unchanged, and it is like the earlier decision on the rate, held for some time in the past."]
    r = check({"summary": ["The Board says the outcome is likely to be the same, as the cash rate is unchanged.", "The Board decided to leave the cash rate unchanged.",
                           "The Board decided on the rate, held for some time."],
               "quotes": [{"paragraph": 1, "text": "The Board decided to leave the cash rate unchanged"}], "coverage": [1]}, doc)
    assert not r.ok and any("the word 'likely' of summary point 1 is not in the document" in e for e in r.errors)      # "like" in the document does not license "likely"


def test_a_soft_word_the_document_does_not_use_is_refused_even_when_attributed():
    r = check(with_summary(GOOD_TEXTS[0], GOOD_TEXTS[1], "The Committee signals a further increase in the target range."))
    assert not r.ok and any("the word 'signals' of summary point 3 is not in the document" in e and "attributed to the bank" in e for e in r.errors)
    r = check(with_summary(GOOD_TEXTS[0], GOOD_TEXTS[1], "The Committee expects inflation to return to 2 percent."))
    assert not r.ok and any("the word 'expects' of summary point 3 is not in the document" in e for e in r.errors)     # the Fed statement of 16 Sep does not say it


def test_a_soft_word_in_the_summarys_own_voice_is_refused_even_when_the_document_uses_it():
    aud = real_paragraphs("AUD", "2026-08-11")
    for own_voice in ("Wages are likely to remain high for some time.",
                      "The Board held the cash rate. Wages are likely to remain high for some time.",                   # the subject is in ANOTHER sentence
                      "The Board held the cash rate; wages are likely to remain high for some time.",                   # ... or before a semicolon
                      "Growth data suggests that wages are likely to remain high."):
        r = with_real(aud, "The Board decided on the cash rate.", own_voice, "The Board describes inflation.", quote_of="inflation is likely to remain high for some time")
        assert not r.ok and any("is not attributed to the bank" in e and "never in your own voice" in e for e in r.errors), own_voice


def test_the_attribution_must_come_before_the_word_in_the_same_sentence():
    aud = real_paragraphs("AUD", "2026-08-11")
    ok = with_real(aud, "At its meeting today, the Board decided to leave the cash rate target unchanged.", "The Board states that, with the pass-through of higher fuel prices, inflation is likely to remain high.",
                   "Today's policy decision was unanimous.", quote_of="inflation is likely to remain high for some time")
    assert ok.ok, ok.errors                                                                                            # the subject may be several words before
    late = with_real(aud, "At its meeting today, the Board decided to leave the cash rate target unchanged.", "Wages are likely to remain high, the Board says.",
                     "Today's policy decision was unanimous.", quote_of="inflation is likely to remain high for some time")
    assert not late.ok and any("is not attributed" in e for e in late.errors)                                          # "the Board" comes AFTER the word


def test_one_error_per_offending_word_and_the_feedback_names_the_point():
    aud = real_paragraphs("AUD", "2026-08-11")
    r = with_real(aud, "Wages are likely to remain high for some time.", "It suggests policy is on hold, and the cash rate is unchanged.", "Today's policy decision was unanimous.",
                  quote_of="inflation is likely to remain high for some time")
    words = [e for e in r.errors if "the word" in e]
    assert len(words) == 2 and "'likely' of summary point 1" in words[0] and "'suggests' of summary point 2" in words[1] and "not in the document" in words[1]


def test_blocked_words_are_whole_words_and_a_quote_may_carry_the_banks_own_words():
    paras = ["The Committee states that the unlikely case was discussed, and the assignment was signed by the members present."]
    r = check({"summary": ["The Committee states that the unlikely case was discussed.", "The assignment was signed by the members present.", "The Committee states the case was discussed."],
               "quotes": [{"paragraph": 1, "text": "the assignment was signed by the members present"}], "coverage": [1]}, paras)
    assert r.ok, r.errors                                                                                                # "unlikely", "assignment", "signed": not vocabulary words
    paras = ["Inflation is likely to return to the Committee's 2 percent goal over the medium term, and the Committee expects to keep policy restrictive."]
    q = {"summary": ["The Committee's 2 percent goal for inflation is over the medium term.", "The Committee keeps policy restrictive.", "The Committee goal is 2 percent inflation."],
         "quotes": [{"paragraph": 1, "text": "Inflation is likely to return to the Committee's 2 percent goal"}], "coverage": [1]}
    assert check(q, paras).ok                                                                                            # 'likely' inside a QUOTE is the bank's word: allowed


# --- grounding: every point cites paragraphs and a verbatim fragment, and the paragraphs must SAY what the point says -----------------------------------

def point(text, paragraphs, *fragments):
    return {"text": text, "evidence": {"paragraphs": paragraphs, "fragments": list(fragments)}}


def grounded(*pts, paras=None, speakers=(), **over):
    """GOOD_FED with these points (dicts, evidence as given) - the quotes and the coverage stay valid."""
    paras = paras or PARAS
    return V.verify(dict(GOOD_FED, summary=list(pts)), paras, speakers=speakers, **rules(**over))


P_VOTE = point(GOOD_TEXTS[0], [1], "approved the following statement for release by a 12 \u2013 0 vote:")
P_RATE = point(GOOD_TEXTS[1], [2], "raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent")
P_ACT = point("The Committee says economic activity is expanding at a solid pace.", [3], "Economic activity is expanding at a solid pace.")


def test_a_faithful_paraphrase_is_accepted_with_its_evidence_recorded():
    para = point("The Committee says domestic spending has been resilient and economic activity is expanding at a solid pace.", [3],
                 "geopolitical developments, domestic spending has been resilient.")
    r = grounded(P_VOTE, P_RATE, para)
    assert r.ok, r.errors
    ev = r.points[2]["evidence"]
    assert ev["paragraphs"] == [3] and ev["fragments"][0]["paragraph"] == 3 and ev["coverage"] == 1.0
    src = V.source_text(PARAS)
    f = ev["fragments"][0]
    assert src[f["start"]:f["end"]] == f["text"] == "geopolitical developments, domestic spending has been resilient."
    assert r.summary[2] == para["text"] and V.verify_stored({"summary": r.points, "quotes": r.quotes, "numbers": r.numbers}, PARAS) == []


def test_an_unsupported_claim_without_numbers_or_forbidden_words_is_refused():
    claim = point("The Committee will begin buying government bonds and mortgage securities at a faster pace.", [2], P_RATE["evidence"]["fragments"][0])   # a real fragment, a false point
    r = grounded(P_VOTE, P_RATE, claim)
    assert not r.ok and len(r.errors) == 2
    e = next(x for x in r.errors if "is not supported" in x)
    assert "summary point 3 is not supported by the paragraph(s) it cites [2]" in e and "not found:" in e and "'bonds'" in e and "'mortgage'" in e
    assert any("evidence fragment 1 of summary point 3 has 0 content word(s) in common with the point" in x for x in r.errors)                # and the fragment is filler
    stated = point("The Committee will begin buying government bonds at a faster pace and raise the federal funds rate.", [2], "raise the target range for the federal funds rate by 1/4 percentage point")
    r = grounded(P_VOTE, P_RATE, stated)                                                                                 # the fragment fits, the point still claims more than the paragraph says
    assert not r.ok and len(r.errors) == 1 and "is not supported" in r.errors[0]


def test_the_evidence_fragment_must_be_verbatim_in_a_cited_paragraph():
    good = P_RATE["evidence"]["fragments"][0]
    for altered in (good.replace("percent", "per cent"), good.replace("raise", "lower"), good.replace("target range", "target  range"), good.replace("raise", "Raise"),
                    good.replace("federal funds rate by 1/4", "federal funds rate by one quarter of a")):
        r = grounded(P_VOTE, point(GOOD_TEXTS[1], [2], altered), P_ACT)
        assert not r.ok and any("evidence fragment 1 of summary point 2 is not verbatim in the paragraph(s) it cites [2]" in e and "character for character" in e for e in r.errors), altered


def test_a_fragment_from_an_uncited_paragraph_is_refused_and_says_which_paragraph_holds_it():
    r = grounded(P_VOTE, point(GOOD_TEXTS[1], [2], "Economic activity is expanding at a solid pace."), P_ACT)
    assert not r.ok and any("point 2 is not verbatim in the paragraph(s) it cites [2] (that text is in paragraph 3: cite it)" in e for e in r.errors)
    two = point("Economic activity is expanding at a solid pace and inflation remains elevated.", [3, 4], "Economic activity is expanding at a solid pace.")
    assert grounded(P_VOTE, P_RATE, two).ok                                                                            # cited: the fragment may be in any of them
    second = grounded(P_VOTE, P_RATE, point("Today's policy action will support a timelier return to the 2 percent goal.", [3, 4], "Today's policy action will support a timelier return"))
    assert second.ok and second.points[2]["evidence"]["fragments"][0]["paragraph"] == 4 and second.points[2]["evidence"]["paragraphs"] == [3, 4]     # the paragraph that holds it, not the first cited


def test_a_fragment_may_not_run_across_two_paragraphs():
    across = "of the workforce, and the unemployment rate has changed little.\nInflation remains elevated. Today's policy"
    r = grounded(P_VOTE, P_RATE, point("Inflation remains elevated, and the unemployment rate has changed little.", [3, 4], across))
    assert not r.ok and any("not verbatim" in e for e in r.errors)


def test_the_points_words_may_come_from_any_of_the_cited_paragraphs_but_only_from_those():
    both = "The Committee says economic activity is expanding at a solid pace, and today's policy action will support a timelier return to the 2 percent goal."
    assert grounded(P_VOTE, P_RATE, point(both, [3, 4], "Economic activity is expanding at a solid pace.")).ok
    r = grounded(P_VOTE, P_RATE, point(both, [3], "Economic activity is expanding at a solid pace."))
    assert not r.ok and any("not supported by the paragraph(s) it cites [3]" in e and "'timelier'" in e and "'goal'" in e and "'return'" in e for e in r.errors)


def test_the_support_threshold_is_the_share_of_content_words_found_in_the_cited_paragraphs():
    def share(text, cited):
        return V.support(text, [PARAS[c - 1] for c in cited], DROP)
    DROP = V.forms_of_text(" ".join(CFG.attribution_words + CFG.attribution_subjects))
    assert share("Economic activity is expanding at a solid pace.", [3]) == (1.0, [])
    s, missing = share("Economic activity is expanding at a solid pace, and bonds are cheap.", [3])
    assert missing == ["bonds", "cheap"] and abs(s - 5 / 7) < 1e-9                                                     # economic, activity, expanding, solid, pace, bonds, cheap: 2 of 7 unsupported
    seven = "Economic activity expanding solid pace resilient spending"                                               # 7 words, all in paragraph 3
    assert grounded(P_VOTE, P_RATE, point(seven, [3], "Economic activity is expanding at a solid pace.")).ok
    one_out = point(seven.replace("spending", "bonds"), [3], "Economic activity is expanding at a solid pace.")       # 6 of 7 = 85.7%: enough
    assert grounded(P_VOTE, P_RATE, one_out).ok
    two_out = point(seven.replace("spending", "bonds").replace("resilient", "mortgages"), [3], "Economic activity is expanding at a solid pace.")   # 5 of 7 = 71%
    r = grounded(P_VOTE, P_RATE, two_out)
    assert not r.ok and any("71% of its content words are there (85% needed)" in e for e in r.errors)
    assert grounded(P_VOTE, P_RATE, two_out, min_support=0.7).ok                                                        # the threshold is the config's


def test_stopwords_forms_and_attribution_vocabulary_are_not_claims():
    # "The Committee said that the activity was expanding." : the, said, that, was = not claims; the Committee = the bank; activity / expanding are in the paragraph in another form
    r = grounded(P_VOTE, P_RATE, point("The Committee noted that economic activities were expanded at a solid pace.", [3], "Economic activity is expanding at a solid pace."))
    assert r.ok, r.errors
    words = V.content_words("The Committee said the Governor noted it was a solid pace.", V.forms_of_text(" ".join(CFG.attribution_words + CFG.attribution_subjects)))
    assert words == ["solid", "pace"]


def test_the_word_forms_the_suffix_rules_and_their_limits():
    same = lambda a, b: bool(V.forms(a) & V.forms(b))                                                                  # noqa: E731
    assert same("policies", "policy") and same("studied", "study") and same("crosses", "cross") and same("raised", "raise") and same("raising", "raise")
    assert same("making", "make") and same("stopped", "stop") and same("signalling", "signal") and same("expected", "expects")
    assert not same("loss", "los") and not same("bed", "b") and not same("agree", "agreed agreement") and not same("rise", "rises fall")
    assert "a" not in V.forms("as") and all(len(f) >= 3 or f == "as" for f in V.forms("as"))                          # a stem shorter than 3 letters is not a stem
    assert V.forms("Expects") == V.forms("expects") and V.forms("likely") >= {"likely", "like"} and not (V.forms("likely", adverbs=False) - {"likely"})


def test_content_words_skip_short_words_stopwords_attribution_and_names():
    drop = V.forms_of_text(" ".join(CFG.attribution_words + CFG.attribution_subjects) + " waller")
    assert V.content_words("The UK and Waller said the Board is watching AI demand.", drop) == ["watching", "demand"]     # uk / ai: two letters; Waller / said / Board: attribution
    assert V.content_words("The G20 and COVID19 support, 2026 and 1,234.", drop) == ["covid", "support"]                # numbers are checked as numbers, not as words


def test_a_point_with_no_content_word_is_supported_and_repeated_words_count_once():
    drop = V.forms_of_text(" ".join(CFG.attribution_words + CFG.attribution_subjects))
    assert V.support("The Committee said that it is so.", [PARAS[0]], drop) == (1.0, [])
    text = "Economic activity expanding solid pace resilient bonds bonds bonds"                                         # 7 distinct words, one of them missing, said three times
    share, missing = V.support(text, [PARAS[2]], drop)
    assert missing == ["bonds"] and abs(share - 6 / 7) < 1e-9
    assert grounded(P_VOTE, P_RATE, point(text, [3], "Economic activity is expanding at a solid pace.")).ok


def test_the_threshold_is_inclusive_exactly_at_the_share():
    paras = [" ".join(["alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango uniform victor"])]
    good = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec"                 # 17 words
    obj = lambda extra: {"summary": [point(f"{good} {extra}", [1], "alpha bravo charlie delta echo foxtrot golf hotel"), point("alpha bravo charlie delta echo foxtrot.", [1], "alpha bravo charlie delta echo foxtrot golf"),
                                     point("golf hotel india juliet kilo lima.", [1], "golf hotel india juliet kilo lima mike")],
                         "quotes": [{"paragraph": 1, "text": "alpha bravo charlie delta echo foxtrot"}], "coverage": [1]}                  # noqa: E731
    assert V.verify(obj("zulu yankee whiskey"), paras, **rules(point_chars=(5, 600))).ok                               # 17 of 20 = 85%: enough
    r = V.verify(obj("zulu yankee whiskey xray"), paras, **rules(point_chars=(5, 600)))                                # 17 of 21 = 81%
    assert not r.ok and any("81% of its content words are there (85% needed)" in e for e in r.errors)


@pytest.mark.parametrize("ev, why", [
    ({"paragraphs": [], "fragments": ["Economic activity is expanding at a solid pace."]}, "'evidence'"),
    ({"paragraphs": [1, 2, 3, 4], "fragments": ["Economic activity is expanding at a solid pace."]}, "cites 4 paragraphs as evidence; allowed 1-3"),
    ({"paragraphs": [9], "fragments": ["Economic activity is expanding at a solid pace."]}, "outside 1-4"),
    ({"paragraphs": [0], "fragments": ["Economic activity is expanding at a solid pace."]}, "outside 1-4"),
    ({"paragraphs": ["3"], "fragments": ["Economic activity is expanding at a solid pace."]}, "'evidence'"),
    ({"paragraphs": [True], "fragments": ["Economic activity is expanding at a solid pace."]}, "'evidence'"),
    ({"paragraphs": 3, "fragments": ["Economic activity is expanding at a solid pace."]}, "'evidence'"),
    ({"paragraphs": [3], "fragments": [5]}, "'evidence'"),
    ({"paragraphs": [3], "fragments": "Economic activity is expanding at a solid pace."}, "'evidence'"),                                                                  # a string, not a list
    ({"paragraphs": [3], "fragment": "Economic activity is expanding at a solid pace."}, "'evidence'"),                                                                   # the single-fragment shape of prompt v4
    ({"paragraphs": [3]}, "'evidence'"),
    ({"fragments": ["Economic activity is expanding at a solid pace."]}, "'evidence'"),
    ({"paragraphs": [3], "fragments": []}, "has 0 evidence fragments; allowed 1-3"),
    ({"paragraphs": [3, 4], "fragments": ["Economic activity is expanding at a solid pace.", "Inflation remains elevated. Today's policy action will support", "Today's policy action will support a timelier return", "The Committee will deliver price stability."]},
     "has 4 evidence fragments; allowed 1-3"),
    ({"paragraphs": [3], "fragments": ["Economic activity is expanding"]}, "has 4 words; allowed 5-40"),
    ({"paragraphs": [3], "fragments": [""]}, "has 0 words; allowed 5-40"),
    (None, "'evidence'"),
    ("Economic activity is expanding at a solid pace.", "'evidence'"),
])
def test_the_evidence_shape_and_limits(ev, why):
    r = grounded(P_VOTE, P_RATE, {"text": P_ACT["text"], "evidence": ev})
    assert not r.ok and any(why in e and "summary point 3" in e for e in r.errors), r.errors


def test_the_evidence_limits_are_inclusive_and_repeated_paragraphs_count_once():
    three = point("The Committee says economic activity is expanding at a solid pace and inflation remains elevated.", [3, 4, 3, 4, 3], "Economic activity is expanding at a solid pace.")
    r = grounded(P_VOTE, P_RATE, three)
    assert r.ok and r.points[2]["evidence"]["paragraphs"] == [3, 4]
    p3 = point(P_ACT["text"], [1, 3, 4], "Economic activity is expanding at a solid pace.")
    assert grounded(P_VOTE, P_RATE, p3).ok                                                                             # 3 paragraphs
    words40 = " ".join(PARAS[2].split()[:40])
    words41 = " ".join(PARAS[2].split()[:41])
    ok40 = grounded(P_VOTE, P_RATE, point("Economic activity is expanding at a solid pace.", [3], words40))
    assert ok40.ok, ok40.errors
    assert not grounded(P_VOTE, P_RATE, point("Economic activity is expanding at a solid pace.", [3], words41)).ok
    assert grounded(P_VOTE, P_RATE, point("Economic activity is expanding at a solid pace.", [3], "Economic activity is expanding at")).ok   # 5 words


def test_every_point_is_checked_and_each_error_names_its_point():
    bad_a = point("The Committee will cut the discount window rate sharply for the federal funds market.", [2], "raise the target range for the federal funds rate by 1/4 percentage point")
    bad_b = point(P_ACT["text"], [3], "Economic activity is expanding at a fast pace overall")
    r = grounded(bad_a, P_RATE, bad_b)
    assert not r.ok and any("summary point 1 is not supported" in e for e in r.errors) and any("evidence fragment 1 of summary point 3" in e for e in r.errors)
    assert not any("point 2" in e for e in r.errors)


def test_a_number_of_a_point_must_be_in_the_paragraphs_it_cites_not_only_in_the_document():
    right = point("The Committee decided to raise the target range to 3-3/4 to 4 percent.", [2], P_RATE["evidence"]["fragments"][0])
    assert grounded(P_VOTE, right, P_ACT).ok
    wrong = point("Today's policy action will support a timelier return to the Committee's 4 percent goal.", [4], "Today's policy action will support a timelier return")   # the 4 percent is in paragraph 2
    r = grounded(P_VOTE, wrong, P_ACT)
    assert not r.ok and any("the number '4 percent' of summary point 2 is in the document but not in the paragraph(s) it cites [4]: cite the paragraph that states it" in e for e in r.errors)
    assert not any("does not appear in the document" in e for e in r.errors)                                             # one message per number: the document does have it
    invented = point("The Committee decided to raise the target range to 4.5 percent.", [2], P_RATE["evidence"]["fragments"][0])
    r = grounded(P_VOTE, invented, P_ACT)
    assert not r.ok and any("'4.5 percent' of summary point 2 does not appear in the document" in e for e in r.errors) and not any("but not in the paragraph" in e for e in r.errors)
    both = point("The Committee approved the statement by a 12 – 0 vote and decided to raise the target range to 4 percent.", [1, 2], "approved the following statement for release by a 12 \u2013 0 vote:")
    assert grounded(both, P_RATE, P_ACT).ok                                                                              # numbers from any of the cited paragraphs


def test_a_stored_evidence_offset_that_no_longer_matches_is_detected():
    r = grounded(P_VOTE, P_RATE, P_ACT)
    assert r.ok
    pts = [dict(p, evidence=dict(p["evidence"], fragments=[dict(f) for f in p["evidence"]["fragments"]])) for p in r.points]
    pts[1]["evidence"]["fragments"][0]["start"] += 2
    errs = V.verify_stored({"summary": pts}, PARAS)
    assert len(errs) == 1 and "point 2" in errs[0] and "evidence fragment" in errs[0]
    assert V.verify_stored({"summary": ["a legacy plain-string point"]}, PARAS) == []                                  # v3 records have none
    legacy = {"paragraphs": [2], "paragraph": 2, "fragment": P_RATE["evidence"]["fragments"][0], "start": r.points[1]["evidence"]["fragments"][0]["start"],
              "end": r.points[1]["evidence"]["fragments"][0]["end"]}
    assert V.verify_stored({"summary": [{"text": "x", "evidence": legacy}]}, PARAS) == []                              # a v4 record: one fragment, its offsets in the evidence itself
    assert len(V.verify_stored({"summary": [{"text": "x", "evidence": dict(legacy, start=legacy["start"] + 1)}]}, PARAS)) == 1


# --- vocabulary: a named speaker of the document may be the attribution; hawkish & co stay refused ----------------------------------------------------

SPEECH = ["Good morning. I am pleased to be here to talk about the economy and the outlook for monetary policy in the coming year.",
          "I expect inflation to move down as the labor market cools, and I see the risks to employment as more important than a month ago.",
          "Inflation is likely to remain above the 2 percent goal for some time, and I support a cautious approach to the next steps in policy."]


def speech_check(*points, speakers=("waller",), paras=SPEECH):
    obj = {"summary": wrap_points(list(points), paras), "quotes": [{"paragraph": 2, "text": "I expect inflation to move down as the labor market cools"}], "coverage": [1, 2, 3]}
    return V.verify(obj, paras, speakers=speakers, **rules())


def test_a_named_speaker_may_be_the_attribution_with_or_without_a_title():
    base = ["Waller talks about the economy and the outlook for monetary policy.", "Waller supports a cautious approach to the next steps in policy."]
    for who in ("Waller", "Governor Waller", "Chair Waller", "waller", "Governor Waller's view is that he"):
        r = speech_check(*base, f"{who} expects inflation to move down as the labor market cools.")
        assert r.ok, (who, r.errors)
    r = speech_check(*base, "Waller says he expects inflation to move down as the labor market cools, and that inflation is likely to remain above the 2 percent goal for some time.")
    assert r.ok, r.errors
    r = speech_check(*base, "Chair Warsh said the labor market cools.", speakers=("warsh", "waller"))
    assert r.ok, r.errors


def test_the_speaker_must_come_before_the_word_in_the_same_sentence_and_be_a_known_name():
    base = ["Waller talks about the economy and the outlook for monetary policy.", "Waller supports a cautious approach to the next steps in policy."]
    own = speech_check(*base, "Prices are likely to remain above the 2 percent goal for some time.")
    assert not own.ok and any("'likely' of summary point 3 is not attributed to the bank or to a named speaker" in e for e in own.errors)
    late = speech_check(*base, "Prices are likely to remain above the 2 percent goal for some time, Waller says.")
    assert not late.ok and any("is not attributed" in e for e in late.errors)
    other_sentence = speech_check(*base, "Waller speaks. Prices are likely to remain above the 2 percent goal for some time.")
    assert not other_sentence.ok
    stranger = speech_check(*base, "Smith expects inflation to move down as the labor market cools.")                # a name that is neither the bank's nor a known speaker
    assert not stranger.ok and any("'expects' of summary point 3 is not attributed" in e for e in stranger.errors)
    nobody = speech_check(*base, "Waller expects inflation to move down as the labor market cools.", speakers=())
    assert not nobody.ok                                                                                                # no roster name known: the speaker is not an attribution


def test_i_expect_in_the_document_allows_the_speaker_expects_but_not_another_word():
    base = ["Waller talks about the economy and the outlook for monetary policy.", "Waller supports a cautious approach to the next steps in policy."]
    assert speech_check(*base, "Waller expects inflation to move down as the labor market cools.").ok                # "I expect" -> "Waller expects"
    r = speech_check(*base, "Waller signals that inflation will move down as the labor market cools.")               # the speech never says "signal"
    assert not r.ok and any("the word 'signals' of summary point 3 is not in the document" in e for e in r.errors)
    r = speech_check(*base, "Waller is hawkish on inflation as the labor market cools, and expects it to move down.")
    assert not r.ok and any("uses the word 'hawkish'" in e for e in r.errors)


def test_a_speakers_name_is_not_counted_as_an_unsupported_word():
    paras = ["I expect inflation to move down as the labor market cools, and I see the risks to employment as more important than a month ago, and I will keep watching the data."]
    obj = {"summary": [point("Waller expects inflation to move down as the labor market cools.", [1], "I expect inflation to move down as the labor market cools,"),
                       point("Waller sees the risks to employment as more important.", [1], "I see the risks to employment as more important than a month ago,"),
                       point("Waller will keep watching the data.", [1], "and I will keep watching the data.")],
           "quotes": [{"paragraph": 1, "text": "I expect inflation to move down as the labor market cools"}], "coverage": [1]}
    assert V.verify(obj, paras, speakers=("waller",), **rules()).ok
    assert not V.verify(obj, paras, speakers=(), **rules()).ok                                                          # unknown name: 'waller' is an unsupported word, and 'expects' unattributed


# --- shape and length ----------------------------------------------------------------------------------------------------------------

def test_length_limits_of_points_quotes_and_the_whole_summary():
    ok = GOOD_TEXTS
    assert not check(with_summary(*ok[:2])).ok and "'summary' has 2 points; allowed 3-6" in check(with_summary(*ok[:2])).errors[0]
    seven = check(with_summary(*(ok + ok + ok[:1])))
    assert not seven.ok and "'summary' has 7 points; allowed 3-6" in seven.errors[0]
    assert not check(with_summary(ok[0], ok[1], "Too short")).ok
    assert not check(with_summary(ok[0], ok[1], "The Committee " + "states " * 80)).ok                                    # a point over 400 characters
    long_total = ["The Committee decided the matters of this meeting and states so in the document. " * 4] * 6
    r = check(with_summary(*long_total))
    assert not r.ok and any("characters in all" in e for e in r.errors)
    assert not check(variant(quotes=[])).ok                                                                             # 1-5 quotes
    assert not check(variant(quotes=[GOOD_FED["quotes"][1]] * 6)).ok


def test_six_points_and_five_quotes_are_the_upper_bounds_and_pass():
    pts = GOOD_TEXTS + ["The Committee is continuing its policy of maintaining ample reserves in the banking system.",
                                 "The Committee states that job gains have kept pace with the workforce.", "The Committee will deliver price stability, it says."]
    qs = [{"paragraph": 4, "text": "Inflation remains elevated."}, {"paragraph": 4, "text": "The Committee will deliver price stability."},
          {"paragraph": 3, "text": "Economic activity is expanding at a solid pace."}, {"paragraph": 3, "text": "Productivity growth is strong"},
          {"paragraph": 1, "text": "approved the following statement for release"}]
    r = check(variant(summary=pts, quotes=qs))
    assert r.ok, r.errors
    assert len(r.summary) == 6 and len(r.quotes) == 5


@pytest.mark.parametrize("cov", [[], [5], [0], ["1"], "1", None, [True]])
def test_coverage_must_name_paragraphs_of_the_document(cov):
    assert not check(variant(coverage=cov)).ok


def test_the_output_shape_is_checked():
    assert not check({"quotes": GOOD_FED["quotes"], "coverage": [1]}).ok
    assert not check(variant(summary="one long string")).ok
    assert not check(variant(summary=[1, 2, 3])).ok
    assert not check(variant(summary=[{"text": t} for t in GOOD_TEXTS])).ok                                              # no evidence
    assert not check(variant(summary=[{"evidence": p["evidence"]} for p in GOOD_FED["summary"]])).ok                     # no text
    assert not check(variant(quotes="x")).ok


# --- parsing ------------------------------------------------------------------------------------------------------------------------------

def test_parse_output_accepts_json_and_one_code_fence_and_nothing_else():
    obj, errs = V.parse_output('{"a": 1}')
    assert obj == {"a": 1} and errs == []
    assert V.parse_output('```json\n{"a": 1}\n```')[0] == {"a": 1}
    assert V.parse_output('```\n{"a": 1}\n```')[0] == {"a": 1}
    for bad in ("Here is the summary: {\"a\": 1}", "{\"a\": 1", "", "[1, 2]", "null", '{"a": 1} trailing', "```json\n{\"a\": 1}"):
        obj, errs = V.parse_output(bad)
        assert obj is None and errs, bad
    assert "not valid JSON" in V.parse_output("{bad")[1][0]
