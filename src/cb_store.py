"""Deterministic parquet stores for the Central Banks module.

Two shapes, one serializer:
  * `Dataset`  - append-only, monthly partitions `<dir>/<prefix>_YYYY-MM.parquet` keyed by `key`, partitioned on the
                 month of `month_field` (market quotes by as-of, official series by date). A row whose content is
                 unchanged keeps its stored `fetched_at`, so idle runs leave no diff; only changed partitions are
                 rewritten.
  * `write_table_file` - a single small file (decisions, projections) rewritten only when its bytes change.

Byte determinism: rows sorted by key, fixed writer settings (pyarrow is pinned in requirements.txt).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import pyarrow as pa
import pyarrow.parquet as pq


@dataclass(frozen=True)
class Dataset:
    name: str                          # directory under data/cb/
    prefix: str                        # partition file prefix
    schema: pa.Schema
    key: tuple
    month_field: str
    content: tuple                     # columns compared to decide "unchanged"

    @property
    def columns(self) -> list:
        return self.schema.names

    def row_key(self, row: dict) -> tuple:
        return tuple(row[k] for k in self.key)

    def directory(self, base: Path) -> Path:
        return Path(base) / self.name

    def partition(self, base: Path, month: str) -> Path:
        return self.directory(base) / f"{self.prefix}_{month}.parquet"


def month_of(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _as_month(v) -> Optional[str]:
    return month_of(v) if isinstance(v, date) else v


def _sort_key(row: dict, key: tuple) -> tuple:
    """Sortable even when a key part is None (None sorts first)."""
    return tuple((0, "") if row[k] is None else (1, row[k]) for k in key)


def serialize(schema: pa.Schema, key: tuple, rows: list) -> bytes:
    rows = sorted(rows, key=lambda r: _sort_key(r, key))
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), sink, compression="zstd", compression_level=3,
                   use_dictionary=True, write_statistics=True, data_page_version="1.0", version="2.6",
                   row_group_size=max(len(rows), 1))
    return sink.getvalue().to_pybytes()


def _write_bytes(f: Path, data: bytes) -> bool:
    if f.exists() and f.read_bytes() == data:
        return False
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, f)
    return True


# ---------------------------------------------------------------------------
# Monthly partitions
# ---------------------------------------------------------------------------

def list_partitions(ds: Dataset, base: Path) -> dict:
    d = ds.directory(base)
    return {f.stem.removeprefix(ds.prefix + "_"): f for f in sorted(d.glob(f"{ds.prefix}_????-??.parquet"))} if d.exists() else {}


def load(ds: Dataset, base: Path, start=None, end=None) -> dict:
    """Every partition, or only the months in [start, end] (inclusive, 'YYYY-MM' or a date)."""
    lo, hi = _as_month(start), _as_month(end)
    store: dict = {}
    for month, f in list_partitions(ds, base).items():
        if (lo and month < lo) or (hi and month > hi):
            continue
        for r in pq.read_table(f).to_pylist():
            store[ds.row_key(r)] = r
    return store


def write(ds: Dataset, base: Path, store: dict, months: Optional[Iterable[str]] = None) -> list:
    """Rewrite only the partitions in `months` (default all) and only when their bytes differ.
    Returns the months actually written."""
    by_month: dict = {}
    for r in store.values():
        by_month.setdefault(month_of(r[ds.month_field]), []).append(r)
    written = []
    for month in sorted(months if months is not None else by_month):
        rows = by_month.get(month)
        if rows and _write_bytes(ds.partition(base, month), serialize(ds.schema, ds.key, rows)):
            written.append(month)
    return written


@dataclass
class Merge:
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    months: set = field(default_factory=set)       # partitions with a new / changed row


def merge(ds: Dataset, store: dict, rows: Iterable[dict]) -> Merge:
    """Last-write-wins on the key; identical content keeps the stored row (and its fetched_at)."""
    st = Merge()
    for row in rows:
        k = ds.row_key(row)
        old = store.get(k)
        if old is not None and all(old[c] == row[c] for c in ds.content):
            st.unchanged += 1
            continue
        store[k] = row
        st.months.add(month_of(row[ds.month_field]))
        if old is None:
            st.new += 1
        else:
            st.updated += 1
    return st


# ---------------------------------------------------------------------------
# Single small file
# ---------------------------------------------------------------------------

def read_table_file(path: Path) -> list:
    return pq.read_table(path).to_pylist() if Path(path).exists() else []


def write_table_file(path: Path, schema: pa.Schema, key: tuple, rows: list) -> bool:
    """One deterministic parquet file; rewritten only when its bytes change. Returns True when written."""
    return _write_bytes(Path(path), serialize(schema, key, rows))
