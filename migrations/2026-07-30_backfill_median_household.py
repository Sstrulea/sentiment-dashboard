"""ONE-TIME DATA MIGRATION — run once, then keep for the audit trail.

Backfills `data/economic_calendar_ff.parquet` with exactly two archive series,
strictly filtered by (currency, name_canonical) — nothing else is read from
`data/archive/ff_calendar_range.json` (read-only, never written):

  (CAD, "Median CPI y/y")        -> feeds the promoted `core_cpi` slot
                                     (matcher repointed in
                                     data/economic_indicators.yaml; see
                                     docs/proposal-cad-core-promotion.md)
  (AUD, "Household Spending m/m") -> feeds the continued `retail_sales` slot
                                     (matcher repointed in
                                     data/economic_indicators.yaml; see
                                     docs/proposal-aud-retail-continuation.md)

Why so strict: `data/archive/ff_calendar_range.json` still contains, in
full, the 219 cross-country PMI rows purged from the live parquet on
2026-07-30 (commit 8223035, "purge cross-country PMI contamination
(GBP/CAD, source-confirmed)") — `gbp_s_p_global_cips_manufacturing_pmi`
(107), `gbp_s_p_global_cips_services_pmi` (73), `cad_s_p_global_
manufacturing_pmi` (39). A broad re-merge of the archive would silently
undo that purge. This script never reads those canonical_ids at all, and
additionally asserts their row counts are bit-identical before and after
(the "PMI guard").

canonical_id collision check (done once, recorded here, not re-derived by
this script): canonical_id('CAD','Median CPI y/y') = 'cad_median_cpi' !=
canonical_id('CAD','Core CPI y/y') = 'cad_core_cpi' — no collision with the
existing (now-orphaned, matcher no longer routes it anywhere) wrong-unit
CAD Core CPI y/y rows. canonical_id('AUD','Household Spending m/m') =
'aud_household_spending', a fresh id with zero existing rows. Both filtered
series have ZERO existing rows under their own canonical_id in the live
parquet (verified below) — this is a pure additive merge, never an
overwrite of existing history.

Uses the PRODUCTION merge function (`src.ff_refresh.merge_weekly`)
unmodified, so the result is byte-identical in shape/dedup/sort convention
to what the normal hourly ingest would have produced, had these events
always been mapped.

Usage: `.venv/bin/python3 migrations/2026-07-30_backfill_median_household.py`
Idempotent-safe: refuses to run twice (checks post-conditions first; if
already backfilled, exits without writing).
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.econ_calendar_ff import parse_jblanked_range, canonical_id  # noqa: E402
from src.ff_refresh import merge_weekly  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
BACKUP_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet.pre-backfill"
ARCHIVE_JSON = ROOT / "data" / "archive" / "ff_calendar_range.json"

# The exact, narrow filter — nothing else is ever taken from the archive.
TARGET_SERIES = [
    ("CAD", "Median CPI y/y"),
    ("AUD", "Household Spending m/m"),
]

# PMI guard: these three canonical_ids must be bit-identical before and
# after this script runs. Values captured 2026-07-30 against the live
# parquet, post-purge (commit 8223035).
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
    existing = pd.read_parquet(FF_PARQUET)

    before_pmi = _pmi_counts(existing)
    if before_pmi != PMI_GUARD:
        raise SystemExit(
            f"PMI guard failed BEFORE backfill: expected {PMI_GUARD}, found {before_pmi}. "
            "Stopping — do not proceed on an unexpected baseline."
        )

    already = existing[existing["canonical_id"].isin(["cad_median_cpi", "aud_household_spending"])]
    if not already.empty:
        raise SystemExit(
            f"{len(already)} row(s) already present under the target canonical_ids — "
            "this migration has already run (or the parquet already has this data). "
            "Refusing to run again to avoid double-inserting. Aborting, no write made."
        )

    arch = parse_jblanked_range(str(ARCHIVE_JSON))
    filtered_parts = []
    for ccy, name in TARGET_SERIES:
        sub = arch[(arch["currency"] == ccy) & (arch["name_canonical"] == name)].copy()
        if sub.empty:
            raise SystemExit(f"archive has 0 rows for ({ccy}, {name!r}) — aborting, nothing to backfill")
        cid = canonical_id(ccy, name)
        bad_cid = sub[sub["canonical_id"] != cid]
        if not bad_cid.empty:
            raise SystemExit(f"unexpected canonical_id mismatch for ({ccy}, {name!r}): {bad_cid['canonical_id'].unique()}")
        filtered_parts.append(sub)
    incoming = pd.concat(filtered_parts, ignore_index=True)

    # Hard filter check: the incoming frame must contain ONLY the two target
    # canonical_ids — never anything else, regardless of what the archive holds.
    allowed_cids = {canonical_id(ccy, name) for ccy, name in TARGET_SERIES}
    stray = set(incoming["canonical_id"].unique()) - allowed_cids
    if stray:
        raise SystemExit(f"incoming frame has unexpected canonical_id(s): {stray} — aborting")

    combined = merge_weekly(existing, incoming)

    after_pmi = _pmi_counts(combined)
    if after_pmi != PMI_GUARD:
        raise SystemExit(
            f"PMI guard failed AFTER backfill: expected {PMI_GUARD}, found {after_pmi}. "
            "The merge altered rows it should never have touched — aborting, NOT writing the parquet."
        )

    n_cad = int((combined["canonical_id"] == "cad_median_cpi").sum())
    n_aud = int((combined["canonical_id"] == "aud_household_spending").sum())
    if n_cad != 43 or n_aud != 9:
        raise SystemExit(f"unexpected backfilled row counts: cad_median_cpi={n_cad} (want 43), "
                         f"aud_household_spending={n_aud} (want 9) — aborting, NOT writing")

    cad_cons_valid = int(combined.loc[combined["canonical_id"] == "cad_median_cpi", "forecast"].notna().sum())
    aud_cons_valid = int(combined.loc[combined["canonical_id"] == "aud_household_spending", "forecast"].notna().sum())
    if cad_cons_valid != 43 or aud_cons_valid != 9:
        raise SystemExit(f"unexpected consensus validity: cad={cad_cons_valid}/43, aud={aud_cons_valid}/9 — aborting")

    if len(combined) != len(existing) + 43 + 9:
        raise SystemExit(
            f"row count delta is not purely additive: existing={len(existing)}, "
            f"combined={len(combined)}, expected={len(existing) + 52} — aborting"
        )

    # All guards passed — back up, then write.
    shutil.copy2(FF_PARQUET, BACKUP_PARQUET)
    combined.to_parquet(FF_PARQUET, index=False)

    print(f"OK — backfilled {len(incoming)} rows (43 CAD Median CPI y/y + 9 AUD Household "
         f"Spending m/m). Parquet: {len(existing)} -> {len(combined)} rows. "
         f"Backup written to {BACKUP_PARQUET}. PMI guard unchanged: {after_pmi}.")


if __name__ == "__main__":
    main()
