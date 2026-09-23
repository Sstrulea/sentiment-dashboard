"""Idempotent data migrations over data/rates.parquet, run by the pipeline
(src.rate_fetch.update_rates calls ensure_all() before any fetch).

Never commit a migrated parquet from a branch: the hourly econ-refresh owns
data/rates.parquet. After the merge, the first pipeline run migrates it; every
later run is a cheap no-op.

gbp_2y_glc (audit 2026-09-23, 1.4)
  The GBP rows written by source 'boe' are IUDSNPY = 5-year nominal PAR yield,
  not a 2y. Drop them and backfill the 2y from the BoE GLC nominal spot curve
  (maturity 2.0) from GBP_BACKFILL_SINCE, so the z baseline (252 W-changes) is
  covered. Done iff no GBP 'boe' row is left AND the 'boe_glc' series starts on
  or before GBP_DONE_IF_START_BEFORE; otherwise it (re)runs. A failed download
  leaves the parquet untouched and reports not-done, so the caller must not
  write GBP that run (a fresh current-month GLC month on top of the 5y history
  would be a mixed series).
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
RATES_FILE = ROOT / "data" / "rates.parquet"

LEGACY_GBP_SOURCE = "boe"
GBP_SOURCE = "boe_glc"
GBP_BACKFILL_SINCE = date(2016, 1, 1)
GBP_DONE_IF_START_BEFORE = date(2016, 1, 31)


def _counts(df: pd.DataFrame, currency: str) -> dict:
    sub = df[df["currency"] == currency]
    out = {}
    for src, g in sub.groupby("source"):
        d = pd.to_datetime(g["date"])
        out[str(src)] = {"n": int(len(g)), "first": str(d.min().date()),
                         "last": str(d.max().date())}
    return out


def gbp_2y_glc_done(df: pd.DataFrame) -> bool:
    gbp = df[df["currency"] == "GBP"]
    if (gbp["source"] == LEGACY_GBP_SOURCE).any():
        return False
    glc = gbp[gbp["source"] == GBP_SOURCE]
    if glc.empty:
        return False
    return pd.to_datetime(glc["date"]).min().date() <= GBP_DONE_IF_START_BEFORE


def migrate_gbp_2y_glc(df: pd.DataFrame,
                       history: Callable[[date], list[tuple[date, float]]]
                       ) -> tuple[pd.DataFrame, dict]:
    """Pure over `df` (history is injected). Returns (new_df, report)."""
    before = _counts(df, "GBP")
    if gbp_2y_glc_done(df):
        return df, {"migration": "gbp_2y_glc", "status": "noop", "before": before,
                    "after": before}
    pts = history(GBP_BACKFILL_SINCE)
    if not pts:
        raise RuntimeError("GLC history returned no 2.0y points")
    rest = df[~((df["currency"] == "GBP") & (df["source"] == LEGACY_GBP_SOURCE))]
    new = pd.DataFrame({"currency": "GBP", "date": pd.to_datetime([d for d, _ in pts]),
                        "tenor": "2y", "yield_pct": [v for _, v in pts],
                        "source": GBP_SOURCE})
    rest = rest.assign(date=pd.to_datetime(rest["date"]))
    # history first, then rows already in the parquet: a current-month row the
    # daily fetch wrote wins its date (keep="last").
    out = (pd.concat([new, rest], ignore_index=True)
           .drop_duplicates(subset=["currency", "date"], keep="last")
           .sort_values(["currency", "date"]).reset_index(drop=True))
    return out, {"migration": "gbp_2y_glc", "status": "migrated", "before": before,
                 "after": _counts(out, "GBP"),
                 "dropped_legacy_rows": int(len(df) - len(rest)),
                 "backfilled_points": len(pts)}


def ensure_all(rates_file: Path = RATES_FILE,
               history: Optional[Callable[[date], list[tuple[date, float]]]] = None
               ) -> dict[str, bool]:
    """Run every pending migration in place. Returns {currency: ok_to_write};
    a currency mapped to False must be skipped by the caller this run."""
    if not rates_file.exists():
        return {"GBP": True}   # fresh build: nothing legacy to mix with
    df = pd.read_parquet(rates_file)
    if gbp_2y_glc_done(df):
        return {"GBP": True}
    if history is None:
        from .rate_sources import BoeSource
        history = BoeSource().fetch_history
    try:
        out, rep = migrate_gbp_2y_glc(df, history)
    except Exception as e:  # noqa: BLE001 — leave the parquet untouched
        log.error("rates migration gbp_2y_glc FAILED (%s); GBP not written this run.", e)
        return {"GBP": False}
    out.to_parquet(rates_file, index=False)
    log.warning("rates migration gbp_2y_glc: %s", rep)
    return {"GBP": True}
