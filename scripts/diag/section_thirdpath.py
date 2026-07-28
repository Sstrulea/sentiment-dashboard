"""Narrow follow-up to §N: the THIRD path (the accumulators that actually
produce score_precise) vs path 1 (the dense table, what a user sees).

READ-ONLY. Nothing fixed, no dead code removed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_render import TABLE_COLUMN_KEYS  # noqa: E402
from scripts.diag.analysis_common import (  # noqa: E402
    _ALL_INDICATORS, CCYS, DOCS_DIR, FX_PAIRS, INSTRUMENTS_CFG, load_today, load_window,
)

INST_CFG = INSTRUMENTS_CFG["instruments"]
SCALE = float(INSTRUMENTS_CFG["scale"])
PAIR_DIVISOR = float(INSTRUMENTS_CFG["pair_divisor"])
KEY_TO_CATEGORY = {k: v["category"] for k, v in _ALL_INDICATORS.items()}
KEY_TO_CATEGORY["rate_expectations"] = "monetary"
CATEGORY_WEIGHT = {"growth": 1.0, "inflation": 1.0, "labour": 1.0, "monetary": 1.0}  # confirmed defaults, all currencies


def _leg_multiplier(card: dict, cat: str) -> float:
    """d(index)/d(score_k) for indicator k in category `cat` on this leg's
    OWN currency index — the exact linear coefficient from
    compute_currency_scorecard: weight_c / (index_wsum * coverage_c), scaled.
    0 if the category is absent/stale (excluded from index_wsum -> no route
    from this indicator to the index at all)."""
    cell = (card.get("categories") or {}).get(cat)
    if cell is None or (cell.get("coverage") or 0) <= 0:
        return 0.0
    index_wsum = card.get("index_wsum", 0.0)
    if not index_wsum:
        return 0.0
    coverage_c = cell["coverage"]
    return (CATEGORY_WEIGHT[cat] / (index_wsum * coverage_c)) * SCALE


def path3_contribution(base_card, quote_card, key: str, eb, eq) -> float:
    """The indicator's exact marginal contribution to macro_score (FUND-only,
    pre-sentiment/pre-trend) — linear decomposition, verified to sum exactly
    to macro_score across all indicators (see run())."""
    cat = KEY_TO_CATEGORY[key]
    base_mult = _leg_multiplier(base_card, cat) if base_card else 0.0
    quote_mult = _leg_multiplier(quote_card, cat) if quote_card else 0.0
    base_term = (eb["score"] * base_mult / PAIR_DIVISOR) if (eb is not None and not eb.get("stale") and base_mult) else 0.0
    quote_term = (eq["score"] * quote_mult / PAIR_DIVISOR) if (eq is not None and not eq.get("stale") and quote_mult) else 0.0
    return base_term - quote_term


def path1_value(eb, eq):
    if eb is None and eq is None:
        return None
    return (eb["score"] if eb else 0) - (eq["score"] if eq else 0)


def per_snapshot_table(snapshot: dict) -> pd.DataFrame:
    currencies = snapshot["currencies"]
    rows = []
    for sym in FX_PAIRS:
        cfg = INST_CFG[sym]
        base, quote = cfg["base"], cfg["quote"]
        base_card, quote_card = currencies.get(base, {}), currencies.get(quote, {})
        bb = (base_card.get("breakdown") or {})
        bq = (quote_card.get("breakdown") or {})
        for key in TABLE_COLUMN_KEYS:
            eb, eq = bb.get(key), bq.get(key)
            v1 = path1_value(eb, eq)
            v3 = path3_contribution(base_card, quote_card, key, eb, eq)
            rows.append({"symbol": sym, "column": key, "path1_displayed": v1, "path3_marginal": v3})
    return pd.DataFrame(rows)


def validate_linearity(snapshot: dict) -> pd.DataFrame:
    """Sanity check: summing path3 contributions over ALL indicators for a
    pair must reconstruct macro_score (FUND-only, no sentiment/trend) EXACTLY
    -- this is a linear decomposition, not an approximation."""
    currencies = snapshot["currencies"]
    rows = []
    for sym in FX_PAIRS:
        cfg = INST_CFG[sym]
        base, quote = cfg["base"], cfg["quote"]
        base_card, quote_card = currencies.get(base, {}), currencies.get(quote, {})
        macro_score_actual = (base_card.get("index", 0.0) - quote_card.get("index", 0.0)) / PAIR_DIVISOR
        bb, bq = (base_card.get("breakdown") or {}), (quote_card.get("breakdown") or {})
        total = sum(path3_contribution(base_card, quote_card, key, bb.get(key), bq.get(key))
                    for key in TABLE_COLUMN_KEYS)
        rows.append({"symbol": sym, "macro_score_actual": macro_score_actual,
                    "sum_path3_contributions": total, "diff": macro_score_actual - total})
    return pd.DataFrame(rows)


def run() -> dict:
    today_pkg = load_today()
    today = today_pkg["snapshot"]
    win = load_window()

    lin = validate_linearity(today)
    max_abs_diff = lin["diff"].abs().max()

    today_table = per_snapshot_table(today)
    today_table.to_csv(DOCS_DIR / "diag-3P-today.csv", index=False)

    window_rows = []
    for s in win["snapshots"]:
        t = per_snapshot_table(s)
        t["as_of"] = s["as_of"]
        window_rows.append(t)
    window_table = pd.concat(window_rows, ignore_index=True)
    window_table.to_csv(DOCS_DIR / "diag-3P-window.csv", index=False)

    valid = window_table.dropna(subset=["path1_displayed", "path3_marginal"]).copy()
    valid["diverges"] = (valid["path1_displayed"] - valid["path3_marginal"]).abs() > 1e-9
    n_diverging = int(valid["diverges"].sum())
    n_total = len(valid)

    diverging = valid[valid["diverges"]].copy()
    diverging["delta"] = diverging["path1_displayed"] - diverging["path3_marginal"]
    ratio = diverging["path1_displayed"] / diverging["path3_marginal"].replace(0, np.nan)
    ratio_rounded = ratio.round(3)

    # NZDUSD monetary + AUD exclusion check
    nzd_card = today["currencies"]["NZD"]
    aud_card = today["currencies"]["AUD"]
    usd_card = today["currencies"]["USD"]
    nzdusd_monetary_mult_base = _leg_multiplier(nzd_card, "monetary")  # NZD leg (base) -- structurally absent
    nzdusd_monetary_mult_quote = _leg_multiplier(usd_card, "monetary")  # USD leg (quote) -- live
    aud_monetary_mult = _leg_multiplier(aud_card, "monetary")  # AUD -- stale

    # Q4: sign mismatch between sum(path1 displayed, all columns) and inst["score"]
    inst_by_sym = {i["symbol"]: i for i in today["instruments"]}
    sign_rows = []
    for sym in FX_PAIRS:
        sub = today_table[today_table.symbol == sym]
        sum_path1 = sub["path1_displayed"].fillna(0).sum()
        final_score = inst_by_sym[sym]["score"]
        sign_rows.append({"symbol": sym, "sum_path1_displayed": sum_path1, "final_score": final_score,
                         "sign_path1": np.sign(sum_path1), "sign_final": np.sign(final_score),
                         "mismatch": np.sign(sum_path1) != np.sign(final_score) and sum_path1 != 0 and final_score != 0})
    sign_df = pd.DataFrame(sign_rows)
    sign_df.to_csv(DOCS_DIR / "diag-3P-sign-check-today.csv", index=False)

    # window-wide sign-mismatch rate
    window_sign_rows = []
    for s in win["snapshots"]:
        t = per_snapshot_table(s)
        inst_by_sym_w = {i["symbol"]: i for i in s["instruments"]}
        for sym in FX_PAIRS:
            sub = t[t.symbol == sym]
            sum_path1 = sub["path1_displayed"].fillna(0).sum()
            final_score = inst_by_sym_w[sym]["score"]
            mismatch = (np.sign(sum_path1) != np.sign(final_score)) and sum_path1 != 0 and final_score != 0
            window_sign_rows.append({"as_of": s["as_of"], "symbol": sym, "mismatch": mismatch})
    window_sign_df = pd.DataFrame(window_sign_rows)
    window_sign_df.to_csv(DOCS_DIR / "diag-3P-sign-check-window.csv", index=False)

    return {
        "linearity_check": lin, "max_abs_diff_linearity": max_abs_diff,
        "n_diverging": n_diverging, "n_total": n_total,
        "delta_stats": diverging["delta"].describe(),
        "ratio_value_counts": ratio_rounded.value_counts().head(15),
        "nzdusd_monetary_mult_base": nzdusd_monetary_mult_base,
        "nzdusd_monetary_mult_quote": nzdusd_monetary_mult_quote,
        "aud_monetary_mult": aud_monetary_mult,
        "sign_df_today": sign_df,
        "window_sign_mismatch_rate": window_sign_df["mismatch"].mean() * 100,
        "window_sign_mismatch_by_symbol": window_sign_df.groupby("symbol")["mismatch"].mean().mul(100).sort_values(ascending=False),
    }


if __name__ == "__main__":
    res = run()
    print("=== linearity validation (sum of path3 contributions == macro_score) ===")
    print(f"max |diff| across 28 pairs, today: {res['max_abs_diff_linearity']:.2e}")

    print(f"\n=== N2-style comparison, path1 (displayed) vs path3 (real marginal contribution) ===")
    print(f"diverging cells: {res['n_diverging']}/{res['n_total']}")
    print("\ndelta stats (path1 - path3), diverging cells:")
    print(res["delta_stats"].to_string())
    print("\nratio (path1/path3) value counts, top 15:")
    print(res["ratio_value_counts"].to_string())

    print("\n=== exclusion check ===")
    print(f"NZDUSD monetary: NZD(base) multiplier = {res['nzdusd_monetary_mult_base']} (0 = excluded, structurally absent)")
    print(f"NZDUSD monetary: USD(quote) multiplier = {res['nzdusd_monetary_mult_quote']} (nonzero = live, included)")
    print(f"AUD monetary multiplier (stale) = {res['aud_monetary_mult']} (0 = excluded)")

    print("\n=== sign check, today ===")
    print(res["sign_df_today"].to_string(index=False))
    print(f"\nwindow-wide sign-mismatch rate: {res['window_sign_mismatch_rate']:.1f}%")
    print(res["window_sign_mismatch_by_symbol"].to_string())
