"""MEASUREMENT INSTRUMENT — not production code. Read-only.

Shared loading + a re-aggregation harness that reuses the PRODUCTION scoring
functions (`compute_indicator_score`, `compute_instrument`, `_clamp_cell`)
unmodified, and swaps ONLY the category-inclusion rule that decides which
scored indicators feed a category's N / score_precise. This mirrors exactly
what a real code change would touch (economic_compute.compute_currency_
scorecard's `if cat in per_cat and not scored.get("stale")` line) without
editing src/.

Three inclusion rules:
  baseline  — current production behavior (exclude only `stale`)
  variant_a — blacklist: exclude `stale` OR (currency, indicator_key) in a
              fixed structural list
  variant_b — per-row: exclude `stale` OR flag == "no_consensus"
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import (  # noqa: E402
    compute_indicator_score,
    compute_instrument,
    _clamp_cell,
    _indicator_applies,
    _rate_entry_for,
)
from src.ff_scoring import build_matcher, to_scoring_frame  # noqa: E402
from src.rate_compute import compute_rate_scores  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
RATES_PARQUET = ROOT / "data" / "rates.parquet"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"

AS_OF = pd.Timestamp("2026-07-30")

# The 6 structurally-affected (currency, indicator_key) series per the
# pre-registered spec (verified in scripts/measure/inventory_no_consensus.py).
STRUCTURAL_BLACKLIST = {
    ("CAD", "core_cpi"),
    ("NZD", "manufacturing_pmi"),
    ("NZD", "services_pmi"),
    ("CAD", "manufacturing_pmi"),
    ("AUD", "manufacturing_pmi"),
    ("AUD", "services_pmi"),
}
# Same list minus CAD core_cpi, for the "combined with CAD promotion" scenario.
STRUCTURAL_BLACKLIST_MINUS_CAD_CORE = STRUCTURAL_BLACKLIST - {("CAD", "core_cpi")}


def load_configs() -> tuple[dict, dict]:
    with open(INDICATORS_YAML) as f:
        ind = yaml.safe_load(f) or {}
    with open(INSTRUMENTS_YAML) as f:
        inst = yaml.safe_load(f) or {}
    return ind, inst


def load_calendar() -> pd.DataFrame:
    ffdf = pd.read_parquet(FF_PARQUET)
    ffdf["datetime_utc"] = pd.to_datetime(ffdf["datetime_utc"])
    cal = to_scoring_frame(ffdf, build_matcher())
    cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    return cal


def rule_baseline(currency: str, key: str, scored: dict) -> bool:
    """True => EXCLUDE from N. Mirrors production exactly (stale only)."""
    return bool(scored.get("stale"))


def rule_variant_a(currency: str, key: str, scored: dict) -> bool:
    return bool(scored.get("stale")) or (currency, key) in STRUCTURAL_BLACKLIST


def rule_variant_a_minus_cad_core(currency: str, key: str, scored: dict) -> bool:
    return bool(scored.get("stale")) or (currency, key) in STRUCTURAL_BLACKLIST_MINUS_CAD_CORE


def rule_variant_b(currency: str, key: str, scored: dict) -> bool:
    return bool(scored.get("stale")) or scored.get("flag") == "no_consensus"


def compute_currency_scorecard_variant(
    calendar_df: pd.DataFrame,
    currency: str,
    indicators_cfg: dict,
    instruments_cfg: dict,
    as_of: pd.Timestamp,
    exclude_rule,
    rate_entry: dict | None = None,
) -> dict:
    """Re-aggregation harness. Calls the UNMODIFIED `compute_indicator_score`
    for every indicator (identical to economic_compute.compute_currency_
    scorecard), then applies `exclude_rule(currency, key, scored)` in place of
    the hardcoded `not scored.get("stale")` check. Everything else (category
    weighted-mean formula, `_clamp_cell`, index formula, monetary category
    handling) is copied verbatim from economic_compute.py so a variant's
    numbers are byte-for-byte comparable to production's.
    """
    defaults = indicators_cfg.get("defaults", {}) or {}
    indicators = indicators_cfg.get("indicators", {}) or {}
    categories_cfg = indicators_cfg.get("categories", {}) or {}
    scale = float(instruments_cfg.get("scale", 5))

    breakdown: dict[str, dict] = {}
    per_cat: dict[str, list[tuple[int, float]]] = {c: [] for c in categories_cfg}

    if calendar_df is not None and not calendar_df.empty:
        ccy_df = calendar_df[calendar_df["currency"] == currency]
    else:
        ccy_df = pd.DataFrame(columns=["currency", "indicator_key", "release_dt", "actual", "consensus"])

    for key, ind_cfg in indicators.items():
        if not _indicator_applies(currency, ind_cfg):
            continue
        sub = ccy_df[ccy_df["indicator_key"] == key] if not ccy_df.empty else ccy_df
        scored = compute_indicator_score(sub, ind_cfg, defaults, as_of,
                                          allow_stale=True, currency=currency)
        if scored is None:
            continue
        scored["category"] = cat = ind_cfg.get("category")
        breakdown[key] = scored
        if cat in per_cat and not exclude_rule(currency, key, scored):
            per_cat[cat].append((scored["score"], float(ind_cfg.get("weight", 1.0))))

    categories_out: dict[str, dict] = {}
    cat_scores_for_index: list[tuple[float, float]] = []
    total_coverage = 0
    for cat, cat_meta in categories_cfg.items():
        entries = per_cat.get(cat, [])
        coverage = len(entries)
        total_coverage += coverage
        weight = float(cat_meta.get("weight", 1.0))
        wsum = sum(w for _, w in entries)
        if coverage > 0 and wsum > 0:
            precise = sum(s * w for s, w in entries) / wsum
        else:
            precise = 0.0
        categories_out[cat] = {
            "score_cell": _clamp_cell(precise) if coverage > 0 else 0,
            "score_precise": float(precise),
            "coverage": coverage,
            "weight": weight,
        }
        if coverage > 0:
            cat_scores_for_index.append((precise, weight))

    if rate_entry is not None and rate_entry.get("score") is not None:
        rscore = int(rate_entry["score"])
        is_stale = bool(rate_entry.get("stale"))
        monetary_weight = float(
            (indicators_cfg.get("categories", {}).get("monetary", {}) or {}).get("weight", 1.0)
        )
        breakdown["rate_expectations"] = dict(rate_entry, category="monetary")
        categories_out["monetary"] = {
            "score_cell": _clamp_cell(float(rscore)),
            "score_precise": float(rscore),
            "coverage": 0 if is_stale else 1,
            "weight": monetary_weight,
            "stale": is_stale,
        }
        if not is_stale:
            total_coverage += 1
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
        "index_num": float(index_num),
        "index_wsum": float(index_wsum),
        "coverage": total_coverage,
        "categories": categories_out,
        "breakdown": breakdown,
    }


def _load_rate_scores(as_of: pd.Timestamp) -> dict:
    if not RATES_PARQUET.exists():
        return {}
    rates_df = pd.read_parquet(RATES_PARQUET)
    return compute_rate_scores(rates_df, as_of=as_of.date())


def build_scorecards(calendar_df: pd.DataFrame, indicators_cfg: dict, instruments_cfg: dict,
                     as_of: pd.Timestamp, exclude_rule) -> dict[str, dict]:
    instruments = instruments_cfg.get("instruments", {}) or {}
    needed: set[str] = set()
    for inst in instruments.values():
        if inst.get("type") == "single":
            needed.add(inst["currency"])
        elif inst.get("type") == "fx":
            needed.add(inst["base"])
            needed.add(inst["quote"])
    # Real monetary/rate-expectations category, fed IDENTICALLY into baseline
    # and every variant (the rate engine reads rates.parquet, never the
    # calendar — completely untouched by any no-consensus-slot rule) so the
    # monetary cell is byte-identical across baseline/A/B and any observed
    # difference is provably confined to growth/inflation/labour.
    rate_scores = _load_rate_scores(as_of)
    return {
        ccy: compute_currency_scorecard_variant(
            calendar_df, ccy, indicators_cfg, instruments_cfg, as_of, exclude_rule,
            rate_entry=_rate_entry_for(rate_scores.get(ccy)),
        )
        for ccy in sorted(needed)
    }


def build_instruments(scorecards: dict[str, dict], instruments_cfg: dict) -> list[dict]:
    instruments = instruments_cfg.get("instruments", {}) or {}
    return [
        compute_instrument(sym, cfg, scorecards, instruments_cfg)
        for sym, cfg in instruments.items()
    ]
