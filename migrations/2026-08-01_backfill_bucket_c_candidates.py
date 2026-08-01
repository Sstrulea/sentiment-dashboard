"""ONE-TIME DATA MIGRATION — run once, then keep for the audit trail.

Backfills `data/economic_calendar_ff.parquet` with the 17 bucket-C candidates
approved 2026-08-01 (docs/bucket-c-merit-evaluation.md FAZA 1,
docs/bucket-c-impact-measurement.md FAZA 2, docs/bucket-c-panel-measurement.md
frequency panel) — strictly filtered by (currency, name_canonical), nothing
else is admitted regardless of what the sources contain:

    (JPY, "Tokyo Core CPI y/y")               -> tokyo_core_cpi_yoy
    (GBP, "Industrial Production m/m")         -> industrial_production_mm
    (USD, "Industrial Production m/m")         -> industrial_production_mm
    (USD, "Durable Goods Orders m/m")          -> durable_goods_orders_mm
    (JPY, "Core Machinery Orders m/m")         -> core_machinery_orders_mm
    (USD, "Personal Spending m/m")             -> personal_spending_mm
    (USD, "Personal Income m/m")               -> personal_income_mm
    (USD, "Import Prices m/m")                 -> import_prices
    (AUD, "Private Capital Expenditure q/q")   -> capital_expenditure
    (JPY, "Capital Spending q/y")              -> capital_expenditure
    (AUD, "Company Operating Profits q/q")     -> company_operating_profits_qoq
    (AUD, "Import Prices q/q")                 -> import_prices
    (JPY, "SPPI y/y")                          -> sppi_yoy
    (JPY, "Prelim GDP Price Index y/y")        -> gdp_price_index
    (USD, "Advance GDP Price Index q/q")       -> gdp_price_index
    (USD, "Prelim Unit Labor Costs q/q")       -> unit_labor_costs_qoq
    (JPY, "Prelim Industrial Production m/m")  -> industrial_production_mm

(indicator_key routing lives in data/economic_indicators.yaml's matcher,
added alongside config/ff_aliases.yaml's identity aliases in the same
commit as this migration — this script only touches the parquet.)

TWO sources, combined: `data/archive/ff_calendar_range.json` (read-only,
full history, but frozen at 2026-07-03 — see docs/bucket-c-panel-measurement.md)
PLUS every `data/jb_raw/jb_range_*.json` snapshot (JBlanked's rolling recent-
actuals pull, same schema, read-only). GBP/USD Industrial Production m/m
were flagged as "permanently stale" using the archive alone; confirmed
directly (see docs/bucket-c-panel-measurement.md) that both series are
still live in the current feed, with real prints in data/jb_raw/ as recent
as 2026-07-16/17 — well past the archive's 2026-06-12/15 cutoff. Combining
both sources means the backfilled series start fresh today, not stale on
day one. Every OTHER of the 17 candidates also gets whatever jb_raw
coverage exists for it (checked, some do) — not just the 2 named.

Why so strict: both sources still contain, in full, the 219 cross-country
PMI rows purged from the live parquet on 2026-07-30 (commit 8223035) and
any other already-purged/reattributed contamination. A broad re-merge of
either source would silently undo that work. This script never reads
those canonical_ids at all, and additionally asserts their row counts are
bit-identical before and after (the "PMI guard").

canonical_id collision check (done once, recorded here, not re-derived by
this script): all 17 canonical_ids computed below are verified to have
ZERO existing rows in the live parquet before this migration runs (checked
in main(), not assumed) — this is a pure additive merge, never an
overwrite of existing history.

Uses the PRODUCTION merge function (`src.ff_refresh.merge_weekly`) and the
PRODUCTION parser (`src.econ_calendar_ff.parse_jblanked_range`) unmodified,
so the result is byte-identical in shape/dedup/sort convention to what the
normal hourly ingest would have produced, had these events always been
mapped (config/ff_aliases.yaml is read fresh by the parser — the identity
aliases added in this same commit are what let these events canonicalize
at all).

Usage: `.venv/bin/python3 migrations/2026-08-01_backfill_bucket_c_candidates.py`
Idempotent-safe: refuses to run twice (checks post-conditions first; if
already backfilled, exits without writing).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.econ_calendar_ff import parse_jblanked_range, canonical_id  # noqa: E402
from src.ff_refresh import merge_weekly  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
BACKUP_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet.pre-backfill-bucket-c"
ARCHIVE_JSON = ROOT / "data" / "archive" / "ff_calendar_range.json"
JB_RAW_DIR = ROOT / "data" / "jb_raw"

# The exact, narrow filter — nothing else is ever taken from either source.
TARGET_SERIES = [
    ("JPY", "Tokyo Core CPI y/y"),
    ("GBP", "Industrial Production m/m"),
    ("USD", "Industrial Production m/m"),
    ("USD", "Durable Goods Orders m/m"),
    ("JPY", "Core Machinery Orders m/m"),
    ("USD", "Personal Spending m/m"),
    ("USD", "Personal Income m/m"),
    ("USD", "Import Prices m/m"),
    ("AUD", "Private Capital Expenditure q/q"),
    ("JPY", "Capital Spending q/y"),
    ("AUD", "Company Operating Profits q/q"),
    ("AUD", "Import Prices q/q"),
    ("JPY", "SPPI y/y"),
    ("JPY", "Prelim GDP Price Index y/y"),
    ("USD", "Advance GDP Price Index q/q"),
    ("USD", "Prelim Unit Labor Costs q/q"),
    ("JPY", "Prelim Industrial Production m/m"),
]

# Expected row counts per canonical_id (archive + jb_raw, deduped by exact
# raw Date string) — captured 2026-08-01 against the read-only sources.
# Asserted below; any drift aborts rather than silently backfilling a
# different amount than reviewed.
EXPECTED_COUNTS = {
    "jpy_tokyo_core_cpi": 48,
    "gbp_industrial_production": 44,
    "usd_industrial_production": 42,
    "usd_durable_goods_orders": 45,
    "jpy_core_machinery_orders": 43,
    "usd_personal_spending": 44,
    "usd_personal_income": 43,
    "usd_import_prices": 42,
    "aud_private_capital_expenditure": 14,
    "jpy_capital_spending": 13,
    "aud_company_operating_profits": 12,
    "aud_import_prices": 19,
    "jpy_sppi": 44,
    "jpy_prelim_gdp_price_index": 14,
    "usd_advance_gdp_price_index": 18,
    "usd_prelim_unit_labor_costs": 14,
    "jpy_prelim_industrial_production": 44,
}

# PMI guard: these canonical_ids must be bit-identical before and after
# this script runs. Captured 2026-08-01 against the live parquet.
PMI_GUARD = {
    "aud_s_p_global_manufacturing_pmi": 8, "aud_s_p_global_services_pmi": 8,
    "cad_s_p_global_manufacturing_pmi": 43, "chf_procure_ch_manufacturing_pmi": 7,
    "chf_procurech_manufacturing_pmi": 37, "eur_s_p_global_manufacturing_pmi": 8,
    "eur_s_p_global_services_pmi": 8, "eur_sp_global_manufacturing_pmi": 35,
    "eur_sp_global_services_pmi": 35, "gbp_s_p_global_cips_manufacturing_pmi": 43,
    "gbp_s_p_global_cips_services_pmi": 44, "jpy_au_jibun_bank_manufacturing_pmi": 40,
    "usd_ism_manufacturing_pmi": 42, "usd_ism_non_manufacturing_pmi": 41,
}


def _pmi_counts(df: pd.DataFrame) -> dict[str, int]:
    return {cid: int((df["canonical_id"] == cid).sum()) for cid in PMI_GUARD}


def _combined_source_events() -> list[dict]:
    """Archive (full history) + every jb_raw snapshot (recent tail), deduped
    by the exact raw Date string within each (Currency, Name) — the rolling
    jb_raw files overlap each other's date ranges heavily."""
    events: dict[tuple, dict] = {}
    for e in json.loads(ARCHIVE_JSON.read_text()):
        events[(e.get("Currency"), e.get("Name"), e.get("Date"))] = e
    for f in sorted(JB_RAW_DIR.glob("jb_range_*.json")):
        for e in json.loads(f.read_text()):
            events[(e.get("Currency"), e.get("Name"), e.get("Date"))] = e
    return list(events.values())


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

    target_cids = {canonical_id(ccy, name) for ccy, name in TARGET_SERIES}
    already = existing[existing["canonical_id"].isin(target_cids)]
    if not already.empty:
        raise SystemExit(
            f"{len(already)} row(s) already present under the target canonical_ids — "
            "this migration has already run (or the parquet already has this data). "
            "Refusing to run again to avoid double-inserting. Aborting, no write made."
        )

    combined_events = _combined_source_events()
    arch = parse_jblanked_range(combined_events)

    filtered_parts = []
    for ccy, name in TARGET_SERIES:
        sub = arch[(arch["currency"] == ccy) & (arch["name_canonical"] == name)].copy()
        if sub.empty:
            raise SystemExit(f"combined sources have 0 rows for ({ccy}, {name!r}) — aborting, nothing to backfill")
        cid = canonical_id(ccy, name)
        bad_cid = sub[sub["canonical_id"] != cid]
        if not bad_cid.empty:
            raise SystemExit(f"unexpected canonical_id mismatch for ({ccy}, {name!r}): {bad_cid['canonical_id'].unique()}")
        n = len(sub)
        want = EXPECTED_COUNTS[cid]
        if n != want:
            raise SystemExit(f"row count drift for {cid}: got {n}, expected {want} — "
                             "sources changed since review, aborting rather than backfilling blind")
        filtered_parts.append(sub)
    incoming = pd.concat(filtered_parts, ignore_index=True)

    # Hard filter check: the incoming frame must contain ONLY the 17 target
    # canonical_ids — never anything else, regardless of what the sources hold.
    stray = set(incoming["canonical_id"].unique()) - target_cids
    if stray:
        raise SystemExit(f"incoming frame has unexpected canonical_id(s): {stray} — aborting")
    if set(incoming["canonical_id"].unique()) & set(PMI_GUARD):
        raise SystemExit("incoming frame contains a PMI-guarded canonical_id — aborting")

    combined = merge_weekly(existing, incoming)

    after_pmi = _pmi_counts(combined)
    if after_pmi != PMI_GUARD:
        raise SystemExit(
            f"PMI guard failed AFTER backfill: expected {PMI_GUARD}, found {after_pmi}. "
            "The merge altered rows it should never have touched — aborting, NOT writing the parquet."
        )

    expected_total_new = sum(EXPECTED_COUNTS.values())
    if len(combined) != len(existing) + expected_total_new:
        raise SystemExit(
            f"row count delta is not purely additive: existing={len(existing)}, "
            f"combined={len(combined)}, expected={len(existing) + expected_total_new} — aborting"
        )

    for cid, want in EXPECTED_COUNTS.items():
        got = int((combined["canonical_id"] == cid).sum())
        if got != want:
            raise SystemExit(f"post-merge count mismatch for {cid}: got {got}, want {want} — aborting")

    # All guards passed — back up, then write.
    shutil.copy2(FF_PARQUET, BACKUP_PARQUET)
    combined.to_parquet(FF_PARQUET, index=False)

    print(f"OK — backfilled {len(incoming)} rows across 17 series (12 indicator_keys). "
         f"Parquet: {len(existing)} -> {len(combined)} rows. "
         f"Backup written to {BACKUP_PARQUET}. PMI guard unchanged: {after_pmi}.")
    for ccy, name in TARGET_SERIES:
        cid = canonical_id(ccy, name)
        sub = combined[combined["canonical_id"] == cid]
        print(f"  {ccy:4s} {name:35s} n={len(sub):3d}  {sub['datetime_utc'].min()} .. {sub['datetime_utc'].max()}")


if __name__ == "__main__":
    main()
