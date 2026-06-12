"""Fetch US 10y real yield → data/real_yields.parquet (cross-asset data layer).

Two keyless sources, stored as SEPARATE series (never mixed — methodology
differs, so mixing would create momentum artifacts):
  - FRED DFII10 (10y TIPS real yield)        → series "DFII10"
  - US Treasury real yield curve, 10 YR col  → series "REAL_10Y_TSY"

The scoring layer (realyield_compute.select_series) picks the preferred AVAILABLE
series (DFII10 if fresh, else REAL_10Y_TSY) and scores momentum on that ONE
series. History-preserving, deterministic merge. NOT wired into scoring/UI here.

    python -m src.realyield_fetch            # fetch + write parquet, print report
    python -m src.realyield_fetch --scores   # also print the −2..+2 momentum score
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .rate_sources import FredSeriesSource, TreasuryRealYieldSource
from .realyield_compute import PREFERRED_SERIES, compute_realyield_score

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
REAL_YIELDS_FILE = ROOT / "data" / "real_yields.parquet"
COLUMNS = ["date", "series", "yield_pct", "source"]

# series_name -> human label
SERIES = {
    "DFII10": "US 10y TIPS real yield (FRED)",
    "REAL_10Y_TSY": "US 10y real yield (Treasury curve)",
}
# Years of Treasury history to pull (current + 2 prior → ≥3y for the 252+21 window).
TSY_YEARS_BACK = 2


def _rows(dates, series: str, vals, source: str) -> pd.DataFrame:
    df = pd.DataFrame({
        "date": pd.to_datetime(dates),
        "series": series,
        "yield_pct": pd.to_numeric(vals, errors="coerce"),
        "source": source,
    }, columns=COLUMNS)
    return df


def fetch_dfii10() -> Optional[pd.DataFrame]:
    """FRED DFII10 → DataFrame(date, series='DFII10', yield_pct, source), else None."""
    src = FredSeriesSource("DFII10")
    raw = src.fetch_series()
    if raw is None:
        log.warning("DFII10 UNRESOLVED — %s (%s)", src.last_status, src.last_note[:60])
        return None
    df = _rows(raw["date"], "DFII10", raw["value"], "fred")
    log.info("DFII10 ← fred: n=%d latest=%s", len(df), df["date"].max().date() if len(df) else "—")
    return df


def fetch_treasury_real_10y(today: Optional[date] = None) -> Optional[pd.DataFrame]:
    """US Treasury 10y real yield → DataFrame(series='REAL_10Y_TSY'), else None."""
    today = today or date.today()
    years = list(range(today.year - TSY_YEARS_BACK, today.year + 1))
    src = TreasuryRealYieldSource()
    raw = src.fetch_real_10y(years)
    if raw is None:
        log.warning("REAL_10Y_TSY UNRESOLVED — %s (%s)", src.last_status, src.last_note[:80])
        return None
    df = _rows(raw["date"], "REAL_10Y_TSY", raw["value"], "treasury")
    log.info("REAL_10Y_TSY ← treasury: n=%d years=%s latest=%s",
             len(df), years, df["date"].max().date() if len(df) else "—")
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


def update_real_yields() -> pd.DataFrame:
    """Fetch both real-yield sources (DFII10 + Treasury) and merge as separate
    series. Either source failing is logged + skipped; the other still lands."""
    today = date.today()
    frames = [f for f in (fetch_dfii10(), fetch_treasury_real_10y(today)) if f is not None]
    if not frames:
        log.warning("No real-yield source resolved this run; parquet unchanged.")
        return pd.read_parquet(REAL_YIELDS_FILE) if REAL_YIELDS_FILE.exists() else pd.DataFrame(columns=COLUMNS)
    return _merge(pd.concat(frames, ignore_index=True))


def _print_report(df: pd.DataFrame) -> None:
    print(f"\nreal_yields.parquet: {len(df)} rows, {df['series'].nunique()} series "
          f"→ {REAL_YIELDS_FILE}")
    print(f"{'SERIES':<14}{'n':>7}  {'history':<26}{'latest':<12}{'value':>8}{'lag':>8}")
    today = date.today()
    for sid in SERIES:
        sub = df[df["series"] == sid]
        if sub.empty:
            print(f"{sid:<14}{'—':>7}  {'—':<26}{'—':<12}{'—':>8}{'—':>8}")
            continue
        sub = sub.sort_values("date")
        first = pd.Timestamp(sub["date"].iloc[0]).date()
        last = pd.Timestamp(sub["date"].iloc[-1]).date()
        val = float(sub["yield_pct"].iloc[-1])
        lag = 0 if last >= today else int(np.busday_count(last, today))
        print(f"{sid:<14}{len(sub):>7}  {f'{first} → {last}':<26}{str(last):<12}{val:>8.2f}{str(lag)+'bd':>8}")


def _print_scores(df: pd.DataFrame) -> None:
    print("\nREAL-YIELD MOMENTUM (single chosen series; raw: rising = +)")
    chosen = compute_realyield_score(df)  # auto-select preferred available series
    print(f"  preference: {PREFERRED_SERIES}")
    if chosen is None:
        print("  CHOSEN: none — no usable series.")
        return
    z = "—" if chosen.z is None else f"{chosen.z:+.2f}"
    dw = "—" if chosen.delta_w is None else f"{chosen.delta_w:+.3f}"
    yld = "—" if chosen.latest_yield is None else f"{chosen.latest_yield:.3f}"
    print(f"  CHOSEN: {chosen.series}  score={chosen.score:+d}  delta_w={dw}  "
          f"yield={yld}  z={z}  method={chosen.method}  stale={chosen.stale}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch US real yields → data/real_yields.parquet")
    ap.add_argument("--scores", action="store_true", help="also print the −2..+2 momentum score")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    df = update_real_yields()
    _print_report(df)
    if args.scores:
        _print_scores(df)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
