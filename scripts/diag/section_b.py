"""Section B — leg correlation vs the theoretical differencing loss.

READ-ONLY. Reads the cached window replay; writes a correlation CSV to docs/.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import CCYS, DOCS_DIR, INSTRUMENTS_CFG, load_window  # noqa: E402
from scripts.diag.section_a import run as run_section_a  # noqa: E402

PAIR_DIVISOR = float(INSTRUMENTS_CFG.get("pair_divisor", 2))


def run() -> dict:
    win = load_window()
    snapshots = win["snapshots"]

    idx_series = pd.DataFrame(
        [{"as_of": s["as_of"], **{c: s["currencies"].get(c, {}).get("index") for c in CCYS}}
         for s in snapshots]
    ).set_index("as_of")
    idx_series.to_csv(DOCS_DIR / "diag-B-currency-index-timeseries.csv")

    corr = idx_series.corr()
    corr.to_csv(DOCS_DIR / "diag-B-currency-correlation-matrix.csv")

    iu = np.triu_indices_from(corr, k=1)
    offdiag = corr.values[iu]
    mean_rho = float(np.nanmean(offdiag))

    a_res = run_section_a()
    sigma_a3_crosssectional = a_res["stage_means"]["a3"]      # per-day cross-sectional std (section A)
    sigma_a4_actual = a_res["stage_means"]["a4"]              # per-day cross-sectional std, differenced

    # Method 1 (task-literal): reuse section A's cross-sectional sigma_a3 as
    # "the" per-leg sigma, combined with the time-series correlation matrix above.
    pred_a4_m1 = np.sqrt(2.0 * sigma_a3_crosssectional**2 * (1 - mean_rho)) / PAIR_DIVISOR

    # Method 2 (fully time-series-consistent cross-check): use the TIME-SERIES
    # variance of each currency's own index (how much currency i's index moves
    # over the window), pooled across the 8 currencies, with the same rho.
    ts_var = idx_series.var(ddof=0)
    pooled_ts_var = float(ts_var.mean())
    pred_a4_m2 = np.sqrt(2.0 * pooled_ts_var * (1 - mean_rho)) / PAIR_DIVISOR

    summary = pd.DataFrame([
        {"method": "M1: cross-sectional sigma_a3 (section A) x time-series rho", "predicted_sigma_a4": pred_a4_m1},
        {"method": "M2: time-series sigma per currency (pooled) x time-series rho", "predicted_sigma_a4": pred_a4_m2},
        {"method": "ACTUAL sigma_a4 (measured, section A)", "predicted_sigma_a4": sigma_a4_actual},
    ])
    summary["ratio_to_actual"] = summary["predicted_sigma_a4"] / sigma_a4_actual
    summary.to_csv(DOCS_DIR / "diag-B-theory-vs-empirical.csv", index=False)

    return {
        "corr": corr,
        "mean_rho": mean_rho,
        "sigma_a3_crosssectional": sigma_a3_crosssectional,
        "sigma_a4_actual": sigma_a4_actual,
        "pred_a4_m1": pred_a4_m1,
        "pred_a4_m2": pred_a4_m2,
        "pooled_ts_var": pooled_ts_var,
        "summary": summary,
    }


if __name__ == "__main__":
    res = run()
    print("Correlation matrix (currency index, time-series over window):")
    print(res["corr"].round(2).to_string())
    print(f"\nmean off-diagonal rho = {res['mean_rho']:.3f}")
    print(res["summary"].to_string(index=False))
