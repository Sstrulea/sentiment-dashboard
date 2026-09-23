"""Build data/ff_provenance_backfill.csv (audit 2026-09-23, 2.1/2.3) from the FULL
git history of data/ff_raw and data/jb_raw (+ data/archive), which a CI checkout
does not have. Payloads committed after --until are ignored (no lookahead when
measuring a snapshot).

    .venv/bin/python scripts/measure/build_ff_provenance_backfill.py \
        [--parquet PATH] [--until ISO] [--out PATH] [--ref REF]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.ff_provenance import BACKFILL_CSV, build_backfill, load_overrides  # noqa: E402


def _git(*args) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=ROOT).stdout


def history_payloads(folder: str, ref: str, until: pd.Timestamp) -> list[tuple[str, list]]:
    """Every file ever added under `folder` on `ref` by a commit <= until."""
    log = _git("log", ref, "--diff-filter=A", "--name-only", "--format=COMMIT %H %cI", "--", folder)
    out, seen, commit, ctime = [], set(), None, None
    for line in log.splitlines():
        if line.startswith("COMMIT "):
            _, commit, t = line.split()
            ctime = pd.Timestamp(t).tz_convert(None)
        elif line.strip() and line not in seen and ctime <= until:
            seen.add(line)
            try:
                out.append((Path(line).stem.split("_", 2)[-1], json.loads(_git("show", f"{commit}:{line}"))))
            except json.JSONDecodeError:
                pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", default=str(ROOT / "data" / "economic_calendar_ff.parquet"))
    ap.add_argument("--until", default=None)
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--out", default=str(BACKFILL_CSV))
    a = ap.parse_args()
    until = pd.Timestamp(a.until) if a.until else pd.Timestamp.utcnow().tz_localize(None)
    weekly = [(tag, ev) for tag, ev in history_payloads("data/ff_raw/", a.ref, until)]
    jb = [("0000-archive", json.loads((ROOT / "data/archive/ff_calendar_range.json").read_text()))]
    jb += history_payloads("data/jb_raw/", a.ref, until)
    pq = pd.read_parquet(a.parquet)
    b = build_backfill(pq, weekly, jb, load_overrides())
    b.to_csv(a.out, index=False)
    print(f"{len(weekly)} FF weekly feeds, {len(jb)} JB payloads (<= {until}) -> {a.out}")
    print(b["forecast_origin"].value_counts().to_dict())
    print(b["jb_status"].value_counts(dropna=False).to_dict())
    return 0


if __name__ == "__main__":
    sys.exit(main())
