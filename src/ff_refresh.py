"""PHASE 3 — production FF calendar refresh (fetch weekly → merge historical parquet).

Fetches the keyless FF weekly JSON each economic-cron tick and merges new/revised prints
into data/economic_calendar_ff.parquet (canonical schema), history-preserving. Carries the
same anti-degradation contract as the MT5 ingest: a failed/empty/thin/bad-schema payload
never degrades the parquet — the last-good file is kept and the run logs + quarantines.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from .econ_calendar_ff import CANON_COLUMNS, parse_ff_weekly, unmapped_summary

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
QUARANTINE_PARQUET = ROOT / "data" / "ff_quarantine.parquet"
PIPELINE_YAML = ROOT / "config" / "pipeline.yaml"


def load_pipeline_config(path: Path = PIPELINE_YAML) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def calendar_source(cfg: Optional[dict] = None) -> str:
    """'ff' (default) or 'mt5' (rollback)."""
    cfg = cfg or load_pipeline_config()
    return str(cfg.get("calendar_source", "ff")).strip().lower()


def _sanity_ok(df: pd.DataFrame, min_ccy: int) -> bool:
    """A good weekly payload maps events for >= min_ccy distinct currencies."""
    return (not df.empty) and (df["currency"].nunique() >= min_ccy)


def merge_weekly(existing: Optional[pd.DataFrame], weekly: pd.DataFrame) -> pd.DataFrame:
    """History-preserving, FIELD-AWARE merge: append new prints, update revised ones.

    Dedup key = (canonical_id, datetime_utc), last-write-wins per row for the
    schedule fields (forecast/previous/names) — BUT an existing non-null `actual`
    is NEVER overwritten by a null/NaN re-delivery. The hybrid flow requires
    this: the daily JBlanked pull writes actuals in the evening; next-day hourly
    faireconomy ticks re-deliver the same events actual-less (the weekly feed is
    structurally actual-less), and whole-row keep="last" would null the actual
    right back out. A non-null incoming actual (revision) still wins."""
    if existing is None or existing.empty:
        combined = weekly.copy()
    else:
        existing = existing.copy()
        existing["datetime_utc"] = pd.to_datetime(existing["datetime_utc"])
        combined = pd.concat([existing, weekly], ignore_index=True)
    combined["datetime_utc"] = pd.to_datetime(combined["datetime_utc"])
    # Field-aware actual: within a key group (concat order = existing first,
    # incoming last) carry the last non-null actual forward, so the kept (last)
    # row inherits it unless the incoming row brings its own non-null actual.
    combined["actual"] = combined.groupby(["canonical_id", "datetime_utc"],
                                          sort=False)["actual"].ffill()
    combined = (combined.sort_values(["canonical_id", "datetime_utc"])
                .drop_duplicates(["canonical_id", "datetime_utc"], keep="last")
                .sort_values(["currency", "canonical_id", "datetime_utc"])
                .reset_index(drop=True))
    return combined[CANON_COLUMNS]


def refresh(*, now_utc: Optional[pd.Timestamp] = None, cfg: Optional[dict] = None,
            parquet_path: Path = FF_PARQUET) -> dict:
    """Fetch the weekly feed, merge, write. Returns a report dict. Never raises on a
    fetch/parse failure — anti-degradation keeps the last-good parquet."""
    cfg = cfg or load_pipeline_config()
    url = cfg.get("ff_weekly_url", "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    min_ccy = int(cfg.get("ff_min_currencies", 4))
    existing = pd.read_parquet(parquet_path) if parquet_path.exists() else None
    n_before = 0 if existing is None else len(existing)

    # --- fetch + parse (graceful) ---
    try:
        weekly = parse_ff_weekly(url, now_utc=now_utc)
    except Exception as e:  # noqa: BLE001 — fetch/HTTP/schema failure
        log.warning("FF weekly fetch/parse FAILED (%s); keeping last-good parquet (%d rows).",
                    str(e)[:100], n_before)
        return {"status": "fetch_failed", "rows_before": n_before, "rows_after": n_before, "merged": 0}

    # --- anti-degradation: empty / thin payload → quarantine, keep last-good ---
    if weekly.empty:
        log.warning("FF weekly payload mapped 0 events; QUARANTINE — keeping last-good (%d rows).", n_before)
        return {"status": "empty", "rows_before": n_before, "rows_after": n_before, "merged": 0}
    if not _sanity_ok(weekly, min_ccy):
        log.warning("FF weekly covers only %d currency(ies) (< %d); QUARANTINE — keeping last-good.",
                    weekly["currency"].nunique(), min_ccy)
        return {"status": "thin", "rows_before": n_before, "rows_after": n_before, "merged": 0}

    merged = merge_weekly(existing, weekly)
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(parquet_path, index=False)

    # FRED cross-check on US actuals (retroactive, fail-open) → quarantine file.
    if cfg.get("run_fred_crosscheck", True):
        try:
            from .ff_fred_crosscheck import crosscheck_us
            q = crosscheck_us(merged, now_utc=now_utc)
            q.to_parquet(QUARANTINE_PARQUET, index=False)
            if len(q):
                log.warning("FRED cross-check quarantined %d US print(s).", len(q))
        except Exception as e:  # noqa: BLE001 — advisory; never break the refresh
            log.warning("FRED cross-check skipped (%s).", str(e)[:80])

    # ingest report: aggregated unmapped summary (makes a future alias gap visible)
    try:
        um = unmapped_summary(url)
        top = "; ".join(f"{r.currency}/{r['count']}×{r.name_raw}" for _, r in um.head(8).iterrows())
    except Exception:  # noqa: BLE001
        top = "(unavailable)"
    log.info("FF refresh: weekly=%d rows, %d ccy; parquet %d -> %d rows. Top unmapped: %s",
             len(weekly), weekly["currency"].nunique(), n_before, len(merged), top or "none")
    return {"status": "ok", "rows_before": n_before, "rows_after": len(merged),
            "merged": len(merged) - n_before, "weekly_rows": len(weekly)}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    rep = refresh()
    print(f"FF refresh: {rep['status']} — parquet {rep['rows_before']} -> {rep['rows_after']} rows")
    return 0 if rep["status"] == "ok" else 0  # never non-zero (graceful)


if __name__ == "__main__":
    import sys
    sys.exit(main())
