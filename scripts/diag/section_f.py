"""Section F — full forensic decomposition, current day: NZDUSD (required),
GBPUSD, AUDUSD, EURUSD.

READ-ONLY. Reuses src.economic_compute's own private helpers (_augmented_index,
_leg_eff_wsum, _fold_trend, bias_label) so every intermediate number is
IDENTICAL to what compute_instrument actually produced — not a re-derivation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import _augmented_index, _leg_eff_wsum, _fold_trend, bias_label  # noqa: E402
from scripts.diag.analysis_common import CATS, DOCS_DIR, INSTRUMENTS_CFG, load_today  # noqa: E402

INST_CFG = INSTRUMENTS_CFG["instruments"]
SCALE = float(INSTRUMENTS_CFG.get("scale", 5))
PAIR_DIVISOR = float(INSTRUMENTS_CFG.get("pair_divisor", 2))
SENTIMENT_WEIGHT = float(INSTRUMENTS_CFG.get("sentiment_weight", 0.5))
TREND_WEIGHT = float(INSTRUMENTS_CFG.get("trend_weight", 0.5))
THRESHOLDS = INSTRUMENTS_CFG.get("bias_thresholds", {})

PAIRS = ["NZDUSD", "GBPUSD", "AUDUSD", "EURUSD"]


def _leg_sentiment(ccy: str, in_pair: bool, fx_cells: dict) -> int | None:
    if ccy == "USD":
        return None if in_pair else fx_cells.get("DXY")
    return fx_cells.get(ccy)


def _indicator_rows(card: dict) -> list[dict]:
    rows = []
    for key, e in (card.get("breakdown") or {}).items():
        rows.append({
            "indicator_key": key,
            "actual": e.get("actual"), "consensus": e.get("consensus"),
            "surprise": e.get("surprise"), "z": e.get("z"),
            "score": e.get("score"), "flag": e.get("flag"), "stale": e.get("stale"),
        })
    return rows


def decompose_pair(symbol: str, snapshot: dict) -> dict:
    cfg = INST_CFG[symbol]
    base, quote = cfg["base"], cfg["quote"]
    currencies = snapshot["currencies"]
    base_card, quote_card = currencies.get(base, {}), currencies.get(quote, {})
    fx_cells = snapshot["fx_cot_cells"]

    v_s_base = _leg_sentiment(base, True, fx_cells)
    v_s_quote = _leg_sentiment(quote, True, fx_cells)

    base_idx_raw = base_card.get("index", 0.0)
    quote_idx_raw = quote_card.get("index", 0.0)
    stage2_macro_no_sentiment = (base_idx_raw - quote_idx_raw) / PAIR_DIVISOR

    base_idx_aug = _augmented_index(base_card, v_s_base, SENTIMENT_WEIGHT, SCALE) if base_card else 0.0
    quote_idx_aug = _augmented_index(quote_card, v_s_quote, SENTIMENT_WEIGHT, SCALE) if quote_card else 0.0
    stage3_macro_with_sentiment = (base_idx_aug - quote_idx_aug) / PAIR_DIVISOR

    macro_weight = (_leg_eff_wsum(base_card, v_s_base, SENTIMENT_WEIGHT)
                    + _leg_eff_wsum(quote_card, v_s_quote, SENTIMENT_WEIGHT)) / 2.0
    trend_value = snapshot["trend_by_symbol"].get(symbol)
    stage4_final = _fold_trend(stage3_macro_with_sentiment, macro_weight, trend_value, TREND_WEIGHT, SCALE)

    inst = next(i for i in snapshot["instruments"] if i["symbol"] == symbol)
    assert abs(inst["score"] - stage4_final) < 1e-6, f"{symbol}: reconstruction mismatch {inst['score']} vs {stage4_final}"

    def sign(v):
        return 0 if v == 0 else (1 if v > 0 else -1)

    stages = [("A: category diff (raw index, no sentiment)", stage2_macro_no_sentiment),
              ("B: + SENTIMENT fold (pre-differencing)", stage3_macro_with_sentiment),
              ("C: + TREND fold (final score)", stage4_final)]
    flips = []
    for (name_a, va), (name_b, vb) in zip(stages, stages[1:]):
        if sign(va) != 0 and sign(vb) != 0 and sign(va) != sign(vb):
            flips.append(f"{name_a} -> {name_b}")

    return {
        "symbol": symbol, "base": base, "quote": quote,
        "base_indicators": _indicator_rows(base_card),
        "quote_indicators": _indicator_rows(quote_card),
        "base_categories": base_card.get("categories", {}),
        "quote_categories": quote_card.get("categories", {}),
        "base_index": base_idx_raw, "quote_index": quote_idx_raw,
        "base_sentiment_value": v_s_base, "quote_sentiment_value": v_s_quote,
        "base_index_augmented": base_idx_aug, "quote_index_augmented": quote_idx_aug,
        "stage2_macro_no_sentiment": stage2_macro_no_sentiment,
        "stage3_macro_with_sentiment": stage3_macro_with_sentiment,
        "trend_value": trend_value, "trend_weight": TREND_WEIGHT, "macro_weight": macro_weight,
        "stage4_final": stage4_final,
        "final_label": inst["bias"],
        "sign_flips": flips,
    }


def run() -> dict:
    today = load_today()["snapshot"]
    results = {sym: decompose_pair(sym, today) for sym in PAIRS}

    rows = []
    for sym, r in results.items():
        for leg, ind_rows in [("base", r["base_indicators"]), ("quote", r["quote_indicators"])]:
            ccy = r["base"] if leg == "base" else r["quote"]
            for ir in ind_rows:
                rows.append({"symbol": sym, "leg": leg, "ccy": ccy, **ir})
    pd.DataFrame(rows).to_csv(DOCS_DIR / "diag-F-forensic-indicators.csv", index=False)

    summary_rows = []
    for sym, r in results.items():
        summary_rows.append({
            "symbol": sym, "base": r["base"], "quote": r["quote"],
            "base_index": r["base_index"], "quote_index": r["quote_index"],
            "base_sentiment": r["base_sentiment_value"], "quote_sentiment": r["quote_sentiment_value"],
            "stage2_no_sentiment": r["stage2_macro_no_sentiment"],
            "stage3_with_sentiment": r["stage3_macro_with_sentiment"],
            "trend_value": r["trend_value"], "stage4_final": r["stage4_final"],
            "final_label": r["final_label"], "sign_flips": "; ".join(r["sign_flips"]) or "none",
        })
    pd.DataFrame(summary_rows).to_csv(DOCS_DIR / "diag-F-forensic-summary.csv", index=False)

    return results


if __name__ == "__main__":
    res = run()
    for sym, r in res.items():
        print(f"\n=== {sym} ({r['base']}/{r['quote']}) ===")
        print(f"  base_index={r['base_index']:.3f}  quote_index={r['quote_index']:.3f}")
        print(f"  sentiment: base={r['base_sentiment_value']} quote={r['quote_sentiment_value']}")
        print(f"  stage2 (no sentiment)  = {r['stage2_macro_no_sentiment']:.3f}")
        print(f"  stage3 (+ sentiment)   = {r['stage3_macro_with_sentiment']:.3f}")
        print(f"  trend_value={r['trend_value']}  stage4 (final) = {r['stage4_final']:.3f}  -> {r['final_label']}")
        print(f"  sign flips: {r['sign_flips'] or 'none'}")
