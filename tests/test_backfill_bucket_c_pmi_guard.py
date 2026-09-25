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


def test_purge_holds_no_foreign_hour_row_reappears(migration):
    """Integration check: the purge must never come undone — but the
    invariant that matters is NOT a frozen row count.

    An earlier version asserted `_pmi_counts(df) == PMI_GUARD` (an exact
    count). These series each gain a row every month, forever, by design,
    so that assertion breaks on every legitimate print exactly like it
    would on a real recontamination — it can't tell the two apart, and
    repeated routine failures train a reflex to bump the constant without
    checking why. See tests/test_backfill_pmi_guard.py for the identical
    reasoning (this file mirrors that one's migration-1 fix).

    What must hold, regardless of how many legitimate prints accumulate:
    none of the known-contaminated-then-purged canonical_ids ever shows a
    row at a foreign local hour again — reusing
    `src.pmi_ingest_guard.country_hour_guard`, the same check the ingest
    itself runs, tests the property instead of a snapshot."""
    from src.pmi_ingest_guard import country_hour_guard
    df = pd.read_parquet(ROOT / "data" / "economic_calendar_ff.parquet")
    flagged = country_hour_guard(df)
    recontaminated = flagged[flagged["canonical_id"].isin(migration.PMI_GUARD.keys())]
    assert recontaminated.empty, (
        f"a known-purged PMI series shows a foreign-local-hour row again "
        f"— investigate before assuming this is routine growth: "
        f"{recontaminated.to_dict('records')}"
    )


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
    """Integration check on the real parquet: every backfilled series has AT
    LEAST its reviewed row count (EXPECTED_COUNTS) — catches the failure
    this test exists for (a silent merge that drops or fails to backfill
    rows) without also failing on ordinary forward progress.

    EXPECTED_COUNTS was measured once, at backfill time. These are live,
    still-scored series (unlike the PMI purge, there is no "this must never
    grow again" story here) — a new print lands on schedule and the count
    legitimately grows past its recorded value, exactly like
    tests/test_backfill_pmi_guard.py's PMI-count assertion did. A floor
    (`got >= want`) still catches the real regression (rows missing or a
    failed backfill) while not breaking on the calendar simply moving
    forward. A row a later migration deliberately deleted is listed in
    data/ff_tombstones.csv (with its reason) and counts as accounted for:
    the floor is about SILENT loss, not documented removals."""
    df = pd.read_parquet(ROOT / "data" / "economic_calendar_ff.parquet")
    tomb_path = ROOT / "data" / "ff_tombstones.csv"
    tomb = pd.read_csv(tomb_path) if tomb_path.exists() else pd.DataFrame(columns=["canonical_id"])
    for ccy, name in migration.TARGET_SERIES:
        cid = migration.canonical_id(ccy, name)
        got = int((df["canonical_id"] == cid).sum())
        removed = int((tomb["canonical_id"] == cid).sum())
        want = migration.EXPECTED_COUNTS[cid]
        assert got + removed >= want, f"{cid}: {got} rows (+{removed} tombstoned), expected at least {want}"
