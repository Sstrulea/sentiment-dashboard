"""Entry point: orchestrates COT (weekly) and sentiment (daily) pipelines.

Modes:
    weekly      — COT full refresh (default; preserved behavior)
    daily       — VIX rebuild + P/C append for today + re-render sentiment pages
    all         — weekly then daily
    backfill-pc — P/C backfill from 2019-10-07 through today, then re-render
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys


def _weekly() -> int:
    """Run the existing COT pipeline: fetch CFTC → compute → render dashboard."""
    from .compute import build_latest_snapshot
    from .fetch import update_history
    from .render import render_dashboard
    from .static_assets import copy_static_assets

    log = logging.getLogger("weekly")
    log.info("COT weekly refresh starting")

    df = update_history()
    if df.empty:
        log.error("No data returned from CFTC API.")
        return 2

    snapshot = build_latest_snapshot(df)
    if snapshot.empty:
        log.error("Snapshot is empty — check contracts.yaml codes vs API output.")
        return 2

    out = render_dashboard(snapshot, df)
    copy_static_assets()
    log.info("Wrote COT dashboard to %s", out)
    return 0


def _daily() -> int:
    """Refresh VIX parquet, append today's P/C row, re-render sentiment pages."""
    from .sentiment_fetch import append_pc_daily, build_sentiment_parquet
    from .sentiment_render import render_pc_ratio_page, render_vix_ratio_page

    log = logging.getLogger("daily")
    log.info("Sentiment daily refresh starting")

    try:
        vix_df = build_sentiment_parquet()
        log.info("VIX parquet rebuilt (%d rows, latest %s)",
                 len(vix_df),
                 vix_df["date"].max().date() if len(vix_df) else "—")
    except Exception as e:
        log.error("VIX refresh failed: %s", e)
        return 2

    today = dt.date.today()
    try:
        appended = append_pc_daily(today)
        if appended:
            log.info("P/C row for %s appended", today)
        else:
            log.warning(
                "No P/C data for %s (weekend / holiday / not yet published). Pages will"
                " reflect the most recent available row.", today,
            )
    except Exception as e:
        log.error("P/C append failed for %s: %s", today, e)
        return 2

    try:
        pc_out = render_pc_ratio_page()
        log.info("P/C page rendered → %s", pc_out)
    except Exception as e:
        log.error("P/C render failed: %s", e)
        return 2

    try:
        vix_out = render_vix_ratio_page()
        log.info("VIX page rendered → %s", vix_out)
    except Exception as e:
        log.error("VIX render failed: %s", e)
        return 2

    return 0


def _backfill_pc() -> int:
    """Full P/C backfill from 2019-10-07 through today."""
    from .sentiment_backfill import _cmd_pc_range  # noqa: PLC2701 (internal but intentional)
    from .sentiment_render import render_pc_ratio_page, render_vix_ratio_page

    log = logging.getLogger("backfill-pc")
    start = dt.date(2019, 10, 7)
    end = dt.date.today()
    log.info("Running P/C backfill from %s to %s", start, end)

    rc = _cmd_pc_range(start, end)
    if rc != 0:
        log.error("P/C backfill exited with code %d", rc)
        return rc

    render_pc_ratio_page()
    render_vix_ratio_page()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dashboard orchestrator")
    parser.add_argument(
        "--mode",
        choices=["weekly", "daily", "all", "backfill-pc"],
        default="weekly",
        help="which pipeline to run (default: weekly)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    if args.mode == "weekly":
        return _weekly()
    if args.mode == "daily":
        return _daily()
    if args.mode == "all":
        rc = _weekly()
        if rc != 0:
            return rc
        return _daily()
    if args.mode == "backfill-pc":
        return _backfill_pc()

    parser.error(f"Unknown mode: {args.mode}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
