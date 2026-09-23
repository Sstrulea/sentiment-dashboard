"""Provenance backfill for data/economic_calendar_ff.parquet (audit 2026-09-23, 2.1/2.3).

New rows get forecast_origin / jb_status at ingest (econ_calendar_ff parsers +
ff_refresh.merge_weekly). Rows already in the parquet are backfilled once:

  forecast_origin
    datetime >= FF_ARCHIVE_START and the event is in an archived FF weekly feed
    (data/ff_raw, newest feed wins): "ff" with the FF value, or "ff_blank" with
    forecast NaN when FF printed "". Otherwise "jb" (value unchanged).
    data/ff_forecast_provenance.json overrides a row with documented evidence
    (e.g. an FF page read by hand before the FF archive existed).
  jb_status
    the newest JBlanked payload (data/archive/ff_calendar_range.json, then every
    data/jb_raw payload in time order) carrying the event: matched on the exact
    datetime first (per event), else on the UTC date with the copy carrying a real
    actual winning (as jb_actuals.clean_jblanked_actuals does). None when no
    payload carried it.

The computed table is committed as data/ff_provenance_backfill.csv (built with
the full git history of data/ff_raw and data/jb_raw, which a CI checkout does not
have) and applied idempotently by ensure_provenance().
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
FF_ARCHIVE_START = pd.Timestamp("2026-08-01")
BACKFILL_CSV = ROOT / "data" / "ff_provenance_backfill.csv"
OVERRIDES_JSON = ROOT / "data" / "ff_forecast_provenance.json"
BACKFILL_COLUMNS = ["canonical_id", "datetime_utc", "forecast_origin", "forecast", "jb_status"]


def _ff_forecasts(weekly: Iterable[tuple[str, list]]) -> dict:
    """{(canonical_id, datetime_utc): (origin, forecast)} — newest feed wins; a
    same-date fallback key (canonical_id, date) is kept alongside."""
    out: dict = {}
    for tag, events in sorted(weekly, key=lambda x: x[0]):
        df = parse_ff_weekly(events, now_utc=pd.Timestamp(tag[:10]) + pd.Timedelta(days=1))
        for r in df.itertuples(index=False):
            v = (r.forecast_origin, r.forecast)
            out[(r.canonical_id, pd.Timestamp(r.datetime_utc))] = v
            out[(r.canonical_id, pd.Timestamp(r.datetime_utc).date())] = v
    return out


def _jb_statuses(payloads: Iterable[tuple[str, list]]) -> tuple[dict, dict]:
    """(exact, by_date), newest payload wins in both:
      exact    {(canonical_id, datetime_utc): jb_status} — per EVENT. The archive
               rows in the parquet were never re-aligned, so their datetime is the
               JB one: a DST duplicate's 0.0 copy keeps its own "Data Not Loaded"
               and does not inherit its real sibling's label.
      by_date  {(canonical_id, date): jb_status} — for rows whose datetime was
               re-aligned to the FF schedule (clean_jblanked_actuals): the copy
               with a real actual wins, as at ingest."""
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


def build_backfill(parquet: pd.DataFrame, weekly: Iterable[tuple[str, list]],
                   jb_payloads: Iterable[tuple[str, list]],
                   overrides: Optional[list[dict]] = None) -> pd.DataFrame:
    ff = _ff_forecasts(weekly)
    jb_exact, jb_by_date = _jb_statuses(jb_payloads)
    ov = {(o["canonical_id"], pd.Timestamp(o["date"]).date()): o for o in (overrides or [])}
    recs = []
    for r in parquet.itertuples(index=False):
        dt = pd.Timestamp(r.datetime_utc)
        origin, forecast = "jb", r.forecast
        if dt >= FF_ARCHIVE_START:
            hit = ff.get((r.canonical_id, dt)) or ff.get((r.canonical_id, dt.date()))
            if hit is not None:
                origin, forecast = hit
        o = ov.get((r.canonical_id, dt.date()))
        if o is not None:
            origin, forecast = o["forecast_origin"], o["forecast"]
        recs.append({"canonical_id": r.canonical_id, "datetime_utc": dt,
                     "forecast_origin": origin, "forecast": forecast,
                     "jb_status": jb_exact.get((r.canonical_id, dt),
                                               jb_by_date.get((r.canonical_id, dt.date())))})
    return pd.DataFrame(recs, columns=BACKFILL_COLUMNS)


def apply_backfill(parquet: pd.DataFrame, backfill: pd.DataFrame) -> pd.DataFrame:
    """Fill the provenance columns (and the FF-sourced forecast) for rows that do
    not have a forecast_origin yet. Idempotent: rows already carrying one — set at
    ingest or by an earlier run — are left alone."""
    df = ensure_provenance_columns(parquet.copy())
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    b = backfill.copy()
    b["datetime_utc"] = pd.to_datetime(b["datetime_utc"])
    b = b.drop_duplicates(["canonical_id", "datetime_utc"], keep="last").set_index(
        ["canonical_id", "datetime_utc"])
    todo = df["forecast_origin"].isna()
    for i in df.index[todo]:
        k = (df.at[i, "canonical_id"], df.at[i, "datetime_utc"])
        if k in b.index:
            row = b.loc[k]
            df.at[i, "forecast_origin"] = row["forecast_origin"]
            df.at[i, "forecast"] = row["forecast"]
            if pd.isna(df.at[i, "jb_status"]) and pd.notna(row["jb_status"]):
                df.at[i, "jb_status"] = row["jb_status"]
        else:
            df.at[i, "forecast_origin"] = "jb"      # pre-2.1 row the table never saw
    return df


def load_backfill(path: Path = BACKFILL_CSV) -> pd.DataFrame:
    b = pd.read_csv(path, keep_default_na=True)
    b["jb_status"] = b["jb_status"].where(b["jb_status"].notna(), None)
    return b


def load_overrides(path: Path = OVERRIDES_JSON) -> list[dict]:
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001
        return []


def ensure_provenance(parquet_path: Path, backfill_path: Path = BACKFILL_CSV) -> bool:
    """Pipeline hook: backfill once if the parquet predates 2.1. Returns True if
    the parquet was rewritten."""
    df = pd.read_parquet(parquet_path)
    if "forecast_origin" in df.columns and df["forecast_origin"].notna().all():
        return False
    out = apply_backfill(df, load_backfill(backfill_path))
    out.to_parquet(parquet_path, index=False)
    log.warning("ff provenance backfill: %s", out["forecast_origin"].value_counts().to_dict())
    return True
