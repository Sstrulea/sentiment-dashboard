"""CORRECTION overrides — a documented official value beats the feed (2026-10).

The Manual Actuals Panel's MISSING / ZERO_CONFIRM entries only fill a gap: they
retire as soon as a real non-zero actual lands (manual_actuals.apply_overrides),
so they cannot fix a WRONG non-zero print. On 2026-09-30 JBlanked delivered
Personal Spending's 0.9 as USD Core PCE m/m (official BEA: 0.2), and every
JB re-delivery wrote it back into the parquet over a hand fix.

A CORRECTION entry in data/manual_actuals_overrides.json:
  * applies whatever the row's current actual is (NaN, 0.0 or non-zero) and
    beats every feed value, JB re-deliveries included — it is applied when the
    FF parquet is READ, never written into it, so no ingest can undo it;
  * must carry an http(s) URL of the official source in `note`; an entry
    without one is ignored (WARNING) — the API refuses to write it.

`read_ff_parquet` is the ONE place corrections are applied; every consumer of
the parquet (scoring + Currency Strength via economic_render._load_calendar_frame,
the manual panel, the integrity report, /history, cb_datasets / cb_probe) reads
through it, so a print has one value everywhere. Writers (ff_refresh,
jb_actuals, archive_backfill, ff_provenance) keep reading the raw file.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
OVERRIDES_JSON = ROOT / "data" / "manual_actuals_overrides.json"

CORRECTION = "CORRECTION"
_SOURCE_URL = re.compile(r"https?://\S+")


def has_source_url(note) -> bool:
    return bool(_SOURCE_URL.search(str(note or "")))


def _load(path: Path) -> list[dict]:
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001 — no file / unreadable → no corrections (fail-open)
        return []


def apply_corrections(ff: pd.DataFrame, overrides: list[dict]) -> pd.DataFrame:
    """Pure — returns `ff` with every valid CORRECTION's actual set on its
    (canonical_id, datetime_utc) row; later entries win. Entries without a
    source URL, or without a matching row, are ignored with a WARNING."""
    entries = [e for e in overrides or [] if e.get("state_resolved") == CORRECTION]
    if not entries or ff is None or ff.empty or "actual" not in ff.columns:
        return ff
    ff = ff.copy()
    dts = pd.to_datetime(ff["datetime_utc"])
    for e in entries:
        cid, dt = e.get("canonical_id"), e.get("datetime_utc")
        if not has_source_url(e.get("note")):
            log.warning("CORRECTION %s @ %s ignored: note has no http(s) source URL.", cid, dt)
            continue
        try:
            mask = (ff["canonical_id"] == cid) & (dts == pd.Timestamp(dt))
            actual = float(e["actual"])
        except (KeyError, TypeError, ValueError):
            log.warning("CORRECTION %s @ %s ignored: malformed entry.", cid, dt)
            continue
        if not mask.any():
            log.warning("CORRECTION %s @ %s ignored: no such row in the FF parquet.", cid, dt)
            continue
        ff.loc[mask, "actual"] = actual
    return ff


def read_ff_parquet(path: Optional[Path] = None, *, overrides_path: Optional[Path] = None,
                    **read_kwargs) -> pd.DataFrame:
    """pd.read_parquet(FF parquet) with CORRECTION overrides applied — the read
    every consumer of the calendar parquet goes through."""
    ff = pd.read_parquet(path or FF_PARQUET, **read_kwargs)
    return apply_corrections(ff, _load(overrides_path or OVERRIDES_JSON))
