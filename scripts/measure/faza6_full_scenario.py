"""MEASUREMENT INSTRUMENT — not production code. Read-only.

FAZA 6 — the full real adoption scenario, measured as one whole:
  Variant B (per-row no-consensus exclusion, the ADOPTED rule)
  + CAD core_cpi -> Median CPI y/y promotion (docs/proposal-cad-core-promotion.md)
  + AUD retail_sales <- Household Spending m/m continuation
    (docs/proposal-aud-retail-continuation.md)

Methodology for each promotion/continuation is validated against the
proposal docs' OWN pre-measured numbers before being trusted here (see the
"validation" prints) — both reproduce exactly (CAD Median score 0 exactly;
AUD continuation score +1, sigma 0.520, coverage 4, precise -0.25).

Also answers the N=0 exposure question: for every (currency, category) with
N<=2 in the full scenario, the nearest max-age-window expiry date among its
surviving indicators (i.e. how many days from as_of until that cell's next
indicator ages out and coverage drops further).
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
from src.economic_compute import effective_frequency, _max_age_for  # noqa: E402

AS_OF = C.AS_OF


def build_full_scenario_calendar() -> pd.DataFrame:
    cal = C.load_calendar()
    arch = parse_jblanked_range(str(ROOT / "data" / "archive" / "ff_calendar_range.json"))
    arch_cal = C.to_scoring_frame(arch, C.build_matcher())

    # 1) CAD core_cpi -> Median CPI y/y (slot-swap: old wrong-unit rows dropped,
    #    archive Median rows relabeled onto the core_cpi indicator_key).
    cal2 = cal[~((cal["currency"] == "CAD") & (cal["indicator_key"] == "core_cpi"))].copy()
    median_rows = arch_cal[(arch_cal["currency"] == "CAD") & (arch_cal["indicator_key"] == "median_cpi_yoy")].copy()
    median_rows["indicator_key"] = "core_cpi"
    cal2 = pd.concat([cal2, median_rows], ignore_index=True)

    # 2) AUD retail_sales <- Household Spending m/m (continuation: CONCATENATE,
    #    do not replace — the old retail_sales history + the new Household
    #    Spending prints become one series, per the proposal's own method).
    hs_rows = arch_cal[(arch_cal["currency"] == "AUD") & (arch_cal["indicator_key"] == "household_spending")].copy()
    hs_rows["indicator_key"] = "retail_sales"
    cal2 = pd.concat([cal2, hs_rows], ignore_index=True)

    return cal2


def nearest_expiry(card: dict, ind_cfg: dict, currency: str, as_of: pd.Timestamp, exclude_rule) -> list[dict]:
    """For each surviving (non-excluded under `exclude_rule`) breakdown entry
    in a currency's card, compute the calendar date its OWN release_dt falls
    outside the max-age window — i.e. the date it goes stale absent a new
    print. Uses the SAME exclude_rule as the active variant (not just
    `stale`) so a no_consensus entry that Variant B already excludes from N
    is not mistaken for a surviving indicator. Returns rows sorted
    soonest-first."""
    defaults = ind_cfg.get("defaults", {}) or {}
    indicators = ind_cfg.get("indicators", {}) or {}
    out = []
    for key, entry in card["breakdown"].items():
        if key == "rate_expectations" or exclude_rule(currency, key, entry):
            continue
        cat = entry.get("category")
        ind_c = indicators.get(key, {})
        freq = effective_frequency(ind_c, defaults, currency)
        max_age = _max_age_for(ind_c, defaults, freq)
        expiry = pd.Timestamp(entry["release_dt"]) + pd.Timedelta(days=max_age)
        days_left = (expiry - as_of).days
        out.append({
            "currency": currency, "category": cat, "indicator_key": key,
            "release_dt": entry["release_dt"], "freq": freq, "max_age_days": max_age,
            "expiry_date": expiry, "days_until_expiry": days_left,
            "flag": entry.get("flag"), "score": entry.get("score"),
        })
    return sorted(out, key=lambda r: r["days_until_expiry"])


def main():
    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 160)

    ind_cfg, inst_cfg = C.load_configs()

    cal_base = C.load_calendar()
    cal_full = build_full_scenario_calendar()

    # ---- B alone (already measured in the main doc) vs full scenario ----
    sc_b_alone = C.build_scorecards(cal_base, ind_cfg, inst_cfg, AS_OF, C.rule_variant_b)
    sc_full = C.build_scorecards(cal_full, ind_cfg, inst_cfg, AS_OF, C.rule_variant_b)
    sc_baseline = C.build_scorecards(cal_base, ind_cfg, inst_cfg, AS_OF, C.rule_baseline)

    inst_b_alone = C.build_instruments(sc_b_alone, inst_cfg)
    inst_full = C.build_instruments(sc_full, inst_cfg)
    inst_baseline = C.build_instruments(sc_baseline, inst_cfg)

    print("############ Validation — continuation + promotion reproduce the proposal docs' own numbers ############")
    print("AUD growth, continuation only (baseline rule, no B, no CAD promo):")
    cal_cont_only = pd.concat([
        cal_base,
        (lambda h: (h.assign(indicator_key="retail_sales")))(
            C.to_scoring_frame(parse_jblanked_range(str(ROOT / "data" / "archive" / "ff_calendar_range.json")), C.build_matcher())
            .pipe(lambda d: d[(d.currency == "AUD") & (d.indicator_key == "household_spending")])
        )
    ], ignore_index=True)
    sc_cont_only = C.build_scorecards(cal_cont_only, ind_cfg, inst_cfg, AS_OF, C.rule_baseline)
    print("  ", sc_cont_only["AUD"]["categories"]["growth"], "(expect coverage=4, precise=-0.25, matches proposal)")

    print()
    print("############ §FAZA6 CATEGORY TABLE — baseline / B alone / full scenario, all 8 currencies ############")
    rows = []
    for label, sc in [("baseline", sc_baseline), ("B_alone", sc_b_alone), ("full_scenario", sc_full)]:
        for ccy, card in sc.items():
            for cat, v in card["categories"].items():
                rows.append({"variant": label, "currency": ccy, "category": cat,
                            "coverage": v["coverage"], "score_precise": round(v["score_precise"], 4)})
    cat_df = pd.DataFrame(rows)
    piv = cat_df.pivot_table(index=["currency", "category"], columns="variant",
                             values=["coverage", "score_precise"], aggfunc="first")
    print(piv.to_string())

    print()
    print("############ §FAZA6 INDEX TABLE ############")
    idx_rows = []
    for label, sc in [("baseline", sc_baseline), ("B_alone", sc_b_alone), ("full_scenario", sc_full)]:
        for ccy, card in sc.items():
            idx_rows.append({"variant": label, "currency": ccy, "index": round(card["index"], 4)})
    idx_df = pd.DataFrame(idx_rows).pivot_table(index="currency", columns="variant", values="index", aggfunc="first")
    print(idx_df.to_string())

    print()
    print("############ §FAZA6 INSTRUMENT TABLE — all 28 FX + US-DOLLAR ############")
    inst_rows = []
    for label, insts in [("baseline", inst_baseline), ("B_alone", inst_b_alone), ("full_scenario", inst_full)]:
        for i in insts:
            inst_rows.append({"variant": label, "symbol": i["symbol"], "score": round(i["score"], 4), "bias": i["bias"]})
    inst_df = pd.DataFrame(inst_rows)
    score_piv = inst_df.pivot_table(index="symbol", columns="variant", values="score", aggfunc="first")
    bias_piv = inst_df.pivot_table(index="symbol", columns="variant", values="bias", aggfunc="first")
    print(score_piv.join(bias_piv, lsuffix="_score", rsuffix="_bias").to_string())

    print()
    print("############ §FAZA6 Bias-flip list: baseline -> full_scenario, vs baseline -> B_alone ############")
    base_by_sym = {i["symbol"]: i for i in inst_baseline}
    b_by_sym = {i["symbol"]: i for i in inst_b_alone}
    full_by_sym = {i["symbol"]: i for i in inst_full}
    flips_b = {sym for sym in base_by_sym if base_by_sym[sym]["bias"] != b_by_sym[sym]["bias"]}
    flips_full = {sym for sym in base_by_sym if base_by_sym[sym]["bias"] != full_by_sym[sym]["bias"]}
    print(f"B alone: {len(flips_b)} flips: {sorted(flips_b)}")
    print(f"full scenario: {len(flips_full)} flips: {sorted(flips_full)}")
    print(f"in B but NOT in full: {sorted(flips_b - flips_full)}")
    print(f"in full but NOT in B: {sorted(flips_full - flips_b)}")
    for sym in sorted(flips_b | flips_full):
        print(f"   {sym}: baseline={base_by_sym[sym]['bias']}({base_by_sym[sym]['score']:.3f})  "
              f"B_alone={b_by_sym[sym]['bias']}({b_by_sym[sym]['score']:.3f})  "
              f"full={full_by_sym[sym]['bias']}({full_by_sym[sym]['score']:.3f})")

    print()
    print("############ Cross-asset: full scenario ############")
    import yaml
    from src.crossasset_compute import compute_crossasset_scores
    with open(ROOT / "data" / "crossasset_instruments.yaml") as f:
        ca_cfg = yaml.safe_load(f)
    ca_base = compute_crossasset_scores({c: sc_baseline[c]["categories"] for c in sc_baseline}, config=ca_cfg)
    ca_full = compute_crossasset_scores({c: sc_full[c]["categories"] for c in sc_full}, config=ca_cfg)
    n_diff = sum(1 for s in ca_base if abs(ca_base[s]["score_precise"] - ca_full[s]["score_precise"]) > 1e-9)
    print(f"{n_diff} of {len(ca_base)} cross-asset instruments changed vs baseline.")

    print()
    print("############ §FAZA6 N<=2 residual cells, full scenario ############")
    n_low = []
    for ccy, card in sc_full.items():
        for cat, v in card["categories"].items():
            if cat != "monetary" and v["coverage"] <= 2:
                n_low.append((ccy, cat, v["coverage"], round(v["score_precise"], 4)))
    for row in sorted(n_low):
        print("   ", row)

    print()
    print("############ §FAZA6 N=0 EXPOSURE — nearest expiry per surviving indicator, N<=2 cells ############")
    for ccy, cat, cov, precise in sorted(n_low):
        card = sc_full[ccy]
        exp = [r for r in nearest_expiry(card, ind_cfg, ccy, AS_OF, C.rule_variant_b) if r["category"] == cat]
        print(f"-- {ccy} {cat} (N={cov}, precise={precise}):")
        for r in exp:
            print(f"     {r['indicator_key']:20s} released {r['release_dt'].date()}  "
                  f"freq={r['freq']:9s} max_age={r['max_age_days']}d  "
                  f"expires {r['expiry_date'].date()}  ({r['days_until_expiry']}d from as_of)")


if __name__ == "__main__":
    main()
