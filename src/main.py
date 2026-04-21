"""Entry point: refresh data, compute metrics, render dashboard."""
from __future__ import annotations

import logging
import sys

from .compute import build_latest_snapshot
from .fetch import update_history
from .render import render_dashboard


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    df = update_history()
    if df.empty:
        logging.error("No data returned from CFTC API.")
        return 2
    snapshot = build_latest_snapshot(df)
    if snapshot.empty:
        logging.error("Snapshot is empty — check that contracts.yaml codes match API output.")
        return 2
    out = render_dashboard(snapshot, df)
    logging.info("Wrote dashboard to %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
