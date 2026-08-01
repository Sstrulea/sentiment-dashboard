"""eval/bucket-c-candidates — FAZA 2, precizare 1: USD growth N=4 -> N=8,
ISOLATED (only the 4 USD growth candidates added, nothing else touched) —
a clean read of the dilution question without interference from the other
13 candidates in the global incremental run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.measure.bucket_c_impact import (  # noqa: E402
    candidate_rows, build_augmented_indicators_cfg, scorecards_and_instruments, OUR_CCYS,
)
from scripts.measure.reconstruct import build_full_scoring_frame, load_yaml, INDICATORS_YAML, INSTRUMENTS_YAML  # noqa: E402

# FAZA-1 rank order among the 4 USD growth candidates only
USD_GROWTH = [
    ("USD", "Industrial Production m/m", "industrial_production_mm"),
    ("USD", "Durable Goods Orders m/m", "durable_goods_orders_mm"),
    ("USD", "Personal Spending m/m", "personal_spending_mm"),
    ("USD", "Personal Income m/m", "personal_income_mm"),
]


def usd_leg_instruments(inst_cfg: dict) -> list[str]:
    out = []
    for sym, cfg in (inst_cfg.get("instruments") or {}).items():
        if cfg.get("type") == "single" and cfg.get("currency") == "USD":
            out.append(sym)
        elif cfg.get("type") == "fx" and ("USD" in (cfg.get("base"), cfg.get("quote"))):
            out.append(sym)
    return out


def main():
    ind_cfg = load_yaml(INDICATORS_YAML)
    inst_cfg = load_yaml(INSTRUMENTS_YAML)
    full_cal_all = build_full_scoring_frame()
    as_of = pd.Timestamp("2026-07-04")
    full_cal = full_cal_all[full_cal_all["release_dt"] <= as_of].copy()
    aug_ind_cfg = build_augmented_indicators_cfg(ind_cfg)
    usd_syms = usd_leg_instruments(inst_cfg)

    cal = full_cal.copy()
    cards, insts = scorecards_and_instruments(cal, ind_cfg, inst_cfg, as_of)
    steps = [{"n": 4, "added": "(baseline, N=4)", "cards": cards, "insts": insts}]

    for i, (ccy, name, key) in enumerate(USD_GROWTH, start=1):
        rows = candidate_rows(ccy, name, key)
        rows = rows[rows["release_dt"] <= as_of]
        cal = pd.concat([cal, rows], ignore_index=True)
        c, ins = scorecards_and_instruments(cal, aug_ind_cfg, inst_cfg, as_of)
        steps.append({"n": 4 + i, "added": name, "cards": c, "insts": ins})

    print("=== USD/growth: score_precise and amplitude as N grows 4 -> 8 (isolated) ===")
    rows_out = []
    for s in steps:
        cell = s["cards"]["USD"]["categories"]["growth"]
        rows_out.append({"N": s["n"], "added": s["added"], "score_precise": cell["score_precise"],
                        "abs_score_precise": abs(cell["score_precise"]), "score_cell": cell["score_cell"]})
        print(f"  N={s['n']}  (+{s['added']:30s})  score_precise={cell['score_precise']:+.4f}  "
              f"|score|={abs(cell['score_precise']):.4f}  score_cell={cell['score_cell']}")
    pd.DataFrame(rows_out).to_csv(ROOT / "docs" / "bucket-c-usd-growth-isolated.csv", index=False)

    print()
    print("=== USD index (macro-only), N=4 -> N=8 ===")
    for s in steps:
        print(f"  N={s['n']:2d}  USD index={s['cards']['USD']['index']:+.4f}")

    print()
    print(f"=== All USD-leg instruments ({len(usd_syms)}), baseline vs N=8 ===")
    base_insts = steps[0]["insts"]
    final_insts = steps[-1]["insts"]
    any_flip = False
    for sym in usd_syms:
        b, f = base_insts[sym], final_insts[sym]
        flip = " <-- FLIP" if b["bias"] != f["bias"] else ""
        if flip:
            any_flip = True
        print(f"  {sym:10s} {b['bias']:14s} ({b['score']:+.2f}) -> {f['bias']:14s} ({f['score']:+.2f}){flip}")
    print()
    print("No USD-leg bias flips." if not any_flip else "See FLIP markers above.")

    print()
    print("=== Marginal step-by-step, USD-leg instrument scores (drift toward/away from zero?) ===")
    prev = steps[0]["insts"]
    for s in steps[1:]:
        deltas = [(sym, prev[sym]["score"], s["insts"][sym]["score"]) for sym in usd_syms]
        moved_toward_zero = sum(1 for sym, p, c in deltas if abs(c) < abs(p))
        moved_away = sum(1 for sym, p, c in deltas if abs(c) > abs(p))
        print(f"  +{s['added']:30s}: {moved_toward_zero}/{len(deltas)} USD-leg instruments moved "
              f"TOWARD zero, {moved_away}/{len(deltas)} moved AWAY from zero")
        prev = s["insts"]


if __name__ == "__main__":
    main()
