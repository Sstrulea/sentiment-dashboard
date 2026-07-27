"""Run the 12-month replay window + the current-day snapshot, cache to pickle.

READ-ONLY. Writes ONLY to scripts/diag/_cache/ (throwaway, not a deliverable —
the report + CSVs in docs/ are the deliverables).

    python -m scripts.diag.run_replay
"""
from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.engine import Context, build_snapshot, business_day_window  # noqa: E402

CACHE_DIR = Path(__file__).resolve().parent / "_cache"


def main() -> int:
    CACHE_DIR.mkdir(exist_ok=True)
    ctx = Context()
    print(f"calendar source = {ctx.cal_source}")

    win = business_day_window(ctx, months=12, freq="W-FRI")
    print(f"window: {win[0].date()} -> {win[-1].date()}  n={len(win)} (weekly, W-FRI)")

    snapshots = []
    t0 = time.time()
    for i, as_of in enumerate(win):
        snap = build_snapshot(as_of, ctx)
        snapshots.append(snap)
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(win)} done, {time.time()-t0:.1f}s elapsed")
    print(f"window replay done in {time.time()-t0:.1f}s")

    now = pd.Timestamp.now().normalize()
    today_snap = build_snapshot(now, ctx)
    print(f"current-day snapshot as_of={now.date()}")

    with open(CACHE_DIR / "window_snapshots.pkl", "wb") as f:
        pickle.dump({"window": win, "snapshots": snapshots}, f)
    with open(CACHE_DIR / "today_snapshot.pkl", "wb") as f:
        pickle.dump({"as_of": now, "snapshot": today_snap}, f)

    print(f"cached {len(snapshots)} window snapshots + 1 current-day snapshot -> {CACHE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
