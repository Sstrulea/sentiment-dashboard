"""measure/coverage-asymmetry — build the daily historical panel of
(as_of, currency, category, score_precise, coverage) from the full FF
scoring frame, using TODAY's fixed config (see reconstruct.py docstring
for why: isolates the N-effect from taxonomy/alias changes over time).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.measure.reconstruct import (  # noqa: E402
    build_full_scoring_frame, scorecards_at, load_yaml,
    INDICATORS_YAML, INSTRUMENTS_YAML, OUR_CCYS, CALC_CATEGORIES,
)

OUT_CSV = ROOT / "docs" / "coverage-asymmetry-history.csv"


def main():
    ind = load_yaml(INDICATORS_YAML)
    inst = load_yaml(INSTRUMENTS_YAML)
    full_cal = build_full_scoring_frame()
    start = full_cal["release_dt"].min().normalize()
    end = full_cal["release_dt"].max().normalize()
    print(f"Building daily panel {start.date()} .. {end.date()} ...")

    rows = []
    for d in pd.date_range(start, end, freq="D"):
        as_of = d + pd.Timedelta(hours=23, minutes=59)
        cards = scorecards_at(full_cal, as_of, ind, inst)
        for ccy in OUR_CCYS:
            for cat in CALC_CATEGORIES:
                cell = cards[ccy][cat]
                rows.append({
                    "date": d.date().isoformat(), "currency": ccy, "category": cat,
                    "score_precise": cell["score_precise"], "coverage": cell["coverage"],
                })

    df = pd.DataFrame(rows)
    df.to_csv(OUT_CSV, index=False)
    print(f"Wrote {len(df)} rows -> {OUT_CSV}")
    print(df.groupby(["currency", "category"])["coverage"].describe()[["min", "max", "mean"]])


if __name__ == "__main__":
    main()
