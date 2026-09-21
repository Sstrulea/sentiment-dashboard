"""Phase 2b, extended attribution: a soft word (likely, expect, ...) is attributed when the point's subject is the very subject of the source sentence that holds the same
word - "the staff expected ..." when the paragraph cited says "the staff's forecast ... higher than previously expected" - and that subject is in ONE clause of the
paragraphs the point cites. The rule of the bank / named speaker before the word is unchanged. Real cases: the 7 points the first backlog run refused (fixture
attribution_cases.json: the point as the model wrote it, the paragraphs it cited)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.cb_summarize import verify as V

from .cb_sum_helpers import FED_PARAS, GOOD_FED
from .test_cb_summarize_run import CFG
from .test_cb_summarize_verify import point, rules

CASES = json.loads((Path(__file__).parent / "fixtures" / "cb" / "attribution_cases.json").read_text())


def attributed(points, cited=None, src=None, speakers=()):
    """`src`: the text of the whole document (the word must be in it, in some form); by default the paragraphs of SRC. `cited`: the paragraphs each point cites."""
    src = " ".join(SRC) if src is None else src
    return V.check_attributed(list(points), src, CFG.attributed_words, CFG.attribution_subjects, speakers, CFG.attribution_pronouns, cited)


@pytest.mark.parametrize("case", CASES, ids=[c["doc"] for c in CASES])
def test_the_real_points_are_attributed_by_the_sentence_of_the_source_they_cite(case):
    src = " ".join(case["cited"])
    assert attributed([case["point"]], [case["cited"]], src) == []
    assert attributed([case["point"]], None, src) != []                                                                 # without the cited paragraphs the strict rule still refuses them


def test_the_seven_real_errors_are_all_lifted():
    n_strict = sum(len(attributed([c["point"]], None, " ".join(c["cited"]))) for c in CASES)
    n_mirror = sum(len(attributed([c["point"]], [c["cited"]], " ".join(c["cited"]))) for c in CASES)
    assert (n_strict, n_mirror) == (7, 0)                                                                               # the CHF point carries two words, 'expected' and 'likely'


SRC = ["The Bank expects inflation to fall as energy prices ease; staff see growth slowing this year. The staff said: wages are likely to stay firm, and the outlook is uncertain.",
       "Members discussed the risks. Consumption is expected to recover — investment is likely to stay weak."]


@pytest.mark.parametrize("text", [
    "Staff see growth slowing, while inflation is expected to fall as energy prices ease.",                             # the subject 'inflation' is the source's own ("The Bank expects inflation to fall")
    "The outlook is uncertain and wages are likely to stay firm.",                                                      # a clause after "and": the subject is the words since the conjunction
    "Consumption is expected to recover.",
    "Investment is likely to stay weak.",                                                                               # after a dash: its own clause
    "Given the weak labor market, consumption is expected to recover.",                                                 # a comma ends the noun phrase that governs the word
    "Prices fall as consumption is expected to recover.",                                                               # ... so does "as"
    "Confidence is falling and consumption is expected to recover.",                                                    # ... and "and"
])
def test_a_subject_of_the_source_clause_carries_the_soft_word(text):
    assert attributed([text], [SRC]) == [], text


@pytest.mark.parametrize("text, why", [
    ("The Treasury expects inflation to fall as energy prices ease.", "a subject the source does not have"),
    ("Staff expect wages to stay firm.", "'staff' is in the clause before the colon, 'likely' in the one after it"),
    ("Staff expect inflation to fall.", "'staff' is in the sentence after the semicolon, not in the clause of 'expects'"),
    ("Investment is expected to recover.", "investment is in the clause of 'likely', not in the one of 'expected'"),
    ("Consumption is likely to stay weak.", "consumption is in the clause of 'expected', not in the one of 'likely' (after the dash)"),
    ("Exports are likely to stay weak.", "no such subject anywhere"),
    ("It is likely to stay weak.", "no content word to anchor: a pronoun is nobody"),
    ("Members discussed inflation. Prices are likely to rise.", "another sentence, another subject"),
    ("Household consumption is expected to recover.", "'household' is not in the source: every word of the subject must be there"),
])
def test_an_invented_subject_or_one_from_another_clause_is_still_refused(text, why):
    errs = attributed([text], [SRC])
    assert errs and "is not attributed to the bank or to a named speaker" in errs[0], why


def test_a_subject_in_the_previous_sentence_is_another_clause():
    cited = [["Inflation rose in the quarter. Wages are likely to stay firm."]]
    assert attributed(["Wages are likely to stay firm."], cited, cited[0][0]) == []
    errs = attributed(["Inflation is likely to stay firm."], cited, cited[0][0])
    assert errs and "is not attributed" in errs[0]                                                                      # 'inflation' ends its sentence at the full stop


def test_the_soft_word_of_the_source_must_be_the_points_own_word_not_a_shorter_one():
    src = "Growth is likely to slow, but consumption looks like a recovery in some sectors."                              # 'likely' is in the document, 'like' is another word
    assert attributed(["Consumption is likely to recover."], [[src.split(", ")[1]]], src) != []
    assert attributed(["Growth is likely to slow."], [[src.split(", ")[0]]], src) == []


def test_only_the_paragraphs_the_point_cites_count():
    text = "Consumption is expected to recover."
    assert attributed([text], [[SRC[1]]]) == []
    assert attributed([text], [[SRC[0]]]) != []                                                                         # the second paragraph is not cited
    assert attributed([text], [[]]) != [] and attributed([text], []) != [] and attributed([text], None) != []           # nothing cited (or nothing given): the strict rule


def test_the_bank_before_the_word_is_still_an_attribution_and_the_document_must_still_use_the_word():
    assert attributed(["The Bank says wages are likely to stay firm."], [[SRC[1]]]) == []                               # the bank is named: no need of the source's clause
    assert attributed(["The Bank says wages are likely to stay firm."], None) == []
    assert attributed(["Staff see growth slowing."], [SRC]) == []                                                       # no soft word at all


def test_the_word_form_of_the_source_is_enough_but_a_different_word_is_not():
    text = "The staff's baseline forecast was for underlying inflation to be higher than previously expected, remaining above 3 per cent."
    assert attributed(["The staff expected underlying inflation to stay above 3 per cent."], [[text]], text) == []      # expected / expected
    assert attributed(["The staff expect underlying inflation to stay above 3 per cent."], [[text]], text) == []        # expect: a form of the same word
    assert attributed(["The staff signals underlying inflation to stay above 3 per cent."], [[text]], text) != []       # 'signals' is not in the document at all


def test_the_comma_does_not_cut_a_clause_but_a_semicolon_a_colon_and_a_dash_do():
    text = "Staff said that growth is slowing, and inflation is expected to fall."                                       # one clause: the comma joins them
    assert attributed(["Staff expect inflation to fall."], [[text]], text) == []
    for cut in ("; ", ": ", " \u2014 ", " \u2013 ", " -- "):
        text = f"Staff said that growth is slowing{cut}inflation is expected to fall."
        assert attributed(["Staff expect inflation to fall."], [[text]], text) != [], repr(cut)


# --- through verify(): the paragraphs the evidence of the point cites are the ones the rule looks at -------------------------------------------------------

PARAS = list(FED_PARAS) + [
    "Consumption is expected to recover as jobs return and financing conditions ease over the next year.",
    "Consumption will recover as jobs return and financing conditions ease over the next year, while the labor market remains resilient."]
MIRROR = "Consumption is expected to recover as jobs return over the next year."
FRAG5 = "Consumption is expected to recover as jobs return and financing conditions ease"
FRAG6 = "Consumption will recover as jobs return and financing conditions ease over the next year,"


def run(text, paragraphs, fragment):
    obj = dict(GOOD_FED, summary=list(GOOD_FED["summary"][:2]) + [point(text, paragraphs, fragment)], coverage=[1, 2, 3, 4, 5, 6])
    return V.verify(obj, PARAS, speakers=(), **rules())


def test_verify_lifts_the_word_only_for_the_paragraphs_the_evidence_cites():
    ok = run(MIRROR, [5], FRAG5)
    assert ok.ok, ok.errors
    other = run(MIRROR, [6], FRAG6)                                                                                     # paragraph 6 says the same without the word: the point is grounded, not attributed
    assert not other.ok and any("'expected' of summary point 3 is not attributed" in e for e in other.errors)
    assert not any("evidence" in e for e in other.errors), other.errors
    both = run(MIRROR, [5, 6], FRAG5)
    assert not any("is not attributed" in e for e in both.errors), both.errors                                          # citing more paragraphs keeps the clause available


def test_verify_still_refuses_the_own_voice_point_when_the_subject_is_not_the_sources():
    r = run("Employment is expected to recover as jobs return and financing conditions ease over the next year.", [5], FRAG5)
    assert not r.ok and any("'expected' of summary point 3 is not attributed" in e for e in r.errors)
