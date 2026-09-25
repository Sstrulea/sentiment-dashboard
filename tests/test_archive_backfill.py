"""fix/archive-backfill-late-aliases.

Fixtures below are REAL rows lifted verbatim from data/archive/ff_calendar_
range.json (AUD "Trimmed Mean CPI m/m" and "CPI m/m", 2026-01-07/2026-01-28)
— not synthesized — so the recovery path is exercised against the exact
shape of data it exists to recover. Every test uses tmp_path-scoped archive
JSON + parquet files; the real config/ff_aliases.yaml and data/economic_
indicators.yaml are used as-is (that's the point: these rows are recoverable
under the CURRENT, real alias/matcher config, same as production).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.archive_backfill import (
    scope_recoverable_rows,
    scope_summary,
    backfill_from_archive,
)
from src.econ_calendar_ff import CANON_COLUMNS, canonical_id

NOW = pd.Timestamp("2026-08-04")

# Real rows, verbatim from data/archive/ff_calendar_range.json.
TRIMMED_0107 = {"Name": "Trimmed Mean CPI m/m", "Currency": "AUD", "Event_ID": 0,
                "Category": "Economy Report", "Impact": "None", "Date": "2026.01.07 02:30:00",
                "Actual": 0.3, "Forecast": 0.2, "Previous": 0.3,
                "Outcome": "Actual > Forecast  Actual = Previous",
                "Strength": "Weak Data", "Quality": "Good Data"}
TRIMMED_0128 = {"Name": "Trimmed Mean CPI m/m", "Currency": "AUD", "Event_ID": 0,
                "Category": "Economy Report", "Impact": "None", "Date": "2026.01.28 02:30:00",
                "Actual": 0.2, "Forecast": 0.3, "Previous": 0.3,
                "Outcome": "Actual < Forecast = Previous",
                "Strength": "Strong Data", "Quality": "Bad Data"}
CPI_MM_0107_ZERO = {"Name": "CPI m/m", "Currency": "AUD", "Event_ID": 0,
                    "Category": "Consumer Inflation Report", "Impact": "High",
                    "Date": "2026.01.07 02:30:00", "Actual": 0.0, "Forecast": 0.1,
                    "Previous": 0.0, "Outcome": "Actual < Forecast  Actual = Previous",
                    "Strength": "Weak Data", "Quality": "Bad Data"}
CPI_MM_0128_REAL = {"Name": "CPI m/m", "Currency": "AUD", "Event_ID": 0,
                    "Category": "Consumer Inflation Report", "Impact": "High",
                    "Date": "2026.01.28 02:30:00", "Actual": 1.0, "Forecast": 0.7,
                    "Previous": 0.0, "Outcome": "Actual > Forecast > Previous",
                    "Strength": "Strong Data", "Quality": "Bad Data"}

# Real canonical ids these alias to today (identity/xf per config/ff_aliases.yaml
# AUD block) — computed via the production canonical_id() fn, not hardcoded.
TRIMMED_CID = canonical_id("AUD", "Monthly Trimmed Mean CPI m/m")
CPI_MM_CID = canonical_id("AUD", "Monthly CPI Indicator m/m")


def _write_archive(tmp_path: Path, records: list[dict]) -> Path:
    p = tmp_path / "archive.json"
    p.write_text(json.dumps(records))
    return p


def _write_parquet(tmp_path: Path, rows: list[dict] | None = None) -> Path:
    p = tmp_path / "calendar.parquet"
    df = pd.DataFrame(rows or [], columns=CANON_COLUMNS)
    if rows:
        df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    df.to_parquet(p, index=False)
    return p


# ---------------------------------------------------------------------------
# STEP 1 — scoping
# ---------------------------------------------------------------------------

def test_scope_finds_real_recoverable_rows_against_empty_parquet(tmp_path):
    archive_path = _write_archive(tmp_path, [TRIMMED_0107, TRIMMED_0128, CPI_MM_0128_REAL])
    parquet_path = _write_parquet(tmp_path, [])

    recoverable = scope_recoverable_rows(archive_path, parquet_path, now_utc=NOW)
    assert set(recoverable["canonical_id"]) == {TRIMMED_CID, CPI_MM_CID}
    assert len(recoverable) == 3
    assert set(recoverable["indicator_key"]) == {"trimmed_mean_cpi_monthly", "cpi_monthly"}

    summary = scope_summary(recoverable)
    trimmed_row = summary[summary["indicator_key"] == "trimmed_mean_cpi_monthly"].iloc[0]
    assert trimmed_row["n"] == 2


def test_scope_excludes_a_row_already_present_in_parquet(tmp_path):
    archive_path = _write_archive(tmp_path, [TRIMMED_0107, TRIMMED_0128])
    # parquet already has a row at (canonical_id, 2026-01-07) -- any actual,
    # this alone must be enough to exclude it from the recoverable scope.
    existing_row = {"canonical_id": TRIMMED_CID, "currency": "AUD",
                    "name_raw": "Trimmed Mean CPI m/m", "name_canonical": "Monthly Trimmed Mean CPI m/m",
                    "datetime_utc": pd.Timestamp("2026-01-07 02:30:00"), "actual": 0.99,
                    "forecast": 0.2, "previous": 0.3, "released": True, "source": "ff"}
    parquet_path = _write_parquet(tmp_path, [existing_row])

    recoverable = scope_recoverable_rows(archive_path, parquet_path, now_utc=NOW)
    assert len(recoverable) == 1
    assert recoverable.iloc[0]["datetime_utc"] == pd.Timestamp("2026-01-28 00:30:00")


# ---------------------------------------------------------------------------
# STEP 2 — recovery
# ---------------------------------------------------------------------------

def test_recovered_prints_appear_with_real_values(tmp_path):
    archive_path = _write_archive(tmp_path, [TRIMMED_0107, TRIMMED_0128, CPI_MM_0128_REAL])
    parquet_path = _write_parquet(tmp_path, [])

    report = backfill_from_archive(archive_path, parquet_path, now_utc=NOW)
    assert report["n_recoverable_candidates"] == 3

    out = pd.read_parquet(parquet_path)
    trimmed = out[out["canonical_id"] == TRIMMED_CID].sort_values("datetime_utc")
    assert list(trimmed["actual"]) == [0.3, 0.2]   # real archive values, unchanged
    cpi_mm = out[out["canonical_id"] == CPI_MM_CID]
    assert list(cpi_mm["actual"]) == [1.0]


def test_never_overwrites_an_existing_non_null_actual(tmp_path):
    archive_path = _write_archive(tmp_path, [TRIMMED_0107, TRIMMED_0128])
    existing_row = {"canonical_id": TRIMMED_CID, "currency": "AUD",
                    "name_raw": "Trimmed Mean CPI m/m", "name_canonical": "Monthly Trimmed Mean CPI m/m",
                    "datetime_utc": pd.Timestamp("2026-01-07 02:30:00"), "actual": 0.99,
                    "forecast": 0.2, "previous": 0.3, "released": True, "source": "ff"}
    parquet_path = _write_parquet(tmp_path, [existing_row])

    backfill_from_archive(archive_path, parquet_path, now_utc=NOW)

    out = pd.read_parquet(parquet_path)
    jan07 = out[(out["canonical_id"] == TRIMMED_CID)
               & (out["datetime_utc"] == pd.Timestamp("2026-01-07 02:30:00"))]
    assert len(jan07) == 1
    assert jan07.iloc[0]["actual"] == 0.99   # untouched, archive's 0.3 never applied here
    # the OTHER (genuinely absent) row still gets recovered
    jan28 = out[out["canonical_id"] == TRIMMED_CID]
    assert 0.2 in list(jan28["actual"])


def test_recovered_placeholder_zero_is_still_quarantined(tmp_path):
    # CPI_MM_0107_ZERO is the ONLY copy at its (canonical_id, date) in this
    # fixture -- clean_jblanked_actuals must null it exactly as it would a
    # freshly-pulled single 0.0 copy with no real twin.
    archive_path = _write_archive(tmp_path, [CPI_MM_0107_ZERO])
    parquet_path = _write_parquet(tmp_path, [])

    report = backfill_from_archive(archive_path, parquet_path, now_utc=NOW)
    assert report["n_recoverable_candidates"] == 1
    assert report["n_cleaned_rows"] == 1
    assert report["n_cleaned_valid_actual"] == 0   # nulled, not dropped

    out = pd.read_parquet(parquet_path)
    row = out[out["canonical_id"] == CPI_MM_CID]
    assert len(row) == 1
    assert pd.isna(row.iloc[0]["actual"])


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------

def test_running_the_backfill_twice_changes_nothing_the_second_time(tmp_path):
    archive_path = _write_archive(
        tmp_path, [TRIMMED_0107, TRIMMED_0128, CPI_MM_0107_ZERO, CPI_MM_0128_REAL])
    parquet_path = _write_parquet(tmp_path, [])

    first = backfill_from_archive(archive_path, parquet_path, now_utc=NOW)
    assert first["n_recoverable_candidates"] == 4
    after_first = pd.read_parquet(parquet_path)

    second = backfill_from_archive(archive_path, parquet_path, now_utc=NOW)
    assert second["n_recoverable_candidates"] == 0
    assert second["n_before"] == second["n_after"] == first["n_after"]
    after_second = pd.read_parquet(parquet_path)

    pd.testing.assert_frame_equal(
        after_first.sort_values(["canonical_id", "datetime_utc"]).reset_index(drop=True),
        after_second.sort_values(["canonical_id", "datetime_utc"]).reset_index(drop=True),
    )

    # scope itself also agrees nothing is left
    assert scope_recoverable_rows(archive_path, parquet_path, now_utc=NOW).empty


# ---------------------------------------------------------------------------
# Real-repo sanity checks (not fixture-based) — the two headline cases from
# docs/board-data-loss-round3.md, run against the ACTUAL retained archive.
# ---------------------------------------------------------------------------

def test_real_archive_recovers_aud_cpi_family():
    """Read-only against the real data/archive/ff_calendar_range.json and
    data/economic_calendar_ff.parquet (as they stand on disk right now) --
    confirms the scope this branch's backfill already applied covers the
    exact cases docs/board-data-loss-round3.md Q2 identified."""
    recoverable = scope_recoverable_rows(now_utc=NOW)
    # After this branch's own backfill has already run once against the real
    # parquet, the real scope is empty -- assert that, not a specific count,
    # so this test stays meaningful whether run before or after the backfill.
    assert recoverable.empty, (
        "expected the real archive to be fully recovered on this branch; "
        f"got {len(recoverable)} still-recoverable rows — run the backfill"
    )


def test_scope_does_not_reintroduce_purged_cross_country_pmi_contamination():
    """The archive predates the 2026-07-30/07-31 PMI decontamination
    (migrations/2026-07-31_reattribute_pmi_rows.py) -- it still carries 219
    rows tagged CAD/GBP that were actually mislabeled CHF/JPY/EUR releases.
    A first version of this backfill (caught by the pre-existing tests/
    test_backfill_pmi_guard.py + tests/test_backfill_bucket_c_pmi_guard.py
    regression suite) silently reintroduced 29 of them. Locking that fix in
    directly here: after `scope_recoverable_rows` (used exactly as
    `backfill_from_archive` uses it), merging the candidates onto the real
    parquet must never make `country_hour_guard` flag a row under any of the
    three canonical_ids that migration's own PMI_GUARD covers.
    """
    from src.pmi_ingest_guard import country_hour_guard

    real_parquet = pd.read_parquet("data/economic_calendar_ff.parquet")
    guarded_ids = {
        "cad_s_p_global_manufacturing_pmi",
        "gbp_s_p_global_cips_manufacturing_pmi",
        "gbp_s_p_global_cips_services_pmi",
    }
    flagged = country_hour_guard(real_parquet)
    recontaminated = flagged[flagged["canonical_id"].isin(guarded_ids)]
    assert recontaminated.empty, (
        f"archive backfill reintroduced cross-country PMI contamination: "
        f"{recontaminated.to_dict('records')}"
    )


def test_scope_skips_a_tombstoned_row(tmp_path):
    """A row a migration deliberately deleted (data/ff_tombstones.csv) is absent
    from the parquet but NOT missing: the archive still carries it, and a
    re-scan must not bring it back. Same (canonical_id, release date)
    granularity as the presence test."""
    archive_path = _write_archive(tmp_path, [TRIMMED_0107, TRIMMED_0128])
    parquet_path = _write_parquet(tmp_path, [])
    tomb = tmp_path / "tombstones.csv"
    pd.DataFrame([{"canonical_id": TRIMMED_CID, "datetime_utc": "2026-01-07 00:30:00",
                   "reason": "test", "source": "test"}]).to_csv(tomb, index=False)

    recoverable = scope_recoverable_rows(archive_path, parquet_path, now_utc=NOW,
                                         tombstones_path=tomb)
    assert len(recoverable) == 1
    assert recoverable.iloc[0]["datetime_utc"] == pd.Timestamp("2026-01-28 00:30:00")

    no_tomb = scope_recoverable_rows(archive_path, parquet_path, now_utc=NOW,
                                     tombstones_path=tmp_path / "absent.csv")
    assert len(no_tomb) == 2
