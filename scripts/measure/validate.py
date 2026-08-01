"""measure/coverage-asymmetry — validate reconstruction against ground truth.

(1) as_of=now: compare against the LIVE public/data/economic.json.
(2) as_of=past: compare against `git show <commit>:public/data/economic.json`
    at 2-3 historical commits within the FF era (2026-07-05 onward) — this is
    the check that actually exercises the manual release_dt<=as_of truncation
    (a bug there would NOT show up in check (1), since today's full parquet
    has no rows released after today anyway).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.measure.reconstruct import (  # noqa: E402
    build_full_scoring_frame, scorecards_at, load_yaml,
    INDICATORS_YAML, INSTRUMENTS_YAML, OUR_CCYS, CALC_CATEGORIES,
)


def compare(reconstructed: dict, ground_truth_json: dict, label: str) -> bool:
    ok = True
    for ccy in OUR_CCYS:
        gt_cats = (ground_truth_json.get("currencies", {}).get(ccy, {}) or {}).get("categories", {})
        for cat in CALC_CATEGORIES:
            gt = gt_cats.get(cat, {"score_precise": 0.0, "coverage": 0})
            mine = reconstructed[ccy][cat]
            sp_match = abs(float(gt.get("score_precise", 0.0)) - float(mine["score_precise"])) < 1e-9
            cov_match = int(gt.get("coverage", 0)) == int(mine["coverage"])
            if not (sp_match and cov_match):
                ok = False
                print(f"  MISMATCH [{label}] {ccy}/{cat}: "
                      f"ground_truth={gt.get('score_precise')},{gt.get('coverage')} "
                      f"mine={mine['score_precise']},{mine['coverage']}")
    print(f"[{label}] {'MATCH' if ok else 'MISMATCH FOUND'}")
    return ok


def git_show_json(commit: str, path: str = "public/data/economic.json") -> dict:
    out = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def main():
    ind = load_yaml(INDICATORS_YAML)
    inst = load_yaml(INSTRUMENTS_YAML)
    full_cal = build_full_scoring_frame()

    all_ok = True

    # (1) today, live
    live = json.loads((ROOT / "public" / "data" / "economic.json").read_text())
    as_of_live = pd.Timestamp(live["as_of"])
    cards = scorecards_at(full_cal, as_of_live, ind, inst)
    all_ok &= compare(cards, live, f"live as_of={as_of_live}")

    # (2) historical commits, but ONLY within the CONFIG-STABLE window (no
    # data/economic_indicators.yaml / config/ff_aliases.yaml change since) —
    # commit 9f76d68 (2026-07-30T21:13:40Z) is the last scoring-config change.
    # Using an as_of BEFORE that would apply today's (different) rules to a
    # historically-rendered JSON that used different rules — a real, expected
    # divergence, not a truncation bug. Isolate the truncation check here.
    commits = subprocess.run(
        ["git", "log", "--since=2026-07-30T21:13:40Z",
         "--format=%H", "--", "public/data/economic.json"],
        cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip().splitlines()
    picks = [commits[0], commits[len(commits)//2], commits[-1]] if len(commits) >= 3 else commits
    for c in picks:
        hist_json = git_show_json(c)
        as_of_hist = pd.Timestamp(hist_json["as_of"])
        cards_hist = scorecards_at(full_cal, as_of_hist, ind, inst)
        all_ok &= compare(cards_hist, hist_json, f"historical {c[:8]} as_of={as_of_hist}")

    print()
    print("ALL VALIDATIONS PASSED" if all_ok else "VALIDATION FAILED — STOP, do not trust historical reconstruction")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
