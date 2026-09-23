"""Audit 2.4 — zeros contradicted by next.previous (the previous-consistency
zero class), measured through the EXISTING data/ff_quarantine.parquet:

  T1  the zeros that are SCORED (scored_source == "ff") go to quarantine;
  T2  T1 + every zero finding is quarantined AND recovered from next.previous
      (data/ff_previous_recovery.parquet, source/actual_origin = 'ff_previous').

A finding's same-day FF siblings carrying 0.0 are quarantined with it (a re-listed
release). Consensus/previous of a recovered row come from the scoring frame.

    .venv/bin/python scripts/measure/zero_recovery_t1_t2.py --root DIR \
        --report integrity.json --cal cal.parquet --mode T1|T2
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

Q_COLUMNS = ["currency", "indicator_key", "datetime_utc", "ff_actual", "fred_value",
             "diff", "tol", "reason"]


def build(root: Path, findings: list[dict], cal: pd.DataFrame, mode: str):
    ff = pd.read_parquet(root / "data" / "economic_calendar_ff.parquet")
    ff["datetime_utc"] = pd.to_datetime(ff["datetime_utc"])
    zeros = [f for f in findings if f["kind"] == "zero"]
    chosen = [f for f in zeros if mode == "T2" or f["scored_source"] == "ff"]
    q, rec = [], []
    cal = cal.copy()
    cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    for f in chosen:
        dt = pd.Timestamp(f["release_dt"])
        sib = ff[(ff["canonical_id"] == f["canonical_id"]) & (ff["datetime_utc"].dt.date == dt.date())
                 & (ff["actual"] == 0.0)]
        for s in sib.itertuples(index=False):
            q.append({"currency": f["currency"], "indicator_key": f["indicator_key"],
                      "datetime_utc": s.datetime_utc, "ff_actual": 0.0,
                      "fred_value": f["next_previous"], "diff": f["diff"], "tol": f["tolerance"],
                      "reason": f"previous_consistency_zero_{mode}"})
        if mode == "T2":
            row = cal[(cal["currency"] == f["currency"]) & (cal["indicator_key"] == f["indicator_key"])
                      & (cal["release_dt"] == dt) & (cal["source"] == "ff")]
            cons = row["consensus"].iloc[0] if len(row) else float("nan")
            prev = row["previous"].iloc[0] if len(row) else float("nan")
            rec.append({"currency": f["currency"], "indicator_key": f["indicator_key"],
                        "release_dt": dt, "actual": float(f["next_previous"]),
                        "consensus": cons, "previous": prev, "name_raw": f["name_raw"]})
    qdf = pd.DataFrame(q, columns=Q_COLUMNS).drop_duplicates(["currency", "indicator_key", "datetime_utc"])
    return qdf, pd.DataFrame(rec), len(chosen)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--cal", required=True)
    ap.add_argument("--mode", choices=["T1", "T2"], required=True)
    a = ap.parse_args()
    root = Path(a.root)
    rep = json.loads(Path(a.report).read_text())
    rep = rep.get("_integrity_report", rep)
    findings = rep["checks"]["previous_consistency"]["findings"]
    qdf, rec, n = build(root, findings, pd.read_parquet(a.cal), a.mode)
    old = pd.read_parquet(root / "data" / "ff_quarantine.parquet")
    pd.concat([old, qdf], ignore_index=True).to_parquet(root / "data" / "ff_quarantine.parquet", index=False)
    if len(rec):
        rec.to_parquet(root / "data" / "ff_previous_recovery.parquet", index=False)
    print(f"{a.mode}: {n} zero finding(s) -> {len(qdf)} quarantine row(s), {len(rec)} recovered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
