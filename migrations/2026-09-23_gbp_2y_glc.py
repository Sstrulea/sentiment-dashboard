"""GBP 2y: drop the IUDSNPY (5y par) rows, backfill the BoE GLC nominal spot 2.0y
from 2016 (audit 2026-09-23, 1.4).

The pipeline runs this automatically (src.rate_fetch.update_rates ->
src.rate_migrations.ensure_all); this CLI is for measuring it on a copy.

Usage: .venv/bin/python migrations/2026-09-23_gbp_2y_glc.py [--rates PATH] [--dry-run]
Idempotent: a no-op once no GBP 'boe' row is left and the 'boe_glc' series
starts in January 2016.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.rate_migrations import RATES_FILE, migrate_gbp_2y_glc  # noqa: E402
from src.rate_sources import BoeSource  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rates", type=Path, default=RATES_FILE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    df = pd.read_parquet(args.rates)
    out, rep = migrate_gbp_2y_glc(df, BoeSource().fetch_history)
    rep["rows_total_before"], rep["rows_total_after"] = len(df), len(out)
    print(json.dumps(rep, indent=1))
    if rep["status"] == "migrated" and not args.dry_run:
        out.to_parquet(args.rates, index=False)
        print(f"written {args.rates}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
