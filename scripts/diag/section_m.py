"""Section M — absent-category audit across the 8 currencies.

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import (  # noqa: E402
    _ALL_INDICATORS, CATS, CCYS, DOCS_DIR, applicable_indicators_for, load_today, load_window,
)

RATES_PATH = ROOT / "data" / "rates.parquet"


def _classify_category(card: dict, cat: str, ccy: str) -> tuple[str, str]:
    """(state, root_cause) for one (currency, category) cell."""
    breakdown = card.get("breakdown", {}) or {}

    if cat == "monetary":
        cell = card.get("categories", {}).get("monetary")
        if cell is None:
            n_rows = 0  # filled in by caller with the real rates.parquet count
            return "ABSENT", "no rate_expectations entry (compute_rate_scores returned nothing for this currency)"
        if cell.get("stale"):
            return "STALE", "rate_expectations present but latest obs older than MAX_AGE_BD -> excluded from index"
        if (cell.get("coverage") or 0) > 0:
            return "PRESENT", "live"
        return "ABSENT", "unexpected: monetary key present with coverage=0 and stale unset"

    cell = card.get("categories", {}).get(cat, {})
    coverage = cell.get("coverage") or 0
    if coverage > 0:
        return "PRESENT", "live"

    # coverage==0: distinguish ABSENT (no applicable indicator ever scored, not
    # even as stale) from STALE (every applicable indicator was scored but
    # excluded as stale) by looking at the raw breakdown entries.
    applicable = [k for k in applicable_indicators_for(ccy) if _ALL_INDICATORS[k]["category"] == cat]
    present_entries = [breakdown.get(k) for k in applicable if breakdown.get(k) is not None]
    if not present_entries:
        return "ABSENT", f"none of {applicable} ever scored (no_data for all)"
    if all(e.get("stale") for e in present_entries):
        return "STALE", f"all of {[k for k in applicable if breakdown.get(k)]} scored but stale"
    return "ABSENT", "mixed no_data/stale, zero non-stale coverage"


def run() -> dict:
    today = load_today()["snapshot"]
    win = load_window()

    rates = pd.read_parquet(RATES_PATH)
    n_rows_by_ccy = rates["currency"].value_counts().to_dict()

    # --- today ---
    today_rows = []
    for ccy in CCYS:
        card = today["currencies"].get(ccy, {})
        for cat in CATS:
            state, cause = _classify_category(card, cat, ccy)
            if cat == "monetary" and state == "ABSENT":
                cause = f"no rate_expectations entry; {n_rows_by_ccy.get(ccy, 0)} rows for {ccy} in data/rates.parquet"
            today_rows.append({"ccy": ccy, "category": cat, "state": state, "root_cause": cause})
    today_df = pd.DataFrame(today_rows)
    today_df.to_csv(DOCS_DIR / "diag-M-category-presence-today.csv", index=False)

    # --- window-wide (fraction of the 53 days in each state) ---
    window_rows = []
    for s in win["snapshots"]:
        for ccy in CCYS:
            card = s["currencies"].get(ccy, {})
            for cat in CATS:
                state, _ = _classify_category(card, cat, ccy)
                window_rows.append({"as_of": s["as_of"], "ccy": ccy, "category": cat, "state": state})
    window_df = pd.DataFrame(window_rows)
    window_summary = (
        window_df.groupby(["ccy", "category"])["state"]
        .value_counts(normalize=True).mul(100).round(1).unstack(fill_value=0)
    )
    window_summary.to_csv(DOCS_DIR / "diag-M-category-presence-window.csv")

    # --- arithmetic confirmation: (a) mean-over-present vs (b) mean-with-zero ---
    nzd_card = today["currencies"]["NZD"]
    precise_vals = [nzd_card["categories"][c]["score_precise"] for c in ("growth", "inflation", "labour")]
    scale = 5.0
    variant_a = (sum(precise_vals) / len(precise_vals)) * scale                 # mean over PRESENT only
    variant_b = (sum(precise_vals) / 4.0) * scale                              # mean over all 4, absent=0
    actual = nzd_card["index"]

    return {
        "today_df": today_df, "window_summary": window_summary,
        "variant_a": variant_a, "variant_b": variant_b, "actual_index": actual,
        "matches_variant_a": abs(actual - variant_a) < 1e-9,
        "matches_variant_b": abs(actual - variant_b) < 1e-9,
    }


if __name__ == "__main__":
    res = run()
    print("TODAY (2026-07-28 snapshot):")
    print(res["today_df"].to_string(index=False))
    print("\nWINDOW-WIDE (% of 53 days in each state), per ccy x category:")
    print(res["window_summary"].to_string())
    print(f"\nNZD index actual        = {res['actual_index']:.6f}")
    print(f"variant (a) mean-over-present x5 = {res['variant_a']:.6f}  match={res['matches_variant_a']}")
    print(f"variant (b) mean-with-zero x5    = {res['variant_b']:.6f}  match={res['matches_variant_b']}")
