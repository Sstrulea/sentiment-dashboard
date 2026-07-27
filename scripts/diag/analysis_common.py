"""Shared loaders + cell-classification helpers for scripts/diag/section_*.py.

READ-ONLY. Reads only the pickled replay cache produced by run_replay.py plus
the YAML configs (for the applicable-indicator universe).
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import _indicator_applies  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent / "_cache"
DOCS_DIR = ROOT / "docs"

CATS = ["growth", "inflation", "labour", "monetary"]
SURPRISE_CATS = ["growth", "inflation", "labour"]  # calendar-driven (z/pct bucketed)
CCYS = ["AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD", "USD"]


def load_window() -> dict:
    with open(CACHE_DIR / "window_snapshots.pkl", "rb") as f:
        return pickle.load(f)


def load_today() -> dict:
    with open(CACHE_DIR / "today_snapshot.pkl", "rb") as f:
        return pickle.load(f)


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


INDICATORS_CFG = load_yaml(ROOT / "data" / "economic_indicators.yaml")
INSTRUMENTS_CFG = load_yaml(ROOT / "data" / "economic_instruments.yaml")
CROSSASSET_CFG = load_yaml(ROOT / "data" / "crossasset_instruments.yaml")

FX_PAIRS = [sym for sym, cfg in INSTRUMENTS_CFG["instruments"].items() if cfg["type"] == "fx"]
SINGLE_SYMS = [sym for sym, cfg in INSTRUMENTS_CFG["instruments"].items() if cfg["type"] == "single"]

# Surprise-based indicators actually feeding a category mean (category in
# growth/inflation/labour). `interest_rate_decision` (category="rates") is
# display-only (weight 0, never enters per_cat — see economic_compute.py) and
# is excluded from the applicable universe below.
_ALL_INDICATORS = INDICATORS_CFG.get("indicators", {}) or {}
SCORED_INDICATOR_KEYS = [
    k for k, cfg in _ALL_INDICATORS.items() if cfg.get("category") in SURPRISE_CATS
]


def applicable_indicators_for(ccy: str) -> list[str]:
    return [k for k in SCORED_INDICATOR_KEYS if _indicator_applies(ccy, _ALL_INDICATORS[k])]


def classify_cells_for_day(snapshot: dict) -> list[dict]:
    """One row per (currency, indicator) applicable cell for one snapshot day:
    {ccy, indicator_key, category, cause, score, stale}. `cause` in
    {"scored_nonzero", "dead_zone", "no_consensus", "no_data", "stale"}.
    Mirrors the exact inclusion logic of compute_currency_scorecard's per_cat
    accumulation (stale excluded from aggregation; category must be one of
    growth/inflation/labour to ever feed a mean)."""
    rows = []
    currencies = snapshot["currencies"]
    for ccy in CCYS:
        card = currencies.get(ccy, {})
        breakdown = card.get("breakdown", {}) or {}
        for key in applicable_indicators_for(ccy):
            cat = _ALL_INDICATORS[key]["category"]
            entry = breakdown.get(key)
            if entry is None:
                rows.append({"ccy": ccy, "indicator_key": key, "category": cat,
                             "cause": "no_data", "score": None, "stale": False})
                continue
            if entry.get("stale"):
                rows.append({"ccy": ccy, "indicator_key": key, "category": cat,
                             "cause": "stale", "score": entry.get("score"), "stale": True})
                continue
            if entry.get("flag") == "no_consensus":
                rows.append({"ccy": ccy, "indicator_key": key, "category": cat,
                             "cause": "no_consensus", "score": entry.get("score"), "stale": False})
                continue
            score = entry.get("score")
            if score == 0:
                rows.append({"ccy": ccy, "indicator_key": key, "category": cat,
                             "cause": "dead_zone", "score": 0, "stale": False})
            else:
                rows.append({"ccy": ccy, "indicator_key": key, "category": cat,
                             "cause": "scored_nonzero", "score": score, "stale": False})

        # monetary (rate_expectations) — separate engine (z-momentum on 2y yield),
        # not part of SCORED_INDICATOR_KEYS; classify with its own method/stale.
        entry = breakdown.get("rate_expectations")
        if entry is None:
            rows.append({"ccy": ccy, "indicator_key": "rate_expectations", "category": "monetary",
                         "cause": "no_data", "score": None, "stale": False})
        elif entry.get("stale"):
            rows.append({"ccy": ccy, "indicator_key": "rate_expectations", "category": "monetary",
                         "cause": "stale", "score": entry.get("score"), "stale": True})
        elif entry.get("score") == 0:
            rows.append({"ccy": ccy, "indicator_key": "rate_expectations", "category": "monetary",
                         "cause": "dead_zone", "score": 0, "stale": False})
        else:
            rows.append({"ccy": ccy, "indicator_key": "rate_expectations", "category": "monetary",
                         "cause": "scored_nonzero", "score": entry.get("score"), "stale": False})
    return rows
