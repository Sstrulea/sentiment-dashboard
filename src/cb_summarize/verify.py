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
    ok: bool
    errors: list = field(default_factory=list)
    summary: list = field(default_factory=list)          # the text of each point
    points: list = field(default_factory=list)           # each point with its checked evidence: {"text", "evidence": {paragraphs, fragment, paragraph, start, end, coverage}}
    quotes: list = field(default_factory=list)
    numbers: list = field(default_factory=list)
    coverage: list = field(default_factory=list)


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


def check_attributed(points: list, src: str, words: tuple, subjects: tuple, speakers: tuple = ()) -> list:
    """likely / expect(s) / signal(s) / suggest(s) (`words`): a bank writes them all the time and reporting that is not an opinion, so a summary point may use one
    ONLY when (1) the document itself uses that word - in any of its forms: "I expect" in a speech allows "Waller expects" - and (2) the point attributes it to the
    bank or to a named speaker - one of `subjects` ("The Committee", "The SNB", "the minutes" ...) or a name of `speakers` (the surname, with or without a title:
    "Chair Warsh", "Waller") comes earlier in the same sentence of the point. Never in the summary's own voice."""
    if not words:
        return []
    rx, subj = blocked_regex(words), blocked_regex(tuple(subjects) + tuple(speakers))
    in_source = forms_of_text(src, adverbs=False)
    errors = []
    for i, s in enumerate(points, 1):
        for m in rx.finditer(s):
            w = m.group(0)
            if not (forms(w, adverbs=False) & in_source):
                errors.append(f"the word '{w}' of summary point {i} is not in the document: only a word the document itself uses (in any of its forms) may appear, and then attributed to the bank or to a named speaker")
                continue
            sentence_start = max([s.rfind(c, 0, m.start()) for c in ".!?;:"] + [-1]) + 1
            if not subj.search(s[sentence_start:m.start()]):
                errors.append(f"the word '{w}' of summary point {i} is not attributed to the bank or to a named speaker: write who says it, e.g. 'The Committee {w} ...' (never in your own voice)")
    return errors


def check_evidence(point, i: int, paragraphs: list, starts: list, lim_paras: tuple, lim_words: tuple, min_share: float, drop_forms: set) -> tuple:
    """(evidence record, errors) for one summary point {text, evidence: {paragraphs: [1-3 numbers], fragment: 5-40 words}}: the fragment is verbatim in one of the
    cited paragraphs (its offsets are then derived), and at least `min_share` of the point's content words are found in the cited paragraphs."""
    ev = point.get("evidence") if isinstance(point, dict) else None
    shape = f"summary point {i} needs 'evidence': {{'paragraphs': [{lim_paras[0]}-{lim_paras[1]} paragraph numbers], 'fragment': '<{lim_words[0]}-{lim_words[1]} words copied verbatim from one of them>'}}"
    if not isinstance(ev, dict) or not isinstance(ev.get("paragraphs"), list) or not isinstance(ev.get("fragment"), str):
        return None, [shape]
    cited = ev["paragraphs"]
    if not cited or not all(isinstance(c, int) and not isinstance(c, bool) for c in cited):
        return None, [shape]
    cited = list(dict.fromkeys(cited))
    if not (lim_paras[0] <= len(cited) <= lim_paras[1]):
        return None, [f"summary point {i} cites {len(cited)} paragraphs as evidence; allowed {lim_paras[0]}-{lim_paras[1]}"]
    if any(not (1 <= c <= len(paragraphs)) for c in cited):
        return None, [f"summary point {i} cites a paragraph outside 1-{len(paragraphs)} as evidence"]
    frag = ev["fragment"]
    n_words = len(frag.split())
    if not (lim_words[0] <= n_words <= lim_words[1]):
        return None, [f"the evidence fragment of summary point {i} has {n_words} words; allowed {lim_words[0]}-{lim_words[1]}"]
    errors = []
    where = next((c for c in cited if frag in paragraphs[c - 1]), None)
    if where is None:
        other = next((k + 1 for k, p in enumerate(paragraphs) if frag in p), None)
        hint = (f" (that text is in paragraph {other}: cite it)" if other else " (that text is not in the document verbatim - copy it character for character, curly quotes included)")
        errors.append(f"the evidence fragment of summary point {i} is not verbatim in the paragraph(s) it cites {cited}{hint}")
    share, missing = support(point["text"], [paragraphs[c - 1] for c in cited], drop_forms)
    if share < min_share:
        errors.append(f"summary point {i} is not supported by the paragraph(s) it cites {cited}: {round(share * 100)}% of its content words are there ({round(min_share * 100)}% needed); "
                      f"not found: {', '.join(repr(w) for w in missing[:8])} - say only what those paragraphs say, or cite the paragraphs that say it")
    if errors:
        return None, errors
    start = starts[where - 1] + paragraphs[where - 1].find(frag)
    return {"paragraphs": cited, "fragment": frag, "paragraph": where, "start": start, "end": start + len(frag), "coverage": round(share, 3)}, []


def verify(obj: dict, paragraphs: list, *, points: tuple, point_chars: tuple, quotes: tuple, quote_chars: tuple, total_chars: int, blocked: tuple,
           attributed: tuple = (), subjects: tuple = (), speakers: tuple = (), evidence_paragraphs: tuple = (1, 3), fragment_words: tuple = (5, 40),
           min_support: float = 0.85, attribution_words: tuple = ()) -> Verified:
    """Check a parsed model output against the source paragraphs. Returns the record parts (points with their evidence, quotes with offsets, numbers with source
    offsets, coverage) only when every check passed. `blocked` words are refused always; `attributed` words only unless the document uses them and the point
    attributes them (check_attributed). Every point carries evidence (paragraphs + a verbatim fragment) and must be supported by the paragraphs it cites."""
    errors: list = []
    src = source_text(paragraphs)
    starts = paragraph_starts(paragraphs)
    drop = forms_of_text(" ".join(tuple(attribution_words) + tuple(subjects) + tuple(speakers)))          # attribution vocabulary and names: not claims

    raw = obj.get("summary")
    summary: list = []
    evidence: list = []
    if not isinstance(raw, list) or not all(isinstance(p, dict) and isinstance(p.get("text"), str) for p in raw):
        errors.append("'summary' must be a list of points, each {'text': '...', 'evidence': {'paragraphs': [...], 'fragment': '...'}}")
        raw = []
    else:
        summary = [p["text"] for p in raw]
        if not (points[0] <= len(summary) <= points[1]):
            errors.append(f"'summary' has {len(summary)} points; allowed {points[0]}-{points[1]}")
    for i, s in enumerate(summary, 1):
        if not (point_chars[0] <= len(s.strip()) <= point_chars[1]):
            errors.append(f"summary point {i} has {len(s.strip())} characters; allowed {point_chars[0]}-{point_chars[1]}")
    if sum(len(s) for s in summary) > total_chars:
        errors.append(f"the summary has {sum(len(s) for s in summary)} characters in all; allowed {total_chars}")
    for i, p in enumerate(raw, 1):
        ev, errs = check_evidence(p, i, paragraphs, starts, evidence_paragraphs, fragment_words, min_support, drop)
        errors += errs
        evidence.append(ev)

    rx = blocked_regex(blocked)
    for i, s in enumerate(summary, 1):
        for m in rx.finditer(s):
            errors.append(f"summary point {i} uses the word '{m.group(0)}': no direction, forecast or evaluation - only what the document says")
    errors += check_attributed(summary, src, attributed, subjects, speakers)

    qs = obj.get("quotes")
    checked_quotes = []
    if not isinstance(qs, list):
        errors.append("'quotes' must be a list")
    else:
        if not (quotes[0] <= len(qs) <= quotes[1]):
            errors.append(f"'quotes' has {len(qs)} items; allowed {quotes[0]}-{quotes[1]}")
        for i, q in enumerate(qs, 1):
            rec, errs = check_quote(q, paragraphs, starts, i, quote_chars)
            errors += errs
            if rec:
                checked_quotes.append(rec)

    src_numbers = numbers_of(src)
    found = []
    for i, s in enumerate(summary, 1):
        ev = evidence[i - 1] if i <= len(evidence) else None
        cited_numbers = numbers_of("\n".join(paragraphs[c - 1] for c in ev["paragraphs"])) if ev else None
        for n in numbers_of(s):
            hit = find_number(n, src_numbers)
            if hit is None:
                errors.append(f"the number '{n.text}' of summary point {i} does not appear in the document (state numbers exactly as the document writes them; never convert or compute)")
            elif cited_numbers is not None and find_number(n, cited_numbers) is None:
                errors.append(f"the number '{n.text}' of summary point {i} is in the document but not in the paragraph(s) it cites {ev['paragraphs']}: cite the paragraph that states it")
            else:
                found.append({"point": i, "text": n.text, "source_text": src[hit.start:hit.end].strip(), "start": hit.start, "end": hit.end})

    cov = obj.get("coverage")
    coverage: list = []
    if not isinstance(cov, list) or not cov or not all(isinstance(c, int) and not isinstance(c, bool) for c in cov):
        errors.append("'coverage' must be a non-empty list of paragraph numbers")
    elif any(not (1 <= c <= len(paragraphs)) for c in cov):
        errors.append(f"'coverage' names a paragraph outside 1-{len(paragraphs)}")
    else:
        coverage = sorted(set(cov))

    if errors:
        return Verified(False, errors)
    pts = [{"text": t.strip(), "evidence": ev} for t, ev in zip(summary, evidence)]
    return Verified(True, [], [t.strip() for t in summary], pts, checked_quotes, found, coverage)


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
        if ev and src[ev["start"]:ev["end"]] != ev["fragment"]:
            errs.append(f"point {i}: the source at {ev['start']}-{ev['end']} is not the evidence fragment")
    return errs

