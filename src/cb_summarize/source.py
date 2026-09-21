"""The text a summary is made of. A decision statement / introductory statement is already committed (data/cb/documents); every other document (press
conference transcript, minutes, account, summary of opinions, deliberations, speech) is downloaded when its turn comes, summarised and NOT committed.
Extraction is the deterministic phase-2a extraction (fixed container per bank, pypdf for PDF), so `input_sha256` is the same hash phase 2a stores."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from ..cb_docs import extract as X
from ..cb_docs import sources as S
from ..cb_docs.http import Fetcher
from . import turns as T

MIN_CHARS = 600                                                           # anything shorter is a page without the document (a media advisory, a wrong container)

GENERIC = [                                                                # tried in this order when no rule is known for a (bank, type); the first with enough text wins
    {"sel": ("div", "content", None)}, {"sel": ("div", "output", None)}, {"sel": ("div", None, "post-content")}, {"sel": ("div", None, "cfct-mod-content")},
    {"sel": ("div", None, "section")}, {"sel": ("div", "article", None), "loose": True}, {"sel": ("div", None, "a-text")}, {"sel": ("main", None, None)},
    {"sel": ("article", None, None)},
]
RULES = {                                                                  # (currency, type) -> extraction rule, where 0B / phase 2a fixed one
    ("AUD", "presser_transcript"): {"sel": ("div", "content", None)},
    ("CAD", "deliberations"): {"sel": ("div", None, "cfct-mod-content")},
    ("EUR", "presser_transcript"): {"sel": ("div", None, "section")},
    ("EUR", "account"): {"sel": ("div", None, "section")},
    ("USD", "speech"): {"sel": ("div", "article", None), "loose": True},
    ("USD", "testimony"): {"sel": ("div", "article", None), "loose": True},
    ("CHF", "speech"): {"sel": ("div", None, "a-text")},
    ("EUR", "speech"): {"sel": ("div", None, "section")},
}


class SourceError(Exception):
    """kind "fetch": the page could not be got (robots, a block, HTTP, network) - transient, tried again on the next run. kind "no_text": the page was
    got but holds no extractable text (no known container, too little text, a scanned PDF) - marked once and not tried again (see run.py)."""

    def __init__(self, message: str, kind: str = "fetch") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class Source:
    paragraphs: list                     # the paragraphs sent to the model (a prefix of the document when it was too long)
    text: str                            # "\n".join(all paragraphs of the document)
    sha256: str                          # of the full text
    method: str
    truncated: bool
    chars_total: int
    turns: Optional[list] = None         # a press-conference transcript: the speaker of each paragraph (turns.Turn or None), aligned with `paragraphs`; None: no speakers known
    tags: Optional[list] = None          # what the model is told about a paragraph besides its text ("question"), aligned with `paragraphs`

    @property
    def chars_sent(self) -> int:
        return sum(len(p) + 1 for p in self.paragraphs)


def rule_for(doc: dict) -> Optional[dict]:
    ccy, typ = doc["currency"], doc["type"]
    if doc.get("meeting_date"):
        for t, _url, _due, rule in S.minutes_urls(ccy, doc["meeting_date"]):
            if t == typ and rule:
                return rule
    return RULES.get((ccy, typ))


def cut(paragraphs: list, max_chars: int) -> tuple:
    """(paragraphs kept, truncated): whole paragraphs from the start while the total fits; at least the first one."""
    if sum(len(p) + 1 for p in paragraphs) <= max_chars:
        return paragraphs, False
    kept, used = [], 0
    for p in paragraphs:
        if kept and used + len(p) + 1 > max_chars:
            break
        kept.append(p)
        used += len(p) + 1
    return kept, True


def from_paragraphs(paragraphs: list, method: str, max_chars: int, min_chars: int = MIN_CHARS, transcript: Optional[dict] = None) -> Source:
    """`transcript` ({"officials": surnames of the bank's members, "bold": the bold paragraphs}) turns the source into speaker turns (a press-conference transcript)."""
    paragraphs = [p for p in paragraphs if p.strip()]                          # the text is kept exactly as extracted (its sha is phase 2a's)
    text = X.to_text(paragraphs)
    sha = X.sha256(text)
    if len(text) < min_chars:
        raise SourceError(f"only {len(text)} characters extracted: the container was not found or the page has no text", kind="no_text")
    turns = None
    if transcript is not None:
        paragraphs, turns = T.segment(paragraphs, frozenset(transcript.get("officials") or ()), transcript.get("bold"))
        text = X.to_text(paragraphs)                                           # the paragraphs sent are the turns; the hash stays that of the extraction
    sent, truncated = cut(paragraphs, max_chars)
    kept = turns[:len(sent)] if turns is not None else None
    return Source(sent, text, sha, method, truncated, len(text), kept, T.tags_of(kept))


def load(doc: dict, fetcher: Optional[Fetcher], max_chars: int, officials: tuple = ()) -> Source:
    """The source of one stored document row. Statements / introductory statements come from the store; the rest is downloaded (politely: robots.txt, 2 s / host).
    `officials`: the surnames of the bank's members, to tell the bank's turns of a press conference from the journalists'."""
    if doc.get("text"):
        return from_paragraphs(doc["text"].split("\n"), "stored", max_chars, min_chars=1)
    if fetcher is None:
        raise SourceError("no fetcher: the document text is not stored")
    r = fetcher.get(doc["url"], conditional=False)
    if r.error:
        raise SourceError(f"{r.error} {doc['url']}")
    is_transcript = doc["type"] == "presser_transcript"
    if doc["url"].lower().endswith(".pdf") or "pdf" in r.headers.get("Content-Type", "").lower():
        return from_paragraphs(X.pdf_paragraphs(r.content), X.METHOD_PDF, max_chars, transcript={"officials": officials} if is_transcript else None)
    rule = rule_for(doc)
    for c in ([rule] if rule else GENERIC):
        got = X.html_paragraphs(r.text, c)
        if sum(len(p) for p in got) >= MIN_CHARS:
            bold = X.html_bold_paragraphs(r.text, c) if is_transcript and doc["currency"] == "EUR" else None
            return from_paragraphs(got, X.METHOD_HTML, max_chars, transcript={"officials": officials, "bold": bold} if is_transcript else None)
    raise SourceError("no container of the page holds the document text", kind="no_text")


def meeting_dates(meetings: dict, ccy: str, today: date, n: int) -> list:
    return sorted(m["date"] for m in meetings.get(ccy, []) if m["date"] <= today)[-n:]
