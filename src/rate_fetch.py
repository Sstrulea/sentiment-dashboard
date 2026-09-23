"""Fetch validated 2y yields → data/rates.parquet (standalone rate engine).

Per currency, runs the keyless adapters in preference order and takes the first
YieldSeries that qualifies (2y-ish, daily, fresh, ≥2y history). Merges into a
deterministic, history-preserving parquet. NOT integrated into the economic
composite/index or UI — that is a later step.

    python -m src.rate_fetch              # fetch + write parquet, print report
    python -m src.rate_fetch --scores     # also print RateScore per currency
    python -m src.rate_fetch --currency USD --currency EUR
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

from .rate_compute import compute_rate_scores
from .rate_migrations import ensure_all as ensure_migrations
from .rate_sources import (
    ALL_SOURCES,
    CURRENCIES,
    SOURCE_PREFERENCE,
    YieldSeries,
    assess,
)

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
RATES_FILE = ROOT / "data" / "rates.parquet"
COLUMNS = ["currency", "date", "tenor", "yield_pct", "source"]


def _with_stored_history(series: YieldSeries, stored: Optional[pd.DataFrame]) -> YieldSeries:
    """Incremental source (serves only a recent window, e.g. BoE GLC current month):
    assess it together with the rows the parquet already holds for the SAME
    currency and source. The fetched points win a shared date."""
    if stored is None or stored.empty:
        return series
    sub = stored[(stored["currency"] == series.currency) & (stored["source"] == series.source)]
    if sub.empty:
        return series
    pts = {pd.Timestamp(d).date(): float(v) for d, v in zip(sub["date"], sub["yield_pct"])}
    pts.update(dict(series.points))
    return YieldSeries(series.currency, series.source, series.tenor, series.frequency,
                       sorted(pts.items()), series.note + f" +{len(sub)} stored")


def fetch_currency(currency: str, today: Optional[date] = None,
                   stored: Optional[pd.DataFrame] = None) -> Optional[YieldSeries]:
    """Try adapters in preference order; return the first qualifying series, else None.
    `stored` (the current parquet) is only read for incremental sources."""
    today = today or date.today()
    pref = SOURCE_PREFERENCE.get(currency, [])
    last_reasons: list[str] = []
    for name in pref:
        src = ALL_SOURCES.get(name)
        if src is None or not src.supports(currency):
            continue
        series = src.fetch(currency)
        if series is None:
            last_reasons.append(f"{name}:{src.last_status or 'fail'}({src.last_note[:40]})")
            continue
        if getattr(src, "incremental", False):
            series = _with_stored_history(series, stored)
        a = assess(series, today)
        if a.qualifies:
            lag = series.business_days_lag(today)
            log.info("%s ← %s: %s %s n=%d latest=%s lag=%sbd [%s]",
                     currency, name, series.tenor, series.frequency,
                     series.n_points, series.latest_date, lag, a.status)
            return series
        last_reasons.append(f"{name}:{a.status}({','.join(a.flags)})")
    log.warning("%s UNRESOLVED — %s", currency, "; ".join(last_reasons) or "no source")
    return None


def _series_to_rows(s: YieldSeries) -> pd.DataFrame:
    df = pd.DataFrame(
        {"currency": s.currency, "date": [d for d, _ in s.points],
         "tenor": s.tenor, "yield_pct": [v for _, v in s.points], "source": s.source},
        columns=COLUMNS,
    )
    df["date"] = pd.to_datetime(df["date"])
    return df


def _merge(new: pd.DataFrame) -> pd.DataFrame:
    if RATES_FILE.exists():
        existing = pd.read_parquet(RATES_FILE)
        existing["date"] = pd.to_datetime(existing["date"])
        combined = pd.concat([existing, new], ignore_index=True)
    else:
        combined = new
    combined = (
        combined.dropna(subset=["currency", "date", "yield_pct"])
        .drop_duplicates(subset=["currency", "date"], keep="last")
        .sort_values(["currency", "date"])
        .reset_index(drop=True)
    )
    RATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(RATES_FILE, index=False)
    return combined


def update_rates(currencies: Optional[list[str]] = None) -> pd.DataFrame:
    """Fetch each currency's best qualifying 2y series and merge into the parquet.

    History-preserving: re-runs merge into prior rows (dedup last-write-wins on
    (currency, date)). A currency whose sources all fail is logged + skipped.
    Returns the full merged frame.
    """
    today = date.today()
    ccys = currencies or CURRENCIES
    ok_to_write = ensure_migrations(RATES_FILE)
    stored = pd.read_parquet(RATES_FILE) if RATES_FILE.exists() else None
    frames: list[pd.DataFrame] = []
    for ccy in ccys:
        if not ok_to_write.get(ccy, True):
            log.error("%s skipped: pending rates migration (see src.rate_migrations).", ccy)
            continue
        s = fetch_currency(ccy, today, stored)
        if s is not None:
            frames.append(_series_to_rows(s))
    if not frames:
        log.warning("No currencies resolved this run; parquet unchanged.")
        return pd.read_parquet(RATES_FILE) if RATES_FILE.exists() else pd.DataFrame(columns=COLUMNS)
    return _merge(pd.concat(frames, ignore_index=True))


def _print_report(df: pd.DataFrame, requested: list[str]) -> None:
    print(f"\nrates.parquet: {len(df)} rows, {df['currency'].nunique()} currencies "
          f"→ {RATES_FILE}")
    print(f"{'CCY':<5}{'source':<13}{'tenor':<6}{'n':>6}  {'latest':<12}{'status'}")
    today = date.today()
    for ccy in requested:
        sub = df[df["currency"] == ccy]
        if sub.empty:
            print(f"{ccy:<5}{'—':<13}{'—':<6}{'—':>6}  {'—':<12}UNRESOLVED")
            continue
        sub = sub.sort_values("date")
        last = pd.Timestamp(sub["date"].iloc[-1]).date()
        lag = 0 if last >= today else int(__import__("numpy").busday_count(last, today))
        src = str(sub["source"].iloc[-1])
        tenor = str(sub["tenor"].iloc[-1])
        print(f"{ccy:<5}{src:<13}{tenor:<6}{len(sub):>6}  {str(last):<12}lag={lag}bd")


def _print_scores(df: pd.DataFrame) -> None:
    scores = compute_rate_scores(df)
    print("\nRATE SCORES (Δ2y repricing momentum)")
    print(f"{'CCY':<5}{'score':>6}  {'delta_w':>9}{'yield':>9}{'z':>8}  {'method':<12}{'stale'}")
    for ccy in sorted(scores):
        s = scores[ccy]
        z = "—" if s.z is None else f"{s.z:+.2f}"
        dw = "—" if s.delta_w is None else f"{s.delta_w:+.3f}"
        yld = "—" if s.latest_yield is None else f"{s.latest_yield:.3f}"
        print(f"{ccy:<5}{s.rate_score:>+6}  {dw:>9}{yld:>9}{z:>8}  {s.method:<12}{s.stale}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch 2y yields → data/rates.parquet")
    ap.add_argument("--currency", action="append", choices=CURRENCIES)
    ap.add_argument("--scores", action="store_true", help="also print RateScore per currency")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    requested = args.currency or CURRENCIES
    df = update_rates(requested)
    _print_report(df, requested)
    if args.scores:
        _print_scores(df)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
