"""Fetch US 10y real yield (FRED DFII10) → data/real_yields.parquet.

Standalone cross-asset data layer, parallel to src/rate_fetch.py. Uses the
generic keyless FRED-series reader (NOT the currency rate adapter). Merges into a
deterministic, history-preserving parquet. NOT integrated into any scoring/UI —
that is 6.3b.

    python -m src.realyield_fetch            # fetch + write parquet, print report
    python -m src.realyield_fetch --scores   # also print the −2..+2 momentum score

NOTE: FRED is blocked in the CC sandbox; run from the residential terminal.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .rate_sources import FredSeriesSource
from .realyield_compute import compute_realyield_score

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
REAL_YIELDS_FILE = ROOT / "data" / "real_yields.parquet"
COLUMNS = ["date", "series", "yield_pct", "source"]

# Series to track (single US real yield for now; extensible).
SERIES = {"DFII10": "US 10y TIPS real yield"}


def fetch_series(series_id: str) -> Optional[pd.DataFrame]:
    """Fetch one FRED series → DataFrame(date, series, yield_pct, source), else None."""
    src = FredSeriesSource(series_id)
    raw = src.fetch_series()
    if raw is None:
        log.warning("%s UNRESOLVED — %s (%s)", series_id, src.last_status, src.last_note[:60])
        return None
    df = pd.DataFrame({
        "date": pd.to_datetime(raw["date"]),
        "series": series_id,
        "yield_pct": pd.to_numeric(raw["value"], errors="coerce"),
        "source": raw["source"],
    }, columns=COLUMNS)
    log.info("%s ← fred_series: n=%d latest=%s",
             series_id, len(df), df["date"].max().date() if len(df) else "—")
    return df


def _merge(new: pd.DataFrame) -> pd.DataFrame:
    if REAL_YIELDS_FILE.exists():
        existing = pd.read_parquet(REAL_YIELDS_FILE)
        existing["date"] = pd.to_datetime(existing["date"])
        combined = pd.concat([existing, new], ignore_index=True)
    else:
        combined = new
    combined = (
        combined.dropna(subset=["date", "series", "yield_pct"])
        .drop_duplicates(subset=["series", "date"], keep="last")
        .sort_values(["series", "date"])
        .reset_index(drop=True)
    )
    REAL_YIELDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(REAL_YIELDS_FILE, index=False)
    return combined


def update_real_yields(series_ids: Optional[list[str]] = None) -> pd.DataFrame:
    """Fetch each series and merge into the parquet (dedup last-write-wins on
    (series, date), history-preserving). A series that fails is logged + skipped."""
    ids = series_ids or list(SERIES)
    frames = [df for sid in ids if (df := fetch_series(sid)) is not None]
    if not frames:
        log.warning("No series resolved this run; parquet unchanged.")
        return pd.read_parquet(REAL_YIELDS_FILE) if REAL_YIELDS_FILE.exists() else pd.DataFrame(columns=COLUMNS)
    return _merge(pd.concat(frames, ignore_index=True))


def _print_report(df: pd.DataFrame, requested: list[str]) -> None:
    print(f"\nreal_yields.parquet: {len(df)} rows, {df['series'].nunique()} series "
          f"→ {REAL_YIELDS_FILE}")
    print(f"{'SERIES':<10}{'n':>7}  {'history':<26}{'latest':<12}{'status'}")
    today = date.today()
    for sid in requested:
        sub = df[df["series"] == sid]
        if sub.empty:
            print(f"{sid:<10}{'—':>7}  {'—':<26}{'—':<12}UNRESOLVED")
            continue
        sub = sub.sort_values("date")
        first = pd.Timestamp(sub["date"].iloc[0]).date()
        last = pd.Timestamp(sub["date"].iloc[-1]).date()
        lag = 0 if last >= today else int(np.busday_count(last, today))
        print(f"{sid:<10}{len(sub):>7}  {f'{first} → {last}':<26}{str(last):<12}lag={lag}bd")


def _print_scores(df: pd.DataFrame, requested: list[str]) -> None:
    print("\nREAL-YIELD MOMENTUM SCORES (ΔDFII10, raw: rising = +)")
    print(f"{'SERIES':<10}{'score':>6}  {'delta_w':>9}{'yield':>9}{'z':>8}  {'method':<12}{'stale'}")
    for sid in requested:
        s = compute_realyield_score(df, series=sid)
        if s is None:
            print(f"{sid:<10}{'—':>6}  {'—':>9}{'—':>9}{'—':>8}  {'no-data':<12}—")
            continue
        z = "—" if s.z is None else f"{s.z:+.2f}"
        dw = "—" if s.delta_w is None else f"{s.delta_w:+.3f}"
        yld = "—" if s.latest_yield is None else f"{s.latest_yield:.3f}"
        print(f"{sid:<10}{s.score:>+6}  {dw:>9}{yld:>9}{z:>8}  {s.method:<12}{s.stale}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch US real yields → data/real_yields.parquet")
    ap.add_argument("--series", action="append", choices=list(SERIES))
    ap.add_argument("--scores", action="store_true", help="also print the −2..+2 momentum score")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    requested = args.series or list(SERIES)
    df = update_real_yields(requested)
    _print_report(df, requested)
    if args.scores:
        _print_scores(df, requested)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
