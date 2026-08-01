"""measure/coverage-asymmetry — Part 1: N <-> |score_precise| amplitude relationship.

Two views:
  (a) pooled across all (currency, category) series — the raw N-vs-amplitude
      relationship, confounded by currency (a currency with intrinsically
      more volatile data might also happen to have low N).
  (b) within-series — for each (currency, category) that visits BOTH a
      low-N (<=2) and a high-N (>=4) state at some point in its own history,
      compare its OWN median |score_precise| across those states. This
      controls for the currency-effect confound: same series, same
      underlying volatility regime (roughly), only N differs over time.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
HIST_CSV = ROOT / "docs" / "coverage-asymmetry-history.csv"


def main():
    df = pd.read_csv(HIST_CSV)
    df["abs_score"] = df["score_precise"].abs()

    print("=== (a) POOLED: |score_precise| by N (all series, all dates) ===")
    low = df[df["coverage"] <= 2]["abs_score"]
    high = df[df["coverage"] >= 4]["abs_score"]
    med_low, med_high = low.median(), high.median()
    print(f"N<=2: n_obs={len(low)}, median|score|={med_low:.4f}, mean={low.mean():.4f}")
    print(f"N>=4: n_obs={len(high)}, median|score|={med_high:.4f}, mean={high.mean():.4f}")
    rel_increase = (med_low - med_high) / med_high * 100 if med_high else float("inf")
    print(f"Relative increase (N<=2 vs N>=4): {rel_increase:+.1f}%")
    print()
    print("Full breakdown by exact N:")
    print(df.groupby("coverage")["abs_score"].agg(["count", "median", "mean"]).to_string())

    print()
    print("=== (b) WITHIN-SERIES: same (currency,category), low-N period vs high-N period ===")
    rows = []
    for (ccy, cat), g in df.groupby(["currency", "category"]):
        g_low = g[g["coverage"] <= 2]["abs_score"]
        g_high = g[g["coverage"] >= 4]["abs_score"]
        if len(g_low) >= 5 and len(g_high) >= 5:
            rows.append({
                "currency": ccy, "category": cat,
                "n_obs_low": len(g_low), "median_low": g_low.median(),
                "n_obs_high": len(g_high), "median_high": g_high.median(),
                "rel_increase_pct": (g_low.median() - g_high.median()) / g_high.median() * 100
                                    if g_high.median() else float("nan"),
            })
    within = pd.DataFrame(rows)
    print(f"{len(within)} (currency,category) series have >=5 obs at BOTH N<=2 and N>=4:")
    print(within.to_string(index=False))
    if len(within):
        print()
        print(f"Median of within-series relative increases: {within['rel_increase_pct'].median():.1f}%")
        print(f"Fraction of series where low-N amplitude > high-N amplitude: "
              f"{(within['median_low'] > within['median_high']).mean()*100:.1f}%")

    within.to_csv(ROOT / "docs" / "coverage-asymmetry-within-series.csv", index=False)


if __name__ == "__main__":
    main()
