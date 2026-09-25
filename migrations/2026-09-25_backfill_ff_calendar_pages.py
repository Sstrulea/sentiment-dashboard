"""ONE-TIME DATA MIGRATION — run once, then keep for the audit trail.

Fills the gaps found by the /history audit of 2026-09-24
(docs/ff-calendar-backfill-2026-09-25/README.md) from the ForexFactory calendar
day pages — the same source the live pipeline ingests (FF weekly feed), read
through https://www.forexfactory.com/calendar?day=<mon><d>.<yyyy>. Every page
used is committed verbatim under docs/ff-calendar-backfill-2026-09-25/pages/
(extracted as JSON, FF display time zone America/New_York, DST-aware), and every
change carries its page URL in docs/ff-calendar-backfill-2026-09-25/changes.csv.

What it does, all on data/economic_calendar_ff.parquet (backup first):
  insert  publications missing from the parquet (JBlanked archive holes of
          1-31 Dec 2023 and 3-6 Apr 2026, single events the archive never
          carried, BoJ decisions the archive only had as 0.0 placeholders,
          SNB decisions after 2025-06, JPY Prelim GDP q/q 2023-2025, the
          USD Q3-2025 first GDP estimate). Only gaps inside an existing
          series (plus jpy_gdp's own 2023-2025 history and the AUD
          Retail->Household Spending handoff month); flash-PMI history is NOT
          added here (separate reattribution).
  delete  JB placeholders sitting next to the real print of the same release:
          0/0/0 rows, 0.0 rows on level/index series, the BoJ 00:00-server-time
          placeholders and pre-print re-listings. Wrong-year rows: the archive
          dated part of January 2024 as January 2023 (identical actual/forecast/
          previous to the 2024 print); where such a row occupies the slot of the
          real Jan-2023 print it is overwritten with that print (update), where
          the 2024 print was missing it is re-inserted at its 2024 date. Three
          rows filed under the wrong currency (same values/instant as another
          country's PMI).
  update  consensus provenance: a JB 0.0 forecast the FF page confirms as a real
          "0.0%" becomes forecast_origin "ff" (effective_consensus keeps it);
          FF blank -> forecast NaN, "ff_blank". Placeholders filled from the page
          (actual/forecast/previous). Three JB rows whose values the page and the
          series' own previous-chain both contradict are corrected.

Guards: every delete/update target must exist exactly once; no insert target may
already exist (refuses to run twice). Row count after = before - deletes + inserts.

Every deleted row is also written to data/ff_tombstones.csv (canonical_id,
datetime_utc, reason, source): the JB archive still carries these rows, and
src.archive_backfill.scope_recoverable_rows skips a tombstoned
(canonical_id, release date), so an archive re-scan never brings them back.

Usage: `.venv/bin/python3 migrations/2026-09-25_backfill_ff_calendar_pages.py`
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
BACKUP_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet.pre-ff-calendar-backfill"
CHANGES_CSV = ROOT / "docs" / "ff-calendar-backfill-2026-09-25" / "changes.csv"
TOMBSTONES_CSV = ROOT / "data" / "ff_tombstones.csv"
MIGRATION = "migrations/2026-09-25_backfill_ff_calendar_pages.py"
# Earlier purges, recorded as tombstones too: 63 of the 64 January-2023 copies purged
# on 2026-07-31 and 11 of the 219 PMI rows purged on 2026-07-30 were back in the
# parquet by 2026-09-24 (an archive re-scan had no record of the deletions).
PRIOR_PURGES = [
    (ROOT / "docs" / "pmi-219-purged-rows.csv",
     "PMI cross-country purge 2026-07-30 (docs/pmi-219-purged-rows.csv)"),
    (ROOT / "docs" / "jan2023-purge-candidates-live-parquet.csv",
     "migrations/2026-07-31_purge_jan2023_duplicates.py"),
]

SETTABLE = {"actual", "forecast", "previous", "forecast_origin", "jb_status"}


def _key(df: pd.DataFrame) -> pd.Series:
    return df["canonical_id"] + "|" + pd.to_datetime(df["datetime_utc"]).dt.strftime("%Y-%m-%d %H:%M:%S")


def apply(existing: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    """Pure: returns the migrated frame; raises on any guard failure."""
    from src.econ_calendar_ff import CANON_COLUMNS, ensure_provenance_columns

    df = ensure_provenance_columns(existing.copy())
    df["datetime_utc"] = pd.to_datetime(df["datetime_utc"])
    for col in ("forecast_origin", "jb_status"):
        df[col] = df[col].astype(object)
    keys = _key(df)
    counts = keys.value_counts()

    ch = changes.copy()
    ch["datetime_utc"] = pd.to_datetime(ch["datetime_utc"])
    ch["tkey"] = _key(ch)

    # guards (inserts first: on an already-migrated parquet they are all present)
    ins = ch[ch["op"] == "insert"]
    present = [x for x in ins["tkey"] if counts.get(x, 0) > 0]
    if present:
        raise SystemExit(f"insert: {len(present)} row(s) already present (already migrated?), e.g. {present[:3]}")
    for op in ("delete", "update"):
        k = ch.loc[ch["op"] == op, "tkey"]
        bad = [x for x in k if counts.get(x, 0) != 1]
        if bad:
            raise SystemExit(f"{op}: {len(bad)} target(s) missing or not unique, e.g. {bad[:3]}")
    if ch["tkey"].duplicated().any():
        raise SystemExit("changes.csv: duplicate (canonical_id, datetime_utc) targets")

    # updates
    idx_by_key = dict(zip(keys, df.index))
    for r in ch[ch["op"] == "update"].itertuples(index=False):
        fields = json.loads(r.set_fields)
        unknown = set(fields) - SETTABLE
        if unknown:
            raise SystemExit(f"update {r.tkey}: unsupported fields {unknown}")
        i = idx_by_key[r.tkey]
        for f, v in fields.items():
            df.at[i, f] = (np.nan if v is None and f in ("actual", "forecast", "previous") else v)

    # deletes
    drop = set(ch.loc[ch["op"] == "delete", "tkey"])
    df = df[~keys.isin(drop)]

    # inserts
    new = pd.DataFrame({
        "canonical_id": ins["canonical_id"], "currency": ins["currency"],
        "name_raw": ins["name_raw"], "name_canonical": ins["name_canonical"],
        "datetime_utc": ins["datetime_utc"],
        "actual": pd.to_numeric(ins["actual"]), "forecast": pd.to_numeric(ins["forecast"]),
        "previous": pd.to_numeric(ins["previous"]),
        "released": True, "source": "ff",
        "forecast_origin": ins["forecast_origin"].astype(object), "jb_status": None,
    })[CANON_COLUMNS]
    out = pd.concat([df[CANON_COLUMNS], new], ignore_index=True)
    out = out.sort_values(["currency", "canonical_id", "datetime_utc"]).reset_index(drop=True)

    expected = len(existing) - len(drop) + len(new)
    if len(out) != expected:
        raise SystemExit(f"row count {len(out)} != expected {expected}")
    return out


def tombstones(changes: pd.DataFrame, current: pd.DataFrame | None = None,
               prior: list[tuple[pd.DataFrame, str]] | None = None,
               migrated: pd.DataFrame | None = None) -> pd.DataFrame:
    """Pure: the deleted rows as tombstones (canonical_id, datetime_utc, reason,
    source), plus the rows of earlier purges (`prior`: (frame, source) pairs),
    merged with an existing tombstone table; a key present in `migrated` (the
    parquet after this migration) is never a tombstone. src.archive_backfill
    reads them so an archive re-scan never brings a deliberately removed row
    back (the JB archive still carries every one of them)."""
    def fmt(s):
        return pd.to_datetime(s).dt.strftime("%Y-%m-%d %H:%M:%S").to_numpy()
    d = changes[changes["op"] == "delete"]
    parts = [] if current is None or not len(current) else [current]
    parts.append(pd.DataFrame({"canonical_id": d["canonical_id"].to_numpy(), "datetime_utc": fmt(d["datetime_utc"]),
                               "reason": d["reason"].to_numpy(), "source": MIGRATION}))
    for frame, source in prior or []:
        parts.append(pd.DataFrame({"canonical_id": frame["canonical_id"].to_numpy(),
                                   "datetime_utc": fmt(frame["datetime_utc"]),
                                   "reason": "purged earlier (see source)", "source": source}))
    new = pd.concat(parts, ignore_index=True).drop_duplicates(["canonical_id", "datetime_utc"], keep="first")
    if migrated is not None:
        live = set(zip(migrated["canonical_id"], fmt(migrated["datetime_utc"])))
        new = new[[k not in live for k in zip(new["canonical_id"], new["datetime_utc"])]]
    return new.sort_values(["canonical_id", "datetime_utc"]).reset_index(drop=True)


def main() -> None:
    if not FF_PARQUET.exists():
        raise SystemExit(f"missing {FF_PARQUET}")
    changes = pd.read_csv(CHANGES_CSV, dtype={"set_fields": str})
    existing = pd.read_parquet(FF_PARQUET)
    out = apply(existing, changes)
    if not BACKUP_PARQUET.exists():
        shutil.copy2(FF_PARQUET, BACKUP_PARQUET)
    out.to_parquet(FF_PARQUET, index=False)
    current = pd.read_csv(TOMBSTONES_CSV) if TOMBSTONES_CSV.exists() else None
    prior = [(pd.read_csv(p), src) for p, src in PRIOR_PURGES if p.exists()]
    tomb = tombstones(changes, current, prior, migrated=out)
    tomb.to_csv(TOMBSTONES_CSV, index=False)
    n = changes["op"].value_counts().to_dict()
    print(f"migrated: {len(existing)} -> {len(out)} rows; {n}; backup {BACKUP_PARQUET.name}; "
          f"{len(tomb)} tombstones in {TOMBSTONES_CSV.name}")


if __name__ == "__main__":
    main()
