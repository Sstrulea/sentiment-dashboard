"""Speaker turns of a press-conference transcript. A statement attributed to a speaker may rest only on that speaker's own words, never on a journalist's question or on
someone else's answer, so the verifier needs to know who says each paragraph. The source says it in one of three ways, all read here without a model:

    Fed (PDF text)      "CHAIRMAN WARSH. Good day. ... MICHELLE SMITH. Edward Lawrence. EDWARD LAWRENCE. Thank you ..." - an all-caps label inside the running text
    RBA (HTML)          a paragraph that holds only a name ("Michele Bullock", "Jacob Shteyman"), then that person's paragraphs
    ECB (HTML)          no labels: the questions are the paragraphs set in bold, the answers and the introductory statement are not

`segment` returns the paragraphs to send (the Fed's are cut at every label, so a paragraph is never two speakers' words; page furniture is dropped) and one `Turn` per
paragraph (None: before the first label). Nothing else about the document changes: the hash phase 2a stores is that of the text as extracted."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Turn:
    name: str            # the label as the source writes it ("CHAIRMAN WARSH", "Jacob Shteyman"); "" for the ECB's unlabelled answers
    official: bool       # a member of the bank (or, for the ECB, an answer); False: a journalist, the moderator

    @property
    def words(self) -> set:
        return {w.lower().strip(".,’'") for w in re.findall(r"[^\W\d_][\w’'\-]*", self.name)}


TITLE_WORDS = frozenset("""chair chairman chairwoman chairperson governor president vice deputy senior mr ms mrs dr minister member""".split())
NOT_A_NAME = frozenset("monetary policy statement minutes board media conference decision audio bank reserve press release".split())     # headings that look like a name

FED_LABEL = re.compile(r"(?:^|(?<=[.?!”\"’)] ))([A-Z][A-Z’'\-]+(?: [A-Z][A-Z’'\-.]+){1,3})\. ")
PAGE_NOISE = re.compile(r"^Page \d+ of \d+$")


def name_words(name: str) -> tuple:
    """The words of a person's name, lower case, in the order written (the surname is the last); initials and titles are left out."""
    return tuple(w for w in (x.lower().strip(".,’'") for x in re.findall(r"[^\W\d_][\w’'\-]*", name)) if len(w) >= 3 and w not in TITLE_WORDS)


def people_of(turns: Optional[list]) -> tuple:
    """The people who speak in a transcript, from its labels - each as name words, surname last: a journalist a point names is the owner of the turns under their name."""
    return tuple(dict.fromkeys(w for w in (name_words(t.name) for t in (turns or []) if t is not None and t.name != "question") if w))


def is_official(name: str, officials: frozenset) -> bool:
    words = {w.lower().strip(".,’'") for w in re.findall(r"[^\W\d_][\w’'\-]*", name)}
    return bool(words & officials) or bool(words & TITLE_WORDS)


def fed_turns(paragraphs: list, officials: frozenset) -> Optional[tuple]:
    """Cut each paragraph at the all-caps labels; the words before a label continue the previous speaker. None when the text has fewer than two labels."""
    running = Counter(p for p in paragraphs if len(p) < 90)
    furniture = {p for p, n in running.items() if n >= 3} | {p for p in paragraphs if PAGE_NOISE.match(p)}          # the page header repeats on every page
    out, turns, cur = [], [], None
    for p in paragraphs:
        if p in furniture:
            continue
        pos = 0
        for m in FED_LABEL.finditer(p):
            if m.start() > pos and p[pos:m.start()].strip():
                out.append(p[pos:m.start()].strip())
                turns.append(cur)
            cur = Turn(m.group(1), is_official(m.group(1), officials))
            pos = m.start()
        if p[pos:].strip():
            out.append(p[pos:].strip())
            turns.append(cur)
    return (out, turns) if len({t.name for t in turns if t}) >= 2 else None


def name_paragraph(paragraphs: list, i: int) -> bool:
    p = paragraphs[i]
    words = p.split()
    return (2 <= len(words) <= 4 and all(re.fullmatch(r"[A-Z][\w’'\-.]*", w) for w in words) and not ({w.lower() for w in words} & NOT_A_NAME)
            and i + 1 < len(paragraphs) and len(paragraphs[i + 1]) >= 40)


def labelled_paragraphs(paragraphs: list, officials: frozenset) -> Optional[tuple]:
    """A paragraph that holds only a person's name labels the paragraphs that follow it, up to the next label."""
    out, turns, cur = [], [], None
    for i, p in enumerate(paragraphs):
        if name_paragraph(paragraphs, i):
            cur = Turn(p, is_official(p, officials))
        out.append(p)
        turns.append(cur)
    return (out, turns) if len({t.name for t in turns if t}) >= 2 else None


def bold_turns(paragraphs: list, bold: set) -> Optional[tuple]:
    """No labels: a paragraph set in bold is a journalist's question, the others are the bank's (introductory statement and answers)."""
    turns = [Turn("question", False) if p in bold else Turn("", True) for p in paragraphs]
    return (list(paragraphs), turns) if any(not t.official for t in turns) else None


def segment(paragraphs: list, officials: frozenset = frozenset(), bold: Optional[set] = None) -> tuple:
    """(paragraphs, turns): `turns` aligned with `paragraphs`, or None when the transcript shows no speakers."""
    for got in ((bold_turns(paragraphs, bold) if bold else None), fed_turns(paragraphs, officials), labelled_paragraphs(paragraphs, officials)):
        if got:
            return got
    return list(paragraphs), None


def tags_of(turns: Optional[list]) -> Optional[list]:
    """What the model is told about a paragraph besides its text: a journalist's question is marked, so it is not summarised as the bank's word."""
    return None if turns is None else ["question" if t is not None and not t.official else None for t in turns]
