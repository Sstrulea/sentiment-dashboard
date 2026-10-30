"""Registry v0 — prospective calls. Saves, for the 8 currencies, the scores and ranks of:
  v3       production v3 currency score (public/data/economic.json currencies.<C>.score_v3, read only)
  macro    macro_momentum composite (equal category weights) as of the snapshot day (src.tree.macro)
  carry    BIS policy rate (data/backbone_rates.parquet) as of the day before
with the as-of date and the scoring dates at 2 / 4 / 8 / 13 weeks -> data/registry/calls.parquet.
Rank 1 = strongest. A re-run on the same asof replaces that asof's rows (idempotent).

    python scripts/registry/snapshot.py [--asof YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.tree.macro import MacroInputs  # noqa: E402

CCYS = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"]
HORIZONS = [2, 4, 8, 13]
CALLS = ROOT / "data" / "registry" / "calls.parquet"
PAYLOAD = ROOT / "public" / "data" / "economic.json"
COLUMNS = ["asof", "source", "source_asof", "ccy", "score", "rank", "horizon_weeks", "due",
           "snapshot_at", "realized_bp", "scored_at"]


def v3_scores(payload: Path = PAYLOAD) -> tuple[dict, str]:
    d = json.loads(payload.read_text())
    return {c: d["currencies"].get(c, {}).get("score_v3") for c in CCYS}, str(d.get("as_of"))


def macro_scores(asof: pd.Timestamp, mi: MacroInputs | None = None) -> dict:
    mi = mi or MacroInputs.load()
    return {c: mi.momentum(c, asof)["composite_eq"] for c in CCYS}


def carry_scores(asof: pd.Timestamp, backbone: Path | None = None) -> dict:
    from src.tree.backbone import BACKBONE_FILE
    bb = pd.read_parquet(backbone or BACKBONE_FILE)
    out = {}
    for c in CCYS:
        s = bb[(bb["series"] == f"POL_{c}") & (bb["avail"] <= asof)].sort_values("date")
        out[c] = float(s["value"].iloc[-1]) if len(s) else None
    return out


def rows_for(asof: pd.Timestamp, source: str, scores: dict, source_asof: str, now: str) -> list[dict]:
    s = pd.Series(scores, dtype=float)
    rank = s.rank(ascending=False, method="min")
    out = []
    for c in CCYS:
        for h in HORIZONS:
            out.append({"asof": asof, "source": source, "source_asof": source_asof, "ccy": c,
                        "score": s.get(c), "rank": rank.get(c), "horizon_weeks": h,
                        "due": asof + pd.Timedelta(weeks=h), "snapshot_at": now,
                        "realized_bp": float("nan"), "scored_at": None})
    return out


def snapshot(asof: pd.Timestamp, calls: Path = CALLS, payload: Path = PAYLOAD) -> pd.DataFrame:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    v3, v3_asof = v3_scores(payload)
    rows = (rows_for(asof, "v3", v3, v3_asof, now)
            + rows_for(asof, "macro_momentum", macro_scores(asof), asof.strftime("%Y-%m-%d"), now)
            + rows_for(asof, "carry", carry_scores(asof), asof.strftime("%Y-%m-%d"), now))
    new = pd.DataFrame(rows, columns=COLUMNS)
    if calls.exists():
        old = pd.read_parquet(calls)
        old = old[pd.to_datetime(old["asof"]) != asof]
        new = pd.concat([old, new], ignore_index=True)
    new = new.sort_values(["asof", "source", "ccy", "horizon_weeks"]).reset_index(drop=True)
    calls.parent.mkdir(parents=True, exist_ok=True)
    new.to_parquet(calls, index=False)
    return new


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default=None)
    a = ap.parse_args()
    asof = pd.Timestamp(a.asof) if a.asof else pd.Timestamp.now(tz="UTC").tz_localize(None).normalize()
    df = snapshot(asof)
    cur = df[df["asof"] == asof].pivot_table(index="ccy", columns="source", values="rank", aggfunc="first")
    print(f"snapshot {asof.date()} -> {CALLS} ({len(df)} rows)")
    print(cur.reindex(CCYS).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
