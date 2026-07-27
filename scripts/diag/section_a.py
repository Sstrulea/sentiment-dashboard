"""Section A — dispersion audit: where does cross-sectional variance die.

READ-ONLY. Reads the cached window replay; writes CSVs to docs/.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import (  # noqa: E402
    CATS, CCYS, DOCS_DIR, FX_PAIRS, INSTRUMENTS_CFG, classify_cells_for_day, load_window,
)

PAIR_DIVISOR = float(INSTRUMENTS_CFG.get("pair_divisor", 2))
SCALE = float(INSTRUMENTS_CFG.get("scale", 5))
INST_CFG = INSTRUMENTS_CFG["instruments"]


def _day_stage_sigmas(snapshot: dict) -> dict:
    currencies = snapshot["currencies"]

    # A1: indicator-level scores, applicable + present + NOT stale.
    cells = classify_cells_for_day(snapshot)
    a1_vals = [c["score"] for c in cells if c["cause"] in ("scored_nonzero", "dead_zone")]

    # A2: category score_precise, coverage > 0.
    a2_vals = []
    for ccy in CCYS:
        card = currencies.get(ccy, {})
        for cat in CATS:
            cell = card.get("categories", {}).get(cat, {})
            if (cell.get("coverage") or 0) > 0:
                a2_vals.append(cell["score_precise"])

    # A3: currency index (macro-only, pre-sentiment/trend).
    a3_vals = [currencies[c]["index"] for c in CCYS if c in currencies]

    # A4: FX pair score from RAW differencing of A3 indices only (no
    # sentiment/trend fold) — isolates the differencing step itself.
    a4_vals = []
    for sym in FX_PAIRS:
        cfg = INST_CFG[sym]
        base_idx = currencies.get(cfg["base"], {}).get("index")
        quote_idx = currencies.get(cfg["quote"], {}).get("index")
        if base_idx is not None and quote_idx is not None:
            a4_vals.append((base_idx - quote_idx) / PAIR_DIVISOR)

    # A5: final production score (TREND + SENTIMENT folded), same 27 FX pairs.
    inst_by_sym = {i["symbol"]: i for i in snapshot["instruments"]}
    a5_vals = [inst_by_sym[s]["score"] for s in FX_PAIRS if s in inst_by_sym]

    return {
        "as_of": snapshot["as_of"],
        "sigma_a1": np.std(a1_vals, ddof=0) if len(a1_vals) > 1 else np.nan,
        "n_a1": len(a1_vals),
        "sigma_a2": np.std(a2_vals, ddof=0) if len(a2_vals) > 1 else np.nan,
        "n_a2": len(a2_vals),
        "sigma_a3": np.std(a3_vals, ddof=0) if len(a3_vals) > 1 else np.nan,
        "n_a3": len(a3_vals),
        "sigma_a4": np.std(a4_vals, ddof=0) if len(a4_vals) > 1 else np.nan,
        "n_a4": len(a4_vals),
        "sigma_a5": np.std(a5_vals, ddof=0) if len(a5_vals) > 1 else np.nan,
        "n_a5": len(a5_vals),
    }


def run() -> dict:
    win = load_window()
    snapshots = win["snapshots"]

    per_day = pd.DataFrame([_day_stage_sigmas(s) for s in snapshots])
    per_day.to_csv(DOCS_DIR / "diag-A-dispersion-by-day.csv", index=False)

    stage_means = {
        stage: per_day[f"sigma_{stage}"].mean() for stage in ["a1", "a2", "a3", "a4", "a5"]
    }
    stages = ["a1", "a2", "a3", "a4", "a5"]
    labels = {
        "a1": "indicator score (-2..2)",
        "a2": "category score_precise",
        "a3": "currency index (macro, x scale)",
        "a4": "FX pair score (differencing only, no fold)",
        "a5": "final score (TREND+SENTIMENT folded)",
    }
    ratios = {}
    for prev, cur in zip(stages, stages[1:]):
        ratios[f"{prev}->{cur}"] = stage_means[cur] / stage_means[prev] if stage_means[prev] else np.nan

    # RAW ratios mix units: a3/a4/a5 carry the currency-index x SCALE display
    # multiplier that a1/a2 don't, so a2->a3 looks like an "expansion" purely
    # because of the x5 relabeling, not because variance was created. Divide
    # a3/a4/a5 by SCALE to bring every stage back to the same underlying
    # [-2,+2]-ish unit before comparing — this is the apples-to-apples chain
    # that actually answers "where does variance die". pair_divisor (a3->a4)
    # is a REAL structural shrink (not a relabeling) and is deliberately left in.
    stage_means_norm = dict(stage_means)
    for s in ("a3", "a4", "a5"):
        stage_means_norm[s] = stage_means[s] / SCALE
    ratios_norm = {}
    for prev, cur in zip(stages, stages[1:]):
        ratios_norm[f"{prev}->{cur}"] = (
            stage_means_norm[cur] / stage_means_norm[prev] if stage_means_norm[prev] else np.nan
        )

    summary = pd.DataFrame({
        "stage": stages,
        "label": [labels[s] for s in stages],
        "mean_sigma_raw": [stage_means[s] for s in stages],
        "mean_sigma_normalized": [stage_means_norm[s] for s in stages],
        "mean_n": [per_day[f"n_{s}"].mean() for s in stages],
    })
    summary["ratio_raw_vs_prev"] = [np.nan] + [ratios[f"{a}->{b}"] for a, b in zip(stages, stages[1:])]
    summary["ratio_normalized_vs_prev"] = [np.nan] + [ratios_norm[f"{a}->{b}"] for a, b in zip(stages, stages[1:])]
    summary.to_csv(DOCS_DIR / "diag-A-dispersion-summary.csv", index=False)

    biggest_contraction = min(
        [(k, v) for k, v in ratios_norm.items() if pd.notna(v)], key=lambda kv: kv[1]
    )

    # Zero-cause split across the full window (A1).
    all_cells = []
    for s in snapshots:
        all_cells.extend(classify_cells_for_day(s))
    cells_df = pd.DataFrame(all_cells)
    cause_split = (
        cells_df["cause"].value_counts(normalize=True).mul(100).round(2).to_dict()
    )
    cause_by_cat = (
        cells_df.groupby(["category", "cause"]).size().unstack(fill_value=0)
    )
    cause_by_cat_pct = cause_by_cat.div(cause_by_cat.sum(axis=1), axis=0).mul(100).round(1)
    cause_by_cat_pct.to_csv(DOCS_DIR / "diag-A-zero-cause-by-category.csv")
    cells_df.to_csv(DOCS_DIR / "diag-A-cells-raw.csv", index=False)

    return {
        "summary_df": summary,
        "stage_means": stage_means,
        "stage_means_norm": stage_means_norm,
        "ratios_raw": ratios,
        "ratios_normalized": ratios_norm,
        "biggest_contraction": biggest_contraction,
        "cause_split_pct": cause_split,
        "cause_by_cat_pct": cause_by_cat_pct,
        "n_days": len(snapshots),
    }


if __name__ == "__main__":
    res = run()
    print(res["summary_df"].to_string(index=False))
    print("\nraw ratios:", res["ratios_raw"])
    print("normalized ratios (a3/a4/a5 / scale=%.0f):" % SCALE, res["ratios_normalized"])
    print("biggest contraction (normalized):", res["biggest_contraction"])
    print("\nzero-cause split (all applicable cells, window-wide):")
    for k, v in sorted(res["cause_split_pct"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:16} {v:5.1f}%")
    print("\nby category:")
    print(res["cause_by_cat_pct"])
