"""fix/archive-backfill-late-aliases — recover archive prints stranded by a
late-added `config/ff_aliases.yaml` alias.

GUARDED AGAINST RE-CONTAMINATION (found running this module's own test
suite): the archive predates the 2026-07-30/07-31 PMI decontamination
(`migrations/2026-07-31_reattribute_pmi_rows.py` — 219 CAD/GBP-tagged PMI
rows that were actually mislabeled CHF/JPY/EUR releases; 139 reattributed to
their real currency, 76 permanently purged, no valid target). Naively
recovering "every row absent from the parquet at (canonical_id, date)" would
silently reintroduce exactly those rows under their ORIGINAL WRONG currency
tag — the archive still has them mislabeled, that's the whole reason they
needed a migration in the first place. `scope_recoverable_rows` runs
`src.pmi_ingest_guard.country_hour_guard` (existing, unmodified, already
built for exactly this pattern — a row's local hour vs. its OWN series'
trailing dominant local hour) against each candidate merged onto the real
parquet, and drops any candidate the guard flags. This is READING an
existing guard, not writing a new quarantine rule or touching `src/ff_
scoring.py`/`src/economic_compute.py`/any weight/threshold/category/alias.

Same reason, second guard: rows a documented migration deliberately deleted
(JB placeholders, the January-2024 prints the archive also dated January 2023,
pre-print re-listings — `migrations/2026-09-25_backfill_ff_calendar_pages.py`)
are listed in `data/ff_tombstones.csv`; the archive still carries them, so a
tombstoned (canonical_id, release date) is never a recovery candidate.

docs/board-data-loss-round3.md (Q2) found real prints sitting in
`data/archive/ff_calendar_range.json` that never reached
`data/economic_calendar_ff.parquet`, because the alias routing them to a
canonical name was added AFTER those prints would have been ingested by the
live pipeline — the earliest confirmed cases were AUD `Trimmed Mean CPI m/m`
(alias added 2026-07-30, per `git log -S`) and CAD's BoC core measures
(`Common CPI y/y` / `Trimmed CPI y/y`, same commit week). This module
generalizes that recovery, board-wide, for any (currency, name_raw) whose
CURRENT alias+matcher config would route it to a configured indicator_key
today, restricted to prints the parquet genuinely never received.

Ingestion fix ONLY. Does not touch `src/ff_scoring.py`, `src/economic_
compute.py`, any weight/threshold/category/alias, and does not promote any
indicator into a scored category — this module only ever writes rows into
`data/economic_calendar_ff.parquet`, upstream of `to_scoring_frame`; whatever
quarantine decision a fresh JBlanked pull would have made for a given raw
value is made identically for a recovered one, the next time `to_scoring_
frame` runs (unmodified) over the enlarged parquet.

Two-stage, mirroring `src.jb_actuals.pull_actuals`'s own real pull path
exactly (imported, never reimplemented):
  1. `scope_recoverable_rows` — read-only. Canonicalizes the FULL archive
     through the real `parse_jblanked_range` (same alias/timezone/value-
     normalization a live pull uses), then restricts to rows whose canonical
     the SCORING matcher (`CompiledMatcher` over `data/economic_indicators.
     yaml`) routes to a configured indicator_key, whose `actual` is
     non-null, and for which the parquet has NO row at all — of any actual
     value — at that exact (canonical_id, release date). This last
     condition is what makes "never overwrite an existing non-null actual"
     structural rather than a runtime check: a row already present, in any
     form, is never a backfill candidate.
  2. `backfill_from_archive` — takes that scoped set, converts it to the
     same canonical-schema frame `parse_jblanked_range` already produced,
     and feeds it through `src.jb_actuals.clean_jblanked_actuals` (the
     placeholder-0.0-collapse a live JBlanked pull applies) then `src.
     ff_refresh.merge_weekly` (the field-aware history-preserving merge) —
     identical calls to `pull_actuals`'s own two-line merge step, imported
     unmodified. A recovered `actual == 0.0` with no real duplicate copy at
     its own (canonical_id, date) is nulled by `clean_jblanked_actuals`
     itself, precisely as it would be for a same-shaped freshly-pulled row.

`data/archive/ff_calendar_range.json`'s own max `Date` is 2026-07-03 (UTC,
per the same `jblanked_to_utc` conversion used here) — this is the hard
ceiling on what this module can ever recover. Any gap dated after that is
invisible to it by construction, not evidence of "nothing missing" there.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from .econ_calendar_ff import CANON_COLUMNS, parse_jblanked_range
from .economic_fetch import CompiledMatcher
from .ff_refresh import FF_PARQUET, merge_weekly
from .ff_scoring import CCY2COUNTRY
from .jb_actuals import clean_jblanked_actuals
from .pmi_ingest_guard import country_hour_guard

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_JSON = ROOT / "data" / "archive" / "ff_calendar_range.json"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
# Rows a documented migration deliberately removed from the parquet (JB
# placeholders, wrong-year copies, re-listings — see each row's `source`).
# The archive still carries them, so "absent from the parquet" is not
# "missing" for these: scope_recoverable_rows skips them.
TOMBSTONES_CSV = ROOT / "data" / "ff_tombstones.csv"

# The archive's own max Date, UTC (via jblanked_to_utc — see module docstring).
# Recomputed by tests against the live file rather than hard-asserted equal,
# so a future archive refresh naturally raises this ceiling without editing
# this module.
ARCHIVE_MAX_DATE_UTC_APPROX = pd.Timestamp("2026-07-03")


def _load_indicators_cfg(path: Path = INDICATORS_YAML) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _build_scoring_matcher(indicators_cfg: dict) -> CompiledMatcher:
    return CompiledMatcher(indicators_cfg.get("matcher", {}) or {})


def load_tombstones(path: Path = TOMBSTONES_CSV) -> set:
    """{(canonical_id, release date)} of rows deliberately removed from the
    parquet. Same granularity as the scope's own presence test (release DATE:
    the archive can list one removed row at a slightly different time)."""
    p = Path(path)
    if not p.exists():
        return set()
    t = pd.read_csv(p)
    return set(zip(t["canonical_id"], pd.to_datetime(t["datetime_utc"]).dt.date))


def scope_recoverable_rows(
    archive_path: Path = ARCHIVE_JSON,
    parquet_path: Path = FF_PARQUET,
    indicators_yaml: Path = INDICATORS_YAML,
    *,
    now_utc: Optional[pd.Timestamp] = None,
    tombstones_path: Path = TOMBSTONES_CSV,
) -> pd.DataFrame:
    """STEP 1 scope. Pure / read-only — reads both files, writes nothing.

    Returns the canonical-schema rows (`CANON_COLUMNS` + `indicator_key`)
    that are recoverable: alias+matcher-routed to a configured indicator_key,
    non-null actual, absent from the parquet at (canonical_id, release date),
    AND not flagged by `country_hour_guard` as the same cross-country PMI
    mislabeling the 2026-07-30/07-31 migrations already purged (see module
    docstring) — the archive predates that correction, so without this check
    a "recoverable" row can be exactly the wrong-currency-tagged data that
    was deliberately removed. Likewise a row tombstoned by a later migration
    (data/ff_tombstones.csv, `load_tombstones`) is never a candidate.
    """
    indicators_cfg = _load_indicators_cfg(indicators_yaml)
    matcher = _build_scoring_matcher(indicators_cfg)

    archive_records = json.loads(Path(archive_path).read_text())
    canon = parse_jblanked_range(archive_records, now_utc=now_utc)
    canon["indicator_key"] = canon.apply(
        lambda r: matcher.match(CCY2COUNTRY.get(r["currency"], ""), r["name_canonical"]),
        axis=1,
    )
    scoped = canon[canon["indicator_key"].notna() & canon["actual"].notna()].copy()

    if Path(parquet_path).exists():
        existing = pd.read_parquet(parquet_path)
        existing["datetime_utc"] = pd.to_datetime(existing["datetime_utc"])
    else:
        existing = pd.DataFrame(columns=CANON_COLUMNS)
    existing_keys = set(zip(existing["canonical_id"], existing["datetime_utc"].dt.date))

    scoped["_release_date"] = scoped["datetime_utc"].dt.date
    removed_keys = load_tombstones(tombstones_path)
    already_present = scoped.apply(
        lambda r: (r["canonical_id"], r["_release_date"]) in existing_keys
        or (r["canonical_id"], r["_release_date"]) in removed_keys, axis=1)
    candidates = scoped[~already_present].drop(columns=["_release_date"]).copy()
    if candidates.empty:
        return candidates.reset_index(drop=True)

    # country_hour_guard needs each candidate compared against ITS OWN
    # series' real trailing history to have anything to deviate from — build
    # that by appending candidates onto the real existing rows for this
    # check only; nothing here is written.
    combined_for_guard = pd.concat(
        [existing.reindex(columns=CANON_COLUMNS), candidates.reindex(columns=CANON_COLUMNS)],
        ignore_index=True,
    )
    flagged = country_hour_guard(combined_for_guard)
    if not flagged.empty:
        flagged_keys = set(zip(flagged["canonical_id"], flagged["datetime_utc"]))
        is_flagged = candidates.apply(
            lambda r: (r["canonical_id"], r["datetime_utc"]) in flagged_keys, axis=1)
        candidates = candidates[~is_flagged]

    return candidates.sort_values(["currency", "canonical_id", "datetime_utc"]).reset_index(drop=True)


def scope_summary(recoverable: pd.DataFrame) -> pd.DataFrame:
    """(currency, indicator_key) -> n / first / last, for the STEP 1 report."""
    if recoverable.empty:
        return pd.DataFrame(columns=["currency", "indicator_key", "n", "first", "last"])
    g = recoverable.groupby(["currency", "indicator_key"]).agg(
        n=("actual", "size"),
        first=("datetime_utc", "min"),
        last=("datetime_utc", "max"),
    ).reset_index()
    return g.sort_values("n", ascending=False).reset_index(drop=True)


def backfill_from_archive(
    archive_path: Path = ARCHIVE_JSON,
    parquet_path: Path = FF_PARQUET,
    indicators_yaml: Path = INDICATORS_YAML,
    *,
    now_utc: Optional[pd.Timestamp] = None,
    dry_run: bool = False,
    tombstones_path: Path = TOMBSTONES_CSV,
) -> dict:
    """STEP 2. Recovers the `scope_recoverable_rows` set through the exact
    same two calls `src.jb_actuals.pull_actuals` uses for a live pull:
    `clean_jblanked_actuals` then `merge_weekly` — both imported, neither
    reimplemented or bypassed. Idempotent: a second run's own `scope_
    recoverable_rows` finds nothing left (every recovered row now has a
    parquet entry at its (canonical_id, date), scored or quarantined-null
    alike), so `cleaned` is empty and `merge_weekly` is a no-op merge.
    """
    recoverable = scope_recoverable_rows(
        archive_path, parquet_path, indicators_yaml, now_utc=now_utc,
        tombstones_path=tombstones_path)

    existing = pd.read_parquet(parquet_path) if Path(parquet_path).exists() else None
    if existing is not None:
        existing["datetime_utc"] = pd.to_datetime(existing["datetime_utc"])

    jb_shaped = (recoverable.drop(columns=["indicator_key"])
                if "indicator_key" in recoverable.columns else recoverable)
    jb_shaped = jb_shaped.reindex(columns=CANON_COLUMNS)

    cleaned = clean_jblanked_actuals(jb_shaped, schedule=existing)
    merged = merge_weekly(existing, cleaned)

    if not dry_run:
        Path(parquet_path).parent.mkdir(parents=True, exist_ok=True)
        merged.to_parquet(parquet_path, index=False)

    return {
        "n_recoverable_candidates": int(len(recoverable)),
        "n_cleaned_rows": int(len(cleaned)),
        "n_cleaned_valid_actual": int(cleaned["actual"].notna().sum()) if len(cleaned) else 0,
        "n_before": 0 if existing is None else int(len(existing)),
        "n_after": int(len(merged)),
    }
