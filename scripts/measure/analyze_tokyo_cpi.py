"""eval/bucket-c-candidates — FAZA 2, precizare 3: JPY Tokyo Core CPI y/y,
alone, in detail — the only High-impact candidate, top pick of FAZA 1.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.measure.bucket_c_impact import (  # noqa: E402
    candidate_rows, build_augmented_indicators_cfg, scorecards_and_instruments, pmi_guard,
)
from scripts.measure.reconstruct import build_full_scoring_frame, load_yaml, INDICATORS_YAML, INSTRUMENTS_YAML  # noqa: E402


def jpy_leg_instruments(inst_cfg: dict) -> list[str]:
    out = []
    for sym, cfg in (inst_cfg.get("instruments") or {}).items():
        if cfg.get("type") == "single" and cfg.get("currency") == "JPY":
            out.append(sym)
        elif cfg.get("type") == "fx" and ("JPY" in (cfg.get("base"), cfg.get("quote"))):
            out.append(sym)
    return out


def main():
    ind_cfg = load_yaml(INDICATORS_YAML)
    inst_cfg = load_yaml(INSTRUMENTS_YAML)
    full_cal_all = build_full_scoring_frame()
    as_of = pd.Timestamp("2026-07-04")
    full_cal = full_cal_all[full_cal_all["release_dt"] <= as_of].copy()
    aug_ind_cfg = build_augmented_indicators_cfg(ind_cfg)
    jpy_syms = jpy_leg_instruments(inst_cfg)

    base_cards, base_insts = scorecards_and_instruments(full_cal, ind_cfg, inst_cfg, as_of)

    rows = candidate_rows("JPY", "Tokyo Core CPI y/y", "tokyo_core_cpi_yoy")
    rows = rows[rows["release_dt"] <= as_of]
    print(f"Tokyo Core CPI y/y: {len(rows)} archived prints used, "
          f"{rows['release_dt'].min().date()} .. {rows['release_dt'].max().date()}")
    cal2 = pd.concat([full_cal, rows], ignore_index=True)
    guard_before, guard_after = pmi_guard(full_cal), pmi_guard(cal2)
    assert guard_before == guard_after, "PMI GUARD FAILED"
    print("PMI guard: OK")

    aug_cards, aug_insts = scorecards_and_instruments(cal2, aug_ind_cfg, inst_cfg, as_of)

    print()
    print("=== JPY/inflation category, before -> after ===")
    b = base_cards["JPY"]["categories"]["inflation"]
    a = aug_cards["JPY"]["categories"]["inflation"]
    print(f"  N: {b['coverage']} -> {a['coverage']}")
    print(f"  score_precise: {b['score_precise']:+.4f} -> {a['score_precise']:+.4f}  "
          f"(delta={a['score_precise']-b['score_precise']:+.4f})")
    print(f"  score_cell: {b['score_cell']} -> {a['score_cell']}")

    print()
    print("=== JPY index (macro-only), before -> after ===")
    print(f"  {base_cards['JPY']['index']:+.4f} -> {aug_cards['JPY']['index']:+.4f}")

    print()
    print("=== Tokyo Core CPI's own contribution (breakdown entry) ===")
    entry = aug_cards["JPY"]["breakdown"].get("tokyo_core_cpi_yoy")
    print(f"  actual={entry['actual']}, consensus={entry['consensus']}, "
          f"surprise={entry['surprise']}, z={entry['z']}, score={entry['score']}, "
          f"flag={entry['flag']}, stale={entry['stale']}")

    print()
    print(f"=== All JPY-leg instruments ({len(jpy_syms)}), before -> after ===")
    any_flip = False
    for sym in jpy_syms:
        b_i, a_i = base_insts[sym], aug_insts[sym]
        flip = " <-- FLIP" if b_i["bias"] != a_i["bias"] else ""
        if flip:
            any_flip = True
        print(f"  {sym:10s} {b_i['bias']:14s} ({b_i['score']:+.2f}) -> "
              f"{a_i['bias']:14s} ({a_i['score']:+.2f}){flip}")
    print("No JPY-leg bias flips." if not any_flip else "See FLIP markers above.")

    print()
    print("=== Baseline verification (CADENCE_THRESHOLD margin) ===")
    print(f"  {len(rows)} valid (actual & forecast) prints vs monthly threshold 24 "
          f"-> margin of {len(rows) - 24}")


if __name__ == "__main__":
    main()
