"""One-off (audit R3): data/archive/jb_status_history.json — the last JBlanked
status seen for every (currency, name_raw, UTC date) across the FULL git history
of data/jb_raw (the gap 2026-07-05 -> the oldest jb_raw payload still on disk).
Committed as an archive; the provenance migration reads it like
data/archive/ff_calendar_range.json. Runtime never needs git (CI fetch-depth 1).
Within one payload, a date's copy carrying a real actual wins (same rule as
jb_actuals.clean_jblanked_actuals); a newer payload overrides an older one.

    .venv/bin/python scripts/measure/build_jb_status_history.py [--ref REF] [--until ISO] [--out PATH]
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

from src.econ_calendar_ff import jb_status_of, jblanked_to_utc  # noqa: E402


def _git(*a) -> str:
    return subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT).stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD")
    ap.add_argument("--until", default=None)
    ap.add_argument("--out", default=str(ROOT / "data" / "archive" / "jb_status_history.json"))
    a = ap.parse_args()
    until = pd.Timestamp(a.until) if a.until else None
    log = _git("log", a.ref, "--diff-filter=A", "--name-only", "--format=COMMIT %H %cI", "--", "data/jb_raw/")
    files, seen, commit, ctime = [], set(), None, None
    for line in log.splitlines():
        if line.startswith("COMMIT "):
            _, commit, t = line.split()
            ctime = pd.Timestamp(t).tz_convert(None)
        elif line.strip() and line not in seen and (until is None or ctime <= until):
            seen.add(line)
            files.append((Path(line).stem, commit, line))
    status: dict = {}
    for stem, commit, path in sorted(files):                 # jb_range_<ISO>: time order
        try:
            events = json.loads(_git("show", f"{commit}:{path}"))
        except json.JSONDecodeError:
            continue
        per_day: dict = {}
        for e in events:
            dt = jblanked_to_utc(e.get("Date", ""))
            if dt is None:
                continue
            k = (str(e.get("Currency", "")).strip(), str(e.get("Name", "")).strip(), str(dt.date()))
            a_ = e.get("Actual")
            real = a_ not in (None, "") and float(a_) != 0.0
            prev = per_day.get(k)
            if prev is None or (real and not prev[1]) or (real == prev[1] and dt >= prev[2]):
                per_day[k] = (jb_status_of(e), real, dt)
        for k, (st, _r, _dt) in per_day.items():
            if st is not None:
                status[k] = st
    out = [{"currency": c, "name_raw": n, "date": d, "jb_status": s}
           for (c, n, d), s in sorted(status.items())]
    Path(a.out).write_text(json.dumps({"built_from": f"git log {a.ref} data/jb_raw ({len(files)} payloads)",
                                       "until": str(until) if until is not None else None,
                                       "entries": out}, indent=1) + "\n")
    print(f"{len(files)} payloads -> {len(out)} (currency, name_raw, date) statuses -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
