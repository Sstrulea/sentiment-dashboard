"""MEASUREMENT INSTRUMENT — not production code. Read-only.

Row-level inventory of "no consensus" rows across ALL 83 (currency, indicator)
series fed through the SAME production bridge the live dashboard uses
(`src.ff_scoring.to_scoring_frame`, which applies the zero-placeholder
quarantine exactly as build_payload sees it).

A row counts as "no consensus" when actual is valid (non-NaN) but consensus is
NaN after quarantine — i.e. exactly the condition compute_indicator_score
flags `no_consensus` for the LATEST row of a series, generalized to every row
so we can see rows buried inside otherwise-healthy series.

Usage: .venv/bin/python3 scripts/measure/inventory_no_consensus.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ff_scoring import build_matcher, to_scoring_frame  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"


def main() -> None:
    ffdf = pd.read_parquet(FF_PARQUET)
    ffdf["datetime_utc"] = pd.to_datetime(ffdf["datetime_utc"])
    cal = to_scoring_frame(ffdf, build_matcher())
    cal["release_dt"] = pd.to_datetime(cal["release_dt"])

    valid_actual = cal["actual"].notna()
    no_cons = valid_actual & cal["consensus"].isna()

    cal = cal.assign(no_cons=no_cons, year=cal["release_dt"].dt.year)

    print("=== Per-series totals (actual valid) — all 83 series ===")
    grp = cal[valid_actual].groupby(["currency", "indicator_key"])
    per_series = grp.agg(
        total_rows=("actual", "size"),
        no_consensus_rows=("no_cons", "sum"),
    ).reset_index()
    per_series["pct_no_consensus"] = (
        per_series["no_consensus_rows"] / per_series["total_rows"] * 100
    ).round(1)
    per_series = per_series.sort_values(
        ["pct_no_consensus", "currency", "indicator_key"], ascending=[False, True, True]
    )
    with pd.option_context("display.max_rows", None, "display.width", 140):
        print(per_series.to_string(index=False))

    print()
    print("=== Series that are OTHERWISE HEALTHY (>50% consensus coverage) "
          "but have >=1 scattered no-consensus row ===")
    scattered = per_series[
        (per_series["no_consensus_rows"] > 0)
        & (per_series["pct_no_consensus"] < 50.0)
    ]
    if scattered.empty:
        print("NONE FOUND.")
    else:
        print(scattered.to_string(index=False))

    print()
    print("=== Row-level detail for scattered cases, by year ===")
    if not scattered.empty:
        keys = set(zip(scattered["currency"], scattered["indicator_key"]))
        detail = cal[valid_actual & cal.apply(
            lambda r: (r["currency"], r["indicator_key"]) in keys, axis=1
        )]
        by_year = detail.groupby(["currency", "indicator_key", "year"]).agg(
            total=("actual", "size"), no_cons=("no_cons", "sum")
        ).reset_index()
        print(by_year.to_string(index=False))

    print()
    print("=== Structural (0% or ~100% consensus) series, by year, for the 6 flagged ===")
    flagged = [
        ("CAD", "core_cpi"), ("NZD", "manufacturing_pmi"), ("NZD", "services_pmi"),
        ("CAD", "manufacturing_pmi"), ("AUD", "manufacturing_pmi"), ("AUD", "services_pmi"),
    ]
    for ccy, key in flagged:
        sub = cal[valid_actual & (cal["currency"] == ccy) & (cal["indicator_key"] == key)]
        by_year = sub.groupby("year").agg(
            total=("actual", "size"), no_cons=("no_cons", "sum")
        ).reset_index()
        total = len(sub)
        withcons = int((~sub["no_cons"]).sum())
        print(f"-- {ccy} {key}: total={total} with_consensus={withcons}")
        print(by_year.to_string(index=False))
        print()

    print("=== Full per-(ccy,year) no-consensus row count, ALL series (for the doc's "
          "distribution table) ===")
    by_ccy_year = cal[valid_actual].groupby(["currency", "year"]).agg(
        total=("actual", "size"), no_cons=("no_cons", "sum")
    ).reset_index()
    print(by_ccy_year.to_string(index=False))


if __name__ == "__main__":
    main()
