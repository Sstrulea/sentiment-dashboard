"""Glue for the Central Banks datasets that are not raw market data (phase 1B-1): decisions, Fed SEP projections, the
manual RBNZ file, the Carry comparison. I/O lives here; the arithmetic is in src/cb_compute/.

  data/cb/decisions.parquet     one row per (currency, meeting_date); changes only at meetings
  data/cb/projections.parquet   Fed SEP medians + every dot (per level, per year and longer run)
  data/cb/manual/rbnz.yaml      RBNZ OCR track per MPS and the 2027 calendar (by hand: the site is bot-walled)
"""
from __future__ import annotations

import logging
import re
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
from .ff_corrections import read_ff_parquet
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


def statement_rates(paths) -> dict:
    """{(currency, meeting_date): {after, lower, upper, doc_id}} - the rates parsed from the banks' own statements (phase 2a)."""
    from .cb_docs import store as dst                                   # late import: cb_docs.collect imports this module
    return {(d["currency"], d["meeting_date"]): {"after": d["rate_after"], "lower": d["rate_lower"], "upper": d["rate_upper"], "doc_id": d["doc_id"]}
            for d in dst.load_documents(paths).values() if d["type"] == "statement" and d["rate_after"] is not None and d["meeting_date"]}


def run_decisions(paths, today: date, *, banks: dict | None = None, calendars: dict | None = None,
                  meetings: dict | None = None, ff_path: Path | str | None = None, n: int = 4, since: date | None = None) -> DecisionsReport:
    from .cb_collect import load_official_store                         # late import: cb_collect imports this module
    banks = banks or load_banks()
    calendars = calendars or load_calendars()
    meetings = meetings or load_meetings(paths.meetings)
    view = SeriesView(list(load_official_store(paths).values()))
    ff = read_ff_parquet(ff_path or FF_PARQUET)
    rows, unresolved = compute_all(banks, meetings, calendars, view, ff_by_bank(banks, ff), today,
                                   load_manual_decisions(paths.manual / "decisions.yaml"), n, statement_rates(paths), since)
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
RBNZ_PERIOD = re.compile(r"^\d{4}Q[1-4]$")


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
                elif not RBNZ_PERIOD.match(pt["period"]):
                    err.append(f"{tag}: ocr_track period must look like 2026Q4: {pt['period']!r}")
            for pt in e.get("bank_bill_90d") or []:
                if not (isinstance(pt.get("period"), str) and isinstance(pt.get("value"), (int, float))):
                    err.append(f"{tag}: bank_bill_90d point needs period and value: {pt}")
                elif not RBNZ_PERIOD.match(pt["period"]):
                    err.append(f"{tag}: bank_bill_90d period must look like 2026Q4: {pt['period']!r}")
            periods = [pt.get("period") for pt in e["ocr_track"]]
            if len(set(periods)) != len(periods):
                err.append(f"{tag}: ocr_track has a duplicate period")
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
    """Per currency: data/policy_rates.yaml (the /carry manual fallback — /carry reads
    decisions.parquet first) vs the latest decision of the CB module, in bp."""
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


def documents_report(rep) -> tuple:
    line = (f"documents: {rep.new} new, {rep.updated} updated, {rep.unchanged} unchanged; statement rates {rep.statement_rates}, votes {rep.votes}, "
            f"redlines {rep.redlines}; {rep.requests} requests")
    types = ", ".join(f"{k}={v}" for k, v in sorted(rep.by_type.items()))
    extra = [f"  FAILED {w}: {why}" for w, why in rep.failed] + [f"  NOTE {n}" for n in rep.notes]
    md = f"### documents\n\n{line}" + (f"\n\nchanged by type: {types}" if types else "") + ("\n\n" + "\n".join(f"- {e.strip()}" for e in extra) if extra else "")
    return "\n".join([line] + ([f"  by type: {types}"] if types else []) + extra), md


def _summaries_status_section(paths, today: date) -> tuple:
    """(title, heads, rows, notes) for --status: what is stored, what is pending, what failed validation, what the last run consumed."""
    import os

    from .cb_collect import load_state
    from .cb_docs import store as dst
    from .cb_summarize import prompts as PR
    from .cb_summarize import run as SR
    from .cb_summarize import store as SS
    from .cb_summarize.config import load as load_cfg
    cfg = load_cfg()
    records, failures, no_text = SS.load(paths.summaries), SS.load_failures(paths.summaries), SS.load_no_text(paths.summaries)
    docs = sorted(dst.load_documents(paths).values(), key=lambda d: (d["currency"], d["published_date"], d["doc_id"]))
    todo = SR.candidates(docs, load_meetings(paths.meetings), today, cfg)
    pending = [d for d in todo if not SR.is_done(records.get(d["doc_id"]), d, PR.for_type(d["type"]), cfg)
               and not SR.failed_before(failures, d, PR.for_type(d["type"]), d.get("text_sha256"), cfg) and not SR.is_no_text(no_text, d)]
    st = load_state(paths).get("summaries", {})
    last, tot = st.get("last_run") or {}, st.get("totals") or {}
    rows = [[len(records), len(todo), len(pending), len(failures), last.get("new", ""), last.get("unchanged", ""), last.get("failed_validation", ""),
             f"{last.get('input_tokens', 0)} / {last.get('output_tokens', 0)}" if last else "", f"${last['cost_usd']:.4f}" if last else ""]]
    notes = [f"provider {cfg.provider}, model {cfg.model} (config/cb_summaries.yaml); prices checked {cfg.price_checked}"]
    if not os.environ.get(cfg.env_key):
        notes.append(f"WARN {cfg.env_key} is not set: the summaries stage is skipped ({len(pending)} candidate documents are waiting)")
    for f in sorted(failures.values(), key=lambda f: f["doc_id"]):
        notes.append(f"validation_failed {f['doc_id']} ({f['prompt_version']}, {f.get('provider', '?')} {f.get('model', '?')}): {'; '.join(f['errors'][:2])[:220]}")
    for d, m in sorted(no_text.items()):
        notes.append(f"{m.get('kind', 'no_text')} {d}: {m['reason']} (marked {m['at'][:10]}, {'not summarised' if m.get('kind') == 'non_prose' else 'not retried'})")
    if last.get("stopped"):
        notes.append(f"last run stopped: {last['stopped']}")
    if tot:
        cost = round(tot.get("input_tokens", 0) / 1e6 * cfg.price_input + tot.get("output_tokens", 0) / 1e6 * cfg.price_output, 4)
        notes.append(f"all runs: {tot.get('summaries', 0)} summaries, {tot.get('calls', 0)} calls, {tot.get('input_tokens', 0)} in / {tot.get('output_tokens', 0)} out tokens, "
                     f"about ${cost:.4f} at the list prices in config/cb_summaries.yaml (checked {cfg.price_checked}; an estimate from the reported tokens, reasoning tokens included, not a bill)")
    return ("summaries (phase 2b)", ["stored", "candidates", "pending", "validation_failed", "last active run: new", "skipped", "failed", "tokens in / out", "est. cost"], rows, notes)


def summaries_report(rep) -> tuple:
    if not rep.enabled:
        line = f"summaries: SKIPPED - {rep.skipped_reason}"
        return line, f"### summaries\n\n{line}"
    line = (f"summaries: {rep.new} new, {rep.unchanged} unchanged, {len(rep.failed_validation)} failed validation, {len(rep.no_text)} newly marked no_text, "
            f"{len(rep.non_prose)} newly marked non_prose, {len(rep.source_errors)} source errors; "
            f"{rep.calls} calls, {rep.input_tokens} input / {rep.output_tokens} output tokens ({rep.reasoning_tokens} of them reasoning), about ${rep.cost_usd:.4f}; "
            f"{rep.pending} still pending" + (f"; usage missing in {rep.usage_estimated} call(s): tokens estimated from the text" if rep.usage_estimated else ""))
    extra = ([f"  STOPPED {rep.stopped}"] if rep.stopped else []) + [f"  VALIDATION FAILED {d}: {'; '.join(e[:2])}" for d, e in rep.failed_validation] + \
            [f"  NO_TEXT {d}: {why} (marked once, not retried)" for d, why in rep.no_text] + [f"  NON_PROSE {d}: {why} (marked once, not summarised)" for d, why in rep.non_prose] + \
            [f"  SOURCE {d}: {why}" for d, why in rep.source_errors]
    if rep.first_pass or rep.retried or rep.failed_validation or rep.partial:
        extra = [f"  ATTEMPTS {rep.first_pass} passed at the first attempt, {len(rep.retried)} after the retry, {len(rep.partial)} published partially, "
                 f"{len(rep.failed_validation)} failed twice"] + extra
    extra += [f"  PARTIAL {d}: published without {len(pts)} point(s){' and ' + str(len(qs)) + ' quote(s)' if qs else ''} - " + "; ".join(f"[{p['text'][:70]}] " + " | ".join(e[:170] for e in p['errors'][:3]) for p in pts[:6]) +
              ("; " if pts and qs else "") + "; ".join(f"[quote {q['text'][:50]}] {q['errors'][0][:120]}" for q in qs[:4]) for d, pts, qs in rep.partial]
    extra += [f"  DOC {x['doc_id']}: {x['outcome']}; {x['attempts']} attempt(s); {x['input']} in / {x['output']} out ({x['reasoning']} reasoning); ${x['cost']:.4f}" +
              (f"; {x['points']} points, {x['dropped']} removed" if "points" in x else "") for x in rep.docs]
    extra += [f"  RETRIED {d}: the first attempt failed - {'; '.join(e[:3])}" for d, e in rep.retried]
    extra += [f"  FIRST ATTEMPT {d} failed - {'; '.join(e[:6])}" for d, e in rep.first_errors]
    extra += [f"  REJECTED OUTPUT {d}: {' '.join(text.split())[:3000]}" for d, text in rep.rejected]
    md = f"### summaries\n\n{line}" + ("\n\n" + "\n".join(f"- {e.strip()}" for e in extra) if extra else "")
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

    from .cb_docs import collect as dcol, store as dst
    docs = dst.load_documents(paths)
    docs_present = bool(docs)
    if docs:
        per: dict = {}
        for d in docs.values():
            per.setdefault(d["currency"], {}).setdefault(d["type"], []).append(d)
        rows = [[c, len(t.get("statement", [])), len(t.get("minutes", [])) + len(t.get("account", [])) + len(t.get("deliberations", [])) + len(t.get("summary_of_opinions", [])),
                 len(t.get("speech", [])) + len(t.get("testimony", [])), len(t.get("presser_video", [])),
                 len(t.get("presser_transcript", [])) + len(t.get("opening_statement", [])),
                 max((d["first_seen_at"] for v in t.values() for d in v)).strftime("%Y-%m-%d %H:%M")] for c, t in sorted(per.items())]
        add("official texts (phase 2a)", ["cur", "statements", "minutes etc.", "speeches", "videos", "transcripts", "last first-seen (UTC)"], rows,
            [f"WARN {w}" for w in dcol.expectations(paths, today)] + ["RBNZ: BOTWALL - manual file only (data/cb/manual/documents.yaml)"])

    if docs_present:                                            # phase 4: how long after the decision the statement was first stored
        from .cb_trigger import latency as lat
        try:
            heads, lrows, lnotes = lat.status_section(list(docs.values()), load_banks())
            add("decision -> site latency (statement first seen - official time)", heads, lrows, lnotes)
        except (OSError, KeyError) as e:
            add("decision -> site latency (statement first seen - official time)", ["error"], [[f"{type(e).__name__}: {e}"]])

    if paths.summaries.exists() or docs_present:
        add(*_summaries_status_section(paths, today))

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
        add("Carry fallback (data/policy_rates.yaml) vs CB module", ["cur", "carry", "cb", "diff bp", "cb status", "cb meeting", "cb effective"],
            [[d["currency"], d["carry"], "" if d["cb"] is None else d["cb"], "" if d["diff_bp"] is None else d["diff_bp"],
              d["cb_status"] or "", d["cb_meeting"] or "", d["cb_effective"] or ""] for d in diff],
            [f"DIFF {d['currency']}: Carry {d['carry']} vs CB {d['cb']} ({d['diff_bp']} bp)" for d in diff if d["diff_bp"]])

    # stance phase S0a: macro series, manual stance files, framework-watch alerts (each section guarded: --status never fails)
    from . import cb_framework, cb_macro
    for title, fn in (("macro series (stance S0a)", lambda: cb_macro.status_section(paths, today)),
                      ("stance inputs (S0a manual files)", lambda: stance_status(paths, today)),
                      ("framework watch (stance S0a): open alerts", lambda: cb_framework.status_section(paths, today))):
        try:
            if title.startswith("macro") and not (Path(paths.dir) / cb_macro.MACRO.name).exists():
                add(title, ["note"], [["no macro series stored yet (stage macro: python -m src.cb_collect --stage macro --backfill)"]])
                continue
            if not (Path(paths.manual) / "mandates.yaml").exists() and not title.startswith("macro"):
                continue
            add(*fn())
        except Exception as e:                                            # noqa: BLE001 - a broken section is reported, not raised
            add(title, ["error"], [[f"{type(e).__name__}: {e}"]])
    return "\n".join(text), "\n".join(md)


# ---------------------------------------------------------------------------
# Stance phase S0a: mandates, neutral, forecasts (manual files) - loading and validation; no stance computation
# ---------------------------------------------------------------------------

STANCE_YAML = ROOT / "config" / "cb_stance.yaml"
STANCE_BANKS = ("USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF")
VALUE_RANGE = (-5.0, 25.0)
HORIZON_RX = re.compile(r"^(\d{4}|\d{4}Q[1-4]|FY\d{4}|M\d{4}|all)$")


class StanceDataError(ValueError):
    """A manual stance file failed validation: it is not loaded (the message lists every error)."""


def _yaml(path: Path) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}


def load_stance_config(path: Path | str | None = None) -> dict:
    doc = _yaml(Path(path or STANCE_YAML))
    cur = next((v for v in doc.get("versions", []) if v["version"] == doc["meta"]["current"]), None)
    if cur is None:
        raise StanceDataError("config/cb_stance.yaml: no version matches meta.current")
    return cur


def _num_ok(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and VALUE_RANGE[0] <= v <= VALUE_RANGE[1]


def _check_values(err: list, tag: str, d: dict, keys=("value", "point", "low", "high")) -> None:
    for k in keys:
        if d.get(k) is not None and not _num_ok(d[k]):
            err.append(f"{tag}: {k}={d[k]!r} outside {VALUE_RANGE[0]}..{VALUE_RANGE[1]} or not a number")
    if d.get("low") is not None and d.get("high") is not None and _num_ok(d["low"]) and _num_ok(d["high"]) and d["low"] > d["high"]:
        err.append(f"{tag}: low {d['low']} > high {d['high']}")


def _check_date(err: list, tag: str, v, today: date, *, future_ok: bool = False, required: bool = True) -> None:
    if v is None:
        if required:
            err.append(f"{tag}: missing date")
        return
    if not isinstance(v, date):
        err.append(f"{tag}: {v!r} is not a date")
    elif not future_ok and v > today:
        err.append(f"{tag}: {v} is in the future")


def _check_source(err: list, tag: str, src, today: date) -> None:
    if not isinstance(src, dict) or not src.get("url") or not src.get("page"):
        err.append(f"{tag}: a filled entry needs source.url and source.page")
        return
    _check_date(err, f"{tag}.retrieved", src.get("retrieved"), today)


def macro_series_ids(official: dict | None = None) -> set:
    from .cb_sources.official import load_official
    return set(((official or load_official()).get("macro") or {}).get("series", {}))


def validate_mandates(doc: dict, today: date, series_ids: set) -> list:
    from .cb_compute import stance_inputs as SI
    err: list = []
    banks = (doc or {}).get("banks") or {}
    for b in STANCE_BANKS:
        if b not in banks:
            err.append(f"mandates: bank {b} missing")
    for b, entry in banks.items():
        vers = entry.get("versions") or []
        if not vers:
            err.append(f"mandates {b}: no versions")
            continue
        for i, v in enumerate(vers):
            tag = f"mandates {b}[{i}]"
            ef, et = v.get("effective_from"), v.get("effective_to")
            _check_date(err, f"{tag}.effective_from", ef, today, future_ok=True)
            _check_date(err, f"{tag}.effective_to", et, today, future_ok=True, required=False)
            if isinstance(ef, date) and isinstance(et, date) and et < ef:
                err.append(f"{tag}: effective_to {et} < effective_from {ef}")
            if v.get("change_reason") not in SI.CHANGE_REASONS:
                err.append(f"{tag}: change_reason must be one of {SI.CHANGE_REASONS}")
            t = v.get("target") or {}
            if t.get("kind") == "point":
                if t.get("value") is None:
                    err.append(f"{tag}: point target needs value")
            elif t.get("kind") == "band":
                if t.get("low") is None or t.get("high") is None:
                    err.append(f"{tag}: band target needs low and high")
            else:
                err.append(f"{tag}: target.kind must be point | band")
            _check_values(err, f"{tag}.target", t)
            mp = t.get("midpoint")
            if mp is not None:
                if not isinstance(mp, dict) or not mp.get("quote") or not _num_ok(mp.get("value")):
                    err.append(f"{tag}: a declared midpoint needs value and quote")
            if (v.get("mandate") or {}).get("type") not in SI.MANDATE_TYPES or not (v.get("mandate") or {}).get("quote"):
                err.append(f"{tag}: mandate needs type {SI.MANDATE_TYPES} and quote")
            h = v.get("horizon") or {}
            if not h.get("text") or not isinstance(h.get("forward_looking"), bool) or not h.get("quote"):
                err.append(f"{tag}: horizon needs text, forward_looking (bool) and quote")
            u = v.get("underlying") or {}
            prim = u.get("primary")
            prims = prim if isinstance(prim, list) else [prim]
            for sid in [v.get("headline")] + prims + list(u.get("secondary") or []):
                if sid not in series_ids:
                    err.append(f"{tag}: series_id {sid!r} not in config/cb_official.yaml macro.series")
            if len(prims) > 1 and u.get("combine") != "mean":
                err.append(f"{tag}: several underlying primaries need combine: mean")
            if u.get("rule_applied") not in (1, 2, 3) or not u.get("evidence_url"):
                err.append(f"{tag}: underlying needs rule_applied 1|2|3 and evidence_url")
            if (v.get("slack") or {}).get("type") not in SI.SLACK_TYPES:
                err.append(f"{tag}: slack.type must be one of {SI.SLACK_TYPES}")
            n = v.get("neutral") or {}
            if n.get("source") not in ("fed_sep", "manual") or n.get("flag") not in SI.NEUTRAL_FLAGS:
                err.append(f"{tag}: neutral needs source fed_sep|manual and a flag in {SI.NEUTRAL_FLAGS}")
            fc = v.get("forecasts") or {}
            if fc.get("source") not in ("fed_sep", "manual") or fc.get("conditioning") not in SI.CONDITIONING:
                err.append(f"{tag}: forecasts needs source fed_sep|manual and conditioning in {SI.CONDITIONING}")
            srcs = v.get("sources") or []
            if not srcs:
                err.append(f"{tag}: no sources")
            for k, s in enumerate(srcs):
                _check_source(err, f"{tag}.sources[{k}]", s, today)
            for k, c in enumerate(v.get("corrections") or []):
                ct = f"{tag}.corrections[{k}]"
                _check_date(err, f"{ct}.date", c.get("date"), today)
                if not (c.get("field") and c.get("reason")):
                    err.append(f"{ct}: a correction needs field and reason")
                _check_source(err, ct, c.get("source"), today)
            lv = v.get("last_verified") or {}
            _check_date(err, f"{tag}.last_verified.date", lv.get("date"), today)
            if not lv.get("checked"):
                err.append(f"{tag}: last_verified.checked lists no url")
        try:
            vs = mandate_versions({"banks": {b: entry}})[b]
        except Exception:                                                   # noqa: BLE001 - the field errors above say why
            continue
        from .cb_compute.stance_inputs import overlaps
        for a, c in overlaps(vs):
            err.append(f"mandates {b}: versions starting {a} and {c} overlap")
        if sum(1 for x in vs if x.current_at(today)) != 1:
            err.append(f"mandates {b}: not exactly one version current at {today}")
    return err


def mandate_versions(doc: dict) -> dict:
    """{currency: [MandateVersion]} (no validation)."""
    from .cb_compute.stance_inputs import MandateVersion, Target
    out: dict = {}
    for b, entry in ((doc or {}).get("banks") or {}).items():
        for v in entry.get("versions") or []:
            t = v["target"]
            mp = t.get("midpoint")
            u = v["underlying"]
            prim = u["primary"] if isinstance(u["primary"], list) else [u["primary"]]
            out.setdefault(b, []).append(MandateVersion(
                currency=b, effective_from=v["effective_from"], effective_to=v.get("effective_to"),
                target=Target(t["kind"], t.get("value"), t.get("low"), t.get("high"), (mp or {}).get("value") if isinstance(mp, dict) else None, t.get("measure", "")),
                mandate_type=v["mandate"]["type"], horizon_text=v["horizon"]["text"], forward_looking=v["horizon"]["forward_looking"],
                headline=v["headline"], underlying=tuple(prim), underlying_combine=u.get("combine"), secondary=tuple(u.get("secondary") or []),
                rule_applied=u["rule_applied"], slack_type=v["slack"]["type"], neutral_source=v["neutral"]["source"], neutral_flag=v["neutral"]["flag"],
                forecasts_source=v["forecasts"]["source"], conditioning=v["forecasts"]["conditioning"],
                last_verified=(v.get("last_verified") or {}).get("date")))
    return out


def load_mandates(paths, *, today: date | None = None, validate: bool = False, series_ids: set | None = None) -> dict:
    doc = _yaml(Path(paths.manual) / "mandates.yaml")
    if validate:
        errs = validate_mandates(doc, today or date.today(), series_ids if series_ids is not None else macro_series_ids())
        if errs:
            raise StanceDataError("data/cb/manual/mandates.yaml:\n  " + "\n  ".join(errs))
    return doc


def _current_mandates(mandates: dict, today: date) -> dict:
    from .cb_compute.stance_inputs import current_version
    return {b: current_version(vs, today) for b, vs in mandate_versions(mandates).items()}


def validate_neutral(doc: dict, today: date, mandates: dict | None = None) -> list:
    err: list = []
    banks = (doc or {}).get("banks") or {}
    cur = _current_mandates(mandates, today) if mandates else {}
    for b in STANCE_BANKS:
        if b == "USD":
            if b in banks:
                err.append("neutral: USD must have no entry (its neutral is the Fed SEP longer-run dots)")
            continue
        if b not in banks:
            err.append(f"neutral: bank {b} missing")
    for b, e in banks.items():
        tag = f"neutral {b}"
        st = e.get("status")
        if st not in ("filled", "pending", "not_published"):
            err.append(f"{tag}: status must be filled | pending | not_published")
        if cur.get(b) is not None and e.get("flag") != cur[b].neutral_flag:
            err.append(f"{tag}: flag {e.get('flag')} != mandates.yaml neutral.flag {cur[b].neutral_flag}")
        ests = e.get("estimates") or []
        if st == "filled" and not ests:
            err.append(f"{tag}: filled without estimates")
        if st == "pending" and not (e.get("reason") and e.get("next_step")):
            err.append(f"{tag}: pending needs reason and next_step")
        if st == "not_published":
            ev = e.get("evidence") or {}
            if not (ev.get("url") and ev.get("page") and ev.get("quote")):
                err.append(f"{tag}: not_published needs evidence url, page and quote")
            pq = e.get("policy_quote") or {}
            if not (pq.get("quote") and pq.get("url") and pq.get("page")):
                err.append(f"{tag}: not_published needs the bank's quote about its own policy (quote, url, page)")
        for i, x in enumerate(ests):
            t = f"{tag}.estimates[{i}]"
            if x.get("basis") not in ("nominal", "real"):
                err.append(f"{t}: basis must be nominal | real")
            if x.get("point") is None and (x.get("low") is None or x.get("high") is None):
                err.append(f"{t}: needs point or low/high")
            _check_values(err, t, x)
            _check_date(err, f"{t}.published", x.get("published"), today)
            _check_source(err, t, x.get("source"), today)
            if x.get("quote") and len(x["quote"].split()) > 30:
                err.append(f"{t}: quote longer than 30 words")
            if x.get("transcription") == "manual" and not x.get("quote"):
                err.append(f"{t}: a manual transcription needs the quote")
            if b == "GBP" and not x.get("member"):
                err.append(f"{t}: GBP estimates need the member")
    return err


def load_neutral(paths, *, today: date | None = None, validate: bool = False, mandates: dict | None = None) -> dict:
    doc = _yaml(Path(paths.manual) / "neutral.yaml")
    if validate:
        errs = validate_neutral(doc, today or date.today(), mandates)
        if errs:
            raise StanceDataError("data/cb/manual/neutral.yaml:\n  " + "\n  ".join(errs))
    return doc


def validate_forecasts(doc: dict, today: date, mandates: dict | None = None, rbnz: dict | None = None) -> list:
    from .cb_compute import stance_inputs as SI
    err: list = []
    banks = (doc or {}).get("banks") or {}
    cur = _current_mandates(mandates, today) if mandates else {}
    for b in STANCE_BANKS:
        if b == "USD":
            if b in banks:
                err.append("forecasts: USD must have no entry (Fed SEP in data/cb/projections.parquet)")
            continue
        if b not in banks:
            err.append(f"forecasts: bank {b} missing")
    for b, e in banks.items():
        tag = f"forecasts {b}"
        st = e.get("status")
        if st not in ("filled", "pending"):
            err.append(f"{tag}: status must be filled | pending")
        if st == "pending" and not (e.get("reason") and e.get("next_step")):
            err.append(f"{tag}: pending needs reason and next_step")
        if e.get("conditioning") not in SI.CONDITIONING:
            err.append(f"{tag}: conditioning must be one of {SI.CONDITIONING}")
        if cur.get(b) is not None and e.get("conditioning") != cur[b].conditioning:
            err.append(f"{tag}: conditioning {e.get('conditioning')} != mandates.yaml {cur[b].conditioning}")
        _check_date(err, f"{tag}.published", e.get("published"), today)
        _check_date(err, f"{tag}.cutoff", e.get("cutoff"), today, required=False)
        if e.get("cutoff_flag") not in (None, "ESTIMATE"):
            err.append(f"{tag}: cutoff_flag must be ESTIMATE when present")
        if st == "filled":
            _check_source(err, tag, e.get("source"), today)
        for i, s in enumerate(e.get("series") or []):
            t = f"{tag}.series[{i}]"
            if s.get("role") not in SI.ROLES:
                err.append(f"{t}: unknown role {s.get('role')!r}")
            if s.get("transform") not in SI.TRANSFORMS:
                err.append(f"{t}: unknown transform {s.get('transform')!r}")
            if s.get("role") == "rate_assumption" and not s.get("instrument"):
                err.append(f"{t}: a rate assumption needs its instrument")
            if s.get("status") == "pending":
                if not (s.get("reason") and s.get("next_step")):
                    err.append(f"{t}: pending needs reason and next_step")
            elif not s.get("values"):
                err.append(f"{t}: no values")
            if s.get("source") is not None:
                _check_source(err, t, s["source"], today)
            for k, v in enumerate(s.get("values") or []):
                if not HORIZON_RX.match(str(v.get("horizon"))):
                    err.append(f"{t}.values[{k}]: unknown horizon {v.get('horizon')!r}")
                if v.get("value") is None and (v.get("low") is None or v.get("high") is None):
                    err.append(f"{t}.values[{k}]: needs value or low/high")
                _check_values(err, f"{t}.values[{k}]", v)
    if rbnz and "NZD" in banks:
        filled = sorted(m["meeting"] for m in (rbnz.get("mps") or []) if m.get("status") == "filled")
        if filled and banks["NZD"].get("meeting") != filled[-1]:
            err.append(f"forecasts NZD: meeting {banks['NZD'].get('meeting')} != latest filled MPS in rbnz.yaml {filled[-1]}")
    return err


def load_forecasts(paths, *, today: date | None = None, validate: bool = False, mandates: dict | None = None) -> dict:
    doc = _yaml(Path(paths.manual) / "forecasts.yaml")
    if validate:
        errs = validate_forecasts(doc, today or date.today(), mandates, load_rbnz(Path(paths.manual) / "rbnz.yaml"))
        if errs:
            raise StanceDataError("data/cb/manual/forecasts.yaml:\n  " + "\n  ".join(errs))
    return doc


def projections_view(paths, forecasts: dict | None = None) -> dict:
    """ONE view of the projections: {currency: Projection} - the latest Fed SEP from data/cb/projections.parquet (written by
    CI only) + the manual publications of forecasts.yaml."""
    from .cb_compute.stance_inputs import ForecastPoint, Projection
    out: dict = {}
    sep = load_projections(paths)
    if sep:
        last = max(r["meeting_date"] for r in sep)
        roles = {"pce_inflation": "headline", "core_pce_inflation": "underlying", "unemployment_rate": "unemployment", "federal_funds_rate": "rate_assumption"}
        pts = tuple(ForecastPoint(roles[r["variable"]], r["variable"], "q4_q4" if "inflation" in r["variable"] else "quarter_level",
                                  "all" if r["horizon"] == "longer_run" else r["horizon"], r["value"])
                    for r in sep if r["meeting_date"] == last and r["kind"] == "median_published" and r["variable"] in roles)
        out["USD"] = Projection("USD", f"FOMC Summary of Economic Projections {last}", last, "own_path", None, "fed_sep", pts,
                                next((r["source_url"] for r in sep if r["meeting_date"] == last), ""))
    for b, e in ((forecasts if forecasts is not None else load_forecasts(paths)).get("banks") or {}).items():
        pts = tuple(ForecastPoint(s["role"], s["variable"], s["transform"], str(v["horizon"]), v.get("value"), v.get("low"), v.get("high"))
                    for s in e.get("series") or [] for v in s.get("values") or [])
        out[b] = Projection(b, e["publication"], e["published"], e["conditioning"], e.get("cutoff"), "manual", pts, (e.get("source") or {}).get("url", ""))
    return out


def stance_inputs(paths, asof: date) -> dict:
    """{currency: StanceInputs} for `asof` - validated manual files + the macro store. Raises StanceDataError when a manual
    file is invalid."""
    from . import cb_macro
    from .cb_compute.macro_yoy import yoy
    from .cb_compute.stance_inputs import NeutralEstimate, Observation, StanceInputs, current_version
    mand = load_mandates(paths, today=asof, validate=True)
    neut = load_neutral(paths, today=asof, validate=True, mandates=mand)
    fc = load_forecasts(paths, today=asof, validate=True, mandates=mand)
    proj = projections_view(paths, fc)
    per = cb_macro.by_series(cb_macro.load_macro(paths, end=asof))
    out = {}
    for b, vs in mandate_versions(mand).items():
        mv = current_version(vs, asof)
        si = StanceInputs(b, asof, mv, projection=proj.get(b))
        if mv is not None:
            roles = {"headline": [mv.headline], "underlying": list(mv.underlying), "secondary": list(mv.secondary)}
            for role, sids in roles.items():
                for sid in sids:
                    rows = {p: r for p, r in (per.get(sid) or {}).items() if r["period_start"] <= asof}
                    if not rows:
                        si.pending.append(f"{role} {sid}: no observation stored")
                        continue
                    p = max(rows)
                    r = rows[p]
                    y = float(yoy({k: x["value"] for k, x in rows.items()}, p)) if r["published_as"] == "index" and yoy({k: x["value"] for k, x in rows.items()}, p) is not None else (r["value"] if r["published_as"] == "yoy" else None)
                    si.observations.setdefault(role, []).append(Observation(sid, p, r["value"], r["published_as"], y, r["release_date"], r["source_url"]))
        e = (neut.get("banks") or {}).get(b) or {}
        si.neutral_status = "fed_sep" if b == "USD" else e.get("status", "")
        si.neutral = [NeutralEstimate(x["label"], x["basis"], x.get("point"), x.get("low"), x.get("high"), x["published"], x.get("member"),
                                      (x.get("source") or {}).get("url", ""), (x.get("source") or {}).get("page", "")) for x in e.get("estimates") or []]
        if e.get("status") == "pending":
            si.pending.append(f"neutral: {e.get('reason')} -> {e.get('next_step')}")
        out[b] = si
    return out


def stance_status(paths, today: date) -> tuple:
    """--status section for the manual stance files: validation, projections newer than forecasts.yaml, stale neutral, pending."""
    from .cb_compute.framework_watch import last_projection_dates
    rows, notes = [], []
    try:
        mand = load_mandates(paths, today=today, validate=True)
        neut = load_neutral(paths, today=today, validate=True, mandates=mand)
        fc = load_forecasts(paths, today=today, validate=True, mandates=mand)
    except StanceDataError as e:
        return ("stance inputs (S0a manual files)", ["error"], [[str(e).split(chr(10))[0]]], [f"INVALID {x.strip()}" for x in str(e).split("\n")[1:]])
    cfg = load_stance_config()
    lastp = last_projection_dates(load_meetings(paths.meetings), today)
    from .cb_compute.stance_inputs import current_version
    cur = {b: current_version(v, today) for b, v in mandate_versions(mand).items()}
    sep_last = max((r["meeting_date"] for r in load_projections(paths)), default=None)
    for b in STANCE_BANKS:
        f = (fc.get("banks") or {}).get(b)
        pub = sep_last if b == "USD" else (f or {}).get("meeting")
        n = (neut.get("banks") or {}).get(b) or {}
        newest = max((x["published"] for x in n.get("estimates") or []), default=None)
        stale_n = newest is not None and (today.year - newest.year) * 12 + today.month - newest.month > cfg["staleness"]["neutral_months"]
        rows.append([b, (cur[b].effective_from if cur.get(b) else "NONE"), pub or "", lastp.get(b, ""), "fed_sep" if b == "USD" else n.get("status", ""), newest or "",
                     "STALE" if stale_n else ""])
        if lastp.get(b) and pub and lastp[b] > pub:
            notes.append(f"WARN forecasts {b}: a newer projection publication ({lastp[b]}) than the one entered ({pub}) - enter it and re-verify the mandate")
        if stale_n:
            notes.append(f"WARN neutral {b}: newest estimate published {newest}, older than {cfg['staleness']['neutral_months']} months")
        if n.get("status") == "pending":
            notes.append(f"PENDING neutral {b}: {n.get('reason')} | next: {n.get('next_step')}")
        for s in (f or {}).get("series") or []:
            if s.get("status") == "pending":
                notes.append(f"PENDING forecasts {b} {s['role']}: {s.get('reason')} | next: {s.get('next_step')}")
        if f and f.get("status") == "pending":
            notes.append(f"PENDING forecasts {b}: {f.get('reason')} | next: {f.get('next_step')}")
    return ("stance inputs (S0a manual files)", ["cur", "mandate from", "forecasts of", "latest projection meeting", "neutral", "newest neutral", "stale"], rows, notes)
