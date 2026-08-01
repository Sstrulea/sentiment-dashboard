"""eval/bucket-c-candidates — summarize the counterfactual panel: per
candidate, frequency of category-score-precise moves >=0.25 and pair-bias
flips, plus the full magnitude distribution. Ordered by FREQUENCY, not
FAZA-1 merit rank.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]


def main():
    cat = pd.read_csv(ROOT / "docs" / "bucket-c-panel-categories.csv")
    inst = pd.read_csv(ROOT / "docs" / "bucket-c-panel-instruments.csv")

    total_days = cat["date"].nunique()
    print(f"Panel: {total_days} distinct evaluation days, "
          f"{cat['date'].min()} .. {cat['date'].max()}")
    print()

    rows = []
    for candidate, g in cat.groupby("candidate"):
        n_total = len(g)
        n_contrib = int(g["contributed"].sum())
        active = g[g["contributed"]]
        n_big = int((active["delta"].abs() >= 0.25).sum())
        pct_big_of_active = 100 * n_big / n_contrib if n_contrib else 0.0
        pct_big_of_total = 100 * n_big / total_days

        ig = inst[inst["candidate"] == candidate]
        flips_by_day = ig.groupby("date")["flip"].any()
        n_flip_days = int(flips_by_day.sum())
        n_flip_events = int(ig["flip"].sum())
        pct_flip_days_of_active = 100 * n_flip_days / n_contrib if n_contrib else 0.0

        rank = g["rank"].iloc[0]
        rows.append({
            "candidate": candidate, "faza1_rank": rank,
            "days_active": n_contrib, "days_total": total_days,
            "pct_active": round(100 * n_contrib / total_days, 1),
            "n_delta_ge_025": n_big,
            "pct_delta_ge_025_of_active": round(pct_big_of_active, 1),
            "pct_delta_ge_025_of_total": round(pct_big_of_total, 1),
            "n_bias_flip_days": n_flip_days,
            "pct_bias_flip_days_of_active": round(pct_flip_days_of_active, 1),
            "n_bias_flip_events": n_flip_events,
            "delta_abs_p25": round(active["delta"].abs().quantile(0.25), 4),
            "delta_abs_median": round(active["delta"].abs().median(), 4),
            "delta_abs_p75": round(active["delta"].abs().quantile(0.75), 4),
            "delta_abs_max": round(active["delta"].abs().max(), 4),
        })

    out = pd.DataFrame(rows).sort_values("pct_delta_ge_025_of_active", ascending=False)
    out.to_csv(ROOT / "docs" / "bucket-c-panel-summary.csv", index=False)

    pd.set_option("display.max_rows", 30)
    pd.set_option("display.width", 200)
    print("=== Ordered by frequency (% of active days with |delta category|>=0.25) ===")
    print(out[["candidate", "faza1_rank", "days_active", "pct_active",
              "pct_delta_ge_025_of_active", "n_bias_flip_days",
              "pct_bias_flip_days_of_active", "delta_abs_median", "delta_abs_max"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
