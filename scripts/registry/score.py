"""Registry v0 — score the calls whose due date has passed: realized return vs the basket
(H.10 noon fixings: last fixing on or before asof -> last on or before due, both within
4 days; bp). FX comes from FRED H.10 (keyless) unless --offline, which reads
data/backbone_rates.parquet. Prints a summary per source x horizon: N, mean rank IC,
top-2 minus bottom-2 spread (bp), sign hit of the cross-sectionally demeaned score.

    python scripts/registry/score.py [--offline]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.registry.snapshot import CALLS, CCYS  # noqa: E402
from src.tree.backbone import basket  # noqa: E402

FX = {"EUR": ("DEXUSEU", 1), "GBP": ("DEXUSUK", 1), "AUD": ("DEXUSAL", 1), "NZD": ("DEXUSNZ", 1),
      "JPY": ("DEXJPUS", -1), "CAD": ("DEXCAUS", -1), "CHF": ("DEXSZUS", -1)}
TOL = 4


def levels(offline: bool) -> pd.DataFrame:
    if offline:
        from src.tree.backbone import Backbone
        return Backbone.load().basket_levels()
    from src.rate_sources import FredSeriesSource
    cols = {}
    for c, (sid, sign) in FX.items():
        df = FredSeriesSource(sid).fetch_series()
        if df is None:
            raise RuntimeError(f"FRED {sid} unavailable; use --offline")
        df = df[df["value"] > 0]
        cols[c] = pd.Series(sign * np.log(df["value"].to_numpy(float)), index=pd.DatetimeIndex(df["date"]))
    w = pd.DataFrame(cols).dropna()
    w.insert(0, "USD", 0.0)
    return basket(w[[c for c in CCYS if c in w.columns]])


def level_at(s: pd.Series, t: pd.Timestamp) -> float:
    i = s.index.searchsorted(t, side="right") - 1
    if i < 0 or (t - s.index[i]).days > TOL:
        return float("nan")
    return float(s.iloc[i])


def score(calls: pd.DataFrame, lv: pd.DataFrame, now: str | None = None) -> pd.DataFrame:
    calls = calls.copy()
    last = lv.index[-1]
    todo = calls["realized_bp"].isna() & (pd.to_datetime(calls["due"]) <= last)
    for i in calls.index[todo]:
        c, a, d = calls.at[i, "ccy"], pd.Timestamp(calls.at[i, "asof"]), pd.Timestamp(calls.at[i, "due"])
        calls.at[i, "realized_bp"] = (level_at(lv[c], d) - level_at(lv[c], a)) * 1e4
        calls.at[i, "scored_at"] = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return calls


def summary(calls: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (src, h), g in calls.dropna(subset=["realized_bp"]).groupby(["source", "horizon_weeks"]):
        ics, spreads, hits = [], [], []
        for _, w in g.groupby("asof"):
            w = w.dropna(subset=["score"])
            if len(w) < 5:
                continue
            ics.append(spearmanr(w["score"], w["realized_bp"]).correlation)
            r = w.sort_values("score")
            spreads.append(r["realized_bp"].tail(2).mean() - r["realized_bp"].head(2).mean())
            dm = w["score"] - w["score"].mean()
            hits += list(np.sign(dm) == np.sign(w["realized_bp"]))
        rows.append({"source": src, "h": h, "snapshots": len(ics), "mean_IC": np.nanmean(ics) if ics else np.nan,
                     "top2_minus_bottom2_bp": np.mean(spreads) if spreads else np.nan,
                     "sign_hit": np.mean(hits) if hits else np.nan})
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    if not CALLS.exists():
        print(f"no {CALLS}; run snapshot.py first")
        return 1
    calls = score(pd.read_parquet(CALLS), levels(a.offline))
    calls.to_parquet(CALLS, index=False)
    s = summary(calls)
    print(s.to_string(index=False) if not s.empty else "nothing due yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
