"""Section H — counterfactual V1: reweight FUND 0.5 / TREND 1.0 / SENTIMENT 0.5,
rates (monetary z-momentum method itself) unchanged.

Scratch reweight in memory only — deep-copies of the YAML dicts, nothing
written to data/*.yaml. Maps the three named pillars onto the architecture's
actual tunable knobs:
  FUND      -> each category weight (growth/inflation/labour/monetary) x0.5
              (currently 1.0 each; monetary defaults to 1.0 when absent from
              the categories dict, so an explicit 0.5 override is added)
  SENTIMENT -> instruments.sentiment_weight (unchanged at 0.5)
  TREND     -> instruments.trend_weight  0.5 -> 1.0

READ-ONLY over data/*.yaml; only in-memory dicts are mutated.
"""
from __future__ import annotations

import copy
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.engine import Context, build_snapshot  # noqa: E402
from scripts.diag.analysis_common import DOCS_DIR, FX_PAIRS, load_window  # noqa: E402


def _reweighted_configs(ctx: Context) -> tuple[dict, dict]:
    ind_cfg = copy.deepcopy(ctx.indicators_cfg)
    inst_cfg = copy.deepcopy(ctx.instruments_cfg)

    for cat in ("growth", "inflation", "labour"):
        ind_cfg["categories"][cat]["weight"] = float(ind_cfg["categories"][cat].get("weight", 1.0)) * 0.5
    ind_cfg["categories"]["monetary"] = {"weight": 0.5, "label": "Monetary Policy"}

    inst_cfg["sentiment_weight"] = 0.5
    inst_cfg["trend_weight"] = 1.0
    return ind_cfg, inst_cfg


def run() -> dict:
    win = load_window()
    snapshots_before = win["snapshots"]

    ctx = Context()
    ind_cfg_cf, inst_cfg_cf = _reweighted_configs(ctx)

    t0 = time.time()
    snapshots_after = [
        build_snapshot(s["as_of"], ctx, indicators_cfg=ind_cfg_cf, instruments_cfg=inst_cfg_cf)
        for s in snapshots_before
    ]
    print(f"counterfactual V1 replay: {time.time()-t0:.1f}s for {len(snapshots_after)} days")

    rows = []
    for sb, sa in zip(snapshots_before, snapshots_after):
        bb = {i["symbol"]: i for i in sb["instruments"]}
        ba = {i["symbol"]: i for i in sa["instruments"]}
        for sym in FX_PAIRS:
            if sym not in bb or sym not in ba:
                continue
            rows.append({
                "as_of": sb["as_of"], "symbol": sym,
                "score_before": bb[sym]["score"], "bias_before": bb[sym]["bias"],
                "score_after": ba[sym]["score"], "bias_after": ba[sym]["bias"],
                "flipped": bb[sym]["bias"] != ba[sym]["bias"],
            })
    df = pd.DataFrame(rows)
    df.to_csv(DOCS_DIR / "diag-H-counterfactual-v1-long.csv", index=False)

    flip_rate = 100.0 * df["flipped"].mean()
    flip_matrix = pd.crosstab(df["bias_before"], df["bias_after"])
    flip_matrix.to_csv(DOCS_DIR / "diag-H-counterfactual-v1-flip-matrix.csv")

    # today snapshot before/after table (readable)
    today_as_of = snapshots_before[-1]["as_of"]
    today_table = df[df["as_of"] == today_as_of].sort_values("symbol")
    today_table.to_csv(DOCS_DIR / "diag-H-counterfactual-v1-today.csv", index=False)

    # new sigma (cross-sectional, mean over days) + new |score| distribution + new bias split
    day_sigma = df.groupby("as_of")["score_after"].apply(lambda s: np.std(s, ddof=0))
    new_sigma_mean = day_sigma.mean()
    old_sigma_mean = df.groupby("as_of")["score_before"].apply(lambda s: np.std(s, ddof=0)).mean()

    bucket_order = ["Very Bearish", "Bearish", "Neutral", "Bullish", "Very Bullish"]
    old_split = (df["bias_before"].value_counts(normalize=True) * 100).reindex(bucket_order).fillna(0)
    new_split = (df["bias_after"].value_counts(normalize=True) * 100).reindex(bucket_order).fillna(0)

    old_abs_pct = df["score_before"].abs().quantile([.5, .75, .9, .95])
    new_abs_pct = df["score_after"].abs().quantile([.5, .75, .9, .95])

    return {
        "flip_rate_pct": flip_rate, "flip_matrix": flip_matrix, "today_table": today_table,
        "old_sigma_mean": old_sigma_mean, "new_sigma_mean": new_sigma_mean,
        "old_split": old_split, "new_split": new_split,
        "old_abs_pct": old_abs_pct, "new_abs_pct": new_abs_pct,
        "n_flips": int(df["flipped"].sum()), "n_obs": len(df),
    }


if __name__ == "__main__":
    res = run()
    print(f"\nflip rate: {res['flip_rate_pct']:.1f}%  ({res['n_flips']}/{res['n_obs']} pair-days)")
    print("\nflip matrix (before rows x after cols):")
    print(res["flip_matrix"].to_string())
    print("\ntoday before/after:")
    print(res["today_table"].to_string(index=False))
    print(f"\nsigma: before={res['old_sigma_mean']:.3f}  after={res['new_sigma_mean']:.3f}")
    print("\nbias split before:")
    print(res["old_split"].round(1).to_string())
    print("bias split after:")
    print(res["new_split"].round(1).to_string())
    print("\n|score| percentiles before:", res["old_abs_pct"].round(3).to_dict())
    print("|score| percentiles after: ", res["new_abs_pct"].round(3).to_dict())
