"""Contract shared by the Central Banks market adapters.

An adapter is a `RateSource`-like object (`name`/`id`, `supports(currency)`,
`fetch(...)`, `last_status`) built on `src.rate_sources.BaseSource` for all HTTP
(IPv4 pin, retries, bot-wall sniffing). `fetch` NEVER raises: it returns a
`FetchResult`, or None with `last_status`/`last_note` set. Quotes carry the value
exactly as published (a price or a rate, with its unit); conversion, spread
adjustment and meeting extraction are phase 1B.
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import yaml

from ..rate_sources import BaseSource, FRED_UA

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SOURCES_YAML = ROOT / "config" / "cb_sources.yaml"
NON_BROWSER_UA = FRED_UA           # RBA/FRED serve it; a Chrome UA is what earns some 403s


@dataclass(frozen=True)
class Quote:
    source: str
    currency: str
    instrument: str
    contract: str                  # contract label (exchange convention) or tenor label ("3M")
    field: str                     # mean | fwd | settle | yield | rate
    ref_start: Optional[date]
    ref_end: Optional[date]        # exclusive
    tenor_months: Optional[float]
    value: float
    unit: str                      # bp | percent | index_points
    asof: date
    asof_inferred: bool
    fetched_at: datetime           # UTC


@dataclass
class FetchResult:
    status: str                    # ok | not_modified | skipped
    quotes: list[Quote] = field(default_factory=list)
    raw: Optional[dict] = None     # snapshot sources: {"asof": date, "text": "<relevant rows, csv>"}
    state: dict = field(default_factory=dict)      # validators / last_asof to persist
    note: str = ""


def quote_order(q: Quote):
    return (q.asof, q.instrument, q.tenor_months or 0.0, q.ref_start or date.min, q.contract, q.field)


def load_sources(path: Path | None = None) -> dict:
    return yaml.safe_load((path or SOURCES_YAML).read_text())


# ---------------------------------------------------------------------------
# Calendar helpers (exchange conventions live here, once)
# ---------------------------------------------------------------------------

def add_months(y: int, m: int, k: int) -> tuple[int, int]:
    yy, mm = divmod(m - 1 + k, 12)
    return y + yy, mm + 1


def add_months_date(d: date, k: int) -> date:
    y, m = add_months(d.year, d.month, k)
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def third_wednesday(y: int, m: int) -> date:
    d = date(y, m, 1)
    return d + timedelta(days=(2 - d.weekday()) % 7 + 14)


def first_wednesday_after_9th(y: int, m: int) -> date:
    d = date(y, m, 10)
    return d + timedelta(days=(2 - d.weekday()) % 7)


def month_bounds(y: int, m: int) -> tuple[date, date]:
    y2, m2 = add_months(y, m, 1)
    return date(y, m, 1), date(y2, m2, 1)


def imm_window(y: int, m: int) -> tuple[date, date]:
    """3M reference window named by its START month: [3rd Wed of (y,m), 3rd Wed of (y,m)+3M).
    JPX 3M TONA and MX CRA (spec wording) - the exchange's month label is the start month."""
    y2, m2 = add_months(y, m, 3)
    return third_wednesday(y, m), third_wednesday(y2, m2)


def roll_back_weekend(d: date) -> date:
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


class MarketSource(BaseSource):
    """Base for market adapters. Subclasses set `id`/`currency` and implement `_fetch`."""
    id = ""
    currency = ""
    ua: Optional[str] = None                       # None -> BaseSource's default browser UA

    def __init__(self, cfg: dict | None = None, now: Callable[[], datetime] | None = None) -> None:
        super().__init__()
        self.cfg = cfg if cfg is not None else load_sources()["sources"][self.id]
        self.name = self.id
        self._now = now or (lambda: datetime.now(timezone.utc))

    # ---- RateSource-like surface -------------------------------------------------
    def supports(self, currency: str) -> bool:
        return currency == self.currency

    def fetch(self, currency: str | None = None, since: date | None = None,
              state: dict | None = None) -> Optional[FetchResult]:
        self.last_status = ""
        self.last_note = ""
        try:
            res = self._fetch(since or (self._now().date() - timedelta(days=10)), state or {})
        except Exception as e:                       # never propagate — graceful per source
            self._parse_fail(f"{type(e).__name__}: {e}")
            return None
        if res is not None:
            res.quotes.sort(key=quote_order)         # deterministic order regardless of the source's layout
        return res

    def _fetch(self, since: date, state: dict) -> Optional[FetchResult]:   # overridden
        return None

    # ---- shared behaviour --------------------------------------------------------
    @property
    def horizon_months(self) -> int:
        return int(self.cfg.get("horizon_months", 36))

    def within_horizon(self, start: date, asof: date) -> bool:
        return start <= add_months_date(asof, self.horizon_months)

    def is_final(self, asof: date) -> bool:
        """A quote whose as-of is the exchange's CURRENT day is final only after eod_cutoff (UTC)."""
        now = self._now()
        today_local = now.astimezone(ZoneInfo(self.cfg["exchange_tz"])).date()
        if asof < today_local:
            return True
        if asof > today_local:
            return False
        return now >= datetime.combine(asof, _hhmm(self.cfg["eod_cutoff"]), tzinfo=timezone.utc)

    def infer_asof(self) -> Optional[date]:
        """For pages WITHOUT an as-of stamp: None inside the intraday window (values may be live),
        otherwise the last completed exchange day from fetch time + cutoff (weekend rolled back).
        Saturday/Sunday have no session, so the window does not apply."""
        now = self._now()
        start, end = (_hhmm(x) for x in self.cfg["intraday_utc"])
        t = now.time().replace(tzinfo=None)
        if now.weekday() < 5 and start <= t < end:
            return None
        d = now.date() if t >= end else now.date() - timedelta(days=1)
        return roll_back_weekend(d)

    def get_url(self, url: str, validators: dict | None = None, ua: Optional[str] = None):
        """GET via BaseSource._get, conditional when validators are known.
        Returns (response | None, not_modified, new_validators)."""
        extra: dict = {}
        if ua or self.ua:
            extra["User-Agent"] = ua or self.ua
        v = validators or {}
        if v.get("etag"):
            extra["If-None-Match"] = v["etag"]
        if v.get("last_modified"):
            extra["If-Modified-Since"] = v["last_modified"]
        r = self._get(url, extra_headers=extra or None, retries=0 if (v.get("etag") or v.get("last_modified")) else 2)
        if r is None:
            if self.last_note == "HTTP 304":
                self.last_status = self.last_note = ""
                return None, True, dict(v)
            return None, False, {}
        return r, False, {"etag": r.headers.get("ETag"), "last_modified": r.headers.get("Last-Modified")}

    def quote(self, instrument: str, contract: str, field_: str, value: float, unit: str, asof: date, *,
              ref_start: date | None = None, ref_end: date | None = None, tenor_months: float | None = None,
              inferred: bool = False) -> Quote:
        return Quote(self.id, self.currency, instrument, contract, field_, ref_start, ref_end, tenor_months,
                     float(value), unit, asof, inferred, self._now())
