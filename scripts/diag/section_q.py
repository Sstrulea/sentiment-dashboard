"""Section Q — restanced series from report 1 (Q2, Q3, Q4). Q1 lives in
section_q1_history.py (uses git history, not the replay cache).

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import DOCS_DIR, FX_PAIRS, load_today  # noqa: E402


def q3_rank_adjacency() -> pd.DataFrame:
    today = load_today()["snapshot"]
    inst_by_sym = {i["symbol"]: i for i in today["instruments"]}
    rows = []
    for sym in FX_PAIRS:
        inst = inst_by_sym.get(sym)
        if inst is None:
            continue
        rows.append({"symbol": sym, "score_precise": inst["score"], "abs_label": inst["bias"]})
    df = pd.DataFrame(rows).sort_values("score_precise", ascending=False).reset_index(drop=True)
    df["percentile"] = df["score_precise"].rank(pct=True) * 100
    # this row's score MINUS the next (lower-ranked) row's score — the gap down
    # to the pair immediately below it in the day's ranking.
    df["gap_to_next"] = (df["score_precise"] - df["score_precise"].shift(-1)).round(4)

    # "Near-identical" adjacent pairs: gap < 0.05 (i.e. would round to the same
    # 2-decimal display value) yet V3 assigns them different rank bands.
    def _band(p):
        if p >= 80:
            return "very_bullish"
        if p >= 60:
            return "bullish"
        if p > 40:
            return "neutral"
        if p > 20:
            return "bearish"
        return "very_bearish"

    df["rank_band"] = df["percentile"].apply(_band)
    df["next_rank_band"] = df["rank_band"].shift(-1)
    df["near_identical_but_separated"] = (
        (df["gap_to_next"] < 0.05) & (df["rank_band"] != df["next_rank_band"])
    )
    n_fabricated = int(df["near_identical_but_separated"].sum())
    df.to_csv(DOCS_DIR / "diag-Q3-rank-adjacency.csv", index=False)
    return df, n_fabricated


def q4_halflife_extremes() -> tuple[pd.DataFrame, pd.DataFrame]:
    sorted_df = pd.read_csv(DOCS_DIR / "diag-K-halflife-sorted.csv")
    fastest = sorted_df.head(15)
    slowest = sorted_df.tail(15)
    return fastest, slowest


def run() -> dict:
    q3_df, n_fabricated = q3_rank_adjacency()

    fastest, slowest = q4_halflife_extremes()

    h_today = pd.read_csv(DOCS_DIR / "diag-H-counterfactual-v1-today.csv")
    h_flip_matrix = pd.read_csv(DOCS_DIR / "diag-H-counterfactual-v1-flip-matrix.csv", index_col=0)

    return {
        "q3_df": q3_df, "n_fabricated": n_fabricated,
        "q4_fastest": fastest, "q4_slowest": slowest,
        "h_today": h_today, "h_flip_matrix": h_flip_matrix,
    }


if __name__ == "__main__":
    res = run()
    print("Q3 — rank adjacency (sorted by score_precise desc):")
    print(res["q3_df"][["symbol", "score_precise", "percentile", "rank_band", "gap_to_next"]].to_string(index=False))
    print(f"\nQ3: {res['n_fabricated']} adjacent pairs are near-identical (gap<0.05) yet land in different rank bands")
    print("\nQ4 fastest half-lives:")
    print(res["q4_fastest"].to_string(index=False))
    print("\nQ4 slowest half-lives:")
    print(res["q4_slowest"].to_string(index=False))
