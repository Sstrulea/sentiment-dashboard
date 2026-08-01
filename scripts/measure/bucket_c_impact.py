"""eval/bucket-c-candidates — FAZA 2: impact measurement for the 17 "adauga"
candidates from FAZA 1 (docs/bucket-c-merit-evaluation.md).

Zero writes to data/economic_calendar_ff.parquet, zero config changes. Every
candidate's history is pulled directly from data/archive/ff_calendar_range.json
(read-only) into synthetic (currency, indicator_key, release_dt, actual,
consensus) rows, concatenated IN MEMORY onto the real production scoring
frame, scored with the real compute_currency_scorecard/compute_instrument
functions (mirrors the shrinkage_today.py precedent from
measure/coverage-asymmetry, and the promotion methodology used for CAD
Median CPI / Variant B). Macro-only throughout (rate_entry=None,
sentiment_cells=None, trend_cells=None) — same scope as every other script
in this measurement lineage.

The 7 "monetary" and 14 "intreaba" candidates from FAZA 1 are explicitly
OUT of this measurement — only the 17 "adauga" ones are scored here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.econ_calendar_ff import normalize_ff_value, jblanked_to_utc, OUR_CCYS  # noqa: E402
from src.economic_compute import compute_currency_scorecard, compute_instrument  # noqa: E402
from scripts.measure.reconstruct import (  # noqa: E402
    build_full_scoring_frame, load_yaml, INDICATORS_YAML, INSTRUMENTS_YAML,
)

ARCHIVE = ROOT / "data" / "archive" / "ff_calendar_range.json"
CALC_CATEGORIES = ["growth", "inflation", "labour"]

# --- The 17 "adauga" candidates, FAZA-1 rank order (strongest first) ------
# (rank, currency, name_raw, indicator_key)
FAZA1_ORDER = [
    (1, "JPY", "Tokyo Core CPI y/y", "tokyo_core_cpi_yoy"),
    (3, "GBP", "Industrial Production m/m", "industrial_production_mm"),
    (4, "USD", "Industrial Production m/m", "industrial_production_mm"),
    (5, "USD", "Durable Goods Orders m/m", "durable_goods_orders_mm"),
    (6, "JPY", "Core Machinery Orders m/m", "core_machinery_orders_mm"),
    (13, "USD", "Personal Spending m/m", "personal_spending_mm"),
    (14, "USD", "Personal Income m/m", "personal_income_mm"),
    (15, "USD", "Import Prices m/m", "import_prices"),
    (16, "AUD", "Private Capital Expenditure q/q", "capital_expenditure"),
    (17, "JPY", "Capital Spending q/y", "capital_expenditure"),
    (18, "AUD", "Company Operating Profits q/q", "company_operating_profits_qoq"),
    (19, "AUD", "Import Prices q/q", "import_prices"),
    (20, "JPY", "SPPI y/y", "sppi_yoy"),
    (21, "JPY", "Prelim GDP Price Index y/y", "gdp_price_index"),
    (22, "USD", "Advance GDP Price Index q/q", "gdp_price_index"),
    (23, "USD", "Prelim Unit Labor Costs q/q", "unit_labor_costs_qoq"),
    (24, "JPY", "Prelim Industrial Production m/m", "industrial_production_mm"),
]

# --- New indicator_key definitions (category/direction/frequency/currencies) ---
NEW_INDICATOR_DEFS = {
    "tokyo_core_cpi_yoy": {"category": "inflation", "direction": 1, "weight": 1.0,
                          "frequency": "monthly", "currencies": ["JPY"]},
    "industrial_production_mm": {"category": "growth", "direction": 1, "weight": 1.0,
                                "frequency": "monthly", "currencies": ["USD", "GBP", "JPY"]},
    "durable_goods_orders_mm": {"category": "growth", "direction": 1, "weight": 1.0,
                              "frequency": "monthly", "currencies": ["USD"]},
    "core_machinery_orders_mm": {"category": "growth", "direction": 1, "weight": 1.0,
                                "frequency": "monthly", "currencies": ["JPY"]},
    "personal_spending_mm": {"category": "growth", "direction": 1, "weight": 1.0,
                            "frequency": "monthly", "currencies": ["USD"]},
    "personal_income_mm": {"category": "growth", "direction": 1, "weight": 1.0,
                          "frequency": "monthly", "currencies": ["USD"]},
    "import_prices": {"category": "inflation", "direction": 1, "weight": 1.0,
                      "frequency": "monthly", "frequency_overrides": {"AUD": "quarterly"},
                      "currencies": ["USD", "AUD"]},
    "capital_expenditure": {"category": "growth", "direction": 1, "weight": 1.0,
                          "frequency": "quarterly", "currencies": ["AUD", "JPY"]},
    "company_operating_profits_qoq": {"category": "growth", "direction": 1, "weight": 1.0,
                                     "frequency": "quarterly", "currencies": ["AUD"]},
    "sppi_yoy": {"category": "inflation", "direction": 1, "weight": 1.0,
                "frequency": "monthly", "currencies": ["JPY"]},
    "gdp_price_index": {"category": "inflation", "direction": 1, "weight": 1.0,
                        "frequency": "quarterly", "currencies": ["USD", "JPY"]},
    "unit_labor_costs_qoq": {"category": "labour", "direction": 1, "weight": 1.0,
                            "frequency": "quarterly", "currencies": ["USD"]},
}

PMI_KEYS = ["manufacturing_pmi", "services_pmi"]


def candidate_rows(ccy: str, name: str, indicator_key: str) -> pd.DataFrame:
    """Synthetic scoring-frame rows for one candidate, straight from the
    read-only archive — never touches data/economic_calendar_ff.parquet."""
    data = json.load(open(ARCHIVE))
    recs = []
    for e in data:
        if e.get("Currency") != ccy or e.get("Name") != name:
            continue
        dt = jblanked_to_utc(e.get("Date", ""))
        if dt is None:
            continue
        try:
            a = normalize_ff_value(e.get("Actual"), name=name)
            f = normalize_ff_value(e.get("Forecast"), name=name)
        except ValueError:
            continue
        if pd.isna(a) or pd.isna(f):
            continue
        recs.append({"currency": ccy, "indicator_key": indicator_key,
                    "release_dt": pd.Timestamp(dt), "actual": a, "consensus": f})
    return pd.DataFrame(recs, columns=["currency", "indicator_key", "release_dt", "actual", "consensus"])


def build_augmented_indicators_cfg(base_cfg: dict) -> dict:
    import copy
    cfg = copy.deepcopy(base_cfg)
    cfg["indicators"].update(NEW_INDICATOR_DEFS)
    return cfg


def pmi_guard(cal: pd.DataFrame) -> dict:
    return {(ccy, key): int(((cal["currency"] == ccy) & (cal["indicator_key"] == key)).sum())
            for ccy in OUR_CCYS for key in PMI_KEYS}


def scorecards_and_instruments(cal: pd.DataFrame, ind_cfg: dict, inst_cfg: dict, as_of: pd.Timestamp):
    cards = {ccy: compute_currency_scorecard(cal, ccy, ind_cfg, inst_cfg, as_of, rate_entry=None)
            for ccy in OUR_CCYS}
    instruments = inst_cfg.get("instruments", {}) or {}
    insts = {sym: compute_instrument(sym, cfg, cards, inst_cfg, sentiment_cells=None, trend_cells=None)
            for sym, cfg in instruments.items()}
    return cards, insts


def category_snapshot(cards: dict) -> dict:
    out = {}
    for ccy in OUR_CCYS:
        entry = {}
        for cat in CALC_CATEGORIES:
            cell = cards[ccy]["categories"].get(cat, {}) or {}
            entry[cat] = cell.get("score_precise")
            entry[f"{cat}_n"] = cell.get("coverage", 0)
        out[ccy] = entry
    return out


if __name__ == "__main__":
    print("Module loaded — see analyze_incremental.py / analyze_usd_growth.py / analyze_tokyo_cpi.py")
