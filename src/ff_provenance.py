"""Provenance migration for data/economic_calendar_ff.parquet (audit 2026-09-23, 2.1/Z5).

New rows get forecast_origin / jb_status at ingest (econ_calendar_ff parsers +
ff_refresh.merge_weekly). ensure_provenance() backfills the rows already in the
parquet ONCE, idempotently, from what is on disk at migration time — no git
history, no committed table:

  forecast_origin
    the event is in an FF weekly snapshot under data/ff_raw (newest wins):
    "ff" with the FF value, or "ff_blank" with forecast NaN when FF printed "".
    data/ff_forecast_provenance.json overrides a row with documented evidence
    (an FF page read by hand before the archive existed). Otherwise "unknown",
    treated like "jb" by ff_scoring.effective_consensus.
  jb_status
    the newest JBlanked payload on disk (data/archive/ff_calendar_range.json,
    then data/jb_raw in time order) carrying the event: exact datetime first
    (per event: a DST duplicate's 0.0 copy keeps its own "Data Not Loaded"),
    else the same UTC date with the copy carrying a real actual. None if none.

RETENTION DEADLINE: data/ff_raw keeps 90 snapshots; those from 2026-08-01 start
leaving it at the end of October 2026 — the migration must have run by then, or
the rows they cover fall back to "unknown".
Called by ff_refresh.refresh() and jb_actuals.pull_actuals() before they merge.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from .econ_calendar_ff import (ensure_provenance_columns, parse_ff_weekly,
                               parse_jblanked_range)

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
FF_RAW_DIR = ROOT / "data" / "ff_raw"
JB_RAW_DIR = ROOT / "data" / "jb_raw"
JB_ARCHIVE = ROOT / "data" / "archive" / "ff_calendar_range.json"
OVERRIDES_JSON = ROOT / "data" / "ff_forecast_provenance.json"
UNKNOWN = "unknown"


def _ff_forecasts(weekly: Iterable[tuple[str, list]]) -> dict:
    """{(canonical_id, datetime_utc) | (canonical_id, date): (origin, forecast)},
    newest feed wins."""
    out: dict = {}
    for tag, events in sorted(weekly, key=lambda x: x[0]):
        df = parse_ff_weekly(events, now_utc=pd.Timestamp(tag[:10]) + pd.Timedelta(days=1))
        for r in df.itertuples(index=False):
            v = (r.forecast_origin, r.forecast)
            out[(r.canonical_id, pd.Timestamp(r.datetime_utc))] = v
            out[(r.canonical_id, pd.Timestamp(r.datetime_utc).date())] = v
    return out


def _jb_statuses(payloads: Iterable[tuple[str, list]]) -> tuple[dict, dict]:
    exact: dict = {}
    by_date: dict = {}
    for tag, events in sorted(payloads, key=lambda x: x[0]):
        df = parse_jblanked_range(events, now_utc=pd.Timestamp("2100-01-01"))
        if df.empty:
            continue
        df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
        df["_date"] = df["datetime_utc"].dt.date
        for r in df.itertuples(index=False):
            exact[(r.canonical_id, r.datetime_utc)] = r.jb_status
        for (cid, d), g in df.groupby(["canonical_id", "_date"], sort=False):
            real = g[g["actual"].notna() & (g["actual"] != 0.0)]
            pick = (real if len(real) else g).sort_values("datetime_utc").iloc[-1]
            by_date[(cid, d)] = pick["jb_status"]
    return exact, by_date


def backfill(parquet: pd.DataFrame, weekly: Iterable[tuple[str, list]],
             jb_payloads: Iterable[tuple[str, list]],
             overrides: Optional[list[dict]] = None) -> pd.DataFrame:
    """Fill forecast_origin (+ the FF forecast) and jb_status for rows without a
    forecast_origin. Pure; idempotent (rows that have one are left alone)."""
    df = ensure_provenance_columns(parquet.copy())
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    df["forecast_origin"] = df["forecast_origin"].astype(object)
    df["jb_status"] = df["jb_status"].astype(object)
    todo = df["forecast_origin"].isna()
    if not todo.any():
        return df
    ff = _ff_forecasts(weekly)
    jb_exact, jb_by_date = _jb_statuses(jb_payloads)
    ov = {(o["canonical_id"], pd.Timestamp(o["date"]).date()): o for o in (overrides or [])}
    for i in df.index[todo]:
        cid, dt = df.at[i, "canonical_id"], df.at[i, "datetime_utc"]
        hit = ff.get((cid, dt)) or ff.get((cid, dt.date()))
        origin, forecast = (hit if hit is not None else (UNKNOWN, df.at[i, "forecast"]))
        o = ov.get((cid, dt.date()))
        if o is not None:
            origin, forecast = o["forecast_origin"], o["forecast"]
        df.at[i, "forecast_origin"] = origin
        df.at[i, "forecast"] = forecast
        if pd.isna(df.at[i, "jb_status"]):
            df.at[i, "jb_status"] = jb_exact.get((cid, dt), jb_by_date.get((cid, dt.date())))
    return df


def _disk_payloads(folder: Path, prefix: str) -> list[tuple[str, list]]:
    out = []
    for p in sorted(Path(folder).glob(f"{prefix}*.json")):
        try:
            out.append((p.stem[len(prefix):], json.loads(p.read_text())))
        except (OSError, json.JSONDecodeError):
            log.warning("provenance: unreadable %s skipped", p.name)
    return out


def load_overrides(path: Path = OVERRIDES_JSON) -> list[dict]:
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001
        return []


def ensure_provenance(parquet_path: Path, ff_raw_dir: Path = FF_RAW_DIR,
                      jb_raw_dir: Path = JB_RAW_DIR, archive: Path = JB_ARCHIVE) -> bool:
    """Pipeline hook (idempotent). Returns True if the parquet was rewritten."""
    parquet_path = Path(parquet_path)
    if not parquet_path.exists():
        return False
    df = pd.read_parquet(parquet_path)
    if "forecast_origin" in df.columns and df["forecast_origin"].notna().all():
        return False
    jb = [("0000-archive", json.loads(Path(archive).read_text()))] if Path(archive).exists() else []
    jb += _disk_payloads(jb_raw_dir, "jb_range_")
    out = backfill(df, _disk_payloads(ff_raw_dir, "ff_weekly_"), jb, load_overrides())
    out.to_parquet(parquet_path, index=False)
    log.warning("ff provenance migration: forecast_origin %s; jb_status %s",
                out["forecast_origin"].value_counts().to_dict(),
                out["jb_status"].value_counts(dropna=False).to_dict())
    return True
