"""feat/board-slot-cleanup-and-cad-promotion.

docs/empty-slot-routing-audit.md classified the board's 11 empty scored
slots and surfaced two actionable items. Three changes, all decided:
  1. JPY ppi_yoy — MATCHER GAP. Raw "PPI y/y" was already aliased with 42
     real parquet prints; Japan's matcher block had no rule for ppi_yoy.
     Added one.
  2. CAD common_cpi_yoy / trimmed_cpi_yoy — promoted from weight-0
     display-only into scored inflation, CAD only (n=43/41, both z-scored,
     ~100% release-date coincidence with CAD's scored cpi_yoy/core_cpi at
     identical timestamps — the same structure that cleared AUD's
     promotion).
  3. Removed the 9 NO SERIES slots from their indicators' currency
     whitelists: NZD/CHF core_cpi, JPY/CHF services_pmi, JPY/CHF
     employment_change, EUR/CAD/CHF wage_growth. Each has zero prints ever
     under any name.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml
import pytest

from src.economic_compute import _indicator_applies
from src.economic_fetch import CompiledMatcher
from src.ff_scoring import CCY2COUNTRY, to_scoring_frame, build_matcher

ROOT = Path(__file__).resolve().parents[1]
# Frozen FF calendar: data/economic_calendar_ff.parquet at snapshot 4ace910
# (2026-09-23 07:06 UTC), migrated with the phase-2 provenance (Z5). Never the
# live data/ file, which the hourly refresh keeps moving (audit 4D).
FROZEN_FF = ROOT / "tests" / "fixtures" / "frozen" / "economic_calendar_ff_4ace910.parquet"

# The as_of every pin in this module was taken at.
PIN_AS_OF = pd.Timestamp("2026-08-05")


def _point_in_time(scored: pd.DataFrame, as_of) -> pd.DataFrame:
    """Only what was released by `as_of` — a pin must not see later prints."""
    return scored[pd.to_datetime(scored["release_dt"]) <= pd.Timestamp(as_of)]
CCYS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]


@pytest.fixture(scope="module")
def indicators_cfg():
    with open(ROOT / "data" / "economic_indicators.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def scored_frame():
    raw = pd.read_parquet(FROZEN_FF)
    return _point_in_time(to_scoring_frame(raw, build_matcher()), PIN_AS_OF)


# ---------------------------------------------------------------------------
# CHANGE 1 — JPY ppi_yoy matcher gap fixed
# ---------------------------------------------------------------------------

def test_jpy_ppi_yoy_matcher_rule_routes(indicators_cfg):
    matcher = CompiledMatcher(indicators_cfg["matcher"])
    assert matcher.match("Japan", "PPI y/y") == "ppi_yoy"


def test_jpy_ppi_yoy_lands_in_scored_inflation(indicators_cfg, scored_frame):
    cfg = indicators_cfg["indicators"]["ppi_yoy"]
    assert cfg["category"] == "inflation"
    assert float(cfg["weight"]) == 1.0
    assert _indicator_applies("JPY", cfg) is True

    sub = scored_frame[(scored_frame.currency == "JPY") & (scored_frame.indicator_key == "ppi_yoy")]
    assert len(sub) == 42
    assert int(sub["actual"].notna().sum()) > 0


def test_jpy_ppi_yoy_matcher_gap_did_not_leak_to_other_countries(indicators_cfg):
    """Only Japan's own matcher block should have gained a rule — every
    other country's ppi_yoy routing is untouched."""
    matcher = CompiledMatcher(indicators_cfg["matcher"])
    for country in matcher.countries:
        if country == "Japan":
            continue
        assert matcher.match(country, "PPI y/y") in (None, "ppi_yoy"), (
            f"{country} unexpectedly changed routing for canonical 'PPI y/y'"
        )


# ---------------------------------------------------------------------------
# CHANGE 2 — CAD common_cpi_yoy / trimmed_cpi_yoy promoted, CAD only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["common_cpi_yoy", "trimmed_cpi_yoy"])
def test_cad_series_are_scored_inflation_for_cad(indicators_cfg, key):
    cfg = indicators_cfg["indicators"][key]
    assert cfg["category"] == "inflation"
    assert float(cfg["weight"]) == 1.0
    assert _indicator_applies("CAD", cfg) is True


@pytest.mark.parametrize("key", ["common_cpi_yoy", "trimmed_cpi_yoy"])
@pytest.mark.parametrize("ccy", [c for c in CCYS if c != "CAD"])
def test_cad_series_remain_excluded_for_every_other_currency(indicators_cfg, key, ccy):
    cfg = indicators_cfg["indicators"][key]
    assert _indicator_applies(ccy, cfg) is False


@pytest.mark.parametrize("key,expected_n", [("common_cpi_yoy", 43), ("trimmed_cpi_yoy", 41)])
def test_cad_series_clear_fallback_min_prints_and_z_score(indicators_cfg, scored_frame, key, expected_n):
    from src.economic_compute import compute_indicator_score

    sub = scored_frame[(scored_frame.currency == "CAD") & (scored_frame.indicator_key == key)]
    n_valid = int(sub["actual"].notna().sum())
    assert n_valid == expected_n
    assert n_valid >= indicators_cfg["defaults"]["fallback_min_prints"]

    result = compute_indicator_score(
        sub, indicators_cfg["indicators"][key], indicators_cfg["defaults"],
        pd.Timestamp("2026-08-05"), allow_stale=False, currency="CAD",
    )
    assert result is not None
    assert result["flag"] is None
    assert result["z"] is not None


def test_matcher_never_routes_another_currency_to_the_cad_promoted_keys(indicators_cfg):
    matcher = CompiledMatcher(indicators_cfg["matcher"])
    other_countries = [c for c in matcher.countries if c != "Canada"]
    assert other_countries
    for country in other_countries:
        for canonical in ("Common CPI y/y", "Trimmed CPI y/y"):
            assert matcher.match(country, canonical) is None, (
                f"{country} unexpectedly routes {canonical!r} to an indicator_key"
            )
    assert matcher.match("Canada", "Common CPI y/y") == "common_cpi_yoy"
    assert matcher.match("Canada", "Trimmed CPI y/y") == "trimmed_cpi_yoy"


# ---------------------------------------------------------------------------
# CHANGE 3 — 9 NO SERIES slots removed from their whitelists
# ---------------------------------------------------------------------------

REMOVED_SLOTS = [
    ("NZD", "core_cpi"), ("CHF", "core_cpi"),
    ("JPY", "services_pmi"), ("CHF", "services_pmi"),
    ("JPY", "employment_change"), ("CHF", "employment_change"),
    ("EUR", "wage_growth"), ("CAD", "wage_growth"), ("CHF", "wage_growth"),
]


@pytest.mark.parametrize("ccy,key", REMOVED_SLOTS)
def test_removed_slot_no_longer_applies(indicators_cfg, ccy, key):
    cfg = indicators_cfg["indicators"][key]
    assert _indicator_applies(ccy, cfg) is False


@pytest.mark.parametrize("ccy,key", REMOVED_SLOTS)
def test_removed_slot_absent_from_every_scored_slot(scored_frame, indicators_cfg, ccy, key):
    """No (ccy, key) row should be reachable through the scoring path at
    all — each had zero valid prints before this change (confirmed
    pre-flight), so this is a no-op on scoring, purely a coverage-honesty
    cleanup."""
    cfg = indicators_cfg["indicators"][key]
    assert _indicator_applies(ccy, cfg) is False
    sub = scored_frame[(scored_frame.currency == ccy) & (scored_frame.indicator_key == key)]
    assert int(sub["actual"].notna().sum()) == 0


def test_currencies_still_applicable_to_removed_indicators_are_unaffected(indicators_cfg):
    """The whitelist edits should change ONLY the 9 removed pairs, not any
    other currency's applicability to core_cpi / services_pmi /
    employment_change / wage_growth."""
    still_applies = {
        "core_cpi": {"USD", "EUR", "GBP", "JPY", "CAD"},
        "services_pmi": {"USD", "EUR", "GBP", "AUD", "NZD", "CAD"},
        "employment_change": {"USD", "EUR", "GBP", "AUD", "NZD", "CAD"},
        "wage_growth": {"USD", "GBP", "JPY", "AUD", "NZD"},
    }
    for key, ccys in still_applies.items():
        cfg = indicators_cfg["indicators"][key]
        for ccy in ccys:
            assert _indicator_applies(ccy, cfg) is True, (key, ccy)
        for ccy in set(CCYS) - ccys:
            assert _indicator_applies(ccy, cfg) is False, (key, ccy)


# ---------------------------------------------------------------------------
# CHANGE 3 — orphaned exceptions
# ---------------------------------------------------------------------------

def test_no_orphaned_exceptions_after_removals():
    from src.calendar_freshness_guard import per_currency_indicator_freshness, currency_freshness_report
    from src.alert_exceptions import load_exceptions, find_orphaned_exceptions

    with open(ROOT / "data" / "economic_indicators.yaml") as f:
        ind_cfg = yaml.safe_load(f)
    raw = pd.read_parquet(FROZEN_FF)
    scored = to_scoring_frame(raw, build_matcher())
    scored = _point_in_time(scored, PIN_AS_OF)

    as_of = pd.Timestamp("2026-08-05")
    per_ind = per_currency_indicator_freshness(scored, ind_cfg, as_of)
    report = currency_freshness_report(per_ind)
    stale_rows = [
        {"currency": ccy, "indicator_key": ind["indicator_key"]}
        for ccy in report["stale_currencies"]
        for ind in report["by_currency"][ccy]["stale_indicators"]
    ]
    for ccy, key in REMOVED_SLOTS:
        assert (ccy, key) not in {(r["currency"], r["indicator_key"]) for r in stale_rows}

    exceptions = load_exceptions(ROOT / "config" / "alert_exceptions.yaml")
    orphans = find_orphaned_exceptions(stale_rows, exceptions, scope="calendar")
    assert orphans == []
