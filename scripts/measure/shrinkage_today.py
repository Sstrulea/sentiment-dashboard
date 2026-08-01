"""measure/coverage-asymmetry — Part 4: shrinkage-toward-zero simulation, TODAY only.

NOT a proposal to adopt — magnitude-only, per task instructions.

Shrinkage form (argued, not the only possible one): factor = min(1, sqrt(N/4)).
N=4 is the CADENCE_THRESHOLD (src/ff_scoring.py) "enough samples" bar already
used elsewhere in this project's own measurements (also the N>=4 bucket used
in Part 1's amplitude comparison) — reusing an existing, already-justified
reference point rather than inventing a new one. sqrt(N) scaling matches the
standard-error-of-a-mean shrinkage (SE ~ sigma/sqrt(N) for roughly-independent
per-print surprises): N=1 -> factor 0.5, N=2 -> 0.707, N=3 -> 0.866, N>=4 -> 1.0
(uncapped above 1, clamped at 1 so already-well-sampled categories are
untouched, not amplified).

Scope: macro-only (rate_entry=None, sentiment_cells=None, trend_cells=None),
mirroring src/ff_scoring.py::score_calendar's own Phase-2 precedent and every
other script in this measurement — isolates the calendar-coverage effect from
the unrelated COT/trend/rate engines. Bias labels compared here are therefore
"macro-only" bias, not the full live dashboard bias (which folds in
sentiment+trend+rate too) — noted explicitly in the report.
"""
from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.economic_compute import compute_currency_scorecard, compute_instrument, _clamp_cell  # noqa: E402
from scripts.measure.reconstruct import (  # noqa: E402
    build_full_scoring_frame, load_yaml, INDICATORS_YAML, INSTRUMENTS_YAML, OUR_CCYS,
)

N_REF = 4.0
CALC_CATEGORIES = ["growth", "inflation", "labour"]


def shrink_factor(n: float) -> float:
    if n <= 0:
        return 1.0
    return min(1.0, math.sqrt(n / N_REF))


def shrink_card(card: dict) -> dict:
    """Deep-copy `card`, replace each macro category's score_precise/score_cell
    with the shrunk value, and rebuild index_num/index_wsum consistently (same
    aggregation compute_currency_scorecard itself uses) so compute_instrument's
    downstream math sees a self-consistent shrunk scorecard."""
    shrunk = copy.deepcopy(card)
    index_num = 0.0
    index_wsum = 0.0
    for cat, cell in shrunk["categories"].items():
        coverage = cell.get("coverage", 0)
        weight = float(cell.get("weight", 1.0))
        if cat in CALC_CATEGORIES and coverage > 0:
            f = shrink_factor(coverage)
            new_precise = cell["score_precise"] * f
            cell["score_precise"] = new_precise
            cell["score_cell"] = _clamp_cell(new_precise)
            index_num += new_precise * weight
            index_wsum += weight
        elif coverage > 0:
            # monetary (or any non-calendar category present) — untouched
            index_num += cell["score_precise"] * weight
            index_wsum += weight
    shrunk["index_num"] = index_num
    shrunk["index_wsum"] = index_wsum
    shrunk["index"] = (index_num / index_wsum) if index_wsum else 0.0
    return shrunk


def main():
    ind = load_yaml(INDICATORS_YAML)
    inst_cfg = load_yaml(INSTRUMENTS_YAML)
    full_cal = build_full_scoring_frame()
    as_of = full_cal["release_dt"].max() + pd.Timedelta(days=1)  # "today"

    orig_cards = {ccy: compute_currency_scorecard(full_cal, ccy, ind, inst_cfg, as_of, rate_entry=None)
                 for ccy in OUR_CCYS}
    shrunk_cards = {ccy: shrink_card(card) for ccy, card in orig_cards.items()}

    print("=== Category cell changes (score_cell rounded, -2..2) ===")
    cell_changes = []
    for ccy in OUR_CCYS:
        for cat in CALC_CATEGORIES:
            o = orig_cards[ccy]["categories"].get(cat, {})
            s = shrunk_cards[ccy]["categories"].get(cat, {})
            if o.get("coverage", 0) == 0:
                continue
            if o.get("score_cell") != s.get("score_cell"):
                cell_changes.append({
                    "currency": ccy, "category": cat, "coverage": o["coverage"],
                    "precise_orig": o["score_precise"], "precise_shrunk": s["score_precise"],
                    "cell_orig": o["score_cell"], "cell_shrunk": s["score_cell"],
                })
    total_cells = sum(1 for ccy in OUR_CCYS for cat in CALC_CATEGORIES
                      if orig_cards[ccy]["categories"].get(cat, {}).get("coverage", 0) > 0)
    print(f"{len(cell_changes)} / {total_cells} category cells change their rounded score_cell:")
    for c in cell_changes:
        print(f"  {c['currency']}/{c['category']} (N={c['coverage']}): "
              f"{c['precise_orig']:.3f}->{c['precise_shrunk']:.3f}  cell {c['cell_orig']}->{c['cell_shrunk']}")

    print()
    print("=== Instrument bias flips ===")
    instruments = inst_cfg.get("instruments", {}) or {}
    flips = []
    for sym, cfg in instruments.items():
        orig_inst = compute_instrument(sym, cfg, orig_cards, inst_cfg,
                                       sentiment_cells=None, trend_cells=None)
        shrunk_inst = compute_instrument(sym, cfg, shrunk_cards, inst_cfg,
                                         sentiment_cells=None, trend_cells=None)
        if orig_inst["bias"] != shrunk_inst["bias"]:
            flips.append({
                "symbol": sym, "bias_orig": orig_inst["bias"], "bias_shrunk": shrunk_inst["bias"],
                "score_orig": orig_inst["score"], "score_shrunk": shrunk_inst["score"],
            })
    print(f"{len(flips)} / {len(instruments)} instruments flip bias label:")
    for f in flips:
        print(f"  {f['symbol']}: {f['bias_orig']} ({f['score_orig']:.2f}) -> "
              f"{f['bias_shrunk']} ({f['score_shrunk']:.2f})")

    pd.DataFrame(cell_changes).to_csv(ROOT / "docs" / "coverage-asymmetry-shrinkage-cells.csv", index=False)
    pd.DataFrame(flips).to_csv(ROOT / "docs" / "coverage-asymmetry-shrinkage-flips.csv", index=False)


if __name__ == "__main__":
    main()
