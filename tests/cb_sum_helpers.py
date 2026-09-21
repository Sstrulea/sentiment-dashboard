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

from .cb_docs_helpers import FakeSession
from .test_cb_docs_collect import ENGINE_FIX, data_dir, fetcher

TODAY = date(2026, 9, 20)
NOW = datetime(2026, 9, 21, 8, 0, tzinfo=timezone.utc)

FED_KEY = "USD:statement:2026-09-16"

from src.cb_summarize import prompts as _PR                          # noqa: E402  (the versions in force: a new prompt version must not need a test edit)
STMT_V = _PR.load("statement").version
MIN_V = _PR.load("minutes").version
NEXT_STMT_V = "statement-v" + str(int(STMT_V.rsplit("v", 1)[1]) + 1)

GOOD_FED = {
    "summary": [
        "The Federal Open Market Committee approved the statement by a 12 – 0 vote.",
        "The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent.",
        "The Committee states that economic activity is expanding at a solid pace and that inflation remains elevated.",
    ],
    "quotes": [
        {"paragraph": 2, "text": "The Committee decided to raise the target range for the federal funds rate by 1/4 percentage point to 3-3/4 to 4 percent"},
        {"paragraph": 4, "text": "Inflation remains elevated."},
    ],
    "coverage": [1, 2, 3, 4],
}


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


def variant(**changes) -> dict:
    """GOOD_FED with some keys replaced."""
    out = json.loads(json.dumps(GOOD_FED))
    out.update(changes)
    return out


def paragraphs_of(messages: list) -> list:
    """The numbered paragraphs of the FIRST user message ("[n] text" lines after "Paragraphs:")."""
    body = messages[0]["content"].split("Paragraphs:\n", 1)[1]
    return [m.group(2) for m in re.finditer(r"^\[(\d+)\] (.*)$", body, flags=re.M)]


def generic_output(messages: list) -> str:
    """A valid output for whatever document is shown: the opening of its first substantial paragraph as the one quote, no numbers, no direction words."""
    paras = paragraphs_of(messages)
    i = next(k for k, p in enumerate(paras) if len(p) >= 60)
    return dumps({"summary": ["The document sets out the matters listed in its opening paragraphs.", "It is an official publication of the bank named in the heading.",
                              "The points below are taken from its text and quoted where they are worded."],
                  "quotes": [{"paragraph": i + 1, "text": paras[i][:40]}], "coverage": [i + 1]})


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
