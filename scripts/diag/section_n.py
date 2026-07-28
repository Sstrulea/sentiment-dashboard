"""Section N — bilateral-cell propagation when only one leg has data.

Two DIFFERENT bilateral-cell computations exist for conceptually the same
base-quote relationship, and they disagree:
  1. src.economic_render._build_indicator_cells (the dense per-indicator table,
     e.g. the "Rate Exp (2y)" column): v = (eb.score if eb else 0) - (eq.score
     if eq else 0)  — RAW difference, NOT divided by pair_divisor.
  2. src.economic_compute._category_cells (the FX pair's `categories` dict,
     e.g. inst["categories"]["monetary"]): precise = (base_precise - quote_precise)
     / pair_divisor, where a missing leg's `categories.get(cat, {})` silently
     defaults score_precise to 0.0 via `.get(..., 0.0)`.

Both silently treat "leg absent" as "leg = 0" (no distinct code path), and
NEITHER carries a visual flag for it (only `stale` and `no_consensus` have
badges in static/economic-chart.js).

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import _category_cells  # noqa: E402
from src.economic_render import TABLE_COLUMN_KEYS  # noqa: E402
from scripts.diag.analysis_common import DOCS_DIR, FX_PAIRS, INSTRUMENTS_CFG, load_today  # noqa: E402

INST_CFG = INSTRUMENTS_CFG["instruments"]
PAIR_DIVISOR = float(INSTRUMENTS_CFG["pair_divisor"])
CATEGORIES_DISPLAY = INSTRUMENTS_CFG.get("categories_display", [])


def _leg_presence(eb, eq) -> str:
    if eb is not None and eq is not None:
        return "both"
    if eb is not None and eq is None:
        return "base_only"
    if eb is None and eq is not None:
        return "quote_only"
    return "neither"


def indicator_cell_table(snapshot: dict) -> pd.DataFrame:
    """N1/N3 — the dense per-indicator table (_build_indicator_cells logic)."""
    rows = []
    currencies = snapshot["currencies"]
    for sym in FX_PAIRS:
        cfg = INST_CFG[sym]
        base, quote = cfg["base"], cfg["quote"]
        bb = (currencies.get(base, {}) or {}).get("breakdown", {}) or {}
        bq = (currencies.get(quote, {}) or {}).get("breakdown", {}) or {}
        for key in TABLE_COLUMN_KEYS:
            eb, eq = bb.get(key), bq.get(key)
            presence = _leg_presence(eb, eq)
            if presence == "neither":
                value = None
            else:
                eb_score = eb["score"] if eb else 0
                eq_score = eq["score"] if eq else 0
                value = eb_score - eq_score   # RAW, NOT divided by pair_divisor
            rows.append({"symbol": sym, "column": key, "leg_presence": presence,
                        "base_score": eb["score"] if eb else None,
                        "quote_score": eq["score"] if eq else None,
                        "displayed_value": value})
    return pd.DataFrame(rows)


def category_cell_table(snapshot: dict) -> pd.DataFrame:
    """The FX pair's `categories` dict (_category_cells logic) — a SEPARATE
    bilateral computation from the dense table, for comparison."""
    rows = []
    currencies = snapshot["currencies"]
    for sym in FX_PAIRS:
        cfg = INST_CFG[sym]
        base, quote = cfg["base"], cfg["quote"]
        base_card, quote_card = currencies.get(base), currencies.get(quote)
        cells = _category_cells(cfg, base_card, quote_card, CATEGORIES_DISPLAY, PAIR_DIVISOR)
        for cat, cell in cells.items():
            base_cat = (base_card or {}).get("categories", {}).get(cat)
            quote_cat = (quote_card or {}).get("categories", {}).get(cat)
            rows.append({"symbol": sym, "category": cat,
                        "base_present": base_cat is not None, "quote_present": quote_cat is not None,
                        "score_precise": cell["score_precise"], "score_cell": cell["score_cell"],
                        "coverage": cell["coverage"]})
    return pd.DataFrame(rows)


def run() -> dict:
    today = load_today()["snapshot"]

    ind_df = indicator_cell_table(today)
    ind_df.to_csv(DOCS_DIR / "diag-N-indicator-cells-today.csv", index=False)

    cat_df = category_cell_table(today)
    cat_df.to_csv(DOCS_DIR / "diag-N-category-cells-today.csv", index=False)

    presence_counts = ind_df["leg_presence"].value_counts()
    single_leg = ind_df[ind_df["leg_presence"].isin(["base_only", "quote_only"])]
    single_leg_by_column = single_leg["column"].value_counts()

    # NZDUSD rate_expectations: the two DIFFERENT bilateral computations side by side.
    nzdusd_rate_exp = ind_df[(ind_df.symbol == "NZDUSD") & (ind_df.column == "rate_expectations")].iloc[0]
    nzdusd_monetary_cat = cat_df[(cat_df.symbol == "NZDUSD") & (cat_df.category == "monetary")].iloc[0]

    return {
        "ind_df": ind_df, "cat_df": cat_df,
        "presence_counts": presence_counts, "single_leg_by_column": single_leg_by_column,
        "n_single_leg_total": len(single_leg), "n_total_cells": len(ind_df),
        "nzdusd_rate_exp_indicator_cell": nzdusd_rate_exp.to_dict(),
        "nzdusd_monetary_category_cell": nzdusd_monetary_cat.to_dict(),
    }


if __name__ == "__main__":
    res = run()
    print("Leg-presence distribution (all FX pairs x all dense-table columns, today):")
    print(res["presence_counts"].to_string())
    print(f"\nsingle-leg cells: {res['n_single_leg_total']}/{res['n_total_cells']}")
    print("\nsingle-leg count by column:")
    print(res["single_leg_by_column"].to_string())
    print("\nNZDUSD 'Rate Exp (2y)' dense-table cell (_build_indicator_cells):")
    print(res["nzdusd_rate_exp_indicator_cell"])
    print("\nNZDUSD 'monetary' category cell (_category_cells) — a DIFFERENT computation:")
    print(res["nzdusd_monetary_category_cell"])
