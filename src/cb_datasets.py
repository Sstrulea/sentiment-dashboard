"""Glue for the Central Banks datasets that are not raw market data (phase 1B-1): decisions, Fed SEP projections, the
manual RBNZ file, the Carry comparison. I/O lives here; the arithmetic is in src/cb_compute/.

  data/cb/decisions.parquet     one row per (currency, meeting_date); changes only at meetings
  data/cb/projections.parquet   Fed SEP medians + every dot (per level, per year and longer run)
  data/cb/manual/rbnz.yaml      RBNZ OCR track per MPS and the 2027 calendar (by hand: the site is bot-walled)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import pyarrow as pa
import yaml

from . import cb_store as cs
from .cb_calendar import load_calendars
from .cb_compute.decisions import SeriesView, compute_all, ff_rows_from_frame
from .cb_sources.calendar import PARSERS, CalendarPages, check_meetings
from .cb_sources import holidays as hol
from .cb_sources.fed_sep import FedSepSource, parse_sep

log = logging.getLogger("cb_datasets")

ROOT = Path(__file__).resolve().parents[1]
BANKS_YAML = ROOT / "config" / "central_banks.yaml"
FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
POLICY_RATES = ROOT / "data" / "policy_rates.yaml"

DECISIONS_SCHEMA = pa.schema([
    ("bank", pa.string()), ("currency", pa.string()), ("meeting_date", pa.date32()),
    ("decision_time_utc", pa.timestamp("us", tz="UTC")), ("rate_before", pa.float64()), ("rate_after", pa.float64()),
    ("lower", pa.float64()), ("upper", pa.float64()), ("delta_bp", pa.float64()), ("effective_date", pa.date32()),
    ("consensus", pa.float64()), ("surprise_consensus_bp", pa.float64()), ("rate_source", pa.string()),
    ("status", pa.string()), ("notes", pa.string()),
])
DECISIONS_KEY = ("currency", "meeting_date")

PROJECTIONS_SCHEMA = pa.schema([
    ("bank", pa.string()), ("meeting_date", pa.date32()), ("variable", pa.string()), ("horizon", pa.string()),
    ("kind", pa.string()), ("level", pa.float64()), ("count", pa.int32()), ("value", pa.float64()),
    ("unit", pa.string()), ("source_url", pa.string()),
])
PROJECTIONS_KEY = ("bank", "meeting_date", "variable", "horizon", "kind", "level")


def load_banks(path: Path | str | None = None) -> dict:
    return yaml.safe_load(Path(path or BANKS_YAML).read_text())["banks"]


def load_meetings(path: Path | str) -> dict:
    """{currency: [{date, first_day, has_projections, has_presser, source, verified}]}"""
    raw = yaml.safe_load(Path(path).read_text())["meetings"]
    return {b: [dict(r) for r in rows] for b, rows in raw.items()}


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------

@dataclass
class DecisionsReport:
    computed: int = 0
    written: bool = False
    total: int = 0
    by_status: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)


def ff_by_bank(banks: dict, ff: "pd.DataFrame") -> dict:
    """FF decision rows per currency, matched on (currency, name) - never on the name alone."""
    out = {}
    for cur, cfg in banks.items():
        name = cfg["policy_rate"].get("ff_name")
        sub = ff[(ff["currency"] == cur) & (ff["name_raw"] == name)] if name else ff.iloc[0:0]
        out[cur] = ff_rows_from_frame(sub)
    return out


def load_manual_decisions(path: Path) -> dict:
    """Optional last-resort entries {(currency, meeting_date): {rate_after, rate_before?, note}}."""
    if not Path(path).exists():
        return {}
    raw = yaml.safe_load(Path(path).read_text()) or {}
    return {(r["currency"], r["meeting_date"]): r for r in raw.get("decisions", [])}


def run_decisions(paths, today: date, *, banks: dict | None = None, calendars: dict | None = None,
                  meetings: dict | None = None, ff_path: Path | str | None = None, n: int = 4) -> DecisionsReport:
    from .cb_collect import load_official_store                         # late import: cb_collect imports this module
    banks = banks or load_banks()
    calendars = calendars or load_calendars()
    meetings = meetings or load_meetings(paths.meetings)
    view = SeriesView(list(load_official_store(paths).values()))
    ff = pd.read_parquet(ff_path or FF_PARQUET)
    rows, unresolved = compute_all(banks, meetings, calendars, view, ff_by_bank(banks, ff), today,
                                   load_manual_decisions(paths.manual / "decisions.yaml"), n)
    keep = {(r["currency"], r["meeting_date"]): r for r in cs.read_table_file(paths.decisions)}
    for r in rows:
        keep[(r["currency"], r["meeting_date"])] = r                    # recomputed inside the window; older rows stay
    merged = sorted(keep.values(), key=lambda r: (r["currency"], r["meeting_date"]))
    rep = DecisionsReport(computed=len(rows), total=len(merged), unresolved=unresolved)
    rep.written = cs.write_table_file(paths.decisions, DECISIONS_SCHEMA, DECISIONS_KEY, merged) if merged else False
    for r in merged:
        rep.by_status[r["status"]] = rep.by_status.get(r["status"], 0) + 1
        if r["status"] == "conflict":
            rep.conflicts.append((r["currency"], r["meeting_date"], r["notes"]))
    return rep


def load_decisions(paths) -> list:
    return cs.read_table_file(paths.decisions)


# ---------------------------------------------------------------------------
# Projections (Fed SEP)
# ---------------------------------------------------------------------------

@dataclass
class ProjectionsReport:
    sep_dates: list = field(default_factory=list)
    fetched: list = field(default_factory=list)
    failed: dict = field(default_factory=dict)
    written: bool = False
    total: int = 0


def run_projections(paths, today: date, *, source: FedSepSource | None = None,
                    now: Callable[[], datetime] | None = None, n: int = 4) -> ProjectionsReport:
    """Last `n` SEPs of the FOMC calendar page; only the ones not stored yet are fetched (the tables never change)."""
    src = source or FedSepSource(now=now)
    rep = ProjectionsReport()
    ds = src.dates(today)
    if ds is None:
        rep.failed["calendar"] = f"{src.last_status} {src.last_note}".strip()
        return rep
    rep.sep_dates = ds[-n:]
    rows = {tuple(r[k] for k in PROJECTIONS_KEY): r for r in cs.read_table_file(paths.projections)}
    have = {r["meeting_date"] for r in rows.values()}
    for d in rep.sep_dates:
        if d in have:
            continue
        page = src.page(d)
        if page is None:
            rep.failed[str(d)] = f"{src.last_status} {src.last_note}".strip()
            continue
        try:
            for r in parse_sep(page, d):
                rows[tuple(r[k] for k in PROJECTIONS_KEY)] = r
            rep.fetched.append(d)
        except Exception as e:
            rep.failed[str(d)] = f"PARSE-FAIL {type(e).__name__}: {e}"
    rep.total = len(rows)
    if rows:
        rep.written = cs.write_table_file(paths.projections, PROJECTIONS_SCHEMA, PROJECTIONS_KEY, list(rows.values()))
    return rep


def load_projections(paths) -> list:
    return cs.read_table_file(paths.projections)


# ---------------------------------------------------------------------------
# RBNZ manual file
# ---------------------------------------------------------------------------

RBNZ_STATUSES = ("placeholder", "filled")


def load_rbnz(path: Path | str) -> dict:
    return yaml.safe_load(Path(path).read_text()) if Path(path).exists() else {}


def validate_rbnz(doc: dict) -> list:
    """Schema errors of data/cb/manual/rbnz.yaml (empty list = valid)."""
    err = []
    if not isinstance(doc, dict) or not doc:
        return ["file missing or empty"]
    for k in ("meta", "published_calendar", "calendar_2027", "mps"):
        if k not in doc:
            err.append(f"missing key {k!r}")
    pub = doc.get("published_calendar") or {}
    if not pub.get("source") or not pub.get("meetings"):
        err.append("published_calendar needs a source and meetings")
    for m in pub.get("meetings") or []:
        if not isinstance(m.get("date"), date) or not isinstance(m.get("has_projections"), bool):
            err.append(f"published_calendar meeting needs date and has_projections: {m}")
    cal = doc.get("calendar_2027") or {}
    if cal.get("status") not in ("pending", "entered"):
        err.append("calendar_2027.status must be pending | entered")
    if cal.get("status") == "entered" and not cal.get("meetings"):
        err.append("calendar_2027 entered without meetings")
    for m in cal.get("meetings") or []:
        if not isinstance(m.get("date"), date) or not isinstance(m.get("has_projections"), bool):
            err.append(f"calendar_2027 meeting needs date and has_projections: {m}")
    seen = set()
    for i, e in enumerate(doc.get("mps") or []):
        tag = f"mps[{i}]"
        if not isinstance(e.get("meeting"), date):
            err.append(f"{tag}: meeting must be a date")
            continue
        if e["meeting"] in seen:
            err.append(f"{tag}: duplicate meeting {e['meeting']}")
        seen.add(e["meeting"])
        if e.get("status") not in RBNZ_STATUSES:
            err.append(f"{tag}: status must be placeholder | filled")
        if not isinstance(e.get("ocr_track"), list):
            err.append(f"{tag}: ocr_track must be a list")
        if e.get("status") == "filled":
            if not e["ocr_track"]:
                err.append(f"{tag}: filled without an ocr_track")
            src = e.get("source") or {}
            if not (src.get("url") and src.get("page")):
                err.append(f"{tag}: a filled track needs source.url and source.page")
            for pt in e["ocr_track"]:
                if not (isinstance(pt.get("period"), str) and isinstance(pt.get("value"), (int, float))):
                    err.append(f"{tag}: ocr_track point needs period (str) and value (number): {pt}")
            for pt in e.get("bank_bill_90d") or []:
                if not (isinstance(pt.get("period"), str) and isinstance(pt.get("value"), (int, float))):
                    err.append(f"{tag}: bank_bill_90d point needs period and value: {pt}")
    return err


def rbnz_warnings(doc: dict, nz_meetings: list, today: date) -> list:
    """Warnings for --status: the LATEST MPS without a filled OCR track (older MPS are not needed), an unusable file, the
    missing 2027 calendar."""
    errs = validate_rbnz(doc)
    if errs:
        return [f"RBNZ manual file invalid: {e}" for e in errs]
    w = []
    filled = {e["meeting"] for e in doc["mps"] if e["status"] == "filled"}
    placeholder = {e["meeting"] for e in doc["mps"] if e["status"] == "placeholder"}
    past = sorted(m["date"] for m in nz_meetings if m.get("has_projections") and m["date"] <= today)
    if past and past[-1] not in filled:
        kind = "placeholder" if past[-1] in placeholder else "no entry"
        w.append(f"RBNZ MPS {past[-1]}: no OCR track ({kind}) - fill data/cb/manual/rbnz.yaml")
    known = {m["date"] for m in nz_meetings} | {m["date"] for m in (doc["calendar_2027"].get("meetings") or [])}
    last = max(known) if known else None
    if doc["calendar_2027"]["status"] == "pending" and last is not None and today >= last:
        w.append(f"RBNZ calendar after {last} not entered yet (calendar_2027 is pending)")
    return w


# ---------------------------------------------------------------------------
# Carry comparison
# ---------------------------------------------------------------------------

def policy_diff(decisions: list, policy_path: Path | str | None = None) -> list:
    """Per currency: data/policy_rates.yaml (Carry) vs the latest decision of the CB module, in bp."""
    doc = yaml.safe_load(Path(policy_path or POLICY_RATES).read_text())["rates"]
    latest: dict = {}
    for r in decisions:
        cur = r["currency"]
        if cur not in latest or r["meeting_date"] > latest[cur]["meeting_date"]:
            latest[cur] = r
    out = []
    for cur in sorted(doc):
        y = doc[cur].get("rate_pct")
        d = latest.get(cur)
        out.append({"currency": cur, "carry": y, "cb": None if d is None else d["rate_after"],
                    "diff_bp": None if (y is None or d is None) else round((y - d["rate_after"]) * 100, 4),
                    "cb_status": None if d is None else d["status"], "cb_meeting": None if d is None else d["meeting_date"],
                    "cb_effective": None if d is None else d["effective_date"]})
    return out


# ---------------------------------------------------------------------------
# Stage reports (text for the log, markdown for $GITHUB_STEP_SUMMARY)
# ---------------------------------------------------------------------------

def decisions_report(rep: DecisionsReport, today: date) -> tuple:
    line = (f"decisions: {rep.computed} recomputed, {rep.total} stored, file {'written' if rep.written else 'unchanged'}; "
            + ", ".join(f"{k}={v}" for k, v in sorted(rep.by_status.items())))
    extra = [f"  CONFLICT {c} {d}: {n}" for c, d, n in rep.conflicts] + \
            [f"  UNRESOLVED {c} {d}: no source has a rate yet" for c, d in rep.unresolved]
    md = f"### decisions\n\n{line}" + ("\n\n" + "\n".join(f"- {e.strip()}" for e in extra) if extra else "")
    return "\n".join([line] + extra), md


def projections_report(rep: ProjectionsReport) -> tuple:
    if rep.failed.get("calendar"):
        line = f"projections: FOMC calendar page failed ({rep.failed['calendar']})"
    else:
        line = (f"projections: SEP dates {[str(d) for d in rep.sep_dates]}, fetched {[str(d) for d in rep.fetched]}, "
                f"{rep.total} rows, file {'written' if rep.written else 'unchanged'}")
    extra = [f"  FAILED {k}: {v}" for k, v in rep.failed.items() if k != "calendar"]
    md = f"### projections\n\n{line}" + ("\n\n" + "\n".join(f"- {e.strip()}" for e in extra) if extra else "")
    return "\n".join([line] + extra), md


# ---------------------------------------------------------------------------
# Weekly check of meetings.yaml against the official calendar pages (warnings only)
# ---------------------------------------------------------------------------

CHECK_KEY = "_calendar_check"          # state.json section {checked: date, warnings: [...], failed: {bank: why}}
CHECK_EVERY_DAYS = 7
CHECKED_BANKS = tuple(PARSERS) + ("CHF",)


@dataclass
class CalendarCheckReport:
    ran: bool = False
    checked: Optional[date] = None
    warnings: list = field(default_factory=list)
    failed: dict = field(default_factory=dict)
    signatures: dict = field(default_factory=dict)      # files that cannot be parsed (SIC PDF): ETag / length at the last check


def stored_calendar_check(state: dict) -> CalendarCheckReport:
    c = state.get(CHECK_KEY) or {}
    return CalendarCheckReport(False, date.fromisoformat(c["checked"]) if c.get("checked") else None,
                               list(c.get("warnings", [])), dict(c.get("failed", {})), dict(c.get("signatures", {})))


CALENDARS_YAML = ROOT / "config" / "cb_calendars.yaml"
HOLIDAY_CALENDARS = ("UK", "JP", "US", "EU", "CA", "AU", "NZ")


def holiday_check(rep: CalendarCheckReport, prev: CalendarCheckReport, today: date, src, rows_by_cal: dict) -> None:
    """Holiday calendars vs the official pages: dates missing from the YAML, dates that vanished, years that became
    verifiable (page appeared / file changed). Appends to `rep`; never touches the YAML."""
    for cal in HOLIDAY_CALENDARS:
        listing = src.listing(cal)
        if listing is None:
            rep.failed[f"HOLIDAYS {cal}"] = f"{getattr(src, 'last_status', '')} {getattr(src, 'last_note', '')}".strip() or "unreachable"
            continue
        rep.warnings += hol.check_calendar(cal, listing, rows_by_cal[cal], today)
    for cal, years in hol.PROBES.items():
        for year, url in years.items():
            w = hol.check_probe(cal, year, src.probe(url), rows_by_cal[cal])
            if w:
                rep.warnings.append(w)
    sig = src.signature(hol.URLS["CH"])
    if sig is None:
        rep.failed["HOLIDAYS CH"] = "SIC file unreachable"
        rep.signatures = dict(prev.signatures)
    else:
        w = hol.check_signature("CH", prev.signatures.get("CH"), sig, rows_by_cal["CH"])
        if w:
            rep.warnings.append(w)
        rep.signatures = {"CH": sig}


def run_calendar_check(paths, today: date, *, source=None, holidays=None, force: bool = False, banks: dict | None = None,
                       meetings: dict | None = None, calendars_yaml: Path | str | None = None) -> CalendarCheckReport:
    """At most once a week: compare meetings.yaml (official rows) with the bank calendar pages AND the holiday calendars
    with the official holiday pages. Warnings only - nothing is rewritten. The result lives in state.json so --status can
    show it."""
    from .cb_collect import load_state, save_state
    state = load_state(paths)
    prev = stored_calendar_check(state)
    if not force and prev.checked and (today - prev.checked).days < CHECK_EVERY_DAYS:
        return prev
    banks = banks or load_banks()
    meetings = meetings or load_meetings(paths.meetings)
    src = source or CalendarPages()
    rep = CalendarCheckReport(True, today)
    for bank in CHECKED_BANKS:
        page = src.meetings(bank, banks[bank]["calendar_url"])
        if page is None:
            rep.failed[bank] = f"{getattr(src, 'last_status', '')} {getattr(src, 'last_note', '')}".strip() or "unreachable"
            continue
        rep.warnings += check_meetings(bank, page, meetings.get(bank, []), today)
    raw = yaml.safe_load(Path(calendars_yaml or CALENDARS_YAML).read_text())["calendars"]
    holiday_check(rep, prev, today, holidays or hol.HolidayPages(), {c: v["holidays"] for c, v in raw.items()})
    new = dict(state)
    new[CHECK_KEY] = {"checked": today.isoformat(), "warnings": rep.warnings, "failed": rep.failed, "signatures": rep.signatures}
    save_state(paths, new)
    return rep


def calendar_check_report(rep: CalendarCheckReport) -> tuple:
    if not rep.ran:
        line = f"calendar check: last run {rep.checked} (weekly), {len(rep.warnings)} warning(s)"
    else:
        line = f"calendar check: ran {rep.checked}, {len(rep.warnings)} warning(s), {len(rep.failed)} page(s) unreachable"
    extra = [f"  WARN {w}" for w in rep.warnings] + [f"  UNREACHABLE {b}: {why}" for b, why in sorted(rep.failed.items())]
    md = f"### calendar check\n\n{line}" + ("\n\n" + "\n".join(f"- {e.strip()}" for e in extra) if extra else "")
    return "\n".join([line] + extra), md


# ---------------------------------------------------------------------------
# --status sections
# ---------------------------------------------------------------------------

def extra_status(paths, today: date, *, banks: dict | None = None, official_cfg: dict | None = None) -> tuple:
    """Extra --status sections for the datasets of phase 1B-1: official series, decisions, projections, RBNZ manual
    file, calendar check, Carry vs CB. A section whose file does not exist yet is left out."""
    from .cb_collect import _fmt_text, _md_table, calendar_of, load_official_store, load_state
    from .cb_sources.official import load_official
    text, md = [], []

    def add(title: str, heads: list, rows: list, notes: list | None = None) -> None:
        notes = notes or []
        body = _fmt_text(heads, rows) if rows else "(none)"
        text.append(f"\n{title}\n{body}" + ("\n" + "\n".join(f"  {n}" for n in notes) if notes else ""))
        md.append(f"\n#### {title}\n\n" + (_md_table(heads, rows) if rows else "_none_")
                  + ("\n\n" + "\n".join(f"- {n}" for n in notes) if notes else ""))

    cals = load_calendars()
    off = load_official_store(paths)
    if off:
        cfg = official_cfg or load_official()
        per: dict = {}
        for r in off.values():
            per.setdefault(r["series_id"], []).append(r)
        rows = []
        for sid, sc in cfg["series"].items():
            rs = per.get(sid)
            if not rs:
                rows.append([sid, 0, "", "", "", ""])
                continue
            days = sorted(r["date"] for r in rs)
            cal = calendar_of(cals, sc.get("calendar_id"))
            rows.append([sid, len(rs), days[0], days[-1], cal.lag(days[-1], today),
                         max(r["fetched_at"] for r in rs).strftime("%Y-%m-%d %H:%M")])
        add("official series", ["series", "rows", "first", "last date", "lag bd", "last write (UTC)"], rows,
            ["lag bd = business days between the last date and the last business day <= today in the series' calendar "
             "(BIS / SNB publish with a 3-5 day lag by design)"])

    dec = cs.read_table_file(paths.decisions)
    if dec:
        latest: dict = {}
        for r in dec:
            if r["currency"] not in latest or r["meeting_date"] > latest[r["currency"]]["meeting_date"]:
                latest[r["currency"]] = r
        rows = [[c, r["meeting_date"], r["rate_before"], r["rate_after"], r["delta_bp"], r["effective_date"],
                 "" if r["consensus"] is None else r["consensus"],
                 "" if r["surprise_consensus_bp"] is None else r["surprise_consensus_bp"], r["status"], r["rate_source"]]
                for c, r in sorted(latest.items())]
        notes = [f"CONFLICT {r['currency']} {r['meeting_date']}: {r['notes']}" for r in dec if r["status"] == "conflict"]
        notes += [f"ff_pending {r['currency']} {r['meeting_date']}: rate from FF, waiting for {'BIS' if r['currency'] in ('JPY', 'NZD') else 'the official series'}"
                  for r in dec if r["status"] == "ff_pending"]
        try:
            meetings = load_meetings(paths.meetings)
            from .cb_compute.decisions import select_meetings
            have = {(r["currency"], r["meeting_date"]) for r in dec}
            for cur, ms in sorted(meetings.items()):
                notes += [f"UNRESOLVED {cur} {m['date']}: passed but no decision row" for m in select_meetings(ms, today)
                          if (cur, m["date"]) not in have]
        except (OSError, KeyError):
            pass
        add("decisions (latest per currency)", ["cur", "meeting", "before", "after", "delta bp", "effective", "consensus",
                                                 "surprise bp", "status", "source"], rows, notes)

    prj = cs.read_table_file(paths.projections)
    if prj:
        dates = sorted({r["meeting_date"] for r in prj})
        add("projections (Fed SEP)", ["SEPs stored", "latest", "rows"], [[len(dates), dates[-1], len(prj)]])

    rb = load_rbnz(paths.manual / "rbnz.yaml")
    if rb:
        try:
            nz = load_meetings(paths.meetings).get("NZD", [])
        except OSError:
            nz = []
        add("RBNZ manual file", ["MPS entries", "filled", "placeholders"],
            [[len(rb.get("mps", [])), sum(1 for e in rb.get("mps", []) if e.get("status") == "filled"),
              sum(1 for e in rb.get("mps", []) if e.get("status") == "placeholder")]],
            [f"WARN {w}" for w in rbnz_warnings(rb, nz, today)])

    chk = stored_calendar_check(load_state(paths))
    if chk.checked:
        age = (today - chk.checked).days
        add("meetings calendar check (weekly, warnings only)", ["last check", "age days", "warnings", "unreachable"],
            [[chk.checked, age, len(chk.warnings), len(chk.failed)]],
            [f"WARN {w}" for w in chk.warnings] + [f"UNREACHABLE {b}: {why}" for b, why in sorted(chk.failed.items())]
            + ([f"WARN the check is {age} days old (runs weekly)"] if age > CHECK_EVERY_DAYS + 1 else []))

    if dec and Path(POLICY_RATES).exists():
        diff = policy_diff(dec)
        add("Carry (data/policy_rates.yaml) vs CB module", ["cur", "carry", "cb", "diff bp", "cb status", "cb meeting", "cb effective"],
            [[d["currency"], d["carry"], "" if d["cb"] is None else d["cb"], "" if d["diff_bp"] is None else d["diff_bp"],
              d["cb_status"] or "", d["cb_meeting"] or "", d["cb_effective"] or ""] for d in diff],
            [f"DIFF {d['currency']}: Carry {d['carry']} vs CB {d['cb']} ({d['diff_bp']} bp)" for d in diff if d["diff_bp"]])
    return "\n".join(text), "\n".join(md)
