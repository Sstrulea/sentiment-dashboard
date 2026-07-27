"""Section C — FX calibration drift: |score| distribution vs mild/very targets.

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import DOCS_DIR, FX_PAIRS, INSTRUMENTS_CFG, load_window  # noqa: E402

TH = INSTRUMENTS_CFG.get("bias_thresholds", {})
MILD, VERY = float(TH.get("mild")), float(TH.get("very"))


def _long_frame(win) -> pd.DataFrame:
    rows = []
    for s in win["snapshots"]:
        by_sym = {i["symbol"]: i for i in s["instruments"]}
        for sym in FX_PAIRS:
            inst = by_sym.get(sym)
            if inst is None:
                continue
            rows.append({"as_of": s["as_of"], "symbol": sym, "score": inst["score"], "bias": inst["bias"]})
    return pd.DataFrame(rows)


def run() -> dict:
    win = load_window()
    df = _long_frame(win)
    df["abs"] = df["score"].abs()
    df.to_csv(DOCS_DIR / "diag-C-fx-scores-long.csv", index=False)

    pct = df["abs"].quantile([.50, .55, .75, .90, .95]).to_dict()
    pct["max"] = df["abs"].max()

    bucket_order = ["Very Bearish", "Bearish", "Neutral", "Bullish", "Very Bullish"]
    bias_pct = (df["bias"].value_counts(normalize=True) * 100).reindex(bucket_order).fillna(0.0)
    neutral_pct = bias_pct["Neutral"]
    directional_pct = bias_pct["Bearish"] + bias_pct["Bullish"]
    very_pct = bias_pct["Very Bearish"] + bias_pct["Very Bullish"]

    # Target: mild calibrated so |score| at p(neutral%) ~= mild; very at p(100-very%) ~= very.
    # Compare realized split (neutral_pct/very_pct) against the DESIGN targets baked
    # into the thresholds' own docstring in the YAML (p55 -> ~55% neutral, p90 -> ~10% very).
    target_neutral, target_very = 55.0, 10.0
    empirical_mild_at_p55 = float(df["abs"].quantile(0.55))
    empirical_very_at_p90 = float(df["abs"].quantile(0.90))

    # Days/month with >= 1 "Very" (Very Bullish or Very Bearish) anywhere on the FX board.
    df["date"] = pd.to_datetime(df["as_of"]).dt.date
    df["month"] = pd.to_datetime(df["as_of"]).dt.to_period("M")
    very_days = df[df["bias"].isin(["Very Bullish", "Very Bearish"])].groupby("date").size()
    days_with_very_per_month = (
        pd.Series(very_days.index).apply(lambda d: pd.Timestamp(d).to_period("M"))
        .value_counts().sort_index()
    )
    all_days_per_month = df.groupby("month")["date"].nunique()
    very_day_rate_by_month = (days_with_very_per_month.reindex(all_days_per_month.index).fillna(0)
                              / all_days_per_month * 100).round(1)

    # Monthly bias-split series (to see WHEN any divergence from target appeared).
    monthly_split = (
        df.groupby("month")["bias"].value_counts(normalize=True).unstack(fill_value=0) * 100
    ).reindex(columns=bucket_order, fill_value=0).round(1)
    monthly_split["directional"] = monthly_split["Bearish"] + monthly_split["Bullish"]
    monthly_split["very"] = monthly_split["Very Bearish"] + monthly_split["Very Bullish"]
    monthly_split.to_csv(DOCS_DIR / "diag-C-monthly-bias-split.csv")

    return {
        "abs_pctiles": pct,
        "bias_pct": bias_pct,
        "neutral_pct": neutral_pct,
        "directional_pct": directional_pct,
        "very_pct": very_pct,
        "current_mild": MILD,
        "current_very": VERY,
        "empirical_mild_at_p55": empirical_mild_at_p55,
        "empirical_very_at_p90": empirical_very_at_p90,
        "very_day_rate_by_month": very_day_rate_by_month,
        "monthly_split": monthly_split,
        "n_obs": len(df),
    }


if __name__ == "__main__":
    res = run()
    print("|score| percentiles (FX, window-wide):")
    for k, v in res["abs_pctiles"].items():
        print(f"  {k}: {v:.3f}")
    print(f"\ncurrent thresholds: mild={res['current_mild']}, very={res['current_very']}")
    print(f"empirical |score| @p55 = {res['empirical_mild_at_p55']:.3f}  (target for mild, ~55% neutral)")
    print(f"empirical |score| @p90 = {res['empirical_very_at_p90']:.3f}  (target for very, ~10% very)")
    print("\nrealized bias split (window-wide):")
    print(res["bias_pct"].round(1).to_string())
    print(f"\n-> Neutral {res['neutral_pct']:.1f}% | directional {res['directional_pct']:.1f}% | very {res['very_pct']:.1f}%")
    print("\n%days/month with >=1 Very on FX board:")
    print(res["very_day_rate_by_month"].to_string())
    print("\nmonthly bias split:")
    print(res["monthly_split"].to_string())
