"""Section L — arithmetic reconciliation trace, current day.

Traces the EXACT fold function (src.economic_compute._augmented_index,
_leg_eff_wsum, _fold_trend) with weights read from data/economic_instruments.yaml
AT RUNTIME (not from docs/comments), for NZDUSD and its "mirror" USDJPY.

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import _augmented_index, _leg_eff_wsum, _fold_trend, bias_label  # noqa: E402
from scripts.diag.analysis_common import DOCS_DIR, INSTRUMENTS_CFG, load_today  # noqa: E402

INST_CFG = INSTRUMENTS_CFG["instruments"]
# Weights read from the YAML at runtime (not hardcoded / not from comments).
SCALE = float(INSTRUMENTS_CFG["scale"])
PAIR_DIVISOR = float(INSTRUMENTS_CFG["pair_divisor"])
SENTIMENT_WEIGHT = float(INSTRUMENTS_CFG["sentiment_weight"])
TREND_WEIGHT = float(INSTRUMENTS_CFG["trend_weight"])
THRESHOLDS = INSTRUMENTS_CFG["bias_thresholds"]


def _leg_sentiment(ccy: str, fx_cells: dict) -> int | None:
    """Exact mirror of economic_compute.compute_instrument._leg_sentiment for a
    PAIR leg (in_pair=True): USD leg inside a pair is always excluded."""
    if ccy == "USD":
        return None
    return fx_cells.get(ccy)


def trace_pair(symbol: str, snapshot: dict) -> dict:
    cfg = INST_CFG[symbol]
    base, quote = cfg["base"], cfg["quote"]
    currencies = snapshot["currencies"]
    base_card, quote_card = currencies.get(base, {}), currencies.get(quote, {})
    fx_cells = snapshot["fx_cot_cells"]

    v_s_base = _leg_sentiment(base, fx_cells)
    v_s_quote = _leg_sentiment(quote, fx_cells)

    base_index_num = base_card.get("index_num", 0.0)
    base_index_wsum = base_card.get("index_wsum", 0.0)
    quote_index_num = quote_card.get("index_num", 0.0)
    quote_index_wsum = quote_card.get("index_wsum", 0.0)

    base_idx_raw = base_card.get("index", 0.0)     # card["index"] — displayed in the leg header
    quote_idx_raw = quote_card.get("index", 0.0)

    base_idx_aug = _augmented_index(base_card, v_s_base, SENTIMENT_WEIGHT, SCALE)
    quote_idx_aug = _augmented_index(quote_card, v_s_quote, SENTIMENT_WEIGHT, SCALE)

    macro_score = (base_idx_aug - quote_idx_aug) / PAIR_DIVISOR   # == compute_instrument's `macro_score`

    base_wsum_eff = _leg_eff_wsum(base_card, v_s_base, SENTIMENT_WEIGHT)
    quote_wsum_eff = _leg_eff_wsum(quote_card, v_s_quote, SENTIMENT_WEIGHT)
    macro_weight = (base_wsum_eff + quote_wsum_eff) / 2.0

    trend_value = snapshot["trend_by_symbol"].get(symbol)
    m = macro_score / SCALE if SCALE else 0.0
    if trend_value is None:
        new_mean, score = m, macro_score
    else:
        new_mean = (m * macro_weight + TREND_WEIGHT * trend_value) / (macro_weight + TREND_WEIGHT) \
            if macro_weight > 0 else float(trend_value)
        score = new_mean * SCALE

    inst = next(i for i in snapshot["instruments"] if i["symbol"] == symbol)
    label = bias_label(score, THRESHOLDS)

    # displayed COT pair cell (mirrors src.economic_render._attach_fx_cot_cells /
    # src.cot_score.pair_cot): base leg cell minus quote leg cell, USD leg = 0.
    def _own_leg_cell(ccy):
        return 0 if ccy == "USD" else fx_cells.get(ccy)
    base_cell, quote_cell = _own_leg_cell(base), _own_leg_cell(quote)
    displayed_cot_cell = (0 if base_cell is None else base_cell) - (0 if quote_cell is None else quote_cell)

    return {
        "symbol": symbol, "base": base, "quote": quote,
        "base_index_num": base_index_num, "base_index_wsum": base_index_wsum,
        "quote_index_num": quote_index_num, "quote_index_wsum": quote_index_wsum,
        "base_index_raw (card['index'], displayed in leg header)": base_idx_raw,
        "quote_index_raw (card['index'], displayed in leg header)": quote_idx_raw,
        "v_s_base (sentiment on base leg)": v_s_base,
        "v_s_quote (sentiment on quote leg)": v_s_quote,
        "base_idx_aug (_augmented_index)": base_idx_aug,
        "quote_idx_aug (_augmented_index)": quote_idx_aug,
        "macro_score = (base_idx_aug - quote_idx_aug)/pair_divisor": macro_score,
        "base_wsum_eff (_leg_eff_wsum)": base_wsum_eff,
        "quote_wsum_eff (_leg_eff_wsum)": quote_wsum_eff,
        "macro_weight (avg of leg eff wsums)": macro_weight,
        "trend_value": trend_value,
        "m = macro_score/scale (_fold_trend)": m,
        "new_mean = (m*w + trend_weight*trend)/(w+trend_weight)": new_mean,
        "score = new_mean*scale (== inst['score'])": score,
        "inst['score'] (production, from payload)": inst["score"],
        "reconciles (1e-6)": abs(score - inst["score"]) < 1e-6,
        "bias_label(score, thresholds)": label,
        "inst['bias'] (production)": inst["bias"],
        "displayed_cot_pair_cell (pair_cot(base_cell, quote_cell))": displayed_cot_cell,
        "table_display (fmtScoreInt = round(score))": round(inst["score"]),
        "modal_display (fmtSigned, 2dp)": f"{inst['score']:+.2f}",
    }


def run() -> dict:
    today = load_today()["snapshot"]
    results = {sym: trace_pair(sym, today) for sym in ["NZDUSD", "USDJPY"]}

    rows = []
    for sym, r in results.items():
        for k, v in r.items():
            rows.append({"symbol": sym, "step": k, "value": v})
    pd.DataFrame(rows).to_csv(DOCS_DIR / "diag-L-arithmetic-trace.csv", index=False)

    return results


if __name__ == "__main__":
    res = run()
    for sym, r in res.items():
        print(f"\n=== {sym} ({r['base']}/{r['quote']}) ===")
        for k, v in r.items():
            if k in ("symbol", "base", "quote"):
                continue
            print(f"  {k:65s} = {v}")
