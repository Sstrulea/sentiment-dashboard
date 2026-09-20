"""Generate config/cb_calendars.yaml (bank / market holidays 2026-2027 for the Central Banks module).

    python scripts/cb_gen_calendars.py [--out config/cb_calendars.yaml]

Machine-readable official sources are fetched live: UK (gov.uk bank-holidays.json, england-and-wales) and JP (Cabinet
Office syukujitsu.csv). The other calendars are tables transcribed from the official pages on 2026-09-20 (page URL on
every date; see SOURCES). `verified: true` = the date was read from that page; `verified: false` = derived from the
statutory rule because the official page does not list that year yet (BoC / SNB-SIC 2027, JP year-end bank holidays).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import date, timedelta
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
UA = "macro-data-analysis/1.0 (+https://github.com/Sstrulea/macro-data-analysis)"
YEARS = (2026, 2027)


def easter(y: int) -> date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
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


def h(y, m, d, name, src, verified=True):
    return {"date": date(y, m, d), "name": name, "source": src, "verified": verified}


# ---------------------------------------------------------------------------
# Machine-readable
# ---------------------------------------------------------------------------

def uk() -> list:
    url = "https://www.gov.uk/bank-holidays.json"
    ev = requests.get(url, headers={"User-Agent": UA}, timeout=30).json()["england-and-wales"]["events"]
    return [{"date": date.fromisoformat(e["date"]), "name": e["title"], "source": url, "verified": True}
            for e in ev if int(e["date"][:4]) in YEARS]


JP_NAMES = {"元日": "New Year's Day", "成人の日": "Coming of Age Day", "建国記念の日": "National Foundation Day",
            "天皇誕生日": "Emperor's Birthday", "春分の日": "Vernal Equinox Day", "昭和の日": "Showa Day",
            "憲法記念日": "Constitution Memorial Day", "みどりの日": "Greenery Day", "こどもの日": "Children's Day",
            "海の日": "Marine Day", "山の日": "Mountain Day", "敬老の日": "Respect for the Aged Day",
            "秋分の日": "Autumnal Equinox Day", "スポーツの日": "Sports Day", "文化の日": "Culture Day",
            "勤労感謝の日": "Labor Thanksgiving Day", "休日": "Substitute / citizens' holiday"}


def jp() -> list:
    url = "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv"
    raw = requests.get(url, headers={"User-Agent": UA}, timeout=30).content.decode("cp932")
    out = []
    for r in list(csv.reader(io.StringIO(raw)))[1:]:
        y, m, d = (int(x) for x in r[0].split("/"))
        if y in YEARS:
            out.append({"date": date(y, m, d), "name": JP_NAMES[r[1]], "source": url, "verified": True})
    bank = "https://laws.e-gov.go.jp/law/357CO0000000040"       # Banking Act Enforcement Order, art. 5 (not fetched)
    for y in YEARS:
        for m, d in ((1, 2), (1, 3), (12, 31)):
            out.append({"date": date(y, m, d), "name": "Bank holiday (year-end / New Year, 31 Dec - 3 Jan)",
                        "source": bank, "verified": False})
    return out


# ---------------------------------------------------------------------------
# Transcribed from official pages (2026-09-20)
# ---------------------------------------------------------------------------

def us() -> list:
    s = "https://www.federalreserve.gov/aboutthefed/k8.htm"       # K.8, incl. the Saturday / Sunday footnotes
    return [
        h(2026, 1, 1, "New Year's Day", s), h(2026, 1, 19, "Birthday of Martin Luther King, Jr.", s),
        h(2026, 2, 16, "Washington's Birthday", s), h(2026, 5, 25, "Memorial Day", s),
        h(2026, 6, 19, "Juneteenth National Independence Day", s),
        h(2026, 7, 3, "Independence Day (observed; 4 Jul is a Saturday)", s), h(2026, 9, 7, "Labor Day", s),
        h(2026, 10, 12, "Columbus Day", s), h(2026, 11, 11, "Veterans Day", s), h(2026, 11, 26, "Thanksgiving Day", s),
        h(2026, 12, 25, "Christmas Day", s),
        h(2027, 1, 1, "New Year's Day", s), h(2027, 1, 18, "Birthday of Martin Luther King, Jr.", s),
        h(2027, 2, 15, "Washington's Birthday", s), h(2027, 5, 31, "Memorial Day", s),
        h(2027, 6, 18, "Juneteenth National Independence Day (observed; 19 Jun is a Saturday)", s),
        h(2027, 7, 5, "Independence Day (observed; 4 Jul is a Sunday)", s), h(2027, 9, 6, "Labor Day", s),
        h(2027, 10, 11, "Columbus Day", s), h(2027, 11, 11, "Veterans Day", s), h(2027, 11, 25, "Thanksgiving Day", s),
        h(2027, 12, 24, "Christmas Day (observed; 25 Dec is a Saturday)", s),
        h(2027, 12, 31, "New Year's Day 2028 (observed; 1 Jan 2028 is a Saturday)", s),
    ]


def eu() -> list:
    s = "https://www.ecb.europa.eu/paym/target/t2/html/index.en.html"      # T2 closing days for euro settlement
    out = []
    for y in YEARS:
        e = easter(y)
        out += [h(y, 1, 1, "New Year's Day", s), h(*(e - timedelta(days=2)).timetuple()[:3], "Good Friday", s),
                h(*(e + timedelta(days=1)).timetuple()[:3], "Easter Monday", s), h(y, 5, 1, "Labour Day", s),
                h(y, 12, 25, "Christmas Day", s), h(y, 12, 26, "Boxing Day", s)]
    return out


def ca() -> list:
    sched = "https://www.bankofcanada.ca/press/upcoming-events/bank-of-canada-holiday-schedule/"
    ev = "https://www.bankofcanada.ca/{y}/{m:02d}/{slug}/"                # one event page per closure (month in the URL)
    out = [
        h(2026, 1, 1, "New Year's Day", ev.format(y=2026, m=1, slug="new-years-day")),
        h(2026, 1, 2, "Day after New Year", ev.format(y=2026, m=1, slug="day-after-new-year")),
        h(2026, 2, 16, "Family Day", ev.format(y=2026, m=2, slug="family-day")),
        h(2026, 4, 3, "Good Friday", ev.format(y=2026, m=4, slug="good-friday")),
        h(2026, 5, 18, "Victoria Day", ev.format(y=2026, m=5, slug="victoria-day")),
        h(2026, 6, 24, "Provincial Holiday", ev.format(y=2026, m=6, slug="provincial-holiday"), False),   # day not on page
        h(2026, 7, 1, "Canada Day", ev.format(y=2026, m=7, slug="canada-day")),
        h(2026, 8, 3, "Civic Holiday", ev.format(y=2026, m=8, slug="civic-holiday")),
        h(2026, 9, 7, "Labour Day", ev.format(y=2026, m=9, slug="labour-day")),
        h(2026, 9, 30, "National Day for Truth and Reconciliation", sched),
        h(2026, 10, 12, "Thanksgiving Day", sched), h(2026, 11, 11, "Remembrance Day", sched),
        h(2026, 12, 25, "Christmas Day", sched), h(2026, 12, 28, "Boxing Day (observed)", sched),
    ]
    # 2027 is not published yet: statutory rule, marked unverified
    out += [h(2027, 1, 1, "New Year's Day", sched, False), h(2027, 2, 15, "Family Day", sched, False),
            h(2027, 3, 26, "Good Friday", sched, False), h(2027, 5, 24, "Victoria Day", sched, False),
            h(2027, 7, 1, "Canada Day", sched, False), h(2027, 8, 2, "Civic Holiday", sched, False),
            h(2027, 9, 6, "Labour Day", sched, False), h(2027, 9, 30, "National Day for Truth and Reconciliation", sched, False),
            h(2027, 10, 11, "Thanksgiving Day", sched, False), h(2027, 11, 11, "Remembrance Day", sched, False),
            h(2027, 12, 27, "Christmas Day (observed)", sched, False), h(2027, 12, 28, "Boxing Day (observed)", sched, False)]
    return out


def au() -> list:
    r26 = "https://www.rba.gov.au/schedules-events/bank-holidays-2026.html"      # New South Wales rows (RBA Sydney)
    n = "https://www.nsw.gov.au/about-nsw/public-holidays"
    return [
        h(2026, 1, 1, "New Year's Day", r26), h(2026, 1, 26, "Australia Day", r26), h(2026, 4, 3, "Good Friday", r26),
        h(2026, 4, 6, "Easter Monday", r26), h(2026, 4, 27, "Additional Day (Anzac Day)", r26),
        h(2026, 6, 8, "King's Birthday", r26), h(2026, 8, 3, "Bank Holiday", r26), h(2026, 10, 5, "Labour Day", r26),
        h(2026, 12, 25, "Christmas Day", r26), h(2026, 12, 28, "Additional Day (Boxing Day)", r26),
        h(2027, 1, 1, "New Year's Day", n), h(2027, 1, 26, "Australia Day", n), h(2027, 3, 26, "Good Friday", n),
        h(2027, 3, 29, "Easter Monday", n), h(2027, 4, 26, "Additional Day (Anzac Day)", n),
        h(2027, 6, 14, "King's Birthday", n), h(2027, 8, 2, "Bank Holiday", n), h(2027, 10, 4, "Labour Day", n),
        h(2027, 12, 27, "Additional Day (Christmas Day)", n), h(2027, 12, 28, "Additional Day (Boxing Day)", n),
    ]


def nz() -> list:
    s = "https://www.employment.govt.nz/leave-and-holidays/public-holidays/public-holidays-and-anniversary-dates/"
    return [
        h(2026, 1, 1, "New Year's Day", s), h(2026, 1, 2, "Day after New Year's Day", s), h(2026, 2, 6, "Waitangi Day", s),
        h(2026, 4, 3, "Good Friday", s), h(2026, 4, 6, "Easter Monday", s), h(2026, 4, 27, "Anzac Day (observed)", s),
        h(2026, 6, 1, "King's Birthday", s), h(2026, 7, 10, "Matariki", s), h(2026, 10, 26, "Labour Day", s),
        h(2026, 12, 25, "Christmas Day", s), h(2026, 12, 28, "Boxing Day (observed)", s),
        h(2027, 1, 1, "New Year's Day", s), h(2027, 1, 4, "Day after New Year's Day (observed)", s),
        h(2027, 2, 8, "Waitangi Day (observed)", s), h(2027, 3, 26, "Good Friday", s), h(2027, 3, 29, "Easter Monday", s),
        h(2027, 4, 26, "Anzac Day (observed)", s), h(2027, 6, 7, "King's Birthday", s), h(2027, 6, 25, "Matariki", s),
        h(2027, 10, 25, "Labour Day", s), h(2027, 12, 27, "Christmas Day (observed)", s),
        h(2027, 12, 28, "Boxing Day (observed)", s),
    ]


def ch() -> list:
    s = "https://www.six-group.com/dam/download/banking-services/interbank-clearing/en/payment_services/sic/banking-holidays.pdf"
    out = [
        h(2026, 1, 1, "New Year's Day", s), h(2026, 1, 2, "Berchtold's Day", s), h(2026, 4, 3, "Good Friday", s),
        h(2026, 4, 6, "Easter Monday", s), h(2026, 5, 1, "Labor Day", s), h(2026, 5, 14, "Ascension Day", s),
        h(2026, 5, 25, "Whit Monday", s), h(2026, 8, 1, "Swiss National Day", s), h(2026, 12, 25, "Christmas Day", s),
        h(2026, 12, 26, "St. Stephen's Day", s), h(2027, 1, 1, "New Year's Day", s), h(2027, 1, 2, "Berchtold's Day", s),
    ]
    e = easter(2027)                                                     # the SIC list stops at 2 Jan 2027: rule-derived
    out += [h(*(e - timedelta(days=2)).timetuple()[:3], "Good Friday", s, False),
            h(*(e + timedelta(days=1)).timetuple()[:3], "Easter Monday", s, False),
            h(2027, 5, 1, "Labor Day", s, False), h(*(e + timedelta(days=39)).timetuple()[:3], "Ascension Day", s, False),
            h(*(e + timedelta(days=50)).timetuple()[:3], "Whit Monday", s, False),
            h(2027, 8, 1, "Swiss National Day", s, False), h(2027, 12, 25, "Christmas Day", s, False),
            h(2027, 12, 26, "St. Stephen's Day", s, False)]
    return out


CALENDARS = {
    "US": ("Federal Reserve Board / US federal holidays as observed (government-securities market)", us,
           "Fed K.8: when a holiday falls on a Saturday the Reserve Banks stay open the Friday before but the Board and "
           "the securities markets are closed - the observed Friday is listed (3 Jul 2026, 18 Jun 2027, 24 Dec 2027, "
           "31 Dec 2027); Sunday holidays move to the Monday."),
    "UK": ("UK bank holidays (England and Wales)", uk, "gov.uk bank-holidays.json."),
    "EU": ("TARGET (T2) closing days", eu, "New Year's Day, Good Friday, Easter Monday, 1 May, 25 and 26 December."),
    "JP": ("Japan national holidays + bank year-end / New Year closure", jp,
           "National holidays from the Cabinet Office CSV; 31 Dec and 2-3 Jan are bank holidays (statute, not fetched)."),
    "CA": ("Bank of Canada holiday closures", ca,
           "BoC lists national and provincial closures; 2027 is not published yet (rule-derived)."),
    "AU": ("New South Wales public holidays + bank holiday (RBA Sydney)", au,
           "RBA page for 2026, NSW Government page for 2027 (RBA has not published 2027)."),
    "NZ": ("New Zealand national public holidays (observed dates)", nz,
           "National holidays only; regional anniversary days are not modelled."),
    "CH": ("Swiss bank holidays (SIC value dates)", ch,
           "SIC list: 24 and 31 December are value days. The list ends at 2 Jan 2027; the rest of 2027 is rule-derived."),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "config" / "cb_calendars.yaml"))
    a = ap.parse_args()
    lines = [
        "# Bank / market holiday calendars 2026-2027 for the Central Banks module. GENERATED by scripts/cb_gen_calendars.py",
        "# on 2026-09-20 - do not edit by hand. Every date carries its official source; `verified: false` = derived from the",
        "# statutory rule because the official page does not list that year yet.",
        "# Weekends are non-business days in every calendar; only weekday closures matter, weekend ones are kept for",
        "# completeness. Outside 2026-2027 a calendar knows only weekends (src/cb_calendar.py reports the coverage).",
        "",
        "meta: {version: 1, generated: 2026-09-20, years: [2026, 2027]}",
        "",
        "calendars:",
    ]
    for cid, (name, fn, note) in CALENDARS.items():
        rows = sorted(fn(), key=lambda r: r["date"])
        assert len({r["date"] for r in rows}) == len(rows), f"{cid}: duplicate dates"
        lines += [f"  {cid}:", f"    name: {json.dumps(name)}", f"    note: {json.dumps(note)}", "    holidays:"]
        for r in rows:
            lines.append(f"      - {{date: {r['date'].isoformat()}, name: {json.dumps(r['name'], ensure_ascii=False)}, "
                         f"source: {r['source']}, verified: {str(r['verified']).lower()}}}")
        lines.append("")
    Path(a.out).write_text("\n".join(lines))
    yaml.safe_load(Path(a.out).read_text())          # must parse
    print(f"wrote {a.out}: " + ", ".join(f"{c}={len(fn())}" for c, (_, fn, _) in CALENDARS.items()))


if __name__ == "__main__":
    main()
