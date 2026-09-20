"""Offline helpers for the phase 2a tests: real documents frozen in tests/fixtures/cb_docs (scripts/cb_freeze_docs_fixture.py) served by a
fake requests session, and small builders for the extracted texts."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from src.cb_docs import extract as X

FIX = Path(__file__).parent / "fixtures" / "cb_docs"
D = date
YOUTUBE_ROBOTS = "User-agent: *\nDisallow: /api/\nDisallow: /feeds/videos.xml\nDisallow: /get_video\n"


def manifest() -> dict:
    return json.loads((FIX / "manifest.json").read_text())


def statement_text(ccy: str, day: str) -> str:
    """The extracted text of a frozen statement (same code path as the collector)."""
    ext = "pdf" if ccy == "JPY" else "html"
    raw = (FIX / "statements" / f"{ccy}_{day}.{ext}").read_bytes()
    paras = X.pdf_paragraphs(raw) if ext == "pdf" else X.html_paragraphs(raw.decode("utf-8"), X.STATEMENT_RULES[ccy])
    return X.to_text(paras)


def statement_paras(ccy: str, day: str) -> list:
    return statement_text(ccy, day).split("\n")


class Resp:
    def __init__(self, status: int, content: bytes = b"", headers: dict | None = None) -> None:
        self.status_code, self.content, self.headers = status, content, headers or {}


class FakeSession:
    """requests.Session stand-in: the frozen documents by URL, robots.txt (YouTube disallows its feed), 404 for the rest."""

    def __init__(self, extra: dict | None = None, head_ok: tuple = ("FOMCpresconf", "mc-gov", "ecb.is"), robots: dict | None = None) -> None:
        self.man = manifest()
        self.extra = extra or {}
        self.calls: list = []
        self.head_ok = head_ok
        self.robots = {"www.youtube.com": YOUTUBE_ROBOTS, **(robots or {})}

    def get(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append(("GET", url, dict(headers or {})))
        if url.endswith("/robots.txt"):
            host = url.split("/")[2]
            return Resp(200, self.robots[host].encode(), {"Content-Type": "text/plain"}) if host in self.robots else Resp(404)
        if url in self.extra:
            return self.extra[url]
        if url in self.man:
            m = self.man[url]
            etag = f'"{abs(hash(url)) % 10 ** 8}"'
            if (headers or {}).get("If-None-Match") == etag:
                return Resp(304, b"", {"ETag": etag})
            return Resp(200, (FIX / m["file"]).read_bytes(), {"Content-Type": m["content_type"], "ETag": etag})
        return Resp(404, b"not in the fixture")

    def head(self, url, headers=None, timeout=None, allow_redirects=True):
        self.calls.append(("HEAD", url, dict(headers or {})))
        return Resp(200 if any(k in url for k in self.head_ok) else 404)

    def urls(self, method: str = "GET") -> list:
        return [u for m, u, _ in self.calls if m == method and not u.endswith("/robots.txt")]
