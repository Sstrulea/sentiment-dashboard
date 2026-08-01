"""eval/bucket-c-candidates — counterfactual panel: for each of the 17
"adauga" candidates, measured INDIVIDUALLY (not cumulatively) across as
many daily evaluation points as the archive allows (2023-01-02 ..
2026-07-31), how often does adding it alone move its category's
score_precise by >=0.25, and how often does it flip a pair bias.

SAME method as docs/measurement-coverage-asymmetry.md: today's fixed
config/rules applied retroactively to historical calendar data, daily
granularity, release_dt<=as_of truncation (validated mechanism). This is
a controlled counterfactual ("how much would this matter under today's
rules"), NOT a literal replay of what the dashboard showed on any given
historical date — same explicit limitation as the coverage-asymmetry
measurement, noted again in the report.

Zero writes to parquet/config. Investigation only.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import compute_currency_scorecard, compute_instrument  # noqa: E402
from scripts.measure.bucket_c_impact import (  # noqa: E402
    FAZA1_ORDER, candidate_rows, build_augmented_indicators_cfg, OUR_CCYS,
)
from scripts.measure.reconstruct import build_full_scoring_frame, load_yaml, INDICATORS_YAML, INSTRUMENTS_YAML  # noqa: E402

OUT_CAT_CSV = ROOT / "docs" / "bucket-c-panel-categories.csv"
OUT_INST_CSV = ROOT / "docs" / "bucket-c-panel-instruments.csv"


def instruments_for_currency(inst_cfg: dict, ccy: str) -> list[str]:
    out = []
    for sym, cfg in (inst_cfg.get("instruments") or {}).items():
        if cfg.get("type") == "single" and cfg.get("currency") == ccy:
            out.append(sym)
        elif cfg.get("type") == "fx" and ccy in (cfg.get("base"), cfg.get("quote")):
            out.append(sym)
    return out


def main(limit_days: int | None = None):
    ind_cfg = load_yaml(INDICATORS_YAML)
    inst_cfg = load_yaml(INSTRUMENTS_YAML)
    instruments = inst_cfg.get("instruments") or {}
    aug_ind_cfg = build_augmented_indicators_cfg(ind_cfg)
    full_cal_all = build_full_scoring_frame()

    start = full_cal_all["release_dt"].min().normalize()
    end = full_cal_all["release_dt"].max().normalize()
    days = pd.date_range(start, end, freq="D")
    if limit_days:
        days = days[-limit_days:]

    # per-candidate raw rows, precomputed once
    cand_rows = {(ccy, name): candidate_rows(ccy, name, key) for _, ccy, name, key in FAZA1_ORDER}
    cand_cat = {(ccy, name): aug_ind_cfg["indicators"][key]["category"] for _, ccy, name, key in FAZA1_ORDER}
    cand_insts = {(ccy, name): instruments_for_currency(inst_cfg, ccy) for _, ccy, name, key in FAZA1_ORDER}

    cat_rows: list[dict] = []
    inst_rows: list[dict] = []

    t0 = time.time()
    for i, day in enumerate(days):
        as_of = day + pd.Timedelta(hours=23, minutes=59)
        trunc_cal = full_cal_all[full_cal_all["release_dt"] <= as_of]

        base_cards = {ccy: compute_currency_scorecard(trunc_cal, ccy, ind_cfg, inst_cfg, as_of, rate_entry=None)
                     for ccy in OUR_CCYS}
        base_insts = {sym: compute_instrument(sym, cfg, base_cards, inst_cfg, sentiment_cells=None, trend_cells=None)
                     for sym, cfg in instruments.items()}

        for rank, ccy, name, key in FAZA1_ORDER:
            rows = cand_rows[(ccy, name)]
            rows_trunc = rows[rows["release_dt"] <= as_of]
            if rows_trunc.empty:
                continue
            aug_cal_ccy = pd.concat([trunc_cal, rows_trunc], ignore_index=True)
            aug_card = compute_currency_scorecard(aug_cal_ccy, ccy, aug_ind_cfg, inst_cfg, as_of, rate_entry=None)

            cat = cand_cat[(ccy, name)]
            base_cell = base_cards[ccy]["categories"].get(cat, {"score_precise": 0.0, "coverage": 0})
            aug_cell = aug_card["categories"].get(cat, {"score_precise": 0.0, "coverage": 0})
            contributed = aug_cell["coverage"] > base_cell["coverage"]
            delta = aug_cell["score_precise"] - base_cell["score_precise"]
            cat_rows.append({
                "candidate": f"{ccy}/{name}", "rank": rank, "date": day.date().isoformat(),
                "category": cat, "contributed": contributed,
                "base_sp": base_cell["score_precise"], "aug_sp": aug_cell["score_precise"], "delta": delta,
            })

            if not contributed:
                continue
            aug_cards_for_this = dict(base_cards)
            aug_cards_for_this[ccy] = aug_card
            for sym in cand_insts[(ccy, name)]:
                cfg = instruments[sym]
                aug_inst = compute_instrument(sym, cfg, aug_cards_for_this, inst_cfg,
                                             sentiment_cells=None, trend_cells=None)
                b_inst = base_insts[sym]
                inst_rows.append({
                    "candidate": f"{ccy}/{name}", "rank": rank, "date": day.date().isoformat(),
                    "symbol": sym, "base_score": b_inst["score"], "aug_score": aug_inst["score"],
                    "base_bias": b_inst["bias"], "aug_bias": aug_inst["bias"],
                    "flip": b_inst["bias"] != aug_inst["bias"],
                })

        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            print(f"  {i+1}/{len(days)} days done ({elapsed:.0f}s elapsed, "
                  f"~{elapsed/(i+1)*len(days):.0f}s total est.)", flush=True)

    cat_df = pd.DataFrame(cat_rows)
    inst_df = pd.DataFrame(inst_rows)
    cat_df.to_csv(OUT_CAT_CSV, index=False)
    inst_df.to_csv(OUT_INST_CSV, index=False)
    print(f"\nWrote {len(cat_df)} category rows -> {OUT_CAT_CSV}")
    print(f"Wrote {len(inst_df)} instrument rows -> {OUT_INST_CSV}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-days", type=int, default=None)
    args = ap.parse_args()
    main(limit_days=args.limit_days)
