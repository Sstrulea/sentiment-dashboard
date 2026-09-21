"""The automatic check every model output must pass BEFORE anything is written. Pure functions over (output, source): no I/O, no clock.

Nothing here edits a summary. A failed check returns errors; the caller retries once with those errors as feedback and, on a second failure,
writes nothing (a `validation_failed` marker instead).

Checks: valid JSON of the agreed shape; 3-6 points of bounded length; every quote verbatim in the paragraph it names (offsets are then derived from
that paragraph); every number of the summary present in the source (separators, percent signs, fractions, "25 basis points" = "25bp" normalised);
no word of the blocked list in a summary point; coverage indices inside the document."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Optional

# ---- numbers ----------------------------------------------------------------------------------------------------------------------

VULGAR = {"¼": Fraction(1, 4), "½": Fraction(1, 2), "¾": Fraction(3, 4), "⅓": Fraction(1, 3), "⅔": Fraction(2, 3), "⅛": Fraction(1, 8),
          "⅜": Fraction(3, 8), "⅝": Fraction(5, 8), "⅞": Fraction(7, 8)}
UNITS = (("pct", r"%|per\s?cent\b|percent\b"), ("pp", r"percentage\s+points?\b|pp\b"), ("bp", r"basis\s+points?\b|bps\b|bp\b"))
_UNIT_RX = "|".join(f"(?P<u_{k}>{rx})" for k, rx in UNITS)
NUMBER_RX = re.compile(
    r"(?<![\w.])(?:"
    r"(?P<mix>\d+)[\s\-](?P<num>\d+)/(?P<den>\d+)"                       # 3-3/4   3 3/4
    r"|(?P<frac_n>\d+)/(?P<frac_d>\d+)"                                    # 1/4
    r"|(?P<vw>\d+)?(?P<vf>[¼½¾⅓⅔⅛⅜⅝⅞])"                                   # 2¼   ¾
    r"|(?P<neg>-)?(?P<int>\d{1,3}(?:[,   ]\d{3})+|\d+)(?P<dec>\.\d+)?"      # -0.5   1,234   1 234 (no-break space)   3.75
    r")(?![\d])(?:\s?(?:" + _UNIT_RX + r"))?", re.I)
_MINUS = str.maketrans({"−": "-", "–": "-", "‑": "-", "−": "-"})


@dataclass(frozen=True)
class Number:
    text: str
    value: Fraction
    unit: Optional[str]
    start: int
    end: int


def _fraction_of(m: re.Match) -> Fraction:
    if m.group("mix"):
        return Fraction(int(m.group("mix"))) + Fraction(int(m.group("num")), int(m.group("den")))
    if m.group("frac_n"):
        return Fraction(int(m.group("frac_n")), int(m.group("frac_d")))
    if m.group("vf"):
        return Fraction(int(m.group("vw") or 0)) + VULGAR[m.group("vf")]
    digits = re.sub(r"[,   ]", "", m.group("int")) + (m.group("dec") or "")
    v = Fraction(digits)
    return -v if m.group("neg") else v


def numbers_of(text: str) -> list:
    """Every number of `text` with its normalised value and unit (percent / percentage point / basis point), and its span in `text`."""
    text = text.translate(_MINUS)
    out = []
    for m in NUMBER_RX.finditer(text):
        unit = next((k for k, _ in UNITS if m.group(f"u_{k}")), None)
        if m.group("int") and m.group("int").startswith("0") and len(m.group("int")) > 1 and not m.group("dec"):
            continue                                                       # 007, 0123: an identifier, not a quantity
        out.append(Number(m.group(0).strip(), _fraction_of(m), unit, m.start(), m.end()))
    return out


def _compatible(a: Optional[str], b: Optional[str]) -> bool:
    return a == b or a is None or b is None


def find_number(n: Number, source: list) -> Optional[Number]:
    """The first source number with the same value whose unit is the same (or one side has no unit)."""
    return next((s for s in source if s.value == n.value and _compatible(n.unit, s.unit)), None)


# ---- blocked vocabulary -----------------------------------------------------------------------------------------------------------

def blocked_regex(words) -> re.Pattern:
    return re.compile(r"(?<!\w)(?:" + "|".join(r"\s+".join(map(re.escape, w.split())) for w in words) + r")(?!\w)", re.I)


# ---- output parsing -----------------------------------------------------------------------------------------------------------------

def parse_output(text: str) -> tuple:
    """(object, errors). A single surrounding ```json fence is formatting, not content, and is removed; nothing else is touched."""
    t = (text or "").strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", t, flags=re.S)
    if m:
        t = m.group(1)
    try:
        obj = json.loads(t)
    except (ValueError, TypeError) as e:
        return None, [f"the output is not valid JSON ({e})"]
    if not isinstance(obj, dict):
        return None, ["the output must be one JSON object"]
    return obj, []


# ---- words: families (expect / expects / expected) and content words ------------------------------------------------------------------------

STOPWORDS = frozenset("""
a an the and or but nor so yet if then than that this these those there here it its it's he she they them their his her we us our you your i me my
is are was were be been being am do does did done has have had having will would shall should can could may might must
of to in on at by for with from into onto over under about above below between among through during before after against without within upon per via
as while although though because since unless until when where whether which who whom whose what whichever
not no nor only also too very more most much many such other another each both either neither any all some same own
just still even ever again further once however therefore thus hence moreover overall
up down out off
""".split())


def forms(word: str, adverbs: bool = True) -> set:
    """The forms a word may be matched by: itself and its stem candidates (expects / expected / expecting -> expect; signalling -> signal; raised -> raise / rais;
    states -> state / stat). Two words are the same word when their forms meet. No dictionary, no dependency: a few suffix rules, applied to both sides."""
    w = word.lower()
    out = {w}

    def add(x: str) -> None:
        if len(x) >= 3:
            out.add(x)

    if (w.endswith("ies") or w.endswith("ied")) and len(w) > 4:
        add(w[:-3] + "y")
    for suf in ("ing", "ed", "s") + (("ly",) if adverbs else ()):                      # "-ly" for content words only: "likely" must not be "like"; "-es" is "-s" + the final-e rule
        if w.endswith(suf) and not (suf == "s" and w.endswith("ss")):
            base = w[:-len(suf)]
            add(base)
            if len(base) > 3 and base[-1] == base[-2]:
                add(base[:-1])                                                  # signall-ing -> signal
    for x in list(out):
        if x.endswith("e") and len(x) > 3:
            add(x[:-1])                                                         # raise -> rais
    return out


def words_of(text: str) -> list:
    """The alphabetic words of a text, lower-case (numbers are checked elsewhere; hyphenated words split)."""
    return re.findall(r"[^\W\d_]+", text.lower())


def forms_of_text(text: str, adverbs: bool = True) -> set:
    out: set = set()
    for w in words_of(text):
        out |= forms(w, adverbs)
    return out


def content_words(text: str, drop_forms: set) -> list:
    """The words of a point that carry a claim: alphabetic, at least 3 letters, not a stopword, not attribution vocabulary (the Committee / Bank / Board ... states,
    said, noted, reported, a speaker's name)."""
    out = []
    for w in words_of(text):
        if len(w) < 3 or w in STOPWORDS or (forms(w) & drop_forms):
            continue
        out.append(w)
    return out


def support(text: str, cited: list, drop_forms: set) -> tuple:
    """(share of the point's content words found in the cited paragraphs, the words that are not). Words meet by their forms."""
    have = forms_of_text(" ".join(cited))
    words = content_words(text, drop_forms)
    missing = [w for w in dict.fromkeys(words) if not (forms(w) & have)]
    uniq = list(dict.fromkeys(words))
    return (1.0 if not uniq else (len(uniq) - len(missing)) / len(uniq)), missing


# ---- the verification -----------------------------------------------------------------------------------------------------------------

@dataclass
class Verified:
    """The result of a check. `ok`: every check passed. Otherwise `errors` are all the reasons; `points` / `quotes` / `numbers` hold what did pass (each point on its own),
    `dropped_points` / `dropped_quotes` what did not and why, and `fatal` the reasons no removal can cure (the shape of the output, the number of points, ...)."""
    ok: bool
    errors: list = field(default_factory=list)
    summary: list = field(default_factory=list)          # the text of each valid point
    points: list = field(default_factory=list)           # each valid point with its checked evidence: {"text", "evidence": {paragraphs, fragments: [{text, paragraph, start, end}], coverage}}
    quotes: list = field(default_factory=list)
    numbers: list = field(default_factory=list)
    coverage: list = field(default_factory=list)
    dropped_points: list = field(default_factory=list)   # [{"text", "errors"}] - a point that did not pass, with the reasons
    dropped_quotes: list = field(default_factory=list)   # [{"text", "errors"}]
    fatal: list = field(default_factory=list)
    decision: Optional[bool] = None                      # a decision statement: a valid point states the decision on the policy rate (None: not asked)

    def publishable(self, min_points: int) -> bool:
        """The valid part may be published on its own: nothing fatal, at least `min_points` valid points and, for a decision statement, the decision point."""
        return not self.fatal and len(self.points) >= min_points and self.decision is not False


def paragraph_starts(paragraphs: list) -> list:
    """Offset of each paragraph in "\\n".join(paragraphs)."""
    out, pos = [], 0
    for p in paragraphs:
        out.append(pos)
        pos += len(p) + 1
    return out


def source_text(paragraphs: list) -> str:
    return "\n".join(paragraphs)


def check_quote(q, paragraphs: list, starts: list, i: int, lim: tuple) -> tuple:
    """(record, errors) for one quote {paragraph, text}: verbatim inside the named paragraph; start / end are absolute offsets in the source text."""
    if not isinstance(q, dict) or not isinstance(q.get("text"), str) or not isinstance(q.get("paragraph"), int) or isinstance(q.get("paragraph"), bool):
        return None, [f"quote {i} must be an object with an integer 'paragraph' and a string 'text'"]
    text, para = q["text"], q["paragraph"]
    if not (1 <= para <= len(paragraphs)):
        return None, [f"quote {i} names paragraph {para}, but the document has paragraphs 1-{len(paragraphs)}"]
    if not (lim[0] <= len(text) <= lim[1]):
        return None, [f"quote {i} has {len(text)} characters; allowed {lim[0]}-{lim[1]}"]
    if "\n" in text:
        return None, [f"quote {i} spans more than one paragraph; a quote must sit inside one paragraph"]
    body = paragraphs[para - 1]
    at = body.find(text)
    if at < 0:
        where = next((k + 1 for k, p in enumerate(paragraphs) if text in p), None)
        hint = f" (that text is in paragraph {where})" if where else " (that text is not in the document verbatim - copy it character for character, curly quotes included)"
        return None, [f"quote {i} is not verbatim in paragraph {para}{hint}"]
    start = starts[para - 1] + at
    return {"paragraph": para, "text": text, "start": start, "end": start + len(text)}, []


def check_attributed(points: list, src: str, words: tuple, subjects: tuple, speakers: tuple = (), pronouns: tuple = ()) -> list:
    """likely / expect(s) / signal(s) / suggest(s) (`words`): a bank writes them all the time and reporting that is not an opinion, so a summary point may use one
    ONLY when (1) the document itself uses that word - in any of its forms: "I expect" in a speech allows "Waller expects" - and (2) the point attributes it: one of
    `subjects` ("The Committee", "The SNB", "the minutes" ...) or a name of `speakers` (the surname, with or without a title) comes earlier in the same sentence, or
    - a pronoun of `pronouns` (he / she / they) comes earlier in the sentence and the bank or the speaker is named earlier in the same POINT ("Waller says X; he
    expects Y"). Never in the summary's own voice."""
    if not words:
        return []
    rx, subj = blocked_regex(words), blocked_regex(tuple(subjects) + tuple(speakers))
    pron = blocked_regex(pronouns) if pronouns else None
    in_source = forms_of_text(src, adverbs=False)
    errors = []
    for i, s in enumerate(points, 1):
        for m in rx.finditer(s):
            w = m.group(0)
            if not (forms(w, adverbs=False) & in_source):
                errors.append(f"the word '{w}' of summary point {i} is not in the document: only a word the document itself uses (in any of its forms) may appear, and then attributed to the bank or to a named speaker")
                continue
            sentence_start = max([b.end() for b in re.finditer(r"[.!?;:](?=\s|$)", s[:m.start()])] + [0])         # (the point of "3.1%" ends nothing)
            if subj.search(s[sentence_start:m.start()]):
                continue
            if pron is not None and pron.search(s[sentence_start:m.start()]) and subj.search(s[:m.start()]):
                continue
            errors.append(f"the word '{w}' of summary point {i} is not attributed to the bank or to a named speaker: write who says it, e.g. 'The Committee {w} ...' (never in your own voice)")
    return errors


# ---- strict classes: dates, months, days, years and proper names come from the cited paragraphs, never derived ---------------------------------------------

MONTHS = frozenset("january february march april may june july august september october november december".split())
WEEKDAYS = frozenset("monday tuesday wednesday thursday friday saturday sunday".split())
TITLES = frozenset("chair chairman chairwoman chairperson governor president vice deputy senior mr ms mrs dr minister member".split())
CAPITALISED = re.compile(r"(?<![^\W\d_])([A-ZÀ-ÖØ-Ý][^\W\d_]+)")
ACRONYM = re.compile(r"(?<![^\W_])([A-Z]{2,})(?![^\W_])")
ORDINAL = re.compile(r"(?<!\w)(\d{1,2}(?:st|nd|rd|th))(?!\w)", re.I)


def _dots_out(text: str) -> str:
    """An acronym is one word however it is written: "U.S." -> "US"."""
    return re.sub(r"\b(?:[A-Za-z]\.){2,}", lambda m: m.group(0).replace(".", ""), text)


def _starts_sentence(text: str, pos: int) -> bool:
    return re.search(r"(?:^|[.!?:])\s*[\"“‘'(\[]?\s*$", text[:pos]) is not None


def strict_tokens(text: str, exclude: set) -> list:
    """The words of a point that must be in the paragraphs it cites, as written: months and weekdays (in any case; the month of "May" only where it is one), ordinal days
    (16th), acronyms and the capitalised words that are not the first of a sentence (persons, institutions, places) - except the bank and the speakers, whose names
    are the attribution, and words of `exclude`. Years and other numbers are checked as numbers."""
    text = _dots_out(text)
    out: dict = {}
    for m in re.finditer(r"[^\W\d_]+", text):
        low = m.group(0).lower()
        if low == "may":                                                   # the month: "May 16" (elsewhere a capital "May" is a name, and "may" the verb)
            after = re.match(r"\s*(\S+)", text[m.end():])
            if after and after.group(1)[0].isdigit():
                out.setdefault(low, m.group(0))
        elif low in MONTHS or low in WEEKDAYS:
            out.setdefault(low, m.group(0))
    for m in CAPITALISED.finditer(text):
        w = m.group(1)                                                     # (the pattern stops at an apostrophe: "Japan's" is "Japan")
        low = w.lower()
        if (low in MONTHS and low != "may") or low in WEEKDAYS or _starts_sentence(text, m.start()) or (low in STOPWORDS and low != "may") or (forms(w) & exclude):
            continue
        out.setdefault(low, w)
    for m in ACRONYM.finditer(text):
        if not (forms(m.group(1)) & exclude):
            out.setdefault(m.group(1).lower(), m.group(1))
    for m in ORDINAL.finditer(text):
        out.setdefault(m.group(1).lower(), m.group(1))
    return list(out.values())


def strict_missing(text: str, cited: list, exclude: set) -> list:
    have = forms_of_text(_dots_out(" ".join(cited)))
    have_words = {w.lower() for w in re.findall(r"\d{1,2}(?:st|nd|rd|th)", " ".join(cited), re.I)}
    return [w for w in strict_tokens(text, exclude) if not (forms(w) & have) and w.lower() not in have_words]


# ---- negation: a point and its closest source sentence say the same, not the opposite -----------------------------------------------------------------------

_SENTENCE = re.compile(r"(?<!\b[A-Z]\.)(?<!\bMr\.)(?<!\bMrs\.)(?<!\bDr\.)(?<!\bNo\.)(?<=[.!?])\s+(?=[A-Z“\"‘(\[])")


def negation_regex(negations: tuple) -> re.Pattern:
    words = [re.escape(n) for n in negations if n not in ("n't", "n’t")]
    return re.compile(r"(?<!\w)(?:" + "|".join(words) + r")(?!\w)|n['’]t(?!\w)", re.I)


def has_negation(text: str, rx: re.Pattern) -> bool:
    return rx.search(re.sub(r"\bnot only\b", "", text, flags=re.I)) is not None


_CLAUSE = re.compile(r"(?:[;:]\s+|,\s+|\s*[\u2014\u2013]\s*|\s+--\s+)|\s+(?:but|while|whereas|although|who|which)\s+", re.I)


def clauses_of(sentence: str) -> list:
    """The clauses of a sentence: cut at ; : , - dashes and at but / while / whereas / although / who / which."""
    return [c.strip() for c in _CLAUSE.split(sentence) if c and c.strip()]


def negation_error(point: str, cited: list, rx: re.Pattern, drop_forms: set, i: int) -> Optional[str]:
    """None when every clause of the point has a negation exactly when the clause of the cited paragraphs that is closest to it has one. The sentences of the cited
    paragraphs are cut in clauses; the closest clause is the one with the most content words of the point's clause (on a tie, one that agrees is taken). A negation elsewhere in a long sentence is another clause's business."""
    units = [c for p in cited for x in _SENTENCE.split(p) if x.strip() for c in clauses_of(x)]
    if not units:
        return None
    for mine_text in clauses_of(point):
        words = list(dict.fromkeys(content_words(mine_text, drop_forms)))
        if not words:
            continue
        scored = [(sum(1 for w in words if forms(w) & forms_of_text(x)), x) for x in units]
        top = max(n for n, _ in scored)
        if top == 0:                                                       # nothing in common with any clause: nothing to compare
            continue
        best = [x for n, x in scored if n == top]
        mine = has_negation(mine_text, rx)
        if not any(has_negation(x, rx) == mine for x in best):
            return (f"summary point {i} {'has a negation' if mine else 'has none'} but the closest clause of the paragraph(s) it cites {'has none' if mine else 'has one'}: "
                    f"\u201c{best[0][:160]}\u201d (its clause: \u201c{mine_text[:100]}\u201d) - state it as the source does; never turn a statement into its opposite")
    return None


# ---- speakers: in a press-conference transcript a statement rests only on its own speaker's turns ------------------------------------------------------------

def _object_of(text: str, at: int) -> bool:
    """True when the name that starts at `at` follows a preposition ("in response to Colby Smith", "the answer to Smith", "asked by Smith"): the person it names is not the subject."""
    return re.search(r"(?<!\w)(?:to|of|from|by|with|for|about|at)\s+(?:[\w’'-]+\s+)?$", text[:at], re.I) is not None


def named_person(text: str, groups: tuple, verbs: tuple = ()) -> Optional[tuple]:
    """(the name words of the person a point attributes the statement to, the word that names them). The person is the one named just before the first reporting verb of
    `verbs` ("In response to Colby Smith, Chairman Warsh said ..." is Warsh's; "Warsh said Smith asked ..." is Warsh's) - at most two words between the name and the
    verb; without one, the first person named. A person is named by their surname (the last word of the group) - by another name word only when the surname is not named."""
    occ = [(m.start(), m.end(), tuple(g), w) for g in groups for w in g for m in re.finditer(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text, re.I)]
    if not occ:
        return None
    if verbs:
        for vm in sorted(blocked_regex(verbs).finditer(text), key=lambda m: m.start()):
            before = [o for o in occ if o[1] <= vm.start() and len(re.findall(r"\w+", text[o[1]:vm.start()])) <= 2 and not _object_of(text, o[0])]
            if before:
                who = max(before, key=lambda o: o[1])[2]                                    # the group of the name closest to the verb
                mine = [o for o in before if o[2] == who]
                best = next((o for o in mine if o[3] == who[-1]), None) or max(mine, key=lambda o: o[1])
                return who, best[3]
    by_surname = [o for o in occ if o[3] == o[2][-1]]
    best = min(by_surname or occ, key=lambda o: o[0])
    return best[2], best[3]


def speaker_errors(point: str, cited: list, labels: list, groups: tuple, i: int, verbs: tuple = ()) -> list:
    """`labels`: one turns.Turn (or None) per paragraph. A point that names a speaker may cite only that speaker's turns - the label must carry the surname the point
    names (or, when the point names a first name only, one of the person's names); a point that names none may cite only the bank's own turns (never a journalist's
    question, presented as the bank's word)."""
    named = named_person(point, groups, verbs)
    errors = []
    for c in cited:
        turn = labels[c - 1]
        if named is not None:
            who, word = named
            matches = turn is not None and ((word in turn.words) if word == who[-1] else bool(set(who) & turn.words))
            if not (matches or (turn is not None and turn.name == "")):                          # (the ECB's answers carry no label)
                by = "no speaker" if turn is None else ("a journalist's question" if turn.name == "question" else f"the turn of {turn.name}")
                errors.append(f"summary point {i} attributes the statement to {who[-1].title()}, but paragraph {c} that it cites is {by}: a statement rests only on "
                              f"the paragraphs of its own speaker's turns")
        elif turn is not None and not turn.official:
            errors.append(f"summary point {i} cites paragraph {c}, {'a journalist' if turn.name == 'question' else 'the turn of ' + turn.name}, for a statement that names no speaker: "
                          f"name the speaker of the paragraphs it cites, or cite the bank's own turns")
    return errors


def check_evidence(point, i: int, paragraphs: list, starts: list, lim_paras: tuple, lim_frags: tuple, lim_words: tuple, min_share: float, drop_forms: set, *,
                   shared: int = 2, exclude_names: frozenset = frozenset(), rx_neg: Optional[re.Pattern] = None, labels: Optional[list] = None, groups: tuple = (), verbs: tuple = (),
                   allowed_rx: Optional[re.Pattern] = None) -> tuple:
    """(evidence record, errors) for one summary point {text, evidence: {paragraphs: [1-3 numbers], fragments: [1-3 verbatim passages of 5-40 words]}}: every
    fragment is verbatim in one of the cited paragraphs (its offsets are then derived) and shares content words with the point; at least `min_share` of the point's
    content words are in the cited paragraphs; its dates, months, days and proper names are there too; its negation matches its closest source sentence; and in a
    transcript it rests on its own speaker's turns."""
    ev = point.get("evidence") if isinstance(point, dict) else None
    shape = (f"summary point {i} needs 'evidence': {{'paragraphs': [{lim_paras[0]}-{lim_paras[1]} paragraph numbers], "
             f"'fragments': [{lim_frags[0]}-{lim_frags[1]} passages of {lim_words[0]}-{lim_words[1]} words copied verbatim from those paragraphs]}}")
    if not isinstance(ev, dict) or not isinstance(ev.get("paragraphs"), list) or not isinstance(ev.get("fragments"), list) or not all(isinstance(f, str) for f in ev["fragments"]):
        return None, [shape]
    cited = ev["paragraphs"]
    if not cited or not all(isinstance(c, int) and not isinstance(c, bool) for c in cited):
        return None, [shape]
    cited = list(dict.fromkeys(cited))
    if not (lim_paras[0] <= len(cited) <= lim_paras[1]):
        return None, [f"summary point {i} cites {len(cited)} paragraphs as evidence; allowed {lim_paras[0]}-{lim_paras[1]}: keep the {lim_paras[1]} that carry the claim, or split the point in two"]
    if any(not (1 <= c <= len(paragraphs)) for c in cited):
        return None, [f"summary point {i} cites a paragraph outside 1-{len(paragraphs)} as evidence"]
    frags = list(dict.fromkeys(ev["fragments"]))
    if not (lim_frags[0] <= len(frags) <= lim_frags[1]):
        return None, [f"summary point {i} has {len(frags)} evidence fragments; allowed {lim_frags[0]}-{lim_frags[1]}: keep the {lim_frags[1]} that state its claims, or split the point in two"]
    errors, found = [], []
    said = point["text"] if allowed_rx is None else allowed_rx.sub(" ", point["text"])                                # ("the Monetary Policy Committee": the bank's own body is a name, not a claim)
    point_words = {w for w in content_words(said, drop_forms)}
    for n, frag in enumerate(frags, 1):
        n_words = len(frag.split())
        if not (lim_words[0] <= n_words <= lim_words[1]):
            errors.append(f"evidence fragment {n} of summary point {i} has {n_words} words; allowed {lim_words[0]}-{lim_words[1]}: \u201c{frag[:90]}\u201d - "
                          + ("extend it with the words next to it in the same sentence: a fragment is a whole clause" if n_words < lim_words[0] else "cut it to the clause that states the claim"))
            continue
        where = next((c for c in cited if frag in paragraphs[c - 1]), None)
        if where is None:
            other = next((k + 1 for k, p in enumerate(paragraphs) if frag in p), None)
            hint = (f" (that text is in paragraph {other}: cite it)" if other else " (that text is not in the document verbatim - copy it character for character, curly quotes included)")
            errors.append(f"evidence fragment {n} of summary point {i} is not verbatim in the paragraph(s) it cites {cited}{hint}")
            continue
        common = {w for w in point_words if forms(w) & forms_of_text(frag)}
        if len(common) < min(shared, len(point_words)):
            errors.append(f"evidence fragment {n} of summary point {i} has {len(common)} content word(s) in common with the point; at least {min(shared, len(point_words))} needed: "
                          f"a fragment must be the passage that states what the point says")
            continue
        start = starts[where - 1] + paragraphs[where - 1].find(frag)
        found.append({"text": frag, "paragraph": where, "start": start, "end": start + len(frag)})
    texts = [paragraphs[c - 1] for c in cited]
    share, missing = support(said, texts, drop_forms)
    if share < min_share:
        errors.append(f"summary point {i} is not supported by the paragraph(s) it cites {cited}: {round(share * 100)}% of its content words are there ({round(min_share * 100)}% needed); "
                      f"not found: {', '.join(repr(w) for w in missing[:8])} - say only what those paragraphs say, or cite the paragraphs that say it")
    named = strict_missing(said, texts, exclude_names)
    if named:
        errors.append(f"summary point {i} names {', '.join(repr(w) for w in named[:8])}, which the paragraph(s) it cites {cited} do not contain: dates, months, days and proper names "
                      f"(persons, institutions, places) must come from the cited paragraphs as written - never derive or infer them")
    if rx_neg is not None:
        neg = negation_error(point["text"], texts, rx_neg, drop_forms, i)
        if neg:
            errors.append(neg)
    if labels is not None:
        errors += speaker_errors(point["text"], cited, labels, groups, i, verbs)
    if errors:
        return None, errors
    return {"paragraphs": cited, "fragments": found, "coverage": round(share, 3)}, []


def point_of(error: str) -> Optional[int]:
    """The summary point an error is about (every error of a point says "summary point N"); None for the errors that are about the output as a whole."""
    m = re.search(r"summary point (\d+)", error)
    return int(m.group(1)) if m else None


def quote_of(error: str) -> Optional[int]:
    m = re.match(r"quote (\d+)", error)
    return int(m.group(1)) if m else None


def decision_regex(terms: tuple) -> re.Pattern:
    return blocked_regex(tuple(terms))


def states_decision(text: str, rate_rx: re.Pattern, action_forms: set) -> bool:
    """A point states the decision on the policy rate: it names the rate (the target range, the cash rate, Bank Rate ...) and an action on it (decided, raised, maintained ...)."""
    return rate_rx.search(text) is not None and bool(forms_of_text(text, adverbs=False) & action_forms)


def verify(obj: dict, paragraphs: list, *, points: tuple, point_chars: tuple, quotes: tuple, quote_chars: tuple, total_chars: int, blocked: tuple,
           attributed: tuple = (), subjects: tuple = (), speakers: tuple = (), evidence_paragraphs: tuple = (1, 3), fragments: tuple = (1, 3), fragment_words: tuple = (5, 40),
           min_support: float = 0.85, attribution_words: tuple = (), fragment_shared: int = 2, pronouns: tuple = (), negations: tuple = (), bank_terms: tuple = (),
           labels: Optional[list] = None, people: tuple = (), speaker_verbs: tuple = (), allowed_names: tuple = (), decision: Optional[dict] = None) -> Verified:
    """Check a parsed model output against the source paragraphs. Every point and every quote is checked on its own; the result says which passed (with their record
    parts: evidence, offsets, numbers, coverage) and which did not (with the reasons), so that the caller may publish the valid part. `blocked` words are refused always;
    `attributed` words only unless the document uses them and the point attributes them (check_attributed). Every point carries evidence (paragraphs + 1-3 verbatim
    fragments) and must be supported by the paragraphs it cites: content words, dates / months / days / proper names, negation and - in a transcript (`labels`, one
    turns.Turn or None per paragraph) - the speaker's own turns. `people`: the name words of each speaker; `bank_terms`: the words that name the bank (and the speaker's
    title): attribution, not claims; `allowed_names`: names that need not be in the cited paragraphs (the bank's bodies). `decision` ({rate_terms, action_terms}): a decision
    statement must state the decision on the policy rate in one point."""
    errors: list = []
    fatal: list = []
    src = source_text(paragraphs)
    starts = paragraph_starts(paragraphs)
    drop = forms_of_text(" ".join(tuple(attribution_words) + tuple(speaker_verbs) + tuple(subjects) + tuple(speakers) + tuple(bank_terms)))    # attribution vocabulary and names: not claims
    exclude_names = frozenset(drop | forms_of_text(" ".join(TITLES)))
    allowed_rx = blocked_regex(tuple(allowed_names)) if allowed_names else None
    groups = tuple(people) or tuple((w,) for w in speakers if w.lower() not in TITLES)
    rx_neg = negation_regex(negations) if negations else None

    raw = obj.get("summary")
    summary: list = []
    evidence: list = []
    if not isinstance(raw, list) or not all(isinstance(p, dict) and isinstance(p.get("text"), str) for p in raw):
        e = "'summary' must be a list of points, each {'text': '...', 'evidence': {'paragraphs': [...], 'fragments': ['...']}}"
        errors.append(e)
        fatal.append(e)
        raw = []
    else:
        summary = [p["text"] for p in raw]
        if not (points[0] <= len(summary) <= points[1]):
            e = f"'summary' has {len(summary)} points; allowed {points[0]}-{points[1]}"
            errors.append(e)
            fatal.append(e)
    for i, s_ in enumerate(summary, 1):
        if not (point_chars[0] <= len(s_.strip()) <= point_chars[1]):
            errors.append(f"summary point {i} has {len(s_.strip())} characters; allowed {point_chars[0]}-{point_chars[1]}")
    for i, p in enumerate(raw, 1):
        ev, errs = check_evidence(p, i, paragraphs, starts, evidence_paragraphs, fragments, fragment_words, min_support, drop, shared=fragment_shared,
                                  exclude_names=exclude_names, rx_neg=rx_neg, labels=labels, groups=groups, verbs=tuple(speaker_verbs) + tuple(attribution_words) + tuple(attributed), allowed_rx=allowed_rx)
        errors += errs
        evidence.append(ev)

    rx = blocked_regex(blocked)
    for i, s_ in enumerate(summary, 1):
        for m in rx.finditer(s_):
            errors.append(f"summary point {i} uses the word '{m.group(0)}': no direction, forecast or evaluation - only what the document says")
    errors += check_attributed(summary, src, attributed, subjects, speakers, pronouns)

    qs = obj.get("quotes")
    checked_quotes: list = []
    dropped_quotes: list = []
    if not isinstance(qs, list):
        e = "'quotes' must be a list"
        errors.append(e)
        fatal.append(e)
    else:
        for i, q in enumerate(qs, 1):
            rec, errs = check_quote(q, paragraphs, starts, i, quote_chars)
            errors += errs
            if rec:
                checked_quotes.append(rec)
            else:
                dropped_quotes.append({"text": q.get("text") if isinstance(q, dict) and isinstance(q.get("text"), str) else str(q)[:200], "errors": errs})
        if not (quotes[0] <= len(qs) <= quotes[1]):
            e = f"'quotes' has {len(qs)} items; allowed {quotes[0]}-{quotes[1]}"
            errors.append(e)
            if len(qs) < quotes[0] or len(checked_quotes) > quotes[1]:
                fatal.append(e)                                                    # no quote at all, or too many valid ones: removing the invalid ones does not cure it

    src_numbers = numbers_of(src)
    found = []
    for i, s_ in enumerate(summary, 1):
        ev = evidence[i - 1] if i <= len(evidence) else None
        cited_numbers = numbers_of("\n".join(paragraphs[c - 1] for c in ev["paragraphs"])) if ev else None
        for n in numbers_of(s_):
            hit = find_number(n, src_numbers)
            if hit is None:
                errors.append(f"the number '{n.text}' of summary point {i} does not appear in the document (state numbers exactly as the document writes them; never convert or compute)")
            elif cited_numbers is not None and find_number(n, cited_numbers) is None:
                errors.append(f"the number '{n.text}' of summary point {i} is in the document but not in the paragraph(s) it cites {ev['paragraphs']}: cite the paragraph that states it")
            else:
                found.append({"point": i, "text": n.text, "source_text": src[hit.start:hit.end].strip(), "start": hit.start, "end": hit.end})

    bad = {point_of(e) for e in errors if point_of(e) is not None}
    keep = [i for i in range(1, len(summary) + 1) if i not in bad and i <= len(evidence) and evidence[i - 1] is not None]
    dropped_points = [{"text": summary[i - 1].strip(), "errors": [e for e in errors if point_of(e) == i]} for i in range(1, len(summary) + 1) if i not in keep]
    if sum(len(summary[i - 1]) for i in keep) > total_chars:
        e = f"the summary has {sum(len(summary[i - 1]) for i in keep)} characters in all; allowed {total_chars}"
        errors.append(e)
        fatal.append(e)
    elif sum(len(x) for x in summary) > total_chars:
        errors.append(f"the summary has {sum(len(x) for x in summary)} characters in all; allowed {total_chars}")

    states = None
    if decision:
        rate_rx, action = decision_regex(decision["rate_terms"]), forms_of_text(" ".join(decision["action_terms"]), adverbs=False)
        if not any(states_decision(t, rate_rx, action) for t in summary):
            errors.append("a decision statement summary must state the decision on the policy rate in one point: what was decided about the rate (the target range, the cash rate, Bank Rate ...) and the level")
        states = any(states_decision(summary[i - 1], rate_rx, action) for i in keep)

    cov = obj.get("coverage")
    coverage: list = []
    if not isinstance(cov, list) or not cov or not all(isinstance(c, int) and not isinstance(c, bool) for c in cov):
        errors.append("'coverage' must be a non-empty list of paragraph numbers")
    elif any(not (1 <= c <= len(paragraphs)) for c in cov):
        errors.append(f"'coverage' names a paragraph outside 1-{len(paragraphs)}")
    else:
        coverage = sorted(set(cov))

    new_index = {i: n for n, i in enumerate(keep, 1)}
    pts = [{"text": summary[i - 1].strip(), "evidence": evidence[i - 1]} for i in keep]
    numbers = [dict(n, point=new_index[n["point"]]) for n in found if n["point"] in new_index]
    if errors:
        coverage = sorted({c for i in keep for c in evidence[i - 1]["paragraphs"]} | {q["paragraph"] for q in checked_quotes})        # what is published is what it draws on
    return Verified(not errors, errors, [p["text"] for p in pts], pts, checked_quotes, numbers, coverage, dropped_points, dropped_quotes, fatal, states)


def verify_stored(record: dict, paragraphs: list) -> list:
    """Integrity of a STORED summary against its source: every quote and number offset slices back to the recorded text. [] = intact."""
    src = source_text(paragraphs)
    errs = []
    for i, q in enumerate(record.get("quotes", []), 1):
        if src[q["start"]:q["end"]] != q["text"]:
            errs.append(f"quote {i}: the source at {q['start']}-{q['end']} is not the recorded text")
    for i, n in enumerate(record.get("numbers", []), 1):
        if src[n["start"]:n["end"]].strip() != n["source_text"]:
            errs.append(f"number {i}: the source at {n['start']}-{n['end']} is not '{n['source_text']}'")
    for i, p in enumerate(record.get("summary", []), 1):
        ev = p.get("evidence") if isinstance(p, dict) else None
        for f in ((ev.get("fragments") or [ev]) if ev else []):                     # (a summary of prompt v4 has one "fragment" with its offsets in the evidence itself)
            text = f.get("text", f.get("fragment"))
            if text is not None and src[f["start"]:f["end"]] != text:
                errs.append(f"point {i}: the source at {f['start']}-{f['end']} is not the evidence fragment")
    return errs

