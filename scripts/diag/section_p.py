"""Section P — data-age asymmetry (current day).

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import (  # noqa: E402
    CCYS, DOCS_DIR, FX_PAIRS, INSTRUMENTS_CFG, SCORED_INDICATOR_KEYS, _ALL_INDICATORS,
    applicable_indicators_for, load_today,
)

INST_CFG = INSTRUMENTS_CFG["instruments"]
RATES_PATH = ROOT / "data" / "rates.parquet"


def contributing_ages(snapshot: dict, as_of: pd.Timestamp, rates_latest: dict) -> pd.DataFrame:
    """One row per (currency, indicator) that ACTUALLY feeds the index today
    (non-stale, present) — growth/inflation/labour indicators use release_dt;
    monetary uses the rates.parquet latest observation date (the breakdown's
    own `as_of` field is the reference date, not the observation date)."""
    rows = []
    for ccy in CCYS:
        card = snapshot["currencies"].get(ccy, {})
        breakdown = card.get("breakdown", {}) or {}
        for key in applicable_indicators_for(ccy):
            e = breakdown.get(key)
            if e is None or e.get("stale") or e.get("score") is None:
                continue
            if e.get("release_dt") is None:
                continue
            age = (as_of.normalize() - pd.Timestamp(e["release_dt"]).normalize()).days
            rows.append({"ccy": ccy, "indicator_key": key, "category": _ALL_INDICATORS[key]["category"],
                        "age_days": age})
        me = breakdown.get("rate_expectations")
        if me is not None and not me.get("stale"):
            latest_date = rates_latest.get(ccy)
            if latest_date is not None:
                age = (as_of.normalize() - pd.Timestamp(latest_date).normalize()).days
                rows.append({"ccy": ccy, "indicator_key": "rate_expectations", "category": "monetary",
                            "age_days": age})
    return pd.DataFrame(rows)


def no_consensus_counts(snapshot: dict) -> pd.DataFrame:
    rows = []
    for ccy in CCYS:
        breakdown = (snapshot["currencies"].get(ccy, {}) or {}).get("breakdown", {}) or {}
        for key in applicable_indicators_for(ccy):
            e = breakdown.get(key)
            cause = "no_data" if e is None else ("no_consensus" if e.get("flag") == "no_consensus"
                                                   else ("dead_zone" if e.get("score") == 0 else "scored"))
            rows.append({"ccy": ccy, "indicator_key": key, "category": _ALL_INDICATORS[key]["category"],
                        "cause": cause})
    df = pd.DataFrame(rows)
    summary = (
        df[df["cause"] == "no_consensus"].groupby(["ccy", "category"]).size()
        .reset_index(name="n_no_consensus")
    )
    totals = df.groupby(["ccy", "category"]).size().reset_index(name="n_applicable")
    out = totals.merge(summary, on=["ccy", "category"], how="left").fillna(0)
    out["n_no_consensus"] = out["n_no_consensus"].astype(int)
    return out, df


def run() -> dict:
    today_pkg = load_today()
    today, as_of = today_pkg["snapshot"], today_pkg["as_of"]

    rates = pd.read_parquet(RATES_PATH)
    rates["date"] = pd.to_datetime(rates["date"])
    rates_latest = rates.sort_values("date").groupby("currency")["date"].last().to_dict()

    ages_df = contributing_ages(today, as_of, rates_latest)
    ages_df.to_csv(DOCS_DIR / "diag-P-contributing-ages.csv", index=False)

    per_ccy = ages_df.groupby("ccy")["age_days"].agg(["mean", "median", "count"]).round(1)
    per_ccy = per_ccy.reindex(CCYS)
    per_ccy.to_csv(DOCS_DIR / "diag-P-age-per-currency.csv")

    pair_rows = []
    for sym in FX_PAIRS:
        cfg = INST_CFG[sym]
        base, quote = cfg["base"], cfg["quote"]
        ab = per_ccy.loc[base, "mean"] if base in per_ccy.index else np.nan
        aq = per_ccy.loc[quote, "mean"] if quote in per_ccy.index else np.nan
        if pd.isna(ab) or pd.isna(aq):
            continue
        ratio = max(ab, aq) / min(ab, aq) if min(ab, aq) > 0 else np.inf
        pair_rows.append({"symbol": sym, "base": base, "base_mean_age": ab, "quote": quote,
                         "quote_mean_age": aq, "age_diff": ab - aq, "abs_age_diff": abs(ab - aq),
                         "age_ratio": ratio})
    pair_df = pd.DataFrame(pair_rows).sort_values("abs_age_diff", ascending=False)
    pair_df.to_csv(DOCS_DIR / "diag-P-pair-age-diff.csv", index=False)

    n_over_2x = int((pair_df["age_ratio"] > 2.0).sum())

    nc_summary, nc_raw = no_consensus_counts(today)
    nc_summary.to_csv(DOCS_DIR / "diag-P-no-consensus-counts.csv", index=False)

    nzd_growth = nc_summary[(nc_summary.ccy == "NZD") & (nc_summary.category == "growth")]

    return {
        "per_ccy": per_ccy, "pair_df": pair_df, "n_over_2x": n_over_2x,
        "nc_summary": nc_summary, "nzd_growth_check": nzd_growth,
    }


if __name__ == "__main__":
    res = run()
    print("Age (days since release) of contributing indicators, per currency:")
    print(res["per_ccy"].to_string())
    print("\nPer-pair age difference (sorted desc by |diff|):")
    print(res["pair_df"].to_string(index=False))
    print(f"\npairs with age_ratio > 2x: {res['n_over_2x']}/{len(res['pair_df'])}")
    print("\nno_consensus counts per (ccy, category):")
    print(res["nc_summary"].to_string(index=False))
    print("\nNZD growth check (expect 2/4):")
    print(res["nzd_growth_check"].to_string(index=False))
