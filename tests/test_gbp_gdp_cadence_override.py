"""fix/gbp-gdp-frequency (FAZA 1E).

Same mechanism as CAD's fix (tests/test_cad_gdp_cadence_override.py,
013b40c), a different matcher route: GBP's `gdp_qoq` is fed by this file's
own matcher (`United Kingdom: '^GDP m/m$' -> gdp_qoq`, added 2026-06-11 —
ONS discontinued "GDP q/q" in 2025-05, so the live scored release is the
monthly "GDP m/m" print), not config/ff_aliases.yaml. But the effect on
`_dedup_flash_final` is identical: GBP's real release gaps are ~29d
(median), yet the declared `quarterly` frequency gives a 45d dedup_gap
that never sees a gap in GBP's real cadence, collapsing the entire 44-row
history into one flash/final cluster (2 kept), forcing a saturated pct
fallback (score -2 on an ordinary -0.4pp miss) instead of a proper
z-score (z=-1.474, score -1). Adding `frequency_overrides: {GBP: monthly}`
tells the engine the truth about GBP's cadence — nothing else changes.
See docs/faza1e-report.md for the full diagnostic and before/after table.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.economic_compute import (
    _dedup_flash_final, _max_age_for, compute_indicator_score, effective_frequency,
)
from src.ff_scoring import build_matcher, to_scoring_frame

ROOT = Path(__file__).resolve().parents[1]
# Frozen FF calendar: data/economic_calendar_ff.parquet at snapshot 4ace910
# (2026-09-23 07:06 UTC), migrated with the phase-2 provenance (Z5). Never the
# live data/ file, which the hourly refresh keeps moving (audit 4D).
FROZEN_FF = ROOT / "tests" / "fixtures" / "frozen" / "economic_calendar_ff_4ace910.parquet"


def _point_in_time(scored: pd.DataFrame, as_of) -> pd.DataFrame:
    """Only what was released by `as_of` — a pin must not see later prints."""
    return scored[pd.to_datetime(scored["release_dt"]) <= pd.Timestamp(as_of)]
CCYS = ["USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"]
AS_OF = pd.Timestamp("2026-08-20T13:19:06")


@pytest.fixture(scope="module")
def indicators_cfg():
    with open(ROOT / "data" / "economic_indicators.yaml") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def scored_frame():
    raw = pd.read_parquet(FROZEN_FF)
    return _point_in_time(to_scoring_frame(raw, build_matcher()), AS_OF)


def _gbp_gdp_sub(scored_frame):
    return scored_frame[(scored_frame.currency == "GBP") & (scored_frame.indicator_key == "gdp_qoq")]


def test_gbp_gdp_qoq_has_the_monthly_override(indicators_cfg):
    cfg = indicators_cfg["indicators"]["gdp_qoq"]
    assert cfg.get("frequency") == "quarterly"  # unchanged, board default
    assert cfg.get("frequency_overrides") == {"CAD": "monthly", "GBP": "monthly"}


def test_gbp_effective_frequency_is_monthly(indicators_cfg):
    cfg = indicators_cfg["indicators"]["gdp_qoq"]
    defaults = indicators_cfg["defaults"]
    freq = effective_frequency(cfg, defaults, "GBP")
    assert freq == "monthly"
    assert _max_age_for(cfg, defaults, freq) == 45


def test_dedup_keeps_the_monthly_prints_instead_of_collapsing_them(indicators_cfg, scored_frame):
    """The whole point of the fix: dedup_gap should now be 18d (monthly),
    not 45d (quarterly) -- so GBP's real ~29d release gaps each start a new
    cluster, and the ~44-row history is NOT collapsed to 1-2 kept rows."""
    cfg = indicators_cfg["indicators"]["gdp_qoq"]
    defaults = indicators_cfg["defaults"]
    sub = _gbp_gdp_sub(scored_frame).sort_values("release_dt").reset_index(drop=True).copy()
    sub["actual"] = pd.to_numeric(sub["actual"], errors="coerce")
    sub["consensus"] = pd.to_numeric(sub["consensus"], errors="coerce")

    freq = effective_frequency(cfg, defaults, "GBP")
    dedup_gap = defaults["dedup_gap_days"][freq]
    assert dedup_gap == 18

    deduped = _dedup_flash_final(sub, dedup_gap)
    assert len(deduped) >= len(sub) - 2, "monthly dedup_gap should not collapse distinct monthly prints"

    pairs = deduped[deduped["actual"].notna() & deduped["consensus"].notna()]
    assert len(pairs) >= indicators_cfg["defaults"]["fallback_min_prints"]


def test_gbp_gdp_qoq_resolves_to_z_score_not_fallback(indicators_cfg, scored_frame):
    cfg = indicators_cfg["indicators"]["gdp_qoq"]
    defaults = indicators_cfg["defaults"]
    sub = _gbp_gdp_sub(scored_frame)
    result = compute_indicator_score(sub, cfg, defaults, AS_OF, allow_stale=True, currency="GBP")
    assert result is not None
    assert result["flag"] is None  # None == z-scored; "fallback" would mean pct
    assert result["z"] is not None


def test_gbp_gdp_qoq_score_is_pinned():
    """Numeric anchor, deliberately pinned -- not a tautology restating
    whatever the code currently does. If the real parquet moves on and this
    no longer holds, that's a genuine discrepancy to investigate, not
    something to adjust this pin to match. Pre-registered in
    docs/faza1e-report.md before this fix was applied; matched exactly."""
    ind_cfg = yaml.safe_load((ROOT / "data" / "economic_indicators.yaml").read_text())
    raw = pd.read_parquet(FROZEN_FF)
    scored = to_scoring_frame(raw, build_matcher())
    scored = _point_in_time(scored, AS_OF)
    sub = _gbp_gdp_sub(scored)
    result = compute_indicator_score(
        sub, ind_cfg["indicators"]["gdp_qoq"], ind_cfg["defaults"], AS_OF,
        allow_stale=True, currency="GBP",
    )
    # Re-pinned by audit 4D (2026-09-23), for ONE documented reason: the
    # consensus. FF revised its GDP m/m forecast for 2026-08-13 from -0.1%
    # (weekly feeds of 08-10/08-11) to 0.0% (feeds of 08-12..08-15, i.e. the
    # last one before the release — data/ff_raw). The old pin's -0.1 came from
    # JBlanked overwriting the FF forecast at merge, which audit phase 2 (2.1)
    # removed: an FF-delivered forecast is never overwritten. The actual and
    # the release are unchanged; z/score follow from the consensus (and the
    # frame is point-in-time at AS_OF — it saw later prints before).
    # Was: consensus -0.1, z -1.4740554623801776, score -1.
    assert result["actual"] == -0.5
    assert result["consensus"] == 0.0
    assert result["flag"] is None
    assert result["z"] == pytest.approx(-1.8776690404970269, abs=1e-9)
    assert result["score"] == -2
    assert result["stale"] is False


def test_no_other_currency_gdp_qoq_frequency_or_max_age_changes(indicators_cfg):
    """The new override is GBP-only (alongside the pre-existing CAD one).
    Every other currency applicable to gdp_qoq must resolve to the same
    frequency/max_age as the board default (quarterly/110d)."""
    cfg = indicators_cfg["indicators"]["gdp_qoq"]
    defaults = indicators_cfg["defaults"]
    for ccy in CCYS:
        if ccy in ("CAD", "GBP"):
            continue
        freq = effective_frequency(cfg, defaults, ccy)
        assert freq == "quarterly", ccy
        assert _max_age_for(cfg, defaults, freq) == 110, ccy


def test_no_other_indicator_key_changed(indicators_cfg):
    """Only gdp_qoq's frequency_overrides changed as part of this fix --
    every other indicator's frequency/frequency_overrides is untouched
    relative to the known pre-existing set."""
    unaffected_with_overrides = {
        "ppi_yoy": {"AUD": "quarterly", "NZD": "quarterly"},
        "employment_change": {"EUR": "quarterly", "NZD": "quarterly"},
        "wage_growth": {"AUD": "quarterly", "NZD": "quarterly"},
        "unemployment_rate": {"NZD": "quarterly"},
        "retail_sales": {"NZD": "quarterly"},
    }
    for key, expected in unaffected_with_overrides.items():
        assert indicators_cfg["indicators"][key].get("frequency_overrides") == expected, key
