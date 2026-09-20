"""Fed Summary of Economic Projections (SEP): parser for the accessible-version HTML tables
`fomcprojtabl{YYYYMMDD}.htm` -> rows for data/cb/projections.parquet.

Extracts, per SEP:
  * the medians of Table 1 for every variable (change in real GDP, unemployment rate, PCE inflation, core PCE
    inflation, federal funds rate) and every horizon - as published (rounded to 0.1);
  * every dot of the "appropriate policy path" chart: participants per 25 bp level, per year and longer run;
  * the federal-funds median recomputed from those dots (exact, e.g. 4.125 where Table 1 prints 4.1).
Pure functions over the page text; the I/O source only lists the SEP links on the FOMC calendar page and fetches the
pages that are not stored yet.
"""
from __future__ import annotations

import html as _html
import re
from datetime import date, datetime, timedelta
from typing import Optional

from ..rate_sources import _safe_float
from .base import HttpSource, NON_BROWSER_UA

CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
SEP_URL = "https://www.federalreserve.gov/monetarypolicy/fomcprojtabl{ymd}.htm"
LINK_RX = re.compile(r"fomcprojtabl(\d{8})\.htm")

VARIABLES = {"change in real gdp": "change_in_real_gdp", "unemployment rate": "unemployment_rate",
             "pce inflation": "pce_inflation", "core pce inflation": "core_pce_inflation",
             "federal funds rate": "federal_funds_rate"}
FFR = "federal_funds_rate"


def _text(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def tables(page: str) -> list:
    out = []
    for tb in re.findall(r"<table.*?</table>", page, flags=re.S):
        out.append([[_text(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.S)]
                    for tr in re.findall(r"<tr.*?</tr>", tb, flags=re.S)])
    return out


def _horizon(label: str) -> str:
    return "longer_run" if label.strip().lower().startswith("longer") else label.strip()


def _block(header_row: list) -> list:
    """Horizons of the first block of the 2nd header row: the year list repeats for central tendency and range."""
    first = header_row[0]
    for i in range(1, len(header_row)):
        if header_row[i] == first:
            return [_horizon(h) for h in header_row[:i]]
    return [_horizon(h) for h in header_row]


def median_from_dots(counts: dict) -> Optional[float]:
    """counts {level: participants}. Median = middle dot, or the mean of the two middle dots (18 participants)."""
    flat = sorted(v for v, n in counts.items() for _ in range(n))
    if not flat:
        return None
    m = len(flat)
    return flat[m // 2] if m % 2 else (flat[m // 2 - 1] + flat[m // 2]) / 2


def parse_sep(page: str, meeting_date: date, bank: str = "USD") -> list:
    """Rows: dicts with bank, meeting_date, variable, horizon, kind (median_published | median_from_dots | dot),
    level (dot level, else None), count (participants), value (median, else None), unit, source_url."""
    tabs = tables(page)
    t1 = next((t for t in tabs if t and t[0] and t[0][0] == "Variable"), None)
    dots = next((t for t in tabs if t and t[0] and t[0][0].startswith("Midpoint of target range")), None)
    if t1 is None or dots is None:
        raise ValueError("SEP tables not found (Table 1 median / dot chart)")
    url = SEP_URL.format(ymd=meeting_date.strftime("%Y%m%d"))

    def row(variable, horizon, kind, *, level=None, count=None, value=None):
        return {"bank": bank, "meeting_date": meeting_date, "variable": variable, "horizon": horizon, "kind": kind,
                "level": level, "count": count, "value": value, "unit": "percent", "source_url": url}

    out = []
    horizons = _block(t1[1])
    for r in t1[2:]:
        name = re.sub(r"\s+\d+$", "", r[0]).strip().lower()             # "Core PCE inflation 4" -> footnote marker dropped
        if name not in VARIABLES or len(r) < 1 + len(horizons):
            continue                                                     # "June projection" rows, memo line
        for h, cell in zip(horizons, r[1:1 + len(horizons)]):
            v = _safe_float(cell)
            if v is not None:
                out.append(row(VARIABLES[name], h, "median_published", value=v))
    if not any(o["variable"] == FFR for o in out):
        raise ValueError("no federal funds rate median row in Table 1")

    years = [_horizon(h) for h in dots[0][1:]]
    counts: dict = {h: {} for h in years}
    for r in dots[1:]:
        level = _safe_float(r[0])
        if level is None:
            continue
        for h, c in zip(years, r[1:]):
            if c.strip().isdigit() and int(c) > 0:
                counts[h][level] = int(c)
                out.append(row(FFR, h, "dot", level=level, count=int(c)))
    for h in years:
        med = median_from_dots(counts[h])
        if med is not None:
            out.append(row(FFR, h, "median_from_dots", count=sum(counts[h].values()), value=med))
    return out


def sep_dates(calendar_page: str, today: date) -> list:
    """SEP release dates linked from the FOMC calendar page (ascending, not in the future)."""
    ds = sorted({datetime.strptime(m, "%Y%m%d").date() for m in LINK_RX.findall(calendar_page)})
    return [d for d in ds if d <= today]


class FedSepSource(HttpSource):
    """Lists the SEP dates on the calendar page and fetches the pages that are missing from the store."""
    name = "fed_sep"
    ua = NON_BROWSER_UA

    def dates(self, today: date) -> Optional[list]:
        self.last_status = self.last_note = ""
        try:
            r, _, _ = self.get_url(CALENDAR_URL)
            return None if r is None else sep_dates(r.text, today)
        except Exception as e:
            self._parse_fail(f"{type(e).__name__}: {e}")
            return None

    def page(self, d: date) -> Optional[str]:
        self.last_status = self.last_note = ""
        try:
            r, _, _ = self.get_url(SEP_URL.format(ymd=d.strftime("%Y%m%d")))
            return None if r is None else r.text
        except Exception as e:
            self._parse_fail(f"{type(e).__name__}: {e}")
            return None
