"""Section I — counterfactual V2: pillar-conflict classification of "Neutral".

For every FX pair currently labeled Neutral, decompose the final score into its
three additive pillar contributions (same units, sum exactly to the final
score):
  FUND      = stage2 (raw category-index differencing, no sentiment/trend)
  SENTIMENT = stage3 - stage2  (incremental effect of the sentiment fold)
  TREND     = stage4 - stage3  (incremental effect of the trend fold)
Then classify:
  CONFLICT — >=2 pillars with opposite sign AND |value| >= 2 each
  SILENT   — >=50% of the pair's applicable (currency,indicator) cells are
             no_data (structurally missing, not scored)
  BALANCED — everything else (pillars present, individually small / cancel)

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import _augmented_index, _leg_eff_wsum, _fold_trend  # noqa: E402
from scripts.diag.analysis_common import (  # noqa: E402
    DOCS_DIR, FX_PAIRS, INSTRUMENTS_CFG, applicable_indicators_for, load_today, load_window,
)

INST_CFG = INSTRUMENTS_CFG["instruments"]
SCALE = float(INSTRUMENTS_CFG.get("scale", 5))
PAIR_DIVISOR = float(INSTRUMENTS_CFG.get("pair_divisor", 2))
SENTIMENT_WEIGHT = float(INSTRUMENTS_CFG.get("sentiment_weight", 0.5))
TREND_WEIGHT = float(INSTRUMENTS_CFG.get("trend_weight", 0.5))
CONFLICT_MAG = 2.0
SILENT_NODATA_FRAC = 0.5


def _leg_sentiment(ccy: str, fx_cells: dict) -> int | None:
    if ccy == "USD":
        return None
    return fx_cells.get(ccy)


def _no_data_fraction(base: str, quote: str, currencies: dict) -> float:
    total, no_data = 0, 0
    for ccy in (base, quote):
        card = currencies.get(ccy, {}) or {}
        bdn = card.get("breakdown", {}) or {}
        for key in applicable_indicators_for(ccy) + ["rate_expectations"]:
            total += 1
            if bdn.get(key) is None:
                no_data += 1
    return (no_data / total) if total else 1.0


def decompose_all_pairs(snapshot: dict) -> pd.DataFrame:
    currencies = snapshot["currencies"]
    fx_cells = snapshot["fx_cot_cells"]
    trend_by_symbol = snapshot["trend_by_symbol"]
    inst_by_sym = {i["symbol"]: i for i in snapshot["instruments"]}

    rows = []
    for sym in FX_PAIRS:
        cfg = INST_CFG[sym]
        base, quote = cfg["base"], cfg["quote"]
        base_card, quote_card = currencies.get(base, {}), currencies.get(quote, {})

        v_s_base = _leg_sentiment(base, fx_cells)
        v_s_quote = _leg_sentiment(quote, fx_cells)

        base_idx_raw = base_card.get("index", 0.0)
        quote_idx_raw = quote_card.get("index", 0.0)
        fund = (base_idx_raw - quote_idx_raw) / PAIR_DIVISOR

        base_idx_aug = _augmented_index(base_card, v_s_base, SENTIMENT_WEIGHT, SCALE) if base_card else 0.0
        quote_idx_aug = _augmented_index(quote_card, v_s_quote, SENTIMENT_WEIGHT, SCALE) if quote_card else 0.0
        stage3 = (base_idx_aug - quote_idx_aug) / PAIR_DIVISOR
        sentiment = stage3 - fund

        macro_weight = (_leg_eff_wsum(base_card, v_s_base, SENTIMENT_WEIGHT)
                        + _leg_eff_wsum(quote_card, v_s_quote, SENTIMENT_WEIGHT)) / 2.0
        trend_value = trend_by_symbol.get(sym)
        stage4 = _fold_trend(stage3, macro_weight, trend_value, TREND_WEIGHT, SCALE)
        trend = stage4 - stage3

        inst = inst_by_sym.get(sym)
        if inst is None:
            continue

        no_data_frac = _no_data_fraction(base, quote, currencies)

        pillars = {"FUND": fund, "SENTIMENT": sentiment, "TREND": trend}
        signs = {k: (1 if v > 1e-9 else (-1 if v < -1e-9 else 0)) for k, v in pillars.items()}
        big = {k: v for k, v in pillars.items() if abs(v) >= CONFLICT_MAG}
        conflict = False
        big_keys = list(big.keys())
        for a in range(len(big_keys)):
            for b in range(a + 1, len(big_keys)):
                if signs[big_keys[a]] != 0 and signs[big_keys[a]] == -signs[big_keys[b]]:
                    conflict = True

        if inst["bias"] == "Neutral":
            if conflict:
                cls = "CONFLICT"
            elif no_data_frac >= SILENT_NODATA_FRAC:
                cls = "SILENT"
            else:
                cls = "BALANCED"
        else:
            cls = "n/a (not Neutral)"

        rows.append({"as_of": snapshot["as_of"], "symbol": sym, "bias": inst["bias"],
                    "score": inst["score"], "fund": fund, "sentiment": sentiment, "trend": trend,
                    "no_data_frac": no_data_frac, "classification": cls})
    return pd.DataFrame(rows)


def run() -> dict:
    today = load_today()["snapshot"]
    win = load_window()

    today_df = decompose_all_pairs(today)
    today_df.to_csv(DOCS_DIR / "diag-I-pillar-classification-today.csv", index=False)

    window_df = pd.concat([decompose_all_pairs(s) for s in win["snapshots"]], ignore_index=True)
    window_df.to_csv(DOCS_DIR / "diag-I-pillar-classification-window.csv", index=False)

    today_neutral = today_df[today_df["bias"] == "Neutral"]
    window_neutral = window_df[window_df["bias"] == "Neutral"]

    today_counts = today_neutral["classification"].value_counts()
    window_counts = window_neutral["classification"].value_counts()
    window_pct = (window_counts / window_counts.sum() * 100).round(1)

    return {
        "today_counts": today_counts, "window_counts": window_counts, "window_pct": window_pct,
        "n_neutral_today": len(today_neutral), "n_neutral_window": len(window_neutral),
        "today_df": today_df,
    }


if __name__ == "__main__":
    res = run()
    print(f"Neutral today: n={res['n_neutral_today']}")
    print(res["today_counts"].to_string())
    print(f"\nNeutral window-wide: n={res['n_neutral_window']}")
    print(res["window_counts"].to_string())
    print("\n(%):")
    print(res["window_pct"].to_string())
