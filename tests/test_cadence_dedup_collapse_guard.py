"""FAZA 1E permanent guard.

The CAD (013b40c) and GBP (this phase) gdp_qoq bugs were the same mechanism
twice: a scored (weight > 0) indicator's DECLARED `frequency` (used to look
up `defaults.dedup_gap_days[freq]`) was wider than its real, empirically-
observed release cadence. Since this feed carries no usable MT5 `period`
for these rows, `_dedup_flash_final` falls back to release-date-proximity
clustering with that too-wide gap, and — because the indicator's true
cadence never produces a gap that large — the ENTIRE history collapses
into one cluster, forcing a saturated pct-fallback score instead of a
proper z-score. This guard scans every (currency, indicator_key) pair that
actually feeds a currency's index (weight > 0) and fails if any of them
would suffer the same collapse today, so a third recurrence (this is
already the second fix of the identical mechanism) is caught in CI instead
of discovered live on the dashboard.

Deliberately excludes weight == 0 (display-only) indicators, e.g.
`interest_rate_decision`: policy-meeting cadence genuinely reads as
"quarterly" to `detect_cadence` on several currencies (CHF/GBP/JPY/NZD)
against a `monthly`-declared frequency, but that mismatch runs the SAFE
direction (declared narrower than real -> a TIGHTER dedup_gap -> under-,
never over-, collapse) and the indicator carries zero scoring weight
either way. See docs/faza1e-report.md Part 1.4 for the full scan output
across all 8 currencies.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from src.economic_compute import _dedup_flash_final, effective_frequency
from src.ff_scoring import build_matcher, detect_cadence, load_can_be_zero, to_scoring_frame

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def indicators_cfg():
    return yaml.safe_load((ROOT / "data" / "economic_indicators.yaml").read_text())


@pytest.fixture(scope="module")
def scored_frame(indicators_cfg):
    ff = pd.read_parquet(ROOT / "data" / "economic_calendar_ff.parquet")
    ff["datetime_utc"] = pd.to_datetime(ff["datetime_utc"])
    matcher = build_matcher()
    cbz = load_can_be_zero(indicators_cfg)
    return to_scoring_frame(ff, matcher, can_be_zero=cbz)


def test_no_scored_indicator_has_a_declared_cadence_wide_enough_to_collapse_its_history(
    indicators_cfg, scored_frame,
):
    defaults = indicators_cfg.get("defaults", {}) or {}
    dedup_gap_days = defaults.get("dedup_gap_days", {}) or {}
    indicators = indicators_cfg.get("indicators", {}) or {}

    offenders = []
    for (ccy, key), sub in scored_frame.groupby(["currency", "indicator_key"]):
        cfg = indicators.get(key, {})
        if float(cfg.get("weight", 1.0)) <= 0:
            continue  # display-only — doesn't feed any currency's index
        allowed = cfg.get("currencies")
        if allowed and ccy not in allowed:
            continue

        sub = sub.sort_values("release_dt").reset_index(drop=True)
        printed = sub[sub["actual"].notna()]
        if len(printed) < 8:
            continue  # too little history for detect_cadence / a collapse to even be meaningful

        empirical = detect_cadence(printed["release_dt"])
        if empirical == "unknown":
            continue

        declared = effective_frequency(cfg, defaults, ccy)
        declared_gap = dedup_gap_days.get(declared)
        empirical_gap = dedup_gap_days.get(empirical)
        if declared_gap is None or empirical_gap is None or declared_gap <= empirical_gap:
            continue  # declared cadence is not WIDER than reality — not the dangerous direction

        n_declared = len(_dedup_flash_final(sub.copy(), declared_gap))
        n_empirical = len(_dedup_flash_final(sub.copy(), empirical_gap))
        if n_empirical > 0 and n_declared <= max(2, n_empirical * 0.3):
            offenders.append(
                f"{ccy}/{key}: declared={declared} (gap={declared_gap}d) but empirical={empirical} "
                f"(gap={empirical_gap}d) — dedup keeps {n_declared}/{len(sub)} rows instead of "
                f"~{n_empirical}. Add `frequency_overrides: {{{ccy}: {empirical}}}` to `{key}` in "
                f"data/economic_indicators.yaml (see the CAD/GBP precedent on this same key)."
            )

    assert not offenders, (
        "Scored indicator(s) with a declared cadence wide enough to collapse their real "
        "history via _dedup_flash_final's proximity fallback (the CAD/GBP gdp_qoq bug, now "
        f"a third time):\n  " + "\n  ".join(offenders)
    )
