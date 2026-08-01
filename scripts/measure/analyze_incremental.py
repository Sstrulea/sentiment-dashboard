"""eval/bucket-c-candidates — FAZA 2, global incremental measurement.

Adds all 17 "adauga" candidates ONE AT A TIME, in FAZA-1 rank order
(strongest first), recomputing every currency scorecard and every
instrument at each step. Reports both the MARGINAL effect (this step vs
the previous one) and the CUMULATIVE effect (this step vs the true,
untouched baseline) — the task explicitly wants both, since the
interaction of several additions to the same category is not just their
sum.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.measure.bucket_c_impact import (  # noqa: E402
    FAZA1_ORDER, candidate_rows, build_augmented_indicators_cfg, pmi_guard,
    scorecards_and_instruments, category_snapshot, OUR_CCYS, CALC_CATEGORIES,
)
from scripts.measure.reconstruct import build_full_scoring_frame, load_yaml, INDICATORS_YAML, INSTRUMENTS_YAML  # noqa: E402


def main():
    ind_cfg = load_yaml(INDICATORS_YAML)
    inst_cfg = load_yaml(INSTRUMENTS_YAML)
    full_cal_all = build_full_scoring_frame()

    # as_of pinned to the ARCHIVE's own horizon (its absolute latest raw
    # event is 2026-07-03), not "today" per the live parquet. All 17
    # candidates come from data/archive/ff_calendar_range.json, a frozen
    # snapshot that stops there; using today's as_of would flag 3 of them
    # (GBP/USD Industrial Production, USD Import Prices m/m) as STALE
    # purely because the archive hasn't been refreshed since early July -
    # an artifact of the data source, not a property of the indicators.
    # Both baseline (real, currently-scored indicators) AND augmented use
    # this SAME as_of + the SAME release_dt<=as_of truncation (mirrors
    # measure/coverage-asymmetry's validated reconstruct.py method) so the
    # comparison is apples-to-apples, not conflated with calendar drift
    # between July and today.
    as_of = pd.Timestamp("2026-07-04")
    full_cal = full_cal_all[full_cal_all["release_dt"] <= as_of].copy()

    aug_ind_cfg = build_augmented_indicators_cfg(ind_cfg)
    baseline_guard = pmi_guard(full_cal)

    steps = []  # list of (step_idx, label, cal, cards, insts)
    cal = full_cal.copy()
    cards, insts = scorecards_and_instruments(cal, ind_cfg, inst_cfg, as_of)
    steps.append({"step": 0, "added": "(baseline)", "cal": cal, "cards": cards, "insts": insts})

    for i, (rank, ccy, name, key) in enumerate(FAZA1_ORDER, start=1):
        rows = candidate_rows(ccy, name, key)
        rows = rows[rows["release_dt"] <= as_of]
        cal = pd.concat([cal, rows], ignore_index=True)
        c, ins = scorecards_and_instruments(cal, aug_ind_cfg, inst_cfg, as_of)
        steps.append({"step": i, "added": f"{ccy}/{name}", "cal": cal.copy(), "cards": c, "insts": ins})

    # PMI guard — must be bit-identical at the final step
    final_guard = pmi_guard(steps[-1]["cal"])
    assert final_guard == baseline_guard, f"PMI GUARD FAILED: {baseline_guard} != {final_guard}"
    print("PMI guard: OK (manufacturing_pmi/services_pmi counts unchanged at every step)")
    print()

    # --- category-level cumulative + marginal table ---
    cat_rows = []
    for s in steps:
        snap = category_snapshot(s["cards"])
        for ccy in OUR_CCYS:
            for cat in CALC_CATEGORIES:
                cat_rows.append({"step": s["step"], "added": s["added"], "currency": ccy, "category": cat,
                                "score_precise": snap[ccy][cat], "n": snap[ccy][f"{cat}_n"]})
    cat_df = pd.DataFrame(cat_rows)
    cat_df.to_csv(ROOT / "docs" / "bucket-c-incremental-categories.csv", index=False)

    # --- instrument-level cumulative + marginal table ---
    inst_rows = []
    for s in steps:
        for sym, inst in s["insts"].items():
            inst_rows.append({"step": s["step"], "added": s["added"], "symbol": sym,
                             "score": inst["score"], "bias": inst["bias"]})
    inst_df = pd.DataFrame(inst_rows)
    inst_df.to_csv(ROOT / "docs" / "bucket-c-incremental-instruments.csv", index=False)

    # --- print: category deltas for touched (currency,category) pairs only ---
    touched = {(ccy, (aug_ind_cfg["indicators"][key]["category"]))
              for _, ccy, _, key in FAZA1_ORDER}
    print("=== Category score_precise / N, baseline -> final (touched currency,category only) ===")
    base_snap = category_snapshot(steps[0]["cards"])
    final_snap = category_snapshot(steps[-1]["cards"])
    for ccy, cat in sorted(touched):
        b_sp, b_n = base_snap[ccy][cat], base_snap[ccy][f"{cat}_n"]
        f_sp, f_n = final_snap[ccy][cat], final_snap[ccy][f"{cat}_n"]
        print(f"  {ccy}/{cat}: N {b_n}->{f_n}  score_precise {b_sp:.3f}->{f_sp:.3f}  "
              f"|delta|={abs(f_sp-b_sp):.3f}")

    print()
    print("=== Marginal effect of EACH addition (step vs step-1), touched cell only ===")
    for i in range(1, len(steps)):
        rank, ccy, name, key = FAZA1_ORDER[i - 1]
        cat = aug_ind_cfg["indicators"][key]["category"]
        prev_snap = category_snapshot(steps[i - 1]["cards"])
        cur_snap = category_snapshot(steps[i]["cards"])
        p_sp, p_n = prev_snap[ccy][cat], prev_snap[ccy][f"{cat}_n"]
        c_sp, c_n = cur_snap[ccy][cat], cur_snap[ccy][f"{cat}_n"]
        print(f"  step {i:2d} +{ccy}/{name:35s} ({cat}): N {p_n}->{c_n}  "
              f"score_precise {p_sp:+.3f}->{c_sp:+.3f}  marginal={c_sp-p_sp:+.3f}")

    print()
    print("=== Bias flips: baseline vs final, ALL 29 instruments ===")
    base_bias = {sym: inst["bias"] for sym, inst in steps[0]["insts"].items()}
    final_bias = {sym: inst["bias"] for sym, inst in steps[-1]["insts"].items()}
    flips = [(sym, base_bias[sym], final_bias[sym]) for sym in base_bias if base_bias[sym] != final_bias[sym]]
    print(f"{len(flips)} / {len(base_bias)} instruments flip bias, baseline -> all 17 added:")
    for sym, b, f in flips:
        b_score = steps[0]["insts"][sym]["score"]
        f_score = steps[-1]["insts"][sym]["score"]
        print(f"  {sym}: {b} ({b_score:.2f}) -> {f} ({f_score:.2f})")

    print()
    print("=== Bias flips: step-by-step (first flip introduced by each addition) ===")
    prev_bias = base_bias
    for i in range(1, len(steps)):
        rank, ccy, name, key = FAZA1_ORDER[i - 1]
        cur_bias = {sym: inst["bias"] for sym, inst in steps[i]["insts"].items()}
        new_flips = [(sym, prev_bias[sym], cur_bias[sym]) for sym in prev_bias if prev_bias[sym] != cur_bias[sym]]
        if new_flips:
            print(f"  step {i:2d} +{ccy}/{name}: {new_flips}")
        prev_bias = cur_bias


if __name__ == "__main__":
    main()
