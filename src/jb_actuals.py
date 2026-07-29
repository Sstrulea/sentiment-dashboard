"""Daily JBlanked actuals pull (Phase 3 structural fix).

The faireconomy weekly feed (the Phase 3 calendar source) is STRUCTURALLY
actual-less — it carries only country/date/forecast/impact/previous/title.
Discovered 2026-07-12: after the JBlanked backfill ended (2026-07-03) no actual
was ever ingested again while every hourly tick logged "ok". This module adds
the missing leg of the hybrid flow: once per due window (the most recent 18:00
UTC instant that has passed — evening when the laptop is awake, otherwise the
first tick after wake-up) ONE authenticated JBlanked range call (gap-aware
span) supplies the actuals; the hourly faireconomy ingest
remains the schedule/forecast source, unchanged.

Free-tier discipline: ≈1 call/day. `should_pull` + the state persisted in
data/jb_last_pull.json guarantee at most one SUCCESSFUL pull per due window; a
failed attempt leaves the state untouched so the next hourly tick retries.
Every 200 response is written raw to data/jb_raw/ BEFORE any parsing (newest
~14 kept) so a bad payload stays reproducible after the fact.

The payload needs cleaning (validated empirically on 2026-07-12, reproduced
here as the pure `clean_jblanked_actuals` — see its docstring). Everything is
fail-open, mirroring the pipeline contract: a JBlanked failure never degrades
the last-good parquet and never blocks the rest of the refresh. All log lines
carry the "JB pull:" prefix so /tmp/econ.log stays greppable. The API key is
read from the environment (.env: JBLANKED_API_KEY) and is never logged.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from .econ_calendar_ff import CANON_COLUMNS, parse_jblanked_range
from .ff_refresh import FF_PARQUET, load_pipeline_config, merge_weekly

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
STATE_JSON = ROOT / "data" / "jb_last_pull.json"
RAW_DIR = ROOT / "data" / "jb_raw"
RAW_KEEP = 14                    # newest raw payloads kept on disk
PULL_HOUR_UTC = 18               # end-of-day window opens here (21:00 local, UTC+3)
RANGE_DAYS = 7                   # MINIMUM range span [today-RANGE_DAYS, today] (UTC)
MAX_RANGE_DAYS = 60              # cap when catching up after a long absence
DEFAULT_RANGE_URL = "https://www.jblanked.com/news/api/forex-factory/calendar/range/"


# ---------------------------------------------------------------------------
# State (data/jb_last_pull.json) + window guard
# ---------------------------------------------------------------------------

def load_state(path: Path = STATE_JSON) -> dict:
    """Read the last-successful-pull state; missing/corrupt file → {} (fail-open)."""
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001 — no state yet, or unreadable
        return {}


def save_state(state: dict, path: Path = STATE_JSON) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1) + "\n")


def due_window_start(now_utc: pd.Timestamp) -> pd.Timestamp:
    """The most recent PULL_HOUR_UTC instant at or before `now` (UTC-naive)."""
    now = pd.Timestamp(now_utc)
    anchor = now.normalize() + pd.Timedelta(hours=PULL_HOUR_UTC)
    return anchor if now >= anchor else anchor - pd.Timedelta(days=1)


def last_success_ts(state: dict) -> Optional[pd.Timestamp]:
    """Last successful pull as a UTC-naive Timestamp, or None. Migrates the
    legacy date-only state by reading it as that day's PULL_HOUR_UTC instant."""
    raw = state.get("last_success_at")
    if raw:
        try:
            ts = pd.Timestamp(raw)
            return ts.tz_convert(None) if ts.tzinfo is not None else ts
        except Exception:  # noqa: BLE001
            pass
    d = state.get("last_success_utc_date")
    if d:
        try:
            return pd.Timestamp(str(d)).normalize() + pd.Timedelta(hours=PULL_HOUR_UTC)
        except Exception:  # noqa: BLE001
            pass
    return None


def should_pull(now_utc: pd.Timestamp, state: dict) -> bool:
    """Due-window guard (wake-triggered). The machine is a laptop that sleeps at
    night, so a fixed clock window is regularly missed and the old
    "morning fallback only if last success != yesterday" rule deadlocked: a
    morning pull marked the day done and blocked the NEXT morning, while the
    evening window was never reached (asleep) — actuals landed ~2 days late.

    New rule: due_window_start = the most recent PULL_HOUR_UTC instant that has
    passed; a pull is due while the last SUCCESS predates it. So: evening pull
    when awake, otherwise the FIRST tick after wake-up, at ANY hour — and at
    most one successful pull per window, so the free tier (~1 call/day) holds.
    """
    now = pd.Timestamp(now_utc)
    due = due_window_start(now)
    last = last_success_ts(state)
    if last is not None and last >= due:
        return False                                    # window already covered
    return True                                         # behind → retry on every tick


# ---------------------------------------------------------------------------
# Payload cleaning (pure) — validated empirically 2026-07-12
# ---------------------------------------------------------------------------

def clean_jblanked_actuals(jb: pd.DataFrame,
                           schedule: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """De-dup/repair a PARSED JBlanked range frame (canonical schema) — pure.

    Empirical defects of the range payload (validated on the real capture
    tests/fixtures/jb_range_raw_2026-07-12.json):
      * the same event can appear TWICE with timestamps exactly 1h apart (a DST
        artifact of the feed's ET+7 wall clock);
      * either copy may carry actual=0.0 — the feed's "unreleased" placeholder
        ("Data Not Loaded"); which copy is the real one is NOT consistent
        (early vs late varies per event).

    Per (canonical_id, UTC calendar date) group:
      1. keep the rows with a REAL actual (non-NaN and != 0.0) when any exists;
         otherwise the group survives as a SCHEDULE row — actual 0.0 is by
         definition the unreleased placeholder here, so it is nulled rather
         than ingested as a fake print (known trade-off: a genuine 0.0 print is
         indistinguishable and stays unfilled until revised);
      2. collapse to ONE row: the last after sorting on datetime_utc;
      3. re-align the kept row's datetime_utc to the schedule row already in
         the parquet for the same (canonical_id, date) — the faireconomy hour
         is the correct UTC one, so at merge time the actual lands ON the
         existing schedule row instead of inserting a ±1h phantom sibling.
    """
    if jb is None or jb.empty:
        return pd.DataFrame(columns=CANON_COLUMNS)
    df = jb.copy()
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    df["_date"] = df["datetime_utc"].dt.date

    picked: list[pd.Series] = []
    for _key, g in df.groupby(["canonical_id", "_date"], sort=False):
        real = g[g["actual"].notna() & (g["actual"] != 0.0)]
        pick = (real if len(real) else g).sort_values("datetime_utc").iloc[-1].copy()
        if not len(real):
            pick["actual"] = float("nan")   # placeholder 0.0 → schedule row
        picked.append(pick)
    out = pd.DataFrame(picked)

    if schedule is not None and len(schedule):
        sched = schedule.copy()
        sched["datetime_utc"] = pd.to_datetime(sched["datetime_utc"])
        sched["_date"] = sched["datetime_utc"].dt.date
        by_group: dict[tuple, list] = {
            key: list(g["datetime_utc"])
            for key, g in sched.groupby(["canonical_id", "_date"], sort=False)
        }
        for idx, row in out.iterrows():
            dt = row["datetime_utc"]
            cand = by_group.get((row["canonical_id"], dt.date()))
            if cand:
                # nearest schedule datetime; deterministic tie-break on the earlier
                out.at[idx, "datetime_utc"] = min(cand, key=lambda t: (abs(t - dt), t))

    out = (out.drop(columns="_date")
              .sort_values(["currency", "canonical_id", "datetime_utc"])
              .reset_index(drop=True))
    return out[CANON_COLUMNS]


# ---------------------------------------------------------------------------
# Fetch + raw persistence
# ---------------------------------------------------------------------------

def _api_key() -> Optional[str]:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001 — dotenv absent (CI) → env vars set directly
        pass
    return os.environ.get("JBLANKED_API_KEY") or None


def fetch_range_raw(from_date: date, to_date: date, *, api_key: str,
                    url: str = DEFAULT_RANGE_URL, timeout: int = 30) -> str:
    """ONE authenticated JBlanked range call → raw response text (unparsed).
    Free tier ≈ 1 call/day — callers must gate through should_pull()."""
    import requests
    r = requests.get(
        url,
        params={"from": from_date.isoformat(), "to": to_date.isoformat()},
        headers={"Authorization": f"Api-Key {api_key}",
                 "User-Agent": "macro-data-analysis/1.0"},
        timeout=timeout,
    )
    r.raise_for_status()
    return r.text


def save_raw(text: str, now_utc: pd.Timestamp, raw_dir: Path = RAW_DIR,
             keep: int = RAW_KEEP) -> Path:
    """Persist a 200 payload to disk BEFORE any parsing; prune to newest `keep`."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    p = raw_dir / f"jb_range_{pd.Timestamp(now_utc).strftime('%Y-%m-%dT%H%M%SZ')}.json"
    p.write_text(text)
    for f in sorted(raw_dir.glob("jb_range_*.json"))[:-keep]:   # ISO names sort by time
        try:
            f.unlink()
        except OSError:
            pass
    return p


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _safe_cfg() -> dict:
    try:
        return load_pipeline_config()
    except Exception:  # noqa: BLE001 — config unreadable → defaults
        return {}


def pull_actuals(*, now_utc: Optional[pd.Timestamp] = None,
                 parquet_path: Path = FF_PARQUET,
                 state_path: Path = STATE_JSON,
                 raw_dir: Path = RAW_DIR,
                 fetcher: Optional[Callable[[date, date], str]] = None,
                 cfg: Optional[dict] = None,
                 force: bool = False) -> dict:
    """Window-gated daily pull → save raw → parse → clean → field-aware merge.

    Never raises: every failure is logged ("JB pull:" prefix), keeps the
    last-good parquet AND leaves the state untouched (→ the next hourly tick
    retries). Only a successful merge advances data/jb_last_pull.json.
    `fetcher(from_date, to_date) -> raw text` is injectable for tests; `force`
    bypasses the window guard for manual/recovery runs (still one real call).
    """
    now = (pd.Timestamp(now_utc) if now_utc is not None
           else pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None)))
    state = load_state(state_path)
    if not force and not should_pull(now, state):
        _due, _last = due_window_start(now), last_success_ts(state)
        log.info("JB pull: skipped (window_covered; window %s, last success %s).",
                 _due.isoformat(), _last.isoformat() if _last is not None else "never")
        return {"status": "skipped", "reason": "window_covered"}

    cfg = cfg if cfg is not None else _safe_cfg()
    if fetcher is None:
        key = _api_key()
        if not key:
            log.warning("JB pull: JBLANKED_API_KEY missing — skipped; calendar "
                        "actuals WILL go stale (watch the dashboard badge).")
            return {"status": "no_api_key"}
        url = cfg.get("jb_range_url", DEFAULT_RANGE_URL)
        fetcher = lambda f, t: fetch_range_raw(f, t, api_key=key, url=url)  # noqa: E731

    to_d = now.date()
    _last = last_success_ts(state)
    span = RANGE_DAYS if _last is None else max(RANGE_DAYS, (to_d - _last.date()).days + 1)
    from_d = to_d - timedelta(days=min(span, MAX_RANGE_DAYS))
    try:
        raw_text = fetcher(from_d, to_d + timedelta(days=1))  # JB to este EXCLUSIV
    except Exception as e:  # noqa: BLE001 — HTTP/network/auth failure
        log.warning("JB pull: range fetch FAILED (%s); will retry next tick.", str(e)[:120])
        return {"status": "fetch_failed"}

    raw_path = save_raw(raw_text, now, raw_dir=raw_dir)   # raw on disk BEFORE parse

    try:
        jb = parse_jblanked_range(json.loads(raw_text), now_utc=now)
    except Exception as e:  # noqa: BLE001 — bad JSON/schema/value
        log.warning("JB pull: parse FAILED (%s); raw kept at %s; will retry next tick.",
                    str(e)[:120], raw_path.name)
        return {"status": "parse_failed", "raw": str(raw_path)}

    if jb.empty:
        log.warning("JB pull: payload mapped 0 events; treated as failure "
                    "(state not advanced); raw kept at %s.", raw_path.name)
        return {"status": "empty", "raw": str(raw_path)}

    parquet_path = Path(parquet_path)
    existing = pd.read_parquet(parquet_path) if parquet_path.exists() else None
    cleaned = clean_jblanked_actuals(jb, schedule=existing)
    merged = merge_weekly(existing, cleaned)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(parquet_path, index=False)

    n_before = 0 if existing is None else len(existing)
    n_act = int(cleaned["actual"].notna().sum())
    save_state({"last_success_utc_date": str(now.date()),
                "last_success_at": now.isoformat(),
                "rows": len(cleaned), "actuals": n_act}, state_path)
    log.info("JB pull: OK — %d cleaned row(s), %d with actuals; parquet %d -> %d rows; raw %s.",
             len(cleaned), n_act, n_before, len(merged), raw_path.name)
    return {"status": "ok", "rows": len(cleaned), "actuals": n_act,
            "rows_before": n_before, "rows_after": len(merged), "raw": str(raw_path)}


def main() -> int:
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    ap = argparse.ArgumentParser(description="Daily JBlanked actuals pull (window-gated)")
    ap.add_argument("--force", action="store_true",
                    help="bypass the due-window guard (manual recovery)")
    args = ap.parse_args()
    rep = pull_actuals(force=args.force)
    extra = (f" — parquet {rep['rows_before']} -> {rep['rows_after']} rows"
             if rep["status"] == "ok" else "")
    print(f"JB pull: {rep['status']}{extra}")
    return 0   # fail-open, like the rest of the refresh


if __name__ == "__main__":
    import sys
    sys.exit(main())
