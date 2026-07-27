"""Section D — cross-asset control group (same aggregation, NO base-quote
differencing). If FX dispersion is much lower than cross-asset, differencing
is implicated as an ADDITIONAL contraction on top of whatever cross-asset also
shows from category-averaging + sentiment/trend folding.

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import CROSSASSET_CFG, DOCS_DIR, load_window  # noqa: E402

TH = CROSSASSET_CFG.get("bias_thresholds", {})
MILD, VERY = float(TH.get("mild")), float(TH.get("very"))
CA_SYMS = list(CROSSASSET_CFG.get("instruments", {}).keys())


def run() -> dict:
    win = load_window()
    snapshots = win["snapshots"]

    rows = []
    day_sigma = []
    for s in snapshots:
        ca = s["crossasset"]
        vals = []
        for sym, r in ca.items():
            rows.append({"as_of": s["as_of"], "symbol": sym, "score_precise": r["score_precise"],
                         "bias": r["bias_label"], "coverage": r["coverage"]})
            vals.append(r["score_precise"])
        day_sigma.append({"as_of": s["as_of"], "sigma_crossasset": np.std(vals, ddof=0) if len(vals) > 1 else np.nan,
                          "n": len(vals)})

    df = pd.DataFrame(rows)
    df["abs"] = df["score_precise"].abs()
    df.to_csv(DOCS_DIR / "diag-D-crossasset-scores-long.csv", index=False)

    sigma_df = pd.DataFrame(day_sigma)
    sigma_df.to_csv(DOCS_DIR / "diag-D-crossasset-sigma-by-day.csv", index=False)
    mean_sigma = sigma_df["sigma_crossasset"].mean()

    pct = df["abs"].quantile([.50, .55, .75, .90, .95]).to_dict()
    pct["max"] = df["abs"].max()

    bucket_order = ["Very Bearish", "Bearish", "Neutral", "Bullish", "Very Bullish"]
    bias_pct = (df["bias"].value_counts(normalize=True) * 100).reindex(bucket_order).fillna(0.0)
    neutral_pct = bias_pct["Neutral"]
    directional_pct = bias_pct["Bearish"] + bias_pct["Bullish"]
    very_pct = bias_pct["Very Bearish"] + bias_pct["Very Bullish"]

    return {
        "mean_sigma_crossasset": mean_sigma,
        "abs_pctiles": pct,
        "bias_pct": bias_pct,
        "neutral_pct": neutral_pct,
        "directional_pct": directional_pct,
        "very_pct": very_pct,
        "current_mild": MILD,
        "current_very": VERY,
        "n_obs": len(df),
    }


if __name__ == "__main__":
    res = run()
    print(f"cross-asset mean daily cross-sectional sigma = {res['mean_sigma_crossasset']:.3f} (n={res['n_obs']} obs)")
    print("|score_precise| percentiles:")
    for k, v in res["abs_pctiles"].items():
        print(f"  {k}: {v:.3f}")
    print(f"\nthresholds: mild={res['current_mild']}, very={res['current_very']}")
    print(res["bias_pct"].round(1).to_string())
    print(f"-> Neutral {res['neutral_pct']:.1f}% | directional {res['directional_pct']:.1f}% | very {res['very_pct']:.1f}%")
