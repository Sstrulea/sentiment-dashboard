#!/usr/bin/env python3
"""FAZA 1E — deterministic before/after snapshot for the GBP gdp_qoq
frequency_override fix.

Pinned `as_of` (passed on the command line, IDENTICAL for both runs) so the
only variable between the "before" and "after" snapshot is the config edit
itself — no clock drift, no risk of a print crossing a staleness boundary
between the two invocations. Uses the real production aggregation
(`economic_compute.build_payload`, `_rate_entry_for`, `compute_rate_scores`)
exactly as `economic_render.build_economic_payload()` does, minus
sentiment/trend (orthogonal to this config — a currency's growth/inflation/
labor/rates category scores and its monetary category don't depend on COT
or trend cells at all, so omitting them only removes noise, not signal).

Read-only: writes only its own JSON snapshot file, never touches data/ or config/.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import _rate_entry_for, build_payload  # noqa: E402
from src.economic_render import RATES_PARQUET, _load_calendar_frame  # noqa: E402
from src.rate_compute import compute_rate_scores  # noqa: E402

INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"


def snapshot(as_of: pd.Timestamp) -> dict:
    indicators_cfg = yaml.safe_load(INDICATORS_YAML.read_text())
    instruments_cfg = yaml.safe_load(INSTRUMENTS_YAML.read_text())

    cal = _load_calendar_frame(as_of)

    rate_scores = {}
    if RATES_PARQUET.exists():
        rates_df = pd.read_parquet(RATES_PARQUET)
        rate_scores = compute_rate_scores(rates_df, as_of=as_of.date())

    payload = build_payload(cal, indicators_cfg, instruments_cfg, as_of=as_of,
                            rate_scores=rate_scores or None,
                            sentiment_cells=None, trend_cells=None)

    out = {"as_of": payload["as_of"], "currencies": {}}
    for ccy, card in payload["currencies"].items():
        out["currencies"][ccy] = {
            "index": card.get("index"),
            "categories": {
                cat: {"score_cell": c.get("score_cell"), "score_precise": c.get("score_precise"),
                     "coverage": c.get("coverage")}
                for cat, c in (card.get("categories") or {}).items()
            },
            "growth_gdp_qoq_breakdown": (card.get("breakdown") or {}).get("gdp_qoq"),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True, help="Fixed ISO timestamp, identical for before/after runs")
    ap.add_argument("--out", required=True, help="Output JSON path")
    args = ap.parse_args()

    as_of = pd.Timestamp(args.as_of)
    snap = snapshot(as_of)
    Path(args.out).write_text(json.dumps(snap, indent=2, sort_keys=True, default=str))
    print(f"Wrote {args.out} (as_of={snap['as_of']}, {len(snap['currencies'])} currencies)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
