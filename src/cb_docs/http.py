"""Polite HTTP for the official-text collector: an identifiable user agent, robots.txt respected, a gentle per-host rate limit and
conditional GET (ETag / Last-Modified kept in data/cb/state.json, advanced only after the data they gate is stored).

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

USER_AGENT = "Mozilla/5.0 (compatible; CbDocsBot/1.0; +https://github.com/Sstrulea/sentiment-dashboard)"
MIN_INTERVAL_S = 2.0                # per host
TIMEOUT_S = 25
MAX_REQUESTS = 400                  # per run: a runaway loop cannot hammer a bank
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
                 max_requests: int = MAX_REQUESTS) -> None:
        self.session = session or requests.Session()
        self.validators = validators if validators is not None else {}
        self._sleep, self._clock, self.ua, self.min_interval, self.max_requests = sleep, clock, user_agent, min_interval, max_requests
        self._last: dict = {}
        self._robots: dict = {}
        self.requests_made = 0
        self.log: list = []

    # -- robots.txt ---------------------------------------------------------------------------------------------------
    def _robots_for(self, url: str) -> Optional[urllib.robotparser.RobotFileParser]:
        """None = robots.txt could not be read (blocked or down): the host is skipped this run."""
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            r = self._request(f"{host}/robots.txt", {})
            if r.error == "network" or r.status in BLOCKED or (r.status is not None and r.status >= 500):
                self._robots[host] = None
            elif r.status == 404 or r.status is None:
                rp.parse([])                                             # no robots.txt = everything allowed
                self._robots[host] = rp
            else:
                rp.parse(r.text.splitlines())
                self._robots[host] = rp
        return self._robots[host]

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return bool(rp and rp.can_fetch(self.ua, url))

    # -- one request ----------------------------------------------------------------------------------------------------
    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self.min_interval - (self._clock() - last)
            if gap > 0:
                self._sleep(gap)
        self._last[host] = self._clock()

    def _request(self, url: str, headers: dict) -> Fetched:
        if self.requests_made >= self.max_requests:
            return Fetched(url, error="network: per-run request budget exhausted")
        self.requests_made += 1
        self._wait(urlsplit(url).netloc)
        h = {"User-Agent": self.ua, "Accept": "*/*", **headers}
        try:
            r = self.session.get(url, headers=h, timeout=TIMEOUT_S, allow_redirects=True)
        except requests.RequestException as e:
            self.log.append((url, "network", str(e)[:120]))
            return Fetched(url, error="network")
        out = Fetched(url, r.status_code, r.content, dict(r.headers))
        out.validators = {k: v for k, v in (("etag", r.headers.get("ETag")), ("last_modified", r.headers.get("Last-Modified"))) if v}
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
            if v.get("etag"):
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
        self._wait(urlsplit(url).netloc)
        try:
            r = self.session.head(url, headers={"User-Agent": self.ua}, timeout=TIMEOUT_S, allow_redirects=True)
        except requests.RequestException:
            return Fetched(url, error="network")
        out = Fetched(url, r.status_code, b"", dict(r.headers))
        if r.status_code in BLOCKED:
            out.error = "blocked"
        elif r.status_code >= 400:
            out.error = f"http {r.status_code}"
        return out

    def remember(self, fetched: Fetched) -> None:
        """Call AFTER the data this response produced is stored: only then do the validators advance."""
        if fetched.validators:
            self.validators[fetched.url] = fetched.validators
