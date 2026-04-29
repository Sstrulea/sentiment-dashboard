"""Entry point: orchestrates COT (weekly) and sentiment (daily) pipelines.

Modes:
    weekly      — COT full refresh (default; preserved behavior)
    daily       — VIX rebuild + P/C append for today + retail snapshot + re-render
    retail      — Retail sentiment snapshot + re-render only (3-hourly cron)
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
    """Refresh VIX parquet, fill any missing recent P/C rows, re-render pages.

    Always attempts to backfill the last ~10 calendar days of business days so
    that transient gaps (yesterday's CBOE file that wasn't published yet at the
    previous run's cron time, holidays mis-skipped, earlier failures) get
    filled in on any later successful run.
    """
    import pandas as pd

    from .sentiment_backfill import is_us_market_holiday, trading_days
    from .sentiment_fetch import PC_FILE, append_pc_daily, build_sentiment_parquet
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
    start = today - dt.timedelta(days=10)

    existing_dates: set[dt.date] = set()
    if PC_FILE.exists():
        existing_dates = {
            pd.Timestamp(d).date() for d in pd.read_parquet(PC_FILE)["date"]
        }

    candidates = [d for d in trading_days(start, today) if d not in existing_dates]
    if not candidates:
        log.info("P/C parquet already has every trading day in [%s, %s].", start, today)
    else:
        log.info("Attempting P/C fetch for %d missing trading day(s): %s",
                 len(candidates), ", ".join(d.isoformat() for d in candidates))

    for d in candidates:
        if is_us_market_holiday(d):
            continue
        try:
            ok = append_pc_daily(d)
        except Exception as e:
            log.error("P/C append failed for %s: %s", d, e)
            return 2
        if ok:
            log.info("P/C row for %s appended", d)
        else:
            log.warning(
                "No P/C data for %s (not yet published or skipped). Will retry"
                " on next run.", d,
            )

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

    # Retail snapshot is best-effort — do not let Myfxbook outages block VIX/PC.
    rc = _retail()
    if rc != 0:
        log.warning("Retail step exited %d (non-fatal in --mode daily)", rc)

    return 0


def _retail() -> int:
    """Fetch + append a fresh retail sentiment snapshot, then re-render the page.

    Returns:
        0 on success or non-credential fetch error (admin can retry later).
        2 on `RetailAuthError` — credentials must be fixed before any retry.
    """
    log = logging.getLogger("retail")
    log.info("Retail sentiment refresh starting")

    from .retail_providers import RetailAuthError

    try:
        from .retail_fetch import snapshot_retail_sentiment
        n = snapshot_retail_sentiment()
        log.info("Retail snapshot complete: %d symbols", n)
    except RetailAuthError as e:
        log.error("Retail auth error (admin must fix credentials): %s", e)
        return 2
    except Exception as e:
        log.warning("Retail snapshot failed (non-fatal): %s", e)

    try:
        from .retail_render import render_retail_sentiment_page
        out = render_retail_sentiment_page()
        log.info("Retail page rendered → %s", out)
    except Exception as e:
        log.warning("Retail render failed: %s", e)

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
        choices=["weekly", "daily", "retail", "all", "backfill-pc"],
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
    if args.mode == "retail":
        return _retail()
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
