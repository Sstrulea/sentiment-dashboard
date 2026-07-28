"""Section Q1 — REAL historical dashboard state from git history of
public/data/economic.json (not a replay). This captures what was actually
LIVE on each day, including the dirty pre-quarantine scoring before the
2026-07-07 zero-placeholder-quarantine merge (commit bdcd431) — something a
replay using TODAY's code cannot show, since to_scoring_frame's can_be_zero
gate is unconditional (not date-gated) and would retroactively "clean" every
historical as_of the same way.

Git history for this file only goes back to 2026-06-08 (~7 weeks before
today) — SHORTER than the 12-month window used elsewhere in this audit. Used
here anyway because it is the only way to see the ACTUAL pre/post-quarantine
transition; reported as its own (shorter, real) window, not extrapolated.

READ-ONLY — reads git history, writes nothing to the repo.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import DOCS_DIR, FX_PAIRS  # noqa: E402

PATH_IN_REPO = "public/data/economic.json"


def _git_log() -> list[tuple[str, str]]:
    out = subprocess.run(
        ["git", "log", "--format=%H|%cI", "--", PATH_IN_REPO],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()
    return [tuple(line.split("|", 1)) for line in out if line]


def _one_commit_per_day(commits: list[tuple[str, str]]) -> dict[str, str]:
    """{date: commit_hash} keeping the LAST commit of each calendar day
    (commits come out newest-first from git log)."""
    by_day: dict[str, str] = {}
    for h, ts in commits:
        day = ts[:10]
        if day not in by_day:   # first time we see a day (newest-first) = latest commit that day
            by_day[day] = h
    return by_day


def _show_json(commit: str) -> dict | None:
    r = subprocess.run(["git", "show", f"{commit}:{PATH_IN_REPO}"], cwd=ROOT,
                      capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def run() -> dict:
    commits = _git_log()
    by_day = _one_commit_per_day(commits)
    days_sorted = sorted(by_day)
    print(f"git history for {PATH_IN_REPO}: {len(commits)} commits, {len(days_sorted)} distinct days, "
          f"{days_sorted[0]} -> {days_sorted[-1]}")

    rows = []
    for day in days_sorted:
        payload = _show_json(by_day[day])
        if payload is None:
            continue
        insts = {i["symbol"]: i for i in payload.get("instruments", [])}
        fx = [insts[s] for s in FX_PAIRS if s in insts]
        if not fx:
            continue
        n = len(fx)
        neutral = sum(1 for i in fx if i["bias"] == "Neutral")
        very = sum(1 for i in fx if i["bias"] in ("Very Bullish", "Very Bearish"))
        directional = n - neutral - very
        rows.append({"date": day, "n": n, "pct_neutral": 100 * neutral / n,
                    "pct_directional": 100 * directional / n, "pct_very": 100 * very / n})

    df = pd.DataFrame(rows)
    df.to_csv(DOCS_DIR / "diag-Q1-real-history-bias-series.csv", index=False)
    return {"df": df}


if __name__ == "__main__":
    res = run()
    print(res["df"].to_string(index=False))
