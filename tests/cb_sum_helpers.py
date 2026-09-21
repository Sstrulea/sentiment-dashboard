"""Offline helpers for the phase 2b tests: a data directory with the frozen real documents collected by phase 2a, recorded model outputs built on the real
Fed statement of 16 Sep 2026, and a responder that writes a valid output for any document it is shown. No test reaches the API."""
from __future__ import annotations

import json
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path

from src import cb_collect as cc
from src.cb_docs import collect as C
from src.cb_summarize import verify as V
from src.cb_summarize.config import load as _load_cfg

from .cb_docs_helpers import FakeSession
from .test_cb_docs_collect import ENGINE_FIX, data_dir, fetcher

TODAY = date(2026, 9, 20)
NOW = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)

FED_KEY = "USD:statement:2026-09-16"

from src.cb_summarize import prompts as _PR                          # noqa: E402  (the versions in force: a new prompt version must not need a test edit)
STMT_V = _PR.load("statement").version
MIN_V = _PR.load("minutes").version
NEXT_STMT_V = "statement-v" + str(int(STMT_V.rsplit("v", 1)[1]) + 1)

_CFG = _load_cfg()
DROP = V.forms_of_text(" ".join(_CFG.attribution_words + _CFG.attribution_subjects))         # what the verifier does not count as a claim


def best_evidence(text: str, paras: list) -> dict:
    """Evidence for a point written in a test: the paragraphs (at most 3) that cover most of its content words, and a verbatim window of 8 words from the first of
    them, starting where the point's first content word occurs. Tests about other checks use it so that grounding does not get in their way."""
    words = V.content_words(text, DROP)
    order = sorted(range(len(paras)), key=lambda k: -len([w for w in set(words) if V.forms(w) & V.forms_of_text(paras[k])]))
    chosen = order[:1]
    for k in order[1:3]:
        if V.support(text, [paras[c] for c in chosen], DROP)[0] >= 0.85:
            break
        chosen.append(k)
    body = paras[chosen[0]]
    m = re.search(r"\S+(?:\s+\S+){4,7}", body)
    at = 0
    for w in words:
        hit = re.search(r"\b" + re.escape(w[:5]), body, flags=re.I)
        if hit:
            at = hit.start()
            break
    frag = re.match(r"\S+(?:\s+\S+){4,7}", body[at:]) or m
    return {"paragraphs": [c + 1 for c in chosen], "fragment": frag.group(0) if frag else body}


def wrap_points(points: list, paras: list) -> list:
    """Strings become points with automatic evidence; anything else (a dict, a malformed item) is kept as it is."""
    return [{"text": p, "evidence": best_evidence(p, paras)} if isinstance(p, str) else p for p in points]


FED_PARAS = ["The Federal Open Market Committee approved the following statement for release by a 12 \u2013 0 vote:",
             "The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent, in support of the Federal Reserve's dual mandate. "
             "The Committee is continuing its policy of maintaining ample reserves in the banking system.",
             "Economic activity is expanding at a solid pace. While uncertainty remains elevated owing, in part, to geopolitical developments, domestic spending has been resilient. "
             "Productivity growth is strong, and capital investment is robust. Job gains have kept pace with the workforce, and the unemployment rate has changed little.",
             "Inflation remains elevated. Today's policy action will support a timelier return to the Committee's 2 percent goal. The Committee will deliver price stability."]

GOOD_FED = {
    "summary": [
        {"text": "The Federal Open Market Committee approved the statement by a 12 \u2013 0 vote.",
         "evidence": {"paragraphs": [1], "fragment": "approved the following statement for release by a 12 \u2013 0 vote:"}},
        {"text": "The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent.",
         "evidence": {"paragraphs": [2], "fragment": "raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent"}},
        {"text": "The Committee states that economic activity is expanding at a solid pace and that inflation remains elevated.",
         "evidence": {"paragraphs": [3, 4], "fragment": "Economic activity is expanding at a solid pace."}},
    ],
    "quotes": [
        {"paragraph": 2, "text": "The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent"},
        {"paragraph": 4, "text": "Inflation remains elevated."},
    ],
    "coverage": [1, 2, 3, 4],
}
GOOD_TEXTS = [p["text"] for p in GOOD_FED["summary"]]


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def variant(**changes) -> dict:
    """GOOD_FED with some keys replaced; `summary` may be given as plain strings (evidence is then made automatically from the Fed statement of 16 Sep)."""
    out = json.loads(json.dumps(GOOD_FED))
    if "summary" in changes and isinstance(changes["summary"], list):
        changes = dict(changes, summary=wrap_points(changes["summary"], FED_PARAS))
    out.update(changes)
    return out


def paragraphs_of(messages: list) -> list:
    """The numbered paragraphs of the FIRST user message ("[n] text" lines after "Paragraphs:")."""
    body = messages[0]["content"].split("Paragraphs:\n", 1)[1]
    return [m.group(2) for m in re.finditer(r"^\[(\d+)\] (.*)$", body, flags=re.M)]


VOCAB = V.forms_of_text(" ".join(_CFG.attributed_words), adverbs=False)
BLOCKED_RX = V.blocked_regex(_CFG.blocked_words)


def clean_window(body: str, size: int = 10):
    """The first `size` words of a paragraph that use no word of the soft vocabulary and no blocked word (a real document is full of "likely" and "expects";
    the generic output must not depend on which one it is shown). None when the paragraph has no such window."""
    toks = body.split()
    for at in range(0, max(1, len(toks) - size + 1)):
        w = toks[at:at + size]
        text = " ".join(w)
        if len(w) >= 5 and not (V.forms_of_text(text, adverbs=False) & VOCAB) and not BLOCKED_RX.search(text):
            return at, w
    return None


def generic_output(messages: list) -> str:
    """A valid output for whatever document is shown: three points that say "The Committee says <ten words of one of its paragraphs>" (so the evidence is verbatim and
    fully covered), the opening of the first substantial paragraph as the one quote. No direction words, no soft words."""
    paras = paragraphs_of(messages)
    big = [k for k, p in enumerate(paras) if len(p) >= 80 and clean_window(p)] or [k for k, p in enumerate(paras) if clean_window(p)] or list(range(len(paras)))
    picks = [big[n % len(big)] for n in range(3)]
    points = []
    for k in picks:
        at, w = clean_window(paras[k]) or (0, paras[k].split()[:10])
        frag = " ".join(w[:8])
        points.append({"text": "The Committee says " + " ".join(w), "evidence": {"paragraphs": [k + 1], "fragment": frag}})
    return dumps({"summary": points, "quotes": [{"paragraph": picks[0] + 1, "text": paras[picks[0]][:40]}], "coverage": sorted({k + 1 for k in picks})})


def responder_generic(system: str, messages: list) -> str:
    return generic_output(messages)


_BASE = None


def collected_dir(tmp_path_factory) -> Path:
    """A data directory in which phase 2a has run on the frozen documents (built once per session, copied per test)."""
    global _BASE
    if _BASE is None:
        d = data_dir(tmp_path_factory.mktemp("sum_base"))
        C.run_documents(cc.Paths(d), TODAY, fetcher=fetcher(FakeSession()), roster=C.load_roster(), now=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
        _BASE = d
    return _BASE


def fresh_copy(base: Path, tmp_path: Path) -> Path:
    d = tmp_path / "cb"
    shutil.copytree(base, d)
    if (d / "summaries").exists():
        shutil.rmtree(d / "summaries")
    return d


# --- OpenAI wire format (recorded responses of the Responses API) --------------------------------------------------------------------------------

class HttpResp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def oai_body(text=None, *, refusal=None, status="completed", incomplete=None, usage=None, reasoning=0) -> HttpResp:
    """A Responses API response: the output text (or a refusal), a status, and usage (None = the default counts, False = no usage at all)."""
    content = [{"type": "refusal", "refusal": refusal}] if refusal is not None else [{"type": "output_text", "text": text, "annotations": []}]
    body = {"id": "resp_test", "object": "response", "status": status, "incomplete_details": incomplete, "error": None,
            "output": [{"type": "reasoning", "id": "rs_1", "summary": []}, {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant", "content": content}]}
    if usage is not False:
        body["usage"] = usage or {"input_tokens": 1500, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 800,
                                  "output_tokens_details": {"reasoning_tokens": reasoning}, "total_tokens": 2300}
    return HttpResp(200, body)


class OpenAISession:
    """requests.Session stand-in for the OpenAI client: answers in order (a function of the request body also works), keeps every request."""

    def __init__(self, *answers, responder=None):
        self.answers, self.responder, self.posts = list(answers), responder, []

    def post(self, url, json=None, headers=None, timeout=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        if self.responder is not None:
            return self.responder(json)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def openai_client(cfg, *answers, responder=None):
    from src.cb_summarize.client import OpenAIClient
    s = OpenAISession(*answers, responder=responder)
    return OpenAIClient(cfg, "sk-test", session=s, sleep=lambda x: None), s
