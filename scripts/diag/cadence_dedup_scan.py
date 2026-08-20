#!/usr/bin/env python3
"""FAZA 1E 1.4 — scan every (currency, indicator_key) pair in
data/economic_indicators.yaml for the CAD/GBP failure mode: declared
frequency != empirical cadence, where the resulting `dedup_gap_days`
(derived from the DECLARED frequency) is wide enough to collapse the
series' real prints (which have no usable MT5 `period` in this feed, so
_dedup_flash_final falls back to release-date proximity clustering).

Read-only: imports and CALLS effective_frequency/_dedup_flash_final from
economic_compute.py (pure functions) but writes nothing, patches nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import _dedup_flash_final, effective_frequency  # noqa: E402
from src.ff_scoring import build_matcher, detect_cadence, load_can_be_zero, to_scoring_frame  # noqa: E402

FF_PARQUET = ROOT / "data" / "economic_calendar_ff.parquet"
IND_YAML = ROOT / "data" / "economic_indicators.yaml"


def main() -> int:
    ff = pd.read_parquet(FF_PARQUET)
    ff["datetime_utc"] = pd.to_datetime(ff["datetime_utc"])
    matcher = build_matcher()
    ind_cfg = yaml.safe_load(IND_YAML.read_text())
    defaults = ind_cfg.get("defaults", {})
    dedup_gap_days = defaults.get("dedup_gap_days", {}) or {}
    cbz = load_can_be_zero(ind_cfg)
    scored = to_scoring_frame(ff, matcher, can_be_zero=cbz, flagged_bad=None)

    rows = []
    for (ccy, key), sub in scored.groupby(["currency", "indicator_key"]):
        cfg = (ind_cfg.get("indicators", {}) or {}).get(key, {})
        allowed = cfg.get("currencies")
        if allowed and ccy not in allowed:
            continue
        sub = sub.sort_values("release_dt").reset_index(drop=True)
        printed = sub[sub["actual"].notna()]
        if len(printed) < 4:
            continue

        declared = effective_frequency(cfg, defaults, ccy)
        empirical = detect_cadence(printed["release_dt"]) if len(printed) >= 2 else "unknown"
        mismatch = empirical != "unknown" and declared != empirical

        declared_gap = dedup_gap_days.get(declared)
        empirical_gap = dedup_gap_days.get(empirical) if empirical != "unknown" else None

        n_declared = len(_dedup_flash_final(sub.copy(), declared_gap))
        n_empirical = len(_dedup_flash_final(sub.copy(), empirical_gap)) if empirical_gap is not None else n_declared

        collapsed = n_empirical > 0 and n_declared <= max(2, n_empirical * 0.3)

        rows.append({
            "ccy": ccy, "key": key, "n_printed": len(printed),
            "declared_freq": declared, "empirical_freq": empirical, "mismatch": mismatch,
            "n_after_dedup_declared": n_declared, "n_after_dedup_empirical_freq": n_empirical,
            "collapsed": collapsed,
        })

    rows.sort(key=lambda r: (not (r["mismatch"] and r["collapsed"]), r["ccy"], r["key"]))
    print(f"{'CCY':<5}{'KEY':<26}{'N':>4}  {'DECLARED':<10}{'EMPIRICAL':<10}{'MISMATCH':<9}"
         f"{'DEDUP(decl)':>12}{'DEDUP(emp)':>12}  COLLAPSED")
    flagged = []
    for r in rows:
        flag = "  <-- SAME FAILURE MODE" if (r["mismatch"] and r["collapsed"]) else ""
        if flag:
            flagged.append(r)
        print(f"{r['ccy']:<5}{r['key']:<26}{r['n_printed']:>4}  {r['declared_freq'] or '-':<10}"
             f"{r['empirical_freq']:<10}{str(r['mismatch']):<9}"
             f"{r['n_after_dedup_declared']:>12}{r['n_after_dedup_empirical_freq']:>12}  "
             f"{str(r['collapsed']):<9}{flag}")

    print(f"\n{len(flagged)} pair(s) with declared!=empirical cadence AND a dedup collapse:")
    for r in flagged:
        print(f"  {r['ccy']}/{r['key']}: declared={r['declared_freq']} empirical={r['empirical_freq']} "
             f"n_printed={r['n_printed']} n_after_dedup={r['n_after_dedup_declared']} "
             f"(vs {r['n_after_dedup_empirical_freq']} at the correct cadence)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
