"""Section O — counterfactual: restoring the `monetary` category.

O1: local-only search for a recoverable NZ/CH 2y series (no network).
O2: sweep the injected monetary cell X in {-2..+2} for NZD and CHF, recompute
    every pair containing that currency (full sentiment+trend fold reused from
    src.economic_compute), report new index/score/label + flips both ways.
O3: count/list FX pairs comparing a 3-category index against a 4-category one,
    TODAY.

READ-ONLY. All sweeps mutate a deepcopy of the currencies dict in memory only.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import _augmented_index, _leg_eff_wsum, _fold_trend, _clamp_cell, bias_label  # noqa: E402
from scripts.diag.analysis_common import CCYS, DOCS_DIR, FX_PAIRS, INSTRUMENTS_CFG, load_today  # noqa: E402

INST_CFG = INSTRUMENTS_CFG["instruments"]
SCALE = float(INSTRUMENTS_CFG["scale"])
PAIR_DIVISOR = float(INSTRUMENTS_CFG["pair_divisor"])
SENTIMENT_WEIGHT = float(INSTRUMENTS_CFG["sentiment_weight"])
TREND_WEIGHT = float(INSTRUMENTS_CFG["trend_weight"])
THRESHOLDS = INSTRUMENTS_CFG["bias_thresholds"]
MONETARY_WEIGHT = 1.0   # matches indicators_cfg.categories.monetary default weight (absent key -> 1.0)

AFFECTED_CCYS = ["NZD", "CHF"]   # the only two ABSENT (not just stale) per section M


def _leg_sentiment(ccy: str, fx_cells: dict):
    if ccy == "USD":
        return None
    return fx_cells.get(ccy)


def score_pair(symbol: str, currencies: dict, fx_cells: dict, trend_by_symbol: dict) -> tuple[float, str]:
    """Exact reuse of compute_instrument's fx-branch fold, over a (possibly
    patched) currencies dict."""
    cfg = INST_CFG[symbol]
    base, quote = cfg["base"], cfg["quote"]
    base_card, quote_card = currencies.get(base, {}), currencies.get(quote, {})

    v_s_base = _leg_sentiment(base, fx_cells)
    v_s_quote = _leg_sentiment(quote, fx_cells)
    base_idx = _augmented_index(base_card, v_s_base, SENTIMENT_WEIGHT, SCALE)
    quote_idx = _augmented_index(quote_card, v_s_quote, SENTIMENT_WEIGHT, SCALE)
    macro_score = (base_idx - quote_idx) / PAIR_DIVISOR

    macro_weight = (_leg_eff_wsum(base_card, v_s_base, SENTIMENT_WEIGHT)
                    + _leg_eff_wsum(quote_card, v_s_quote, SENTIMENT_WEIGHT)) / 2.0
    trend_value = trend_by_symbol.get(symbol)
    score = _fold_trend(macro_score, macro_weight, trend_value, TREND_WEIGHT, SCALE)
    return score, bias_label(score, THRESHOLDS)


def _inject_monetary(card: dict, x: int) -> dict:
    """Deepcopy `card` with categories['monetary'] injected at precise=x,
    coverage=1 (as if a fresh, non-stale rate_expectations reading existed),
    and index/index_num/index_wsum recomputed accordingly — mirrors exactly
    what compute_currency_scorecard does when rate_entry is present & not stale."""
    c = copy.deepcopy(card)
    old_num = c.get("index_num", 0.0)
    old_wsum = c.get("index_wsum", 0.0)
    new_num = old_num + x * MONETARY_WEIGHT
    new_wsum = old_wsum + MONETARY_WEIGHT
    c["index_num"] = new_num
    c["index_wsum"] = new_wsum
    c["index"] = (new_num / new_wsum) * SCALE if new_wsum else 0.0
    c["categories"] = dict(c.get("categories", {}))
    c["categories"]["monetary"] = {"score_cell": _clamp_cell(float(x)), "score_precise": float(x), "coverage": 1}
    return c


def run() -> dict:
    today = load_today()["snapshot"]
    currencies = today["currencies"]
    fx_cells = today["fx_cot_cells"]
    trend_by_symbol = today["trend_by_symbol"]
    inst_by_sym = {i["symbol"]: i for i in today["instruments"]}

    rows = []
    for ccy in AFFECTED_CCYS:
        pairs = [s for s in FX_PAIRS if INST_CFG[s]["base"] == ccy or INST_CFG[s]["quote"] == ccy]
        for x in range(-2, 3):
            patched_currencies = dict(currencies)
            patched_currencies[ccy] = _inject_monetary(currencies[ccy], x)
            new_index = patched_currencies[ccy]["index"]
            for sym in pairs:
                cur_inst = inst_by_sym[sym]
                new_score, new_label = score_pair(sym, patched_currencies, fx_cells, trend_by_symbol)
                rows.append({
                    "ccy": ccy, "x": x, "new_ccy_index": new_index,
                    "symbol": sym, "current_score": cur_inst["score"], "current_label": cur_inst["bias"],
                    "new_score": new_score, "new_label": new_label,
                    "flipped": new_label != cur_inst["bias"],
                })
    df = pd.DataFrame(rows)
    df.to_csv(DOCS_DIR / "diag-O2-monetary-sweep.csv", index=False)

    flips_summary = (
        df[df["flipped"]].groupby(["ccy", "x"])["symbol"]
        .apply(lambda s: ", ".join(sorted(s))).reset_index()
        .rename(columns={"symbol": "flipped_pairs"})
    )
    flips_summary.to_csv(DOCS_DIR / "diag-O2-flips-summary.csv", index=False)

    # O3: 3-category vs 4-category index comparisons, TODAY.
    n_cats = {ccy: currencies[ccy].get("index_wsum", 0.0) for ccy in CCYS}
    # index_wsum counts category WEIGHT, all category weights are 1.0 here so
    # it equals the count of present (non-stale) categories.
    o3_rows = []
    for sym in FX_PAIRS:
        cfg = INST_CFG[sym]
        base, quote = cfg["base"], cfg["quote"]
        nb, nq = n_cats.get(base, 0), n_cats.get(quote, 0)
        if nb != nq:
            o3_rows.append({"symbol": sym, "base": base, "base_n_categories": nb,
                           "quote": quote, "quote_n_categories": nq})
    o3_df = pd.DataFrame(o3_rows)
    o3_df.to_csv(DOCS_DIR / "diag-O3-mismatched-category-count-pairs.csv", index=False)

    return {"sweep_df": df, "flips_summary": flips_summary, "n_cats": n_cats,
            "o3_df": o3_df, "n_o3": len(o3_df)}


if __name__ == "__main__":
    res = run()
    print("category count per currency (today):", res["n_cats"])
    print(f"\nO3: {res['n_o3']}/28 FX pairs compare mismatched category counts:")
    print(res["o3_df"].to_string(index=False))
    print("\nO2 flips summary (which pairs flip bias at which X):")
    print(res["flips_summary"].to_string(index=False))
    print(f"\ntotal sweep rows: {len(res['sweep_df'])}")
