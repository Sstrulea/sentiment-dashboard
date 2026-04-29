"""CLI entry point for Retail Sentiment ops & diagnostics.

Usage:
    python -m src.retail_backfill --check
    python -m src.retail_backfill --snapshot-now
    python -m src.retail_backfill --render
    python -m src.retail_backfill --summary
"""
from __future__ import annotations

import argparse
import logging
import sys

import pandas as pd

from src.retail_fetch import (
    RETAIL_GENERAL_FILE,
    RETAIL_HISTORY_FILE,
    snapshot_retail_sentiment,
)
from src.retail_providers import MyfxbookProvider, RetailAuthError

log = logging.getLogger(__name__)


def _cmd_check() -> int:
    print("Probing Myfxbook auth…")
    try:
        provider = MyfxbookProvider()
    except RetailAuthError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 2

    ok = provider.health_check()
    if ok:
        print("OK: Myfxbook login + logout succeeded.")
        return 0
    print("FAIL: Myfxbook login returned an error.", file=sys.stderr)
    return 2


def _cmd_snapshot_now() -> int:
    try:
        n = snapshot_retail_sentiment()
    except RetailAuthError as e:
        print(f"AUTH ERROR: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"FETCH ERROR: {e}", file=sys.stderr)
        return 3
    print(f"Snapshotted {n} symbol(s).")
    return 0


def _cmd_render() -> int:
    # Late import so --check doesn't drag in jinja unnecessarily.
    from src.retail_render import render_retail_sentiment_page

    out = render_retail_sentiment_page()
    print(f"Rendered: {out}")
    return 0


def _cmd_summary() -> int:
    print("=" * 72)
    print("retail_history.parquet")
    print("=" * 72)
    if not RETAIL_HISTORY_FILE.exists():
        print("  (does not exist)")
    else:
        df = pd.read_parquet(RETAIL_HISTORY_FILE)
        print(f"  rows:        {len(df)}")
        if not df.empty:
            df["fetched_at"] = pd.to_datetime(df["fetched_at"])
            print(f"  date range:  {df['fetched_at'].min()} → {df['fetched_at'].max()}")
            print(f"  symbols:     {df['symbol'].nunique()} unique")
            print(f"  per symbol:  {df.groupby('symbol').size().describe().to_dict()}")
            print("  symbol list:", ", ".join(sorted(df["symbol"].unique().tolist())))

    print()
    print("=" * 72)
    print("retail_general.parquet")
    print("=" * 72)
    if not RETAIL_GENERAL_FILE.exists():
        print("  (does not exist)")
    else:
        df = pd.read_parquet(RETAIL_GENERAL_FILE)
        print(f"  rows:        {len(df)}")
        if not df.empty:
            df["fetched_at"] = pd.to_datetime(df["fetched_at"])
            print(f"  date range:  {df['fetched_at'].min()} → {df['fetched_at'].max()}")
            last = df.sort_values("fetched_at").iloc[-1]
            print(f"  latest snapshot:")
            for col in (
                "real_account_pct", "demo_account_pct", "profitable_pct",
                "nonprofitable_pct", "total_funds_usd", "average_deposit_usd",
            ):
                print(f"    {col}: {last.get(col)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Retail sentiment ops CLI")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="Probe Myfxbook auth")
    g.add_argument("--snapshot-now", action="store_true", help="Fetch + append a single snapshot")
    g.add_argument("--render", action="store_true", help="Re-render HTML/JSON without fetching")
    g.add_argument("--summary", action="store_true", help="Print parquet stats")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.check:
        return _cmd_check()
    if args.snapshot_now:
        return _cmd_snapshot_now()
    if args.render:
        return _cmd_render()
    if args.summary:
        return _cmd_summary()
    p.error("no command selected")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
