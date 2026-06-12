"""Pure Cross-Asset scoring (indices + metals) — no I/O, no network.

Derives a −10..+10 bias score per instrument from:
  - per-currency category scores (Growth/Inflation/Labour/Monetary), read from
    the instrument's HOME currency (build_payload category `score_cell`, −2..+2);
  - the global US 10y real-yield momentum score (DFII10, −2..+2).

Mirrors the FX aggregation logic: a weighted MEAN over the PRESENT factors
(× scale), so a missing factor — real_yield when `real_yields.parquet` is empty,
or a Monetary gap on a currency — is excluded gracefully, never zero-filled.

    compute_crossasset_scores(categories_by_ccy, realyield_score, config) -> dict

This module reads nothing and writes nothing; the caller supplies the already
computed category scores and real-yield score. It reuses the FX `bias_label`
(read-only import) so the 5-level mapping never diverges.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from src.economic_compute import bias_label  # read-only reuse (not modified)

CATEGORY_FACTORS = ("growth", "inflation", "labour", "monetary")
REAL_YIELD_FACTOR = "real_yield"


def _cell(v: Any) -> Optional[int]:
    """Coerce a category value to its int score_cell, or None if absent.

    Accepts a raw number or a build_payload category dict ({score_cell, coverage}).
    A dict with coverage == 0 is treated as ABSENT (no data), not a real 0 —
    build_payload emits score_cell 0 for both a genuine Neutral and a no-data
    category, and only coverage distinguishes them (matches the FX gap semantics).
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
    """Coerce a real-yield input to its int score, or None.
    Accepts a RealYieldScore (uses .score), a number, or None."""
    if ry is None:
        return None
    if isinstance(ry, (int, float)):
        return None if (isinstance(ry, float) and math.isnan(ry)) else int(ry)
    s = getattr(ry, "score", None)
    return None if s is None else int(s)


def compute_instrument_score(
    symbol: str,
    inst_cfg: dict,
    categories_by_ccy: dict,
    realyield_raw: Optional[int],
    scale: float,
    thresholds: dict,
) -> dict:
    """Score one instrument: weighted mean over present factors × scale."""
    home = inst_cfg.get("home_ccy")
    factors_cfg = inst_cfg.get("factors", {}) or {}
    cats = (categories_by_ccy or {}).get(home, {}) or {}

    factor_rows: list[dict] = []
    num = 0.0
    wsum = 0.0
    for name, fc in factors_cfg.items():
        sign = float(fc.get("sign", 1))
        weight = float(fc.get("weight", 1.0))
        if name == REAL_YIELD_FACTOR:
            raw = realyield_raw
            source = "DFII10"
        else:
            raw = _cell(cats.get(name))
            source = f"{home} {name}"
        present = raw is not None
        contribution = (sign * weight * raw) if present else None
        if present:
            num += sign * weight * raw
            wsum += weight
        factor_rows.append({
            "name": name,
            "raw": raw,
            "sign": sign,
            "weight": weight,
            "contribution": None if contribution is None else float(contribution),
            "present": present,
            "source": source,
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
    }


def compute_crossasset_scores(
    categories_by_ccy: dict,
    realyield_score: Any = None,
    config: Optional[dict] = None,
) -> dict:
    """Compute every cross-asset instrument's bias.

    Args:
      categories_by_ccy: {CCY: {category: score_cell|None}} or {CCY: {category:
        {"score_cell": int, ...}}}. Typically built from
        build_payload(...)["currencies"][CCY]["categories"].
      realyield_score: a RealYieldScore, a number, or None (None → real_yield
        factor excluded everywhere, gracefully).
      config: parsed crossasset_instruments.yaml (instruments, scale, thresholds).

    Returns {symbol: {score, score_precise, bias_label, coverage, factors[...]}}.
    Pure — no file or network access.
    """
    config = config or {}
    scale = float(config.get("scale", 5))
    thresholds = config.get("bias_thresholds", {}) or {}
    instruments = config.get("instruments", {}) or {}
    ry_raw = _realyield_raw(realyield_score)

    return {
        sym: compute_instrument_score(sym, cfg, categories_by_ccy, ry_raw, scale, thresholds)
        for sym, cfg in instruments.items()
    }
