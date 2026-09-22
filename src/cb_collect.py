"""Central Banks market collector -> data/cb/market_quotes/market_quotes_YYYY-MM.parquet (phase 1A).

    python -m src.cb_collect                       # normal run: last 10 days, every source
    python -m src.cb_collect --backfill            # official-history sources from config meta.backfill_from
    python -m src.cb_collect --source boe_ois      # subset
    python -m src.cb_collect --status              # last data write / as-of / lag / missing days per source

Store rules
  * append-only; key (source, instrument, contract, field, asof); last-write-wins on the same key, but a row
    whose content is unchanged keeps its original `fetched_at` (idempotent runs => no git diff);
  * stored monthly: one parquet per calendar month of the AS-OF (so the key stays globally unique), same columns;
    only partitions whose content changed are rewritten, byte-deterministically (sorted rows, fixed writer settings);
  * sources with an inferred as-of (MX): a new as-of whose quotes all equal those of the previous recorded as-of
    is not recorded (holiday / page not refreshed) and is noted in state.json for --status;
  * a quote whose as-of is the exchange's CURRENT day is recorded only after the source's `eod_cutoff`;
  * history is never deleted; a failed source is logged and skipped (exit 0 unless EVERY source failed);
  * validators (ETag / Last-Modified) live in data/cb/state.json and are advanced only after the data they
    describe is on disk, and never when part of the response was held back by the cutoff;
  * snapshot sources (JPX, MX, ASX) also keep the relevant raw rows, gzip, in data/cb/raw/<source>/<asof>.csv.gz.

Single writer: after merge, data/cb/ is written only by CI (.github/workflows/cb-refresh.yml).
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from . import cb_store as cs
from .cb_sources import ADAPTERS, load_sources
from .cb_calendar import Calendar, load_calendars, weekends_only
from .cb_sources.base import Quote
from .cb_sources.official import PROVIDERS, Obs, load_official

log = logging.getLogger("cb_collect")

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "cb"
LOOKBACK_DAYS = 10

SCHEMA = pa.schema([
    ("source", pa.string()), ("currency", pa.string()), ("instrument", pa.string()), ("contract", pa.string()),
    ("field", pa.string()), ("ref_start", pa.date32()), ("ref_end", pa.date32()), ("tenor_months", pa.float64()),
    ("value", pa.float64()), ("unit", pa.string()), ("asof", pa.date32()), ("asof_inferred", pa.bool_()),
    ("fetched_at", pa.timestamp("us", tz="UTC")),
])
COLUMNS = SCHEMA.names
CLASSES = {c.__name__: c for c in ADAPTERS.values()}          # config `adapter:` -> class
KEY = ("source", "instrument", "contract", "field", "asof")
CONTENT = ("currency", "ref_start", "ref_end", "tenor_months", "value", "unit", "asof_inferred")


class Paths:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.dir = Path(data_dir) if data_dir else DATA_DIR
        self.quotes = self.dir / "market_quotes"
        self.state = self.dir / "state.json"
        self.raw = self.dir / "raw"
        self.meetings = self.dir / "meetings.yaml"
        self.decisions = self.dir / "decisions.parquet"
        self.projections = self.dir / "projections.parquet"
        self.manual = self.dir / "manual"
        self.documents = self.dir / "documents"                  # monthly partitions documents_YYYY-MM.parquet (phase 2a)
        self.votes = self.dir / "votes.parquet"
        self.redlines = self.dir / "redlines.parquet"
        self.summaries = self.dir / "summaries"                  # monthly partitions summaries_YYYY-MM.json + failures.json (phase 2b)

    def partition(self, month: str) -> Path:
        return self.quotes / f"market_quotes_{month}.parquet"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

OFFICIAL_SCHEMA = pa.schema([
    ("series_id", pa.string()), ("currency", pa.string()), ("date", pa.date32()), ("value", pa.float64()),
    ("unit", pa.string()), ("fetched_at", pa.timestamp("us", tz="UTC")),
])
OFFICIAL = cs.Dataset("official_series", "official_series", OFFICIAL_SCHEMA, ("series_id", "date"), "date",
                      ("currency", "value", "unit"))
MARKET = cs.Dataset("market_quotes", "market_quotes", SCHEMA, KEY, "asof", CONTENT)
Merge = cs.Merge
month_of = cs.month_of


def _key(row: dict) -> tuple:
    return MARKET.row_key(row)


def list_partitions(paths: Paths) -> dict[str, Path]:
    """{'2026-09': path, ...} for every market partition on disk."""
    return cs.list_partitions(MARKET, paths.dir)


def load_store(paths: Paths, start: str | date | None = None, end: str | date | None = None) -> dict[tuple, dict]:
    """Every partition, or only the months in [start, end] (inclusive, 'YYYY-MM' or a date)."""
    return cs.load(MARKET, paths.dir, start, end)


def serialize_partition(rows: list[dict]) -> bytes:
    """Same rows -> same bytes: rows sorted by key, fixed writer settings (pyarrow is pinned in requirements.txt)."""
    return cs.serialize(SCHEMA, KEY, rows)


def write_store(paths: Paths, store: dict[tuple, dict], months: set[str] | None = None) -> list[str]:
    """Rewrite only the partitions in `months` (default: all) - and, of those, only when the bytes differ.
    Returns the months actually written."""
    return cs.write(MARKET, paths.dir, store, months)


def quote_row(q: Quote) -> dict:
    return {c: getattr(q, c) for c in COLUMNS}


def merge_quotes(store: dict[tuple, dict], quotes: list[Quote]) -> Merge:
    """Last-write-wins on the key; identical content keeps the stored row (and its fetched_at)."""
    return cs.merge(MARKET, store, (quote_row(q) for q in quotes))


# ---------------------------------------------------------------------------
# Raw rows for snapshot sources, state
# ---------------------------------------------------------------------------

def write_raw(paths: Paths, source: str, asof: date, text: str) -> bool:
    """gzip with mtime=0 (byte-deterministic); untouched when the stored content is identical."""
    f = paths.raw / source / f"{asof.isoformat()}.csv.gz"
    payload = (text.rstrip("\n") + "\n").encode()
    if f.exists():
        try:
            if gzip.decompress(f.read_bytes()) == payload:
                return False
        except OSError:
            pass
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(gzip.compress(payload, mtime=0))
    return True


def load_state(paths: Paths) -> dict:
    try:
        return json.loads(paths.state.read_text())
    except (OSError, ValueError):
        return {}


def save_state(paths: Paths, state: dict) -> bool:
    text = json.dumps(state, indent=2, sort_keys=True) + "\n"
    if paths.state.exists() and paths.state.read_text() == text:
        return False
    paths.state.parent.mkdir(parents=True, exist_ok=True)
    paths.state.write_text(text)
    return True


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

NO_NEW = "_no_new_values"          # state.json section {source: {asof: identical-to-asof}} for --status
NO_NEW_KEEP = 30


@dataclass
class SourceReport:
    id: str
    status: str = ""               # ok | not_modified | skipped | FAILED
    note: str = ""
    merge: Merge = field(default_factory=Merge)
    dropped: int = 0               # held back by eod_cutoff
    skipped: dict = field(default_factory=dict)      # inferred as-of -> previous as-of it equals (not recorded)
    raw_written: bool = False
    asof_max: Optional[date] = None
    lag_bd: Optional[int] = None


def business_day_lag(asof: date, today: date, cal: Optional[Calendar] = None) -> int:
    """Business days between the as-of and the last business day <= today in the source's calendar (0A convention:
    on a Saturday, Friday's data = 0 and Thursday's = 1). Without a calendar only weekends are skipped."""
    return (cal or weekends_only()).lag(asof, today)


def calendar_of(cals: dict, cal_id: Optional[str]) -> Calendar:
    return cals.get(cal_id) or weekends_only()


def drop_unchanged_inferred(store: dict[tuple, dict], sid: str, quotes: list[Quote]) -> tuple[list[Quote], dict]:
    """Sources whose as-of is inferred (MX): a NEW as-of whose quotes all equal those of the previous recorded
    as-of means a holiday or a page that was not refreshed - it is not recorded. An as-of that is already stored
    goes through the normal last-write-wins merge (a Saturday re-fetch of Friday's page)."""
    asofs = sorted({q.asof for q in quotes if q.asof_inferred})
    if not asofs:
        return quotes, {}
    have: dict[date, dict] = {}
    for r in store.values():
        if r["source"] == sid:
            have.setdefault(r["asof"], {})[(r["instrument"], r["contract"], r["field"])] = r["value"]
    skipped: dict[date, date] = {}
    for a in asofs:
        if a in have:
            continue
        cur = {(q.instrument, q.contract, q.field): q.value for q in quotes if q.asof == a and q.asof_inferred}
        prev = max((d for d in have if d < a), default=None)
        if prev is not None and cur and all(have[prev].get(k) == v for k, v in cur.items()):
            skipped[a] = prev
        else:
            have[a] = cur                                  # later as-ofs of this batch compare against it
    return [q for q in quotes if q.asof not in skipped], skipped


def select_sources(cfg: dict, only: list[str] | None, backfill: bool) -> list[str]:
    ids = list(cfg["sources"])
    if only:
        unknown = [i for i in only if i not in ids]
        if unknown:
            raise SystemExit(f"unknown source(s): {unknown}; known: {ids}")
        return only
    if backfill:
        return [i for i in ids if cfg["sources"][i].get("history") == "official"]
    return ids


def run(paths: Paths, *, only: list[str] | None = None, backfill_from: date | None = None,
        lookback_days: int = LOOKBACK_DAYS, now: Callable[[], datetime] | None = None,
        cfg: dict | None = None) -> list[SourceReport]:
    cfg = cfg or load_sources()
    clock = now or (lambda: datetime.now(timezone.utc))
    today = clock().date()
    since = backfill_from or (today - timedelta(days=lookback_days))
    ids = select_sources(cfg, only, backfill=backfill_from is not None)
    store = load_store(paths)
    state = load_state(paths)
    new_state = dict(state)
    marks = {sid: dict(m) for sid, m in state.get(NO_NEW, {}).items()}
    reports: list[SourceReport] = []
    raws: list[tuple[str, date, str]] = []

    for sid in ids:
        rep = SourceReport(sid)
        reports.append(rep)
        try:
            src = CLASSES[cfg["sources"][sid]["adapter"]](cfg["sources"][sid], now=clock)
        except Exception as e:                                   # a broken adapter must not stop the rest
            rep.status, rep.note = "FAILED", f"init {type(e).__name__}: {e}"
            log.error("%s: %s", sid, rep.note)
            continue
        validators = {} if backfill_from else state.get(sid, {})   # backfill = full download
        res = src.fetch(since=since, state=validators)
        if res is None:
            rep.status, rep.note = "FAILED", f"{src.last_status} {src.last_note}".strip()
            log.warning("%s: %s", sid, rep.note)
            continue
        rep.status, rep.note = res.status, res.note
        final = [q for q in res.quotes if src.is_final(q.asof)]
        rep.dropped = len(res.quotes) - len(final)
        final, rep.skipped = drop_unchanged_inferred(store, sid, final)
        if rep.skipped:
            marks.setdefault(sid, {}).update({a.isoformat(): p.isoformat() for a, p in rep.skipped.items()})
            rep.note = (rep.note + " fără valori noi: " + ", ".join(
                f"as-of {a} identic cu {p}" for a, p in sorted(rep.skipped.items())) + " - neînregistrat").strip()
        rep.merge = merge_quotes(store, final)
        if res.raw and final and any(q.asof == res.raw["asof"] for q in final):
            raws.append((sid, res.raw["asof"], res.raw["text"]))
        if res.state and rep.dropped == 0 and (rep.merge.new or rep.merge.updated or sid not in state):   # cutoff held rows back -> re-fetch next run
            new_state[sid] = res.state                              # advance only with new data: validators that changed alone would be a commit for nothing
        elif rep.dropped:
            rep.note = (rep.note + f" {rep.dropped} quote(s) before eod_cutoff, validators kept").strip()

    # markers of "no new values" disappear once that as-of is recorded; keep the latest few
    recorded = {(r["source"], r["asof"].isoformat()) for r in store.values()} if marks else set()
    for sid in list(marks):
        marks[sid] = {a: p for a, p in sorted(marks[sid].items()) if (sid, a) not in recorded}
        marks[sid] = dict(list(marks[sid].items())[-NO_NEW_KEEP:])
        if not marks[sid]:
            del marks[sid]
    if marks:
        new_state[NO_NEW] = marks
    else:
        new_state.pop(NO_NEW, None)

    # persist: data first (only the partitions that changed), then raw, then validators
    write_store(paths, store, set().union(*(r.merge.months for r in reports)))
    for sid, asof, text in raws:
        next(r for r in reports if r.id == sid).raw_written = write_raw(paths, sid, asof, text)
    save_state(paths, new_state)

    by_src: dict[str, list[date]] = {}
    for row in store.values():
        by_src.setdefault(row["source"], []).append(row["asof"])
    for r in reports:
        if r.id in by_src:
            r.asof_max = max(by_src[r.id])
            r.lag_bd = business_day_lag(r.asof_max, today)
    return reports


def obs_row(o: Obs) -> dict:
    return {c: getattr(o, c) for c in OFFICIAL_SCHEMA.names}


def load_official_store(paths: Paths, start=None, end=None) -> dict:
    return cs.load(OFFICIAL, paths.dir, start, end)


def run_official(paths: Paths, *, only: list[str] | None = None, backfill_from: date | None = None,
                 lookback_days: int | None = None, now: Callable[[], datetime] | None = None,
                 cfg: dict | None = None) -> list[SourceReport]:
    """Official rate series (config/cb_official.yaml) -> data/cb/official_series/. One report per provider."""
    cfg = cfg or load_official()
    clock = now or (lambda: datetime.now(timezone.utc))
    today = clock().date()
    since = backfill_from or (today - timedelta(days=lookback_days or cfg["meta"]["lookback_days"]))
    names = only or list(cfg["providers"])
    unknown = [n for n in names if n not in cfg["providers"]]
    if unknown:
        raise SystemExit(f"unknown provider(s): {unknown}; known: {list(cfg['providers'])}")
    store = load_official_store(paths)
    state = load_state(paths)
    new_state = dict(state)
    reports: list[SourceReport] = []
    for prov in names:
        rep = SourceReport(f"official:{prov}")
        reports.append(rep)
        series = {sid: sc for sid, sc in cfg["series"].items() if sc["provider"] == prov}
        try:
            src = PROVIDERS[prov](cfg["providers"][prov], series, now=clock)
        except Exception as e:
            rep.status, rep.note = "FAILED", f"init {type(e).__name__}: {e}"
            log.error("%s: %s", rep.id, rep.note)
            continue
        res = src.fetch(since=since, state={} if backfill_from else state.get(rep.id, {}))
        if res is None:
            rep.status, rep.note = "FAILED", f"{src.last_status} {src.last_note}".strip()
            log.warning("%s: %s", rep.id, rep.note)
            continue
        rep.status, rep.note = res.status, res.note
        rep.merge = cs.merge(OFFICIAL, store, (obs_row(o) for o in res.obs))
        if res.state and not res.failed and (rep.merge.new or rep.merge.updated or rep.id not in state):
            new_state[rep.id] = res.state                           # validators advance only with new data (see run())
    cs.write(OFFICIAL, paths.dir, store, set().union(*(r.merge.months for r in reports)))
    save_state(paths, new_state)
    cals = load_calendars()
    last: dict[str, date] = {}
    for row in store.values():
        prov = row["series_id"].split(":", 1)[0]
        last[prov] = max(last.get(prov, row["date"]), row["date"])
    for r in reports:
        prov = r.id.split(":", 1)[1]
        if prov in last:
            r.asof_max = last[prov]
            first = next(sc for sc in cfg["series"].values() if sc["provider"] == prov)
            r.lag_bd = business_day_lag(last[prov], today, calendar_of(cals, first.get("calendar_id")))
    return reports


def exit_code(reports: list[SourceReport]) -> int:
    return 1 if reports and all(r.status == "FAILED" for r in reports) else 0


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def _md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join("" if c is None else str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def run_report(reports: list[SourceReport], today: date) -> tuple[str, str]:
    heads = ["source", "status", "new", "upd", "same", "held", "raw", "as-of", "lag bd", "note"]
    rows = [[r.id, r.status, r.merge.new, r.merge.updated, r.merge.unchanged, r.dropped,
             "y" if r.raw_written else "", r.asof_max or "", "" if r.lag_bd is None else r.lag_bd, r.note]
            for r in reports]
    text = f"cb_collect {today}\n" + _fmt_text(heads, rows)
    md = f"### cb_collect {today}\n\n" + _md_table(heads, rows)
    return text, md


def _fmt_text(heads: list[str], rows: list[list]) -> str:
    cells = [[str(c) if c is not None else "" for c in r] for r in [heads] + rows]
    w = [max(len(r[i]) for r in cells) for i in range(len(heads))]
    return "\n".join("  ".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip() for r in cells)


def status(paths: Paths, today: date, cfg: dict | None = None) -> tuple[str, str]:
    cfg = cfg or load_sources()
    store = load_store(paths)
    state = load_state(paths)
    marks = state.get(NO_NEW, {})
    cals = load_calendars()
    per: dict[str, list[dict]] = {}
    for row in store.values():
        per.setdefault(row["source"], []).append(row)
    heads = ["source", "history", "rows", "first", "last as-of", "lag bd", "last write (UTC)", "missing wd",
             "validators", "note"]
    rows, gaps = [], []
    for sid, c in cfg["sources"].items():
        rs = per.get(sid, [])
        if not rs:
            rows.append([sid, c.get("history"), 0, "", "", "", "", "", "yes" if state.get(sid) else "no", ""])
            continue
        days = sorted({r["asof"] for r in rs})
        have = set(days)
        m = marks.get(sid, {})
        cal = calendar_of(cals, c.get("calendar_id"))
        missing = cal.missing_days(have, days[0], days[-1])            # weekdays that are not holidays and have no row
        pending = sorted(a for a in m if date.fromisoformat(a) > days[-1])
        note = f"fără valori noi (as-of {pending[-1]} = {m[pending[-1]]})" if pending else ""
        wrote = max(r["fetched_at"] for r in rs)
        rows.append([sid, c.get("history"), len(rs), days[0], days[-1], cal.lag(days[-1], today),
                     wrote.strftime("%Y-%m-%d %H:%M"), len(missing), "yes" if state.get(sid) else "no", note])
        if missing:
            tag = lambda d: d.isoformat() + (" (fără valori noi)" if d.isoformat() in m else "")   # noqa: E731
            gaps.append(f"{sid}: {', '.join(tag(d) for d in missing[-8:])}"
                        + (f" (+{len(missing) - 8} earlier)" if len(missing) > 8 else ""))
    foot = ("last write = newest fetched_at of a row that was new or changed (unchanged rows keep their original "
            "timestamp so idle runs leave no diff); lag bd = business days between the as-of and the last business "
            "day <= today in the source's holiday calendar (Saturday: Friday's data = 0); missing wd = business days of that "
            "calendar without a row between first and last as-of (its holidays are excluded).")
    text = f"cb_collect --status  {today}\n" + _fmt_text(heads, rows) + "\n\n" + foot
    if gaps:
        text += "\nmissing weekdays (latest 8): \n  " + "\n  ".join(gaps)
    md = f"### cb_collect --status {today}\n\n" + _md_table(heads, rows) + f"\n\n_{foot}_"
    if gaps:
        md += "\n\nMissing weekdays (latest 8):\n" + "\n".join(f"- {g}" for g in gaps)
    from . import cb_datasets as dsets                        # decisions / projections / manual / calendar sections
    et, em = dsets.extra_status(paths, today)
    return text + ("\n" + et if et else ""), md + ("\n" + em if em else "")


def _summary(md: str) -> None:
    f = os.environ.get("GITHUB_STEP_SUMMARY")
    if f:
        with open(f, "a") as fh:
            fh.write(md + "\n\n")


STAGES = ("market", "official", "decisions", "documents", "projections", "calendar", "schedule", "summaries")
DEFAULT_STAGES = STAGES[:-1]                 # `summaries` calls a paid API: it runs only when asked for (--stage summaries; a workflow step with the secret)


def _run_stage_report(title: str, reports: list, today: date) -> tuple[str, str]:
    text, md = run_report(reports, today)
    return text.replace("cb_collect", title, 1), md.replace("cb_collect", title, 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Central Banks collector: market quotes, official series, decisions, "
                                             "projections, calendar check")
    ap.add_argument("--stage", action="append", choices=STAGES, help="stage to run (repeatable); default: every stage except summaries")
    ap.add_argument("--source", action="append", help="market source id (repeatable); default all")
    ap.add_argument("--provider", action="append", help="official-series provider (repeatable); default all")
    ap.add_argument("--backfill", action="store_true",
                    help="market: official-history sources from meta.backfill_from; official series from theirs")
    ap.add_argument("--backfill-from", type=date.fromisoformat, help="explicit backfill start (YYYY-MM-DD), both datasets")
    ap.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
    ap.add_argument("--force-calendar-check", action="store_true", help="run the weekly meetings check now")
    ap.add_argument("--documents-bank", action="append", metavar="CCY", help="documents stage: only these banks (repeatable), e.g. USD")
    ap.add_argument("--watch-decision", metavar="CCY", help="phase 4: wait for the statement of this bank's decision (--decision-date), poll every minute for at most "
                                                           "config/cb_trigger.yaml decision.watch_minutes, store it, then the documents of that bank and the decisions; exits clean when it does not appear")
    ap.add_argument("--decision-date", type=date.fromisoformat, help="--watch-decision: the decision day (YYYY-MM-DD, the bank's own calendar)")
    ap.add_argument("--summaries-bank", action="append", metavar="CCY", help="summaries stage: only these banks (repeatable), e.g. USD")
    ap.add_argument("--summaries-type", action="append", metavar="TYPE", help="summaries stage: only these document types (repeatable), e.g. statement")
    ap.add_argument("--summaries-doc", action="append", metavar="DOC_ID", help="summaries stage: only these documents (repeatable), e.g. USD:statement:2026-09-16")
    ap.add_argument("--summaries-effort", choices=("low", "medium", "high"), help="summaries stage: the reasoning effort of this run (default: the config's)")
    ap.add_argument("--summaries-scratch", action="store_true", help="summaries stage: a measurement - write the summaries to a scratch directory and leave data/ and state.json alone")
    ap.add_argument("--summaries-dry-run", action="store_true", help="summaries stage: measure what is still to summarise and estimate the cost; no model call, no key needed")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--data-dir", help="override data/cb (tests, dry runs)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    paths = Paths(a.data_dir)
    today = datetime.now(timezone.utc).date()

    if a.status:
        text, md = status(paths, today)
        print(text)
        _summary(md)
        return 0

    stages = a.stage or list(DEFAULT_STAGES)
    fetch_reports: list[SourceReport] = []
    out_text, out_md = [], []

    def emit(text: str, md: str) -> None:
        out_text.append(text)
        out_md.append(md)

    doc_banks = tuple(a.documents_bank) if a.documents_bank else None
    if a.watch_decision:                                            # phase 4: the job dispatched by the external trigger a minute after a decision
        if a.decision_date is None:
            ap.error("--watch-decision needs --decision-date")
        from .cb_docs.watch import report as watch_report, watch_statement
        from .cb_trigger.schedule import load_config as load_trigger
        dec = load_trigger()["decision"]
        try:
            wrep = watch_statement(paths, a.watch_decision, a.decision_date, minutes=dec["watch_minutes"], poll_seconds=dec["poll_seconds"])
            emit(*watch_report(wrep, dec["watch_minutes"]))
        except Exception as e:                                       # a failed watch never fails the job: the regular runs collect the statement
            log.exception("decision watch failed")
            emit(f"decision watch: FAILED {type(e).__name__}: {e}", f"### decision watch\n\nFAILED `{type(e).__name__}: {e}`")
        stages, doc_banks = ["decisions", "documents"], (a.watch_decision,)

    if "market" in stages:
        bf = a.backfill_from or (load_sources()["meta"]["backfill_from"] if a.backfill else None)
        if isinstance(bf, str):
            bf = date.fromisoformat(bf)
        reps = run(paths, only=a.source, backfill_from=bf, lookback_days=a.lookback_days)
        fetch_reports += reps
        emit(*run_report(reps, today))
    if "official" in stages:
        bf = a.backfill_from or (load_official()["meta"]["backfill_from"] if a.backfill else None)
        if isinstance(bf, str):
            bf = date.fromisoformat(bf)
        reps = run_official(paths, only=a.provider, backfill_from=bf)
        fetch_reports += reps
        emit(*_run_stage_report("cb_collect official series", reps, today))
    # derived / manual datasets never fail the run: they warn (a stale source must not block the others)
    from . import cb_datasets as dsets
    if "decisions" in stages:
        try:
            emit(*dsets.decisions_report(dsets.run_decisions(paths, today), today))
        except Exception as e:
            log.exception("decisions stage failed")
            emit(f"decisions: FAILED {type(e).__name__}: {e}", f"### decisions\n\nFAILED `{type(e).__name__}: {e}`")
    if "documents" in stages:
        try:
            from .cb_docs.collect import run_documents
            rep = run_documents(paths, today, state=load_state(paths), **({"banks": doc_banks} if doc_banks else {}))
            emit(*dsets.documents_report(rep))
            if rep.rate_rows_changed:                                   # a statement rate is new: the decisions take it (precedence official > statement > BIS > FF)
                emit(*dsets.decisions_report(dsets.run_decisions(paths, today), today))
        except Exception as e:
            log.exception("documents stage failed")
            emit(f"documents: FAILED {type(e).__name__}: {e}", f"### documents\n\nFAILED `{type(e).__name__}: {e}`")
    if "projections" in stages:
        try:
            emit(*dsets.projections_report(dsets.run_projections(paths, today)))
        except Exception as e:
            log.exception("projections stage failed")
            emit(f"projections: FAILED {type(e).__name__}: {e}", f"### projections\n\nFAILED `{type(e).__name__}: {e}`")
    if "calendar" in stages:
        try:
            emit(*dsets.calendar_check_report(dsets.run_calendar_check(paths, today, force=a.force_calendar_check)))
        except Exception as e:
            log.exception("calendar check failed")
            emit(f"calendar check: FAILED {type(e).__name__}: {e}", f"### calendar check\n\nFAILED `{type(e).__name__}: {e}`")
    if "schedule" in stages:                                         # phase 4: the schedule the external trigger reads (offline: meetings + config)
        try:
            from .cb_trigger import schedule as trig
            emit(*trig.report(trig.run_schedule(paths, today)))
        except Exception as e:
            log.exception("trigger schedule failed")
            emit(f"trigger schedule: FAILED {type(e).__name__}: {e}", f"### trigger schedule\n\nFAILED `{type(e).__name__}: {e}`")
    if "summaries" in stages:                                        # last: after every text it summarises has been collected; a failure here never fails the run
        try:
            from .cb_summarize import run as sum_run
            narrow = dict(banks=set(a.summaries_bank or []) or None, types=set(a.summaries_type or []) or None,
                          only={d for v in (a.summaries_doc or []) for d in v.split(",") if d.strip()} or None)                      # (a comma separates documents)
            if a.summaries_dry_run:
                text = sum_run.estimate_report(sum_run.estimate(paths, today, **narrow))
                emit(text, "### summaries (dry run)\n\n```\n" + text + "\n```")
            else:
                import dataclasses
                import tempfile
                from .cb_summarize.config import load as load_summaries_config
                cfg = load_summaries_config()
                if a.summaries_effort:
                    cfg = dataclasses.replace(cfg, reasoning_effort=a.summaries_effort)
                if a.summaries_scratch:
                    paths.summaries = Path(tempfile.mkdtemp(prefix="cb_scratch_"))
                emit(*dsets.summaries_report(sum_run.run_summaries(paths, today, state={} if a.summaries_scratch else load_state(paths), cfg=cfg, persist=not a.summaries_scratch, **narrow)))
        except Exception as e:
            log.exception("summaries stage failed")
            emit(f"summaries: FAILED {type(e).__name__}: {e}", f"### summaries\n\nFAILED `{type(e).__name__}: {e}`")
    print("\n\n".join(out_text))
    _summary("\n\n".join(out_md))
    return exit_code(fetch_reports)


if __name__ == "__main__":
    sys.exit(main())
