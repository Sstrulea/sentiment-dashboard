"""Metals under v3 (2026-10-07): measure the RMS of GOLD and SILVER and the metal
thresholds, and run the pre-registered acceptance of the metal formula —
instrumentation only, never imported by production.

  new metal score = (0.67 × Rates + 0.33 × Macro) × 2.5   (production, V3_METAL_WEIGHTS)
  old metal score = (0.56 × Macro + 0.33 × Rates + 0.11 × COT) × 2.5   (v3 as merged)

Weeks: the v3 reconstruction cache of scripts/measure/v3_scales.py (Fridays 21:00
UTC, 2023-09-22 .. 2026-10-02, 159 weeks; run `v3_scales.py collect` first).
RMS = sqrt(mean(score²)) per metal over the last 156 weeks; thresholds = p55 / p90 of
|score / RMS| over GOLD and SILVER, last 53 weeks.

Acceptance (pre-registered): Stooq daily closes read from ~/Downloads/xauusd_d.csv
and ~/Downloads/xagusd_d.csv (never copied into the repo); entry = first close on or
after the Monday after the Friday, exit = first close on or after entry + 7·h days.
  1. Spearman(new score, forward return) > 0 at 1, 2 and 4 weeks, both metals;
  2. each metal Very in 5–20% of the weeks;
  3. averaged over GOLD and SILVER, the new score's Spearman at 2 and at 4 weeks
     is at least the old score's.

    python scripts/measure/v3_metals.py
"""
from __future__ import annotations

import json
import math
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.crossasset_compute import V3_METAL_WEIGHTS, v3_macro_raw, v3_yield_signals  # noqa: E402
from src.economic_compute import bias_label, v3_combine  # noqa: E402

CACHE = ROOT / "scripts" / "measure" / "__pycache__" / "v3_weeks.pkl"
PRICES = {"GOLD": Path.home() / "Downloads" / "xauusd_d.csv", "SILVER": Path.home() / "Downloads" / "xagusd_d.csv"}
OLD_WEIGHTS = {"macro": 0.56, "rates": 0.33, "cot": 0.11}
METALS = ("GOLD", "SILVER")


def scores() -> pd.DataFrame:
    fx = yaml.safe_load((ROOT / "data/economic_instruments.yaml").read_text())["v3"]
    xcfg = yaml.safe_load((ROOT / "data/crossasset_instruments.yaml").read_text())
    kx = xcfg["v3"]
    rates = pd.read_parquet(ROOT / "data/rates.parquet")
    real = pd.read_parquet(ROOT / "data/real_yields.parquet")
    rows = []
    for r in pickle.load(open(CACHE, "rb"))["recs"]:
        t = r["week"]
        us2y = (v3_yield_signals(rates, real, t.date(), kx["sigma_y"]).get("USD") or {}).get("signal")
        for m in METALS:
            raw = v3_macro_raw(xcfg["instruments"][m], r["cats"]["USD"], fx["sigma_ccy"]["USD"])
            mb = None if raw is None else raw / kx["sigma_macro"][m]
            rb = None if us2y is None else us2y / kx["sigma_rates"][m]
            cell = r["metal_cells"].get(m)
            cb = None if cell is None or not kx.get("sigma_cot_metal") else cell / kx["sigma_cot_metal"]
            new, _ = v3_combine({"macro": mb, "rates": rb}, V3_METAL_WEIGHTS)
            old, _ = v3_combine({"macro": mb, "rates": rb, "cot": cb}, OLD_WEIGHTS)
            rows.append({"week": t, "m": m, "new": None if new is None else 2.5 * new,
                         "old": None if old is None else 2.5 * old})
    return pd.DataFrame(rows)


def fwd(px: pd.Series, t: pd.Timestamp, h: int):
    x = px[px.index >= t.normalize() + pd.Timedelta(days=3)]
    if not len(x):
        return None
    d0, p0 = x.index[0], x.iloc[0]
    y = px[px.index >= d0 + pd.Timedelta(days=7 * h)]
    return None if not len(y) else math.log(y.iloc[0] / p0)


def main() -> int:
    D = scores()
    weeks = sorted(D.week.unique())
    last156, last53 = set(weeks[-156:]), set(weeks[-53:])
    rms = {m: float(math.sqrt(np.mean(np.square(D[(D.m == m) & D.week.isin(last156)]["new"])))) for m in METALS}
    z = [abs(r.new / rms[r.m]) for r in D[D.week.isin(last53)].itertuples()]
    thr = {"mild": float(np.quantile(z, .55)), "very": float(np.quantile(z, .90))}
    thr_r = {k: round(v, 2) for k, v in thr.items()}
    out = {"window": [str(weeks[0]), str(weeks[-1]), len(weeks)], "rms": rms, "thresholds_metal": thr}
    out["very_share_%"] = {m: round(100 * float(np.mean([bias_label(r.new / rms[m], thr_r).startswith("Very")
                                                         for r in D[D.m == m].itertuples()])), 1) for m in METALS}
    px = {m: (lambda d: pd.Series(d.Close.values, index=d.Date).sort_index())(pd.read_csv(p, parse_dates=["Date"]))
          for m, p in PRICES.items()}
    rho = {}
    for m in METALS:
        sub = D[D.m == m]
        for h in (1, 2, 4):
            pairs = [(r.old, r.new, fwd(px[m], r.week, h)) for r in sub.itertuples()]
            pairs = [p for p in pairs if p[2] is not None]
            ret = pd.Series([p[2] for p in pairs]).rank()
            for i, v in ((0, "old"), (1, "new")):
                rho[f"{m} {v} {h}w"] = float(pd.Series([p[i] for p in pairs]).rank().corr(ret))
    out["spearman"] = {k: round(v, 4) for k, v in rho.items()}
    avg = {f"{v} {h}w": (rho[f"GOLD {v} {h}w"] + rho[f"SILVER {v} {h}w"]) / 2 for v in ("old", "new") for h in (1, 2, 4)}
    out["spearman_avg"] = {k: round(v, 4) for k, v in avg.items()}
    out["acceptance"] = {
        "1_new_rho_positive": all(rho[f"{m} new {h}w"] > 0 for m in METALS for h in (1, 2, 4)),
        "2_very_5_20": all(5 <= v <= 20 for v in out["very_share_%"].values()),
        "3_avg_rho_2w_4w_ge_old": all(avg[f"new {h}w"] >= avg[f"old {h}w"] for h in (2, 4)),
    }
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
