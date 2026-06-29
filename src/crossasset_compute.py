"""Pure Cross-Asset scoring (indices + metals) — no I/O, no network.

Derives a −10..+10 bias score per instrument from TOP-LEVEL factors:
    {growth, inflation, labour, rates}
where each of growth/inflation/labour reads the instrument's HOME currency
category score (build_payload `score_cell`, −2..+2), and `rates` is a COMPOSITE:
the weighted mean of its present sub-components
    {rate_exp_2y (home-ccy monetary/2y), real_yield_10y (global US 10y real)}.

Grouping rates this way bounds the combined rate signal at ±2 (so aligned 2y +
10y don't stack to ±4 and dominate); divergent sub-components cancel toward 0.
It is also where a future `balance_sheet` sub-component slots in (Step 7).

Instrument score = weighted MEAN over the PRESENT top-level factors × scale
(mirrors the FX aggregation): a missing factor — or a missing rate sub-component
— is excluded gracefully, never zero-filled. Reuses the FX `bias_label`.

    compute_crossasset_scores(categories_by_ccy, realyield_score, config) -> dict
"""
from __future__ import annotations

import math
from typing import Any, Optional

from src.economic_compute import bias_label  # read-only reuse (not modified)

SIMPLE_FACTORS = ("growth", "inflation", "labour")
RATES_FACTOR = "rates"
# Display-positioning factor. Its value is the instrument's SENTIMENT cell (COT
# for metals ±4, P/C for US indices ±3), already in the "+ = bullish for the
# asset" convention. It enters the weighted mean exactly like a simple factor
# (no separate scale); a missing sentiment value → factor ABSENT (excluded).
SENTIMENT_FACTOR = "sentiment"
# Display-positioning factor. Its value is the instrument's TREND cell (±3 MA
# structure × ADX strength, from src.trend_score), already "+ = bullish for the
# asset". Enters the weighted mean exactly like SENTIMENT (weight 0.5, no
# separate scale); a missing trend value → factor ABSENT (excluded). Cross-asset
# trend is DIRECT on the instrument's own price series.
TREND_FACTOR = "trend"
# rate sub-component -> (source kind, key). "category" reads the home-ccy
# category cell; "realyield"/"liquidity" read a GLOBAL momentum score.
RATE_SUBCOMPONENTS = {
    "rate_exp_2y": ("category", "monetary"),   # home-ccy 2y rate expectations
    "real_yield_10y": ("realyield", None),     # global US 10y real yield
    "balance_sheet": ("liquidity", None),      # global Fed net liquidity (WALCL−TGA−RRP)
}


def _cell(v: Any) -> Optional[int]:
    """Coerce a category value to its int score_cell, or None if absent.

    Accepts a raw number or a build_payload category dict ({score_cell, coverage}).
    A dict with coverage == 0 is treated as ABSENT (no data), not a real 0.
    """
    if v is None:
        return None
    if isinstance(v, dict):
        if "coverage" in v and (v.get("coverage") or 0) == 0:
            return None
        c = v.get("score_cell")
        return None if c is None else int(c)
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _realyield_raw(ry: Any) -> Optional[int]:
    """Coerce a real-yield input to its int score, or None."""
    if ry is None:
        return None
    if isinstance(ry, (int, float)):
        return None if (isinstance(ry, float) and math.isnan(ry)) else int(ry)
    s = getattr(ry, "score", None)
    return None if s is None else int(s)


def _realyield_series(ry: Any) -> str:
    """Label for the real-yield sub-component's source (the series actually used)."""
    s = getattr(ry, "series", None)
    return str(s) if s else "DFII10"


def _realyield_stale(ry: Any) -> bool:
    """Whether the real-yield input is stale (excluded from the mean, kept visible)."""
    return bool(getattr(ry, "stale", False))


def _global_raw(g: Any) -> Optional[int]:
    """Coerce a global momentum input (RealYield/Liquidity score, number, None)
    to its int score, or None."""
    if g is None:
        return None
    if isinstance(g, (int, float)):
        return None if (isinstance(g, float) and math.isnan(g)) else int(g)
    s = getattr(g, "score", None)
    return None if s is None else int(s)


def _subcell_display(v: Any) -> tuple[Optional[int], bool, bool]:
    """For a category-sourced rate sub-component, return (raw_for_display, stale,
    present_for_mean). A build_payload monetary cell carries `stale` + coverage:
    when stale we keep its value for display but mark it NOT present (excluded
    from the mean) — mirroring the calendar stale policy. A genuine no-data cell
    (coverage 0, not stale) is absent entirely.
    """
    if v is None:
        return None, False, False
    if isinstance(v, dict):
        sc = v.get("score_cell")
        if sc is None:
            return None, False, False
        stale = bool(v.get("stale"))
        if (v.get("coverage") or 0) == 0 and not stale:
            return None, False, False
        return int(sc), stale, (not stale)
    if isinstance(v, float) and math.isnan(v):
        return None, False, False
    try:
        return int(v), False, True
    except (TypeError, ValueError):
        return None, False, False


def _rates_factor(rates_cfg: dict, cats: dict, home: str,
                  globals_by_kind: dict) -> tuple[Optional[float], list[dict]]:
    """Composite rates value = weighted mean of PRESENT (non-stale) sub-components
    (signed), bounded at ±2 regardless of how many components there are. Stale
    sub-components are kept in the rows for display (raw + stale flag) but excluded
    from the mean. `globals_by_kind` maps a non-category kind ("realyield",
    "liquidity") -> (raw|None, stale, source_label). Returns (value|None, rows)."""
    comps_cfg = rates_cfg.get("components", {}) or {}
    sub_rows: list[dict] = []
    num = 0.0
    wsum = 0.0
    for name, cc in comps_cfg.items():
        sign = float(cc.get("sign", 1))
        weight = float(cc.get("weight", 1.0))
        kind, key = RATE_SUBCOMPONENTS.get(name, ("category", name))
        if kind in globals_by_kind:
            raw, stale, source = globals_by_kind[kind]
            present = (raw is not None) and not stale
        else:
            raw, stale, present = _subcell_display(cats.get(key))
            source = f"{home} {key}"
        # Display contribution whenever a value exists (even if stale/excluded).
        contribution = (sign * weight * raw) if (raw is not None) else None
        if present:
            num += sign * weight * raw
            wsum += weight
        sub_rows.append({
            "name": name, "raw": raw, "sign": sign, "weight": weight,
            "contribution": None if contribution is None else float(contribution),
            "present": present, "stale": stale, "source": source,
        })
    rates_value = (num / wsum) if wsum > 0 else None
    return rates_value, sub_rows


def compute_instrument_score(
    symbol: str,
    inst_cfg: dict,
    categories_by_ccy: dict,
    realyield_raw: Optional[int],
    scale: float,
    thresholds: dict,
    realyield_label: str = "DFII10",
    realyield_stale: bool = False,
    liquidity_raw: Optional[int] = None,
    liquidity_label: str = "NET_LIQUIDITY",
    liquidity_stale: bool = False,
    sentiment_value: Optional[int] = None,
    trend_value: Optional[int] = None,
) -> dict:
    """Score one instrument: weighted mean over present top-level factors × scale.

    `sentiment_value` is the instrument's SENTIMENT cell (COT/P/C, already signed
    "+ = bullish"), or None when the instrument has no sentiment (e.g. foreign
    indices) — in which case the `sentiment` factor, if configured, is absent and
    the score is identical to the no-sentiment baseline.
    `trend_value` is the instrument's TREND cell (±3, already "+ = bullish"), or
    None when the instrument has no price series (e.g. NASDAQ/DAX/NIKKEI/FTSE100)
    — the `trend` factor, if configured, is then absent and the score is identical
    to the no-trend baseline. Structurally a twin of `sentiment_value`.
    """
    home = inst_cfg.get("home_ccy")
    factors_cfg = inst_cfg.get("factors", {}) or {}
    cats = (categories_by_ccy or {}).get(home, {}) or {}
    globals_by_kind = {
        "realyield": (realyield_raw, bool(realyield_stale), realyield_label),
        "liquidity": (liquidity_raw, bool(liquidity_stale), liquidity_label),
    }

    factor_rows: list[dict] = []
    num = 0.0
    wsum = 0.0
    for name, fc in factors_cfg.items():
        weight = float(fc.get("weight", 1.0))
        if name == RATES_FACTOR:
            value, sub_rows = _rates_factor(fc, cats, home, globals_by_kind)
            present = value is not None
            contribution = (weight * value) if present else None
            if present:
                num += weight * value
                wsum += weight
            factor_rows.append({
                "name": name,
                "value": None if value is None else float(value),
                "weight": weight,
                "contribution": None if contribution is None else float(contribution),
                "present": present,
                "components": sub_rows,
            })
        elif name == SENTIMENT_FACTOR:
            # SENTIMENT cell (±4 COT / ±3 P/C), already signed "+ = bullish".
            # Enters the mean on its native scale; absent when no value.
            sign = float(fc.get("sign", 1))
            raw = sentiment_value
            present = raw is not None
            value = (sign * raw) if present else None
            contribution = (weight * value) if present else None
            if present:
                num += weight * value
                wsum += weight
            factor_rows.append({
                "name": name, "raw": raw, "sign": sign, "weight": weight,
                "value": None if value is None else float(value),
                "contribution": None if contribution is None else float(contribution),
                "present": present, "source": "sentiment",
            })
        elif name == TREND_FACTOR:
            # TREND cell (±3), already signed "+ = bullish for the asset". Enters
            # the mean like SENTIMENT; absent when the instrument has no series.
            sign = float(fc.get("sign", 1))
            raw = trend_value
            present = raw is not None
            value = (sign * raw) if present else None
            contribution = (weight * value) if present else None
            if present:
                num += weight * value
                wsum += weight
            factor_rows.append({
                "name": name, "raw": raw, "sign": sign, "weight": weight,
                "value": None if value is None else float(value),
                "contribution": None if contribution is None else float(contribution),
                "present": present, "source": "trend",
            })
        else:
            sign = float(fc.get("sign", 1))
            raw = _cell(cats.get(name))
            present = raw is not None
            value = (sign * raw) if present else None         # signed factor value
            contribution = (weight * value) if present else None  # = sign*weight*raw
            if present:
                num += weight * value
                wsum += weight
            factor_rows.append({
                "name": name, "raw": raw, "sign": sign, "weight": weight,
                "value": None if value is None else float(value),
                "contribution": None if contribution is None else float(contribution),
                "present": present, "source": f"{home} {name}",
            })

    score_precise = (num / wsum) * scale if wsum > 0 else 0.0
    return {
        "symbol": symbol,
        "home_ccy": home,
        "type": inst_cfg.get("type"),
        "score": int(round(score_precise)),
        "score_precise": float(score_precise),
        "bias_label": bias_label(score_precise, thresholds),
        "coverage": int(sum(1 for f in factor_rows if f["present"])),
        "factors": factor_rows,
        # Display cell for the TREND column (reused from the same value folded into
        # the score above); None → blank cell.
        "trend": None if trend_value is None else int(trend_value),
    }


def compute_crossasset_scores(
    categories_by_ccy: dict,
    realyield_score: Any = None,
    config: Optional[dict] = None,
    liquidity_score: Any = None,
    sentiment_by_symbol: Optional[dict] = None,
    trend_by_symbol: Optional[dict] = None,
) -> dict:
    """Compute every cross-asset instrument's bias.

    Args:
      categories_by_ccy: {CCY: {category: score_cell|None}} or {CCY: {category:
        {"score_cell": int, "coverage": int}}} — from
        build_payload(...)["currencies"][CCY]["categories"].
      realyield_score: a RealYieldScore, a number, or None (None → real_yield_10y
        excluded from the rates composite).
      liquidity_score: a LiquidityScore, a number, or None (None → balance_sheet
        excluded). Default None keeps callers backward-compatible.
      sentiment_by_symbol: {symbol: sentiment_cell} (COT/P/C, signed "+ = bullish").
        A symbol absent here → its `sentiment` factor (if configured) is excluded,
        so the score is identical to the no-sentiment baseline. Default None.
      trend_by_symbol: {symbol: trend_cell} (±3, signed "+ = bullish"). A symbol
        absent here → its `trend` factor (if configured) is excluded, so the score
        is identical to the no-trend baseline. Default None. (Cross-asset reads
        only its own board keys from this map — FX pairs never collide.)
      config: parsed crossasset_instruments.yaml.

    Returns {symbol: {score, score_precise, bias_label, coverage, factors[...]}},
    where the `rates` factor carries its `components` (rate_exp_2y, real_yield_10y,
    balance_sheet) with raw/sign/weight/contribution. The composite is the weighted
    mean over PRESENT (non-stale) sub-components → bounded ±2 even with 3 aligned.
    Pure.
    """
    config = config or {}
    scale = float(config.get("scale", 5))
    thresholds = config.get("bias_thresholds", {}) or {}
    instruments = config.get("instruments", {}) or {}
    ry_raw = _realyield_raw(realyield_score)
    ry_label = _realyield_series(realyield_score)
    ry_stale = _realyield_stale(realyield_score)
    liq_raw = _global_raw(liquidity_score)
    liq_stale = bool(getattr(liquidity_score, "stale", False))
    sentiment_by_symbol = sentiment_by_symbol or {}
    trend_by_symbol = trend_by_symbol or {}

    return {
        sym: compute_instrument_score(sym, cfg, categories_by_ccy, ry_raw,
                                      scale, thresholds, realyield_label=ry_label,
                                      realyield_stale=ry_stale,
                                      liquidity_raw=liq_raw, liquidity_stale=liq_stale,
                                      sentiment_value=sentiment_by_symbol.get(sym),
                                      trend_value=trend_by_symbol.get(sym))
        for sym, cfg in instruments.items()
    }
