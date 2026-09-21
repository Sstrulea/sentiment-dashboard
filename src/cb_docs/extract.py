"""Deterministic text extraction: one fixed container selector per bank for HTML (no heuristics), pypdf for PDF with the spacing
artifacts of the BoJ PDFs normalised. The text is paragraphs joined by newlines; its sha256 identifies the document content."""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from typing import Optional

from ..cb_probe import BOILERPLATE, _dom, _norm, _paras, _pick

METHOD_HTML = "html-selector"
METHOD_PDF = "pypdf"

# (tag, #id, .class) of the container, an optional regex of the first paragraph NOT to keep, and `loose` for bare text runs
# (the Fed's "approved the following statement ... by a 12-0 vote:" line sits directly in the container).
STATEMENT_RULES = {
    "USD": {"sel": ("div", "article", None), "stop": r"^(For media inquiries|Implementation Note)", "loose": True},
    "EUR": {"sel": ("div", None, "section"), "stop": None},
    "GBP": {"sel": ("div", "output", None), "stop": r"^Minutes of the Monetary Policy Committee meeting"},
    "CAD": {"sel": ("div", None, "post-content"), "stop": None},
    "AUD": {"sel": ("div", None, "rss-mr-content"), "stop": None},
    "CHF": {"sel": ("div", None, "a-text"), "stop": None},
}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# page furniture that sits inside the container: share / print buttons, "For release at ...", a date on its own line, the release headline
FURNITURE = re.compile(r"^(?:Share|Print|Download|Tweet|Back to top)\b.*$|^For release at .*$|^(?:[A-Z][a-z]+day, )?\d{1,2} [A-Z][a-z]+ \d{4}$|^[A-Z][a-z]+ \d{1,2}, \d{4}$|"
                       r"^Federal Reserve issues FOMC statement$|^Press release$|^Media release$", re.I)


def html_paragraphs(html: str, rule: dict) -> list:
    node = _pick(_dom(html), *rule["sel"])
    if node is None:
        return []
    paras = _paras(node, loose=bool(rule.get("loose")))
    if rule.get("start"):                                                       # the same page carries two documents: keep the part from the heading on
        first = next((i for i, p in enumerate(paras) if re.match(rule["start"], p)), None)
        paras = paras[first:] if first is not None else []
    if rule.get("stop"):
        cut = next((i for i, p in enumerate(paras) if re.match(rule["stop"], p)), None)
        paras = paras[:cut] if cut is not None else paras
    return [p for p in paras if not any(p.startswith(b) for b in BOILERPLATE) and not FURNITURE.match(p)]


def html_bold_paragraphs(html: str, rule: dict) -> set:
    """The paragraphs of the container that are set entirely in bold (<strong> / <b>): the ECB marks a journalist's question this way and has no speaker label."""
    from ..cb_probe import _SKIP, _node_text
    node = _pick(_dom(html), *rule["sel"])
    out: set = set()

    def walk(x) -> None:
        if isinstance(x, str) or x.tag in _SKIP:
            return
        if x.tag in ("p", "li"):
            whole = _norm(_node_text(x))
            bold = _norm("".join(_node_text(k) for k in x.kids if not isinstance(k, str) and k.tag in ("strong", "b")))
            if whole and bold == whole:
                out.add(whole)
            return
        for k in x.kids:
            walk(k)
    if node is not None:
        walk(node)
    return out


# ---- PDF ---------------------------------------------------------------------------------------------------------------------

def normalize_pdf_text(text: str) -> str:
    """pypdf leaves spacing artifacts in the BoJ statements ("202 6", "0. 25", "Funds -Supplying", a hyphenated line break): repair the
    ones that cannot be a legitimate space, keep everything else as extracted."""
    t = text.replace("­", "").replace(" ", " ")
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)                                   # hyphenated line break
    t = re.sub(r"\b(20\d) (\d)\b", r"\1\2", t)                                # "202 6" -> "2026"
    t = re.sub(r"(\d) ?\. (\d)", r"\1.\2", t)                                 # "0. 25" -> "0.25"
    t = re.sub(r"(\w) -(\w)", r"\1-\2", t)                                    # "Funds -Supplying"
    t = re.sub(r"(\w)- (\w)", r"\1-\2", t)
    t = re.sub(r"[ \t]+", " ", t)
    return t


VOCAB_FILE = Path(__file__).with_name("pdf_vocab.txt")
_STATIC_VOCAB: Optional[set] = None


def static_vocab() -> set:
    """Words seen unfragmented in the BoJ statements (scripts/cb_gen_pdf_vocab.py); committed, so the repair is deterministic."""
    global _STATIC_VOCAB
    if _STATIC_VOCAB is None:
        _STATIC_VOCAB = set(VOCAB_FILE.read_text().split()) if VOCAB_FILE.exists() else set()
    return _STATIC_VOCAB


SHORT_WORDS = {"a", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is", "it", "me", "my", "no", "of", "on", "or", "so", "to", "up", "us", "we"}


def repair_spacing(text: str) -> str:
    """pypdf splits words at kerning positions ("o vernight", "operati ons", "V oting", "SA TO Ayano"). Join two adjacent fragments only when
    (a) the joined word occurs elsewhere in the same document as a single token, or (b) two upper-case fragments precede a Capitalised name
    (BoJ surnames are upper-case: "TAKA TA Hajime" -> "TAKATA Hajime"). Nothing else is touched."""
    text = re.sub(r"\bV oting\b", "Voting", text)
    toks = text.split(" ")
    vocab = {re.sub(r"\W", "", t).lower() for t in toks if re.fullmatch(r"[A-Za-z]{4,}\W*", t)} | static_vocab()
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if nxt is not None:
            a, b = re.sub(r"\W", "", t), re.sub(r"\W", "", nxt)
            if (a.isupper() and b.isupper() and len(a) >= 2 and len(b) <= 3 and i + 2 < len(toks) and re.fullmatch(r"[A-Z][a-z]+\W*", toks[i + 2])
                    and re.fullmatch(r"[A-Z]+", t) and re.fullmatch(r"[A-Z]+\W*", nxt)):
                out.append(t + nxt)
                i += 2
                continue
            if (len(a) >= 4 and 1 <= len(b) <= 2 and b.islower() and t[-1:].islower() and (a + b).lower() in vocab):        # "financia l", "Japa n"
                out.append(t + nxt)
                i += 2
                continue
            if (a.isalpha() and b.isalpha() and (len(a) <= 3 or len(b) <= 3) and a.lower() not in SHORT_WORDS and b.lower() not in SHORT_WORDS
                    and re.fullmatch(r"[A-Za-z]+", t) and (a + b).lower() in vocab and not ({a.lower(), b.lower()} & vocab)):
                out.append(t + nxt)
                i += 2
                continue
        out.append(t)
        i += 1
    return " ".join(out)


def pdf_paragraphs(content: bytes) -> list:
    try:
        from pypdf import PdfReader
    except ImportError:                                                        # pragma: no cover - pypdf is pinned in requirements.txt
        return []
    raw = "\n\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(content)).pages)
    text = repair_spacing(normalize_pdf_text(raw))
    paras = []
    for block in re.split(r"\n\s*\n", text):
        joined = _norm(block)                                                  # lines of one block are one paragraph
        if joined:
            paras.append(joined)
    return paras


def to_text(paras: list) -> str:
    return "\n".join(paras)
