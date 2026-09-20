"""python -m src.cb_compute --report [--asof YYYY-MM-DD] [--data-dir DIR] [--points N] [--currency USD ...]"""
from __future__ import annotations

import argparse
import sys
from datetime import date

from ..cb_loader import load_context, load_pairs
from . import report as rp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.cb_compute", description=__doc__)
    ap.add_argument("--report", action="store_true", help="print the per-bank tables and the pairs table")
    ap.add_argument("--asof", type=date.fromisoformat, default=None, help="as-of date (default: the newest market snapshot)")
    ap.add_argument("--data-dir", default=None, help="data/cb directory (default: the repo's)")
    ap.add_argument("--points", type=int, default=8, help="trajectory points shown per bank")
    ap.add_argument("--currency", action="append", help="limit the bank blocks to these currencies (the pairs table needs all)")
    a = ap.parse_args(argv)
    if not a.report:
        ap.print_help()
        return 2
    ctx = load_context(a.data_dir)
    asof = a.asof or ctx.market.newest()
    if asof is None:
        ap.error("no market quotes on record")
    reports, pairs = rp.build(ctx, asof, load_pairs())
    if a.currency:
        reports = {c: r for c, r in reports.items() if c in a.currency}
    sys.stdout.write(rp.render(reports, pairs, asof, a.points))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
