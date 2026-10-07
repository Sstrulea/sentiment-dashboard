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

2026-10-07 — /calendar/today/: since 2026-10-02 JBlanked's free daily request
applies ONLY to /calendar/today/; the range endpoint answers 401 "requires
credits" (last range success 2026-10-05 01:06 UTC). `pull_actuals` now makes ONE
today call per window (first tick after TODAY_PULL_UTC, 20:30) and NEVER retries —
the guard is on the last ATTEMPT (`should_attempt_today`), not the last success.
The range path (`pull_actuals_range`, `should_pull` above) is kept but not called.

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

from .econ_calendar_ff import (CANON_COLUMNS, ensure_provenance_columns, jblanked_to_utc,
                              parse_jblanked_range)
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
# Since 2026-10-02 JBlanked's free daily request applies ONLY to /calendar/today/
# (the range endpoint answers 401 "requires credits"). One ATTEMPT per day, at the
# first tick after TODAY_PULL_UTC — after the last release of the JB day — and
# never a retry, whatever the outcome: a retry could spend the one free request.
DEFAULT_TODAY_URL = "https://www.jblanked.com/news/api/forex-factory/calendar/today/"
TODAY_PULL_UTC = (20, 30)        # 23:30 Romania (UTC+3)
# Fields ingestion needs; extra fields (e.g. "Trend", added by JB 2026-10-06) are ignored.
REQUIRED_FIELDS = ("Name", "Currency", "Date", "Actual", "Forecast", "Previous")


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


def attempt_window_start(now_utc: pd.Timestamp) -> pd.Timestamp:
    """The most recent TODAY_PULL_UTC instant at or before `now` (UTC-naive)."""
    now = pd.Timestamp(now_utc)
    anchor = now.normalize() + pd.Timedelta(hours=TODAY_PULL_UTC[0], minutes=TODAY_PULL_UTC[1])
    return anchor if now >= anchor else anchor - pd.Timedelta(days=1)


def should_attempt_today(now_utc: pd.Timestamp, state: dict,
                         not_before: Optional[pd.Timestamp] = None) -> bool:
    """At most ONE attempt per window — judged on the last ATTEMPT, not the last
    success, so a failure (401, network, bad payload) waits for the next day.
    `not_before` (config jb_today_not_before) holds the first call back."""
    now = pd.Timestamp(now_utc)
    if not_before is not None and now < pd.Timestamp(not_before):
        return False
    raw = state.get("last_attempt_at")
    if raw:
        try:
            last = pd.Timestamp(raw)
            last = last.tz_convert(None) if last.tzinfo is not None else last
            if last >= attempt_window_start(now):
                return False
        except Exception:  # noqa: BLE001 — unreadable → treat as never attempted
            pass
    return True


def payload_format_ok(data) -> bool:
    """A list of event objects, each carrying REQUIRED_FIELDS (extras ignored)."""
    return isinstance(data, list) and all(
        isinstance(e, dict) and all(f in e for f in REQUIRED_FIELDS) for e in data)


# ---------------------------------------------------------------------------
# Payload cleaning (pure) — validated empirically 2026-07-12
# ---------------------------------------------------------------------------

FF_FORECAST_TOL = 1e-6           # JB forecast == FF consensus (both normalized)


def _ff_forecasts_by_key(schedule: Optional[pd.DataFrame]) -> dict[tuple, float]:
    """{(canonical_id, UTC date): FF consensus} from the parquet rows FF itself
    delivered a forecast for. A key with two different FF forecasts is left
    out (ambiguous → the guard does not apply)."""
    if schedule is None or schedule.empty:
        return {}
    s = ensure_provenance_columns(schedule)
    s = s[(s["forecast_origin"] == "ff") & s["forecast"].notna()]
    if s.empty:
        return {}
    dates = pd.to_datetime(s["datetime_utc"]).dt.date
    out: dict[tuple, float] = {}
    for key, fc in s.groupby([s["canonical_id"], dates], sort=False)["forecast"]:
        vals = fc.unique()
        if len(vals) == 1:
            out[key] = float(vals[0])
    return out


def _forecast_guard(real: pd.DataFrame, ff_fc: Optional[float],
                    key: tuple) -> tuple[pd.DataFrame, bool]:
    """(rows to pick from, rejected) for one group's REAL-actual rows — see the
    forecast guard in clean_jblanked_actuals' docstring."""
    if ff_fc is None or not len(real):
        return real, False
    matches = (real["forecast"] - ff_fc).abs() <= FF_FORECAST_TOL   # NaN → False
    if len(real) == 1:
        if matches.iloc[0]:
            return real, False
        log.warning("JB pull: forecast mismatch %s %s — JB forecast %s vs FF %s; "
                    "actual %s not ingested.", key[0], key[1], real["forecast"].iloc[0],
                    ff_fc, real["actual"].iloc[0])
        return real, True
    if real["actual"].nunique() == 1:
        return real, False
    kept = real[matches]
    if kept["actual"].nunique() == 1:
        return kept, False
    log.warning("JB pull: conflicting duplicates %s %s — actuals %s with forecasts %s "
                "vs FF %s; no actual ingested.", key[0], key[1], list(real["actual"]),
                list(real["forecast"]), ff_fc)
    return real, True

def clean_jblanked_actuals(jb: pd.DataFrame,
                           schedule: Optional[pd.DataFrame] = None, *,
                           preserve_zero_actuals: bool = False) -> pd.DataFrame:
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
         otherwise the group survives as a SCHEDULE row. By default
         (preserve_zero_actuals=False) a lone actual=0.0 is nulled here rather
         than ingested, since at this layer it is indistinguishable from the
         "Data Not Loaded" placeholder. With preserve_zero_actuals=True this
         nulling is skipped and 0.0 is kept as the actual — ingest records what
         the feed sent rather than discarding it; distinguishing a genuine 0.0
         print from the placeholder is deferred to ff_scoring.to_scoring_frame's
         single placeholder rule (jb_status persisted per row, audit 2.3 — was:
         can_be_zero widening, name_raw m/m|q/q suffix + build_flagged_bad_lookup
         Quality/Strength check), which quarantines the flagged ones instead of
         guessing at ingest time. The "real beats zero" pick below is unchanged
         either way — this only affects the case where NO row in the group has
         a real value;
      2. collapse to ONE row: the last after sorting on datetime_utc;
      3. re-align the kept row's datetime_utc to the schedule row already in
         the parquet for the same (canonical_id, date) — the faireconomy hour
         is the correct UTC one, so at merge time the actual lands ON the
         existing schedule row instead of inserting a ±1h phantom sibling.

    Forecast guard (fix/jb-duplicate-forecast-guard, 2026-10): the payload can
    also carry a NEIGHBOUR's numbers under an event's name — on 2026-09-30 the
    12:30 "Core PCE Price Index m/m" copy held Personal Spending's 0.9/0.8,
    and "last real row" picked it over the real 0.2/0.3. When `schedule` holds
    an FF consensus for the (canonical_id, date) key (forecast_origin "ff",
    non-NaN, one distinct value), the JB forecast must match it (FF_FORECAST_TOL):
      a. several real rows with DIFFERENT actuals → keep the matching rows; one
         value left → that one, otherwise no actual (NaN) + WARNING;
      b. a single real row whose forecast differs → no actual (NaN) + WARNING;
      c. identical actuals, or no FF consensus for the key → unchanged.
    A nulled group lands as a schedule row (MISSING in the manual panel).
    """
    if jb is None or jb.empty:
        return pd.DataFrame(columns=CANON_COLUMNS)
    df = ensure_provenance_columns(jb.copy())
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    df["_date"] = df["datetime_utc"].dt.date
    ff_forecast = _ff_forecasts_by_key(schedule)

    picked: list[pd.Series] = []
    for key, g in df.groupby(["canonical_id", "_date"], sort=False):
        real = g[g["actual"].notna() & (g["actual"] != 0.0)]
        real, rejected = _forecast_guard(real, ff_forecast.get(key), key)
        pick = (real if len(real) else g).sort_values("datetime_utc").iloc[-1].copy()
        if rejected or (not len(real) and not preserve_zero_actuals):
            pick["actual"] = float("nan")   # placeholder 0.0 / rejected print → schedule row
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
# Raw-payload quality lookup (fix/can-be-zero-transform)
# ---------------------------------------------------------------------------

ARCHIVE_JSON = ROOT / "data" / "archive" / "ff_calendar_range.json"

# WHITELIST, not blacklist (2026-08 audit finding: the original blacklist let
# an unrecognized future Quality/Strength value silently pass as "not bad" —
# the exact silent-corruption risk this guard exists to prevent). Verified
# closed against ALL available jb_raw history (disk + git, 2194 raw rows,
# 2026-08): exactly these values, nothing else, ever.
QUALITY_ACCEPTED = {"Good Data"}
QUALITY_FLAGGED = {"Bad Data", "Data Not Loaded"}
STRENGTH_ACCEPTED = {"Strong Data", "Weak Data"}
STRENGTH_FLAGGED = {"Data Not Loaded"}


# build_flagged_bad_lookup (the OR of Quality/Strength across every retained
# payload) is gone (audit 2026-09-23, 2.3): the verdict is now persisted per row
# at ingest as jb_status (newest payload wins) and read by
# ff_scoring.actual_is_placeholder. QUALITY_*/STRENGTH_* above document the
# values ever observed.


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


ERROR_BODY_CHARS = 300
_RATE_HEADER_HINTS = ("ratelimit", "rate-limit", "retry-after", "quota", "x-limit")

# Observability (audit F5): what the LAST real call returned — HTTP status, the
# rate-limit headers if any, and the start of the body on an error. Set by
# fetch_range_raw, read by pull_actuals into data/jb_last_pull.json. Never holds
# the key (redacted before it is stored).
_last_response_meta: Optional[dict] = None


def _response_meta(r, api_key: str) -> dict:
    headers = {k: v for k, v in r.headers.items()
               if any(h in k.lower() for h in _RATE_HEADER_HINTS)}
    meta = {"http_status": r.status_code, "rate_limit_headers": headers}
    if r.status_code >= 400:
        body = (r.text or "")[:ERROR_BODY_CHARS]
        meta["body"] = body.replace(api_key, "***") if api_key else body
    return meta


def fetch_range_raw(from_date: date, to_date: date, *, api_key: str,
                    url: str = DEFAULT_RANGE_URL, timeout: int = 30) -> str:
    """ONE authenticated JBlanked range call → raw response text (unparsed).
    Free tier ≈ 1 call/day — callers must gate through should_pull()."""
    global _last_response_meta
    import requests
    r = requests.get(
        url,
        params={"from": from_date.isoformat(), "to": to_date.isoformat()},
        headers={"Authorization": f"Api-Key {api_key}",
                 "User-Agent": "macro-data-analysis/1.0"},
        timeout=timeout,
    )
    _last_response_meta = _response_meta(r, api_key)
    r.raise_for_status()
    return r.text


def fetch_today_raw(*, api_key: str, url: str = DEFAULT_TODAY_URL, timeout: int = 30) -> str:
    """ONE authenticated /calendar/today/ call → raw response text (unparsed).
    The free tier's single daily request — callers gate through should_attempt_today()."""
    global _last_response_meta
    import requests
    r = requests.get(url, headers={"Authorization": f"Api-Key {api_key}",
                                   "User-Agent": "macro-data-analysis/1.0"}, timeout=timeout)
    _last_response_meta = _response_meta(r, api_key)
    r.raise_for_status()
    return r.text


def _record_attempt(state_path: Path, now: pd.Timestamp, status: str,
                    error: Optional[str] = None) -> None:
    """Persist the outcome of a REAL call next to the last-success fields, which
    are left exactly as they were (observability only, no behavior change)."""
    meta = _last_response_meta or {}
    state = load_state(state_path)
    state.update({
        "last_attempt_at": now.isoformat(),
        "last_status": status,
        "last_http_status": meta.get("http_status"),
        "last_error": (meta.get("body") or error or None) if status != "ok" else None,
        "last_rate_limit_headers": meta.get("rate_limit_headers") or {},
    })
    if state.get("last_error"):
        state["last_error"] = str(state["last_error"])[:ERROR_BODY_CHARS]
    save_state(state, state_path)


def save_raw(text: str, now_utc: pd.Timestamp, raw_dir: Path = RAW_DIR,
             keep: int = RAW_KEEP, prefix: str = "jb_range") -> Path:
    """Persist a payload to disk BEFORE any parsing; prune to newest `keep`."""
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    p = raw_dir / f"{prefix}_{pd.Timestamp(now_utc).strftime('%Y-%m-%dT%H%M%SZ')}.json"
    p.write_text(text)
    for f in sorted(raw_dir.glob(f"{prefix}_*.json"))[:-keep]:   # ISO names sort by time
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


def pull_actuals_range(*, now_utc: Optional[pd.Timestamp] = None,
                 parquet_path: Path = FF_PARQUET,
                 state_path: Path = STATE_JSON,
                 raw_dir: Path = RAW_DIR,
                 fetcher: Optional[Callable[[date, date], str]] = None,
                 cfg: Optional[dict] = None,
                 force: bool = False) -> dict:
    """RANGE endpoint (kept, NOT called since 2026-10-07 — it needs paid
    credits since JBlanked's 2026-10-02 change; see pull_actuals for the free
    /calendar/today/ path). Window-gated daily pull → save raw → parse → clean →
    field-aware merge.

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
    global _last_response_meta
    _last_response_meta = None
    try:
        raw_text = fetcher(from_d, to_d + timedelta(days=1))  # JB to este EXCLUSIV
    except Exception as e:  # noqa: BLE001 — HTTP/network/auth failure
        log.warning("JB pull: range fetch FAILED (%s); will retry next tick.", str(e)[:120])
        meta = _last_response_meta or {}
        if meta.get("body"):
            log.warning("JB pull: HTTP %s body: %s", meta.get("http_status"), meta["body"])
        _record_attempt(state_path, now, "fetch_failed", f"{type(e).__name__}: {e}")
        return {"status": "fetch_failed"}

    raw_path = save_raw(raw_text, now, raw_dir=raw_dir)   # raw on disk BEFORE parse

    try:
        jb = parse_jblanked_range(json.loads(raw_text), now_utc=now)
    except Exception as e:  # noqa: BLE001 — bad JSON/schema/value
        log.warning("JB pull: parse FAILED (%s); raw kept at %s; will retry next tick.",
                    str(e)[:120], raw_path.name)
        _record_attempt(state_path, now, "parse_failed", f"{type(e).__name__}: {e}")
        return {"status": "parse_failed", "raw": str(raw_path)}

    if jb.empty:
        log.warning("JB pull: payload mapped 0 events; treated as failure "
                    "(state not advanced); raw kept at %s.", raw_path.name)
        _record_attempt(state_path, now, "empty", "payload mapped 0 events")
        return {"status": "empty", "raw": str(raw_path)}

    parquet_path = Path(parquet_path)
    from .ff_provenance import ensure_provenance
    ensure_provenance(parquet_path)            # audit Z5: one-off, idempotent
    existing = pd.read_parquet(parquet_path) if parquet_path.exists() else None
    cleaned = clean_jblanked_actuals(jb, schedule=existing, preserve_zero_actuals=True)
    merged = merge_weekly(existing, cleaned)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(parquet_path, index=False)

    n_before = 0 if existing is None else len(existing)
    n_act = int(cleaned["actual"].notna().sum())
    save_state({"last_success_utc_date": str(now.date()),
                "last_success_at": now.isoformat(),
                "rows": len(cleaned), "actuals": n_act}, state_path)
    _record_attempt(state_path, now, "ok")
    log.info("JB pull: OK — %d cleaned row(s), %d with actuals; parquet %d -> %d rows; raw %s.",
             len(cleaned), n_act, n_before, len(merged), raw_path.name)
    return {"status": "ok", "rows": len(cleaned), "actuals": n_act,
            "rows_before": n_before, "rows_after": len(merged), "raw": str(raw_path)}


def pull_actuals(*, now_utc: Optional[pd.Timestamp] = None,
                 parquet_path: Path = FF_PARQUET,
                 state_path: Path = STATE_JSON,
                 raw_dir: Path = RAW_DIR,
                 fetcher: Optional[Callable[[], str]] = None,
                 cfg: Optional[dict] = None,
                 force: bool = False) -> dict:
    """The daily pull on the FREE /calendar/today/ endpoint (2026-10-07).

    One attempt per window (first tick after TODAY_PULL_UTC), never retried:
    every attempt — 200 or not — is recorded in data/jb_last_pull.json
    (last_attempt_at/status/http_status/error) and its body saved to
    data/jb_raw/jb_today_*.json, so the next tick of the same window skips.
    A 200 is ingested only when every event carries REQUIRED_FIELDS (extra
    fields ignored); otherwise status `format_mismatch`, nothing ingested.
    Ingestion is the range path's: parse → clean_jblanked_actuals (forecast
    guard) → field-aware merge; CORRECTION overrides apply at read time.
    Never raises. `fetcher() -> raw text` is injectable for tests; `force`
    bypasses the window guard (manual recovery only — it spends the request).
    """
    now = (pd.Timestamp(now_utc) if now_utc is not None
           else pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None)))
    cfg = cfg if cfg is not None else _safe_cfg()
    state = load_state(state_path)
    nb = cfg.get("jb_today_not_before")
    not_before = None
    if nb:
        not_before = pd.Timestamp(nb)
        not_before = not_before.tz_convert(None) if not_before.tzinfo is not None else not_before
    if not force and not should_attempt_today(now, state, not_before):
        log.info("JB pull: skipped (attempt window %s already tried, or before %s).",
                 attempt_window_start(now).isoformat(), not_before)
        return {"status": "skipped", "reason": "window_attempted"}

    if fetcher is None:
        key = _api_key()
        if not key:
            log.warning("JB pull: JBLANKED_API_KEY missing — skipped; calendar "
                        "actuals WILL go stale (watch the dashboard badge).")
            return {"status": "no_api_key"}
        url = cfg.get("jb_today_url", DEFAULT_TODAY_URL)
        fetcher = lambda: fetch_today_raw(api_key=key, url=url)  # noqa: E731

    global _last_response_meta
    _last_response_meta = None
    try:
        raw_text = fetcher()
    except Exception as e:  # noqa: BLE001 — HTTP/network/auth failure: no retry today
        meta = _last_response_meta or {}
        if meta.get("body") is not None:
            save_raw(meta["body"], now, raw_dir=raw_dir, prefix="jb_today")
        log.warning("JB pull: today fetch FAILED (HTTP %s: %s); next attempt in the next window.",
                    meta.get("http_status"), str(meta.get("body") or e)[:160])
        _record_attempt(state_path, now, "fetch_failed", f"{type(e).__name__}: {e}")
        return {"status": "fetch_failed", "http_status": meta.get("http_status")}

    raw_path = save_raw(raw_text, now, raw_dir=raw_dir, prefix="jb_today")
    try:
        data = json.loads(raw_text)
    except Exception as e:  # noqa: BLE001
        log.warning("JB pull: today payload is not JSON (%s); raw kept at %s.", str(e)[:120], raw_path.name)
        _record_attempt(state_path, now, "format_mismatch", f"not JSON: {e}")
        return {"status": "format_mismatch", "raw": str(raw_path)}
    if not payload_format_ok(data):
        log.warning("JB pull: today payload lacks %s; NOT ingested; raw kept at %s.",
                    "/".join(REQUIRED_FIELDS), raw_path.name)
        _record_attempt(state_path, now, "format_mismatch",
                        "payload is not a list of events with " + ", ".join(REQUIRED_FIELDS))
        return {"status": "format_mismatch", "raw": str(raw_path)}

    try:
        jb = parse_jblanked_range([{f: e[f] for f in REQUIRED_FIELDS + ("Quality", "Strength") if f in e}
                                   for e in data], now_utc=now)
    except Exception as e:  # noqa: BLE001
        log.warning("JB pull: today parse FAILED (%s); raw kept at %s.", str(e)[:120], raw_path.name)
        _record_attempt(state_path, now, "parse_failed", f"{type(e).__name__}: {e}")
        return {"status": "parse_failed", "raw": str(raw_path)}
    if jb.empty:
        log.warning("JB pull: today payload mapped 0 events; raw kept at %s.", raw_path.name)
        _record_attempt(state_path, now, "empty", "payload mapped 0 events")
        return {"status": "empty", "raw": str(raw_path)}

    parquet_path = Path(parquet_path)
    from .ff_provenance import ensure_provenance
    ensure_provenance(parquet_path)
    existing = pd.read_parquet(parquet_path) if parquet_path.exists() else None
    cleaned = clean_jblanked_actuals(jb, schedule=existing, preserve_zero_actuals=True)
    merged = merge_weekly(existing, cleaned)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(parquet_path, index=False)

    n_before = 0 if existing is None else len(existing)
    n_act = int(cleaned["actual"].notna().sum())
    save_state({"last_success_utc_date": str(now.date()),
                "last_success_at": now.isoformat(), "endpoint": "today",
                "rows": len(cleaned), "actuals": n_act}, state_path)
    _record_attempt(state_path, now, "ok")
    log.info("JB pull: OK (today) — %d cleaned row(s), %d with actuals; parquet %d -> %d rows; raw %s.",
             len(cleaned), n_act, n_before, len(merged), raw_path.name)
    return {"status": "ok", "rows": len(cleaned), "actuals": n_act,
            "rows_before": n_before, "rows_after": len(merged), "raw": str(raw_path)}


def main() -> int:
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    ap = argparse.ArgumentParser(description="Daily JBlanked actuals pull (/calendar/today/, "
                                             "one attempt per window)")
    ap.add_argument("--force", action="store_true",
                    help="bypass the attempt-window guard (manual recovery — spends the free request)")
    args = ap.parse_args()
    rep = pull_actuals(force=args.force)
    extra = (f" — parquet {rep['rows_before']} -> {rep['rows_after']} rows"
             if rep["status"] == "ok" else "")
    print(f"JB pull: {rep['status']}{extra}")
    return 0   # fail-open, like the rest of the refresh


if __name__ == "__main__":
    import sys
    sys.exit(main())
