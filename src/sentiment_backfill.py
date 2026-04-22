"""CLI entry point for VIX and Put/Call data acquisition.

Usage:
    python -m src.sentiment_backfill --check
    python -m src.sentiment_backfill --probe-historical
    python -m src.sentiment_backfill --vix
    python -m src.sentiment_backfill --pc-today
    python -m src.sentiment_backfill --pc-from 2024-01-01 --pc-to 2024-03-31
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time

import pandas as pd

from src.sentiment_fetch import (
    PC_COLUMNS,
    PC_FILE,
    append_pc_daily,
    build_sentiment_parquet,
    check_pc_backfill_availability,
    fetch_pc_daily,
)

# Pre-2019 probe schedule (deep history). Ordered newest → oldest.
HISTORICAL_PROBE_DATES = [
    dt.date(2018, 6, 15),
    dt.date(2017, 3, 10),
    dt.date(2016, 9, 20),
    dt.date(2015, 1, 15),
    dt.date(2014, 5, 22),
    dt.date(2013, 8, 14),
    dt.date(2012, 11, 7),
    dt.date(2011, 4, 18),
    dt.date(2010, 10, 25),
    dt.date(2009, 12, 1),
    dt.date(2008, 9, 15),  # Lehman
    dt.date(2007, 2, 27),  # flash crash precursor
    dt.date(2005, 7, 7),
    dt.date(2003, 10, 21),
]

PC_VARIANT_KEYS = ("total", "equity", "index", "spx_spxw", "vix")

# Rate limiting
REQUEST_INTERVAL_SEC = 0.20  # 200ms → 5 req/s max
RETRY_SLEEP_SEC = 30.0
RETRY_THRESHOLD = 3  # pause + retry after this many consecutive failures
ABORT_THRESHOLD = 10  # abort after this many consecutive failures
PROGRESS_EVERY = 50

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Date parsing, holiday calendar
# ---------------------------------------------------------------------------

def _parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def _easter_sunday(year: int) -> dt.date:
    # Anonymous Gregorian algorithm (Meeus/Jones/Butcher).
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> dt.date:
    """Return the date of the n-th weekday (Mon=0 ... Sun=6) of the month."""
    d = dt.date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=offset + 7 * (n - 1))


def _last_weekday_of_month(year: int, month: int, weekday: int) -> dt.date:
    if month == 12:
        d = dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
    else:
        d = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    while d.weekday() != weekday:
        d -= dt.timedelta(days=1)
    return d


def _observed(d: dt.date) -> dt.date:
    """NYSE-style observed date: Sat → Fri before, Sun → Mon after."""
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


def _us_market_holidays(year: int) -> set[dt.date]:
    """Approximate NYSE holiday calendar (full-day closures)."""
    holidays: set[dt.date] = set()
    holidays.add(_observed(dt.date(year, 1, 1)))  # New Year's Day
    holidays.add(_nth_weekday_of_month(year, 1, 0, 3))  # MLK (3rd Mon Jan), since 1998
    holidays.add(_nth_weekday_of_month(year, 2, 0, 3))  # Presidents (3rd Mon Feb)
    holidays.add(_easter_sunday(year) - dt.timedelta(days=2))  # Good Friday
    holidays.add(_last_weekday_of_month(year, 5, 0))  # Memorial Day (last Mon May)
    if year >= 2021:
        holidays.add(_observed(dt.date(year, 6, 19)))  # Juneteenth
    holidays.add(_observed(dt.date(year, 7, 4)))  # Independence Day
    holidays.add(_nth_weekday_of_month(year, 9, 0, 1))  # Labor Day (1st Mon Sep)
    holidays.add(_nth_weekday_of_month(year, 11, 3, 4))  # Thanksgiving (4th Thu Nov)
    holidays.add(_observed(dt.date(year, 12, 25)))  # Christmas
    return holidays


_HOLIDAY_CACHE: dict[int, set[dt.date]] = {}


def is_us_market_holiday(d: dt.date) -> bool:
    year = d.year
    if year not in _HOLIDAY_CACHE:
        _HOLIDAY_CACHE[year] = _us_market_holidays(year)
    return d in _HOLIDAY_CACHE[year]


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """US trading days in [start, end] — business days minus NYSE holidays."""
    out: list[dt.date] = []
    for ts in pd.bdate_range(start=start, end=end):
        d = ts.date()
        if is_us_market_holiday(d):
            continue
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def _cmd_check() -> int:
    report = check_pc_backfill_availability()
    print("=" * 72)
    print("CBOE Put/Call backfill probe")
    print("=" * 72)
    for r in report["results"]:
        sample = r["sample"]
        if sample:
            sample_str = " ".join(
                f"{k}={v}" if v is not None else f"{k}=NA"
                for k, v in sample.items()
            )
        else:
            sample_str = r.get("detail") or ""
        print(f"  {r['date']:>10}  {r['status']:<8}  {sample_str}")
    print("-" * 72)
    print(f"supports_backfill: {report['supports_backfill']}")
    print(f"recommendation:    {report['recommendation']}")
    return 0


def _cmd_probe_historical() -> int:
    """Probe deep-history dates to discover how far back CBOE serves data."""
    import requests

    from src.sentiment_fetch import HTTP_TIMEOUT, PC_URL_TEMPLATE

    print("=" * 86)
    print("DEEP historical probe — pre-2019 coverage")
    print("=" * 86)
    header = f"{'DATE':<12} {'STATUS':<9} {'TOTAL':>6} {'INDEX':>6} {'EQUITY':>7} {'SPX_SPXW':>8} {'VIX':>6}"
    print(header)
    print("-" * len(header))

    successes: list[tuple[dt.date, dict]] = []

    def _fmt(v):
        return f"{v:.2f}" if isinstance(v, (int, float)) else "-"

    for d in HISTORICAL_PROBE_DATES:
        url = PC_URL_TEMPLATE.format(date=d.strftime("%Y-%m-%d"))
        try:
            r = requests.get(url, timeout=HTTP_TIMEOUT)
            status_code = r.status_code
        except Exception as e:
            status_code = None
            log.warning("Network error probing %s: %s", d, e)

        time.sleep(REQUEST_INTERVAL_SEC)

        if status_code == 200:
            row = fetch_pc_daily(d)
            time.sleep(REQUEST_INTERVAL_SEC)
            if row is None:
                print(
                    f"{d.isoformat():<12} {'parse_err':<9} "
                    f"{'-':>6} {'-':>6} {'-':>7} {'-':>8} {'-':>6}"
                )
                continue
            print(
                f"{d.isoformat():<12} {'success':<9} "
                f"{_fmt(row.get('total')):>6} "
                f"{_fmt(row.get('index')):>6} "
                f"{_fmt(row.get('equity')):>7} "
                f"{_fmt(row.get('spx_spxw')):>8} "
                f"{_fmt(row.get('vix')):>6}"
            )
            successes.append((d, row))
        else:
            status_label = str(status_code) if status_code is not None else "error"
            print(
                f"{d.isoformat():<12} {status_label:<9} "
                f"{'-':>6} {'-':>6} {'-':>7} {'-':>8} {'-':>6}"
            )

    print("-" * len(header))
    if not successes:
        print("No historical dates succeeded. Earliest available is whatever the")
        print("regular --check probe confirmed (>= 2019-12-02).")
        return 0

    all_five = [d for d, row in successes if all(row.get(k) is not None for k in PC_VARIANT_KEYS)]
    with_vix = [d for d, row in successes if row.get("vix") is not None]

    earliest = min(d for d, _ in successes)
    print(f"Earliest confirmed date:           {earliest.isoformat()}")
    if all_five:
        print(f"Dates with all 5 variants:         {min(all_five).isoformat()} onwards")
    else:
        print("Dates with all 5 variants:         (none — some variant missing on every probe)")
    if with_vix:
        print(f"Dates with VIX P/C available:      {min(with_vix).isoformat()} onwards")
    else:
        print("Dates with VIX P/C available:      (none)")
    return 0


def _cmd_vix() -> int:
    df = build_sentiment_parquet()
    print(f"Wrote {len(df)} rows to data/sentiment_history.parquet")
    if not df.empty:
        print(f"  date range:  {df['date'].min().date()} → {df['date'].max().date()}")
        first = df.iloc[0]
        last = df.iloc[-1]
        print(
            f"  first row:   date={first['date'].date()} "
            f"vix={first['vix_close']:.4f} vix3m={first['vix3m_close']:.4f} "
            f"ratio={first['ratio']:.4f}"
        )
        print(
            f"  last row:    date={last['date'].date()} "
            f"vix={last['vix_close']:.4f} vix3m={last['vix3m_close']:.4f} "
            f"ratio={last['ratio']:.4f}"
        )
    return 0


def _cmd_pc_today() -> int:
    today = dt.date.today()
    row = fetch_pc_daily(today)
    if row is None:
        print(f"{today}: NO DATA")
        return 1
    print(
        f"{today}: "
        + " ".join(
            f"{k}={row[k]}" if row[k] is not None else f"{k}=NA"
            for k in PC_VARIANT_KEYS
        )
    )
    ok = append_pc_daily(today)
    if not ok:
        return 1
    return 0


def _load_existing_pc_dates() -> set[dt.date]:
    if not PC_FILE.exists():
        return set()
    existing = pd.read_parquet(PC_FILE)
    if existing.empty:
        return set()
    return {d.date() if isinstance(d, (pd.Timestamp, dt.datetime)) else d
            for d in pd.to_datetime(existing["date"])}


def _flush_rows(rows: list[dict]) -> None:
    if not rows:
        return
    new_df = pd.DataFrame(rows, columns=PC_COLUMNS)
    new_df["date"] = pd.to_datetime(new_df["date"])
    if PC_FILE.exists():
        existing = pd.read_parquet(PC_FILE)
        existing["date"] = pd.to_datetime(existing["date"])
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = (
        combined.drop_duplicates(subset=["date"], keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    PC_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(PC_FILE, index=False)


def _cmd_pc_range(pc_from: dt.date, pc_to: dt.date) -> int:
    if pc_from > pc_to:
        print(f"error: --pc-from ({pc_from}) is after --pc-to ({pc_to})", file=sys.stderr)
        return 2

    already_have = _load_existing_pc_dates()
    all_days = trading_days(pc_from, pc_to)
    todo = [d for d in all_days if d not in already_have]
    total = len(all_days)
    skipped = total - len(todo)

    print(
        f"Backfill plan: {total} trading days in [{pc_from}, {pc_to}], "
        f"{skipped} already present, {len(todo)} to fetch."
    )
    if not todo:
        print("Nothing to do.")
        return 0

    rows: list[dict] = []
    consecutive_failures = 0
    processed = 0
    n_ok = 0
    n_miss = 0

    for d in todo:
        row = fetch_pc_daily(d)
        time.sleep(REQUEST_INTERVAL_SEC)
        processed += 1

        if row is None:
            n_miss += 1
            consecutive_failures += 1
            log.warning("No data for %s (consecutive failures: %d)", d, consecutive_failures)
            if consecutive_failures >= ABORT_THRESHOLD:
                log.error(
                    "%d consecutive failures — aborting. Last tried: %s",
                    consecutive_failures, d,
                )
                _flush_rows(rows)
                print(
                    f"ABORT after {consecutive_failures} consecutive failures at {d}. "
                    f"Processed {processed}/{len(todo)}; {n_ok} written, {n_miss} missing."
                )
                return 3
            if consecutive_failures >= RETRY_THRESHOLD and consecutive_failures % RETRY_THRESHOLD == 0:
                log.warning(
                    "Pausing %.0fs after %d consecutive failures…",
                    RETRY_SLEEP_SEC, consecutive_failures,
                )
                time.sleep(RETRY_SLEEP_SEC)
        else:
            consecutive_failures = 0
            rows.append(row)
            n_ok += 1

        if processed % PROGRESS_EVERY == 0:
            pct = 100.0 * processed / len(todo)
            print(
                f"Processed {processed}/{len(todo)} ({pct:.0f}%), current date: {d}, "
                f"ok={n_ok}, miss={n_miss}"
            )
            _flush_rows(rows)
            rows = []

    _flush_rows(rows)
    print(f"Done. {n_ok} day(s) written, {n_miss} day(s) missing (out of {len(todo)} attempted).")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="VIX and Put/Call data acquisition CLI")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="Probe backfill availability")
    g.add_argument(
        "--probe-historical",
        action="store_true",
        help="Probe deep-history dates (pre-2019) to find earliest available",
    )
    g.add_argument("--vix", action="store_true", help="Build full VIX/VIX3M parquet")
    g.add_argument("--pc-today", action="store_true", help="Fetch today's P/C ratios")
    g.add_argument(
        "--pc-from",
        type=_parse_date,
        metavar="YYYY-MM-DD",
        help="Start of P/C backfill range (requires --pc-to)",
    )
    p.add_argument(
        "--pc-to",
        type=_parse_date,
        metavar="YYYY-MM-DD",
        default=None,
        help="End of P/C backfill range (inclusive)",
    )
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.check:
        return _cmd_check()
    if args.probe_historical:
        return _cmd_probe_historical()
    if args.vix:
        return _cmd_vix()
    if args.pc_today:
        return _cmd_pc_today()
    if args.pc_from is not None:
        if args.pc_to is None:
            p.error("--pc-from requires --pc-to")
        return _cmd_pc_range(args.pc_from, args.pc_to)
    p.error("no command selected")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
