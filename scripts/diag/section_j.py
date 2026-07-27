"""Section J — counterfactual V3: cross-sectional rank mapping (current day).

Percentile of each FX pair's score_precise WITHIN the day (28 pairs), mapped to
a rank-based label: p<=20 very bearish, 20-40 bearish, 40-60 neutral, 60-80
bullish, p>=80 very bullish (symmetric extension of the task's ">=80 strong
directional, 60-80 directional").

READ-ONLY.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.diag.analysis_common import DOCS_DIR, FX_PAIRS, load_today  # noqa: E402


def _rank_label(pct: float) -> str:
    if pct >= 80:
        return "Very Bullish (rank)"
    if pct >= 60:
        return "Bullish (rank)"
    if pct > 40:
        return "Neutral (rank)"
    if pct > 20:
        return "Bearish (rank)"
    return "Very Bearish (rank)"


def run() -> dict:
    today = load_today()["snapshot"]
    inst_by_sym = {i["symbol"]: i for i in today["instruments"]}

    rows = []
    for sym in FX_PAIRS:
        inst = inst_by_sym.get(sym)
        if inst is None:
            continue
        rows.append({"symbol": sym, "score_precise": inst["score"], "abs_label": inst["bias"]})
    df = pd.DataFrame(rows)
    df["percentile"] = df["score_precise"].rank(pct=True) * 100
    df["rank_label"] = df["percentile"].apply(_rank_label)

    def _directional(label: str) -> bool:
        return not label.startswith("Neutral")

    df["would_flip_neutral_to_directional"] = (df["abs_label"] == "Neutral") & _directional_series(df["rank_label"])
    df = df.sort_values("percentile", ascending=False)
    df.to_csv(DOCS_DIR / "diag-J-rank-mapping-today.csv", index=False)

    flips = df[df["would_flip_neutral_to_directional"]]
    return {"df": df, "flips": flips, "n_flips": len(flips), "n_total": len(df)}


def _directional_series(s: pd.Series) -> pd.Series:
    return ~s.str.startswith("Neutral")


if __name__ == "__main__":
    res = run()
    print(res["df"].to_string(index=False))
    print(f"\n{res['n_flips']}/{res['n_total']} pairs would move Neutral -> directional under rank mapping:")
    print(res["flips"][["symbol", "score_precise", "percentile", "rank_label"]].to_string(index=False))
