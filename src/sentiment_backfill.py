"""CLI entry point for VIX and Put/Call data acquisition.

Usage:
    python -m src.sentiment_backfill --check
    python -m src.sentiment_backfill --vix
    python -m src.sentiment_backfill --pc-today
    python -m src.sentiment_backfill --pc-from 2024-01-01 --pc-to 2024-03-31
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

import pandas as pd

from src.sentiment_fetch import (
    PC_COLUMNS,
    PC_FILE,
    append_pc_daily,
    build_sentiment_parquet,
    check_pc_backfill_availability,
    fetch_pc_daily,
)


def _parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


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
            for k in ("total", "equity", "index", "spx_spxw", "vix")
        )
    )
    ok = append_pc_daily(today)
    if not ok:
        return 1
    return 0


def _cmd_pc_range(pc_from: dt.date, pc_to: dt.date) -> int:
    if pc_from > pc_to:
        print(f"error: --pc-from ({pc_from}) is after --pc-to ({pc_to})", file=sys.stderr)
        return 2

    rows: list[dict] = []
    n_ok = 0
    n_miss = 0
    for ts in pd.bdate_range(start=pc_from, end=pc_to):
        d = ts.date()
        row = fetch_pc_daily(d)
        if row is None:
            print(f"{d}: NO DATA")
            n_miss += 1
            continue
        rows.append(row)
        n_ok += 1
        print(
            f"{d}: "
            + " ".join(
                f"{k}={row[k]}" if row[k] is not None else f"{k}=NA"
                for k in ("total", "equity", "index", "spx_spxw", "vix")
            )
        )

    if rows:
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

    print(f"Done. {n_ok} day(s) written, {n_miss} day(s) missing.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="VIX and Put/Call data acquisition CLI")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="Probe backfill availability")
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
