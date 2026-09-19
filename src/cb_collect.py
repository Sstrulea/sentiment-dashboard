"""Central Banks market collector -> data/cb/market_quotes.parquet (phase 1A).

    python -m src.cb_collect                       # normal run: last 10 days, every source
    python -m src.cb_collect --backfill            # official-history sources from config meta.backfill_from
    python -m src.cb_collect --source boe_ois      # subset
    python -m src.cb_collect --status              # last data write / as-of / lag / missing days per source

Store rules
  * append-only; key (source, instrument, contract, field, asof); last-write-wins on the same key, but a row
    whose content is unchanged keeps its original `fetched_at` (idempotent runs => no git diff);
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

from .cb_sources import ADAPTERS, load_sources
from .cb_sources.base import Quote

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
        self.parquet = self.dir / "market_quotes.parquet"
        self.state = self.dir / "state.json"
        self.raw = self.dir / "raw"


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

def _key(row: dict) -> tuple:
    return tuple(row[k] for k in KEY)


def load_store(path: Path) -> dict[tuple, dict]:
    if not path.exists():
        return {}
    return {_key(r): r for r in pq.read_table(path).to_pylist()}


def write_store(path: Path, store: dict[tuple, dict]) -> None:
    rows = [store[k] for k in sorted(store)]
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


def quote_row(q: Quote) -> dict:
    return {c: getattr(q, c) for c in COLUMNS}


@dataclass
class Merge:
    new: int = 0
    updated: int = 0
    unchanged: int = 0


def merge_quotes(store: dict[tuple, dict], quotes: list[Quote]) -> Merge:
    """Last-write-wins on the key; identical content keeps the stored row (and its fetched_at)."""
    st = Merge()
    for q in quotes:
        row = quote_row(q)
        k = _key(row)
        old = store.get(k)
        if old is None:
            store[k] = row
            st.new += 1
        elif all(old[c] == row[c] for c in CONTENT):
            st.unchanged += 1
        else:
            store[k] = row
            st.updated += 1
    return st


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

@dataclass
class SourceReport:
    id: str
    status: str = ""               # ok | not_modified | skipped | FAILED
    note: str = ""
    merge: Merge = field(default_factory=Merge)
    dropped: int = 0               # held back by eod_cutoff
    raw_written: bool = False
    asof_max: Optional[date] = None
    lag_bd: Optional[int] = None


def _bdays(a: date, b: date) -> int:
    return int(np.busday_count(a, b)) if b >= a else 0


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
    store = load_store(paths.parquet)
    state = load_state(paths)
    new_state = dict(state)
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
        rep.merge = merge_quotes(store, final)
        if res.raw and final and any(q.asof == res.raw["asof"] for q in final):
            raws.append((sid, res.raw["asof"], res.raw["text"]))
        if res.state and rep.dropped == 0:                          # cutoff held rows back -> re-fetch next run
            new_state[sid] = res.state
        elif rep.dropped:
            rep.note = (rep.note + f" {rep.dropped} quote(s) before eod_cutoff, validators kept").strip()

    # persist: data first, then raw, then validators
    if any(r.merge.new or r.merge.updated for r in reports):
        write_store(paths.parquet, store)
    for sid, asof, text in raws:
        next(r for r in reports if r.id == sid).raw_written = write_raw(paths, sid, asof, text)
    save_state(paths, new_state)

    by_src: dict[str, list[date]] = {}
    for row in store.values():
        by_src.setdefault(row["source"], []).append(row["asof"])
    for r in reports:
        if r.id in by_src:
            r.asof_max = max(by_src[r.id])
            r.lag_bd = _bdays(r.asof_max, today)
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
    store = load_store(paths.parquet)
    state = load_state(paths)
    per: dict[str, list[dict]] = {}
    for row in store.values():
        per.setdefault(row["source"], []).append(row)
    heads = ["source", "history", "rows", "first", "last as-of", "lag bd", "last write (UTC)", "missing wd", "validators"]
    rows, gaps = [], []
    for sid, c in cfg["sources"].items():
        rs = per.get(sid, [])
        if not rs:
            rows.append([sid, c.get("history"), 0, "", "", "", "", "", "yes" if state.get(sid) else "no"])
            continue
        days = sorted({r["asof"] for r in rs})
        have = set(days)
        missing = [d for d in (days[0] + timedelta(n) for n in range((days[-1] - days[0]).days + 1))
                   if d.weekday() < 5 and d not in have]
        wrote = max(r["fetched_at"] for r in rs)
        rows.append([sid, c.get("history"), len(rs), days[0], days[-1], _bdays(days[-1], today),
                     wrote.strftime("%Y-%m-%d %H:%M"), len(missing), "yes" if state.get(sid) else "no"])
        if missing:
            gaps.append(f"{sid}: {', '.join(d.isoformat() for d in missing[-8:])}"
                        + (f" (+{len(missing) - 8} earlier)" if len(missing) > 8 else ""))
    foot = ("last write = newest fetched_at of a row that was new or changed (unchanged rows keep their original "
            "timestamp so idle runs leave no diff); missing wd = weekdays without a row between first and last as-of "
            "(exchange holidays included - the calendar of each exchange is not modelled).")
    text = f"cb_collect --status  {today}\n" + _fmt_text(heads, rows) + "\n\n" + foot
    if gaps:
        text += "\nmissing weekdays (latest 8): \n  " + "\n  ".join(gaps)
    md = f"### cb_collect --status {today}\n\n" + _md_table(heads, rows) + f"\n\n_{foot}_"
    if gaps:
        md += "\n\nMissing weekdays (latest 8):\n" + "\n".join(f"- {g}" for g in gaps)
    return text, md


def _summary(md: str) -> None:
    f = os.environ.get("GITHUB_STEP_SUMMARY")
    if f:
        with open(f, "a") as fh:
            fh.write(md + "\n\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Central Banks market-quote collector")
    ap.add_argument("--source", action="append", help="source id (repeatable); default all")
    ap.add_argument("--backfill", action="store_true", help="official-history sources from meta.backfill_from")
    ap.add_argument("--backfill-from", type=date.fromisoformat, help="explicit backfill start (YYYY-MM-DD)")
    ap.add_argument("--lookback-days", type=int, default=LOOKBACK_DAYS)
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

    bf = a.backfill_from or (load_sources()["meta"]["backfill_from"] if a.backfill else None)
    if isinstance(bf, str):
        bf = date.fromisoformat(bf)
    reports = run(paths, only=a.source, backfill_from=bf, lookback_days=a.lookback_days)
    text, md = run_report(reports, today)
    print(text)
    _summary(md)
    return exit_code(reports)


if __name__ == "__main__":
    sys.exit(main())
