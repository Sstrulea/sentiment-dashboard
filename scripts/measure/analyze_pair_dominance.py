"""measure/coverage-asymmetry — Part 2 (pair-cell dominance, all 28 FX pairs)
and Part 3 (USD/CHF labour extreme case, in detail).

Operationalization of "which side dominates the |diff|": for a pair's
category cell with BOTH legs present (coverage>0), the diff is
score_precise_base - score_precise_quote. Each side's SHARE of the total
magnitude is |score_precise_side| / (|score_precise_base| + |score_precise_quote|)
(0.5/0.5 split when both are exactly 0 — a genuine tie, no side dominates).
"Dominance" = share > 0.5. Compared against which side has the SMALLER N.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
HIST_CSV = ROOT / "docs" / "coverage-asymmetry-history.csv"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"


def load_fx_pairs():
    d = yaml.safe_load(open(INSTRUMENTS_YAML))
    return [(k, v["base"], v["quote"]) for k, v in (d.get("instruments") or {}).items()
            if v.get("type") == "fx"]


def main():
    df = pd.read_csv(HIST_CSV)
    piv = df.set_index(["date", "currency", "category"])

    fx_pairs = load_fx_pairs()
    rows = []
    for sym, base, quote in fx_pairs:
        for cat in ["growth", "inflation", "labour"]:
            try:
                b = piv.xs((base, cat), level=("currency", "category"))
                q = piv.xs((quote, cat), level=("currency", "category"))
            except KeyError:
                continue
            merged = b.join(q, lsuffix="_base", rsuffix="_quote", how="inner")
            merged = merged[(merged["coverage_base"] > 0) & (merged["coverage_quote"] > 0)]
            if merged.empty:
                continue
            for date, r in merged.iterrows():
                n_base, n_quote = r["coverage_base"], r["coverage_quote"]
                sp_base, sp_quote = r["score_precise_base"], r["score_precise_quote"]
                total_mag = abs(sp_base) + abs(sp_quote)
                share_base = abs(sp_base) / total_mag if total_mag > 0 else 0.5
                if n_base < n_quote:
                    smaller_n_side = "base"
                elif n_quote < n_base:
                    smaller_n_side = "quote"
                else:
                    smaller_n_side = "tie"
                if share_base > 0.5:
                    dominant_side = "base"
                elif share_base < 0.5:
                    dominant_side = "quote"
                else:
                    dominant_side = "tie"
                rows.append({
                    "pair": sym, "category": cat, "date": date,
                    "n_base": n_base, "n_quote": n_quote,
                    "sp_base": sp_base, "sp_quote": sp_quote,
                    "smaller_n_side": smaller_n_side, "dominant_side": dominant_side,
                    "share_base": share_base,
                })

    out = pd.DataFrame(rows)
    out.to_csv(ROOT / "docs" / "coverage-asymmetry-pair-dominance.csv", index=False)
    print(f"{len(out)} (pair, category, date) observations with both legs present.")

    decisive = out[out["smaller_n_side"] != "tie"]
    print(f"{len(decisive)} observations where N differs between legs (excludes N ties).")
    match = (decisive["smaller_n_side"] == decisive["dominant_side"])
    print(f"Smaller-N side dominates |diff|: {match.mean()*100:.1f}% of the time "
          f"({match.sum()}/{len(decisive)})")

    print()
    print("=== By category ===")
    for cat, g in decisive.groupby("category"):
        m = (g["smaller_n_side"] == g["dominant_side"]).mean()
        print(f"  {cat}: {m*100:.1f}% ({len(g)} obs)")

    print()
    print("=== USD/CHF labour, in detail (Part 3) ===")
    usdchf_lab = out[(out["pair"] == "USDCHF") & (out["category"] == "labour")]
    print(f"{len(usdchf_lab)} observations (both USD and CHF labour present).")
    usdchf_lab_decisive = usdchf_lab[usdchf_lab["smaller_n_side"] != "tie"]
    print(f"  smaller_n_side counts: {usdchf_lab_decisive['smaller_n_side'].value_counts().to_dict()}")
    chf_is_smaller_n = usdchf_lab_decisive[usdchf_lab_decisive["smaller_n_side"] == "quote"]  # CHF=quote
    if len(chf_is_smaller_n):
        chf_dominates = (chf_is_smaller_n["dominant_side"] == "quote").mean()
        print(f"  Of {len(chf_is_smaller_n)} obs where CHF has smaller N: "
              f"CHF (single indicator) determines the diff in {chf_dominates*100:.1f}% of them")
    print(f"  Overall (any N relationship): CHF dominates in "
          f"{(usdchf_lab['dominant_side']=='quote').mean()*100:.1f}% of all {len(usdchf_lab)} obs")


if __name__ == "__main__":
    main()
