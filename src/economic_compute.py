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


def _late_quantize(z: float) -> float:
    """V4.2 continuous per-indicator contribution from z (late quantization):
    |z| < 0.2 → 0.0 (anti-rounding dead-zone); else sign(z)·min(2, |z|·2/1.54).
    Anchors: z=0.81→1.052, z=1.54→2.0, |z|≥3.0→2.0 (clamped)."""
    if z is None or (isinstance(z, float) and math.isnan(z)):
        return 0.0
    if abs(z) < 0.2:
        return 0.0
    return math.copysign(min(2.0, abs(z) * (2.0 / 1.54)), z)


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

def effective_frequency(indicator_cfg: dict, defaults: dict, currency: str | None) -> str | None:
    """Resolve an indicator's frequency for a currency (per-currency override wins)."""
    overrides = indicator_cfg.get("frequency_overrides", {}) or {}
    if currency and currency in overrides:
        return overrides[currency]
    return indicator_cfg.get("frequency") or defaults.get("default_frequency")


def _max_age_for(indicator_cfg: dict, defaults: dict, freq: str | None) -> int:
    """Recency window (days): explicit per-indicator override > per-frequency > fallback."""
    if "max_age_days" in indicator_cfg:
        return int(indicator_cfg["max_age_days"])
    by_freq = defaults.get("max_age_by_frequency", {}) or {}
    if freq and freq in by_freq:
        return int(by_freq[freq])
    return int(defaults.get("max_age_days", 120))


def _valid_period(s: pd.Series) -> pd.Series:
    """Mask of rows whose MT5 `period` is a usable reference period."""
    p = s.astype("string").str.strip()
    return p.notna() & ~p.isin(["", "0", "0.0", "nan", "none", "None", "NaT"])


def _keep_latest_published(rows: pd.DataFrame):
    """Index of the row to keep for one group: the latest release_dt among rows
    with a non-null `actual` (a future/scheduled row has null actual → never
    chosen); if the whole group is unpublished, keep its latest row."""
    published = rows[rows["actual"].notna()]
    pick = published if not published.empty else rows
    return pick["release_dt"].idxmax()


def _dedup_flash_final(df: pd.DataFrame, gap_days: int | None) -> pd.DataFrame:
    """Collapse flash/final double prints of one reference period into the single
    PUBLISHED final, so the current value and the rolling sigma use one print per
    period (never a flash, never a future/scheduled blank).

    `df` is one (currency, indicator). Primary key is the MT5 `period` column
    (exact). Rows without a usable period fall back to release-date proximity
    (gap_days), still guarded so a blank/future row is never selected.
    """
    if df is None or len(df) < 2:
        return df
    df = df.sort_values("release_dt")

    has_period = "period" in df.columns
    valid = _valid_period(df["period"]) if has_period else pd.Series(False, index=df.index)

    keep: list = []
    # exact period grouping
    if valid.any():
        sub = df[valid]
        for _, g in sub.groupby(sub["period"].astype("string").str.strip(), sort=False):
            keep.append(_keep_latest_published(g))
    # proximity fallback for rows lacking a period
    rest = df[~valid]
    if not rest.empty:
        if gap_days is None or gap_days <= 0:
            keep.extend(rest.index.tolist())  # no clustering, but the latest-actual
            #                                   guard in compute still excludes blanks
        else:
            rel = list(rest["release_dt"])
            idx = list(rest.index)
            cluster = {idx[0]: 0}
            cid = 0
            for i in range(1, len(rest)):
                if (rel[i] - rel[i - 1]).days > gap_days:
                    cid += 1
                cluster[idx[i]] = cid
            rest = rest.assign(_c=[cluster[i] for i in idx])
            for _, g in rest.groupby("_c", sort=False):
                keep.append(_keep_latest_published(g))

    return df.loc[sorted(set(keep))].sort_values("release_dt").reset_index(drop=True)


def _period_ts(v) -> pd.Timestamp | None:
    """Parse a calendar `period` value ('YYYY.MM.DD') to a Timestamp, or None."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in {"nan", "nat", "none", "0", "0.0"}:
        return None
    ts = pd.to_datetime(s, errors="coerce")
    return None if pd.isna(ts) else pd.Timestamp(ts)


# A newer scheduled release only SUPERSEDES the served print once it is OVERDUE by
# more than this normal reporting lag (days, by cadence). A just-due / recently-due
# release (actual still pending, e.g. a monthly PMI at 30d whose next print is due
# today) must NOT flag the current reading — that was the STRAT-2 false-positive.
# Chosen so no in-window cell is ever superseded on a normal lag: max in-window
# overdue is (window − cadence) ≈ 15 (monthly) / 19 (quarterly) / 7 (weekly).
_SUPERSEDE_GRACE = {"weekly": 7, "monthly": 20, "quarterly": 25}


def _superseded_missing(df: pd.DataFrame, latest: pd.Series, as_of: pd.Timestamp,
                        freq: str | None) -> bool:
    """STRAT 2 — the served (older) print is SUPERSEDED by a genuinely MISSED newer
    release: a scheduled row for a NEWER reference period whose date is OVERDUE by
    more than the normal reporting lag (`_SUPERSEDE_GRACE`) yet still has no valid
    `actual`. A release merely due today/recently does NOT supersede (its actual is
    pending). Only rule (2) excludes; there is no blind cadence rule. Never
    substitutes forecast/previous for actual.
    """
    served_rel = pd.Timestamp(latest["release_dt"])
    served_per = _period_ts(latest.get("period"))
    grace = pd.Timedelta(days=_SUPERSEDE_GRACE.get(freq, 20))

    rel = pd.to_datetime(df["release_dt"])
    overdue_missing = df[df["actual"].isna() & (rel <= as_of - grace) & (rel > served_rel)]
    for _, r in overdue_missing.iterrows():
        per = _period_ts(r.get("period"))
        if served_per is None or per is None or per > served_per:
            return True
    return False


def compute_indicator_score(
    sub_df: pd.DataFrame,
    indicator_cfg: dict,
    defaults: dict,
    as_of: pd.Timestamp,
    allow_stale: bool = False,
    currency: str | None = None,
) -> dict | None:
    """Score the latest release of one (currency, indicator).

    `sub_df` holds only that indicator's rows for one currency. Recency uses a
    per-frequency window (defaults.max_age_by_frequency, resolved via the
    indicator's frequency + per-currency override) so quarterly prints (GDP) are
    not dropped while still current. Flash/final double prints are collapsed to
    the final before scoring so the rolling sigma isn't polluted.

    Returns None if there is no release with a non-NaN `actual` at all (truly
    absent). If the latest actual is OUTSIDE the window:
      - allow_stale=False (default): returns None (stale never enters aggregation).
      - allow_stale=True: scores it but marks `stale: True` for greyed display.
    Returns: {actual, consensus, surprise, z, score, flag, release_dt, stale}.
    """
    if sub_df is None or sub_df.empty:
        return None

    window_k = int(defaults.get("surprise_window_k", 12))
    z_buckets = defaults.get("z_buckets", [1.0, 0.33])
    pct_buckets = defaults.get("pct_buckets", [0.10, 0.02])
    min_prints = int(defaults.get("fallback_min_prints", 6))
    sigma_method = str(defaults.get("sigma_method", "std")).lower()   # V4: std | mad
    quantize = str(defaults.get("quantize", "early")).lower()         # V4: early | late
    sigma_floor = float(indicator_cfg.get("sigma_floor", 0.0))        # V4 (mad path only)
    freq = effective_frequency(indicator_cfg, defaults, currency)
    max_age_days = _max_age_for(indicator_cfg, defaults, freq)
    dedup_gap = (defaults.get("dedup_gap_days", {}) or {}).get(freq)
    direction = float(indicator_cfg.get("direction", 1))

    df = sub_df.sort_values("release_dt").reset_index(drop=True)
    df = df.copy()
    df["actual"] = pd.to_numeric(df["actual"], errors="coerce")
    df["consensus"] = pd.to_numeric(df["consensus"], errors="coerce")
    # Collapse flash/final to one print per period (cleans the sigma baseline).
    df = _dedup_flash_final(df, dedup_gap)

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

    # STRAT 2 — expected-next-release gate: a newer release is due-but-missing (or
    # the served print is overdue vs cadence) → don't serve the old value as fresh.
    superseded = _superseded_missing(df, latest, as_of, freq)
    if superseded:
        stale = True

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
            "superseded_missing": superseded,
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
    if n_pairs >= 2:
        if sigma_method == "mad":
            # V4.1 robust σ on the SAME raw (pre-direction) surprises: 1.4826*MAD,
            # floored (mad path only). Reduces to a scale estimator like std for
            # Gaussian data but is outlier-resistant.
            med = float(diffs.median())
            sigma = 1.4826 * float((diffs - med).abs().median())
            sigma = max(sigma, sigma_floor)
        else:
            sigma = float(diffs.std(ddof=1))
    else:
        sigma = float("nan")

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
                "contribution": 0.0,
                "flag": "no_consensus",
                "release_dt": release_dt,
                "stale": stale,
                "superseded_missing": superseded,
            }
        pct = direction * surprise / abs(consensus)
        pct_score = bucket_score(pct, pct_buckets)
        return {
            "actual": actual,
            "consensus": consensus,
            "surprise": surprise,
            "z": None,
            "score": pct_score,
            "contribution": float(pct_score),   # fallback rule unchanged under late-quantize
            "flag": "fallback",
            "release_dt": release_dt,
            "stale": stale,
            "superseded_missing": superseded,
        }

    z = direction * surprise / sigma
    score = bucket_score(z, z_buckets)
    # V4.2 late-quantize: the per-indicator CONTRIBUTION to the category mean becomes
    # a continuous z-map; the DISPLAYED cell (`score`) stays bucketed. Under `early`
    # (production) contribution == score → byte-identical.
    contribution = float(score) if quantize != "late" else _late_quantize(z)
    return {
        "actual": actual,
        "consensus": consensus,
        "surprise": surprise,
        "z": z,
        "score": score,
        "contribution": contribution,
        "flag": None,
        "release_dt": release_dt,
        "stale": stale,
        "superseded_missing": superseded,
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
        scored = compute_indicator_score(sub, ind_cfg, defaults, as_of,
                                          allow_stale=True, currency=currency)
        if scored is None:
            continue
        breakdown[key] = scored
        cat = ind_cfg.get("category")
        if cat in per_cat and not scored.get("stale"):
            # V4: the category mean uses `contribution` (== score under early-quantize,
            # continuous under late). Falls back to score for any older-shaped entry.
            per_cat[cat].append((scored.get("contribution", scored["score"]),
                                 float(ind_cfg.get("weight", 1.0))))

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
        is_stale = bool(rate_entry.get("stale"))
        breakdown["rate_expectations"] = dict(rate_entry)
        # Mirror the calendar-indicator stale policy (see the `not scored.get("stale")`
        # guard above): a stale rate is kept for DISPLAY (cell value + stale flag,
        # coverage 0) but EXCLUDED from the currency index.
        categories_out["monetary"] = {
            "score_cell": _clamp_cell(float(rscore)),
            "score_precise": float(rscore),
            "coverage": 0 if is_stale else 1,
            "stale": is_stale,
        }
        if not is_stale:
            total_coverage += 1
            monetary_weight = float(
                (indicators_cfg.get("categories", {}).get("monetary", {}) or {}).get("weight", 1.0)
            )
            cat_scores_for_index.append((float(rscore), monetary_weight))

    if cat_scores_for_index:
        index_num = sum(p * w for p, w in cat_scores_for_index)
        index_wsum = sum(w for _, w in cat_scores_for_index)
        index = (index_num / index_wsum) * scale if index_wsum else 0.0
    else:
        index_num = 0.0
        index_wsum = 0.0
        index = 0.0

    return {
        "currency": currency,
        "index": float(index),
        # Pre-scale accumulators of the macro (category-only) weighted mean, so the
        # instrument layer can fold in the SENTIMENT factor without recomputing —
        # and WITHOUT touching `index`/`categories` (which cross-asset reads).
        "index_num": float(index_num),
        "index_wsum": float(index_wsum),
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


def _augmented_index(card: dict | None, sentiment_value, sentiment_weight: float,
                     scale: float) -> float:
    """Currency index × scale with the SENTIMENT factor folded into the weighted
    mean. `sentiment_value` None → factor absent (index identical to the macro-only
    `card["index"]`); a present value (incl. 0 for a USD pair leg) adds its weight
    to the mean. Reads the stored accumulators; never mutates the card.
    """
    if card is None:
        return 0.0
    num = float(card.get("index_num", 0.0))
    wsum = float(card.get("index_wsum", 0.0))
    if sentiment_value is not None:
        num += sentiment_weight * float(sentiment_value)
        wsum += sentiment_weight
    return (num / wsum) * scale if wsum else 0.0


def _leg_eff_wsum(card: dict | None, sentiment_value, sentiment_weight: float) -> float:
    """Effective total factor weight of a currency leg AFTER sentiment folding —
    the macro category weight sum (stored `index_wsum`) plus the sentiment weight
    when a sentiment value is present. Mirrors `_augmented_index`'s denominator.
    """
    if card is None:
        return 0.0
    wsum = float(card.get("index_wsum", 0.0))
    if sentiment_value is not None:
        wsum += sentiment_weight
    return wsum


def _fold_trend(macro_score: float, macro_weight: float, trend_value,
                trend_weight: float, scale: float) -> float:
    """Fold the per-instrument TREND cell DIRECTLY into the score as one more
    member of the existing weighted mean (NOT decomposed base−quote).

    `macro_score` is the existing macro+sentiment score (× scale); `macro_weight`
    is the aggregate weight that mean already represents (≈ the pair's leg weight,
    so trend's 0.5 keeps a cross-asset-comparable ~10% pull → scale preserved,
    bias thresholds unchanged). `trend_value` None → score returned unchanged
    (trend absent ⇒ bit-identical to the no-trend baseline).
    """
    if trend_value is None:
        return macro_score
    m = (macro_score / scale) if scale else 0.0          # pre-scale mean (~±2)
    w = float(macro_weight)
    if w <= 0:
        new_mean = float(trend_value)                    # trend is the only factor
    else:
        new_mean = (m * w + trend_weight * float(trend_value)) / (w + trend_weight)
    return new_mean * scale


def compute_instrument(
    symbol: str,
    inst_cfg: dict,
    scorecards: dict[str, dict],
    instruments_cfg: dict,
    sentiment_cells: dict | None = None,
    trend_cells: dict | None = None,
) -> dict:
    """Derive a single instrument payload from precomputed currency scorecards.

    `sentiment_cells` (optional) maps a CFTC COT symbol → its currency's COT cell
    (EUR/GBP/JPY/CHF/CAD/AUD/NZD + DXY for the dollar). When given, SENTIMENT
    enters each currency's index as a weight-`sentiment_weight` factor:
      - a PAIR leg of USD is ABSENT (excluded from that currency's mean — the
        contracts are already vs-USD, so the dollar must not add a sentiment leg);
      - the SINGLE US-dollar row uses the DXY contract cell (the dollar's own COT);
      - every other currency leg uses its own COT cell.
    `sentiment_cells` None → no sentiment (identical to the macro-only baseline).

    `trend_cells` (optional) maps an instrument board key → its TREND cell (±3).
    UNLIKE sentiment, trend is DIRECT on the pair (NOT decomposed base−quote):
    the pair's own price series gives one number, folded into the pair's weighted
    mean as a weight-`trend_weight` factor (see `_fold_trend`). A symbol absent
    here (e.g. the US-DOLLAR single row — DXY has no series) → trend excluded,
    score bit-identical to the no-trend baseline.
    """
    pair_divisor = float(instruments_cfg.get("pair_divisor", 2))
    thresholds = instruments_cfg.get("bias_thresholds", {}) or {}
    categories_display = instruments_cfg.get("categories_display", []) or []
    scale = float(instruments_cfg.get("scale", 5))
    sentiment_weight = float(instruments_cfg.get("sentiment_weight", 0.5))
    trend_weight = float(instruments_cfg.get("trend_weight", 0.5))
    sentiment_on = sentiment_cells is not None
    trend_value = trend_cells.get(symbol) if trend_cells is not None else None
    itype = inst_cfg.get("type")

    def _leg_sentiment(ccy: str, in_pair: bool):
        """SENTIMENT value for a currency leg, or None when the factor is absent.
        USD inside a pair → ABSENT (excluded from that currency's mean — the
        contracts are already vs-USD, so the dollar must not add a leg); the
        single US-dollar row uses the dollar's own DXY cell; any other currency →
        its own COT cell."""
        if not sentiment_on:
            return None
        if ccy == "USD":
            return None if in_pair else sentiment_cells.get("DXY")
        return sentiment_cells.get(ccy)

    if itype == "single":
        currency = inst_cfg["currency"]
        sign = float(inst_cfg.get("sign", 1))
        base_card = scorecards.get(currency)
        v_s = _leg_sentiment(currency, in_pair=False)
        macro_score = (_augmented_index(base_card, v_s, sentiment_weight, scale) * sign) if base_card else 0.0
        macro_weight = _leg_eff_wsum(base_card, v_s, sentiment_weight)
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
        v_s_base = _leg_sentiment(base, in_pair=True)
        v_s_quote = _leg_sentiment(quote, in_pair=True)
        base_idx = _augmented_index(base_card, v_s_base, sentiment_weight, scale) if base_card else 0.0
        quote_idx = _augmented_index(quote_card, v_s_quote, sentiment_weight, scale) if quote_card else 0.0
        macro_score = (base_idx - quote_idx) / pair_divisor
        # Pair aggregate weight = mean of the two legs' effective weights, so the
        # weight-0.5 trend factor pulls the pair's mean by a cross-asset-comparable
        # fraction (scale preserved → bias thresholds unchanged).
        macro_weight = (_leg_eff_wsum(base_card, v_s_base, sentiment_weight)
                        + _leg_eff_wsum(quote_card, v_s_quote, sentiment_weight)) / 2.0
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

    # Fold TREND directly into the pair/instrument score (one more weighted-mean
    # member). trend_value None → score == macro_score (bit-identical baseline).
    score = _fold_trend(macro_score, macro_weight, trend_value, trend_weight, scale)

    cells = _category_cells(inst_cfg, base_card, quote_card, categories_display, pair_divisor)

    return {
        "symbol": symbol,
        "display": inst_cfg.get("display", symbol),
        "type": itype,
        "score": float(score),
        "bias": bias_label(score, thresholds),
        "categories": cells,
        "breakdown": breakdown,
        # Display cell for the TREND column (same value folded into the score).
        "trend": None if trend_value is None else int(trend_value),
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
    sentiment_cells: dict | None = None,
    trend_cells: dict | None = None,
) -> dict:
    """Compute every currency scorecard and every instrument payload.

    Pure: `as_of` defaults to now (UTC, naive) but is best passed explicitly for
    deterministic tests. `rate_scores` (optional) is a {currency: RateScore}
    mapping from the rate engine; when given, each scored currency gains the
    standing `monetary` category. When None, behavior is identical to the
    surprise-only payload (full backward compatibility).
    `sentiment_cells` (optional) maps a COT symbol → its currency's COT cell;
    when given, SENTIMENT enters each instrument's score as a weight-0.5 factor
    (see compute_instrument). When None, scores are the macro-only baseline.
    `trend_cells` (optional) maps an instrument board key → its TREND cell (±3);
    when given, TREND is folded DIRECTLY into each instrument's score as a
    weight-0.5 factor. When None, scores are unchanged by trend.
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
        compute_instrument(sym, cfg, scorecards, instruments_cfg,
                           sentiment_cells=sentiment_cells, trend_cells=trend_cells)
        for sym, cfg in instruments.items()
    ]

    return {
        "as_of": as_of.isoformat(),
        "currencies": scorecards,
        "instruments": instrument_payloads,
    }
