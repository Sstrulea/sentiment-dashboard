"""Official holiday listings -> what each calendar of config/cb_calendars.yaml should contain, and the weekly check.

`check_calendar` only WARNS (never rewrites the YAML - scripts/cb_gen_calendars.py does that):
  * a weekday date on the official page that is missing from the calendar;
  * a date the calendar has as verified that the official page (for a year it covers) no longer lists;
  * a year whose rows are `verified: false` that the official source now covers - it has become verifiable.
Sources: UK gov.uk JSON, JP Cabinet Office CSV, US Fed K.8, EU ECB T2 closing-day rule, CA BoC upcoming closures (+ probe of
the 2027 event pages), AU NSW Government page (+ probe of the RBA 2027 page), NZ employment.govt.nz, CH SIX SIC PDF
(no PDF library: the file's ETag / length is compared instead).
"""
from __future__ import annotations

import csv
import html as _html
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from .base import HttpSource, NON_BROWSER_UA

YEARS = (2026, 2027)
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September",
                                      "October", "November", "December"], start=1)}
_MON = "|".join(MONTHS)
_WD = "Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"

URLS = {
    "UK": "https://www.gov.uk/bank-holidays.json",
    "JP": "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv",
    "US": "https://www.federalreserve.gov/aboutthefed/k8.htm",
    "EU": "https://www.ecb.europa.eu/paym/target/t2/html/index.en.html",
    "CA": "https://www.bankofcanada.ca/press/upcoming-events/bank-of-canada-holiday-schedule/",
    "AU": "https://www.nsw.gov.au/about-nsw/public-holidays",
    "NZ": "https://www.employment.govt.nz/leave-and-holidays/public-holidays/public-holidays-and-anniversary-dates/",
    "CH": "https://www.six-group.com/dam/download/banking-services/interbank-clearing/en/payment_services/sic/banking-holidays.pdf",
}
# "a year became verifiable": a page that exists only once the bank / government publishes that year
PROBES = {"CA": {2027: "https://www.bankofcanada.ca/2027/01/new-years-day/"},
          "AU": {2027: "https://www.rba.gov.au/schedules-events/bank-holidays-2027.html"}}


@dataclass
class Listing:
    years: set                                   # years the source covers completely
    dates: dict = field(default_factory=dict)    # date -> name
    partial: bool = False                        # the source lists only upcoming dates (BoC): no "removed" warnings


def _text(s: str) -> str:
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.S)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s)))


def _in_years(d: date) -> bool:
    return d.year in YEARS


def easter(y: int) -> date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo, dd = divmod(h + l - 7 * m + 114, 31)
    return date(y, mo, dd + 1)


# ---------------------------------------------------------------------------
# Parsers (pure)
# ---------------------------------------------------------------------------

def parse_uk(text: str) -> Listing:
    ev = json.loads(text)["england-and-wales"]["events"]
    return Listing({2026, 2027}, {date.fromisoformat(e["date"]): e["title"] for e in ev if _in_years(date.fromisoformat(e["date"]))})


def parse_jp(text: str) -> Listing:
    out = {}
    for r in list(csv.reader(io.StringIO(text)))[1:]:
        y, m, d = (int(x) for x in r[0].split("/"))
        if y in YEARS:
            out[date(y, m, d)] = r[1]
    return Listing({2026, 2027}, out)


US_NAMES = ["New Year's Day", "Birthday of Martin Luther King, Jr.", "Washington's Birthday", "Memorial Day",
            "Juneteenth National Independence Day", "Independence Day", "Labor Day", "Columbus Day", "Veterans Day",
            "Thanksgiving Day", "Christmas Day"]


def parse_us(page: str) -> Listing:
    """Fed K.8: five year columns (2026 ... 2030). Saturday holidays are observed the Friday before (Board closed),
    Sunday holidays the Monday after - the K.8 footnote lists the same observed dates."""
    t = _text(page)
    head = re.search(r"\b(20\d\d) (20\d\d) (20\d\d) (20\d\d) (20\d\d)\b", t)
    if not head:
        raise ValueError("K.8 year header not found")
    years = [int(y) for y in head.groups()]
    body = t[head.end():]
    out = {}
    for name in US_NAMES:
        lead = r"(?<!National )" if name == "Independence Day" else ""            # not the Juneteenth row
        m = re.search(lead + re.escape(name) + r"((?: (?:" + _MON + r") \d{1,2}\*{0,2}){5})", body)
        if not m:
            raise ValueError(f"K.8 row not found: {name}")
        for y, cell in zip(years, re.findall(r"(" + _MON + r") (\d{1,2})", m.group(1))):
            if y in YEARS:
                d = date(y, MONTHS[cell[0]], int(cell[1]))
                out[d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d] = name
    for frag in re.findall(r"closed on ([^.]*?)\.\s", t):                          # footnotes: the observed dates, e.g. 31 Dec 2027
        for mon, day, yr in re.findall(r"(" + _MON + r") (\d{1,2}), (20\d\d)", frag):
            d = date(int(yr), MONTHS[mon], int(day))
            if d.year in YEARS:
                out.setdefault(d, "observed holiday (K.8 footnote)")
    return Listing({2026, 2027}, out)


def parse_eu(page: str) -> Listing:
    """ECB T2: closed on 1 Jan, Good Friday, Easter Monday, 1 May, 25 Dec, 26 Dec (rule; dates computed)."""
    t = _text(page)
    need = ["1 January", "Good Friday", "Easter Monday", "1 May", "25 December", "26 December"]
    i = t.find("T2 is also closed on")
    if i < 0 or not all(n in t[i:i + 500] for n in need):
        raise ValueError("ECB T2 closing-day list changed")
    out = {}
    for y in YEARS:
        e = easter(y)
        out.update({date(y, 1, 1): "New Year's Day", e - timedelta(days=2): "Good Friday", e + timedelta(days=1): "Easter Monday",
                    date(y, 5, 1): "Labour Day", date(y, 12, 25): "Christmas Day", date(y, 12, 26): "Boxing Day"})
    return Listing({2026, 2027}, out)


NZ_NAMES = ["New Year's Day", "Day after New Year's Day", "Waitangi Day", "Good Friday", "Easter Monday", "Anzac Day", "ANZAC Day",
            "King's Birthday", "Matariki", "Labour Day", "Christmas Day", "Boxing Day"]


def _year_of(wd: str, day: int, mon: str) -> Optional[int]:
    """The year in (2026, 2027) in which day/month falls on the given weekday."""
    for y in YEARS:
        if date(y, MONTHS[mon], day).strftime("%A") == wd:
            return y
    return None


def parse_nz(page: str) -> Listing:
    t = _text(page)
    out = {}
    names = "|".join(re.escape(n) for n in sorted(NZ_NAMES, key=len, reverse=True))
    for m in re.finditer(r"(" + names + r") (?:Varies|\d{1,2} (?:" + _MON + r")|1st Monday in June|4th Monday in October) (" + _WD + r") (\d{1,2}) (" + _MON + r")", t):
        y = _year_of(m.group(2), int(m.group(3)), m.group(4))
        if y:
            out[date(y, MONTHS[m.group(4)], int(m.group(3)))] = m.group(1)
    if len(out) < 20:
        raise ValueError(f"NZ page parsed to {len(out)} public holidays (expected ~22)")
    return Listing({2026, 2027}, out)


NSW_NAMES = ["New Year's Day", "Australia Day", "Good Friday", "Easter Saturday", "Easter Sunday", "Easter Monday", "Anzac Day",
             "Additional Day", "King's Birthday", "Bank Holiday", "Labour Day", "Christmas Day", "Boxing Day"]


def parse_nsw(page: str) -> Listing:
    t = _text(page)
    i = t.find("NSW public holidays 2026 to 2027")
    if i < 0:
        raise ValueError("NSW table header not found")
    t = t[i:]
    out = {}
    pos, name = 0, None
    tok = re.compile(r"(" + "|".join(re.escape(n) for n in NSW_NAMES) + r")|(" + _WD + r") (\d{1,2}) (" + _MON + r") (2026|2027)")
    for m in tok.finditer(t):
        if m.group(1):
            name = m.group(1)
        else:
            out[date(int(m.group(5)), MONTHS[m.group(4)], int(m.group(3)))] = name or "?"
    if len(out) < 20:
        raise ValueError(f"NSW page parsed to {len(out)} dates (expected ~28)")
    return Listing({2026, 2027}, out)


def parse_boc(page: str) -> Listing:
    """BoC 'Upcoming events': the holiday closures still to come (national / provincial holiday excerpt)."""
    out = {}
    for m in re.finditer(r"media-date[^>]*>\s*(" + _MON + r") (\d{1,2}), (\d{4})</span>.*?<a [^>]*>([^<]+)</a>.*?media-excerpt'>\s*([^<]*?)\s*</div>", page, flags=re.S):
        if "holiday" in m.group(5).lower():
            d = date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
            if _in_years(d):
                out[d] = _html.unescape(m.group(4)).strip()
    if not out:
        raise ValueError("no holiday closures on the BoC upcoming-events page")
    return Listing(set(), out, partial=True)


PARSERS = {"UK": parse_uk, "JP": parse_jp, "US": parse_us, "EU": parse_eu, "CA": parse_boc, "AU": parse_nsw, "NZ": parse_nz}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def scope_rows(cal_id: str, rows: list) -> list:
    """JP: only the national holidays come from the CSV; the year-end bank holidays (statute) can never be checked there."""
    return [r for r in rows if r["source"] == URLS["JP"]] if cal_id == "JP" else rows


def check_calendar(cal_id: str, listing: Listing, rows: list, today: date) -> list:
    """Warnings for one calendar. `rows` = the calendar's holidays from cb_calendars.yaml ({date, name, verified})."""
    rows = scope_rows(cal_id, rows)
    w = []
    weekday = lambda d: d.weekday() < 5                                            # noqa: E731
    have = {r["date"]: r for r in rows}
    for d, name in sorted(listing.dates.items()):
        if weekday(d) and d not in have:
            w.append(f"HOLIDAYS {cal_id}: {d} ({name}) is on the official page but not in cb_calendars.yaml")
    if not listing.partial:
        for d, r in sorted(have.items()):
            if r["verified"] and weekday(d) and d.year in listing.years and d not in listing.dates:
                w.append(f"HOLIDAYS {cal_id}: {d} ({r['name']}) is in cb_calendars.yaml as verified but no longer on the official page")
    for y in sorted(listing.years):
        unverified = [r for r in rows if r["date"].year == y and not r["verified"]]
        listed = [d for d in listing.dates if d.year == y]
        if unverified and listed:
            w.append(f"HOLIDAYS {cal_id}: {y} has {len(unverified)} unverified date(s) and the official page now covers it - re-run "
                     f"scripts/cb_gen_calendars.py to verify them")
    return w


def check_probe(cal_id: str, year: int, exists: bool, rows: list) -> Optional[str]:
    unverified = [r for r in rows if r["date"].year == year and not r["verified"]]
    if exists and unverified:
        return (f"HOLIDAYS {cal_id}: the official {year} page now exists ({PROBES[cal_id][year]}) and {len(unverified)} {year} date(s) are "
                f"unverified - re-run scripts/cb_gen_calendars.py")
    return None


def check_signature(cal_id: str, old: Optional[dict], new: dict, rows: list) -> Optional[str]:
    """CH: SIC PDF ETag / length changed since the last check while some dates are unverified."""
    if not old or old == new:
        return None
    unverified = [r for r in rows if not r["verified"]]
    return (f"HOLIDAYS {cal_id}: the official file changed ({old.get('length')} -> {new.get('length')} bytes)"
            + (f" and {len(unverified)} date(s) are unverified" if unverified else "") + " - re-run scripts/cb_gen_calendars.py")


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

class HolidayPages(HttpSource):
    """Fetches the official listings. `listing(cal)` -> Listing or None (status in last_status / last_note)."""
    name = "cb_holiday_pages"

    def listing(self, cal_id: str) -> Optional[Listing]:
        self.last_status = self.last_note = ""
        try:
            r, _, _ = self.get_url(URLS[cal_id], ua=NON_BROWSER_UA if cal_id == "JP" else None)
            if r is None:
                return None
            if cal_id == "JP":
                return parse_jp(r.content.decode("cp932"))
            return PARSERS[cal_id](r.text)
        except Exception as e:
            self._parse_fail(f"{type(e).__name__}: {e}")
            return None

    def probe(self, url: str) -> bool:
        r, _, _ = self.get_url(url)
        self.last_status = self.last_note = ""                       # a 404 is an answer, not a failure
        return r is not None

    def signature(self, url: str) -> Optional[dict]:
        """ETag / Content-Length of a file we cannot parse (GET: the standard client has no HEAD)."""
        self.last_status = self.last_note = ""
        r, _, v = self.get_url(url)
        return None if r is None else {"etag": v.get("etag"), "length": len(r.content)}
