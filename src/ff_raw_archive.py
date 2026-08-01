"""fix/alert-noise-and-ff-archive — FAZA 2: archive the faireconomy WEEKLY
SCHEDULE payload (title/country/date/impact/forecast/previous[/actual]).

Closes the limit flagged in docs/calendar-freshness-per-currency.md: no
weekly SCHEDULE snapshot is saved anywhere, so "wasn't published" vs.
"wasn't scheduled" stays an approximation — `data/jb_raw/` only holds
JBlanked's ACTUALS-focused range payloads. This gives the missing
discriminator, going forward (utility accrues over the coming weeks/months,
not today — see the module docstring in the proposal doc).

Mirrors `src/jb_actuals.py`'s raw-persistence philosophy (write the raw
response before any parsing, so a bad/thin payload stays reproducible) but
NOT its per-tick cadence: the weekly SCHEDULE changes far less often than
JBlanked's daily actuals, so this saves at most once per CALENDAR DAY, with
a content-hash-equivalent dedup (byte comparison against the most recent
saved snapshot) skipping the write entirely when nothing changed — avoiding
both wasted commits and unbounded repo growth.

Fully decoupled from `src.econ_calendar_ff.parse_ff_weekly` (which fetches
AND parses in one call, never exposing the raw text) — this module does its
OWN independent fetch of the same keyless, public, unauthenticated URL
`src.ff_refresh.refresh()` already calls. The extra GET is a few tens of KB
on a feed with no rate limit; the alternative (threading a raw-text hook
through the shared parser) would touch code used by both the FF weekly and
JBlanked-range paths for a purely additive archival feature — decoupling
keeps the blast radius to this one new file.

Pure I/O in a small, easily-testable surface: `save_weekly_payload` never
touches the network; `fetch_and_archive_weekly` is the only function that
does, and is meant to be called from `ff_refresh.refresh()` inside its own
try/except (mirroring the existing FRED-cross-check block there) — a
failure here must NEVER affect the merge/quarantine path.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "ff_raw"

# 90 daily snapshots ~= 3 months. Reasoning (docs/proposal-ff-weekly-archive.md):
# gives 2-3 full release cycles for every monthly-cadence indicator (matching
# the same "at least 2 prints before judging a pattern" bar used for the
# alert_exceptions review_by dates), and reaches a full cycle for quarterly
# series. At the feed's own ~30-90 KB size, 90 files is a few MB — bounded,
# not unbounded growth, same rotate-oldest-out mechanism as data/jb_raw/.
RETAIN = 90


def fetch_weekly_raw_text(url: str, *, timeout: int = 30) -> str:
    """The one network call in this module. No retry/backoff — a transient
    failure here is advisory (see module docstring); the caller's try/except
    is what keeps this from ever affecting the refresh's own contract."""
    r = requests.get(url, headers={"User-Agent": "macro-data-analysis/1.0"}, timeout=timeout)
    r.raise_for_status()
    return r.text


def _today_str(as_of: Optional[pd.Timestamp]) -> str:
    ts = pd.Timestamp(as_of) if as_of is not None else pd.Timestamp.utcnow()
    return ts.strftime("%Y-%m-%d")


def save_weekly_payload(
    raw_text: str,
    as_of: Optional[pd.Timestamp] = None,
    raw_dir: Path = RAW_DIR,
    retain: int = RETAIN,
) -> dict:
    """Persist `raw_text` to `raw_dir/ff_weekly_<YYYY-MM-DD>.json` — at most
    ONE file per calendar day. Returns a report dict with `status`:

      - "invalid"            — raw_text is empty or not a non-empty JSON
                               array; NOTHING is written (a failed/garbled
                               fetch never produces an empty or partial file)
      - "already_saved_today" — a snapshot for today's date already exists;
                               skipped (cadence gate, not a failure)
      - "unchanged"          — content is byte-identical to the most recent
                               existing snapshot; skipped (dedup — no point
                               committing the same schedule twice)
      - "saved"              — a new file was written; `rotated_out` lists
                               any files removed to stay within `retain`

    Never raises — a write/rotation error is the caller's problem to guard,
    same fail-open shape as the rest of this pipeline.
    """
    if not raw_text or not raw_text.strip():
        return {"status": "invalid", "reason": "empty payload"}
    try:
        parsed = json.loads(raw_text)
    except Exception as e:  # noqa: BLE001
        return {"status": "invalid", "reason": f"not valid JSON ({e})"}
    if not isinstance(parsed, list) or not parsed:
        return {"status": "invalid", "reason": "not a non-empty JSON array"}

    raw_dir.mkdir(parents=True, exist_ok=True)
    today_path = raw_dir / f"ff_weekly_{_today_str(as_of)}.json"

    if today_path.exists():
        return {"status": "already_saved_today", "path": str(today_path)}

    existing = sorted(raw_dir.glob("ff_weekly_*.json"))
    if existing:
        latest = existing[-1]
        try:
            if latest.read_text() == raw_text:
                return {"status": "unchanged", "path": str(latest)}
        except OSError:
            pass  # unreadable latest file -> fall through and save anyway

    today_path.write_text(raw_text)

    all_files = sorted(raw_dir.glob("ff_weekly_*.json"))
    rotated_out: list[str] = []
    while len(all_files) > retain:
        oldest = all_files.pop(0)
        oldest.unlink()
        rotated_out.append(oldest.name)

    return {"status": "saved", "path": str(today_path), "rotated_out": rotated_out}


def fetch_and_archive_weekly(
    url: str,
    *,
    now_utc: Optional[pd.Timestamp] = None,
    raw_dir: Path = RAW_DIR,
    retain: int = RETAIN,
) -> dict:
    """Fetch + archive in one call — the network boundary. Callers (see
    `src.ff_refresh.refresh()`) must wrap this in their own try/except; a
    fetch failure here raises, by design, so the caller's log message can
    say WHY nothing was archived this tick (mirrors the FRED cross-check
    block's own try/except shape one level up, not duplicated here)."""
    text = fetch_weekly_raw_text(url)
    return save_weekly_payload(text, as_of=now_utc, raw_dir=raw_dir, retain=retain)
