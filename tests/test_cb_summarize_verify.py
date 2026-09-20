"""Phase 2b: the automatic check that stands between a model output and the store. Offline, on the real Fed statement of 16 Sep 2026 and small
constructed sources for what a real statement does not contain (curly quotes, accents, separators)."""
from __future__ import annotations

from fractions import Fraction

import pytest

from src.cb_summarize import verify as V
from src.cb_summarize.config import load as load_cfg

from .cb_docs_helpers import statement_paras
from .cb_sum_helpers import GOOD_FED, variant

CFG = load_cfg()
PARAS = statement_paras("USD", "2026-09-16")


def check(obj, paras=None):
    return V.verify(obj, paras or PARAS, points=CFG.summary_points, point_chars=CFG.point_chars, quotes=CFG.quotes, quote_chars=CFG.quote_chars,
                    total_chars=CFG.summary_total_chars, blocked=CFG.blocked_words)


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
    lim = dict(points=(1, 6), point_chars=(5, 400), quotes=(1, 5), quote_chars=(15, 500), total_chars=1800, blocked=CFG.blocked_words)
    ok = lambda text, para: V.verify({"summary": ["The document states its view on inflation."], "quotes": [{"paragraph": para, "text": text}], "coverage": [1]}, src, **lim)   # noqa: E731
    assert ok("The Governing Council’s view is that “inflation” has returned to target.", 1).ok
    assert ok("Vujčić a déclaré que les taux resteront inchangés jusqu’en 2027", 2).ok                                  # accents and the curly apostrophe are matched as they are
    assert not ok("The Governing Council's view is that “inflation” has returned", 1).ok                                # straight apostrophe: not the document's character
    assert not ok("The Governing Council’s view is that \"inflation\" has returned", 1).ok                             # straight double quotes
    assert not ok("the governing council’s view is that “inflation” has returned", 1).ok                               # case
    assert not ok("Vujcic a déclaré que les taux resteront inchangés jusqu’en 2027", 2).ok                              # accent dropped
    assert not ok("Vujčić a déclaré que les taux resteront inchangés jusqu’en 2027", 2).ok                       # the same letter, decomposed: not the same characters


def test_offsets_are_absolute_in_the_joined_text():
    src = ["First paragraph of the document, long enough.", "Second paragraph with the quoted words inside it."]
    lim = dict(points=(1, 6), point_chars=(5, 400), quotes=(1, 5), quote_chars=(10, 500), total_chars=1800, blocked=CFG.blocked_words)
    r = V.verify({"summary": ["The document has two paragraphs."], "quotes": [{"paragraph": 2, "text": "quoted words"}], "coverage": [2]}, src, **lim)
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
    r = check(with_summary(GOOD_FED["summary"][0], "The Committee decided to raise the target range to 4.5 percent.", GOOD_FED["summary"][2]))
    assert not r.ok and any("'4.5 percent' of summary point 2 does not appear" in e for e in r.errors)


def test_a_converted_number_is_refused():
    r = check(with_summary(GOOD_FED["summary"][0], "The Committee raised the target range by 25 basis points to 3-3/4 to 4 percent.", GOOD_FED["summary"][2]))
    assert not r.ok and any("'25 basis points'" in e and "never convert" in e for e in r.errors)                      # the document says 1/4 percentage point


def test_the_same_value_in_another_form_is_accepted():
    r = check(with_summary(GOOD_FED["summary"][0], "The Committee raised the target range by 0.25 percentage point to 3.75 to 4 percent.", GOOD_FED["summary"][2]))
    assert r.ok, r.errors


# --- direction / forecast / evaluation vocabulary ---------------------------------------------------------------------------------

@pytest.mark.parametrize("word", list(CFG.blocked_words))
def test_every_blocked_word_is_refused_in_a_summary_point(word):
    r = check(with_summary(GOOD_FED["summary"][0], GOOD_FED["summary"][1], f"The statement was described as {word.upper()} in its tone."))
    assert not r.ok and any("summary point 3" in e and "no direction, forecast or evaluation" in e for e in r.errors)


def test_blocked_words_are_whole_words_and_a_quote_may_carry_the_banks_own_words():
    r = check(with_summary(GOOD_FED["summary"][0], GOOD_FED["summary"][1], "The Committee states that the unlikely case was discussed and the assignment was signed."))
    assert r.ok, r.errors                                                                                                # "unlikely", "assignment", "signed": not blocked words
    paras = ["Inflation is likely to return to the Committee's 2 percent goal over the medium term, and the Committee expects to keep policy restrictive."]
    lim = dict(points=(3, 6), point_chars=(5, 400), quotes=(1, 5), quote_chars=(15, 500), total_chars=1800, blocked=CFG.blocked_words)
    q = {"summary": ["The statement covers inflation.", "It refers to a 2 percent goal.", "It refers to the stance of policy."],
         "quotes": [{"paragraph": 1, "text": "Inflation is likely to return to the Committee's 2 percent goal"}], "coverage": [1]}
    assert V.verify(q, paras, **lim).ok                                                                                  # 'likely' inside a QUOTE is the bank's word: allowed


# --- shape and length ----------------------------------------------------------------------------------------------------------------

def test_length_limits_of_points_quotes_and_the_whole_summary():
    ok = GOOD_FED["summary"]
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
    pts = GOOD_FED["summary"] + ["The Committee is continuing its policy of maintaining ample reserves in the banking system.",
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
