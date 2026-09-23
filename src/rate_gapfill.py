"""Month-boundary gap fill for incremental 2y sources (audit F2).

BoE GLC serves only the current month. If the last run of a month did not see
its final business days, the stored series and the new month's file leave a gap.
Fill it from the source's history archive (BoeSource.fetch_history,
glcnominalddata.zip):

  missing = business days strictly between the last stored date and the first
            date of the fetched window, minus days already confirmed without data
  at most one attempt per UTC day per source (state file);
  a missing day on or before the archive's coverage date (the day before its
  newest workbook was written) without a value is a non-trading day (e.g. the
  UK bank holiday 2026-08-31: the archive built 2026-09-03 ends 2026-08-28)
  -> confirmed_no_data, never asked again.
Idempotent: filled days leave the gap, confirmed days are skipped.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Callable

import pandas as pd

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
GAP_STATE_JSON = ROOT / "data" / "rates_gap_state.json"


def load_state(path: Path = GAP_STATE_JSON) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except Exception:  # noqa: BLE001 — missing/corrupt -> fresh
        return {}


def save_state(state: dict, path: Path = GAP_STATE_JSON) -> None:
    Path(path).write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def missing_business_days(last_stored: date, first_new: date,
                          confirmed: set[str]) -> list[date]:
    if last_stored is None or first_new is None or first_new <= last_stored:
        return []
    days = pd.bdate_range(pd.Timestamp(last_stored) + pd.Timedelta(days=1),
                          pd.Timestamp(first_new) - pd.Timedelta(days=1))
    return [d.date() for d in days if d.date().isoformat() not in confirmed]


def fill_gap(source: str, stored_dates: list[date], new_points: list[tuple[date, float]],
             today: date, state: dict,
             history: Callable[[date], tuple[list[tuple[date, float]], date]]
             ) -> tuple[list[tuple[date, float]], dict]:
    """Pure over `state` (returns a new dict). Returns (points to add, state)."""
    st = dict(state.get(source) or {})
    confirmed = set(st.get("confirmed_no_data", []))
    last = max(stored_dates) if stored_dates else None
    first = min(d for d, _ in new_points) if new_points else None
    missing = missing_business_days(last, first, confirmed)
    if not missing:
        return [], state
    if st.get("last_attempt_utc_date") == today.isoformat():
        return [], state              # already tried today
    st["last_attempt_utc_date"] = today.isoformat()
    st["gap"] = [missing[0].isoformat(), missing[-1].isoformat()]
    try:
        pts, covered_until = history(missing[0])
    except Exception as e:  # noqa: BLE001 — retried tomorrow
        st["last_result"] = f"error: {str(e)[:200]}"
        log.warning("%s gap %s..%s: history fetch failed (%s)", source, missing[0], missing[-1], e)
        return [], {**state, source: st}
    have = dict(pts)
    added = [(d, have[d]) for d in missing if d in have]
    for d in missing:
        if d not in have and d <= covered_until:
            confirmed.add(d.isoformat())
    st["confirmed_no_data"] = sorted(confirmed)
    still = [d for d in missing if d not in have and d.isoformat() not in confirmed]
    st["last_result"] = f"filled {len(added)}, still missing {len(still)}"
    log.warning("%s gap %s..%s: %s", source, missing[0], missing[-1], st["last_result"])
    return added, {**state, source: st}
