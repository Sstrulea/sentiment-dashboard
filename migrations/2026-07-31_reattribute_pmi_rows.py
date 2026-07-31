"""ONE-TIME DATA MIGRATION — run once, then keep for the audit trail.

FAZA 3 (docs/pmi-reattribution-before-after.md) — reattributes 139 of the
219 rows purged on 2026-07-30 (commit 8223035, cross-country PMI
contamination) to their real country, confirmed by local-hour matching
against EUR/JPY/CHF's own correctly-labeled 2026 copies:

  cad_s_p_global_manufacturing_pmi @ 07:30/08:30 UTC -> CHF manufacturing_pmi (37 rows)
  gbp_s_p_global_cips_manufacturing_pmi @ 00:30 UTC  -> JPY manufacturing_pmi (32 rows)
  gbp_s_p_global_cips_manufacturing_pmi @ 08:00/09:00 UTC -> EUR manufacturing_pmi (35 rows)
  gbp_s_p_global_cips_services_pmi @ 08:00/09:00 UTC -> EUR services_pmi (35 rows)

FAZA 2's Jan/Feb-2023-duplicate filter was applied to this 219-row subset
FIRST (per the task's dependency) — 4 rows excluded as year-shift
duplicates, not genuine history (see docs/pmi-reattribution-before-after.md).

76 rows (a distinct 09:45 America/New_York cluster, confirmed via zoneinfo
conversion — matches the known US S&P Global Final PMI release time) are
NOT reattributed here: no USD S&P Global PMI canonical exists anywhere in
data/economic_calendar_ff.parquet or data/archive/ff_calendar_range.json to
validate against (unlike EUR/JPY/CHF's own 2026 copies), and no
`indicator_key` exists in the taxonomy for it (only ISM Manufacturing/
Non-Manufacturing PMI are mapped for United States) — reattribution would
require a taxonomy decision (a new indicator_key + matcher rule), not a
data operation. These 76 rows stay purged. Reopening this is a separate,
future decision — not resolved here.

A PMI guard (43/43/44, the three REAL post-2026-07-30-purge canonical_ids)
is asserted before and after — this migration must never touch those row
counts; it only ADDS rows under the reattributed currencies' own
canonical_ids.

Usage: `.venv/bin/python3 migrations/2026-07-31_reattribute_pmi_rows.py`
Idempotent-safe: refuses to run if any reattributed row is already present.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
BACKUP_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet.pre-pmi-reattribution"
CANDIDATES_CSV = ROOT / "docs" / "pmi-reattribution-candidates.csv"

PMI_GUARD = {
    "cad_s_p_global_manufacturing_pmi": 43,
    "gbp_s_p_global_cips_manufacturing_pmi": 43,
    "gbp_s_p_global_cips_services_pmi": 44,
}

REATTRIBUTION_TARGET = {
    ("CHF", "manufacturing_pmi"): ("CHF", "procure.ch Manufacturing PMI"),
    ("JPY", "manufacturing_pmi"): ("JPY", "au Jibun Bank Manufacturing PMI"),
    ("EUR", "manufacturing_pmi"): ("EUR", "S&P Global Manufacturing PMI"),
    ("EUR", "services_pmi"): ("EUR", "S&P Global Services PMI"),
}
EXPECTED_COUNTS = {("CHF", "manufacturing_pmi"): 37, ("JPY", "manufacturing_pmi"): 32,
                  ("EUR", "manufacturing_pmi"): 35, ("EUR", "services_pmi"): 35}


def _pmi_counts(df: pd.DataFrame) -> dict[str, int]:
    return {cid: int((df["canonical_id"] == cid).sum()) for cid in PMI_GUARD}


def main() -> None:
    if not FF_PARQUET.exists():
        raise SystemExit(f"missing {FF_PARQUET}")
    if not CANDIDATES_CSV.exists():
        raise SystemExit(f"missing {CANDIDATES_CSV} — run scripts/measure/pmi_reattribution_before_after.py first")

    existing = pd.read_parquet(FF_PARQUET)
    candidates = pd.read_csv(CANDIDATES_CSV, parse_dates=["datetime_utc"])

    if len(candidates) != 139:
        raise SystemExit(f"expected exactly 139 candidate rows, found {len(candidates)} — aborting")

    counts = candidates.groupby(["reatt_ccy", "reatt_key"]).size().to_dict()
    if counts != EXPECTED_COUNTS:
        raise SystemExit(f"candidate breakdown mismatch: expected {EXPECTED_COUNTS}, found {counts} — aborting")

    before_pmi = _pmi_counts(existing)
    if before_pmi != PMI_GUARD:
        raise SystemExit(f"PMI guard failed BEFORE reattribution: expected {PMI_GUARD}, found {before_pmi}. Aborting.")

    # Build the relabeled rows and check none are already present (idempotency).
    added_parts = []
    for (ccy, key), grp in candidates.groupby(["reatt_ccy", "reatt_key"]):
        target_ccy, target_canonical = REATTRIBUTION_TARGET[(ccy, key)]
        relabeled = grp.copy()
        relabeled["currency"] = target_ccy
        relabeled["name_canonical"] = target_canonical
        relabeled["canonical_id"] = f"{target_ccy.lower()}_{target_canonical.lower().replace(' ', '_').replace('.', '').replace('&', '')}"
        added_parts.append(relabeled[["canonical_id", "currency", "name_raw", "name_canonical",
                                      "datetime_utc", "actual", "forecast", "previous", "released", "source"]])
    to_add = pd.concat(added_parts, ignore_index=True)

    already = existing.merge(to_add[["canonical_id", "datetime_utc"]], on=["canonical_id", "datetime_utc"], how="inner")
    if not already.empty:
        raise SystemExit(f"{len(already)} reattributed row(s) already present in the parquet — "
                         "this migration has already run. Refusing to run again. Aborting, no write made.")

    combined = pd.concat([existing, to_add], ignore_index=True)
    combined = combined.sort_values(["currency", "canonical_id", "datetime_utc"]).reset_index(drop=True)

    after_pmi = _pmi_counts(combined)
    if after_pmi != PMI_GUARD:
        raise SystemExit(
            f"PMI guard failed AFTER reattribution: expected {PMI_GUARD}, found {after_pmi}. "
            "This migration must never touch those three canonical_ids — aborting, NOT writing the parquet."
        )

    if len(combined) - len(existing) != 139:
        raise SystemExit(
            f"row delta mismatch: existing={len(existing)}, combined={len(combined)}, "
            f"expected delta=139 — aborting"
        )

    for (ccy, key), n_expected in EXPECTED_COUNTS.items():
        target_ccy, target_canonical = REATTRIBUTION_TARGET[(ccy, key)]
        cid = f"{target_ccy.lower()}_{target_canonical.lower().replace(' ', '_').replace('.', '').replace('&', '')}"
        n_actual = int((combined["canonical_id"] == cid).sum())
        if n_actual < n_expected:
            raise SystemExit(f"{cid}: expected at least {n_expected} rows after merge, found {n_actual} — aborting")

    # All guards passed — back up, then write.
    shutil.copy2(FF_PARQUET, BACKUP_PARQUET)
    combined.to_parquet(FF_PARQUET, index=False)

    print(f"OK — reattributed {len(to_add)} PMI rows (CHF mfg 37, JPY mfg 32, EUR mfg 35, EUR services 35). "
         f"Parquet: {len(existing)} -> {len(combined)} rows. "
         f"Backup written to {BACKUP_PARQUET}. PMI guard unchanged: {after_pmi}. "
         f"76 rows (US-hour cluster, no taxonomy slot) remain unreattributed, purged — see "
         f"docs/pmi-reattribution-before-after.md.")


if __name__ == "__main__":
    main()
