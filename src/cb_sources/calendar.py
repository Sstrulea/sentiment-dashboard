"""Official meeting-calendar pages -> meetings, and the weekly check of data/cb/meetings.yaml against them.

Parsers are extracted from the A5 probes (src/cb_probe.py stays as the spike record). `check_meetings` only WARNS:
new / changed / missing dates, a changed first day, a changed projections flag where the page states it. It never
rewrites meetings.yaml. RBNZ has no parser (the site is behind a Cloudflare challenge; its dates are manual).
"""
from __future__ import annotations

import calendar as _cal
import html as _html
import re
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from .base import HttpSource, NON_BROWSER_UA

MONTHS = {m.lower(): i for i, m in enumerate(_cal.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(_cal.month_abbr) if m})
MONTHS["sept"] = 9

# banks whose calendar page states the projections flag itself (the others derive it from the bank's practice)
PAGE_STATES_PROJECTIONS = {"USD", "GBP", "JPY", "CAD"}
# banks whose page lists only upcoming meetings: a past meeting missing from the page is not a change
UPCOMING_ONLY = {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"}
MULTI_DAY = {"USD", "EUR", "JPY", "AUD"}


def _text(s: str) -> str:
    s = re.sub(r"<script.*?</script>|<style.*?</style>", " ", s, flags=re.S)
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", s)))


def _tables(s: str) -> list:
    out = []
    for tb in re.findall(r"<table.*?</table>", s, flags=re.S):
        out.append([[_text(c).strip() for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, flags=re.S)]
                    for tr in re.findall(r"<tr.*?</tr>", tb, flags=re.S)])
    return out


def _dm(day: int, mon: str, year: int) -> date:
    return date(year, MONTHS[mon.lower().rstrip(".")], day)


def _m(decision: date, first_day: Optional[date] = None, projections: Optional[bool] = None) -> dict:
    return {"date": decision, "first_day": first_day, "has_projections": projections}


_MON = "January|February|March|April|May|June|July|August|September|October|November|December"


def parse_fed(page: str) -> list:
    txt = _text(page)
    out = []
    for m in re.finditer(r"(\d{4}) FOMC Meetings(.*?)(?=\d{4} FOMC Meetings|\* Meeting associated|\Z)", txt):
        year, blk = int(m.group(1)), m.group(2)
        for mm in re.finditer(rf"({_MON}) (\d{{1,2}})(?:-(\d{{1,2}}))?(\*?)(?![\d,])", blk):
            mon, d1, d2, star = mm.groups()
            out.append(_m(_dm(int(d2 or d1), mon, year), _dm(int(d1), mon, year) if d2 else None, bool(star)))
    return out


def parse_ecb(page: str) -> list:
    txt = _text(page)
    day1 = {datetime.strptime(d, "%d/%m/%Y").date() for d in re.findall(
        r"(\d{2}/\d{2}/\d{4}) Governing Council of the ECB: monetary policy meeting[^0-9]{0,40}\(Day 1\)", txt)}
    out = []
    for d in re.findall(r"(\d{2}/\d{2}/\d{4}) Governing Council of the ECB: monetary policy meeting[^0-9]{0,60}\(Day 2\)", txt):
        dd = datetime.strptime(d, "%d/%m/%Y").date()
        out.append(_m(dd, dd - timedelta(days=1) if (dd - timedelta(days=1)) in day1 else None))
    return out


def parse_boe(page: str) -> list:
    txt = _text(page)
    out = []
    for year, blk in ((2026, txt[txt.find("2026 confirmed dates"):txt.find("2027 provisional dates")]),
                      (2027, txt[txt.find("2027 provisional dates"):])):
        if not blk:
            continue
        for mm in re.finditer(r"(?:Monday|Tuesday|Wednesday|Thursday|Friday) (\d{1,2}) ([A-Z][a-z]+) (.*?)"
                              r"(?=(?:Monday|Tuesday|Wednesday|Thursday|Friday) \d{1,2} [A-Z][a-z]+ |Current Bank Rate|"
                              r"Monetary Policy Committee voting|Monetary Policy Committee Reports|2027 provisional|$)", blk):
            d, mon, desc = mm.groups()
            out.append(_m(_dm(int(d), mon, year), None, "Monetary Policy Report" in desc))
    return out


def parse_boj(page: str) -> list:
    out = []
    for year, tb in zip((2026, 2027), [t for t in _tables(page) if t and t[0] and "MPM" in " ".join(t[0])][:2]):
        for row in tb[1:]:
            if not row or not re.match(r"[A-Z][a-z]{2,4}\.? \d", row[0]):
                continue
            mm = re.match(r"([A-Z][a-z]{2,4})\.? (\d{1,2}) \([^)]*\)(?:, (\d{1,2}))?", row[0])
            if not mm:
                continue
            mon, d1, d2 = mm.groups()
            outlook = len(row) > 1 and bool(re.match(r"[A-Z][a-z]{2,4}\.? \d", row[1]))
            out.append(_m(_dm(int(d2 or d1), mon, year), _dm(int(d1), mon, year) if d2 else None, outlook))
    return out


def parse_boc(page: str) -> list:
    txt = _text(page)
    out = []
    for m in re.finditer(r"Schedule for (\d{4}) Dates Publications(.*?)(?=Schedule for \d{4}|\Z)", txt):
        year = int(m.group(1))
        for mm in re.finditer(rf"({_MON}) (\d{{1,2}}) Interest rate announcement( and Monetary Policy Report)?", m.group(2)):
            mon, d, mpr = mm.groups()
            out.append(_m(_dm(int(d), mon, year), None, bool(mpr)))
    return out


def parse_rba(page: str) -> list:
    txt = _text(page)
    out = []
    for m in re.finditer(r"Board meeting schedules (\d{4}) Month Monetary Policy Board(.*?)(?=Board meeting schedules \d{4}|Related Information|\Z)", txt):
        year = int(m.group(1))
        for mm in re.finditer(r"(\d{1,2})[–-](\d{1,2}) ([A-Z][a-z]+)", m.group(2)):
            d1, d2, mon = mm.groups()
            mo = MONTHS[mon.lower()]
            out.append(_m(date(year, mo, int(d2)), date(year, mo, int(d1))))
    return out


def parse_snb(schedule_page: str, archive_page: Optional[str]) -> list:
    out = []
    if archive_page:
        for d, mon, y in re.findall(r"Monetary policy assessment of (\d{1,2}) ([A-Z][a-z]+) (\d{4})", _text(archive_page)):
            out.append(_m(_dm(int(d), mon, int(y))))
    for d in re.findall(r"(\d{2}\.\d{2}\.\d{4}) \d{2}:\d{2} Monetary policy assessment of [^()]*\(press release\)", _text(schedule_page)):
        out.append(_m(datetime.strptime(d, "%d.%m.%Y").date()))
    return out


PARSERS: dict = {"USD": parse_fed, "EUR": parse_ecb, "GBP": parse_boe, "JPY": parse_boj, "CAD": parse_boc, "AUD": parse_rba}
SNB_ARCHIVE = "https://www.snb.ch/en/the-snb/mandates-goals/monetary-policy/decisions"


class CalendarPages(HttpSource):
    """Fetches the official calendar pages (URL from config `calendar_url`)."""
    name = "cb_calendar_pages"

    def meetings(self, bank: str, url: str) -> Optional[list]:
        self.last_status = self.last_note = ""
        try:
            r, _, _ = self.get_url(url, ua=NON_BROWSER_UA if bank == "AUD" else None)
            if r is None:
                return None
            if bank == "CHF":
                a, _, _ = self.get_url(SNB_ARCHIVE)
                return parse_snb(r.text, a.text if a is not None else None)
            return PARSERS[bank](r.text)
        except Exception as e:
            self._parse_fail(f"{type(e).__name__}: {e}")
            return None


def _in_scope(bank: str, d: date) -> bool:
    return date(2026, 1, 1) <= d <= date(2027, 12, 31) or (bank == "CHF" and d >= date(2025, 9, 1))


def check_meetings(bank: str, page: list, rows: list, today: date) -> list:
    """Warnings for one bank: meetings.yaml (official rows) vs the parsed page."""
    w = []
    page = [m for m in page if _in_scope(bank, m["date"])]
    by_page = {m["date"]: m for m in page}
    official = {r["date"]: r for r in rows if r.get("source") == "official"}
    if not page:
        return [f"{bank}: the calendar page parsed to no meeting in scope (layout changed?)"]
    for d, m in sorted(by_page.items()):
        if d not in official:
            known = next((r for r in rows if r["date"] == d), None)
            w.append(f"{bank}: {d} is on the official page but " + (f"meetings.yaml has it as source={known['source']}" if known else "not in meetings.yaml"))
    for d, r in sorted(official.items()):
        if d not in by_page:
            if d >= today:
                w.append(f"{bank}: {d} is in meetings.yaml but no longer on the official page (moved or cancelled?)")
            continue
        m = by_page[d]
        if bank in MULTI_DAY and m["first_day"] and r.get("first_day") != m["first_day"]:
            w.append(f"{bank}: {d} first_day is {m['first_day']} on the page, {r.get('first_day')} in meetings.yaml")
        if bank in PAGE_STATES_PROJECTIONS and m["has_projections"] is not None and bool(m["has_projections"]) != bool(r["has_projections"]):
            w.append(f"{bank}: {d} has_projections is {m['has_projections']} on the page, {r['has_projections']} in meetings.yaml")
    return w
