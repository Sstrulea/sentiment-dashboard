"""feat/aud-inflation-monthly-promotion.

AUD's quarterly core_cpi (RBA Trimmed Mean CPI y/y) is dead (config/
alert_exceptions.yaml documented this before this change removed the now-
orphaned entry). PR #8's archive backfill landed AUD cpi_monthly and
trimmed_mean_cpi_monthly at n=6 valid scored prints each, exactly
fallback_min_prints, z-scored not pct (confirmed via compute_indicator_score
before this change — see the PR body's pre-flight section). This promotes
both into the scored `inflation` category for AUD only, and removes AUD from
core_cpi's scope.
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
# CHANGE 1 — promoted, AUD only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key", ["cpi_monthly", "trimmed_mean_cpi_monthly"])
def test_promoted_series_are_scored_inflation_for_aud(indicators_cfg, key):
    cfg = indicators_cfg["indicators"][key]
    assert cfg["category"] == "inflation"
    assert float(cfg["weight"]) == 1.0
    assert _indicator_applies("AUD", cfg) is True


@pytest.mark.parametrize("key", ["cpi_monthly", "trimmed_mean_cpi_monthly"])
@pytest.mark.parametrize("ccy", [c for c in CCYS if c != "AUD"])
def test_promoted_series_remain_excluded_for_every_other_currency(indicators_cfg, key, ccy):
    cfg = indicators_cfg["indicators"][key]
    assert _indicator_applies(ccy, cfg) is False


@pytest.mark.parametrize("key,expected_n", [("cpi_monthly", 6), ("trimmed_mean_cpi_monthly", 6)])
def test_promoted_series_clear_fallback_min_prints_and_z_score(
    indicators_cfg, scored_frame, key, expected_n
):
    """Locks in the pre-flight finding: this promotion is only correct
    because both series land on the z-score path, not pct fallback."""
    from src.economic_compute import compute_indicator_score

    sub = scored_frame[(scored_frame.currency == "AUD") & (scored_frame.indicator_key == key)]
    n_valid = int(sub["actual"].notna().sum())
    assert n_valid == expected_n
    assert n_valid >= indicators_cfg["defaults"]["fallback_min_prints"]

    result = compute_indicator_score(
        sub, indicators_cfg["indicators"][key], indicators_cfg["defaults"],
        pd.Timestamp("2026-08-05"), allow_stale=False, currency="AUD",
    )
    assert result is not None
    assert result["flag"] is None   # None == z-scored; "fallback" would mean pct
    assert result["z"] is not None


def test_matcher_never_routes_another_currency_to_the_promoted_keys(indicators_cfg):
    """The currencies:[AUD] whitelist is a belt-and-suspenders guard on top
    of a structural fact: no OTHER country's matcher block has a rule for
    either key. Confirm that directly via CompiledMatcher, not by reading
    the YAML."""
    matcher = CompiledMatcher(indicators_cfg["matcher"])
    other_countries = [c for c in matcher.countries if c != "Australia"]
    assert other_countries, "sanity: matcher should know about other countries"
    for country in other_countries:
        for canonical in ("Monthly CPI Indicator m/m", "Monthly Trimmed Mean CPI m/m"):
            assert matcher.match(country, canonical) is None, (
                f"{country} unexpectedly routes {canonical!r} to an indicator_key"
            )
    assert matcher.match("Australia", "Monthly CPI Indicator m/m") == "cpi_monthly"
    assert matcher.match("Australia", "Monthly Trimmed Mean CPI m/m") == "trimmed_mean_cpi_monthly"


# ---------------------------------------------------------------------------
# CHANGE 2 — AUD core_cpi removed
# ---------------------------------------------------------------------------

def test_aud_core_cpi_no_longer_applies(indicators_cfg):
    cfg = indicators_cfg["indicators"]["core_cpi"]
    assert _indicator_applies("AUD", cfg) is False


def test_other_currencies_core_cpi_applicability_unchanged(indicators_cfg):
    """As of this test's own change (fix/aud-inflation-monthly-promotion),
    NZD/CHF still applied here -- they were listed in the whitelist purely
    to preserve their pre-existing no_data applicability, unaffected by the
    AUD removal. feat/board-slot-cleanup-and-cad-promotion later removed
    NZD/CHF from core_cpi's scope for real (docs/empty-slot-routing-audit.md:
    both are NO SERIES, zero prints ever under any name) -- this pin is
    updated to that, not reverted."""
    cfg = indicators_cfg["indicators"]["core_cpi"]
    still_applies = {"USD", "EUR", "GBP", "JPY", "CAD"}
    for ccy in still_applies:
        assert _indicator_applies(ccy, cfg) is True, ccy
    for ccy in {"NZD", "CHF"}:
        assert _indicator_applies(ccy, cfg) is False, ccy


def test_aud_core_cpi_absent_from_every_scored_slot(scored_frame, indicators_cfg):
    """No (AUD, core_cpi) row should be reachable through the scoring path
    at all -- the matcher can still historically tag old rows core_cpi (the
    dead canonical's raw rows don't disappear from the parquet), but the
    indicator no longer applies to AUD, so no per-currency computation ever
    considers it."""
    cfg = indicators_cfg["indicators"]["core_cpi"]
    assert _indicator_applies("AUD", cfg) is False
    # the underlying historical rows are untouched (this change doesn't
    # rewrite history), but nothing should treat them as AUD's scored slot
    sub = scored_frame[(scored_frame.currency == "AUD") & (scored_frame.indicator_key == "core_cpi")]
    assert len(sub) > 0, "sanity: the old dead-series rows still exist historically"


# ---------------------------------------------------------------------------
# CHANGE 3 — orphaned exception cleaned up
# ---------------------------------------------------------------------------

def test_no_orphaned_aud_core_cpi_exception():
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
    assert ("AUD", "core_cpi") not in {(r["currency"], r["indicator_key"]) for r in stale_rows}

    exceptions = load_exceptions(ROOT / "config" / "alert_exceptions.yaml")
    assert not any(e["currency"] == "AUD" and e["indicator"] == "core_cpi" for e in exceptions)
    orphans = find_orphaned_exceptions(stale_rows, exceptions, scope="calendar")
    assert orphans == []
