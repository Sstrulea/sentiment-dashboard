"""The single HTTP policy of the whole system (econ, Central Banks, documents, scripts) - fix/sources-compliance.

    from src import http_policy as HP
    r = HP.get(url, params=..., headers=..., timeout=...)          # a requests.Response, or HP.RobotsDisallowed

Every outbound data request goes through `request()`:
  * robots.txt is checked for the exact path requested, for our user-agent, cached once per host per day (memory + an optional
    on-disk cache). RFC 9309: robots 2xx -> parsed; 4xx -> no restriction; 5xx / network error -> everything disallowed (this run).
  * crawl-delay respected per host: max(the robots `Crawl-delay`, DEFAULT_DELAY_S = 2 s) between two requests to the same host.
  * one honest user-agent (USER_AGENT, no personal e-mail); a caller cannot override it. No browser UA, no spoofed Referer.
  * a block is never worked around: a 403 / bot wall reaches the caller as is (no retry with other headers).

Exempt (EXEMPT_HOSTS): authenticated service APIs that are not data sources (the LLM providers of the summaries, the GitHub API of
the trigger) - they have no robots semantics; they still get the honest user-agent.
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.robotparser as robotparser
from datetime import date
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

import requests

log = logging.getLogger("http_policy")

USER_AGENT = "macro-data-analysis/1.0 (+https://github.com/Sstrulea/sentiment-dashboard)"
DEFAULT_DELAY_S = 2.0
ROBOTS_TIMEOUT_S = 20
EXEMPT_HOSTS = frozenset({"api.openai.com", "api.anthropic.com", "api.github.com"})


class RobotsDisallowed(requests.RequestException):
    """The host's robots.txt does not allow this path for our user-agent: the request is not sent."""


class Policy:
    def __init__(self, session: Optional[requests.Session] = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, today: Callable[[], date] = date.today,
                 cache_dir: Optional[Path] = None, default_delay: float = DEFAULT_DELAY_S, robots: str = "check"):
        self.session = session or requests.Session()
        self._clock, self._sleep, self._today = clock, sleep, today
        self.cache_dir = cache_dir
        self.default_delay = default_delay
        self.robots_mode = robots                    # "check" | "allow" (tests that are not about the policy itself)
        self._robots: dict = {}                      # host -> (day, RobotFileParser | "allow" | "deny", crawl_delay)
        self._last: dict = {}                        # host -> monotonic time of the last request
        self._lock = threading.Lock()
        self.requests_sent: list = []                # (method, url) - tests and the run report

    # ---- robots ---------------------------------------------------------------------------------------------------------------
    def _load_robots(self, scheme: str, host: str):
        day = self._today().isoformat()
        hit = self._robots.get(host)
        if hit and hit[0] == day:
            return hit
        text, status = None, None
        disk = self.cache_dir / f"{host}_{day}.txt" if self.cache_dir else None
        if disk is not None and disk.exists():
            text, status = disk.read_text(encoding="utf-8", errors="replace"), 200
        else:
            self._wait(host, self.default_delay)
            try:
                r = self.session.get(f"{scheme}://{host}/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=ROBOTS_TIMEOUT_S)
                status = r.status_code
                body = getattr(r, "content", None)
                text = (body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else getattr(r, "text", "")) \
                    if 200 <= r.status_code < 300 else None
            except requests.RequestException as e:
                status = f"network:{type(e).__name__}"
            self._last[host] = self._clock()
        if text is not None:
            rp = robotparser.RobotFileParser()
            rp.parse(text.splitlines())
            cd = rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*")
            entry = (day, rp, float(cd) if cd else None)
            if disk is not None and not disk.exists():
                disk.parent.mkdir(parents=True, exist_ok=True)
                disk.write_text(text, encoding="utf-8")
        elif isinstance(status, int) and 400 <= status < 500:
            entry = (day, "allow", None)                                   # RFC 9309 2.3.1.3: unavailable -> no restriction
        else:
            entry = (day, "deny", None)                                    # RFC 9309 2.3.1.4: unreachable -> complete disallow
            log.warning("robots.txt of %s unreachable (%s): every path disallowed for this run", host, status)
        self._robots[host] = entry
        return entry

    def allowed(self, url: str) -> bool:
        u = urlsplit(url)
        host = u.hostname or ""
        if host in EXEMPT_HOSTS or host in ("127.0.0.1", "localhost") or self.robots_mode == "allow":
            return True
        _, rp, _ = self._load_robots(u.scheme or "https", host)
        if rp == "allow":
            return True
        if rp == "deny":
            return False
        return rp.can_fetch(USER_AGENT, url)

    def delay(self, host: str) -> float:
        entry = self._robots.get(host)
        cd = entry[2] if entry else None
        return max(self.default_delay, cd or 0.0)

    # ---- pacing -----------------------------------------------------------------------------------------------------------------
    def _wait(self, host: str, gap: float) -> None:
        last = self._last.get(host)
        if last is not None:
            left = gap - (self._clock() - last)
            if left > 0:
                self._sleep(left)

    # ---- request ----------------------------------------------------------------------------------------------------------------
    def request(self, method: str, url: str, *, headers: Optional[dict] = None, **kw) -> requests.Response:
        u = urlsplit(url)
        host = u.hostname or ""
        with self._lock:
            if not self.allowed(url):
                raise RobotsDisallowed(f"robots.txt of {host} disallows {u.path} for {USER_AGENT}")
            if host not in EXEMPT_HOSTS:
                self._wait(host, self.delay(host))
            h = {k: v for k, v in (headers or {}).items() if k.lower() not in ("user-agent", "referer")}
            h["User-Agent"] = USER_AGENT
            try:
                return getattr(self.session, method.lower())(url, headers=h, **kw)
            finally:
                self._last[host] = self._clock()
                self.requests_sent.append((method, url))


POLICY = Policy(session=requests, cache_dir=Path.home() / ".cache" / "macro-data-analysis" / "robots")   # the module: requests.get / .post / .head


def get(url: str, **kw) -> requests.Response:
    return POLICY.request("GET", url, **kw)


def post(url: str, **kw) -> requests.Response:
    return POLICY.request("POST", url, **kw)
