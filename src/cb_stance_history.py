"""Stance (phase S1) - daily history, point-in-time by construction: data/cb/stance_history/stance_history_YYYY.parquet.

    python -m src.cb_collect --stage stance        # the daily scheduled run (cb-refresh, cron 23 7 * * *), after macro + framework

Key (bank, snapshot_date). APPEND-ONLY: a day once written is never rewritten - a second write of the same day is refused and
logged (the report lists it). Each row keeps the outputs of src/cb_compute/stance.py (not rounded), the as-of and source of
every input (`inputs_json`), the mandate version id, the cb_stance version and the code version. Written by CI only after the
merge (single writer, ARCHITECTURE section 6); a branch carries one seed snapshot in a separate commit.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import pyarrow as pa

from . import cb_store as cs

log = logging.getLogger("cb_stance_history")

F64, STR = pa.float64(), pa.string()
NUMERIC = ("v5_position", "v5_need", "rate", "pi_under", "pi_head", "gap_under", "gap_head", "y", "x", "x_headline", "N_mid", "N_lo", "N_hi", "S", "S_lo", "S_hi",
           "G", "G_lo", "G_hi", "margin", "pi_12m", "gap_12m", "y_12m", "x_12m", "G_12m", "pivot", "lens_value", "horizon_gap")
TEXT = ("asof", "N_asof", "N_flag", "situation", "situation_12m", "pi_12m_period", "pi_12m_basis", "lens_kind", "lens_flag", "lens_na",
        "flags", "flags_12m", "mandate_id", "code_version", "v5_conclusion", "v5_reason")
SCHEMA = pa.schema([("bank", STR), ("snapshot_date", pa.date32())]
                   + [(c, F64) for c in NUMERIC] + [(c, STR) for c in TEXT]
                   + [("direction", pa.int32()), ("direction_12m", pa.int32()), ("consistent", pa.bool_()), ("stance_version", pa.int32()),
                      ("inputs_json", STR), ("lens_json", STR), ("cycle_json", STR), ("market_json", STR), ("written_at", pa.timestamp("us", tz="UTC"))])
CONTENT = tuple(c for c in SCHEMA.names if c not in ("bank", "snapshot_date", "written_at"))
HISTORY = cs.Dataset("stance_history", "stance_history", SCHEMA, ("bank", "snapshot_date"), "snapshot_date", CONTENT, granularity="year")


def row_of(r: dict, snapshot: date, now: datetime) -> dict:
    """One history row from a stance.compute result (pure)."""
    N, lens = r.get("N") or {}, r.get("lens") or {}
    lv = lens.get("delta_bp") if lens.get("kind") in ("market", "constant_rate") else lens.get("gap_bp")
    v5 = r.get("v5") or {}
    return {"bank": r["currency"], "snapshot_date": snapshot, "asof": r["asof"],
            "v5_position": v5.get("P"), "v5_need": v5.get("N"), "v5_conclusion": v5.get("conclusion"), "v5_reason": v5.get("reason"),
            **{c: r.get(c) for c in ("rate", "pi_under", "pi_head", "gap_under", "gap_head", "y", "x", "x_headline", "S", "S_lo", "S_hi",
                                     "G", "G_lo", "G_hi", "margin", "pi_12m", "gap_12m", "y_12m", "x_12m", "G_12m", "pivot")},
            "N_mid": N.get("mid"), "N_lo": N.get("lo"), "N_hi": N.get("hi"), "N_asof": N.get("asof"), "N_flag": N.get("flag"),
            "situation": r.get("situation"), "situation_12m": r.get("situation_12m"), "pi_12m_period": r.get("pi_12m_period"),
            "pi_12m_basis": r.get("pi_12m_basis") or None, "lens_kind": lens.get("kind"), "lens_value": lv, "lens_flag": lens.get("flag"),
            "lens_na": lens.get("na"), "horizon_gap": lens.get("horizon_gap"), "consistent": lens.get("consistent"),
            "flags": ",".join(r.get("flags") or []), "flags_12m": ",".join(r.get("flags_12m") or []), "mandate_id": r["mandate_id"], "code_version": r["code_version"],
            "direction": r.get("direction"), "direction_12m": r.get("direction_12m"), "stance_version": r["stance_version"],
            "inputs_json": json.dumps(r.get("inputs"), sort_keys=True, default=str), "lens_json": json.dumps(lens, sort_keys=True, default=str),
            "cycle_json": json.dumps(r.get("cycle"), sort_keys=True, default=str), "market_json": json.dumps(r.get("market"), sort_keys=True, default=str),
            "written_at": now}


@dataclass
class HistoryReport:
    snapshot: date
    written: list = field(default_factory=list)
    refused: list = field(default_factory=list)      # banks whose day already existed
    partitions: list = field(default_factory=list)


def append(paths, results: dict, snapshot: date, now: Optional[datetime] = None) -> HistoryReport:
    """Append the day; an existing (bank, day) is refused, never rewritten."""
    now = now or datetime.now(timezone.utc)
    store = cs.load(HISTORY, paths.dir)
    rep = HistoryReport(snapshot)
    rows = []
    for cur, r in results.items():
        if (cur, snapshot) in store:
            rep.refused.append(cur)
            log.warning("stance history %s %s: already written - refused (append-only)", cur, snapshot)
            continue
        rows.append(row_of(r, snapshot, now))
        rep.written.append(cur)
    st = cs.merge(HISTORY, store, rows)
    rep.partitions = cs.write(HISTORY, paths.dir, store, st.months)
    return rep


def load(paths) -> dict:
    return cs.load(HISTORY, paths.dir)


def report(rep: HistoryReport) -> tuple:
    line = f"stance history {rep.snapshot}: {len(rep.written)} written ({', '.join(rep.written) or '-'}), {len(rep.refused)} refused (already written: {', '.join(rep.refused) or '-'})"
    return line, f"### stance\n\n{line}"
