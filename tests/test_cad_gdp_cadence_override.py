"""fix/cad-gdp-cadence-override.

docs/diag-cad-gdp-fallback.md established the cause: CAD gdp_qoq is fed via
config/ff_aliases.yaml's "GDP m/m": "GDP q/q" # xf -- genuinely monthly data
routed into a quarterly-configured slot. `_dedup_flash_final` got
`dedup_gap=45d` from the static `quarterly` frequency, but CAD's real
release gaps are 24-39d, so the entire history collapsed into one
flash/final cluster (42 rows -> 2 kept -> 1 with a valid consensus),
forcing pct fallback and saturating a 0.1pp beat on a 0.40 consensus to
+2. Adding `frequency_overrides: {CAD: monthly}` to gdp_qoq tells the
engine the truth about CAD's cadence -- nothing else changes.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml
import pytest

from src.economic_compute import (
    compute_indicator_score, effective_frequency, _max_age_for, _dedup_flash_final,
)
from src.ff_scoring import to_scoring_frame, build_matcher

ROOT = Path(__file__).resolve().parents[1]
# Frozen FF calendar: data/economic_calendar_ff.parquet at snapshot 4ace910
# (2026-09-23 07:06 UTC), migrated with the phase-2 provenance (Z5). Never the
# live data/ file, which the hourly refresh keeps moving (audit 4D).
FROZEN_FF = ROOT / "tests" / "fixtures" / "frozen" / "economic_calendar_ff_4ace910.parquet"


def _point_in_time(scored: pd.DataFrame, as_of) -> pd.DataFrame:
    """Only what was released by `as_of` — a pin must not see later prints."""
    return scored[pd.to_datetime(scored["release_dt"]) <= pd.Timestamp(as_of)]
CCYS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
AS_OF = pd.Timestamp("2026-08-05")


@pytest.fixture(scope="module")
def indicators_cfg():
    with open(ROOT / "data" / "economic_indicators.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def scored_frame():
    raw = pd.read_parquet(FROZEN_FF)
    return _point_in_time(to_scoring_frame(raw, build_matcher()), AS_OF)


def _cad_gdp_sub(scored_frame):
    return scored_frame[(scored_frame.currency == "CAD") & (scored_frame.indicator_key == "gdp_qoq")]


def test_cad_gdp_qoq_has_the_monthly_override(indicators_cfg):
    cfg = indicators_cfg["indicators"]["gdp_qoq"]
    assert cfg.get("frequency") == "quarterly"  # unchanged, board default
    # GBP joined this dict in FAZA 1E (fix/gbp-gdp-frequency, same mechanism,
    # see tests/test_gbp_gdp_cadence_override.py) -- CAD's own entry is untouched.
    assert cfg.get("frequency_overrides", {}).get("CAD") == "monthly"


def test_cad_effective_frequency_is_monthly(indicators_cfg):
    cfg = indicators_cfg["indicators"]["gdp_qoq"]
    defaults = indicators_cfg["defaults"]
    freq = effective_frequency(cfg, defaults, "CAD")
    assert freq == "monthly"
    assert _max_age_for(cfg, defaults, freq) == 45


def test_dedup_keeps_the_monthly_prints_instead_of_collapsing_them(indicators_cfg, scored_frame):
    """The whole point of the fix: dedup_gap should now be 18d (monthly),
    not 45d (quarterly) -- so CAD's real ~28d release gaps each start a new
    cluster, and the 42-row history is NOT collapsed to a single kept row."""
    cfg = indicators_cfg["indicators"]["gdp_qoq"]
    defaults = indicators_cfg["defaults"]
    sub = _cad_gdp_sub(scored_frame).sort_values("release_dt").reset_index(drop=True).copy()
    sub["actual"] = pd.to_numeric(sub["actual"], errors="coerce")
    sub["consensus"] = pd.to_numeric(sub["consensus"], errors="coerce")

    freq = effective_frequency(cfg, defaults, "CAD")
    dedup_gap = defaults["dedup_gap_days"][freq]
    assert dedup_gap == 18

    deduped = _dedup_flash_final(sub, dedup_gap)
    assert len(deduped) == len(sub), "monthly dedup_gap should not collapse distinct monthly prints"

    pairs = deduped[deduped["actual"].notna() & deduped["consensus"].notna()]
    assert len(pairs) >= indicators_cfg["defaults"]["fallback_min_prints"]


def test_cad_gdp_qoq_resolves_to_z_score_not_fallback(indicators_cfg, scored_frame):
    cfg = indicators_cfg["indicators"]["gdp_qoq"]
    defaults = indicators_cfg["defaults"]
    sub = _cad_gdp_sub(scored_frame)
    result = compute_indicator_score(sub, cfg, defaults, AS_OF, allow_stale=False, currency="CAD")
    assert result is not None
    assert result["flag"] is None  # None == z-scored; "fallback" would mean pct
    assert result["z"] is not None


def test_cad_gdp_qoq_score_is_pinned():
    """Numeric anchor, deliberately pinned -- not a tautology restating
    whatever the code currently does. If the real parquet moves on and this
    no longer holds, that's a genuine discrepancy to investigate, not
    something to adjust this pin to match."""
    ind_cfg = yaml.safe_load((ROOT / "data" / "economic_indicators.yaml").read_text())
    raw = pd.read_parquet(FROZEN_FF)
    scored = to_scoring_frame(raw, build_matcher())
    scored = _point_in_time(scored, AS_OF)
    sub = _cad_gdp_sub(scored)
    result = compute_indicator_score(
        sub, ind_cfg["indicators"]["gdp_qoq"], ind_cfg["defaults"], AS_OF,
        allow_stale=False, currency="CAD",
    )
    assert result["actual"] == 0.5
    assert result["consensus"] == 0.4
    assert result["flag"] is None
    assert result["z"] == pytest.approx(0.9228287507581274, abs=1e-9)
    assert result["score"] == 1
    assert result["stale"] is False


def test_no_other_currency_gdp_qoq_frequency_or_max_age_changes(indicators_cfg):
    """CAD's own override doesn't affect any currency besides itself and
    GBP (which gained its own, independent override in FAZA 1E -- see
    tests/test_gbp_gdp_cadence_override.py). Every other currency applicable
    to gdp_qoq must resolve to the board default (quarterly/110d)."""
    cfg = indicators_cfg["indicators"]["gdp_qoq"]
    defaults = indicators_cfg["defaults"]
    for ccy in CCYS:
        if ccy in ("CAD", "GBP"):
            continue
        freq = effective_frequency(cfg, defaults, ccy)
        assert freq == "quarterly", ccy
        assert _max_age_for(cfg, defaults, freq) == 110, ccy


def test_no_other_indicator_key_changed(indicators_cfg):
    """Only gdp_qoq gained a frequency_overrides entry as part of this fix
    -- every other indicator's frequency/frequency_overrides is untouched
    relative to the known pre-existing set (spot-checked against a few
    indicators known to carry their own overrides already)."""
    unaffected_with_overrides = {
        "ppi_yoy": {"AUD": "quarterly", "NZD": "quarterly"},
        "employment_change": {"EUR": "quarterly", "NZD": "quarterly"},
        "wage_growth": {"AUD": "quarterly", "NZD": "quarterly"},
        "unemployment_rate": {"NZD": "quarterly"},
        "retail_sales": {"NZD": "quarterly"},
    }
    for key, expected in unaffected_with_overrides.items():
        assert indicators_cfg["indicators"][key].get("frequency_overrides") == expected, key
