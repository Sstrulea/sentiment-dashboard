"""Polite HTTP for the official-text collector, on the single HTTP policy of the system (src/http_policy.py: robots.txt for the
exact path, crawl-delay per host with a 2 s floor, one honest user-agent) + a per-run request budget and conditional GET (ETag /
Last-Modified kept in data/cb/state.json, advanced only after the data they gate is stored).

Nothing here works around a block: a 401 / 403 / 429 / 503 is reported as a failure (`blocked`), never retried with another identity.
RBNZ sits behind a Cloudflare challenge and stays manual.
"""
from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlsplit

import requests

from .. import http_policy as HP

USER_AGENT = HP.USER_AGENT
MIN_INTERVAL_S = 2.0                # per host
TIMEOUT_S = 25
MAX_REQUESTS = 400                  # per run: a runaway loop cannot hammer a bank
UNSTABLE_ETAG_HOSTS = ("www.ecb.europa.eu",)              # its edge servers send a different ETag ("myra-...") for the same bytes: the ETag is never stored nor sent
BLOCKED = {401, 403, 429, 451, 503}


@dataclass
class Fetched:
    url: str
    status: Optional[int] = None
    content: bytes = b""
    headers: dict = field(default_factory=dict)
    not_modified: bool = False
    error: str = ""                 # "" | robots | blocked | http <n> | network: ...
    validators: dict = field(default_factory=dict)      # {"etag": ..., "last_modified": ...} of THIS response

    @property
    def ok(self) -> bool:
        return not self.error and (self.not_modified or (self.status is not None and 200 <= self.status < 300))

    @property
    def text(self) -> str:
        """UTF-8 unless the server declared another charset (requests would guess ISO-8859-1 and mangle en-dashes)."""
        ctype = self.headers.get("Content-Type", "").lower()
        if "charset" in ctype:
            enc = ctype.split("charset=")[-1].split(";")[0].strip() or "utf-8"
            try:
                return self.content.decode(enc)
            except (LookupError, UnicodeDecodeError):
                pass
        try:
            return self.content.decode("utf-8")
        except UnicodeDecodeError:
            return self.content.decode("latin-1")


class Fetcher:
    def __init__(self, session=None, validators: Optional[dict] = None, *, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, user_agent: str = USER_AGENT, min_interval: float = MIN_INTERVAL_S,
                 max_requests: int = MAX_REQUESTS, policy: Optional[HP.Policy] = None) -> None:
        # one policy per process (shared robots cache and crawl-delay with every other fetcher); a test session gets its own
        self.policy = policy or (HP.Policy(session=session, clock=clock, sleep=sleep, default_delay=min_interval) if session is not None
                                 else HP.POLICY)
        self.session = self.policy.session
        self.validators = validators if validators is not None else {}
        for url, v in self.validators.items():                              # an ETag stored before this rule existed: dropped once
            if self._unstable(url):
                v.pop("etag", None)
        self.ua, self.min_interval, self.max_requests = HP.USER_AGENT, min_interval, max_requests
        self.requests_made = 0
        self.log: list = []

    def allowed(self, url: str) -> bool:
        return self.policy.allowed(url)

    # -- one request ----------------------------------------------------------------------------------------------------
    def _request(self, url: str, headers: dict) -> Fetched:
        if self.requests_made >= self.max_requests:
            return Fetched(url, error="network: per-run request budget exhausted")
        self.requests_made += 1
        try:
            r = self.policy.request("GET", url, headers={"Accept": "*/*", **headers}, timeout=TIMEOUT_S, allow_redirects=True)
        except HP.RobotsDisallowed:
            return Fetched(url, error="robots")
        except requests.RequestException as e:
            self.log.append((url, "network", str(e)[:120]))
            return Fetched(url, error="network")
        out = Fetched(url, r.status_code, r.content, dict(r.headers))
        out.validators = {k: v for k, v in (("etag", None if self._unstable(url) else r.headers.get("ETag")), ("last_modified", r.headers.get("Last-Modified"))) if v}
        if r.status_code == 304:
            out.not_modified = True
        elif r.status_code in BLOCKED:
            out.error = "blocked"
        elif r.status_code >= 400:
            out.error = f"http {r.status_code}"
        self.log.append((url, r.status_code, ""))
        return out

    def get(self, url: str, *, conditional: bool = True) -> Fetched:
        """robots.txt -> conditional GET. A 304 has `not_modified`: the caller keeps what it stored."""
        if not self.allowed(url):
            return Fetched(url, error="robots")
        h = {}
        v = self.validators.get(url) if conditional else None
        if v:
            if v.get("etag") and not self._unstable(url):
                h["If-None-Match"] = v["etag"]
            if v.get("last_modified"):
                h["If-Modified-Since"] = v["last_modified"]
        return self._request(url, h)

    def head(self, url: str) -> Fetched:
        """Existence check for a document that is only linked (transcript PDFs): robots-aware, same rate limit, no body."""
        if not self.allowed(url):
            return Fetched(url, error="robots")
        if self.requests_made >= self.max_requests:
            return Fetched(url, error="network: per-run request budget exhausted")
        self.requests_made += 1
        try:
            r = self.policy.request("HEAD", url, timeout=TIMEOUT_S, allow_redirects=True)
        except HP.RobotsDisallowed:
            return Fetched(url, error="robots")
        except requests.RequestException:
            return Fetched(url, error="network")
        out = Fetched(url, r.status_code, b"", dict(r.headers))
        if r.status_code in BLOCKED:
            out.error = "blocked"
        elif r.status_code >= 400:
            out.error = f"http {r.status_code}"
        return out

    @staticmethod
    def _unstable(url: str) -> bool:
        return urlsplit(url).netloc in UNSTABLE_ETAG_HOSTS

    def remember(self, fetched: Fetched, content_sha: Optional[str] = None) -> None:
        """Call AFTER the data this response produced is stored: only then do the validators advance - and only when the stored CONTENT changed.
        `content_sha` is the hash of what was stored (the extracted text, not the raw bytes: a page carries a nonce or a timestamp that changes every
        time). The same content under new validators (an ETag that jitters, a Last-Modified that is bumped) leaves the state untouched: no state.json
        change, so no commit made only for validators. The cost is a full GET instead of a 304 on the next run, on that page only."""
        if not fetched.validators:
            return
        old = self.validators.get(fetched.url)
        if old is not None and content_sha is not None and old.get("sha256") == content_sha:
            return
        v = dict(fetched.validators)
        if content_sha is not None:
            v["sha256"] = content_sha
        self.validators[fetched.url] = v
