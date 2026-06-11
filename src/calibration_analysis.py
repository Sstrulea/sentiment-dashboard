"""Calibration backfill — READ-ONLY analysis for tuning the composite scoring.

Replays the economic composite (`build_payload` + rate scores) at each historical
as-of over the available window, builds a score/bias distribution, and recommends
{scale, mild, very, category weights}. It does NOT modify any scoring parameter:
the recommendation is for review (D3 applies it).

    python -m src.calibration_analysis [--step N] [--csv]

Determinism: for each as-of we slice the raw parquets to releases on/before that
date and call the existing pure compute, so each row is exactly what the dashboard
would have shown on that day.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.economic_compute import bias_label, build_payload
from src.rate_compute import compute_rate_scores

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
CAL = ROOT / "data" / "economic_calendar.parquet"
RATES = ROOT / "data" / "rates.parquet"
INDICATORS_YAML = ROOT / "data" / "economic_indicators.yaml"
INSTRUMENTS_YAML = ROOT / "data" / "economic_instruments.yaml"
CSV_OUT = ROOT / "data" / "calibration_backfill.csv"

CATS = ["growth", "inflation", "labour", "monetary"]
BUCKETS = ["Very Bearish", "Bearish", "Neutral", "Bullish", "Very Bullish"]


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def run_backfill(step: int = 1, max_asof: int = 252):
    cal = pd.read_parquet(CAL)
    cal["release_dt"] = pd.to_datetime(cal["release_dt"])
    rates = pd.read_parquet(RATES)
    rates["date"] = pd.to_datetime(rates["date"])
    ind = yaml.safe_load(open(INDICATORS_YAML))
    inst = yaml.safe_load(open(INSTRUMENTS_YAML))

    # As-of window: business days, ending at the latest released actual, starting
    # ~120d after the calendar begins (so sigma has prints to work with).
    cal_actual = cal[cal["actual"].notna()]
    last = cal_actual["release_dt"].max().normalize()
    first = (cal["release_dt"].min() + pd.Timedelta(days=120)).normalize()
    asofs = pd.bdate_range(first, last)
    if len(asofs) > max_asof:
        asofs = asofs[-max_asof:]
    asofs = asofs[::step]

    inst_rows, ccy_rows = [], []
    for as_of in asofs:
        cal_slice = cal[cal["release_dt"] <= as_of]
        rates_slice = rates[rates["date"] <= as_of]
        rs = compute_rate_scores(rates_slice, as_of=as_of.date()) if not rates_slice.empty else {}
        payload = build_payload(cal_slice, ind, inst, as_of=as_of, rate_scores=rs or None)

        for ip in payload["instruments"]:
            cats = ip.get("categories", {})
            row = {"as_of": as_of.date(), "symbol": ip["symbol"], "type": ip["type"],
                   "score": ip["score"], "bias": ip["bias"]}
            for c in CATS:
                cell = cats.get(c, {})
                row[f"{c}_precise"] = cell.get("score_precise")
                row[f"{c}_cov"] = cell.get("coverage", 0)
            inst_rows.append(row)

        for ccy, card in payload["currencies"].items():
            r = {"as_of": as_of.date(), "ccy": ccy, "index": card["index"],
                 "coverage": card["coverage"]}
            for c in CATS:
                cell = card.get("categories", {}).get(c, {})
                r[f"{c}_precise"] = cell.get("score_precise")
                r[f"{c}_cov"] = cell.get("coverage", 0)
            ccy_rows.append(r)

    return pd.DataFrame(inst_rows), pd.DataFrame(ccy_rows), (asofs[0].date(), asofs[-1].date(), len(asofs))


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _pctiles(s: pd.Series) -> dict:
    s = s.dropna()
    if s.empty:
        return {}
    return {"min": s.min(), "p50": s.quantile(.50), "p75": s.quantile(.75),
            "p90": s.quantile(.90), "p95": s.quantile(.95), "max": s.max(), "mean": s.mean()}


def _bias_counts(biases: pd.Series) -> dict:
    n = len(biases)
    vc = biases.value_counts()
    return {b: 100.0 * vc.get(b, 0) / n for b in BUCKETS} if n else {}


def analyze(inst_df: pd.DataFrame, ccy_df: pd.DataFrame, window) -> dict:
    print("\n" + "=" * 92)
    print(f"CALIBRATION BACKFILL — as-of {window[0]} → {window[1]} "
          f"({window[2]} days × {inst_df['symbol'].nunique()} instruments = {len(inst_df)} obs)")
    print("=" * 92)

    inst_df = inst_df.copy()
    inst_df["abs"] = inst_df["score"].abs()

    # A) |score| distribution
    print("\n[A] |score| DISTRIBUTION (current scale=5)")
    for label, sub in [("ALL", inst_df), ("single", inst_df[inst_df.type == "single"]),
                       ("pair", inst_df[inst_df.type == "fx"])]:
        p = _pctiles(sub["abs"])
        if p:
            print(f"  {label:7} n={len(sub):5}  "
                  + " ".join(f"{k}={v:.2f}" for k, v in p.items()))
    print("  signed score percentiles (ALL):")
    sp = inst_df["score"].quantile([.05, .25, .5, .75, .95]).round(2).to_dict()
    print("   " + " ".join(f"p{int(k*100)}={v}" for k, v in sp.items()))

    # B) bias distribution (current params, read live from the YAML)
    th = (yaml.safe_load(open(INSTRUMENTS_YAML)).get("bias_thresholds", {}) or {})
    print(f"\n[B] BIAS DISTRIBUTION — current params "
          f"(mild={th.get('mild')}, very={th.get('very')}), all obs")
    bc = _bias_counts(inst_df["bias"])
    for b in BUCKETS:
        print(f"  {b:14} {bc.get(b, 0):5.1f}%")
    directional = sum(bc.get(b, 0) for b in ("Bearish", "Bullish"))
    very = sum(bc.get(b, 0) for b in ("Very Bearish", "Very Bullish"))
    print(f"  -> Neutral {bc.get('Neutral',0):.1f}% | directional {directional:.1f}% | very {very:.1f}%")

    # C) per-instrument: time-in-bias + flip frequency
    print("\n[C] PER-INSTRUMENT — dominant bias, %Neutral, day-to-day flip rate")
    print(f"  {'symbol':10}{'%neut':>7}{'%dir':>7}{'%very':>7}{'flip/yr':>9}  dominant")
    flips_all = []
    for sym, g in inst_df.groupby("symbol"):
        g = g.sort_values("as_of")
        bc_s = _bias_counts(g["bias"])
        flips = int((g["bias"].values[1:] != g["bias"].values[:-1]).sum())
        flip_rate = 252.0 * flips / max(1, len(g) - 1)
        flips_all.append(flip_rate)
        dom = g["bias"].mode().iloc[0] if not g.empty else "—"
        dirc = bc_s.get("Bearish", 0) + bc_s.get("Bullish", 0)
        vry = bc_s.get("Very Bearish", 0) + bc_s.get("Very Bullish", 0)
        print(f"  {sym:10}{bc_s.get('Neutral',0):>7.0f}{dirc:>7.0f}{vry:>7.0f}{flip_rate:>9.0f}  {dom}")
    print(f"  median flip/yr across instruments: {np.median(flips_all):.0f}")

    # D) category contribution (currency level)
    print("\n[D] CATEGORY CONTRIBUTION (currency-day index variance)")
    print(f"  {'cat':10}{'%present':>10}{'std(precise)':>14}{'corr w/ index':>15}")
    for c in CATS:
        col = ccy_df[f"{c}_precise"]
        present = 100.0 * (ccy_df[f"{c}_cov"] > 0).mean()
        sub = ccy_df[ccy_df[f"{c}_cov"] > 0]
        std = sub[f"{c}_precise"].std()
        corr = sub[f"{c}_precise"].corr(sub["index"]) if len(sub) > 2 else float("nan")
        print(f"  {c:10}{present:>9.0f}%{std:>14.3f}{corr:>15.2f}")
    print("  (monetary present only for currencies with a resolved 2y rate)")

    return {"abs_pctiles": _pctiles(inst_df["abs"]), "inst_df": inst_df}


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

def recommend(inst_df: pd.DataFrame,
              target_neutral=55.0, target_very=10.0,
              cur_scale=5.0, cur_mild=1.5, cur_very=3.5):
    a = inst_df["score"].abs().dropna()
    # thresholds straight off the empirical |score| distribution at current scale
    mild = float(a.quantile(target_neutral / 100.0))
    very = float(a.quantile(1.0 - target_very / 100.0))
    mild_r, very_r = round(mild, 1), round(very, 1)

    print("\n" + "=" * 92)
    print("RECOMMENDATION (review only — not applied)")
    print("=" * 92)
    print(f"  TARGET typical spread: ~{target_neutral:.0f}% Neutral · "
          f"~{100-target_neutral-target_very:.0f}% directional · ~{target_very:.0f}% Very (split bull/bear)")
    print(f"  Empirical |score|: mild@p{target_neutral:.0f}={mild:.2f}→{mild_r}, "
          f"very@p{100-target_very:.0f}={very:.2f}→{very_r} (scale unchanged at {cur_scale:g})")

    # projected distribution under recommended thresholds (same scale)
    th = {"very": very_r, "mild": mild_r}
    proj = inst_df["score"].apply(lambda s: bias_label(s, th))
    pc = _bias_counts(proj)
    print("\n  PROJECTED bias distribution under recommended (mild=%.1f, very=%.1f):" % (mild_r, very_r))
    for b in BUCKETS:
        print(f"    {b:14} {pc.get(b,0):5.1f}%")
    directional = pc.get("Bearish", 0) + pc.get("Bullish", 0)
    veryp = pc.get("Very Bearish", 0) + pc.get("Very Bullish", 0)
    print(f"    -> Neutral {pc.get('Neutral',0):.1f}% | directional {directional:.1f}% | very {veryp:.1f}%")

    print("\n  PARAMETER TABLE")
    print(f"  {'param':16}{'current':>10}{'recommended':>14}   rationale")
    print(f"  {'scale':16}{cur_scale:>10g}{cur_scale:>14g}   linear knob; thresholds carry the spread")
    print(f"  {'mild':16}{cur_mild:>10g}{mild_r:>14g}   p{target_neutral:.0f} of |score| → ~{target_neutral:.0f}% Neutral")
    print(f"  {'very':16}{cur_very:>10g}{very_r:>14g}   p{100-target_very:.0f} of |score| → ~{target_very:.0f}% Very")
    return {"mild": mild_r, "very": very_r}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only calibration backfill analysis.")
    ap.add_argument("--step", type=int, default=1, help="business-day step between as-ofs")
    ap.add_argument("--max-asof", type=int, default=252)
    ap.add_argument("--csv", action="store_true", help="write data/calibration_backfill.csv")
    ap.add_argument("--target-neutral", type=float, default=55.0)
    ap.add_argument("--target-very", type=float, default=10.0)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)

    inst_df, ccy_df, window = run_backfill(step=args.step, max_asof=args.max_asof)
    analyze(inst_df, ccy_df, window)
    recommend(inst_df, target_neutral=args.target_neutral, target_very=args.target_very)
    if args.csv:
        inst_df.to_csv(CSV_OUT, index=False)
        print(f"\nwrote {CSV_OUT} ({len(inst_df)} rows)")
    print("\n[STOP] Analysis only — no parameters changed.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
