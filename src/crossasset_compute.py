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
    "balance_sheet": ("liquidity", None),      # global bank reserves (WRBWFRBL), net_liquidity fallback
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


def _precise(v: Any) -> Optional[float]:
    """A category's score_precise (rule N-c), or None when absent — same
    presence test as `_cell` (coverage 0 → absent)."""
    if v is None:
        return None
    if isinstance(v, dict):
        if "coverage" in v and (v.get("coverage") or 0) == 0:
            return None
        p = v.get("score_precise", v.get("score_cell"))
        return None if p is None else float(p)
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rate_signal(z: Any, fallback: Any, unit: float, cap: float) -> Optional[float]:
    """Continuous rate signal clip(z/unit, −cap, cap) (rule N-c: unit 0.87 = p60
    of |z|, cap 2 ≈ p87.5 — the indicator rule without the steps). A series with
    no z (fallback/insufficient method) keeps its bucketed score, already ±2."""
    if z is not None and not (isinstance(z, float) and math.isnan(z)):
        return float(max(-cap, min(cap, float(z) / unit)))
    if fallback is None or (isinstance(fallback, float) and math.isnan(fallback)):
        return None
    return float(fallback)


def _scaled(value: float, sigma: float, clip: float) -> float:
    """value / σ, clipped to ±clip — one factor on the common scale."""
    return float(max(-clip, min(clip, value / sigma)))


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


LIQUIDITY_SERIES_LABELS = {"reserves": "WRBWFRBL", "net_liquidity": "NET_LIQUIDITY"}


def _liquidity_series(liq: Any) -> str:
    """Label for the liquidity sub-component's source (the series actually
    scored — "reserves" (WRBWFRBL) by default, or the net_liquidity fallback
    when reserves is unresolved). Mirrors _realyield_series."""
    s = getattr(liq, "series", None)
    return LIQUIDITY_SERIES_LABELS.get(s, "WRBWFRBL")


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


def _subcell_signal(v: Any, rate_sig: dict) -> tuple[Optional[float], bool, bool]:
    """`_subcell_display` on the continuous rate signal: the monetary cell's z
    (attached by the caller from the rate engine) → clip(z/unit, ±cap)."""
    raw, stale, present = _subcell_display(v)
    if raw is None or not isinstance(v, dict):
        return raw, stale, present
    sig = rate_signal(v.get("z"), raw, float(rate_sig["unit"]), float(rate_sig["cap"]))
    return sig, stale, present


def _rates_factor(rates_cfg: dict, cats: dict, home: str,
                  globals_by_kind: dict, rate_sig: Optional[dict] = None
                  ) -> tuple[Optional[float], list[dict]]:
    """Composite rates value = weighted mean of PRESENT (non-stale), non-zero-
    weight sub-components (signed), bounded at ±2 regardless of how many
    components there are. Stale sub-components are kept in the rows for display
    (raw + stale flag) but excluded from the mean. A sub-component configured at
    weight 0 (an accepted degradation — see docs/accepted-degradations.md) is
    likewise kept in the rows (raw + `excluded` flag) but contributes nothing to
    the mean: this is mathematically identical to the sub-component being absent
    from `comps_cfg` entirely, just still inspectable. `globals_by_kind` maps a
    non-category kind ("realyield", "liquidity") -> (raw|None, stale,
    source_label). Returns (value|None, rows)."""
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
            if rate_sig is not None:
                raw, stale, present = _subcell_signal(cats.get(key), rate_sig)
            else:
                raw, stale, present = _subcell_display(cats.get(key))
            source = f"{home} {key}"
        # Display contribution whenever a value exists (even if stale/excluded).
        contribution = (sign * weight * raw) if (raw is not None) else None
        if present and weight > 0:
            num += sign * weight * raw
            wsum += weight
        sub_rows.append({
            "name": name, "raw": raw, "sign": sign, "weight": weight,
            "contribution": None if contribution is None else float(contribution),
            "present": present, "stale": stale, "source": source,
            "excluded": weight <= 0,
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
    liquidity_label: str = "WRBWFRBL",
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


def categories_by_currency(currencies: dict, config: Optional[dict] = None) -> dict:
    """{CCY: categories} for compute_crossasset_scores from a payload's
    `currencies`. Under rule N-c (`factor_scales` in the config) each monetary
    cell is a COPY carrying the rate engine's z (breakdown.rate_expectations),
    which the 2Y component turns into its continuous signal; the payload itself
    is never modified."""
    out = {ccy: card.get("categories", {}) for ccy, card in (currencies or {}).items()}
    if not (config or {}).get("factor_scales"):
        return out
    for ccy, card in (currencies or {}).items():
        mon = (card.get("categories") or {}).get("monetary")
        entry = (card.get("breakdown") or {}).get("rate_expectations")
        if mon is not None and entry is not None:
            out[ccy] = dict(out[ccy], monetary=dict(mon, z=entry.get("z")))
    return out


def compute_instrument_score_scaled(
    symbol: str,
    inst_cfg: dict,
    categories_by_ccy: dict,
    scale: float,
    thresholds: dict,
    sigmas: dict,
    rate_sig: dict,
    clip: float,
    globals_by_kind: dict,
    sentiment_value: Optional[int] = None,
    trend_value: Optional[int] = None,
) -> dict:
    """Rule N-c (feat/factor-scales): every factor on the same scale.

    Inputs, signed for the asset: growth/inflation/labour = sign × the home
    currency's category score_precise (not the rounded cell); rates = the
    weighted mean of the present, non-stale components, each sign ×
    clip(z/unit, ±cap) (home 2Y, DFII10); sentiment = sign × the COT / P-C cell.
    Each input is divided by its σ (`factor_scales[type]`, the historical std
    on this board) and clipped to ±clip; score = weighted mean × scale, with the
    same weights/signs as v1. A factor without a σ (trend — off) enters unscaled.

    Each factor row carries raw, value (signed input), sigma, value_sigma and a
    contribution = weight × value_sigma / Σweight × scale, so the contributions
    add up to score_precise exactly.
    """
    home = inst_cfg.get("home_ccy")
    factors_cfg = inst_cfg.get("factors", {}) or {}
    cats = (categories_by_ccy or {}).get(home, {}) or {}

    factor_rows: list[dict] = []
    for name, fc in factors_cfg.items():
        weight = float(fc.get("weight", 1.0))
        sign = float(fc.get("sign", 1))
        row: dict = {"name": name, "weight": weight}
        if name == RATES_FACTOR:
            value, sub_rows = _rates_factor(fc, cats, home, globals_by_kind, rate_sig)
            row["components"] = sub_rows
            raw = value
        elif name == SENTIMENT_FACTOR:
            raw = sentiment_value
            value = None if raw is None else sign * raw
            row.update(sign=sign, source="sentiment")
        elif name == TREND_FACTOR:
            raw = trend_value
            value = None if raw is None else sign * raw
            row.update(sign=sign, source="trend")
        else:
            raw = _precise(cats.get(name))
            value = None if raw is None else sign * raw
            row.update(sign=sign, source=f"{home} {name}")
        sigma = sigmas.get(name)
        present = value is not None
        y = None
        if present:
            y = _scaled(value, float(sigma), clip) if sigma else float(value)
        row.update(raw=None if raw is None else float(raw),
                   value=None if value is None else float(value),
                   sigma=None if sigma is None else float(sigma),
                   value_sigma=y, present=present)
        factor_rows.append(row)

    wsum = sum(r["weight"] for r in factor_rows if r["present"])
    for r in factor_rows:
        r["contribution"] = (r["weight"] * r["value_sigma"] / wsum * scale
                             if r["present"] and wsum > 0 else None)
    score_precise = (float(sum(r["contribution"] for r in factor_rows if r["present"]))
                     if wsum > 0 else 0.0)
    return {
        "symbol": symbol,
        "home_ccy": home,
        "type": inst_cfg.get("type"),
        "score": int(round(score_precise)),
        "score_precise": score_precise,
        "bias_label": bias_label(score_precise, thresholds),
        "coverage": int(sum(1 for f in factor_rows if f["present"])),
        "factors": factor_rows,
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
    liq_label = _liquidity_series(liquidity_score)
    liq_stale = bool(getattr(liquidity_score, "stale", False))
    sentiment_by_symbol = sentiment_by_symbol or {}
    trend_by_symbol = trend_by_symbol or {}

    # Rule N-c (feat/factor-scales) — only when the config carries the scales;
    # without them every score is exactly the v1 path below.
    factor_scales = config.get("factor_scales")
    if factor_scales:
        rate_sig = config.get("rate_signal") or {"unit": 0.87, "cap": 2}
        clip = float(config.get("factor_clip", 3))
        unit, cap = float(rate_sig["unit"]), float(rate_sig["cap"])
        ry_sig = (rate_signal(getattr(realyield_score, "z", None), ry_raw, unit, cap)
                  if ry_raw is not None else None)
        globals_by_kind = {
            "realyield": (ry_sig, ry_stale, ry_label),
            "liquidity": (liq_raw, liq_stale, liq_label),
        }
        thresholds_v2 = config.get("bias_thresholds_scaled") or thresholds
        out = {}
        for sym, cfg in instruments.items():
            r = compute_instrument_score_scaled(
                sym, cfg, categories_by_ccy, scale, thresholds_v2,
                factor_scales.get(cfg.get("type"), {}) or {}, rate_sig, clip, globals_by_kind,
                sentiment_value=sentiment_by_symbol.get(sym),
                trend_value=trend_by_symbol.get(sym))
            out[sym] = r
        return out

    return {
        sym: compute_instrument_score(sym, cfg, categories_by_ccy, ry_raw,
                                      scale, thresholds, realyield_label=ry_label,
                                      realyield_stale=ry_stale,
                                      liquidity_raw=liq_raw, liquidity_label=liq_label,
                                      liquidity_stale=liq_stale,
                                      sentiment_value=sentiment_by_symbol.get(sym),
                                      trend_value=trend_by_symbol.get(sym))
        for sym, cfg in instruments.items()
    }


# ---------------------------------------------------------------------------
# Scoring v3 "swing" (2026-10-07) — pure pieces of the cross-asset score
# ---------------------------------------------------------------------------

V3_RATE_W = 63          # observations: ~3 months
V3_RATE_END_N = 5       # averaged ends, as src/rate_compute.py does for 21
V3_INDEX_WEIGHTS = {"macro": 0.67, "rates": 0.33}
V3_METAL_WEIGHTS = {"macro": 0.56, "rates": 0.33, "cot": 0.11}
V3_SCALE = 2.5


def v3_yield_change(dates: list, ys: list, as_of, W: int = V3_RATE_W, k: int = V3_RATE_END_N
                    ) -> tuple[Optional[float], Any]:
    """(Δ, latest date) at as_of: mean of the last k observations − mean of the
    k observations ending W observations earlier; (None, latest) when the
    series is too short."""
    import pandas as pd
    cut = pd.Timestamp(as_of)
    idx = [i for i, d in enumerate(dates) if pd.Timestamp(d) <= cut]
    if not idx:
        return None, None
    n = idx[-1] + 1
    if n < W + k:
        return None, dates[n - 1]
    last = sum(ys[n - k:n]) / k
    prev = sum(ys[n - W - k:n - W]) / k
    return float(last - prev), dates[n - 1]


def v3_macro_raw(inst_cfg: dict, categories: dict, sigmas_home: dict) -> Optional[float]:
    """Weighted mean (today's weights and signs) of sign × score_precise(home,
    category) / σ(home, category) over the present macro categories — before
    dividing by the instrument's σ_Macro. None when none is present."""
    num = wsum = 0.0
    for name, fc in (inst_cfg.get("factors") or {}).items():
        if name not in ("growth", "inflation", "labour"):
            continue
        cell = (categories or {}).get(name)
        sg = (sigmas_home or {}).get(name)
        if cell is None or not sg or (cell.get("coverage") or 0) <= 0:
            continue
        w = float(fc.get("weight", 1.0))
        num += w * float(fc.get("sign", 1)) * float(cell["score_precise"]) / float(sg)
        wsum += w
    return (num / wsum) if wsum > 0 else None


def v3_index_combine(m_block: Optional[float], r_block: Optional[float]) -> tuple[Optional[float], dict]:
    """Indices: m = 0.67 × Macro, r = 0.33 × Rates; s = m + r when r ≥ 0; when
    r < 0, s = max(m + r, 0) if m > 0, otherwise s = m (falling rates lift, rising
    rates only erase a bullish macro, never push it negative). A missing block:
    the other alone (weight rescaled to 1). Returns (s, {block: contribution})."""
    if m_block is None and r_block is None:
        return None, {}
    if r_block is None:
        return float(m_block), {"macro": float(m_block), "rates": 0.0}
    if m_block is None:
        return float(r_block), {"macro": 0.0, "rates": float(r_block)}
    m = V3_INDEX_WEIGHTS["macro"] * float(m_block)
    r = V3_INDEX_WEIGHTS["rates"] * float(r_block)
    if r >= 0:
        s = m + r
    elif m > 0:
        s = max(m + r, 0.0)
    else:
        s = m
    return float(s), {"macro": m, "rates": float(s - m)}


V3_YIELD_SOURCES = {"USD": "USD", "EUR": "EUR", "JPY": "JPY", "GBP": "GBP", "REAL10": "DFII10"}
V3_REAL_MAX_AGE_BD = 7      # = src.realyield_compute.MAX_AGE_BD


def v3_yield_signals(rates_df, real_df, as_of, sigma_y: dict) -> dict:
    """{name: {signal, delta, latest, stale}} for the v3 yields — the home-currency
    2Y (data/rates.parquet) and the US real 10Y DFII10 (data/real_yields.parquet):
    signal = −Δ63 / σ_y (rising yields = bearish), None when the series is too
    short or stale (latest obs older than its source's max age, as the rate
    pillar judges it). Pure given the frames."""
    import pandas as pd
    from .momentum_common import bday_lag
    from .rate_compute import _latest_source, _series_for, max_age_for
    ref = pd.Timestamp(as_of).date()
    out = {}
    for name, key in V3_YIELD_SOURCES.items():
        if name == "REAL10":
            if real_df is None or real_df.empty:
                continue
            s = real_df[(real_df["series"] == key) & real_df["yield_pct"].notna()].sort_values("date")
            s = s[pd.to_datetime(s["date"]) <= pd.Timestamp(ref)]
            dates = [pd.Timestamp(d).date() for d in s["date"]]
            ys = [float(v) for v in s["yield_pct"]]
            max_age = V3_REAL_MAX_AGE_BD
        else:
            if rates_df is None or rates_df.empty:
                continue
            r = rates_df[pd.to_datetime(rates_df["date"], errors="coerce") <= pd.Timestamp(ref)]
            dates, ys = _series_for(r, key)
            max_age = max_age_for(_latest_source(r, key))
        if not ys:
            continue
        delta, latest = v3_yield_change(dates, ys, ref)
        stale = latest is None or bday_lag(latest, ref) > max_age
        sg = (sigma_y or {}).get(name)
        signal = None if (delta is None or stale or not sg) else -float(delta) / float(sg)
        out[name] = {"signal": signal, "delta": delta, "stale": bool(stale),
                     "latest": None if latest is None else str(latest), "latest_yield": ys[-1]}
    return out


def compute_crossasset_scores_v3(categories_by_ccy: dict, config: dict, fx_sigma_ccy: dict,
                                 yield_signals: dict, metal_cot: Optional[dict] = None) -> dict:
    """Scoring v3 for indices + metals (2026-10-07).

    Macro = weighted mean (config weights/signs) of sign × score_precise(home,
    cat) / σ(home, cat) (the FX per-currency σ), / σ_Macro[symbol]. Rates =
    mean of the PRESENT signals — indices: home 2Y and US real 10Y; metals: US
    2Y — / σ_Rates[symbol]. Indices: the asymmetric 0.67 / 0.33 rule
    (v3_index_combine); metals: 0.56 Macro + 0.33 Rates + 0.11 COT/σ_cot_metal.
    score_precise = s × 2.5; label from RMS[symbol] and the board thresholds.
    Factor rows (growth/inflation/labour, rates with its components, cot) carry
    contributions that add up to score_precise exactly. P/C, the real yield at
    metals, balance_sheet and TREND are not in the score."""
    from .economic_compute import bias_label as _bl, v3_combine
    k = config.get("v3") or {}
    thresholds = k.get("thresholds") or {}
    out = {}
    for sym, cfg in (config.get("instruments") or {}).items():
        home, itype = cfg.get("home_ccy"), cfg.get("type")
        cats = (categories_by_ccy or {}).get(home, {}) or {}
        sig_home = (fx_sigma_ccy or {}).get(home, {}) or {}
        m_raw = v3_macro_raw(cfg, cats, sig_home)
        s_mac = (k.get("sigma_macro") or {}).get(sym)
        m_block = None if (m_raw is None or not s_mac) else m_raw / float(s_mac)
        comp_names = ["USD"] if itype == "metal" else [home, "REAL10"]
        comps = []
        for n in comp_names:
            y = (yield_signals or {}).get(n) or {}
            comps.append({"name": "real_yield_10y" if n == "REAL10" else "rate_exp_2y",
                          "series": "DFII10" if n == "REAL10" else f"{n} 2Y",
                          "signal": y.get("signal"), "delta": y.get("delta"),
                          "stale": bool(y.get("stale")), "present": y.get("signal") is not None})
        present = [c["signal"] for c in comps if c["present"]]
        r_raw = (sum(present) / len(present)) if present else None
        s_rat = (k.get("sigma_rates") or {}).get(sym)
        r_block = None if (r_raw is None or not s_rat) else r_raw / float(s_rat)
        cot_block = None
        cell = (metal_cot or {}).get(sym) if itype == "metal" else None
        if cell is not None and k.get("sigma_cot_metal"):
            cot_block = float(cell) / float(k["sigma_cot_metal"])
        if itype == "index":
            s, parts = v3_index_combine(m_block, r_block)
            w_m = (V3_INDEX_WEIGHTS["macro"] if r_block is not None else 1.0) if m_block is not None else 0.0
            macro_part = parts.get("macro", 0.0)
            rates_part = parts.get("rates", 0.0)
            cot_part = None
        else:
            s, parts = v3_combine({"macro": m_block, "rates": r_block, "cot": cot_block}, V3_METAL_WEIGHTS)
            wsum = sum(V3_METAL_WEIGHTS[b] for b in parts)
            w_m = (V3_METAL_WEIGHTS["macro"] / wsum) if "macro" in parts else 0.0
            macro_part, rates_part, cot_part = parts.get("macro", 0.0), parts.get("rates"), parts.get("cot")
        score = 0.0 if s is None else float(s) * V3_SCALE
        # macro split per category (exact: Σ = macro_part × 2.5)
        rows = []
        wsum_c = sum(float(fc.get("weight", 1.0)) for n, fc in (cfg.get("factors") or {}).items()
                     if n in ("growth", "inflation", "labour") and (cats.get(n) or {}).get("coverage", 0) > 0
                     and sig_home.get(n))
        for name, fc in (cfg.get("factors") or {}).items():
            if name not in ("growth", "inflation", "labour"):
                continue
            cell_c = cats.get(name) or {}
            sg = sig_home.get(name)
            present_c = (cell_c.get("coverage") or 0) > 0 and bool(sg) and m_block is not None
            sign, w = float(fc.get("sign", 1)), float(fc.get("weight", 1.0))
            value = sign * float(cell_c["score_precise"]) / float(sg) if present_c else None
            contribution = (V3_SCALE * w_m * w * value / wsum_c / float(s_mac)) if present_c else None
            rows.append({"name": name, "sign": sign, "weight": w, "present": present_c,
                         "raw": None if not present_c else float(cell_c["score_precise"]),
                         "value": value, "sigma": sg, "contribution": contribution,
                         "source": f"{home} {name}"})
        # display fields on the components: raw = the signal, a share of the rates
        # contribution proportional to it (Σ components = the rates contribution)
        rc = None if r_block is None or rates_part is None else V3_SCALE * rates_part
        tot = sum(c["signal"] for c in comps if c["present"])
        n_p = sum(1 for c in comps if c["present"])
        for c in comps:
            c.update(raw=c["signal"], sign=1.0, weight=(1.0 / n_p) if (c["present"] and n_p) else 0.0,
                     source=c["series"] + " · 63-obs change / σ_y")
            if rc is None or not c["present"]:
                c["contribution"] = None
            else:
                c["contribution"] = rc * (c["signal"] / tot if tot else 1.0 / n_p)
        rows.append({"name": "rates", "weight": V3_INDEX_WEIGHTS["rates"] if itype == "index" else V3_METAL_WEIGHTS["rates"],
                     "present": r_block is not None, "value": r_block, "raw": r_raw,
                     "contribution": None if r_block is None or rates_part is None else V3_SCALE * rates_part,
                     "components": comps})
        if itype == "metal":
            rows.append({"name": "cot", "sign": 1.0, "weight": V3_METAL_WEIGHTS["cot"], "present": cot_block is not None,
                         "raw": None if cell is None else int(cell), "value": cot_block,
                         "contribution": None if cot_part is None else V3_SCALE * cot_part, "source": "COT"})
        rms = (k.get("rms") or {}).get(sym)
        out[sym] = {"symbol": sym, "home_ccy": home, "type": itype,
                    "score": int(round(score)), "score_precise": score,
                    "bias_label": "Neutral" if not rms else _bl(score / float(rms), thresholds),
                    "coverage": int(sum(1 for r in rows if r["present"])), "factors": rows,
                    "v3": {"macro": m_block, "rates": r_block, "cot": cot_block}}
    return out
