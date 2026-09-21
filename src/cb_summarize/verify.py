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


# ---- the verification -----------------------------------------------------------------------------------------------------------------

@dataclass
class Verified:
    ok: bool
    errors: list = field(default_factory=list)
    summary: list = field(default_factory=list)
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


def check_attributed(points: list, src: str, words: tuple, subjects: tuple) -> list:
    """likely / expects / signals / suggests (`words`): a bank writes them all the time and reporting that is not an opinion, so a summary point may use one
    ONLY when (1) the document itself uses that same word and (2) the point attributes it to the bank - one of `subjects` ("The Committee", "The SNB", "the
    minutes" ...) comes earlier in the same sentence of the point ("The Committee expects ..."). Never in the summary's own voice."""
    if not words:
        return []
    rx, subj = blocked_regex(words), blocked_regex(subjects)
    in_source = {m.group(0).lower() for m in rx.finditer(src)}
    errors = []
    for i, s in enumerate(points, 1):
        for m in rx.finditer(s):
            w = m.group(0)
            if w.lower() not in in_source:
                errors.append(f"the word '{w}' of summary point {i} is not in the document: only a word the document itself uses may appear, and then attributed to the bank")
                continue
            sentence_start = max([s.rfind(c, 0, m.start()) for c in ".!?;:"] + [-1]) + 1
            if not subj.search(s[sentence_start:m.start()]):
                errors.append(f"the word '{w}' of summary point {i} is not attributed to the bank: write who says it, e.g. 'The Committee {w} ...' (never in your own voice)")
    return errors


def verify(obj: dict, paragraphs: list, *, points: tuple, point_chars: tuple, quotes: tuple, quote_chars: tuple, total_chars: int, blocked: tuple,
           attributed: tuple = (), subjects: tuple = ()) -> Verified:
    """Check a parsed model output against the source paragraphs. Returns the record parts (summary, quotes with offsets, numbers with source offsets,
    coverage) only when every check passed. `blocked` words are refused always; `attributed` words only unless the document uses them and the point
    attributes them (check_attributed)."""
    errors: list = []
    src = source_text(paragraphs)
    starts = paragraph_starts(paragraphs)

    summary = obj.get("summary")
    if not isinstance(summary, list) or not all(isinstance(s, str) for s in summary):
        errors.append("'summary' must be a list of strings")
        summary = []
    elif not (points[0] <= len(summary) <= points[1]):
        errors.append(f"'summary' has {len(summary)} points; allowed {points[0]}-{points[1]}")
    for i, s in enumerate(summary, 1):
        if not (point_chars[0] <= len(s.strip()) <= point_chars[1]):
            errors.append(f"summary point {i} has {len(s.strip())} characters; allowed {point_chars[0]}-{point_chars[1]}")
    if sum(len(s) for s in summary) > total_chars:
        errors.append(f"the summary has {sum(len(s) for s in summary)} characters in all; allowed {total_chars}")

    rx = blocked_regex(blocked)
    for i, s in enumerate(summary, 1):
        for m in rx.finditer(s):
            errors.append(f"summary point {i} uses the word '{m.group(0)}': no direction, forecast or evaluation - only what the document says")
    errors += check_attributed(summary, src, attributed, subjects)

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
        for n in numbers_of(s):
            hit = find_number(n, src_numbers)
            if hit is None:
                errors.append(f"the number '{n.text}' of summary point {i} does not appear in the document (state numbers exactly as the document writes them; never convert or compute)")
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
    return Verified(True, [], [s.strip() for s in summary], checked_quotes, found, coverage)


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
    return errs

