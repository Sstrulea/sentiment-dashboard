"""Bucket-C candidates (approved 2026-08-01, docs/bucket-c-merit-evaluation.md):
matcher resolution + label completeness for the 12 new indicator_keys.

Mirrors tests/test_ff_scoring.py's XF_SERIES pattern.
"""
from __future__ import annotations

import pandas as pd

from src.econ_calendar_ff import CANON_COLUMNS
from src.ff_scoring import to_scoring_frame
from src.economic_fetch import CompiledMatcher, _load_indicators_cfg
from src.economic_render import INDICATOR_LABELS

# (currency, name_canonical, indicator_key) for all 17 bucket-C candidates.
BUCKET_C_SERIES = [
    ("JPY", "Tokyo Core CPI y/y", "tokyo_core_cpi_yoy"),
    ("GBP", "Industrial Production m/m", "industrial_production_mm"),
    ("USD", "Industrial Production m/m", "industrial_production_mm"),
    ("JPY", "Prelim Industrial Production m/m", "industrial_production_mm"),
    ("USD", "Durable Goods Orders m/m", "durable_goods_orders_mm"),
    ("JPY", "Core Machinery Orders m/m", "core_machinery_orders_mm"),
    ("USD", "Personal Spending m/m", "personal_spending_mm"),
    ("USD", "Personal Income m/m", "personal_income_mm"),
    ("USD", "Import Prices m/m", "import_prices"),
    ("AUD", "Import Prices q/q", "import_prices"),
    ("AUD", "Private Capital Expenditure q/q", "capital_expenditure"),
    ("JPY", "Capital Spending q/y", "capital_expenditure"),
    ("AUD", "Company Operating Profits q/q", "company_operating_profits_qoq"),
    ("JPY", "SPPI y/y", "sppi_yoy"),
    ("JPY", "Prelim GDP Price Index y/y", "gdp_price_index"),
    ("USD", "Advance GDP Price Index q/q", "gdp_price_index"),
    ("USD", "Prelim Unit Labor Costs q/q", "unit_labor_costs_qoq"),
]

NEW_INDICATOR_KEYS = sorted({key for _, _, key in BUCKET_C_SERIES})


def _ff_row(ccy, canon, actual, dt):
    return {"canonical_id": f"{ccy.lower()}_x", "currency": ccy, "name_raw": "raw",
            "name_canonical": canon, "datetime_utc": pd.Timestamp(dt),
            "actual": actual, "forecast": actual - 0.1, "previous": actual - 0.2,
            "released": True, "source": "ff"}


def _ff_frame():
    rows = []
    for i, (ccy, canon, _key) in enumerate(BUCKET_C_SERIES):
        for m in range(1, 5):
            rows.append(_ff_row(ccy, canon, 1.0 + 0.1 * m, f"2026-0{m}-1{i % 9}"))
    return pd.DataFrame(rows, columns=CANON_COLUMNS)


def test_all_17_resolve_to_expected_indicator_key():
    frame = to_scoring_frame(_ff_frame())
    for ccy, canon, key in BUCKET_C_SERIES:
        sub = frame[(frame["currency"] == ccy) & (frame["indicator_key"] == key)]
        assert len(sub) >= 1, f"({ccy}, {canon!r}) did not resolve to {key!r}"
        assert (sub["source"] == "ff").all()


def test_indicator_key_applies_only_to_its_declared_currencies():
    """A shared indicator_key (e.g. industrial_production_mm across
    USD/GBP/JPY) must not silently apply to a currency never approved for
    it — the config's own `currencies:` whitelist is what's tested here."""
    cfg = _load_indicators_cfg()
    indicators = cfg.get("indicators", {})
    expected_currencies = {
        "tokyo_core_cpi_yoy": {"JPY"},
        "industrial_production_mm": {"USD", "GBP", "JPY"},
        "durable_goods_orders_mm": {"USD"},
        "core_machinery_orders_mm": {"JPY"},
        "personal_spending_mm": {"USD"},
        "personal_income_mm": {"USD"},
        "import_prices": {"USD", "AUD"},
        "capital_expenditure": {"AUD", "JPY"},
        "company_operating_profits_qoq": {"AUD"},
        "sppi_yoy": {"JPY"},
        "gdp_price_index": {"USD", "JPY"},
        "unit_labor_costs_qoq": {"USD"},
    }
    for key, expected in expected_currencies.items():
        assert key in indicators, f"{key} missing from data/economic_indicators.yaml"
        got = set(indicators[key].get("currencies") or [])
        assert got == expected, f"{key}: currencies={got}, expected={expected}"


def test_direction_is_bullish_for_all_17():
    """FAZA 1 rated every one of the 17 "adauga" candidates as direction-clear,
    higher=bullish (+1) — none of the direction-ambiguous "intreaba" ones are
    among them. Regression: a future edit must not silently invert one."""
    cfg = _load_indicators_cfg()
    indicators = cfg.get("indicators", {})
    for key in NEW_INDICATOR_KEYS:
        assert indicators[key]["direction"] == 1, f"{key}: expected direction=1"


def test_matcher_has_no_duplicate_pattern_per_country():
    """Each (country, pattern) must map to exactly one indicator — a second
    rule for the same raw name would silently shadow the first."""
    cfg = _load_indicators_cfg()
    matcher_cfg = cfg.get("matcher", {})
    for country, rules in matcher_cfg.items():
        patterns = [r["pattern"] for r in rules]
        assert len(patterns) == len(set(patterns)), f"{country}: duplicate pattern(s) in matcher"


def test_all_12_new_keys_have_a_label():
    for key in NEW_INDICATOR_KEYS:
        assert key in INDICATOR_LABELS, f"{key} missing from INDICATOR_LABELS"
        assert INDICATOR_LABELS[key].strip() != ""


def test_shared_key_industrial_production_routes_jpy_flash_variant():
    """JPY feeds industrial_production_mm via its own flash ('Prelim') name —
    not the plain 'Industrial Production m/m' USD/GBP use (JPY never
    publishes that exact string; a matcher rule for it would just never
    fire, not error, so this is checked explicitly)."""
    cfg = _load_indicators_cfg()
    matcher = CompiledMatcher(cfg.get("matcher", {}))
    assert matcher.match("Japan", "Prelim Industrial Production m/m") == "industrial_production_mm"
    assert matcher.match("United States", "Industrial Production m/m") == "industrial_production_mm"
    assert matcher.match("United Kingdom", "Industrial Production m/m") == "industrial_production_mm"
