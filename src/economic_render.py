"""Render the Economic Dashboard page (HTML + JSON).

Read-only over the scoring layer: it reads data/economic_calendar.parquet and
the two YAML configs, calls the PURE `economic_compute.build_payload`, enriches
the per-indicator breakdown with display metadata (labels, recency, stale flag),
and writes:

    public/data/economic.json
    public/economic.html

No scoring or thresholds live here — all of that is in economic_compute.py and
the YAML (the authoritative source). This module only shapes the payload for the
client and renders the Jinja template, mirroring the existing COT/Retail render
modules.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.economic_compute import build_payload, bias_label
from src.cot_score import (
    load_currencies_history,
    load_metals_history,
    pair_cot,
    score_currencies,
    score_metals,
)
from src.sentiment_compute import compute_pc_metrics, pc_index_score
from src.rate_compute import compute_pair_spread_scores, compute_rate_scores
from src.realyield_compute import compute_realyield_score
from src.liquidity_compute import compute_liquidity_score
from src.crossasset_compute import compute_crossasset_scores, LIQUIDITY_SERIES_LABELS
from src.trend_score import score_all as trend_score_all
from src.static_assets import copy_static_assets
from src.price_fetch import SYMBOLS_YAML as PRICE_SYMBOLS_YAML, load_symbol_map as _load_price_symbol_map
from src.price_freshness_guard import per_instrument_freshness, freshness_report

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = ROOT / "templates"
PUBLIC_DIR = ROOT / "public"
PARQUET = ROOT / "data" / "economic_calendar.parquet"
MANUAL_ACTUALS_OVERRIDES = ROOT / "data" / "manual_actuals_overrides.json"
RATES_PARQUET = ROOT / "data" / "rates.parquet"
REAL_YIELDS_PARQUET = ROOT / "data" / "real_yields.parquet"
NET_LIQUIDITY_PARQUET = ROOT / "data" / "net_liquidity.parquet"
PRICE_HISTORY_PARQUET = ROOT / "data" / "price_history.parquet"

# Per-source freshness thresholds (days). Beyond this, a source is flagged STALE
# (visible in economic.json + the dashboard badge + the cron watchdog) so a
# silent freeze surfaces immediately. General — applies to every source, not a
# single indicator. `actuals_pull` tracks the daily JBlanked pull (the weekly
# calendar feed is structurally actual-less): no successful pull for >2 days →
# its own distinct badge, separate from calendar-stale (compared with >=, see
# _freshness — the 2026-07-03..12 actuals freeze was invisible in logs).
# `price` has no fixed threshold here: it's derived per instrument in
# src.price_freshness_guard (each instrument's own trailing gap history), not
# a single flat day count — see the "price" block in _freshness() below.
FRESHNESS_STALE_DAYS = {"calendar": 3, "actuals_pull": 2}
PIPELINE_YAML = ROOT / "config" / "pipeline.yaml"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"
CROSSASSET_YAML = ROOT / "data" / "crossasset_instruments.yaml"
PC_HISTORY_PARQUET = ROOT / "data" / "pc_history.parquet"
PC_THRESHOLDS_YAML = ROOT / "data" / "pc_thresholds.yaml"

# Display labels for the cross-asset top-level factors + instruments.
CROSSASSET_FACTOR_LABELS = {
    "growth": "Growth", "inflation": "Inflation", "labour": "Labour Market",
    "rates": "Rates & Liquidity",
}
CROSSASSET_DISPLAY = {
    "DJIA": "Dow 30", "SP500": "S&P 500", "NASDAQ": "Nasdaq 100",
    "DAX": "DAX 40", "NIKKEI": "Nikkei 225", "FTSE100": "FTSE 100",
    "GOLD": "Gold", "SILVER": "Silver",
}

# FX-style sub-column layout for the cross-asset table: each top-level factor is
# a column GROUP with sub-columns showing the home-currency raw indicator scores
# (growth/inflation/labour reuse the FX indicator keys); the `rates` group's
# sub-columns are the rate composite's components (room for Balance Sheet later).
CROSSASSET_TABLE_LAYOUT = [
    {"key": "growth", "label": "Growth", "columns": [
        ("manufacturing_pmi", "Mfg PMI"), ("services_pmi", "Services"),
        ("gdp_qoq", "GDP"), ("retail_sales", "Retail")]},
    {"key": "inflation", "label": "Inflation", "columns": [
        ("cpi_yoy", "CPI"), ("core_cpi", "Core CPI"),
        ("core_pce", "Core PCE"), ("ppi_yoy", "PPI")]},
    {"key": "labour", "label": "Labour Market", "columns": [
        ("employment_change", "NFP/Emp"), ("unemployment_rate", "Unemp"),
        ("wage_growth", "Wages"), ("adp", "ADP"), ("jolts", "JOLTS"),
        ("jobless_claims", "Claims")]},
    {"key": "rates", "label": "Rates & Liquidity", "columns": [
        ("rate_exp_2y", "Rate Exp 2Y"), ("real_yield_10y", "10Y Real Yield"),
        ("balance_sheet", "Bank Reserves")]},
]
# Home-ccy indicator keys to read from the FX breakdown for the sub-columns.
CROSSASSET_CATEGORY_KEYS = [
    k for g in CROSSASSET_TABLE_LAYOUT if g["key"] != "rates" for (k, _) in g["columns"]
]
# indicator_key -> its category (for applying the per-asset category sign to cells).
CROSSASSET_KEY_CATEGORY = {
    k: g["key"] for g in CROSSASSET_TABLE_LAYOUT if g["key"] != "rates"
    for (k, _) in g["columns"]
}

# Human-readable labels for the breakdown modal (presentation only — scoring is
# untouched). Keyed by the taxonomy indicator_key.
INDICATOR_LABELS = {
    "cpi_yoy": "CPI (YoY)",
    "core_cpi": "Core CPI",
    "ppi_yoy": "PPI",
    "core_pce": "Core PCE",
    "gdp_qoq": "GDP (QoQ)",
    "manufacturing_pmi": "Manufacturing PMI",
    "services_pmi": "Services PMI",
    "retail_sales": "Retail Sales",
    "employment_change": "Employment Change",
    "unemployment_rate": "Unemployment Rate",
    "wage_growth": "Wage Growth",
    "adp": "ADP Employment",
    "jolts": "JOLTS Job Openings",
    "jobless_claims": "Jobless Claims",
    "interest_rate_decision": "Interest Rate Decision",
    "rate_expectations": "Rate Expectations (2y)",
    "cpi_monthly": "Monthly CPI Indicator (m/m)",
    "trimmed_mean_cpi_monthly": "Trimmed Mean CPI (m/m)",
    "common_cpi_yoy": "Common CPI (y/y)",
    "median_cpi_yoy": "Median CPI (y/y)",
    "trimmed_cpi_yoy": "Trimmed CPI (y/y)",
    "household_spending": "Household Spending (m/m)",
    # Bucket-C candidates, approved 2026-08-01 (docs/bucket-c-merit-evaluation.md).
    "tokyo_core_cpi_yoy": "Tokyo Core CPI (YoY)",
    "industrial_production_mm": "Industrial Production (MoM)",
    "durable_goods_orders_mm": "Durable Goods Orders (MoM)",
    "core_machinery_orders_mm": "Core Machinery Orders (MoM)",
    "personal_spending_mm": "Personal Spending (MoM)",
    "personal_income_mm": "Personal Income (MoM)",
    "import_prices": "Import Prices",
    "capital_expenditure": "Capital Expenditure",
    "company_operating_profits_qoq": "Company Operating Profits (QoQ)",
    "sppi_yoy": "Services PPI (YoY)",
    "gdp_price_index": "GDP Price Index",
    "unit_labor_costs_qoq": "Unit Labor Costs (QoQ)",
}

# Display-only unit hints for the breakdown table (Actual/Forecast/Previous
# columns) — presentation only, never scales the underlying value. Checked
# against data/economic_calendar_ff.parquet raw `actual` magnitudes (2026-08-17):
# employment_change/adp/jobless_claims already arrive in thousands (e.g. NFP
# prints as -23.0, not -23000) so "K" is a label, not a divisor; jolts arrives
# in millions (6.5..11.0) so "M"; everything else in the taxonomy is a
# percent or an index level (PMI). A key absent here (should not happen —
# every INDICATOR_LABELS key is covered) falls back to no suffix via
# DEFAULT_INDICATOR_UNIT, never raises.
DEFAULT_INDICATOR_UNIT = {"suffix": "", "decimals": 1}

INDICATOR_UNITS: dict[str, dict] = {
    "cpi_yoy": {"suffix": "%", "decimals": 1},
    "core_cpi": {"suffix": "%", "decimals": 1},
    "ppi_yoy": {"suffix": "%", "decimals": 1},
    "core_pce": {"suffix": "%", "decimals": 1},
    "gdp_qoq": {"suffix": "%", "decimals": 1},
    "manufacturing_pmi": {"suffix": "", "decimals": 1},
    "services_pmi": {"suffix": "", "decimals": 1},
    "retail_sales": {"suffix": "%", "decimals": 1},
    "employment_change": {"suffix": "K", "decimals": 0},
    "unemployment_rate": {"suffix": "%", "decimals": 1},
    "wage_growth": {"suffix": "%", "decimals": 1},
    "adp": {"suffix": "K", "decimals": 0},
    "jolts": {"suffix": "M", "decimals": 2},
    "jobless_claims": {"suffix": "K", "decimals": 0},
    "interest_rate_decision": {"suffix": "%", "decimals": 2},
    # rate_expectations has no calendar actual/consensus — its closest analog
    # is `latest_yield` (a 2y yield %), read that way by the /strength drilldown.
    "rate_expectations": {"suffix": "%", "decimals": 2},
    "cpi_monthly": {"suffix": "%", "decimals": 1},
    "trimmed_mean_cpi_monthly": {"suffix": "%", "decimals": 1},
    "common_cpi_yoy": {"suffix": "%", "decimals": 1},
    "median_cpi_yoy": {"suffix": "%", "decimals": 1},
    "trimmed_cpi_yoy": {"suffix": "%", "decimals": 1},
    "household_spending": {"suffix": "%", "decimals": 1},
    "tokyo_core_cpi_yoy": {"suffix": "%", "decimals": 1},
    "industrial_production_mm": {"suffix": "%", "decimals": 1},
    "durable_goods_orders_mm": {"suffix": "%", "decimals": 1},
    "core_machinery_orders_mm": {"suffix": "%", "decimals": 1},
    "personal_spending_mm": {"suffix": "%", "decimals": 1},
    "personal_income_mm": {"suffix": "%", "decimals": 1},
    "import_prices": {"suffix": "%", "decimals": 1},
    "capital_expenditure": {"suffix": "%", "decimals": 1},
    "company_operating_profits_qoq": {"suffix": "%", "decimals": 1},
    "sppi_yoy": {"suffix": "%", "decimals": 1},
    "gdp_price_index": {"suffix": "%", "decimals": 1},
    "unit_labor_costs_qoq": {"suffix": "%", "decimals": 1},
}

# Currency-Strength (/strength.html) display constant — pct = clamp(50 +
# strength_score * K, 0, 100). Re-measured for Strength-as-aggregate-of-pairs
# (audit B1) with the SAME method as the first calibration
# (scripts/measure/strength_index_distribution.py, 2026-08-17: K = 40 / p95,
# rounded to 0.5): scripts/measure/strength_pairs_distribution.py, weekly
# point-in-time 2025-09-24 .. 2026-09-23 (53 weeks, 424 (currency, week)
# observations, snapshot 4ace910 + audit phases 1-2): p95(|score|) = 1.817 ->
# K_raw = 22.017 -> K = 22.0. Saturation at |score| >= 2.273. (Was 10.5 on the
# old per-currency index.) Changing it requires re-running that script.
STRENGTH_PCT_K = 22.0

# Per-currency label overrides for cpi_yoy / core_cpi / ppi_yoy — the global
# INDICATOR_LABELS text ("CPI (YoY)", "Core CPI", "PPI") is accurate for SOME
# currencies but not others, because these 3 indicator_keys are fed by
# whatever inflation print each central bank actually publishes on the FF
# calendar (config/ff_aliases.yaml aliases a currency's real release onto a
# shared canonical name so the SAME matcher rule in
# data/economic_indicators.yaml can route it — see e.g. CAD's "CPI m/m" ->
# "CPI y/y" `# xf` alias). The z-score itself is unaffected (name_raw is
# 100% homogeneous per (currency, indicator_key) pair — verified against
# data/economic_calendar_ff.parquet 2026-08-18, see the investigation this
# fixes), but the DISPLAYED unit was wrong: "CAD CPI (YoY) = -0.4%" reads as
# deflation when the underlying print is a single month's m/m change.
#
# Built from an exhaustive enumeration of every (currency, indicator_key)
# pair's dominant name_raw for these 3 keys (script run 2026-08-18, no
# currency assumed clean without checking): entries below are exactly the
# pairs whose actual transform/series differs from what the generic label
# implies. (AUD, core_cpi) has a matcher rule (Australia: '^RBA Trimmed Mean
# CPI y/y$' -> core_cpi) but core_cpi's own `currencies:` whitelist
# (data/economic_indicators.yaml) excludes AUD, so that rule is permanently
# dead — confirmed absent from every AUD breakdown; deliberately NOT listed
# here (would be an override for a key that can never render).
#
# {"label": display text, "transform": normalized period tag for the
#  pasul-3 pair-cell mismatch check below}.
INDICATOR_LABEL_OVERRIDES: dict[tuple[str, str], dict] = {
    ("CAD", "cpi_yoy"):  {"label": "CPI (MoM)",              "transform": "MoM"},  # fed by "CPI m/m"
    ("CAD", "ppi_yoy"):  {"label": "IPPI (MoM)",              "transform": "MoM"},  # fed by "IPPI m/m" (Industrial Product Price Index, not PPI)
    ("CAD", "core_cpi"): {"label": "Median CPI (YoY)",        "transform": "YoY"},  # fed by "Median CPI y/y" — a DIFFERENT BoC series, not a core-CPI variant
    ("CHF", "cpi_yoy"):  {"label": "CPI (MoM)",                "transform": "MoM"},  # fed by "CPI m/m"
    ("CHF", "ppi_yoy"):  {"label": "PPI (MoM)",                "transform": "MoM"},  # fed by "PPI m/m"
    ("NZD", "cpi_yoy"):  {"label": "CPI (QoQ)",                "transform": "QoQ"},  # NZ CPI is quarterly, fed by "CPI q/q"
    ("NZD", "ppi_yoy"):  {"label": "PPI Output (QoQ)",         "transform": "QoQ"},  # fed by "PPI Output q/q"
    ("USD", "core_cpi"): {"label": "Core CPI (MoM)",           "transform": "MoM"},  # fed by "Core CPI m/m"
    ("USD", "ppi_yoy"):  {"label": "PPI (MoM)",                "transform": "MoM"},  # fed by "PPI m/m"
    ("EUR", "ppi_yoy"):  {"label": "PPI (MoM)",                "transform": "MoM"},  # fed by "PPI m/m"
    ("GBP", "ppi_yoy"):  {"label": "PPI Output (MoM)",         "transform": "MoM"},  # fed by "PPI Output m/m"
    ("JPY", "cpi_yoy"):  {"label": "National Core CPI (YoY)",  "transform": "YoY"},  # fed by "National Core CPI y/y" — Japan's own ex-fresh-food core measure, not a headline CPI
    ("AUD", "ppi_yoy"):  {"label": "PPI (QoQ)",                "transform": "QoQ"},  # AUD PPI is quarterly, fed by "PPI q/q"
    # GBP employment_change is fed by "Claimant Count Change" (UK matcher
    # rule), not a jobs-created series like the rest of this indicator_key —
    # display label only; the polarity fix itself is
    # data/economic_indicators.yaml's direction_overrides (fix/claimant-
    # count-polarity, 2026-08), a separate mechanism from this one.
    ("GBP", "employment_change"): {"label": "Claimant Count Change"},
}

# Default transform for a currency/indicator pair with NO entry above — i.e.
# the generic INDICATOR_LABELS text is already correct for that currency
# (verified in the same enumeration). Scoped to exactly the 3 keys audited
# above; a key absent here has no determinable transform and is never
# compared (pasul 3's mismatch check simply skips it) rather than guessing.
INDICATOR_DEFAULT_TRANSFORM: dict[str, str] = {
    "cpi_yoy": "YoY",
    "core_cpi": "YoY",
    "ppi_yoy": "YoY",
}


def _indicator_transform(currency: str, key: str) -> str | None:
    """The verified transform tag ("MoM"/"QoQ"/"YoY") for (currency, key), or
    None if not determinable (key outside the audited set)."""
    override = INDICATOR_LABEL_OVERRIDES.get((currency, key))
    if override is not None:
        return override.get("transform")
    return INDICATOR_DEFAULT_TRANSFORM.get(key)


def _indicator_label_for(currency: str, key: str) -> str:
    """Per-currency display label: override when the data disagrees with the
    generic name, else the global INDICATOR_LABELS text."""
    override = INDICATOR_LABEL_OVERRIDES.get((currency, key))
    if override is not None:
        return override["label"]
    return INDICATOR_LABELS.get(key, key)


CATEGORY_LABEL_FALLBACK = {
    "growth": "Growth",
    "inflation": "Inflation",
    "labour": "Labour Market",
    "monetary": "Monetary Policy",
    # "(display-only)" dropped 2026-08 — static/economic-chart.js's legHtml
    # already appends its own "display-only" badge, DERIVED from data (absence
    # of the key in card.categories, not this label) — the parenthetical was
    # hardcoded on top of it, doubling to "X (DISPLAY-ONLY) DISPLAY-ONLY" in
    # the drawer, and would go stale the moment a category like this one is
    # ever promoted (as inflation_display's AUD members were). Keep the badge,
    # drop the parenthetical. CROSSASSET_TABLE_LAYOUT's "Rates & Liquidity" is
    # a separate, hardcoded label in its own literal list below — unaffected.
    "rates": "Rates",
    "inflation_display": "Inflation",
    "growth_display": "Growth",
}

# Dense per-indicator table layout (EdgeFinder-style): one column per indicator,
# grouped under category headers. `interest_rate_decision` is intentionally
# omitted (display-only, weight 0) — it stays in the modal. Short labels are the
# column headers. This is presentation only; scoring is untouched.
TABLE_LAYOUT = [
    {"category": "growth", "columns": [
        ("manufacturing_pmi", "Mfg PMI"),
        ("services_pmi", "Services"),
        ("gdp_qoq", "GDP"),
        ("retail_sales", "Retail"),
    ]},
    {"category": "inflation", "columns": [
        ("cpi_yoy", "CPI"),
        ("core_cpi", "Core CPI"),
        ("core_pce", "Core PCE"),
        ("ppi_yoy", "PPI"),
    ]},
    {"category": "labour", "columns": [
        ("employment_change", "NFP/Emp"),
        ("unemployment_rate", "Unemp"),
        ("wage_growth", "Wages"),
        ("adp", "ADP"),
        ("jolts", "JOLTS"),
        ("jobless_claims", "Claims"),
    ]},
    {"category": "monetary", "columns": [
        ("rate_expectations", "Rate Exp (2y)"),
    ]},
]

TABLE_COLUMN_KEYS = [k for g in TABLE_LAYOUT for (k, _) in g["columns"]]


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _jsonable(v: Any) -> Any:
    """Recursively coerce a value to JSON-safe form (NaN->None, Timestamp->iso)."""
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) else v
    if isinstance(v, (pd.Timestamp, datetime)):
        return pd.Timestamp(v).isoformat()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
        return None if isinstance(v, float) and math.isnan(v) else v
    return v


def _build_meta(indicators_cfg: dict, instruments_cfg: dict) -> dict:
    """Static display metadata the client needs: labels, categories, thresholds."""
    defaults = indicators_cfg.get("defaults", {}) or {}
    indicators = indicators_cfg.get("indicators", {}) or {}
    categories_cfg = indicators_cfg.get("categories", {}) or {}

    ind_meta: dict[str, dict] = {}
    for key, cfg in indicators.items():
        ind_meta[key] = {
            "label": INDICATOR_LABELS.get(key, key),
            "category": cfg.get("category"),
            "pillar": cfg.get("pillar"),
            "direction": int(cfg.get("direction", 1)),
            "weight": float(cfg.get("weight", 1.0)),
            "max_age_days": int(cfg.get("max_age_days", defaults.get("max_age_days", 120))),
            "unit": INDICATOR_UNITS.get(key, DEFAULT_INDICATOR_UNIT),
        }
    # Synthetic meta for the standing rate sub-indicator (not in the YAML taxonomy).
    ind_meta["rate_expectations"] = {
        "label": INDICATOR_LABELS["rate_expectations"],
        "category": "monetary",
        "pillar": "monetary",
        "direction": 1,
        "weight": 1.0,
        "max_age_days": 7,
        "unit": INDICATOR_UNITS.get("rate_expectations", DEFAULT_INDICATOR_UNIT),
    }

    cat_meta: dict[str, dict] = {}
    for key in list(categories_cfg) + ["monetary", "rates", "inflation_display", "growth_display"]:
        cat_meta[key] = {
            "label": (categories_cfg.get(key, {}) or {}).get(
                "label", CATEGORY_LABEL_FALLBACK.get(key, key.title())
            )
        }

    table_layout = [
        {
            "category": g["category"],
            "label": cat_meta.get(g["category"], {}).get("label", g["category"].title()),
            "columns": [{"key": k, "label": lbl} for k, lbl in g["columns"]],
        }
        for g in TABLE_LAYOUT
    ]

    # Per-currency label overrides — {currency: {indicator_key: label}} — for
    # the drilldown/modal contexts (/economic's per-leg breakdown, /strength's
    # drilldown), which know both currency and indicator_key. The dense
    # table's own column headers stay global (table_layout above, untouched):
    # a column is shared across every row, so it can't carry a per-currency
    # label. See INDICATOR_LABEL_OVERRIDES's docstring for how each entry
    # was verified.
    label_overrides: dict[str, dict[str, str]] = {}
    for (ccy, key), entry in INDICATOR_LABEL_OVERRIDES.items():
        label_overrides.setdefault(ccy, {})[key] = entry["label"]

    # Per-currency direction overrides — {currency: {indicator_key: direction}},
    # sparse, only where it differs from the global direction (mirrors
    # data/economic_indicators.yaml's own direction_overrides, e.g. GBP
    # employment_change/"Claimant Count Change" — see effective_direction in
    # economic_compute.py). The "⤵ Inverted" badge checks this first, falls
    # back to indicators[key].direction (global) when absent.
    direction_overrides: dict[str, dict[str, int]] = {}
    for key, cfg in indicators.items():
        for ccy, d in (cfg.get("direction_overrides") or {}).items():
            if int(d) != int(cfg.get("direction", 1)):
                direction_overrides.setdefault(ccy, {})[key] = int(d)

    return {
        "categories": cat_meta,
        "indicators": ind_meta,
        "indicator_label_overrides": label_overrides,
        "indicator_direction_overrides": direction_overrides,
        "categories_display": instruments_cfg.get("categories_display", []),
        "table_layout": table_layout,
        "bias_thresholds": instruments_cfg.get("bias_thresholds", {}),
        "scale": instruments_cfg.get("scale"),
        "pair_divisor": instruments_cfg.get("pair_divisor"),
        # /strength.html: single source of truth for the pct<->index relationship
        # (pct = clamp(50 + index*K, 0, 100)), so strength.js never hardcodes K —
        # see STRENGTH_PCT_K's docstring above for how it was measured.
        "strength_pct_k": STRENGTH_PCT_K,
    }


def _build_indicator_cells(payload: dict, instruments_cfg: dict) -> None:
    """Attach a per-indicator cell to each instrument for the dense table.

    Pure recombination of the already-computed per-indicator scores (no scoring):
      - single  -> currency's indicator score × sign
      - fx pair -> base score − quote score (a leg missing the indicator counts
                   as 0; if BOTH legs lack it — e.g. a USD-only indicator on a
                   non-USD cross — the cell is None and renders as “—”).
    Each cell is {"v": int|None, "stale": bool}; `stale` is true when any
    contributing leg's indicator is stale (shown greyed, not folded into scoring).

    `transform_mismatch` (fx pairs only): True when BOTH legs have a
    determinable transform (INDICATOR_DEFAULT_TRANSFORM / the "transform" tag
    in INDICATOR_LABEL_OVERRIDES — the same verified data behind the label
    overrides, never a fresh guess) and they differ — e.g. CAD's cpi_yoy is
    MoM, USD's is YoY, so a CAD/USD cell is subtracting two different kinds
    of surprise. Absent (not just False) when either leg's transform isn't
    determinable, so the UI can tell "checked, no mismatch" from "not
    checked" if it ever needs to.
    """
    currencies = payload.get("currencies", {}) or {}
    inst_cfg = instruments_cfg.get("instruments", {}) or {}

    def _bd(ccy: str | None) -> dict:
        return (currencies.get(ccy, {}) or {}).get("breakdown", {}) or {}

    for inst in payload.get("instruments", []):
        cfg = inst_cfg.get(inst["symbol"], {})
        cells: dict[str, dict] = {}
        bdn = inst.get("breakdown", {}) or {}
        base_ccy = (bdn.get("base") or {}).get("currency")
        quote_ref = bdn.get("quote")
        quote_ccy = quote_ref.get("currency") if quote_ref else None

        if inst.get("type") == "single":
            sign = float(cfg.get("sign", 1))
            bd = _bd(base_ccy)
            for k in TABLE_COLUMN_KEYS:
                e = bd.get(k)
                if e is None:
                    cells[k] = {"v": None, "stale": False}
                else:
                    cells[k] = {"v": int(round(e["score"] * sign)), "stale": bool(e.get("stale"))}
        else:
            bb, bq = _bd(base_ccy), _bd(quote_ccy)
            mp = inst.get("monetary_pair")
            for k in TABLE_COLUMN_KEYS:
                eb, eq = bb.get(k), bq.get(k)
                if k == "rate_expectations" and mp:
                    # audit 5B: the pair's monetary cell is its 2y-spread m
                    cells[k] = {"v": int(mp["m"]), "stale": False, "pair_spread": True}
                    continue
                if eb is None and eq is None:
                    cells[k] = {"v": None, "stale": False}
                else:
                    v = (eb["score"] if eb else 0) - (eq["score"] if eq else 0)
                    stale = bool((eb and eb.get("stale")) or (eq and eq.get("stale")))
                    cell = {"v": int(v), "stale": stale}
                    if eb is not None and eq is not None and base_ccy and quote_ccy:
                        bt = _indicator_transform(base_ccy, k)
                        qt = _indicator_transform(quote_ccy, k)
                        if bt is not None and qt is not None and bt != qt:
                            cell["transform_mismatch"] = True
                            cell["transform_tip"] = (
                                f"{base_ccy}: {_indicator_label_for(base_ccy, k)} · "
                                f"{quote_ccy}: {_indicator_label_for(quote_ccy, k)} — "
                                "transformări diferite"
                            )
                    cells[k] = cell
        inst["indicator_cells"] = cells


def _previous_lookup(cal: pd.DataFrame) -> dict[tuple[str, str, pd.Timestamp], Any]:
    """{(currency, indicator_key, release_dt): previous} straight off the scoring
    calendar frame `cal` (SCORING_COLUMNS already carries `previous` from the
    parquet row that produced it, FF and MT5 alike — see ff_scoring.SCORING_COLUMNS
    and manual_actuals.apply_overrides). No fallback: a row whose source never
    had a `previous` value stays absent from this map (looked up as None)."""
    if cal is None or cal.empty or "previous" not in cal.columns:
        return {}
    return {
        (row.currency, row.indicator_key, pd.Timestamp(row.release_dt)): row.previous
        for row in cal.itertuples(index=False)
    }


def _enrich_breakdowns(payload: dict, ind_meta: dict, as_of: pd.Timestamp,
                       previous_lookup: dict | None = None) -> None:
    """Add recency (release_dt iso, age_days) and `previous` to every
    per-indicator entry.

    Mutates in place. `stale` is set authoritatively by compute (an indicator is
    stale iff its latest actual is older than max_age_days — it's then shown but
    excluded from every average/index); here we only add the age in days and a
    defensive fallback if compute didn't set the flag. `previous` (the print
    BEFORE the one that produced this score) is looked up from `previous_lookup`
    (built off the same calendar frame that was scored) by the exact
    (currency, indicator_key, release_dt) that produced this entry — None if the
    entry has no release_dt (e.g. the synthetic rate_expectations entry) or the
    source never carried a `previous` for that row.
    """
    previous_lookup = previous_lookup or {}
    for ccy, card in payload.get("currencies", {}).items():
        for key, entry in (card.get("breakdown") or {}).items():
            rdt = entry.get("release_dt")
            ts = pd.Timestamp(rdt) if rdt is not None else None
            max_age = (ind_meta.get(key, {}) or {}).get("max_age_days", 120)
            if ts is not None and not pd.isna(ts):
                age = int((as_of - ts).days)
                entry["age_days"] = age
                entry.setdefault("stale", age > max_age)
            else:
                entry["age_days"] = None
                entry.setdefault("stale", False)
            prev = previous_lookup.get((ccy, key, ts)) if ts is not None else None
            entry["previous"] = None if prev is None or (
                isinstance(prev, float) and math.isnan(prev)
            ) else float(prev)


FUND_EXCLUDED_KEYS = ("sentiment", "trend")


def _pair_fund_score(inst: dict) -> float:
    """A pair's FUNDAMENTAL score: compute_instrument's `fund_score`
    (= macro_score_no_sentiment: D1=D intersection, no COT, no trend), which the
    sum of its fund contributions reconstructs (the fallback)."""
    if inst.get("fund_score") is not None:
        return float(inst["fund_score"])
    return float(sum(c["contribution"] for c in inst.get("contributions", [])
                     if c.get("key") not in FUND_EXCLUDED_KEYS))


def strength_from_pairs(payload: dict) -> dict:
    """/strength as an aggregate of the /economic pairs (audit B1).

    scores[ccy] = mean over the pairs ccy belongs to (7 on the 8-currency board)
    of the pair's fundamental score, + when ccy is the base, - when it is the
    quote. The monetary category enters a pair only when both legs have it (the
    D1=D rule of /economic) — no second mechanism. Scores are in pair units, so
    the pair bias thresholds apply; they sum to 0 over the board.
    matrix[row][col] = fundamental score of row vs col (row = base; the board's
    reverse orientation is the negated pair)."""
    pairs: dict[str, dict[str, float]] = {}
    for inst in payload.get("instruments", []):
        if inst.get("type") != "fx":
            continue
        base = inst["breakdown"]["base"]["currency"]
        quote = inst["breakdown"]["quote"]["currency"]
        v = _pair_fund_score(inst)
        pairs.setdefault(base, {})[quote] = v
        pairs.setdefault(quote, {})[base] = -v
    scores = {ccy: sum(o.values()) / len(o) for ccy, o in pairs.items() if o}
    return {"scores": scores, "matrix": pairs}


def _monetary_state(card: dict) -> dict:
    """Own 2y monetary sub-score, informative on the card (not in the Strength
    score by itself): ok (scored, fresh) / stale / missing — read from
    categories.monetary, not from whether the rate engine saw the currency."""
    cat = (card.get("categories") or {}).get("monetary")
    rate = (card.get("breakdown") or {}).get("rate_expectations") or {}
    if not cat:
        return {"state": "missing", "score": None}
    state = "stale" if (cat.get("stale") or not cat.get("coverage")) else "ok"
    return {"state": state, "score": rate.get("score", cat.get("score_cell"))}


def _attach_strength_fields(payload: dict, instruments_cfg: dict, rate_scores: dict) -> None:
    """Attach /strength.html display fields to each payload["currencies"][CCY]
    (audit B1: Strength is the aggregate of the pairs, see strength_from_pairs).

    `strength_score` (pair units) drives pct = clamp(50 + score * K) and the bias
    (pair thresholds, bias_label — the same function /economic reads).
    `strength_pairs` feeds the divergence matrix. `index`, N (`coverage`) and the
    own monetary state stay informative. /economic is untouched.""" 
    thresholds = instruments_cfg.get("bias_thresholds", {})
    agg = strength_from_pairs(payload)
    for ccy, card in payload.get("currencies", {}).items():
        score = float(agg["scores"].get(ccy, 0.0))
        raw_pct = 50.0 + score * STRENGTH_PCT_K
        card["strength_score"] = score
        card["strength_pairs"] = agg["matrix"].get(ccy, {})
        card["pct_clamped"] = raw_pct < 0.0 or raw_pct > 100.0
        card["pct"] = round(max(0.0, min(100.0, raw_pct)), 1)
        card["bias_label"] = bias_label(score, thresholds)
        card["monetary"] = _monetary_state(card)
        card["monetary_available"] = card["monetary"]["state"] == "ok"


def _metals_cot_map() -> dict[str, dict]:
    """{metal_symbol: COT detail dict} from the validated COT engine (read-only).

    Computed ONCE and reused for both the cross-asset SCORE (sentiment factor)
    and the display sub-cell. Reuses src.cot_score.score_metals over the extremes
    from src.compute — no scoring logic here. COT parquet missing/empty → {} (so
    metals carry no `cot` key and are excluded from the sentiment factor exactly
    like a missing factor), mirroring the graceful real_yield/net_liquidity path.
    """
    try:
        scored = score_metals(load_metals_history())
    except Exception as e:  # noqa: BLE001 — display-only path; never break the page
        log.warning("COT sentiment unavailable (%s); metals shown blank.", e)
        return {}
    if scored.empty:
        return {}
    return {
        row["symbol"]: {
            "cell": int(row["cell"]),
            "level": int(row["level"]),
            "flow": int(row["flow"]),
            "blend": float(row["blend"]),
            "z": None if pd.isna(row["z"]) else float(row["z"]),
            "basis": str(row["basis"]),
        }
        for _, row in scored.iterrows()
    }


def _current_equity_pc_percentile() -> float | None:
    """Latest rolling-1Y percentile (0..100) of the CBOE equity P/C ratio.

    Reuses the existing P/C pipeline (sentiment_compute.compute_pc_metrics over
    data/pc_history.parquet) — read-only; the percentile is NOT recomputed here.
    Returns None if the parquet is missing/unreadable or the percentile is not
    available (so the caller can render a blank cell instead of a fake 0).
    """
    if not PC_HISTORY_PARQUET.exists():
        return None
    try:
        pc_df = pd.read_parquet(PC_HISTORY_PARQUET)
        metrics = compute_pc_metrics(pc_df, str(PC_THRESHOLDS_YAML))
        return metrics["equity"]["current"]["percentile_rank"]
    except Exception as e:  # noqa: BLE001 — display-only; never break the page
        log.warning("equity P/C percentile unavailable (%s); US indices blank.", e)
        return None


def _us_index_pc(cfg: dict) -> tuple[int | None, dict | None, set[str], set[str]]:
    """(cell, detail, native_syms, proxy_syms) for the market-wide US equity P/C score.

    The CBOE equity put/call ratio is market-wide: NATIVE consumers are the US indices
    (type=index, home_ccy=USD). PROXY consumers are instruments flagged
    `sentiment_proxy: us_equity_pc` (DAX/NIKKEI/FTSE100) — they take the SAME cell as a
    global-risk proxy (same sign/weight/thresholds; labelled distinctly in the UI). A
    missing percentile → cell None (all blank, score unchanged).
    """
    instruments_cfg = cfg.get("instruments", {}) or {}
    native_syms = {
        sym for sym, c in instruments_cfg.items()
        if c.get("type") == "index" and c.get("home_ccy") == "USD"
    }
    proxy_syms = {
        sym for sym, c in instruments_cfg.items()
        if c.get("sentiment_proxy") == "us_equity_pc"
    }
    pct = _current_equity_pc_percentile()
    if pct is None:
        return None, None, native_syms, proxy_syms
    cell, detail = pc_index_score(pct)
    return int(cell), detail, native_syms, proxy_syms


def _fx_currency_cells() -> tuple[dict[str, int], dict[str, dict]]:
    """(cells, details) per currency COT contract from score_currencies (read-only).

    Computed ONCE and reused for both the FX SCORE (sentiment factor, via
    build_payload) and the display sub-cell. cells: {COT_symbol: cell int};
    details: {COT_symbol: {level, flow, blend, z, basis}}. Empty on failure
    (FX rows then carry no sentiment in score or display).
    """
    try:
        scored = score_currencies(load_currencies_history())
    except Exception as e:  # noqa: BLE001 — never break the page
        log.warning("FX COT sentiment unavailable (%s); FX shown blank.", e)
        return {}, {}
    if scored.empty:
        return {}, {}
    cells = {row["symbol"]: int(row["cell"]) for _, row in scored.iterrows()}
    details = {
        row["symbol"]: {
            "level": int(row["level"]), "flow": int(row["flow"]),
            "blend": float(row["blend"]),
            "z": None if pd.isna(row["z"]) else float(row["z"]),
            "basis": str(row["basis"]),
        }
        for _, row in scored.iterrows()
    }
    return cells, details


def _attach_fx_cot_cells(payload: dict, cells: dict[str, int],
                         details: dict[str, dict]) -> None:
    """Attach the COT SENTIMENT sub-cell to each FX instrument (in place).

    Display sub-cell, reusing the SAME per-currency cells folded into the FX
    score (build_payload sentiment_cells). CFTC currency contracts are quoted vs
    USD, so an FX-pair cell = pair_cot(leg(base), leg(quote)) with the USD leg = 0
    (no DXY here — that would double-count the dollar). The single USD-index row
    uses the DXY contract directly. A pair whose non-USD leg has no COT data
    renders blank. No cells → every FX row blank.
    """
    instruments = payload.get("instruments", []) or []
    if not instruments or not cells:
        return

    def _leg(ccy: str | None):
        """(cell, detail) for a leg. USD -> 0 (no detail). Missing ccy -> None."""
        if ccy == "USD":
            return 0, None
        return cells.get(ccy), details.get(ccy)

    for inst in instruments:
        bdn = inst.get("breakdown", {}) or {}
        base_ccy = (bdn.get("base") or {}).get("currency")
        quote_ref = bdn.get("quote")
        quote_ccy = quote_ref.get("currency") if quote_ref else None

        if inst.get("type") == "single" and base_ccy == "USD":
            # USD-index row → the DXY contract cell directly.
            dxy = cells.get("DXY")
            if dxy is None:
                continue
            inst["cot"] = {
                "cell": dxy, "base": "USD", "quote": None,
                "base_cell": dxy, "quote_cell": None,
                "base_detail": details.get("DXY"), "quote_detail": None,
            }
            continue

        if inst.get("type") != "fx":
            continue

        base_cell, base_detail = _leg(base_ccy)
        quote_cell, quote_detail = _leg(quote_ccy)
        # A genuinely-missing (non-USD) currency leg → leave the pair blank.
        if (base_ccy != "USD" and base_cell is None) or (
            quote_ccy != "USD" and quote_cell is None
        ):
            continue

        inst["cot"] = {
            "cell": pair_cot(base_cell, quote_cell),
            "base": base_ccy, "quote": quote_ccy,
            "base_cell": base_cell, "quote_cell": quote_cell,
            "base_detail": base_detail, "quote_detail": quote_detail,
        }


def _freshness(as_of: pd.Timestamp | None = None,
                trend_enabled: bool | None = None) -> dict:
    """Per-source data freshness — GENERAL watchdog over every feed (not a single
    indicator). Reports {source: {last_update, age_days, stale}} for the calendar
    (most recent PUBLISHED actual, any currency) and the price feed (most recent
    bar). `any_stale` rolls them up. Graceful: a missing/unreadable source is
    simply omitted. Used by the payload (dashboard badge) and the cron watchdog.

    `trend_enabled` (optional): when False, the "price" entry is skipped
    entirely — price_history.parquet is TREND's only consumer, so watching it
    is pointless while the feature is off. None (default) reads the live
    config/pipeline.yaml flag; pass explicitly to test the price-freshness
    mechanics in isolation regardless of the flag's current value."""
    if as_of is None:
        as_of = pd.Timestamp.utcnow().tz_localize(None)
    if trend_enabled is None:
        trend_enabled = _trend_enabled()
    out: dict = {}

    def _age(last: pd.Timestamp) -> int:
        return int((as_of - pd.Timestamp(last)).days)

    # The calendar entry must watch the ACTIVE calendar source (Phase 3): with
    # calendar_source=ff, the MT5 parquet is frozen — watching it would pin the
    # badge permanently red/green regardless of what the dashboard scores from.
    try:
        from src.ff_refresh import calendar_source
        cal_src = calendar_source()
    except Exception as e:  # noqa: BLE001 — config unreadable → mt5 (mirrors _load_calendar_frame)
        log.warning("freshness: calendar_source unresolved (%s); assuming mt5.", e)
        cal_src = "mt5"

    if cal_src == "ff":
        # fix/calendar-freshness-measures-source: measures whether
        # ff_refresh.refresh() itself last SUCCEEDED (its own state file,
        # advanced only on "ok" — see ff_refresh.STATE_JSON), not the most
        # recent PUBLISHED actual. A quiet window with no new prints
        # (weekend, no scheduled releases) is not the same as the refresh
        # pipeline being stuck, and the old content-based reading conflated
        # the two — every gap over FRESHNESS_STALE_DAYS["calendar"] days
        # turned the badge red regardless of whether ff_refresh ran fine.
        # Symmetric with the actuals_pull block below, which already reads
        # jb_actuals' own state file the same way — including "absent state
        # -> stale=True, never a silent False" (no state ever written, or
        # never a successful run, must stay fail-visible).
        try:
            from src.ff_refresh import STATE_JSON as FF_STATE_JSON
            from src.ff_refresh import load_state as ff_load_state
            st = ff_load_state(FF_STATE_JSON)
            last_at = st.get("last_success_at") or st.get("last_success_utc_date")
            if last_at:
                last = pd.Timestamp(last_at)
                age = _age(last)
                out["calendar"] = {"last_update": last.isoformat(), "age_days": age,
                                   "stale": age > FRESHNESS_STALE_DAYS["calendar"]}
            else:
                out["calendar"] = {"last_update": None, "age_days": None, "stale": True}
        except Exception as e:  # noqa: BLE001
            log.warning("freshness(calendar) unavailable: %s", e)
    else:
        try:
            if PARQUET.exists():
                c = pd.read_parquet(PARQUET)
                c["release_dt"] = pd.to_datetime(c["release_dt"], errors="coerce")
                c["actual"] = pd.to_numeric(c["actual"], errors="coerce")
                pub = c[c["actual"].notna()]
                if len(pub):
                    last = pub["release_dt"].max()
                    age = _age(last)
                    out["calendar"] = {"last_update": last.isoformat(), "age_days": age,
                                       "stale": age > FRESHNESS_STALE_DAYS["calendar"]}
        except Exception as e:  # noqa: BLE001
            log.warning("freshness(calendar) unavailable: %s", e)

    # Daily JBlanked actuals pull (ff source only) — a DISTINCT badge, separate
    # from calendar-stale: the 2026-07-03..12 actuals freeze was invisible in the
    # logs (every tick "ok") and the dashboard badge is the one alert channel
    # that is actually read. No state file (pull never succeeded) → fail-visible.
    if cal_src == "ff":
        try:
            from src.jb_actuals import STATE_JSON, load_state
            st = load_state(STATE_JSON)
            last_at = st.get("last_success_at") or st.get("last_success_utc_date")
            if last_at:
                last = pd.Timestamp(last_at)
                age = _age(last)
                # >= (not >): "no successful pull for >2 days" — with floor'd
                # int days, age >= 2 is exactly "two evening windows missed".
                out["actuals_pull"] = {
                    "last_update": last.isoformat(), "age_days": age,
                    "stale": age >= FRESHNESS_STALE_DAYS["actuals_pull"]}
            else:
                out["actuals_pull"] = {"last_update": None, "age_days": None,
                                       "stale": True}
        except Exception as e:  # noqa: BLE001
            log.warning("freshness(actuals_pull) unavailable: %s", e)

    # Per-instrument, not a blind aggregate: p["date"].max() over ALL board
    # instruments combined would hide a single frozen instrument (e.g.
    # FTSE100) for as long as any other instrument keeps updating. Each
    # instrument is judged against its own derived cadence threshold
    # (src.price_freshness_guard); `stale_instruments` names the culprits so
    # the badge can say who, not just report a generic age.
    # Skipped entirely while TREND is disabled: price_history.parquet (MT5
    # OHLC export) has no other consumer, so watching its freshness is
    # pointless — see docs/accepted-degradations.md.
    try:
        if trend_enabled and PRICE_HISTORY_PARQUET.exists():
            p = pd.read_parquet(PRICE_HISTORY_PARQUET)
            _, board_symbols = _load_price_symbol_map(PRICE_SYMBOLS_YAML)
            per_instrument = per_instrument_freshness(p, board_symbols, as_of)
            report = freshness_report(per_instrument)
            last = pd.to_datetime(p["date"]).max() if len(p) else None
            out["price"] = {
                "last_update": last.isoformat() if last is not None else None,
                "age_days": _age(last) if last is not None else None,
                "stale": report["any_stale"],
                "stale_count": report["stale_count"],
                "fresh_count": report["fresh_count"],
                "total_count": report["total_count"],
                "stale_instruments": report["stale"],
                "no_data": report["no_data"],
            }
    except Exception as e:  # noqa: BLE001
        log.warning("freshness(price) unavailable: %s", e)

    try:
        rates = _rates_freshness(as_of)
        if rates is not None:
            out["rates"] = rates
    except Exception as e:  # noqa: BLE001
        log.warning("freshness(rates) unavailable: %s", e)

    try:
        pr = _policy_rates_freshness(as_of)
        if pr is not None:
            out["policy_rates"] = pr
    except Exception as e:  # noqa: BLE001
        log.warning("freshness(policy_rates) unavailable: %s", e)

    out["any_stale"] = any(isinstance(v, dict) and v.get("stale") for v in out.values())
    return out


MEETINGS_YAML = ROOT / "data" / "cb" / "meetings.yaml"


def _policy_rates_freshness(as_of: pd.Timestamp, meetings_path: Path | None = None,
                            decisions_path: Path | None = None) -> dict | None:
    """Displayed policy rate (audit F4): every meeting in data/cb/meetings.yaml
    dated before today (UTC) must have its decision in data/cb/decisions.parquet.
    The parquet keeps only each bank's last decisions (4 today), so the check
    covers the meetings from the oldest decision kept onward — a meeting older
    than that window is out of the file by design, not missing. A currency with
    meetings but no decision at all is stale."""
    from src.policy_rate import DECISIONS_PARQUET
    meetings_path = meetings_path or MEETINGS_YAML
    decisions_path = decisions_path or DECISIONS_PARQUET
    if not meetings_path.exists() or not decisions_path.exists():
        return None
    meetings = (_load_yaml(meetings_path).get("meetings") or {})
    d = pd.read_parquet(decisions_path, columns=["currency", "meeting_date"])
    d["meeting_date"] = pd.to_datetime(d["meeting_date"]).dt.date
    today = as_of.date()
    per: dict = {}
    for ccy, lst in sorted(meetings.items()):
        have = set(d.loc[d["currency"] == ccy, "meeting_date"])
        past = sorted(pd.Timestamp(m["date"]).date() for m in (lst or [])
                      if pd.Timestamp(m["date"]).date() < today)
        oldest = min(have) if have else None
        due = [m for m in past if oldest is None or m >= oldest]
        missing = [m.isoformat() for m in due if m not in have]
        per[ccy] = {"last_meeting": past[-1].isoformat() if past else None,
                    "last_decision": max(have).isoformat() if have else None,
                    "missing": missing, "stale": bool(missing) or (bool(past) and not have)}
    stale = [c for c, v in per.items() if v["stale"]]
    return {"per_currency": per, "stale_currencies": stale, "stale": bool(stale)}


def _rates_freshness(as_of: pd.Timestamp) -> dict | None:
    """2y yields, per currency (audit 1.5): last date in data/rates.parquet at or
    before as_of, lag in business days, stale iff lag > rate_compute.max_age_for(source of the last row)
    (the same per-source threshold that makes the monetary score stale). A currency with no
    2y source at all (NZD/CHF today, see docs) is listed under `missing` and does
    not roll into `stale` — it is a known gap, not a feed that stopped."""
    if not RATES_PARQUET.exists():
        return None
    import numpy as np
    from src.rate_compute import MAX_AGE_BD, max_age_for
    from src.rate_sources import CURRENCIES
    r = pd.read_parquet(RATES_PARQUET, columns=["currency", "date", "source"])
    r["date"] = pd.to_datetime(r["date"])
    r = r[r["date"] <= as_of]
    ref = as_of.date()
    per: dict = {}
    missing: list[str] = []
    for ccy in CURRENCIES:
        sub = r[r["currency"] == ccy]
        if sub.empty:
            missing.append(ccy)
            continue
        last_row = sub.loc[sub["date"].idxmax()]
        last = last_row["date"].date()
        lag = 0 if last >= ref else int(np.busday_count(last, ref))
        src = str(last_row["source"])
        per[ccy] = {"last_update": last.isoformat(), "lag_bd": lag, "source": src,
                    "max_lag_bd": max_age_for(src), "stale": lag > max_age_for(src)}
    stale_ccys = [c for c, v in per.items() if v["stale"]]
    return {"per_currency": per, "max_lag_bd": MAX_AGE_BD,
            "stale_currencies": stale_ccys, "missing": missing,
            "stale": bool(stale_ccys)}


def _trend_enabled(cfg: dict | None = None) -> bool:
    """False (default) — TREND kill switch (config/pipeline.yaml). Disables the
    feature entirely: no trend_score_all() call, no trend/trend_detail key on
    any instrument (FX or cross-asset), no "price" freshness watch (TREND is
    price_history.parquet's only consumer). True is the rollback path,
    mirroring calendar_source's ff/mt5 switch — restores pre-flag behavior
    bit-for-bit. See docs/accepted-degradations.md (MT5 dependency)."""
    cfg = cfg if cfg is not None else _load_yaml(PIPELINE_YAML)
    return bool(cfg.get("trend_enabled", False))


def _trend_full() -> dict[str, dict]:
    """{board_key: {bull_points,regime,slope_atr,momentum,adx,trend_cell}} (TREND v2,
    read-only). Computed ONCE; the cells feed the FX/cross-asset scores
    AND the TREND column, while the full entry feeds the pop-up decomposition.
    Each table reads only its own board keys (FX pairs vs GOLD/SILVER/SP500/DJIA),
    so the shared map never cross-contaminates. Missing/empty parquet → score_all
    returns all-None entries → trend excluded everywhere (graceful)."""
    try:
        return trend_score_all()
    except Exception as e:  # noqa: BLE001 — display/scoring add-on; never break the page
        log.warning("TREND unavailable (%s); trend excluded everywhere.", e)
        return {}


def _trend_cells(full: dict) -> dict[str, int]:
    """{board_key: trend_cell} for the present cells only (drops None entries)."""
    return {k: int(v["trend_cell"]) for k, v in full.items() if v.get("trend_cell") is not None}


def _build_crossasset_block(payload: dict, as_of: pd.Timestamp,
                            trend_full: dict[str, dict] | None = None,
                            trend_enabled: bool = True) -> dict:
    """Compute the cross-asset section (indices + metals) from the FX payload's
    per-currency category scores + the US real-yield momentum. Read-only over
    local parquets; real_yields.parquet missing/empty → real_yield excluded
    gracefully. Returns a JSON-ready dict (instruments sorted most-bullish first).

    `trend_enabled` (default True — the pre-flag behavior): when False,
    `compute_crossasset_scores` is called with `trend_by_symbol=None` (TREND
    excluded from every score), no `trend_detail` key is set on any
    instrument, and the `trend` key `compute_instrument_score` always sets is
    stripped afterward — no scoring file is touched to get there.
    """
    try:
        cfg = _load_yaml(CROSSASSET_YAML)
    except FileNotFoundError:
        return {}

    categories_by_ccy = {
        ccy: card.get("categories", {})
        for ccy, card in payload.get("currencies", {}).items()
    }

    real_yield_score = None
    real_yield_meta = {"present": False, "series": "DFII10"}
    if REAL_YIELDS_PARQUET.exists():
        try:
            rdf = pd.read_parquet(REAL_YIELDS_PARQUET)
            if len(rdf):
                rys = compute_realyield_score(rdf, as_of=as_of.date())
                if rys is not None:
                    real_yield_score = rys
                    real_yield_meta = {
                        "present": True, "series": rys.series, "score": rys.score,
                        "delta_w": rys.delta_w, "latest_yield": rys.latest_yield,
                        "z": rys.z, "method": rys.method,
                        "as_of": rys.as_of.isoformat() if rys.as_of is not None else None,
                        "stale": rys.stale,
                    }
        except Exception as e:
            log.warning("real_yields.parquet present but unreadable (%s); real_yield excluded.", e)

    # Bank reserves (WRBWFRBL) → balance_sheet sub-component, falling back to
    # net_liquidity (WALCL−TGA−RRP, the rollback path) when reserves is
    # unresolved — see compute_liquidity_score. Missing parquet → excluded
    # gracefully (mirrors real_yield).
    liquidity_score = None
    liquidity_meta = {"present": False, "series": "WRBWFRBL"}
    if NET_LIQUIDITY_PARQUET.exists():
        try:
            ndf = pd.read_parquet(NET_LIQUIDITY_PARQUET)
            if len(ndf):
                ls = compute_liquidity_score(ndf, as_of=as_of.date())
                if ls is not None:
                    liquidity_score = ls
                    liquidity_meta = {
                        "present": True,
                        "series": LIQUIDITY_SERIES_LABELS.get(ls.series, "WRBWFRBL"),
                        "score": ls.score,
                        "roc": ls.roc, "latest": ls.latest, "method": ls.method,
                        "as_of": ls.as_of.isoformat() if ls.as_of is not None else None,
                        "stale": ls.stale,
                    }
        except Exception as e:
            log.warning("net_liquidity.parquet present but unreadable (%s); balance_sheet excluded.", e)

    # SENTIMENT cells computed ONCE (read-only over the COT/P-C engines), then
    # fed into the SCORE as a weight-0.5 factor AND reused for the display cell.
    metal_cot = _metals_cot_map()
    pc_cell, pc_detail, us_index_syms, pc_proxy_syms = _us_index_pc(cfg)
    pc_syms = us_index_syms | pc_proxy_syms   # native US indices + foreign proxy consumers
    sentiment_by_symbol: dict[str, int] = {
        sym: d["cell"] for sym, d in metal_cot.items()
    }
    if pc_cell is not None:
        for sym in pc_syms:
            sentiment_by_symbol[sym] = pc_cell   # SAME cell for native + proxy (identical scoring)

    trend_full = trend_full or {}
    trend_by_symbol = _trend_cells(trend_full) if trend_enabled else None
    scores = compute_crossasset_scores(categories_by_ccy, real_yield_score, cfg,
                                       liquidity_score=liquidity_score,
                                       sentiment_by_symbol=sentiment_by_symbol,
                                       trend_by_symbol=trend_by_symbol)

    currencies = payload.get("currencies", {}) or {}

    def _home_breakdown(home: str) -> dict:
        return (currencies.get(home, {}) or {}).get("breakdown", {}) or {}

    instruments = []
    for sym, r in scores.items():
        r = dict(r)
        r["display"] = CROSSASSET_DISPLAY.get(sym, sym)
        home = r.get("home_ccy")
        brk = _home_breakdown(home)

        # Sub-column cells: the PER-ASSET signed score (home-ccy raw × this
        # instrument's category sign), so each cell already points the right way
        # for the asset. The per-indicator `direction` is already baked into the
        # home-ccy raw score; the category sign is the separate asset layer — we
        # apply it exactly once here.
        fsign = {f["name"]: f.get("sign") for f in r.get("factors", []) if f.get("sign") is not None}
        cells: dict[str, dict] = {}
        for key in CROSSASSET_CATEGORY_KEYS:
            e = brk.get(key)
            raw = None if e is None else e.get("score")
            if raw is None:
                cells[key] = {"score": None, "stale": False}
            else:
                s = fsign.get(CROSSASSET_KEY_CATEGORY.get(key, ""), 1)
                cells[key] = {"score": float(s) * raw, "stale": bool(e.get("stale"))}
        # Rate sub-columns: signed by the component's own sign (already directional).
        # Shown even when stale (greyed); the stale exclusion happens in the mean.
        rates_factor = next((f for f in r.get("factors", []) if f.get("name") == "rates"), None)
        for c in (rates_factor.get("components", []) if rates_factor else []):
            raw = c.get("raw")
            signed = (float(c.get("sign", 1)) * raw) if raw is not None else None
            cells[c["name"]] = {"score": signed, "stale": bool(c.get("stale")),
                               "excluded": bool(c.get("excluded"))}
        r["cells"] = cells

        # TREND decomposition for the pop-up (column shows the final cell only).
        # Disabled → no trend_detail key at all, and strip the `trend` key
        # compute_instrument_score always sets (crossasset_compute.py untouched).
        if trend_enabled:
            r["trend_detail"] = trend_full.get(sym)
        else:
            r.pop("trend", None)

        # SENTIMENT display sub-cell, reusing the SAME cells folded into the score
        # above: COT for metals, P/C for US indices. Foreign indices get neither.
        if sym in metal_cot:
            r["cot"] = metal_cot[sym]
        elif pc_cell is not None and sym in pc_syms:
            r["sentiment"] = {
                "source": "pc", "cell": pc_cell,
                "pct": float(pc_detail["pct"]), "basis": str(pc_detail["basis"]),
                "proxy": sym in pc_proxy_syms,   # foreign index → US-equity-P/C global-risk proxy
            }

        instruments.append(r)

    instruments.sort(key=lambda r: r.get("score_precise", 0.0), reverse=True)

    return {
        "instruments": instruments,
        "table_layout": [
            {"key": g["key"], "label": g["label"],
             "columns": [{"key": k, "label": lbl} for k, lbl in g["columns"]]}
            for g in CROSSASSET_TABLE_LAYOUT
        ],
        "factor_labels": dict(CROSSASSET_FACTOR_LABELS),
        "real_yield": real_yield_meta,
        "net_liquidity": liquidity_meta,
        "bias_thresholds": cfg.get("bias_thresholds", {}),
        "scale": cfg.get("scale"),
    }


_EMPTY_CAL_COLUMNS = ["currency", "indicator_key", "release_dt", "actual", "consensus"]


def _load_calendar_frame(as_of: pd.Timestamp) -> pd.DataFrame:
    """Load the scoring calendar per config/pipeline.yaml `calendar_source` (Phase 3).

      ff  → the FF parquet (data/economic_calendar_ff.parquet) bridged to the MT5
            calendar schema, minus any FRED-quarantined prints (data/ff_quarantine.parquet),
            plus any still-valid Manual Actuals Panel overrides (source='manual').
      mt5 → the MT5 calendar parquet (pre-Phase-3 behavior) — the rollback path.

    Anti-degradation: a missing/empty FF parquet returns an empty frame (empty page),
    never the MT5 data — the sources stay cleanly separated.
    """
    try:
        from src.ff_refresh import FF_PARQUET, calendar_source
        source = calendar_source()
    except Exception as e:  # noqa: BLE001 — config missing → default to mt5 (safe)
        log.warning("calendar_source unresolved (%s); defaulting to mt5.", e)
        source = "mt5"

    if source == "ff":
        if not FF_PARQUET.exists():
            log.warning("FF calendar parquet missing — empty Economic page.")
            return pd.DataFrame(columns=_EMPTY_CAL_COLUMNS)
        from src.ff_scoring import build_matcher, to_scoring_frame
        ffdf = pd.read_parquet(FF_PARQUET)
        ffdf["datetime_utc"] = pd.to_datetime(ffdf["datetime_utc"])
        cal = to_scoring_frame(ffdf, build_matcher())
        cal["release_dt"] = pd.to_datetime(cal["release_dt"])
        q_path = ROOT / "data" / "ff_quarantine.parquet"
        if q_path.exists() and len(cal):
            q = pd.read_parquet(q_path)
            if len(q):
                q["datetime_utc"] = pd.to_datetime(q["datetime_utc"])
                key = ["currency", "indicator_key", "release_dt"]
                qkey = q.rename(columns={"datetime_utc": "release_dt"})[key]
                before = len(cal)
                cal = cal.merge(qkey.assign(_q=1), on=key, how="left")
                cal = cal[cal["_q"].isna()].drop(columns="_q")
                if len(cal) < before:
                    log.warning("FF calendar: %d print(s) excluded by FRED quarantine.", before - len(cal))

        # Manual Actuals Panel (Phase B): human-supplied actuals, unioned in
        # AFTER FRED quarantine — a reviewed manual entry is the pipeline's
        # final say, not subject to an unrelated automated quarantine list.
        # Never routed through to_scoring_frame (it hardcodes source='ff').
        from src.manual_actuals import apply_overrides, load_overrides
        overrides = load_overrides(MANUAL_ACTUALS_OVERRIDES)
        if overrides:
            manual_rows, _ = apply_overrides(ffdf, overrides, now_utc=as_of)
            if len(manual_rows):
                cal = pd.concat([cal, manual_rows], ignore_index=True)
                from src.ff_scoring import zero_beside_real_value
                cal = zero_beside_real_value(cal)
                log.info("Manual Actuals Panel: %d override(s) applied to scoring.",
                        len(manual_rows))

        log.info("Economic calendar source = FF (%d scored rows).", len(cal))
        return cal
    # --- rollback: MT5 ---
    if PARQUET.exists():
        cal = pd.read_parquet(PARQUET)
        cal["release_dt"] = pd.to_datetime(cal["release_dt"])
        return cal
    log.warning("%s missing — rendering an empty Economic page.", PARQUET)
    return pd.DataFrame(columns=_EMPTY_CAL_COLUMNS)


_MANUAL_ACTUALS_COLUMNS = ["canonical_id", "currency", "indicator_key", "name_raw",
                          "name_canonical", "datetime_utc", "forecast", "previous",
                          "actual", "state"]


def _load_actionable_rows(as_of: pd.Timestamp) -> pd.DataFrame:
    """Manual Actuals Panel — rows STILL needing human intervention, via the
    pure `manual_actuals.apply_overrides` (its `remaining_actionable`): a row
    with a still-valid override (feat/manual-actuals-panel Phase B) has
    already been handled and drops off this list; a row whose override went
    stale (a real actual landed since) reappears here as normal, since it is
    no longer actionable at all — see `apply_overrides`'s docstring.

    Sourced ONLY from the FF parquet (data/economic_calendar_ff.parquet), the
    single source of truth for this panel, regardless of the active
    `calendar_source`: the mt5 rollback path predates the FF-only MISSING /
    ZERO_CONFIRM contract and has no equivalent panel (empty, not an error)."""
    try:
        from src.ff_refresh import FF_PARQUET, calendar_source
        source = calendar_source()
    except Exception as e:  # noqa: BLE001 — config missing → panel empty (safe)
        log.warning("calendar_source unresolved (%s); manual actuals panel empty.", e)
        return pd.DataFrame(columns=_MANUAL_ACTUALS_COLUMNS)
    if source != "ff" or not FF_PARQUET.exists():
        return pd.DataFrame(columns=_MANUAL_ACTUALS_COLUMNS)
    from src.manual_actuals import apply_overrides, load_overrides
    ffdf = pd.read_parquet(FF_PARQUET)
    ffdf["datetime_utc"] = pd.to_datetime(ffdf["datetime_utc"])
    overrides = load_overrides(MANUAL_ACTUALS_OVERRIDES)
    _manual_rows, remaining = apply_overrides(ffdf, overrides, now_utc=as_of)
    # audit V3: the policy rate comes only from data/cb/decisions.parquet, so a
    # manual interest_rate_decision entry would never be used — not listed.
    from src.policy_rate import KEY as POLICY_KEY
    return remaining[remaining["indicator_key"] != POLICY_KEY].reset_index(drop=True)


def _build_manual_actuals_block(as_of: pd.Timestamp) -> dict:
    """JSON-ready {count, older_count, rows[]} for the Manual Actuals Panel
    button + list. `rows`/`count` are windowed to
    manual_actuals.RELEVANCE_WINDOW (default 45d) — display-only, per
    apply_relevance_window's docstring: an older row is still fully
    actionable/overridable, just not listed. `older_count` is exactly that
    many rows, for a discreet "+N older" indicator, never expanded into rows."""
    from src.manual_actuals import apply_relevance_window
    rows = _load_actionable_rows(as_of)
    recent, older_count = apply_relevance_window(rows, now_utc=as_of)
    return {
        "count": int(len(recent)),
        "older_count": older_count,
        "rows": [
            {
                "canonical_id": r.canonical_id, "currency": r.currency,
                "indicator_key": r.indicator_key, "name_raw": r.name_raw,
                "name_canonical": r.name_canonical, "datetime_utc": r.datetime_utc,
                "forecast": r.forecast, "previous": r.previous,
                "actual": r.actual, "state": r.state,
            }
            for r in recent.itertuples(index=False)
        ],
    }


def _policy_rate_decisions() -> pd.DataFrame | None:
    """data/cb/decisions.parquet (src.policy_rate) — None if unreadable."""
    from src.policy_rate import DECISIONS_PARQUET, load_decisions
    try:
        return load_decisions(DECISIONS_PARQUET)
    except Exception as e:  # noqa: BLE001
        log.warning("decisions.parquet unavailable (%s); no policy rate displayed.", e)
        return None


def _with_policy_rate(cal: pd.DataFrame, decisions: pd.DataFrame | None,
                      as_of: pd.Timestamp) -> pd.DataFrame:
    """interest_rate_decision comes ONLY from the CB decisions (audit 1.6): the
    FF/JB/manual rows are dropped; no decisions file -> no rows, never a fallback."""
    from src.policy_rate import KEY, replace_in_calendar
    if cal is None or cal.empty or "indicator_key" not in cal.columns:
        return cal
    if decisions is None:
        return cal[cal["indicator_key"] != KEY].reset_index(drop=True)
    return replace_in_calendar(cal, decisions, as_of)


def _attach_policy_rate_fields(payload: dict, decisions: pd.DataFrame | None,
                               policy_fresh: dict | None = None) -> None:
    """Fed range + provenance on each interest_rate_decision breakdown entry, and
    its `stale` flag (audit V1): a policy rate holds until the next meeting, so
    age is not a criterion — the entry is stale iff freshness.policy_rates says a
    past meeting has no decision (never from max_age_days). The instrument
    breakdowns share these dicts, so they follow."""
    from src.policy_rate import KEY, display_fields
    per = (policy_fresh or {}).get("per_currency") or {}
    for ccy, card in payload.get("currencies", {}).items():
        entry = (card.get("breakdown") or {}).get(KEY)
        if entry is None:
            continue
        if decisions is not None and entry.get("release_dt") is not None:
            entry.update(display_fields(decisions, ccy, entry["release_dt"]))
        entry["stale"] = bool((per.get(ccy) or {}).get("stale", False))
        entry["stale_basis"] = "policy_rates"


INTEGRITY_REPORT_JSON = ROOT / "data" / "integrity_report.json"


def _latest_weekly_raw(as_of: pd.Timestamp) -> Path | None:
    """Latest data/ff_raw/ff_weekly_YYYY-MM-DD.json dated <= as_of (so a pinned
    as_of never reads a feed it could not have seen)."""
    from src.ff_raw_archive import RAW_DIR
    cands = sorted(p for p in RAW_DIR.glob("ff_weekly_*.json")
                   if p.stem.removeprefix("ff_weekly_") <= as_of.strftime("%Y-%m-%d"))
    return cands[-1] if cands else None


def _all_weekly_raw(as_of: pd.Timestamp) -> list[tuple[str, list]]:
    """Every archived FF weekly feed on disk dated <= as_of: (date tag, events)."""
    from src.ff_raw_archive import RAW_DIR
    out = []
    for p in sorted(RAW_DIR.glob("ff_weekly_*.json")):
        tag = p.stem.removeprefix("ff_weekly_")
        if tag <= as_of.strftime("%Y-%m-%d"):
            try:
                out.append((tag, json.loads(p.read_text())))
            except (OSError, ValueError):
                log.warning("integrity: unreadable %s skipped", p.name)
    return out


def _finding_id(check: str, f: dict) -> str:
    keys = ("canonical_id", "release_dt", "kind", "after", "before")
    return check + "|" + "|".join(str(f.get(k)) for k in keys)


def _previous_findings(path: Path | None = None) -> set:
    try:
        old = json.loads((path or INTEGRITY_REPORT_JSON).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — first run / unreadable -> everything is new
        return set()
    return {_finding_id(name, f) for name, c in (old.get("checks") or {}).items()
            for f in c.get("findings", [])}


def _integrity_report(cal: pd.DataFrame, as_of: pd.Timestamp,
                      previous_path: Path | None = None) -> dict:
    """Integrity checks over the scored calendar (audit 1.2, 4B). Pure checks —
    never feed back into scoring (release conflicts are excluded by
    ff_scoring itself; this only reports them).

    Levels: WARN = unresolved AND inside the series' scoring window (its latest
    print or the K=12 sigma window); INFO = resolved (already out of scoring)
    or outside the window. A finding is `new` when the previous report did not
    have it; only new WARN findings are logged as alerts."""
    from src.previous_consistency import check_previous_consistency, scoring_windows
    report: dict = {"as_of": as_of.isoformat(), "checks": {}}
    try:
        from src.ff_refresh import FF_PARQUET, calendar_source
        if calendar_source() != "ff" or not FF_PARQUET.exists():
            return report
        from src.econ_calendar_ff import parse_ff_weekly
        from src.ff_scoring import build_matcher, load_zero_possible
        from src.release_integrity import (find_series_gaps, find_unfed_scheduled,
                                           resolve_conflicts)
        ffdf = pd.read_parquet(FF_PARQUET)
        zp = load_zero_possible()
        matcher = build_matcher()
        excluded, conflicts = resolve_conflicts(ffdf, zp)
        for f in conflicts:
            f["level"] = "INFO"                # resolved: already out of scoring
        report["checks"]["release_conflict"] = {"findings": conflicts}

        weekly = _latest_weekly_raw(as_of)
        upcoming = parse_ff_weekly(weekly, now_utc=as_of) if weekly is not None else None
        res = check_previous_consistency(ffdf, cal, matcher, upcoming=upcoming,
                                         zero_possible=zp, excluded=excluded)
        res["weekly_feed"] = weekly.name if weekly is not None else None
        report["checks"]["previous_consistency"] = res

        windows = scoring_windows(cal)
        key_of = {}
        for r in ffdf.drop_duplicates("canonical_id").itertuples(index=False):
            from src.ff_scoring import CCY2COUNTRY
            key_of[r.canonical_id] = matcher.match(CCY2COUNTRY.get(r.currency, ""), r.name_canonical)
        missing = find_series_gaps(ffdf, as_of, zp) + find_unfed_scheduled(
            ffdf, _all_weekly_raw(as_of), as_of, matcher)
        for f in missing:
            w = windows.get((f["currency"], key_of.get(f["canonical_id"])))
            when = pd.Timestamp(f.get("before") or f.get("release_dt"))
            f["level"] = "WARN" if (w is not None and when >= w) else "INFO"
        report["checks"]["missing_release"] = {"findings": missing}

        seen = _previous_findings(previous_path)
        new_warn = []
        for name, c in report["checks"].items():
            for f in c.get("findings", []):
                f["new"] = _finding_id(name, f) not in seen
                if f["new"] and f.get("level") == "WARN":
                    new_warn.append((name, f))
        counts = {name: {lvl: sum(1 for f in c.get("findings", []) if f.get("level") == lvl)
                         for lvl in ("WARN", "INFO")} for name, c in report["checks"].items()}
        report["counts"] = counts
        log.info("integrity: %s -> %s", counts, INTEGRITY_REPORT_JSON.name)
        for name, f in new_warn:
            log.warning("integrity NEW WARN %s: %s %s %s", name, f.get("canonical_id"),
                        f.get("release_dt") or f"{f.get('after')}..{f.get('before')}",
                        f.get("reason") or f.get("kind") or "")
    except Exception as e:  # noqa: BLE001 — a check must never break the render
        log.warning("integrity report unavailable: %s", e)
        report["error"] = str(e)
    return report


def _findings_key(report: dict) -> str:
    """What decides a rewrite: the findings (and the formula), not the timestamps."""
    checks = {name: {"findings": c.get("findings"), "formula": c.get("formula")}
              for name, c in (report.get("checks") or {}).items()}
    return json.dumps({"checks": checks, "error": report.get("error")}, sort_keys=True)


def _write_integrity_report(report: dict, generated_at: str | None,
                            path: Path | None = None) -> bool:
    """Rewrite data/integrity_report.json only when the set of findings changed,
    so its timestamps never cause an hourly commit. Returns True if written."""
    path = path or INTEGRITY_REPORT_JSON
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt -> write
        old = None
    if old is not None and _findings_key(old) == _findings_key(report):
        return False
    report = dict(report, generated_at=generated_at)
    path.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return True


def _integrity_summary(report: dict) -> dict:
    """Counter for payload["freshness"]["integrity"]. WARN level, deliberately no
    `stale` key: it does not roll into any_stale. `warn` counts unresolved
    findings inside a scoring window; `new_warn` the ones the previous run did
    not have (the only ones that alert)."""
    checks = report.get("checks") or {}
    pc = checks.get("previous_consistency")
    if pc is None:
        return {"previous_consistency": None, "level": "unavailable"}
    allf = [f for c in checks.values() for f in c.get("findings", [])]
    warn = sum(1 for f in allf if f.get("level") == "WARN")
    return {"previous_consistency": len(pc["findings"]),
            "previous_consistency_by_source": pc.get("findings_by_source", {}),
            "previous_consistency_by_kind": pc.get("findings_by_kind", {}),
            "release_conflict": len((checks.get("release_conflict") or {}).get("findings", [])),
            "missing_release": len((checks.get("missing_release") or {}).get("findings", [])),
            "warn": warn,
            "new_warn": sum(1 for f in allf if f.get("level") == "WARN" and f.get("new")),
            "level": "warn" if warn else "ok"}


def build_economic_payload(as_of: pd.Timestamp | None = None) -> dict:
    """Read parquet + configs, compute, enrich, and return the JSON-ready payload.

    `as_of` (optional, naive UTC) pins the reference instant — default now. Used to
    reproduce a committed snapshot bit-for-bit (see scripts/measure/)."""
    indicators_cfg = _load_yaml(INDICATORS_YAML)
    instruments_cfg = _load_yaml(INSTRUMENTS_YAML)

    as_of = (pd.Timestamp.utcnow().tz_localize(None) if as_of is None
             else pd.Timestamp(as_of))

    cal = _load_calendar_frame(as_of)   # Phase 3: FF (default) or MT5 (rollback) per config
    decisions = _policy_rate_decisions()
    cal = _with_policy_rate(cal, decisions, as_of)

    # Rate-Expectations engine (C1): optional 4th "monetary" category. Missing
    # parquet → render exactly as before (3 categories), no crash.
    rate_scores = {}
    pair_monetary = {}
    rate_sources_by_ccy: dict[str, str] = {}
    if RATES_PARQUET.exists():
        try:
            rates_df = pd.read_parquet(RATES_PARQUET)
            rate_scores = compute_rate_scores(rates_df, as_of=as_of.date())
            # Audit 5B: pair monetary on the 2y spread (used only where both
            # legs' own 2y is fresh — the D1=D intersection decides).
            fx_pairs = [(sym, c["base"], c["quote"])
                        for sym, c in (instruments_cfg.get("instruments") or {}).items()
                        if c.get("type") == "fx"]
            pair_monetary = compute_pair_spread_scores(rates_df, fx_pairs, as_of=as_of.date())
            rates_df = rates_df.sort_values("date")
            rate_sources_by_ccy = {
                str(c): str(g["source"].iloc[-1])
                for c, g in rates_df.groupby("currency")
            }
        except Exception as e:
            log.warning("rates.parquet present but unreadable (%s); skipping monetary.", e)
            rate_scores = {}
            pair_monetary = {}

    # SENTIMENT (COT) per currency, computed ONCE: fed into the FX SCORE as a
    # weight-0.5 factor AND reused for the display sub-cell below.
    fx_cells, fx_details = _fx_currency_cells()

    # TREND kill switch (config/pipeline.yaml, default false — see
    # docs/accepted-degradations.md). Disabled → trend_score_all() is never
    # called; trend_full/trend_by_symbol stay empty, so build_payload folds no
    # trend into any FX score (bit-identical to the existing "trend absent"
    # path already covered by test_fx_trend_off_is_bit_identical).
    trend_on = _trend_enabled()

    # TREND per board key, computed ONCE (when enabled): cells fold into the FX
    # score (per-pair) and the cross-asset score (per-instrument) AND drive the
    # TREND column; the full decomposition feeds the pop-up.
    trend_full = _trend_full() if trend_on else {}
    trend_by_symbol = _trend_cells(trend_full) if trend_on else {}

    from src.ff_scoring import scoring_view
    payload = build_payload(scoring_view(cal), indicators_cfg, instruments_cfg,
                            as_of=as_of, rate_scores=rate_scores or None,
                            sentiment_cells=fx_cells or None,
                            trend_cells=trend_by_symbol or None,
                            pair_monetary=pair_monetary or None)

    meta = _build_meta(indicators_cfg, instruments_cfg)
    meta["trend_enabled"] = trend_on
    _enrich_breakdowns(payload, meta["indicators"], as_of, _previous_lookup(cal))
    _attach_strength_fields(payload, instruments_cfg, rate_scores)
    _attach_policy_rate_fields(payload, decisions, _policy_rates_freshness(as_of))
    # Attach the chosen source to each rate_expectations breakdown entry.
    for ccy, card in payload.get("currencies", {}).items():
        entry = (card.get("breakdown") or {}).get("rate_expectations")
        if entry is not None and ccy in rate_sources_by_ccy:
            entry["source"] = rate_sources_by_ccy[ccy]
    _build_indicator_cells(payload, instruments_cfg)

    # Presentation-only label trim (keeps the symbol column tight).
    for inst in payload.get("instruments", []):
        if inst.get("display"):
            inst["display"] = inst["display"].replace("(DXY proxy)", "(DXY)")

    # SENTIMENT display sub-cell on the FX rows, reusing the same per-currency
    # cells folded into the FX score above.
    _attach_fx_cot_cells(payload, fx_cells, fx_details)

    # TREND decomposition for the FX pop-up (the column already shows the final
    # cell; the modal shows short/long/slope/raw/adx/factor). None entry → "no
    # data". Disabled → no trend_detail key at all, and strip the `trend` key
    # economic_compute.compute_instrument always sets (that file is untouched —
    # this is a post-hoc pop, not a scoring change).
    if trend_on:
        for inst in payload.get("instruments", []):
            inst["trend_detail"] = trend_full.get(inst["symbol"])
    else:
        for inst in payload.get("instruments", []):
            inst.pop("trend", None)

    # Cross-Asset block (indices + metals) — separate key, FX payload untouched.
    payload["crossasset"] = _build_crossasset_block(payload, as_of, trend_full,
                                                     trend_enabled=trend_on)

    payload["meta"] = meta
    payload["freshness"] = _freshness(as_of, trend_enabled=trend_on)
    from src.ff_scoring import scoring_view as _sv
    integrity = _integrity_report(_sv(cal), as_of)
    payload["freshness"]["integrity"] = _integrity_summary(integrity)
    payload["_integrity_report"] = integrity   # popped by render_economic_page
    payload["manual_actuals"] = _build_manual_actuals_block(as_of)
    payload["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return _jsonable(payload)


def render_economic_page() -> Path:
    """Build payload, write JSON + HTML, copy static assets. Returns the HTML path."""
    copy_static_assets()

    payload = build_economic_payload()
    integrity = payload.pop("_integrity_report", None)
    if integrity is not None:
        _write_integrity_report(integrity, payload.get("generated_at"))

    data_dir = PUBLIC_DIR / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / "economic.json"
    json_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")

    env = _env()
    template = env.get_template("economic.html.j2")
    html = template.render(
        active_page="economic",
        data_url="/data/economic.json",
    )
    out_path = PUBLIC_DIR / "economic.html"
    out_path.write_text(html, encoding="utf-8")
    log.info("Economic page rendered → %s (%d instruments)",
             out_path, len(payload.get("instruments", [])))
    return out_path


def render_strength_page() -> Path:
    """Render /strength.html — a pure REPRESENTATION of the same
    data/economic.json build_economic_payload already writes (PASUL 1:
    every currency card carries `pct`/`bias_label`/`monetary_available`/
    `pct_clamped` next to its `index`). No new JSON, no recomputation —
    strength.js reads the exact same /data/economic.json url as
    economic.html.j2, so the two pages can never drift apart. Callers must
    run render_economic_page() first (or at least once) so that file exists;
    this function does not build it."""
    copy_static_assets()

    env = _env()
    template = env.get_template("strength.html.j2")
    html = template.render(
        active_page="strength",
        data_url="/data/economic.json",
    )
    out_path = PUBLIC_DIR / "strength.html"
    out_path.write_text(html, encoding="utf-8")
    log.info("Currency Strength page rendered → %s", out_path)
    return out_path


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    out = render_economic_page()
    print(f"Rendered: {out}")
    strength_out = render_strength_page()
    print(f"Rendered: {strength_out}")
