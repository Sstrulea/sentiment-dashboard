"""Scoring v3 shadow check (pre-registered 2026-10-07) — instrumentation only.

For each Friday 21:00 UTC from the merge day: public/data/economic.json as it
was committed then (the last commit at or before that instant, from git
history), per currency `score_v3` and `score_v2_ccy`; the 2-week IC of each by
the method of acceptance criterion 1 (scripts/measure/v3_scales.currency_ic:
FRED H.10, entry = first fix on or after the Monday after the Friday, exit =
first fix on or after entry + 14 days, log return vs USD (USD = 0) demeaned
across the 8, Spearman across the 8 each week, mean over the weeks).

PRE-REGISTERED RULE (do not change after looking at the numbers):
  revert to v2 only if IC(v3) < IC(v2) − 0.10;
  a negative IC(v3) alone means re-analysing the factors, NOT reverting.
It is run after 26 weeks of shadow; before that only as a dry run.

    python scripts/measure/v3_shadow_ic.py --since 2026-10-09 [--until ISO] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "measure"))
sys.path.insert(0, str(ROOT))

import v3_scales as V  # noqa: E402

JSON_PATH = "public/data/economic.json"
REVERT_MARGIN = 0.10
H_WEEKS = 2


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def payload_at(t: pd.Timestamp) -> dict | None:
    """economic.json from the last commit at or before t (UTC)."""
    sha = _git("rev-list", "-1", f"--before={t.strftime('%Y-%m-%dT%H:%M:%SZ')}", "HEAD", "--", JSON_PATH).strip()
    if not sha:
        return None
    try:
        return json.loads(_git("show", f"{sha}:{JSON_PATH}"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="the merge day (first Friday on or after it)")
    ap.add_argument("--until", default=None)
    ap.add_argument("--dry-run", action="store_true", help="report coverage; no decision")
    a = ap.parse_args()
    until = pd.Timestamp(a.until) if a.until else pd.Timestamp.utcnow().tz_localize(None)
    fridays = [f + pd.Timedelta(hours=21) for f in pd.date_range(pd.Timestamp(a.since), until, freq="W-FRI")]
    v3, v2, missing = {}, {}, 0
    for t in fridays:
        p = payload_at(t)
        cur = (p or {}).get("currencies") or {}
        s3 = {c: cur.get(c, {}).get("score_v3") for c in V.CCYS}
        s2 = {c: cur.get(c, {}).get("score_v2_ccy") for c in V.CCYS}
        if any(v is None for v in s3.values()) or any(v is None for v in s2.values()):
            missing += 1
            continue
        v3[t], v2[t] = s3, s2
    out = {"fridays": len(fridays), "with_v3_and_v2": len(v3), "without": missing,
           "rule": f"revert only if IC(v3) < IC(v2) - {REVERT_MARGIN}; IC(v3) < 0 alone -> re-analyse"}
    if v3:
        prices = V.fred_usd_values()
        last_day = max(s.index.max() for s in prices.values())
        ic3 = V.currency_ic(v3, prices, last_day)[H_WEEKS]
        ic2 = V.currency_ic(v2, prices, last_day)[H_WEEKS]
        out.update({"ic_v3_2w": ic3, "ic_v2_2w": ic2})
        if not a.dry_run:
            out["decision"] = ("REVERT to v2" if ic3["ic"] < ic2["ic"] - REVERT_MARGIN
                               else "keep v3" + (" — re-analyse the factors (IC(v3) < 0)" if ic3["ic"] < 0 else ""))
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
