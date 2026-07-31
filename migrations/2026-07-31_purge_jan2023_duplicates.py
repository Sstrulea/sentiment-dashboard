"""ONE-TIME DATA MIGRATION — run once, then keep for the audit trail.

FAZA 2 (docs/jan2023-duplicate-purge.md) — purges the 64 rows in
data/economic_calendar_ff.parquet that are exact duplicates of a January/
February 2024 row shifted back one year (a backfill-window-edge artifact:
the whole month of January 2024 was copied into January 2023 with the wrong
year during the original archive ingest).

Strict criterion (no partial matches): same currency, same name_raw, same
actual, same forecast, same day-of-month, exactly one year later (Jan/Feb
2023 -> Jan/Feb 2024). Candidate rows are identified by exact
(canonical_id, datetime_utc) key — see docs/jan2023-purge-candidates-live-
parquet.csv for the full list, produced by
scripts/measure/jan2023_purge_before_after.py.

Explicitly EXCLUDES the 3 canonical_ids entangled with the separate 219-row
PMI cross-country contamination (gbp_s_p_global_cips_manufacturing_pmi,
gbp_s_p_global_cips_services_pmi, cad_s_p_global_manufacturing_pmi) — those
are FAZA 3's responsibility (local-hour reattribution, not this strict
year-shift criterion). A PMI guard (43/43/44, unchanged from the 2026-07-30
purge, commit 8223035) is asserted before and after — this migration must
never touch those three canonical_ids' row counts.

Measured effect (docs/jan2023-duplicate-purge.md): 0 bias flips, 0
instrument score changes, cell scores bit-identical across all 43 scored
(currency, indicator) pairs touched, with one marginal sigma-only exception
(AUD core_cpi, quarterly cadence) that still doesn't cross a bucket
threshold. Purging anyway per explicit instruction: src/calibration_analysis.py
and src/crossasset_calibration.py read full history (not the trailing-12
window), so these 64 false rows would contaminate any future threshold
recalibration even though they're inert for today's live scores.

Usage: `.venv/bin/python3 migrations/2026-07-31_purge_jan2023_duplicates.py`
Idempotent-safe: refuses to run if none of the candidate rows are present
(already purged).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
BACKUP_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet.pre-jan2023-purge"
CANDIDATES_CSV = ROOT / "docs" / "jan2023-purge-candidates-live-parquet.csv"

PMI_CONTAMINATED_IDS = {
    "gbp_s_p_global_cips_manufacturing_pmi",
    "gbp_s_p_global_cips_services_pmi",
    "cad_s_p_global_manufacturing_pmi",
}
PMI_GUARD = {
    "cad_s_p_global_manufacturing_pmi": 43,
    "gbp_s_p_global_cips_manufacturing_pmi": 43,
    "gbp_s_p_global_cips_services_pmi": 44,
}


def _pmi_counts(df: pd.DataFrame) -> dict[str, int]:
    return {cid: int((df["canonical_id"] == cid).sum()) for cid in PMI_GUARD}


def main() -> None:
    if not FF_PARQUET.exists():
        raise SystemExit(f"missing {FF_PARQUET}")
    if not CANDIDATES_CSV.exists():
        raise SystemExit(f"missing {CANDIDATES_CSV} — run scripts/measure/jan2023_purge_before_after.py first")

    existing = pd.read_parquet(FF_PARQUET)
    candidates = pd.read_csv(CANDIDATES_CSV, parse_dates=["datetime_utc"])

    stray = set(candidates["canonical_id"]) & PMI_CONTAMINATED_IDS
    if stray:
        raise SystemExit(f"candidate list contains PMI-contaminated canonical_id(s): {stray} — "
                         "these belong to FAZA 3, not this migration. Aborting.")

    before_pmi = _pmi_counts(existing)
    if before_pmi != PMI_GUARD:
        raise SystemExit(f"PMI guard failed BEFORE purge: expected {PMI_GUARD}, found {before_pmi}. Aborting.")

    drop_keys = set(zip(candidates["canonical_id"], candidates["datetime_utc"]))
    present_mask = existing.apply(lambda r: (r["canonical_id"], r["datetime_utc"]) in drop_keys, axis=1)
    n_present = int(present_mask.sum())
    if n_present != len(candidates):
        raise SystemExit(
            f"expected all {len(candidates)} candidate rows present in the live parquet, "
            f"found {n_present} — aborting (candidate list may be stale, re-run the measurement script)."
        )

    after = existing[~present_mask].reset_index(drop=True)

    after_pmi = _pmi_counts(after)
    if after_pmi != PMI_GUARD:
        raise SystemExit(
            f"PMI guard failed AFTER purge: expected {PMI_GUARD}, found {after_pmi}. "
            "The purge touched rows it should never have touched — aborting, NOT writing the parquet."
        )

    if len(existing) - len(after) != len(candidates):
        raise SystemExit(
            f"row delta mismatch: existing={len(existing)}, after={len(after)}, "
            f"expected delta={len(candidates)} — aborting"
        )

    # All guards passed — back up, then write.
    shutil.copy2(FF_PARQUET, BACKUP_PARQUET)
    after.to_parquet(FF_PARQUET, index=False)

    print(f"OK — purged {len(candidates)} January-2023 duplicate rows. "
         f"Parquet: {len(existing)} -> {len(after)} rows. "
         f"Backup written to {BACKUP_PARQUET}. PMI guard unchanged: {after_pmi}.")


if __name__ == "__main__":
    main()
