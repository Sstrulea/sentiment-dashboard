"""Pure compute layer for the Economic Dashboard.

No fresh I/O — every function operates on an in-memory normalized calendar
DataFrame (schema from economic_fetch.CALENDAR_COLUMNS) plus the parsed YAML
configs. The render layer is responsible for reading parquet/YAML.

Pipeline (see DESIGN spec):
    release  -> atomic score (-2..+2)   compute_indicator_score
    indicator-> per-category subtotal   compute_currency_scorecard
    category -> currency index (~±10)   compute_currency_scorecard
    currency -> instrument score/bias   compute_instrument
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------

def bucket_score(signed_value: float, buckets: list[float]) -> int:
    """Map a signed magnitude (z or pct) to an integer score in -2..+2.

    `buckets` = [hi, lo] (both positive, hi > lo). Per spec:
        v >= hi  -> +2 ;  v >= lo -> +1 ;  v <= -hi -> -2 ;  v <= -lo -> -1 ;
        else 0.
    Boundaries are inclusive on the positive threshold (z=1.0 -> +2, z=0.33 -> +1).
    """
    hi, lo = float(buckets[0]), float(buckets[1])
    if signed_value is None or (isinstance(signed_value, float) and math.isnan(signed_value)):
        return 0
    if signed_value >= hi:
        return 2
    if signed_value >= lo:
        return 1
    if signed_value <= -hi:
        return -2
    if signed_value <= -lo:
        return -1
    return 0


def _is_num(v: Any) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _clamp_cell(precise: float) -> int:
    """Round a precise category score to an int and clamp to [-2, 2]."""
    if precise is None or (isinstance(precise, float) and math.isnan(precise)):
        return 0
    v = int(round(precise))
    return max(-2, min(2, v))


# ---------------------------------------------------------------------------
# Atomic per-indicator score
# ---------------------------------------------------------------------------

def compute_indicator_score(
    sub_df: pd.DataFrame,
    indicator_cfg: dict,
    defaults: dict,
    as_of: pd.Timestamp,
    allow_stale: bool = False,
) -> dict | None:
    """Score the latest release of one (currency, indicator).

    `sub_df` holds only that indicator's rows for one currency. Returns None if
    there is no release with a non-NaN `actual` at all (truly absent). If the
    latest actual is OUTSIDE `max_age_days`:
      - allow_stale=False (default): returns None (the historical scoring
        behavior — stale data never enters any average/index).
      - allow_stale=True: still scores it but marks `stale: True`, so callers
        can DISPLAY it (greyed) without feeding it into aggregation.
    Returns: {actual, consensus, surprise, z, score, flag, release_dt, stale}.
    flags: None (z-scored), "fallback", "no_consensus".
    """
    if sub_df is None or sub_df.empty:
        return None

    window_k = int(defaults.get("surprise_window_k", 12))
    z_buckets = defaults.get("z_buckets", [1.0, 0.33])
    pct_buckets = defaults.get("pct_buckets", [0.10, 0.02])
    min_prints = int(defaults.get("fallback_min_prints", 6))
    max_age_days = int(indicator_cfg.get("max_age_days", defaults.get("max_age_days", 120)))
    direction = float(indicator_cfg.get("direction", 1))

    df = sub_df.sort_values("release_dt").reset_index(drop=True)
    df = df.copy()
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    df["consensus"] = pd.to_numeric(df["consensus"], errors="coerce")

    cutoff = as_of - pd.Timedelta(days=max_age_days)
    fresh = df[df["actual"].notna() & (df["release_dt"] >= cutoff)]
    if not fresh.empty:
        latest = fresh.iloc[-1]
        stale = False
    elif allow_stale:
        any_actual = df[df["actual"].notna()]
        if any_actual.empty:
            return None
        latest = any_actual.iloc[-1]
        stale = True
    else:
        return None

    actual = float(latest["actual"])
    consensus = latest["consensus"]
    release_dt = pd.Timestamp(latest["release_dt"])

    if not _is_num(consensus):
        return {
            "actual": actual,
            "consensus": None,
            "surprise": None,
            "z": None,
            "score": 0,
            "flag": "no_consensus",
            "release_dt": release_dt,
            "stale": stale,
        }

    consensus = float(consensus)
    surprise = actual - consensus

    # Trailing K prints with BOTH values present, up to and including latest.
    pairs = df[
        df["actual"].notna()
        & df["consensus"].notna()
        & (df["release_dt"] <= release_dt)
    ]
    diffs = (pairs["actual"] - pairs["consensus"]).tail(window_k)
    n_pairs = int(len(diffs))
    sigma = float(diffs.std(ddof=1)) if n_pairs >= 2 else float("nan")

    use_fallback = (
        n_pairs < min_prints or sigma == 0.0 or math.isnan(sigma)
    )

    if use_fallback:
        if consensus == 0.0:
            return {
                "actual": actual,
                "consensus": consensus,
                "surprise": surprise,
                "z": None,
                "score": 0,
                "flag": "no_consensus",
                "release_dt": release_dt,
                "stale": stale,
            }
        pct = direction * surprise / abs(consensus)
        return {
            "actual": actual,
            "consensus": consensus,
            "surprise": surprise,
            "z": None,
            "score": bucket_score(pct, pct_buckets),
            "flag": "fallback",
            "release_dt": release_dt,
            "stale": stale,
        }

    z = direction * surprise / sigma
    return {
        "actual": actual,
        "consensus": consensus,
        "surprise": surprise,
        "z": z,
        "score": bucket_score(z, z_buckets),
        "flag": None,
        "release_dt": release_dt,
        "stale": stale,
    }


# ---------------------------------------------------------------------------
# Per-currency scorecard
# ---------------------------------------------------------------------------

def _indicator_applies(currency: str, indicator_cfg: dict) -> bool:
    wl = indicator_cfg.get("currencies")
    return (not wl) or (currency in wl)


def compute_currency_scorecard(
    calendar_df: pd.DataFrame,
    currency: str,
    indicators_cfg: dict,
    instruments_cfg: dict,
    as_of: pd.Timestamp,
    rate_entry: dict | None = None,
) -> dict:
    """Aggregate one currency: indicator scores -> category subtotals -> index.

    `rate_entry` (optional) adds the standing **monetary** category with the
    single sub-indicator `rate_expectations` (score = rate_entry["score"]). When
    None, the scorecard is identical to the surprise-only behavior (3 categories).

    Returns:
        {
          "currency": str,
          "index": float,                 # ~[-10, 10]
          "coverage": int,                # total indicators present
          "categories": {cat: {"score_cell": int, "score_precise": float, "coverage": int}},
          "breakdown": {indicator_key: {...atomic score...}},
        }
    """
    defaults = indicators_cfg.get("defaults", {}) or {}
    indicators = indicators_cfg.get("indicators", {}) or {}
    categories_cfg = indicators_cfg.get("categories", {}) or {}
    scale = float(instruments_cfg.get("scale", 5))

    breakdown: dict[str, dict] = {}
    # category -> list of (score, weight)
    per_cat: dict[str, list[tuple[int, float]]] = {c: [] for c in categories_cfg}

    if calendar_df is not None and not calendar_df.empty:
        ccy_df = calendar_df[calendar_df["currency"] == currency]
    else:
        ccy_df = pd.DataFrame(columns=["currency", "indicator_key", "release_dt", "actual", "consensus"])

    for key, ind_cfg in indicators.items():
        if not _indicator_applies(currency, ind_cfg):
            continue
        sub = ccy_df[ccy_df["indicator_key"] == key] if not ccy_df.empty else ccy_df
        # allow_stale=True so stale indicators appear in the breakdown (for
        # display), but they are EXCLUDED from the category average/index below.
        scored = compute_indicator_score(sub, ind_cfg, defaults, as_of, allow_stale=True)
        if scored is None:
            continue
        breakdown[key] = scored
        cat = ind_cfg.get("category")
        if cat in per_cat and not scored.get("stale"):
            per_cat[cat].append((scored["score"], float(ind_cfg.get("weight", 1.0))))

    categories_out: dict[str, dict] = {}
    cat_scores_for_index: list[tuple[float, float]] = []
    total_coverage = 0
    for cat, cat_meta in categories_cfg.items():
        entries = per_cat.get(cat, [])
        coverage = len(entries)
        total_coverage += coverage
        wsum = sum(w for _, w in entries)
        if coverage > 0 and wsum > 0:
            precise = sum(s * w for s, w in entries) / wsum
        else:
            precise = 0.0
        categories_out[cat] = {
            "score_cell": _clamp_cell(precise) if coverage > 0 else 0,
            "score_precise": float(precise),
            "coverage": coverage,
        }
        if coverage > 0:
            cat_scores_for_index.append((precise, float(cat_meta.get("weight", 1.0))))

    # Standing monetary category (Rate Expectations). Same shape as a surprise
    # category but its score comes from the precomputed rate engine, not the
    # calendar. Weight 1.0 (equal) — provisional until calibration.
    if rate_entry is not None and rate_entry.get("score") is not None:
        rscore = int(rate_entry["score"])
        breakdown["rate_expectations"] = dict(rate_entry)
        categories_out["monetary"] = {
            "score_cell": _clamp_cell(float(rscore)),
            "score_precise": float(rscore),
            "coverage": 1,
        }
        total_coverage += 1
        monetary_weight = float(
            (indicators_cfg.get("categories", {}).get("monetary", {}) or {}).get("weight", 1.0)
        )
        cat_scores_for_index.append((float(rscore), monetary_weight))

    if cat_scores_for_index:
        wsum = sum(w for _, w in cat_scores_for_index)
        index = (sum(p * w for p, w in cat_scores_for_index) / wsum) * scale if wsum else 0.0
    else:
        index = 0.0

    return {
        "currency": currency,
        "index": float(index),
        "coverage": total_coverage,
        "categories": categories_out,
        "breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# Bias mapping + instrument derivation
# ---------------------------------------------------------------------------

def bias_label(score: float, thresholds: dict) -> str:
    """Map an instrument score to a bias label using bias_thresholds.

    |score| < mild           -> Neutral
    mild <= |score| < very   -> Bullish / Bearish
    |score| >= very          -> Very Bullish / Very Bearish
    """
    very = float(thresholds.get("very", 7))
    mild = float(thresholds.get("mild", 3))
    a = abs(score)
    if a < mild:
        return "Neutral"
    direction_bull = score > 0
    if a >= very:
        return "Very Bullish" if direction_bull else "Very Bearish"
    return "Bullish" if direction_bull else "Bearish"


def _category_cells(
    inst_cfg: dict,
    base_card: dict | None,
    quote_card: dict | None,
    categories_display: list[str],
    pair_divisor: float,
) -> dict[str, dict]:
    """Per-category display cells for an instrument (precise + clamped int)."""
    out: dict[str, dict] = {}
    is_fx = inst_cfg.get("type") == "fx"
    sign = float(inst_cfg.get("sign", 1))
    for cat in categories_display:
        base_cat = (base_card or {}).get("categories", {}).get(cat, {})
        quote_cat = (quote_card or {}).get("categories", {}).get(cat, {})
        base_precise = float(base_cat.get("score_precise", 0.0))
        base_cov = int(base_cat.get("coverage", 0))
        if is_fx:
            quote_precise = float(quote_cat.get("score_precise", 0.0))
            quote_cov = int(quote_cat.get("coverage", 0))
            precise = (base_precise - quote_precise) / pair_divisor
            coverage = base_cov + quote_cov
        else:
            precise = base_precise * sign
            coverage = base_cov
        out[cat] = {
            "score_cell": _clamp_cell(precise) if coverage > 0 else 0,
            "score_precise": float(precise),
            "coverage": coverage,
        }
    return out


def compute_instrument(
    symbol: str,
    inst_cfg: dict,
    scorecards: dict[str, dict],
    instruments_cfg: dict,
) -> dict:
    """Derive a single instrument payload from precomputed currency scorecards."""
    pair_divisor = float(instruments_cfg.get("pair_divisor", 2))
    thresholds = instruments_cfg.get("bias_thresholds", {}) or {}
    categories_display = instruments_cfg.get("categories_display", []) or []
    itype = inst_cfg.get("type")

    if itype == "single":
        currency = inst_cfg["currency"]
        sign = float(inst_cfg.get("sign", 1))
        base_card = scorecards.get(currency)
        score = (base_card["index"] * sign) if base_card else 0.0
        quote_card = None
        breakdown = {
            "base": {
                "currency": currency,
                "indicators": (base_card or {}).get("breakdown", {}),
            },
            "quote": None,
        }
    elif itype == "fx":
        base, quote = inst_cfg["base"], inst_cfg["quote"]
        base_card = scorecards.get(base)
        quote_card = scorecards.get(quote)
        base_idx = base_card["index"] if base_card else 0.0
        quote_idx = quote_card["index"] if quote_card else 0.0
        score = (base_idx - quote_idx) / pair_divisor
        breakdown = {
            "base": {
                "currency": base,
                "indicators": (base_card or {}).get("breakdown", {}),
            },
            "quote": {
                "currency": quote,
                "indicators": (quote_card or {}).get("breakdown", {}),
            },
        }
    else:
        raise ValueError(f"Unknown instrument type {itype!r} for {symbol}")

    cells = _category_cells(inst_cfg, base_card, quote_card, categories_display, pair_divisor)

    return {
        "symbol": symbol,
        "display": inst_cfg.get("display", symbol),
        "type": itype,
        "score": float(score),
        "bias": bias_label(score, thresholds),
        "categories": cells,
        "breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# Top-level payload
# ---------------------------------------------------------------------------

def _rate_entry_for(rs) -> dict | None:
    """Normalize a RateScore (dataclass or dict) into a breakdown entry, or None."""
    if rs is None:
        return None
    def g(k):
        return getattr(rs, k) if hasattr(rs, k) else (rs.get(k) if isinstance(rs, dict) else None)
    score = g("rate_score")
    if score is None:
        return None
    as_of = g("as_of")
    return {
        "score": int(score),
        "delta_w": g("delta_w"),
        "latest_yield": g("latest_yield"),
        "z": g("z"),
        "method": g("method"),
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else as_of,
        "stale": bool(g("stale")) if g("stale") is not None else False,
        "pillar": "monetary",
    }


def build_payload(
    calendar_df: pd.DataFrame,
    indicators_cfg: dict,
    instruments_cfg: dict,
    as_of: pd.Timestamp | None = None,
    rate_scores: dict | None = None,
) -> dict:
    """Compute every currency scorecard and every instrument payload.

    Pure: `as_of` defaults to now (UTC, naive) but is best passed explicitly for
    deterministic tests. `rate_scores` (optional) is a {currency: RateScore}
    mapping from the rate engine; when given, each scored currency gains the
    standing `monetary` category. When None, behavior is identical to the
    surprise-only payload (full backward compatibility).
    Returns {"as_of", "currencies", "instruments"}.
    """
    if as_of is None:
        as_of = pd.Timestamp.utcnow().tz_localize(None)
    as_of = pd.Timestamp(as_of)

    if calendar_df is not None and not calendar_df.empty:
        calendar_df = calendar_df.copy()
        calendar_df["release_dt"] = pd.to_datetime(calendar_df["release_dt"])

    # Which currencies do we need? Everything referenced by an instrument.
    instruments = instruments_cfg.get("instruments", {}) or {}
    needed: set[str] = set()
    for inst in instruments.values():
        if inst.get("type") == "single":
            needed.add(inst["currency"])
        elif inst.get("type") == "fx":
            needed.add(inst["base"])
            needed.add(inst["quote"])

    rate_scores = rate_scores or {}
    scorecards = {
        ccy: compute_currency_scorecard(
            calendar_df, ccy, indicators_cfg, instruments_cfg, as_of,
            rate_entry=_rate_entry_for(rate_scores.get(ccy)),
        )
        for ccy in sorted(needed)
    }

    instrument_payloads = [
        compute_instrument(sym, cfg, scorecards, instruments_cfg)
        for sym, cfg in instruments.items()
    ]

    return {
        "as_of": as_of.isoformat(),
        "currencies": scorecards,
        "instruments": instrument_payloads,
    }
