"""measure/strength-index-distribution — PASUL 0 of the /strength.html route.

Reconstructs point-in-time `index` (as produced today by
compute_currency_scorecard, WITH rate_entry / monetary included — unlike
reconstruct.py::scorecards_at, which passes rate_entry=None) for the 8
board currencies, weekly, over the trailing 12 months. Reports the
distribution so a display constant K can be chosen for

    pct = clamp(50 + index * K, 0, 100)

One-off measurement. Nothing here is stored; K is hardcoded in
src/economic_render.py afterwards, with a comment citing this script's
output and the date it was run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.measure.reconstruct import (  # noqa: E402
    OUR_CCYS,
    build_full_scoring_frame,
    load_yaml,
    INDICATORS_YAML,
    INSTRUMENTS_YAML,
)
from src.economic_compute import compute_currency_scorecard, _rate_entry_for  # noqa: E402
from src.rate_compute import compute_rate_scores  # noqa: E402

RATES_PARQUET = ROOT / "data" / "rates.parquet"
WEEKS = 52


def weekly_as_of_dates(end: pd.Timestamp, weeks: int = WEEKS) -> list[pd.Timestamp]:
    return [end - pd.Timedelta(days=7 * i) for i in range(weeks, -1, -1)]


def reconstruct_indices() -> pd.DataFrame:
    indicators_cfg = load_yaml(INDICATORS_YAML)
    instruments_cfg = load_yaml(INSTRUMENTS_YAML)
    full_cal = build_full_scoring_frame()
    rates_df = pd.read_parquet(RATES_PARQUET) if RATES_PARQUET.exists() else pd.DataFrame()

    end = pd.Timestamp.utcnow().tz_localize(None).normalize()
    as_of_dates = weekly_as_of_dates(end)

    rows = []
    for as_of in as_of_dates:
        trunc_cal = full_cal[full_cal["release_dt"] <= as_of]
        rate_scores = compute_rate_scores(rates_df, as_of=as_of.date()) if len(rates_df) else {}
        for ccy in OUR_CCYS:
            rate_entry = _rate_entry_for(rate_scores.get(ccy))
            card = compute_currency_scorecard(
                trunc_cal, ccy, indicators_cfg, instruments_cfg, as_of,
                rate_entry=rate_entry,
            )
            rows.append({
                "as_of": as_of,
                "currency": ccy,
                "index": card["index"],
                "coverage": card["coverage"],
                "monetary_available": rate_entry is not None,
            })
    return pd.DataFrame(rows)


def _pctiles(s: pd.Series) -> dict:
    qs = [0.0, 0.05, 0.25, 0.5, 0.75, 0.95, 1.0]
    labels = ["min", "p5", "p25", "median", "p75", "p95", "max"]
    vals = s.quantile(qs)
    return {lab: float(vals.loc[q]) for lab, q in zip(labels, qs)}


def main() -> None:
    df = reconstruct_indices()

    print(f"Reconstructed {len(df)} (currency, week) index observations "
          f"over {df['as_of'].nunique()} weeks, {df['as_of'].min().date()} .. {df['as_of'].max().date()}")
    print()

    print("=== GLOBAL distribution of `index` ===")
    g = _pctiles(df["index"])
    for k, v in g.items():
        print(f"  {k:>8}: {v:+.3f}")
    print()

    print("=== per-currency distribution of `index` ===")
    for ccy in OUR_CCYS:
        sub = df[df["currency"] == ccy]["index"]
        p = _pctiles(sub)
        avail = df[df["currency"] == ccy]["monetary_available"].mean()
        print(f"  {ccy}: min={p['min']:+.2f} p5={p['p5']:+.2f} p25={p['p25']:+.2f} "
              f"median={p['median']:+.2f} p75={p['p75']:+.2f} p95={p['p95']:+.2f} "
              f"max={p['max']:+.2f}  (monetary_available frac={avail:.2f})")
    print()

    out_of_band = df[(df["index"] < -10) | (df["index"] > 10)]
    print(f"Observations with |index| > 10: {len(out_of_band)} / {len(df)}")
    if len(out_of_band):
        print(out_of_band[["as_of", "currency", "index"]].to_string(index=False))
    print()

    # Pre-registered K criterion: K = 40 / p95_abs (global, pooled), rounded to 0.5.
    p95_abs = float(df["index"].abs().quantile(0.95))
    k_raw = 40.0 / p95_abs if p95_abs else float("nan")
    k = round(k_raw * 2) / 2  # nearest 0.5
    print(f"Global p95(|index|) = {p95_abs:.3f}")
    print(f"K_raw = 40 / p95_abs = {k_raw:.3f}")
    print(f"Proposed K (rounded to 0.5) = {k}")
    print()

    print("=== sanity: p5..p95 mapped through proposed K ===")
    pct = (50 + df["index"] * k).clip(0, 100)
    pp = _pctiles(pct)
    for kk, v in pp.items():
        print(f"  {kk:>8}: {v:.1f}%")


if __name__ == "__main__":
    main()
