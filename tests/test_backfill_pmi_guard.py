"""Regression: the 2026-07-30 CAD Median CPI y/y / AUD Household Spending
backfill (migrations/2026-07-30_backfill_median_household.py) must never
reintroduce the 219 cross-country PMI rows purged from
data/economic_calendar_ff.parquet on 2026-07-30 (commit 8223035, "purge
cross-country PMI contamination (GBP/CAD, source-confirmed)").

Two things are checked: the migration's OWN filter logic never lets a PMI
canonical_id through even if the source archive contains it (synthetic,
isolated), and the REAL current parquet's PMI counts still match the known-
good post-purge baseline (integration, catches ANY future drift — not just
from re-running this one migration).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "2026-07-30_backfill_median_household.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("backfill_median_household", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def migration():
    return _load_migration_module()


def _row(cid, ccy, name_canonical, dt, actual=1.0, forecast=1.0):
    return {
        "canonical_id": cid, "currency": ccy, "name_raw": name_canonical,
        "name_canonical": name_canonical, "datetime_utc": pd.Timestamp(dt),
        "actual": actual, "forecast": forecast, "previous": actual,
        "released": True, "source": "ff",
    }


def test_pmi_guard_constant_matches_real_parquet(migration):
    """Integration check: the real, current parquet's PMI counts must still
    equal the known post-purge baseline. Fails loudly if anything (this
    migration re-run, a future refresh, a bad merge) ever changes them."""
    df = pd.read_parquet(ROOT / "data" / "economic_calendar_ff.parquet")
    counts = migration._pmi_counts(df)
    assert counts == migration.PMI_GUARD


def test_target_series_filter_never_admits_a_pmi_row(migration):
    """Simulate an archive that (like the REAL data/archive/ff_calendar_range.json)
    contains both the two legitimate target series AND full-strength PMI
    contamination under the exact purged canonical_ids. Applying the
    migration's own filter (currency, name_canonical) equality, exactly as
    its `main()` does, must select ONLY the two target series — never a PMI
    row — regardless of how much contamination the source holds."""
    contaminated_archive = pd.DataFrame([
        _row("cad_median_cpi", "CAD", "Median CPI y/y", "2026-06-22"),
        _row("aud_household_spending", "AUD", "Household Spending m/m", "2026-06-25"),
        # contamination present in the source, under the EXACT purged ids:
        _row("cad_s_p_global_manufacturing_pmi", "CAD", "S&P Global Manufacturing PMI", "2026-06-01"),
        _row("gbp_s_p_global_cips_manufacturing_pmi", "GBP", "S&P Global/CIPS Manufacturing PMI", "2026-06-01"),
        _row("gbp_s_p_global_cips_services_pmi", "GBP", "S&P Global/CIPS Services PMI", "2026-06-01"),
    ])

    filtered_parts = [
        contaminated_archive[(contaminated_archive["currency"] == ccy)
                             & (contaminated_archive["name_canonical"] == name)]
        for ccy, name in migration.TARGET_SERIES
    ]
    incoming = pd.concat(filtered_parts, ignore_index=True)

    assert set(incoming["canonical_id"]) == {"cad_median_cpi", "aud_household_spending"}
    assert not (set(incoming["canonical_id"]) & set(migration.PMI_GUARD))
    assert len(incoming) == 2


def test_merge_weekly_additive_backfill_leaves_pmi_rows_untouched(migration):
    """End-to-end (synthetic, fast): existing parquet already has PMI rows +
    the two target series absent; after merge_weekly with the strictly
    filtered incoming frame, PMI counts are bit-identical and only the two
    new canonical_ids appear."""
    existing = pd.DataFrame([
        _row("cad_s_p_global_manufacturing_pmi", "CAD", "S&P Global Manufacturing PMI", "2023-01-03"),
        _row("cad_s_p_global_manufacturing_pmi", "CAD", "S&P Global Manufacturing PMI", "2023-02-01"),
        _row("gbp_s_p_global_cips_manufacturing_pmi", "GBP", "S&P Global/CIPS Manufacturing PMI", "2023-01-03"),
    ])
    incoming = pd.DataFrame([
        _row("cad_median_cpi", "CAD", "Median CPI y/y", "2026-06-22"),
        _row("aud_household_spending", "AUD", "Household Spending m/m", "2026-06-25"),
    ])

    before = migration._pmi_counts(existing) if all(
        c in existing["canonical_id"].values for c in migration.PMI_GUARD
    ) else {cid: int((existing["canonical_id"] == cid).sum()) for cid in migration.PMI_GUARD}

    combined = migration.merge_weekly(existing, incoming)
    after = {cid: int((combined["canonical_id"] == cid).sum()) for cid in migration.PMI_GUARD}

    assert after == before   # PMI rows untouched by the additive merge
    assert int((combined["canonical_id"] == "cad_median_cpi").sum()) == 1
    assert int((combined["canonical_id"] == "aud_household_spending").sum()) == 1
    assert len(combined) == len(existing) + len(incoming)
