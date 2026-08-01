"""fix/alert-noise-and-ff-archive — FAZA 1: alert suppression (Option A).

docs/proposal-alert-suppression.md — a documented, dated exception list,
never a mutable state file committed by CI. Scoped strictly to (scope,
currency, indicator); an exception can never suppress a different series.

Three things this module guarantees, all covered by tests:
  1. Expiry is LOUD: past `review_by`, the exception no longer suppresses —
     the alert returns with an explicit "exception expired on <date>"
     message, distinguishable from a brand-new defect.
  2. Orphan detection: an exception whose (currency, indicator) is NOT
     currently stale is flagged as removable — dead config accumulates
     otherwise, and would silently mask a RECURRENCE of the same series.
  3. Scoping is absolute: classifying one stale row never looks at any
     other row — a new defect on a different series, or a different
     indicator on the SAME currency, is never touched by an exception
     that doesn't name it explicitly.

Pure — no I/O beyond what's passed in (load_exceptions does the one file read).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml


def load_exceptions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("exceptions", []) or []


def _find_exception(exceptions: list[dict], scope: str, currency: str, indicator: str) -> dict | None:
    for exc in exceptions:
        if (exc.get("scope") == scope and exc.get("currency") == currency
                and exc.get("indicator") == indicator):
            return exc
    return None


def classify_stale_rows(
    stale_rows: list[dict],
    exceptions: list[dict],
    as_of: pd.Timestamp,
    scope: str = "calendar",
) -> list[dict]:
    """`stale_rows`: [{"currency", "indicator_key", ...}] — one per currently
    stale (currency, indicator). Returns the same rows, each annotated with:
      - `gate`: "suppressed" (valid exception, excluded from exit-1) or
        "alert" (no exception, or exception expired)
      - `exception_status`: "none" | "active" | "expired"
      - `exception_note`: human-readable, includes the expiry date when
        expired — never silent about why an old exception stopped applying
    """
    as_of = pd.Timestamp(as_of)
    out = []
    for row in stale_rows:
        exc = _find_exception(exceptions, scope, row["currency"], row["indicator_key"])
        annotated = dict(row)
        if exc is None:
            annotated.update(gate="alert", exception_status="none", exception_note=None)
        else:
            review_by = pd.Timestamp(exc["review_by"])
            if as_of <= review_by:
                annotated.update(
                    gate="suppressed", exception_status="active",
                    exception_note=f"excepted (review by {review_by.date()}): {exc.get('reason', '').strip()}")
            else:
                annotated.update(
                    gate="alert", exception_status="expired",
                    exception_note=f"EXCEPTION EXPIRED on {review_by.date()} — re-evaluate, not a brand-new defect")
        out.append(annotated)
    return out


def find_orphaned_exceptions(
    stale_rows: list[dict],
    exceptions: list[dict],
    scope: str = "calendar",
) -> list[dict]:
    """Exceptions whose (currency, indicator) is NOT currently stale — the
    underlying defect looks fixed. Flagged as removable so dead config
    doesn't silently accumulate and mask a future recurrence of the SAME
    series (a fresh staleness on a series with a stale, unremoved exception
    would otherwise be silently suppressed again)."""
    stale_keys = {(r["currency"], r["indicator_key"]) for r in stale_rows}
    orphans = []
    for exc in exceptions:
        if exc.get("scope") != scope:
            continue
        key = (exc.get("currency"), exc.get("indicator"))
        if key not in stale_keys:
            orphans.append(exc)
    return orphans
