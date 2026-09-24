"""measure — monetary on the 2y spread as ADOPTED (audit 5B), over the trailing 3 years.

Uses the production functions (rate_compute.compute_pair_spread_scores and
compute_rate_scores, averaged ends) point in time, business day by business
day, for the 15 board pairs of the 6 currencies with a 2y. "today" = the
definition before 5B: the difference of the legs' own single-point scores.

  dispersion    std(m) / std(today's difference)            (K and thresholds keep if ~1)
  jumps         |m_t − m_t−1| ≥ 1, per pair per month
  daily change  mean |m_t − m_t−1|
  corr          with the pair's log return over the same 21 rows and the next 20 rows
                (data/price_history.parquet)
  own score     how often the currency's own score (card, single-currency row,
                cross-asset rate_exp_2y) differs between single-point and averaged ends

    .venv/bin/python scripts/measure/monetary_spread_adopted.py --as-of 2026-09-24T06:06:11 [--years 3]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import src.economic_render as er  # noqa: E402
from src.momentum_common import bucket, clean_series, sign  # noqa: E402
from src.rate_compute import (BASELINE_N, FB_HI, FB_LO, MIN_FOR_Z, W_DEFAULT, Z_HI, Z_LO,  # noqa: E402
                              compute_pair_spread_scores, compute_rate_scores)

WITH_2Y = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD"]
W = W_DEFAULT


def single_point_score(ys: list[float]) -> int:
    """The pre-5B own score (Δ = y[-1] − y[-1-W]) — kept here only to measure."""
    n = len(ys)
    if n < W + 1:
        return 0
    d = ys[-1] - ys[-1 - W]
    ch = [ys[i] - ys[i - W] for i in range(W, n)]
    if len(ch) >= MIN_FOR_Z:
        std = float(np.std(ch[-BASELINE_N:], ddof=1))
        if std > 0:
            return bucket(d / std, Z_HI, Z_LO) * sign(d)
    return bucket(d, FB_HI, FB_LO) * sign(d)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", required=True)
    ap.add_argument("--years", type=int, default=3)
    a = ap.parse_args()
    as_of = pd.Timestamp(a.as_of)
    rates = pd.read_parquet(er.RATES_PARQUET)
    rates = rates[pd.to_datetime(rates["date"]) <= as_of]
    inst = er._load_yaml(er.INSTRUMENTS_YAML)["instruments"]
    pairs = [(s, v["base"], v["quote"]) for s, v in inst.items()
             if v.get("type") == "fx" and v["base"] in WITH_2Y and v["quote"] in WITH_2Y]
    series = {c: clean_series(g, "yield_pct") for c, g in rates.groupby("currency")}
    days = pd.bdate_range(as_of.normalize() - pd.DateOffset(years=a.years), as_of.normalize())

    rows, own = [], []
    for d in days:
        ref = d.date()
        new_own = compute_rate_scores(rates, as_of=ref)
        old_own = {}
        for c, (ds, ys) in series.items():
            k = int(np.searchsorted(np.array(ds, dtype="datetime64[D]"), np.datetime64(ref), side="right"))
            old_own[c] = single_point_score(ys[:k])
        for c in WITH_2Y:
            if c in new_own:
                own.append({"date": d, "ccy": c, "old": old_own[c], "new": new_own[c].rate_score,
                            "stale": new_own[c].stale})
        sp = compute_pair_spread_scores(rates, pairs, as_of=ref)
        for s, b, q in pairs:
            if s not in sp or new_own[b].stale or new_own[q].stale:
                continue
            rows.append({"symbol": s, "date": d, "m": sp[s]["m"], "z": sp[s]["z"],
                         "today": old_own[b] - old_own[q], "method": sp[s]["method"]})
    h = pd.DataFrame(rows).sort_values(["symbol", "date"])
    res = {"window": [str(days.min().date()), str(days.max().date())], "obs": len(h),
           "fallback_share": float((h["method"] != "z").mean()),
           "dispersion_m_over_today": float(h["m"].std(ddof=1) / h["today"].std(ddof=1))}
    months = len(days) / 21.0
    for col in ("m", "today"):
        dm = h.groupby("symbol")[col].diff().abs()
        res[f"jumps_ge1_per_pair_month_{col}"] = float((dm >= 1).sum() / h["symbol"].nunique() / months)
        res[f"mean_daily_change_{col}"] = float(dm.mean())
    pr = pd.read_parquet(ROOT / "data" / "price_history.parquet")
    rets = []
    for s, g in pr.groupby("symbol"):
        g = g.sort_values("date").drop_duplicates("date", keep="last")
        lp = pd.Series(np.log(g["close"].astype(float).to_numpy()), index=pd.to_datetime(g["date"]))
        rets.append(pd.DataFrame({"symbol": s, "date": lp.index, "r_same": (lp - lp.shift(W)).values,
                                  "r_f20": (lp.shift(-20) - lp).values}))
    j = h.merge(pd.concat(rets), on=["symbol", "date"])
    for col in ("r_same", "r_f20"):
        x = j.dropna(subset=[col])
        res[f"corr_{col}"] = {"m": float(np.corrcoef(x["m"], x[col])[0, 1]),
                              "today": float(np.corrcoef(x["today"], x[col])[0, 1]), "n": len(x)}
    o = pd.DataFrame(own)
    res["own_score_changed_share"] = float((o["old"] != o["new"]).mean())
    res["own_score_changed_by_ccy"] = {c: float((g["old"] != g["new"]).mean()) for c, g in o.groupby("ccy")}
    res["own_score_mean_daily_change"] = {k: float(o.sort_values(["ccy", "date"]).groupby("ccy")[k].diff().abs().mean())
                                          for k in ("old", "new")}
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
