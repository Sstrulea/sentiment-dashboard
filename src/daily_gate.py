"""Once-a-day gate for a workflow step (fix/sources-compliance): the state is data/daily_gate.json, committed with the data, so every
run - Worker or backup schedule - sees the same day.

    python -m src.daily_gate check fred --after 21:00     # prints true / false: not done today (UTC) and the time is past `after`
    python -m src.daily_gate mark fred                    # records today (after the step succeeded)
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

STATE = Path(__file__).resolve().parents[1] / "data" / "daily_gate.json"


def due(name: str, after: str, now: Optional[datetime] = None, path: Path = STATE) -> bool:
    now = now or datetime.now(timezone.utc)
    state = json.loads(path.read_text()) if path.exists() else {}
    hh, mm = (int(x) for x in after.split(":"))
    return state.get(name) != now.date().isoformat() and (now.hour, now.minute) >= (hh, mm)


def mark(name: str, now: Optional[datetime] = None, path: Path = STATE) -> None:
    now = now or datetime.now(timezone.utc)
    state = json.loads(path.read_text()) if path.exists() else {}
    state[name] = now.date().isoformat()
    path.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("check", "mark"))
    ap.add_argument("name")
    ap.add_argument("--after", default="00:00")
    a = ap.parse_args(argv)
    if a.action == "check":
        print("true" if due(a.name, a.after) else "false")
    else:
        mark(a.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
