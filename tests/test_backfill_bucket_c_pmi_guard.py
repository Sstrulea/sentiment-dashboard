"""Regression: the 2026-08-01 bucket-C backfill
(migrations/2026-08-01_backfill_bucket_c_candidates.py) must never
reintroduce the 219 cross-country PMI rows purged 2026-07-30, or any other
PMI-guarded canonical_id, regardless of what data/archive/ff_calendar_range.json
or data/jb_raw/ contain.

Mirrors tests/test_backfill_pmi_guard.py's pattern exactly for this
migration.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "migrations" / "2026-08-01_backfill_bucket_c_candidates.py"


def _load_migration_module():
    spec = importlib.util.spec_from_file_location("backfill_bucket_c", MIGRATION_PATH)
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
    """Simulate combined sources that (like the REAL archive + jb_raw)
    contain both a legitimate target series AND full-strength PMI
    contamination under the exact purged canonical_ids. Applying the
    migration's own filter (currency, name_canonical) equality, exactly as
    its `main()` does, must select ONLY target series — never a PMI row —
    regardless of how much contamination the sources hold."""
    contaminated = pd.DataFrame([
        _row("jpy_tokyo_core_cpi", "JPY", "Tokyo Core CPI y/y", "2026-06-25"),
        _row("usd_import_prices", "USD", "Import Prices m/m", "2026-06-16"),
        # contamination present in the source, under the EXACT purged ids:
        _row("cad_s_p_global_manufacturing_pmi", "CAD", "S&P Global Manufacturing PMI", "2026-06-01"),
        _row("gbp_s_p_global_cips_manufacturing_pmi", "GBP", "S&P Global/CIPS Manufacturing PMI", "2026-06-01"),
        _row("gbp_s_p_global_cips_services_pmi", "GBP", "S&P Global/CIPS Services PMI", "2026-06-01"),
        _row("usd_ism_manufacturing_pmi", "USD", "ISM Manufacturing PMI", "2026-06-01"),
    ])

    filtered_parts = [
        contaminated[(contaminated["currency"] == ccy) & (contaminated["name_canonical"] == name)]
        for ccy, name in migration.TARGET_SERIES
        if not contaminated[(contaminated["currency"] == ccy) & (contaminated["name_canonical"] == name)].empty
    ]
    incoming = pd.concat(filtered_parts, ignore_index=True)

    assert set(incoming["canonical_id"]) == {"jpy_tokyo_core_cpi", "usd_import_prices"}
    assert not (set(incoming["canonical_id"]) & set(migration.PMI_GUARD))
    assert len(incoming) == 2


def test_merge_weekly_additive_backfill_leaves_pmi_rows_untouched(migration):
    """End-to-end (synthetic, fast): existing parquet already has PMI rows +
    two target series absent; after merge_weekly with the strictly filtered
    incoming frame, PMI counts are bit-identical and only the new
    canonical_ids appear."""
    existing = pd.DataFrame([
        _row("cad_s_p_global_manufacturing_pmi", "CAD", "S&P Global Manufacturing PMI", "2023-01-03"),
        _row("cad_s_p_global_manufacturing_pmi", "CAD", "S&P Global Manufacturing PMI", "2023-02-01"),
        _row("gbp_s_p_global_cips_manufacturing_pmi", "GBP", "S&P Global/CIPS Manufacturing PMI", "2023-01-03"),
    ])
    incoming = pd.DataFrame([
        _row("jpy_tokyo_core_cpi", "JPY", "Tokyo Core CPI y/y", "2026-06-25"),
        _row("usd_import_prices", "USD", "Import Prices m/m", "2026-06-16"),
    ])

    before = {cid: int((existing["canonical_id"] == cid).sum()) for cid in migration.PMI_GUARD}
    combined = migration.merge_weekly(existing, incoming)
    after = {cid: int((combined["canonical_id"] == cid).sum()) for cid in migration.PMI_GUARD}

    assert after == before   # PMI rows untouched by the additive merge
    assert int((combined["canonical_id"] == "jpy_tokyo_core_cpi").sum()) == 1
    assert int((combined["canonical_id"] == "usd_import_prices").sum()) == 1
    assert len(combined) == len(existing) + len(incoming)


def test_all_17_target_canonical_ids_present_with_expected_counts(migration):
    """Integration check on the real parquet: every backfilled series has
    exactly its reviewed row count (EXPECTED_COUNTS) — catches silent data
    drift, not just a failed merge."""
    df = pd.read_parquet(ROOT / "data" / "economic_calendar_ff.parquet")
    for ccy, name in migration.TARGET_SERIES:
        cid = migration.canonical_id(ccy, name)
        got = int((df["canonical_id"] == cid).sum())
        want = migration.EXPECTED_COUNTS[cid]
        assert got == want, f"{cid}: {got} rows, expected {want}"
