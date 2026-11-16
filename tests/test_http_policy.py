"""fix/sources-compliance: the single HTTP policy (robots.txt, crawl-delay, honest user-agent) and the guard against the hosts / paths
that are disallowed by their owners."""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from src import http_policy as HP

ROOT = Path(__file__).resolve().parents[1]


class Resp:
    def __init__(self, status, body=b""):
        self.status_code, self.content = status, body
        self.text = body.decode() if isinstance(body, bytes) else body
        self.headers = {}


class FakeSession:
    def __init__(self, robots=None):
        self.robots = robots or {}                 # host -> Resp
        self.calls = []

    def get(self, url, headers=None, **kw):
        self.calls.append((url, dict(headers or {})))
        host = url.split("/")[2]
        if url.endswith("/robots.txt"):
            return self.robots.get(host, Resp(404))
        return Resp(200, b"ok")

    head = get


class Clock:
    def __init__(self):
        self.t, self.naps = 0.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.naps.append(round(s, 3))
        self.t += s


def policy(robots=None, delay=2.0):
    s, c = FakeSession(robots), Clock()
    return HP.Policy(session=s, clock=c, sleep=c.sleep, today=lambda: date(2026, 11, 16), default_delay=delay), s, c


def test_robots_disallow_blocks_the_request_and_nothing_is_sent():
    p, s, _ = policy({"stooq.com": Resp(200, b"User-agent: *\nDisallow: /\n")})
    with pytest.raises(HP.RobotsDisallowed):
        p.request("GET", "https://stooq.com/q/d/l/?s=2usy.b&i=d")
    assert [u for u, _ in s.calls] == ["https://stooq.com/robots.txt"]


def test_robots_is_checked_on_the_exact_path_and_cached_for_the_day():
    p, s, _ = policy({"www.bankofengland.co.uk": Resp(200, b"User-agent: *\nDisallow: /boeapps/iadb\n")})
    assert not p.allowed("https://www.bankofengland.co.uk/boeapps/iadb/fromshowcolumns.asp?x=1")
    assert p.allowed("https://www.bankofengland.co.uk/boeapps/database/Bank-Rate.asp")
    assert sum(u.endswith("/robots.txt") for u, _ in s.calls) == 1                       # one robots.txt per host per day


def test_rfc9309_unavailable_allows_unreachable_denies():
    p, _, _ = policy({"a.example": Resp(403, b"denied"), "b.example": Resp(503, b"down")})
    assert p.allowed("https://a.example/data.csv")                                        # 4xx: no restriction
    assert not p.allowed("https://b.example/data.csv")                                    # 5xx: complete disallow


def test_crawl_delay_is_respected_per_host_with_a_2s_floor():
    p, _, c = policy({"www.eurex.com": Resp(200, b"User-agent: *\nCrawl-delay: 30\n"), "x.example": Resp(404)})
    p.request("GET", "https://www.eurex.com/page")
    p.request("GET", "https://www.eurex.com/file.csv")
    assert c.naps[-1] == 30.0                                                             # robots Crawl-delay
    p.request("GET", "https://x.example/a")
    p.request("GET", "https://x.example/b")
    assert c.naps[-1] == 2.0                                                              # default floor


def test_one_honest_user_agent_never_a_browser_and_no_referer():
    p, s, _ = policy()
    p.request("GET", "https://www.rba.gov.au/statistics/tables/csv/f2-data.csv",
              headers={"User-Agent": "Mozilla/5.0 Chrome/120", "Referer": "https://www.rba.gov.au/statistics/tables/", "Accept": "text/csv"})
    ua = {h.get("User-Agent") for _, h in s.calls}
    assert ua == {HP.USER_AGENT} and "Mozilla" not in HP.USER_AGENT and "@" not in HP.USER_AGENT
    assert all("Referer" not in h for _, h in s.calls) and s.calls[-1][1]["Accept"] == "text/csv"


def test_exempt_service_apis_skip_robots_but_keep_the_honest_ua():
    p, s, _ = policy()
    p.request("GET", "https://api.github.com/repos/x/y")
    assert [u for u, _ in s.calls] == ["https://api.github.com/repos/x/y"] and s.calls[0][1]["User-Agent"] == HP.USER_AGENT


# ---- guard: hosts / paths the owners disallow never come back ----

FORBIDDEN = {
    "BoE IADB (robots.txt Disallow)": r"boeapps/iadb",
    "DBnomics (robots.txt Disallow /)": r"db\.nomics",
    "Stooq (robots.txt Disallow /)": r"stooq\.(com|pl)",
    "Yahoo Finance (robots.txt Disallow /)": r"finance\.yahoo",
    "YouTube feeds (robots.txt Disallow)": r"feeds/videos\.xml",
    "RBNZ B2 hb2-daily (Cloudflare; a browser UA tried to get past it)": r"hb2-daily",
    "unofficial ASX mirror": r"bpalmer4",
}


def test_guard_no_forbidden_host_or_path_in_code_scripts_or_config():
    hits = []
    for top in ("src", "scripts", "config"):
        for f in (ROOT / top).rglob("*"):
            if not f.is_file() or f.suffix not in (".py", ".yaml", ".yml", ".sh", ".js", ".json", ".md", ".txt") or "__pycache__" in f.parts:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            for what, rx in FORBIDDEN.items():
                if re.search(rx, text):
                    hits.append((str(f.relative_to(ROOT)), what))
    assert hits == [], hits


def test_guard_no_browser_user_agent_and_no_direct_requests_call():
    bad = []
    for f in list((ROOT / "src").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py")):
        t = f.read_text(encoding="utf-8")
        if re.search(r"Chrome/\d|AppleWebKit|Safari/\d", t):
            bad.append((str(f.relative_to(ROOT)), "browser UA"))
        if f.name != "http_policy.py" and re.search(r"requests\.(get|post|head)\(", t):
            bad.append((str(f.relative_to(ROOT)), "requests.* outside http_policy"))
    assert bad == [], bad
