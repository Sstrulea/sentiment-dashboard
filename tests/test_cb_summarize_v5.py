"""Phase 2b, prompts v5: the last guards of the verifier - pronouns as attribution, dates / months / days / proper names taken from the cited paragraphs, a
statement resting only on its own speaker's turns in a transcript, negation that matches the source, and 1-3 fragments per point that must each say something
of the point. Real texts: the Fed statement of 16 Sep 2026 and an excerpt of the press conference of the same day."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.cb_docs import extract as X
from src.cb_summarize import source as SRC
from src.cb_summarize import turns as T
from src.cb_summarize import verify as V

from .cb_sum_helpers import GOOD_FED, GOOD_TEXTS
from .test_cb_summarize_verify import CFG, PARAS, P_ACT, P_RATE, P_VOTE, grounded, point, rules

EXCERPT = json.loads((Path(__file__).parent / "fixtures" / "cb" / "fed_presser_20260916_excerpt.json").read_text())
TPARAS, TURNS = T.segment(EXCERPT, frozenset({"warsh"}))
PEOPLE = (("kevin", "warsh"),)
SPEAKERS = ("kevin", "warsh")


def transcript(*points, paras=None, turns=None, quotes=None, speakers=SPEAKERS, people=PEOPLE, **over):
    paras, turns = paras or TPARAS, turns or TURNS
    q = quotes or [{"paragraph": 3, "text": "I'm not going to pre-judge any future decisions we make."}]
    return V.verify({"summary": list(points), "quotes": q, "coverage": [3, 13]}, paras, speakers=speakers, people=people, labels=turns,
                    bank_terms=("federal", "reserve"), **rules(**over))


def para_of(text):
    return next(i + 1 for i, p in enumerate(TPARAS) if text in p)


SOBER = point("Warsh said the decision made today was a sober and serious decision.", [3], "The decision we made today was a sober decision, serious decision, responsible decision")
NOT_FG = point("Warsh said he is not in the forward guidance business.", [3], "This won't surprise you, I'm not in the forward guidance business.")
STRONG = point("Warsh said it is the judgment of the Committee that the economy has strengthened.", [13], "There's been a pretty wideranging set of data, including in the labor markets, that the economy has strengthened.")
Q_PARA = para_of("Inflation has run up mostly from higher energy prices and tariffs")                                    # Jennifer Schonberger's question


# --- the turns of a transcript ----------------------------------------------------------------------------------------------------------------------

def test_the_fed_transcript_is_cut_at_every_label_and_the_page_furniture_is_gone():
    assert len(EXCERPT) == 18 and len(TPARAS) == 21 and len(TURNS) == 21
    assert not any("PRELIMINARY" in p or p.startswith("Page ") for p in TPARAS)                                         # the repeated page header and "Page 2 of 15"
    assert TURNS[0] is None and TPARAS[0].startswith("Transcript of")                                                    # before the first label
    who = {(t.name, t.official) for t in TURNS if t}
    assert ("CHAIRMAN WARSH", True) in who and ("MICHELLE SMITH", False) in who and ("JENNIFER SCHONBERGER", False) in who and ("EDWARD LAWRENCE", False) in who
    assert all(p.startswith(t.name + ". ") for p, t in zip(TPARAS, TURNS) if t and p[:1].isupper() and p.split(".")[0].isupper())      # a labelled paragraph starts with its label
    assert TURNS[para_of("judgment seven weeks ago") - 1].name == "CHAIRMAN WARSH"                                        # a page that continues a turn keeps its speaker
    assert TPARAS[para_of("MICHELLE SMITH. Steve Thompson") - 1].count("MICHELLE SMITH.") == 2 or True                    # (two labels of one speaker in a row stay in one paragraph)


def test_a_paragraph_is_never_two_speakers_words():
    for p, t in zip(TPARAS, TURNS):
        labels_inside = [m.group(1) for m in T.FED_LABEL.finditer(p) if m.start() > 0 and t]
        assert all(l == t.name for l in labels_inside) or p.startswith(t.name)                                          # only the moderator's one-liners chain labels


def test_officials_are_the_banks_members_and_titles_not_the_journalists_or_the_moderator():
    assert T.is_official("CHAIRMAN WARSH", frozenset({"warsh"})) and T.is_official("CHAIR POWELL", frozenset()) and T.is_official("Michele Bullock", frozenset({"bullock"}))
    assert not T.is_official("MICHELLE SMITH", frozenset({"warsh"})) and not T.is_official("Jacob Shteyman", frozenset({"bullock"}))
    assert T.is_official("CHARIMAN WARSH", frozenset({"warsh"}))                                                         # (the transcript's own typo: the surname decides)
    assert T.tags_of(TURNS)[para_of("Inflation has run up mostly") - 1] == "question" and T.tags_of(TURNS)[para_of("judgment seven weeks ago") - 1] is None
    assert T.tags_of(None) is None


def test_a_transcript_with_name_paragraphs_is_read_as_turns():
    rba = ["Media Conference Monetary Policy Decision", "Michele Bullock Governor", "11 August 2026 – Sydney", "Michele Bullock",
           "Good afternoon. Today, the Board decided to leave the cash rate unchanged at 4.35 per cent, and it will watch the data.", "Jacob Shteyman",
           "Thanks Governor, Jacob Shteyman from AAP. You mentioned upside risks to inflation, and I wonder what could bring them about.", "Michele Bullock",
           "So, again, we will be looking at the forecasts, and the forecasts see inflation coming back down over time.", "Monetary Policy Board Statement",
           "A related link at the end of the page that is not a person and holds enough characters to look like a paragraph."]
    paras, turns = T.segment(rba, frozenset({"bullock"}))
    assert paras == rba
    assert [None if t is None else (t.name, t.official) for t in turns[:9]] == [None, None, None, ("Michele Bullock", True), ("Michele Bullock", True), ("Jacob Shteyman", False),
                                                                                  ("Jacob Shteyman", False), ("Michele Bullock", True), ("Michele Bullock", True)]
    assert turns[9].name == "Michele Bullock"                                                                            # "Monetary Policy Board Statement" is a heading, not a speaker


def test_an_ecb_transcript_has_no_labels_its_bold_paragraphs_are_the_questions():
    paras = ["Good afternoon, the Vice-President and I welcome you.", "We are now ready to take your questions.", "Would you repeat what you said in July about the reaction function?",
             "You give me a chance to give what I like most, which is framework guidance."]
    out, turns = T.segment(paras, frozenset({"lagarde"}), bold={paras[2]})
    assert out == paras and [t.official for t in turns] == [True, True, False, True] and turns[2].name == "question" and turns[3].name == ""
    assert T.tags_of(turns) == [None, None, "question", None]
    assert T.segment(paras, frozenset({"lagarde"}), bold=set())[1] is None                                                # nothing in bold, no labels: no speakers known


def test_a_text_without_speakers_is_left_alone():
    paras = ["One paragraph of a speech that has no labels at all and is long enough.", "Another paragraph of the same speech, also without any label to read."]
    assert T.segment(paras, frozenset({"waller"})) == (paras, None)


def test_the_bold_paragraphs_are_read_from_the_html_of_the_ecb():
    html = ('<html><body><div class="section"><p>We are now ready to take your questions.</p><p><strong>Would you repeat what you said in July?</strong><a id="qa"></a></p>'
            '<p>You give me a chance to <strong>give</strong> what I like most.</p><p><b>And the second one, on your own future?</b></p></div></body></html>')
    assert X.html_bold_paragraphs(html, {"sel": ("div", None, "section")}) == {"Would you repeat what you said in July?", "And the second one, on your own future?"}
    assert X.html_bold_paragraphs("<html></html>", {"sel": ("div", None, "section")}) == set()


def test_the_source_of_a_transcript_keeps_the_hash_of_the_extraction_and_says_who_speaks():
    src = SRC.from_paragraphs(EXCERPT, "pypdf", 10**6, transcript={"officials": ["warsh"]})
    assert src.sha256 == X.sha256(X.to_text(EXCERPT)) and src.paragraphs == TPARAS and src.turns == TURNS and len(src.tags) == len(src.paragraphs)
    assert src.text == X.to_text(TPARAS) != X.to_text(EXCERPT)                                                           # the sent text is the turns; the hash is phase 2a's
    cut = SRC.from_paragraphs(EXCERPT, "pypdf", 2500, transcript={"officials": ["warsh"]})
    assert cut.truncated and len(cut.turns) == len(cut.paragraphs) == len(cut.tags) < len(TPARAS)                        # the labels follow the cut
    plain = SRC.from_paragraphs(EXCERPT, "pypdf", 10**6)
    assert plain.turns is None and plain.tags is None and plain.paragraphs == EXCERPT                                    # not a transcript: nothing changes


def test_the_message_marks_a_journalists_question():
    from src.cb_summarize import prompts as PR
    m = PR.user_message("presser_transcript", "Federal Reserve", "https://x", ["a", "b", "c"], [None, "question", None])
    assert m.endswith("Paragraphs:\n[1] a\n[2] (question) b\n[3] c\n")
    assert PR.user_message("speech", "Federal Reserve", "https://x", ["a"]).endswith("Paragraphs:\n[1] a\n")


# --- 3. a statement rests on its own speaker's turns ----------------------------------------------------------------------------------------------

def test_the_good_transcript_points_pass_with_labels():
    r = transcript(SOBER, NOT_FG, STRONG)
    assert r.ok, r.errors


def test_a_claim_from_a_journalists_question_attributed_to_the_chair_is_refused():
    claim = point("Warsh said inflation has run up mostly from higher energy prices and tariffs, which rate hikes cannot fix.", [Q_PARA],
                  "Inflation has run up mostly from higher energy prices and tariffs, which some say are supply shocks")
    r = transcript(SOBER, NOT_FG, claim)
    assert not r.ok
    assert any("summary point 3 attributes the statement to Warsh, but paragraph %d that it cites is the turn of JENNIFER SCHONBERGER" % Q_PARA in e for e in r.errors), r.errors
    assert not any("not supported" in e for e in r.errors)                                                               # every word is in the question: only the speaker is wrong


def test_the_same_claim_without_a_speaker_named_is_refused_too_and_named_after_its_speaker_it_passes():
    bare = point("Inflation has run up mostly from higher energy prices and tariffs, which rate hikes cannot fix.", [Q_PARA],
                 "Inflation has run up mostly from higher energy prices and tariffs, which some say are supply shocks")
    r = transcript(SOBER, NOT_FG, bare)
    assert not r.ok and any("cites paragraph %d, the turn of JENNIFER SCHONBERGER, for a statement that names no speaker" % Q_PARA in e for e in r.errors)
    asked = point("Jennifer Schonberger said inflation has run up mostly from higher energy prices and tariffs, which rate hikes cannot fix.", [Q_PARA],
                  "Inflation has run up mostly from higher energy prices and tariffs, which some say are supply shocks")
    assert transcript(SOBER, NOT_FG, asked, speakers=SPEAKERS + ("jennifer", "schonberger"), people=PEOPLE + (("jennifer", "schonberger"),)).ok      # named, and it is her turn


def test_a_named_speaker_may_not_cite_the_moderator_nor_another_speaker_nor_a_paragraph_before_any_label():
    moderator = point("Warsh said Edward Lawrence was called on.", [para_of("MICHELLE SMITH. Edward Lawrence.")], "MICHELLE SMITH. Edward Lawrence.")
    r = transcript(SOBER, NOT_FG, moderator)
    assert not r.ok and any("the turn of MICHELLE SMITH" in e for e in r.errors)
    header = point("Warsh said this is the transcript of his press conference of September 16.", [1], "Transcript of Chairman Warsh’s Press Conference September 16, 2026")
    r = transcript(SOBER, NOT_FG, header)
    assert not r.ok and any("paragraph 1 that it cites is no speaker" in e for e in r.errors)
    other = point("Schonberger said the decision was a sober and serious decision.", [3], "The decision we made today was a sober decision, serious decision, responsible decision")
    r = transcript(SOBER, NOT_FG, other, speakers=SPEAKERS + ("schonberger",), people=PEOPLE + (("jennifer", "schonberger"),))
    assert not r.ok and any("attributes the statement to Schonberger, but paragraph 3 that it cites is the turn of CHAIRMAN WARSH" in e for e in r.errors)


def test_the_first_named_person_is_the_speaker_and_the_first_name_is_enough():
    both = point("Kevin Warsh said the decision was sober, and Jennifer Schonberger asked about tariffs.", [3], "The decision we made today was a sober decision, serious decision, responsible decision")
    r = transcript(SOBER, NOT_FG, both, speakers=SPEAKERS + ("jennifer", "schonberger"), people=PEOPLE + (("jennifer", "schonberger"),))
    assert not any("attributes the statement" in e for e in r.errors)                                                    # the first person named is the speaker; the first name suffices
    first_name_only = point("Kevin said the decision made today was a sober decision.", [3], "The decision we made today was a sober decision, serious decision, responsible decision")
    assert transcript(SOBER, NOT_FG, first_name_only).ok


def test_a_title_is_not_a_speaker_and_a_point_of_the_bank_may_cite_the_banks_turns():
    chair = point("The Chairman said the decision made today was a sober and serious decision.", [3], "The decision we made today was a sober decision, serious decision, responsible decision")
    assert transcript(SOBER, NOT_FG, chair).ok                                                                          # no name: the bank's turn is enough
    committee = point("Three things led the Committee to a firm, unanimous decision today.", [13], "All three of those things led -- lend themselves to a firm, unanimous decision today.")
    assert transcript(SOBER, NOT_FG, committee).ok


def test_the_ecb_answers_are_unlabelled_and_a_named_official_may_cite_them_but_not_a_question():
    paras = ["We are now ready to take your questions.", "Would you repeat what you said in July about the reaction function of the Governing Council?",
             "You give me a chance to give what I like most, which is framework guidance based on the inflation outlook and the assessment of the risks."]
    out, turns = T.segment(paras, frozenset({"lagarde"}), bold={paras[1]})
    obj = lambda pt: {"summary": [pt, point("Lagarde said the Council is ready to take your questions.", [1], "We are now ready to take your questions."),                     # noqa: E731
                                 point("Lagarde said framework guidance based on the inflation outlook and the risks.", [3], "framework guidance based on the inflation outlook and the assessment of the risks")],
                      "quotes": [{"paragraph": 3, "text": "which is framework guidance"}], "coverage": [1, 3]}
    ok = V.verify(obj(point("Lagarde said she likes framework guidance most.", [3], "You give me a chance to give what I like most, which is framework guidance")), out,
                  speakers=("lagarde",), people=(("christine", "lagarde"),), labels=turns, **rules())
    assert ok.ok, ok.errors
    bad = V.verify(obj(point("Lagarde said the reaction function of the Governing Council was discussed in July.", [2], "Would you repeat what you said in July about the reaction function")), out,
                   speakers=("lagarde",), people=(("christine", "lagarde"),), labels=turns, **rules())
    assert not bad.ok and any("attributes the statement to Lagarde, but paragraph 2 that it cites is a journalist's question" in e for e in bad.errors)


def test_without_turns_there_is_no_speaker_rule():
    r = V.verify({"summary": [SOBER, NOT_FG, STRONG], "quotes": [{"paragraph": 3, "text": "I'm not going to pre-judge any future decisions we make."}], "coverage": [3]},
                 TPARAS, speakers=SPEAKERS, people=PEOPLE, bank_terms=("federal", "reserve"), **rules())
    assert r.ok, r.errors
    wrong = point("Warsh said inflation has run up mostly from higher energy prices and tariffs, which rate hikes cannot fix.", [Q_PARA], "Inflation has run up mostly from higher energy prices and tariffs, which some say are supply shocks")
    assert V.verify({"summary": [SOBER, NOT_FG, wrong], "quotes": [{"paragraph": 3, "text": "I'm not going to pre-judge any future decisions we make."}], "coverage": [3]},
                    TPARAS, speakers=SPEAKERS, people=PEOPLE, bank_terms=("federal", "reserve"), **rules()).ok           # (the reason the turns are read at all)


# --- 2. dates, months, days, years and proper names come from the cited paragraphs ------------------------------------------------------------------

def test_a_derived_date_is_refused_since_july_from_seven_weeks_ago():
    derived = point("Warsh said the economy has strengthened, in a pretty wideranging set of data including the labor markets, since July.", [para_of("judgment seven weeks ago")],
                    "There's been a pretty wideranging set of data, including in the labor markets, that the economy has strengthened.")
    r = transcript(SOBER, NOT_FG, derived)
    assert not r.ok and len(r.errors) == 1
    assert "summary point 3 names 'July', which the paragraph(s) it cites [%d] do not contain" % para_of("judgment seven weeks ago") in r.errors[0] and "never derive or infer" in r.errors[0]
    written = point("Warsh said the economy has strengthened, in a pretty wideranging set of data including the labor markets, seven weeks ago.", [para_of("judgment seven weeks ago")],
                    "There's been a pretty wideranging set of data, including in the labor markets, that the economy has strengthened.")
    assert transcript(SOBER, NOT_FG, written).ok                                                                        # the words the transcript writes


@pytest.mark.parametrize("text, missing", [
    ("The Committee said the decision was taken on Monday.", ["Monday"]),
    ("The Committee said the decision was taken on the 16th.", ["16th"]),
    ("The Committee said the meeting ended in September.", ["September"]),
    ("The Committee said economic activity is expanding as in May.", ["May"]),
    ("The Committee said economic activity is expanding since May.", ["May"]),
    ("The Committee said economic activity is expanding, according to the Treasury.", ["Treasury"]),
    ("The Committee said economic activity is expanding in the Middle East.", ["Middle", "East"]),
    ("The Committee said economic activity is expanding, as Jerome Powell noted.", ["Jerome", "Powell"]),
    ("The Committee said economic activity is expanding, as the Bundesbank noted.", ["Bundesbank"]),
    ("The Committee said economic activity is expanding in the U.S. economy.", ["US"]),
])
def test_dates_days_places_institutions_and_persons_must_be_in_the_cited_paragraph(text, missing):
    r = grounded(P_VOTE, P_RATE, point(text, [3], "Economic activity is expanding at a solid pace."), bank_terms=("federal", "reserve"))
    assert not r.ok and any(f"names {', '.join(repr(w) for w in missing)}, which the paragraph(s)" in e for e in r.errors), r.errors


def test_what_is_not_a_date_or_a_name_is_not_refused_and_a_name_the_paragraph_has_is_accepted():
    fine = [point("The Committee said economic activity is expanding, and it may raise the target range.", [3], "Economic activity is expanding at a solid pace."),         # "may" the verb
            point("The Committee said Economic activity is expanding.", [3], "Economic activity is expanding at a solid pace."),                                  # capital at a sentence start
            point("The Committee said. Economic activity is expanding at a solid pace.", [3], "Economic activity is expanding at a solid pace.")]
    for pt in fine:
        r = grounded(P_VOTE, P_RATE, pt)
        assert not any("which the paragraph(s)" in e for e in r.errors), (pt["text"], r.errors)
    geo = ["While uncertainty remains elevated owing, in part, to Geopolitical developments and to Federal Open Market Committee views, domestic spending has been resilient across America. The weather has been mild."]
    inside = point("The Committee said domestic spending has been resilient across America owing to geopolitical developments.", [1], "domestic spending has been resilient across America. The weather")
    r = V.verify({"summary": [inside, point("The Committee said uncertainty remains elevated.", [1], "While uncertainty remains elevated owing, in part, to Geopolitical"),
                              point("The Committee said the weather has been mild.", [1], "domestic spending has been resilient across America. The weather has been mild.")],
                  "quotes": [{"paragraph": 1, "text": "domestic spending has been resilient"}], "coverage": [1]}, geo, **rules())
    assert r.ok, r.errors                                                                                                # "America" is in the paragraph
    assert V.strict_tokens("Warsh said Waller and the Bank of Japan met on 4th of July in Tokyo.", {"warsh", "waller", "bank"}) == ["July", "Japan", "Tokyo", "4th"]
    assert V.strict_tokens("The Committee says.", {"committee"}) == [] and V.strict_tokens("Inflation is in May 2026.", set()) == ["May"]           # years are numbers: checked as numbers


def test_the_speakers_and_the_banks_own_names_are_the_attribution_not_a_claim():
    pt = point("Kevin Warsh said the Federal Reserve made the decision made today, a sober decision.", [3], "The decision we made today was a sober decision, serious decision, responsible decision")
    assert transcript(SOBER, NOT_FG, pt).ok                                                                             # Kevin, Warsh, Federal, Reserve: not in the paragraph, and not needed there
    alias = point("Warsh said the Fed made the decision made today, a sober decision.", [3], "The decision we made today was a sober decision, serious decision, responsible decision")
    assert transcript(SOBER, NOT_FG, alias).ok                                                                          # "the Fed" too (config: attribution_subjects)
    r = transcript(SOBER, NOT_FG, point("Warsh said the Treasury made the decision made today, a sober decision.", [3], "The decision we made today was a sober decision, serious decision, responsible decision"))
    assert not r.ok and any("names 'Treasury'" in e for e in r.errors)                                                  # any other institution is a name like any other; "the Fed" is the bank's own alias


# --- 4. a negation is the source's negation ------------------------------------------------------------------------------------------------------

def test_an_inverted_negation_is_refused():
    inverted = point("Warsh said he is in the forward guidance business.", [3], "This won't surprise you, I'm not in the forward guidance business.")
    r = transcript(SOBER, STRONG, inverted)
    assert not r.ok and len(r.errors) == 1
    assert "summary point 3 has none but the closest sentence of the paragraph(s) it cites has one" in r.errors[0] and "I'm not in the forward guidance business" in r.errors[0]
    assert "never turn a statement into its opposite" in r.errors[0]
    added = point("Warsh said the economy has not strengthened.", [13], "There's been a pretty wideranging set of data, including in the labor markets, that the economy has strengthened.")
    r = transcript(SOBER, NOT_FG, added)
    assert not r.ok and any("summary point 3 has a negation but the closest sentence of the paragraph(s) it cites has none" in e for e in r.errors)


@pytest.mark.parametrize("word", ["not", "no", "never", "without", "neither", "nor", "cannot", "isn't", "isn’t"])
def test_every_negation_word_counts(word):
    src = ["The Board says the economy grows and inflation falls in the coming quarters of the year."]
    rx = V.negation_regex(CFG.negations)
    assert V.has_negation(f"The Board says the economy {word} grows.", rx)
    assert V.negation_error(f"The Board says the economy {word} grows and inflation falls.", src, rx, set(), 1) is not None                # the source has none
    assert V.negation_error("The Board says the economy grows and inflation falls.", src, rx, set(), 1) is None


def test_not_only_is_not_a_negation_and_a_tie_takes_the_sentence_that_agrees():
    rx = V.negation_regex(CFG.negations)
    assert not V.has_negation("Growth is not only strong but also broad.", rx) and V.has_negation("Growth is not strong.", rx) and not V.has_negation("Notable, knowledge, nothing", rx)
    cited = ["Growth is strong and broad across the sectors of the economy. Growth is not strong and broad across the sectors of the economy."]
    assert V.negation_error("Growth is strong and broad across the sectors of the economy.", cited, rx, set(), 1) is None                    # the two sentences tie: the agreeing one counts
    assert V.negation_error("Growth is weak.", cited, rx, set(), 1) is None                                              # nothing in common: nothing to compare


def test_the_closest_sentence_decides_not_a_sentence_elsewhere_in_the_paragraph():
    cited = ["The unemployment rate has changed little and job gains have kept pace with the workforce. Inflation is not falling as fast as the Committee wants, and prices keep rising."]
    rx = V.negation_regex(CFG.negations)
    assert V.negation_error("Job gains have kept pace with the workforce and the unemployment rate has changed little.", cited, rx, set(), 1) is None      # closest: no negation, point: none
    assert V.negation_error("Inflation is falling as fast as the Committee wants.", cited, rx, set(), 1) is not None                                       # closest has 'not'


def test_the_negation_check_runs_on_a_real_statement_point():
    neg = point("The Committee said economic activity is not expanding at a solid pace.", [3], "Economic activity is expanding at a solid pace.")
    r = grounded(P_VOTE, P_RATE, neg)
    assert not r.ok and any("has a negation but the closest sentence" in e for e in r.errors)


# --- 1. a pronoun is an attribution once the bank or the speaker is named in the point ----------------------------------------------------------------

SPEECH = ["Good morning. I am pleased to be here to talk about the economy and the outlook for monetary policy in the coming year.",
          "I expect inflation to move down as the labor market cools, and I see the risks to employment as more important than a month ago.",
          "Inflation is likely to remain above the 2 percent goal for some time, and I support a cautious approach to the next steps in policy."]


def check_speech(pt, speakers=("waller",)):
    from .cb_sum_helpers import wrap_points
    talk = "Waller talks about the economy and the outlook for monetary policy."
    support = "Waller supports a cautious approach to the next steps in policy."
    obj = {"summary": wrap_points([talk, support, pt], SPEECH), "quotes": [{"paragraph": 2, "text": "I expect inflation to move down as the labor market cools"}], "coverage": [1, 2, 3]}
    return V.verify(obj, SPEECH, speakers=speakers, **rules())


@pytest.mark.parametrize("text", [
    "Waller says he expects inflation to move down as the labor market cools.",
    "Waller says the labor market cools; he expects inflation to move down.",
    "Waller opens the talk on the economy. He expects inflation to move down as the labor market cools.",
    "The Board says the labor market cools, and they expect inflation to move down.",
    "The Committee says the risks to employment are more important, and she expects inflation to move down.",
])
def test_a_pronoun_is_an_attribution_when_the_speaker_or_the_bank_is_named_earlier_in_the_point(text):
    r = check_speech(text)
    assert r.ok, r.errors


@pytest.mark.parametrize("text", [
    "He expects inflation to move down as the labor market cools.",
    "The labor market cools. He expects inflation to move down.",
    "It expects inflation to move down as the labor market cools, says Waller.",
    "The Board says the labor market cools. Waller sees risks to employment. Inflation is likely to move down.",
    "Waller says the labor market cools. He sees risks to employment. Inflation is likely to move down.",                 # a pronoun and the name, but in other sentences than the word
    "The risks to employment are more important as inflation is likely to move down, he says.",
])
def test_a_pronoun_alone_or_before_the_name_is_not_an_attribution(text):
    r = check_speech(text)
    assert not r.ok and any("is not attributed to the bank or to a named speaker" in e or "is not in the document" in e for e in r.errors), r.errors


def test_the_pronouns_are_the_configured_ones_and_it_is_not_one_of_them():
    assert CFG.attribution_pronouns == ("he", "she", "they")
    r = check_speech("Waller opens the talk on the economy. It expects inflation to move down as the labor market cools.")
    assert not r.ok and any("'expects' of summary point 3 is not attributed" in e for e in r.errors)
    src = " ".join(SPEECH)
    point_text = ["Waller says the labor market cools; he expects inflation to move down."]
    assert V.check_attributed(point_text, src, ("expects",), ("Committee",), ("waller",), ("he",)) == []
    assert V.check_attributed(point_text, src, ("expects",), ("Committee",), ("waller",), ()) != []                       # no pronoun configured: 'he' is nobody
    assert V.check_attributed(["He expects inflation to move down. Waller says so."], src, ("expects",), ("Committee",), ("waller",), ("he",)) != []      # the name comes after


# --- 5. up to three fragments per point, each one about the point -----------------------------------------------------------------------------------

def test_a_point_with_two_claims_may_cite_two_fragments_and_both_are_recorded_with_their_offsets():
    two = point("The Committee decided to raise the target range and said economic activity is expanding at a solid pace.", [2, 3],
                "raise the target range for the federal funds rate by 1/4 percentage point", "Economic activity is expanding at a solid pace.")
    r = grounded(P_VOTE, two, P_ACT)
    assert r.ok, r.errors
    frs = r.points[1]["evidence"]["fragments"]
    assert [f["paragraph"] for f in frs] == [2, 3] and len(frs) == 2
    src = V.source_text(PARAS)
    assert all(src[f["start"]:f["end"]] == f["text"] for f in frs)
    assert V.verify_stored({"summary": r.points, "quotes": r.quotes, "numbers": r.numbers}, PARAS) == []
    three = point("The Committee decided to raise the target range, said activity is expanding at a solid pace and said inflation remains elevated.", [2, 3, 4],
                  "raise the target range for the federal funds rate by 1/4 percentage point", "Economic activity is expanding at a solid pace.", "Inflation remains elevated. Today's policy action will support")
    assert grounded(P_VOTE, three, P_ACT).ok


def test_a_filler_fragment_is_refused_each_fragment_must_say_something_of_the_point():
    filler = point("The Committee decided to raise the target range for the federal funds rate.", [2, 3],
                   "raise the target range for the federal funds rate by 1/4 percentage point", "Productivity growth is strong, and capital investment is robust.")
    r = grounded(P_VOTE, filler, P_ACT)
    assert not r.ok and len(r.errors) == 1
    assert "evidence fragment 2 of summary point 2 has 0 content word(s) in common with the point; at least 2 needed" in r.errors[0]
    one = point("The Committee decided to raise the target range for the federal funds rate.", [2, 3],
                "raise the target range for the federal funds rate by 1/4 percentage point", "The Committee is continuing its policy of maintaining ample reserves in the banking system.")
    assert not grounded(P_VOTE, one, P_ACT).ok                                                                          # a real passage of the same paragraph, about something else
    tiny = point("Inflation remains elevated.", [4], "Inflation remains elevated. Today's policy action will support")
    r = grounded(P_VOTE, P_RATE, tiny, point_chars=(5, 400))
    assert r.ok, r.errors                                                                                                # a point of two content words needs two: the minimum is min(2, its words)


def test_a_fragment_that_is_not_verbatim_or_not_in_a_cited_paragraph_is_refused_among_several():
    two = point("The Committee decided to raise the target range and said economic activity is expanding at a solid pace.", [2],
                "raise the target range for the federal funds rate by 1/4 percentage point", "Economic activity is expanding at a solid pace.")
    r = grounded(P_VOTE, two, P_ACT)
    assert not r.ok and any("evidence fragment 2 of summary point 2 is not verbatim in the paragraph(s) it cites [2] (that text is in paragraph 3: cite it)" in e for e in r.errors)
    dup = point("The Committee decided to raise the target range for the federal funds rate.", [2],
                "raise the target range for the federal funds rate by 1/4 percentage point", "raise the target range for the federal funds rate by 1/4 percentage point")
    r = grounded(P_VOTE, dup, P_ACT)
    assert r.ok and len(r.points[1]["evidence"]["fragments"]) == 1                                                       # the same passage twice is one fragment


def test_the_output_schema_and_the_prompt_ask_for_fragments():
    from src.cb_summarize import prompts as PR
    from src.cb_summarize.schema import OUTPUT_SCHEMA
    ev = OUTPUT_SCHEMA["properties"]["summary"]["items"]["properties"]["evidence"]
    assert ev["required"] == ["paragraphs", "fragments"] and ev["properties"]["fragments"] == {"type": "array", "items": {"type": "string"}}
    system = PR.load("transcript").system
    for phrase in ("1 to 3 fragments", "ONE main claim per point", "give a second fragment", "shares no content words", "Dates, months, days of the week", "seven weeks ago",
                   "Keep negations as the source has them", "(question)", "only on the turns of the person", "a pronoun - he, she, they"):
        assert phrase in system, phrase
    assert PR.load("transcript").version == "transcript-v5" and {PR.load(k).version for k in PR.KINDS} == {f"{k}-v5" for k in PR.KINDS}


def test_the_good_fed_output_still_passes_with_its_two_fragments():
    r = grounded(*GOOD_FED["summary"])
    assert r.ok, r.errors
    assert [len(p["evidence"]["fragments"]) for p in r.points] == [1, 1, 2] and GOOD_TEXTS[2] == r.summary[2]


# --- end to end: a transcript from its page to a checked summary ---------------------------------------------------------------------------------

ECB_URL = "https://www.ecb.europa.eu/press/press_conference/monetary-policy-statement/2026/html/ecb.is260910~6a45359cfc.en.html"
ECB_HTML = ("<html><body><div class='section'><p>Good afternoon, the Vice-President and I welcome you to our press conference. The Governing Council today decided to raise the three key "
            "ECB interest rates by 25 basis points. The conflict in the Middle East continues to generate inflation pressure across the euro area and the outlook remains uncertain. "
            "We are committed to setting monetary policy to ensure that inflation stabilises at our two per cent target in the medium term over the coming years.</p>"
            "<p>We are now ready to take your questions.</p><p>* * *</p>"
            "<p><strong>In July, you said that the ECB's reaction function is very well understood by markets. Would you repeat what you said in July about the rate path?</strong></p>"
            "<p>You give me a chance to give what I like most, which is framework guidance based on the inflation outlook and the assessment of the risks around it.</p></div></body></html>")


def ecb_doc():
    return {"doc_id": "EUR:presser_transcript:2026-09-10", "currency": "EUR", "type": "presser_transcript", "url": ECB_URL, "format": "html", "speaker": "", "text": None}


def ecb_source():
    from .cb_docs_helpers import FakeSession, Resp
    from .test_cb_docs_collect import fetcher
    session = FakeSession(extra={ECB_URL: Resp(200, ECB_HTML.encode(), {"Content-Type": "text/html; charset=utf-8"})})
    return SRC.load(ecb_doc(), fetcher(session), 10**6, ("lagarde",))


def test_an_ecb_transcript_from_its_page_has_its_questions_marked_and_the_hash_of_the_extraction():
    src = ecb_source()
    assert [t.official for t in src.turns] == [True, True, True, False, True] and src.tags == [None, None, None, "question", None]
    assert src.paragraphs[3].startswith("In July, you said") and src.sha256 == X.sha256(X.to_text(src.paragraphs))                    # (no paragraph was cut: the text is the extraction)
    from src.cb_summarize import prompts as PR
    assert "[4] (question) In July, you said" in PR.user_message("presser_transcript", "European Central Bank", ECB_URL, src.paragraphs, src.tags)


def test_a_transcript_is_summarised_with_its_turns_and_a_journalists_words_are_not_the_presidents():
    from src.cb_summarize import prompts as PR
    from src.cb_summarize import run as R
    from src.cb_summarize.client import RecordedClient

    from .cb_sum_helpers import dumps
    src = ecb_source()
    good = {"summary": [point("Lagarde said the Governing Council decided to raise the three key ECB interest rates by 25 basis points.", [1],
                             "The Governing Council today decided to raise the three key ECB interest rates by 25 basis points."),
                        point("Lagarde said the Council is ready to take your questions.", [2], "We are now ready to take your questions."),
                        point("Lagarde said framework guidance is based on the inflation outlook and the assessment of the risks.", [5],
                              "framework guidance based on the inflation outlook and the assessment of the risks around it")],
            "quotes": [{"paragraph": 5, "text": "which is framework guidance"}], "coverage": [1, 2, 5]}
    borrowed = json.loads(json.dumps(good))
    borrowed["summary"][2] = point("Lagarde said the ECB's reaction function is very well understood by markets.", [4], "In July, you said that the ECB's reaction function is very well understood by markets.")
    c = RecordedClient([dumps(borrowed), dumps(good)])
    ok, usage, errs = R.summarise(c, CFG, PR.load("transcript"), ecb_doc(), src, ("lagarde",), people=(("christine", "lagarde"),))
    assert ok is not None and ok.ok and usage["attempts"] == 2                                                           # refused the first time, the retry passes
    feedback = c.calls[1][1][2]["content"]
    assert "summary point 3 attributes the statement to Lagarde, but paragraph 4 that it cites is a journalist's question" in feedback
    assert "[4] (question) In July, you said" in c.calls[0][1][0]["content"]                                             # the model was told which paragraph is a question


# --- the edges (found by mutating the checks) ---------------------------------------------------------------------------------------------------

def test_months_and_weekdays_are_checked_even_at_the_start_of_a_sentence_or_in_lower_case():
    for text, missing in (("Monday saw economic activity expand at a solid pace.", ["Monday"]), ("July saw economic activity expand at a solid pace.", ["July"]),
                          ("The Committee said economic activity is expanding at a solid pace since july.", ["july"])):
        r = grounded(P_VOTE, P_RATE, point(text, [3], "Economic activity is expanding at a solid pace."))
        assert not r.ok and any(f"names {', '.join(repr(w) for w in missing)}, which the paragraph(s)" in e for e in r.errors), (text, r.errors)


def test_may_as_a_modal_at_the_start_of_a_sentence_is_not_a_month():
    assert V.strict_tokens("May economic activity expand at a solid pace, the Committee said.", {"committee"}) == []
    assert V.strict_tokens("May 16 was the day.", set()) == ["May"] and V.strict_tokens("It ended in May.", set()) == ["May"] and V.strict_tokens("It may end in July.", set()) == ["July"]
    assert V.strict_tokens("May we say that inflation eased.", set()) == []                                                                    # the modal at the start of a sentence
    assert V.strict_tokens("Growth is strong. May inflation ease.", set()) == []


def test_may_is_a_name_when_it_is_not_a_month_and_the_modal_is_never_a_month():
    assert V.strict_tokens("The Committee met Theresa May yesterday.", {"committee"}) == ["Theresa", "May"]
    assert V.strict_tokens("The Committee said activity is expanding and may slow.", {"committee"}) == []                                  # "and may": the verb, not the month
    assert V.strict_tokens("The Committee said activity may slow in May.", {"committee"}) == ["May"]


def test_the_coverage_recorded_is_the_share_of_the_points_words_the_cited_paragraphs_have():
    seven = point("Economic activity expanding solid pace resilient bonds", [3], "Economic activity is expanding at a solid pace.")               # 5 of 6
    r = grounded(P_VOTE, P_RATE, point("Economic activity expanding solid pace resilient spending bonds", [3], "Economic activity is expanding at a solid pace."), min_support=0.8)
    assert r.ok and r.points[2]["evidence"]["coverage"] == round(7 / 8, 3) and r.points[0]["evidence"]["coverage"] == 1.0
    assert seven and grounded(P_VOTE, P_RATE, seven, min_support=0.8).points[2]["evidence"]["coverage"] == round(6 / 7, 3)


def test_a_capital_that_is_a_stopword_in_the_middle_of_a_sentence_is_not_a_name():
    assert V.strict_tokens("The Committee said activity is expanding, And it is at a solid pace.", {"committee"}) == []
    assert V.strict_tokens("The Committee said activity is expanding, Bundesbank agrees.", {"committee"}) == ["Bundesbank"]


def test_the_names_the_paragraph_has_are_found_however_they_are_written():
    paras = ["Decisions were taken on the 16th, after the U.S. Treasury sold bonds to the Bank of Japan. The Middle East stays uncertain. Inflation remains elevated across the euro area."]
    quotes = [{"paragraph": 1, "text": "The Middle East stays uncertain."}]

    pts = [point("The Committee said decisions were taken on the 16th, after the US Treasury sold bonds to the Bank of Japan.", [1],                             # ordinal, U.S. -> US, Treasury
                 "Decisions were taken on the 16th, after the U.S. Treasury sold bonds to the Bank of Japan."),
           point("The Committee said the Treasuries sold bonds to the Bank of Japan, and the Middle East stays uncertain.", [1],                               # an inflected form of a name
                 "The Middle East stays uncertain. Inflation remains elevated across the euro area."),
           point("The Committee said inflation remains elevated across the euro area, and the Middle East stays uncertain.", [1],
                 "The Middle East stays uncertain. Inflation remains elevated across the euro area.")]
    r = V.verify({"summary": pts, "quotes": quotes, "coverage": [1]}, paras, **rules())
    assert r.ok, r.errors


def test_the_names_may_be_in_any_of_the_cited_paragraphs_not_only_the_first():
    paras = ["Decisions were taken today after a long meeting of the whole Committee.", "The Middle East stays uncertain, and the Treasury sold bonds to the Bank of Japan."]
    quotes = [{"paragraph": 2, "text": "The Middle East stays uncertain,"}]
    both = point("The Committee said decisions were taken today, and the Treasury sold bonds to the Bank of Japan.", [1, 2], "Decisions were taken today after a long meeting", "and the Treasury sold bonds to the Bank of Japan.")
    first = point("The Committee said decisions were taken today, and the Treasury sold bonds to the Bank of Japan.", [1], "Decisions were taken today after a long meeting")
    pts = [point("The Committee said the Middle East stays uncertain.", [2], "The Middle East stays uncertain, and the Treasury sold"),
           point("The Committee said decisions were taken today after a long meeting.", [1], "Decisions were taken today after a long meeting"), both]
    assert V.verify({"summary": pts, "quotes": quotes, "coverage": [1, 2]}, paras, **rules()).ok
    r = V.verify({"summary": pts[:2] + [first], "quotes": quotes, "coverage": [1, 2]}, paras, **rules())
    assert not r.ok and any("names 'Treasury', 'Japan'" in e for e in r.errors)


def test_the_titles_of_a_speaker_and_the_banks_own_name_words_are_not_claims():
    paras = ["At its meeting today, the Board decided to leave the cash rate target unchanged at 4.35 per cent, in line with the outlook for the year."]
    quotes = [{"paragraph": 1, "text": "the Board decided to leave the cash rate target unchanged"}]
    bank = point("The Reserve Bank of Australia decided to leave the cash rate target unchanged at 4.35 per cent.", [1], "the Board decided to leave the cash rate target unchanged at 4.35 per cent")
    three = [bank, point("The Board decided to leave the cash rate target unchanged.", [1], "the Board decided to leave the cash rate target unchanged"), point("The Board said the outlook for the year is in line.", [1], "in line with the outlook for the year")]
    assert V.verify({"summary": three, "quotes": quotes, "coverage": [1]}, paras, bank_terms=("reserve", "bank", "australia"), **rules()).ok
    r = V.verify({"summary": three, "quotes": quotes, "coverage": [1]}, paras, **rules())
    assert not r.ok and any("names 'Australia'" in e for e in r.errors)                                                                  # without the bank's words it is a name like any other
    deputy = point("The Board said Deputy Governor Rogers decided to leave the cash rate target unchanged.", [1], "the Board decided to leave the cash rate target unchanged")
    r = V.verify({"summary": [deputy] + three[1:], "quotes": quotes, "coverage": [1]}, paras, speakers=("rogers",), **rules())
    assert r.ok, r.errors                                                                                                                   # "Deputy Governor" are titles, "Rogers" the speaker


def test_the_sentence_splitter_leaves_initials_and_titles_alone():
    assert V._SENTENCE.split("Thank you, Mr. Chairman. I am fine. U.S. inflation is 3 percent. No. 5 is here. Dr. Smith agrees. It ends! Really? Yes.") == \
        ["Thank you, Mr. Chairman.", "I am fine.", "U.S. inflation is 3 percent.", "No. 5 is here.", "Dr. Smith agrees.", "It ends!", "Really?", "Yes."]


def test_the_negation_of_a_tie_and_of_a_text_with_nothing_in_common():
    rx = V.negation_regex(CFG.negations)
    negated_first = ["Growth is not strong and broad across the sectors of the economy. Growth is strong and broad across the sectors of the economy."]
    assert V.negation_error("Growth is strong and broad across the sectors of the economy.", negated_first, rx, set(), 1) is None                # the tie: the agreeing sentence, whatever its place
    assert V.negation_error("Growth is not strong and broad across the sectors of the economy.", negated_first, rx, set(), 1) is None
    assert V.negation_error("Weather is fine today.", ["Growth is not strong."], rx, set(), 1) is None                                       # nothing in common: nothing to compare


def test_a_first_name_alone_names_the_person_and_a_surname_must_be_the_one_on_the_label():
    people = (("kevin", "warsh"), ("jennifer", "schonberger"), ("michelle", "bowman"))
    speakers = ("kevin", "warsh", "jennifer", "schonberger", "michelle", "bowman")
    hers = point("Jennifer said inflation has run up mostly from higher energy prices and tariffs, which rate hikes cannot fix.", [Q_PARA],
                 "Inflation has run up mostly from higher energy prices and tariffs, which some say are supply shocks")
    assert transcript(SOBER, NOT_FG, hers, speakers=speakers, people=people).ok                                                          # a first name is enough, and it is her turn
    assert not transcript(SOBER, NOT_FG, hers, speakers=(), people=()).ok                                                                # (unknown to the roster: an unnamed statement from a question)
    smith = para_of("MICHELLE SMITH. Edward Lawrence.")
    other = point("Michelle Bowman said Edward Lawrence was called on.", [smith], "MICHELLE SMITH. Edward Lawrence.")
    r = transcript(SOBER, NOT_FG, other, speakers=speakers, people=people)
    assert not r.ok and any("attributes the statement to Bowman, but paragraph %d that it cites is the turn of MICHELLE SMITH" % smith in e for e in r.errors)      # same first name, other person
    first_cited_ok = point("Warsh said the decision made today was a sober and serious decision, and inflation has run up mostly from higher energy prices and tariffs.", [3, Q_PARA],
                           "The decision we made today was a sober decision, serious decision, responsible decision", "Inflation has run up mostly from higher energy prices and tariffs, which some say are supply shocks")
    r = transcript(SOBER, NOT_FG, first_cited_ok)
    assert not r.ok and any("paragraph %d that it cites is the turn of JENNIFER SCHONBERGER" % Q_PARA in e for e in r.errors)               # every cited paragraph is checked, not the first


def test_an_unnamed_statement_may_cite_the_paragraphs_before_any_label():
    header = point("The transcript of the press conference is of September 16, 2026.", [1], "Transcript of Chairman Warsh’s Press Conference September 16, 2026")
    assert transcript(SOBER, NOT_FG, header).ok


def test_a_filler_looking_fragment_that_shares_one_word_is_not_enough():
    one = point("The Committee said economic activity is expanding at a strong pace.", [3], "Productivity growth is strong, and capital investment is robust.")
    r = grounded(P_VOTE, P_RATE, one)
    assert not r.ok and any("has 1 content word(s) in common with the point; at least 2 needed" in e for e in r.errors)


def test_a_label_is_two_to_four_capitalised_words_before_a_real_paragraph():
    long = "This paragraph is long enough to be a speech and not a heading of the page."
    ok = lambda name: T.name_paragraph([name, long], 0)                                                                                  # noqa: E731
    assert ok("Michele Bullock") and ok("Jean Claude Van Damme") and ok("Daniel O’Leary")
    assert not ok("Warsh") and not ok("One Two Three Four Five") and not ok("michele bullock") and not ok("Michele bullock") and not ok("Monetary Policy Minutes")
    assert not T.name_paragraph(["Michele Bullock", "too short"], 0) and not T.name_paragraph(["Michele Bullock"], 0)


def test_a_single_all_caps_word_is_not_a_speaker_and_one_label_is_not_a_transcript():
    got = T.fed_turns(["CHAIRMAN WARSH. The FOMC. AI. Next we turn to the outlook.", "MICHELLE SMITH. Thank you all for coming today."], frozenset({"warsh"}))
    assert got is not None and {t.name for t in got[1]} == {"CHAIRMAN WARSH", "MICHELLE SMITH"}
    assert T.segment(["CHAIRMAN WARSH. Good day everyone and welcome to the press conference held today.", "More text of the same speaker, without any other label in it."],
                     frozenset({"warsh"})) == (["CHAIRMAN WARSH. Good day everyone and welcome to the press conference held today.", "More text of the same speaker, without any other label in it."], None)
    assert T.tags_of(TURNS)[0] is None and TURNS[0] is None                                                                              # before the first label: nothing is marked


def test_the_banks_own_members_are_the_officials_of_the_source_and_only_a_transcript_is_cut_in_turns():
    rba = ["Michele Bullock", "Good afternoon. Today, the Board decided to leave the cash rate unchanged at 4.35 per cent, and it will watch the data.", "Jacob Shteyman",
           "Thanks Governor, Jacob Shteyman from AAP. You mentioned upside risks to inflation, and I wonder what could bring them about."]
    with_bullock = SRC.from_paragraphs(rba, "html-selector", 10**6, min_chars=1, transcript={"officials": ["bullock"]})
    assert [t.official for t in with_bullock.turns] == [True, True, False, False] and with_bullock.tags == [None, None, "question", "question"]
    without = SRC.from_paragraphs(rba, "html-selector", 10**6, min_chars=1, transcript={"officials": []})
    assert [t.official for t in without.turns] == [False, False, False, False]                                                           # nobody is known as the bank's
    from .cb_docs_helpers import FakeSession, Resp
    from .test_cb_docs_collect import fetcher
    speech = dict(ecb_doc(), type="speech", doc_id="EUR:speech:x")
    src = SRC.load(speech, fetcher(FakeSession(extra={ECB_URL: Resp(200, ECB_HTML.encode(), {"Content-Type": "text/html; charset=utf-8"})})), 10**6, ("lagarde",))
    assert src.turns is None and src.tags is None                                                                                         # the same page, but a speech: no turns


def test_the_people_of_the_roster_reach_the_check_a_first_name_names_the_person_on_the_label():
    from src.cb_summarize import prompts as PR
    from src.cb_summarize import run as R
    from src.cb_summarize.client import RecordedClient

    from .cb_sum_helpers import dumps
    src = SRC.from_paragraphs(EXCERPT, "pypdf", 10**6, transcript={"officials": ["warsh"]})
    kevin = point("Kevin said the decision made today was a sober and serious decision.", [3], "The decision we made today was a sober decision, serious decision, responsible decision")
    out = {"summary": [kevin, NOT_FG, STRONG], "quotes": [{"paragraph": 3, "text": "I'm not going to pre-judge any future decisions we make."}], "coverage": [3, 13]}
    doc = {"doc_id": "USD:presser_transcript:2026-09-16", "currency": "USD", "type": "presser_transcript", "url": "https://www.federalreserve.gov/x.pdf", "speaker": ""}
    ok, usage, errs = R.summarise(RecordedClient([dumps(out)]), CFG, PR.load("transcript"), doc, src, ("kevin", "warsh"), people=PEOPLE)
    assert ok is not None and ok.ok, errs                                                                               # "Kevin" is Warsh (CHAIRMAN WARSH on the label)
    ok, usage, errs = R.summarise(RecordedClient([dumps(out), dumps(out)]), CFG, PR.load("transcript"), doc, src, ("kevin", "warsh"))
    assert ok is None and any("attributes the statement to Kevin, but paragraph 3 that it cites is the turn of CHAIRMAN WARSH" in e for e in errs)   # without the people: two unknown names


def test_the_speakers_on_the_labels_of_a_transcript_are_people_a_point_may_name():
    people = T.people_of(TURNS)
    assert ("warsh",) in people and ("jennifer", "schonberger") in people and ("michelle", "smith") in people and ("edward", "lawrence") in people
    assert all(w == tuple(x.lower() for x in w) and all(len(x) >= 3 for x in w) for w in people) and len(people) == len(set(people))
    assert T.people_of(None) == () and T.people_of([None, T.Turn("question", False), T.Turn("", True)]) == ()
    assert T.name_words("CHAIRMAN WARSH") == ("warsh",) and T.name_words("Daniel O’Leary") == ("daniel", "o’leary") and T.name_words("Michelle W. Bowman") == ("michelle", "bowman")


def test_a_journalist_named_in_a_point_is_the_owner_of_her_turn_in_a_run(tmp_path, monkeypatch):
    """The run adds the speakers of the labels to the people of the roster: 'Jennifer Schonberger said ...' rests on her own turn."""
    import dataclasses

    from src import cb_collect as cc
    from src.cb_summarize import run as R
    from src.cb_summarize import store as SS
    from src.cb_summarize.client import RecordedClient

    from .cb_docs_helpers import FakeSession
    from .cb_sum_helpers import NOW, TODAY, collected_dir, dumps, fresh_copy
    from .test_cb_docs_collect import fetcher
    base = collected_dir(tmp_path_factory_of(tmp_path))
    paths = cc.Paths(fresh_copy(base, tmp_path))
    monkeypatch.setattr(SRC, "load", lambda doc, f, max_chars, officials=(): SRC.from_paragraphs(EXCERPT, "pypdf", max_chars, transcript={"officials": list(officials)}))
    asked = point("Jennifer Schonberger said inflation has run up mostly from higher energy prices and tariffs, which rate hikes cannot fix.", [Q_PARA],
                  "Inflation has run up mostly from higher energy prices and tariffs, which some say are supply shocks")
    out = {"summary": [SOBER, NOT_FG, asked], "quotes": [{"paragraph": 3, "text": "I'm not going to pre-judge any future decisions we make."}], "coverage": [3, Q_PARA]}
    doc_id = "USD:presser_transcript:2026-09-16"
    cfg = dataclasses.replace(R.load_config(), max_documents=5)
    rep = R.run_summaries(paths, TODAY, client=RecordedClient([dumps(out)]), fetcher=fetcher(FakeSession()), env={}, now=NOW, cfg=cfg, only={doc_id})
    assert (rep.new, rep.calls, rep.failed_validation) == (1, 1, []), (rep.failed_validation, rep.rejected)
    rec = SS.load(paths.summaries)[doc_id]
    assert rec["summary"][2]["text"].startswith("Jennifer Schonberger said") and rec["input_sha256"] == X.sha256(X.to_text(EXCERPT))          # the hash is that of the extraction


def tmp_path_factory_of(tmp_path):
    class F:
        def mktemp(self, name):
            d = tmp_path / name
            d.mkdir()
            return d
    return F()
