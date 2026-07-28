"""§N — full extent of the path-1 (dense table, _build_indicator_cells) vs
path-2 (_category_cells, inst["categories"]) divergence, across all FX pairs
and the 53-week window.

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
from scripts.diag.analysis_common import (  # noqa: E402
    CATS, DOCS_DIR, FX_PAIRS, INSTRUMENTS_CFG, _ALL_INDICATORS, load_today, load_window,
)

INST_CFG = INSTRUMENTS_CFG["instruments"]
PAIR_DIVISOR = float(INSTRUMENTS_CFG["pair_divisor"])
CATEGORIES_DISPLAY = INSTRUMENTS_CFG.get("categories_display", [])
KEY_TO_CATEGORY = {k: v["category"] for k, v in _ALL_INDICATORS.items()}
KEY_TO_CATEGORY["rate_expectations"] = "monetary"


def _leg_presence(eb, eq) -> str:
    if eb is not None and eq is not None:
        return "both"
    if eb is not None and eq is None:
        return "base_only"
    if eb is None and eq is not None:
        return "quote_only"
    return "neither"


def path1_indicator_cells(snapshot: dict) -> pd.DataFrame:
    """Path 1: src.economic_render._build_indicator_cells logic, one row per
    (pair, indicator_key)."""
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
            value = None if presence == "neither" else (eb["score"] if eb else 0) - (eq["score"] if eq else 0)
            rows.append({"symbol": sym, "indicator_key": key, "category": KEY_TO_CATEGORY[key],
                        "path1_leg_presence": presence, "path1_value": value})
    return pd.DataFrame(rows)


def path2_category_cells(snapshot: dict) -> pd.DataFrame:
    """Path 2: src.economic_compute._category_cells logic, one row per
    (pair, category) — inst["categories"] as actually computed in production."""
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
            presence = _leg_presence(base_cat if (base_cat and (base_cat.get("coverage") or 0) > 0) else None,
                                     quote_cat if (quote_cat and (quote_cat.get("coverage") or 0) > 0) else None)
            rows.append({"symbol": sym, "category": cat, "path2_leg_presence": presence,
                        "path2_precise": cell["score_precise"], "path2_cell": cell["score_cell"]})
    return pd.DataFrame(rows)


def combined_divergence_table(snapshot: dict) -> pd.DataFrame:
    """Per (pair, category): path2's value vs an aggregated path-1 quantity.
    For `monetary` (exactly one indicator: rate_expectations) path1_agg IS
    path1's raw value directly — the TRUE apples-to-apples comparison from
    report 2. For growth/inflation/labour (multiple indicators per category)
    there is no single path-1 value to compare 1:1; path1_agg here is
    SUM(path1 per-indicator values)/pair_divisor — a constructed, explicitly
    labeled aggregate, not a claim that these are "the same computation"."""
    p1 = path1_indicator_cells(snapshot)
    p2 = path2_category_cells(snapshot)

    agg_rows = []
    for sym in FX_PAIRS:
        for cat in CATEGORIES_DISPLAY:
            sub1 = p1[(p1.symbol == sym) & (p1.category == cat)]
            if cat == "monetary":
                # exactly one indicator (rate_expectations) -> direct comparison
                row = sub1.iloc[0]
                path1_agg = row["path1_value"]
                path1_presence = row["path1_leg_presence"]
                comparable = "direct (single indicator)"
            else:
                vals = sub1["path1_value"].dropna()
                path1_agg = (vals.sum() / PAIR_DIVISOR) if len(vals) else None
                presences = set(sub1["path1_leg_presence"])
                path1_presence = "mixed" if len(presences) > 1 else next(iter(presences), "neither")
                comparable = "constructed aggregate (sum/pair_divisor)"
            agg_rows.append({"symbol": sym, "category": cat, "path1_agg": path1_agg,
                            "path1_presence": path1_presence, "comparability": comparable})
    agg = pd.DataFrame(agg_rows)

    out = agg.merge(p2, on=["symbol", "category"], how="left")
    out["delta"] = out["path1_agg"] - out["path2_precise"]
    out["both_rendered_in_ui"] = False  # path 2 (inst.categories) is not read by static/economic-chart.js (N3/N1)
    out["path1_rendered_in_ui"] = out["category"] == "monetary"  # only rate_expectations is a TABLE_COLUMN_KEYS entry
    return out


def run() -> dict:
    today = load_today()["snapshot"]
    win = load_window()

    today_table = combined_divergence_table(today)
    today_table.to_csv(DOCS_DIR / "diag-N2-divergence-today.csv", index=False)

    monetary_today = today_table[today_table.category == "monetary"]
    n_diverge_today = int((monetary_today["delta"].abs() > 1e-9).sum())

    window_rows = []
    for s in win["snapshots"]:
        t = combined_divergence_table(s)
        t["as_of"] = s["as_of"]
        window_rows.append(t)
    window_table = pd.concat(window_rows, ignore_index=True)
    window_table.to_csv(DOCS_DIR / "diag-N2-divergence-window.csv", index=False)

    monetary_window = window_table[window_table.category == "monetary"].copy()
    monetary_window["diverges"] = monetary_window["delta"].abs() > 1e-9
    by_day = monetary_window.groupby("as_of")["diverges"].mean() * 100
    by_day_df = by_day.reset_index().rename(columns={"diverges": "pct_diverging_pairs"})
    by_day_df.to_csv(DOCS_DIR / "diag-N2-divergence-by-day-monetary.csv", index=False)

    # systematic-vs-case-dependent: sign of delta among diverging monetary cells
    diverging = monetary_window[monetary_window["diverges"]]
    sign_counts = diverging["delta"].apply(lambda d: "path1>path2" if d > 0 else "path1<path2").value_counts()

    return {
        "today_table": today_table,
        "monetary_today": monetary_today,
        "n_diverge_today_monetary": n_diverge_today,
        "n_pairs_total": len(monetary_today),
        "window_table": window_table,
        "by_day_pct": by_day_df,
        "sign_counts": sign_counts,
        "delta_abs_stats": diverging["delta"].abs().describe(),
    }


if __name__ == "__main__":
    res = run()
    print("=== MONETARY, today (the true apples-to-apples comparison) ===")
    print(res["monetary_today"][["symbol", "path1_agg", "path2_precise", "delta",
                                 "path1_presence", "path2_leg_presence"]].to_string(index=False))
    print(f"\ndiverging pairs today: {res['n_diverge_today_monetary']}/{res['n_pairs_total']}")
    print("\nsign of divergence (path1 vs path2), all diverging monetary cells, window-wide:")
    print(res["sign_counts"].to_string())
    print("\n|delta| distribution, window-wide (monetary):")
    print(res["delta_abs_stats"].to_string())
    print("\n% of pairs diverging (monetary), by day:")
    print(res["by_day_pct"].to_string(index=False))
