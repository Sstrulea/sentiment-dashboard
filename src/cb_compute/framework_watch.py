"""Framework watch (stance phase S0a) - the pure part: page normalisation and hash, keyword hits, open alerts.

Detection only: an alert asks a human to look; nothing here (or in src/cb_framework.py) ever writes the mandate,
neutral or forecast files. No I/O: the caller (src/cb_framework.py) fetches, stores the snapshots and passes everything in.

Alerts (all with a link):
  page_changed          the normalised text of a watched page hashes differently from the hash confirmed at the last
                        review (data/cb/manual/framework_reviews.yaml)
  keyword_hit           a newly collected official text (phase 2a) contains a change-signalling phrase and its doc_id has
                        not been reviewed
  calendar_due          a scheduled review (config/cb_framework.yaml `calendar`) is within +-window days (year precision:
                        during that year)
  verification_overdue  the bank's mandate (data/cb/manual/mandates.yaml last_verified) is older than the bank's latest
                        projection publication, or older than the maximum age
  rbnz_manual           the RBNZ site is bot-walled: a reminder at each Monetary Policy Statement not yet reviewed by hand
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Optional

ALERT_KINDS = ("page_changed", "keyword_hit", "calendar_due", "verification_overdue", "rbnz_manual")

# display furniture that changes without the framework changing: dates of display, "last updated", page counters
VOLATILE = re.compile(
    r"^(?:Last (?:updated|modified|reviewed)\b.*|Page last (?:updated|reviewed)\b.*|Updated:?\s.*|Published:?\s.*|"
    r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,? .*\d{4}$|\d{1,2} [A-Z][a-z]+ \d{4}$|[A-Z][a-z]+ \d{1,2}, \d{4}$|"
    r"[A-Z][a-z]{2}\. \d{1,2}, \d{4}$|\d{4}-\d{2}-\d{2}$|Back to top|Share|Print|Copyright.*|©.*)$", re.I)


def normalise(paras: Iterable[str], *, select: Optional[str] = None, drop: Optional[str] = None) -> list:
    """The paragraphs that define the page: whitespace collapsed, display furniture and dates removed; `select` keeps only
    the matching paragraphs (a listing page whose other entries move every week), `drop` removes the matching ones."""
    sel = re.compile(select, re.I) if select else None
    drp = re.compile(drop, re.I) if drop else None
    out = []
    for p in paras:
        t = re.sub(r"\s+", " ", p or "").strip()
        if not t or VOLATILE.match(t):
            continue
        if drp is not None and drp.search(t):
            continue
        if sel is not None and not sel.search(t):
            continue
        out.append(t)
    return out


def text_hash(paras: list) -> str:
    return hashlib.sha256("\n".join(paras).encode("utf-8")).hexdigest()


def keyword_hits(text: str, keywords: Iterable[str]) -> list:
    """The configured phrases found in `text` (case-insensitive, whole words, any run of whitespace between words)."""
    hits = []
    for k in keywords:
        rx = r"\b" + r"\s+".join(re.escape(w) for w in k.split()) + r"\b"
        if re.search(rx, text or "", re.I):
            hits.append(k)
    return hits


@dataclass(frozen=True)
class Alert:
    kind: str
    bank: str
    ref: str                 # watch target id, doc_id or calendar event
    url: str
    detail: str
    redline: dict = field(default_factory=dict, compare=False, hash=False)


def page_alerts(targets: list, current: dict, reviews: dict) -> list:
    """targets: [{id, bank, url, manual_only?}]; current: {id: {hash, checked}}; reviews: {id: {confirmed_hash, ...}}."""
    out = []
    for t in targets:
        if t.get("manual_only"):
            continue
        cur = (current.get(t["id"]) or {}).get("hash")
        conf = (reviews.get(t["id"]) or {}).get("confirmed_hash")
        if cur is None:
            continue
        if cur != conf:
            out.append(Alert("page_changed", t["bank"], t["id"], t["url"],
                             f"normalised text changed: hash {cur[:12]} vs confirmed {(conf or 'none')[:12]} - review the page, then update framework_reviews.yaml"))
    return out


def keyword_alerts(flags: dict, reviewed: Iterable[str]) -> list:
    """flags: {doc_id: {currency, title, url, hits, published}}; reviewed: doc_ids already reviewed."""
    seen = set(reviewed)
    return [Alert("keyword_hit", f["currency"], d, f.get("url") or "", f"{f.get('published', '')} '{(f.get('title') or '')[:90]}': " + ", ".join(f["hits"]))
            for d, f in sorted(flags.items()) if d not in seen]


def calendar_alerts(calendar: dict, today: date, window_days: int) -> list:
    """calendar: {bank: [{event, due, precision: day|month|year, url}]}; precision year = the whole year."""
    out = []
    for bank, events in sorted(calendar.items()):
        for e in events or []:
            due = e.get("due")
            if due is None:
                continue
            prec = e.get("precision", "day")
            if prec == "year":
                hit = today.year == int(str(due)[:4])
            else:
                d = due if isinstance(due, date) else date.fromisoformat(str(due) + ("-01" if prec == "month" else ""))
                hit = abs((today - d).days) <= window_days
            if hit:
                out.append(Alert("calendar_due", bank, e["event"], e.get("url") or "", f"scheduled review due {due} ({prec}): {e['event']}"))
    return out


def verification_alerts(last_verified: dict, last_projection: dict, today: date, max_days: int) -> list:
    """last_verified: {bank: date}; last_projection: {bank: date of the latest projection publication <= today}."""
    out = []
    for bank, lv in sorted(last_verified.items()):
        lp = last_projection.get(bank)
        why = []
        if lv is None:
            why.append("never verified")
        else:
            if lp is not None and lv < lp:
                why.append(f"last verified {lv}, before the projection publication of {lp}")
            if (today - lv).days > max_days:
                why.append(f"last verified {lv}, more than {max_days} days ago")
        if why:
            out.append(Alert("verification_overdue", bank, "mandate", "", "; ".join(why) + " - re-check target, measures and the projected measure"))
    return out


def rbnz_alerts(mps_dates: list, reviewed_mps: Optional[date], today: date, url: str = "") -> list:
    """A reminder for each RBNZ Monetary Policy Statement (date <= today) newer than the last one reviewed by hand."""
    past = sorted(d for d in mps_dates if d <= today)
    if past and (reviewed_mps is None or past[-1] > reviewed_mps):
        return [Alert("rbnz_manual", "NZD", "NZD.framework", url,
                      f"MPS {past[-1]} not reviewed by hand (last reviewed MPS {reviewed_mps}); rbnz.govt.nz is bot-walled - check the Remit and the MPS by hand")]
    return []


def last_projection_dates(meetings: dict, today: date) -> dict:
    """{bank: latest meeting with projections <= today} from meetings.yaml rows."""
    out = {}
    for bank, rows in meetings.items():
        past = [r["date"] for r in rows if r.get("has_projections") and r["date"] <= today]
        if past:
            out[bank] = max(past)
    return out


def within_days(d: Optional[date], today: date, days: int) -> bool:
    return d is not None and today - d < timedelta(days=days)
