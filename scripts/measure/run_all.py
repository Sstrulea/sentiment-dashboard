"""MEASUREMENT INSTRUMENT — not production code. Read-only, no parquet writes.

Runs baseline / Variant A (blacklist) / Variant B (per-row) / combined-with-
CAD-promotion scenarios through the harness in common.py (which reuses the
UNMODIFIED production compute_indicator_score + compute_instrument), and
prints every table needed for docs/measurement-no-consensus-slots.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import common as C  # noqa: E402
from src.econ_calendar_ff import parse_jblanked_range  # noqa: E402

AFFECTED_CCY = {"CAD", "NZD", "AUD"}
AFFECTED_CATS = {"growth", "inflation"}


def scorecards_for(cal, ind_cfg, inst_cfg, rule):
    return C.build_scorecards(cal, ind_cfg, inst_cfg, C.AS_OF, rule)


def category_table(label, scorecards):
    rows = []
    for ccy in sorted(scorecards):
        card = scorecards[ccy]
        for cat, v in card["categories"].items():
            rows.append({
                "variant": label, "currency": ccy, "category": cat,
                "coverage": v["coverage"], "score_precise": round(v["score_precise"], 4),
                "score_cell": v["score_cell"],
            })
    return pd.DataFrame(rows)


def index_table(label, scorecards):
    return pd.DataFrame([
        {"variant": label, "currency": ccy, "index": round(scorecards[ccy]["index"], 4),
         "coverage": scorecards[ccy]["coverage"]}
        for ccy in sorted(scorecards)
    ])


def instrument_table(label, instruments):
    return pd.DataFrame([
        {"variant": label, "symbol": i["symbol"], "score": round(i["score"], 4), "bias": i["bias"]}
        for i in instruments
    ])


def which_rows_excluded(scorecards, rule):
    """For each currency's breakdown, which entries does `rule` newly exclude
    (i.e. rule(...) True but NOT already stale) -> the actual slots removed."""
    out = []
    for ccy, card in scorecards.items():
        for key, entry in card["breakdown"].items():
            if key == "rate_expectations":
                continue
            excluded_by_rule = rule(ccy, key, entry)
            already_stale = bool(entry.get("stale"))
            if excluded_by_rule and not already_stale:
                out.append({
                    "currency": ccy, "indicator_key": key, "category": entry.get("category"),
                    "flag": entry.get("flag"), "score": entry.get("score"),
                    "actual": entry.get("actual"), "consensus": entry.get("consensus"),
                })
    return pd.DataFrame(out)


def main():
    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 160)

    cal = C.load_calendar()
    ind_cfg, inst_cfg = C.load_configs()

    # ---- baseline / A / B ----
    sc_base = scorecards_for(cal, ind_cfg, inst_cfg, C.rule_baseline)
    sc_a = scorecards_for(cal, ind_cfg, inst_cfg, C.rule_variant_a)
    sc_b = scorecards_for(cal, ind_cfg, inst_cfg, C.rule_variant_b)

    inst_base = C.build_instruments(sc_base, inst_cfg)
    inst_a = C.build_instruments(sc_a, inst_cfg)
    inst_b = C.build_instruments(sc_b, inst_cfg)

    print("############ §2/§4 CATEGORY TABLE (baseline vs A vs B), affected currencies only ############")
    cat_all = pd.concat([
        category_table("baseline", sc_base),
        category_table("variant_a", sc_a),
        category_table("variant_b", sc_b),
    ])
    cat_all = cat_all[cat_all["currency"].isin(AFFECTED_CCY)]
    piv = cat_all.pivot_table(index=["currency", "category"], columns="variant",
                               values=["coverage", "score_precise", "score_cell"], aggfunc="first")
    print(piv.to_string())

    print()
    print("############ §2/§4 FULL CATEGORY TABLE, ALL 8 CURRENCIES (baseline vs A vs B) ############")
    piv_full = pd.concat([
        category_table("baseline", sc_base),
        category_table("variant_a", sc_a),
        category_table("variant_b", sc_b),
    ]).pivot_table(index=["currency", "category"], columns="variant",
                    values=["coverage", "score_precise"], aggfunc="first")
    print(piv_full.to_string())

    print()
    print("############ §2/§4 INDEX TABLE (baseline vs A vs B), all currencies ############")
    idx_all = pd.concat([
        index_table("baseline", sc_base),
        index_table("variant_a", sc_a),
        index_table("variant_b", sc_b),
    ])
    idx_piv = idx_all.pivot_table(index="currency", columns="variant", values=["index", "coverage"], aggfunc="first")
    print(idx_piv.to_string())

    print()
    print("############ §4 INSTRUMENT TABLE (baseline vs A vs B), all 28 FX pairs ############")
    inst_all = pd.concat([
        instrument_table("baseline", inst_base),
        instrument_table("variant_a", inst_a),
        instrument_table("variant_b", inst_b),
    ])
    inst_piv = inst_all.pivot_table(index="symbol", columns="variant", values="score", aggfunc="first")
    bias_piv = inst_all.pivot_table(index="symbol", columns="variant", values="bias", aggfunc="first")
    combo = inst_piv.join(bias_piv, lsuffix="_score", rsuffix="_bias")
    print(combo.to_string())

    print()
    print("############ §3/§4 EXPLICIT LIST — pairs whose BIAS changes, baseline -> A ############")
    a_by_sym = {i["symbol"]: i for i in inst_a}
    base_by_sym = {i["symbol"]: i for i in inst_base}
    b_by_sym = {i["symbol"]: i for i in inst_b}
    for label, variant_by_sym in [("A", a_by_sym), ("B", b_by_sym)]:
        changed = [(sym, base_by_sym[sym]["bias"], variant_by_sym[sym]["bias"],
                   round(base_by_sym[sym]["score"], 3), round(variant_by_sym[sym]["score"], 3))
                  for sym in base_by_sym if base_by_sym[sym]["bias"] != variant_by_sym[sym]["bias"]]
        print(f"-- baseline -> variant {label}: {len(changed)} pair(s) change bias")
        for row in changed:
            print("   ", row)

    print()
    print("############ §3 A vs B divergence — do they differ from each other? ############")
    diff_ab = [(sym, round(a_by_sym[sym]["score"], 6), round(b_by_sym[sym]["score"], 6))
              for sym in a_by_sym if abs(a_by_sym[sym]["score"] - b_by_sym[sym]["score"]) > 1e-9]
    print(f"{len(diff_ab)} instrument(s) differ between A and B numerically:")
    for row in diff_ab:
        print("   ", row)

    print()
    print("############ §5 N=1 / N=0 check, all (currency, category) cells ############")
    for label, sc in [("baseline", sc_base), ("variant_a", sc_a), ("variant_b", sc_b)]:
        n1 = n0 = 0
        cells = []
        for ccy, card in sc.items():
            for cat, v in card["categories"].items():
                if cat == "monetary":
                    continue
                if v["coverage"] == 1:
                    n1 += 1
                    cells.append((ccy, cat, "N=1"))
                elif v["coverage"] == 0:
                    n0 += 1
                    cells.append((ccy, cat, "N=0"))
        print(f"-- {label}: N=1 cells={n1}, N=0 cells={n0}")
        for c in cells:
            print("   ", c)

    print()
    print("############ Slot-level explanation — rows newly excluded by A / by B (not already stale) ############")
    print("-- Variant A excludes:")
    print(which_rows_excluded(sc_base, C.rule_variant_a).to_string(index=False))
    print("-- Variant B excludes:")
    print(which_rows_excluded(sc_base, C.rule_variant_b).to_string(index=False))

    print()
    print("############ §6 Median CPI y/y consensus check (archive) ############")
    arch = parse_jblanked_range(str(ROOT / "data" / "archive" / "ff_calendar_range.json"))
    arch_cal = C.to_scoring_frame(arch, C.build_matcher())
    for key in ["median_cpi_yoy", "common_cpi_yoy", "trimmed_cpi_yoy", "core_cpi"]:
        sub = arch_cal[(arch_cal.currency == "CAD") & (arch_cal.indicator_key == key)]
        print(f"CAD {key}: rows={len(sub)} actual_valid={sub['actual'].notna().sum()} "
              f"consensus_valid={sub['consensus'].notna().sum()}")

    print()
    print("############ §6 Combined scenario: 5-series exclusion (minus CAD core_cpi) + CAD Median promotion ############")
    # Build a combined calendar: drop CAD's wrong-unit core_cpi rows, splice in
    # CAD's archive median_cpi_yoy rows relabeled as core_cpi (simulating the
    # backfill-merge the promotion proposal describes).
    combined_cal = cal[~((cal["currency"] == "CAD") & (cal["indicator_key"] == "core_cpi"))].copy()
    median_rows = arch_cal[(arch_cal["currency"] == "CAD") & (arch_cal["indicator_key"] == "median_cpi_yoy")].copy()
    median_rows["indicator_key"] = "core_cpi"
    combined_cal = pd.concat([combined_cal, median_rows], ignore_index=True)

    sc_combined = C.build_scorecards(combined_cal, ind_cfg, inst_cfg, C.AS_OF, C.rule_variant_a_minus_cad_core)
    inst_combined = C.build_instruments(sc_combined, inst_cfg)

    print("-- CAD scorecard, combined scenario:")
    for cat, v in sc_combined["CAD"]["categories"].items():
        print("   ", cat, v)
    print("   core_cpi breakdown entry:", sc_combined["CAD"]["breakdown"].get("core_cpi"))

    print("-- CAD-leg instrument scores: baseline vs variant_a(5-series) vs combined(5-series+promotion)")
    for i_base, i_a, i_comb in zip(inst_base, inst_a, inst_combined):
        assert i_base["symbol"] == i_a["symbol"] == i_comb["symbol"]
        sym = i_base["symbol"]
        cfg = inst_cfg["instruments"][sym]
        involves_cad = cfg.get("currency") == "CAD" or cfg.get("base") == "CAD" or cfg.get("quote") == "CAD"
        if involves_cad:
            print(f"   {sym}: baseline={i_base['score']:.4f} ({i_base['bias']})  "
                  f"variantA={i_a['score']:.4f} ({i_a['bias']})  "
                  f"combined={i_comb['score']:.4f} ({i_comb['bias']})")


if __name__ == "__main__":
    main()
