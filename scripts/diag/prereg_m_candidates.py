"""D1 x D2-c candidate scoring — the §5 pre-registered measurement engine.

Implements, for each snapshot (as produced by scripts/diag/engine.py), the
four surviving D1 candidates (C, D, E, NULL — A/B eliminated under CN-1),
each combined with D2-c on BOTH folds:

  SENTIMENT (per leg, pre-differencing):
    leg = (mean(present_or_intersected_categories) + w_s * sentiment) / (1 + w_s)
    w_s = 0.125  (constant effective weight 11.11% for every currency)

  TREND (per pair, post-differencing):
    new_mean = (m * W + trend_weight * trend_value) / (W + trend_weight)
    W = macro_weight_target = 4.371794871794871  (constant, replaces the
        per-pair-varying macro_weight)
    trend_weight = 0.5 (unchanged — only the denominator is fixed)

D1 candidates differ ONLY in which categories feed `mean(...)` above:
  NULL/E — mean over each currency's OWN present (non-stale) categories,
           exactly today's production FUND computation (E and NULL are
           NUMERICALLY IDENTICAL — they differ only in a display flag that
           these measurements cannot see; see the report for why).
  D      — mean over the INTERSECTION of categories present on BOTH legs of
           the pair (a leg's own extra category, e.g. USD's monetary against
           NZD, is discarded for that specific pair).
  C      — a pair is scored ONLY IF both legs have all 4 categories present;
           otherwise the pair is `insufficient_coverage` (no score at all).

READ-ONLY. Nothing here writes to data/ or config/.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import INSTRUMENTS_CFG  # noqa: E402

INST_CFG = INSTRUMENTS_CFG["instruments"]
SCALE = float(INSTRUMENTS_CFG["scale"])
PAIR_DIVISOR = float(INSTRUMENTS_CFG["pair_divisor"])
THRESHOLDS = INSTRUMENTS_CFG["bias_thresholds"]   # PA-4: mild/very NOT touched this cycle

W_S = 0.125                              # D2-c sentiment constant
TREND_WEIGHT = 0.5                       # unchanged
MACRO_WEIGHT_TARGET = 4.371794871794871  # D2-c trend constant (signed, prereg §13.3)

ALL_CATS = ("growth", "inflation", "labour", "monetary")


def bias_label(score: float) -> str:
    very = float(THRESHOLDS["very"])
    mild = float(THRESHOLDS["mild"])
    a = abs(score)
    if a < mild:
        return "Neutral"
    bull = score > 0
    if a >= very:
        return "Very Bullish" if bull else "Very Bearish"
    return "Bullish" if bull else "Bearish"


def present_categories(card: dict) -> dict:
    """{category: score_precise} for categories with coverage>0 — this single
    filter already treats structural absence (no key at all, e.g. NZD/CHF
    monetary) and staleness (key present, coverage=0) identically, exactly as
    verified in prereg §13.2."""
    out = {}
    for cat, cell in (card.get("categories") or {}).items():
        if (cell.get("coverage") or 0) > 0:
            out[cat] = cell["score_precise"]
    return out


def _augmented_leg_mean(leg_mean: float, v_s) -> float:
    if v_s is None or leg_mean is None:
        return leg_mean
    return (leg_mean + W_S * v_s) / (1.0 + W_S)


def _leg_sentiment(ccy: str, fx_cells: dict):
    if ccy == "USD":
        return None
    return fx_cells.get(ccy)


def score_pair(symbol: str, snapshot: dict, d1: str) -> dict:
    """Return {score, bias, coverage_ok, base_present, quote_present} for one
    pair under one D1 candidate (+ D2-c on both folds), or coverage_ok=False
    with score=None if `d1='C'` excludes it."""
    cfg = INST_CFG[symbol]
    base, quote = cfg["base"], cfg["quote"]
    currencies = snapshot["currencies"]
    base_card, quote_card = currencies.get(base, {}), currencies.get(quote, {})
    fx_cells = snapshot["fx_cot_cells"]
    trend_value = snapshot["trend_by_symbol"].get(symbol)

    base_present = present_categories(base_card)
    quote_present = present_categories(quote_card)

    if d1 in ("null", "e"):
        base_mean = sum(base_present.values()) / len(base_present) if base_present else None
        quote_mean = sum(quote_present.values()) / len(quote_present) if quote_present else None
    elif d1 == "d":
        inter = set(base_present) & set(quote_present)
        if not inter:
            return {"score": None, "bias": None, "coverage_ok": False}
        base_mean = sum(base_present[c] for c in inter) / len(inter)
        quote_mean = sum(quote_present[c] for c in inter) / len(inter)
    elif d1 == "c":
        if len(base_present) < 4 or len(quote_present) < 4:
            return {"score": None, "bias": None, "coverage_ok": False}
        base_mean = sum(base_present.values()) / len(base_present)
        quote_mean = sum(quote_present.values()) / len(quote_present)
    else:
        raise ValueError(d1)

    if base_mean is None or quote_mean is None:
        return {"score": None, "bias": None, "coverage_ok": False}

    v_s_base = _leg_sentiment(base, fx_cells)
    v_s_quote = _leg_sentiment(quote, fx_cells)
    base_idx_aug = _augmented_leg_mean(base_mean, v_s_base) * SCALE
    quote_idx_aug = _augmented_leg_mean(quote_mean, v_s_quote) * SCALE
    macro_score = (base_idx_aug - quote_idx_aug) / PAIR_DIVISOR

    if trend_value is None:
        score = macro_score
    else:
        m = macro_score / SCALE
        new_mean = (m * MACRO_WEIGHT_TARGET + TREND_WEIGHT * trend_value) / (MACRO_WEIGHT_TARGET + TREND_WEIGHT)
        score = new_mean * SCALE

    return {"score": score, "bias": bias_label(score), "coverage_ok": True,
            "base_present": set(base_present), "quote_present": set(quote_present)}


def effective_sentiment_weight(card: dict, fx_cells: dict, ccy: str) -> float:
    """Effective sentiment weight for a currency leg under D2-c: constant
    W_S/(1+W_S) whenever sentiment is present, independent of category count."""
    v_s = _leg_sentiment(ccy, fx_cells)
    if v_s is None:
        return None
    return W_S / (1.0 + W_S)


def effective_trend_weight(symbol: str, snapshot: dict) -> float:
    trend_value = snapshot["trend_by_symbol"].get(symbol)
    if trend_value is None:
        return None
    return TREND_WEIGHT / (MACRO_WEIGHT_TARGET + TREND_WEIGHT)
